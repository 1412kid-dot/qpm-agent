from pathlib import Path
import hashlib,json
import numpy as np
import pytest
import LN_LT_COMSOL_SHG_QPM_Automation as base
import LN_LT_Nonlinear_Efficiency as legacy
from qpm_simulation.materials import refractive_indices
from qpm_simulation.physics import normalized_efficiency,effective_area
from scipy.constants import c

@pytest.mark.parametrize('material',['LN','LT'])
def test_material_regression(material):
    for wavelength in (775,1550,2000): assert refractive_indices(material,wavelength)==pytest.approx(base.refractive_indices(material,wavelength),rel=1e-14)

@pytest.mark.parametrize('process',['SHG','SFG','DFG'])
def test_efficiency_formula_and_overlap_regression(process):
    w=np.array([775,1550,1550]); n=(2.1,2,2); area=1e-12; d=27*1e-12*2/np.pi; omega=2*np.pi*c/(w*1e-9)
    expected={'SHG':lambda:legacy.normalized_efficiency_shg(d,omega[0],n[0],n[1],area),
        'SFG':lambda:legacy.normalized_efficiency_sfg(d,omega[0],*n,area),
        'DFG':lambda:legacy.normalized_efficiency_dfg(d,omega[1],omega[2],*n,area)}[process]()
    assert normalized_efficiency(process,w,n,area,27)==pytest.approx(expected,rel=1e-14)
    x=np.linspace(-2e-6,2e-6,51); y=np.linspace(-1e-6,1e-6,41); xx,yy=np.meshgrid(x,y)
    e=np.exp(-(xx/1e-6)**2-(yy/1e-6)**2); mask=abs(yy)<.3e-6
    fields=(e*np.exp(1j*xx/1e-6),e,e if process=='SHG' else e*.8)
    records=[{'field':f,'x_m':x,'y_m':y,'material_mask':mask} for f in fields]
    assert effective_area(process,records)==pytest.approx(legacy.effective_area(process,fields,x,y,mask)['area_m2'],rel=1e-14)

def test_original_files_unchanged():
    audit=Path(__file__).resolve().parents[1]/'original_hashes.json'
    if not audit.exists(): pytest.skip('private original-file audit is not distributed with public source')
    manifest=json.loads(audit.read_text(encoding='utf-8'))
    if not all(Path(path).exists() for path in manifest['original_files']): pytest.skip('local original-file audit not available on this machine')
    for path,expected in manifest['original_files'].items(): assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected,path
