"""COMSOL 6.4 FEM automation for x-cut LN/LT rib-waveguide SPDC PMF.

The COMSOL model owns the wavelength-dependent material functions. Python
changes ``lda0`` and, only on search failure, the eigenvalue search ``nshift``;
no per-wavelength material refractive-index override is used.
The convention is omega_p = omega_s + omega_i: wave 1 is the short-wave
pump, and waves 2 and 3 are signal and idler.  Linear mode simulations are
equally applicable to classical reverse-process characterization.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from qpm_simulation.crystal import search_expression, dielectric_expressions
from typing import Any, Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.io import loadmat, savemat
from scipy.interpolate import PchipInterpolator, griddata
from scipy.optimize import brentq

# %% Configuration and material references
# ----------------------------- USER CONFIGURATION ---------------------------
CORE_MATERIAL = "LT"                 # "LN" or "LT"; rib and slab are locked
TARGET_MODE = "TE0"                  # "TE0" or "TM0"; one target mode per run
RUN_PROFILE = "full"                 # "preview", "mini", or "full"
RUN_NAME = "LT_AIR_SPDC_PMF_T800_W3000_D550_A67_D1310_span100"
OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs" / "legacy"
RESUME_CURRENT_RUN = True

FILM_THICKNESSES_NM = (800.0,)
TOP_WIDTHS_NM = (3000.0,)             # add values to enable width scans
ETCH_DEPTHS_NM = (550.0,)
SIDEWALL_ANGLE_DEG = 67.0
DEGENERATE_WAVELENGTH_NM = 1310.0
PUMP_WAVELENGTH_NM = 655.0
LONG_WAVELENGTH_MIN_NM = 1210.0
LONG_WAVELENGTH_MAX_NM = 1410.0
PUMP_SCAN_MIN_NM = 604.0
PUMP_SCAN_MAX_NM = 706.0
COMSOL_WAVELENGTH_STEP_NM = 2.0
HEATMAP_STEP_NM = 0.5
INTERACTION_LENGTH_MM = 20.0
PMF_HALF_MAX_LEVEL = 0.5
BANDWIDTH_COARSE_STEP_NM = 0.5
BANDWIDTH_ROOT_TOLERANCE_NM = 1e-5
INITIAL_NUMBER_OF_MODES = 20
FALLBACK_NUMBER_OF_MODES = 40
ENABLE_ADJACENT_MODE_TRACKING = True
TRACKING_NUMBER_OF_MODES = 6
TRACKING_FIELD_OVERLAP_MIN = 0.95
ROBUST_RESEED_INTERVAL = 4
# 原搜索失败后才启用预测中心；不改变正常点的20/40模式搜索路径。
ENABLE_PREDICTED_SEARCH_FALLBACK = True
PREDICTED_FIELD_OVERLAP_MIN = 0.90
TE_THRESHOLD = 0.5
SEARCH_NEFF = "TARGET_CORE_INDEX"    # TE0 -> ne(lambda), TM0 -> no(lambda)
COMSOL_THREADS = 4
CORE_MAX_ELEMENT_NM = 30.0
SIDEWALL_MAX_ELEMENT_NM = 20.0
REFINED_CORE_MAX_ELEMENT_NM = 20.0
REFINED_SIDEWALL_MAX_ELEMENT_NM = 15.0
BACKGROUND_MAX_ELEMENT_NM = 70.0
REFINED_BACKGROUND_MAX_ELEMENT_NM = 50.0
PML_LAYERS = 8
REFINED_PML_LAYERS = 12
TOP_CLADDING = "AIR"                 # "SIO2" or "AIR"
# ---------------------------------------------------------------------------

LN_SOURCE = "Zelmon et al., JOSA B 14, 3319 (1997), congruent LN, 21 C"
LT_SOURCE = "Abedin and Ito, J. Appl. Phys. 80, 6561 (1996), congruent LT, 25 C"

@dataclass(frozen=True)
class Config:
    crystal_cut: str = "x"
    core_material: str = CORE_MATERIAL
    target_mode: str = TARGET_MODE
    run_profile: str = RUN_PROFILE
    run_name: str = RUN_NAME
    output_root: Path = OUTPUT_ROOT
    resume_current_run: bool = RESUME_CURRENT_RUN
    film_thicknesses_nm: tuple[float, ...] = FILM_THICKNESSES_NM
    top_widths_nm: tuple[float, ...] = TOP_WIDTHS_NM
    etch_depths_nm: tuple[float, ...] = ETCH_DEPTHS_NM
    sidewall_angle_deg: float = SIDEWALL_ANGLE_DEG
    degenerate_wavelength_nm: float = DEGENERATE_WAVELENGTH_NM
    pump_wavelength_nm: float = PUMP_WAVELENGTH_NM
    long_wavelength_min_nm: float = LONG_WAVELENGTH_MIN_NM
    long_wavelength_max_nm: float = LONG_WAVELENGTH_MAX_NM
    pump_scan_min_nm: float = PUMP_SCAN_MIN_NM
    pump_scan_max_nm: float = PUMP_SCAN_MAX_NM
    comsol_wavelength_step_nm: float = COMSOL_WAVELENGTH_STEP_NM
    heatmap_step_nm: float = HEATMAP_STEP_NM
    interaction_length_mm: float = INTERACTION_LENGTH_MM
    pmf_half_max_level: float = PMF_HALF_MAX_LEVEL
    bandwidth_coarse_step_nm: float = BANDWIDTH_COARSE_STEP_NM
    bandwidth_root_tolerance_nm: float = BANDWIDTH_ROOT_TOLERANCE_NM
    initial_number_of_modes: int = INITIAL_NUMBER_OF_MODES
    fallback_number_of_modes: int = FALLBACK_NUMBER_OF_MODES
    adjacent_mode_tracking: bool = ENABLE_ADJACENT_MODE_TRACKING
    tracking_number_of_modes: int = TRACKING_NUMBER_OF_MODES
    tracking_field_overlap_min: float = TRACKING_FIELD_OVERLAP_MIN
    robust_reseed_interval: int = ROBUST_RESEED_INTERVAL
    predicted_search_fallback: bool = ENABLE_PREDICTED_SEARCH_FALLBACK
    predicted_field_overlap_min: float = PREDICTED_FIELD_OVERLAP_MIN
    te_threshold: float = TE_THRESHOLD
    search_neff: str | float = SEARCH_NEFF
    threads: int = COMSOL_THREADS
    core_max_element_nm: float = CORE_MAX_ELEMENT_NM
    sidewall_max_element_nm: float = SIDEWALL_MAX_ELEMENT_NM
    refined_core_max_element_nm: float = REFINED_CORE_MAX_ELEMENT_NM
    refined_sidewall_max_element_nm: float = REFINED_SIDEWALL_MAX_ELEMENT_NM
    background_max_element_nm: float = BACKGROUND_MAX_ELEMENT_NM
    refined_background_max_element_nm: float = REFINED_BACKGROUND_MAX_ELEMENT_NM
    pml_layers: int = PML_LAYERS
    refined_pml_layers: int = REFINED_PML_LAYERS
    top_cladding: str = TOP_CLADDING
    window_width_um: float = 15.0
    window_height_um: float = 4.0
    window_center_y_um: float = -0.25
    pml_thickness_um: float = 1.0
    box_thickness_um: float = 2.0
    field_dx_nm: float = 40.0

    def validate(self) -> None:
        if self.target_mode not in {"TE0","TM0"}:
            raise ValueError("TARGET_MODE must be 'TE0' or 'TM0'.")
        if self.tracking_number_of_modes < 1 or self.robust_reseed_interval < 1:
            raise ValueError("Tracking mode count and robust reseed interval must be positive.")
        if not 0 < self.tracking_field_overlap_min <= 1:
            raise ValueError("Tracking field-overlap threshold must lie in (0, 1].")
        if not 0 < self.predicted_field_overlap_min <= 1:
            raise ValueError("Predicted-search field overlap must lie in (0, 1].")
        if self.core_material not in {"LN", "LT"}:
            raise ValueError("CORE_MATERIAL must be 'LN' or 'LT'.")
        if self.run_profile not in {"preview", "mini", "full"}:
            raise ValueError("RUN_PROFILE must be preview, mini, or full.")
        if self.top_cladding not in {"SIO2", "AIR"}:
            raise ValueError("TOP_CLADDING must be 'SIO2' or 'AIR'.")
        if max(self.etch_depths_nm) > min(self.film_thicknesses_nm):
            raise ValueError("Etch depth cannot exceed film thickness.")
        if not self.top_widths_nm or not self.etch_depths_nm or not self.film_thicknesses_nm:
            raise ValueError("Thickness, width and etch lists cannot be empty.")
        if min(self.signal_wavelengths_nm()) < 800:
            raise ValueError("Signal/idler band must remain in the long-wave branch.")
        if min(self.pump_wavelengths_nm()) < 400:
            raise ValueError("Pump band is outside the supported Sellmeier range.")
        if min(self.comsol_wavelength_step_nm, self.heatmap_step_nm, self.interaction_length_mm,
               self.bandwidth_coarse_step_nm, self.bandwidth_root_tolerance_nm) <= 0:
            raise ValueError("Wavelength steps and interaction length must be positive.")
        if not math.isclose(self.pump_wavelength_nm*2,self.degenerate_wavelength_nm,abs_tol=1e-9):
            raise ValueError("Degenerate wavelength must equal twice the fixed pump wavelength.")
        if not 0 < self.pmf_half_max_level < 1:
            raise ValueError("PMF half-max level must lie in (0, 1).")

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_name

    @property
    def sellmeier_source(self) -> str:
        return LN_SOURCE if self.core_material == "LN" else LT_SOURCE

    def wavelengths_nm(self) -> tuple[float, ...]:
        if self.run_profile == "preview":
            return (self.pump_center_nm, self.degenerate_wavelength_nm)
        return tuple(sorted(set(self.pump_wavelengths_nm()) | set(self.signal_wavelengths_nm())))

    @property
    def pump_center_nm(self) -> float:
        return self.pump_wavelength_nm

    def _inclusive_range(self, start: float, stop: float, step: float) -> tuple[float, ...]:
        count = int(round((stop-start)/step))
        if not math.isclose(start + count*step, stop, abs_tol=1e-9):
            raise ValueError("Configured wavelength span must be divisible by its step.")
        return tuple(float(start+i*step) for i in range(count+1))

    def signal_wavelengths_nm(self) -> tuple[float, ...]:
        return self._inclusive_range(self.long_wavelength_min_nm,self.long_wavelength_max_nm,
                                     self.comsol_wavelength_step_nm)

    def pump_wavelengths_nm(self) -> tuple[float, ...]:
        return tuple(sorted(set(self._inclusive_range(self.pump_scan_min_nm,self.pump_scan_max_nm,
            self.comsol_wavelength_step_nm)) | {self.pump_center_nm}))

    def heatmap_wavelengths_nm(self) -> np.ndarray:
        return np.asarray(self._inclusive_range(self.long_wavelength_min_nm,
            self.long_wavelength_max_nm,self.heatmap_step_nm))

    def thicknesses_nm(self) -> tuple[float, ...]:
        return (800.0,) if self.run_profile in {"preview","mini"} else self.film_thicknesses_nm

    def widths_nm(self) -> tuple[float, ...]:
        return (3000.0,) if self.run_profile in {"preview","mini"} else self.top_widths_nm

    def etches_nm(self) -> tuple[float, ...]:
        return (550.0,) if self.run_profile in {"preview","mini"} else self.etch_depths_nm

    def fingerprint(self) -> str:
        raw = json.dumps(serializable_config(self), sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


def serializable_config(cfg: Config) -> dict[str, Any]:
    d = asdict(cfg)
    d["output_root"] = str(cfg.output_root)
    return d

def refractive_indices(material: str, wavelength_nm: float) -> tuple[float, float]:
    """Independent Python reference for tests/reporting; COMSOL uses its own functions."""
    u = wavelength_nm / 1000.0
    limit = 5.0 if material == "LN" else 4.0
    if not 0.4 <= u <= limit:
        raise ValueError(f"{material} Sellmeier valid only on 0.4-{limit} um; got {u:g} um")
    if material == "LN":
        def z(a: float, b: float, c: float, d: float, e: float, f: float) -> float:
            q = u * u
            return math.sqrt(1 + a*q/(q-b) + c*q/(q-d) + e*q/(q-f))
        no = z(2.6734, .01764, 1.2290, .05914, 12.614, 474.6)
        ne = z(2.9804, .02047, .5981, .0666, 8.9543, 416.08)
    else:
        q = u * u
        no = math.sqrt(4.51224 + .0847522/(q-.19876**2) - .0239046*q)
        ne = math.sqrt(4.52999 + .0844313/(q-.20344**2) - .0237909*q)
    return no, ne

def bottom_width_nm(top_nm: float, depth_nm: float, angle_deg: float) -> float:
    return top_nm + 2 * depth_nm / math.tan(math.radians(angle_deg))


def silica_index(wavelength_nm: float) -> float:
    u=wavelength_nm/1000.0; q=u*u
    return math.sqrt(1+0.6961663*q/(q-0.0684043**2)+0.4079426*q/(q-0.1162414**2)+0.8974794*q/(q-9.896161**2))


def prepare_run(cfg: Config) -> logging.Logger:
    for p in (cfg.run_dir / "model", cfg.run_dir / "data" / "mat", cfg.run_dir / "data" / "figures"):
        p.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("lnlt_comsol")
    logger.handlers.clear(); logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.FileHandler(cfg.run_dir / "data" / "run.log", encoding="utf-8"), logging.StreamHandler()):
        h.setFormatter(formatter); logger.addHandler(h)
    payload = serializable_config(cfg) | {"fingerprint": cfg.fingerprint(), "created": datetime.now().isoformat()}
    (cfg.run_dir / "data" / "run_config.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return logger


def _set_string_array(node: Any, prop: str, values: Iterable[str]) -> None:
    node.set(prop, list(values))

# %% COMSOL model construction and selectable fundamental-mode extraction
def build_comsol_model(client: Any, cfg: Config, mesh_profile: str = "default") -> Any:
    from LN_LT_COMSOL_SHG_QPM_Automation import build_comsol_model as shared_builder
    return shared_builder(client, cfg, mesh_profile)


def _scalar_modes(value: Any) -> np.ndarray:
    return np.atleast_1d(np.asarray(value)).reshape(-1)


def extract_selected_mode(model: Any, cfg: Config, wavelength_nm: float, top_width_nm: float) -> dict[str, Any]:
    extraction_started=time.perf_counter()
    neff = _scalar_modes(model.evaluate("ewfd.neff"))
    # Exact MODE-equivalent TE fraction after coordinate mapping:
    # old MODE Ey -> COMSOL Ex; only transverse Ex/Ey enter the denominator.
    te = _scalar_modes(model.evaluate("intphys(abs(ewfd.Ex)^2)/(intphys(abs(ewfd.Ex)^2+abs(ewfd.Ey)^2)+1e-300)"))
    # PML coordinate stretching makes its integration Jacobian complex.  Use
    # magnitudes of the two region integrals instead of a cancellation-prone
    # integral over the transformed whole domain.
    pml = _scalar_modes(model.evaluate("abs(intpml(ewfd.normE^2))/(abs(intcore(ewfd.normE^2))+abs(intpml(ewfd.normE^2))+1e-300)"))
    loss = _scalar_modes(model.evaluate("ewfd.dampzdB", unit="dB/m"))
    candidates=[]
    for i,n in enumerate(neff):
        te_fraction=float(np.real(te[i]))
        target_fraction=te_fraction if cfg.target_mode=="TE0" else 1-te_fraction
        candidates.append({"mode_index":i+1,"neff":complex(n),"TE_pol":te_fraction,
                           "TM_pol":1-te_fraction,"polarization_fraction":target_fraction,
                           "pml_fraction":float(np.real(pml[i])),"loss":float(np.real(loss[i]))})
    good=[x for x in candidates if x["polarization_fraction"]>cfg.te_threshold
          and x["pml_fraction"]<0.5 and 1 < x["neff"].real < 3]
    if not good: raise RuntimeError(f"No guided {cfg.target_mode} mode found. Candidates: {candidates}")
    gx=np.arange(-cfg.window_width_um/2,cfg.window_width_um/2+1e-9,cfg.field_dx_nm/1000)
    gy=np.arange(cfg.window_center_y_um-cfg.window_height_um/2,cfg.window_center_y_um+cfg.window_height_um/2+1e-9,cfg.field_dx_nm/1000)
    xx,yy=np.meshgrid(gx,gy)
    field_diagnostics=[]
    field_expression="ewfd.Ex" if cfg.target_mode=="TE0" else "ewfd.Ey"
    for trial in sorted(good,key=lambda item:item["neff"].real,reverse=True):
        mode_index=trial["mode_index"]
        x,y,dominant=model.evaluate(["x","y",field_expression],inner=[mode_index])
        x=np.asarray(x).real.reshape(-1); y=np.asarray(y).real.reshape(-1); dominant=np.asarray(dominant).reshape(-1)
        field=griddata((x,y),dominant,(xx,yy),method="linear",fill_value=0)
        # Phase-aware mirror correlation distinguishes the even fundamental
        # mode (+1) from the first odd lateral mode (-1) for either polarization.
        denom=np.vdot(field,field).real+1e-300
        even_corr=float(np.real(np.vdot(field,np.fliplr(field))/denom))
        peak_col=int(np.unravel_index(np.abs(field).argmax(),field.shape)[1])
        peak_x_um=float(gx[peak_col])
        centered_peak=abs(peak_x_um) <= top_width_nm/2000
        field_diagnostics.append({"mode_index":mode_index,"even_symmetry":even_corr,"peak_x_um":peak_x_um})
        if even_corr>0.9 and centered_peak:
            selected=dict(trial)
            selected.update({"y":gx*1e-6,"z":gy*1e-6,"Ey":field,"candidates":candidates,
                             "field_diagnostics":field_diagnostics,"even_symmetry":even_corr,
                             "peak_x_um":peak_x_um,"centered_peak":centered_peak,
                             "target_mode":cfg.target_mode,"dominant_comsol_component":field_expression,
                             "is_TE0":cfg.target_mode=="TE0","is_TM0":cfg.target_mode=="TM0",
                             "extraction_elapsed_s":time.perf_counter()-extraction_started})
            return selected
    raise RuntimeError(f"No {cfg.target_mode} fundamental field found among guided candidates: {field_diagnostics}")

# %% Geometry/wavelength scan and resumable MAT export
@dataclass(frozen=True)
class ScanPoint:
    thickness_nm: float
    top_width_nm: float
    etch_depth_nm: float
    wavelength_nm: float

    @property
    def geometry(self) -> tuple[float, float, float]:
        return self.thickness_nm, self.top_width_nm, self.etch_depth_nm


def scan_points(cfg: Config) -> tuple[ScanPoint, ...]:
    return tuple(
        ScanPoint(t, w, d, wl)
        for t in cfg.thicknesses_nm()
        for w in cfg.widths_nm()
        for d in cfg.etches_nm()
        for wl in cfg.wavelengths_nm()
    )


def mat_path(cfg: Config, thickness_nm: float, top_width_nm: float,
             etch_depth_nm: float, wavelength_nm: float, suffix: str="") -> Path:
    mode_suffix="" if cfg.target_mode=="TE0" else "_TM0"
    name = (f"{cfg.core_material}_T{thickness_nm:.0f}_W{top_width_nm:.0f}_"
            f"D{etch_depth_nm:.0f}_A{cfg.sidewall_angle_deg:.0f}_L{wavelength_nm:g}{mode_suffix}{suffix}.mat")
    return cfg.run_dir / "data" / "mat" / name


def save_point(cfg: Config, result: dict[str, Any], point: ScanPoint,
               mesh_profile: str, modes_requested: int, path: Path) -> None:
    save_started=time.perf_counter()
    bw=bottom_width_nm(point.top_width_nm,point.etch_depth_nm,cfg.sidewall_angle_deg)
    candidate_json=[]
    for item in result["candidates"]:
        clean={}
        for k,v in item.items():
            scalar=np.asarray(v).reshape(-1)[0]
            if np.iscomplexobj(scalar): clean[k]={"real":float(np.real(scalar)),"imag":float(np.imag(scalar))}
            elif k=="mode_index": clean[k]=int(scalar)
            else: clean[k]=float(scalar)
        candidate_json.append(clean)
    payload={
        "film_thickness":point.thickness_nm*1e-9,"top_width":point.top_width_nm*1e-9,"bottom_width":bw*1e-9,
        "etch_depth":point.etch_depth_nm*1e-9,"sidewall_angle":cfg.sidewall_angle_deg,"wavelength":point.wavelength_nm*1e-9,
        "mode_index":result["mode_index"],"TE_pol":result["TE_pol"],"neff":result["neff"],"neff_real":result["neff"].real,
        "TM_pol":result.get("TM_pol",1-result["TE_pol"]),"polarization_fraction":result["polarization_fraction"],
        "target_mode":cfg.target_mode,"dominant_comsol_component":result["dominant_comsol_component"],
        "loss":result["loss"],"y":result["y"],"z":result["z"],"Ey":result["Ey"],
        "core_material":cfg.core_material,"top_cladding":cfg.top_cladding,
        "sellmeier_source":cfg.sellmeier_source,"comsol_version":"6.4.0.293",
        "relative_permittivity_tensor":json.dumps(dielectric_expressions(cfg.crystal_cut)),
        "solver_settings":json.dumps({"FEM":"quadratic edge elements","modes_requested":modes_requested,
                                      "shift":result.get("search_shift_used",cfg.search_neff)}),
        "mesh_info":json.dumps(mesh_settings(cfg,mesh_profile)),"pml_energy_fraction":result["pml_fraction"],
        "TE_definition":"MODE-equivalent: int_phys(|COMSOL Ex|^2)/int_phys(|Ex|^2+|Ey|^2)",
        "target_polarization_definition":"TE0: TE_pol; TM0: 1-TE_pol",
        "even_symmetry":result["even_symmetry"],"centered_peak":result["centered_peak"],
        "is_TE0":result["is_TE0"],"is_TM0":result["is_TM0"],
        "candidate_modes_json":json.dumps(candidate_json),
        "search_method":result.get("search_method","primary"),
        "search_shift_used":str(result.get("search_shift_used",cfg.search_neff)),
        "prediction_anchors_nm":np.asarray(result.get("prediction_anchors_nm",[])),
        "overlap_reference_wavelength_nm":result.get("overlap_reference_wavelength_nm",float("nan")),
        "prediction_field_overlap":result.get("prediction_field_overlap",float("nan")),
        "primary_search_error":result.get("primary_search_error",""),
        "tracking_error":result.get("tracking_error",""),
        "tracking_method":result.get("tracking_method",result.get("search_method","primary")),
        "solve_elapsed_s":result.get("solve_elapsed_s",float("nan")),
        "extraction_elapsed_s":result.get("extraction_elapsed_s",float("nan")),
        "attempts_json":json.dumps(result.get("attempts",[])),
    }
    payload["save_elapsed_s_before_compression"]=time.perf_counter()-save_started
    tmp=path.with_suffix(".tmp.mat"); savemat(tmp,payload,do_compression=True); os.replace(tmp,path)


def mesh_settings(cfg: Config, profile: str) -> dict[str, Any]:
    refined = profile == "refined"
    return {
        "profile": profile,
        "core_hmax_nm": cfg.refined_core_max_element_nm if refined else cfg.core_max_element_nm,
        "sidewall_hmax_nm": cfg.refined_sidewall_max_element_nm if refined else cfg.sidewall_max_element_nm,
        "background_hmax_nm": cfg.refined_background_max_element_nm if refined else cfg.background_max_element_nm,
        "pml_layers": cfg.refined_pml_layers if refined else cfg.pml_layers,
        "pml_mesh": "mapped",
    }


def point_complete(path: Path) -> bool:
    if not path.exists(): return False
    try:
        d=loadmat(path); return all(k in d for k in ("neff","TE_pol","Ey","wavelength","film_thickness")) and d["Ey"].size>10
    except Exception: return False


def should_solve_point(path: Path, cfg: Config, force: bool=False) -> bool:
    """Return whether this point must be solved within the current run."""
    return bool(force or not cfg.resume_current_run or not point_complete(path))


def _load_index(path: Path) -> dict[tuple[str,float,float,float,float], dict[str,Any]]:
    if not path.exists():
        return {}
    try:
        frame=pd.read_csv(path).fillna("")
        return {(str(getattr(r,"target_mode","TE0")),float(r.thickness_nm),float(r.top_width_nm),float(r.etch_depth_nm),float(r.wavelength_nm)):r.to_dict()
                for _,r in frame.iterrows()}
    except Exception:
        return {}


def _write_index(path: Path, records: dict[tuple[str,float,float,float,float],dict[str,Any]]) -> None:
    rows=list(records.values())
    if rows:
        pd.DataFrame(rows).sort_values(["thickness_nm","top_width_nm","etch_depth_nm","wavelength_nm"]).to_csv(path,index=False)


def _set_mode_count(model: Any, count: int) -> None:
    if getattr(model,"_lnlt_mode_count",None)==count:
        return
    model.java.study("std1").feature("mode").set("neigs",str(count))
    # Recreate the generated solver sequence so the new eigenvalue count is
    # honored, while geometry and mesh stay intact.
    try:
        model.java.sol().remove("sol1")
    except Exception:
        pass
    model.java.study("std1").createAutoSequences("all")
    model._lnlt_mode_count=count


def _solve_once_and_select(model: Any, cfg: Config, point: ScanPoint, count: int) -> dict[str,Any]:
    _set_mode_count(model,count)
    started=time.perf_counter()
    model.solve()
    solve_elapsed=time.perf_counter()-started
    result=extract_selected_mode(model,cfg,point.wavelength_nm,point.top_width_nm)
    result["solve_elapsed_s"]=solve_elapsed
    return result


def _solve_and_select(model: Any, cfg: Config, point: ScanPoint) -> tuple[dict[str,Any],int]:
    last_error: Exception | None = None
    attempts=[]
    for count in dict.fromkeys((cfg.initial_number_of_modes,cfg.fallback_number_of_modes)):
        try:
            result=_solve_once_and_select(model,cfg,point,count)
            attempts.append({"modes":count,"status":"success","solve_elapsed_s":result["solve_elapsed_s"]})
            result["attempts"]=attempts
            return result,count
        except RuntimeError as exc:
            attempts.append({"modes":count,"status":"failed","error":str(exc)})
            last_error=exc
            if count == cfg.fallback_number_of_modes:
                break
    raise RuntimeError(f"{cfg.target_mode} selection failed after {cfg.fallback_number_of_modes} candidates: {last_error}")


class PredictionUnavailable(RuntimeError):
    """No trustworthy same-geometry, same-branch anchor is available yet."""


def _primary_search_expression(cfg: Config) -> str:
    return ("ne_sell(lam_um)" if cfg.search_neff=="TE_CORE_INDEX" else
            "no_sell(lam_um)" if cfg.search_neff=="MAX_CORE_INDEX" else
            search_expression(cfg.crystal_cut, cfg.target_mode)
            if cfg.search_neff=="TARGET_CORE_INDEX" else str(cfg.search_neff))


def _mode_branch(wavelength_nm: float) -> str:
    return "pump" if wavelength_nm < 800 else "long"


def _cache_prediction_result(cache: dict[float,dict[str,Any]], wavelength_nm: float,
                             result: dict[str,Any], cfg: Config) -> None:
    if not (cfg.predicted_search_fallback or cfg.adjacent_mode_tracking):
        return
    field_value=np.asarray(result["Ey"])
    target_pol=result["TE_pol"] if cfg.target_mode=="TE0" else result.get("TM_pol",1-result["TE_pol"])
    if (np.isfinite(result["neff"]) and np.isfinite(field_value).all()
            and result.get("target_mode",cfg.target_mode)==cfg.target_mode and result["centered_peak"]
            and target_pol>cfg.te_threshold and result["even_symmetry"]>0.9
            and result["pml_fraction"]<0.5):
        cache[wavelength_nm]={"neff":float(np.real(result["neff"])),"field":field_value,
                              "y":np.asarray(result["y"]).reshape(-1),
                              "z":np.asarray(result["z"]).reshape(-1)}


def _cache_resumed_point(cache: dict[float,dict[str,Any]], path: Path, cfg: Config,
                         point: ScanPoint, mesh_profile: str) -> None:
    """Only this run's skipped point, with matching geometry/material/mesh, can anchor a retry."""
    if not (cfg.predicted_search_fallback or cfg.adjacent_mode_tracking):
        return
    try:
        data=loadmat(path)
        checks={"film_thickness":point.thickness_nm*1e-9,"top_width":point.top_width_nm*1e-9,
                "etch_depth":point.etch_depth_nm*1e-9,"wavelength":point.wavelength_nm*1e-9,
                "sidewall_angle":cfg.sidewall_angle_deg}
        if not all(math.isclose(float(np.asarray(data[k]).squeeze()),v,rel_tol=1e-9,abs_tol=1e-15)
                   for k,v in checks.items()):
            return
        if (str(np.asarray(data["core_material"]).squeeze())!=cfg.core_material
                or str(np.asarray(data["top_cladding"]).squeeze())!=cfg.top_cladding
                or json.loads(str(np.asarray(data["mesh_info"]).squeeze()))!=mesh_settings(cfg,mesh_profile)):
            return
        stored_mode=str(np.asarray(data.get("target_mode","TE0")).squeeze())
        if stored_mode!=cfg.target_mode:
            return
        result={k:np.asarray(data[k]).squeeze().item()
                for k in ("neff","centered_peak","TE_pol","even_symmetry")}
        result.update(target_mode=stored_mode,TM_pol=float(np.asarray(data.get("TM_pol",1-result["TE_pol"])).squeeze()),
                      Ey=data["Ey"],y=np.asarray(data["y"]).squeeze(),z=np.asarray(data["z"]).squeeze(),
                      pml_fraction=float(np.asarray(data["pml_energy_fraction"]).squeeze()))
        _cache_prediction_result(cache,point.wavelength_nm,result,cfg)
    except (OSError,ValueError,KeyError,TypeError):
        # Incomplete diagnostics must never become prediction anchors.
        return


