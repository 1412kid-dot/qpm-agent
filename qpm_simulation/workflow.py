from pathlib import Path
from dataclasses import asdict,replace
from datetime import datetime
import numpy as np
from .config import as_task,Geometry
from .backends import create_backend,BudgetExceeded
from .metrics import curve,tolerance_curves,objective_value,mismatch,safe_mode
from .physics import qpm_period,pmf,waves
from .reporting import save_result,write_json

def plan_task(payload):
    t=as_task(payload)
    from .conventions import interpret_task
    from .planning import estimate_task
    return {'sampling':estimate_task(t),'task':t.to_dict(),'interpretation':interpret_task(t),'solver_started':False,'display_wavelengths_nm':t.wavelengths(),
        'steps':['validate','target-only initial coverage and adaptive selection' if t.task_kind=='search' else 'solve requested wavelengths',
                 'candidate measured validation' if t.task_kind=='search' else 'compute metrics','nominal and real tolerance endpoints','export tables and curves'],
        'derivatives':{'gvd_step_nm':t.dispersion_step_nm,'gvd_local_points':t.dispersion_stencil_points,'five_point_wavelengths_nm':t.dispersion_refinement_wavelengths_nm,'gvm_low_offset_nm':t.fw_derivative_nm,'gvm_high_offset_nm':t.sh_derivative_nm,'thickness_offset_nm':t.thickness_derivative_nm},
        'efficiency':'lossless per-wavelength matching; tolerance efficiency uses nominal grating momentum',
        'search_additional_limit':t.max_additional_geometries,'warning':'demo data are synthetic; COMSOL validation is a separate explicit operation'}

def spectral(task,backend):
    a=task.wavelength_axes_nm
    if task.process not in {'SFG','SPDC'}: raise ValueError('2D supports SFG or SPDC')
    x=a.get('signal_nm'); y=a.get('idler_nm')
    if not x or not y: raise ValueError('2D requires explicit signal_nm and idler_nm arrays')
    nominal=mismatch(task,backend,task.geometry,waves(task,task.center_nm)) if task.process=='SPDC' else None
    rows=[]; values=[]
    for idler in y:
        line=[]
        for signal in x:
            pump=1/(1/signal+1/idler)
            delta=mismatch(task,backend,task.geometry,[pump,signal,idler])
            value=float(pmf(delta-nominal,task.length_mm)) if task.process=='SPDC' else qpm_period(delta)
            line.append(value); rows.append({'signal_nm':signal,'idler_nm':idler,'pump_nm':pump,'value':value})
        values.append(line)
    return {'x_nm':x,'y_nm':y,'x_label':'Signal wavelength (nm)','y_label':'Idler wavelength (nm)',
        'metric':'PMF (dimensionless, varying pump follows energy conservation)' if task.process=='SPDC' else 'QPM period (um)',
        'values':values,'rows':rows,'nominal_grating_delta_beta_m_inv':nominal}

