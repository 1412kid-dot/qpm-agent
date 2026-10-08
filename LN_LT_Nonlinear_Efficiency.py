"""COMSOL 6.4 automation for LN/LT SHG, SFG and DFG efficiencies.

The three processes deliberately use independent formula branches.  In
particular, an SFG configuration is never dispatched to the SHG formula when
its two long wavelengths happen to be equal.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import importlib.util
import json
import logging
import math
import os
import sys
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.constants import c, epsilon_0
from scipy.io import loadmat, savemat

# ============================ USER CONFIGURATION ============================
# Select exactly one nonlinear process.  SFG never degenerates into SHG.
PROCESS_TYPE = "SHG"                 # "SHG", "SFG", or "DFG"
TARGET_MODE = "TE0"                  # "TE0" -> ewfd.Ex; "TM0" -> ewfd.Ey

CORE_MATERIAL = "LT"                 # "LN" or "LT"
TOP_CLADDING = "AIR"                 # "AIR" or "SIO2"
RUN_NAME = "LT_SHG_657"
OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs" / "legacy"
COMSOL_BIN = Path(r"F:\Comsol\COMSOL64\Multiphysics\bin\win64")
COMSOL_VERSION = "6.4"
COMSOL_THREADS = 4
MPH_RUNTIME_PYTHON = Path(os.environ.get("QPM_PYTHON", sys.executable))

# Wave 1 is always the highest-frequency wave.  All three values are entered
# explicitly and are validated; the program never silently changes them.
LAMBDA_1_NM = 657
LAMBDA_2_NM = 1314
LAMBDA_3_NM = 1314

FILM_THICKNESS_NM = 800.0
ETCH_DEPTH_NM = 550.0
TOP_WIDTH_NM = 3000.0
SIDEWALL_ANGLE_DEG = 67.0            # bottom interior angle

# Both material values are intentionally editable here.  The present LT run
# uses the value selected from the user-provided LT review.
D33_LN_PM_PER_V = 27.0
D33_LT_PM_PER_V = 16.0
QPM_ORDER = 1
QPM_DUTY_CYCLE = 0.50

DEVICE_LENGTH_MM = 20.0
ALPHA_1_DB_PER_CM = 0.0
ALPHA_2_DB_PER_CM = 0.0
ALPHA_3_DB_PER_CM = 0.0

# FEM and post-processing controls.
INITIAL_NUMBER_OF_MODES = 20
FALLBACK_NUMBER_OF_MODES = 40
ENABLE_ADJACENT_MODE_TRACKING = True
TRACKING_NUMBER_OF_MODES = 6
TRACKING_FIELD_OVERLAP_MIN = 0.95
ROBUST_RESEED_INTERVAL = 4
ENABLE_PREDICTED_SEARCH_FALLBACK = True
PREDICTED_FIELD_OVERLAP_MIN = 0.90
TARGET_POLARIZATION_THRESHOLD = 0.50
SEARCH_NEFF = "TARGET_CORE_INDEX"    # TE0 -> ne(lambda); TM0 -> no(lambda)
FIELD_SAMPLE_NM = 10.0               # exported master grid
INTEGRATION_GRID_NM = 20.0
INTEGRATION_CHECK_GRID_NM = 10.0

CORE_MAX_ELEMENT_NM = 30.0
SIDEWALL_MAX_ELEMENT_NM = 20.0
BACKGROUND_MAX_ELEMENT_NM = 70.0
PML_LAYERS = 8
REFINED_CORE_MAX_ELEMENT_NM = 20.0
REFINED_SIDEWALL_MAX_ELEMENT_NM = 15.0
REFINED_BACKGROUND_MAX_ELEMENT_NM = 50.0
REFINED_PML_LAYERS = 12

NEFF_ABS_TOL = 1e-4
MODE_OVERLAP_MIN = 0.99
EFFICIENCY_REL_TOL = 0.02
INTEGRATION_REL_TOL = 0.005
# ===========================================================================

FORMULA_VERSION = "user-corrected-independent-process-formulas-2026-09-01-v2"
FORMULA_EXPRESSIONS = {
    "SHG": "d_eff^2*omega_1^2/(2*epsilon_0*c^3*n_1*n_2^2*S_eff)",
    "SFG": "2*d_eff^2*omega_1^2/(epsilon_0*c^3*n_1*n_2*n_3*A_eff)",
    "DFG": "2*d_eff^2*omega_2*omega_3/(epsilon_0*c^3*n_1*n_2*n_3*S_eff)",
}


def configure_text_output() -> None:
    """Use UTF-8 for Chinese CLI result text on Windows when supported."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
SOURCE_DIR = (Path(__file__).resolve().parent if "__file__" in globals()
              else Path(__file__).resolve().parent)
BASE_BACKEND = SOURCE_DIR / "LN_LT_COMSOL_SHG_QPM_Automation.py"


