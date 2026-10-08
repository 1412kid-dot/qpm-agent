from pathlib import Path
import numpy as np
def plots(result,root):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import pandas as pd
    root=Path(root); figs=root/'figures'; figs.mkdir(exist_ok=True); files=[]
    rows=result.get('curves',[])
    if rows:
        frame=pd.DataFrame(rows); frame.to_csv(root/'curves.csv',index=False)
        specs=[('period_um','Required QPM period (um)'),('normalized_efficiency_W_inv_m_inv2','Normalized efficiency (1/W/m²)'),
          ('process_coefficient_W_inv',f"Process coefficient (1/W), L={result['task']['length_mm']} mm"),('gvm_abs_ps_m','|GVM| (ps/m)'),('gvm_signed_ps_m','Signed GVM (ps/m)')]
        if 'thickness_sensitivity_m_inv_nm_inv' in frame: specs.append(('thickness_sensitivity_m_inv_nm_inv','d(delta beta)/dt (1/m/nm)'))
        for metric,label in specs:
            if metric not in frame or not np.isfinite(frame[metric].astype(float)).any(): continue
            fig,ax=plt.subplots(); x=frame.wavelength_nm; y=frame[metric].astype(float)
            ax.plot(x,y,'o-',label='nominal')
            for index,axis in enumerate(result['task']['tolerances_nm']):
                endpoints=[v for k,v in result.get('tolerances',{}).items() if k.startswith(axis) and isinstance(v,list)]
                if len(endpoints)==2:
                    vals=np.array([y.to_numpy()]+[[r[metric] for r in ep] for ep in endpoints],float)
                    lo=np.min(vals,axis=0); hi=np.max(vals,axis=0)
                    ax.errorbar(x,y,yerr=np.vstack([y-lo,hi-y]),fmt='none',ecolor=['tab:orange','tab:green'][index%2],capsize=4,label=f'{axis} endpoints')
            ax.set(xlabel='Scan wavelength (nm)',ylabel=label,title=result['validation']['data_kind']); ax.grid(alpha=.3); ax.legend()
            fig.tight_layout(); path=figs/f'{metric}.png'; fig.savefig(path,dpi=140); plt.close(fig); files.append(str(path))
        for suffix,label in [('beta1_ps_mm','β1 (ps/mm)'),('gvd_fs2_mm','β2 / GVD (fs²/mm)'),('D_ps_nm_km','D (ps/nm/km)')]:
            if not any(np.isfinite(frame[f'{role}_{suffix}'].astype(float)).any() for role in ('pump','signal','idler')): continue
            fig,ax=plt.subplots()
            for role in ('pump','signal','idler'): ax.plot(frame.wavelength_nm,frame[f'{role}_{suffix}'],label=role)
            ax.set(xlabel='Scan wavelength (nm)',ylabel=label,title=result['validation']['data_kind']); ax.legend(); ax.grid(alpha=.3)
            fig.tight_layout(); path=figs/f'{suffix}.png'; fig.savefig(path,dpi=140); plt.close(fig); files.append(str(path))
        for name,rows in result.get('tolerances',{}).items():
            if isinstance(rows,list): pd.DataFrame(rows).to_csv(root/f'tolerance_{name}.csv',index=False)
    if 'spectral' in result:
        data=result['spectral']; pd.DataFrame(data['rows']).to_csv(root/'spectral.csv',index=False)
        fig,ax=plt.subplots(); array=np.array(data['values'],float)
        im=ax.pcolormesh(data['x_nm'],data['y_nm'],array,shading='auto'); fig.colorbar(im,ax=ax,label=data['metric'])
        ax.set(xlabel=data['x_label'],ylabel=data['y_label'],title=result['validation']['data_kind'])
        fig.tight_layout(); path=figs/'spectral_heatmap.png'; fig.savefig(path,dpi=140); plt.close(fig); files.append(str(path))
    if 'search' in result:
        rows=[dict(r['geometry'],value=r['value'],valid=r['valid'],stage=r['stage'],failure=r['failure']) for r in result['search']['observations']]
        pd.DataFrame(rows).to_csv(root/'candidates.csv',index=False)
    return files