def verify_candidate(task,backend,g):
    base=objective_value(task,backend,g)
    if not task.verify_numerics:
        return {'nominal_target':base,'refined_mesh_target':None,'half_derivative_step_target':None,
                'mesh_field_overlap':None,'adjacent_field_overlap_min':None,'numerically_stable':False,
                'numerical_verification':'not requested; convergence unverified',
                'physical_validation':'pending real COMSOL science acceptance'}
    refined=objective_value(task,backend,g,mesh='refined') if task.verify_numerics else None
    if task.verify_numerics:
        if task.objective['metric']=='gvd': finer=objective_value(task,backend,g,step=task.dispersion_step_nm/2)
        else:
            half=replace(task,fw_derivative_nm=task.fw_derivative_nm/2,sh_derivative_nm=task.sh_derivative_nm/2,thickness_derivative_nm=task.thickness_derivative_nm/2)
            finer=objective_value(half,backend,g)
    else: finer=None
    role=task.objective.get('role','signal')
    wl=waves(task,task.objective.get('wavelength_nm',task.center_nm))[('pump','signal','idler').index(role)]; mode=task.modes[role]
    a=safe_mode(backend,g,mode,wl,'default'); b=safe_mode(backend,g,mode,wl,'refined') if task.verify_numerics else None
    overlap=np.nan
    if a and b and a['field'].shape==b['field'].shape and np.array_equal(a['x_m'],b['x_m']) and np.array_equal(a['y_m'],b['y_m']):
        from dispersion_core import normalized_field_overlap
        overlap=normalized_field_overlap(a['field'],b['field'])
    from dispersion_core import normalized_field_overlap
    continuity=[]
    wave_centers=waves(task,task.objective.get('wavelength_nm',task.center_nm))
    roles=[role] if task.objective['metric']=='gvd' else ['pump','signal','idler']
    for wave_role in roles:
        center=wave_centers[('pump','signal','idler').index(wave_role)]
        fields=[safe_mode(backend,g,task.modes[wave_role],center+k*task.dispersion_step_nm,'default') for k in range(-(task.dispersion_stencil_points//2),task.dispersion_stencil_points//2+1)]
        for left,right in zip(fields,fields[1:]):
            valid=left and right and np.array_equal(left['x_m'],right['x_m']) and np.array_equal(left['y_m'],right['y_m'])
            continuity.append(normalized_field_overlap(left['field'],right['field']) if valid else np.nan)
    continuity_min=float(np.min(continuity))
    limit=max(abs(base)*.05, task.objective.get('verification_abs_tolerance',1.0 if task.objective['metric']=='gvd' else 1e-3))
    stable=bool(task.verify_numerics and np.isfinite([base,refined,overlap,continuity_min]).all() and abs(refined-base)<=limit and overlap>=.90 and continuity_min>=.90 and (finer is None or abs(finer-base)<=limit))
    return {'nominal_target':base,'refined_mesh_target':refined,'half_derivative_step_target':finer,'mesh_field_overlap':overlap,
        'adjacent_field_overlap_min':continuity_min,'numerically_stable':stable,'numerical_verification':'performed' if task.verify_numerics else 'not requested; convergence unverified','comparison_tolerance':limit,'physical_validation':'pending original x-cut regression and representative z-cut COMSOL runs'}

def run_task(payload,output=None,backend=None):
    t=as_task(payload); root=(Path(output) if output else Path(__file__).resolve().parents[1]/'outputs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')).resolve()
    root.mkdir(parents=True,exist_ok=True)
    if (root/'task.json').exists():
        import json
        previous=as_task(json.loads((root/'task.json').read_text(encoding='utf-8'))).to_dict()
        ignored={'budget','provided_fields','assumptions','schema_version','length_defaulted'}
        if {k:v for k,v in previous.items() if k not in ignored}!={k:v for k,v in t.to_dict().items() if k not in ignored}: raise ValueError('resume task changed; create a new run for changed physics or sampling')
    write_json(root/'task.json',t.to_dict()); write_json(root/'plan.json',plan_task(t))
    b=backend or create_backend(t,root)
    result={'task':t.to_dict(),'validation':{'data_kind':'SYNTHETIC DEMO' if t.backend=='demo' else 'COMSOL' if t.backend=='comsol' else 'VALIDATED CACHE REPLAY',
         'status':'implemented; real COMSOL acceptance pending','scientifically_validated':False},'failures':[]}
    try:
        g=t.geometry
        if t.task_kind=='search':
            from .optimization import search_geometry
            result['search']=search_geometry(t,b)
            if result['search']['best'] is None: raise ValueError('no valid measured candidate')
            g=Geometry(**result['search']['best']['geometry']); result['best_geometry']=asdict(g)
            result['validation']['candidate']=verify_candidate(t,b,g)
            verification=result['validation']['candidate']; tolerance=t.objective.get('tolerance')
            verified_values=[verification['nominal_target'],verification['refined_mesh_target'],verification['half_derivative_step_target']]
            result['search']['target_verified_within_tolerance']=bool(tolerance is not None and verification['numerically_stable'] and all(abs(v)<=tolerance for v in verified_values))
        if t.task_kind=='spectral_2d': result['spectral']=spectral(t,b)
        else:
            from .planning import sampling_points
            for geom,mode,wl,mesh in sampling_points(t,g): safe_mode(b,geom,mode,wl,mesh)
            result['curves']=curve(t,b,g); result['tolerances']=tolerance_curves(t,b,g,result['curves'])
    except (BudgetExceeded,ValueError) as exc:
        result['failures'].append(str(exc)); result['validation']['status']='incomplete: '+str(exc)
    finally:
        result['failures']+=b.failures; result['cost']=b.summary(); b.checkpoint('postprocessing')
        if backend is None: b.close()
    if result['failures']: result['validation']['status']='incomplete; inspect failures'
    return save_result(result,root)

def search_task(payload,output=None,backend=None):
    t=as_task(payload); t.task_kind='search'; t.validate(); return run_task(t,output,backend)
