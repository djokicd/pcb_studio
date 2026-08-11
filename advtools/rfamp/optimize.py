"""
Band-target optimization of Gamma_S / Gamma_L.

Given a (possibly stabilized) device, a band of interest and targets for
chain gain |S21|, input SWR and output SWR, search the two reflection
coefficients so the *realized* chain (with the chosen matching-network
realizations synthesized at the band center) meets the targets at every
frequency point inside the band.
"""

import numpy as np
from .. import rfnet as skrf
from scipy.optimize import differential_evolution

from . import metrics
from .amplifier import AmplifierChain
from .matching import LMatch
from .stabilize import stabilized_s_array


def _safe_swr(s11):
    g = np.clip(np.abs(s11), 0.0, 0.9999)
    return (1 + g) / (1 - g)


def _band_device(device, f_low, f_high, n_points=None):
    """
    Device restricted to [f_low, f_high].

    If the measured grid is too sparse inside the band (or an explicit
    density ``n_points`` is requested), the S-parameters are cubically
    interpolated onto a uniform grid of ``n_points`` frequencies.
    Interpolation only -- a band outside the measured range raises.

    Returns (network, interpolated_flag).
    """
    if f_low < device.f[0] or f_high > device.f[-1]:
        raise ValueError(
            f"band [{f_low/1e9:g}, {f_high/1e9:g}] GHz exceeds the "
            f"measured range [{device.f[0]/1e9:g}, {device.f[-1]/1e9:g}] "
            "GHz -- refusing to extrapolate S-parameters")
    if f_high <= f_low:
        raise ValueError("f_high must be greater than f_low")

    # f_low / f_high are arbitrary within the measured range: the grid
    # always contains the exact band edges (interpolated when they fall
    # between samples) plus every measured point inside the band.
    mask = (device.f >= f_low) & (device.f <= f_high)
    f_meas = device.f[mask]
    base = np.union1d(f_meas, [f_low, f_high])

    if n_points is None:
        # auto: densify only when the band grid is too sparse
        n_points = 11 if len(base) < 11 else 0
    n_points = int(n_points)

    grid = base
    if n_points > len(base):
        grid = np.union1d(f_meas, np.linspace(f_low, f_high, n_points))

    interpolated = len(grid) > len(f_meas)
    if not interpolated:
        freq = skrf.Frequency.from_f(f_meas, unit="hz")
        return skrf.Network(frequency=freq, s=device.s[mask],
                            z0=float(np.real(device.z0[0, 0]))), False
    freq = skrf.Frequency.from_f(grid, unit="hz")
    return device.interpolate(freq, kind="cubic"), True


def _de_callback(cost, progress):
    """Wrap a user progress callable into a scipy DE callback."""
    if progress is None:
        return None
    state = {"gen": 0}

    def cb(xk, convergence=0.0):
        state["gen"] += 1
        progress(state["gen"], cost(xk), convergence)

    return cb


