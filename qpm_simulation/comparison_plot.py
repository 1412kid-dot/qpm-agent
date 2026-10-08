"""Read-only result comparison and bounded plotting DSL. No solver imports."""
import ast
import hashlib
import json
import math
from pathlib import Path
import numpy as np

UNITS={'thickness_sensitivity_m_inv_nm_inv':'m⁻¹/nm','wavelength_nm':'nm','period_um':'um','gvd_fs2_mm':'fs²/mm','beta1_ps_mm':'ps/mm','D_ps_nm_km':'ps/nm/km','gvm_abs_ps_m':'ps/m','gvm_signed_ps_m':'ps/m','normalized_efficiency_W_inv_m_inv2':'1/W/m²','process_coefficient_W_inv':'1/W'}

def script_options(code, preset):
    """Interpret a small Python-shaped language; never eval/exec user code."""
    if len(code)>20000: raise ValueError('script limit: 20000 characters')
    tree=ast.parse(code)
    if len(list(ast.walk(tree)))>2000: raise ValueError('script syntax limit')
    result=dict(preset)
    result['series']=list(result.get('series',[]))
    def literal(node):
        value=ast.literal_eval(node)
        if len(json.dumps(value))>20000: raise ValueError('literal size limit')
        return value
    allowed={'title','xlabel','ylabel','xlim','ylim','xticks','yticks','xscale','yscale','figsize','font','fontsize','legend_location','layout','formats','dpi','errors'}
    for statement in tree.body:
        if not isinstance(statement,ast.Expr) or not isinstance(statement.value,ast.Call): raise ValueError('only plot.option/plot.series calls supported')
        call=statement.value
        if not isinstance(call.func,ast.Attribute) or not isinstance(call.func.value,ast.Name) or call.func.value.id!='plot': raise ValueError('unsupported script API')
        if call.args or any(k.arg is None for k in call.keywords): raise ValueError('keyword literals only')
        values={k.arg:literal(k.value) for k in call.keywords}
        if call.func.attr=='option':
            if set(values)-allowed: raise ValueError('unsupported plot option')
            result.update(values)
        elif call.func.attr=='series': result['series'].append(values)
        else: raise ValueError('unsupported script API')
    return result