def predict_search_center(cfg: Config, wavelength_nm: float,
                          cache: dict[float,dict[str,Any]]) -> tuple[float,list[float],float]:
    xs=sorted(x for x,v in cache.items() if x!=wavelength_nm
              and _mode_branch(x)==_mode_branch(wavelength_nm) and np.isfinite(v["neff"]))
    if not xs:
        raise PredictionUnavailable(f"No same-band successful {cfg.target_mode} anchor for {wavelength_nm:g} nm")
    left=[x for x in xs if x<wavelength_nm]; right=[x for x in xs if x>wavelength_nm]
    anchors=[left[-1],right[0]] if left and right else (xs[-2:] if left else xs[:2])
    predicted=float(cache[anchors[0]]["neff"])
    if len(anchors)==2:
        a,b=anchors
        predicted+=float((cache[b]["neff"]-cache[a]["neff"])*(wavelength_nm-a)/(b-a))
    if not np.isfinite(predicted) or not 1<predicted<max(refractive_indices(cfg.core_material,wavelength_nm)):
        raise PredictionUnavailable(f"Predicted neff={predicted:g} is outside physical bounds at {wavelength_nm:g} nm")
    return predicted,anchors,min(xs,key=lambda x:abs(x-wavelength_nm))


def prediction_field_overlap(reference: dict[str,Any], result: dict[str,Any]) -> float:
    a=np.asarray(reference["field"]); b=np.asarray(result["Ey"])
    if a.shape!=b.shape:
        raise RuntimeError("Prediction reference field grid shape differs from the current mode")
    for axis in ("y","z"):
        old=np.asarray(reference[axis]).reshape(-1); new=np.asarray(result[axis]).reshape(-1)
        if old.shape!=new.shape or not np.allclose(old,new,rtol=0,atol=1e-12):
            raise RuntimeError("Prediction reference field coordinates differ from the current mode")
    denominator=float(np.vdot(a,a).real*np.vdot(b,b).real)
    return float(abs(np.vdot(a,b))**2/denominator) if denominator>0 else float("nan")


