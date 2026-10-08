from dataclasses import replace
import numpy as np
from .physics import beta,local_dispersion,group_index,gvm,waves,qpm_period,pmf,effective_area,normalized_efficiency
from .crystal import nonlinear_coefficient
from .backends import BudgetExceeded

ROLES=('pump','signal','idler')

def safe_mode(backend,g,mode,wl,mesh):
    try: return backend.solve(g,mode,wl,mesh)
    except BudgetExceeded: raise
    except Exception: return None

def sample(backend,g,mode,wl,mesh='default'):
    r=safe_mode(backend,g,mode,wl,mesh)
    return float(r['neff']) if r else np.nan

def dispersion(task,backend,g,role,wl,mesh='default',step=None,axis=None):
    from .planning import stencil_count
    axis=axis if axis is not None else (wl*2 if role=='pump' and task.process=='SHG' else wl)
    count=stencil_count(task,axis)
    w=wl+np.arange(-(count//2),count//2+1)*(step or task.dispersion_step_nm)
    n=[sample(backend,g,task.modes[role],x,mesh) for x in w]
    # One delayed pass: failed points remain NaN if validated anchors still cannot rescue them.
    for i in np.flatnonzero(~np.isfinite(n)): n[i]=sample(backend,g,task.modes[role],w[i],mesh)
    return local_dispersion(w,n,wl)

def mismatch(task,backend,g,wavelengths,mesh='default'):
    n=[sample(backend,g,task.modes[r],w,mesh) for r,w in zip(ROLES,wavelengths)]
    b=beta(n,wavelengths); return float(b[0]-b[1]-b[2])

def shifted_geometry(task,g,axis,delta):
    changes={axis:getattr(g,axis)+delta}
    if axis=='thickness_nm' and task.thickness_etch_relation=='fixed_slab': changes['etch_depth_nm']=g.etch_depth_nm+delta
    return replace(g,**changes).validate()

def objective_value(task,backend,g,mesh='default',step=None):
    obj=task.objective; metric=obj['metric']; role=obj.get('role','signal')
    points=task.wavelengths() if obj.get('reduction','at_wavelength')!='at_wavelength' else [obj.get('wavelength_nm',task.center_nm)]
    values=[]
    for axis in points:
        w=waves(task,axis)
        if metric=='gvd': value=dispersion(task,backend,g,role,w[ROLES.index(role)],mesh,step,axis=axis)['gvd_fs2_mm']
        elif metric=='gvm': value=group_mismatch(task,backend,g,w,mesh)[0]
        else:
            h=task.thickness_derivative_nm
            a=mismatch(task,backend,shifted_geometry(task,g,'thickness_nm',-h),w,mesh)
            b=mismatch(task,backend,shifted_geometry(task,g,'thickness_nm',h),w,mesh)
            value=(b-a)/(2*h) # derivative of signed phase mismatch, m^-1/nm
        values.append(value)
    if not np.isfinite(values).all(): return np.nan
    reduction=obj.get('reduction','at_wavelength')
    if reduction=='max_abs_over_band': return max(abs(np.asarray(values)))
    if reduction=='zero_in_band': return values[int(np.argmin(abs(np.asarray(values))))]
    return values[0]

def group_mismatch(task,backend,g,w,mesh='default'):
    ng=[]
    for i in (0,1):
        h=task.sh_derivative_nm if i==0 else task.fw_derivative_nm
        role=ROLES[i]; mode=task.modes[role]; center=w[i]
        points=[center,center-h,center+h]; n=[sample(backend,g,mode,x,mesh) for x in points]
        for j in np.flatnonzero(~np.isfinite(n)): n[j]=sample(backend,g,mode,points[j],mesh)
        ng.append(group_index(center,*n,h))
    return gvm(*ng)

def curve(task,backend,g,nominal=None,mesh='default'):
    from .planning import requested_metrics, dispersion_roles
    requested=requested_metrics(task); roles=dispersion_roles(task)
    rows=[]; d,model=nonlinear_coefficient(task.material,task.crystal_cut,list(task.modes.values()),task.nonlinear_coefficient_pm_v)
    if task.process=='SHG' and task.modes['signal']!=task.modes['idler']: d=None; model='mixed_degenerate_modes_need_tensor_model'
    for j,axis in enumerate(task.wavelengths()):
        w=waves(task,axis)
        dispersions={role:dispersion(task,backend,g,role,wl,mesh,axis=axis) for role,wl in zip(ROLES,w) if role in roles}
        need_phase=bool(requested & {'period','normalized_efficiency','process_coefficient'})
        records=[safe_mode(backend,g,task.modes[r],wl,mesh) if need_phase else None for r,wl in zip(ROLES,w)]
        n=[float(r['neff']) if r else np.nan for r in records]; b=beta(n,w); delta=float(b[0]-b[1]-b[2])
        period=qpm_period(delta); coefficient=np.nan; area=np.nan
        if requested & {'normalized_efficiency','process_coefficient'} and d is not None and all(r is not None for r in records):
            area=effective_area(task.process,records); coefficient=normalized_efficiency(task.process,w,n,area,d)
        if nominal is not None:
            # Signed grating momentum of the nominal structure at this wavelength.
            coefficient*=pmf(delta-nominal[j]['delta_beta_m_inv'],task.length_mm)
        row={'wavelength_nm':axis,'period_um':period,'delta_beta_m_inv':delta,'effective_area_um2':area*1e12,
             'normalized_efficiency_W_inv_m_inv2':float(coefficient),'process_coefficient_W_inv':float(coefficient*(task.length_mm*1e-3)**2),
             'length_mm':task.length_mm,'nonlinear_model':model,'valid':bool(np.isfinite(delta)) if need_phase else all(np.isfinite(v['gvd_fs2_mm']) for v in dispersions.values())}
        row['efficiency_status']='SPDC absolute efficiency not modeled' if task.process=='SPDC' else 'available' if d is not None else 'nonlinear model required'
        row['gvm_signed_ps_m'],row['gvm_abs_ps_m']=group_mismatch(task,backend,g,w,mesh) if 'gvm' in requested else (np.nan,np.nan)
        for role,wl in zip(ROLES,w):
            row[f'{role}_wavelength_nm']=wl
            for name,value in dispersions.get(role,{'beta1_ps_mm':np.nan,'gvd_fs2_mm':np.nan,'D_ps_nm_km':np.nan,'fit_error_fs2_mm':np.nan}).items(): row[f'{role}_{name}']=value
        if task.objective['metric']=='thickness_sensitivity' or 'thickness_sensitivity' in task.required_metrics:
            h=task.thickness_derivative_nm
            minus=mismatch(task,backend,shifted_geometry(task,g,'thickness_nm',-h),w,mesh)
            plus=mismatch(task,backend,shifted_geometry(task,g,'thickness_nm',h),w,mesh)
            row['thickness_sensitivity_m_inv_nm_inv']=(plus-minus)/(2*h)
        rows.append(row)
    if task.adaptive_dispersion and task.dispersion_stencil_points==3:
        adaptive_curve(task,backend,g,rows,roles,mesh)
    return rows

def tolerance_curves(task,backend,g,nominal):
    output={}
    for axis,tolerance in task.tolerances_nm.items():
        for sign in (-1,1):
            label=f'{axis}_{sign*tolerance:+g}'
            try: output[label]=curve(task,backend,shifted_geometry(task,g,axis,sign*tolerance),nominal)
            except ValueError as exc: output[label]={'failure':str(exc)}
    return output


def adaptive_curve(task,backend,g,rows,roles,mesh):
    """Only isolated curvature anomalies with valid neighbours; never trigger on a zero alone."""
    import math
    from dataclasses import asdict
    for role in roles:
        key=f'{role}_gvd_fs2_mm'
        limit=max(1,math.ceil(len(rows)*.1)); upgraded=0
        for i in range(1,len(rows)-1):
            a,v,b=[rows[j].get(key,np.nan) for j in (i-1,i,i+1)]
            if not np.isfinite([a,v,b]).all(): continue
            threshold=max(task.dispersion_absolute_tolerance_fs2_mm,task.dispersion_relative_tolerance*max(abs(a),abs(v),abs(b)))
            # Large isolated deviation, not smooth extrema or a mere sign change.
            if abs(v-(a+b)/2)<=max(8*threshold,2*abs(b-a)): continue
            if upgraded>=limit: break
            center=rows[i][f'{role}_wavelength_nm']; h=task.dispersion_step_nm
            quality={'order':3,'reason':'isolated_neighbor_anomaly','status':'low_confidence','added_logical_points':0}
            previous=v
            # Reuse the already solved three-point fields; no added logical wavelengths.
            from dispersion_core import normalized_field_overlap
            anchors=[safe_mode(backend,g,task.modes[role],center+k*h,mesh) for k in (-1,0,1)]
            overlaps=[]
            for left,right in zip(anchors,anchors[1:]):
                valid=left is not None and right is not None and np.array_equal(left['x_m'],right['x_m']) and np.array_equal(left['y_m'],right['y_m'])
                overlaps.append(normalized_field_overlap(left['field'],right['field']) if valid else np.nan)
            if not np.isfinite(overlaps).all() or min(overlaps)<.90:
                quality.update(status='mode_continuity_requires_review',field_overlap_min=float(np.min(overlaps)))
                rows[i][f'{role}_dispersion_quality']=quality
                continue
            for count in (5,7):
                ww=center+np.arange(-(count//2),count//2+1)*h
                try: nn=[sample(backend,g,task.modes[role],x,mesh) for x in ww]
                except BudgetExceeded:
                    quality['status']='budget_limited'; break
                quality['added_logical_points']=count-3
                estimate=local_dispersion(ww,nn,center)
                if not np.isfinite(estimate['gvd_fs2_mm']): quality['status']='outer_point_failed'; break
                delta=abs(estimate['gvd_fs2_mm']-previous)
                tolerance=max(task.dispersion_absolute_tolerance_fs2_mm,task.dispersion_relative_tolerance*max(abs(previous),abs(estimate['gvd_fs2_mm'])))
                for name,value in estimate.items(): rows[i][f'{role}_{name}']=value
                quality.update(order=count,order_difference_fs2_mm=delta,status='stable' if delta<=tolerance else 'low_confidence')
                previous=estimate['gvd_fs2_mm']
                if delta<=tolerance: break
            rows[i][f'{role}_dispersion_quality']=quality; upgraded+=1
