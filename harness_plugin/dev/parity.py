"""Compare the packaged protocol with the existing workflow on synthetic tasks."""
import json, os, subprocess, sys, pathlib, copy, numpy as np
root=pathlib.Path(__file__).resolve().parents[1]; core=root.parent
sys.path.insert(0,str(core))
from qpm_simulation import run_task
from qpm_simulation.reporting import clean
import uuid
out=root/'.fixtures'/'parity'/uuid.uuid4().hex;out.mkdir(parents=True,exist_ok=True)
for name in ('shg','search_gvd','sfg_2d','spdc_2d'):
    task=json.loads((root/'examples'/f'{name}.json').read_text(encoding='utf-8-sig'))
    if name=='search_gvd':
        task['max_additional_geometries']=0;task['wavelengths_nm']=[1550];task['budget']['max_geometry_count']=16
    direct=clean(run_task(copy.deepcopy(task),out/name/'direct'))
    proc=subprocess.run([sys.executable,'-u','-m','qpm_simulation.harness_worker'],input=json.dumps({'action':'run','task':task,'output':str(out/name/'worker')})+'\n',text=True,encoding='utf-8',capture_output=True,timeout=60,env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(core)})
    assert proc.returncode==0,proc.stderr+proc.stdout
    messages=[json.loads(s) for s in proc.stdout.splitlines()];assert messages[-1]['kind']=='result'
    worker=json.loads((out/name/'worker'/'result.json').read_text(encoding='utf-8'))
    for key in ('curves','tolerances','spectral','best_geometry'):
        assert direct.get(key)==worker.get(key),f'{name}: {key} differs'
    assert worker['cost']['solver_calls']<=direct['cost']['solver_calls']  # validated shared cache can avoid worker solves
    print(f'{name}: workflow/protocol numerical parity passed; figures={len(worker["figures"])}')