def optimize_match(device, f_low, f_high, target_gain_db,
                   max_swr_in=2.0, max_swr_out=2.0,
                   realization_in=None, realization_out=None,
                   f0=None, gamma_max=0.95, n_band_points=None,
                   seed=1, maxiter=40, popsize=12, progress=None):
    """
    Optimize Gamma_S and Gamma_L for band targets.

    Parameters
    ----------
    device : skrf.Network
        Two-port (stabilize it first if needed -- the optimizer treats
        it as given).
    f_low, f_high : float
        Band of interest in Hz.
    target_gain_db : float
        Minimum chain |S21| in dB to hold over the band.
    max_swr_in, max_swr_out : float
        Maximum chain input/output SWR over the band.
    realization_in, realization_out : callable(gamma, f0) -> MatchingNetwork
        Matching-network factories (default: lumped LMatch).
    f0 : float
        Synthesis frequency for the networks (default: band center).
    gamma_max : float
        Search-space limit on |Gamma|.
    n_band_points : int or None
        Density of the in-band evaluation grid.  None (default) uses the
        measured points, cubically interpolating up to 11 points when the
        band holds fewer than that; an explicit value forces cubic
        interpolation onto that many uniformly spaced points (when it
        exceeds the measured in-band count).

    Returns
    -------
    (gs, gl, result) where ``result`` is a dict with the achieved
    worst-case values over the band and a ``met`` flag per target.
    """
    if realization_in is None:
        realization_in = lambda g, f: LMatch(g, f)
    if realization_out is None:
        realization_out = lambda g, f: LMatch(g, f)

    dev_band, interpolated = _band_device(device, f_low, f_high,
                                          n_points=n_band_points)
    if f0 is None:
        f0 = 0.5 * (f_low + f_high)

    def make_chain(gs, gl):
        mn_in = realization_in(gs, f0) if abs(gs) > 1e-6 else None
        mn_out = realization_out(gl, f0) if abs(gl) > 1e-6 else None
        return AmplifierChain(dev_band, mn_in, mn_out)

    def evaluate(gs, gl):
        chain = make_chain(gs, gl)
        s = chain.network.s
        s21_db = 20 * np.log10(np.abs(s[:, 1, 0]) + 1e-12)
        swr_i = _safe_swr(s[:, 0, 0])
        swr_o = _safe_swr(s[:, 1, 1])
        gin = np.abs(metrics.input_reflection(dev_band, chain.gamma_l))
        gout = np.abs(metrics.output_reflection(dev_band, chain.gamma_s))
        return s21_db, swr_i, swr_o, gin, gout

    def cost(x):
        gs = complex(x[0], x[1])
        gl = complex(x[2], x[3])
        if abs(gs) > gamma_max or abs(gl) > gamma_max:
            return 1e3 * (1 + abs(gs) + abs(gl))
        try:
            s21_db, swr_i, swr_o, gin, gout = evaluate(gs, gl)
        except (ValueError, FloatingPointError):
            return 1e6
        c = np.mean(np.maximum(0.0, target_gain_db - s21_db) ** 2)
        c += 4.0 * np.mean(np.maximum(0.0, swr_i - max_swr_in) ** 2)
        c += 4.0 * np.mean(np.maximum(0.0, swr_o - max_swr_out) ** 2)
        # hard guard against operating points at the edge of oscillation
        c += 1e4 * np.sum(np.maximum(0.0, np.maximum(gin, gout) - 0.98) ** 2)
        return float(c)

    bnd = [(-gamma_max, gamma_max)] * 4
    res = differential_evolution(cost, bounds=bnd, seed=seed,
                                 maxiter=maxiter, popsize=popsize,
                                 tol=1e-8, polish=True,
                                 callback=_de_callback(cost, progress))
    gs = complex(res.x[0], res.x[1])
    gl = complex(res.x[2], res.x[3])

    s21_db, swr_i, swr_o, gin, gout = evaluate(gs, gl)
    result = {
        "f0": f0,
        "band": (f_low, f_high),
        "n_points": len(dev_band.f),
        "interpolated": interpolated,
        "min_gain_db": float(s21_db.min()),
        "max_swr_in": float(swr_i.max()),
        "max_swr_out": float(swr_o.max()),
        "max_gamma_in": float(gin.max()),
        "max_gamma_out": float(gout.max()),
        "met": {
            "gain": bool(s21_db.min() >= target_gain_db - 0.05),
            "swr_in": bool(swr_i.max() <= max_swr_in + 0.02),
            "swr_out": bool(swr_o.max() <= max_swr_out + 0.02),
        },
        "cost": float(res.fun),
    }
    return gs, gl, result


