"""
Matching-network synthesis.

Every matching network is designed to present a chosen reflection
coefficient ``gamma`` (referenced to ``z0``) at its *device-facing* port
when the other port is terminated in ``z0``, at the design frequency
``f0``.  Evaluating the network over a frequency band shows the real,
realization-dependent off-frequency behavior.

Port convention of ``network(frequency)``:

    port 1 : external side (Z0 source or load)
    port 2 : device side

so with port 1 terminated in Z0, the reflection coefficient seen looking
into port 2 is simply S22.  ``AmplifierChain`` flips the network when it
is used on the output side.
"""

from abc import ABC, abstractmethod

import numpy as np
from .. import rfnet as skrf
from ..rfnet import DefinedGammaZ0

C0 = 299792458.0  # vacuum speed of light, m/s


# ----------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------

def gamma_from_ma(mag, ang_deg):
    """Reflection coefficient from magnitude and angle in degrees."""
    return mag * np.exp(1j * np.deg2rad(ang_deg))


def gamma_from_z(z, z0=50.0):
    """Reflection coefficient of impedance ``z`` referenced to ``z0``."""
    return (z - z0) / (z + z0)


def z_from_gamma(gamma, z0=50.0):
    """Impedance corresponding to reflection coefficient ``gamma``."""
    return z0 * (1 + gamma) / (1 - gamma)


def _reactance_to_element(x, w0):
    """Series reactance -> ('L', henry) or ('C', farad)."""
    if x > 0:
        return ("L", x / w0)
    return ("C", -1.0 / (w0 * x))


def _susceptance_to_element(b, w0):
    """Shunt susceptance -> ('C', farad) or ('L', henry)."""
    if b > 0:
        return ("C", b / w0)
    return ("L", -1.0 / (w0 * b))


# ----------------------------------------------------------------------
# base class
# ----------------------------------------------------------------------

class MatchingNetwork(ABC):
    """
    Base class for two-port matching networks.

    Parameters
    ----------
    gamma : complex
        Reflection coefficient to present at the device-facing port
        (|gamma| < 1), referenced to ``z0``.
    f0 : float
        Design frequency in Hz.
    z0 : float
        Reference / system impedance in ohm.
    """

    def __init__(self, gamma, f0, z0=50.0):
        gamma = complex(gamma)
        if not np.isfinite(gamma):
            raise ValueError(
                "gamma is NaN/inf -- if it came from simultaneous_match(), "
                "the device is not unconditionally stable at this frequency "
                "(K < 1): a simultaneous conjugate match does not exist. "
                "Choose Gamma_S/Gamma_L inside the stable regions instead "
                "(see metrics.source/load_stability_circle) or stabilize "
                "the device first.")
        if abs(gamma) >= 1.0:
            raise ValueError(f"|gamma| must be < 1, got {abs(gamma):.4f}")
        if f0 <= 0:
            raise ValueError("f0 must be positive")
        self.gamma = gamma
        self.f0 = float(f0)
        self.z0 = float(z0)
        self.elements = {}  # filled by subclasses: realization details

    # -- infrastructure ------------------------------------------------

    def _media(self, frequency, char_z0=None):
        """Lossless TEM media with vp = c (gamma = j*w/c)."""
        z = self.z0 if char_z0 is None else char_z0
        return DefinedGammaZ0(frequency=frequency,
                              gamma=1j * frequency.w / C0, z0=z)

    def _renorm(self, ntwk):
        """Make sure the network is referenced to self.z0 at both ports."""
        if not np.allclose(ntwk.z0, self.z0):
            ntwk.renormalize(self.z0)
        return ntwk

    @property
    def lambda0(self):
        """Guided wavelength at f0 (vp = c for the ideal media used here)."""
        return C0 / self.f0

    # -- main API ------------------------------------------------------

    @abstractmethod
    def network(self, frequency):
        """
        Return the matching network as an ``skrf.Network`` evaluated on
        ``frequency`` (an ``skrf.Frequency``).  Port 1 = external (Z0)
        side, port 2 = device side.
        """

    def achieved_gamma(self, frequency):
        """Reflection presented at the device port vs frequency (= S22)."""
        return self.network(frequency).s[:, 1, 1]

    def __repr__(self):
        return (f"{type(self).__name__}(gamma={self.gamma:.4f}, "
                f"f0={self.f0/1e9:g} GHz, z0={self.z0:g}, "
                f"elements={self.elements})")


# ----------------------------------------------------------------------
# lumped LC L-section
# ----------------------------------------------------------------------

