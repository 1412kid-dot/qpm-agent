"""Explicit backends. Importing this module never starts MPh."""
from pathlib import Path
from dataclasses import asdict
from collections import OrderedDict
import hashlib, json, time, os, uuid
import numpy as np
from scipy.constants import c
from .crystal import extraordinary

class BudgetExceeded(BaseException):
    """Cancellation must pass through legacy broad Exception retry handlers."""
    pass

class Backend:
    def __init__(self, task, output, persist=True):
        self.task=task; self.root=Path(output); self.started=time.perf_counter()
        self.cost={'solver_calls':0,'mode_candidates':0,'cache_hits':0,'failed_points':0}
        self.memory={}; self.memory_meta={}; self.failures=[]; self.rejected={}; self.geometries=set()
        self.persist=persist and task.backend!='replay'; self.previous_elapsed=0.; self.cache_index=None
        checkpoint=self.root/'checkpoint.json'
        if self.persist and checkpoint.exists():
            previous=json.loads(checkpoint.read_text(encoding='utf-8'))
            if previous.get('task'):
                ignored={'budget','provided_fields','assumptions','schema_version','length_defaulted'}
                from .config import Task
                normalized_previous=Task.from_dict(previous['task']).to_dict()
                if {k:v for k,v in normalized_previous.items() if k not in ignored}!={k:v for k,v in task.to_dict().items() if k not in ignored}: raise ValueError('checkpoint task changed; new run required')
            self.cost.update({k:previous.get('cost',{}).get(k,0) for k in self.cost})
            self.previous_elapsed=previous.get('cost',{}).get('elapsed_s',0.)
            self.geometries=set(previous.get('geometries',[]))
        elif self.persist and (self.root/'plugin-job.json').exists():
            # Legacy interrupted runs lack a point journal: retain the best recorded cost.
            record=json.loads((self.root/'plugin-job.json').read_text(encoding='utf-8'))
            recorded=record.get('result') or {}; recorded_cost=recorded.get('cost') or (record.get('progress') or {}).get('cost') or {}
            self.cost.update({k:recorded_cost.get(k,0) for k in self.cost})
            self.previous_elapsed=recorded_cost.get('elapsed_s',0.)
        compatibility=json.loads(Path(__file__).with_name('cache_compatibility.json').read_text(encoding='utf-8'))
        self.legacy_fingerprints=set(compatibility['verified_legacy_fingerprints'])
        paths=[Path(__file__),Path(__file__).with_name('crystal.py'),Path(__file__).parents[1]/'LN_LT_COMSOL_SHG_QPM_Automation.py']
        paths=[Path(__file__).with_name('crystal.py'),Path(__file__).with_name('materials.py'),Path(__file__).parents[1]/'LN_LT_COMSOL_SHG_QPM_Automation.py']
        self.fingerprint=hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()
        version_file=Path(__file__).with_name('source-version.json')
        if version_file.exists():
            version=json.loads(version_file.read_text(encoding='utf-8'))
            if version.get('affects_modes'): self.fingerprint=hashlib.sha256((self.fingerprint+version['digest']).encode()).hexdigest()
    def check_budget(self):
        b=self.task.budget
        if self.cost['solver_calls'] >= b.get('max_solver_calls',float('inf')): raise BudgetExceeded('solver-call budget exhausted')
        if self.previous_elapsed+time.perf_counter()-self.started >= b.get('max_wall_time_s',float('inf')): raise BudgetExceeded('wall-time budget exhausted')
    def identity(self, geometry, mode, wavelength, mesh):
        return {'version':'0.1','implementation_sha256':self.fingerprint,'backend':self.task.replay_source_backend if self.task.backend=='replay' else self.task.backend,'material':self.task.material,'cut':self.task.crystal_cut,
            'propagation':'Y' if self.task.crystal_cut=='x' else 'X','cladding':self.task.cladding,
            'geometry':asdict(geometry),'mode':mode,'wavelength_nm':float(wavelength),'mesh':mesh,'solver':self.task.solver}
    def solve(self, geometry, mode, wavelength, mesh='default'):
        geometry.validate(); meta=self.identity(geometry,mode,wavelength,mesh)
        key=hashlib.sha256(json.dumps(meta,sort_keys=True).encode()).hexdigest()
        if key in self.memory: self.cost['cache_hits']+=1; return self.memory[key]
        if key in self.rejected: raise RuntimeError(self.rejected[key])
        self.check_time()
        cache=(Path(self.task.replay_dir) if self.task.backend=='replay' else self.root/'cache')/key
        result=self.cached_result(meta)
        if result is not None:
            self.cost['cache_hits']+=1; self.memory[key]=result; self.memory_meta[key]=meta
            if self.task.backend!='replay':
                from .reporting import write_json
                import shutil
                source=self.cache_index[self.semantic_key(meta)].with_suffix('.npz')
                cache.parent.mkdir(parents=True,exist_ok=True)
                if source.resolve()!=cache.with_suffix('.npz').resolve():
                    temporary=cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.tmp')
                    shutil.copyfile(source,temporary); os.replace(temporary,cache.with_suffix('.npz'))
                    write_json(cache.with_suffix('.json'),meta)
            self.checkpoint('cache_hit'); return result
        if self.task.backend=='replay': raise RuntimeError('validated replay point unavailable')
        self.check_budget()
        geometry_key=json.dumps(asdict(geometry),sort_keys=True)
        if geometry_key not in self.geometries and len(self.geometries)>=self.task.budget.get('max_geometry_count',float('inf')): raise BudgetExceeded('geometry budget exhausted')
        self.geometries.add(geometry_key)
        try:
            result=self.compute(geometry,mode,wavelength,mesh); self.validate_result(result); self.validate_physics(result,wavelength)
        except BudgetExceeded: raise
        except Exception as exc:
            self.cost['failed_points']+=1
            self.failures=[f for f in self.failures if f['point']!=meta]
            self.failures.append({'point':meta,'reason':str(exc)})
            self.checkpoint('point_failed')
            if type(exc).__name__!='PredictionUnavailable': self.rejected[key]=str(exc)
            raise
        cache.parent.mkdir(parents=True,exist_ok=True)
        temporary=cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.tmp')
        with temporary.open('wb') as f:
            np.savez_compressed(f,**result); f.flush(); os.fsync(f.fileno())
        os.replace(temporary,cache.with_suffix('.npz'))
        from .reporting import write_json
        write_json(cache.with_suffix('.json'),meta)
        if self.cache_index is not None: self.cache_index[self.semantic_key(meta)]=cache.with_suffix('.json')
        self.failures=[f for f in self.failures if f['point']!=meta]
        self.memory[key]=result; self.memory_meta[key]=meta; self.checkpoint('point_completed'); return result
    @staticmethod
    def semantic_key(meta):
        return json.dumps({k:v for k,v in meta.items() if k not in ('implementation_sha256','version')},sort_keys=True)
    def cached_result(self,meta):
        if self.cache_index is None:
            self.cache_index={}
            dirs=[Path(self.task.replay_dir)] if self.task.backend=='replay' else [self.root/'cache']+[p/'cache' for p in self.root.parent.iterdir() if p.is_dir() and (p/'task.json').is_file()] if self.root.parent.exists() else [self.root/'cache']
            for folder in dirs:
                for candidate in sorted(folder.glob('*.json')):
                    try:
                        stored=json.loads(candidate.read_text(encoding='utf-8'))
                        if stored.get('implementation_sha256') not in self.legacy_fingerprints|{self.fingerprint}: continue
                        if candidate.with_suffix('.npz').is_file(): self.cache_index[self.semantic_key(stored)]=candidate
                    except (OSError,ValueError): continue
        candidate=self.cache_index.get(self.semantic_key(meta))
        if candidate is None: return None
        try:
            stored=json.loads(candidate.read_text(encoding='utf-8'))
            if self.semantic_key(stored)!=self.semantic_key(meta) or stored['implementation_sha256'] not in self.legacy_fingerprints|{self.fingerprint}: return None
            with np.load(candidate.with_suffix('.npz'),allow_pickle=False) as z: result={k:z[k] for k in z.files}
            self.validate_result(result); self.validate_physics(result,meta['wavelength_nm'])
            return result
        except (OSError,ValueError,KeyError,EOFError): return None
    def checkpoint(self,stage):
        if not self.persist: return
        from .reporting import write_json
        write_json(self.root/'checkpoint.json',{'stage':stage,'task':self.task.to_dict(),'cost':self.summary(),'geometries':sorted(self.geometries),'failures':self.failures})
    def check_time(self):
        if self.previous_elapsed+time.perf_counter()-self.started >= self.task.budget.get('max_wall_time_s',float('inf')): raise BudgetExceeded('wall-time budget exhausted')
    @staticmethod
    def validate_result(r):
        if not np.isfinite(r['neff']) or float(r['neff'])<=1: raise ValueError('invalid neff')
        if not np.isfinite(r['field']).all() or r['field'].shape!=(len(r['y_m']),len(r['x_m'])): raise ValueError('invalid field grid')
        if np.vdot(r['field'],r['field']).real<=0: raise ValueError('zero modal field')
        if np.asarray(r['material_mask']).shape!=r['field'].shape: raise ValueError('invalid material mask')
        if not np.isfinite(r['x_m']).all() or not np.isfinite(r['y_m']).all() or np.any(np.diff(r['x_m'])<=0) or np.any(np.diff(r['y_m'])<=0): raise ValueError('invalid field coordinates')
    def validate_physics(self,result,wavelength):
        origin=self.task.replay_source_backend if self.task.backend=='replay' else self.task.backend
        if origin=='comsol':
            from .materials import refractive_indices
            if float(result['neff'])>=max(refractive_indices(self.task.material,wavelength)): raise ValueError('neff outside guided material bounds')
    def summary(self): return dict(self.cost,unique_computed_geometries=len(self.geometries),elapsed_s=self.previous_elapsed+time.perf_counter()-self.started,
        actual_comsol_calls=self.cost['solver_calls'] if self.task.backend=='comsol' else 0,electricity='not measured')
    def close(self): pass

