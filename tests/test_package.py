import json,importlib,sys
from itertools import product
from pathlib import Path
from dataclasses import replace
import numpy as np
import pytest
from scipy.constants import c
from qpm_simulation import describe_capabilities,plan_task
from qpm_simulation.config import Task,Geometry
from qpm_simulation.crystal import mapping,rotation,rotate_tensor,nonlinear_coefficient,dielectric_expressions
from qpm_simulation.physics import beta,local_dispersion,group_index,gvm,normalized_efficiency,pmf
from qpm_simulation.backends import DemoBackend,Backend,BudgetExceeded
from qpm_simulation.metrics import curve,tolerance_curves,objective_value
from qpm_simulation.optimization import search_geometry
from qpm_simulation.workflow import run_task,spectral

def task(**kwargs):
    p={'material':'LN','crystal_cut':'x','cladding':'AIR','geometry':{'thickness_nm':600,'etch_depth_nm':300,'top_width_nm':1800},'backend':'demo'}
    p.update(kwargs); return Task.from_dict(p)

@pytest.mark.parametrize('material,cut,mode,clad',list(product(('LN','LT'),('x','z'),('TE0','TM0'),('AIR','SIO2'))))
def test_combinations(material,cut,mode,clad):
    t=task(material=material,crystal_cut=cut,cladding=clad,modes={r:mode for r in ('pump','signal','idler')})
    assert t.geometry.validate(); assert mapping(cut)['lab_z']==('Y' if cut=='x' else 'X')
    r=rotation(cut); assert np.linalg.det(r)==1
    tensor=np.zeros((3,3,3)); tensor[2,2,2]=27
    transformed=rotate_tensor(tensor,cut); axis=0 if cut=='x' else 1
    assert transformed[axis,axis,axis]==27
    assert (nonlinear_coefficient(material,cut,[mode]*3)[0] is not None)==(mode==('TE0' if cut=='x' else 'TM0'))
    assert 'ne_sell' in dielectric_expressions(cut)[axis]

def test_defaults_and_side_effects():
    t=task(); assert t.wavelengths()==[1500,1520,1540,1550,1560,1580,1600]; assert t.length_mm==5
    assert len(describe_capabilities()['combinations'])==16
    before=set(sys.modules); p=plan_task(t); assert not p['solver_started']; assert 'mph' not in set(sys.modules)-before
    assert task(crystal_cut='z').modes['signal']=='TM0'
    assert task(wavelengths_nm=[1540,1560]).wavelengths()==[1540,1560]
    import qpm_simulation as qpm
    from qpm_simulation import optimization
    assert callable(qpm.search)

@pytest.mark.parametrize('bad',[{'search_axes':{'thickness_nm':{'min':500,'max':650,'step':float('nan')}}}, {'task_kind':'search'}, {'backend':'replay'}, {'length_mm':0}, {'material':'Si'}, {'process':'SFG'}, {'center_nm':500}, {'process':'SPDC','wavelengths_nm':[700]}])
def test_invalid(bad):
    with pytest.raises(ValueError): task(**bad)

def test_analytic_dispersion_and_missing_segments():
    w=1550+np.arange(-1,2)*2; om=2*np.pi*c/(w*1e-9); o0=2*np.pi*c/(1550e-9)
    b1=2.3/c; b2=40e-27; b=9e6+b1*(om-o0)+b2/2*(om-o0)**2; n=b*w*1e-9/(2*np.pi)
    result=local_dispersion(w,n,1550)
    assert result['beta1_ps_mm']==pytest.approx(b1*1e9,rel=1e-5)
    assert result['gvd_fs2_mm']==pytest.approx(40,abs=1e-4)
    assert result['D_ps_nm_km']==pytest.approx(-2*np.pi*c/(1550e-9)**2*b2*1e6,rel=1e-5)
    n[1]=np.nan; assert np.isnan(local_dispersion(w,n,1550)['gvd_fs2_mm'])
    assert group_index(1550,2,1.998,2.002,2)==pytest.approx(.45)
    signed,absolute=gvm(2,2.1); assert signed<0 and absolute==-signed

def test_cache_budget_and_replay(tmp_path):
    t=task(); b=DemoBackend(t,tmp_path); r=b.solve(t.geometry,'TE0',1550); b.solve(t.geometry,'TE0',1550)
    assert b.cost['solver_calls']==1 and b.cost['cache_hits']==1
    b.solve(t.geometry,'TM0',1550); b.solve(t.geometry,'TE0',1550,'refined'); assert b.cost['solver_calls']==3
    replay=replace(t,backend='replay',replay_source_backend='demo',replay_dir=str(tmp_path/'cache'))
    rb=Backend(replay,tmp_path); assert rb.solve(t.geometry,'TE0',1550)['neff']==r['neff']; assert rb.cost['solver_calls']==0
    with pytest.raises(RuntimeError): rb.solve(t.geometry,'TE0',1549)
    limited=DemoBackend(task(budget={'max_solver_calls':0}),tmp_path/'limited')
    with pytest.raises(BudgetExceeded): limited.solve(t.geometry,'TE0',1550)

