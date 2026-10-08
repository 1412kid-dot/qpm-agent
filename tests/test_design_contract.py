import copy,json,math
from pathlib import Path
import pytest
from qpm_simulation import validate_task,plan_task
from qpm_simulation.conventions import templates,crystal_convention,describe_conventions
from qpm_simulation.task_schema import task_schema
from qpm_simulation.extensions import validate_analysis,result_context,export_analysis

BASE=templates()['fixed']['task']

def test_templates_and_generated_schema_match():
    directory=Path(__file__).resolve().parents[1]/'harness_plugin/skills/qpm-design'
    assert json.loads((directory/'task.schema.json').read_text(encoding='utf-8'))==task_schema()
    assert json.loads((directory/'templates.json').read_text(encoding='utf-8'))==templates()
    conventions=json.loads((directory/'conventions.json').read_text(encoding='utf-8'))
    for topic,value in conventions.items(): assert value==describe_conventions(topic)['content']
    for item in templates().values():
        plan=plan_task(item['task'])
        assert plan['solver_started'] is False
        assert not set(plan['task']['required_metrics'])-set(plan['interpretation']['metrics']['available'])
    assert 'crystal_cut' in task_schema()['properties']

@pytest.mark.parametrize('cut,axis,mode',[('x','Y','TE0'),('z','X','TM0')])
def test_axes_and_explicit_provenance(cut,axis,mode):
    task=copy.deepcopy(BASE);task.update(crystal_cut=cut,propagation_axis=axis)
    plan=plan_task(task);c=plan['interpretation']['crystal']
    assert c==crystal_convention(cut)
    assert c['propagation_crystal']==axis and c['default_mode']==mode
    assert plan['task']['modes']['signal']==mode
    assert plan['interpretation']['wavelengths']['roles_nm'][3]=={'pump':775,'signal':1550,'idler':1550}
    assert 'center_nm' in plan['interpretation']['explicit_input_fields']
    assert 'length_mm' in plan['interpretation']['defaulted_fields']

@pytest.mark.parametrize('change',[
    {'propagation_axis':'X'}, {'required_metrics':['absolute_pair_rate']},
    {'required_metrics':['pmf']}, {'modes':{'signal':'TM0'},'required_metrics':['normalized_efficiency']},
    {'process':'SPDC','required_metrics':['normalized_efficiency']},
    {'wavelength_axes_nm':{'pump_nm':775}}, {'objective':{'metric':'gvm','pair':['signal','idler']}},
    {'modes':{'TE':'TE0'}}, {'objective':{'metric':'gvd','verification_abs_tolerance':-1}}])
def test_requests_cannot_silently_disappear(change):
    with pytest.raises(ValueError): validate_task(dict(copy.deepcopy(BASE),**change))

def test_non_default_mode_linear_only_and_nondefault_length():
    task=copy.deepcopy(BASE);task.update(required_metrics=['gvd','period'],modes={'pump':'TM0','signal':'TM0','idler':'TM0'},length_mm=9,length_defaulted=True)
    plan=plan_task(task)
    assert 'normalized_efficiency' in plan['interpretation']['capability_gaps']
    assert 'length defaulted to 9 mm' in plan['task']['assumptions']
    assert 'length defaulted to 5 mm' not in plan['task']['assumptions']

def metric(value=2,valid=True):
    return {'metrics':[{'name':'new_metric','value':value,'valid':valid,'unit':'ps/m','definition':'signed beta1 difference *1000','source':'signal_beta1_ps_mm, idler_beta1_ps_mm'}]}

def test_analysis_contract_and_missing_points(tmp_path):
    assert validate_analysis(metric(None,False))
    for invalid in (metric(None),metric(math.nan),metric(math.inf),{'metrics':[{'name':'x','value':1}]}, {'solver':{}}, {'metrics':[]}):
        with pytest.raises(ValueError): validate_analysis(invalid)
    curve={'curves':[{'name':'signed_gvm','x':[1530,1550,1570],'y':[1,None,3],'x_unit':'nm','y_unit':'ps/m','definition':'signal-idler beta1*1000'}]}
    assert validate_analysis(curve)['curves'][0]['valid']==[True,False,True]
    with pytest.raises(ValueError): validate_analysis({'curves':[dict(curve['curves'][0],valid=[True,True,True])]})
    context=result_context({'task':BASE,'validation':{'data_kind':'SYNTHETIC DEMO','scientifically_validated':False},'cost':{},'failures':['missing sample'],'curves':[{'valid':False,'period_um':None}]},tmp_path/'source.json')
    exported=export_analysis(curve,tmp_path/'extension-output',context,{'id':'metric','digest':'test'})
    assert exported['solver_started'] is False and exported['validation']['scientifically_validated'] is False
    assert exported['source_failures']==['missing sample']
    assert Path(exported['figures'][0]).is_file()

def test_table_contract():
    table={'tables':[{'name':'candidates','columns':['width_nm','value'],'units':{'width_nm':'nm','value':'ps/m'},'rows':[{'width_nm':1200,'value':None}]}]}
    assert validate_analysis(table)
    table['tables'][0]['units'].pop('value')
    with pytest.raises(ValueError): validate_analysis(table)
