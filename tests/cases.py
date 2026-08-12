"""Benchmark case definitions, shared by the pytest suite and the GUI's
test runner. Each case builds a project model exactly as the GUI would,
runs it through scriptgen + octave, and evaluates metrics with explicit
reference values and acceptance windows."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import (stackup, sim_settings, rect, lumped_port, msl_port,
                     db, at, s_min, unwrapped_eeff, hammerstad, cbcpw)


def metric(label, value, lo, hi, unit='', ref=''):
    return {'label': label, 'value': round(float(value), 4),
            'lo': lo, 'hi': hi, 'unit': unit, 'ref': ref,
            'pass': bool(lo <= value <= hi)}


# ---------------------------------------------------------------- cases
def _series_r_model():
    return {
        'board': {'width': 40, 'height': 20},
        'stackup': stackup(er=4.2, h=1.524),
        'shapes': [rect('tl1', 1, 8.5, 18.5, 3), rect('tl2', 20.5, 8.5, 18.5, 3)],
        'components': [{'id': 9, 'ref': 'R1', 'ctype': 'R', 'value': 100,
                        'package': '0603', 'x': 20, 'y': 10, 'rot': 0, 'layer': 'top'}],
        'ports': [lumped_port(1, 1, 8.5, 0.5, 3, excite=True),
                  lumped_port(2, 38.5, 8.5, 0.5, 3)],
        'sim': sim_settings(fStart=0.05, fStop=0.5, endCriteria=-50,
                            maxTimesteps=40000, edgeRes=0.5, airMargin=15),
    }


def _series_r_eval(rows):
    r = at(rows, 0.1)
    zin = at(rows, 0.05)['zin']
    return [
        metric('|S11| @ 0.1 GHz', abs(r['s'][1]), 0.47, 0.53, '', '0.500 (theory)'),
        metric('|S21| @ 0.1 GHz', abs(r['s'][2]), 0.47, 0.53, '', '0.500 (theory)'),
        metric('Re(Zin) @ 0.05 GHz', zin.real, 138, 162, 'Ω', '150 Ω (100+50)'),
    ]


def _line_model():
    return {
        'board': {'width': 30, 'height': 16},
        'stackup': stackup(er=4.6, h=0.8),
        'shapes': [rect('tl', 5, 7.275, 20, 1.45, thirds=True)],
        'ports': [msl_port(1, 5, 7.275, 4, 1.45, '+x', excite=True),
                  msl_port(2, 21, 7.275, 4, 1.45, '-x')],
        'sim': sim_settings(boundary='PML_8', airMargin=25),
    }


def _line_eval(rows):
    _, eeff_ref = hammerstad(1.45, 0.8, 4.6)
    worst = max(db(r['s'][1]) for r in rows)
    eeffs = [e for f, e in unwrapped_eeff(rows, 2, 12e-3) if f > 1.5e9]
    mean = sum(eeffs) / len(eeffs)
    return [
        metric('worst |S11| in band', worst, -100, -20, 'dB',
               '< -20 dB (line is 50 Ω)'),
        metric('εeff from S21 phase', mean,
               round(eeff_ref * 0.94, 2), round(eeff_ref * 1.06, 2), '',
               f'{eeff_ref:.2f} (Hammerstad)'),
    ]


def _notch_model():
    return {
        'board': {'width': 25, 'height': 24},
        'stackup': stackup(er=3.66, h=0.254),
        'shapes': [
            rect('line', 1, 9.7, 23, 0.6, thirds=True),
            rect('stub', 12.2, 10.3, 0.6, 12.0, thirds=True),
        ],
        'ports': [msl_port(1, 1, 9.7, 2, 0.6, '+x', excite=True),
                  msl_port(2, 22, 9.7, 2, 0.6, '-x')],
        'sim': sim_settings(fStart=2.0, fStop=5.5, points=301, boundary='PML_8',
                            endCriteria=-35, maxTimesteps=80000, airMargin=20),
    }


def _notch_eval(rows):
    f_notch, depth = s_min(rows, 2)
    return [
        metric('notch frequency', f_notch, 3.3, 4.1, 'GHz',
               '≈3.7 GHz (12 mm λ/4 stub)'),
        metric('notch depth', depth, -100, -15, 'dB', '< -15 dB'),
        metric('passband S21 @ 2.2 GHz', db(at(rows, 2.2)['s'][2]),
               -3, 0.5, 'dB', '≈ 0 dB (through line)'),
    ]


def _gcpw_model():
    # 1.1 mm strip, 0.3 mm gaps, pours stitched to the bottom ground:
    # conformal mapping gives Z0 = 49.6 Ω, εeff = 3.06. The far end is
    # SHORTED to the pours by copper, so the lumped z-port reads a pure
    # shorted stub: Zin = jZ0·tan(βl). The quarter-wave peak pins εeff,
    # and Im(Zin) at exactly half that frequency (βl = π/4, tan = 1) IS
    # Z0 - measured with no component and no wave-port mode assumptions.
    # (An MSL wave port is deliberately NOT used: its U/I decomposition
    # breaks on the coplanar mode - |S11|²+|S21|² comes out above 1.)
    # The 0.3 mm slots are what the coplanar-gap meshing must resolve -
    # without it, the effective Z0 depends on where the mesh happens to
    # fall in the gap.
    pours = [rect('gnd_lo', 1, 1.0, 28, 6.15), rect('gnd_hi', 1, 8.85, 28, 6.15)]
    short = rect('short', 26, 6.0, 2.0, 4.0)
    vias = [{'id': 300 + k, 'x': 2.0 + 2.0 * (k % 13), 'y': 6.6 if k < 13 else 9.4,
             'drill': 0.3, 'pad': 0.6, 'from': 'bot', 'to': 'top',
             'mesh': {'lines': 1}} for k in range(26)]
    return {
        'board': {'width': 30, 'height': 16},
        'stackup': stackup(er=4.6, h=0.8),
        'shapes': [rect('tl', 4, 7.45, 22.5, 1.1, thirds=True), short] + pours,
        'vias': vias,
        'ports': [lumped_port(1, 4, 7.45, 1, 1.1, excite=True)],
        'sim': sim_settings(fStart=0.6, fStop=2.6, boundary='MUR', airMargin=25),
    }


def _gcpw_eval(rows):
    z0_ref, eeff_ref = cbcpw(1.1, 0.3, 0.8, 4.6)
    # quarter-wave peak: |Zin| maximum of the shorted stub. Electrical
    # length: port centre (x=4.5) to the short (x=26).
    peak = max(rows, key=lambda r: abs(r['zin']))
    f_ref = 3e8 / (4 * 21.5e-3 * math.sqrt(eeff_ref)) / 1e9
    # at half the MEASURED peak frequency βl = π/4 exactly, so the launch
    # -length uncertainty cancels and Im(Zin) = Z0·tan(π/4) = Z0
    half = min(rows, key=lambda r: abs(r['f'] - peak['f'] / 2))
    # ±10%: at this cell density FDTD carries a known capacitance bias
    # (finer substrate z cells move Z0 up toward the reference - see the
    # coplanar-gaps notes in the README). The gate exists to catch
    # regressions: the pre-slot-meshing grid measured 42.5 Ω here, and a
    # (miscalibrated) MSL wave port read 35 Ω.
    return [
        metric('Z₀ = Im(Zin) @ f_peak/2', half['zin'].imag,
               round(z0_ref * 0.90, 1), round(z0_ref * 1.10, 1), 'Ω',
               f'{z0_ref:.1f} Ω (conformal mapping)'),
        metric('λ/4 peak frequency', peak['f'] / 1e9,
               round(f_ref * 0.92, 2), round(f_ref * 1.08, 2), 'GHz',
               f'{f_ref:.2f} GHz (εeff {eeff_ref:.2f})'),
    ]


def _patch_model():
    return {
        'board': {'width': 60, 'height': 60},
        'stackup': stackup(er=3.38, h=1.524),
        'shapes': [rect('patch', 14, 10, 32, 40)],
        'ports': [lumped_port(1, 23.5, 29.5, 1, 1, excite=True)],
        'sim': sim_settings(fStart=1.8, fStop=3.0, boundary='MUR',
                            maxTimesteps=60000, airMargin=25),
    }


def _patch_eval(rows):
    f_res, depth = s_min(rows, 1)
    return [
        metric('resonant frequency', f_res, 2.25, 2.75, 'GHz',
               '≈2.5 GHz (cavity model, 32 mm)'),
        metric('S11 depth at resonance', depth, -100, -8, 'dB', '< -8 dB'),
    ]


CASES = {
    'series_r': {
        'title': 'Series 100 Ω resistor',
        'desc': 'Discrete R bridging a gap in a matched line. Circuit theory '
                'gives exactly |S11| = |S21| = 0.5 and Zin = 150 Ω.',
        'minutes': 1,
        'build': _series_r_model,
        'evaluate': _series_r_eval,
    },
    'line_z0': {
        'title': 'Microstrip Z₀ / εeff (MSL ports)',
        'desc': '1.45 mm line on 0.8 mm εr 4.6 — Hammerstad: Z₀ = 50.8 Ω, '
                'εeff = 3.45. Matched MSL feed must give low S11.',
        'minutes': 1,
        'build': _line_model,
        'evaluate': _line_eval,
    },
    'gcpw_z0': {
        'title': 'Grounded CPW Z₀ / εeff (0.3 mm slots)',
        'desc': '1.1 mm strip with 0.3 mm gaps to stitched pours on 0.8 mm '
                'εr 4.6 — conformal mapping: Z₀ = 49.6 Ω, εeff = 3.06. '
                'Exercises the coplanar-slot meshing.',
        'minutes': 2,
        'build': _gcpw_model,
        'evaluate': _gcpw_eval,
    },
    'stub_notch': {
        'title': 'Quarter-wave stub notch',
        'desc': '12 mm open stub on εr 3.66 / 0.254 mm (openEMS notch-filter '
                'tutorial cross-section): notch at ≈3.7 GHz.',
        'minutes': 2,
        'build': _notch_model,
        'evaluate': _notch_eval,
    },
    'patch': {
        'title': 'Patch antenna resonance',
        'desc': '32×40 mm patch on εr 3.38 / 1.524 mm (GUI demo project): '
                'fundamental resonance ≈2.5 GHz.',
        'minutes': 2,
        'build': _patch_model,
        'evaluate': _patch_eval,
    },
}