def test_efficiency_tolerance_endpoints(tmp_path):
    t=task(wavelengths_nm=[1550],tolerances_nm={'thickness_nm':5}); b=DemoBackend(t,tmp_path)
    nominal=curve(t,b,t.geometry); tol=tolerance_curves(t,b,t.geometry,nominal)
    for sign in (-1,1):
        g=replace(t.geometry,thickness_nm=600+sign*5); matched=curve(t,b,g)[0]; pert=tol[f'thickness_nm_{sign*5:+g}'][0]
        factor=pmf(pert['delta_beta_m_inv']-nominal[0]['delta_beta_m_inv'],5)
        assert pert['process_coefficient_W_inv']==pytest.approx(matched['process_coefficient_W_inv']*factor)
        assert pert['period_um']==matched['period_um']
    assert nominal[0]['process_coefficient_W_inv']==pytest.approx(nominal[0]['normalized_efficiency_W_inv_m_inv2']*(.005)**2)
    assert sorted({m['geometry']['thickness_nm'] for f in (tmp_path/'cache').glob('*.json') for m in [json.loads(f.read_text())]})==[595,600,605]

@pytest.mark.parametrize('dimensions,count',[(1,4),(2,16),(3,27)])
def test_initial_cartesian_and_constraints(tmp_path,dimensions,count):
    axes={'thickness_nm':{'min':550,'max':650,'step':10},'top_width_nm':{'min':1600,'max':2000,'step':50},'etch_depth_nm':{'min':250,'max':350,'step':25}}
    t=task(task_kind='search',search_axes=dict(list(axes.items())[:dimensions]),max_additional_geometries=0)
    b=DemoBackend(t,tmp_path); r=search_geometry(t,b,lambda g:g.thickness_nm-600)
    assert r['initial_legal_count']==count; assert r['evaluated_count']==count
    for row in r['observations']:
        for axis,spec in t.search_axes.items(): assert (row['geometry'][axis]-spec['min'])/spec['step']==pytest.approx(round((row['geometry'][axis]-spec['min'])/spec['step']))

def test_adaptive_budgets_and_no_surrogate_result(tmp_path):
    t=task(task_kind='search',search_axes={'thickness_nm':{'min':550,'max':650,'step':1}},budget={'max_geometry_count':9})
    b=DemoBackend(t,tmp_path); seen=[]
    def f(g): seen.append(g.thickness_nm); return g.thickness_nm-602
    r=search_geometry(t,b,f); assert len(seen)==9; assert r['stop_reason']=='geometry_budget'
    assert all(len(batch)<=4 for batch in r['batches']); assert all(row['value']==row['geometry']['thickness_nm']-602 for row in r['candidates'])
    assert not r['target_within_tolerance']
    t=task(task_kind='search',search_axes={'thickness_nm':None},max_additional_geometries=8)
    r=search_geometry(t,DemoBackend(t,tmp_path/'continuous'),lambda g:g.thickness_nm-602)
    assert r['additional_count']==8; assert len(r['observations'])==12

def test_spectral_and_complete_workflow(tmp_path):
    t=task(process='SPDC',task_kind='spectral_2d',wavelength_axes_nm={'signal_nm':[1500,1550],'idler_nm':[1500,1550]})
    r=spectral(t,DemoBackend(t,tmp_path)); assert np.shape(r['values'])==(2,2); assert r['values'][1][1]==pytest.approx(1)
    t=task(wavelengths_nm=[1550]); r=run_task(t,tmp_path/'fixed')
    assert not r['failures']; assert not r['validation']['scientifically_validated']; assert len(r['figures'])==8
    assert (tmp_path/'fixed'/'curves.csv').exists(); assert 'null' in (tmp_path/'fixed'/'result.json').read_text() or r['curves']

def test_seven_scripts_import_without_solver():
    names=['LN_LT_COMSOL_SHG_QPM_Automation','LN_LT_COMSOL_SFG_Period_Automation','LN_LT_COMSOL_SPDC_PMF_Automation','LN_LT_COMSOL_Dispersion_Automation','LN_LT_COMSOL_GVM_Automation','LN_LT_Nonlinear_Efficiency','TH_sensitivity_COMSOL']
    for name in names: importlib.import_module(name)
    assert 'mph' not in sys.modules
