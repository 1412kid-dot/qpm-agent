"""Model-readable scientific contract, derived from the implemented Python functions."""
from copy import deepcopy
from .crystal import mapping, default_mode, nonlinear_coefficient

CONTRACT_VERSION = 'qpm-design/1'
METRICS = {'period':'um','beta1':'ps/mm','gvd':'fs²/mm','D':'ps/nm/km',
           'gvm':'ps/m','thickness_sensitivity':'m^-1/nm',
           'normalized_efficiency':'W^-1 m^-2','process_coefficient':'W^-1',
           'pmf':'dimensionless'}

def crystal_convention(cut):
    axes=mapping(cut)
    return {'crystal_cut':cut,'lab_axes':{'x':'horizontal','y':'vertical / film normal','z':'propagation'},
            'lab_to_crystal':axes,'film_normal_crystal':axes['lab_y'],
            'propagation_crystal':axes['lab_z'],'default_mode':default_mode(cut),
            'default_dominant_electric_field_crystal':'Z',
            'TE0_definition':'fundamental quasi-TE; dominant lab Ex',
            'TM0_definition':'fundamental quasi-TM; dominant lab Ey',
            'nonlinear_approximation':'dominant-component scalar d33, not a full tensor model'}

def metric_availability(t):
    d,model=nonlinear_coefficient(t.material,t.crystal_cut,list(t.modes.values()),t.nonlinear_coefficient_pm_v)
    if t.process=='SHG' and t.modes['signal']!=t.modes['idler']: d=None; model='mixed_degenerate_modes_need_tensor_model'
    available=['period','beta1','gvd','D','gvm'] if t.task_kind!='spectral_2d' else ['pmf' if t.process=='SPDC' else 'period']
    if t.task_kind!='spectral_2d' and (t.objective['metric']=='thickness_sensitivity' or 'thickness_sensitivity' in t.required_metrics): available.append('thickness_sensitivity')
    if t.task_kind!='spectral_2d' and t.process!='SPDC' and d is not None: available+=['normalized_efficiency','process_coefficient']
    unavailable={key:('SPDC absolute pair rate/efficiency is not modeled' if t.process=='SPDC' and 'efficiency' in key else
                 'nonlinear coefficient or tensor model required' if d is None and key in {'normalized_efficiency','process_coefficient'} else
                 'not computed by this task kind; change task or use a reviewed extension') for key in METRICS if key not in available}
    return {'available':available,'unavailable':unavailable,'nonlinear_model':model,'d_eff_pm_v':d,'units':METRICS}

def templates():
    base={'material':'LN','crystal_cut':'x','propagation_axis':'Y','cladding':'AIR',
          'geometry':{'thickness_nm':600,'etch_depth_nm':300,'top_width_nm':1200,'sidewall_angle_deg':67},
          'process':'SHG','center_nm':1550,'backend':'demo'}
    def item(prompt,**changes):
        return {'prompt':prompt,'task':dict(deepcopy(base),**changes),'tools':['skill(qpm-design)','qpm_conventions','qpm_environment','qpm_plan','qpm_submit','jobs wait','qpm_status']}
    return {
      'fixed':item('LN x切、空气包层，600 nm厚刻300 nm、顶宽1200 nm，1550→775倍频，输出周期、效率、色散和GVM。',required_metrics=['period','normalized_efficiency','beta1','gvd','D','gvm']),
      'tolerance':item('上述结构，厚度与顶宽各±5 nm，画工艺误差棒。',tolerances_nm={'thickness_nm':5,'top_width_nm':5}),
      'search':item('在厚度570–630 nm步长10 nm、顶宽1000–1400 nm步长100 nm中寻找1550 nm信号波GVD最接近0的结构。',task_kind='search',search_axes={'thickness_nm':{'min':570,'max':630,'step':10},'top_width_nm':{'min':1000,'max':1400,'step':100}},objective={'metric':'gvd','role':'signal','wavelength_nm':1550,'tolerance':None}),
      'spdc_pmf':item('775 nm附近泵浦的SPDC：信号与闲频分别1530、1550、1570 nm的二维PMF；各格点泵浦随能量守恒变化。',process='SPDC',task_kind='spectral_2d',wavelength_axes_nm={'signal_nm':[1530,1550,1570],'idler_nm':[1530,1550,1570]},required_metrics=['pmf']),
      'sfg_2d':item('信号1500、1550、1600 nm，闲频1800、1900、2000 nm的SFG二维匹配周期。',process='SFG',task_kind='spectral_2d',wavelength_axes_nm={'signal_nm':[1500,1550,1600],'idler_nm':[1800,1900,2000]},required_metrics=['period'])}

