"""Pure-Python numerics for the LN/LT COMSOL dispersion automation."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import math
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.constants import c
from scipy.interpolate import PchipInterpolator
from scipy.linalg import cho_factor, cho_solve
from scipy.optimize import brentq, minimize


C0 = float(c)


def inclusive_range(start: float, stop: float, step: float) -> tuple[float, ...]:
    if step <= 0 or stop < start:
        raise ValueError("range requires stop >= start and step > 0")
    count = int(round((stop - start) / step))
    if not math.isclose(start + count * step, stop, rel_tol=0, abs_tol=1e-8):
        raise ValueError("range span must be divisible by step")
    return tuple(float(start + i * step) for i in range(count + 1))


def normalized_field_overlap(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a).reshape(-1)
    b = np.asarray(b).reshape(-1)
    if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        return float("nan")
    denom = float(np.vdot(a, a).real * np.vdot(b, b).real)
    return float(abs(np.vdot(a, b)) ** 2 / denom) if denom > 0 else float("nan")


def _local_derivatives(x: np.ndarray, y: np.ndarray, degree: int, window: int) -> tuple[np.ndarray, np.ndarray]:
    """First and second local-polynomial derivatives on an irregular grid."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.ndim != 1 or y.shape != x.shape or np.any(np.diff(x) <= 0):
        raise ValueError("x and y must be same-length 1-D arrays with increasing x")
    if len(x) < degree + 1:
        raise ValueError("not enough points for requested polynomial degree")
    count = min(max(degree + 1, window), len(x))
    first = np.full(len(x), np.nan)
    second = np.full(len(x), np.nan)
    half = count // 2
    for i, x0 in enumerate(x):
        start = max(0, min(i - half, len(x) - count))
        stop = start + count
        scale = max(float(np.ptp(x[start:stop])), np.finfo(float).eps)
        u = (x[start:stop] - x0) / scale
        coeff = np.polyfit(u, y[start:stop], degree)
        poly = np.poly1d(coeff)
        first[i] = float(np.polyder(poly, 1)(0.0) / scale)
        second[i] = float(np.polyder(poly, 2)(0.0) / scale**2)
    return first, second


def finite_segments(values: np.ndarray) -> list[np.ndarray]:
    indices = np.flatnonzero(np.isfinite(values))
    if not len(indices):
        return []
    return list(np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1))


def compute_dispersion(wavelength_nm: Sequence[float], neff: Sequence[float]) -> pd.DataFrame:
    """Compute beta, beta1, beta2 and D without fitting across missing data."""
    wl = np.asarray(wavelength_nm, dtype=float)
    n = np.asarray(neff, dtype=float)
    if wl.ndim != 1 or n.shape != wl.shape or np.any(np.diff(wl) <= 0):
        raise ValueError("wavelength and neff must be same-length with increasing wavelength")
    omega = 2 * np.pi * C0 / (wl * 1e-9)
    beta = n * omega / C0
    beta1 = np.full(len(wl), np.nan)
    beta2 = np.full(len(wl), np.nan)
    beta2_cross = np.full(len(wl), np.nan)
    fit_degree = np.zeros(len(wl), dtype=int)
    # Polynomial helper requires increasing x; omega decreases with wavelength.
    for segment in finite_segments(n):
        if len(segment) < 5:
            continue
        rev = segment[::-1]
        x = omega[rev]
        y = beta[rev]
        degree = min(4, len(segment) - 1)
        d1, d2 = _local_derivatives(x, y, degree, 7)
        beta1[rev] = d1
        beta2[rev] = d2
        fit_degree[segment] = degree
        if len(segment) >= 6:
            degree2 = min(5, len(segment) - 1)
            _, d2b = _local_derivatives(x, y, degree2, 9)
            beta2_cross[rev] = d2b
    beta2_error = abs(beta2 - beta2_cross)
    d_si = -(2 * np.pi * C0 / (wl * 1e-9) ** 2) * beta2
    return pd.DataFrame({
        "wavelength_nm": wl,
        "neff": n,
        "omega_rad_s": omega,
        "beta_rad_m": beta,
        "beta1_s_m": beta1,
        "beta1_ps_mm": beta1 * 1e9,
        "gvd_s2_m": beta2,
        "gvd_ps2_km": beta2 / 1e-27,
        "dispersion_D_s_m2": d_si,
        "dispersion_D_ps_nm_km": d_si * 1e6,
        "gvd_cross_s2_m": beta2_cross,
        "gvd_fit_error_s2_m": beta2_error,
        "gvd_fit_error_ps2_km": beta2_error / 1e-27,
        "fit_degree": fit_degree,
    })