class DemoBackend(Backend):
    """Analytic synthetic beta, only for software tests; never a material prediction."""
    def compute(self,g,mode,wavelength,mesh):
        self.cost['solver_calls']+=1
        omega=2*np.pi*c/(wavelength*1e-9); o0=2*np.pi*c/(1550e-9); u=omega-o0
        b2=((g.thickness_nm-600)*.7+(g.top_width_nm-1800)*.08)*1e-27
        b=2.05*o0/c+2.2/c*u+.5*b2*u*u+1e-42*u**3/6
        n=b*c/omega + (.03 if self.task.material=='LT' else 0) + (.01 if not extraordinary(self.task.crystal_cut,mode) else 0)
        x=np.linspace(-3e-6,3e-6,61); y=np.linspace(-2e-6,1e-6,61); xx,yy=np.meshgrid(x,y)
        field=np.exp(-(xx/(g.top_width_nm*1e-9))**2-((yy+g.thickness_nm*.5e-9)/(g.thickness_nm*1e-9))**2)
        slab=(yy>=-g.thickness_nm*1e-9)&(yy<=-g.etch_depth_nm*1e-9)
        width=g.top_width_nm*1e-9+2*(-yy)/np.tan(np.deg2rad(g.sidewall_angle_deg))
        mask=slab|((yy>=-g.etch_depth_nm*1e-9)&(yy<=0)&(abs(xx)<=width/2))
        return {'neff':float(n),'field':field,'x_m':x,'y_m':y,'material_mask':mask,'field_overlap':1.0}

