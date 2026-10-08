import argparse,json
from pathlib import Path
from . import describe_capabilities,describe_conventions,task_schema,validate_task,plan_task,run_task,search,postprocess
from .environment import doctor
from .reporting import clean

def main():
    p=argparse.ArgumentParser(description='QPM simulation tools (no implicit COMSOL execution)')
    p.add_argument('command',choices=['capabilities','conventions','schema','doctor','validate','plan','run','search','postprocess','estimate','resume','license-trial'])
    p.add_argument('input',nargs='?'); p.add_argument('--output'); p.add_argument('--backend',choices=['demo','comsol','replay'])
    p.add_argument('--workspace',help='Results root for validated cache and historical time estimates')
    p.add_argument('--budget',help='JSON cumulative budget overrides for resume, subject to caller caps')
    p.add_argument('--paths',help='JSON containing comsol_bin; Python executable is selected by the caller')
    a=p.parse_args()
    if a.command=='capabilities': result=describe_capabilities()
    elif a.command=='conventions': result=describe_conventions(a.input or 'overview')
    elif a.command=='schema': result=task_schema()
    elif a.command=='doctor': result=doctor(a.input)
    elif a.command=='resume':
        root=Path(a.input); task=json.loads((root/'task.json').read_text(encoding='utf-8'))
        if a.budget: task['budget']={**task.get('budget',{}),**json.loads(a.budget)}
        result=run_task(task,root)
    elif a.command=='postprocess': result=postprocess(a.input)
    else:
        if not a.input: p.error('task JSON path required')
        task=json.loads(Path(a.input).read_text(encoding='utf-8-sig'))
        if a.paths:
            paths=json.loads(Path(a.paths).read_text(encoding='utf-8-sig'))
            if paths.get('comsol_bin'): task.setdefault('solver',{})['comsol_bin']=paths['comsol_bin']
        if a.backend: task['backend']=a.backend
        if a.command=='license-trial':
            task['backend']='comsol'; task['task_kind']='fixed'
            from .config import as_task
            from .backends import create_backend
            t=as_task(task); root=Path(a.output or Path(__file__).resolve().parents[1]/'outputs'/'license_trial'); root.mkdir(parents=True,exist_ok=True)
            b=create_backend(t,root)
            try:
                r=b.solve(t.geometry,t.modes['signal'],t.center_nm); result={'neff':float(r['neff']),'cost':b.summary(),'scientifically_validated':False}
            finally: b.close()
        elif a.command=='validate': result=validate_task(task).to_dict()
        elif a.command=='estimate':
            from .planning import estimate_task
            result=estimate_task(task,a.workspace)
        elif a.command=='plan': result=plan_task(task)
        elif a.command=='search': result=search(task,a.output)
        else: result=run_task(task,a.output)
    if a.command in {'run','search','postprocess'}: result={k:result.get(k) for k in ('raw_data_dir','validation','cost','failures','figures')}
    print(json.dumps(clean(result),ensure_ascii=False,indent=2))
if __name__=='__main__': main()