def optimize_amplifier(device, f_low, f_high, target_gain_db,
                       max_swr_in=2.0, max_swr_out=2.0,
                       realization_in=None, realization_out=None,
                       f0=None, gamma_max=0.95, n_band_points=None,
                       require_stability=True, stability_margin=1.02,
                       rs_max=50.0, rp_min=50.0, le_max=5e-9,
                       c_series_in=0.0, c_series_out=0.0,
                       seed=1, maxiter=45, popsize=10, progress=None):
    """
    Jointly optimize the stabilization network AND Gamma_S / Gamma_L.

    Search space (9 parameters): Re/Im of both reflection coefficients
    plus series input/output resistance, shunt input/output conductance
    (0 = element absent, resistance >= ``rp_min`` when present) and the
    emitter inductance (up to ``le_max``).

    The cost combines the band targets of :func:`optimize_match` with,
    when ``require_stability`` is True, a strong penalty unless the
    stabilized device is unconditionally stable (mu and mu' >=
    ``stability_margin``) over the device's ENTIRE measured range --
    an amplifier must not oscillate out of band either.

    Returns
    -------
    (gs, gl, stab, result) where ``stab`` is a dict of stabilize()
    keyword arguments and ``result`` extends the optimize_match report
    with the achieved stability figures.
    """
    if realization_in is None:
        realization_in = lambda g, f: LMatch(g, f)
    if realization_out is None:
        realization_out = lambda g, f: LMatch(g, f)

    raw_band, interpolated = _band_device(device, f_low, f_high,
                                          n_points=n_band_points)
    if f0 is None:
        f0 = 0.5 * (f_low + f_high)

    z0 = float(np.real(device.z0[0, 0]))
    w_band = raw_band.frequency.w
    w_full = device.frequency.w
    freq_band = raw_band.frequency
    gp_max = 1.0 / rp_min

    def stab_from_x(x):
        rs_i, gp_i, rs_o, gp_o, le = x[4:]
        return dict(
            r_series_in=rs_i if rs_i > 0.05 else 0.0,
            r_shunt_in=1.0 / gp_i if gp_i > 1e-5 else 0.0,
            r_series_out=rs_o if rs_o > 0.05 else 0.0,
            r_shunt_out=1.0 / gp_o if gp_o > 1e-5 else 0.0,
            l_emitter=le if le > 1e-12 else 0.0,
            # coupling caps are user-fixed, not searched
            c_series_in=c_series_in,
            c_series_out=c_series_out,
        )

    def band_metrics(gs, gl, stab):
        s_band = stabilized_s_array(raw_band.s, w_band, z0, **stab)
        dev_band = skrf.Network(frequency=freq_band, s=s_band, z0=z0)
        mn_in = realization_in(gs, f0) if abs(gs) > 1e-6 else None
        mn_out = realization_out(gl, f0) if abs(gl) > 1e-6 else None
        chain = AmplifierChain(dev_band, mn_in, mn_out)
        s = chain.network.s
        s21_db = 20 * np.log10(np.abs(s[:, 1, 0]) + 1e-12)
        swr_i = _safe_swr(s[:, 0, 0])
        swr_o = _safe_swr(s[:, 1, 1])
        gin = np.abs(metrics.input_reflection(dev_band, chain.gamma_l))
        gout = np.abs(metrics.output_reflection(dev_band, chain.gamma_s))
        return s21_db, swr_i, swr_o, gin, gout

    def full_range_mu(stab):
        s_full = stabilized_s_array(device.s, w_full, z0, **stab)
        return np.minimum(metrics.mu_load(s_full), metrics.mu_source(s_full))

    def cost(x):
        gs = complex(x[0], x[1])
        gl = complex(x[2], x[3])
        if abs(gs) > gamma_max or abs(gl) > gamma_max:
            return 1e3 * (1 + abs(gs) + abs(gl))
        stab = stab_from_x(x)
        try:
            s21_db, swr_i, swr_o, gin, gout = band_metrics(gs, gl, stab)
        except (ValueError, FloatingPointError):
            return 1e6
        c = np.mean(np.maximum(0.0, target_gain_db - s21_db) ** 2)
        c += 4.0 * np.mean(np.maximum(0.0, swr_i - max_swr_in) ** 2)
        c += 4.0 * np.mean(np.maximum(0.0, swr_o - max_swr_out) ** 2)
        c += 1e4 * np.sum(np.maximum(0.0, np.maximum(gin, gout) - 0.98) ** 2)
        if require_stability:
            mu = full_range_mu(stab)
            c += 50.0 * np.mean(np.maximum(0.0, stability_margin - mu) ** 2)
        return float(c)

    bnd = ([(-gamma_max, gamma_max)] * 4 +
           [(0.0, rs_max), (0.0, gp_max),
            (0.0, rs_max), (0.0, gp_max),
            (0.0, le_max)])
    res = differential_evolution(cost, bounds=bnd, seed=seed,
                                 maxiter=maxiter, popsize=popsize,
                                 tol=1e-8, polish=True,
                                 callback=_de_callback(cost, progress))
    gs = complex(res.x[0], res.x[1])
    gl = complex(res.x[2], res.x[3])
    stab = stab_from_x(res.x)

    s21_db, swr_i, swr_o, gin, gout = band_metrics(gs, gl, stab)
    mu = full_range_mu(stab)
    result = {
        "f0": f0,
        "band": (f_low, f_high),
        "n_points": len(raw_band.f),
        "interpolated": interpolated,
        "stabilization": stab,
        "min_gain_db": float(s21_db.min()),
        "max_swr_in": float(swr_i.max()),
        "max_swr_out": float(swr_o.max()),
        "max_gamma_in": float(gin.max()),
        "max_gamma_out": float(gout.max()),
        "min_mu_full_range": float(mu.min()),
        "unconditionally_stable": bool(mu.min() > 1.0),
        "met": {
            "gain": bool(s21_db.min() >= target_gain_db - 0.05),
            "swr_in": bool(swr_i.max() <= max_swr_in + 0.02),
            "swr_out": bool(swr_o.max() <= max_swr_out + 0.02),
            "stability": bool(not require_stability or mu.min() > 1.0),
        },
        "cost": float(res.fun),
    }
    return gs, gl, stab, result