def _solve_with_prediction_fallback(model: Any, cfg: Config, point: ScanPoint,
                                    cache: dict[float,dict[str,Any]], *, prediction_only: bool=False,
                                    primary_error: str="") -> tuple[dict[str,Any],int]:
    """Use the original 20/40 search first and predict nshift only after failure."""
    primary_shift=_primary_search_expression(cfg)
    logger=logging.getLogger("lnlt_comsol"); model.parameter("nshift",primary_shift)
    try:
        if not prediction_only:
            try:
                result,modes=_solve_and_select(model,cfg,point)
                result.update(search_method="primary",search_shift_used=primary_shift,primary_search_error="")
                return result,modes
            except Exception as exc:
                if not cfg.predicted_search_fallback:
                    raise
                primary_error=f"{type(exc).__name__}: {exc}"
                logger.warning("PRIMARY FAILED T=%g W=%g D=%g wavelength=%g; predicting nshift: %s",
                               *point.geometry,point.wavelength_nm,primary_error)
        predicted,anchors,reference=predict_search_center(cfg,point.wavelength_nm,cache)
        retry_cfg=replace(cfg,search_neff=predicted,initial_number_of_modes=cfg.fallback_number_of_modes)
        model.parameter("nshift",str(predicted))
        logger.info("PREDICTED RETRY wavelength=%g shift=%.10g anchors=%s reference=%g",
                    point.wavelength_nm,predicted,anchors,reference)
        result,modes=_solve_and_select(model,retry_cfg,point)
        overlap=prediction_field_overlap(cache[reference],result)
        if not np.isfinite(overlap) or overlap<cfg.predicted_field_overlap_min:
            raise RuntimeError(f"Predicted {cfg.target_mode} field overlap {overlap:.6g} < {cfg.predicted_field_overlap_min:g}")
        result.update(search_method="predicted_fallback",search_shift_used=predicted,
                      prediction_anchors_nm=anchors,overlap_reference_wavelength_nm=reference,
                      prediction_field_overlap=overlap,primary_search_error=primary_error)
        return result,modes
    except PredictionUnavailable as exc:
        raise PredictionUnavailable(f"{exc}; primary failure: {primary_error}") from exc
    finally:
        model.parameter("nshift",primary_shift)


