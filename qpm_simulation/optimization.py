"""Bounded deterministic selection; GP predictions never become observations."""
from itertools import product
from dataclasses import replace,asdict
import numpy as np
from .config import grid
from .metrics import objective_value
from .backends import BudgetExceeded

def axis_values(spec,count):
    if 'step' in spec:
        values=np.asarray(grid(spec['min'],spec['max'],spec['step']))
        targets=np.linspace(spec['min'],spec['max'],count)
        initial=sorted(set(float(values[np.argmin(abs(values-x))]) for x in targets))
    else:
        initial=list(np.linspace(spec['min'],spec['max'],count))
        values=np.linspace(spec['min'],spec['max'],max(count,2*count-1))
    return initial,list(values)

def search_geometry(task,backend,evaluator=None):
    from dispersion_core import MaternGP
    names=list(task.search_axes); count=3 if len(names)>=3 else 4
    axes=[axis_values(task.search_axes[n],count) for n in names]
    def legal(point):
        try: return replace(task.geometry,**dict(zip(names,point))).validate()
        except ValueError: return None
    initial=[p for p in product(*(a[0] for a in axes)) if legal(p)]
    pool=[p for p in product(*(a[1] for a in axes)) if legal(p)]
    all_grid=all('step' in task.search_axes[n] for n in names)
    if task.full_scan and not all_grid: raise ValueError('full_scan requires explicit step on every axis')
    queue=list(pool if task.full_scan else initial); rows=[]; attempted=set(); stop='candidate_pool_exhausted'
    from .reporting import write_json
    import json
    state_file=backend.root/'search-checkpoint.json'
    signature={'axes':task.search_axes,'objective':task.objective,'stencil':task.dispersion_stencil_points,'step':task.dispersion_step_nm}
    if state_file.exists() and evaluator is None:
        saved=json.loads(state_file.read_text(encoding='utf-8'))
        if saved['signature']!=signature: raise ValueError('search checkpoint configuration mismatch')
        rows=saved['rows']; attempted={tuple(r['point']) for r in rows}
    cap=task.budget.get('max_geometry_count',float('inf')); extra=sum(r['stage']=='adaptive' for r in rows); batches=[]
    evaluate=evaluator or (lambda g:objective_value(task,backend,g))
    def observe(p,stage):
        if p in attempted: return
        g=legal(p)
        try:
            backend.check_budget(); value=float(evaluate(g)); reason='' if np.isfinite(value) else 'target metric unavailable'
        except BudgetExceeded: raise
        except Exception as exc: value=np.nan; reason=str(exc)
        attempted.add(p)
        rows.append({'geometry':asdict(g),'point':list(p),'value':value,'valid':bool(np.isfinite(value)),'stage':stage,'failure':reason})
        if evaluator is None: write_json(state_file,{'signature':signature,'rows':rows})
    try:
        for p in queue:
            if len(rows)>=cap: stop='geometry_budget'; break
            observe(p,'full_scan' if task.full_scan else 'initial')
        else:
            while not task.full_scan and extra<task.max_additional_geometries:
                available=[p for p in pool if p not in attempted]
                if not available and not all_grid:
                    # Refine measured intervals only on continuous dimensions.
                    refined=[]
                    for i,n in enumerate(names):
                        spec=task.search_axes[n]
                        if 'step' in spec: vals=axes[i][1]
                        else:
                            measured=sorted(set(p[i] for p in attempted))
                            vals=sorted(set(measured+[(a+b)/2 for a,b in zip(measured,measured[1:])]))
                        refined.append(vals)
                    pool=[p for p in product(*refined) if legal(p)]
                    available=[p for p in pool if p not in attempted]
                if not available: break
                if len(rows)>=cap: stop='geometry_budget'; break
                good=[r for r in rows if r['valid']]
                if len(good)>=2:
                    y=np.array([r['value'] for r in good])
                    minimum=task.objective['metric']=='thickness_sensitivity' or task.objective.get('reduction')=='max_abs_over_band'
                    if minimum: y=abs(y)
                    gp=MaternGP().fit(np.array([r['point'] for r in good]),y)
                    mean,std=gp.predict(np.array(available)); score=(-mean+2*std) if minimum else (3*std-abs(mean))
                    ordering=np.argsort(-score,kind='stable')
                else: ordering=np.arange(len(available))
                size=min(task.batch_size,task.max_additional_geometries-extra,int(min(cap-len(rows),len(available))))
                batch=[available[i] for i in ordering[:size]]; batches.append([list(p) for p in batch])
                for p in batch: observe(p,'adaptive'); extra+=1
            if extra>=task.max_additional_geometries: stop='additional_limit'
    except BudgetExceeded as exc: stop=str(exc)
    good=sorted((r for r in rows if r['valid']),key=lambda r:abs(r['value']))
    tolerance=task.objective.get('tolerance')
    return {'candidates':good,'observations':rows,'best':good[0] if good else None,'stop_reason':stop,
        'initial_legal_count':len(initial),'initial_requested_count':count**len(names),'candidate_pool_count':len(pool),
        'evaluated_count':len(rows),'additional_count':extra,'batches':batches,'full_grid_exhausted':all_grid and len(attempted)==len(pool),
        'target_within_tolerance':bool(good and tolerance is not None and abs(good[0]['value'])<=tolerance),
        'claim':'best measured candidate; no global no-solution claim','coverage':task.search_axes,
        'uncompleted':[] if len(attempted)==len(pool) else ['candidate pool not fully evaluated']}
