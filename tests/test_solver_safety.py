from dataclasses import replace
import numpy as np
import pytest
import LN_LT_COMSOL_SHG_QPM_Automation as base

class FakeModel:
    def __init__(self): self.params={}
    def parameter(self,name,value=None):
        if value is not None: self.params[name]=value
        return self.params.get(name)

def record():
    return {'neff':1.9,'Ey':np.ones((3,3)),'y':np.arange(3)*1e-7,'z':np.arange(3)*1e-7,'solve_elapsed_s':0}

def config(): return base.Config(fundamental_wavelengths_nm=(1548,1550,1552))
def point(): return base.ScanPoint(600,1800,300,1550)

def test_twenty_to_forty(monkeypatch):
    calls=[]
    def solve(m,c,p,count):
        calls.append(count)
        if count==20: raise RuntimeError('no valid mode')
        return record()
    monkeypatch.setattr(base,'_solve_once_and_select',solve)
    r,n=base._solve_and_select(FakeModel(),config(),point()); assert calls==[20,40] and n==40

def test_fast_tracking_and_reseed(monkeypatch):
    calls=[]
    monkeypatch.setattr(base,'_solve_once_and_select',lambda m,c,p,n:(calls.append(n) or record()))
    cache={}; base._remember_prediction(cache,1548,record()); m=FakeModel()
    r,n=base._solve_scan_point(m,config(),point(),cache,force_robust=False); assert calls==[6] and n==6
    assert m.params['nshift']=='ne_sell(lam_um)'
    calls.clear(); base._solve_scan_point(m,config(),point(),cache,force_robust=True); assert calls==[20]

def test_prediction_retry_and_center_restoration(monkeypatch):
    calls=[]; m=FakeModel(); cache={}; base._remember_prediction(cache,1548,record())
    def solve(m,c,p,n):
        calls.append((n,m.params['nshift']))
        if m.params['nshift']=='ne_sell(lam_um)': raise RuntimeError('primary failure')
        return record()
    monkeypatch.setattr(base,'_solve_once_and_select',solve)
    r,n=base._solve_with_prediction(m,config(),point(),cache)
    assert [x[0] for x in calls]==[20,40,40]; assert r['search_method']=='predicted_fallback'; assert m.params['nshift']=='ne_sell(lam_um)'

def test_overlap_failure_and_delayed_anchor(monkeypatch):
    m=FakeModel(); calls=[]
    def solve(m,c,p,n): calls.append(n); raise RuntimeError('not found')
    monkeypatch.setattr(base,'_solve_once_and_select',solve)
    with pytest.raises(base.PredictionUnavailable): base._solve_scan_point(m,config(),point(),{},force_robust=True)
    assert calls==[20,40]; assert m.params['nshift']=='ne_sell(lam_um)'
    cache={}; base._remember_prediction(cache,1548,record())
    def flipped(m,c,p,n):
        r=record(); r['Ey']=np.eye(3); return r
    monkeypatch.setattr(base,'_solve_once_and_select',flipped)
    with pytest.raises(RuntimeError,match='overlap'): base._solve_with_prediction(m,config(),point(),cache,prediction_only=True)
    assert m.params['nshift']=='ne_sell(lam_um)'

def test_zcut_search_center():
    assert base._primary_search_expression(replace(config(),crystal_cut='z',target_mode='TM0'))=='ne_sell(lam_um)'
    assert base._primary_search_expression(replace(config(),crystal_cut='z',target_mode='TE0'))=='no_sell(lam_um)'
