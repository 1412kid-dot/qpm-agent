"""Shared model-facing JSON schema; numerical/physical constraints remain in Task.validate."""
from dataclasses import fields
from .config import Task

def task_schema():
    def enum(values,description): return {'type':'string','enum':values,'description':description}
    def number(description): return {'type':'number','description':description}
    def array(item,description): return {'type':'array','items':item,'description':description}
    def obj(properties,description='',required=()): return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False,'description':description}
    numeric=number('Positive finite value; see qpm_conventions for unit and role.')
    nullable=lambda schema:{'oneOf':[schema,{'type':'null'}]}
    geometry=obj({k:number(v) for k,v in {'thickness_nm':'Total film thickness, nm','etch_depth_nm':'Etch depth nm; 0<etch<thickness','top_width_nm':'Rib TOP width, nm','sidewall_angle_deg':'Degrees from HORIZONTAL, not normal; default67; 0<angle<90'}.items()},required=['thickness_nm','etch_depth_nm','top_width_nm'])
    axis=nullable(obj({'min':number('Lower legal bound'),'max':number('Upper legal bound'),'step':number('Explicit manufacturing grid step; never output off-grid candidate')},required=['min','max']))
    props={f.name:{'description':f'Advanced serialized field {f.name}; read conventions before use.'} for f in fields(Task)}
    props.update({
      'material':enum(['LN','LT'],'Lithium niobate / lithium tantalate'),
      'crystal_cut':enum(['x','z'],'Film normal crystal X or Z; NOT propagation direction'),
      'propagation_axis':nullable(enum(['X','Y','Z'],'Crystal propagation axis: x-cut supports Y only; z-cut supports X only')),
      'cladding':enum(['AIR','SIO2'],'Air / silica upper cladding'),'geometry':geometry,
      'process':enum(['SHG','SFG','DFG','SPDC'],'Frequency conversion process'),
      'task_kind':enum(['fixed','search','spectral_2d'],'Curves / structure optimization / SFG or SPDC heatmap'),
      'backend':enum(['demo','comsol','replay'],'demo synthetic; comsol requires settings enable; replay requires cache path'),
      'center_nm':number('SHG fundamental center; SPDC twice fixed pump; SFG/DFG signal center. 775→1550 SPDC: center_nm=1550.'),
      'wavelengths_nm':array(numeric,'Exact display signal/fundamental wavelength list nm'),
      'wavelength_range_nm':array(numeric,'[min,max] signal/fundamental scan nm; include center'),
      'modes':obj({r:enum(['TE0','TM0'],f'{r} mode: TE dominant lab Ex, TM dominant lab Ey') for r in ('pump','signal','idler')},'pump is HIGH frequency role; x-cut defaultTE0; z-cut defaultTM0'),
      'search_axes':obj({k:axis for k in ('thickness_nm','etch_depth_nm','top_width_nm','sidewall_angle_deg')},'null bounds select nominal±10%; explicit step restricts legal grid'),
      'objective':obj({'metric':enum(['gvd','gvm','thickness_sensitivity'],'Search target; GVM signed pump-signal'),
          'role':enum(['pump','signal','idler'],'GVD wave role; GVM is always pump-signal'),
          'wavelength_nm':number('SCAN axis wavelength nm; actual role wavelength is derived'),
          'tolerance':nullable(number('Absolute acceptance tolerance in target units; absent means closest only')),
          'reduction':enum(['at_wavelength','zero_in_band','max_abs_over_band'],'Target reduction'),
          'verification_abs_tolerance':number('Numerical stability comparison tolerance')},required=['metric']),
      'tolerances_nm':obj({k:number('Real ± tolerance nm; independent of derivative step') for k in ('thickness_nm','top_width_nm')}),
      'wavelength_axes_nm':obj({'signal_nm':array(numeric,'2D increasing signal wavelengths nm'),'idler_nm':array(numeric,'2D increasing idler wavelengths nm'),'fixed_idler_nm':number('Required fixed idler wavelength for SFG/DFG 1D')},'2D pump VARIES by energy conservation; fixed-pump rectangular SPDC not supported'),
      'dispersion_stencil_points':{'type':'integer','enum':[3,5,7],'description':'Default3; anomaly-limited 5/7; explicit 5/7 supported; never9'},
      'dispersion_refinement_wavelengths_nm':array(numeric,'Explicit display wavelengths to check with5 points; default empty'),
      'dispersion_roles':array(enum(['pump','signal','idler'],'Wave role'),'GVD-only defaults objective role; explicit selection avoids unused bands'),
      'adaptive_dispersion':{'type':'boolean','description':'Defaulttrue; limited isolated anomaly upgrades 3→5→7'},
      'dispersion_absolute_tolerance_fs2_mm':number('Nonnegative order-comparison tolerance fs²/mm, default1'),
      'dispersion_relative_tolerance':number('Nonnegative relative order-comparison tolerance, default0.05'),
      'verify_numerics':{'type':'boolean','description':'Defaultfalse; true explicitly adds refined mesh and half-step validation; cost must be planned'},
      'required_metrics':array(enum(['period','beta1','gvd','D','gvm','thickness_sensitivity','normalized_efficiency','process_coefficient','pmf'],'Explicitly required output; unsupported requests rejected, not dropped'),'Copy user-required metrics here; new metrics need postprocess extension'),
      'budget':obj({k:number('Cannot exceed plugin settings ceiling') for k in ('max_geometry_count','max_solver_calls','max_wall_time_s')}),
      'length_mm':number('Device length mm, default5; coefficient not actual conversion percentage'),
      'nonlinear_coefficient_pm_v':nullable(number('User-established scalar d_eff pm/V; not full tensor. Never invent value.')),
      'thickness_etch_relation':enum(['fixed_etch','fixed_slab'],'Thickness perturbation keeps etch depth or slab fixed'),
      'full_scan':{'type':'boolean','description':'Only true when explicitly asked to traverse entire legal grid'},
      'batch_size':{'type':'integer','description':'Adaptive batch size 1..4'},'max_additional_geometries':{'type':'integer','description':'Additional structures nonnegative; actual geometry budget enforced'},
      'solver':{'type':'object','additionalProperties':True,'description':'Advanced mesh/solver settings; paths managed by settings, physics keys validated by Python'},
      'assumptions':array({'type':'string'},'Record assumptions explicitly'),
      'provided_fields':array({'type':'string'},'Runtime provenance: fields supplied before defaults'),
      'schema_version':{'type':'string','description':'Task format version'},
      'length_defaulted':{'type':'boolean','description':'Runtime provenance for default length'},
      'replay_dir':nullable({'type':'string','description':'Validated cache directory'}),
      'replay_source_backend':enum(['demo','comsol'],'Source identity checked before replay')})
    for name in ('wavelength_step_nm','thickness_derivative_nm','fw_derivative_nm','sh_derivative_nm','dispersion_step_nm','spectral_solve_step_nm'):
        props[name]=number(f'{name} in nm; default display20, thickness2, low2, high1, dispersion2')
    return obj(props,'QPM task: query qpm_conventions templates before building unfamiliar tasks.',required=['material','crystal_cut','cladding','geometry'])
