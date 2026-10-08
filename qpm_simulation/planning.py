"""Pure sampling and cost planning. Never imports a solver."""
from dataclasses import asdict
from pathlib import Path
import json
import numpy as np
from .physics import waves

DEFAULT_METRICS={'period','normalized_efficiency','beta1','gvd','D','gvm'}

def requested_metrics(t): return set(t.required_metrics) or DEFAULT_METRICS

def dispersion_roles(t):
    if not requested_metrics(t)&{'beta1','gvd','D'}: return []
    return t.dispersion_roles or ([t.objective.get('role','signal')] if set(t.required_metrics)=={'gvd'} else ['pump','signal','idler'])

def stencil_count(t,axis):
    return 5 if any(abs(axis-v)<1e-8 for v in t.dispersion_refinement_wavelengths_nm) else t.dispersion_stencil_points

def sampling_points(t,g=None,objective_only=False):
    from .metrics import shifted_geometry
    g=g or t.geometry; points={}; roles=('pump','signal','idler')
    def add(geom,role,wl):
        key=(tuple(asdict(geom).values()),t.modes[role],round(float(wl),9),'default')
        points[key]=(geom,t.modes[role],float(wl),'default')
    requested=requested_metrics(t)
    axes=t.wavelengths()
    if objective_only:
        requested={t.objective['metric']}
        if t.objective.get('reduction','at_wavelength')=='at_wavelength': axes=[t.objective.get('wavelength_nm',t.center_nm)]
    for axis in axes:
        ww=waves(t,axis)
        selected=[t.objective.get('role','signal')] if objective_only and 'gvd' in requested else dispersion_roles(t)
        if requested&{'gvd','beta1','D'}:
            for role in selected:
                radius=stencil_count(t,axis)//2; center=ww[roles.index(role)]
                for k in range(-radius,radius+1): add(g,role,center+k*t.dispersion_step_nm)
        if requested&{'period','normalized_efficiency','process_coefficient'}:
            for role,wl in zip(roles,ww): add(g,role,wl)
        if 'gvm' in requested:
            for i,h in ((0,t.sh_derivative_nm),(1,t.fw_derivative_nm)):
                for offset in (-h,0,h): add(g,roles[i],ww[i]+offset)
        if 'thickness_sensitivity' in requested:
            for sign in (-1,1):
                changed=shifted_geometry(t,g,'thickness_nm',sign*t.thickness_derivative_nm)
                for role,wl in zip(roles,ww): add(changed,role,wl)
    return sorted(points.values(),key=lambda p:(tuple(asdict(p[0]).values()),p[1],p[2],p[3]))

def estimate_task(t,workspace=None):
    from .config import as_task
    t=as_task(t); unknown=[]; points=[]; multiplier=1
    if t.task_kind=='spectral_2d':
        a=t.wavelength_axes_nm; seen=set()
        for signal in a['signal_nm']:
            for idler in a['idler_nm']:
                for role,wl in zip(('pump','signal','idler'),(1/(1/signal+1/idler),signal,idler)):
                    seen.add((t.modes[role],round(wl,9)))
        count=len(seen); unknown.append('SPDC nominal grating may require extra center points')
    else:
        points=sampling_points(t,objective_only=t.task_kind=='search'); count=len(points)
        if t.task_kind=='search':
            from .optimization import axis_values
            from itertools import product
            from dataclasses import replace
            names=list(t.search_axes); n=3 if len(names)>=3 else 4
            initial=0
            for values in product(*(axis_values(t.search_axes[k],n)[0] for k in names)):
                try: replace(t.geometry,**dict(zip(names,values))).validate(); initial+=1
                except ValueError: pass
            multiplier=min(t.budget.get('max_geometry_count',float('inf')),initial+t.max_additional_geometries)
            unknown.append('search count is an upper bound; best-candidate curves are additional')
        else:
            from .metrics import shifted_geometry
            for axis,delta in t.tolerances_nm.items():
                for sign in (-1,1): points+=sampling_points(t,shifted_geometry(t,t.geometry,axis,sign*delta))
            count=len(points)
    cached=0; timings=[]
    if workspace:
        from .backends import Backend
        probe=Backend(t,Path(workspace)/'.estimate',persist=False)
        for geom,mode,wl,mesh in points:
            if probe.cached_result(probe.identity(geom,mode,wl,mesh)) is not None: cached+=1
        for run in sorted(Path(workspace).iterdir()) if Path(workspace).exists() else []:
            if not run.is_dir() or not (run/'task.json').is_file(): continue
            try:
                task=json.loads((run/'task.json').read_text(encoding='utf-8'))
                if any(task.get(k)!=t.to_dict().get(k) for k in ('backend','material','crystal_cut','cladding','modes','solver')): continue
                cost=json.loads((run/'checkpoint.json').read_text(encoding='utf-8'))['cost'] if (run/'checkpoint.json').exists() else json.loads((run/'result.json').read_text(encoding='utf-8'))['cost']
                if cost.get('solver_calls',0)>0: timings.append(cost['elapsed_s']/cost['solver_calls'])
            except (OSError,ValueError,KeyError,TypeError): continue
    remaining=max(0,int(count*multiplier)-cached)
    # Per logical point can require multiple eigenvalue attempts; include observed retry factor.
    factors=[]
    if workspace and Path(workspace).exists():
        for run in Path(workspace).iterdir():
            try:
                task=json.loads((run/'task.json').read_text(encoding='utf-8'))
                if any(task.get(k)!=t.to_dict().get(k) for k in ('backend','material','crystal_cut','cladding','modes','solver')): continue
                cost=json.loads((run/'result.json').read_text(encoding='utf-8'))['cost']
                completed=len(list((run/'cache').glob('*.npz')))
                if completed: factors.append(max(1,cost['solver_calls']/completed))
            except (OSError,ValueError,KeyError,TypeError): continue
    factor=max(factors,default=1.5)
    seconds=None if not timings else [remaining*float(np.percentile(timings,50))*factor,remaining*float(np.percentile(timings,90))*factor*1.25]
    return {'logical_mode_points':int(count*multiplier),'cached_points_checked':cached,'remaining_mode_points':remaining,
            'derivative_points_default':t.dispersion_stencil_points,'estimated_seconds_range':seconds,'historical_samples':len(timings),
            'confidence':'preliminary historical estimate' if timings else 'insufficient comparable history; no invented duration',
            'recommended_wall_time_s':None if seconds is None else int(seconds[1]+120),'recommended_design_iterations':2,
            'budget_feasible':None if seconds is None else seconds[1]<=t.budget.get('max_wall_time_s',float('inf')),
            'notes':unknown+['model initialization, retries and field extraction vary; estimate never raises budget ceilings']}
