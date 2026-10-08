import hashlib,json
import pytest
from qpm_simulation.comparison_plot import render,script_options

def test_cross_run(tmp_path):
    sources=[]
    for i in range(2):
        f=tmp_path/f'r{i}.json'
        f.write_text(json.dumps({'curves':[{'wavelength_nm':1500+j*(10+i),'signal_gvd_fs2_mm':j if j!=1 else None} for j in range(3)],'validation':{'data_kind':'demo'}}))
        sources.append({'alias':f'r{i}','file':str(f)})
    before=[hashlib.sha256(open(s['file'],'rb').read()).hexdigest() for s in sources]
    result=render(sources,{'series':[{'source':s['alias'],'metric':'signal_gvd_fs2_mm'} for s in sources],'formats':['png','svg','pdf']},tmp_path/'plots',"plot.option(title='中文比较')")
    assert len(result['figures'])==3
    assert before==[hashlib.sha256(open(s['file'],'rb').read()).hexdigest() for s in sources]

@pytest.mark.parametrize('code',["import os","open('secret')","plot.__class__()","plot.option(title=open('secret'))","while True: pass","plot.series(**{})"])
def test_script_rejects(code):
    with pytest.raises((ValueError,TypeError)): script_options(code,{})


def test_tolerance_units_heatmap_missing(tmp_path):
    f=tmp_path/'source.json'
    rows=[{'wavelength_nm':1500+i*10,'period_um':10+i,'thickness_sensitivity_m_inv_nm_inv':i,'custom':i} for i in range(3)]
    f.write_text(json.dumps({'curves':rows,'tolerances':{'thickness_plus':[{**r,'period_um':r['period_um']+1} for r in rows],'thickness_minus':[{**r,'period_um':r['period_um']-1} for r in rows]},'task':{'tolerances_nm':{'thickness':5}},'validation':{'data_kind':'demo'},'spectral':{'x_nm':[1,2],'y_nm':[3,4],'values':[[1,None],[2,3]],'metric':'PMF','x_label':'x','y_label':'y'}}))
    sources=[{'alias':'a','file':str(f)}]
    result=render(sources,{'series':[{'metric':'period_um','unit':'nm'},{'metric':'missing'}],'errors':'envelope'},tmp_path/'error')
    assert result['warnings']
    render(sources,{'series':[{'metric':'thickness_sensitivity_m_inv_nm_inv'},{'metric':'custom'}],'layout':'subplots','metric_units':{'custom':'arb'},'metric_definitions':{'custom':'test metric'}},tmp_path/'extended')
    render(sources,{'layout':'heatmaps'},tmp_path/'heat')


def test_transformations_keep_gaps(tmp_path):
    f=tmp_path/'input.json'
    f.write_text(json.dumps({'curves':[{'wavelength_nm':i,'period_um':v} for i,v in enumerate([1,2,None,4,5])],'validation':{'data_kind':'demo'}}))
    r=render([{'alias':'a','file':str(f)}],{'series':[{'metric':'period_um','interpolate_nm':[0,.5,1,1.5,2,2.5,3,3.5,4],'smooth_window':3}]},tmp_path/'out')
    manifest=json.loads(open(r['manifest']).read())
    assert manifest['transformations'][0]['smooth_window']==3