def _solve_scan_point(model: Any, cfg: Config, point: ScanPoint,
                      cache: dict[float,dict[str,Any]], *, force_robust: bool,
                      prediction_only: bool=False, primary_error: str="") -> tuple[dict[str,Any],int]:
    """Track adjacent wavelengths with few modes; restore robust search on any failure."""
    logger=logging.getLogger("lnlt_comsol"); original=_primary_search_expression(cfg); tracking_error=""
    if cfg.adjacent_mode_tracking and not force_robust and not prediction_only:
        try:
            predicted,anchors,reference=predict_search_center(cfg,point.wavelength_nm,cache)
            model.parameter("nshift",str(predicted))
            result=_solve_once_and_select(model,cfg,point,cfg.tracking_number_of_modes)
            overlap=prediction_field_overlap(cache[reference],result)
            if not np.isfinite(overlap) or overlap<cfg.tracking_field_overlap_min:
                raise RuntimeError(f"Adjacent-mode overlap={overlap:g}; required {cfg.tracking_field_overlap_min:g}")
            result.update(search_method="adjacent_tracking",tracking_method="adjacent_tracking",
                          search_shift_used=predicted,prediction_anchors_nm=anchors,
                          overlap_reference_wavelength_nm=reference,prediction_field_overlap=overlap,
                          primary_search_error="",attempts=[{"stage":"tracking","modes":cfg.tracking_number_of_modes,
                          "status":"success","solve_elapsed_s":result["solve_elapsed_s"]}])
            return result,cfg.tracking_number_of_modes
        except Exception as exc:
            tracking_error=f"{type(exc).__name__}: {exc}"
            logger.warning("FAST TRACKING FAILED %s; restoring robust path: %s",point,tracking_error)
        finally:
            model.parameter("nshift",original)
    result,count=_solve_with_prediction_fallback(model,cfg,point,cache,
                                                  prediction_only=prediction_only,primary_error=primary_error)
    base_method=result.get("search_method","primary")
    result["tracking_method"]=("robust_reseed" if force_robust else
                               "robust_after_tracking_failure" if tracking_error else base_method)
    result["tracking_error"]=tracking_error
    return result,count


def robust_reseed_required(cfg: Config, wavelength_nm: float) -> bool:
    relevant=[wl for wl in cfg.wavelengths_nm() if _mode_branch(wl)==_mode_branch(wavelength_nm)]
    position=next((i for i,wl in enumerate(relevant) if math.isclose(wl,wavelength_nm,abs_tol=1e-6)),0)
    return position % cfg.robust_reseed_interval==0


def verify_material_domains(model: Any, cfg: Config, point: ScanPoint) -> dict[str,Any]:
    """Sample the solved epsilon_xx in mutually exclusive air/core/BOX domains."""
    x,y,eps=model.evaluate(["x","y","ewfd.epsilonrxx"],inner=[1])
    x=np.asarray(x).real.reshape(-1); y=np.asarray(y).real.reshape(-1); eps=np.asarray(eps).real.reshape(-1)
    samples={"upper_cladding":(cfg.window_width_um/3,0.5),"core":(0,-point.etch_depth_nm/2000),
             "box":(0,-point.thickness_nm/1000-0.5)}
    actual={}
    for name,(px,py) in samples.items():
        index=int(np.argmin((x-px)**2+(y-py)**2)); actual[name]={"x_um":float(x[index]),"y_um":float(y[index]),"epsilon_xx":float(eps[index])}
    _,ne=refractive_indices(cfg.core_material,point.wavelength_nm)
    expected={"upper_cladding":1.0 if cfg.top_cladding=="AIR" else silica_index(point.wavelength_nm)**2,
              "core":ne**2,"box":silica_index(point.wavelength_nm)**2}
    passed=all(abs(actual[k]["epsilon_xx"]-v)<2e-3 for k,v in expected.items())
    result={"point":asdict(point),"actual":actual,"expected_epsilon_xx":expected,"passed":passed}
    if not passed: raise RuntimeError(f"Material-domain epsilon check failed: {result}")
    return result