@dataclass(frozen=True)
class Config:
    process_type: str = PROCESS_TYPE
    target_mode: str = TARGET_MODE
    core_material: str = CORE_MATERIAL
    top_cladding: str = TOP_CLADDING
    run_name: str = RUN_NAME
    output_root: Path = OUTPUT_ROOT
    comsol_bin: Path = COMSOL_BIN
    comsol_version: str = COMSOL_VERSION
    threads: int = COMSOL_THREADS
    lambda_1_nm: float = LAMBDA_1_NM
    lambda_2_nm: float = LAMBDA_2_NM
    lambda_3_nm: float = LAMBDA_3_NM
    film_thickness_nm: float = FILM_THICKNESS_NM
    etch_depth_nm: float = ETCH_DEPTH_NM
    top_width_nm: float = TOP_WIDTH_NM
    sidewall_angle_deg: float = SIDEWALL_ANGLE_DEG
    d33_ln_pm_per_v: float = D33_LN_PM_PER_V
    d33_lt_pm_per_v: float = D33_LT_PM_PER_V
    qpm_order: int = QPM_ORDER
    qpm_duty_cycle: float = QPM_DUTY_CYCLE
    device_length_mm: float = DEVICE_LENGTH_MM
    alpha_1_db_per_cm: float = ALPHA_1_DB_PER_CM
    alpha_2_db_per_cm: float = ALPHA_2_DB_PER_CM
    alpha_3_db_per_cm: float = ALPHA_3_DB_PER_CM
    field_sample_nm: float = FIELD_SAMPLE_NM
    integration_grid_nm: float = INTEGRATION_GRID_NM
    integration_check_grid_nm: float = INTEGRATION_CHECK_GRID_NM
    initial_number_of_modes: int = INITIAL_NUMBER_OF_MODES
    fallback_number_of_modes: int = FALLBACK_NUMBER_OF_MODES
    adjacent_mode_tracking: bool = ENABLE_ADJACENT_MODE_TRACKING
    tracking_number_of_modes: int = TRACKING_NUMBER_OF_MODES
    tracking_field_overlap_min: float = TRACKING_FIELD_OVERLAP_MIN
    robust_reseed_interval: int = ROBUST_RESEED_INTERVAL
    predicted_search_fallback: bool = ENABLE_PREDICTED_SEARCH_FALLBACK
    predicted_field_overlap_min: float = PREDICTED_FIELD_OVERLAP_MIN
    target_polarization_threshold: float = TARGET_POLARIZATION_THRESHOLD
    search_neff: str | float = SEARCH_NEFF
    core_max_element_nm: float = CORE_MAX_ELEMENT_NM
    sidewall_max_element_nm: float = SIDEWALL_MAX_ELEMENT_NM
    background_max_element_nm: float = BACKGROUND_MAX_ELEMENT_NM
    pml_layers: int = PML_LAYERS
    refined_core_max_element_nm: float = REFINED_CORE_MAX_ELEMENT_NM
    refined_sidewall_max_element_nm: float = REFINED_SIDEWALL_MAX_ELEMENT_NM
    refined_background_max_element_nm: float = REFINED_BACKGROUND_MAX_ELEMENT_NM
    refined_pml_layers: int = REFINED_PML_LAYERS

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_name

    @property
    def wavelengths_nm(self) -> tuple[float, ...]:
        return tuple(sorted(set(self.wave_wavelengths_nm)))

    @property
    def wave_wavelengths_nm(self) -> tuple[float, float, float]:
        return self.lambda_1_nm, self.lambda_2_nm, self.lambda_3_nm

    @property
    def d33_pm_per_v(self) -> float:
        return self.d33_ln_pm_per_v if self.core_material == "LN" else self.d33_lt_pm_per_v

    @property
    def deff_m_per_v(self) -> float:
        # Fourier coefficient of a binary QPM grating with arbitrary duty.
        return (2.0 / (math.pi * self.qpm_order)
                * math.sin(math.pi * self.qpm_order * self.qpm_duty_cycle)
                * abs(self.d33_pm_per_v) * 1e-12)

    @property
    def alphas_db_per_cm(self) -> tuple[float, float, float]:
        return self.alpha_1_db_per_cm, self.alpha_2_db_per_cm, self.alpha_3_db_per_cm

    def validate(self) -> None:
        if self.process_type not in {"SHG", "SFG", "DFG"}:
            raise ValueError("PROCESS_TYPE must be SHG, SFG, or DFG.")
        if self.target_mode not in {"TE0", "TM0"}:
            raise ValueError("TARGET_MODE must be TE0 or TM0.")
        if self.core_material not in {"LN", "LT"}:
            raise ValueError("CORE_MATERIAL must be LN or LT.")
        if self.top_cladding not in {"AIR", "SIO2"}:
            raise ValueError("TOP_CLADDING must be AIR or SIO2.")
        if not (0 < self.etch_depth_nm <= self.film_thickness_nm):
            raise ValueError("Etch depth must lie in (0, film thickness].")
        if min(self.wave_wavelengths_nm) <= 0 or self.device_length_mm <= 0:
            raise ValueError("Wavelengths and device length must be positive.")
        if min(self.d33_ln_pm_per_v, self.d33_lt_pm_per_v) <= 0:
            raise ValueError("Both editable d33 values must be positive magnitudes.")
        if self.qpm_order < 1 or not 0 < self.qpm_duty_cycle < 1:
            raise ValueError("QPM order and duty cycle are invalid.")
        if min(self.initial_number_of_modes, self.fallback_number_of_modes,
               self.tracking_number_of_modes, self.robust_reseed_interval) < 1:
            raise ValueError("Mode counts and robust reseed interval must be positive.")
        if not 0 < self.tracking_field_overlap_min <= 1:
            raise ValueError("Tracking field-overlap threshold must be in (0, 1].")
        if not 0 < self.predicted_field_overlap_min <= 1:
            raise ValueError("Prediction field-overlap threshold must be in (0, 1].")
        if not 0 < self.target_polarization_threshold <= 1:
            raise ValueError("Target-polarization threshold must be in (0, 1].")
        if min(self.alphas_db_per_cm) < 0:
            raise ValueError("Propagation losses cannot be negative.")
        inv = np.reciprocal(np.asarray(self.wave_wavelengths_nm, dtype=float))
        energy_error = abs(inv[0] - inv[1] - inv[2])
        energy_scale = max(abs(inv[0]), 1e-300)
        if energy_error / energy_scale > 1e-10:
            raise ValueError("Wavelengths violate omega_1 = omega_2 + omega_3.")
        if self.process_type == "SHG":
            if not (math.isclose(self.lambda_2_nm, self.lambda_3_nm, abs_tol=1e-9)
                    and math.isclose(self.lambda_2_nm, 2*self.lambda_1_nm, abs_tol=1e-9)):
                raise ValueError("SHG requires lambda_2=lambda_3=2*lambda_1.")
        ratio = self.integration_grid_nm / self.field_sample_nm
        check_ratio = self.integration_check_grid_nm / self.field_sample_nm
        if not (math.isclose(ratio, round(ratio)) and math.isclose(check_ratio, round(check_ratio))):
            raise ValueError("Integration grids must be integer multiples of FIELD_SAMPLE_NM.")


