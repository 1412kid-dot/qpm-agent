"""COMSOL 6.4 automation for non-degenerate SFG QPM-period contours.

The COMSOL model owns the wavelength-dependent material functions. Python
changes only ``lda0``; no per-wavelength refractive-index override is used.
The convention is omega_1 = omega_2 + omega_3: wave 1 is the generated SFG
wave, wave 2 is the signal, and wave 3 is the idler.
"""
from __future__ import annotations

import argparse
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

# %% Configuration and material references
# ----------------------------- USER CONFIGURATION ---------------------------
CORE_MATERIAL = "LT"                 # "LN" or "LT"; rib and slab are locked
TARGET_MODE = "TE0"                  # "TE0" or "TM0"; one target per run
RUN_PROFILE = "full"                 # "preview", "mini", or "full"
RUN_NAME = "LT_AIR_SFG_Period_T800_W3000_D550_A67_expansion"
OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs" / "legacy"
RESUME_CURRENT_RUN = True
SCAN_MODE = "2d"                    # "2d", "signal_1d", or "idler_1d"

FILM_THICKNESSES_NM = (800.0,)
TOP_WIDTHS_NM = (3000.0,)             # add values to enable width scans
ETCH_DEPTHS_NM = (550.0,)
SIDEWALL_ANGLE_DEG = 67.0
SIGNAL_MIN_NM = 1020.0
SIGNAL_MAX_NM = 1070.0
IDLER_MIN_NM = 1575.0
IDLER_MAX_NM = 1625.0
SFG_SCAN_MIN_NM = 619.0
SFG_SCAN_MAX_NM = 647.0
COMSOL_WAVELENGTH_STEP_NM = 2.0
PLOT_WAVELENGTH_STEP_NM = 0.5
CONTOUR_PERIOD_STEP_UM = 0.02
ONE_D_SCAN_STEP_NM = 10.0
FIXED_SIGNAL_WAVELENGTH_NM = None    # required only for SCAN_MODE="idler_1d"
FIXED_IDLER_WAVELENGTH_NM = None     # required only for SCAN_MODE="signal_1d"
INITIAL_NUMBER_OF_MODES = 20
FALLBACK_NUMBER_OF_MODES = 40
ENABLE_ADJACENT_MODE_TRACKING = True
TRACKING_NUMBER_OF_MODES = 6
TRACKING_FIELD_OVERLAP_MIN = 0.95
ROBUST_RESEED_INTERVAL = 4
# 原搜索成功时不启用预测；只有原有候选模搜索全部失败后才重试。
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
    scan_mode: str = SCAN_MODE
    film_thicknesses_nm: tuple[float, ...] = FILM_THICKNESSES_NM
    top_widths_nm: tuple[float, ...] = TOP_WIDTHS_NM
    etch_depths_nm: tuple[float, ...] = ETCH_DEPTHS_NM
    sidewall_angle_deg: float = SIDEWALL_ANGLE_DEG
    signal_min_nm: float = SIGNAL_MIN_NM
    signal_max_nm: float = SIGNAL_MAX_NM
    idler_min_nm: float = IDLER_MIN_NM
    idler_max_nm: float = IDLER_MAX_NM
    sfg_scan_min_nm: float = SFG_SCAN_MIN_NM
    sfg_scan_max_nm: float = SFG_SCAN_MAX_NM
    comsol_wavelength_step_nm: float = COMSOL_WAVELENGTH_STEP_NM
    plot_wavelength_step_nm: float = PLOT_WAVELENGTH_STEP_NM
    contour_period_step_um: float = CONTOUR_PERIOD_STEP_UM
    one_d_scan_step_nm: float = ONE_D_SCAN_STEP_NM
    fixed_signal_wavelength_nm: float | None = FIXED_SIGNAL_WAVELENGTH_NM
    fixed_idler_wavelength_nm: float | None = FIXED_IDLER_WAVELENGTH_NM
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
        if min(self.initial_number_of_modes,self.fallback_number_of_modes,
               self.tracking_number_of_modes,self.robust_reseed_interval) < 1:
            raise ValueError("Mode counts and robust reseed interval must be positive.")
        if not 0 < self.tracking_field_overlap_min <= 1:
            raise ValueError("Tracking field-overlap threshold must lie in (0, 1].")
        if self.core_material not in {"LN", "LT"}:
            raise ValueError("CORE_MATERIAL must be 'LN' or 'LT'.")
        if self.run_profile not in {"preview", "mini", "full"}:
            raise ValueError("RUN_PROFILE must be preview, mini, or full.")
        if self.scan_mode not in {"2d","signal_1d","idler_1d"}:
            raise ValueError("SCAN_MODE must be 2d, signal_1d, or idler_1d.")
        if self.top_cladding not in {"SIO2", "AIR"}:
            raise ValueError("TOP_CLADDING must be 'SIO2' or 'AIR'.")
        if not 0 < self.predicted_field_overlap_min <= 1:
            raise ValueError("Predicted-search field-overlap threshold must lie in (0, 1].")
        if max(self.etch_depths_nm) > min(self.film_thicknesses_nm):
            raise ValueError("Etch depth cannot exceed film thickness.")
        if not self.top_widths_nm or not self.etch_depths_nm or not self.film_thicknesses_nm:
            raise ValueError("Thickness, width and etch lists cannot be empty.")
        if min(self.comsol_wavelength_step_nm,self.plot_wavelength_step_nm,
               self.contour_period_step_um,self.one_d_scan_step_nm) <= 0:
            raise ValueError("Wavelength and contour steps must be positive.")
        if self.scan_mode=="2d":
            if not (self.signal_min_nm<self.signal_max_nm and self.idler_min_nm<self.idler_max_nm
                    and self.sfg_scan_min_nm<self.sfg_scan_max_nm):
                raise ValueError("Every 2-D wavelength minimum must be smaller than its maximum.")
            generated=np.asarray(sum_frequency_wavelength_nm(
                [self.signal_min_nm,self.signal_max_nm],[self.idler_min_nm,self.idler_max_nm]))
            if generated.min()<self.sfg_scan_min_nm-1e-9 or generated.max()>self.sfg_scan_max_nm+1e-9:
                raise ValueError("SFG COMSOL scan does not bracket every generated sum-frequency wavelength.")
        elif self.scan_mode=="signal_1d":
            if not self.signal_min_nm<self.signal_max_nm or self.fixed_idler_wavelength_nm is None:
                raise ValueError("signal_1d requires a signal range and FIXED_IDLER_WAVELENGTH_NM.")
            if self.fixed_idler_wavelength_nm<=0: raise ValueError("Fixed idler wavelength must be positive.")
        else:
            if not self.idler_min_nm<self.idler_max_nm or self.fixed_signal_wavelength_nm is None:
                raise ValueError("idler_1d requires an idler range and FIXED_SIGNAL_WAVELENGTH_NM.")
            if self.fixed_signal_wavelength_nm<=0: raise ValueError("Fixed signal wavelength must be positive.")
        for wavelength in self.wavelengths_nm():
            refractive_indices(self.core_material,wavelength)

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_name

    @property
    def sellmeier_source(self) -> str:
        return LN_SOURCE if self.core_material == "LN" else LT_SOURCE

    def wavelengths_nm(self) -> tuple[float, ...]:
        if self.run_profile == "preview" and self.scan_mode=="2d":
            return tuple(sorted({self.sfg_center_nm,self.signal_center_nm,self.idler_center_nm}))
        return tuple(sorted(set(self.sfg_wavelengths_nm()) | set(self.signal_wavelengths_nm()) |
                            set(self.idler_wavelengths_nm())))

    @property
    def signal_center_nm(self) -> float: return (self.signal_min_nm+self.signal_max_nm)/2
    @property
    def idler_center_nm(self) -> float: return (self.idler_min_nm+self.idler_max_nm)/2
    @property
    def sfg_center_nm(self) -> float:
        return float(sum_frequency_wavelength_nm(self.signal_center_nm,self.idler_center_nm))

    def _inclusive_range(self, start: float, stop: float, step: float) -> tuple[float, ...]:
        count = int(round((stop-start)/step))
        if not math.isclose(start + count*step, stop, abs_tol=1e-9):
            raise ValueError("Configured wavelength span must be divisible by its step.")
        return tuple(float(start+i*step) for i in range(count+1))

    def signal_wavelengths_nm(self) -> tuple[float, ...]:
        if self.scan_mode=="idler_1d": return (float(self.fixed_signal_wavelength_nm),)
        step=self.one_d_scan_step_nm if self.scan_mode=="signal_1d" else self.comsol_wavelength_step_nm
        return self._inclusive_range(self.signal_min_nm,self.signal_max_nm,
                                     step)

    def idler_wavelengths_nm(self) -> tuple[float, ...]:
        if self.scan_mode=="signal_1d": return (float(self.fixed_idler_wavelength_nm),)
        step=self.one_d_scan_step_nm if self.scan_mode=="idler_1d" else self.comsol_wavelength_step_nm
        return self._inclusive_range(self.idler_min_nm,self.idler_max_nm,step)

    def sfg_wavelengths_nm(self) -> tuple[float, ...]:
        if self.scan_mode!="2d":
            return tuple(sorted(set(round(float(sum_frequency_wavelength_nm(s,i)),12)
                for s,i in self.one_d_pairs_nm())))
        return self._inclusive_range(self.sfg_scan_min_nm,self.sfg_scan_max_nm,self.comsol_wavelength_step_nm)

    def one_d_pairs_nm(self) -> tuple[tuple[float,float], ...]:
        if self.scan_mode=="signal_1d":
            return tuple((s,float(self.fixed_idler_wavelength_nm)) for s in self.signal_wavelengths_nm())
        if self.scan_mode=="idler_1d":
            return tuple((float(self.fixed_signal_wavelength_nm),i) for i in self.idler_wavelengths_nm())
        raise ValueError("one_d_pairs_nm is available only in a one-dimensional scan mode.")

    def signal_plot_nm(self) -> np.ndarray:
        return np.asarray(self._inclusive_range(self.signal_min_nm,self.signal_max_nm,self.plot_wavelength_step_nm))

    def idler_plot_nm(self) -> np.ndarray:
        return np.asarray(self._inclusive_range(self.idler_min_nm,self.idler_max_nm,self.plot_wavelength_step_nm))

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

