"""Shared helpers for the test suite: model builders, the octave sim runner
and analytic reference formulas."""
import cmath
import math
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(__file__).resolve().parent / '_work'

C0 = 299792458.0


# ---------------------------------------------------------------- models
def stackup(er=4.6, h=0.8, tand=0.0):
    return [
        {'id': 'top', 'name': 'Top', 'type': 'conductor', 'thickness': 0.035, 'fill': False},
        {'id': 'core', 'name': 'core', 'type': 'dielectric', 'thickness': h, 'er': er, 'tand': tand},
        {'id': 'bot', 'name': 'Bottom', 'type': 'conductor', 'thickness': 0.035, 'fill': True},
    ]


def sim_settings(**kw):
    base = {
        'fStart': 1, 'fStop': 3, 'points': 201, 'boundary': 'MUR',
        'endCriteria': -40, 'maxTimesteps': 60000, 'meshDiv': 20,
        'edgeRes': None, 'meshMerge': 0.1, 'airMargin': 25,
        'dumpJ': False, 'dumpJt': False,
    }
    base.update(kw)
    return base


def lumped_port(number, x, y, w, h, excite=False, r=50):
    return {'id': 100 + number, 'number': number, 'x': x, 'y': y, 'w': w, 'h': h,
            'direction': 'z', 'layerFrom': 'bot', 'layerTo': 'top',
            'impedance': r, 'excite': excite}


def msl_port(number, x, y, w, h, orient, excite=False, r=50):
    return {'id': 100 + number, 'ptype': 'msl', 'number': number,
            'x': x, 'y': y, 'w': w, 'h': h, 'orient': orient,
            'layerFrom': 'bot', 'layerTo': 'top', 'impedance': r, 'excite': excite}


def rect(name, x, y, w, h, thirds=False, res=None):
    return {'id': abs(hash(name)) % 10000 + 1000, 'name': name, 'type': 'rect',
            'layer': 'top', 'priority': 10, 'x': x, 'y': y, 'w': w, 'h': h,
            'mesh': {'thirds': thirds, 'res': res}}


# ---------------------------------------------------------------- runner
def run_sim(model, tag, timeout=420):
    """Generate the Octave script for `model`, run it, return parsed rows."""
    from scriptgen import generate_script
    d = WORK / tag
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / 'pcb_sim.m').write_text(generate_script(model))
    res = subprocess.run(
        ['octave', '--no-gui', 'pcb_sim.m'],
        cwd=d, capture_output=True, text=True, timeout=timeout)
    log = res.stdout + res.stderr
    assert 'GUI_MARKER: done' in log, f'simulation failed:\n{log[-2000:]}'
    return parse_sparams(d / 'sparams.csv')


def parse_sparams(path):
    """Rows of {'f': Hz, 's': {portnum: S_port,exc}, 'refl': {portnum: S_ii},
    'zin': complex}. The excited-port columns come first in the file, so
    the first occurrence of a port number wins for 's'."""
    lines = Path(path).read_text().strip().split('\n')
    header = lines[0].lstrip('#').split(',')
    out = []
    for line in lines[1:]:
        v = [float(x) for x in line.split(',')]
        row = {'f': v[0], 's': {}, 'refl': {}}
        for ci in range(1, len(header) - 2, 2):
            label = header[ci]           # e.g. S21_re / S22_re
            i, j = int(label[1]), int(label[2])
            val = complex(v[ci], v[ci + 1])
            if i == j:
                row['refl'][i] = val
            if i not in row['s']:
                row['s'][i] = val
        row['zin'] = complex(v[-2], v[-1])
        out.append(row)
    return out


def db(x):
    return 20 * math.log10(max(abs(x), 1e-12))


def at(rows, f_ghz):
    return min(rows, key=lambda r: abs(r['f'] - f_ghz * 1e9))


def s_min(rows, num):
    """(freq_GHz, dB) of the minimum of |S_num,exc|."""
    r = min(rows, key=lambda r: abs(r['s'][num]))
    return r['f'] / 1e9, db(r['s'][num])


def unwrapped_eeff(rows, num, length_m):
    """Effective permittivity from the unwrapped transmission phase."""
    prev = None
    unw = 0.0
    out = []
    for r in rows:
        ph = cmath.phase(r['s'][num])
        if prev is not None:
            dp = ph - prev
            if dp > math.pi:
                dp -= 2 * math.pi
            if dp < -math.pi:
                dp += 2 * math.pi
            unw += dp
        else:
            unw = ph
        prev = ph
        beta_l = -unw
        if beta_l > 0.3:   # ignore the noisy near-zero-phase start
            out.append((r['f'], (beta_l * C0 / (2 * math.pi * r['f'] * length_m)) ** 2))
    return out


# ---------------------------------------------------------------- analytic
def hammerstad(w_mm, h_mm, er):
    """(Z0, eeff) of a zero-thickness microstrip (Hammerstad-Jensen)."""
    u = w_mm / h_mm
    eeff = (er + 1) / 2 + (er - 1) / 2 / math.sqrt(1 + 12 / u)
    if u >= 1:
        z0 = 120 * math.pi / (math.sqrt(eeff) * (u + 1.393 + 0.667 * math.log(u + 1.444)))
    else:
        z0 = 60 / math.sqrt(eeff) * math.log(8 / u + u / 4)
    return z0, eeff
