import math

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
