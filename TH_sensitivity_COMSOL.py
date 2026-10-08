"""COMSOL 6.4 automation for thickness sensitivity of x-cut LN/LT SHG waveguides.

Geometry is configured in the user-configuration block near the top of this
file. The model uses x-cut LN or LT with a selectable upper cladding. FW and SH use
the same selectable fundamental polarization branch (TE0 or TM0) in a 2-D
EWFD mode analysis.

The script intentionally reuses the validated COMSOL model builder in the
neighbouring Period project.  All run state and results are nevertheless
written to the standalone TH_sensitivity output directory.
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
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, ScalarFormatter
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from scipy.io import loadmat, savemat
from scipy.optimize import brentq


def inclusive_wavelength_grid(start_nm: float, stop_nm: float, step_nm: float) -> tuple[float, ...]:
    """Return a floating-point-safe, closed, uniformly spaced wavelength grid."""
    values = (float(start_nm), float(stop_nm), float(step_nm))
    if not all(math.isfinite(x) for x in values):
        raise ValueError("Wavelength start, stop and step must be finite.")
    if step_nm <= 0 or stop_nm < start_nm:
        raise ValueError("Wavelength step must be positive and stop must be >= start.")
    intervals = (stop_nm - start_nm) / step_nm
    rounded_intervals = round(intervals)
    if not math.isclose(intervals, rounded_intervals, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError(
            "WAVELENGTH_STOP_NM must lie on the start/step grid; "
            "use an explicit FUNDAMENTAL_WAVELENGTHS_NM tuple for a nonuniform grid."
        )
    return tuple(float(start_nm + i * step_nm) for i in range(rounded_intervals + 1))


# ====================== 用户配置：保存位置与运行名称 ======================
# 修改下面两项即可；最终结果目录 = OUTPUT_PARENT_DIR / RUN_NAME。
# RUN_NAME 填子文件夹名，不填完整路径。不同结构/参数建议使用不同运行名称，
# 避免断点续算误用同名目录中已有的 MAT 数据。
OUTPUT_PARENT_DIR = Path(__file__).resolve().parent / "outputs" / "legacy_thickness"  # 结果父目录
RUN_NAME = "LN_T400_D200_W3000_TH"                            # 本次运行的子文件夹名称
# 例如父目录改为 E:\work\Comsol Simulation\TH_sensitivity，
# RUN_NAME 改为 LT_T800_D550_W3000_run02，则结果保存到该父目录下的 run02 子目录。
OUTPUT_ROOT = OUTPUT_PARENT_DIR / RUN_NAME              # 自动拼接，无需手动修改

CODE_ROOT = Path(__file__).resolve().parent
BASE_MODULE_PATH = Path(__file__).resolve().parent / "LN_LT_COMSOL_SHG_QPM_Automation.py"

CORE_MATERIAL = "LN"                 # "LN" 或 "LT"
WAVELENGTH_START_NM = 1480.0
WAVELENGTH_STOP_NM = 1620.0
WAVELENGTH_STEP_NM = 20.0
FUNDAMENTAL_WAVELENGTHS_NM = inclusive_wavelength_grid(
    WAVELENGTH_START_NM, WAVELENGTH_STOP_NM, WAVELENGTH_STEP_NM
)
# 非均匀扫描时，可直接将上一行替换为严格递增的元组，例如：
# FUNDAMENTAL_WAVELENGTHS_NM = (1480.0, 1505.0, 1540.0, 1585.0, 1620.0)
NOMINAL_THICKNESS_NM = 400.0
MAIN_HALF_STEP_NM = 2.0
CHECK_HALF_STEP_NM = 1.0
TOP_WIDTH_NM = 3000.0
ETCH_DEPTH_NM = 200.0
SIDEWALL_ANGLE_DEG = 67.0
TOP_CLADDING = "SIO2"               # "AIR" 或 "SIO2"
TARGET_MODE = "TE0"                 # "TE0" 或 "TM0"；FW和SH使用同一模式支路
# 留空时自动审计首点、中间点、末点和初扫最小|灵敏度|点；手动点必须属于正式网格。
MANUAL_AUDIT_WAVELENGTHS_NM: tuple[float, ...] = ()
GROUP_AUDIT_PUMP_HALF_STEP_NM = 2.0
# 可选观察波段；设为 None 时不绘制。仅与扫描范围相交时才显示。
OBSERVED_BAND_NM: tuple[float, float] | None = None
# SHG周期代码同款提速：每个模式支路每4点稳健重播种，其余点优先6模跟踪。
ENABLE_ADJACENT_MODE_TRACKING = True
TRACKING_NUMBER_OF_MODES = 6
TRACKING_FIELD_OVERLAP_MIN = 0.95
ROBUST_RESEED_INTERVAL = 4
# 快速跟踪失败后回到原20/40搜索；仍失败才预测中心用40模重试。
ENABLE_PREDICTED_SEARCH_FALLBACK = True
PREDICTED_FIELD_OVERLAP_MIN = 0.90
INITIAL_NUMBER_OF_MODES = 20
FALLBACK_NUMBER_OF_MODES = 40

C_M_PER_S = 299_792_458.0
GVM_FS_PER_MM_PER_INDEX = 1.0e12 / C_M_PER_S


def _load_base_module() -> Any:
    if not BASE_MODULE_PATH.exists():
        raise FileNotFoundError(f"Validated Period automation is missing: {BASE_MODULE_PATH}")
    name = "lnlt_period_automation_base"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, BASE_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {BASE_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = _load_base_module()


@dataclass(frozen=True)
class Settings:
    output_root: Path = OUTPUT_ROOT
    core_material: str = CORE_MATERIAL
    fundamental_wavelengths_nm: tuple[float, ...] = FUNDAMENTAL_WAVELENGTHS_NM
    nominal_thickness_nm: float = NOMINAL_THICKNESS_NM
    main_half_step_nm: float = MAIN_HALF_STEP_NM
    check_half_step_nm: float = CHECK_HALF_STEP_NM
    top_width_nm: float = TOP_WIDTH_NM
    etch_depth_nm: float = ETCH_DEPTH_NM
    sidewall_angle_deg: float = SIDEWALL_ANGLE_DEG
    top_cladding: str = TOP_CLADDING
    target_mode: str = TARGET_MODE
    threads: int = 4
    adjacent_mode_tracking: bool = ENABLE_ADJACENT_MODE_TRACKING
    tracking_number_of_modes: int = TRACKING_NUMBER_OF_MODES
    tracking_field_overlap_min: float = TRACKING_FIELD_OVERLAP_MIN
    robust_reseed_interval: int = ROBUST_RESEED_INTERVAL
    initial_number_of_modes: int = INITIAL_NUMBER_OF_MODES
    fallback_number_of_modes: int = FALLBACK_NUMBER_OF_MODES
    field_overlap_min: float = 0.90  # cross-thickness audit threshold
    group_index_abs_tolerance: float = 2.0e-3
    thickness_relative_tolerance: float = 0.05
    thickness_absolute_tolerance: float = 1.0e-5
    predicted_search_fallback: bool = ENABLE_PREDICTED_SEARCH_FALLBACK
    predicted_field_overlap_min: float = PREDICTED_FIELD_OVERLAP_MIN
    manual_audit_wavelengths_nm: tuple[float, ...] = MANUAL_AUDIT_WAVELENGTHS_NM
    group_audit_pump_half_step_nm: float = GROUP_AUDIT_PUMP_HALF_STEP_NM
    observed_band_nm: tuple[float, float] | None = OBSERVED_BAND_NM

    def validate(self) -> None:
        material = self.core_material.upper()
        if material not in {"LN", "LT"}:
            raise ValueError("CORE_MATERIAL must be 'LN' or 'LT'.")
        if self.nominal_thickness_nm <= self.etch_depth_nm:
            raise ValueError("Film thickness must exceed etch depth.")
        if self.main_half_step_nm <= 0 or self.check_half_step_nm <= 0:
            raise ValueError("Finite-difference half steps must be positive.")
        if self.target_mode not in {"TE0", "TM0"}:
            raise ValueError("TARGET_MODE must be 'TE0' or 'TM0'.")
        if self.top_cladding not in {"AIR", "SIO2"}:
            raise ValueError("TOP_CLADDING must be 'AIR' or 'SIO2'.")
        if min(self.tracking_number_of_modes, self.initial_number_of_modes,
               self.fallback_number_of_modes, self.robust_reseed_interval) < 1:
            raise ValueError("Mode counts and robust reseed interval must be positive.")
        if not 0 < self.tracking_field_overlap_min <= 1 or not 0 < self.predicted_field_overlap_min <= 1:
            raise ValueError("Mode-overlap thresholds must be in (0, 1].")
        pump = np.asarray(self.fundamental_wavelengths_nm, dtype=float)
        if pump.size < 3:
            raise ValueError("At least three fundamental wavelengths are required for dispersion fitting.")
        if not np.all(np.isfinite(pump)) or np.any(pump <= 0):
            raise ValueError("All fundamental wavelengths must be finite and positive.")
        if np.any(np.diff(pump) <= 0):
            raise ValueError("Fundamental wavelengths must be strictly increasing and non-duplicated.")
        _validate_sellmeier_wavelengths(material, physical_wavelengths(pump))
        grid = tuple(float(x) for x in pump)
        off_grid = [
            float(x) for x in self.manual_audit_wavelengths_nm
            if not any(math.isclose(float(x), y, rel_tol=0.0, abs_tol=1.0e-8) for y in grid)
        ]
        if off_grid:
            raise ValueError(f"Manual audit wavelengths must belong to the formal grid; invalid: {off_grid}")
        if self.group_audit_pump_half_step_nm <= 0:
            raise ValueError("GROUP_AUDIT_PUMP_HALF_STEP_NM must be positive.")
        if self.observed_band_nm is not None:
            if len(self.observed_band_nm) != 2 or not all(math.isfinite(float(x)) for x in self.observed_band_nm):
                raise ValueError("OBSERVED_BAND_NM must be None or two finite wavelengths.")
            if self.observed_band_nm[0] >= self.observed_band_nm[1]:
                raise ValueError("OBSERVED_BAND_NM must be strictly increasing.")


def _validate_sellmeier_wavelengths(material: str, wavelengths_nm: Iterable[float]) -> None:
    limits_um = {"LN": (0.4, 5.0), "LT": (0.4, 4.0)}
    lo_um, hi_um = limits_um[material.upper()]
    bad = sorted({float(w) for w in wavelengths_nm if not lo_um * 1000 <= float(w) <= hi_um * 1000})
    if bad:
        raise ValueError(
            f"{material.upper()} Sellmeier model is valid from {lo_um:g} to {hi_um:g} um; "
            f"offending physical wavelength(s): {bad} nm. Both FW and SH wavelengths are checked."
        )


def make_base_config(settings: Settings, mesh_profile: str = "default") -> Any:
    del mesh_profile  # mesh profile is passed to the builder, not stored here.
    return base.Config(
        core_material=settings.core_material.upper(),
        target_mode=settings.target_mode,
        run_profile="full",
        run_name="",  # 内部适配：output_root 已包含顶部 RUN_NAME，不要在此重复设置。
        output_root=settings.output_root,
        film_thicknesses_nm=(
            settings.nominal_thickness_nm - settings.main_half_step_nm,
            settings.nominal_thickness_nm,
            settings.nominal_thickness_nm + settings.main_half_step_nm,
        ),
        top_widths_nm=(settings.top_width_nm,),
        etch_depths_nm=(settings.etch_depth_nm,),
        sidewall_angle_deg=settings.sidewall_angle_deg,
        fundamental_wavelengths_nm=settings.fundamental_wavelengths_nm,
        initial_number_of_modes=settings.initial_number_of_modes,
        fallback_number_of_modes=settings.fallback_number_of_modes,
        adjacent_mode_tracking=settings.adjacent_mode_tracking,
        tracking_number_of_modes=settings.tracking_number_of_modes,
        tracking_field_overlap_min=settings.tracking_field_overlap_min,
        robust_reseed_interval=settings.robust_reseed_interval,
        predicted_search_fallback=settings.predicted_search_fallback,
        predicted_field_overlap_min=settings.predicted_field_overlap_min,
        te_threshold=0.5,
        search_neff="TARGET_CORE_INDEX",
        threads=settings.threads,
        core_max_element_nm=30.0,
        sidewall_max_element_nm=20.0,
        refined_core_max_element_nm=20.0,
        refined_sidewall_max_element_nm=15.0,
        background_max_element_nm=70.0,
        refined_background_max_element_nm=50.0,
        pml_layers=8,
        refined_pml_layers=12,
        top_cladding=settings.top_cladding,
        experimental_qpm_data=(),
    )


def physical_wavelengths(fundamentals_nm: Iterable[float]) -> tuple[float, ...]:
    values = {float(x) for x in fundamentals_nm}
    values.update(float(x) / 2.0 for x in fundamentals_nm)
    return tuple(sorted(values))


def ensure_output_dirs(settings: Settings) -> None:
    for path in (
        settings.output_root / "model",
        settings.output_root / "data" / "mat",
        settings.output_root / "data" / "figures",
        settings.output_root / "data" / "audit",
    ):
        path.mkdir(parents=True, exist_ok=True)


def configure_logger(settings: Settings) -> logging.Logger:
    ensure_output_dirs(settings)
    logger = logging.getLogger("lnlt_thickness_sensitivity")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(settings.output_root / "data" / "run.log", encoding="utf-8")
    stream_handler = logging.StreamHandler()
    for handler in (file_handler, stream_handler):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    # The shared SHG solver logs tracking/reseed/fallback decisions here.
    base_logger = logging.getLogger("lnlt_comsol")
    base_logger.handlers.clear()
    base_logger.setLevel(logging.INFO)
    base_logger.propagate = False
    for handler in (file_handler, stream_handler):
        base_logger.addHandler(handler)
    return logger


def save_config(settings: Settings) -> None:
    payload = asdict(settings)
    payload["output_root"] = str(settings.output_root)
    payload["output_parent_dir"] = str(settings.output_root.parent)
    payload["run_name"] = settings.output_root.name
    payload["created_or_updated"] = datetime.now().isoformat()
    payload["definitions"] = {
        "phase_mismatch": "Delta_k = beta_SH(lambda/2) - 2 beta_FW(lambda) - 2*pi/Lambda",
        "thickness_sensitivity": "dDelta_k/dt at fixed pump wavelength and fixed QPM period",
        "group_index": "n_g = n_eff - lambda*dn_eff/dlambda",
        "GVM": "abs(n_g_SH - n_g_FW)/c",
        "wavelength_sensitivity": "dlambda_PM/dt = -(dDelta_k/dt)/(dDelta_k/dlambda)",
    }
    (settings.output_root / "data" / "run_config.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def mat_path(settings: Settings, thickness_nm: float, wavelength_nm: float, suffix: str = "") -> Path:
    mode_suffix = "" if settings.target_mode == "TE0" else "_TM0"
    name = (
        f"{settings.core_material.upper()}_T{thickness_nm:g}_W{settings.top_width_nm:.0f}_D{settings.etch_depth_nm:.0f}_"
        f"A{settings.sidewall_angle_deg:.0f}_L{wavelength_nm:g}{mode_suffix}{suffix}.mat"
    )
    return settings.output_root / "data" / "mat" / name


def model_filename(settings: Settings, kind: str = "main") -> str:
    mode_suffix = "" if settings.target_mode == "TE0" else "_TM0"
    kind_suffix = "" if kind == "main" else f"_{kind}"
    return f"{settings.core_material.upper()}_xcut_TH_sensitivity{mode_suffix}{kind_suffix}.mph"


def point_complete(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = loadmat(path)
        return all(k in data for k in ("neff_real", "TE_pol", "Ey", "wavelength")) and data["Ey"].size > 100
    except Exception:
        return False


def configured_point_complete(settings: Settings, thickness_nm: float, wavelength_nm: float,
                              suffix: str = "", mesh_profile: str = "default") -> bool:
    """Reject a structurally valid MAT if its physical configuration differs."""
    path = mat_path(settings, thickness_nm, wavelength_nm, suffix)
    if not point_complete(path):
        return False
    try:
        data = loadmat(path, squeeze_me=True)
        expected_scalars = {
            "film_thickness": thickness_nm * 1e-9,
            "top_width": settings.top_width_nm * 1e-9,
            "etch_depth": settings.etch_depth_nm * 1e-9,
            "sidewall_angle": settings.sidewall_angle_deg,
            "wavelength": wavelength_nm * 1e-9,
        }
        if str(data.get("core_material", "")) != settings.core_material.upper():
            return False
        if str(data.get("top_cladding", "")) != settings.top_cladding:
            return False
        if str(data.get("target_mode", "TE0")) != settings.target_mode:
            return False
        for key, expected in expected_scalars.items():
            if key not in data or not math.isclose(float(data[key]), expected, rel_tol=0.0,
                                                    abs_tol=max(abs(expected) * 1e-8, 1e-12)):
                return False
        expected_mesh = base.mesh_settings(make_base_config(settings), mesh_profile)
        stored_mesh = json.loads(str(data["mesh_info"]))
        return all(stored_mesh.get(key) == value for key, value in expected_mesh.items())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def _read_index(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        rows = pd.read_csv(path).fillna("").to_dict("records")
        return {str(row["key"]): row for row in rows}
    except Exception:
        return {}


def _write_index(path: Path, records: dict[str, dict[str, Any]]) -> None:
    if not records:
        return
    tmp = path.with_suffix(".tmp.csv")
    pd.DataFrame(records.values()).sort_values(["mesh_profile", "thickness_nm", "wavelength_nm"]).to_csv(tmp, index=False)
    os.replace(tmp, path)


def normalized_field_overlap(field_a: np.ndarray, field_b: np.ndarray) -> float:
    a = np.asarray(field_a).reshape(-1)
    b = np.asarray(field_b).reshape(-1)
    if a.size != b.size:
        return float("nan")
    denom = float(np.vdot(a, a).real * np.vdot(b, b).real)
    if denom <= 0:
        return float("nan")
    return float(abs(np.vdot(a, b)) ** 2 / denom)


def _augment_mat(path: Path, additions: dict[str, Any]) -> None:
    data = {k: v for k, v in loadmat(path).items() if not k.startswith("__")}
    data.update(additions)
    tmp = path.with_suffix(".tmp.mat")
    savemat(tmp, data, do_compression=True)
    os.replace(tmp, path)


PredictionUnavailable = base.PredictionUnavailable


def load_prediction_cache(settings: Settings, thickness_nm: float, suffix: str = "",
                          mesh_profile: str = "default") -> dict[float, dict[str, Any]]:
    """Expose the SHG cache loader through the TH interface for tests/tools."""
    cfg = make_base_config(settings, mesh_profile)
    geometry = (thickness_nm, settings.top_width_nm, settings.etch_depth_nm)
    return base.load_prediction_cache(cfg, geometry, mesh_profile, suffix)


def predict_search_center(settings: Settings, wavelength_nm: float,
                          cache: dict[float, dict[str, Any]]) -> tuple[float, list[float], float]:
    return base.predict_search_center(make_base_config(settings), wavelength_nm, cache)


def _solve_point(model: Any, cfg: Any, settings: Settings, thickness_nm: float, wavelength_nm: float,
                 mesh_profile: str, suffix: str, prediction_cache: dict[float, dict[str, Any]], *,
                 force_robust: bool, prediction_only: bool = False,
                 primary_error: str = "") -> tuple[dict[str, Any], int, float]:
    """Use the SHG 6-mode tracking -> robust 20/40 -> predicted-40 pipeline."""
    point = base.ScanPoint(thickness_nm, settings.top_width_nm, settings.etch_depth_nm, wavelength_nm)
    model.parameter("lda0", f"{wavelength_nm:g}[nm]")
    if prediction_only and not primary_error:
        primary_error = "Previously failed/missing point: explicit repair"
    result, modes = base._solve_scan_point(
        model, cfg, point, prediction_cache, force_robust=force_robust,
        prediction_only=prediction_only, primary_error=primary_error,
    )
    overlap = float(result.get("prediction_field_overlap", 1.0))
    path = mat_path(settings, thickness_nm, wavelength_nm, suffix)
    base.save_point(cfg, result, point, mesh_profile, modes, path)
    _augment_mat(path, {"adjacent_mode_overlap": overlap, "scan_kind": suffix or "main"})
    base._remember_prediction(prediction_cache, wavelength_nm, result)
    return result, modes, overlap


def run_jobs(settings: Settings, jobs: dict[float, Iterable[float]], *, mesh_profile: str = "default",
             suffix: str = "", force: bool = False, model_filename_override: str | None = None,
             prediction_only: bool = False) -> None:
    """Solve arbitrary thickness/wavelength jobs with one build+mesh per thickness."""
    settings.validate()
    ensure_output_dirs(settings)
    save_config(settings)
    logger = configure_logger(settings)
    cfg = make_base_config(settings, mesh_profile)
    import mph

    index_path = settings.output_root / "data" / "scan_index.csv"
    records = _read_index(index_path)
    client = mph.Client(cores=settings.threads, version="6.4")
    model = None
    try:
        model = base.build_comsol_model(client, cfg, mesh_profile)
        for thickness_nm, requested in jobs.items():
            wavelengths = tuple(sorted(set(float(x) for x in requested)))
            logger.info("BUILD+MESH profile=%s T=%g nm; %d wavelengths", mesh_profile, thickness_nm, len(wavelengths))
            model.parameter("tfilm", f"{thickness_nm:g}[nm]")
            model.parameter("wtop", f"{settings.top_width_nm:g}[nm]")
            model.parameter("etch", f"{settings.etch_depth_nm:g}[nm]")
            model.build()
            model.mesh()
            geometry = (thickness_nm, settings.top_width_nm, settings.etch_depth_nm)
            prediction_cache = {} if force else base.load_prediction_cache(cfg, geometry, mesh_profile, suffix)
            deferred: list[float] = []
            material_checked = False
            # One deferred pass handles early failures in a fresh scan that initially has no anchors.
            queue = [(wl, prediction_only, "") for wl in wavelengths]
            for wavelength_nm, use_prediction_only, prior_error in queue:
                path = mat_path(settings, thickness_nm, wavelength_nm, suffix)
                key = (f"{settings.core_material.upper()}|{settings.top_cladding}|{settings.target_mode}|"
                       f"{mesh_profile}|{thickness_nm:g}|{wavelength_nm:g}|{suffix}")
                if configured_point_complete(settings, thickness_nm, wavelength_nm, suffix, mesh_profile) and not force:
                    logger.info("SKIP complete %s", path.name)
                    records[key] = records.get(key, {}) | {
                        "key": key, "mesh_profile": mesh_profile, "thickness_nm": thickness_nm,
                        "wavelength_nm": wavelength_nm, "target_mode": settings.target_mode,
                        "status": "skipped", "mat": str(path), "error": "",
                    }
                    _write_index(index_path, records)
                    continue
                started = datetime.now().isoformat()
                logger.info("SOLVE profile=%s T=%g lambda=%g nm", mesh_profile, thickness_nm, wavelength_nm)
                try:
                    result, modes, overlap = _solve_point(
                        model, cfg, settings, thickness_nm, wavelength_nm, mesh_profile, suffix,
                        prediction_cache,
                        force_robust=base.robust_reseed_required(cfg, wavelength_nm),
                        prediction_only=use_prediction_only, primary_error=prior_error,
                    )
                    if not material_checked:
                        check = base.verify_material_domains(
                            model, cfg, base.ScanPoint(thickness_nm, settings.top_width_nm, settings.etch_depth_nm, wavelength_nm)
                        )
                        material_checked = True
                        check_path = settings.output_root / "data" / f"material_check_{mesh_profile}_T{thickness_nm:g}{suffix}.json"
                        check_path.write_text(json.dumps(check, indent=2), encoding="utf-8")
                    status, error = "success", ""
                    logger.info(
                        "OK mode=%s neff=%.10g pol=%.4f symmetry=%.4f PML=%.3g overlap=%.6f modes=%d method=%s",
                        settings.target_mode, result["neff"].real, result["polarization_fraction"],
                        result["even_symmetry"], result["pml_fraction"], overlap, modes,
                        result.get("tracking_method", result.get("search_method", "primary")),
                    )
                except Exception as exc:
                    status = "failed"
                    error = f"{type(exc).__name__}: {exc}"
                    logger.error("FAILED %s\n%s", error, traceback.format_exc())
                    if isinstance(exc, PredictionUnavailable) and wavelength_nm not in deferred:
                        deferred.append(wavelength_nm)
                        queue.append((wavelength_nm, True, error))
                        logger.info("DEFER wavelength=%g until other points provide prediction anchors", wavelength_nm)
                records[key] = {
                    "key": key, "mesh_profile": mesh_profile, "thickness_nm": thickness_nm,
                    "wavelength_nm": wavelength_nm, "target_mode": settings.target_mode,
                    "status": status, "started": started,
                    "finished": datetime.now().isoformat(), "mat": str(path), "error": error,
                    "search_method": result.get("search_method", "primary") if status == "success" else "failed",
                    "tracking_method": result.get("tracking_method", status) if status == "success" else status,
                    "modes_requested": modes if status == "success" else "",
                    "prediction_field_overlap": result.get("prediction_field_overlap", float("nan")) if status == "success" else float("nan"),
                }
                _write_index(index_path, records)
        # Leave the independently openable main model at the nominal geometry.
        model.parameter("tfilm", f"{settings.nominal_thickness_nm:g}[nm]")
        model.build()
        model.mesh()
        filename = model_filename_override or model_filename(
            settings, "refined" if mesh_profile == "refined" else "main"
        )
        model.save(settings.output_root / "model" / filename)
    finally:
        try:
            client.clear()
        except Exception:
            pass


def run_main(settings: Settings, force: bool = False) -> None:
    wavelengths = physical_wavelengths(settings.fundamental_wavelengths_nm)
    jobs = {
        settings.nominal_thickness_nm - settings.main_half_step_nm: wavelengths,
        settings.nominal_thickness_nm: wavelengths,
        settings.nominal_thickness_nm + settings.main_half_step_nm: wavelengths,
    }
    run_jobs(settings, jobs, force=force)


def repair_missing_points(settings: Settings) -> None:
    """Explicit one-off repair: reuse successful MATs and predict only missing points."""
    jobs = {}
    for t in (settings.nominal_thickness_nm - settings.main_half_step_nm,
              settings.nominal_thickness_nm, settings.nominal_thickness_nm + settings.main_half_step_nm):
        missing = [wl for wl in physical_wavelengths(settings.fundamental_wavelengths_nm)
                   if not configured_point_complete(settings, t, wl)]
        if missing:
            jobs[t] = missing
    if jobs:
        run_jobs(settings, jobs, prediction_only=True)
    postprocess(settings)


def load_neff(settings: Settings, thickness_nm: float, wavelength_nm: float, suffix: str = "") -> float:
    path = mat_path(settings, thickness_nm, wavelength_nm, suffix)
    if not configured_point_complete(settings, thickness_nm, wavelength_nm, suffix,
                                     "refined" if suffix == "_refined" else "default"):
        raise FileNotFoundError(f"Missing or invalid mode result: {path}")
    value = float(np.asarray(loadmat(path)["neff_real"]).squeeze().real)
    if not np.isfinite(value):
        raise ValueError(f"Non-finite effective index: {path}")
    return value


def local_polynomial_derivative(x: np.ndarray, y: np.ndarray, *, degree: int = 3, window: int = 5) -> np.ndarray:
    """Derivative dy/dx from a centred local polynomial, one-sided at edges."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.ndim != 1 or y.shape != x.shape or x.size < degree + 1:
        raise ValueError("x and y must be 1-D arrays with enough points.")
    if np.any(np.diff(x) <= 0):
        raise ValueError("x must be strictly increasing.")
    count = min(window, x.size)
    if count <= degree:
        count = degree + 1
    result = np.empty_like(y)
    half = count // 2
    for i, x0 in enumerate(x):
        start = max(0, min(i - half, x.size - count))
        stop = start + count
        scale = max(abs(x[stop - 1] - x[start]), 1.0)
        u = (x[start:stop] - x0) / scale
        coeff = np.polyfit(u, y[start:stop], degree)
        result[i] = np.polyder(np.poly1d(coeff))(0.0) / scale
    return result


