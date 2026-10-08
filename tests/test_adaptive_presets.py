import json
import numpy as np
import pytest
from qpm_simulation.physics import local_dispersion
from qpm_simulation.plot_presets import render

def test_seven_point_polynomial_and_missing():
    w=1550+np.arange(-3,4)*2
    n=2.1+1e-7*(w-1550)**2+1e-10*(w-1550)**4
    seven=local_dispersion(w,n,1550)
    three=local_dispersion(w[2:5],n[2:5],1550)
    assert seven['gvd_fs2_mm'] != pytest.approx(three['gvd_fs2_mm'],rel=1e-4)
    n[0]=np.nan
    assert np.isnan(local_dispersion(w,n,1550)['gvd_fs2_mm'])

def test_preset_source_and_missing_points(tmp_path):
    source=tmp_path/'result.json'
    source.write_text(json.dumps({'curves':[{'wavelength_nm':1540,'signal_gvd_fs2_mm':1},{'wavelength_nm':1550,'signal_gvd_fs2_mm':None},{'wavelength_nm':1560,'signal_gvd_fs2_mm':2}], 'validation':{'data_kind':'SYNTHETIC DEMO'}}),encoding='utf-8')
    raw=source.read_bytes()
    result=render(tmp_path,{'series':[{'metric':'signal_gvd_fs2_mm'}]},tmp_path/'plots')
    assert source.read_bytes()==raw
    assert result['figures']
    with pytest.raises(ValueError,match='incompatible'):
        render(tmp_path,{'series':[{'metric':'signal_gvd_fs2_mm'},{'metric':'period_um'}]},tmp_path/'bad')

def test_anomaly_limit_and_zero_does_not_trigger():
    from types import SimpleNamespace
    from qpm_simulation.metrics import adaptive_curve
    task=SimpleNamespace(dispersion_absolute_tolerance_fs2_mm=1,dispersion_relative_tolerance=.05)
    rows=[{'signal_gvd_fs2_mm':v} for v in [-4,-2,0,2,4]]
    adaptive_curve(task,None,None,rows,['signal'],'default')
    assert all('signal_dispersion_quality' not in r for r in rows)

def test_old_checkpoint_defaults_are_migrated(tmp_path):
    from qpm_simulation.config import Task
    from qpm_simulation.backends import DemoBackend
    task=Task.from_dict({'material':'LN','crystal_cut':'x','cladding':'AIR','geometry':{'thickness_nm':600,'etch_depth_nm':300,'top_width_nm':1200}})
    old=task.to_dict()
    for key in ['adaptive_dispersion','dispersion_absolute_tolerance_fs2_mm','dispersion_relative_tolerance']: old.pop(key)
    (tmp_path/'checkpoint.json').write_text(json.dumps({'task':old,'cost':{'solver_calls':2}}),encoding='utf-8')
    backend=DemoBackend(task,tmp_path)
    assert backend.cost['solver_calls']==2

def test_postprocess_export_preserves_source(tmp_path):
    from qpm_simulation.reporting import postprocess_export
    source=tmp_path/'result.json'
    value={'task':{'length_mm':5,'tolerances_nm':{}},'validation':{'data_kind':'SYNTHETIC DEMO'},'curves':[{'wavelength_nm':1550,'period_um':8,**{f'{role}_{metric}':None for role in ('pump','signal','idler') for metric in ('gvd_fs2_mm','beta1_ps_mm','D_ps_nm_km')}}]}
    source.write_text(json.dumps(value),encoding='utf-8');original=source.read_bytes()
    result=postprocess_export(tmp_path,tmp_path/'postprocess/p001')
    assert result['figures'] and source.read_bytes()==original

def test_mode_discontinuity_does_not_add_stencil_points():
    from types import SimpleNamespace
    from qpm_simulation.metrics import adaptive_curve
    task=SimpleNamespace(dispersion_absolute_tolerance_fs2_mm=1,dispersion_relative_tolerance=.05,dispersion_step_nm=2,modes={'signal':'TE0'})
    class Backend:
        def __init__(self): self.points=[]
        def solve(self,g,mode,wl,mesh):
            self.points.append(wl)
            return {'neff':2,'field':np.eye(2)[0 if wl<1550 else 1],'x_m':np.arange(2),'y_m':np.arange(2)}
    backend=Backend();rows=[{'signal_gvd_fs2_mm':v,'signal_wavelength_nm':w} for v,w in zip([0,100,0],[1540,1550,1560])]
    adaptive_curve(task,backend,None,rows,['signal'],'default')
    assert rows[1]['signal_dispersion_quality']['status']=='mode_continuity_requires_review'
    assert backend.points==[1548,1550,1552]