# %% COMSOL model construction and TE0 extraction
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
    field_expression="ewfd.Ex" if cfg.target_mode=="TE0" else "ewfd.Ey"
    candidates=[]
    for i,n in enumerate(neff):
        te_pol=float(np.real(te[i])); target_pol=te_pol if cfg.target_mode=="TE0" else 1-te_pol
        candidates.append({"mode_index":i+1,"neff":complex(n),"TE_pol":te_pol,"TM_pol":1-te_pol,
                           "polarization_fraction":target_pol,"pml_fraction":float(np.real(pml[i])),
                           "loss":float(np.real(loss[i]))})
    good=[x for x in candidates if x["polarization_fraction"]>cfg.te_threshold
          and x["pml_fraction"]<0.5 and 1 < x["neff"].real < 3]
    if not good: raise RuntimeError(f"No guided {cfg.target_mode} mode found. Candidates: {candidates}")
    gx=np.arange(-cfg.window_width_um/2,cfg.window_width_um/2+1e-9,cfg.field_dx_nm/1000)
    gy=np.arange(cfg.window_center_y_um-cfg.window_height_um/2,cfg.window_center_y_um+cfg.window_height_um/2+1e-9,cfg.field_dx_nm/1000)
    xx,yy=np.meshgrid(gx,gy)
    field_diagnostics=[]
    for trial in sorted(good,key=lambda item:item["neff"].real,reverse=True):
        mode_index=trial["mode_index"]
        x,y,dominant=model.evaluate(["x","y",field_expression],inner=[mode_index])
        x=np.asarray(x).real.reshape(-1); y=np.asarray(y).real.reshape(-1); dominant=np.asarray(dominant).reshape(-1)
        field=griddata((x,y),dominant,(xx,yy),method="linear",fill_value=0)
        # Phase-aware mirror correlation distinguishes even TE0 (+1) from
        # odd TE1 (-1). A TE0 peak must lie beneath the ridge, near x=0.
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
        "search_method":result.get("search_method","primary"),
        "search_shift_used":str(result.get("search_shift_used",cfg.search_neff)),
        "prediction_anchors_nm":np.asarray(result.get("prediction_anchors_nm",[]),dtype=float),
        "overlap_reference_wavelength_nm":result.get("overlap_reference_wavelength_nm",float("nan")),
        "prediction_field_overlap":result.get("prediction_field_overlap",float("nan")),
        "primary_search_error":result.get("primary_search_error",""),
        "tracking_error":result.get("tracking_error",""),
        "tracking_method":result.get("tracking_method",result.get("search_method","primary")),
        "solve_elapsed_s":result.get("solve_elapsed_s",float("nan")),
        "extraction_elapsed_s":result.get("extraction_elapsed_s",float("nan")),
        "attempts_json":json.dumps(result.get("attempts",[])),
        "mesh_info":json.dumps(mesh_settings(cfg,mesh_profile)),"pml_energy_fraction":result["pml_fraction"],
        "TE_definition":"MODE-equivalent: int_phys(|COMSOL Ex|^2)/int_phys(|Ex|^2+|Ey|^2)",
        "target_polarization_definition":"TE0: TE_pol; TM0: 1-TE_pol",
        "even_symmetry":result["even_symmetry"],"centered_peak":result["centered_peak"],
        "is_TE0":result["is_TE0"],"is_TM0":result["is_TM0"],
        "candidate_modes_json":json.dumps(candidate_json),
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
    started=time.perf_counter(); model.solve(); solve_elapsed=time.perf_counter()-started
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
        except Exception as exc:
            attempts.append({"modes":count,"status":"failed","error":str(exc)})
            last_error=exc
            if count == cfg.fallback_number_of_modes:
                break
    raise RuntimeError(f"{cfg.target_mode} selection failed after {cfg.fallback_number_of_modes} candidates: {last_error}")


