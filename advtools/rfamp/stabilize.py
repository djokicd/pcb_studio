"""
Device stabilization: resistive loading and emitter series feedback.

``stabilize`` embeds the raw transistor two-port into

    port1 -- [R series in] -- [R shunt in] -- DEVICE* -- [R shunt out] -- [R series out] -- port2

where DEVICE* is the original device with an optional inductor in the
common (emitter/source) lead, applied as series-series feedback in the
Z-parameter domain:  Z' = Z + jw*Le * [[1,1],[1,1]].

Resistive loading buys stability (K, mu > 1) at the price of gain and
noise figure; small emitter inductance improves stability and input
match with much lower penalty at moderate frequencies.
"""

import numpy as np
from .. import rfnet as skrf


def _series_two_port(frequency, z, z0):
    """Two-port of a series impedance ``z`` (scalar or per-frequency)."""
    n = len(frequency)
    a = np.zeros((n, 2, 2), dtype=complex)
    a[:, 0, 0] = 1.0
    a[:, 1, 1] = 1.0
    a[:, 0, 1] = z
    s = skrf.network.a2s(a, z0=z0)
    return skrf.Network(frequency=frequency, s=s, z0=z0)


def _shunt_two_port(frequency, y, z0):
    """Two-port of a shunt admittance ``y`` (scalar or per-frequency)."""
    n = len(frequency)
    a = np.zeros((n, 2, 2), dtype=complex)
    a[:, 0, 0] = 1.0
    a[:, 1, 1] = 1.0
    a[:, 1, 0] = y
    s = skrf.network.a2s(a, z0=z0)
    return skrf.Network(frequency=frequency, s=s, z0=z0)


def add_emitter_inductance(device, l_emitter):
    """
    Series-series feedback: inductor ``l_emitter`` (H) in the common
    (emitter/source) lead of a two-port given in common-emitter/source
    configuration.  Adds jw*Le to all four Z-parameters.
    """
    if l_emitter == 0:
        return device.copy()
    z0 = device.z0
    z = skrf.network.s2z(device.s, z0=z0)
    ze = 1j * device.frequency.w * l_emitter
    z = z + ze[:, None, None]
    s = skrf.network.z2s(z, z0=z0)
    out = skrf.Network(frequency=device.frequency, s=s, z0=z0)
    out.name = (device.name or "device") + "+Le"
    return out


def stabilized_s_array(s, w, z0, r_series_in=0.0, r_shunt_in=0.0,
                       r_series_out=0.0, r_shunt_out=0.0, l_emitter=0.0,
                       c_series_in=0.0, c_series_out=0.0):
    """
    Fast vectorized equivalent of :func:`stabilize` operating directly on
    an S-parameter array of shape (nfreq, 2, 2) -- no skrf Network
    objects, just ABCD matrix products.  Used by the optimizer where
    thousands of evaluations are needed.

    ``w`` is the angular-frequency array, ``z0`` the (scalar, real)
    reference impedance.  Element order matches ``stabilize``.
    """
    n = len(w)
    if l_emitter > 0:
        z = skrf.network.s2z(s, z0=z0)
        z = z + (1j * w * l_emitter)[:, None, None]
        s = skrf.network.z2s(z, z0=z0)
    a = skrf.network.s2a(s, z0=z0)

    def series_a(r):
        m = np.zeros((n, 2, 2), dtype=complex)
        m[:, 0, 0] = m[:, 1, 1] = 1.0
        m[:, 0, 1] = r
        return m

    def shunt_a(r):
        m = np.zeros((n, 2, 2), dtype=complex)
        m[:, 0, 0] = m[:, 1, 1] = 1.0
        m[:, 1, 0] = 1.0 / r
        return m

    if r_shunt_in > 0:
        a = np.matmul(shunt_a(r_shunt_in), a)
    if r_series_in > 0:
        a = np.matmul(series_a(r_series_in), a)
    if c_series_in > 0:
        a = np.matmul(series_a(1.0 / (1j * w * c_series_in)), a)
    if r_shunt_out > 0:
        a = np.matmul(a, shunt_a(r_shunt_out))
    if r_series_out > 0:
        a = np.matmul(a, series_a(r_series_out))
    if c_series_out > 0:
        a = np.matmul(a, series_a(1.0 / (1j * w * c_series_out)))
    return skrf.network.a2s(a, z0=z0)


def stabilize(device, r_series_in=0.0, r_shunt_in=0.0,
              r_series_out=0.0, r_shunt_out=0.0, l_emitter=0.0,
              c_series_in=0.0, c_series_out=0.0):
    """
    Return a stabilized copy of ``device`` (an skrf two-port).

    Parameters
    ----------
    r_series_in, r_series_out : float
        Series resistance (ohm) at the input / output port; 0 = absent.
    r_shunt_in, r_shunt_out : float
        Shunt-to-ground resistance (ohm) at the input / output port;
        0 = absent (i.e. infinite).
    l_emitter : float
        Inductance (H) in the common lead (series-series feedback);
        0 = absent.
    c_series_in, c_series_out : float
        Series (DC-blocking / coupling) capacitance (F) at the input /
        output, outermost elements; 0 = absent.  Their rising impedance
        toward low frequencies reduces low-frequency loop gain and
        substantially improves stability there.

    Element order (from the input port):
    C -> series R -> shunt R -> device(+Le) -> shunt R -> series R -> C.
    """
    freq = device.frequency
    z0 = float(np.real(device.z0[0, 0]))
    w = freq.w

    ntwk = add_emitter_inductance(device, l_emitter)

    if r_shunt_in > 0:
        ntwk = _shunt_two_port(freq, 1.0 / r_shunt_in, z0) ** ntwk
    if r_series_in > 0:
        ntwk = _series_two_port(freq, r_series_in, z0) ** ntwk
    if c_series_in > 0:
        ntwk = _series_two_port(freq, 1.0 / (1j * w * c_series_in),
                                z0) ** ntwk
    if r_shunt_out > 0:
        ntwk = ntwk ** _shunt_two_port(freq, 1.0 / r_shunt_out, z0)
    if r_series_out > 0:
        ntwk = ntwk ** _series_two_port(freq, r_series_out, z0)
    if c_series_out > 0:
        ntwk = ntwk ** _series_two_port(freq, 1.0 / (1j * w * c_series_out),
                                        z0)

    ntwk.name = (device.name or "device") + "_stabilized"
    return ntwk
