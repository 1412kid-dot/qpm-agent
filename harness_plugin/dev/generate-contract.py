"""Rebuild packaged guidance from the Python contract, before build/pack."""
import json,pathlib,sys,re,os
root=pathlib.Path(__file__).resolve().parents[1]
core_root=pathlib.Path(os.environ.get('QPM_CORE_ROOT',root.parent)).resolve()
sys.path.insert(0,str(core_root))
from qpm_simulation.conventions import describe_conventions,templates,crystal_convention
from qpm_simulation.task_schema import task_schema,study_schema
from qpm_simulation.extension_examples import example
from qpm_simulation import describe_capabilities
out=root/'skills/qpm-design';out.mkdir(parents=True,exist_ok=True)
def write(name,value): (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
write('task.schema.json',task_schema());write('study.schema.json',study_schema());write('templates.json',templates())
capabilities=describe_capabilities()
# Keep the model-facing acceptance summary aligned with the public release
# scope. Raw private evaluation counts and detailed COMSOL cases stay in the
# maintainer's evidence tree, not in Skill context.
validation=capabilities.get('validation',{})
physics=validation.get('physics',{})
capabilities['validation']={
 'release':validation.get('release'),
 'physics':{'representative_acceptance_passed':physics.get('representative_acceptance_passed'),
            'scope':'代表性物理检查通过；不代表所有材料、晶向、几何及非线性过程均已验证。'},
 'plugin_workflow':'插件当前已实现范围内的工作流已完成初步验收。',
 'future_scope':'超出当前插件能力范围的语义任务和更完整的自主研究流程将在后续版本扩展。'
}
write('capabilities.json',capabilities)
write('conventions.json',{name:describe_conventions(name)['content'] for name in ('crystal','geometry','wavelengths','metrics','routing','extensions','research','quality','plotting')})
example_root=root/'examples/extensions';example_root.mkdir(parents=True,exist_ok=True)
e=example()
(example_root/'signal_idler_gvm.py').write_text(e['code'],encoding='utf-8')
(example_root/'test_signal_idler_gvm.py').write_text(e['tests'],encoding='utf-8')
(example_root/'metadata.json').write_text(json.dumps(e['metadata'],indent=2,ensure_ascii=False),encoding='utf-8')
rows=['| 切向 | 薄膜法向（晶体） | 传播方向（晶体） | lab x/y/z对应晶轴 | 默认模式 | 默认主要电场 |',
      '|---|---|---|---|---|---|']
for cut in ('x','z'):
    c=crystal_convention(cut)
    rows.append(f"| {cut} 切 | {c['film_normal_crystal']} | {c['propagation_crystal']} | {'/'.join(c['lab_to_crystal'].values())} | {c['default_mode']} | 晶体 {c['default_dominant_electric_field_crystal']} |")
body='\n'.join(rows)+'\n\n仿真 lab x 水平、lab y 竖直（膜法向）、lab z 传播。非默认传播方向不支持，不能仅改切向名称代替旋转材料模型。'
skill=out/'SKILL.md';text=skill.read_text(encoding='utf-8')
text=re.sub(r'<!-- BEGIN GENERATED CRYSTAL -->[\s\S]*?<!-- END GENERATED CRYSTAL -->',lambda m:'<!-- BEGIN GENERATED CRYSTAL -->\n'+body+'\n<!-- END GENERATED CRYSTAL -->',text)
skill.write_text(text,encoding='utf-8')
print('Generated skill conventions, five task templates and task schema from Python source.')
import shutil
for filename in ('study_shg_demo.json','study_evaluate.json','study_verify.json','shg_type_ii_backward.json'):
    source=core_root/'examples'/filename
    if source.is_file(): shutil.copyfile(source,root/'examples'/filename)