def prepare_directories(cfg: Config) -> logging.Logger:
    for sub in ("model", "data", "data/mat/default", "data/mat/refined", "data/figures", "logs"):
        (cfg.run_dir / sub).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("nonlinear_efficiency")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(cfg.run_dir / "logs" / "run.log", encoding="utf-8")
    stream_handler = logging.StreamHandler(sys.stdout)
    file_handler.setFormatter(formatter); stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler); logger.addHandler(stream_handler)
    payload = serializable_config(cfg)
    payload.update(formula_version=FORMULA_VERSION, created=datetime.now().isoformat())
    (cfg.run_dir / "data" / "run_config.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return logger


def serializable_config(cfg: Config) -> dict[str, Any]:
    result = asdict(cfg)
    for key, value in list(result.items()):
        if isinstance(value, Path):
            result[key] = str(value)
    result["d33_selected_pm_per_v"] = cfg.d33_pm_per_v
    result["deff_m_per_v"] = cfg.deff_m_per_v
    result["python_executable"] = sys.executable
    result["mph_runtime_python"] = str(MPH_RUNTIME_PYTHON)
    result["mph_importable"] = importlib.util.find_spec("mph") is not None
    return result


def load_backend() -> Any:
    embedded = globals().get("_NOTEBOOK_BACKEND")
    if embedded is not None:
        return embedded
    if not BASE_BACKEND.exists():
        raise FileNotFoundError(f"COMSOL backend not found: {BASE_BACKEND}")
    spec = importlib.util.spec_from_file_location("lnlt_efficiency_backend", BASE_BACKEND)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load backend: {BASE_BACKEND}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def backend_config(cfg: Config, backend: Any) -> Any:
    # The proven FEM backend is wavelength-generic.  Configure it for one
    # fixed geometry and the required unique wavelengths.
    return backend.Config(
        core_material=cfg.core_material,
        target_mode=cfg.target_mode,
        run_profile="full",
        run_name=cfg.run_name,
        output_root=cfg.output_root,
        film_thicknesses_nm=(cfg.film_thickness_nm,),
        top_widths_nm=(cfg.top_width_nm,),
        etch_depths_nm=(cfg.etch_depth_nm,),
        sidewall_angle_deg=cfg.sidewall_angle_deg,
        # Preserve separate FW/SH prediction branches for SHG.  For SFG/DFG,
        # all requested wavelengths belong to one continuous selected-mode
        # family and may seed adjacent tracking after overlap validation.
        fundamental_wavelengths_nm=((cfg.lambda_2_nm,) if cfg.process_type == "SHG"
                                    else cfg.wavelengths_nm),
        initial_number_of_modes=cfg.initial_number_of_modes,
        fallback_number_of_modes=cfg.fallback_number_of_modes,
        adjacent_mode_tracking=cfg.adjacent_mode_tracking,
        tracking_number_of_modes=cfg.tracking_number_of_modes,
        tracking_field_overlap_min=cfg.tracking_field_overlap_min,
        robust_reseed_interval=cfg.robust_reseed_interval,
        predicted_search_fallback=cfg.predicted_search_fallback,
        predicted_field_overlap_min=cfg.predicted_field_overlap_min,
        te_threshold=cfg.target_polarization_threshold,
        search_neff=cfg.search_neff,
        threads=cfg.threads,
        core_max_element_nm=cfg.core_max_element_nm,
        sidewall_max_element_nm=cfg.sidewall_max_element_nm,
        background_max_element_nm=cfg.background_max_element_nm,
        pml_layers=cfg.pml_layers,
        refined_core_max_element_nm=cfg.refined_core_max_element_nm,
        refined_sidewall_max_element_nm=cfg.refined_sidewall_max_element_nm,
        refined_background_max_element_nm=cfg.refined_background_max_element_nm,
        refined_pml_layers=cfg.refined_pml_layers,
        top_cladding=cfg.top_cladding,
        field_dx_nm=cfg.field_sample_nm,
        experimental_qpm_data=(),
    )


def add_comsol_to_process_path(cfg: Config) -> None:
    executable = cfg.comsol_bin / "comsolmphserver.exe"
    if not executable.exists():
        raise FileNotFoundError(f"COMSOL server not found: {executable}")
    current = os.environ.get("PATH", "")
    entries = current.split(os.pathsep)
    if str(cfg.comsol_bin) not in entries:
        os.environ["PATH"] = str(cfg.comsol_bin) + os.pathsep + current


def import_mph_runtime() -> Any:
    """Import MPh, with a safe fallback for compatible notebook kernels."""
    try:
        return importlib.import_module("mph")
    except ModuleNotFoundError as exc:
        if exc.name != "mph":
            raise
        site_packages = MPH_RUNTIME_PYTHON.parent.parent / "Lib" / "site-packages"
        # The verified MPh environment uses CPython 3.12 and a CPython-specific
        # JPype extension; only inject it into an ABI-compatible kernel.
        if sys.version_info[:2] == (3, 12) and site_packages.exists():
            sys.path.insert(0, str(site_packages))
            return importlib.import_module("mph")
        raise ModuleNotFoundError(
            "MPh is unavailable in this Python kernel. Run this file with "
            f"'{MPH_RUNTIME_PYTHON}', or select that interpreter as the Jupyter kernel. "
            f"Current interpreter: '{sys.executable}'.") from exc


def mat_path(cfg: Config, profile: str, wavelength_nm: float) -> Path:
    suffix = "" if cfg.target_mode == "TE0" else "_TM0"
    return cfg.run_dir / "data" / "mat" / profile / f"wave_{wavelength_nm:g}nm{suffix}.mat"


def model_path(cfg: Config, profile: str) -> Path:
    suffix = "" if cfg.target_mode == "TE0" else "_TM0"
    return (cfg.run_dir / "model" /
            f"{cfg.core_material}_{cfg.process_type}_{cfg.lambda_1_nm:g}_{profile}{suffix}.mph")


def save_mode_result(cfg: Config, profile: str, wavelength_nm: float,
                     result: dict[str, Any], modes_requested: int) -> Path:
    path = mat_path(cfg, profile, wavelength_nm)
    field = np.asarray(result["Ey"], dtype=np.complex128)
    component = result.get(
        "dominant_comsol_component", "ewfd.Ex" if cfg.target_mode == "TE0" else "ewfd.Ey")
    payload = {
        "wavelength_nm": wavelength_nm,
        "x_m": np.asarray(result["y"], dtype=float),
        "y_m": np.asarray(result["z"], dtype=float),
        "E_target": field,
        # Compatibility aliases are explicit and documented.
        "legacy_y_m": np.asarray(result["y"], dtype=float),
        "legacy_z_m": np.asarray(result["z"], dtype=float),
        "neff": result["neff"],
        "neff_real": float(np.real(result["neff"])),
        "loss_comsol_db_per_m": result["loss"],
        "TE_pol": result["TE_pol"],
        "TM_pol": result.get("TM_pol", 1-result["TE_pol"]),
        "polarization_fraction": result.get(
            "polarization_fraction",
            result["TE_pol"] if cfg.target_mode == "TE0" else 1-result["TE_pol"]),
        "target_mode": cfg.target_mode,
        "mode_index": result["mode_index"],
        "pml_energy_fraction": result["pml_fraction"],
        "even_symmetry": result["even_symmetry"],
        "peak_x_um": result["peak_x_um"],
        "centered_peak": result["centered_peak"],
        "is_TE0": result["is_TE0"],
        "is_TM0": result.get("is_TM0", cfg.target_mode == "TM0"),
        "mesh_profile": profile,
        "modes_requested": modes_requested,
        "field_component": component,
        "core_material": cfg.core_material,
        "top_cladding": cfg.top_cladding,
        "film_thickness_nm": cfg.film_thickness_nm,
        "etch_depth_nm": cfg.etch_depth_nm,
        "top_width_nm": cfg.top_width_nm,
        "sidewall_angle_deg": cfg.sidewall_angle_deg,
        "coordinate_definition": "x_m=COMSOL x; y_m=COMSOL y; propagation=COMSOL z",
        "search_method": result.get("search_method", "primary"),
        "tracking_method": result.get("tracking_method", result.get("search_method", "primary")),
        "tracking_error": result.get("tracking_error", ""),
        "search_shift_used": str(result.get("search_shift_used", cfg.search_neff)),
        "prediction_anchors_nm": np.asarray(result.get("prediction_anchors_nm", []), dtype=float),
        "overlap_reference_wavelength_nm": result.get(
            "overlap_reference_wavelength_nm", float("nan")),
        "prediction_field_overlap": result.get("prediction_field_overlap", float("nan")),
        "primary_search_error": result.get("primary_search_error", ""),
        "solve_elapsed_s": result.get("solve_elapsed_s", float("nan")),
        "extraction_elapsed_s": result.get("extraction_elapsed_s", float("nan")),
        "attempts_json": json.dumps(result.get("attempts", [])),
    }
    if cfg.target_mode == "TE0":
        payload["Ex"] = field
        payload["legacy_Ey_is_COMSOL_Ex"] = field
    else:
        payload["Ey"] = field
    temp = path.with_suffix(".tmp.mat")
    savemat(temp, payload, do_compression=True)
    os.replace(temp, path)
    return path


def mode_file_complete(path: Path, target_mode: str = "TE0") -> bool:
    if not path.exists():
        return False
    try:
        data = loadmat(path, squeeze_me=True)
        stored_mode = str(data.get("target_mode", "TE0"))
        field_key = "E_target" if "E_target" in data else ("Ex" if target_mode == "TE0" else "Ey")
        return (stored_mode == target_mode
                and all(key in data for key in ("x_m", "y_m", field_key, "neff_real", "TE_pol"))
                and np.asarray(data[field_key]).size > 100)
    except (OSError, ValueError, KeyError):
        return False


def load_local_prediction_cache(cfg: Config, profile: str) -> dict[float, dict[str, Any]]:
    """Load only validated same-run/same-profile anchors for safe resume tracking."""
    cache: dict[float, dict[str, Any]] = {}
    for wavelength_nm in cfg.wavelengths_nm:
        path = mat_path(cfg, profile, wavelength_nm)
        if not mode_file_complete(path, cfg.target_mode):
            continue
        try:
            data = loadmat(path, squeeze_me=True)
            checks = (
                str(data.get("core_material", "")) == cfg.core_material,
                str(data.get("top_cladding", "")) == cfg.top_cladding,
                str(data.get("mesh_profile", "")) == profile,
                math.isclose(float(data.get("film_thickness_nm", float("nan"))), cfg.film_thickness_nm),
                math.isclose(float(data.get("etch_depth_nm", float("nan"))), cfg.etch_depth_nm),
                math.isclose(float(data.get("top_width_nm", float("nan"))), cfg.top_width_nm),
                float(data.get("polarization_fraction", 0)) > cfg.target_polarization_threshold,
                float(data["pml_energy_fraction"]) < 0.5,
                float(data["even_symmetry"]) > 0.9,
                bool(data["centered_peak"]),
            )
            if not all(checks):
                continue
            field_key = "E_target" if "E_target" in data else ("Ex" if cfg.target_mode == "TE0" else "Ey")
            cache[wavelength_nm] = {
                "neff": float(np.real(data["neff_real"])),
                "field": np.asarray(data[field_key]),
                "y": np.asarray(data["x_m"]).reshape(-1),
                "z": np.asarray(data["y_m"]).reshape(-1),
            }
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return cache


def solve_profiles(cfg: Config, force: bool = False) -> None:
    cfg.validate()
    logger = prepare_directories(cfg)
    add_comsol_to_process_path(cfg)
    backend = load_backend()
    bcfg = backend_config(cfg, backend)
    for wavelength_nm in cfg.wavelengths_nm:
        backend.refractive_indices(cfg.core_material, wavelength_nm)
    mph = import_mph_runtime()
    client = mph.Client(cores=cfg.threads, version=cfg.comsol_version)
    try:
        for profile in ("default", "refined"):
            expected = [mat_path(cfg, profile, wavelength) for wavelength in cfg.wavelengths_nm]
            saved_model = model_path(cfg, profile)
            if (not force and saved_model.exists()
                    and all(mode_file_complete(path, cfg.target_mode) for path in expected)):
                logger.info("SKIP complete profile=%s target=%s", profile, cfg.target_mode)
                continue
            logger.info("BUILD profile=%s target=%s", profile, cfg.target_mode)
            model = backend.build_comsol_model(client, bcfg, profile)
            model.parameter("tfilm", f"{cfg.film_thickness_nm}[nm]")
            model.parameter("wtop", f"{cfg.top_width_nm}[nm]")
            model.parameter("etch", f"{cfg.etch_depth_nm}[nm]")
            model.build(); model.mesh()
            cache = {} if force else load_local_prediction_cache(cfg, profile)
            anchor_loader = lambda current_profile=profile: load_local_prediction_cache(
                cfg, current_profile)
            material_checked = False
            deferred: list[tuple[float, str]] = []
            for wavelength_nm in cfg.wavelengths_nm:
                point = backend.ScanPoint(cfg.film_thickness_nm, cfg.top_width_nm,
                                          cfg.etch_depth_nm, wavelength_nm)
                try:
                    model.parameter("lda0", f"{wavelength_nm}[nm]")
                    if hasattr(backend, "_solve_scan_point"):
                        result, modes = backend._solve_scan_point(
                            model, bcfg, point, cache,
                            force_robust=backend.robust_reseed_required(bcfg, wavelength_nm),
                            anchor_loader=anchor_loader)
                    else:
                        result, modes = backend._solve_with_prediction(
                            model, bcfg, point, cache, anchor_loader=anchor_loader)
                    save_mode_result(cfg, profile, wavelength_nm, result, modes)
                    backend._remember_prediction(cache, wavelength_nm, result)
                    if not material_checked:
                        check = backend.verify_material_domains(model, bcfg, point)
                        suffix = "" if cfg.target_mode == "TE0" else "_TM0"
                        (cfg.run_dir / "data" / f"material_domain_{profile}{suffix}.json").write_text(
                            json.dumps(check, indent=2), encoding="utf-8")
                        material_checked = True
                    logger.info(
                        "SOLVED profile=%s wavelength=%g target=%s neff=%.10g pol=%.5f "
                        "method=%s modes=%d loss=%.6g",
                        profile, wavelength_nm, cfg.target_mode, np.real(result["neff"]),
                        result.get("polarization_fraction", result["TE_pol"]),
                        result.get("tracking_method", result.get("search_method", "primary")),
                        modes, result["loss"])
                except backend.PredictionUnavailable as exc:
                    deferred.append((wavelength_nm, str(exc)))
                    logger.warning("DEFER profile=%s wavelength=%g: %s", profile, wavelength_nm, exc)
            # At most one delayed visit, preserving the project fallback rule.
            for wavelength_nm, primary_error in deferred:
                point = backend.ScanPoint(cfg.film_thickness_nm, cfg.top_width_nm,
                                          cfg.etch_depth_nm, wavelength_nm)
                model.parameter("lda0", f"{wavelength_nm}[nm]")
                if hasattr(backend, "_solve_scan_point"):
                    result, modes = backend._solve_scan_point(
                        model, bcfg, point, cache, force_robust=True,
                        anchor_loader=anchor_loader, prediction_only=True,
                        primary_error=primary_error)
                else:
                    result, modes = backend._solve_with_prediction(
                        model, bcfg, point, cache, anchor_loader=anchor_loader,
                        prediction_only=True, primary_error=primary_error)
                save_mode_result(cfg, profile, wavelength_nm, result, modes)
                backend._remember_prediction(cache, wavelength_nm, result)
            model.save(saved_model)
            client.remove(model)
    finally:
        try:
            client.clear()
        except Exception:
            pass


def load_mode(cfg: Config, profile: str, wavelength_nm: float) -> dict[str, Any]:
    path = mat_path(cfg, profile, wavelength_nm)
    if not mode_file_complete(path, cfg.target_mode):
        raise FileNotFoundError(f"Missing or incomplete mode file: {path}")
    data = loadmat(path)
    field_key = "E_target" if "E_target" in data else ("Ex" if cfg.target_mode == "TE0" else "Ey")
    return {
        "path": path,
        "x_m": np.asarray(data["x_m"]).reshape(-1),
        "y_m": np.asarray(data["y_m"]).reshape(-1),
        "field": np.asarray(data[field_key], dtype=np.complex128),
        "neff": float(np.asarray(data["neff_real"]).squeeze()),
        "loss_db_per_m": float(np.asarray(data["loss_comsol_db_per_m"]).squeeze()),
        "te_pol": float(np.asarray(data["TE_pol"]).squeeze()),
        "target_pol": float(np.asarray(data.get(
            "polarization_fraction", data["TE_pol"] if cfg.target_mode == "TE0"
            else 1-data["TE_pol"])).squeeze()),
        "pml_fraction": float(np.asarray(data["pml_energy_fraction"]).squeeze()),
        "even_symmetry": float(np.asarray(data["even_symmetry"]).squeeze()),
    }


def phase_align(field: np.ndarray) -> np.ndarray:
    field = np.asarray(field, dtype=np.complex128)
    peak = field.reshape(-1)[int(np.argmax(np.abs(field)))]
    if abs(peak) == 0:
        raise ValueError("Cannot phase-align a zero field.")
    aligned = field * np.exp(-1j*np.angle(peak))
    if np.real(aligned.reshape(-1)[int(np.argmax(np.abs(aligned)))]) < 0:
        aligned = -aligned
    return aligned


def downsample_grid(mode: dict[str, Any], master_step_nm: float,
                    target_step_nm: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    stride = int(round(target_step_nm / master_step_nm))
    if stride < 1 or not math.isclose(stride*master_step_nm, target_step_nm, abs_tol=1e-9):
        raise ValueError("Target grid is not an integer multiple of the master grid.")
    return mode["x_m"][::stride], mode["y_m"][::stride], phase_align(mode["field"])[::stride, ::stride]


def nonlinear_material_mask(x_m: np.ndarray, y_m: np.ndarray, cfg: Config) -> np.ndarray:
    xx, yy = np.meshgrid(np.asarray(x_m), np.asarray(y_m))
    t = cfg.film_thickness_nm * 1e-9
    etch = cfg.etch_depth_nm * 1e-9
    top = cfg.top_width_nm * 1e-9
    angle = math.radians(cfg.sidewall_angle_deg)
    slab = (yy >= -t-1e-15) & (yy <= -etch+1e-15)
    half_width = top/2 - yy/math.tan(angle)
    rib = (yy > -etch) & (yy <= 1e-15) & (np.abs(xx) <= half_width+1e-15)
    return slab | rib


def integrate2d(values: np.ndarray, x_m: np.ndarray, y_m: np.ndarray) -> complex:
    return np.trapezoid(np.trapezoid(values, x=x_m, axis=1), x=y_m, axis=0)


def effective_area(process: str, fields: tuple[np.ndarray, np.ndarray, np.ndarray],
                   x_m: np.ndarray, y_m: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    e1, e2, e3 = fields
    energies = tuple(float(np.real(integrate2d(abs(field)**2, x_m, y_m))) for field in fields)
    if min(energies) <= 0:
        raise ValueError("Every modal energy integral must be positive.")
    if process == "SHG":
        overlap_density = np.conjugate(e1) * e2**2 * mask
        numerator = energies[0] * energies[1]**2
    elif process == "SFG":
        overlap_density = np.conjugate(e1) * e2 * e3 * mask
        numerator = energies[0] * energies[1] * energies[2]
    elif process == "DFG":
        overlap_density = np.conjugate(e3) * e1 * np.conjugate(e2) * mask
        numerator = energies[0] * energies[1] * energies[2]
    else:
        raise ValueError(f"Unknown process: {process}")
    overlap = integrate2d(overlap_density, x_m, y_m)
    if abs(overlap) == 0:
        raise ValueError("Nonlinear overlap integral is zero.")
    area = float(numerator / abs(overlap)**2)
    return {"area_m2": area, "energies": energies, "overlap": overlap,
            "overlap_density": overlap_density}


# These three functions intentionally retain separate prefactors.
def normalized_efficiency_shg(deff_m_per_v: float, omega_1: float,
                              n1: float, n2: float, seff_m2: float) -> float:
    """SHG limit with its degeneracy factor kept independent from SFG."""
    return deff_m_per_v**2*omega_1**2 / (2*epsilon_0*c**3*n1*n2**2*seff_m2)


def normalized_efficiency_sfg(deff_m_per_v: float, omega_1: float,
                              n1: float, n2: float, n3: float, aeff_m2: float) -> float:
    return 2*deff_m_per_v**2*omega_1**2 / (epsilon_0*c**3*n1*n2*n3*aeff_m2)


def normalized_efficiency_dfg(deff_m_per_v: float, omega_2: float, omega_3: float,
                              n1: float, n2: float, n3: float, seff_m2: float) -> float:
    return 2*deff_m_per_v**2*omega_2*omega_3 / (epsilon_0*c**3*n1*n2*n3*seff_m2)


def process_efficiency(cfg: Config, neffs: tuple[float, float, float], area_m2: float) -> float:
    n1, n2, n3 = neffs
    wavelengths_m = np.asarray(cfg.wave_wavelengths_nm)*1e-9
    omegas = 2*math.pi*c/wavelengths_m
    if cfg.process_type == "SHG":
        return normalized_efficiency_shg(cfg.deff_m_per_v, omegas[0], n1, n2, area_m2)
    if cfg.process_type == "SFG":
        return normalized_efficiency_sfg(cfg.deff_m_per_v, omegas[0], n1, n2, n3, area_m2)
    if cfg.process_type == "DFG":
        return normalized_efficiency_dfg(cfg.deff_m_per_v, omegas[1], omegas[2], n1, n2, n3, area_m2)
    raise ValueError(cfg.process_type)


def db_per_cm_to_power_alpha_per_m(value: float) -> float:
    return value*100*math.log(10)/10


def loss_effective_length_m(cfg: Config) -> float:
    a1, a2, a3 = (db_per_cm_to_power_alpha_per_m(x) for x in cfg.alphas_db_per_cm)
    if cfg.process_type == "SHG":
        output, drive = a1, 2*a2
    elif cfg.process_type == "SFG":
        output, drive = a1, a2+a3
    else:
        output, drive = a3, a1+a2
    length = cfg.device_length_mm*1e-3
    q = (drive-output)/2
    interaction = length if abs(q*length) < 1e-10 else -math.expm1(-q*length)/q
    return math.exp(-output*length/2)*interaction


def mode_overlap(a: np.ndarray, b: np.ndarray) -> float:
    av = np.asarray(a).reshape(-1); bv = np.asarray(b).reshape(-1)
    denominator = float(np.vdot(av, av).real*np.vdot(bv, bv).real)
    return float(abs(np.vdot(av, bv))**2/denominator) if denominator > 0 else float("nan")


def compute_profile(cfg: Config, profile: str, grid_nm: float) -> dict[str, Any]:
    unique = {wavelength: load_mode(cfg, profile, wavelength) for wavelength in cfg.wavelengths_nm}
    modes = tuple(unique[wavelength] for wavelength in cfg.wave_wavelengths_nm)
    sampled = [downsample_grid(mode, cfg.field_sample_nm, grid_nm) for mode in modes]
    x_m, y_m = sampled[0][0], sampled[0][1]
    for x_other, y_other, _ in sampled[1:]:
        if not (np.array_equal(x_m, x_other) and np.array_equal(y_m, y_other)):
            raise ValueError("The three exported fields do not share one common grid.")
    fields = tuple(item[2] for item in sampled)
    mask = nonlinear_material_mask(x_m, y_m, cfg)
    area = effective_area(cfg.process_type, fields, x_m, y_m, mask)
    neffs = tuple(mode["neff"] for mode in modes)
    eta_si = process_efficiency(cfg, neffs, area["area_m2"])
    leff = loss_effective_length_m(cfg)
    beta = tuple(2*math.pi*n/(wavelength*1e-9)
                 for n, wavelength in zip(neffs, cfg.wave_wavelengths_nm))
    mismatch = beta[0]-beta[1]-beta[2]
    qpm_period_um = float(2*math.pi/abs(mismatch)*1e6) if mismatch != 0 else float("inf")
    return {
        "profile": profile, "grid_nm": grid_nm, "x_m": x_m, "y_m": y_m,
        "fields": fields, "mask": mask, **area, "neffs": neffs,
        "losses_comsol_db_per_m": tuple(mode["loss_db_per_m"] for mode in modes),
        "te_pols": tuple(mode["te_pol"] for mode in modes),
        "target_pols": tuple(mode["target_pol"] for mode in modes),
        "pml_fractions": tuple(mode["pml_fraction"] for mode in modes),
        "eta_norm_w_inv_m2": eta_si,
        "eta_norm_percent_w_inv_cm2": eta_si*0.01,
        "loss_effective_length_m": leff,
        "finite_length_ratio_w_inv": eta_si*leff**2,
        "ideal_qpm_period_um": qpm_period_um,
    }


def write_plots(cfg: Config, refined: dict[str, Any]) -> None:
    figdir = cfg.run_dir / "data" / "figures"
    x_um = refined["x_m"]*1e6; y_um = refined["y_m"]*1e6
    fields = refined["fields"]
    unique_indices = [0, 1] if cfg.process_type == "SHG" else [0, 1, 2]
    fig, axes = plt.subplots(1, len(unique_indices), figsize=(6*len(unique_indices), 4.8), squeeze=False)
    for ax, index in zip(axes[0], unique_indices):
        image = ax.imshow(abs(fields[index]), extent=[x_um.min(), x_um.max(), y_um.min(), y_um.max()],
                          origin="lower", aspect="auto", cmap="magma")
        ax.contour(x_um, y_um, refined["mask"].astype(float), levels=[.5], colors="cyan", linewidths=.8)
        component = "Ex" if cfg.target_mode == "TE0" else "Ey"
        ax.set_title(f"Wave {index+1}: {cfg.wave_wavelengths_nm[index]:g} nm, |{component}|")
        ax.set_xlabel("COMSOL x (µm)"); ax.set_ylabel("COMSOL y (µm)")
        fig.colorbar(image, ax=ax, label="a.u.")
    fig.tight_layout(); fig.savefig(figdir/f"{cfg.target_mode.lower()}_fields.png", dpi=220); plt.close(fig)

    density = refined["overlap_density"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for ax, values, title in ((axes[0], np.real(density), "Re(nonlinear overlap density)"),
                              (axes[1], abs(density), "|nonlinear overlap density|")):
        image = ax.imshow(values, extent=[x_um.min(), x_um.max(), y_um.min(), y_um.max()],
                          origin="lower", aspect="auto", cmap="coolwarm" if ax is axes[0] else "viridis")
        ax.contour(x_um, y_um, refined["mask"].astype(float), levels=[.5], colors="white", linewidths=.8)
        ax.set_title(title); ax.set_xlabel("COMSOL x (µm)"); ax.set_ylabel("COMSOL y (µm)")
        fig.colorbar(image, ax=ax)
    fig.tight_layout(); fig.savefig(figdir/"nonlinear_overlap.png", dpi=220); plt.close(fig)

    labels = ["Seff (µm²)", "ηnorm (% W⁻¹ cm⁻²)", "L result (W⁻¹)"]
    values = [refined["area_m2"]*1e12, refined["eta_norm_percent_w_inv_cm2"],
              refined["finite_length_ratio_w_inv"]]
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, values, color=["#4c78a8", "#f58518", "#54a24b"])
    ax.bar_label(bars, fmt="%.6g", padding=3); ax.set_yscale("log"); ax.grid(axis="y", alpha=.25)
    ax.set_title(f"{cfg.core_material} {cfg.process_type} theoretical efficiency")
    fig.tight_layout(); fig.savefig(figdir/"efficiency_summary.png", dpi=220); plt.close(fig)


def postprocess(cfg: Config) -> dict[str, Any]:
    cfg.validate(); prepare_directories(cfg)
    default = compute_profile(cfg, "default", cfg.integration_grid_nm)
    refined = compute_profile(cfg, "refined", cfg.integration_grid_nm)
    refined_check = compute_profile(cfg, "refined", cfg.integration_check_grid_nm)
    neff_deltas = [abs(a-b) for a, b in zip(default["neffs"], refined["neffs"])]
    field_overlaps = [mode_overlap(a, b) for a, b in zip(default["fields"], refined["fields"])]
    area_rel = abs(refined["area_m2"]-default["area_m2"])/abs(refined["area_m2"])
    efficiency_rel = float(abs(refined["eta_norm_w_inv_m2"]-default["eta_norm_w_inv_m2"])/abs(refined["eta_norm_w_inv_m2"]))
    integration_rel = float(abs(refined_check["eta_norm_w_inv_m2"]-refined["eta_norm_w_inv_m2"])/abs(refined_check["eta_norm_w_inv_m2"]))
    convergence = {
        "neff_abs_deltas": neff_deltas,
        "neff_abs_delta_max": max(neff_deltas),
        "field_overlaps": field_overlaps,
        "field_overlap_min": min(field_overlaps),
        "area_relative_delta": area_rel,
        "efficiency_relative_delta": efficiency_rel,
        "integration_20nm_vs_10nm_relative_delta": integration_rel,
        "comsol_loss_default_db_per_m": default["losses_comsol_db_per_m"],
        "comsol_loss_refined_db_per_m": refined["losses_comsol_db_per_m"],
    }
    convergence["passed"] = bool(
        convergence["neff_abs_delta_max"] <= NEFF_ABS_TOL
        and convergence["field_overlap_min"] >= MODE_OVERLAP_MIN
        and area_rel <= EFFICIENCY_REL_TOL
        and efficiency_rel <= EFFICIENCY_REL_TOL
        and integration_rel <= INTEGRATION_REL_TOL)

    summary = {
        "status": "THEORETICAL_LIMIT_ACCEPTED" if convergence["passed"] else "NOT_CONVERGED",
        "formula_process": cfg.process_type,
        "formula_version": FORMULA_VERSION,
        "formula_expression": FORMULA_EXPRESSIONS[cfg.process_type],
        "formula_source": ("user-corrected SHG omega formula, 2026-09-01"
                           if cfg.process_type == "SHG" else "user-supplied formula images"),
        "target_mode": cfg.target_mode,
        "dominant_comsol_component": "ewfd.Ex" if cfg.target_mode == "TE0" else "ewfd.Ey",
        "material": cfg.core_material,
        "top_cladding": cfg.top_cladding,
        "wavelengths_nm": cfg.wave_wavelengths_nm,
        "geometry_nm": {"film": cfg.film_thickness_nm, "etch": cfg.etch_depth_nm,
                         "top_width": cfg.top_width_nm, "bottom_angle_deg": cfg.sidewall_angle_deg},
        "d33_pm_per_v": cfg.d33_pm_per_v,
        "deff_pm_per_v": cfg.deff_m_per_v*1e12,
        "neff": refined["neffs"],
        "target_polarization_fraction": refined["target_pols"],
        "te_polarization_fraction": refined["te_pols"],
        "comsol_loss_db_per_m": refined["losses_comsol_db_per_m"],
        "manual_loss_db_per_cm": cfg.alphas_db_per_cm,
        "effective_area_m2": refined["area_m2"],
        "effective_area_um2": refined["area_m2"]*1e12,
        "eta_norm_w_inv_m2": refined["eta_norm_w_inv_m2"],
        "eta_norm_percent_w_inv_cm2": refined["eta_norm_percent_w_inv_cm2"],
        "device_length_mm": cfg.device_length_mm,
        "loss_effective_length_mm": refined["loss_effective_length_m"]*1e3,
        "finite_length_power_ratio_w_inv": refined["finite_length_ratio_w_inv"],
        "ideal_qpm_period_um": refined["ideal_qpm_period_um"],
        "convergence": convergence,
    }
    summary["theoretical_limit_text"] = theoretical_limit_text(summary)
    data_dir = cfg.run_dir / "data"
    (data_dir/"efficiency_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (data_dir/"convergence_report.json").write_text(json.dumps(convergence, indent=2), encoding="utf-8")
    with (data_dir/"efficiency_summary.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "status", "material", "process", "target_mode", "lambda_1_nm", "lambda_2_nm", "lambda_3_nm",
            "n1", "n2", "n3", "d33_pm_per_v", "deff_pm_per_v", "effective_area_um2",
            "eta_norm_w_inv_m2", "eta_norm_percent_w_inv_cm2", "device_length_mm",
            "finite_length_power_ratio_w_inv", "ideal_qpm_period_um"])
        writer.writeheader(); writer.writerow({
            "status": summary["status"], "material": cfg.core_material, "process": cfg.process_type,
            "target_mode": cfg.target_mode,
            "lambda_1_nm": cfg.lambda_1_nm, "lambda_2_nm": cfg.lambda_2_nm, "lambda_3_nm": cfg.lambda_3_nm,
            "n1": refined["neffs"][0], "n2": refined["neffs"][1], "n3": refined["neffs"][2],
            "d33_pm_per_v": cfg.d33_pm_per_v, "deff_pm_per_v": cfg.deff_m_per_v*1e12,
            "effective_area_um2": refined["area_m2"]*1e12,
            "eta_norm_w_inv_m2": refined["eta_norm_w_inv_m2"],
            "eta_norm_percent_w_inv_cm2": refined["eta_norm_percent_w_inv_cm2"],
            "device_length_mm": cfg.device_length_mm,
            "finite_length_power_ratio_w_inv": refined["finite_length_ratio_w_inv"],
            "ideal_qpm_period_um": refined["ideal_qpm_period_um"],
        })
    write_plots(cfg, refined)
    report = [
        "# Nonlinear efficiency validation report", "",
        f"- Status: **{summary['status']}**",
        f"- Process / mode / material / cladding: {cfg.process_type} / {cfg.target_mode} / {cfg.core_material} / {cfg.top_cladding}",
        f"- Wavelengths (nm): {cfg.wave_wavelengths_nm}",
        f"- Refined neff: {refined['neffs']}",
        f"- Seff/Aeff: {summary['effective_area_um2']:.10g} um^2",
        f"- eta_norm: {summary['eta_norm_percent_w_inv_cm2']:.10g} % W^-1 cm^-2",
        f"- {summary['theoretical_limit_text']}",
        f"- 20-mm P_out/product(P_in): {summary['finite_length_power_ratio_w_inv']:.10g} W^-1",
        f"- Ideal QPM period: {summary['ideal_qpm_period_um']:.10g} um",
        f"- Convergence: {convergence}",
    ]
    (data_dir/"validation_report.md").write_text("\n".join(report), encoding="utf-8")
    return summary


def theoretical_limit_text(summary: dict[str, Any]) -> str:
    """Human-readable result required by both the Python and notebook entry points."""
    process = str(summary["formula_process"])
    percent = float(summary["eta_norm_percent_w_inv_cm2"])
    si_value = float(summary["eta_norm_w_inv_m2"])
    return (f"{process}过程归一化效率理论极限为 {percent:.10g} % W^-1 cm^-2"
            f"（{si_value:.10g} W^-1 m^-2）。")


def validate_outputs(cfg: Config) -> dict[str, Any]:
    required = [mat_path(cfg, profile, wavelength)
                for profile in ("default", "refined") for wavelength in cfg.wavelengths_nm]
    summary_path = cfg.run_dir/"data"/"efficiency_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    required_models = [model_path(cfg, profile) for profile in ("default", "refined")]
    missing = [str(path) for path in required if not mode_file_complete(path, cfg.target_mode)]
    missing.extend(str(path) for path in required_models if not path.exists())
    figures = sorted((cfg.run_dir/"data"/"figures").glob("*.png"))
    models = [path for path in required_models if path.exists()]
    reopen_path = cfg.run_dir/"data"/"mph_reopen_check.json"
    reopen = json.loads(reopen_path.read_text(encoding="utf-8")) if reopen_path.exists() else {}
    result = {"passed": not missing and len(figures) >= 3 and len(models) == 2
              and summary.get("status") == "THEORETICAL_LIMIT_ACCEPTED"
              and summary.get("target_mode", "TE0") == cfg.target_mode
              and (not reopen or reopen.get("passed") is True),
              "missing": missing, "figures": [str(x) for x in figures],
              "models": [str(x) for x in models], "summary_status": summary.get("status"),
              "mph_reopen": reopen}
    (cfg.run_dir/"data"/"output_integrity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def reopen_saved_models(cfg: Config) -> dict[str, Any]:
    """Reopen both independently saved MPH files and require problems()==[]."""
    add_comsol_to_process_path(cfg)
    mph = import_mph_runtime()
    client = mph.Client(cores=cfg.threads, version=cfg.comsol_version)
    checks = []
    try:
        for profile in ("default", "refined"):
            path = model_path(cfg, profile)
            model = client.load(path)
            problems = model.problems()
            checks.append({"profile": profile, "path": str(path), "problems": problems,
                           "passed": problems == []})
            client.remove(model)
    finally:
        try:
            client.clear()
        except Exception:
            pass
    result = {"passed": all(item["passed"] for item in checks), "models": checks}
    (cfg.run_dir/"data"/"mph_reopen_check.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    return result


def run(cfg: Config, force: bool = False) -> dict[str, Any]:
    solve_profiles(cfg, force=force)
    summary = postprocess(cfg)
    integrity = validate_outputs(cfg)
    if not integrity["passed"]:
        raise RuntimeError(f"Output validation failed: {integrity}")
    return summary


def main() -> None:
    configure_text_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "postprocess", "validate", "reopen", "config"),
                        nargs="?", default="run")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    cfg = Config(); cfg.validate()
    if args.command == "run":
        result = run(cfg, force=args.force)
        print(theoretical_limit_text(result))
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.command == "postprocess":
        result = postprocess(cfg)
        print(theoretical_limit_text(result))
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.command == "validate":
        print(json.dumps(validate_outputs(cfg), indent=2))
    elif args.command == "reopen":
        print(json.dumps(reopen_saved_models(cfg), indent=2))
    else:
        print(json.dumps(serializable_config(cfg), indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