def run_scan(cfg: Config, force: bool=False, mesh_profile: str="default") -> None:
    cfg.validate(); logger=prepare_run(cfg)
    for wl in cfg.wavelengths_nm(): refractive_indices(cfg.core_material,wl)
    import mph
    client=mph.Client(cores=cfg.threads,version="6.4")
    index_path=cfg.run_dir/"data"/"scan_index.csv"
    records=_load_index(index_path)
    model=None
    try:
        model=build_comsol_model(client,cfg,mesh_profile)
        mode_suffix="" if cfg.target_mode=="TE0" else "_TM0"
        model_path=cfg.run_dir/"model"/f"{cfg.core_material}_xcut_rib_SPDC_PMF{mode_suffix}.mph"
        for geometry in ((t,w,d) for t in cfg.thicknesses_nm() for w in cfg.widths_nm() for d in cfg.etches_nm()):
            t,w,d=geometry
            material_checked=False
            prediction_cache: dict[float,dict[str,Any]]={}
            logger.info("BUILD+MESH T=%g W=%g D=%g nm (%s)",t,w,d,mesh_profile)
            model.parameter("tfilm",f"{t}[nm]"); model.parameter("wtop",f"{w}[nm]"); model.parameter("etch",f"{d}[nm]")
            model.build(); model.mesh()
            queue=[(wl,False,"") for wl in cfg.wavelengths_nm()]
            for wl,prediction_only,primary_error in queue:
                point=ScanPoint(t,w,d,wl); key=(cfg.target_mode,t,w,d,wl); path=mat_path(cfg,t,w,d,wl)
                if not prediction_only and not should_solve_point(path,cfg,force):
                    logger.info("SKIP complete %s",path.name)
                    _cache_resumed_point(prediction_cache,path,cfg,point,mesh_profile)
                    records[key]={"fingerprint":cfg.fingerprint(),"thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
                                  "wavelength_nm":wl,"target_mode":cfg.target_mode,
                                  "status":"skipped","mat":str(path),"error":""}
                    _write_index(index_path,records); continue
                started=datetime.now().isoformat(); logger.info("SOLVE T=%g W=%g D=%g lambda=%g nm",t,w,d,wl)
                point_started=time.perf_counter(); result={}; modes=0
                try:
                    model.parameter("lda0",f"{wl}[nm]")
                    result,modes=_solve_scan_point(model,cfg,point,prediction_cache,
                        force_robust=robust_reseed_required(cfg,wl),prediction_only=prediction_only,
                        primary_error=primary_error)
                    if not material_checked:
                        check=verify_material_domains(model,cfg,point); material_checked=True
                        check_path=cfg.run_dir/"data"/f"material_domain_check_T{t:g}_W{w:g}_D{d:g}.json"
                        check_path.write_text(json.dumps(check,indent=2),encoding="utf-8")
                        logger.info("MATERIAL DOMAINS PASS eps_xx=%s",{k:v["epsilon_xx"] for k,v in check["actual"].items()})
                    save_point(cfg,result,point,mesh_profile,modes,path)
                    _cache_prediction_result(prediction_cache,wl,result,cfg)
                    status="success"; error=""
                    logger.info("OK mode=%s neff=%s pol=%.4f symmetry=%.4f PML=%.3g modes=%d path=%s solve=%.3fs",
                                cfg.target_mode,result["neff"],result["polarization_fraction"],result["even_symmetry"],
                                result["pml_fraction"],modes,result.get("tracking_method"),
                                result.get("solve_elapsed_s",float("nan")))
                except Exception as exc:
                    error=f"{type(exc).__name__}: {exc}"
                    if isinstance(exc,PredictionUnavailable) and not prediction_only:
                        status="deferred"; queue.append((wl,True,error))
                        logger.warning("DEFER %s until same-band anchors are available: %s",point,error)
                    else:
                        status="failed"; logger.error("FAILED %s\n%s",error,traceback.format_exc())
                records[key]={"fingerprint":cfg.fingerprint(),"thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
                              "wavelength_nm":wl,"mesh_profile":mesh_profile,"status":status,"started":started,
                              "finished":datetime.now().isoformat(),"mat":str(path),"error":error,
                              "search_method":result.get("search_method",status),
                              "search_shift_used":result.get("search_shift_used",""),
                              "prediction_anchors_nm":json.dumps(result.get("prediction_anchors_nm",[])),
                              "prediction_field_overlap":result.get("prediction_field_overlap",float("nan")),
                              "primary_search_error":result.get("primary_search_error",primary_error),
                              "tracking_method":result.get("tracking_method",status),"target_mode":cfg.target_mode,
                              "modes_requested":modes if status=="success" else "",
                              "solve_elapsed_s":result.get("solve_elapsed_s",float("nan")),
                              "extraction_elapsed_s":result.get("extraction_elapsed_s",float("nan")),
                              "point_elapsed_s":time.perf_counter()-point_started}
                _write_index(index_path,records)
        model.save(model_path)
    finally:
        try: client.clear()
        except Exception: pass
    postprocess(cfg)

def load_scalar(d: dict[str,Any], key: str) -> float:
    return float(np.asarray(d[key]).squeeze().real)


# %% Two-dimensional SPDC PMF processing, ridge angle and bandwidth
def sum_frequency_wavelength_nm(signal_nm: Any, idler_nm: Any) -> np.ndarray:
    """Return lambda_1 for omega_1=omega_2+omega_3."""
    signal=np.asarray(signal_nm,dtype=float); idler=np.asarray(idler_nm,dtype=float)
    if np.any(signal<=0) or np.any(idler<=0):
        raise ValueError("All wavelengths must be positive.")
    return 1.0/(1.0/signal+1.0/idler)


def _dispersion_interpolator(wavelength_nm: Iterable[float], neff: Iterable[float]) -> PchipInterpolator:
    wl=np.asarray(tuple(wavelength_nm),dtype=float); n=np.asarray(tuple(neff),dtype=float)
    order=np.argsort(wl); wl=wl[order]; n=n[order]
    if len(wl)<2 or len(wl)!=len(np.unique(wl)) or not np.all(np.isfinite(n)):
        raise ValueError("Dispersion samples must contain at least two unique finite points.")
    return PchipInterpolator(wl,n,extrapolate=False)


def _evaluate_checked(interp: PchipInterpolator, wavelength_nm: Any, label: str) -> np.ndarray:
    request=np.asarray(wavelength_nm,dtype=float)
    if request.min()>=interp.x[0]-1e-9 and request.max()<=interp.x[-1]+1e-9:
        request=np.clip(request,interp.x[0],interp.x[-1])
    value=np.asarray(interp(request),dtype=float)
    if not np.all(np.isfinite(value)):
        raise ValueError(f"{label} interpolation would extrapolate: {request.min():g}-{request.max():g} nm")
    return value


def compute_pmf_grid(cfg: Config, signal_sample_nm: Iterable[float], signal_neff: Iterable[float],
                     pump_sample_nm: Iterable[float], pump_neff: Iterable[float]) -> dict[str,Any]:
    """Build the 2-D SPDC mismatch and finite-length phase-matching function."""
    long_interp=_dispersion_interpolator(signal_sample_nm,signal_neff)
    pump_interp=_dispersion_interpolator(pump_sample_nm,pump_neff)
    axis=cfg.heatmap_wavelengths_nm()
    lambda2,lambda3=np.meshgrid(axis,axis,indexing="xy")
    lambda1=sum_frequency_wavelength_nm(lambda2,lambda3)
    n1=_evaluate_checked(pump_interp,lambda1,"Pump"); n2=_evaluate_checked(long_interp,lambda2,"Signal")
    n3=_evaluate_checked(long_interp,lambda3,"Idler")
    beta1=2*np.pi*n1/(lambda1*1e-9); beta2=2*np.pi*n2/(lambda2*1e-9); beta3=2*np.pi*n3/(lambda3*1e-9)
    n1d=float(_evaluate_checked(pump_interp,cfg.pump_center_nm,"Degenerate pump"))
    nd=float(_evaluate_checked(long_interp,cfg.degenerate_wavelength_nm,"Degenerate long wave"))
    beta1d=2*np.pi*n1d/(cfg.pump_center_nm*1e-9)
    betad=2*np.pi*nd/(cfg.degenerate_wavelength_nm*1e-9)
    reference=beta1d-2*betad
    natural=beta1-beta2-beta3
    relative=natural-reference
    abs_relative=np.abs(relative)
    maximum=float(np.max(abs_relative)); mismatch_norm=abs_relative/maximum if maximum else np.zeros_like(abs_relative)
    matching_degree=1.0-mismatch_norm
    length_m=cfg.interaction_length_mm*1e-3
    intensity=np.sinc(relative*length_m/(2*np.pi))**2
    intensity/=float(np.max(intensity))
    return {"axis_nm":axis,"lambda1_nm":lambda1,"lambda2_nm":lambda2,"lambda3_nm":lambda3,
            "n1":n1,"n2":n2,"n3":n3,"beta1_rad_m":beta1,"beta2_rad_m":beta2,"beta3_rad_m":beta3,
            "natural_mismatch_rad_m":natural,"qpm_mismatch_rad_m":relative,"relative_mismatch_rad_m":relative,
            "abs_relative_mismatch_rad_m":abs_relative,"normalized_mismatch":mismatch_norm,
            "normalized_phase_matching_degree":matching_degree,
            "phase_matching_intensity":intensity,"reference_mismatch_rad_m":reference,
            "qpm_period_um":2*np.pi/abs(reference)*1e6,"long_interp":long_interp,"pump_interp":pump_interp}


def _roots_1d(x: np.ndarray, y: np.ndarray) -> list[float]:
    roots=[]
    for i in range(len(x)-1):
        if y[i]==0: roots.append(float(x[i]))
        if y[i]*y[i+1]<0:
            roots.append(float(x[i]-y[i]*(x[i+1]-x[i])/(y[i+1]-y[i])))
    if y[-1]==0: roots.append(float(x[-1]))
    return roots


