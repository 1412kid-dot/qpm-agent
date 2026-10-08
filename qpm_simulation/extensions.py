"""Reviewed postprocessing contract. This is validation, not an OS sandbox."""
import json, math, re
from copy import deepcopy
from pathlib import Path
from .conventions import CONTRACT_VERSION, METRICS
from .reporting import write_json

def result_context(result, source):
    from .config import as_task
    # Input is a completed result envelope, not arbitrary paths or raw fields.
    as_task(result['task'])
    for key in ('validation','cost','failures'):
        if key not in result: raise ValueError('incomplete result envelope: '+key)
    return {'contract_version':CONTRACT_VERSION,'source_result_file':str(source),
            'metric_units':METRICS,**{k:deepcopy(result.get(k, [] if k in ('curves','failures') else {})) for k in
                ('task','validation','cost','failures','curves','tolerances','spectral','search')}}

def validate_analysis(value):
    if not isinstance(value,dict) or set(value)-{'metrics','tables','curves','notes'}: raise ValueError('postprocess output fields: metrics/tables/curves/notes only')
    # Do not silently replace NaN/Infinity with fabricated successes.
    json.dumps(value,allow_nan=False)
    if not any(value.get(k) for k in ('metrics','tables','curves')): raise ValueError('extension must return a metric, table or curve')
    names=set()
    def name(item):
        n=item.get('name')
        if not isinstance(n,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',n): raise ValueError('invalid extension output name')
        if n in names: raise ValueError('duplicate extension output name')
        names.add(n)
    for collection in ('metrics','tables','curves'):
        if not isinstance(value.get(collection,[]),list): raise ValueError('output collections must be arrays')
        for item in value.get(collection,[]):
            if not isinstance(item,dict): raise ValueError('output item must be object')
            name(item)
    for m in value.get('metrics',[]):
        if not {'name','value','unit','definition','valid','source'}<=set(m): raise ValueError('metric needs value/unit/definition/valid/source')
        if not all(isinstance(m[k],str) and m[k].strip() for k in ('unit','definition','source')): raise ValueError('metric needs nonempty unit/definition/source')
        if type(m['valid']) is not bool: raise ValueError('metric valid must be boolean')
        if m['value'] is not None and (type(m['value']) not in (int,float) or not math.isfinite(m['value'])): raise ValueError('metric value must be finite number or null')
        if m['valid'] and m['value'] is None: raise ValueError('missing metric cannot be valid')
    for table in value.get('tables',[]):
        if not {'columns','units','rows'}<=set(table): raise ValueError('table needs columns/units/rows')
        columns=table['columns']
        if not isinstance(columns,list) or not columns or len(set(columns))!=len(columns) or any(not isinstance(c,str) for c in columns): raise ValueError('invalid table columns')
        if not isinstance(table['units'],dict) or set(table['units'])!=set(columns) or any(not isinstance(u,str) or not u for u in table['units'].values()): raise ValueError('table units must cover all columns')
        if not isinstance(table['rows'],list) or any(not isinstance(r,dict) or set(r)!=set(columns) for r in table['rows']): raise ValueError('invalid table rows')
    for curve in value.get('curves',[]):
        if not {'x','y','x_unit','y_unit','definition'}<=set(curve): raise ValueError('curve needs x/y units and definition')
        if not all(isinstance(curve[k],str) and curve[k].strip() for k in ('x_unit','y_unit','definition')): raise ValueError('curve unit/definition required')
        x,y=curve['x'],curve['y']
        if not isinstance(x,list) or not isinstance(y,list) or not x or len(x)!=len(y): raise ValueError('invalid curve shape')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in x): raise ValueError('curve x must be finite')
        if any(v is not None and (type(v) not in (int,float) or not math.isfinite(v)) for v in y): raise ValueError('curve y finite or null')
        valid=curve.get('valid',[v is not None for v in y])
        if not isinstance(valid,list) or len(valid)!=len(y) or any(type(v) is not bool for v in valid): raise ValueError('invalid curve mask')
        if any(ok and v is None for ok,v in zip(valid,y)): raise ValueError('missing curve point cannot be valid')
        curve['valid']=valid
    if not isinstance(value.get('notes',[]),list) or any(not isinstance(n,str) for n in value.get('notes',[])): raise ValueError('notes must be strings')
    return value

def export_analysis(value, output, context, extension):
    import csv
    value=validate_analysis(value);root=Path(output)
    if root.exists() and any(root.iterdir()): raise ValueError('analysis output is not empty; refusing overwrite')
    root.mkdir(parents=True,exist_ok=True)
    figures=[];tables=[]
    for table in value.get('tables',[]):
        file=root/(table['name']+'.csv')
        with file.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=table['columns']);writer.writeheader();writer.writerows(table['rows'])
        tables.append(str(file))
    if value.get('curves'):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        for curve in value['curves']:
            fig,ax=plt.subplots();ax.plot(curve['x'],[v if ok else math.nan for ok,v in zip(curve['valid'],curve['y'])],'o-')
            ax.set(xlabel=curve['x_unit'],ylabel=curve['y_unit'],title=context['validation']['data_kind']+' / '+curve['name']);ax.grid(alpha=.3)
            fig.tight_layout();file=root/(curve['name']+'.png');fig.savefig(file,dpi=140);plt.close(fig);figures.append(str(file))
    envelope={'extension':extension,'source_result_file':context['source_result_file'],'validation':context['validation'],
              'source_failures':context['failures'],'analysis':value,'figures':figures,'tables':tables,'solver_started':False}
    write_json(root/'analysis.json',envelope)
    return {'analysis_file':str(root/'analysis.json'),**envelope}