class LMatch(MatchingNetwork):
    """
    Lumped-element L-section.

    Transforms the Z0 termination into Z_target = z0*(1+gamma)/(1-gamma)
    at f0 using one series and one shunt reactance.  Up to four solutions
    exist (two topologies x two element signs); select with ``solution``.

    Topologies (seen from the device):

    * ``'series-shunt'`` : device -- series X -- shunt B -- Z0
    * ``'shunt-series'`` : device -- shunt B -- series X -- Z0
    """

    def __init__(self, gamma, f0, z0=50.0, solution=0):
        super().__init__(gamma, f0, z0)
        self.solutions = self._solve()
        if not self.solutions:
            raise ValueError("no L-section solution found (gamma too close to 0?)")
        if solution >= len(self.solutions):
            raise ValueError(
                f"solution index {solution} out of range "
                f"({len(self.solutions)} solutions available)")
        self.solution = solution
        self.elements = self.solutions[solution]

    def _solve(self):
        z0 = self.z0
        w0 = 2 * np.pi * self.f0
        zt = z_from_gamma(self.gamma, z0)
        rt, xt = zt.real, zt.imag
        sols = []

        if abs(self.gamma) < 1e-9:
            # already matched -> a thru
            return [{"topology": "thru", "series": None, "shunt": None,
                     "X": 0.0, "B": 0.0}]

        # topology A: device -- series X -- shunt B -- Z0   (needs rt <= z0)
        if rt <= z0:
            b_mag = np.sqrt(max(z0 / rt - 1.0, 0.0)) / z0
            for b in (+b_mag, -b_mag):
                den = 1.0 + (b * z0) ** 2
                x = xt + b * z0 ** 2 / den
                sols.append({
                    "topology": "series-shunt",
                    "series": _reactance_to_element(x, w0) if abs(x) > 1e-15 else None,
                    "shunt": _susceptance_to_element(b, w0) if abs(b) > 1e-18 else None,
                    "X": x, "B": b,
                })

        # topology B: device -- shunt B -- series X -- Z0   (needs G_t <= 1/z0)
        yt = 1.0 / zt
        gt, bt = yt.real, yt.imag
        if gt <= 1.0 / z0:
            x_mag = np.sqrt(max(z0 / gt - z0 ** 2, 0.0))
            for x in (+x_mag, -x_mag):
                # Yin = jB + 1/(z0 + jX) must equal yt = gt + j*bt
                b_shunt = bt + x / (z0 ** 2 + x ** 2)
                sols.append({
                    "topology": "shunt-series",
                    "series": _reactance_to_element(x, w0) if abs(x) > 1e-15 else None,
                    "shunt": _susceptance_to_element(b_shunt, w0) if abs(b_shunt) > 1e-18 else None,
                    "X": x, "B": b_shunt,
                })
        return sols

    def network(self, frequency):
        med = self._media(frequency)
        sol = self.elements

        def series_ntwk(el):
            if el is None:
                return med.line(0, "m")
            kind, val = el
            return med.inductor(val) if kind == "L" else med.capacitor(val)

        def shunt_ntwk(el):
            if el is None:
                return med.line(0, "m")
            kind, val = el
            return (med.shunt_inductor(val) if kind == "L"
                    else med.shunt_capacitor(val))

        if sol["topology"] == "thru":
            return self._renorm(med.line(0, "m"))
        if sol["topology"] == "series-shunt":
            # from source (port 1): shunt first, then series toward device
            ntwk = shunt_ntwk(sol["shunt"]) ** series_ntwk(sol["series"])
        else:  # shunt-series
            ntwk = series_ntwk(sol["series"]) ** shunt_ntwk(sol["shunt"])
        return self._renorm(ntwk)


# ----------------------------------------------------------------------
# single-stub tuner
# ----------------------------------------------------------------------