def fit_phase_matching_ridge(axis_nm: np.ndarray, relative_mismatch: np.ndarray,
                             center_nm: float) -> dict[str,Any]:
    points=[]
    for ix,x in enumerate(axis_nm):
        candidates=_roots_1d(axis_nm,relative_mismatch[:,ix])
        if candidates:
            expected=2*center_nm-x
            points.append((float(x),min(candidates,key=lambda value:abs(value-expected))))
    if len(points)<3:
        raise ValueError("Could not extract a connected phase-matching ridge.")
    pts=np.asarray(points); centered=pts-pts.mean(axis=0)
    _,_,vh=np.linalg.svd(centered,full_matrices=False); direction=vh[0]
    if direction[0]<0: direction=-direction
    angle=float(np.degrees(np.arctan2(direction[1],direction[0]))%180)
    normal=np.array([-direction[1],direction[0]])
    rms=float(np.sqrt(np.mean((centered@normal)**2)))
    acute_x=min(angle,180-angle); acute_y=abs(90-angle)
    return {"points_nm":pts,"direction":direction,"normal":normal,"angle_from_positive_signal_axis_deg":angle,
            "acute_angle_to_signal_axis_deg":float(acute_x),"acute_angle_to_idler_axis_deg":float(acute_y),
            "orthogonal_rms_nm":rms}


def _half_max_width(x: np.ndarray, intensity: np.ndarray, preferred_center: float) -> float:
    x=np.asarray(x,dtype=float); y=np.asarray(intensity,dtype=float)
    valid=np.isfinite(x)&np.isfinite(y); x=x[valid]; y=y[valid]
    if len(x)<3: return float("nan")
    order=np.argsort(x); x=x[order]; y=y[order]
    peaks=np.flatnonzero(y==np.max(y)); peak=int(peaks[np.argmin(abs(x[peaks]-preferred_center))])
    left=peak
    while left>0 and y[left]>=.5: left-=1
    right=peak
    while right<len(y)-1 and y[right]>=.5: right+=1
    if left==0 or right==len(y)-1: return float("nan")
    xl=float(np.interp(.5,[y[left],y[left+1]],[x[left],x[left+1]]))
    xr=float(np.interp(.5,[y[right],y[right-1]],[x[right],x[right-1]]))
    return xr-xl


def _intensity_at(cfg: Config, grid: dict[str,Any], lambda2_nm: np.ndarray, lambda3_nm: np.ndarray) -> np.ndarray:
    relative=_relative_mismatch_at(grid,lambda2_nm,lambda3_nm)
    return np.sinc(relative*(cfg.interaction_length_mm*1e-3)/(2*np.pi))**2


def _relative_mismatch_at(grid: dict[str,Any], lambda2_nm: Any, lambda3_nm: Any) -> np.ndarray:
    lambda2_nm=np.asarray(lambda2_nm,dtype=float); lambda3_nm=np.asarray(lambda3_nm,dtype=float)
    lambda1=sum_frequency_wavelength_nm(lambda2_nm,lambda3_nm)
    n1=_evaluate_checked(grid["pump_interp"],lambda1,"Pump")
    n2=_evaluate_checked(grid["long_interp"],lambda2_nm,"Signal")
    n3=_evaluate_checked(grid["long_interp"],lambda3_nm,"Idler")
    natural=2*np.pi*n1/(lambda1*1e-9)-2*np.pi*n2/(lambda2_nm*1e-9)-2*np.pi*n3/(lambda3_nm*1e-9)
    return natural-grid["reference_mismatch_rad_m"]


def fixed_pump_bandwidth(cfg: Config, grid: dict[str,Any]) -> tuple[dict[str,Any],dict[str,np.ndarray]]:
    """Measure central-lobe FWHM on the exact pump line without extra COMSOL solves."""
    axis=grid["axis_nm"]; step=cfg.bandwidth_coarse_step_nm
    signal=np.asarray(cfg._inclusive_range(float(axis[0]),float(axis[-1]),step))
    idler=1.0/(1.0/cfg.pump_wavelength_nm-1.0/signal)
    valid=(idler>=axis[0])&(idler<=axis[-1]); signal=signal[valid]; idler=idler[valid]
    relative=_relative_mismatch_at(grid,signal,idler)
    intensity=np.sinc(relative*(cfg.interaction_length_mm*1e-3)/(2*np.pi))**2
    argument_half=brentq(lambda z:(np.sin(z)/z)**2-cfg.pmf_half_max_level,1e-12,np.pi-1e-12)
    threshold=2*argument_half/(cfg.interaction_length_mm*1e-3)
    residual_samples=intensity-cfg.pmf_half_max_level
    center=int(np.argmin(abs(signal-cfg.degenerate_wavelength_nm)))

    def refine(a: float,b: float) -> float:
        def residual(x: float) -> float:
            y=1.0/(1.0/cfg.pump_wavelength_nm-1.0/x)
            mismatch=float(_relative_mismatch_at(grid,x,y))
            return float(np.sinc(mismatch*(cfg.interaction_length_mm*1e-3)/(2*np.pi))**2-cfg.pmf_half_max_level)
        return float(brentq(residual,a,b,xtol=cfg.bandwidth_root_tolerance_nm))

    left=None
    for j in range(center-1,-1,-1):
        if residual_samples[j]<=0<=residual_samples[j+1]:
            left=refine(float(signal[j]),float(signal[j+1])); break
    right=None
    for j in range(center,len(signal)-1):
        if residual_samples[j]>=0>=residual_samples[j+1]:
            right=refine(float(signal[j]),float(signal[j+1])); break

    left_idler=float(1/(1/cfg.pump_wavelength_nm-1/left)) if left is not None else float("nan")
    right_idler=float(1/(1/cfg.pump_wavelength_nm-1/right)) if right is not None else float("nan")
    if left is not None and right is not None: status="complete"
    elif left is None and right is None: status="range_insufficient"
    elif left is None: status="left_missing"
    else: status="right_missing"
    missing_directions={"complete":"","range_insufficient":"left,right",
                        "left_missing":"left","right_missing":"right"}[status]
    left_delta=float(_relative_mismatch_at(grid,left,left_idler)) if left is not None else float("nan")
    right_delta=float(_relative_mismatch_at(grid,right,right_idler)) if right is not None else float("nan")
    metrics={"pmf_bandwidth_status":status,"pmf_bandwidth_detection_method":"first_half_max_bracket_brentq",
        "pmf_half_max_level":cfg.pmf_half_max_level,
        "missing_crossing_directions":missing_directions,
        "half_max_abs_delta_k_rad_m":float(threshold),"left_signal_nm":left if left is not None else float("nan"),
        "left_idler_nm":left_idler,"left_intersection_pmf":cfg.pmf_half_max_level if left is not None else float("nan"),
        "left_intersection_delta_k_rad_m":left_delta,
        "right_signal_nm":right if right is not None else float("nan"),"right_idler_nm":right_idler,
        "right_intersection_pmf":cfg.pmf_half_max_level if right is not None else float("nan"),
        "right_intersection_delta_k_rad_m":right_delta,
        "spdc_signal_fwhm_nm":float(right-left) if status=="complete" else float("nan"),
        "spdc_idler_fwhm_nm":float(left_idler-right_idler) if status=="complete" else float("nan"),
        "pump_line_left_boundary_intensity":float(intensity[0]),
        "pump_line_right_boundary_intensity":float(intensity[-1]),
        "pump_line_left_boundary_abs_delta_k_rad_m":float(abs(relative[0])),
        "pump_line_right_boundary_abs_delta_k_rad_m":float(abs(relative[-1]))}
    intersections=np.asarray([[left,left_idler],[right,right_idler]],dtype=float) if status=="complete" else np.empty((0,2))
    return metrics,{"signal_nm":signal,"idler_nm":idler,"intensity":intensity,
                    "relative_mismatch_rad_m":relative,"intersections_nm":intersections}


def pmf_bandwidth_metrics(cfg: Config, grid: dict[str,Any], ridge: dict[str,Any]) -> tuple[dict[str,Any],dict[str,np.ndarray]]:
    axis=grid["axis_nm"]; center=cfg.degenerate_wavelength_nm
    # FWHM extraction uses a finer post-processing line than the displayed
    # heatmap; it never launches additional COMSOL solves.
    metric_step=min(0.025,cfg.heatmap_step_nm/20)
    fine=np.linspace(axis[0],axis[-1],int(round((axis[-1]-axis[0])/metric_step))+1)
    center_line=np.full_like(fine,center)
    fixed_idler=_half_max_width(fine,_intensity_at(cfg,grid,fine,center_line),center)
    fixed_signal=_half_max_width(fine,_intensity_at(cfg,grid,center_line,fine),center)
    extent=max(center-axis[0],axis[-1]-center)*np.sqrt(2)
    s=np.linspace(-extent,extent,int(round(2*extent/metric_step))+1)
    normal=ridge["normal"]; l2=center+s*normal[0]; l3=center+s*normal[1]
    inside=(l2>=axis[0])&(l2<=axis[-1])&(l3>=axis[0])&(l3<=axis[-1])
    normal_intensity=_intensity_at(cfg,grid,l2[inside],l3[inside])
    normal_width=_half_max_width(s[inside],normal_intensity,0.0)
    metrics={"normal_fwhm_nm":float(normal_width),"fixed_idler_signal_fwhm_nm":float(fixed_idler),
             "fixed_signal_idler_fwhm_nm":float(fixed_signal),
             "half_max_abs_delta_k_rad_per_mm":float(2.7831147565/cfg.interaction_length_mm)}
    pump_metrics,pump_curve=fixed_pump_bandwidth(cfg,grid); metrics.update(pump_metrics)
    return metrics,pump_curve


def _geometry_stem(cfg: Config, t: float, w: float, d: float) -> str:
    mode_suffix="" if cfg.target_mode=="TE0" else "_TM0"
    return f"{cfg.core_material}_T{t:g}_W{w:g}_D{d:g}_A{cfg.sidewall_angle_deg:g}{mode_suffix}"


