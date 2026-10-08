"""Pure numerical functions, SI internally. No COMSOL imports."""
import math
import numpy as np
from scipy.constants import c, epsilon_0

FORMULA_VERSION = "legacy-independent-classical-process-formulas-v2"

def beta(neff, wavelength_nm): return 2 * np.pi * np.asarray(neff) / (np.asarray(wavelength_nm) * 1e-9)

def local_dispersion(wavelength_nm, neff, center_nm):
    """Three/five/seven-point wavelength differences; convert analytically to omega derivatives."""
    w=np.asarray(wavelength_nm,float); n=np.asarray(neff,float)
    order=np.argsort(w); w,n=w[order],n[order]
    empty={'beta1_ps_mm':math.nan,'gvd_fs2_mm':math.nan,'D_ps_nm_km':math.nan,'fit_error_fs2_mm':math.nan}
    if len(w) not in (3,5,7) or not np.isfinite(n).all(): return empty
    center=len(w)//2; h=(w[1]-w[0])*1e-9; lam=center_nm*1e-9
    if h<=0 or not np.allclose(np.diff(w),h*1e9,rtol=1e-8,atol=1e-9) or not np.isclose(w[center],center_nm): return empty
    first=(n[center+1]-n[center-1])/(2*h)
    second=(n[center-1]-2*n[center]+n[center+1])/h**2
    error=math.nan
    if len(w)>=5:
        local=n[center-2:center+3]
        coarse=second
        first=(local[0]-8*local[1]+8*local[3]-local[4])/(12*h)
        second=(-local[0]+16*local[1]-30*local[2]+16*local[3]-local[4])/(12*h*h)
        error=abs(second-coarse)*lam**3/(2*np.pi*c*c)/1e-27
    if len(w)==7:
        coarse=second
        first=float(np.dot([-1,9,-45,0,45,-9,1],n))/(60*h)
        second=float(np.dot([2,-27,270,-490,270,-27,2],n))/(180*h*h)
        error=abs(second-coarse)*lam**3/(2*np.pi*c*c)/1e-27
    b1=(n[center]-lam*first)/c
    b2=lam**3*second/(2*np.pi*c*c)
    return {'beta1_ps_mm':float(b1*1e9),'gvd_fs2_mm':float(b2/1e-27),
            'D_ps_nm_km':float(-lam/c*second*1e6),'fit_error_fs2_mm':float(error)}

def group_index(center_nm, n0, n_minus, n_plus, offset_nm):
    return n0 - center_nm*(n_plus-n_minus)/(2*offset_nm)

def gvm(ng_high, ng_low):
    signed = (ng_high-ng_low)*1e12/c
    return signed, abs(signed)

def qpm_period(delta_beta, order=1):
    if not np.isfinite(delta_beta) or abs(delta_beta) < 1e-12: return math.nan
    return 2*np.pi*abs(order)/abs(delta_beta)*1e6

def pmf(delta_k, length_mm):
    return np.sinc(np.asarray(delta_k)*length_mm*1e-3/(2*np.pi))**2

def effective_area(process, modes, mask=None):
    high, low1, low2 = modes
    x, y = np.asarray(high["x_m"]), np.asarray(high["y_m"])
    if any(not np.array_equal(x,m["x_m"]) or not np.array_equal(y,m["y_m"]) for m in modes):
        raise ValueError("fields must share common coordinates")
    def integral(v): return np.trapezoid(np.trapezoid(v, x=x, axis=1), x=y, axis=0)
    e1,e2,e3 = (np.asarray(m["field"]) for m in modes)
    energies=[float(integral(abs(e)**2).real) for e in (e1,e2,e3)]
    if mask is None: mask = high.get("material_mask")
    if mask is None: raise ValueError("nonlinear material mask required")
    density=np.conj(e1)*e2*e3*mask
    if process == "DFG": density=np.conj(e3)*e1*np.conj(e2)*mask
    overlap=integral(density)
    if min(energies)<=0: raise ValueError("invalid field normalization")
    if abs(overlap)==0: return math.inf
    return math.prod(energies)/abs(overlap)**2

def normalized_efficiency(process, wavelengths, indices, area_m2, d_pm_v, order=1, duty=.5):
    """Retain audited legacy scalar formulas; not a quantum SPDC rate."""
    if process == "SPDC": return math.nan
    w=2*np.pi*c/(np.asarray(wavelengths)*1e-9); n1,n2,n3=indices
    d=d_pm_v*1e-12*2*abs(np.sin(np.pi*order*duty))/(np.pi*abs(order))
    if process == "SHG": return d*d*w[0]**2/(2*epsilon_0*c**3*n1*n2**2*area_m2)
    if process == "SFG": return 2*d*d*w[0]**2/(epsilon_0*c**3*n1*n2*n3*area_m2)
    if process == "DFG": return 2*d*d*w[1]*w[2]/(epsilon_0*c**3*n1*n2*n3*area_m2)
    raise ValueError("unknown process")

def waves(task, axis_nm):
    """Always return high-frequency, low1, low2 in that order."""
    if task.process == "SHG": return [axis_nm/2, axis_nm, axis_nm]
    if task.process == "SPDC":
        pump = task.center_nm/2
        if axis_nm <= pump: raise ValueError("SPDC signal must be longer than pump")
        return [pump, axis_nm, 1/(1/pump-1/axis_nm)]
    other = task.wavelength_axes_nm.get("fixed_idler_nm")
    if other is None: raise ValueError("SFG/DFG 1D requires wavelength_axes_nm.fixed_idler_nm")
    return [1/(1/axis_nm+1/other), axis_nm, other]
