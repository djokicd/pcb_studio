"""
Two-port amplifier metrics (vectorized over frequency).

All functions accept either an ``skrf.Network`` or an S-parameter array
of shape (nfreq, 2, 2).  Reflection-coefficient arguments ``gs`` / ``gl``
may be scalars or arrays broadcastable to the frequency axis.
"""

import numpy as np
from .. import rfnet as skrf


def _s(dev):
    if isinstance(dev, skrf.Network):
        return dev.s
    return np.asarray(dev)


def _unpack(dev):
    s = _s(dev)
    return s[:, 0, 0], s[:, 0, 1], s[:, 1, 0], s[:, 1, 1]


# ----------------------------------------------------------------------
# reflection coefficients
# ----------------------------------------------------------------------

def delta(dev):
    """Determinant of the S-matrix."""
    s11, s12, s21, s22 = _unpack(dev)
    return s11 * s22 - s12 * s21


def input_reflection(dev, gl):
    """Gamma_in seen at the device input for load reflection ``gl``."""
    s11, s12, s21, s22 = _unpack(dev)
    return s11 + s12 * s21 * gl / (1 - s22 * gl)


def output_reflection(dev, gs):
    """Gamma_out seen at the device output for source reflection ``gs``."""
    s11, s12, s21, s22 = _unpack(dev)
    return s22 + s12 * s21 * gs / (1 - s11 * gs)


# ----------------------------------------------------------------------
# gains
# ----------------------------------------------------------------------

def transducer_gain(dev, gs, gl):
    """Transducer power gain G_T (linear)."""
    s11, s12, s21, s22 = _unpack(dev)
    num = (1 - np.abs(gs) ** 2) * np.abs(s21) ** 2 * (1 - np.abs(gl) ** 2)
    den = np.abs((1 - s11 * gs) * (1 - s22 * gl) - s12 * s21 * gs * gl) ** 2
    return num / den


def available_gain(dev, gs):
    """Available power gain G_A (linear)."""
    s11, s12, s21, s22 = _unpack(dev)
    gout = output_reflection(dev, gs)
    num = (1 - np.abs(gs) ** 2) * np.abs(s21) ** 2
    den = np.abs(1 - s11 * gs) ** 2 * (1 - np.abs(gout) ** 2)
    return num / den


def operating_gain(dev, gl):
    """Operating (power) gain G_P (linear)."""
    s11, s12, s21, s22 = _unpack(dev)
    gin = input_reflection(dev, gl)
    num = np.abs(s21) ** 2 * (1 - np.abs(gl) ** 2)
    den = (1 - np.abs(gin) ** 2) * np.abs(1 - s22 * gl) ** 2
    return num / den


def max_gain(dev):
    """
    MAG where K > 1 (and |S12| > 0), MSG where K <= 1 (linear).
    For unilateral points (S12 == 0) returns |S21|^2 / ((1-|S11|^2)(1-|S22|^2)).
    """
    s11, s12, s21, s22 = _unpack(dev)
    k = rollett_k(dev)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.abs(s21) / np.abs(s12)
        mag = ratio * (k - np.sqrt(np.maximum(k ** 2 - 1, 0)))
        g = np.where(k > 1, mag, ratio)
        # unilateral fallback
        uni = np.abs(s21) ** 2 / ((1 - np.abs(s11) ** 2) * (1 - np.abs(s22) ** 2))
        g = np.where(np.abs(s12) == 0, uni, g)
    return g


# ----------------------------------------------------------------------
# stability
# ----------------------------------------------------------------------

def rollett_k(dev):
    """Rollett stability factor K."""
    s11, s12, s21, s22 = _unpack(dev)
    d = delta(dev)
    return (1 - np.abs(s11) ** 2 - np.abs(s22) ** 2 + np.abs(d) ** 2) / \
        (2 * np.abs(s12 * s21))


def mu_load(dev):
    """mu stability factor (load side).  mu > 1 => unconditionally stable."""
    s11, s12, s21, s22 = _unpack(dev)
    d = delta(dev)
    return (1 - np.abs(s11) ** 2) / \
        (np.abs(s22 - d * np.conj(s11)) + np.abs(s12 * s21))


def mu_source(dev):
    """mu' stability factor (source side)."""
    s11, s12, s21, s22 = _unpack(dev)
    d = delta(dev)
    return (1 - np.abs(s22) ** 2) / \
        (np.abs(s11 - d * np.conj(s22)) + np.abs(s12 * s21))


def is_unconditionally_stable(dev):
    """Boolean per frequency: K > 1 and |Delta| < 1."""
    return (rollett_k(dev) > 1) & (np.abs(delta(dev)) < 1)