class PredictionUnavailable(RuntimeError):
    """No physically valid same-geometry, same-band prediction is available yet."""


def _prediction_bands(cfg: Config, wavelength_nm: float) -> set[str]:
    # Configured bands, not hard-coded 800/900 nm boundaries: never bridge
    # the unsampled gap between the signal and idler branches.
    groups=(("sfg",cfg.sfg_wavelengths_nm()),("signal",cfg.signal_wavelengths_nm()),
            ("idler",cfg.idler_wavelengths_nm()))
    return {name for name,values in groups
            if min(values)-1e-9<=wavelength_nm<=max(values)+1e-9}


def mode_branch(cfg: Config, wavelength_nm: float) -> str:
    exact=[name for name,values in (("sfg",cfg.sfg_wavelengths_nm()),
           ("signal",cfg.signal_wavelengths_nm()),("idler",cfg.idler_wavelengths_nm()))
           if any(math.isclose(wavelength_nm,value,abs_tol=1e-9) for value in values)]
    if exact: return exact[0]
    raise ValueError(f"Wavelength {wavelength_nm:g} nm is not in any configured branch.")


def load_prediction_cache(cfg: Config, geometry: tuple[float,float,float],
                          mesh_profile: str) -> dict[float,dict[str,Any]]:
    """Load anchors only from this run, geometry, mesh and configured wavelengths."""
    cache={}
    for wl in cfg.wavelengths_nm():
        path=mat_path(cfg,*geometry,wl)
        if not path.exists():
            continue
        try:
            data=loadmat(path,squeeze_me=True)
            if str(data.get("target_mode","TE0"))!=cfg.target_mode:
                continue
            actual=[load_scalar(data,key)*1e9 for key in
                    ("film_thickness","top_width","etch_depth","wavelength")]
            if not np.allclose(actual,(*geometry,wl),rtol=0,atol=1e-6):
                continue
            if not math.isclose(load_scalar(data,"sidewall_angle"),cfg.sidewall_angle_deg,abs_tol=1e-9):
                continue
            if (str(np.asarray(data["core_material"]).squeeze()) != cfg.core_material
                    or str(np.asarray(data["top_cladding"]).squeeze()) != cfg.top_cladding):
                continue
            if json.loads(str(np.asarray(data["mesh_info"]).squeeze())) != mesh_settings(cfg,mesh_profile):
                continue
            neff=load_scalar(data,"neff_real"); field_data=np.asarray(data["Ey"])
            target_pol=(load_scalar(data,"TE_pol") if cfg.target_mode=="TE0"
                        else load_scalar(data,"TM_pol"))
            if (np.isfinite(neff) and np.isfinite(field_data).all() and field_data.size>10
                    and np.vdot(field_data,field_data).real>0
                    and target_pol>cfg.te_threshold
                    and load_scalar(data,"pml_energy_fraction")<0.5
                    and load_scalar(data,"even_symmetry")>0.9
                    and bool(load_scalar(data,"centered_peak"))
                    and bool(load_scalar(data,"is_TE0" if cfg.target_mode=="TE0" else "is_TM0"))):
                cache[wl]={"neff":neff,"field":field_data,
                           "y":np.asarray(data["y"]).reshape(-1),"z":np.asarray(data["z"]).reshape(-1)}
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return cache


