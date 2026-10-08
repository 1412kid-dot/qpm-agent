"""COMSOL 6.4 FEM automation for x-cut LN/LT rib-waveguide SHG/QPM.

The COMSOL model owns the wavelength-dependent material functions.  Python
changes only ``lda0``; no per-wavelength refractive-index override is used.
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
from scipy.interpolate import griddata

# ----------------------------- USER CONFIGURATION ---------------------------
CORE_MATERIAL = "LN"                 # "LN" or "LT"; rib and slab are locked
TARGET_MODE = "TE0"                  # "TE0" or "TM0"; one target per run
RUN_PROFILE = "full"                 # "preview", "mini", or "full"
RUN_NAME = "LN_SIO2_T398_400_402_W2000_D200_A67_L1480_1620"
OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs" / "legacy"
MODE_BASELINE_ROOT = Path(os.environ.get("QPM_MODE_BASELINE_ROOT", str(Path(__file__).resolve().parent / "mode_baseline")))

FILM_THICKNESSES_NM = (398.0, 400.0, 402.0)
TOP_WIDTHS_NM = (2000.0,)             # add values to enable width scans
ETCH_DEPTHS_NM = (200.0,)
# Interior sidewall angle measured from the horizontal bottom plane.
# With this convention an etched rib has wbot = wtop + 2*etch/tan(angle).
SIDEWALL_ANGLE_DEG = 67.0
FUNDAMENTAL_WAVELENGTHS_NM = tuple(float(x) for x in range(1480, 1621, 20))
INITIAL_NUMBER_OF_MODES = 20
FALLBACK_NUMBER_OF_MODES = 40
ENABLE_ADJACENT_MODE_TRACKING = True
TRACKING_NUMBER_OF_MODES = 6
TRACKING_FIELD_OVERLAP_MIN = 0.95
ROBUST_RESEED_INTERVAL = 4
# Normal points keep the original search. Predict a center ONLY after failure.
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
TOP_CLADDING = "SIO2"                 # "SIO2" or "AIR"
EXPERIMENTAL_QPM_DATA = (
    # Fabrication-stage design: leave empty (or use (0, 0)) for simulation only.
    # After fabrication, enter (wavelength_nm, period_um) points here.
)
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
    mode_baseline_root: Path = MODE_BASELINE_ROOT
    film_thicknesses_nm: tuple[float, ...] = FILM_THICKNESSES_NM
    top_widths_nm: tuple[float, ...] = TOP_WIDTHS_NM
    etch_depths_nm: tuple[float, ...] = ETCH_DEPTHS_NM
    sidewall_angle_deg: float = SIDEWALL_ANGLE_DEG
    fundamental_wavelengths_nm: tuple[float, ...] = FUNDAMENTAL_WAVELENGTHS_NM
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
    experimental_qpm_data: Any = EXPERIMENTAL_QPM_DATA

    def validate(self) -> None:
        normalize_experimental_qpm_data(self.experimental_qpm_data)
        if self.target_mode not in {"TE0","TM0"}:
            raise ValueError("TARGET_MODE must be 'TE0' or 'TM0'.")
        if self.tracking_number_of_modes < 1 or self.robust_reseed_interval < 1:
            raise ValueError("Tracking mode count and robust reseed interval must be positive.")
        if not 0 < self.tracking_field_overlap_min <= 1:
            raise ValueError("Tracking field-overlap threshold must be in (0, 1].")
        if not 0 < self.predicted_field_overlap_min <= 1:
            raise ValueError("Prediction field-overlap threshold must be in (0, 1].")
        if self.core_material not in {"LN", "LT"}:
            raise ValueError("CORE_MATERIAL must be 'LN' or 'LT'.")
        if self.run_profile not in {"preview", "mini", "full"}:
            raise ValueError("RUN_PROFILE must be preview, mini, or full.")
        if self.top_cladding not in {"SIO2", "AIR"}:
            raise ValueError("TOP_CLADDING must be 'SIO2' or 'AIR'.")
        if max(self.etch_depths_nm) > min(self.film_thicknesses_nm):
            raise ValueError("Etch depth cannot exceed film thickness.")
        if not 0 < self.sidewall_angle_deg < 90:
            raise ValueError("Sidewall bottom angle must be between 0 and 90 degrees.")
        if not self.top_widths_nm or not self.etch_depths_nm or not self.fundamental_wavelengths_nm:
            raise ValueError("Thickness, width, etch and wavelength lists cannot be empty.")

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_name

    @property
    def sellmeier_source(self) -> str:
        return LN_SOURCE if self.core_material == "LN" else LT_SOURCE

    def wavelengths_nm(self) -> tuple[float, ...]:
        if self.run_profile == "preview":
            return (750.0, 1500.0)
        if self.run_profile == "mini":
            return (750.0, 1500.0)
        vals = set(self.fundamental_wavelengths_nm)
        vals.update(v / 2 for v in self.fundamental_wavelengths_nm)
        return tuple(sorted(vals))

    def thicknesses_nm(self) -> tuple[float, ...]:
        return (800.0,) if self.run_profile == "preview" else self.film_thicknesses_nm

    def widths_nm(self) -> tuple[float, ...]:
        return (3000.0,) if self.run_profile == "preview" else self.top_widths_nm

    def etches_nm(self) -> tuple[float, ...]:
        return (550.0,) if self.run_profile == "preview" else self.etch_depths_nm

    def fingerprint(self) -> str:
        raw = json.dumps(serializable_config(self), sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


def normalize_experimental_qpm_data(data: Any) -> tuple[tuple[float,float], ...]:
    """Normalize empty, zero-placeholder, flat single-point and N-by-2 inputs."""
    if data is None:
        return ()
    try:
        values=np.asarray(data,dtype=float)
    except (TypeError,ValueError) as exc:
        raise ValueError("Experimental QPM data must contain numeric (wavelength_nm, period_um) pairs.") from exc
    if values.size==0:
        return ()
    if values.ndim==1:
        if values.shape!=(2,):
            raise ValueError("Experimental QPM data must be one pair or an N-by-2 array.")
        values=values.reshape(1,2)
    elif values.ndim!=2 or values.shape[1]!=2:
        raise ValueError("Experimental QPM data must be one pair or an N-by-2 array.")
    if not np.isfinite(values).all():
        raise ValueError("Experimental QPM data cannot contain NaN or infinity.")
    placeholders=np.all(values<=0,axis=1)
    values=values[~placeholders]
    if values.size==0:
        return ()
    if np.any(values<=0):
        raise ValueError("Experimental wavelengths and QPM periods must both be positive.")
    values=values[np.argsort(values[:,0],kind="stable")]
    return tuple((float(wavelength),float(period)) for wavelength,period in values)


def serializable_config(cfg: Config) -> dict[str, Any]:
    d = asdict(cfg)
    d["output_root"] = str(cfg.output_root)
    d["mode_baseline_root"] = str(cfg.mode_baseline_root)
    d["experimental_qpm_data"]=[list(point) for point in normalize_experimental_qpm_data(cfg.experimental_qpm_data)]
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
    """Return rib bottom width for an angle measured from the bottom plane."""
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

def build_comsol_model(client: Any, cfg: Config, mesh_profile: str = "default") -> Any:
    """Build a fresh, independently openable 2-D COMSOL model via Java API."""
    if mesh_profile not in {"default", "refined"}:
        raise ValueError("mesh_profile must be 'default' or 'refined'.")
    core_mesh_nm = cfg.core_max_element_nm if mesh_profile == "default" else cfg.refined_core_max_element_nm
    sidewall_mesh_nm = cfg.sidewall_max_element_nm if mesh_profile == "default" else cfg.refined_sidewall_max_element_nm
    background_mesh_nm = cfg.background_max_element_nm if mesh_profile == "default" else cfg.refined_background_max_element_nm
    pml_layers = cfg.pml_layers if mesh_profile == "default" else cfg.refined_pml_layers
    model = client.create(f"{cfg.core_material}_{cfg.crystal_cut}cut_rib_FEM")
    j = model.java
    j.modelPath(str(cfg.run_dir / "model"))
    j.label(f"{cfg.core_material}_{cfg.crystal_cut}cut_rib_FEM.mph")
    j.component().create("comp1", True)
    comp = j.component("comp1")
    comp.geom().create("geom1", 2); geom = comp.geom("geom1"); geom.lengthUnit("um")
    comp.mesh().create("mesh1")
    comp.physics().create("ewfd", "ElectromagneticWavesFrequencyDomain", "geom1")

    # Parameters. COMSOL x/y are old MODE y/z; propagation is COMSOL z.
    params = {
        "lda0": "1560[nm]", "f0": "c_const/lda0", "lam_um": "lda0/1[um]",
        "tfilm": f"{cfg.thicknesses_nm()[0]}[nm]", "wtop": f"{cfg.widths_nm()[0]}[nm]",
        "etch": f"{cfg.etches_nm()[0]}[nm]", "angle": f"{cfg.sidewall_angle_deg}[deg]",
        "wbot": "wtop+2*etch/tan(angle)", "tslab": "tfilm-etch",
        "nshift": ("no_sell(lam_um)" if cfg.search_neff == "MAX_CORE_INDEX" else
                   "ne_sell(lam_um)" if cfg.search_neff == "TE_CORE_INDEX" else
                   search_expression(cfg.crystal_cut, cfg.target_mode)
                   if cfg.search_neff == "TARGET_CORE_INDEX" else str(cfg.search_neff)),
        "hcore": f"{core_mesh_nm}[nm]", "hside": f"{sidewall_mesh_nm}[nm]",
        "hbackground": f"{background_mesh_nm}[nm]", "hpml": f"{1000*cfg.pml_thickness_um/pml_layers}[nm]",
    }
    for key, val in params.items(): j.param().set(key, val)

    # Analytic Sellmeier functions evaluated inside COMSOL for every lda0.
    if cfg.core_material == "LN":
        no_expr = "sqrt(1+2.6734*x^2/(x^2-0.01764)+1.2290*x^2/(x^2-0.05914)+12.614*x^2/(x^2-474.6))"
        ne_expr = "sqrt(1+2.9804*x^2/(x^2-0.02047)+0.5981*x^2/(x^2-0.0666)+8.9543*x^2/(x^2-416.08))"
    else:
        no_expr = "sqrt(4.51224+0.0847522/(x^2-0.19876^2)-0.0239046*x^2)"
        ne_expr = "sqrt(4.52999+0.0844313/(x^2-0.20344^2)-0.0237909*x^2)"
    for tag, name, expr in (("no_fun", "no_sell", no_expr), ("ne_fun", "ne_sell", ne_expr)):
        j.func().create(tag, "Analytic"); fn = j.func(tag); fn.set("funcname", name); fn.set("expr", expr); fn.set("args", "x")
    # Malitson fused-silica dispersion, equivalent to the COMSOL optical library entry.
    sio2 = "sqrt(1+0.6961663*x^2/(x^2-0.0684043^2)+0.4079426*x^2/(x^2-0.1162414^2)+0.8974794*x^2/(x^2-9.896161^2))"
    j.func().create("sio2_fun", "Analytic"); j.func("sio2_fun").set("funcname", "n_sio2"); j.func("sio2_fun").set("expr", sio2); j.func("sio2_fun").set("args", "x")

    # Tiled outer PML and an overlapping inner background. Union preserves interfaces.
    # The configured window is the non-PML physical region, matching MODE.
    # COMSOL's finite-thickness PML is added outside that window.
    ix0, ix1 = -cfg.window_width_um/2, cfg.window_width_um/2
    iy0 = cfg.window_center_y_um-cfg.window_height_um/2
    iy1 = cfg.window_center_y_um+cfg.window_height_um/2
    p = cfg.pml_thickness_um
    xmin, xmax, ymin, ymax = ix0-p, ix1+p, iy0-p, iy1+p
    pml_rects = [
        (xmin,ymin,p,ymax-ymin), (xmax-p,ymin,p,ymax-ymin),
        (ix0,ymin,ix1-ix0,p), (ix0,ymax-p,ix1-ix0,p),
    ]
    for k,(x,y,w,h) in enumerate(pml_rects,1):
        tag=f"pmlr{k}"; geom.create(tag,"Rectangle"); q=geom.feature(tag); q.set("pos",[f"{x}",f"{y}"]); q.set("size",[f"{w}",f"{h}"]); q.set("selresult",True)
    geom.create("pmlu","Union"); geom.feature("pmlu").selection("input").set([f"pmlr{k}" for k in range(1,5)]); geom.feature("pmlu").set("selresult",True)
    geom.create("bg","Rectangle"); geom.feature("bg").set("pos",[str(ix0),str(iy0)]); geom.feature("bg").set("size",[str(ix1-ix0),str(iy1-iy0)]); geom.feature("bg").set("selresult",True)
    # MODE convention: film bottom=-tfilm, ridge top=0.  The slab extends
    # through the lateral PML just as the 100-um-wide MODE slab does.
    geom.create("slab","Rectangle"); geom.feature("slab").set("base","center"); geom.feature("slab").set("pos",["0","-(tfilm+etch)/2"]); geom.feature("slab").set("size",[str(xmax-xmin),"tslab"])
    geom.create("rib","Polygon"); rib=geom.feature("rib"); rib.set("source","table"); rib.set("selresult",True)
    rib.set("table", [["-wbot/2","-etch"],["wbot/2","-etch"],["wtop/2","0"],["-wtop/2","0"]])
    geom.create("coreu","Union"); geom.feature("coreu").selection("input").set(["slab","rib"]); geom.feature("coreu").set("selresult",True)
    # BOX top is the film bottom. Extend it through the lower PML so the
    # absorbing layer continues the silica substrate instead of introducing
    # an artificial air strip below the nominal 2-um BOX.
    geom.create("box","Rectangle"); geom.feature("box").set("pos",[str(xmin),str(ymin)]); geom.feature("box").set("size",[str(xmax-xmin),f"-tfilm-({ymin})"]); geom.feature("box").set("selresult",True)
    # A finalized-geometry boundary selection avoids relying on the original
    # rib tag, which is consumed by the core union.
    geom.create("corebnd","BoxSelection"); geom.feature("corebnd").set("entitydim","1")
    geom.feature("corebnd").set("xmin","-wbot/2-1[nm]"); geom.feature("corebnd").set("xmax","wbot/2+1[nm]")
    geom.feature("corebnd").set("ymin","-tfilm-1[nm]"); geom.feature("corebnd").set("ymax","1[nm]")
    geom.run()

    # PML coordinate stretching on the geometry-created PML selection.
    comp.coordSystem().create("pml1","PML"); comp.coordSystem("pml1").selection().named("geom1_pmlu_dom")

    # Background materials are mutually exclusive. Creating both Air and
    # silica as unselected (all-domain) materials lets COMSOL material priority
    # silently make the nominal silica cladding air.
    if cfg.top_cladding == "SIO2":
        comp.material().create("sio2","Common"); comp.material("sio2").label("Fused silica (Malitson)")
        comp.material("sio2").propertyGroup("def").set("relpermittivity",["n_sio2(lam_um)^2"])
        background_tags=("sio2",)
    else:
        comp.material().create("air","Common"); comp.material("air").label("Air")
        comp.material("air").propertyGroup("def").set("relpermittivity",["1"])
        comp.material().create("sio2","Common"); comp.material("sio2").label("Fused silica BOX (Malitson)")
        comp.material("sio2").selection().named("geom1_box_dom"); comp.material("sio2").propertyGroup("def").set("relpermittivity",["n_sio2(lam_um)^2"])
        background_tags=("air","sio2")
    comp.material().create("coremat","Common"); comp.material("coremat").label(f"{cfg.core_material} {cfg.crystal_cut}-cut dispersive")
    comp.material("coremat").selection().named("geom1_coreu_dom")
    comp.material("coremat").propertyGroup("def").set("relpermittivity",dielectric_expressions(cfg.crystal_cut))
    for tag in (*background_tags, "coremat"):
        comp.material(tag).propertyGroup("def").set("relpermeability", ["1"])
        comp.material(tag).propertyGroup("def").set("electricconductivity", ["0[S/m]"])

    comp.physics("ewfd").feature("wee1").set("DisplacementFieldModel","RelativePermittivity")
    # Integral operators make polarization/PML classification global per eigenmode.
    comp.cpl().create("intcore","Integration"); comp.cpl("intcore").selection().named("geom1_coreu_dom")
    comp.cpl().create("intpml","Integration"); comp.cpl("intpml").selection().named("geom1_pmlu_dom")
    comp.cpl().create("intphys","Integration"); comp.cpl("intphys").selection().named("geom1_bg_dom")
    comp.cpl().create("intall","Integration")

    # Create the mode study before asking COMSOL to derive physics-controlled
    # mesh scales; the ewfd interface otherwise has no frequency context.
    j.study().create("std1"); j.study("std1").create("mode","ModeAnalysis"); mode=j.study("std1").feature("mode")
    mode.set("modeFreq","f0"); mode.set("neigsactive",True); mode.set("neigs",str(cfg.initial_number_of_modes)); mode.set("shiftactive",True); mode.set("shift","nshift")

    mesh=comp.mesh("mesh1")
    mesh.feature("size").set("custom",True); mesh.feature("size").set("hmax","hbackground"); mesh.feature("size").set("hmin","8[nm]")
    mesh.create("sizecore","Size"); mesh.feature("sizecore").selection().named("geom1_coreu_dom"); mesh.feature("sizecore").set("custom",True); mesh.feature("sizecore").set("hmax","hcore"); mesh.feature("sizecore").set("hmin","5[nm]")
    mesh.create("sizeside","Size"); mesh.feature("sizeside").selection().geom("geom1",1); mesh.feature("sizeside").selection().named("geom1_corebnd"); mesh.feature("sizeside").set("custom",True); mesh.feature("sizeside").set("hmax","hside")
    mesh.create("sizepml","Size"); mesh.feature("sizepml").selection().named("geom1_pmlu_dom"); mesh.feature("sizepml").set("custom",True); mesh.feature("sizepml").set("hmax","hpml")
    # The geometry ring is four non-overlapping mapped rectangles. COMSOL may
    # partition them at material interfaces, but the remaining physical region
    # is still independently triangularized.
    mesh.create("map1","Map"); mesh.feature("map1").selection().named("geom1_pmlu_dom")
    mesh.create("ftri1","FreeTri"); mesh.feature("ftri1").selection().remaining(); mesh.run()

    j.study("std1").createAutoSequences("all")
    model._lnlt_mesh_profile = mesh_profile
    model._lnlt_mode_count = cfg.initial_number_of_modes
    return model

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
                           "polarization_fraction":target_pol,"pml_fraction":float(np.real(pml[i])),"loss":float(np.real(loss[i]))})
    good=[x for x in candidates if x["polarization_fraction"]>cfg.te_threshold and x["pml_fraction"]<0.5 and 1 < x["neff"].real < 3]
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
    _set_mode_count(model,count); started=time.perf_counter(); model.solve(); solve_elapsed=time.perf_counter()-started
    result=extract_selected_mode(model,cfg,point.wavelength_nm,point.top_width_nm)
    result["solve_elapsed_s"]=solve_elapsed
    return result


def _solve_and_select(model: Any, cfg: Config, point: ScanPoint) -> tuple[dict[str,Any],int]:
    last_error: Exception | None = None
    attempts=[]
    for count in dict.fromkeys((cfg.initial_number_of_modes,cfg.fallback_number_of_modes)):
        try:
            result=_solve_once_and_select(model,cfg,point,count); attempts.append({"modes":count,"status":"success","solve_elapsed_s":result["solve_elapsed_s"]})
            result["attempts"]=attempts
            return result,count
        except RuntimeError as exc:
            attempts.append({"modes":count,"status":"failed","error":str(exc)})
            last_error=exc
            if count == cfg.fallback_number_of_modes:
                break
    raise RuntimeError(f"TE0 selection failed after {cfg.fallback_number_of_modes} candidates: {last_error}")


class PredictionUnavailable(RuntimeError):
    """No trustworthy same-geometry, same-branch anchor is available yet."""


def _primary_search_expression(cfg: Config) -> str:
    return ("ne_sell(lam_um)" if cfg.search_neff == "TE_CORE_INDEX" else
            "no_sell(lam_um)" if cfg.search_neff == "MAX_CORE_INDEX" else
            search_expression(cfg.crystal_cut, cfg.target_mode)
            if cfg.search_neff == "TARGET_CORE_INDEX" else str(cfg.search_neff))


def prediction_branches(cfg: Config, wavelength_nm: float) -> set[str]:
    # Do not hard-code the 900-nm split: SHG wavelength ranges are editable.
    fundamentals=set(cfg.fundamental_wavelengths_nm)
    if cfg.run_profile in {"mini","preview"}:
        fundamentals.add(1500.0)
    return {name for name,values in (("FW",fundamentals),("SH",{x/2 for x in fundamentals}))
            if any(math.isclose(wavelength_nm,x,rel_tol=0,abs_tol=1e-6) for x in values)}


def _remember_prediction(cache: dict[float,dict[str,Any]], wavelength_nm: float,
                         result: dict[str,Any]) -> None:
    # Called only after a point was successfully accepted and saved.
    cache[wavelength_nm]={"neff":float(np.real(result["neff"])),"field":np.asarray(result["Ey"]),
                          "y":np.asarray(result["y"]).reshape(-1),"z":np.asarray(result["z"]).reshape(-1)}


def load_prediction_cache(cfg: Config, geometry: tuple[float,float,float],
                          mesh_profile: str, suffix: str="") -> dict[float,dict[str,Any]]:
    """Lazy resume cache: reject other geometries/materials/claddings/meshes.

    Existing main-scan MAT files are accepted without demanding the new Config
    fingerprint, which also includes output paths and nonphysical switches.
    """
    cache={}
    for wl in cfg.wavelengths_nm():
        path=mat_path(cfg,*geometry,wl,suffix)
        if not point_complete(path):
            continue
        try:
            data=loadmat(path,squeeze_me=True)
            if str(data.get("target_mode","TE0"))!=cfg.target_mode:
                continue
            for key,expected in zip(("film_thickness","top_width","etch_depth"),geometry):
                if not math.isclose(float(data[key])*1e9,expected,rel_tol=0,abs_tol=1e-6):
                    raise ValueError("Different geometry")
            if (str(data["core_material"])!=cfg.core_material or str(data["top_cladding"])!=cfg.top_cladding
                    or not math.isclose(float(data["sidewall_angle"]),cfg.sidewall_angle_deg)
                    or not math.isclose(float(data["wavelength"])*1e9,wl,rel_tol=0,abs_tol=1e-6)):
                continue
            stored_mesh=json.loads(str(data["mesh_info"]))
            if any(stored_mesh.get(k)!=v for k,v in mesh_settings(cfg,mesh_profile).items()):
                continue
            target_polarization = (float(data["TE_pol"]) if cfg.target_mode == "TE0"
                                   else float(data.get("TM_pol", 1.0 - float(data["TE_pol"]))))
            if (target_polarization<=cfg.te_threshold or float(data["pml_energy_fraction"])>=0.5
                    or float(data["even_symmetry"])<=0.9 or not bool(data["centered_peak"])):
                continue
            neff=float(np.real(data["neff"]))
            if not 1 < neff < max(refractive_indices(cfg.core_material,wl)) or not np.isfinite(data["Ey"]).all():
                continue
            _remember_prediction(cache,wl,data)
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return cache


def predict_search_center(cfg: Config, wavelength_nm: float,
                          cache: dict[float,dict[str,Any]]) -> tuple[float,list[float],float]:
    branch=prediction_branches(cfg,wavelength_nm)
    xs=sorted(x for x,v in cache.items() if x!=wavelength_nm
              and branch.intersection(prediction_branches(cfg,x)) and np.isfinite(v["neff"]))
    if not xs:
        raise PredictionUnavailable(f"No validated same-branch anchor at lambda={wavelength_nm:g} nm")
    left=[x for x in xs if x<wavelength_nm]; right=[x for x in xs if x>wavelength_nm]
    anchors=[left[-1],right[0]] if left and right else (xs[:2] if not left else xs[-2:])
    predicted=float(cache[anchors[0]]["neff"])
    if len(anchors)==2:
        a,b=anchors
        predicted=float(cache[a]["neff"]+(cache[b]["neff"]-cache[a]["neff"])*(wavelength_nm-a)/(b-a))
    if not np.isfinite(predicted) or not 1 < predicted < max(refractive_indices(cfg.core_material,wavelength_nm)):
        raise PredictionUnavailable(f"Predicted neff={predicted:g} outside material bounds at {wavelength_nm:g} nm")
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


def _solve_with_prediction(model: Any, cfg: Config, point: ScanPoint,
                            cache: dict[float,dict[str,Any]], *, anchor_loader: Any=None,
                            prediction_only: bool=False, primary_error: str="") -> tuple[dict[str,Any],int]:
    """SHG safety wrapper. Keep _solve_and_select unchanged for TH callers.

    Original 20/40 search first; one predicted-center attempt with 40 modes
    only on failure. No relaxed TE/PML criteria, no unbounded retry loop.
    """
    logger=logging.getLogger("lnlt_comsol"); original=_primary_search_expression(cfg)
    model.parameter("nshift",original)
    try:
        if not prediction_only:
            try:
                result,count=_solve_and_select(model,cfg,point)
            except Exception as exc:
                # COMSOL/JPype solve errors need not inherit RuntimeError.
                # KeyboardInterrupt/SystemExit are deliberately not caught.
                if not cfg.predicted_search_fallback:
                    raise
                primary_error=f"{type(exc).__name__}: {exc}"
                logger.warning("PRIMARY FAILED %s; predicting search center: %s",point,primary_error)
            else:
                result.update(search_method="primary",search_shift_used=original,primary_search_error="")
                return result,count
        elif not cfg.predicted_search_fallback:
            raise RuntimeError("Predicted search is disabled")
        if anchor_loader is not None:
            # Only failed points incur resume-MAT loading; fresh successes win.
            restored=anchor_loader(); restored.update(cache); cache.update(restored)
        try:
            predicted,anchors,reference=predict_search_center(cfg,point.wavelength_nm,cache)
        except PredictionUnavailable as exc:
            raise PredictionUnavailable(f"{exc}; primary failure: {primary_error}") from exc
        logger.info("PREDICTED RETRY %s shift=%.10g anchors=%s reference=%g",point,predicted,anchors,reference)
        retry_cfg=replace(cfg,search_neff=predicted,initial_number_of_modes=cfg.fallback_number_of_modes)
        model.parameter("nshift",str(predicted))
        try:
            result,count=_solve_and_select(model,retry_cfg,point)
            overlap=prediction_field_overlap(cache[reference],result)
            if not np.isfinite(result["neff"]) or not np.isfinite(overlap) or overlap<cfg.predicted_field_overlap_min:
                raise RuntimeError(f"Predicted-search mode overlap={overlap:g}; required {cfg.predicted_field_overlap_min:g}")
        except Exception as exc:
            raise RuntimeError(f"Original search failed: {primary_error}; predicted search failed: {exc}") from exc
        result.update(search_method="predicted_fallback",search_shift_used=predicted,
                      prediction_anchors_nm=anchors,overlap_reference_wavelength_nm=reference,
                      prediction_field_overlap=overlap,primary_search_error=primary_error)
        return result,count
    finally:
        # Do not contaminate subsequent wavelengths or the saved MPH.
        model.parameter("nshift",original)


def _solve_scan_point(model: Any, cfg: Config, point: ScanPoint,
                      cache: dict[float,dict[str,Any]], *, force_robust: bool,
                      anchor_loader: Any=None, prediction_only: bool=False,
                      primary_error: str="") -> tuple[dict[str,Any],int]:
    """Fast adjacent tracking outside the unchanged robust/failure handler."""
    logger=logging.getLogger("lnlt_comsol"); original=_primary_search_expression(cfg)
    tracking_error=""
    if cfg.adjacent_mode_tracking and not force_robust and not prediction_only:
        if anchor_loader is not None:
            restored=anchor_loader(); restored.update(cache); cache.update(restored)
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
    result,count=_solve_with_prediction(model,cfg,point,cache,anchor_loader=anchor_loader,
                                        prediction_only=prediction_only,primary_error=primary_error)
    base_method=result.get("search_method","primary")
    result["tracking_method"]=("robust_reseed" if force_robust else
                                "robust_after_tracking_failure" if tracking_error else base_method)
    result["tracking_error"]=tracking_error
    return result,count


def robust_reseed_required(cfg: Config, wavelength_nm: float) -> bool:
    branches=prediction_branches(cfg,wavelength_nm)
    relevant=sorted(w for w in cfg.wavelengths_nm() if branches.intersection(prediction_branches(cfg,w)))
    position=next((i for i,w in enumerate(relevant) if math.isclose(w,wavelength_nm,abs_tol=1e-6)),0)
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
    no,ne=refractive_indices(cfg.core_material,point.wavelength_nm)
    expected={"upper_cladding":1.0 if cfg.top_cladding=="AIR" else silica_index(point.wavelength_nm)**2,
              "core":(ne if cfg.crystal_cut=="x" else no)**2,"box":silica_index(point.wavelength_nm)**2}
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
        model_path=cfg.run_dir/"model"/f"{cfg.core_material}_{cfg.crystal_cut}cut_rib_FEM.mph"
        for geometry in ((t,w,d) for t in cfg.thicknesses_nm() for w in cfg.widths_nm() for d in cfg.etches_nm()):
            t,w,d=geometry
            material_checked=False
            prediction_cache: dict[float,dict[str,Any]]={} if force else load_prediction_cache(cfg,(t,w,d),mesh_profile)
            logger.info("BUILD+MESH T=%g W=%g D=%g nm (%s)",t,w,d,mesh_profile)
            model.parameter("tfilm",f"{t}[nm]"); model.parameter("wtop",f"{w}[nm]"); model.parameter("etch",f"{d}[nm]")
            model.build(); model.mesh()
            # A failed early point without anchors gets ONE deferred attempt
            # after the other wavelengths of this geometry have been visited.
            queue=[(wl,False,"") for wl in cfg.wavelengths_nm()]
            for wl,prediction_only,primary_error in queue:
                point=ScanPoint(t,w,d,wl); key=(cfg.target_mode,t,w,d,wl); path=mat_path(cfg,t,w,d,wl)
                if point_complete(path) and not force:
                    logger.info("SKIP complete %s",path.name)
                    records[key]=records.get(key,{}) | {"fingerprint":cfg.fingerprint(),"thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
                                  "wavelength_nm":wl,"target_mode":cfg.target_mode,"status":"skipped","mat":str(path),"error":""}
                    _write_index(index_path,records); continue
                started=datetime.now().isoformat(); logger.info("SOLVE T=%g W=%g D=%g lambda=%g nm",t,w,d,wl)
                point_started=time.perf_counter()
                result={}
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
                    _remember_prediction(prediction_cache,wl,result)
                    status="success"; error=""
                    logger.info("OK mode=%s neff=%s pol=%.4f symmetry=%.4f PML=%.3g modes=%d path=%s solve=%.3fs",
                        cfg.target_mode,result["neff"],result["polarization_fraction"],result["even_symmetry"],result["pml_fraction"],
                        modes,result.get("tracking_method"),result.get("solve_elapsed_s",float("nan")))
                except Exception as exc:
                    error=f"{type(exc).__name__}: {exc}"
                    if isinstance(exc,PredictionUnavailable) and not prediction_only:
                        status="deferred"; queue.append((wl,True,error))
                        logger.warning("DEFER %s until same-branch anchors are available: %s",point,error)
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


def postprocess(cfg: Config) -> pd.DataFrame:
    rows=[]
    for t in cfg.thicknesses_nm():
        for w in cfg.widths_nm():
            for d in cfg.etches_nm():
                for fundamental in cfg.fundamental_wavelengths_nm:
                    p1,p2=mat_path(cfg,t,w,d,fundamental),mat_path(cfg,t,w,d,fundamental/2)
                    if point_complete(p1) and point_complete(p2):
                        n1=load_scalar(loadmat(p1),"neff_real"); n2=load_scalar(loadmat(p2),"neff_real")
                        period_um=(fundamental/1000)/(2*abs(n2-n1))
                        rows.append({"core_material":cfg.core_material,"top_cladding":cfg.top_cladding,
                                     "target_mode":cfg.target_mode,
                                     "film_thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
                                     "fundamental_nm":fundamental,"neff_fundamental":n1,"neff_sh":n2,"qpm_period_um":period_um})
    out=pd.DataFrame(rows); out.to_csv(cfg.run_dir/"data"/"qpm_results.csv",index=False)
    figdir=cfg.run_dir/"data"/"figures"; figdir.mkdir(parents=True,exist_ok=True)
    # The formal result contains no neff curve and no separate tolerance curve.
    for legacy in ("neff_vs_wavelength.png","thickness_tolerance.png","top_width_tolerance.png"):
        (figdir/legacy).unlink(missing_ok=True)
    if not out.empty:
        # Only the nominal/central geometry is a curve. All other scanned
        # thicknesses (or widths) form asymmetric error bars about it.
        center_t=sorted(out.film_thickness_nm.unique())[out.film_thickness_nm.nunique()//2]
        center_w=sorted(out.top_width_nm.unique())[out.top_width_nm.nunique()//2]
        center_d=sorted(out.etch_depth_nm.unique())[out.etch_depth_nm.nunique()//2]
        central=out[(out.film_thickness_nm==center_t)&(out.top_width_nm==center_w)&(out.etch_depth_nm==center_d)].sort_values("fundamental_nm")
        tolerance=out[out.etch_depth_nm==center_d]
        if out.film_thickness_nm.nunique()>1:
            tolerance=tolerance[tolerance.top_width_nm==center_w]
            center_label=f"T = {center_t:g} nm"
        elif out.top_width_nm.nunique()>1:
            tolerance=tolerance[tolerance.film_thickness_nm==center_t]
            center_label=f"W = {center_w:g} nm"
        else:
            center_label=f"T = {center_t:g} nm"
        envelope=tolerance.groupby("fundamental_nm").qpm_period_um.agg(["min","max"]).reindex(central.fundamental_nm).reset_index()
        y=central.qpm_period_um.to_numpy()
        yerr=np.maximum(0,np.vstack((y-envelope["min"].to_numpy(),envelope["max"].to_numpy()-y)))
        curve=plt.errorbar(central.fundamental_nm,y,yerr=yerr,fmt="o-",color="blue",ecolor="blue",lw=2,ms=6,capsize=5,label=center_label,zorder=3)
        exp_points=normalize_experimental_qpm_data(cfg.experimental_qpm_data)
        pure_figure=figdir/"qpm_vs_wavelength.png"; experiment_figure=figdir/"qpm_vs_wavelength_experiment.png"
        comparison_path=cfg.run_dir/"data"/"experimental_comparison.csv"
        metrics_path=cfg.run_dir/"data"/"experimental_error_metrics.csv"
        residual_figure=figdir/"experimental_residuals.png"
        if exp_points:
            exp=pd.DataFrame(exp_points,columns=["experimental_wavelength_nm","experimental_period_um"])
            points=plt.scatter(exp.experimental_wavelength_nm,exp.experimental_period_um,marker="o",s=85,
                facecolor="orange",edgecolor="black",linewidth=.9,label="Experimental Data",zorder=5)
            plt.legend([curve,points],[center_label,"Experimental Data"]); figure_path=experiment_figure
            pure_figure.unlink(missing_ok=True)
        else:
            plt.legend([curve],[center_label]); figure_path=pure_figure
            for stale in (experiment_figure,comparison_path,metrics_path,residual_figure): stale.unlink(missing_ok=True)
        plt.xlabel("Wavelength λ (nm)"); plt.ylabel("QPM Period Λ (μm)"); plt.grid(True,alpha=.55)
        plt.tight_layout(); plt.savefig(figure_path,dpi=220); plt.close()
        if exp_points:
            comparison=[]; metrics=[]
            for (t,w,d),g in out.groupby(["film_thickness_nm","top_width_nm","etch_depth_nm"]):
                g=g.sort_values("fundamental_nm"); interp=np.interp(exp.experimental_wavelength_nm,g.fundamental_nm,g.qpm_period_um)
                residual=interp-exp.experimental_period_um.to_numpy()
                for (_,e),simulated,residue in zip(exp.iterrows(),interp,residual):
                    comparison.append({"film_thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
                        **e.to_dict(),"simulated_period_um":simulated,"residual_um":residue})
                metrics.append({"film_thickness_nm":t,"top_width_nm":w,"etch_depth_nm":d,
                    "mae_um":float(np.mean(abs(residual))),"rmse_um":float(np.sqrt(np.mean(residual**2))),
                    "mean_bias_um":float(np.mean(residual))})
            comparison_df=pd.DataFrame(comparison); comparison_df.to_csv(comparison_path,index=False)
            metrics_df=pd.DataFrame(metrics).sort_values("rmse_um"); metrics_df["best_match"]=False
            if len(metrics_df): metrics_df.loc[metrics_df.index[0],"best_match"]=True
            metrics_df.to_csv(metrics_path,index=False)
            central_residual=comparison_df[(comparison_df.film_thickness_nm==center_t)&
                (comparison_df.top_width_nm==center_w)&(comparison_df.etch_depth_nm==center_d)].sort_values("experimental_wavelength_nm")
            plt.scatter(central_residual.experimental_wavelength_nm,central_residual.residual_um,s=65,color="blue")
            if len(central_residual)>1:
                plt.plot(central_residual.experimental_wavelength_nm,central_residual.residual_um,color="blue")
            plt.axhline(0,color="k",lw=1); plt.xlabel("Experimental wavelength (nm)"); plt.ylabel("Simulation - experiment (μm)")
            plt.grid(True,alpha=.3); plt.tight_layout(); plt.savefig(residual_figure,dpi=180); plt.close()
    mats=sorted((cfg.run_dir/"data"/"mat").glob("*.mat"))
    if mats:
        preferred=mat_path(cfg,800,3000,550,1500)
        d=loadmat(preferred if preferred.exists() else mats[0]); field=np.abs(d["Ey"]); y=d["y"].squeeze()*1e6; z=d["z"].squeeze()*1e6
        plt.imshow(field,extent=[y.min(),y.max(),z.min(),z.max()],origin="lower",aspect="auto",cmap="magma"); plt.colorbar(label="|Ex| (a.u.)"); plt.xlabel("COMSOL x / old MODE y (um)"); plt.ylabel("COMSOL y / old MODE z (um)"); plt.tight_layout(); plt.savefig(figdir/"mode_field_preview.png",dpi=180); plt.close()
    return out

def validate_outputs(cfg: Config, mph_reopen_ok: bool | None = None) -> dict[str,Any]:
    """Re-read all expected MAT files and issue the final validation report."""
    required=("film_thickness","top_width","bottom_width","etch_depth","sidewall_angle","wavelength","mode_index","TE_pol","neff","neff_real","loss","y","z","Ey")
    rows=[]; missing=[]
    for point in scan_points(cfg):
        p=mat_path(cfg,*point.geometry,point.wavelength_nm)
        if not p.exists(): missing.append(str(p)); continue
        data=loadmat(p); absent=[k for k in required if k not in data]
        if absent or data["Ey"].size<100: missing.append(f"{p}: {absent or 'empty field'}"); continue
        rows.append({"thickness_nm":point.thickness_nm,"top_width_nm":point.top_width_nm,"etch_depth_nm":point.etch_depth_nm,
                     "wavelength_nm":point.wavelength_nm,"neff":load_scalar(data,"neff_real"),"TE_pol":load_scalar(data,"TE_pol"),
                     "pml_fraction":load_scalar(data,"pml_energy_fraction")})
    points=pd.DataFrame(rows); points.to_csv(cfg.run_dir/"data"/"validated_mat_points.csv",index=False)
    conv_path=cfg.run_dir/"data"/"mesh_convergence.json"
    conv=json.loads(conv_path.read_text(encoding="utf-8")) if conv_path.exists() else {"passed":False,"delta_neff_max":float("nan"),"qpm_delta_um":float("nan")}
    qpm_path=cfg.run_dir/"data"/"qpm_results.csv"; qpm=pd.read_csv(qpm_path) if qpm_path.exists() else pd.DataFrame()
    metrics_path=cfg.run_dir/"data"/"experimental_error_metrics.csv"; metrics=pd.read_csv(metrics_path) if metrics_path.exists() else pd.DataFrame()
    material_checks=[]
    for path in (cfg.run_dir/"data").glob("material_domain_check*.json"):
        material_checks.append(json.loads(path.read_text(encoding="utf-8")))
    material_ok=bool(material_checks) and all(item.get("passed") for item in material_checks)
    expected=len(scan_points(cfg)); expected_pairs=len(cfg.thicknesses_nm())*len(cfg.widths_nm())*len(cfg.etches_nm())*len(cfg.fundamental_wavelengths_nm)
    ok=(not missing and len(rows)==expected and len(qpm)==expected_pairs and bool(conv.get("passed"))
        and material_ok and mph_reopen_ok is not False)
    best=metrics.iloc[0].to_dict() if len(metrics) else {}
    report=["# COMSOL 6.4 FEM validation report","",f"- Result: **{'PASS' if ok else 'FAIL'}**",f"- Material / upper cladding: {cfg.core_material} / {cfg.top_cladding}",
            f"- MAT integrity: {len(rows)}/{expected}",f"- Fundamental/SH pairs: {len(qpm)}/{expected_pairs}",
            f"- Saved MPH reopened with problems=[]: {mph_reopen_ok}",f"- Mesh max |Delta neff|: {conv.get('delta_neff_max')} (limit 5e-4)",
            f"- Mesh QPM period difference: {conv.get('qpm_delta_um')} um (limit 0.02 um)",f"- Best experimental match: {best}","",f"Missing/invalid: {missing or 'none'}"]
    report.insert(7,f"- Air/LT/SiO2 actual epsilon domain check: {material_ok}")
    (cfg.run_dir/"data"/"validation_report.md").write_text("\n".join(report),encoding="utf-8")
    return {"passed":ok,"rows":len(rows),"expected":expected,"missing":missing,"mesh":conv,"material_domains":material_ok,
            "best_match":best,"mph_reopen_ok":mph_reopen_ok}


def convergence_run(cfg: Config) -> dict[str,Any]:
    import mph
    client=mph.Client(cores=cfg.threads,version="6.4")
    values: dict[str,Any]={}
    try:
        for label in ("default","refined"):
            m=build_comsol_model(client,cfg,label); m.parameter("tfilm","800[nm]"); m.parameter("wtop","3000[nm]"); m.parameter("etch","550[nm]")
            m.build(); m.mesh(); neffs={}; mode_indices={}; diagnostics={}; prediction_cache={}
            for wl in (750.0,1500.0):
                point=ScanPoint(800,3000,550,wl); m.parameter("lda0",f"{wl}[nm]")
                # Keep convergence profiles isolated: never use a coarse-grid
                # anchor to rescue a refined-grid solve.
                loader=lambda: load_prediction_cache(cfg,(800,3000,550),label,f"_{label}")
                r,modes=_solve_with_prediction(m,cfg,point,prediction_cache,anchor_loader=loader)
                path=mat_path(cfg,800,3000,550,wl,f"_{label}")
                save_point(cfg,r,point,label,modes,path); neffs[str(int(wl))]=r["neff"].real; mode_indices[str(int(wl))]=r["mode_index"]
                _remember_prediction(prediction_cache,wl,r)
                diagnostics[str(int(wl))]={"TE_pol":r["TE_pol"],"even_symmetry":r["even_symmetry"],
                                           "centered_peak":bool(r["centered_peak"]),"pml_fraction":r["pml_fraction"]}
            period=1.5/(2*abs(neffs["750"]-neffs["1500"]))
            values[label]={"neff":neffs,"mode_index":mode_indices,"diagnostics":diagnostics,"qpm_period_um":period}
            m.save(cfg.run_dir/"model"/f"{cfg.core_material}_xcut_rib_{label}.mph"); client.remove(m)
    finally:
        try: client.clear()
        except Exception: pass
    deltas={wl:abs(values["refined"]["neff"][wl]-values["default"]["neff"][wl]) for wl in ("750","1500")}
    values["delta_neff"]=deltas; values["delta_neff_max"]=max(deltas.values())
    values["qpm_delta_um"]=abs(values["refined"]["qpm_period_um"]-values["default"]["qpm_period_um"])
    # Eigenpair list indices are not physical mode labels and may reorder when
    # the mesh changes. Confirm the same TE0 by field diagnostics and neff.
    values["same_te0"]=all(
        values[p]["diagnostics"][wl]["TE_pol"]>cfg.te_threshold
        and values[p]["diagnostics"][wl]["even_symmetry"]>0.9
        and values[p]["diagnostics"][wl]["centered_peak"]
        and values[p]["diagnostics"][wl]["pml_fraction"]<0.5
        for p in ("default","refined") for wl in ("750","1500")
    )
    values["passed"]=values["same_te0"] and values["delta_neff_max"]<=5e-4 and values["qpm_delta_um"]<=0.02
    (cfg.run_dir/"data"/"mesh_convergence.json").write_text(json.dumps(values,indent=2),encoding="utf-8")
    return values

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
