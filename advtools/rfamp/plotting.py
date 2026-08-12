"""Plot helpers for AmplifierChain."""

import numpy as np
import matplotlib.pyplot as plt
from skrf.plotting import smith

from . import metrics


def plot_chain_summary(chain, f0=None, filename=None, show=False,
                       stability_circles=False):
    """
    Four-panel overview of an AmplifierChain:

    1. chain |S21|, |S11|, |S22| in dB vs frequency
    2. device stability (K, mu) vs frequency
    3. |Gamma_S|, |Gamma_L| presented to the device vs frequency
    4. Smith chart: Gamma_S(f) and Gamma_L(f) trajectories
       (with the device stability circles at f0 if
       ``stability_circles=True`` -- requires ``f0``)

    Returns the matplotlib Figure.
    """
    f = chain.frequency.f
    fghz = f / 1e9
    net = chain.network

    fig, axes = plt.subplots(2, 2, figsize=(11, 8.5))
    ax1, ax2, ax3, ax4 = axes.flat

    # --- panel 1: chain S-params ---
    ax1.plot(fghz, metrics.db_mag(net.s[:, 1, 0]), label="|S21|")
    ax1.plot(fghz, metrics.db_mag(net.s[:, 0, 0]), label="|S11|")
    ax1.plot(fghz, metrics.db_mag(net.s[:, 1, 1]), label="|S22|")
    ax1.set_xlabel("Frequency (GHz)")
    ax1.set_ylabel("dB")
    ax1.set_title("Chain S-parameters")
    ax1.grid(True, alpha=0.3)
    ax1.legend()

    # --- panel 2: stability ---
    k = chain.rollett_k()
    mu = chain.mu_load()
    ax2.plot(fghz, k, label="K")
    ax2.plot(fghz, mu, label=r"$\mu$")
    ax2.axhline(1.0, color="k", lw=0.8, ls="--")
    ax2.set_xlabel("Frequency (GHz)")
    ax2.set_ylim(0, min(5, max(np.nanmax(k), np.nanmax(mu)) * 1.1))
    ax2.set_title("Device stability")
    ax2.grid(True, alpha=0.3)
    ax2.legend()

    # --- panel 3: presented reflection coefficients ---
    gs, gl = chain.gamma_s, chain.gamma_l
    ax3.plot(fghz, np.abs(gs), label=r"$|\Gamma_S|$")
    ax3.plot(fghz, np.abs(gl), label=r"$|\Gamma_L|$")
    for mn, color in ((chain.input_match, "C0"), (chain.output_match, "C1")):
        if mn is not None:
            ax3.plot(mn.f0 / 1e9, abs(mn.gamma), "*", ms=12, color=color)
    ax3.set_xlabel("Frequency (GHz)")
    ax3.set_ylabel(r"$|\Gamma|$")
    ax3.set_ylim(0, 1)
    ax3.set_title("Reflection presented to device (stars: design targets)")
    ax3.grid(True, alpha=0.3)
    ax3.legend()

    # --- panel 4: Smith chart ---
    smith(ax=ax4, draw_labels=True)
    ax4.plot(gs.real, gs.imag, "-", color="C0", label=r"$\Gamma_S(f)$")
    ax4.plot(gl.real, gl.imag, "-", color="C1", label=r"$\Gamma_L(f)$")
    for mn, color in ((chain.input_match, "C0"), (chain.output_match, "C1")):
        if mn is not None:
            ax4.plot(mn.gamma.real, mn.gamma.imag, "*", ms=12, color=color)
    if f0 is not None:
        idx = chain.f_index(f0)
        ax4.plot(gs[idx].real, gs[idx].imag, "o", color="C0", mfc="none")
        ax4.plot(gl[idx].real, gl[idx].imag, "o", color="C1", mfc="none")
        if stability_circles:
            cs = metrics.source_stability_circle(chain.device, idx)
            cl = metrics.load_stability_circle(chain.device, idx)
            ax4.plot(cs.real, cs.imag, "--", color="C0", lw=1,
                     label="src stab. circle")
            ax4.plot(cl.real, cl.imag, "--", color="C1", lw=1,
                     label="load stab. circle")
            ax4.set_xlim(-1.6, 1.6)
            ax4.set_ylim(-1.6, 1.6)
    ax4.legend(loc="lower right", fontsize=8)
    ax4.set_title("Smith chart")

    fig.tight_layout()
    if filename:
        fig.savefig(filename, dpi=140)
    if show:
        plt.show()
    return fig
