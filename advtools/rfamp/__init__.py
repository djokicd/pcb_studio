"""
rfamp -- framework for analyzing RF amplifier chains:

    [Z0 source] -- input matching network -- active device (.s2p) -- output matching network -- [Z0 load]

Matching networks are synthesized to present an arbitrary reflection
coefficient (Gamma_S / Gamma_L) to the device at a design frequency f0,
with several physical realizations available (lumped LC L-section,
single-stub tuner, quarter-wave transformer + line, ideal lossless).
"""

from .matching import (
    MatchingNetwork,
    LMatch,
    SingleStubMatch,
    QuarterWaveMatch,
    IdealMatch,
    gamma_from_ma,
    gamma_from_z,
    z_from_gamma,
)
from .amplifier import AmplifierChain
from .stabilize import stabilize, add_emitter_inductance
from .optimize import optimize_match, optimize_amplifier
from . import metrics


__version__ = "0.1.0"

__all__ = [
    "MatchingNetwork",
    "LMatch",
    "SingleStubMatch",
    "QuarterWaveMatch",
    "IdealMatch",
    "AmplifierChain",
    "stabilize",
    "add_emitter_inductance",
    "optimize_match",
    "optimize_amplifier",
    "metrics",

    "gamma_from_ma",
    "gamma_from_z",
    "z_from_gamma",
]