@dataclass(frozen=True)
class ZDPRoot:
    wavelength_nm: float
    kind: str
    bracket_left_nm: float
    bracket_right_nm: float
    uncertainty_nm: float


def find_zdp_roots(frame: pd.DataFrame, wavelength_tolerance_nm: float = 1.0) -> list[ZDPRoot]:
    x = frame["wavelength_nm"].to_numpy(float)
    y = frame["gvd_s2_m"].to_numpy(float)
    err = frame["gvd_fit_error_s2_m"].to_numpy(float)
    roots: list[ZDPRoot] = []
    for segment in finite_segments(y):
        if len(segment) < 2:
            continue
        xs, ys, es = x[segment], y[segment], err[segment]
        interp = PchipInterpolator(xs, ys, extrapolate=False)
        for a, b, ya, yb in zip(xs[:-1], xs[1:], ys[:-1], ys[1:]):
            if ya * yb < 0:
                root = float(brentq(lambda z: float(interp(z)), a, b))
                roots.append(ZDPRoot(root, "crossing", float(a), float(b), min(abs(b-a)/2, wavelength_tolerance_nm)))
        exact = np.flatnonzero(ys == 0)
        for i in exact:
            left = max(0, i-1); right = min(len(xs)-1, i+1)
            kind = "crossing" if left != i and right != i and ys[left]*ys[right] < 0 else "touching_candidate"
            roots.append(ZDPRoot(float(xs[i]), kind, float(xs[left]), float(xs[right]),
                                 min(abs(xs[right]-xs[left])/2, wavelength_tolerance_nm)))
        # Tangential/near-zero candidates are accepted only when zero lies in
        # the fit-disagreement band and the point is a local |GVD| minimum.
        for i in range(1, len(xs)-1):
            band = es[i] if np.isfinite(es[i]) else 0.0
            if abs(ys[i]) <= band and abs(ys[i]) <= min(abs(ys[i-1]), abs(ys[i+1])):
                roots.append(ZDPRoot(float(xs[i]), "touching_candidate", float(xs[i-1]), float(xs[i+1]), abs(xs[i+1]-xs[i-1])/2))
    dedup: list[ZDPRoot] = []
    for root in sorted(roots, key=lambda r: r.wavelength_nm):
        if not dedup or abs(root.wavelength_nm - dedup[-1].wavelength_nm) > wavelength_tolerance_nm:
            dedup.append(root)
        elif root.kind == "crossing" and dedup[-1].kind != "crossing":
            dedup[-1] = root
    return dedup


@dataclass(frozen=True)
class GeometryAxis:
    values_nm: tuple[float, ...] = ()
    minimum_nm: float | None = None
    maximum_nm: float | None = None
    initial_step_nm: float | None = None
    minimum_step_nm: float | None = None

    def initial_values(self) -> tuple[float, ...]:
        if self.values_nm:
            return tuple(sorted(set(map(float, self.values_nm))))
        if None in (self.minimum_nm, self.maximum_nm, self.initial_step_nm):
            raise ValueError("axis needs values_nm or min/max/initial_step")
        return inclusive_range(float(self.minimum_nm), float(self.maximum_nm), float(self.initial_step_nm))

    def candidate_values(self) -> tuple[float, ...]:
        if self.minimum_nm is None or self.maximum_nm is None:
            return self.initial_values()
        step = self.minimum_step_nm or self.initial_step_nm
        if step is None:
            raise ValueError("range axis needs a step")
        return inclusive_range(float(self.minimum_nm), float(self.maximum_nm), float(step))


def geometry_lattice(thicknesses_nm: Iterable[float], etch_axis: GeometryAxis,
                     width_axis: GeometryAxis) -> tuple[tuple[float, float, float], ...]:
    rows = []
    for t, d, w in product(sorted(set(map(float, thicknesses_nm))), etch_axis.candidate_values(), width_axis.candidate_values()):
        if d <= t:
            rows.append((t, d, w))
    if not rows:
        raise ValueError("geometry lattice is empty")
    return tuple(rows)


