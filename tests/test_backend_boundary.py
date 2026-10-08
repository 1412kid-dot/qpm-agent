"""Use fake MPh model; no solver or license is involved."""
import sys,types
import numpy as np
import pytest
from qpm_simulation.config import Task
from qpm_simulation.backends import ComsolBackend,BudgetExceeded
import LN_LT_COMSOL_SHG_QPM_Automation as base

def test_real_backend_adapter_and_delay_with_fake_mph(tmp_path,monkeypatch):
    starts=[]
    class Model:
        def __init__(self): self.params={}
        def parameter(self,k,v): self.params[k]=v
        def solve(self): pass
        def name(self): return 'fake'
    class Client:
        def remove(self,m): pass
    fake=types.SimpleNamespace(start=lambda **kw:(starts.append(kw) or Client()))
    monkeypatch.setitem(sys.modules,'mph',fake)
    monkeypatch.setattr(base,'build_comsol_model',lambda client,cfg,mesh:Model())
    monkeypatch.setattr(base,'_set_mode_count',lambda model,n:setattr(model,'_lnlt_mode_count',n))
    def extract(model,cfg,wl,width):
        if wl==1548 and model.params['nshift']=='ne_sell(lam_um)': raise RuntimeError('primary deliberately failed')
        x=np.linspace(-2e-6,2e-6,5); y=np.linspace(-1e-6,1e-6,5)
        return {'neff':1.9,'Ey':np.ones((5,5)),'y':x,'z':y}
    monkeypatch.setattr(base,'extract_selected_mode',extract)
    t=Task.from_dict({'material':'LN','crystal_cut':'z','cladding':'AIR','geometry':{'thickness_nm':600,'etch_depth_nm':300,'top_width_nm':1800},'backend':'comsol'})
    b=ComsolBackend(t,tmp_path); assert len(starts)==1
    with pytest.raises(base.PredictionUnavailable): b.solve(t.geometry,'TM0',1548)
    b.solve(t.geometry,'TM0',1550)
    b.solve(t.geometry,'TM0',1548)
    assert not b.failures; assert b.cost['mode_candidates']==20+40+20+40
    assert b.cost['solver_calls']==4
    model,cfg=next(iter(b.models.values())); assert model.params['nshift']=='ne_sell(lam_um)'; assert cfg.crystal_cut=='z'
    assert [r['wavelength_nm'] for r in b.attempt_log]==[1548,1548,1550,1548]
    b.close(); assert (tmp_path/'solver_attempts.json').exists()

def test_budget_cancellation_is_not_caught_by_legacy_retries(monkeypatch):
    class Model:
        def __init__(self): self.params={}
        def parameter(self,k,v): self.params[k]=v
    m=Model()
    monkeypatch.setattr(base,'_solve_once_and_select',lambda *args:(_ for _ in ()).throw(BudgetExceeded('stop')))
    with pytest.raises(BudgetExceeded): base._solve_with_prediction(m,base.Config(),base.ScanPoint(600,1800,300,1550),{})
    assert m.params['nshift']=='ne_sell(lam_um)'