def render(sources,preset,output,script=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .reporting import write_json
    if script: preset=script_options(script,preset)
    permitted={'series','title','xlabel','ylabel','xlim','ylim','xscale','yscale','legend','legend_location','dpi','heatmap','xticks','yticks','errors','figsize','font','fontsize','layout','formats','metric_units','metric_definitions'}
    if set(preset)-permitted: raise ValueError('unsupported plot options: '+str(set(preset)-permitted))
    layout=preset.get('layout','overlay')
    if layout not in ('overlay','subplots','dual_axis','heatmaps'): raise ValueError('invalid layout')
    formats=preset.get('formats',['png'])
    if not formats or len(formats)>3 or any(f not in ('png','svg','pdf') for f in formats): raise ValueError('formats must be png/svg/pdf')
    dpi=preset.get('dpi',160); size=preset.get('figsize',[8,5])
    if not isinstance(dpi,(int,float)) or not 50<=dpi<=600 or len(size)!=2 or any(not isinstance(x,(int,float)) or not 1<=x<=20 for x in size): raise ValueError('invalid figure size/dpi')
    if not 5<=preset.get('fontsize',10)<=40: raise ValueError('invalid fontsize')
    loaded={}; hashes={}; warnings=[]
    if not sources or len(sources)>30: raise ValueError('1..30 sources required')
    for source in sources:
        raw=Path(source['file']).read_bytes()
        if len(raw)>128*1024*1024: raise ValueError('source exceeds 128 MiB')
        loaded[source['alias']]=json.loads(raw.decode('utf-8-sig')); hashes[source['alias']]=hashlib.sha256(raw).hexdigest()
    series=preset.get('series',[])
    heat=layout=='heatmaps' or preset.get('heatmap',False)
    if heat and not series: series=[{'source':alias} for alias in loaded]
    if not series or len(series)>30: raise ValueError('1..30 series required')
    n=len(series) if layout in ('subplots','heatmaps') else 1
    if n>12: raise ValueError('at most 12 panels')
    rc={'font.size':preset.get('fontsize',10),'font.family':preset.get('font',['Microsoft YaHei','SimHei','DejaVu Sans'])}
    figures=[]; units_by_axis={}; transformations=[]; drawn=0
    with plt.rc_context(rc):
        fig,axes=plt.subplots(n,1,figsize=(size[0],min(40,size[1]*n)),squeeze=False)
        twin=None
        try:
            for i,spec in enumerate(series):
                allowed={'source','run_id','metric','x','label','color','linestyle','marker','dataset','axis','kind','unit','scale','normalize','x_range','interpolate_nm','smooth_window'}
                if set(spec)-allowed: raise ValueError('unsupported series option')
                alias=spec.get('source',next(iter(loaded))); data=loaded.get(alias)
                if data is None: raise ValueError('unknown source alias '+alias)
                ax=axes[i if n>1 else 0,0]
                if layout=='dual_axis' and spec.get('axis','left')=='right':
                    if twin is None: twin=ax.twinx()
                    ax=twin
                if heat:
                    spectral=data.get('spectral')
                    if not spectral: warnings.append(alias+': heatmap unavailable'); continue
                    values=np.asarray(spectral['values'],float)
                    if not np.isfinite(values).any(): warnings.append(alias+': heatmap has no valid points');continue
                    drawn+=1
                    image=ax.pcolormesh(spectral['x_nm'],spectral['y_nm'],np.ma.masked_invalid(values),shading='auto')
                    fig.colorbar(image,ax=ax,label=spectral['metric']); ax.set(xlabel=spectral['x_label'],ylabel=spectral['y_label'],title=spec.get('label',alias)); continue
                metric=spec.get('metric'); xkey=spec.get('x','wavelength_nm')
                rows=data.get('curves',[]) if spec.get('dataset','nominal')=='nominal' else data.get('tolerances',{}).get(spec['dataset'],[])
                if len(rows)>100000: raise ValueError('series point limit: 100000')
                if not rows or not any(metric in r for r in rows): warnings.append(alias+': metric unavailable '+str(metric)); continue
                unit=next((v for k,v in UNITS.items() if metric.endswith(k)),None)
                unit=unit or data.get('metric_units',{}).get(metric) or preset.get('metric_units',{}).get(metric)
                if not unit: raise ValueError('register metric unit: '+metric)
                target=spec.get('unit',unit); factor=1.
                conversions={('um','nm'):1000.,('nm','um'):.001,('fs²/mm','ps²/m'):.001,('ps²/m','fs²/mm'):1000.}
                if target!=unit:
                    if (unit,target) not in conversions: raise ValueError('unsupported unit conversion')
                    factor=conversions[(unit,target)]
                if spec.get('normalize'): target='normalized'
                previous=units_by_axis.get(id(ax))
                if previous and previous!=target: raise ValueError('different units: use subplots or dual_axis')
                units_by_axis[id(ax)]=target
                x=np.asarray([r.get(xkey,np.nan) for r in rows],float); y=np.asarray([r.get(metric,np.nan) for r in rows],float)*factor
                y=np.where([r.get('valid',True) is not False for r in rows],y,np.nan)
                scale=spec.get('scale',1.)
                if not isinstance(scale,(int,float)) or not math.isfinite(scale): raise ValueError('invalid series scale')
                y*=scale
                if spec.get('normalize'):
                    denominator=np.nanmax(np.abs(y))
                    if denominator: y/=denominator
                    transformations.append({'source':alias,'metric':metric,'normalize':'max_absolute'}); target='normalized'
                if 'x_range' in spec:
                    bounds=spec['x_range']
                    if not isinstance(bounds,list) or len(bounds)!=2 or not np.isfinite(bounds).all() or bounds[0]>=bounds[1]: raise ValueError('invalid x_range')
                    mask=(x>=bounds[0])&(x<=bounds[1]); x=x[mask]; y=y[mask]
                if 'interpolate_nm' in spec or 'smooth_window' in spec:
                    grid=spec.get('interpolate_nm'); window=spec.get('smooth_window',1)
                    if not isinstance(window,int) or not 1<=window<=51: raise ValueError('smooth_window must be 1..51')
                    if grid is not None and (not isinstance(grid,list) or len(grid)>100000 or not np.isfinite(grid).all() or np.any(np.diff(grid)<=0)): raise ValueError('invalid interpolation grid')
                    newx=[];newy=[];valid=np.isfinite(x)&np.isfinite(y)
                    breaks=np.flatnonzero(np.diff(np.r_[False,valid,False]))
                    for start,end in zip(breaks[::2],breaks[1::2]):
                        sx=x[start:end];sy=y[start:end]
                        if grid is not None:
                            if np.any(np.diff(sx)<=0): raise ValueError('interpolation requires ascending original points')
                            selected=np.asarray(grid);selected=selected[(selected>=sx[0])&(selected<=sx[-1])]
                            sy=np.interp(selected,sx,sy);sx=selected
                        if window>1:
                            sy=np.asarray([np.mean(sy[max(0,k-window//2):min(len(sy),k+window//2+1)]) for k in range(len(sy))])
                        newx.extend(sx);newy.extend(sy);newx.append(np.nan);newy.append(np.nan)
                    x=np.asarray(newx);y=np.asarray(newy);transformations.append({'source':alias,'metric':metric,'interpolate_nm':grid,'smooth_window':window})
                if not (np.isfinite(x)&np.isfinite(y)).any(): warnings.append(alias+': no valid points '+metric);continue
                drawn+=1
                style={k:spec[k] for k in ('color','linestyle','marker') if k in spec}
                style.setdefault('marker','o')
                if spec.get('kind')=='scatter': style.pop('linestyle',None); ax.scatter(x,y,label=spec.get('label',alias+': '+metric),**style)
                else: ax.plot(x,y,label=spec.get('label',alias+': '+metric),**style)
                if preset.get('errors') and (spec.get('normalize') or any(k in spec for k in ('x_range','interpolate_nm','smooth_window'))): warnings.append(alias+': tolerance overlay skipped for transformed series; use original series to show endpoints')
                if preset.get('errors') and spec.get('dataset','nominal')=='nominal' and 'x_range' not in spec and not spec.get('normalize') and 'interpolate_nm' not in spec and 'smooth_window' not in spec:
                    for dimension in data.get('task',{}).get('tolerances_nm',{}):
                        endpoints=[v for k,v in data.get('tolerances',{}).items() if k.startswith(dimension) and isinstance(v,list)]
                        if len(endpoints)==2:
                            vals=[]
                            for endpoint in endpoints:
                                table={r.get(xkey):r.get(metric,np.nan) for r in endpoint}
                                vals.append(np.asarray([table.get(v,np.nan) for v in x],float)*factor*scale)
                            values=np.asarray(vals+[y]); lo=np.min(values,axis=0); hi=np.max(values,axis=0)
                            if preset['errors']=='envelope': ax.fill_between(x,lo,hi,alpha=.2)
                            else: ax.errorbar(x,y,yerr=[y-lo,hi-y],fmt='none',capsize=3)
                ax.set(xlabel=preset.get('xlabel',xkey),ylabel=preset.get('ylabel',target))
            if not drawn: raise ValueError('no valid metrics available for plotting')
            allaxes=list(axes[:,0])+([twin] if twin else [])
            for ax in allaxes:
                for key in ('xlim','ylim','xticks','yticks'):
                    if key in preset:
                        values=preset[key]
                        if not isinstance(values,list) or len(values)>200 or not np.isfinite(values).all(): raise ValueError('invalid '+key)
                        if key.endswith('lim') and (len(values)!=2 or values[0]>=values[1]): raise ValueError('invalid '+key)
                        getattr(ax,'set_'+key)(values)
                for key in ('xscale','yscale'):
                    if key in preset:
                        if preset[key] not in ('linear','log','symlog'): raise ValueError('invalid scale')
                        getattr(ax,'set_'+key)(preset[key])
                if preset.get('legend',True) and ax.get_legend_handles_labels()[0]: ax.legend(loc=preset.get('legend_location','best'))
                ax.grid(alpha=.25)
            provenance={alias:data.get('validation',{}) for alias,data in loaded.items()}
            fig.suptitle(preset.get('title','QPM comparison')); fig.text(.01,.005,' | '.join(alias+': '+str(v.get('data_kind','unknown')) for alias,v in provenance.items()),fontsize=7)
            fig.tight_layout(rect=[0,.04,1,.95]); root=Path(output); root.mkdir(parents=True,exist_ok=True)
            for fmt in formats:
                file=root/('comparison.'+fmt); fig.savefig(file,dpi=dpi); figures.append(str(file))
        finally: plt.close(fig)
    for source in sources:
        if hashlib.sha256(Path(source['file']).read_bytes()).hexdigest()!=hashes[source['alias']]: raise ValueError('source changed during plotting')
    manifest={'renderer':'comparison-v1','preset':preset,'sources':sources,'source_hashes':hashes,'validation':provenance,'metric_definitions':preset.get('metric_definitions',{}),'warnings':warnings,'transformations':transformations,'figures':figures}
    write_json(root/'plot-manifest.json',manifest)
    if not figures: raise ValueError('no plot outputs')
    if script: (root/'plot-script.py').write_text(script,encoding='utf-8')
    return {'figures':figures,'manifest':str(root/'plot-manifest.json'),'warnings':warnings}

def main():
    import sys
    # Bound worker working-set memory independently of simulation budgets.
    import threading,time,os
    def watch_memory():
        if sys.platform!='win32': return
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        kernel=ctypes.WinDLL('kernel32'); kernel.GetCurrentProcess.restype=wintypes.HANDLE
        psapi=ctypes.WinDLL('psapi'); psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        while True:
            counters=Counters();counters.cb=ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb) and counters.WorkingSetSize>1024*1024*1024:
                print(json.dumps({'error':'plot memory limit exceeded: 1 GiB'}),flush=True);os._exit(3)
            time.sleep(.25)
    threading.Thread(target=watch_memory,daemon=True).start()
    request=json.loads(sys.stdin.readline())
    try:
        result=render(**request); print(json.dumps({'result':result},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False)); sys.exit(1)

if __name__=='__main__': main()