def beta_per_um(neff: np.ndarray | float, wavelength_nm: np.ndarray | float) -> np.ndarray:
    wavelength_um = np.asarray(wavelength_nm, dtype=float) / 1000.0
    return 2.0 * np.pi * np.asarray(neff, dtype=float) / wavelength_um


def finite_segments(values: np.ndarray) -> list[np.ndarray]:
    indices = np.flatnonzero(np.isfinite(values))
    if not indices.size:
        return []
    return list(np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1))


def segmented_derivative(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Never fit across missing samples; use quadratic fits for 3-point runs."""
    result = np.full(len(y), np.nan)
    degree_used = np.zeros(len(y), dtype=int)
    for segment in finite_segments(y):
        if len(segment) < 3:
            continue
        degree = min(3, len(segment) - 1)
        result[segment] = local_polynomial_derivative(x[segment], y[segment], degree=degree)
        degree_used[segment] = degree
    return result, degree_used


def _nearest_bracket_root(x: np.ndarray, y: np.ndarray, target_x: float) -> float:
    # Restrict the cross-check to the contiguous valid run containing the target.
    segment = next((s for s in finite_segments(y) if x[s[0]] <= target_x <= x[s[-1]]), None)
    if segment is None:
        return float("nan")
    x, y = x[segment], y[segment]
    if len(x) < 2:
        return float(x[0]) if np.isclose(y[0], 0.0, atol=1e-14) else float("nan")
    interp = PchipInterpolator(x, y, extrapolate=False)
    exact = np.flatnonzero(np.isclose(y, 0.0, atol=1e-14))
    if exact.size:
        return float(x[exact[np.argmin(abs(x[exact] - target_x))]])
    brackets: list[tuple[float, float]] = []
    for left, right, yl, yr in zip(x[:-1], x[1:], y[:-1], y[1:]):
        if yl * yr < 0:
            brackets.append((float(left), float(right)))
    if not brackets:
        return float("nan")
    left, right = min(brackets, key=lambda pair: abs((pair[0] + pair[1]) / 2.0 - target_x))
    return float(brentq(lambda z: float(interp(z)), left, right))


def _branch_arrays(settings: Settings, thickness_nm: float, suffix: str = "", *,
                   issues: list[dict[str, Any]] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pump = np.asarray(settings.fundamental_wavelengths_nm, dtype=float)
    def read_branch(branch: str, divisor: float) -> np.ndarray:
        values = []
        for wl in pump:
            physical = wl / divisor
            try:
                values.append(load_neff(settings, thickness_nm, physical, suffix))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                values.append(float("nan"))
                if issues is not None:
                    issues.append({
                        "fundamental_wavelength_nm": float(wl), "branch": branch,
                        "physical_wavelength_nm": float(physical), "thickness_nm": thickness_nm,
                        "file": str(mat_path(settings, thickness_nm, physical, suffix)),
                        "reason": f"{type(exc).__name__}: {exc}",
                    })
        return np.asarray(values)
    n_fw = read_branch("FW", 1.0)
    n_sh = read_branch("SH", 2.0)
    return pump, n_fw, n_sh


def compute_results(settings: Settings) -> pd.DataFrame:
    settings.validate()
    t0 = settings.nominal_thickness_nm
    h = settings.main_half_step_nm
    issues: list[dict[str, Any]] = []
    pump, n_fw_0, n_sh_0 = _branch_arrays(settings, t0, issues=issues)
    _, n_fw_minus, n_sh_minus = _branch_arrays(settings, t0 - h, issues=issues)
    _, n_fw_plus, n_sh_plus = _branch_arrays(settings, t0 + h, issues=issues)

    sh_wavelength = pump / 2.0
    dn_fw_dlambda, fw_fit_degree = segmented_derivative(pump, n_fw_0)
    dn_sh_dlambda, sh_fit_degree = segmented_derivative(sh_wavelength, n_sh_0)
    ng_fw = n_fw_0 - pump * dn_fw_dlambda
    ng_sh = n_sh_0 - sh_wavelength * dn_sh_dlambda
    delta_ng = ng_sh - ng_fw
    gvm = abs(delta_ng) * GVM_FS_PER_MM_PER_INDEX

    beta_fw_0 = beta_per_um(n_fw_0, pump)
    beta_sh_0 = beta_per_um(n_sh_0, sh_wavelength)
    beta_fw_minus = beta_per_um(n_fw_minus, pump)
    beta_fw_plus = beta_per_um(n_fw_plus, pump)
    beta_sh_minus = beta_per_um(n_sh_minus, sh_wavelength)
    beta_sh_plus = beta_per_um(n_sh_plus, sh_wavelength)

    d_beta_sh_dt = (beta_sh_plus - beta_sh_minus) / (2.0 * h)
    d_2beta_fw_dt = (2.0 * beta_fw_plus - 2.0 * beta_fw_minus) / (2.0 * h)
    thickness_sensitivity = d_beta_sh_dt - d_2beta_fw_dt

    linear_mismatch_0 = beta_sh_0 - 2.0 * beta_fw_0
    qpm_period_um = np.full(pump.shape, np.nan)
    positive_qpm = np.isfinite(linear_mismatch_0) & (linear_mismatch_0 > 0)
    qpm_period_um[positive_qpm] = 2.0 * np.pi / linear_mismatch_0[positive_qpm]
    pump_um = pump / 1000.0
    d_delta_k_dlambda_per_um2 = 4.0 * np.pi * (ng_fw - ng_sh) / pump_um**2
    wavelength_sensitivity_formula = np.full(pump.shape, np.nan)
    nonsingular = (positive_qpm & np.isfinite(thickness_sensitivity)
                   & np.isfinite(d_delta_k_dlambda_per_um2) & (abs(d_delta_k_dlambda_per_um2) > 1e-12))
    wavelength_sensitivity_formula[nonsingular] = (
        -thickness_sensitivity[nonsingular] / d_delta_k_dlambda_per_um2[nonsingular] * 1000.0
    )

    mismatch_minus = beta_sh_minus - 2.0 * beta_fw_minus
    mismatch_plus = beta_sh_plus - 2.0 * beta_fw_plus
    root_minus = np.full(pump.shape, np.nan)
    root_plus = np.full(pump.shape, np.nan)
    for i, grating_vector in enumerate(linear_mismatch_0):
        if not positive_qpm[i]:
            continue
        root_minus[i] = _nearest_bracket_root(pump, mismatch_minus - grating_vector, pump[i])
        root_plus[i] = _nearest_bracket_root(pump, mismatch_plus - grating_vector, pump[i])
    wavelength_sensitivity_root = (root_plus - root_minus) / (2.0 * h)
    root_difference = wavelength_sensitivity_root - wavelength_sensitivity_formula

    rows = pd.DataFrame({
        "core_material": settings.core_material.upper(),
        "target_mode": settings.target_mode,
        "top_cladding": settings.top_cladding,
        "fundamental_wavelength_nm": pump,
        "second_harmonic_wavelength_nm": sh_wavelength,
        "qpm_period_um": qpm_period_um,
        "neff_fw": n_fw_0,
        "neff_sh": n_sh_0,
        "group_index_fw": ng_fw,
        "group_index_sh": ng_sh,
        "group_fit_degree_fw": fw_fit_degree,
        "group_fit_degree_sh": sh_fit_degree,
        "delta_group_index_sh_minus_fw": delta_ng,
        "gvm_fs_per_mm": gvm,
        "d_beta_sh_dt_per_um_per_nm": d_beta_sh_dt,
        "d_2beta_fw_dt_per_um_per_nm": d_2beta_fw_dt,
        "d_delta_k_dt_per_um_per_nm": thickness_sensitivity,
        "d_delta_k_dlambda_per_um2": d_delta_k_dlambda_per_um2,
        "d_lambda_pm_dt_formula_nm_per_nm": wavelength_sensitivity_formula,
        "lambda_pm_minus_nm": root_minus,
        "lambda_pm_plus_nm": root_plus,
        "d_lambda_pm_dt_root_nm_per_nm": wavelength_sensitivity_root,
        "root_minus_formula_nm_per_nm": root_difference,
        "root_crosscheck_available": np.isfinite(wavelength_sensitivity_root),
        "thickness_sensitivity_available": np.isfinite(thickness_sensitivity),
        "gvm_available": np.isfinite(gvm),
        "wavelength_sensitivity_available": np.isfinite(wavelength_sensitivity_formula),
    })
    rows.attrs["mode_issues"] = issues

    # Cross-thickness field overlap at identical wavelengths catches mode swaps.
    overlaps_minus, overlaps_plus = [], []
    def read_field(thickness: float, wavelength: float) -> np.ndarray | None:
        path = mat_path(settings, thickness, wavelength)
        if not point_complete(path):
            return None
        try:
            return loadmat(path)["Ey"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
    for wavelength_nm in physical_wavelengths(pump):
        f0 = read_field(t0, wavelength_nm)
        fm = read_field(t0 - h, wavelength_nm)
        fp = read_field(t0 + h, wavelength_nm)
        overlaps_minus.append((wavelength_nm, normalized_field_overlap(f0, fm)
                               if f0 is not None and fm is not None else float("nan")))
        overlaps_plus.append((wavelength_nm, normalized_field_overlap(f0, fp)
                              if f0 is not None and fp is not None else float("nan")))
    overlap_frame = pd.DataFrame(overlaps_minus, columns=["physical_wavelength_nm", "overlap_Tminus_T0"])
    overlap_frame.insert(0, "core_material", settings.core_material.upper())
    overlap_frame.insert(1, "top_cladding", settings.top_cladding)
    overlap_frame.insert(2, "target_mode", settings.target_mode)
    overlap_frame["overlap_Tplus_T0"] = [x[1] for x in overlaps_plus]
    overlap_frame.to_csv(settings.output_root / "data" / "mode_overlap_checks.csv", index=False)
    return rows


def sensitivity_ylim(values: np.ndarray, padding_fraction: float = 0.10) -> tuple[float, float]:
    """Fit the finite data range without forcing zero into a single-sign curve."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise ValueError("No finite thickness-sensitivity values to plot.")
    low, high = float(finite.min()), float(finite.max())
    span = high - low
    padding = max(span * padding_fraction, max(abs(low), abs(high)) * 1e-6, 1e-12)
    if span == 0:
        padding = max(abs(low) * 0.05, 1e-12)
    return low - padding, high + padding


def plot_results(settings: Settings, results: pd.DataFrame) -> None:
    figdir = settings.output_root / "data" / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    x = results["fundamental_wavelength_nm"].to_numpy()
    span = float(x.max() - x.min())
    smallest_spacing = float(np.min(np.diff(x))) if len(x) > 1 else 1.0
    x_padding = max(0.03 * span, 0.5 * smallest_spacing, 1.0)
    metadata_title = (
        f"{settings.core_material.upper()} x-cut | {settings.target_mode} | "
        f"{settings.top_cladding} upper cladding | {x.min():g}-{x.max():g} nm"
    )

    def decorate(ax: Any, ylabel: str, formula: str, *, formula_above: bool = False) -> None:
        if settings.observed_band_nm is not None:
            band_left = max(float(settings.observed_band_nm[0]), float(x.min()))
            band_right = min(float(settings.observed_band_nm[1]), float(x.max()))
            if band_left < band_right:
                ax.axvspan(band_left, band_right, color="#f2c94c", alpha=0.18,
                           label=f"Observed band {settings.observed_band_nm[0]:g}-{settings.observed_band_nm[1]:g} nm")
        ax.set_xlabel("Fundamental wavelength (nm)")
        ax.set_ylabel(ylabel)
        ax.set_xlim(float(x.min()) - x_padding, float(x.max()) + x_padding)
        ax.set_title(metadata_title, fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.text(0.15 if formula_above else 0.02, 1.035 if formula_above else 0.97,
                formula, transform=ax.transAxes,
                va="bottom" if formula_above else "top", ha="left",
                bbox={"facecolor": "white", "alpha": 0.88, "edgecolor": "0.75"})

    def note_missing(ax: Any, values: np.ndarray) -> None:
        missing = int((~np.isfinite(values)).sum())
        if missing:
            ax.text(0.98, 1.015, f"{missing}/{len(values)} unavailable; gaps retained",
                    transform=ax.transAxes, ha="right", va="bottom", fontsize=8,
                    bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "0.8"})
        if missing == len(values):
            ax.text(0.5, 0.5, "No valid data for this metric", transform=ax.transAxes, ha="center")

    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    y = results["d_delta_k_dt_per_um_per_nm"].to_numpy()
    ax.plot(x, y, "o-", lw=2, color="#1455d9", label=r"$\partial\Delta k/\partial t$")
    lower, upper = sensitivity_ylim(y) if np.isfinite(y).any() else (-1, 1)
    ax.set_ylim(lower, upper)
    if lower <= 0 <= upper:
        ax.axhline(0, color="black", lw=0.9)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    formatter = ScalarFormatter(useOffset=False, useMathText=True)
    formatter.set_powerlimits((-3, 3))
    ax.yaxis.set_major_formatter(formatter)
    if np.isfinite(y).any():
        idx = int(np.nanargmin(abs(y)))
        ax.scatter([x[idx]], [y[idx]], s=85, color="#d62728", zorder=5,
                   label=f"min available |sensitivity|: {x[idx]:.0f} nm")
    note_missing(ax, y)
    decorate(ax, r"$\partial\Delta k/\partial t$ ($\mu$m$^{-1}$ nm$^{-1}$)",
             r"$\Delta k=\beta_{SH}-2\beta_{FW}-2\pi/\Lambda$" + "\n" +
             r"$\partial_t\Delta k\approx[\Delta k(t+h)-\Delta k(t-h)]/(2h),\ h=$"
             + f"{settings.main_half_step_nm:g} nm", formula_above=True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(figdir / "thickness_phase_mismatch_sensitivity.png", dpi=240)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    ax.plot(x, results["gvm_fs_per_mm"], "o-", lw=2, color="#00876c")
    note_missing(ax, results["gvm_fs_per_mm"].to_numpy())
    decorate(ax, "Group-velocity mismatch (fs/mm)",
             r"$n_g=n_{eff}-\lambda\,dn_{eff}/d\lambda$" + "\n" +
             r"$\mathrm{GVM}=|n_{g,SH}-n_{g,FW}|/c$", formula_above=True)
    fig.tight_layout()
    fig.savefig(figdir / "group_velocity_mismatch.png", dpi=240)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    ax.plot(x, results["d_lambda_pm_dt_formula_nm_per_nm"], "o-", lw=2, color="#8c2dba",
            label="Derivative formula")
    note_missing(ax, results["d_lambda_pm_dt_formula_nm_per_nm"].to_numpy())
    valid = results["root_crosscheck_available"].astype(bool).to_numpy()
    ax.scatter(x[valid], results.loc[valid, "d_lambda_pm_dt_root_nm_per_nm"], marker="x", s=45,
               color="#d95f02", label="Fixed-period root cross-check")
    ax.axhline(0, color="black", lw=0.9)
    decorate(ax, r"$d\lambda_{PM}/dt$ (nm/nm)",
             r"$d\lambda_{PM}/dt=-(\partial_t\Delta k)/(\partial_\lambda\Delta k)$" + "\n" +
             r"$\partial_\lambda\Delta k=4\pi(n_{g,FW}-n_{g,SH})/\lambda^2$", formula_above=True)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(figdir / "phase_matching_wavelength_sensitivity.png", dpi=240)
    plt.close(fig)


def write_postprocess_report(settings: Settings, results: pd.DataFrame) -> None:
    issues = results.attrs.get("mode_issues", [])
    index = _read_index(settings.output_root / "data" / "scan_index.csv")
    lines = [
        "后处理缺失点及可用结果报告", f"运行目录：{settings.output_root}",
        f"生成时间：{datetime.now().isoformat()}",
        f"材料/目标模式/上包层：{settings.core_material.upper()} / {settings.target_mode} / {settings.top_cladding}",
        f"正式基频范围：{settings.fundamental_wavelengths_nm[0]:g}–{settings.fundamental_wavelengths_nm[-1]:g} nm；"
        f"共 {len(settings.fundamental_wavelengths_nm)} 点",
        f"缺失/无效模式文件：{len(issues)} 个", "",
        "缺失点不填补、不跨缺口连线；不同指标只使用自身所需的有效数据。",
        "群折射率仅在连续有效波段内求导：4点及以上使用局部三次拟合，",
        "连续3点使用二次拟合（精度降低，非额外收敛验证），不足3点不计算。",
        "固定周期寻根不跨缺口、不外推。图中最小值仅指已有有效点，不代表全波段最小值。", "",
        "一、缺失或无效的模式点：",
    ]
    for issue in sorted(issues, key=lambda item: (item["fundamental_wavelength_nm"], item["thickness_nm"], item["branch"])):
        config_key = (f"{settings.core_material.upper()}|{settings.top_cladding}|{settings.target_mode}|default|"
                      f"{issue['thickness_nm']:g}|{issue['physical_wavelength_nm']:g}|")
        new_key = f"{settings.target_mode}|default|{issue['thickness_nm']:g}|{issue['physical_wavelength_nm']:g}|"
        legacy_key = f"default|{issue['thickness_nm']:g}|{issue['physical_wavelength_nm']:g}|"
        original_error = str(index.get(config_key, index.get(new_key, index.get(legacy_key, {}))).get(
            "error", "")).split("Candidates:")[0].strip()
        lines.extend([
            f"基频 {issue['fundamental_wavelength_nm']:g} nm；{issue['branch']}物理波长 "
            f"{issue['physical_wavelength_nm']:g} nm；膜厚 {issue['thickness_nm']:g} nm",
            f"  文件：{issue['file']}", f"  原因：{issue['reason']}",
        ])
        if original_error:
            lines.append(f"  求解日志：{original_error}")
    if not issues:
        lines.append("无。")
    lines.extend(["", "二、各指标有效点与不可用基频波长："])
    for label, column in (
        ("厚度相位失配敏感度", "d_delta_k_dt_per_um_per_nm"),
        ("群速度失配 GVM", "gvm_fs_per_mm"),
        ("匹配波长厚度敏感度", "d_lambda_pm_dt_formula_nm_per_nm"),
    ):
        mask = np.isfinite(results[column].to_numpy())
        missing = results.loc[~mask, "fundamental_wavelength_nm"]
        wavelengths = ", ".join(f"{wl:g}" for wl in missing) or "无"
        lines.append(f"{label}：{int(mask.sum())}/{len(results)} 个有效点；不可用波长(nm)：{wavelengths}")
    lines.extend(["", "三、降低阶数的群折射率拟合点："])
    for branch in ("fw", "sh"):
        reduced = results.loc[results[f"group_fit_degree_{branch}"] == 2, "fundamental_wavelength_nm"]
        lines.append(f"{branch.upper()} 二次拟合对应基频(nm)：" + (", ".join(f"{wl:g}" for wl in reduced) or "无"))
    (settings.output_root / "data" / "missing_points_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Postprocess: {len(issues)} missing/invalid mode files. Report: "
          f"{settings.output_root / 'data' / 'missing_points_report.txt'}")
    if issues:
        print("Affected pump wavelengths (nm): " + ", ".join(
            f"{wl:g}" for wl in sorted({item["fundamental_wavelength_nm"] for item in issues})))


def postprocess(settings: Settings) -> pd.DataFrame:
    ensure_output_dirs(settings)
    results = compute_results(settings)
    write_postprocess_report(settings, results)
    results.to_csv(settings.output_root / "data" / "sensitivity_results.csv", index=False)
    plot_results(settings, results)
    valid_thickness = results.loc[np.isfinite(results["d_delta_k_dt_per_um_per_nm"])]
    minimum = None
    if not valid_thickness.empty:
        row = valid_thickness.loc[valid_thickness["d_delta_k_dt_per_um_per_nm"].abs().idxmin()]
        minimum = {"fundamental_wavelength_nm": float(row["fundamental_wavelength_nm"]),
                   "value_per_um_per_nm": float(row["d_delta_k_dt_per_um_per_nm"])}
    summary = {
        "core_material": settings.core_material.upper(),
        "target_mode": settings.target_mode,
        "top_cladding": settings.top_cladding,
        "fundamental_wavelengths_nm": list(settings.fundamental_wavelengths_nm),
        "fundamental_wavelength_range_nm": [settings.fundamental_wavelengths_nm[0],
                                              settings.fundamental_wavelengths_nm[-1]],
        "points": len(results),
        "missing_mode_files": len(results.attrs.get("mode_issues", [])),
        "valid_thickness_points": len(valid_thickness),
        "valid_gvm_points": int(np.isfinite(results["gvm_fs_per_mm"]).sum()),
        "valid_wavelength_sensitivity_points": int(np.isfinite(results["d_lambda_pm_dt_formula_nm_per_nm"]).sum()),
        "minimum_abs_thickness_sensitivity": minimum,
        "root_crosschecks_available": int(results["root_crosscheck_available"].sum()),
        "generated": datetime.now().isoformat(),
    }
    (settings.output_root / "data" / "result_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return results


def representative_wavelengths(settings: Settings) -> tuple[float, ...]:
    grid = tuple(float(x) for x in settings.fundamental_wavelengths_nm)
    values = {grid[0], grid[len(grid) // 2], grid[-1], *map(float, settings.manual_audit_wavelengths_nm)}
    result_path = settings.output_root / "data" / "sensitivity_results.csv"
    if result_path.exists():
        frame = pd.read_csv(result_path)
        finite = frame.loc[np.isfinite(frame["d_delta_k_dt_per_um_per_nm"])]
        if not finite.empty:
            row = finite.loc[finite["d_delta_k_dt_per_um_per_nm"].abs().idxmin()]
            values.add(float(row["fundamental_wavelength_nm"]))
    return tuple(sorted(values))


def run_step_and_group_audit(settings: Settings, force: bool = False) -> None:
    targets = representative_wavelengths(settings)
    physical = set(physical_wavelengths(targets))
    # Extra baseline wavelengths for direct small-step group-index checks.
    d = settings.group_audit_pump_half_step_nm
    for pump in targets:
        physical.update((pump - d, pump + d, (pump - d) / 2.0, (pump + d) / 2.0))
    _validate_sellmeier_wavelengths(settings.core_material, physical)
    jobs = {
        settings.nominal_thickness_nm - settings.check_half_step_nm: physical_wavelengths(targets),
        settings.nominal_thickness_nm: tuple(sorted(physical)),
        settings.nominal_thickness_nm + settings.check_half_step_nm: physical_wavelengths(targets),
    }
    run_jobs(settings, jobs, suffix="_audit", force=force,
             model_filename_override=model_filename(settings, "audit"))
    write_step_and_group_audit(settings, targets)


def write_step_and_group_audit(settings: Settings, targets: Iterable[float]) -> None:
    main = pd.read_csv(settings.output_root / "data" / "sensitivity_results.csv").set_index("fundamental_wavelength_nm")
    rows: list[dict[str, Any]] = []
    t0 = settings.nominal_thickness_nm
    h = settings.check_half_step_nm
    for pump in targets:
        nfm = load_neff(settings, t0 - h, pump, "_audit")
        nfp = load_neff(settings, t0 + h, pump, "_audit")
        nsm = load_neff(settings, t0 - h, pump / 2.0, "_audit")
        nsp = load_neff(settings, t0 + h, pump / 2.0, "_audit")
        d1 = ((beta_per_um(nsp, pump / 2.0) - 2 * beta_per_um(nfp, pump)) -
              (beta_per_um(nsm, pump / 2.0) - 2 * beta_per_um(nfm, pump))) / (2 * h)
        d2 = float(main.loc[pump, "d_delta_k_dt_per_um_per_nm"])
        difference = abs(float(d1) - d2)
        tolerance = settings.thickness_absolute_tolerance if abs(d2) < 1e-4 else settings.thickness_relative_tolerance * abs(d2)
        rows.append({
            "audit_kind": "thickness_step", "fundamental_wavelength_nm": pump,
            "main_h2_per_um_per_nm": d2, "check_h1_per_um_per_nm": float(d1),
            "absolute_difference": difference, "tolerance": tolerance, "passed": difference <= tolerance,
        })

    for pump in targets:
        d = settings.group_audit_pump_half_step_nm
        n_fw_0 = float(main.loc[pump, "neff_fw"])
        n_sh_0 = float(main.loc[pump, "neff_sh"])
        dn_fw = (load_neff(settings, t0, pump + d, "_audit") - load_neff(settings, t0, pump - d, "_audit")) / (2 * d)
        dn_sh = (load_neff(settings, t0, (pump + d) / 2.0, "_audit") -
                 load_neff(settings, t0, (pump - d) / 2.0, "_audit")) / d
        ng_fw_micro = n_fw_0 - pump * dn_fw
        ng_sh_micro = n_sh_0 - (pump / 2.0) * dn_sh
        for branch, fitted, micro in (
            ("FW", float(main.loc[pump, "group_index_fw"]), ng_fw_micro),
            ("SH", float(main.loc[pump, "group_index_sh"]), ng_sh_micro),
        ):
            difference = abs(fitted - micro)
            rows.append({
                "audit_kind": f"group_index_{branch}", "fundamental_wavelength_nm": pump,
                "main_local_fit": fitted, "microstep_check": micro, "absolute_difference": difference,
                "tolerance": settings.group_index_abs_tolerance,
                "passed": difference <= settings.group_index_abs_tolerance,
            })
    frame = pd.DataFrame(rows)
    frame.insert(0, "core_material", settings.core_material.upper())
    frame.insert(1, "target_mode", settings.target_mode)
    frame.insert(2, "top_cladding", settings.top_cladding)
    frame.to_csv(settings.output_root / "data" / "audit" / "step_and_group_audit.csv", index=False)


def run_refined_audit(settings: Settings, force: bool = False) -> None:
    targets = representative_wavelengths(settings)
    wavelengths = physical_wavelengths(targets)
    h = settings.main_half_step_nm
    jobs = {
        settings.nominal_thickness_nm - h: wavelengths,
        settings.nominal_thickness_nm + h: wavelengths,
    }
    run_jobs(settings, jobs, mesh_profile="refined", suffix="_refined", force=force)
    main = pd.read_csv(settings.output_root / "data" / "sensitivity_results.csv").set_index("fundamental_wavelength_nm")
    rows = []
    for pump in targets:
        nfm = load_neff(settings, settings.nominal_thickness_nm - h, pump, "_refined")
        nfp = load_neff(settings, settings.nominal_thickness_nm + h, pump, "_refined")
        nsm = load_neff(settings, settings.nominal_thickness_nm - h, pump / 2.0, "_refined")
        nsp = load_neff(settings, settings.nominal_thickness_nm + h, pump / 2.0, "_refined")
        refined = ((beta_per_um(nsp, pump / 2.0) - 2 * beta_per_um(nfp, pump)) -
                   (beta_per_um(nsm, pump / 2.0) - 2 * beta_per_um(nfm, pump))) / (2 * h)
        default = float(main.loc[pump, "d_delta_k_dt_per_um_per_nm"])
        difference = abs(float(refined) - default)
        tolerance = settings.thickness_absolute_tolerance if abs(default) < 1e-4 else settings.thickness_relative_tolerance * abs(default)
        rows.append({
            "fundamental_wavelength_nm": pump, "default_per_um_per_nm": default,
            "refined_per_um_per_nm": float(refined), "absolute_difference": difference,
            "tolerance": tolerance, "passed": difference <= tolerance,
        })
    frame = pd.DataFrame(rows)
    frame.insert(0, "core_material", settings.core_material.upper())
    frame.insert(1, "target_mode", settings.target_mode)
    frame.insert(2, "top_cladding", settings.top_cladding)
    frame.to_csv(settings.output_root / "data" / "audit" / "mesh_audit.csv", index=False)


def validate_outputs(settings: Settings) -> dict[str, Any]:
    required = [
        settings.output_root / "model" / model_filename(settings),
        settings.output_root / "data" / "sensitivity_results.csv",
        settings.output_root / "data" / "figures" / "thickness_phase_mismatch_sensitivity.png",
        settings.output_root / "data" / "figures" / "group_velocity_mismatch.png",
        settings.output_root / "data" / "figures" / "phase_matching_wavelength_sensitivity.png",
    ]
    missing = [str(path) for path in required if not path.exists()]
    results = pd.read_csv(settings.output_root / "data" / "sensitivity_results.csv") if not missing else pd.DataFrame()
    overlap_path = settings.output_root / "data" / "mode_overlap_checks.csv"
    overlap_ok = False
    overlap_min = float("nan")
    if overlap_path.exists():
        overlaps = pd.read_csv(overlap_path)
        overlap_min = float(overlaps[["overlap_Tminus_T0", "overlap_Tplus_T0"]].min().min())
        overlap_ok = overlap_min >= settings.field_overlap_min
    audit_files = [
        settings.output_root / "data" / "audit" / "step_and_group_audit.csv",
        settings.output_root / "data" / "audit" / "mesh_audit.csv",
    ]
    audit_status = {}
    for path in audit_files:
        if path.exists():
            frame = pd.read_csv(path)
            audit_status[path.name] = bool(frame["passed"].astype(bool).all())
        else:
            audit_status[path.name] = None
    metric_columns = ["d_delta_k_dt_per_um_per_nm", "gvm_fs_per_mm", "d_lambda_pm_dt_formula_nm_per_nm"]
    complete = not results.empty and np.isfinite(results[metric_columns].to_numpy()).all()
    report = {
        "passed": not missing and bool(complete) and len(results) == len(settings.fundamental_wavelengths_nm) and overlap_ok and
                  all(value is not False for value in audit_status.values()),
        "all_main_metrics_complete": bool(complete),
        "missing": missing,
        "result_rows": len(results),
        "expected_rows": len(settings.fundamental_wavelengths_nm),
        "minimum_cross_thickness_field_overlap": overlap_min,
        "field_overlap_passed": overlap_ok,
        "audit_status": audit_status,
        "checked": datetime.now().isoformat(),
    }
    (settings.output_root / "data" / "validation_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="main",
        choices=("main", "postprocess", "repair", "audit", "refined", "validate", "all"),
        help="Operation to run. If omitted (for example, VS Code Run button), defaults to 'main'.",
    )
    parser.add_argument("--force", action="store_true", help="Recompute completed COMSOL points.")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_cli_args()
    settings = Settings()
    settings.validate()
    print(
        f"Configuration: {settings.core_material.upper()} / {settings.target_mode} / "
        f"{settings.top_cladding}; {settings.fundamental_wavelengths_nm[0]:g}-"
        f"{settings.fundamental_wavelengths_nm[-1]:g} nm, "
        f"{len(settings.fundamental_wavelengths_nm)} points",
        flush=True,
    )
    print(f"Results directory: {settings.output_root}", flush=True)
    if args.command in {"main", "all"}:
        run_main(settings, force=args.force)
        postprocess(settings)
    if args.command == "postprocess":
        postprocess(settings)
    if args.command == "repair":
        repair_missing_points(settings)
    if args.command in {"audit", "all"}:
        run_step_and_group_audit(settings, force=args.force)
    if args.command in {"refined", "all"}:
        run_refined_audit(settings, force=args.force)
    if args.command in {"validate", "all"}:
        report = validate_outputs(settings)
        print(json.dumps(report, indent=2))
        if not report["passed"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
