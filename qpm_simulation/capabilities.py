from itertools import product
from .crystal import mapping,extraordinary

def describe_capabilities():
    from .conventions import CONTRACT_VERSION
    return {'schema_version':'0.1','package':'qpm_simulation','solver_side_effects_on_import':False,
        'design_contract':CONTRACT_VERSION,'convention_topics':['overview','crystal','geometry','wavelengths','metrics','routing','extensions','templates'],
        'extension_interfaces':['adapt_task(dict)->dict','analyze_result(context)->analysis'],
        'combinations':[{'material':m,'cut':cut,'mode':mode,'cladding':clad,'axes':mapping(cut),
             'linear_metrics':True,'default_d33':extraordinary(cut,mode),'status':'implemented; real COMSOL validation pending'}
             for m,cut,mode,clad in product(('LN','LT'),('x','z'),('TE0','TM0'),('AIR','SIO2'))],
        'entry_points':['describe_capabilities','validate_task','plan_task','run_task','postprocess','search'],
        'processes':['SHG','SFG','DFG','SPDC'],'metrics':{'beta1':'ps/mm','gvd':'fs²/mm','D':'ps/nm/km','gvm':'ps/m',
            'thickness_sensitivity':'d(delta_beta)/dt, m^-1/nm','period':'um','normalized_efficiency':'W^-1 m^-2','process_coefficient':'W^-1'},
        'plots':['fixed-structure curves','real tolerance endpoint envelopes','SPDC spectral PMF heatmap','SFG spectral period heatmap'],
        'limitations':['demo data synthetic','no absolute SPDC pair rate','scalar dominant-component nonlinear model only',
            'no general propagation azimuth','real x-cut/z-cut solver acceptance pending','Harness integration distributed as a separate plugin bundle']}
