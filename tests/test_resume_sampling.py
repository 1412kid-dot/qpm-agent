import json
from dataclasses import replace
import numpy as np
import pytest
from scipy.constants import c
from qpm_simulation.config import as_task
from qpm_simulation.backends import DemoBackend,BudgetExceeded
from qpm_simulation.metrics import curve
from qpm_simulation.physics import local_dispersion
from qpm_simulation.planning import sampling_points,estimate_task
from qpm_simulation.workflow import run_task,verify_candidate

def task(**kw):
    return as_task(dict(material='LN',crystal_cut='x',cladding='AIR',geometry=dict(thickness_nm=600,etch_depth_nm=300,top_width_nm=1200),backend='demo',wavelengths_nm=[1550],required_metrics=['gvd'],**kw))

def test_exact_lambda_polynomial_and_five_point():
    lam=1550e-9
    for count in (3,5):
        w=1550+np.arange(-(count//2),count//2+1)*2
        n=2+.1*(w*1e-9-lam)/lam+.025*((w*1e-9-lam)/lam)**2
        got=local_dispersion(w,n,1550)
        assert got['beta1_ps_mm']==pytest.approx(1.9/c*1e9,rel=1e-8)
        assert got['gvd_fs2_mm']==pytest.approx(lam*.05/(2*np.pi*c*c)/1e-27,rel=1e-8)
        assert np.isnan(got['fit_error_fs2_mm']) if count==3 else np.isfinite(got['fit_error_fs2_mm'])
        n[0]=np.nan
        assert np.isnan(local_dispersion(w,n,1550)['gvd_fs2_mm'])

def test_requested_band_three_five_and_no_hidden_verification(tmp_path):
    t=task(); b=DemoBackend(t,tmp_path)
    row=curve(t,b,t.geometry)[0]
    assert b.cost['solver_calls']==3
    assert row['valid'] and np.isfinite(row['signal_gvd_fs2_mm'])
    assert np.isnan(row['pump_gvd_fs2_mm'])
    verify_candidate(t,b,t.geometry)
    assert b.cost['solver_calls']==3
    five=replace(t,dispersion_refinement_wavelengths_nm=[1550])
    assert len(sampling_points(five))==5
    b=DemoBackend(five,tmp_path/'five');curve(five,b,t.geometry)
    assert b.cost['solver_calls']==5

def test_durable_budget_resume_and_shared_cache(tmp_path):
    t=task(budget={'max_solver_calls':2}); first=tmp_path/'first'
    b=DemoBackend(t,first)
    for wl in (1548,1550): b.solve(t.geometry,'TE0',wl)
    with pytest.raises(BudgetExceeded): b.solve(t.geometry,'TE0',1552)
    resumed=replace(t,budget={'max_solver_calls':3}); b=DemoBackend(resumed,first)
    curve(resumed,b,t.geometry)
    assert b.cost['solver_calls']==3
    assert b.cost['cache_hits']>=2
    (first/'task.json').write_text(json.dumps(resumed.to_dict()),encoding='utf-8')
    second=DemoBackend(resumed,tmp_path/'second');curve(resumed,second,t.geometry)
    assert second.cost['solver_calls']==0
    assert len(list((tmp_path/'second/cache').glob('*.npz')))==3
    assert estimate_task(resumed,tmp_path)['cached_points_checked']==3

def test_corrupt_and_wrong_physics_cache_not_accepted(tmp_path):
    t=task(); first=tmp_path/'first'; first.mkdir()
    (first/'task.json').write_text(json.dumps(t.to_dict()),encoding='utf-8')
    b=DemoBackend(t,first);b.solve(t.geometry,'TE0',1550)
    next((first/'cache').glob('*.npz')).write_bytes(b'broken')
    b=DemoBackend(t,tmp_path/'second');b.solve(t.geometry,'TE0',1550)
    assert b.cost['solver_calls']==1
    b=DemoBackend(replace(t,material='LT'),tmp_path/'third');b.solve(t.geometry,'TE0',1550)
    assert b.cost['solver_calls']==1

def test_checkpoint_reject_changed_task_and_sampling_estimate(tmp_path):
    t=task();run_task(t,tmp_path/'run')
    with pytest.raises(ValueError,match='resume task changed'): run_task(replace(t,material='LT'),tmp_path/'run')
    assert estimate_task(t)['remaining_mode_points']==3
    assert estimate_task(t)['estimated_seconds_range'] is None
    with pytest.raises(ValueError): task(dispersion_stencil_points=9)