def _plot_heatmap(path: Path, axis: np.ndarray, values: np.ndarray, title: str, colorbar: str,
                  pump_curve: dict[str,np.ndarray] | None=None,
                  mark_intersections: bool=False) -> None:
    fig,ax=plt.subplots(figsize=(7.2,6.1))
    im=ax.imshow(values,extent=[axis[0],axis[-1],axis[0],axis[-1]],origin="lower",aspect="equal",cmap="viridis",vmin=0,vmax=1)
    if pump_curve is not None:
        ax.plot(pump_curve["signal_nm"],pump_curve["idler_nm"],color="#FFFFFF",alpha=1.0,
                linestyle=(0,(9,7)),lw=1.5,zorder=4,label="655 nm pump-energy line")
        points=pump_curve["intersections_nm"]
        if mark_intersections and len(points): ax.scatter(points[:,0],points[:,1],s=55,c="white",edgecolors="red",zorder=5,label="PMF half-max intersections")
    if pump_curve is not None: ax.legend()
    ax.set_xlabel("Signal wavelength λ₂ (nm)"); ax.set_ylabel("Idler wavelength λ₃ (nm)"); ax.set_title(title)
    fig.colorbar(im,ax=ax,label=colorbar); fig.tight_layout(); fig.savefig(path,dpi=220); plt.close(fig)


def _plot_pump_line_cut(path: Path, cfg: Config, curve: dict[str,np.ndarray], metrics: dict[str,Any]) -> None:
    fig,ax=plt.subplots(figsize=(7.2,4.6)); x=curve["signal_nm"]; y=curve["intensity"]
    ax.plot(x,y,color="blue",lw=1.8,label="PMF on 655 nm pump line")
    ax.axhline(cfg.pmf_half_max_level,color="black",ls="--",lw=1,label="half maximum")
    points=curve["intersections_nm"]
    if len(points):
        ax.scatter(points[:,0],[cfg.pmf_half_max_level]*2,c="red",zorder=4)
        ax.axvspan(points[0,0],points[1,0],color="orange",alpha=.15,
                   label=f"FWHM = {metrics['spdc_signal_fwhm_nm']:.4f} nm")
    else: ax.text(.5,.08,f"Bandwidth status: {metrics['pmf_bandwidth_status']}",transform=ax.transAxes,ha="center",color="red")
    ax.set_xlabel("Signal wavelength λ₂ (nm)"); ax.set_ylabel("Normalized PMF intensity")
    ax.set_ylim(-.02,1.04); ax.grid(alpha=.25); ax.legend(); fig.tight_layout(); fig.savefig(path,dpi=220); plt.close(fig)


def _plot_mode_preview(cfg: Config) -> None:
    all_mats=sorted((cfg.run_dir/"data"/"mat").glob("*.mat"))
    mats=([p for p in all_mats if "_TM0" in p.stem] if cfg.target_mode=="TM0"
          else [p for p in all_mats if "_TM0" not in p.stem])
    figdir=cfg.run_dir/"data"/"figures"
    if not mats: return
    preferred=mat_path(cfg,800,3000,550,cfg.degenerate_wavelength_nm)
    data=loadmat(preferred if preferred.exists() else mats[0]); field=np.abs(data["Ey"])
    y=data["y"].squeeze()*1e6; z=data["z"].squeeze()*1e6
    plt.imshow(field,extent=[y.min(),y.max(),z.min(),z.max()],origin="lower",aspect="auto",cmap="magma")
    component="Ex" if cfg.target_mode=="TE0" else "Ey"
    plt.colorbar(label=f"|{component}| (a.u.)"); plt.xlabel("COMSOL x (μm)"); plt.ylabel("COMSOL y (μm)")
    plt.tight_layout(); plt.savefig(figdir/"mode_field_preview.png",dpi=180); plt.close()


def postprocess(cfg: Config) -> pd.DataFrame:
    cfg.validate(); prepare_run(cfg); figdir=cfg.run_dir/"data"/"figures"
    if cfg.run_profile=="preview":
        _plot_mode_preview(cfg); return pd.DataFrame()
    summaries=[]; grids=[]
    for t in cfg.thicknesses_nm():
        for w in cfg.widths_nm():
            for d in cfg.etches_nm():
                signal_wl=cfg.signal_wavelengths_nm(); pump_wl=cfg.pump_wavelengths_nm()
                paths=[mat_path(cfg,t,w,d,wl) for wl in (*signal_wl,*pump_wl)]
                missing=[str(path) for path in paths if not point_complete(path)]
                if missing: raise FileNotFoundError(f"Cannot postprocess T={t:g}, W={w:g}, D={d:g}; missing {len(missing)} MAT files, first: {missing[0]}")
                signal_n=[load_scalar(loadmat(mat_path(cfg,t,w,d,wl)),"neff_real") for wl in signal_wl]
                pump_n=[load_scalar(loadmat(mat_path(cfg,t,w,d,wl)),"neff_real") for wl in pump_wl]
                grid=compute_pmf_grid(cfg,signal_wl,signal_n,pump_wl,pump_n)
                ridge=fit_phase_matching_ridge(grid["axis_nm"],grid["relative_mismatch_rad_m"],cfg.degenerate_wavelength_nm)
                bandwidth,pump_curve=pmf_bandwidth_metrics(cfg,grid,ridge); stem=_geometry_stem(cfg,t,w,d)
                summary={"core_material":cfg.core_material,"top_cladding":cfg.top_cladding,"target_mode":cfg.target_mode,"film_thickness_nm":t,
                         "top_width_nm":w,"etch_depth_nm":d,"degenerate_wavelength_nm":cfg.degenerate_wavelength_nm,
                         "pump_wavelength_nm":cfg.pump_wavelength_nm,"interaction_length_mm":cfg.interaction_length_mm,
                         "qpm_period_um":grid["qpm_period_um"],**{k:v for k,v in ridge.items() if k not in {"points_nm","direction","normal"}},**bandwidth}
                summaries.append(summary)
                flat=pd.DataFrame({"lambda1_pump_nm":grid["lambda1_nm"].ravel(),"lambda2_signal_nm":grid["lambda2_nm"].ravel(),
                    "lambda3_idler_nm":grid["lambda3_nm"].ravel(),"neff1":grid["n1"].ravel(),"neff2":grid["n2"].ravel(),
                    "neff3":grid["n3"].ravel(),"beta1_rad_m":grid["beta1_rad_m"].ravel(),"beta2_rad_m":grid["beta2_rad_m"].ravel(),
                    "beta3_rad_m":grid["beta3_rad_m"].ravel(),"natural_mismatch_rad_m":grid["natural_mismatch_rad_m"].ravel(),
                    "qpm_mismatch_rad_m":grid["qpm_mismatch_rad_m"].ravel(),"relative_mismatch_rad_m":grid["relative_mismatch_rad_m"].ravel(),
                    "abs_relative_mismatch_rad_m":grid["abs_relative_mismatch_rad_m"].ravel(),"normalized_mismatch":grid["normalized_mismatch"].ravel(),
                    "normalized_phase_matching_degree":grid["normalized_phase_matching_degree"].ravel(),
                    "normalized_phase_matching_intensity":grid["phase_matching_intensity"].ravel(),"qpm_period_um":grid["qpm_period_um"]})
                flat.insert(0,"geometry",stem); flat.to_csv(cfg.run_dir/"data"/f"pmf_grid_{stem}.csv",index=False); grids.append(flat)
                (figdir/f"normalized_mismatch_{stem}.png").unlink(missing_ok=True)
                _plot_heatmap(figdir/f"pmf_matching_degree_{stem}.png",grid["axis_nm"],grid["normalized_phase_matching_degree"],
                              f"Normalized SPDC phase-matching degree: {stem}","1 - |delta k| / max(|delta k|)",pump_curve)
                _plot_heatmap(figdir/f"pmf_intensity_{stem}.png",grid["axis_nm"],grid["phase_matching_intensity"],
                              f"Normalized SPDC PMF intensity: {stem}","sinc squared(delta k L / 2)",pump_curve,True)
                _plot_pump_line_cut(figdir/f"pmf_fixed_pump_cut_{stem}.png",cfg,pump_curve,bandwidth)
                pd.DataFrame({"signal_nm":pump_curve["signal_nm"],"idler_nm":pump_curve["idler_nm"],
                    "pmf_intensity":pump_curve["intensity"],"relative_mismatch_rad_m":pump_curve["relative_mismatch_rad_m"]
                    }).to_csv(cfg.run_dir/"data"/f"pmf_fixed_pump_cut_{stem}.csv",index=False)
    summary_df=pd.DataFrame(summaries); summary_df.to_csv(cfg.run_dir/"data"/"pmf_bandwidth_summary.csv",index=False)
    if grids: pd.concat(grids,ignore_index=True).to_csv(cfg.run_dir/"data"/"pmf_all_geometries.csv",index=False)
    payload={"definitions":{"wave1":"pump","wave2":"signal","wave3":"idler",
        "target_mode":cfg.target_mode,
        "normalized_intensity":"finite-length uniform-QPM phase-matching function, not absolute conversion efficiency"},
        "config":serializable_config(cfg),"geometries":summaries}
    (cfg.run_dir/"data"/"pmf_metrics.json").write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
    _plot_mode_preview(cfg); return summary_df

