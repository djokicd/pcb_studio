"""
AmplifierChain: source -- input match -- device (.s2p) -- output match -- load.
"""

import numpy as np
import skrf

from . import metrics
from .matching import MatchingNetwork


class AmplifierChain:
    """
    Cascade of an input matching network, an active two-port device
    (from an ``.s2p`` file or an ``skrf.Network``) and an output
    matching network, between Z0 terminations.

    Parameters
    ----------
    device : str | skrf.Network
        Path to a Touchstone .s2p file, or an skrf two-port.
    input_match, output_match : MatchingNetwork | None
        Networks presenting Gamma_S / Gamma_L to the device.  ``None``
        means a direct (thru) connection.
    """

    def __init__(self, device, input_match=None, output_match=None):
        if isinstance(device, str):
            device = skrf.Network(device)
        if device.nports != 2:
            raise ValueError("device must be a two-port")
        self.device = device
        self.input_match = input_match
        self.output_match = output_match
        for mn, name in ((input_match, "input_match"),
                         (output_match, "output_match")):
            if mn is not None and not isinstance(mn, MatchingNetwork):
                raise TypeError(f"{name} must be a MatchingNetwork or None")

    # -- basic properties ---------------------------------------------

    @property
    def frequency(self):
        return self.device.frequency

    @property
    def z0(self):
        return float(np.real(self.device.z0[0, 0]))

    def _thru(self):
        from skrf.media import DefinedGammaZ0
        med = DefinedGammaZ0(frequency=self.frequency, z0=self.z0)
        return med.line(0, "m")

    @property
    def input_network(self):
        """Input MN over the device band (port 1 = source, port 2 = device)."""
        if self.input_match is None:
            return self._thru()
        return self.input_match.network(self.frequency)

    @property
    def output_network(self):
        """Output MN over the device band (port 1 = device, port 2 = load)."""
        if self.output_match is None:
            return self._thru()
        return self.output_match.network(self.frequency).flipped()

    @property
    def network(self):
        """Full cascade as an skrf.Network."""
        ntwk = self.input_network ** self.device ** self.output_network
        ntwk.name = "amplifier_chain"
        return ntwk

    # -- reflection coefficients actually presented to the device -----

    @property
    def gamma_s(self):
        """Gamma presented to the device input, vs frequency."""
        if self.input_match is None:
            return np.zeros(len(self.frequency), dtype=complex)
        return self.input_match.achieved_gamma(self.frequency)

    @property
    def gamma_l(self):
        """Gamma presented to the device output, vs frequency."""
        if self.output_match is None:
            return np.zeros(len(self.frequency), dtype=complex)
        return self.output_match.achieved_gamma(self.frequency)

    # -- gains ---------------------------------------------------------

    def transducer_gain(self):
        """G_T (linear) of the full chain vs frequency (= |S21_total|^2)."""
        return np.abs(self.network.s[:, 1, 0]) ** 2

    def transducer_gain_formula(self):
        """G_T from the device S-params + achieved Gamma_S/Gamma_L.

        Matches ``transducer_gain`` exactly when the matching networks
        are lossless; the difference is the matching-network loss."""
        return metrics.transducer_gain(self.device, self.gamma_s, self.gamma_l)

    def available_gain(self):
        return metrics.available_gain(self.device, self.gamma_s)

    def operating_gain(self):
        return metrics.operating_gain(self.device, self.gamma_l)

    def max_gain(self):
        """MAG/MSG of the bare device."""
        return metrics.max_gain(self.device)

    # -- stability -----------------------------------------------------

    def rollett_k(self):
        return metrics.rollett_k(self.device)

    def mu_load(self):
        return metrics.mu_load(self.device)

    def is_unconditionally_stable(self):
        return metrics.is_unconditionally_stable(self.device)

    # -- helpers -------------------------------------------------------

    def f_index(self, f):
        """Index of the frequency point closest to ``f`` (Hz)."""
        return int(np.argmin(np.abs(self.frequency.f - f)))

    def summary(self, f=None):
        """Formatted single-frequency summary (defaults to band center)."""
        idx = self.f_index(f) if f is not None else len(self.frequency) // 2
        fghz = self.frequency.f[idx] / 1e9
        net = self.network
        gs, gl = self.gamma_s[idx], self.gamma_l[idx]
        gin = metrics.input_reflection(self.device, self.gamma_l)[idx]
        gout = metrics.output_reflection(self.device, self.gamma_s)[idx]
        k = self.rollett_k()[idx]
        lines = [
            f"===== AmplifierChain summary @ {fghz:.4f} GHz =====",
            f" input match : {self.input_match!r}",
            f" output match: {self.output_match!r}",
            f" Gamma_S = {abs(gs):.4f} ∠ {np.degrees(np.angle(gs)):7.2f}°"
            f"   (VSWR {metrics.vswr(gs):.2f})",
            f" Gamma_L = {abs(gl):.4f} ∠ {np.degrees(np.angle(gl)):7.2f}°"
            f"   (VSWR {metrics.vswr(gl):.2f})",
            f" Gamma_in(device)  = {abs(gin):.4f} ∠ {np.degrees(np.angle(gin)):7.2f}°",
            f" Gamma_out(device) = {abs(gout):.4f} ∠ {np.degrees(np.angle(gout)):7.2f}°",
            f" |S21| chain = {metrics.db_mag(net.s[idx,1,0]):7.2f} dB "
            f"(G_T = {metrics.db(self.transducer_gain()[idx]):.2f} dB)",
            f" |S11| chain = {metrics.db_mag(net.s[idx,0,0]):7.2f} dB",
            f" |S22| chain = {metrics.db_mag(net.s[idx,1,1]):7.2f} dB",
            f" G_A = {metrics.db(self.available_gain()[idx]):.2f} dB, "
            f"G_P = {metrics.db(self.operating_gain()[idx]):.2f} dB, "
            f"MAG/MSG = {metrics.db(self.max_gain()[idx]):.2f} dB",
            f" K = {k:.3f}, |Delta| = {abs(metrics.delta(self.device)[idx]):.3f}, "
            f"mu = {self.mu_load()[idx]:.3f} "
            f"({'unconditionally stable' if self.is_unconditionally_stable()[idx] else 'conditionally stable'})",
        ]
        return "\n".join(lines)
