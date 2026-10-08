"""Lab x horizontal, y vertical, z propagation; crystal axes are X/Y/Z."""
import numpy as np

def mapping(cut):
    if cut == "x":
        return {"lab_x": "Z", "lab_y": "X", "lab_z": "Y"}
    if cut == "z":
        return {"lab_x": "Y", "lab_y": "Z", "lab_z": "X"}
    raise ValueError("crystal_cut must be x or z")

def default_mode(cut):
    mapping(cut)
    return "TE0" if cut == "x" else "TM0"

def extraordinary(cut, mode):
    return mode == default_mode(cut)

def search_expression(cut, mode):
    return "ne_sell(lam_um)" if extraordinary(cut, mode) else "no_sell(lam_um)"

def dielectric_expressions(cut):
    mapping(cut)
    return ["ne_sell(lam_um)^2", "no_sell(lam_um)^2", "no_sell(lam_um)^2"] if cut == "x" else ["no_sell(lam_um)^2", "ne_sell(lam_um)^2", "no_sell(lam_um)^2"]

def rotation(cut):
    axes = mapping(cut)
    return np.array([[float(axes[f"lab_{axis}"] == name) for name in "XYZ"] for axis in "xyz"])

def rotate_tensor(tensor, cut):
    tensor = np.asarray(tensor, dtype=float)
    if tensor.shape != (3, 3, 3):
        raise ValueError("nonlinear tensor must have shape (3,3,3), crystal XYZ order")
    r = rotation(cut)
    return np.einsum("ia,jb,kc,abc->ijk", r, r, r, tensor)

def nonlinear_coefficient(material, cut, modes, supplied=None):
    if supplied is not None:
        return float(supplied), "user_supplied_scalar_d_eff"
    if all(extraordinary(cut, mode) for mode in modes):
        return (27.0 if material == "LN" else 16.0), "legacy_d33_dominant_component"
    return None, "needs_nonlinear_coefficient"