class ComsolBackend(Backend):
    def __init__(self,task,output):
        super().__init__(task,output)
        # This is the sole solver-start boundary, reached only by explicit execution.
        import mph
        import LN_LT_COMSOL_SHG_QPM_Automation as base
        self.base=base
        old_path=os.environ.get('PATH',''); binary=task.solver.get('comsol_bin')
        try:
            if binary:
                if not Path(binary).is_dir(): raise ValueError('comsol_bin directory does not exist')
                os.environ['PATH']=str(binary)+os.pathsep+old_path
            self.client=mph.start(version=task.solver.get('version','6.4'),cores=task.solver.get('threads',4))
        finally: os.environ['PATH']=old_path
        self.models=OrderedDict(); self.histories={}; self.deferred={}; self.attempt_log=[]; self.selection_log=[]
        for filename,attr in [('solver_attempts.json','attempt_log'),('mode_selection.json','selection_log')]:
            if (self.root/filename).exists(): setattr(self,attr,json.loads((self.root/filename).read_text(encoding='utf-8')))
    def compute(self,g,mode,wl,mesh):
        from dataclasses import replace
        base=self.base; key=(tuple(asdict(g).values()),mode,mesh)
        from .materials import refractive_indices
        refractive_indices(self.task.material,wl)
        if key not in self.models:
            if len(self.models)>=self.task.solver.get('model_cache_size',2):
                old_key,(old_model,old_cfg)=self.models.popitem(last=False)
                self.client.remove(old_model.name())
            options={k:v for k,v in self.task.solver.items() if k in base.Config.__dataclass_fields__}
            cfg=base.Config(**dict(options,core_material=self.task.material,crystal_cut=self.task.crystal_cut,
                target_mode=mode,top_cladding=self.task.cladding,film_thicknesses_nm=(g.thickness_nm,),
                top_widths_nm=(g.top_width_nm,),etch_depths_nm=(g.etch_depth_nm,),sidewall_angle_deg=g.sidewall_angle_deg,
                output_root=self.root,run_name='solver',fundamental_wavelengths_nm=()))
            (cfg.run_dir/'model').mkdir(parents=True,exist_ok=True)
            raw=base.build_comsol_model(self.client,cfg,mesh)
            if hasattr(raw,'rename'): raw.rename('QPM_'+hashlib.sha256(repr(key).encode()).hexdigest()[:12])
            owner=self
            class CountedModel:
                def __getattr__(self,name): return getattr(raw,name)
                def solve(self):
                    owner.check_budget(); owner.cost['solver_calls']+=1
                    count=int(getattr(raw,'_lnlt_mode_count',20)); owner.cost['mode_candidates']+=count
                    entry={'modes':count,'wavelength_nm':getattr(raw,'_qpm_wavelength_nm',wl),'geometry':asdict(g),'mesh':mesh,'mode':mode,'status':'running'}
                    owner.attempt_log.append(entry); begun=time.perf_counter()
                    owner.checkpoint('solver_running'); owner.flush_logs()
                    try: raw.solve(); entry['status']='solved'
                    except Exception as exc: entry.update(status='failed',reason=str(exc)); raise
                    finally:
                        entry['elapsed_s']=time.perf_counter()-begun; owner.checkpoint('solver_finished'); owner.flush_logs()
                def __setattr__(self,name,value): setattr(raw,name,value)
            self.models[key]=(CountedModel(),cfg); self.histories.setdefault(key,{})
        model,cfg=self.models[key]; history=self.histories[key]
        for cache_key,data in self.memory.items():
            meta=self.memory_meta.get(cache_key,{})
            if meta.get('geometry')==asdict(g) and meta.get('mode')==mode and meta.get('mesh')==mesh:
                history[meta['wavelength_nm']]={'neff':float(data['neff']),'field':data['field'],'y':data['x_m'],'z':data['y_m']}
        self.models.move_to_end(key)
        model._qpm_wavelength_nm=float(wl)
        # All wavelengths in this instance belong to one geometry/mode/mesh.
        cfg=replace(cfg,fundamental_wavelengths_nm=tuple(sorted(set(history)|{float(wl)})))
        model.parameter('lda0',f'{wl}[nm]')
        point=base.ScanPoint(g.thickness_nm,g.top_width_nm,g.etch_depth_nm,wl)
        pending_key=(key,float(wl))
        if pending_key in self.deferred:
            if len(history)<=self.deferred[pending_key]['history_count']: raise base.PredictionUnavailable('delayed until a same-geometry anchor exists')
            old=self.deferred.pop(pending_key)
            r,_=base._solve_scan_point(model,cfg,point,history,force_robust=True,prediction_only=True,primary_error=old['reason'])
        else:
            try: r,_=base._solve_scan_point(model,cfg,point,history,force_robust=len(history)%4==0)
            except base.PredictionUnavailable as exc:
                self.deferred[pending_key]={'history_count':len(history),'reason':str(exc)}
                self.selection_log.append({'wavelength_nm':wl,'geometry':asdict(g),'mode':mode,'status':'delayed','reason':str(exc)}); raise
        self.selection_log.append({'wavelength_nm':wl,'geometry':asdict(g),'mode':mode,'mesh':mesh,'status':'accepted',
            'method':r.get('tracking_method'), 'tracking_error':r.get('tracking_error'), 'primary_error':r.get('primary_search_error'),
            'overlap':r.get('prediction_field_overlap'),'attempts':r.get('attempts')})
        base._remember_prediction(history,wl,r)
        x,y=np.asarray(r['y']),np.asarray(r['z']); xx,yy=np.meshgrid(x,y)
        slab=(yy>=-g.thickness_nm*1e-9)&(yy<=-g.etch_depth_nm*1e-9)
        width=g.top_width_nm*1e-9+2*(-yy)/np.tan(np.deg2rad(g.sidewall_angle_deg))
        mask=slab|((yy>=-g.etch_depth_nm*1e-9)&(yy<=0)&(abs(xx)<=width/2))
        return {'neff':float(np.real(r['neff'])),'field':r['Ey'],'x_m':x,'y_m':y,'material_mask':mask,
                'field_overlap':float(r.get('prediction_field_overlap',np.nan))}
    def flush_logs(self):
        from .reporting import write_json
        write_json(self.root/'solver_attempts.json',self.attempt_log)
        write_json(self.root/'mode_selection.json',self.selection_log)
    def close(self):
        self.flush_logs(); self.checkpoint('closed')
        for model,cfg in self.models.values():
            try: self.client.remove(model.name())
            except Exception: pass

def create_backend(task,output):
    return {'demo':DemoBackend,'comsol':ComsolBackend,'replay':Backend}[task.backend](task,output)
