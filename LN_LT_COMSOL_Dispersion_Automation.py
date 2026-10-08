"""COMSOL 6.4 automation for TE0/TM0 waveguide dispersion and ZDP scans.

The proven x-cut LN/LT model builder is reused from the Period automation.
This program owns its configuration, scan/cache format, dual-polarization mode
tracking, dispersion numerics, active ZDP search, plots, and validation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Iterable, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.interpolate import griddata
from scipy.io import loadmat, savemat

from dispersion_core import (
    GeometryAxis, MaternGP, ZDPRoot, compute_dispersion, find_zdp_roots,
    geometry_lattice, inclusive_range, maximin_seeds, normalized_field_overlap,
    straddle_score,
)


# ----------------------------- USER CONFIGURATION ---------------------------
RUN_MODE = "wavelength_scan"       # "wavelength_scan" or "zdp_scan"
# Select exactly one polarization branch for this run.
MODE_BRANCH = "TE0"                # "TE0" or "TM0"
RUN_NAME = "LT_AIR_T800_W3000_D550_A67_L1200_1600_S50"
OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs" / "legacy"
CORE_MATERIAL = "LT"               # "LN" or "LT"
TOP_CLADDING = "AIR"               # "AIR" or "SIO2"
SIDEWALL_ANGLE_DEG = 67.0
COMSOL_THREADS = 4

# wavelength_scan fixed geometry and official output grid
FIXED_THICKNESS_NM = 800.0
FIXED_ETCH_DEPTH_NM = 550.0
FIXED_TOP_WIDTH_NM = 3000.0
WAVELENGTH_MIN_NM = 1200.0
WAVELENGTH_MAX_NM = 1600.0
WAVELENGTH_STEP_NM = 50.0

# zdp_scan finite manufacturing lattice. Thickness is never auto-refined.
ZDP_THICKNESSES_NM = (800.0,)
ZDP_ETCH_AXIS = GeometryAxis(minimum_nm=200.0, maximum_nm=550.0,
                             initial_step_nm=100.0, minimum_step_nm=50.0)
ZDP_WIDTH_AXIS = GeometryAxis(minimum_nm=1000.0, maximum_nm=3000.0,
                              initial_step_nm=500.0, minimum_step_nm=100.0)
ZDP_INITIAL_GEOMETRIES = 9
ZDP_WAVELENGTH_TOL_NM = 1.0
GP_CONFIDENCE_SIGMA = 3.0
MAX_GEOMETRY_SOLVES: int | None = None

INITIAL_NUMBER_OF_MODES = 20
FALLBACK_NUMBER_OF_MODES = 40
POLARIZATION_THRESHOLD = 0.5
PML_FRACTION_MAX = 0.5
EVEN_SYMMETRY_MIN = 0.90
FIELD_OVERLAP_MIN = 0.90
ENABLE_PREDICTED_SEARCH_FALLBACK = True
AUTO_REFINE_WAVELENGTH = True
MAX_WAVELENGTH_REFINEMENT_ROUNDS = 1
GVD_RELATIVE_FIT_TOL = 0.05
GVD_ABSOLUTE_FIT_TOL_PS2_KM = 1.0
FIELD_DX_NM = 40.0
# ---------------------------------------------------------------------------


SCRIPT_DIR = Path(__file__).resolve().parent
PERIOD_BACKEND = SCRIPT_DIR / "LN_LT_COMSOL_SHG_QPM_Automation.py"


@dataclass(frozen=True)
class Config:
    run_mode: str = RUN_MODE
    run_name: str = RUN_NAME
    output_root: Path = OUTPUT_ROOT
    core_material: str = CORE_MATERIAL
    top_cladding: str = TOP_CLADDING
    mode_branch: str = MODE_BRANCH
    sidewall_angle_deg: float = SIDEWALL_ANGLE_DEG
    threads: int = COMSOL_THREADS
    fixed_thickness_nm: float = FIXED_THICKNESS_NM
    fixed_etch_depth_nm: float = FIXED_ETCH_DEPTH_NM
    fixed_top_width_nm: float = FIXED_TOP_WIDTH_NM
    wavelength_min_nm: float = WAVELENGTH_MIN_NM
    wavelength_max_nm: float = WAVELENGTH_MAX_NM
    wavelength_step_nm: float = WAVELENGTH_STEP_NM
    zdp_thicknesses_nm: tuple[float, ...] = ZDP_THICKNESSES_NM
    zdp_etch_axis: GeometryAxis = field(default_factory=lambda: ZDP_ETCH_AXIS)
    zdp_width_axis: GeometryAxis = field(default_factory=lambda: ZDP_WIDTH_AXIS)
    zdp_initial_geometries: int = ZDP_INITIAL_GEOMETRIES
    zdp_wavelength_tolerance_nm: float = ZDP_WAVELENGTH_TOL_NM
    gp_confidence_sigma: float = GP_CONFIDENCE_SIGMA
    max_geometry_solves: int | None = MAX_GEOMETRY_SOLVES
    initial_number_of_modes: int = INITIAL_NUMBER_OF_MODES
    fallback_number_of_modes: int = FALLBACK_NUMBER_OF_MODES
    polarization_threshold: float = POLARIZATION_THRESHOLD
    pml_fraction_max: float = PML_FRACTION_MAX
    even_symmetry_min: float = EVEN_SYMMETRY_MIN
    field_overlap_min: float = FIELD_OVERLAP_MIN
    predicted_search_fallback: bool = ENABLE_PREDICTED_SEARCH_FALLBACK
    auto_refine_wavelength: bool = AUTO_REFINE_WAVELENGTH
    max_wavelength_refinement_rounds: int = MAX_WAVELENGTH_REFINEMENT_ROUNDS
    gvd_relative_fit_tol: float = GVD_RELATIVE_FIT_TOL
    gvd_absolute_fit_tol_ps2_km: float = GVD_ABSOLUTE_FIT_TOL_PS2_KM
    field_dx_nm: float = FIELD_DX_NM
    window_width_um: float = 15.0
    window_height_um: float = 4.0
    window_center_y_um: float = -0.25

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_name

    def official_wavelengths_nm(self) -> tuple[float, ...]:
        return inclusive_range(self.wavelength_min_nm, self.wavelength_max_nm, self.wavelength_step_nm)

    def fixed_geometry(self) -> tuple[float, float, float]:
        return self.fixed_thickness_nm, self.fixed_etch_depth_nm, self.fixed_top_width_nm

    @property
    def mode_branches(self) -> tuple[str, ...]:
        """Internal one-element sequence retained for shared scan loops."""
        return (self.mode_branch,)

    def validate(self) -> None:
        if self.run_mode not in {"wavelength_scan", "zdp_scan"}:
            raise ValueError("run_mode must be wavelength_scan or zdp_scan")
        if self.core_material not in {"LN", "LT"} or self.top_cladding not in {"AIR", "SIO2"}:
            raise ValueError("invalid material or top cladding")
        if self.mode_branch not in {"TE0", "TM0"}:
            raise ValueError("mode_branch must be TE0 or TM0")
        if self.fixed_etch_depth_nm > self.fixed_thickness_nm:
            raise ValueError("etch depth cannot exceed thickness")
        if not 0 < self.field_overlap_min <= 1 or not 0 < self.even_symmetry_min <= 1:
            raise ValueError("overlap/symmetry thresholds must lie in (0,1]")
        if self.run_mode == "zdp_scan":
            if (self.zdp_etch_axis.minimum_step_nm is not None and self.zdp_etch_axis.minimum_step_nm < 50):
                raise ValueError("etch refinement finer than 50 nm is not allowed")
            if (self.zdp_width_axis.minimum_step_nm is not None and self.zdp_width_axis.minimum_step_nm < 100):
                raise ValueError("top-width refinement finer than 100 nm is not allowed")
            geometry_lattice(self.zdp_thicknesses_nm, self.zdp_etch_axis, self.zdp_width_axis)
        # Material range validation is delegated to the proven backend below.


@dataclass(frozen=True)
class ScanPoint:
    thickness_nm: float
    etch_depth_nm: float
    top_width_nm: float
    wavelength_nm: float
    branch: str
    official: bool = True

    @property
    def geometry(self) -> tuple[float, float, float]:
        return self.thickness_nm, self.etch_depth_nm, self.top_width_nm


class PredictionUnavailable(RuntimeError):
    pass


def serializable_config(cfg: Config) -> dict[str, Any]:
    data = asdict(cfg)
    data["output_root"] = str(cfg.output_root)
    return data


def fingerprint(cfg: Config) -> str:
    return hashlib.sha256(json.dumps(serializable_config(cfg), sort_keys=True).encode()).hexdigest()[:16]


def load_backend() -> Any:
    if not PERIOD_BACKEND.exists():
        raise FileNotFoundError(f"Proven COMSOL backend not found: {PERIOD_BACKEND}")
    spec = importlib.util.spec_from_file_location("lnlt_period_backend_for_dispersion", PERIOD_BACKEND)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def backend_config(cfg: Config) -> Any:
    backend = load_backend()
    return backend.Config(
        core_material=cfg.core_material, run_profile="full", run_name=cfg.run_name,
        output_root=cfg.output_root, film_thicknesses_nm=(cfg.fixed_thickness_nm,),
        top_widths_nm=(cfg.fixed_top_width_nm,), etch_depths_nm=(cfg.fixed_etch_depth_nm,),
        sidewall_angle_deg=cfg.sidewall_angle_deg,
        fundamental_wavelengths_nm=cfg.official_wavelengths_nm(),
        initial_number_of_modes=cfg.initial_number_of_modes,
        fallback_number_of_modes=cfg.fallback_number_of_modes,
        predicted_search_fallback=cfg.predicted_search_fallback,
        predicted_field_overlap_min=cfg.field_overlap_min,
        te_threshold=cfg.polarization_threshold, threads=cfg.threads,
        top_cladding=cfg.top_cladding, field_dx_nm=cfg.field_dx_nm,
    )


def ensure_output_dirs(cfg: Config) -> None:
    for path in (cfg.run_dir/"model", cfg.run_dir/"data"/"mat", cfg.run_dir/"data"/"figures", cfg.run_dir/"logs"):
        path.mkdir(parents=True, exist_ok=True)


def prepare_run(cfg: Config) -> logging.Logger:
    cfg.validate(); ensure_output_dirs(cfg)
    (cfg.run_dir/"data"/"run_config.json").write_text(
        json.dumps(serializable_config(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    logger = logging.getLogger("dispersion_comsol")
    logger.setLevel(logging.INFO); logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(cfg.run_dir/"logs"/"run.log", encoding="utf-8")
    fh.setFormatter(formatter); logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(formatter); logger.addHandler(sh)
    return logger


def mat_path(cfg: Config, point: ScanPoint, mesh_profile: str = "default") -> Path:
    suffix = "" if mesh_profile == "default" else f"_{mesh_profile}"
    name = (f"{cfg.core_material}_T{point.thickness_nm:g}_W{point.top_width_nm:g}_"
            f"D{point.etch_depth_nm:g}_A{cfg.sidewall_angle_deg:g}_L{point.wavelength_nm:g}_"
            f"{point.branch}{suffix}.mat")
    return cfg.run_dir/"data"/"mat"/name


def point_complete(path: Path) -> bool:
    if not path.exists(): return False
    try:
        data = loadmat(path)
        return all(k in data for k in ("neff_real", "field", "branch", "wavelength")) and data["field"].size > 10
    except Exception:
        return False


def _load_index(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path) if path.exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _append_index(path: Path, row: dict[str, Any]) -> None:
    old = _load_index(path)
    key = ["thickness_nm","etch_depth_nm","top_width_nm","wavelength_nm","branch","mesh_profile"]
    out = pd.concat([old,pd.DataFrame([row])],ignore_index=True)
    if all(k in out for k in key):
        out = out.drop_duplicates(key,keep="last").sort_values(key)
    tmp = path.with_suffix(".tmp.csv"); out.to_csv(tmp,index=False); os.replace(tmp,path)


def _set_mode_count(model: Any, count: int) -> None:
    model.java.study("std1").feature("mode").set("neigs", str(count))
    try: model.java.sol().remove("sol1")
    except Exception: pass
    model.java.study("std1").createAutoSequences("all")


def _field_grid(model: Any, cfg: Config, mode_index: int) -> tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray]:
    x,y,ex,ey = model.evaluate(["x","y","ewfd.Ex","ewfd.Ey"], inner=[mode_index])
    x=np.asarray(x).real.reshape(-1); y=np.asarray(y).real.reshape(-1)
    gx=np.arange(-cfg.window_width_um/2,cfg.window_width_um/2+1e-9,cfg.field_dx_nm/1000)
    gy=np.arange(cfg.window_center_y_um-cfg.window_height_um/2,
                 cfg.window_center_y_um+cfg.window_height_um/2+1e-9,cfg.field_dx_nm/1000)
    xx,yy=np.meshgrid(gx,gy)
    exg=griddata((x,y),np.asarray(ex).reshape(-1),(xx,yy),method="linear",fill_value=0)
    eyg=griddata((x,y),np.asarray(ey).reshape(-1),(xx,yy),method="linear",fill_value=0)
    return gx*1e-6,gy*1e-6,exg,eyg


def extract_branch(model: Any, cfg: Config, point: ScanPoint,
                   reference: dict[str,Any] | None = None) -> dict[str,Any]:
    neff=np.atleast_1d(np.asarray(model.evaluate("ewfd.neff"))).reshape(-1)
    te=np.atleast_1d(np.asarray(model.evaluate(
        "intphys(abs(ewfd.Ex)^2)/(intphys(abs(ewfd.Ex)^2+abs(ewfd.Ey)^2)+1e-300)"))).reshape(-1)
    pml=np.atleast_1d(np.asarray(model.evaluate(
        "abs(intpml(ewfd.normE^2))/(abs(intcore(ewfd.normE^2))+abs(intpml(ewfd.normE^2))+1e-300)"))).reshape(-1)
    loss=np.atleast_1d(np.asarray(model.evaluate("ewfd.dampzdB",unit="dB/m"))).reshape(-1)
    candidates=[]
    for i,n in enumerate(neff):
        frac=float(np.real(te[i])); pf=float(np.real(pml[i]))
        dominant = frac > cfg.polarization_threshold if point.branch=="TE0" else frac < (1-cfg.polarization_threshold)
        if dominant and pf < cfg.pml_fraction_max and 1 < float(np.real(n)) < 3:
            candidates.append((i+1,complex(n),frac,pf,float(np.real(loss[i]))))
    if not candidates:
        raise RuntimeError(f"No guided {point.branch} candidate")
    accepted=[]
    for mode_index,n,frac,pf,ls in candidates:
        gx,gy,ex,ey=_field_grid(model,cfg,mode_index)
        field=ex if point.branch=="TE0" else ey
        denom=float(np.vdot(field,field).real)+1e-300
        symmetry=float(np.real(np.vdot(field,np.fliplr(field)))/denom)
        peak_col=int(np.unravel_index(np.abs(field).argmax(),field.shape)[1])
        peak_x_um=float(gx[peak_col]*1e6)
        centered=abs(peak_x_um)<=point.top_width_nm/2000
        overlap=1.0 if reference is None else normalized_field_overlap(reference["field"],field)
        if symmetry>=cfg.even_symmetry_min and centered and (reference is None or overlap>=cfg.field_overlap_min):
            accepted.append({"mode_index":mode_index,"neff":n,"TE_fraction":frac,"TM_fraction":1-frac,
                "pml_fraction":pf,"loss":ls,"x":gx,"y":gy,"Ex":ex,"Ey":ey,"field":field,
                "even_symmetry":symmetry,"peak_x_um":peak_x_um,"centered_peak":centered,
                "field_overlap":overlap,"branch":point.branch})
    if not accepted:
        raise RuntimeError(f"No {point.branch} passed field-shape/overlap criteria")
    if reference is not None:
        return max(accepted,key=lambda r:(r["field_overlap"],r["neff"].real))
    return max(accepted,key=lambda r:r["neff"].real)


def primary_shift(cfg: Config, branch: str) -> str:
    # x-cut mapping: Ex is extraordinary (quasi-TE); Ey is ordinary (quasi-TM).
    return "ne_sell(lam_um)" if branch=="TE0" else "no_sell(lam_um)"


def predict_neff(cfg: Config, wavelength_nm: float, cache: dict[float,dict[str,Any]]) -> tuple[float,list[float],float]:
    xs=sorted(x for x,v in cache.items() if x!=wavelength_nm and np.isfinite(v["neff_real"]))
    if not xs: raise PredictionUnavailable("no validated same-geometry/same-branch anchor")
    left=[x for x in xs if x<wavelength_nm]; right=[x for x in xs if x>wavelength_nm]
    anchors=[left[-1],right[0]] if left and right else (xs[:2] if not left else xs[-2:])
    predicted=float(cache[anchors[0]]["neff_real"])
    if len(anchors)==2:
        a,b=anchors
        predicted += (float(cache[b]["neff_real"])-float(cache[a]["neff_real"]))*(wavelength_nm-a)/(b-a)
    backend=load_backend(); upper=max(backend.refractive_indices(cfg.core_material,wavelength_nm))
    if not 1 < predicted < upper: raise PredictionUnavailable(f"predicted neff {predicted:g} outside physical bounds")
    reference=min(xs,key=lambda x:abs(x-wavelength_nm))
    return predicted,anchors,reference


def solve_at_shift(model: Any, cfg: Config, point: ScanPoint, shift: str | float,
                   reference: dict[str,Any] | None, counts: Iterable[int]) -> tuple[dict[str,Any],int]:
    model.parameter("nshift",str(shift)); last=None
    for count in dict.fromkeys(counts):
        try:
            _set_mode_count(model,count); model.solve()
            return extract_branch(model,cfg,point,reference),count
        except Exception as exc:
            last=exc
    raise RuntimeError(f"{point.branch} selection failed: {last}")


def solve_with_prediction(model: Any, cfg: Config, point: ScanPoint,
                          cache: dict[float,dict[str,Any]], prediction_only: bool=False,
                          primary_error: str="") -> tuple[dict[str,Any],int]:
    original=primary_shift(cfg,point.branch); model.parameter("nshift",original)
    reference=None
    if cache:
        nearest=min(cache,key=lambda x:abs(x-point.wavelength_nm)); reference=cache[nearest]
    try:
        if not prediction_only:
            try:
                result,count=solve_at_shift(model,cfg,point,original,reference,
                                            (cfg.initial_number_of_modes,cfg.fallback_number_of_modes))
            except Exception as exc:
                primary_error=f"{type(exc).__name__}: {exc}"
                if not cfg.predicted_search_fallback: raise
            else:
                result.update(search_method="primary",search_shift_used=original,
                              primary_search_error="",prediction_anchors_nm=[])
                return result,count
        predicted,anchors,refwl=predict_neff(cfg,point.wavelength_nm,cache)
        result,count=solve_at_shift(model,cfg,point,predicted,cache[refwl],(cfg.fallback_number_of_modes,))
        result.update(search_method="predicted_fallback",search_shift_used=predicted,
                      primary_search_error=primary_error,prediction_anchors_nm=anchors,
                      overlap_reference_wavelength_nm=refwl)
        return result,count
    finally:
        model.parameter("nshift",original)


def save_point(cfg: Config, point: ScanPoint, result: dict[str,Any], modes: int,
               mesh_profile: str="default") -> Path:
    path=mat_path(cfg,point,mesh_profile)
    payload={
        "film_thickness":point.thickness_nm*1e-9,"etch_depth":point.etch_depth_nm*1e-9,
        "top_width":point.top_width_nm*1e-9,"sidewall_angle":cfg.sidewall_angle_deg,
        "wavelength":point.wavelength_nm*1e-9,"branch":point.branch,"official":point.official,
        "neff":result["neff"],"neff_real":result["neff"].real,"mode_index":result["mode_index"],
        "TE_fraction":result["TE_fraction"],"TM_fraction":result["TM_fraction"],
        "pml_fraction":result["pml_fraction"],"loss_dB_m":result["loss"],
        "x":result["x"],"y":result["y"],"Ex":result["Ex"],"Ey":result["Ey"],"field":result["field"],
        "even_symmetry":result["even_symmetry"],"centered_peak":result["centered_peak"],
        "peak_x_um":result["peak_x_um"],"field_overlap":result["field_overlap"],
        "search_method":result["search_method"],"search_shift_used":str(result["search_shift_used"]),
        "primary_search_error":result.get("primary_search_error",""),
        "prediction_anchors_nm":np.asarray(result.get("prediction_anchors_nm",[]),float),
        "modes_requested":modes,"mesh_profile":mesh_profile,"core_material":cfg.core_material,
        "top_cladding":cfg.top_cladding,"comsol_version":"6.4.0.293",
    }
    tmp=path.with_suffix(".tmp.mat"); savemat(tmp,payload,do_compression=True); os.replace(tmp,path)
    return path


def load_cached_point(path: Path) -> dict[str,Any]:
    d=loadmat(path,squeeze_me=True)
    return {"neff_real":float(np.real(d["neff_real"])),"field":np.asarray(d["field"]),
            "x":np.asarray(d["x"]).reshape(-1),"y":np.asarray(d["y"]).reshape(-1)}


def ordered_wavelengths(wavelengths: Sequence[float], center: float | None=None) -> list[float]:
    values=sorted(set(map(float,wavelengths)))
    center=float(center if center is not None else (values[0]+values[-1])/2)
    return sorted(values,key=lambda x:(abs(x-center),x))


def configure_geometry(model: Any, geometry: tuple[float,float,float]) -> None:
    t,d,w=geometry
    model.parameter("tfilm",f"{t}[nm]"); model.parameter("etch",f"{d}[nm]"); model.parameter("wtop",f"{w}[nm]")
    model.build(); model.mesh()


def solve_geometry(model: Any, cfg: Config, geometry: tuple[float,float,float],
                   wavelengths_nm: Sequence[float], *, official_wavelengths: set[float],
                   mesh_profile: str="default", force: bool=False,
                   predicted_center_nm: float | None=None,
                   branches: Sequence[str] | None=None) -> None:
    logger=logging.getLogger("dispersion_comsol"); configure_geometry(model,geometry)
    index_path=cfg.run_dir/"data"/"scan_index.csv"
    for branch in (tuple(branches) if branches is not None else cfg.mode_branches):
        cache: dict[float,dict[str,Any]]={}
        # Auxiliary/refined solves must still see validated formal-grid fields;
        # otherwise a failed midpoint has no same-geometry prediction anchor.
        cache_wavelengths=set(map(float,wavelengths_nm)) | set(map(float,official_wavelengths))
        for wl in cache_wavelengths:
            point=ScanPoint(*geometry,wl,branch,wl in official_wavelengths)
            path=mat_path(cfg,point,mesh_profile)
            if point_complete(path) and not force:
                cache[wl]=load_cached_point(path)
        queue=[(wl,False,"") for wl in ordered_wavelengths(wavelengths_nm,predicted_center_nm)]
        for wl,prediction_only,primary_error in queue:
            point=ScanPoint(*geometry,wl,branch,wl in official_wavelengths); path=mat_path(cfg,point,mesh_profile)
            if point_complete(path) and not force:
                logger.info("SKIP %s",path.name); continue
            model.parameter("lda0",f"{wl}[nm]"); started=datetime.now().isoformat(); result={}
            try:
                result,modes=solve_with_prediction(model,cfg,point,cache,prediction_only,primary_error)
                save_point(cfg,point,result,modes,mesh_profile)
                cache[wl]={"neff_real":float(result["neff"].real),"field":result["field"],
                           "x":result["x"],"y":result["y"]}
                status="success"; error=""
                logger.info("OK %s lambda=%g neff=%.10g overlap=%.4f method=%s",
                            branch,wl,result["neff"].real,result["field_overlap"],result["search_method"])
            except PredictionUnavailable as exc:
                error=f"{type(exc).__name__}: {exc}"
                if not prediction_only:
                    status="deferred"; queue.append((wl,True,error)); logger.warning("DEFER %s %g: %s",branch,wl,error)
                else:
                    status="failed"; logger.error("FAILED %s %g: %s",branch,wl,error)
            except Exception as exc:
                error=f"{type(exc).__name__}: {exc}"; status="failed"
                logger.error("FAILED %s %g: %s\n%s",branch,wl,error,traceback.format_exc())
            _append_index(index_path,{"fingerprint":fingerprint(cfg),"thickness_nm":geometry[0],
                "etch_depth_nm":geometry[1],"top_width_nm":geometry[2],"wavelength_nm":wl,
                "branch":branch,"official":point.official,"mesh_profile":mesh_profile,"status":status,
                "started":started,"finished":datetime.now().isoformat(),"mat":str(path),"error":error,
                "search_method":result.get("search_method",status),"search_shift_used":result.get("search_shift_used",""),
                "primary_search_error":result.get("primary_search_error",primary_error)})


def geometry_mode_frame(cfg: Config, geometry: tuple[float,float,float], branch: str,
                        mesh_profile: str="default") -> pd.DataFrame:
    rows=[]
    matdir=cfg.run_dir/"data"/"mat"
    for path in matdir.glob(f"{cfg.core_material}_T{geometry[0]:g}_W{geometry[2]:g}_D{geometry[1]:g}_A{cfg.sidewall_angle_deg:g}_L*_{branch}{'' if mesh_profile=='default' else '_'+mesh_profile}.mat"):
        try:
            d=loadmat(path,squeeze_me=True)
            if str(d["branch"])!=branch: continue
            rows.append({"wavelength_nm":float(d["wavelength"])*1e9,"neff":float(np.real(d["neff_real"])),
                         "official":bool(d.get("official",True)),"TE_fraction":float(d["TE_fraction"]),
                         "TM_fraction":float(d["TM_fraction"]),"mode_index":int(d["mode_index"]),
                         "field_overlap":float(d["field_overlap"]),"even_symmetry":float(d["even_symmetry"]),
                         "pml_fraction":float(d["pml_fraction"]),"loss_dB_m":float(d["loss_dB_m"])})
        except Exception:
            continue
    if not rows: return pd.DataFrame()
    scalar=pd.DataFrame(rows).drop_duplicates("wavelength_nm",keep="last").sort_values("wavelength_nm")
    dispersion=compute_dispersion(scalar.wavelength_nm,scalar.neff)
    return dispersion.merge(scalar,on=["wavelength_nm","neff"],how="left")


def refinement_wavelengths(cfg: Config, frame: pd.DataFrame) -> list[float]:
    if frame.empty: return []
    err=frame.gvd_fit_error_ps2_km.to_numpy(float); value=abs(frame.gvd_ps2_km.to_numpy(float))
    bad=np.flatnonzero(np.isfinite(err)&(err>np.maximum(cfg.gvd_absolute_fit_tol_ps2_km,cfg.gvd_relative_fit_tol*value)))
    xs=frame.wavelength_nm.to_numpy(float); additions=set()
    for i in bad:
        if i>0: additions.add((xs[i-1]+xs[i])/2)
        if i+1<len(xs): additions.add((xs[i]+xs[i+1])/2)
    existing=set(xs)
    return sorted(x for x in additions if cfg.wavelength_min_nm<=x<=cfg.wavelength_max_nm and x not in existing)


def export_dispersion(cfg: Config, geometry: tuple[float,float,float], branch: str,
                      prefix: str="dispersion") -> tuple[pd.DataFrame,list[ZDPRoot]]:
    frame=geometry_mode_frame(cfg,geometry,branch)
    if frame.empty: return frame,[]
    outdir=cfg.run_dir/"data"; figdir=outdir/"figures"
    tag=f"T{geometry[0]:g}_W{geometry[2]:g}_D{geometry[1]:g}_{branch}"
    frame.to_csv(outdir/f"{prefix}_{tag}.csv",index=False)
    formal=frame[frame.official.astype(bool)]
    plots=(
        ("beta1",formal.beta1_ps_mm,r"$d\beta/d\omega$ (ps/mm)",False),
        ("gvd",formal.gvd_ps2_km,r"GVD $\beta_2$ (ps$^2$/km)",True),
        ("D",formal.dispersion_D_ps_nm_km,"D (ps/(nm km))",True),
    )
    for metric,values,ylabel,zero_line in plots:
        fig,ax=plt.subplots(figsize=(8,5.5))
        ax.plot(formal.wavelength_nm,values,"o-")
        if zero_line: ax.axhline(0,color="k",lw=.8)
        ax.set_xlabel("Wavelength (nm)"); ax.set_ylabel(ylabel)
        ax.set_title(f"{cfg.core_material} {cfg.top_cladding} {tag} {metric}")
        ax.grid(True,alpha=.25); fig.tight_layout()
        fig.savefig(figdir/f"{prefix}_{metric}_{tag}.png",dpi=220); plt.close(fig)
    return frame,find_zdp_roots(frame,cfg.zdp_wavelength_tolerance_nm)


def local_root_stencil(cfg: Config, roots: Sequence[ZDPRoot]) -> list[float]:
    step=max(cfg.zdp_wavelength_tolerance_nm*5.0,1.0)
    return sorted(set(round(r.wavelength_nm+i*step,9) for r in roots for i in range(-3,4)
                      if cfg.wavelength_min_nm <= r.wavelength_nm+i*step <= cfg.wavelength_max_nm))


def nearest_root(target: ZDPRoot, candidates: Sequence[ZDPRoot], max_distance_nm: float) -> ZDPRoot | None:
    if not candidates: return None
    root=min(candidates,key=lambda r:abs(r.wavelength_nm-target.wavelength_nm))
    return root if abs(root.wavelength_nm-target.wavelength_nm)<=max_distance_nm else None


def cross_mesh_field_overlap(cfg: Config, geometry: tuple[float,float,float], branch: str,
                             wavelengths_nm: Sequence[float], target_nm: float) -> float:
    if not wavelengths_nm: return float("nan")
    wl=min(wavelengths_nm,key=lambda value:abs(value-target_nm))
    point=ScanPoint(*geometry,wl,branch,False)
    default_path=mat_path(cfg,point,"default"); refined_path=mat_path(cfg,point,"refined")
    if not point_complete(default_path) or not point_complete(refined_path): return float("nan")
    a=loadmat(default_path,squeeze_me=True)["field"]
    b=loadmat(refined_path,squeeze_me=True)["field"]
    return normalized_field_overlap(a,b)


def create_models(client: Any, cfg: Config, mesh_profile: str="default") -> Any:
    backend=load_backend(); base=backend_config(cfg)
    return backend.build_comsol_model(client,base,mesh_profile)


def run_wavelength_scan(cfg: Config, force: bool=False) -> None:
    logger=prepare_run(cfg); backend=load_backend()
    for wl in cfg.official_wavelengths_nm(): backend.refractive_indices(cfg.core_material,wl)
    import mph
    client=mph.Client(cores=cfg.threads,version="6.4"); model=None
    try:
        model=create_models(client,cfg,"default"); geometry=cfg.fixed_geometry(); official=set(cfg.official_wavelengths_nm())
        solve_geometry(model,cfg,geometry,cfg.official_wavelengths_nm(),official_wavelengths=official,force=force)
        if cfg.auto_refine_wavelength:
            for _ in range(cfg.max_wavelength_refinement_rounds):
                additions=set()
                for branch in cfg.mode_branches: additions.update(refinement_wavelengths(cfg,geometry_mode_frame(cfg,geometry,branch)))
                if not additions: break
                logger.info("AUXILIARY wavelength refinement: %s",sorted(additions))
                solve_geometry(model,cfg,geometry,sorted(additions),official_wavelengths=official,force=force)
        for branch in cfg.mode_branches:
            frame,_=export_dispersion(cfg,geometry,branch)
            logger.info("EXPORTED %s rows=%d official=%d",branch,len(frame),int(frame.official.sum()) if not frame.empty else 0)
        model_path=cfg.run_dir/"model"/f"{cfg.core_material}_xcut_dispersion.mph"; model.save(model_path)
        logger.info("SAVED %s",model_path)
    finally:
        try: client.clear()
        except Exception: pass


def gp_observations(cfg: Config, scanned: Sequence[tuple[float,float,float]], branch: str) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    x=[]; y=[]; noise=[]
    for geometry in scanned:
        frame=geometry_mode_frame(cfg,geometry,branch)
        for row in frame.itertuples():
            if np.isfinite(row.gvd_ps2_km):
                x.append([*geometry,row.wavelength_nm]); y.append(row.gvd_ps2_km)
                noise.append(row.gvd_fit_error_ps2_km if np.isfinite(row.gvd_fit_error_ps2_km) else 1.0)
    return np.asarray(x,float),np.asarray(y,float),np.asarray(noise,float)


def run_zdp_scan(cfg: Config, force: bool=False) -> None:
    logger=prepare_run(cfg); candidates=list(geometry_lattice(cfg.zdp_thicknesses_nm,cfg.zdp_etch_axis,cfg.zdp_width_axis))
    seeds=maximin_seeds(candidates,min(cfg.zdp_initial_geometries,len(candidates)))
    import mph
    client=mph.Client(cores=cfg.threads,version="6.4"); model=None; scanned=[]; excluded=[]
    official=set(cfg.official_wavelengths_nm()); budget=cfg.max_geometry_solves or len(candidates)
    try:
        model=create_models(client,cfg,"default")
        queue=list(seeds)
        while queue or (len(scanned)<min(budget,len(candidates))):
            if queue: geometry=queue.pop(0)
            else:
                remaining=[g for g in candidates if g not in scanned and g not in excluded]
                if not remaining: break
                scores={g:-np.inf for g in remaining}
                for branch in cfg.mode_branches:
                    x,y,noise=gp_observations(cfg,scanned,branch)
                    if len(x)<2: continue
                    gp=MaternGP().fit(x,y,noise)
                    for g in remaining:
                        q=np.asarray([[*g,wl] for wl in cfg.official_wavelengths_nm()])
                        mean,sigma=gp.predict(q); scores[g]=max(scores[g],float(np.max(straddle_score(mean,sigma,cfg.gp_confidence_sigma))))
                best=max(scores,key=scores.get)
                if scores[best]<0:
                    excluded=remaining; break
                geometry=best
            if geometry in scanned: continue
            logger.info("ZDP GEOMETRY %d/%d %s",len(scanned)+1,budget,geometry)
            solve_geometry(model,cfg,geometry,cfg.official_wavelengths_nm(),official_wavelengths=official,force=force)
            scanned.append(geometry)
            if len(scanned)>=budget: break
        # Reproducible audit of high-confidence exclusions.
        if excluded and len(scanned)<budget:
            rng=np.random.default_rng(20260902); count=min(20,max(3,math.ceil(0.05*len(excluded))),budget-len(scanned))
            audit=[excluded[i] for i in sorted(rng.choice(len(excluded),size=min(count,len(excluded)),replace=False))]
            audit_positive=False
            for geometry in audit:
                logger.info("ZDP EXCLUSION AUDIT %s",geometry)
                solve_geometry(model,cfg,geometry,cfg.official_wavelengths_nm(),official_wavelengths=official,force=force)
                scanned.append(geometry)
                for branch in cfg.mode_branches:
                    frame=geometry_mode_frame(cfg,geometry,branch)
                    if not frame.empty and find_zdp_roots(frame,cfg.zdp_wavelength_tolerance_nm):
                        audit_positive=True
            if audit_positive:
                logger.warning("EXCLUSION AUDIT found a ZDP candidate; reopening the excluded region")
                reopened=[g for g in excluded if g not in scanned]
                excluded=[]
                # A failed confidence audit invalidates screening. Conservatively
                # solve the reopened region up to the configured budget.
                for geometry in reopened:
                    if len(scanned)>=budget: break
                    solve_geometry(model,cfg,geometry,cfg.official_wavelengths_nm(),official_wavelengths=official,force=force)
                    scanned.append(geometry)
        # Discover candidates without exporting curves for negative structures.
        root_candidates: dict[tuple[tuple[float,float,float],str],list[ZDPRoot]]={}
        for geometry in scanned:
            for branch in cfg.mode_branches:
                frame=geometry_mode_frame(cfg,geometry,branch)
                roots=find_zdp_roots(frame,cfg.zdp_wavelength_tolerance_nm) if not frame.empty else []
                if roots: root_candidates[(geometry,branch)]=roots

        # Refine the default-mesh roots locally first, then independently solve
        # the identical wavelength stencils on the refined mesh. Only roots
        # agreeing to the configured tolerance become user-facing positives.
        for (geometry,branch),roots in list(root_candidates.items()):
            stencil=local_root_stencil(cfg,roots)
            solve_geometry(model,cfg,geometry,stencil,official_wavelengths=official,
                           force=force,branches=(branch,))
            frame=geometry_mode_frame(cfg,geometry,branch)
            refined_default=find_zdp_roots(frame,cfg.zdp_wavelength_tolerance_nm)
            root_candidates[(geometry,branch)]=[
                match for root in roots
                if (match:=nearest_root(root,refined_default,max(cfg.wavelength_step_nm,15.0))) is not None]

        summary=[]; refined_model=None
        if any(root_candidates.values()):
            refined_model=create_models(client,cfg,"refined")
            for (geometry,branch),roots in root_candidates.items():
                if not roots: continue
                stencil=local_root_stencil(cfg,roots)
                solve_geometry(refined_model,cfg,geometry,stencil,official_wavelengths=set(),
                               mesh_profile="refined",force=force,branches=(branch,))
                refined_frame=geometry_mode_frame(cfg,geometry,branch,"refined")
                refined_roots=find_zdp_roots(refined_frame,cfg.zdp_wavelength_tolerance_nm) if not refined_frame.empty else []
                confirmed=[]
                for root in roots:
                    check=nearest_root(root,refined_roots,max(15.0,cfg.wavelength_step_nm))
                    mesh_overlap=cross_mesh_field_overlap(cfg,geometry,branch,stencil,root.wavelength_nm)
                    if (check is None or abs(check.wavelength_nm-root.wavelength_nm)>cfg.zdp_wavelength_tolerance_nm
                            or not np.isfinite(mesh_overlap) or mesh_overlap<cfg.field_overlap_min):
                        logger.warning("ZDP REJECT mesh disagreement %s %s default=%g refined=%s",
                                       geometry,branch,root.wavelength_nm,None if check is None else check.wavelength_nm)
                        continue
                    confirmed.append(root)
                    summary.append({"branch":branch,"root_index":len(confirmed),"thickness_nm":geometry[0],
                        "etch_depth_nm":geometry[1],"top_width_nm":geometry[2],
                        "zdp_wavelength_nm":root.wavelength_nm,"refined_zdp_wavelength_nm":check.wavelength_nm,
                        "mesh_delta_nm":abs(check.wavelength_nm-root.wavelength_nm),"root_kind":root.kind,
                        "default_refined_field_overlap":mesh_overlap,
                        "uncertainty_nm":max(root.uncertainty_nm,check.uncertainty_nm),
                        "validation":"confirmed_default_and_refined_mesh"})
                if confirmed:
                    export_dispersion(cfg,geometry,branch,prefix="zdp_curve")
        out=pd.DataFrame(summary); data_dir=cfg.run_dir/"data"
        if not out.empty:
            out.to_csv(data_dir/"zdp_summary.csv",index=False)
        elif any(root_candidates.values()):
            (data_dir/"inconclusive.txt").write_text("ZDP candidates were found but did not pass default/refined mesh agreement and cross-mesh field-overlap validation.\n",encoding="utf-8")
        elif len(scanned)>=len(candidates)-len(excluded):
            (data_dir/"no_zdp.txt").write_text("No confirmed TE0 or TM0 zero-dispersion point in the configured domain at 3-sigma screening confidence.\n",encoding="utf-8")
        else:
            (data_dir/"inconclusive.txt").write_text(f"Geometry budget exhausted after {len(scanned)} structures; unresolved candidates remain.\n",encoding="utf-8")
        pd.DataFrame([{"thickness_nm":g[0],"etch_depth_nm":g[1],"top_width_nm":g[2],"status":"scanned"} for g in scanned]
                     +[{"thickness_nm":g[0],"etch_depth_nm":g[1],"top_width_nm":g[2],"status":"3sigma_excluded"} for g in excluded]
                    ).to_csv(data_dir/"zdp_geometry_audit.csv",index=False)
        model.save(cfg.run_dir/"model"/f"{cfg.core_material}_xcut_zdp_scan.mph")
        if refined_model is not None:
            refined_model.save(cfg.run_dir/"model"/f"{cfg.core_material}_xcut_zdp_scan_refined.mph")
    finally:
        try: client.clear()
        except Exception: pass


def postprocess(cfg: Config) -> None:
    cfg.validate(); ensure_output_dirs(cfg)
    if cfg.run_mode=="wavelength_scan":
        for branch in cfg.mode_branches: export_dispersion(cfg,cfg.fixed_geometry(),branch)
    else:
        index=_load_index(cfg.run_dir/"data"/"scan_index.csv")
        if index.empty: raise FileNotFoundError("No scan index to postprocess")
        geometries=sorted(set(zip(index.thickness_nm,index.etch_depth_nm,index.top_width_nm)))
        rows=[]
        for g in geometries:
            for branch in cfg.mode_branches:
                frame=geometry_mode_frame(cfg,g,branch)
                roots=find_zdp_roots(frame,cfg.zdp_wavelength_tolerance_nm) if not frame.empty else []
                refined=geometry_mode_frame(cfg,g,branch,"refined")
                refined_roots=find_zdp_roots(refined,cfg.zdp_wavelength_tolerance_nm) if not refined.empty else []
                confirmed=[]
                for r in roots:
                    check=nearest_root(r,refined_roots,max(15.0,cfg.wavelength_step_nm))
                    if check is not None and abs(check.wavelength_nm-r.wavelength_nm)<=cfg.zdp_wavelength_tolerance_nm:
                        confirmed.append((r,check))
                if confirmed: export_dispersion(cfg,g,branch,prefix="zdp_curve")
                for i,(r,check) in enumerate(confirmed,1): rows.append({"branch":branch,"root_index":i,"thickness_nm":g[0],"etch_depth_nm":g[1],"top_width_nm":g[2],"zdp_wavelength_nm":r.wavelength_nm,"refined_zdp_wavelength_nm":check.wavelength_nm,"mesh_delta_nm":abs(check.wavelength_nm-r.wavelength_nm),"root_kind":r.kind,"uncertainty_nm":max(r.uncertainty_nm,check.uncertainty_nm)})
        pd.DataFrame(rows).to_csv(cfg.run_dir/"data"/"zdp_summary.csv",index=False)


def reopen_model(cfg: Config) -> None:
    import mph
    paths=sorted((cfg.run_dir/"model").glob("*.mph"))
    if not paths: raise FileNotFoundError("No MPH model saved")
    client=mph.Client(cores=cfg.threads,version="6.4")
    try:
        model=client.load(paths[-1]); problems=model.problems()
        if problems: raise RuntimeError(f"Saved MPH reports problems: {problems}")
        print(f"REOPEN PASS problems=[] {paths[-1]}")
    finally: client.clear()


def validate_outputs(cfg: Config) -> dict[str,Any]:
    ensure_output_dirs(cfg)
    index=_load_index(cfg.run_dir/"data"/"scan_index.csv")
    result={"run_dir":str(cfg.run_dir),"index_rows":len(index),"passed":False}
    if cfg.run_mode=="wavelength_scan":
        expected=len(cfg.official_wavelengths_nm())*len(cfg.mode_branches)
        ok=index[(index.status.isin(["success","skipped"])) & index.official.astype(bool)] if not index.empty else index
        csvs=list((cfg.run_dir/"data").glob("dispersion_*.csv")); pngs=list((cfg.run_dir/"data"/"figures").glob("dispersion_*.png"))
        result.update(expected_official_points=expected,successful_official_points=len(ok),csv_count=len(csvs),png_count=len(pngs))
        result["passed"]=(len(ok)>=expected and len(csvs)>=len(cfg.mode_branches)
                          and len(pngs)>=3*len(cfg.mode_branches))
    (cfg.run_dir/"data"/"validation.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def main(argv: Sequence[str] | None=None) -> None:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    run=sub.add_parser("run"); run.add_argument("--force",action="store_true")
    sub.add_parser("postprocess"); sub.add_parser("reopen"); sub.add_parser("validate")
    args=parser.parse_args(argv); cfg=Config(); cfg.validate()
    if args.command=="run":
        (run_wavelength_scan if cfg.run_mode=="wavelength_scan" else run_zdp_scan)(cfg,args.force)
    elif args.command=="postprocess": postprocess(cfg)
    elif args.command=="reopen": reopen_model(cfg)
    elif args.command=="validate": print(json.dumps(validate_outputs(cfg),indent=2))


if __name__=="__main__": main()
