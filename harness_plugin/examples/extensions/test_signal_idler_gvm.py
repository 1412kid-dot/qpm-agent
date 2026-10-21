from extension import analyze_result

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