def maximin_seeds(candidates: Sequence[tuple[float, ...]], count: int) -> list[tuple[float, ...]]:
    if count >= len(candidates):
        return list(candidates)
    x = np.asarray(candidates, dtype=float)
    span = np.ptp(x, axis=0)
    span[span == 0] = 1.0
    z = (x - x.min(axis=0)) / span
    center = np.full(x.shape[1], 0.5)
    selected = [int(np.argmin(np.linalg.norm(z-center, axis=1)))]
    while len(selected) < count:
        distance = np.min(np.linalg.norm(z[:, None, :] - z[selected][None, :, :], axis=2), axis=1)
        distance[selected] = -1
        selected.append(int(np.argmax(distance)))
    return [tuple(map(float, x[i])) for i in selected]


def matern52_distance(a: np.ndarray, b: np.ndarray, length_scales: np.ndarray) -> np.ndarray:
    delta = (a[:, None, :] - b[None, :, :]) / length_scales
    r = np.sqrt(np.sum(delta**2, axis=2))
    q = np.sqrt(5.0) * r
    return (1.0 + q + q**2/3.0) * np.exp(-q)


class MaternGP:
    """Small deterministic Matérn-5/2 GP with optimized anisotropic scales."""
    def __init__(self, noise_floor: float = 1e-8):
        self.noise_floor = float(noise_floor)

    def fit(self, x: np.ndarray, y: np.ndarray, noise: np.ndarray | None = None) -> "MaternGP":
        x = np.asarray(x, float); y = np.asarray(y, float)
        if x.ndim != 2 or y.shape != (len(x),) or len(x) < 2:
            raise ValueError("GP needs at least two observations")
        self.xmin = x.min(0); span = np.ptp(x, axis=0); span[span == 0] = 1
        self.x = (x-self.xmin)/span; self.xspan = span
        self.ymean = float(y.mean()); self.yscale = float(y.std()) or 1.0
        self.y = (y-self.ymean)/self.yscale
        obs_noise = np.zeros(len(y)) if noise is None else np.asarray(noise,float)/self.yscale
        obs_noise = np.maximum(obs_noise, self.noise_floor)
        def objective(theta: np.ndarray) -> float:
            ls = np.exp(theta[:-1]); jitter = np.exp(theta[-1])
            k = matern52_distance(self.x,self.x,ls)
            k.flat[::len(k)+1] += obs_noise**2 + jitter**2
            try:
                cf = cho_factor(k, lower=True, check_finite=False)
                alpha = cho_solve(cf,self.y,check_finite=False)
            except np.linalg.LinAlgError:
                return 1e100
            return float(0.5*self.y@alpha + np.log(np.diag(cf[0])).sum() + 0.5*len(y)*np.log(2*np.pi))
        start = np.log(np.r_[np.full(x.shape[1],0.4), 1e-3])
        opt = minimize(objective,start,method="L-BFGS-B",bounds=[(-4,3)]*x.shape[1]+[(-12,0)])
        self.length_scales = np.exp(opt.x[:-1])
        self.jitter = float(np.exp(opt.x[-1]))
        self.k = matern52_distance(self.x,self.x,self.length_scales)
        self.k.flat[::len(self.k)+1] += obs_noise**2 + self.jitter**2
        self.cf = cho_factor(self.k,lower=True,check_finite=False)
        self.alpha = cho_solve(self.cf,self.y,check_finite=False)
        return self

    def predict(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        q = (np.asarray(x,float)-self.xmin)/self.xspan
        cross = matern52_distance(q,self.x,self.length_scales)
        mean = cross@self.alpha
        v = cho_solve(self.cf,cross.T,check_finite=False)
        variance = np.maximum(1.0 - np.sum(cross*v.T,axis=1),0.0)
        return mean*self.yscale+self.ymean, np.sqrt(variance)*self.yscale


def straddle_score(mean: np.ndarray, sigma: np.ndarray, confidence_sigma: float = 3.0) -> np.ndarray:
    return confidence_sigma*np.asarray(sigma)-abs(np.asarray(mean))
