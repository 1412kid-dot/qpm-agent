"""Versioned input contract; all defaults are serialized into every run."""
from dataclasses import dataclass, field, asdict, fields
import math
from .crystal import default_mode, mapping

AXES = ("thickness_nm", "etch_depth_nm", "top_width_nm", "sidewall_angle_deg")

def finite(value, name):
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")

def grid(start, stop, step):
    for x in (start, stop, step): finite(x, "grid")
    if step <= 0 or stop < start: raise ValueError("invalid grid")
    count = int(math.floor((stop - start) / step + 1e-9))
    return [round(start + i * step, 9) for i in range(count + 1)]

@dataclass
class Geometry:
    thickness_nm: float
    etch_depth_nm: float
    top_width_nm: float
    sidewall_angle_deg: float = 67.0

    def validate(self):
        for key, value in asdict(self).items(): finite(value, key)
        if self.thickness_nm <= 0 or self.top_width_nm <= 0 or not 0 < self.etch_depth_nm < self.thickness_nm:
            raise ValueError("rib requires positive width and 0 < etch < thickness (nonzero slab)")
        if not 0 < self.sidewall_angle_deg < 90: raise ValueError("angle measured from horizontal must be 0..90 exclusive")
        return self

@dataclass
class Task:
    material: str
    crystal_cut: str
    cladding: str
    geometry: Geometry
    process: str = "SHG"
    center_nm: float = 1550.0
    wavelengths_nm: list = field(default_factory=list)
    wavelength_range_nm: list = field(default_factory=list)
    wavelength_step_nm: float = 20.0
    modes: dict = field(default_factory=dict)
    length_mm: float = 5.0
    length_defaulted: bool = True
    nonlinear_coefficient_pm_v: float | None = None
    backend: str = "demo"
    task_kind: str = "fixed"
    search_axes: dict = field(default_factory=dict)
    objective: dict = field(default_factory=lambda: {"metric": "gvd", "wavelength_nm": 1550.0, "tolerance": None})
    tolerances_nm: dict = field(default_factory=dict)
    thickness_derivative_nm: float = 2.0
    fw_derivative_nm: float = 2.0
    sh_derivative_nm: float = 1.0
    dispersion_step_nm: float = 2.0
    dispersion_stencil_points: int = 3
    adaptive_dispersion: bool = True
    dispersion_absolute_tolerance_fs2_mm: float = 1.0
    dispersion_relative_tolerance: float = 0.05
    dispersion_refinement_wavelengths_nm: list = field(default_factory=list)
    dispersion_roles: list = field(default_factory=list)
    verify_numerics: bool = False
    thickness_etch_relation: str = "fixed_etch"
    wavelength_axes_nm: dict = field(default_factory=dict)
    spectral_solve_step_nm: float = 2.0
    budget: dict = field(default_factory=dict)
    full_scan: bool = False
    max_additional_geometries: int = 16
    batch_size: int = 4
    solver: dict = field(default_factory=dict)
    replay_dir: str | None = None
    replay_source_backend: str = 'comsol'
    assumptions: list = field(default_factory=list)
    schema_version: str = "0.1"
    propagation_axis: str | None = None
    required_metrics: list = field(default_factory=list)
    provided_fields: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload):
        payload = dict(payload)
        payload.setdefault("provided_fields", sorted(payload))
        allowed = {f.name for f in fields(cls)}
        extra = set(payload) - allowed
        if extra: raise ValueError(f"unknown task fields: {sorted(extra)}")
        payload["geometry"] = Geometry(**payload["geometry"])
        payload["length_defaulted"] = payload.get("length_defaulted", "length_mm" not in payload)
        if "objective" not in payload:
            payload["objective"] = {"metric": "gvd", "wavelength_nm": payload.get("center_nm", 1550.0), "tolerance": None}
        return cls(**payload).validate()

    def validate(self):
        self.geometry.validate()
        if not isinstance(self.adaptive_dispersion,bool): raise ValueError("adaptive_dispersion must be boolean")
        for v in (self.dispersion_absolute_tolerance_fs2_mm,self.dispersion_relative_tolerance):
            if not isinstance(v,(int,float)) or not math.isfinite(v) or v<0: raise ValueError("invalid dispersion tolerance")
        if self.dispersion_stencil_points not in (3,5,7): raise ValueError('dispersion_stencil_points must be 3, 5 or 7')
        if not isinstance(self.verify_numerics,bool): raise ValueError('verify_numerics must be boolean')
        if not isinstance(self.dispersion_roles,list) or any(r not in ('pump','signal','idler') for r in self.dispersion_roles): raise ValueError('invalid dispersion_roles')
        if not isinstance(self.dispersion_refinement_wavelengths_nm,list) or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in self.dispersion_refinement_wavelengths_nm): raise ValueError('invalid refinement wavelengths')
        if self.material not in {"LN", "LT"} or self.cladding not in {"AIR", "SIO2"}: raise ValueError("material LN/LT; cladding AIR/SIO2")
        mapping(self.crystal_cut)
        expected_axis = mapping(self.crystal_cut)['lab_z']
        if self.propagation_axis is None: self.propagation_axis = expected_axis
        if self.propagation_axis != expected_axis:
            raise ValueError(f"unsupported propagation_axis: {self.crystal_cut}-cut currently propagates along crystal {expected_axis}; new solver physics requires separate development")
        from .conventions import METRICS
        if not isinstance(self.required_metrics,list) or any(k not in METRICS for k in self.required_metrics):
            raise ValueError('unsupported required_metrics; query qpm_conventions metrics or prepare a reviewed postprocess extension')
        if self.process not in {"SHG", "SFG", "DFG", "SPDC"}: raise ValueError("unsupported process")
        if self.backend not in {"demo", "comsol", "replay"}: raise ValueError("backend demo/comsol/replay")
        if self.replay_source_backend not in {'demo','comsol'}: raise ValueError('invalid replay source')
        if self.backend=='replay' and not self.replay_dir: raise ValueError('replay requires replay_dir pointing to cache')
        if self.task_kind not in {"fixed", "search", "spectral_2d"}: raise ValueError("task_kind fixed/search/spectral_2d")
        if self.thickness_etch_relation not in {"fixed_etch", "fixed_slab"}: raise ValueError("unknown thickness_etch_relation")
        for name in ("center_nm", "length_mm", "wavelength_step_nm", "fw_derivative_nm", "sh_derivative_nm", "thickness_derivative_nm", "dispersion_step_nm", "spectral_solve_step_nm"):
            finite(getattr(self, name), name)
            if getattr(self, name) <= 0: raise ValueError(f"{name} must be positive")
        length_note=f"length defaulted to {self.length_mm:g} mm"
        if self.length_defaulted and length_note not in self.assumptions:
            self.assumptions.append(length_note)
        if self.nonlinear_coefficient_pm_v is not None: finite(self.nonlinear_coefficient_pm_v, "d_eff")
        for role in ("pump", "signal", "idler"):
            self.modes.setdefault(role, default_mode(self.crystal_cut))
        if set(self.modes)-{'pump','signal','idler'}: raise ValueError('mode roles pump/signal/idler only')
        if any(x not in {"TE0", "TM0"} for x in self.modes.values()): raise ValueError("mode must be TE0/TM0")
        for key, value in self.tolerances_nm.items():
            if key not in {"thickness_nm", "top_width_nm"}: raise ValueError("tolerances support thickness_nm/top_width_nm")
            finite(value, key)
            if value < 0: raise ValueError("negative tolerance")
        if self.objective.get("metric") not in {"gvd", "gvm", "thickness_sensitivity"}: raise ValueError("unknown objective metric")
        if set(self.objective)-{'metric','wavelength_nm','tolerance','role','reduction','verification_abs_tolerance'}: raise ValueError('unknown objective fields')
        if set(self.wavelength_axes_nm)-{'signal_nm','idler_nm','fixed_idler_nm'}: raise ValueError('unknown wavelength axes; fixed-pump 2D SPDC is not implemented')
        if self.objective.get("reduction", "at_wavelength") not in {"at_wavelength", "zero_in_band", "max_abs_over_band"}: raise ValueError("unknown reduction")
        if self.objective.get("role", "signal") not in self.modes: raise ValueError("invalid objective role")
        if self.objective['metric']!='gvd' and self.objective.get('role','signal')!='signal': raise ValueError('role selection applies to GVD only; existing GVM is pump-signal, thickness sensitivity is full phase mismatch')
        if self.objective.get("tolerance") is not None:
            finite(self.objective["tolerance"], "tolerance")
            if self.objective["tolerance"] < 0: raise ValueError("negative target tolerance")
        for key, limits in self.search_axes.items():
            if key not in AXES: raise ValueError(f"unsupported search axis {key}")
            if limits is None:
                nominal = getattr(self.geometry, key)
                self.search_axes[key] = {"min": nominal * .9, "max": nominal * 1.1}
                self.assumptions.append(f"{key}: exploratory nominal +/-10%, not a manufacturing guarantee")
            else:
                if set(limits) - {"min", "max", "step"}: raise ValueError("axis fields min/max/step only")
                finite(limits["min"], key); finite(limits["max"], key)
                if limits["max"] < limits["min"]: raise ValueError("reversed geometry bounds")
                if "step" in limits:
                    finite(limits['step'],key)
                    if limits["step"] <= 0: raise ValueError("step must be positive")
        if self.task_kind == "search" and not self.search_axes: raise ValueError("search requires axes; use null bounds for +/-10%")
        if not 1 <= self.batch_size <= 4 or not 0 <= self.max_additional_geometries: raise ValueError("batch 1..4, additional nonnegative; executor enforces geometry budget")
        for key, value in self.budget.items():
            if key not in {"max_geometry_count", "max_solver_calls", "max_wall_time_s"}: raise ValueError(f"unknown budget {key}")
            finite(value, key)
            if value < 0: raise ValueError("negative budget")
        self.wavelengths()
        permitted_solver={'version','comsol_bin','threads','model_cache_size','core_max_element_nm','sidewall_max_element_nm','background_max_element_nm',
            'refined_core_max_element_nm','refined_sidewall_max_element_nm','refined_background_max_element_nm',
            'pml_layers','refined_pml_layers','pml_thickness_um','field_dx_nm','window_width_um','window_height_um','window_center_y_um'}
        if set(self.solver)-permitted_solver: raise ValueError(f'unsupported solver settings: {sorted(set(self.solver)-permitted_solver)}')
        for k,v in self.solver.items():
            if k in {'version','comsol_bin'}: continue
            finite(v,k)
            if k!='window_center_y_um' and v<=0: raise ValueError('solver sizes/counts must be positive')
            if k in {'threads','model_cache_size','pml_layers','refined_pml_layers'} and int(v)!=v: raise ValueError('solver counts must be integers')
        finite(self.objective.get('wavelength_nm',self.center_nm),'objective wavelength')
        if self.objective.get('wavelength_nm',self.center_nm)<=0: raise ValueError('invalid objective wavelength')
        if any(w<=2*self.dispersion_step_nm for w in self.wavelengths()): raise ValueError('derivative stencil crosses zero wavelength')
        if self.process in {'SFG','DFG'} and self.task_kind!='spectral_2d':
            other=self.wavelength_axes_nm.get('fixed_idler_nm')
            if other is None or not math.isfinite(other) or other<=0: raise ValueError('fixed_idler_nm required for SFG/DFG curves')
        if self.task_kind=='spectral_2d':
            if self.process not in {'SFG','SPDC'}: raise ValueError('spectral_2d supports SFG/SPDC')
            for name in ('signal_nm','idler_nm'):
                values=self.wavelength_axes_nm.get(name,[])
                if len(values)<2 or any(not math.isfinite(v) or v<=0 for v in values) or any(b<=a for a,b in zip(values,values[1:])): raise ValueError('spectral axes need 2+ positive increasing values')
        from .physics import waves
        from .materials import refractive_indices
        if self.task_kind=='spectral_2d':
            x=self.wavelength_axes_nm['signal_nm']; y=self.wavelength_axes_nm['idler_nm']
            points=[v for a in (x[0],x[-1]) for b in (y[0],y[-1]) for v in (a,b,1/(1/a+1/b))]
        else:
            points=[]
            for axis in self.wavelengths()+[self.objective.get('wavelength_nm',self.center_nm)]:
                for wavelength in waves(self,axis):
                    points.extend([wavelength-2*self.dispersion_step_nm,wavelength+2*self.dispersion_step_nm])
        for wavelength in points: refractive_indices(self.material,wavelength)
        for name in ('batch_size','max_additional_geometries'):
            if int(getattr(self,name))!=getattr(self,name): raise ValueError('counts must be integers')
        if self.objective.get('verification_abs_tolerance') is not None:
            finite(self.objective['verification_abs_tolerance'],'verification tolerance')
            if self.objective['verification_abs_tolerance']<0: raise ValueError('negative verification tolerance')
        from .conventions import metric_availability
        unavailable=metric_availability(self)['unavailable']
        missing=[k for k in self.required_metrics if k in unavailable]
        if missing: raise ValueError('required metrics unavailable: '+str({k:unavailable[k] for k in missing}))
        return self

    def wavelengths(self):
        if self.wavelength_range_nm and len(self.wavelength_range_nm)!=2: raise ValueError('wavelength range needs min/max')
        values = self.wavelengths_nm or grid(*(self.wavelength_range_nm or [self.center_nm - 50, self.center_nm + 50]), self.wavelength_step_nm)
        # Explicit user display arrays remain exact; default/range scans include center.
        result = sorted(set(float(v) for v in (values if self.wavelengths_nm else [*values,self.center_nm])))
        if any(not math.isfinite(v) or v <= 0 for v in result): raise ValueError("wavelengths must be positive finite")
        return result

    def to_dict(self): return asdict(self)

def as_task(value):
    return Task.from_dict(value) if isinstance(value, dict) else value.validate()