# %% Output validation and mesh convergence
def validate_outputs(cfg: Config, mph_reopen_ok: bool | None = None) -> dict[str,Any]:
    """Re-read all expected MAT files and issue the final validation report."""
    required=("film_thickness","top_width","bottom_width","etch_depth","sidewall_angle","wavelength","mode_index","TE_pol","neff","neff_real","loss","y","z","Ey")
    rows=[]; missing=[]
    for point in scan_points(cfg):
        p=mat_path(cfg,*point.geometry,point.wavelength_nm)
        if not p.exists(): missing.append(str(p)); continue
        data=loadmat(p); absent=[k for k in required if k not in data]
        if absent or data["Ey"].size<100: missing.append(f"{p}: {absent or 'empty field'}"); continue
        stored_mode=str(np.asarray(data.get("target_mode","TE0")).squeeze())
        if stored_mode!=cfg.target_mode:
            missing.append(f"{p}: target_mode={stored_mode}, expected {cfg.target_mode}"); continue
        rows.append({"target_mode":cfg.target_mode,"thickness_nm":point.thickness_nm,"top_width_nm":point.top_width_nm,"etch_depth_nm":point.etch_depth_nm,
                     "wavelength_nm":point.wavelength_nm,"neff":load_scalar(data,"neff_real"),"TE_pol":load_scalar(data,"TE_pol"),
                     "pml_fraction":load_scalar(data,"pml_energy_fraction")})
    points=pd.DataFrame(rows); points.to_csv(cfg.run_dir/"data"/"validated_mat_points.csv",index=False)
    conv_path=cfg.run_dir/"data"/"mesh_convergence.json"
    conv=json.loads(conv_path.read_text(encoding="utf-8")) if conv_path.exists() else {"passed":False,"delta_neff_max":float("nan"),"qpm_delta_um":float("nan")}
    summary_path=cfg.run_dir/"data"/"pmf_bandwidth_summary.csv"
    summary=pd.read_csv(summary_path) if summary_path.exists() else pd.DataFrame()
    material_checks=[]
    for path in (cfg.run_dir/"data").glob("material_domain_check*.json"):
        material_checks.append(json.loads(path.read_text(encoding="utf-8")))
    material_ok=bool(material_checks) and all(item.get("passed") for item in material_checks)
    expected=len(scan_points(cfg)); expected_geometries=len(cfg.thicknesses_nm())*len(cfg.widths_nm())*len(cfg.etches_nm())
    index_path=cfg.run_dir/"data"/"scan_index.csv"
    index=pd.read_csv(index_path) if index_path.exists() else pd.DataFrame()
    if not index.empty:
        index_modes=index["target_mode"].fillna("TE0") if "target_mode" in index else pd.Series("TE0",index=index.index)
        index=index[index_modes.eq(cfg.target_mode)]
    index_ok=(len(index)==expected and "status" in index and not index["status"].eq("failed").any()
              and not index.duplicated(["thickness_nm","top_width_nm","etch_depth_nm","wavelength_nm"]).any())
    grid_rows_expected=len(cfg.heatmap_wavelengths_nm())**2
    grids_ok=cfg.run_profile=="preview" or (len(summary)==expected_geometries and all(
        (lambda p: p.exists() and len(pd.read_csv(p))==grid_rows_expected)(
            cfg.run_dir/"data"/f"pmf_grid_{_geometry_stem(cfg,t,w,d)}.csv")
        for t in cfg.thicknesses_nm() for w in cfg.widths_nm() for d in cfg.etches_nm()))
    ok=(not missing and len(rows)==expected and index_ok and grids_ok and bool(conv.get("passed"))
        and material_ok and mph_reopen_ok is not False)
    bandwidth_ok=cfg.run_profile=="preview" or (len(summary)==expected_geometries and all(summary.get("pmf_bandwidth_status",pd.Series(dtype=str))=="complete"))
    ok=ok and bandwidth_ok
    report=["# COMSOL 6.4 SPDC PMF validation report","",f"- Result: **{'PASS' if ok else 'FAIL'}**",f"- Material / upper cladding / mode: {cfg.core_material} / {cfg.top_cladding} / {cfg.target_mode}",
            f"- MAT integrity: {len(rows)}/{expected}",f"- PMF geometry grids: {len(summary)}/{expected_geometries}; {grid_rows_expected} rows each",
            f"- Scan index complete with no failed/duplicate points: {index_ok}",
            f"- Fixed-pump bandwidth status: {summary.get('pmf_bandwidth_status',pd.Series(dtype=str)).tolist()}",
            f"- Saved MPH reopened with problems=[]: {mph_reopen_ok}",f"- Mesh max |Delta neff|: {conv.get('delta_neff_max')} (limit 5e-4)",
            f"- Mesh QPM period difference: {conv.get('qpm_delta_um')} um (limit 0.02 um)","",f"Missing/invalid: {missing or 'none'}"]
    report.insert(8,f"- Air/LT/SiO2 actual epsilon domain check: {material_ok}")
    (cfg.run_dir/"data"/"validation_report.md").write_text("\n".join(report),encoding="utf-8")
    return {"passed":ok,"rows":len(rows),"expected":expected,"missing":missing,"scan_index_ok":index_ok,
            "mesh":conv,"material_domains":material_ok,
            "pmf_grids_ok":grids_ok,"bandwidth_ok":bandwidth_ok,"mph_reopen_ok":mph_reopen_ok}


def convergence_run(cfg: Config) -> dict[str,Any]:
    cfg.validate(); prepare_run(cfg)
    import mph
    client=mph.Client(cores=cfg.threads,version="6.4")
    values: dict[str,Any]={}
    try:
        for label in ("default","refined"):
            m=build_comsol_model(client,cfg,label); m.parameter("tfilm","800[nm]"); m.parameter("wtop","3000[nm]"); m.parameter("etch","550[nm]")
            m.build(); m.mesh(); neffs={}; mode_indices={}; diagnostics={}
            check_wavelengths=(min(cfg.pump_wavelengths_nm()),cfg.pump_center_nm,max(cfg.pump_wavelengths_nm()),
                               min(cfg.signal_wavelengths_nm()),cfg.degenerate_wavelength_nm,max(cfg.signal_wavelengths_nm()))
            prediction_cache: dict[float,dict[str,Any]]={}
            queue=[(wl,False,"") for wl in check_wavelengths]
            for wl,prediction_only,primary_error in queue:
                point=ScanPoint(800,3000,550,wl); m.parameter("lda0",f"{wl}[nm]")
                try:
                    r,modes=_solve_scan_point(m,cfg,point,prediction_cache,force_robust=True,
                                              prediction_only=prediction_only,primary_error=primary_error)
                except PredictionUnavailable as exc:
                    if prediction_only:
                        raise
                    queue.append((wl,True,str(exc)))
                    logging.getLogger("lnlt_comsol").info("DEFER convergence %s wavelength=%g once",label,wl)
                    continue
                path=mat_path(cfg,800,3000,550,wl,f"_{label}")
                key=f"{wl:g}"; save_point(cfg,r,point,label,modes,path); neffs[key]=r["neff"].real; mode_indices[key]=r["mode_index"]
                _cache_prediction_result(prediction_cache,wl,r,cfg)
                diagnostics[key]={"TE_pol":r["TE_pol"],"TM_pol":r["TM_pol"],
                                           "polarization_fraction":r["polarization_fraction"],"target_mode":cfg.target_mode,
                                           "even_symmetry":r["even_symmetry"],
                                           "centered_peak":bool(r["centered_peak"]),"pml_fraction":r["pml_fraction"]}
            pump_key=f"{cfg.pump_center_nm:g}"; long_key=f"{cfg.degenerate_wavelength_nm:g}"
            beta_p=2*np.pi*neffs[pump_key]/(cfg.pump_center_nm*1e-9)
            beta_l=2*np.pi*neffs[long_key]/(cfg.degenerate_wavelength_nm*1e-9)
            period=2*np.pi/abs(beta_p-2*beta_l)*1e6
            values[label]={"neff":neffs,"mode_index":mode_indices,"diagnostics":diagnostics,"qpm_period_um":period}
            mode_suffix="" if cfg.target_mode=="TE0" else "_TM0"
            m.save(cfg.run_dir/"model"/f"{cfg.core_material}_xcut_rib_SPDC_PMF{mode_suffix}_{label}.mph"); client.remove(m)
    finally:
        try: client.clear()
        except Exception: pass
    keys=tuple(values["default"]["neff"])
    deltas={wl:abs(values["refined"]["neff"][wl]-values["default"]["neff"][wl]) for wl in keys}
    values["delta_neff"]=deltas; values["delta_neff_max"]=max(deltas.values())
    values["qpm_delta_um"]=abs(values["refined"]["qpm_period_um"]-values["default"]["qpm_period_um"])
    # Eigenpair list indices are not physical mode labels and may reorder when
    # the mesh changes. Confirm the same selected fundamental mode by field diagnostics and neff.
    values["same_target_mode"]=all(
        values[p]["diagnostics"][wl]["polarization_fraction"]>cfg.te_threshold
        and values[p]["diagnostics"][wl]["even_symmetry"]>0.9
        and values[p]["diagnostics"][wl]["centered_peak"]
        and values[p]["diagnostics"][wl]["pml_fraction"]<0.5
        for p in ("default","refined") for wl in keys
    )
    values["passed"]=values["same_target_mode"] and values["delta_neff_max"]<=5e-4 and values["qpm_delta_um"]<=0.02
    (cfg.run_dir/"data"/"mesh_convergence.json").write_text(json.dumps(values,indent=2),encoding="utf-8")
    return values

# %% Command-line entry point
def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("command",choices=("run","postprocess","convergence","validate","sellmeier"),nargs="?",default="run"); p.add_argument("--force",action="store_true"); args=p.parse_args()
    cfg=Config()
    if args.command=="run": run_scan(cfg,args.force)
    elif args.command=="postprocess": postprocess(cfg)
    elif args.command=="convergence": print(convergence_run(cfg))
    elif args.command=="validate": print(validate_outputs(cfg))
    else:
        for wl in cfg.wavelengths_nm(): print(wl,refractive_indices(cfg.core_material,wl))

if __name__ == "__main__":
    main()