def predict_search_center(cfg: Config, wavelength_nm: float,
                          cache: dict[float,dict[str,Any]]) -> tuple[float,list[float],float]:
    bands=_prediction_bands(cfg,wavelength_nm)
    xs=sorted(x for x,value in cache.items() if x != wavelength_nm
              and bands.intersection(_prediction_bands(cfg,x)) and np.isfinite(value["neff"]))
    if not xs:
        raise PredictionUnavailable(f"No same-band successful mode at wavelength={wavelength_nm:g} nm")
    left=[x for x in xs if x<wavelength_nm]; right=[x for x in xs if x>wavelength_nm]
    anchors=[left[-1],right[0]] if left and right else (xs[-2:] if left else xs[:2])
    predicted=float(cache[anchors[0]]["neff"])
    if len(anchors)==2:
        a,b=anchors
        predicted += float((cache[b]["neff"]-cache[a]["neff"])*(wavelength_nm-a)/(b-a))
    if not np.isfinite(predicted) or not 1 < predicted < max(refractive_indices(cfg.core_material,wavelength_nm)):
        raise PredictionUnavailable(f"Predicted neff={predicted:g} outside physical bounds at {wavelength_nm:g} nm")
    return predicted,anchors,min(xs,key=lambda x:abs(x-wavelength_nm))


def _primary_search_expression(cfg: Config) -> str:
    return ("ne_sell(lam_um)" if cfg.search_neff=="TE_CORE_INDEX" else
            "no_sell(lam_um)" if cfg.search_neff=="MAX_CORE_INDEX" else
            search_expression(cfg.crystal_cut, cfg.target_mode)
            if cfg.search_neff=="TARGET_CORE_INDEX" else str(cfg.search_neff))


def _remember_prediction(cache: dict[float,dict[str,Any]], wavelength_nm: float,
                         result: dict[str,Any]) -> None:
    cache[wavelength_nm]={"neff":float(np.real(result["neff"])),"field":np.asarray(result["Ey"]),
                          "y":np.asarray(result["y"]).reshape(-1),"z":np.asarray(result["z"]).reshape(-1)}


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


def _solve_with_prediction(model: Any, cfg: Config, point: ScanPoint,
                           cache: dict[float,dict[str,Any]], *,
                           prediction_only: bool=False, primary_error: str="") -> tuple[dict[str,Any],int]:
    """Preserve the original solve path; predict only after its complete failure."""
    primary_shift=_primary_search_expression(cfg)
    model.parameter("nshift",primary_shift)
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
                logging.getLogger("lnlt_comsol").warning(
                    "PRIMARY FAILED T=%g W=%g D=%g lambda=%g; trying predicted search: %s",
                    *point.geometry,point.wavelength_nm,primary_error)
        elif not cfg.predicted_search_fallback:
            raise RuntimeError("Predicted search is disabled")
        try:
            predicted,anchors,reference=predict_search_center(cfg,point.wavelength_nm,cache)
        except PredictionUnavailable as exc:
            raise PredictionUnavailable(f"Primary search failed: {primary_error}; {exc}") from exc
        retry_cfg=replace(cfg,search_neff=predicted,initial_number_of_modes=cfg.fallback_number_of_modes)
        model.parameter("nshift",str(predicted))
        logging.getLogger("lnlt_comsol").info(
            "PREDICTED RETRY lambda=%g shift=%.10g anchors=%s reference=%g",
            point.wavelength_nm,predicted,anchors,reference)
        result,modes=_solve_and_select(model,retry_cfg,point)
        overlap=prediction_field_overlap(cache[reference],result)
        if not np.isfinite(result["neff"]) or not np.isfinite(overlap) or overlap<cfg.predicted_field_overlap_min:
            raise RuntimeError(f"Predicted-search {cfg.target_mode} field-overlap check failed: {overlap:g}")
        result.update(search_method="predicted_fallback",search_shift_used=predicted,
                      prediction_anchors_nm=anchors,overlap_reference_wavelength_nm=reference,
                      prediction_field_overlap=overlap,primary_search_error=primary_error)
        return result,modes
    finally:
        # Restore even when prediction, solve or overlap validation fails.
        model.parameter("nshift",primary_shift)


def _solve_scan_point(model: Any, cfg: Config, point: ScanPoint,
                      cache: dict[float,dict[str,Any]], *, force_robust: bool,
                      prediction_only: bool=False, primary_error: str="") -> tuple[dict[str,Any],int]:
    """Use fast adjacent tracking, with the complete robust path as fallback."""
    logger=logging.getLogger("lnlt_comsol"); original=_primary_search_expression(cfg)
    tracking_error=""
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
    result,count=_solve_with_prediction(model,cfg,point,cache,
                                        prediction_only=prediction_only,primary_error=primary_error)
    base_method=result.get("search_method","primary")
    result["tracking_method"]=("robust_reseed" if force_robust else
                               "robust_after_tracking_failure" if tracking_error else base_method)
    result["tracking_error"]=tracking_error
    return result,count