class SingleStubMatch(MatchingNetwork):
    """
    Shunt single-stub tuner (all lines have characteristic impedance z0).

    Seen from the source:  Z0 -- [shunt stub] -- series line d -- device.
    The stub (open or short) placed at the Z0 termination creates the
    required |gamma|; the series line rotates its phase to the target.

    Parameters
    ----------
    stub_type : 'open' | 'short'
    solution : 0 or 1 (sign of the stub susceptance)
    """

    def __init__(self, gamma, f0, z0=50.0, stub_type="open", solution=0):
        super().__init__(gamma, f0, z0)
        if stub_type not in ("open", "short"):
            raise ValueError("stub_type must be 'open' or 'short'")
        self.stub_type = stub_type

        g = self.gamma
        lam = self.lambda0
        beta0 = 2 * np.pi / lam

        if abs(g) < 1e-9:
            self.elements = {"stub_type": stub_type, "l_stub": 0.0, "d_line": 0.0}
            return

        # normalized stub susceptance magnitude for |gamma|
        b_mag = 2 * abs(g) / np.sqrt(1 - abs(g) ** 2)
        b = b_mag if solution == 0 else -b_mag

        # reflection at the stub plane (stub in parallel with Z0 termination)
        y = 1.0 + 1j * b                     # normalized admittance
        gamma_stub = (1.0 - y) / (1.0 + y)   # = -jb / (2 + jb)

        # line rotates:  gamma_target = gamma_stub * exp(-2j*beta*d)
        theta = np.angle(gamma_stub) - np.angle(g)
        d = (theta / (2 * beta0)) % (lam / 2)

        # stub length for normalized susceptance b
        if stub_type == "open":               # b_in = tan(beta*l)
            l_stub = (np.arctan(b) / beta0) % (lam / 2)
        else:                                 # b_in = -cot(beta*l)
            l_stub = (np.arctan2(1.0, -b) / beta0) % (lam / 2)

        self.elements = {"stub_type": stub_type, "b_norm": b,
                         "l_stub": l_stub, "d_line": d}

    def network(self, frequency):
        med = self._media(frequency)
        el = self.elements
        if el["l_stub"] == 0.0 and el["d_line"] == 0.0:
            return self._renorm(med.line(0, "m"))
        if el["stub_type"] == "open":
            stub = med.shunt_delay_open(el["l_stub"], unit="m")
        else:
            stub = med.shunt_delay_short(el["l_stub"], unit="m")
        ntwk = stub ** med.line(el["d_line"], unit="m")
        return self._renorm(ntwk)


# ----------------------------------------------------------------------
# quarter-wave transformer + phase line
# ----------------------------------------------------------------------

class QuarterWaveMatch(MatchingNetwork):
    """
    Quarter-wave transformer followed by a z0 phase-rotation line.

    Seen from the source:  Z0 -- [lambda/4, Z1] -- [z0 line, d] -- device.
    The transformer (Z1 = sqrt(z0 * R)) produces a purely real mismatch R
    with |Gamma| = |gamma|; the z0 line rotates the phase to the target.
    """

    def __init__(self, gamma, f0, z0=50.0):
        super().__init__(gamma, f0, z0)
        g = self.gamma
        lam = self.lambda0
        beta0 = 2 * np.pi / lam

        if abs(g) < 1e-9:
            self.elements = {"z1": z0, "l_qw": 0.0, "d_line": 0.0}
            return

        r = z0 * (1 + abs(g)) / (1 - abs(g))   # real impedance, > z0
        z1 = np.sqrt(z0 * r)
        # rotate real gamma (angle 0) to target angle:
        # gamma = |g| * exp(-2j*beta*d)  ->  d = -angle(g)/(2*beta) mod lam/2
        d = (-np.angle(g) / (2 * beta0)) % (lam / 2)

        self.elements = {"z1": z1, "r_virtual": r, "l_qw": lam / 4, "d_line": d}

    def network(self, frequency):
        el = self.elements
        med = self._media(frequency)
        if el["l_qw"] == 0.0:
            return self._renorm(med.line(0, "m"))
        med_qw = self._media(frequency, char_z0=el["z1"])
        qw = med_qw.line(el["l_qw"], unit="m")
        ntwk = qw ** med.line(el["d_line"], unit="m")
        return self._renorm(ntwk)


# ----------------------------------------------------------------------
# ideal (frequency-flat) lossless matching network
# ----------------------------------------------------------------------

class IdealMatch(MatchingNetwork):
    """
    Ideal lossless, reciprocal two-port presenting ``gamma`` at every
    frequency (frequency-flat).  Useful as a reference against physical
    realizations:

        S = [[-conj(gamma), t], [t, gamma]],   t = sqrt(1 - |gamma|^2)
    """

    def __init__(self, gamma, f0, z0=50.0):
        super().__init__(gamma, f0, z0)
        self.elements = {"type": "ideal lossless two-port"}

    def network(self, frequency):
        g = self.gamma
        t = np.sqrt(1 - abs(g) ** 2)
        s = np.empty((len(frequency), 2, 2), dtype=complex)
        s[:, 0, 0] = -np.conj(g)
        s[:, 0, 1] = t
        s[:, 1, 0] = t
        s[:, 1, 1] = g
        return skrf.Network(frequency=frequency, s=s, z0=self.z0,
                            name="ideal_match")