def source_stability_circle(dev, idx, npoints=201):
    """
    Source-plane stability circle at frequency index ``idx``.
    Returns complex points on the circle in the Gamma_S plane.
    """
    s11, s12, s21, s22 = _unpack(dev)
    d = delta(dev)
    den = np.abs(s11[idx]) ** 2 - np.abs(d[idx]) ** 2
    center = np.conj(s11[idx] - d[idx] * np.conj(s22[idx])) / den
    radius = np.abs(s12[idx] * s21[idx] / den)
    t = np.linspace(0, 2 * np.pi, npoints)
    return center + radius * np.exp(1j * t)


def load_stability_circle(dev, idx, npoints=201):
    """
    Load-plane stability circle at frequency index ``idx``.
    Returns complex points on the circle in the Gamma_L plane.
    """
    s11, s12, s21, s22 = _unpack(dev)
    d = delta(dev)
    den = np.abs(s22[idx]) ** 2 - np.abs(d[idx]) ** 2
    center = np.conj(s22[idx] - d[idx] * np.conj(s11[idx])) / den
    radius = np.abs(s12[idx] * s21[idx] / den)
    t = np.linspace(0, 2 * np.pi, npoints)
    return center + radius * np.exp(1j * t)


# ----------------------------------------------------------------------
# simultaneous conjugate match
# ----------------------------------------------------------------------

def simultaneous_match(dev):
    """
    Simultaneous conjugate match (Gamma_MS, Gamma_ML) per frequency.
    Entries are NaN where the device is not unconditionally stable.
    """
    s11, s12, s21, s22 = _unpack(dev)
    d = delta(dev)
    b1 = 1 + np.abs(s11) ** 2 - np.abs(s22) ** 2 - np.abs(d) ** 2
    b2 = 1 + np.abs(s22) ** 2 - np.abs(s11) ** 2 - np.abs(d) ** 2
    c1 = s11 - d * np.conj(s22)
    c2 = s22 - d * np.conj(s11)

    def solve(b, c):
        with np.errstate(divide="ignore", invalid="ignore"):
            disc = b ** 2 - 4 * np.abs(c) ** 2
            root = np.sqrt(disc.astype(complex))
            gp = (b + root) / (2 * c)
            gm = (b - root) / (2 * c)
            g = np.where(np.abs(gm) <= 1, gm, gp)
        return g

    gms = solve(b1, c1)
    gml = solve(b2, c2)
    unstable = ~is_unconditionally_stable(dev)
    gms = np.where(unstable, np.nan + 1j * np.nan, gms)
    gml = np.where(unstable, np.nan + 1j * np.nan, gml)
    return gms, gml


def stable_region_match(dev, idx, gamma_limit=0.85, n_mag=60, n_ang=90):
    """
    Design helper for conditionally stable devices (K < 1), where a
    simultaneous conjugate match does not exist.

    Grid-searches Gamma_L over the unit disk, conjugate-matches the
    device input (Gamma_S = conj(Gamma_in)), and keeps only candidates
    with |Gamma_in| <= gamma_limit and |Gamma_out| <= gamma_limit, i.e.
    an operating point with margin from the instability regions.
    ``gamma_limit`` trades gain against stability margin.

    Returns
    -------
    (gs, gl, gt) : chosen Gamma_S, Gamma_L and the resulting transducer
        gain (linear) at frequency index ``idx``.
    """
    s = _s(dev)
    s11, s12, s21, s22 = (s[idx, 0, 0], s[idx, 0, 1],
                          s[idx, 1, 0], s[idx, 1, 1])

    r = np.linspace(0, 0.95, n_mag)
    a = np.linspace(-np.pi, np.pi, n_ang, endpoint=False)
    gl = (r[:, None] * np.exp(1j * a[None, :])).ravel()

    gin = s11 + s12 * s21 * gl / (1 - s22 * gl)
    gs = np.conj(gin)
    gout = s22 + s12 * s21 * gs / (1 - s11 * gs)
    ok = (np.abs(gin) <= gamma_limit) & (np.abs(gout) <= gamma_limit)
    if not ok.any():
        raise ValueError(
            f"no operating point with |Gamma| margins <= {gamma_limit}; "
            "the device may need resistive stabilization at this frequency")

    num = (1 - np.abs(gs) ** 2) * np.abs(s21) ** 2 * (1 - np.abs(gl) ** 2)
    den = np.abs((1 - s11 * gs) * (1 - s22 * gl) - s12 * s21 * gs * gl) ** 2
    gt = np.where(ok, num / den, 0.0)
    i = int(np.argmax(gt))
    return gs[i], gl[i], gt[i]


# ----------------------------------------------------------------------
# convenience
# ----------------------------------------------------------------------

def db(x):
    """Linear power ratio -> dB."""
    return 10 * np.log10(np.abs(x))


def db_mag(x):
    """Complex amplitude -> dB (20 log10 |x|)."""
    return 20 * np.log10(np.abs(x))


def vswr(gamma):
    """Voltage standing wave ratio from a reflection coefficient."""
    g = np.abs(gamma)
    return (1 + g) / (1 - g)
