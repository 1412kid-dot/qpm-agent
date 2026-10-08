"""A reviewed-code template: new signal-idler GVM from exported beta1 only."""
CODE='''import math

def analyze_result(context):
    rows=context.get("curves", [])
    if not rows:
        raise ValueError("requires fixed/search exported curves with role beta1")
    x=[];y=[];valid=[]
    for row in rows:
        a=row.get("signal_beta1_ps_mm");b=row.get("idler_beta1_ps_mm")
        ok=bool(row.get("valid")) and all(type(v) in (int,float) and math.isfinite(v) for v in (a,b))
        x.append(row["wavelength_nm"]);y.append((a-b)*1000 if ok else None);valid.append(ok)
    measured=[abs(v) for v in y if v is not None]
    return {"metrics":[{"name":"max_abs_signal_idler_gvm", "value":max(measured) if measured else None,
              "valid":bool(measured), "unit":"ps/m", "definition":"max measured abs(beta1_signal-beta1_idler)*1000; no interpolation over missing points",
              "source":"curves.signal_beta1_ps_mm and curves.idler_beta1_ps_mm"}],
            "curves":[{"name":"signal_idler_gvm","x":x,"y":y,"valid":valid,"x_unit":"nm","y_unit":"ps/m",
              "definition":"signed (beta1_signal-beta1_idler)*1000"}],
            "notes":["Additional analysis preserves source validation; missing points remain null."]}
'''
TESTS='''from extension import analyze_result

def test_units_sign_and_missing():
    result=analyze_result({"curves":[
      {"wavelength_nm":1530,"signal_beta1_ps_mm":7,"idler_beta1_ps_mm":8,"valid":True},
      {"wavelength_nm":1550,"signal_beta1_ps_mm":None,"idler_beta1_ps_mm":8,"valid":False},
      {"wavelength_nm":1570,"signal_beta1_ps_mm":9,"idler_beta1_ps_mm":8,"valid":True}]})
    assert result["curves"][0]["y"]==[-1000,None,1000]
    assert result["metrics"][0]["value"]==1000

def test_no_successful_samples():
    result=analyze_result({"curves":[{"wavelength_nm":1550,"valid":False}]})
    assert result["metrics"][0]["valid"] is False
    assert result["metrics"][0]["value"] is None

def test_no_curve_data():
    try:
        analyze_result({"curves":[]})
    except ValueError:
        return
    raise AssertionError("missing source data must fail")
'''

def example():
    return {'id':'signal-idler-gvm','metadata':{'kind':'postprocess','version':'1.0.0',
      'description':'Signal-idler signed GVM and max absolute measured mismatch',
      'applicability':'Exported fixed/search curves with signal/idler beta1 ps/mm; demo remains synthetic'},
      'code':CODE,'tests':TESTS}