def describe_conventions(topic='overview',template=None):
    from .extension_examples import example
    if template and topic!='templates': raise ValueError('template selector only valid for topic=templates')
    sections={
      'crystal':{'cuts':{c:crystal_convention(c) for c in ('x','z')},'unsupported':'arbitrary propagation direction/azimuth; do not infer propagation from cut name'},
      'geometry':{'units':'nm; sidewall angle deg','thickness_nm':'total film thickness','etch_depth_nm':'etch depth; slab=thickness-etch >0','top_width_nm':'rib top width, not bottom width','sidewall_angle_deg':'measured from horizontal, strictly between 0 and 90','thickness_etch_relation':{'fixed_etch':'thickness changes at fixed etch depth','fixed_slab':'etch changes with thickness at fixed slab'}},
      'wavelengths':{'internal_role_order':['pump','signal','idler'],'pump_role':'highest frequency even for SHG/SFG; not necessarily the experimental input named pump',
          'center_nm':'SHG fundamental; SPDC twice fixed 1D pump wavelength; SFG/DFG signal scan center',
          'SHG':'[axis/2, axis, axis]; default 1550±50 nm step20, plus center',
          'SPDC_1d':'pump=center_nm/2 fixed; scan signal=axis, idler=(1/pump-1/signal)^-1',
          'SFG_DFG_1d':'signal=axis; fixed_idler_nm required; high-frequency pump=(1/signal+1/idler)^-1',
          'spectral_2d':'signal_nm/idler_nm increasing arrays; pump varies at every cell by energy conservation; SPDC uses nominal grating set at center degeneracy',
          'unsupported':'independent fixed-pump rectangular SPDC scan, absolute SPDC pair rate; do not claim varying-pump heatmap is a fixed-pump experiment'},
      'metrics':{'units':METRICS,'gvm':'signed (ng_pump-ng_signal)/c in ps/m, plus absolute curve; signal-idler GVM requires new indicator extension',
          'gvd':'beta2=d²beta/domega²; objective wavelength is the scan axis, role selects actual wave; default three-point wavelength difference at ±2 nm; isolated anomalies limited to ceil(10%) per curve may upgrade to5/7; explicit5/7 supported; never9; convergence unverified unless requested',
          'thickness_sensitivity':'signed d(delta_beta)/dt using t±2 nm; optimization scores absolute value',
          'efficiency':'lossless per-wavelength matching classical coefficient; default L=5 mm; not actual percent; no SPDC absolute efficiency',
          'tolerances':'nominal plus true ± endpoints; required-period changes plotted, efficiency uses fixed nominal grating; ±5 nm tolerance independent of ±2 nm derivative',
          'search':'GP guides measured sampling; no tolerance means closest/minimum candidate only; best candidate curves, no default structure heatmap'},
      'routing':{'fixed':'fixed structure curves','tolerance':'fixed + tolerances_nm','search':'search + search_axes + objective','spdc_pmf':'spectral_2d + SPDC','sfg_2d':'spectral_2d + SFG',
          'order':['load skill','conventions/template if needed','environment','plan','submit','wait/status','analyze'],
          'extension_levels':['existing configuration: direct call','task adapter / postprocess: stage, human approval, tests, execute','new solver physics: separately develop and validate']},
      'extensions':{'adapter_signature':'adapt_task(task: dict) -> dict','postprocess_signature':'analyze_result(context: dict) -> dict',
          'postprocess_context':['contract_version','source_result_file','task','validation','cost','failures','curves (missing numbers=null)','tolerances','spectral','search','metric_units'],
          'postprocess_output':'metrics [{name,value,unit,definition,valid,source}], tables [{name,columns,units,rows}], curves [{name,x,y,x_unit,y_unit,definition,valid?}]; finite numbers or null only',
          'scope':'no solver calls or core-file modifications; approved Python is trusted local code, not an OS sandbox',
          'review':'code + tests + metadata content pinned; native Harness per-version approval; automatically test before enabling; old settings tokens supported','example':example()},
      'templates':templates()}
    if topic=='overview': value={'topics':list(sections),'workflow':sections['routing'],'contract_version':CONTRACT_VERSION}
    elif topic in sections:
        value=deepcopy(sections[topic])
        if topic=='templates' and template:
            if template not in value: raise ValueError('unknown QPM template')
            value=value[template]
    else: raise ValueError('unknown convention topic')
    return {'contract_version':CONTRACT_VERSION,'topic':topic,'content':value,'solver_started':False}

def interpret_task(t):
    from .physics import waves
    wavelengths=t.wavelengths()
    role_waves=[dict(zip(('pump','signal','idler'),waves(t,w))) for w in wavelengths] if t.task_kind!='spectral_2d' else []
    provided=set(t.provided_fields)
    return {'contract_version':CONTRACT_VERSION,'crystal':crystal_convention(t.crystal_cut),
            'material':t.material,'cladding':t.cladding,'modes':t.modes,'geometry':t.to_dict()['geometry'],
            'wavelengths':{'scan_axis':'signal/fundamental wavelength','scan_nm':wavelengths if t.task_kind!='spectral_2d' else None,
                'roles_nm':role_waves,'axes_nm':t.wavelength_axes_nm,'convention':describe_conventions('wavelengths')['content']},
            'metrics':metric_availability(t),'required_metrics':t.required_metrics,'budget':t.budget,
            'explicit_input_fields':sorted(provided),'defaulted_fields':sorted(set(t.to_dict())-provided-{'provided_fields','assumptions','length_defaulted'}),
            'assumptions':t.assumptions,'capability_gaps':metric_availability(t)['unavailable']}
