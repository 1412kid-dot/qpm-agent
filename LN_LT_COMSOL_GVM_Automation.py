"""COMSOL 6.4 automation for x-cut LN/LT SHG group-velocity matching.

The validated Period solver supplies the anisotropic cross-section model,
mode selection, adjacent-mode tracking, robust search and prediction fallback.
This driver scans geometry and wavelength, calculates group indices, refines
promising candidates, and exports reproducible tables and figures.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import os
import sys
import traceback
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.io import loadmat


# ============================ DEFAULT CONFIGURATION =========================
DEFAULT_OUTPUT_PARENT = Path(__file__).resolve().parent / "outputs" / "legacy_gvm"
DEFAULT_RUN_NAME = "LN_SIO2_T400_D200_W1000_4000_A67_L1500_1600_S20_TE0_TE0"
BASE_MODULE_PATH = Path(__file__).resolve().parent / "LN_LT_COMSOL_SHG_QPM_Automation.py"


def inclusive_grid(start: float, stop: float, step: float) -> tuple[float, ...]:
    if not all(math.isfinite(float(v)) for v in (start, stop, step)) or step <= 0 or stop < start:
        raise ValueError("Grid requires finite values, positive step, and stop >= start.")
    intervals = (stop - start) / step
    count = round(intervals)
    if not math.isclose(intervals, count, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("Grid end must fall on the start/step lattice.")
    return tuple(float(start + i * step) for i in range(count + 1))


@dataclass(frozen=True)
class Settings:
    output_parent_dir: Path = DEFAULT_OUTPUT_PARENT
    run_name: str = DEFAULT_RUN_NAME
    core_material: str = "LN"
    top_cladding: str = "SIO2"
    thickness_nm: float = 400.0
    etch_depth_nm: float = 200.0
    sidewall_angle_deg: float = 67.0
    top_width_start_nm: float = 1000.0
    top_width_stop_nm: float = 4000.0
    top_width_step_nm: float = 500.0
    wavelength_start_nm: float = 1500.0
    wavelength_stop_nm: float = 1600.0
    wavelength_step_nm: float = 20.0
    fw_derivative_offset_nm: float = 2.0
    sh_derivative_offset_nm: float = 1.0
    mode_pairs: tuple[tuple[str, str], ...] = (("TE0", "TE0"),)
    threads: int = 4
    mesh_profile: str = "default"
    initial_number_of_modes: int = 20
    adjacent_mode_tracking: bool = True
    tracking_number_of_modes: int = 6
    tracking_field_overlap_min: float = 0.95
    robust_reseed_interval: int = 4
    predicted_search_fallback: bool = True
    predicted_field_overlap_min: float = 0.90
    te_threshold: float = 0.5
    field_dx_nm: float = 40.0
    core_max_element_nm: float = 30.0
    sidewall_max_element_nm: float = 20.0
    background_max_element_nm: float = 70.0
    refined_core_max_element_nm: float = 20.0
    refined_sidewall_max_element_nm: float = 15.0
    refined_background_max_element_nm: float = 50.0
    pml_layers: int = 8
    refined_pml_layers: int = 12
    window_width_um: float = 15.0
    window_height_um: float = 4.0
    window_center_y_um: float = -0.25
    pml_thickness_um: float = 1.0
    box_thickness_um: float = 2.0
    # First pass produces only the requested coarse width/wavelength curves.
    enable_refinement: bool = False
    refinement_max_seeds: int = 3
    refinement_width_half_range_nm: float = 200.0
    refinement_width_step_nm: float = 100.0
    refinement_wavelength_half_range_nm: float = 10.0
    refinement_wavelength_step_nm: float = 5.0
    enable_mesh_check: bool = False
    output_root: Path | None = None

    @property
    def run_dir(self) -> Path:
        return self.output_root or (self.output_parent_dir / self.run_name)

    def widths_nm(self) -> tuple[float, ...]:
        return inclusive_grid(self.top_width_start_nm, self.top_width_stop_nm, self.top_width_step_nm)

    def wavelengths_nm(self) -> tuple[float, ...]:
        return inclusive_grid(self.wavelength_start_nm, self.wavelength_stop_nm, self.wavelength_step_nm)

    def mode_branch_wavelengths_nm(self) -> tuple[float, ...]:
        """Pump-equivalent wavelengths registering both center and derivative branches."""
        values = set(self.wavelengths_nm())
        for pump in self.wavelengths_nm():
            values.update((pump - self.fw_derivative_offset_nm, pump + self.fw_derivative_offset_nm,
                           pump - 2.0 * self.sh_derivative_offset_nm,
                           pump + 2.0 * self.sh_derivative_offset_nm))
        return tuple(sorted(values))

    def validate(self) -> None:
        if self.core_material.upper() not in {"LN", "LT"}:
            raise ValueError("core_material must be LN or LT.")
        if self.top_cladding.upper() not in {"AIR", "SIO2"}:
            raise ValueError("top_cladding must be AIR or SIO2.")
        if self.thickness_nm <= self.etch_depth_nm or self.etch_depth_nm < 0:
            raise ValueError("Film thickness must be greater than a nonnegative etch depth.")
        if (not math.isfinite(self.fw_derivative_offset_nm) or self.fw_derivative_offset_nm <= 0
                or not math.isfinite(self.sh_derivative_offset_nm) or self.sh_derivative_offset_nm <= 0):
            raise ValueError("FW and SH derivative offsets must be finite and positive.")
        if not 0 < self.sidewall_angle_deg < 90:
            raise ValueError("sidewall_angle_deg must be between 0 and 90 degrees.")
        if not self.widths_nm() or len(self.wavelengths_nm()) < 3:
            raise ValueError("Need at least one width and at least three pump wavelengths.")
        if self.mesh_profile not in {"default", "refined"}:
            raise ValueError("mesh_profile must be 'default' or 'refined'.")
        if min(self.threads, self.initial_number_of_modes,
               self.tracking_number_of_modes, self.robust_reseed_interval) < 1:
            raise ValueError("Thread, mode and reseed counts must be positive.")
        if not 0 < self.tracking_field_overlap_min <= 1 or not 0 < self.predicted_field_overlap_min <= 1:
            raise ValueError("Field-overlap thresholds must be in (0, 1].")
        if not self.mode_pairs:
            raise ValueError("At least one (FW mode, SH mode) pair is required.")
        for fw, sh in self.mode_pairs:
            if fw not in {"TE0", "TM0"} or sh not in {"TE0", "TM0"}:
                raise ValueError("Each mode pair must contain TE0 and/or TM0.")
        if self.refinement_max_seeds < 0 or self.refinement_width_step_nm <= 0 or self.refinement_wavelength_step_nm <= 0:
            raise ValueError("Invalid local refinement settings.")
        pumps = self.wavelengths_nm()
        physical = [*pumps, *(x / 2 for x in pumps),
                    *(x + d for x in pumps for d in (-self.fw_derivative_offset_nm, self.fw_derivative_offset_nm)),
                    *(x / 2 + d for x in pumps for d in (-self.sh_derivative_offset_nm, self.sh_derivative_offset_nm))]
        limits = (400.0, 5000.0) if self.core_material.upper() == "LN" else (400.0, 4000.0)
        if min(physical) < limits[0] or max(physical) > limits[1]:
            raise ValueError(f"Both FW and SH wavelengths must be within {self.core_material} Sellmeier limits {limits} nm.")


def load_backend() -> Any:
    if not BASE_MODULE_PATH.exists():
        raise FileNotFoundError(f"Validated Period automation not found: {BASE_MODULE_PATH}")
    name = "lnlt_period_automation_for_gvm"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, BASE_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {BASE_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_settings(path: Path | None) -> Settings:
    if path is None:
        cfg = Settings()
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        valid = {item.name for item in fields(Settings)}
        metadata = {"top_widths_nm", "fundamental_wavelengths_nm", "definitions", "created",
                    "fallback_number_of_modes"}
        unknown = set(raw) - valid - metadata
        if unknown:
            raise ValueError(f"Unknown configuration field(s): {', '.join(sorted(unknown))}")
        raw = {key: value for key, value in raw.items() if key in valid}
        raw["output_parent_dir"] = Path(raw.get("output_parent_dir", DEFAULT_OUTPUT_PARENT))
        if raw.get("output_root"):
            raw["output_root"] = Path(raw["output_root"])
        if "mode_pairs" in raw:
            raw["mode_pairs"] = tuple(tuple(p) for p in raw["mode_pairs"])
        cfg = Settings(**raw)
    cfg.validate()
    return cfg


def serializable_settings(cfg: Settings) -> dict[str, Any]:
    data = asdict(cfg)
    data["output_parent_dir"] = str(cfg.output_parent_dir)
    data["output_root"] = str(cfg.run_dir)
    data["mode_pairs"] = [list(p) for p in cfg.mode_pairs]
    data["top_widths_nm"] = list(cfg.widths_nm())
    data["fundamental_wavelengths_nm"] = list(cfg.wavelengths_nm())
    data["definitions"] = {
        "group_index": "n_g = n_eff(lambda0) - lambda0 * [n_eff(lambda0+offset)-n_eff(lambda0-offset)]/(2*offset)",
        "fw_derivative_offset_nm": data["fw_derivative_offset_nm"],
        "sh_derivative_offset_nm": data["sh_derivative_offset_nm"],
        "derivative_sampling": "Fixed symmetric offsets on each wave's own wavelength axis; independent of pump scan step.",
        "signed_group_index_difference": "delta_ng = ng_SH - ng_FW",
        "group_velocity_mismatch": "GVM = abs(delta_ng)/c; fs/mm",
        "sidewall_convention": "angle is measured from horizontal bottom plane; w_bottom = w_top + 2*etch/tan(angle)",
    }
    return data


def prepare_run(cfg: Settings) -> logging.Logger:
    for p in (cfg.run_dir / "model", cfg.run_dir / "data" / "mat", cfg.run_dir / "data" / "figures"):
        p.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "data" / "run_config.json").write_text(
        json.dumps(serializable_settings(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    logger = logging.getLogger("lnlt_gvm")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.FileHandler(cfg.run_dir / "data" / "run.log", encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    base_logger = logging.getLogger("lnlt_comsol")
    base_logger.handlers.clear()
    base_logger.setLevel(logging.INFO)
    base_logger.propagate = False
    for handler in logger.handlers:
        base_logger.addHandler(handler)
    return logger


def base_config(settings: Settings, mode: str, *, widths: tuple[float, ...],
                mesh_profile: str) -> Any:
    base = load_backend()
    return base.Config(
        core_material=settings.core_material.upper(), target_mode=mode, run_profile="full",
        run_name="", output_root=settings.run_dir,
        film_thicknesses_nm=(settings.thickness_nm,), top_widths_nm=widths,
        etch_depths_nm=(settings.etch_depth_nm,), sidewall_angle_deg=settings.sidewall_angle_deg,
        fundamental_wavelengths_nm=settings.mode_branch_wavelengths_nm(),
        initial_number_of_modes=settings.initial_number_of_modes,
        # Match the predicted-center retry to the normal candidate cap; do not
        # trigger the backend's expanded candidate-count pass.
        fallback_number_of_modes=40,
        adjacent_mode_tracking=settings.adjacent_mode_tracking,
        tracking_number_of_modes=settings.tracking_number_of_modes,
        tracking_field_overlap_min=settings.tracking_field_overlap_min,
        robust_reseed_interval=settings.robust_reseed_interval,
        predicted_search_fallback=settings.predicted_search_fallback,
        predicted_field_overlap_min=settings.predicted_field_overlap_min,
        te_threshold=settings.te_threshold, search_neff="TARGET_CORE_INDEX", threads=settings.threads,
        core_max_element_nm=settings.core_max_element_nm,
        sidewall_max_element_nm=settings.sidewall_max_element_nm,
        refined_core_max_element_nm=settings.refined_core_max_element_nm,
        refined_sidewall_max_element_nm=settings.refined_sidewall_max_element_nm,
        background_max_element_nm=settings.background_max_element_nm,
        refined_background_max_element_nm=settings.refined_background_max_element_nm,
        pml_layers=settings.pml_layers, refined_pml_layers=settings.refined_pml_layers,
        top_cladding=settings.top_cladding.upper(), field_dx_nm=settings.field_dx_nm,
        window_width_um=settings.window_width_um, window_height_um=settings.window_height_um,
        window_center_y_um=settings.window_center_y_um, pml_thickness_um=settings.pml_thickness_um,
        box_thickness_um=settings.box_thickness_um, experimental_qpm_data=(),
    )


def mat_path(base: Any, cfg: Any, width_nm: float, wavelength_nm: float,
             suffix: str = "") -> Path:
    return base.mat_path(cfg, cfg.thickness_nm if hasattr(cfg, "thickness_nm") else cfg.film_thicknesses_nm[0],
                         width_nm, cfg.etch_depths_nm[0], wavelength_nm, suffix)


def point_matches(path: Path, cfg: Any, width_nm: float, wavelength_nm: float,
                  mode: str, mesh_profile: str) -> bool:
    base = load_backend()
    if not base.point_complete(path):
        return False
    try:
        data = loadmat(path, squeeze_me=True)
        expected = {"film_thickness": cfg.film_thicknesses_nm[0] * 1e-9,
                    "top_width": width_nm * 1e-9, "etch_depth": cfg.etch_depths_nm[0] * 1e-9,
                    "wavelength": wavelength_nm * 1e-9}
        if str(data.get("core_material", "")) != cfg.core_material or str(data.get("top_cladding", "")) != cfg.top_cladding:
            return False
        if str(data.get("target_mode", "TE0")) != mode:
            return False
        for key, value in expected.items():
            if key not in data or not math.isclose(float(data[key]), value, rel_tol=0, abs_tol=max(abs(value) * 1e-8, 1e-12)):
                return False
        if "sidewall_angle" not in data or not math.isclose(float(data["sidewall_angle"]), cfg.sidewall_angle_deg,
                                                              rel_tol=0, abs_tol=1e-9):
            return False
        expected_mesh = base.mesh_settings(cfg, mesh_profile)
        stored_mesh = json.loads(str(data["mesh_info"]))
        return all(stored_mesh.get(k) == v for k, v in expected_mesh.items())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def write_index(path: Path, records: dict[str, dict[str, Any]]) -> None:
    if not records:
        return
    temp = path.with_suffix(".tmp.csv")
    pd.DataFrame(records.values()).sort_values(["stage", "target_mode", "top_width_nm", "wavelength_nm"]).to_csv(temp, index=False)
    os.replace(temp, path)


def mode_wavelengths(mode_pairs: Iterable[tuple[str, str]], wavelengths: Iterable[float]) -> dict[str, set[float]]:
    requested: dict[str, set[float]] = {}
    for fw_mode, sh_mode in mode_pairs:
        requested.setdefault(fw_mode, set()).update(map(float, wavelengths))
        requested.setdefault(sh_mode, set()).update(float(x) / 2 for x in wavelengths)
    return requested


def derivative_mode_wavelengths(mode_pairs: Iterable[tuple[str, str]], wavelengths: Iterable[float],
                                fw_offset_nm: float, sh_offset_nm: float) -> dict[str, set[float]]:
    """Auxiliary neff samples around each FW/SH center for fixed-step ng."""
    requested: dict[str, set[float]] = {}
    for fw_mode, sh_mode in mode_pairs:
        for pump in map(float, wavelengths):
            requested.setdefault(fw_mode, set()).update((pump - fw_offset_nm, pump + fw_offset_nm))
            sh_center = pump / 2.0
            requested.setdefault(sh_mode, set()).update((sh_center - sh_offset_nm, sh_center + sh_offset_nm))
    return requested


def derivative_stage(stage: str) -> str:
    return f"{stage}_derivative"


def scan_stage(settings: Settings, width: float, pumps: Iterable[float], *, stage: str,
               mesh_profile: str, force: bool, logger: logging.Logger, client: Any) -> None:
    """Solve center and fixed-offset wavelengths as separate resumable stages."""
    pumps = tuple(map(float, pumps))
    center_requests = mode_wavelengths(settings.mode_pairs, pumps)
    derivative_requests = derivative_mode_wavelengths(
        settings.mode_pairs, pumps, settings.fw_derivative_offset_nm, settings.sh_derivative_offset_nm)
    for sample_stage, requests in ((stage, center_requests), (derivative_stage(stage), derivative_requests)):
        for mode, wavelengths in requests.items():
            if wavelengths:
                scan_mode_branch(settings, mode, width, wavelengths, stage=sample_stage,
                                 mesh_profile=mesh_profile, force=force, logger=logger, client=client)


def scan_mode_branch(settings: Settings, mode: str, width_nm: float, wavelengths_nm: Iterable[float],
                     *, stage: str, mesh_profile: str, force: bool,
                     logger: logging.Logger, client: Any) -> None:
    """Solve a same-geometry/mode set while retaining backend retry safeguards."""
    base = load_backend()
    cfg = base_config(settings, mode, widths=(width_nm,), mesh_profile=mesh_profile)
    geometry = (settings.thickness_nm, width_nm, settings.etch_depth_nm)
    # Start near the middle of each branch. This gives edge points a validated
    # same-branch anchor for the post-failure predicted-neff retry.
    requested_set = set(float(x) for x in wavelengths_nm)
    if requested_set:
        center = 0.5 * (min(requested_set) + max(requested_set))
        requested = tuple(sorted(requested_set, key=lambda x: (abs(x - center), x)))
    else:
        requested = ()
    if not requested:
        return
    index_path = settings.run_dir / "data" / "scan_index.csv"
    records = {}
    if index_path.exists():
        try:
            for r in pd.read_csv(index_path).fillna("").to_dict("records"):
                records[str(r["key"])] = r
        except (OSError, ValueError, KeyError):
            pass
    model = None
    try:
        model = base.build_comsol_model(client, cfg, mesh_profile)
        model.parameter("tfilm", f"{settings.thickness_nm:g}[nm]")
        model.parameter("wtop", f"{width_nm:g}[nm]")
        model.parameter("etch", f"{settings.etch_depth_nm:g}[nm]")
        model.build()
        model.mesh()
        cache = {} if force else base.load_prediction_cache(cfg, geometry, mesh_profile, "_" + stage if stage != "coarse" else "")
        suffix = "" if stage == "coarse" else "_" + stage
        queue = [(wl, False, "") for wl in requested]
        material_checked = False
        for wl, prediction_only, primary_error in queue:
            path = base.mat_path(cfg, *geometry, wl, suffix)
            key = f"{stage}|{settings.core_material.upper()}|{settings.top_cladding.upper()}|{mode}|{mesh_profile}|{settings.thickness_nm:g}|{width_nm:g}|{settings.etch_depth_nm:g}|{wl:g}"
            if not force and point_matches(path, cfg, width_nm, wl, mode, mesh_profile):
                logger.info("SKIP complete %s", path.name)
                records[key] = {"key": key, "stage": stage, "target_mode": mode, "top_width_nm": width_nm,
                                "wavelength_nm": wl, "mesh_profile": mesh_profile, "status": "skipped", "mat": str(path), "error": ""}
                write_index(index_path, records)
                continue
            result: dict[str, Any] = {}
            modes: Any = ""
            started = datetime.now().isoformat()
            try:
                model.parameter("lda0", f"{wl:g}[nm]")
                result, modes = base._solve_scan_point(
                    model, cfg, base.ScanPoint(settings.thickness_nm, width_nm, settings.etch_depth_nm, wl),
                    cache, force_robust=branch_reseed_required(base, cfg, wl, requested),
                    prediction_only=prediction_only, primary_error=primary_error)
                base.save_point(cfg, result, base.ScanPoint(settings.thickness_nm, width_nm, settings.etch_depth_nm, wl),
                                mesh_profile, modes, path)
                base._remember_prediction(cache, wl, result)
                if not material_checked:
                    check = base.verify_material_domains(model, cfg, base.ScanPoint(settings.thickness_nm, width_nm, settings.etch_depth_nm, wl))
                    check_name = f"material_domain_check_{stage}_{mode}_W{width_nm:g}.json"
                    (settings.run_dir / "data" / check_name).write_text(json.dumps(check, indent=2), encoding="utf-8")
                    material_checked = True
                status, error = "success", ""
                logger.info("OK stage=%s mode=%s W=%g lambda=%g neff=%.10g pol=%.4f PML=%.3g overlap=%.5f method=%s",
                            stage, mode, width_nm, wl, result["neff"].real, result["polarization_fraction"],
                            result["pml_fraction"], result.get("prediction_field_overlap", 1.0),
                            result.get("tracking_method", result.get("search_method", "primary")))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                status = "failed"
                logger.error("FAILED stage=%s mode=%s W=%g lambda=%g: %s\n%s", stage, mode, width_nm, wl, error, traceback.format_exc())
                if isinstance(exc, base.PredictionUnavailable) and not prediction_only:
                    queue.append((wl, True, error))
                    status = "deferred"
            records[key] = {
                "key": key, "stage": stage, "target_mode": mode, "top_width_nm": width_nm,
                "wavelength_nm": wl, "mesh_profile": mesh_profile, "status": status,
                "started": started, "finished": datetime.now().isoformat(), "mat": str(path),
                "error": error, "search_method": result.get("search_method", status),
                "tracking_method": result.get("tracking_method", status),
                "mode_index": result.get("mode_index", ""),
                "polarization_fraction": result.get("polarization_fraction", float("nan")),
                "pml_fraction": result.get("pml_fraction", float("nan")),
                "even_symmetry": result.get("even_symmetry", float("nan")),
                "prediction_field_overlap": result.get("prediction_field_overlap", float("nan")),
                "search_shift_used": result.get("search_shift_used", float("nan")),
                "prediction_anchors_nm": json.dumps(result.get("prediction_anchors_nm", [])),
                "overlap_reference_wavelength_nm": result.get("overlap_reference_wavelength_nm", float("nan")),
                "primary_search_error": result.get("primary_search_error", ""),
                "modes_requested": modes,
            }
            write_index(index_path, records)
        model.save(settings.run_dir / "model" / f"{settings.core_material.upper()}_xcut_{mode}_{stage}_W{width_nm:g}.mph")
    finally:
        try:
            client.clear()
        except Exception:
            pass


def branch_reseed_required(base: Any, cfg: Any, wavelength_nm: float,
                           branch_wavelengths: Iterable[float] | None = None) -> bool:
    """Apply periodic robust reseeding on the actual stage wavelength grid."""
    branch = base.prediction_branches(cfg, wavelength_nm)
    if branch_wavelengths is None:
        pump_wavelengths = set(map(float, cfg.wavelengths_nm()))
        candidates = sorted(pump_wavelengths | {x / 2 for x in pump_wavelengths})
    else:
        candidates = sorted(set(map(float, branch_wavelengths)))
    relevant = [x for x in candidates if branch.intersection(base.prediction_branches(cfg, x))]
    position = next((i for i, x in enumerate(relevant)
                     if math.isclose(x, wavelength_nm, rel_tol=0.0, abs_tol=1e-6)), None)
    return position is None or position % cfg.robust_reseed_interval == 0


def group_indices(pump_nm: np.ndarray, neff_fw: np.ndarray, neff_sh: np.ndarray,
                  neff_fw_minus: np.ndarray, neff_fw_plus: np.ndarray,
                  neff_sh_minus: np.ndarray, neff_sh_plus: np.ndarray,
                  fw_offset_nm: float = 2.0, sh_offset_nm: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    pump_nm = np.asarray(pump_nm, dtype=float)
    neff_fw = np.asarray(neff_fw, dtype=float)
    neff_sh = np.asarray(neff_sh, dtype=float)
    neff_fw_minus = np.asarray(neff_fw_minus, dtype=float)
    neff_fw_plus = np.asarray(neff_fw_plus, dtype=float)
    neff_sh_minus = np.asarray(neff_sh_minus, dtype=float)
    neff_sh_plus = np.asarray(neff_sh_plus, dtype=float)
    arrays = (neff_fw, neff_sh, neff_fw_minus, neff_fw_plus, neff_sh_minus, neff_sh_plus)
    if pump_nm.ndim != 1 or any(x.shape != pump_nm.shape for x in arrays):
        raise ValueError("Pump wavelengths and all center/auxiliary neff arrays must have matching 1-D shapes.")
    if fw_offset_nm <= 0 or sh_offset_nm <= 0:
        raise ValueError("Derivative offsets must be positive.")
    sh_nm = pump_nm / 2.0
    d_fw = (neff_fw_plus - neff_fw_minus) / (2.0 * fw_offset_nm)
    d_sh = (neff_sh_plus - neff_sh_minus) / (2.0 * sh_offset_nm)
    ng_fw = neff_fw - pump_nm * d_fw
    ng_sh = neff_sh - sh_nm * d_sh
    return ng_fw, ng_sh


def group_velocity_metrics(ng_fw: np.ndarray | float, ng_sh: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
    delta_ng = np.asarray(ng_sh, dtype=float) - np.asarray(ng_fw, dtype=float)
    gvm_fs_per_mm = np.abs(delta_ng) * 1e12 / 299_792_458.0
    return delta_ng, gvm_fs_per_mm


def read_mode(path: Path) -> dict[str, Any] | None:
    base = load_backend()
    if not base.point_complete(path):
        return None
    try:
        d = loadmat(path, squeeze_me=True)
        return {
            "neff": float(np.real(np.asarray(d["neff_real"]).squeeze())),
            "mode_index": int(np.asarray(d["mode_index"]).squeeze()),
            "polarization_fraction": float(np.asarray(d["polarization_fraction"]).squeeze()),
            "pml_fraction": float(np.asarray(d["pml_energy_fraction"]).squeeze()),
            "even_symmetry": float(np.asarray(d["even_symmetry"]).squeeze()),
            "prediction_field_overlap": float(np.asarray(d.get("prediction_field_overlap", 1.0)).squeeze()),
            "mat": str(path),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def read_configured_mode(path: Path, cfg: Any, width_nm: float, wavelength_nm: float,
                         mode: str, mesh_profile: str) -> dict[str, Any] | None:
    if not point_matches(path, cfg, width_nm, wavelength_nm, mode, mesh_profile):
        return None
    return read_mode(path)


def calculate_grid(settings: Settings, widths: Iterable[float], wavelengths: Iterable[float],
                   pairs: Iterable[tuple[str, str]], *, stage: str, mesh_profile: str = "default") -> pd.DataFrame:
    base = load_backend()
    rows: list[dict[str, Any]] = []
    pumps = np.asarray(sorted(set(float(x) for x in wavelengths)), dtype=float)
    for fw_mode, sh_mode in pairs:
        fw_arrays: dict[float, list[dict[str, Any] | None]] = {}
        sh_arrays: dict[float, list[dict[str, Any] | None]] = {}
        for width in sorted(set(map(float, widths))):
            fw_cfg = base_config(settings, fw_mode, widths=(width,), mesh_profile=mesh_profile)
            sh_cfg = base_config(settings, sh_mode, widths=(width,), mesh_profile=mesh_profile)
            suffix = "" if stage == "coarse" else "_" + stage
            deriv_suffix = "_" + derivative_stage(stage)
            fw = [read_configured_mode(base.mat_path(fw_cfg, settings.thickness_nm, width, settings.etch_depth_nm,
                                                     float(wl), suffix), fw_cfg, width, float(wl), fw_mode, mesh_profile)
                  for wl in pumps]
            sh = [read_configured_mode(base.mat_path(sh_cfg, settings.thickness_nm, width, settings.etch_depth_nm,
                                                     float(wl / 2), suffix), sh_cfg, width, float(wl / 2), sh_mode, mesh_profile)
                  for wl in pumps]
            fw_arrays[width], sh_arrays[width] = fw, sh
            nfw = np.array([x["neff"] if x else np.nan for x in fw])
            nsh = np.array([x["neff"] if x else np.nan for x in sh])

            def read_aux(mode_cfg: Any, mode: str, wavelengths_nm: np.ndarray) -> list[dict[str, Any] | None]:
                return [read_configured_mode(
                    base.mat_path(mode_cfg, settings.thickness_nm, width, settings.etch_depth_nm,
                                  float(wl), deriv_suffix), mode_cfg, width, float(wl), mode, mesh_profile)
                        for wl in wavelengths_nm]

            sh_wls = pumps / 2.0
            fw_minus = read_aux(fw_cfg, fw_mode, pumps - settings.fw_derivative_offset_nm)
            fw_plus = read_aux(fw_cfg, fw_mode, pumps + settings.fw_derivative_offset_nm)
            sh_minus = read_aux(sh_cfg, sh_mode, sh_wls - settings.sh_derivative_offset_nm)
            sh_plus = read_aux(sh_cfg, sh_mode, sh_wls + settings.sh_derivative_offset_nm)
            nfw_m = np.array([x["neff"] if x else np.nan for x in fw_minus])
            nfw_p = np.array([x["neff"] if x else np.nan for x in fw_plus])
            nsh_m = np.array([x["neff"] if x else np.nan for x in sh_minus])
            nsh_p = np.array([x["neff"] if x else np.nan for x in sh_plus])
            ngf, ngs = group_indices(pumps, nfw, nsh, nfw_m, nfw_p, nsh_m, nsh_p,
                                     settings.fw_derivative_offset_nm, settings.sh_derivative_offset_nm)
            delta, gvm = group_velocity_metrics(ngf, ngs)
            for i, pump in enumerate(pumps):
                f, s = fw[i], sh[i]
                fm, fp, sm, sp = fw_minus[i], fw_plus[i], sh_minus[i], sh_plus[i]
                centers_complete = f is not None and s is not None
                derivatives_complete = all(x is not None for x in (fm, fp, sm, sp))
                complete = centers_complete and derivatives_complete
                rows.append({
                    "stage": stage, "core_material": settings.core_material.upper(), "top_cladding": settings.top_cladding.upper(),
                    "film_thickness_nm": settings.thickness_nm, "etch_depth_nm": settings.etch_depth_nm,
                    "sidewall_angle_deg": settings.sidewall_angle_deg, "top_width_nm": width,
                    "bottom_width_nm": base.bottom_width_nm(width, settings.etch_depth_nm, settings.sidewall_angle_deg),
                    "fw_mode": fw_mode, "sh_mode": sh_mode,
                    "fundamental_wavelength_nm": float(pump), "second_harmonic_wavelength_nm": float(pump / 2),
                    "neff_fw": nfw[i], "neff_sh": nsh[i], "group_index_fw": ngf[i], "group_index_sh": ngs[i],
                    "delta_ng_sh_minus_fw": delta[i], "gvm_fs_per_mm": gvm[i],
                    "fw_derivative_offset_nm": settings.fw_derivative_offset_nm,
                    "sh_derivative_offset_nm": settings.sh_derivative_offset_nm,
                    "neff_fw_minus": nfw_m[i], "neff_fw_plus": nfw_p[i],
                    "neff_sh_minus": nsh_m[i], "neff_sh_plus": nsh_p[i],
                    "fw_mode_index": f["mode_index"] if f else np.nan, "sh_mode_index": s["mode_index"] if s else np.nan,
                    "fw_polarization_fraction": f["polarization_fraction"] if f else np.nan,
                    "sh_polarization_fraction": s["polarization_fraction"] if s else np.nan,
                    "fw_pml_fraction": f["pml_fraction"] if f else np.nan, "sh_pml_fraction": s["pml_fraction"] if s else np.nan,
                    "fw_even_symmetry": f["even_symmetry"] if f else np.nan, "sh_even_symmetry": s["even_symmetry"] if s else np.nan,
                    "fw_mode_overlap": f["prediction_field_overlap"] if f else np.nan,
                    "sh_mode_overlap": s["prediction_field_overlap"] if s else np.nan,
                    "fw_minus_mode_index": fm["mode_index"] if fm else np.nan,
                    "fw_plus_mode_index": fp["mode_index"] if fp else np.nan,
                    "sh_minus_mode_index": sm["mode_index"] if sm else np.nan,
                    "sh_plus_mode_index": sp["mode_index"] if sp else np.nan,
                    "fw_minus_polarization_fraction": fm["polarization_fraction"] if fm else np.nan,
                    "fw_plus_polarization_fraction": fp["polarization_fraction"] if fp else np.nan,
                    "sh_minus_polarization_fraction": sm["polarization_fraction"] if sm else np.nan,
                    "sh_plus_polarization_fraction": sp["polarization_fraction"] if sp else np.nan,
                    "fw_minus_pml_fraction": fm["pml_fraction"] if fm else np.nan,
                    "fw_plus_pml_fraction": fp["pml_fraction"] if fp else np.nan,
                    "sh_minus_pml_fraction": sm["pml_fraction"] if sm else np.nan,
                    "sh_plus_pml_fraction": sp["pml_fraction"] if sp else np.nan,
                    "fw_minus_mat": fm["mat"] if fm else "", "fw_plus_mat": fp["mat"] if fp else "",
                    "sh_minus_mat": sm["mat"] if sm else "", "sh_plus_mat": sp["mat"] if sp else "",
                    "fw_mat": f["mat"] if f else "", "sh_mat": s["mat"] if s else "",
                    "status": "success" if complete and np.isfinite(gvm[i]) else
                              ("group_index_unavailable" if complete else
                               ("derivative_mode_missing" if centers_complete else "mode_missing")),
                })
    return pd.DataFrame(rows)


def choose_refinement_seeds(coarse: pd.DataFrame, settings: Settings) -> list[tuple[float, float, str, str]]:
    valid = coarse[np.isfinite(coarse["gvm_fs_per_mm"])].sort_values("gvm_fs_per_mm")
    chosen: list[tuple[float, float, str, str]] = []
    for row in valid.itertuples(index=False):
        candidate = (float(row.top_width_nm), float(row.fundamental_wavelength_nm), row.fw_mode, row.sh_mode)
        if all(candidate[2:] != prev[2:] or abs(candidate[0]-prev[0]) > settings.top_width_step_nm/2
               or abs(candidate[1]-prev[1]) > settings.wavelength_step_nm/2 for prev in chosen):
            chosen.append(candidate)
        if len(chosen) >= settings.refinement_max_seeds:
            break
    return chosen


def refinement_jobs(seeds: Iterable[tuple[float, float, str, str]], settings: Settings) -> dict[tuple[str, str], dict[float, set[float]]]:
    jobs: dict[tuple[str, str], dict[float, set[float]]] = {}
    width_bounds = (settings.top_width_start_nm, settings.top_width_stop_nm)
    wl_bounds = (settings.wavelength_start_nm, settings.wavelength_stop_nm)
    for w0, l0, fw_mode, sh_mode in seeds:
        widths = local_lattice(w0, settings.refinement_width_half_range_nm,
                               settings.refinement_width_step_nm, width_bounds)
        wavelengths = local_lattice(l0, settings.refinement_wavelength_half_range_nm,
                                    settings.refinement_wavelength_step_nm, wl_bounds)
        pair = (fw_mode, sh_mode)
        per_width = jobs.setdefault(pair, {})
        for width in widths:
            per_width.setdefault(width, set()).update(wavelengths)
    return jobs


def local_lattice(center: float, half_range: float, step: float,
                  bounds: tuple[float, float]) -> tuple[float, ...]:
    count = int(math.floor(half_range / step + 1e-10))
    points = {float(center + k * step) for k in range(-count, count + 1)}
    points.add(float(center))
    return tuple(sorted(x for x in points if bounds[0] - 1e-9 <= x <= bounds[1] + 1e-9))


def matching_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    valid = frame[np.isfinite(frame["delta_ng_sh_minus_fw"])].copy()
    if valid.empty:
        return pd.DataFrame(columns=["kind", "top_width_nm", "fundamental_wavelength_nm",
                                    "bracket_start_nm", "bracket_end_nm",
                                    "delta_ng_sh_minus_fw", "gvm_fs_per_mm"])
    for keys, group in valid.groupby(["stage", "fw_mode", "sh_mode", "top_width_nm"]):
        group = group.sort_values("fundamental_wavelength_nm")
        for r in group.itertuples(index=False):
            if r.delta_ng_sh_minus_fw == 0:
                rows.append({"kind": "exact_grid_zero", "stage": keys[0], "fw_mode": keys[1], "sh_mode": keys[2],
                             "top_width_nm": keys[3], "fundamental_wavelength_nm": r.fundamental_wavelength_nm,
                             "bracket_start_nm": r.fundamental_wavelength_nm,
                             "bracket_end_nm": r.fundamental_wavelength_nm,
                             "delta_ng_sh_minus_fw": 0.0, "gvm_fs_per_mm": 0.0})
        for a, b in zip(group.iloc[:-1].itertuples(index=False), group.iloc[1:].itertuples(index=False)):
            if a.delta_ng_sh_minus_fw * b.delta_ng_sh_minus_fw < 0:
                frac = -a.delta_ng_sh_minus_fw / (b.delta_ng_sh_minus_fw - a.delta_ng_sh_minus_fw)
                pump = a.fundamental_wavelength_nm + frac * (b.fundamental_wavelength_nm-a.fundamental_wavelength_nm)
                rows.append({"kind": "wavelength_sign_crossing_linear_estimate", "stage": keys[0], "fw_mode": keys[1], "sh_mode": keys[2],
                             "top_width_nm": keys[3], "fundamental_wavelength_nm": pump,
                             "bracket_start_nm": a.fundamental_wavelength_nm,
                             "bracket_end_nm": b.fundamental_wavelength_nm,
                             "delta_ng_sh_minus_fw": 0.0, "gvm_fs_per_mm": 0.0})
    minima = valid.assign(abs_delta_ng=valid.delta_ng_sh_minus_fw.abs()).sort_values("abs_delta_ng")
    for r in minima.itertuples(index=False):
        rows.append({"kind": "sampled_minimum", "stage": r.stage, "fw_mode": r.fw_mode, "sh_mode": r.sh_mode,
                     "top_width_nm": r.top_width_nm, "fundamental_wavelength_nm": r.fundamental_wavelength_nm,
                     "bracket_start_nm": np.nan, "bracket_end_nm": np.nan,
                     "delta_ng_sh_minus_fw": r.delta_ng_sh_minus_fw, "gvm_fs_per_mm": r.gvm_fs_per_mm})
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("gvm_fs_per_mm", kind="stable")
    return out


def export_plots(frame: pd.DataFrame, cfg: Settings) -> None:
    fig_dir = cfg.run_dir / "data" / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    for (fw, sh, stage), group in frame.groupby(["fw_mode", "sh_mode", "stage"]):
        fig, ax = plt.subplots(figsize=(8.2, 5.2))
        for width, curve in group.groupby("top_width_nm"):
            curve = curve.sort_values("fundamental_wavelength_nm")
            ax.plot(curve.fundamental_wavelength_nm, curve.gvm_fs_per_mm, marker="o", label=f"W={width/1000:g} µm")
        ax.set(xlabel="Fundamental wavelength (nm)", ylabel="Group-velocity mismatch (fs/mm)",
               title=f"{cfg.core_material.upper()} {cfg.top_cladding.upper()} {fw}→{sh} ({stage})")
        ax.grid(True, alpha=.35)
        ax.legend(ncol=2, fontsize=8)
        fig.tight_layout()
        fig.savefig(fig_dir / f"gvm_vs_wavelength_{fw}_{sh}_{stage}.png", dpi=220)
        plt.close(fig)
    coarse = frame[(frame.stage == "coarse") & np.isfinite(frame.gvm_fs_per_mm)]
    if coarse.empty:
        return
    for (fw, sh), group in coarse.groupby(["fw_mode", "sh_mode"]):
        pivot = group.pivot_table(index="top_width_nm", columns="fundamental_wavelength_nm", values="gvm_fs_per_mm", aggfunc="first").sort_index()
        fig, ax = plt.subplots(figsize=(8.2, 5.2))
        image = ax.imshow(pivot.to_numpy(), origin="lower", aspect="auto", interpolation="nearest",
                          extent=[pivot.columns.min(), pivot.columns.max(), pivot.index.min()/1000, pivot.index.max()/1000],
                          cmap="viridis")
        ax.set(xlabel="Fundamental wavelength (nm)", ylabel="Top width (µm)", title=f"Coarse GVM map: {fw}→{sh}")
        fig.colorbar(image, ax=ax, label="GVM (fs/mm)")
        fig.tight_layout()
        fig.savefig(fig_dir / f"gvm_heatmap_{fw}_{sh}.png", dpi=220)
        plt.close(fig)


def postprocess(settings: Settings) -> pd.DataFrame:
    coarse = calculate_grid(settings, settings.widths_nm(), settings.wavelengths_nm(), settings.mode_pairs, stage="coarse")
    frames = [coarse]
    coarse_path = settings.run_dir / "data" / "gvm_coarse_results.csv"
    coarse.to_csv(coarse_path, index=False)
    seeds = choose_refinement_seeds(coarse, settings) if settings.enable_refinement else []
    (settings.run_dir / "data" / "refinement_seeds.json").write_text(json.dumps([
        {"top_width_nm": w, "fundamental_wavelength_nm": l, "fw_mode": fw, "sh_mode": sh} for w,l,fw,sh in seeds
    ], indent=2), encoding="utf-8")
    if seeds:
        jobs = refinement_jobs(seeds, settings)
        refined_rows: list[pd.DataFrame] = []
        for pair, width_jobs in jobs.items():
            for width, pump_grid in width_jobs.items():
                refined_rows.append(calculate_grid(settings, [width], pump_grid, [pair], stage="refined", mesh_profile=settings.mesh_profile))
        if refined_rows:
            refined = pd.concat(refined_rows, ignore_index=True).drop_duplicates(
                ["stage", "fw_mode", "sh_mode", "top_width_nm", "fundamental_wavelength_nm"], keep="last")
            refined.to_csv(settings.run_dir / "data" / "gvm_refined_results.csv", index=False)
            frames.append(refined)
    all_results = pd.concat(frames, ignore_index=True)
    all_results.to_csv(settings.run_dir / "data" / "gvm_all_results.csv", index=False)
    index_path = settings.run_dir / "data" / "scan_index.csv"
    if index_path.exists():
        try:
            index = pd.read_csv(index_path).fillna("")
            failed = index[index.status.astype(str).isin(["failed", "deferred"])]
            lines = [f"Scanned eigenmode points: {len(index)}", f"Failed/deferred points: {len(failed)}"]
            if not failed.empty:
                lines.extend(["", "Failures:"])
                for row in failed.itertuples(index=False):
                    lines.append(f"{row.stage} {row.target_mode} W={row.top_width_nm} nm, lambda={row.wavelength_nm} nm: {row.error}")
            (settings.run_dir / "data" / "missing_points_report.txt").write_text("\n".join(lines), encoding="utf-8")
        except (OSError, ValueError, AttributeError):
            pass
    candidates = matching_candidates(all_results)
    candidates.to_csv(settings.run_dir / "data" / "match_candidates.csv", index=False)
    export_plots(all_results, settings)
    valid = all_results[np.isfinite(all_results.gvm_fs_per_mm)].sort_values("gvm_fs_per_mm")
    summary = {
        "created": datetime.now().isoformat(), "run_config": str(settings.run_dir / "data" / "run_config.json"),
        "coarse_expected_points_per_pair": len(settings.widths_nm()) * len(settings.wavelengths_nm()),
        "coarse_valid_points": int(np.isfinite(coarse.gvm_fs_per_mm).sum()),
        "coarse_expected_mode_samples": len(settings.widths_nm()) * (
            sum(len(v) for v in mode_wavelengths(settings.mode_pairs, settings.wavelengths_nm()).values())
            + sum(len(v) for v in derivative_mode_wavelengths(
                settings.mode_pairs, settings.wavelengths_nm(), settings.fw_derivative_offset_nm,
                settings.sh_derivative_offset_nm).values())),
        "fw_derivative_offset_nm": settings.fw_derivative_offset_nm,
        "sh_derivative_offset_nm": settings.sh_derivative_offset_nm,
        "refined_valid_points": int(np.isfinite(all_results.loc[all_results.stage == "refined", "gvm_fs_per_mm"]).sum()),
        "mode_pairs": [list(p) for p in settings.mode_pairs],
        "best_sample": None,
        "linear_wavelength_crossings": int((candidates.kind == "wavelength_sign_crossing_linear_estimate").sum()) if not candidates.empty else 0,
    }
    if not valid.empty:
        best = valid.iloc[0]
        summary["best_sample"] = {k: (float(best[k]) if isinstance(best[k], (float, np.floating)) else str(best[k]))
                                   for k in ("stage", "fw_mode", "sh_mode", "top_width_nm", "fundamental_wavelength_nm",
                                             "second_harmonic_wavelength_nm", "delta_ng_sh_minus_fw", "gvm_fs_per_mm")}
    (settings.run_dir / "data" / "result_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return all_results


def scan(settings: Settings, *, force: bool = False) -> None:
    settings.validate()
    logger = prepare_run(settings)
    logger.info("Run directory: %s", settings.run_dir)
    logger.info("Configuration: %s %s; widths=%s nm; pumps=%s nm; mode pairs=%s",
                settings.core_material.upper(), settings.top_cladding.upper(), settings.widths_nm(), settings.wavelengths_nm(), settings.mode_pairs)
    base = load_backend()
    for wl in (*settings.wavelengths_nm(), *(x/2 for x in settings.wavelengths_nm())):
        base.refractive_indices(settings.core_material.upper(), wl)
    import mph
    client = mph.Client(cores=settings.threads, version="6.4")
    try:
        for width in settings.widths_nm():
            scan_stage(settings, width, settings.wavelengths_nm(), stage="coarse",
                       mesh_profile=settings.mesh_profile, force=force, logger=logger, client=client)
        coarse = calculate_grid(settings, settings.widths_nm(), settings.wavelengths_nm(), settings.mode_pairs, stage="coarse")
        coarse.to_csv(settings.run_dir / "data" / "gvm_coarse_results.csv", index=False)
        seeds = choose_refinement_seeds(coarse, settings) if settings.enable_refinement else []
        seed_rows = [{"top_width_nm": w, "fundamental_wavelength_nm": l, "fw_mode": fw, "sh_mode": sh} for w,l,fw,sh in seeds]
        (settings.run_dir / "data" / "refinement_seeds.json").write_text(json.dumps(seed_rows, indent=2), encoding="utf-8")
        for pair, width_jobs in refinement_jobs(seeds, settings).items():
            for width, pump_grid in width_jobs.items():
                scan_stage(settings, width, pump_grid, stage="refined", mesh_profile=settings.mesh_profile,
                           force=force, logger=logger, client=client)
        results = postprocess(settings)
        # Optional refined-mesh verification is intentionally off for the first-pass plots.
        if settings.enable_mesh_check:
            valid = results[np.isfinite(results.gvm_fs_per_mm)].sort_values("gvm_fs_per_mm")
            if not valid.empty:
                best = valid.iloc[0]
                wl = float(best.fundamental_wavelength_nm)
                w = float(best.top_width_nm)
                local_wls = sorted(set([wl, max(settings.wavelength_start_nm, wl-settings.refinement_wavelength_step_nm),
                                        max(settings.wavelength_start_nm, wl-2*settings.refinement_wavelength_step_nm),
                                        min(settings.wavelength_stop_nm, wl+settings.refinement_wavelength_step_nm),
                                        min(settings.wavelength_stop_nm, wl+2*settings.refinement_wavelength_step_nm)]))
                pair = (best.fw_mode, best.sh_mode)
                scan_stage(settings, w, local_wls, stage="meshcheck_default", mesh_profile="default",
                           force=force, logger=logger, client=client)
                scan_stage(settings, w, local_wls, stage="meshcheck", mesh_profile="refined",
                           force=force, logger=logger, client=client)
                check = calculate_grid(settings, [w], local_wls, [pair], stage="meshcheck", mesh_profile="refined")
                check.to_csv(settings.run_dir / "data" / "mesh_convergence_best_candidate.csv", index=False)
                default_same = calculate_grid(settings, [w], local_wls, [pair], stage="meshcheck_default", mesh_profile="default")
                compare = check.merge(default_same[["top_width_nm", "fundamental_wavelength_nm", "gvm_fs_per_mm"]],
                                      on=["top_width_nm", "fundamental_wavelength_nm"], suffixes=("_refined", "_default"))
                if not compare.empty:
                    compare["gvm_difference_fs_per_mm"] = compare.gvm_fs_per_mm_refined - compare.gvm_fs_per_mm_default
                    compare.to_csv(settings.run_dir / "data" / "mesh_convergence_comparison.csv", index=False)
        logger.info("Completed scan and exports in %s", settings.run_dir)
    finally:
        try:
            client.clear()
        except Exception:
            pass


def load_existing_and_postprocess(settings: Settings) -> pd.DataFrame:
    settings.validate()
    return postprocess(settings)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("run", "postprocess"), default="run")
    parser.add_argument("--config", type=Path, help="JSON file overriding scan parameters.")
    parser.add_argument("--force", action="store_true", help="Recompute completed scan points.")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    if args.command == "run":
        scan(settings, force=args.force)
    else:
        load_existing_and_postprocess(settings)


if __name__ == "__main__":
    main()
