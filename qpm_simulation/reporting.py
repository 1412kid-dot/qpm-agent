from pathlib import Path
import json, math
import numpy as np

def clean(value):
    if isinstance(value,dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [clean(v) for v in value]
    if isinstance(value,np.ndarray): return clean(value.tolist())
    if isinstance(value,(float,np.floating)): return float(value) if math.isfinite(value) else None
    if isinstance(value,np.integer): return int(value)
    return value

def write_json(path,value):
    import os,uuid
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        f.write(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False)); f.flush(); os.fsync(f.fileno())
    os.replace(temporary,path)

def plots(result,root):
    from .plotting import plots as render
    return render(result,root)

def save_result(result,root):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    version=Path(__file__).with_name('source-version.json')
    if version.exists(): result['validation']['source_version']=json.loads(version.read_text(encoding='utf-8'))
    write_json(root/'result.json',result)
    result['figures']=plots(result,root); result['raw_data_dir']=result['task']['replay_dir'] if result['task']['backend']=='replay' else str(root/'cache')
    write_json(root/'result.json',result)
    text=f"# QPM run\n\nData: {result['validation']['data_kind']}\n\nStatus: {result['validation']['status']}\n\nLength: {result['task']['length_mm']} mm. Classical coefficients are not conversion percentages. SPDC PMF is not an absolute pair rate.\n\n"
    text+='## Assumptions\n\n'+'\n'.join('- '+x for x in result['task']['assumptions'])
    text+='\n\n## Cost\n\n```json\n'+json.dumps(result['cost'],indent=2)+'\n```\n'
    text+='\n## Validation and outstanding items\n\n```json\n'+json.dumps(clean(result['validation']),indent=2,ensure_ascii=False)+'\n```\n'
    (root/'report.md').write_text(text,encoding='utf-8')
    return result

def postprocess(run_dir):
    root=Path(run_dir); result=json.loads((root/'result.json').read_text(encoding='utf-8'))
    # JSON null values convert back to NaN for numerical plotting.
    for rows in [result.get('curves',[])]+[v for v in result.get('tolerances',{}).values() if isinstance(v,list)]:
        for row in rows:
            for k,v in row.items():
                if v is None: row[k]=float('nan')
    return save_result(result,root)


def postprocess_export(run_dir,output):
    import hashlib
    source=Path(run_dir)/'result.json';raw=source.read_bytes();result=json.loads(raw.decode('utf-8-sig'))
    root=Path(output);root.mkdir(parents=True,exist_ok=True)
    for rows in [result.get('curves',[])]+[v for v in result.get('tolerances',{}).values() if isinstance(v,list)]:
        for row in rows:
            for key,value in row.items():
                if value is None: row[key]=float('nan')
    figures=plots(result,root)
    write_json(root/'processing-manifest.json',{'source_result_file':str(source),'source_sha256':hashlib.sha256(raw).hexdigest(),'processor':'qpm_simulation/0.2.0-history-v1','validation':result.get('validation'),'figures':figures})
    if source.read_bytes()!=raw: raise ValueError('source result changed during postprocessing')
    return {'source_result_file':str(source),'result_file':str(source),'figures':figures,'output_dir':str(root),'manifest':str(root/'processing-manifest.json'),'validation':result.get('validation')}
