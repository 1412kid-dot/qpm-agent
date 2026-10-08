"""Validated declarative plot presets. Never invoke a solver or mutate source data."""
from pathlib import Path
import json
import hashlib
import numpy as np

def render(run_dir, preset, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .reporting import write_json
    allowed={'series','title','xlabel','ylabel','xlim','ylim','xscale','yscale','legend','dpi','heatmap','xticks','yticks','errors'}
    if not isinstance(preset,dict) or set(preset)-allowed: raise ValueError('unsupported plot options')
    if not isinstance(preset.get('dpi',160),(int,float)) or not 50<=preset.get('dpi',160)<=600: raise ValueError('dpi must be 50..600')
    for key in ('xscale','yscale'):
        if key in preset and preset[key] not in ('linear','log','symlog'): raise ValueError('invalid axis scale')
    for key in ('xlim','ylim'):
        if key in preset and (not isinstance(preset[key],list) or len(preset[key])!=2 or not np.isfinite(preset[key]).all() or preset[key][0]>=preset[key][1]): raise ValueError('invalid axis range')
    for key in ('xticks','yticks'):
        if key in preset and (not isinstance(preset[key],list) or len(preset[key])>200 or not np.isfinite(preset[key]).all()): raise ValueError('invalid ticks')
    for key in ('errors','heatmap','legend'):
        if key in preset and not isinstance(preset[key],bool): raise ValueError('plot flag must be boolean')
    source=Path(run_dir)/'result.json'; raw=source.read_bytes(); result=json.loads(raw)
    root=Path(output); root.mkdir(parents=True,exist_ok=True)
    fig,ax=plt.subplots()
    try:
        if preset.get('heatmap'):
            data=result['spectral']
            im=ax.pcolormesh(data['x_nm'],data['y_nm'],np.ma.masked_invalid(np.asarray(data['values'],float)),shading='auto')
            fig.colorbar(im,ax=ax,label=data['metric'])
            ax.set(xlabel=data['x_label'],ylabel=data['y_label'])
        else:
            series=preset.get('series',[])
            if not isinstance(series,list) or not series or len(series)>30 or any(not isinstance(item,dict) or not isinstance(item.get('metric'),str) for item in series): raise ValueError('valid series required (1..30)')
            def unit(key):
                suffixes={'gvd_fs2_mm':'fs²/mm','beta1_ps_mm':'ps/mm','D_ps_nm_km':'ps/nm/km','period_um':'um','gvm_abs_ps_m':'ps/m','gvm_signed_ps_m':'ps/m','normalized_efficiency_W_inv_m_inv2':'1/W/m²','process_coefficient_W_inv':'1/W'}
                return next((v for k,v in suffixes.items() if key.endswith(k)),None)
            units={unit(s['metric']) for s in series}
            if None in units or len(units)!=1: raise ValueError('unknown or incompatible units; use separate plots')
            for spec in series:
                if set(spec)-{'metric','label','color','linestyle','dataset'}: raise ValueError('unsupported series option')
                rows=result.get('curves',[]) if spec.get('dataset','nominal')=='nominal' else result.get('tolerances',{}).get(spec['dataset'],[])
                if not isinstance(rows,list) or not rows: raise ValueError('dataset unavailable')
                if any(spec['metric'] not in row for row in rows): raise ValueError('metric unavailable')
                ax.plot([row['wavelength_nm'] for row in rows],np.asarray([row[spec['metric']] for row in rows],float),label=spec.get('label',spec['metric']),**{k:spec[k] for k in ('color','linestyle') if k in spec})
            ax.set(xlabel=preset.get('xlabel','Scan wavelength (nm)'),ylabel=preset.get('ylabel',next(iter(units))))
            if preset.get('errors'):
                metric=series[0]['metric']; rows=result['curves']; y=np.asarray([row[metric] for row in rows],float)
                for axis in result.get('task',{}).get('tolerances_nm',{}):
                    endpoints=[v for k,v in result.get('tolerances',{}).items() if k.startswith(axis) and isinstance(v,list)]
                    if len(endpoints)==2:
                        vals=np.asarray([[row[metric] for row in ep] for ep in endpoints]+[y.tolist()],float)
                        lo=np.min(vals,axis=0);hi=np.max(vals,axis=0)
                        ax.errorbar([row['wavelength_nm'] for row in rows],y,yerr=np.vstack([y-lo,hi-y]),fmt='none',capsize=4,label=axis+' endpoints')
            if preset.get('legend',True): ax.legend()
        for key in ('xlim','ylim','xscale','yscale','xticks','yticks'):
            if key in preset: getattr(ax,'set_'+key)(preset[key])
        ax.set_title(preset.get('title',result['validation']['data_kind'])); ax.grid(alpha=.3)
        fig.text(.01,.005,result['validation']['data_kind'],fontsize=8); fig.tight_layout(rect=[0,.03,1,1]); file=root/'plot.png'; fig.savefig(file,dpi=preset.get('dpi',160))
    finally: plt.close(fig)
    if source.read_bytes()!=raw: raise ValueError('source result changed')
    manifest={'preset':preset,'source':str(source),'source_sha256':hashlib.sha256(raw).hexdigest(),'validation':result['validation'],'renderer':'declarative-v1','units':list(units) if not preset.get('heatmap') else result['spectral']['metric']}
    write_json(root/'plot-manifest.json',manifest)
    return {'figures':[str(file)],'manifest':str(root/'plot-manifest.json')}