def robust_reseed_required(cfg: Config, wavelength_nm: float) -> bool:
    branch=mode_branch(cfg,wavelength_nm)
    relevant={"sfg":cfg.sfg_wavelengths_nm(),"signal":cfg.signal_wavelengths_nm(),
              "idler":cfg.idler_wavelengths_nm()}[branch]
    position=next((i for i,w in enumerate(relevant)
                   if math.isclose(w,wavelength_nm,rel_tol=0,abs_tol=1e-9)),0)
    return position % cfg.robust_reseed_interval == 0


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
        model_path=cfg.run_dir/"model"/f"{cfg.core_material}_xcut_rib_SFG_Period.mph"
        for geometry in ((t,w,d) for t in cfg.thicknesses_nm() for w in cfg.widths_nm() for d in cfg.etches_nm()):
            t,w,d=geometry
            material_checked=False
            prediction_cache=(load_prediction_cache(cfg,geometry,mesh_profile)
                              if (cfg.predicted_search_fallback or cfg.adjacent_mode_tracking)
                              and cfg.resume_current_run and not force else {})
            deferred=set()
            logger.info("BUILD+MESH T=%g W=%g D=%g nm (%s)",t,w,d,mesh_profile)
            model.parameter("tfilm",f"{t}[nm]"); model.parameter("wtop",f"{w}[nm]"); model.parameter("etch",f"{d}[nm]")
            model.build(); model.mesh()
            # A fresh scan may fail before any anchor exists. Defer that point
            # once, then retry prediction only after the remaining wavelengths.
            queue=[(wl,None) for wl in cfg.wavelengths_nm()]
            for wl,deferred_error in queue:
                point=ScanPoint(t,w,d,wl); key=(cfg.target_mode,t,w,d,wl); path=mat_path(cfg,t,w,d,wl)
                if not should_solve_point(path,cfg,force):
                    logger.info("SKIP complete %s",path.name)
                    records[key]={"fingerprint":cfg.fingerprint(),"thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
                                  "wavelength_nm":wl,"target_mode":cfg.target_mode,"status":"skipped","mat":str(path),"error":""}
                    _write_index(index_path,records); continue
                started=datetime.now().isoformat(); logger.info("SOLVE T=%g W=%g D=%g lambda=%g nm",t,w,d,wl)
                point_started=time.perf_counter(); result={}; modes=0
                try:
                    model.parameter("lda0",f"{wl}[nm]")
                    result,modes=_solve_scan_point(
                        model,cfg,point,prediction_cache,force_robust=robust_reseed_required(cfg,wl),
                        prediction_only=deferred_error is not None,primary_error=deferred_error or "")
                    if not material_checked:
                        check=verify_material_domains(model,cfg,point); material_checked=True
                        check_path=cfg.run_dir/"data"/f"material_domain_check_T{t:g}_W{w:g}_D{d:g}.json"
                        check_path.write_text(json.dumps(check,indent=2),encoding="utf-8")
                        logger.info("MATERIAL DOMAINS PASS eps_xx=%s",{k:v["epsilon_xx"] for k,v in check["actual"].items()})
                    save_point(cfg,result,point,mesh_profile,modes,path)
                    _remember_prediction(prediction_cache,wl,result)
                    status="success"; error=""
                    logger.info("OK mode=%s neff=%s pol=%.4f symmetry=%.4f PML=%.3g modes=%d method=%s solve=%.3fs",
                        cfg.target_mode,result["neff"],result["polarization_fraction"],result["even_symmetry"],
                        result["pml_fraction"],modes,result.get("tracking_method"),result.get("solve_elapsed_s",float("nan")))
                except Exception as exc:
                    status="failed"; error=f"{type(exc).__name__}: {exc}"; logger.error("FAILED %s\n%s",error,traceback.format_exc())
                    if isinstance(exc,PredictionUnavailable) and wl not in deferred:
                        status="deferred"
                        deferred.add(wl)
                        queue.append((wl,error))
                        logger.info("DEFER lambda=%g: retry predicted search once after other points supply anchors",wl)
                records[key]={"fingerprint":cfg.fingerprint(),"thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
                              "wavelength_nm":wl,"mesh_profile":mesh_profile,"status":status,"started":started,
                              "finished":datetime.now().isoformat(),"mat":str(path),"error":error,
                              "search_method":result.get("search_method",status),
                              "search_shift_used":result.get("search_shift_used",""),
                              "prediction_anchors_nm":json.dumps(result.get("prediction_anchors_nm",[])),
                              "prediction_field_overlap":result.get("prediction_field_overlap",float("nan")),
                              "primary_search_error":result.get("primary_search_error",deferred_error or ""),
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


# %% Two-dimensional non-degenerate SFG period processing
def sum_frequency_wavelength_nm(signal_nm: Any, idler_nm: Any) -> np.ndarray:
    """Return lambda_1 for omega_1 = omega_2 + omega_3."""
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


def compute_sfg_period_grid(
    cfg: Config,
    sfg_sample_nm: Iterable[float], sfg_neff: Iterable[float],
    signal_sample_nm: Iterable[float], signal_neff: Iterable[float],
    idler_sample_nm: Iterable[float], idler_neff: Iterable[float],
) -> dict[str,Any]:
    """Calculate the first-order QPM period for every signal/idler pair."""
    sfg_interp=_dispersion_interpolator(sfg_sample_nm,sfg_neff)
    signal_interp=_dispersion_interpolator(signal_sample_nm,signal_neff)
    idler_interp=_dispersion_interpolator(idler_sample_nm,idler_neff)
    signal_axis=cfg.signal_plot_nm(); idler_axis=cfg.idler_plot_nm()
    lambda2,lambda3=np.meshgrid(signal_axis,idler_axis,indexing="xy")
    lambda1=sum_frequency_wavelength_nm(lambda2,lambda3)
    n1=_evaluate_checked(sfg_interp,lambda1,"SFG")
    n2=_evaluate_checked(signal_interp,lambda2,"Signal")
    n3=_evaluate_checked(idler_interp,lambda3,"Idler")
    beta1=2*np.pi*n1/(lambda1*1e-9)
    beta2=2*np.pi*n2/(lambda2*1e-9)
    beta3=2*np.pi*n3/(lambda3*1e-9)
    mismatch=beta1-beta2-beta3
    with np.errstate(divide="ignore",invalid="ignore"):
        period_um=2*np.pi/np.abs(mismatch)*1e6
    if not np.all(np.isfinite(period_um)) or np.any(period_um<=0):
        raise ValueError("The SFG grid contains a zero mismatch or non-finite/non-positive QPM period.")
    return {
        "signal_axis_nm":signal_axis,"idler_axis_nm":idler_axis,
        "lambda1_sfg_nm":lambda1,"lambda2_signal_nm":lambda2,"lambda3_idler_nm":lambda3,
        "neff1_sfg":n1,"neff2_signal":n2,"neff3_idler":n3,
        "beta1_rad_m":beta1,"beta2_rad_m":beta2,"beta3_rad_m":beta3,
        "natural_mismatch_rad_m":mismatch,"qpm_period_um":period_um,
    }


def contour_period_levels(period_um: np.ndarray, step_um: float) -> np.ndarray:
    """Return contour levels aligned to integer multiples of step_um."""
    values=np.asarray(period_um,dtype=float)
    finite=values[np.isfinite(values)]
    if finite.size==0 or step_um<=0:
        raise ValueError("Finite period data and a positive contour step are required.")
    first=math.ceil((float(finite.min())-1e-12)/step_um)*step_um
    last=math.floor((float(finite.max())+1e-12)/step_um)*step_um
    if first>last:
        return np.asarray([],dtype=float)
    count=int(round((last-first)/step_um))+1
    return np.round(first+np.arange(count)*step_um,10)


def _geometry_stem(cfg: Config, t: float, w: float, d: float) -> str:
    return f"{cfg.core_material}_AIR_SFG_Period_T{t:g}_W{w:g}_D{d:g}_A{cfg.sidewall_angle_deg:g}"


def plot_sfg_period_contour(path: Path, grid: dict[str,Any], step_um: float,
                            target_mode: str="TE0") -> None:
    signal=grid["signal_axis_nm"]; idler=grid["idler_axis_nm"]; period=grid["qpm_period_um"]
    levels=contour_period_levels(period,step_um)
    fig,ax=plt.subplots(figsize=(7.5,6.35))
    background=ax.pcolormesh(signal,idler,period,shading="auto",cmap="coolwarm",
                             vmin=float(period.min()),vmax=float(period.max()),rasterized=True)
    if levels.size:
        lines=ax.contour(signal,idler,period,levels=levels,colors="#202020",linewidths=.72,alpha=.88)
        ax.clabel(lines,levels=levels,inline=True,inline_spacing=5,fontsize=8,
                  fmt=lambda value:f"{value:.2f} μm")
    colorbar=fig.colorbar(background,ax=ax,pad=.025)
    colorbar.set_label("First-order QPM period Λ (μm)")
    ax.set_xlabel("Signal wavelength λ₂ (nm)")
    ax.set_ylabel("Idler wavelength λ₃ (nm)")
    ax.set_title(f"Non-degenerate SFG quasi-phase-matching period ({target_mode})")
    ax.set_xlim(signal[0],signal[-1]); ax.set_ylim(idler[0],idler[-1])
    ax.set_aspect("equal",adjustable="box")
    ax.tick_params(direction="in",top=True,right=True)
    for spine in ax.spines.values(): spine.set_linewidth(1.0)
    fig.tight_layout(); fig.savefig(path,dpi=300,bbox_inches="tight"); plt.close(fig)


def compute_sfg_period_curve(cfg: Config, neff_by_wavelength: dict[float,float]) -> pd.DataFrame:
    """Direct, interpolation-free QPM periods for configured 1-D pairs."""
    rows=[]
    for signal,idler in cfg.one_d_pairs_nm():
        sfg=round(float(sum_frequency_wavelength_nm(signal,idler)),12)
        n1=float(neff_by_wavelength[sfg]); n2=float(neff_by_wavelength[signal]); n3=float(neff_by_wavelength[idler])
        beta1=2*np.pi*n1/(sfg*1e-9); beta2=2*np.pi*n2/(signal*1e-9); beta3=2*np.pi*n3/(idler*1e-9)
        mismatch=beta1-beta2-beta3
        if not np.isfinite(mismatch) or mismatch==0: raise ValueError("Invalid zero/non-finite 1-D SFG mismatch.")
        rows.append({"target_mode":cfg.target_mode,"lambda1_sfg_nm":sfg,
                     "lambda2_signal_nm":signal,"lambda3_idler_nm":idler,
                     "neff1_sfg":n1,"neff2_signal":n2,"neff3_idler":n3,
                     "beta1_rad_m":beta1,"beta2_rad_m":beta2,"beta3_rad_m":beta3,
                     "natural_mismatch_rad_m":mismatch,"qpm_period_um":2*np.pi/abs(mismatch)*1e6})
    return pd.DataFrame(rows)


def plot_sfg_period_curve(path: Path, cfg: Config, curve: pd.DataFrame) -> None:
    signal_scan=cfg.scan_mode=="signal_1d"
    x=curve.lambda2_signal_nm if signal_scan else curve.lambda3_idler_nm
    label="Signal wavelength λ₂ (nm)" if signal_scan else "Idler wavelength λ₃ (nm)"
    fixed=(f"idler λ₃ = {cfg.fixed_idler_wavelength_nm:g} nm" if signal_scan
           else f"signal λ₂ = {cfg.fixed_signal_wavelength_nm:g} nm")
    fig,ax=plt.subplots(figsize=(7.2,4.8))
    ax.plot(x,curve.qpm_period_um,color="#145DA0",lw=2.0,marker="o",ms=6,
            markerfacecolor="#E4572E",markeredgecolor="white",markeredgewidth=.8)
    ax.set_xlabel(label); ax.set_ylabel("First-order QPM period Λ (μm)")
    ax.set_title(f"Non-degenerate SFG/QFC QPM period ({cfg.target_mode}; {fixed})")
    ax.grid(True,alpha=.28,ls="--"); ax.tick_params(direction="in",top=True,right=True)
    fig.tight_layout(); fig.savefig(path,dpi=300,bbox_inches="tight"); plt.close(fig)


def postprocess_1d(cfg: Config) -> pd.DataFrame:
    figdir=cfg.run_dir/"data"/"figures"
    for old in figdir.glob("*.png"): old.unlink()
    summaries=[]; geometries=[(t,w,d) for t in cfg.thicknesses_nm() for w in cfg.widths_nm() for d in cfg.etches_nm()]
    for t,w,d in geometries:
        missing=[str(mat_path(cfg,t,w,d,wl)) for wl in cfg.wavelengths_nm()
                 if not point_complete(mat_path(cfg,t,w,d,wl))]
        if missing: raise FileNotFoundError(f"Cannot postprocess 1-D scan; missing {len(missing)} MAT files, first: {missing[0]}")
        neff={wl:load_scalar(loadmat(mat_path(cfg,t,w,d,wl)),"neff_real") for wl in cfg.wavelengths_nm()}
        curve=compute_sfg_period_curve(cfg,neff); stem=_geometry_stem(cfg,t,w,d)
        curve.insert(0,"geometry",stem)
        csv_name="sfg_period_curve.csv" if len(geometries)==1 else f"sfg_period_curve_{stem}.csv"
        png_name="sfg_period_curve.png" if len(geometries)==1 else f"sfg_period_curve_{stem}.png"
        curve.to_csv(cfg.run_dir/"data"/csv_name,index=False); plot_sfg_period_curve(figdir/png_name,cfg,curve)
        summaries.append({"geometry":stem,"target_mode":cfg.target_mode,"scan_mode":cfg.scan_mode,"points":len(curve),
                          "qpm_period_min_um":float(curve.qpm_period_um.min()),
                          "qpm_period_max_um":float(curve.qpm_period_um.max())})
    summary=pd.DataFrame(summaries); summary.to_csv(cfg.run_dir/"data"/"sfg_period_summary.csv",index=False)
    (cfg.run_dir/"data"/"sfg_period_metadata.json").write_text(json.dumps({
        "definitions":{"wave1":"generated SFG","wave2":"signal","wave3":"idler",
                       "calculation":"direct one-to-one modes; no wavelength interpolation"},
        "config":serializable_config(cfg),"geometries":summaries},indent=2,ensure_ascii=False),encoding="utf-8")
    return summary


def postprocess(cfg: Config) -> pd.DataFrame:
    cfg.validate(); prepare_run(cfg)
    if cfg.run_profile=="preview" and cfg.scan_mode=="2d":
        return pd.DataFrame()
    if cfg.scan_mode!="2d": return postprocess_1d(cfg)
    figdir=cfg.run_dir/"data"/"figures"
    for old in figdir.glob("*.png"): old.unlink()
    summaries=[]; grids=[]
    geometries=[(t,w,d) for t in cfg.thicknesses_nm() for w in cfg.widths_nm() for d in cfg.etches_nm()]
    for t,w,d in geometries:
        sfg_wl=cfg.sfg_wavelengths_nm(); signal_wl=cfg.signal_wavelengths_nm(); idler_wl=cfg.idler_wavelengths_nm()
        missing=[str(mat_path(cfg,t,w,d,wl)) for wl in cfg.wavelengths_nm()
                 if not point_complete(mat_path(cfg,t,w,d,wl))]
        if missing:
            raise FileNotFoundError(
                f"Cannot postprocess T={t:g}, W={w:g}, D={d:g}; missing {len(missing)} MAT files, first: {missing[0]}")
        def neff_at(wavelengths: Iterable[float]) -> list[float]:
            return [load_scalar(loadmat(mat_path(cfg,t,w,d,wl)),"neff_real") for wl in wavelengths]
        grid=compute_sfg_period_grid(cfg,sfg_wl,neff_at(sfg_wl),
                                     signal_wl,neff_at(signal_wl),idler_wl,neff_at(idler_wl))
        stem=_geometry_stem(cfg,t,w,d)
        flat=pd.DataFrame({
            "lambda1_sfg_nm":grid["lambda1_sfg_nm"].ravel(),
            "lambda2_signal_nm":grid["lambda2_signal_nm"].ravel(),
            "lambda3_idler_nm":grid["lambda3_idler_nm"].ravel(),
            "neff1_sfg":grid["neff1_sfg"].ravel(),
            "neff2_signal":grid["neff2_signal"].ravel(),
            "neff3_idler":grid["neff3_idler"].ravel(),
            "beta1_rad_m":grid["beta1_rad_m"].ravel(),
            "beta2_rad_m":grid["beta2_rad_m"].ravel(),
            "beta3_rad_m":grid["beta3_rad_m"].ravel(),
            "natural_mismatch_rad_m":grid["natural_mismatch_rad_m"].ravel(),
            "qpm_period_um":grid["qpm_period_um"].ravel(),
        })
        flat.insert(0,"target_mode",cfg.target_mode); flat.insert(0,"geometry",stem); grids.append(flat)
        output_name="sfg_period_grid.csv" if len(geometries)==1 else f"sfg_period_grid_{stem}.csv"
        flat.to_csv(cfg.run_dir/"data"/output_name,index=False)
        figure_name="sfg_period_contour.png" if len(geometries)==1 else f"sfg_period_contour_{stem}.png"
        plot_sfg_period_contour(figdir/figure_name,grid,cfg.contour_period_step_um,cfg.target_mode)
        summaries.append({
            "geometry":stem,"target_mode":cfg.target_mode,"core_material":cfg.core_material,"top_cladding":cfg.top_cladding,
            "film_thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
            "signal_min_nm":cfg.signal_min_nm,"signal_max_nm":cfg.signal_max_nm,
            "idler_min_nm":cfg.idler_min_nm,"idler_max_nm":cfg.idler_max_nm,
            "grid_rows":len(flat),"qpm_period_min_um":float(flat.qpm_period_um.min()),
            "qpm_period_max_um":float(flat.qpm_period_um.max()),
            "contour_step_um":cfg.contour_period_step_um,
        })
    summary=pd.DataFrame(summaries)
    summary.to_csv(cfg.run_dir/"data"/"sfg_period_summary.csv",index=False)
    if len(grids)>1: pd.concat(grids,ignore_index=True).to_csv(cfg.run_dir/"data"/"sfg_period_all_geometries.csv",index=False)
    payload={"definitions":{"wave1":"generated SFG","wave2":"signal","wave3":"idler",
              "energy_conservation":"1/lambda1 = 1/lambda2 + 1/lambda3",
              "qpm_period":"2*pi/abs(beta1-beta2-beta3)"},
             "config":serializable_config(cfg),"geometries":summaries}
    (cfg.run_dir/"data"/"sfg_period_metadata.json").write_text(
        json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
    return summary

# %% Output validation
def reopen_saved_model(cfg: Config) -> bool:
    """Open the saved MPH and require COMSOL to report no model problems."""
    import mph
    model_path=cfg.run_dir/"model"/f"{cfg.core_material}_xcut_rib_SFG_Period.mph"
    if not model_path.exists(): return False
    client=mph.Client(cores=cfg.threads,version="6.4")
    try:
        model=client.load(model_path)
        return model.problems()==[]
    finally:
        try: client.clear()
        except Exception: pass


def validate_outputs(cfg: Config, mph_reopen_ok: bool | None=None) -> dict[str,Any]:
    """Re-read all expected results and write a concise validation report."""
    cfg.validate()
    required=("film_thickness","top_width","bottom_width","etch_depth","sidewall_angle",
              "wavelength","mode_index","TE_pol","neff","neff_real","loss","y","z","Ey")
    rows=[]; missing=[]
    for point in scan_points(cfg):
        path=mat_path(cfg,*point.geometry,point.wavelength_nm)
        if not path.exists():
            missing.append(str(path)); continue
        data=loadmat(path); absent=[key for key in required if key not in data]
        stored_mode=str(np.asarray(data.get("target_mode","TE0")).squeeze())
        if absent or data["Ey"].size<100 or stored_mode!=cfg.target_mode:
            missing.append(f"{path}: {absent or 'empty field'}"); continue
        rows.append({"thickness_nm":point.thickness_nm,"top_width_nm":point.top_width_nm,
                     "etch_depth_nm":point.etch_depth_nm,"wavelength_nm":point.wavelength_nm,
                     "target_mode":stored_mode,
                     "neff":load_scalar(data,"neff_real"),"TE_pol":load_scalar(data,"TE_pol"),
                     "pml_fraction":load_scalar(data,"pml_energy_fraction")})
    points=pd.DataFrame(rows)
    points.to_csv(cfg.run_dir/"data"/"validated_mat_points.csv",index=False)
    expected=len(scan_points(cfg))
    index_path=cfg.run_dir/"data"/"scan_index.csv"
    index=pd.read_csv(index_path) if index_path.exists() else pd.DataFrame()
    index_ok=(len(index)==expected and "status" in index and not index["status"].eq("failed").any()
              and not index.duplicated(["thickness_nm","top_width_nm","etch_depth_nm","wavelength_nm"]).any())
    one_d=cfg.scan_mode!="2d"
    result_path=cfg.run_dir/"data"/("sfg_period_curve.csv" if one_d else "sfg_period_grid.csv")
    result=pd.read_csv(result_path) if result_path.exists() else pd.DataFrame()
    result_rows_expected=(len(cfg.one_d_pairs_nm()) if one_d
                          else len(cfg.signal_plot_nm())*len(cfg.idler_plot_nm()))
    result_ok=(len(result)==result_rows_expected and "qpm_period_um" in result
               and np.all(np.isfinite(result["qpm_period_um"])) and np.all(result["qpm_period_um"]>0))
    figures=list((cfg.run_dir/"data"/"figures").glob("*.png"))
    expected_figure="sfg_period_curve.png" if one_d else "sfg_period_contour.png"
    figure_ok=len(figures)==1 and figures[0].name==expected_figure
    material_checks=[json.loads(path.read_text(encoding="utf-8"))
                     for path in (cfg.run_dir/"data").glob("material_domain_check*.json")]
    material_ok=bool(material_checks) and all(item.get("passed") for item in material_checks)
    if mph_reopen_ok is None:
        try: mph_reopen_ok=reopen_saved_model(cfg)
        except Exception: mph_reopen_ok=False
    ok=(not missing and len(rows)==expected and index_ok and result_ok and figure_ok
        and material_ok and mph_reopen_ok is True)
    report=["# COMSOL 6.4 non-degenerate SFG-period validation report","",
            f"- Result: **{'PASS' if ok else 'FAIL'}**",
            f"- Material / upper cladding: {cfg.core_material} / {cfg.top_cladding}",
            f"- MAT integrity: {len(rows)}/{expected}",
            f"- Scan index complete with no failed/duplicate points: {index_ok}",
            f"- SFG period result: {len(result)}/{result_rows_expected}; finite positive periods: {result_ok}",
            f"- Exactly one final {expected_figure}: {figure_ok}",
            f"- Material-domain epsilon check: {material_ok}",
            f"- Saved MPH reopened with problems=[]: {mph_reopen_ok}","",
            f"Missing/invalid: {missing or 'none'}"]
    (cfg.run_dir/"data"/"validation_report.md").write_text("\n".join(report),encoding="utf-8")
    return {"passed":ok,"rows":len(rows),"expected":expected,"missing":missing,
            "scan_index_ok":index_ok,"result_ok":result_ok,"figure_ok":figure_ok,
            "material_domains":material_ok,"mph_reopen_ok":mph_reopen_ok}

# %% Command-line entry point
def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("command",choices=("run","postprocess","validate","sellmeier"),nargs="?",default="run"); p.add_argument("--force",action="store_true"); args=p.parse_args()
    cfg=Config()
    if args.command=="run": run_scan(cfg,args.force)
    elif args.command=="postprocess": postprocess(cfg)
    elif args.command=="validate": print(validate_outputs(cfg))
    else:
        for wl in cfg.wavelengths_nm(): print(wl,refractive_indices(cfg.core_material,wl))

if __name__ == "__main__":
    main()
