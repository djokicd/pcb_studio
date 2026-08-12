"""RF amplifier chain designer.

    [Z0 source] - input match - device (.s2p) - output match - [Z0 load]

Wraps the `rfamp` framework: matching networks synthesized to present a
chosen Gamma_S / Gamma_L at f0, optional resistive/inductive
stabilization, band optimization of both reflection coefficients (and
optionally the stabilization network itself), and the resulting gains,
SWR and stability over the device band.

Device files come from the project's own Touchstone library (`devices/`),
so anything already imported for an S-parameter device co-simulation is
immediately available here.
"""
import math
import os
import threading

import numpy as np

import skrf
from ..rfamp import (AmplifierChain, IdealMatch, LMatch, QuarterWaveMatch,
                     SingleStubMatch, metrics)
from ..rfamp.optimize import optimize_amplifier, optimize_match
from ..rfamp.stabilize import stabilize

TOOL = {
    'id': 'rf_chain',
    'name': 'RF amplifier chain',
    'group': 'RF design',
    'icon': '⚡',
    'description': 'Match a transistor .s2p into a gain stage: synthesize '
                   'the input/output networks, check stability and SWR over '
                   'the band, and read off the element values.',
}

DEV_ROOT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), 'devices')

REALIZATIONS = [
    ['lmatch', 'Lumped L-section'],
    ['stub_open', 'Shunt open stub + line'],
    ['stub_short', 'Shunt short stub + line'],
    ['qwave', 'Quarter-wave transformer'],
    ['ideal', 'Ideal (lossless reference)'],
]

# background optimizer state, so a long search does not block the request
_JOBS = {}
_LOCK = threading.Lock()


# ------------------------------------------------------------------ helpers
def device_files():
    """Two-port Touchstone files in the project's device library."""
    out = []
    try:
        for name in sorted(os.listdir(DEV_ROOT)):
            if not name.lower().endswith(('.s2p', '.snp')):
                continue
            try:
                net = skrf.Network(os.path.join(DEV_ROOT, name))
            except Exception:
                continue
            if net.nports != 2 or len(net.f) < 2:
                continue
            out.append({'value': name, 'label': name,
                        'f0': float(net.f[0]), 'f1': float(net.f[-1]),
                        'points': len(net.f)})
    except FileNotFoundError:
        pass
    return out


def _load(name):
    if not name:
        raise ValueError('no device selected')
    safe = os.path.basename(str(name))
    path = os.path.join(DEV_ROOT, safe)
    if not os.path.isfile(path):
        raise ValueError(f'device file {safe!r} is not in the library')
    net = skrf.Network(path)
    if net.nports != 2:
        raise ValueError(f'{safe} is not a two-port')
    return net


def _stabilized(dev, p):
    """Apply the stabilization section when any element is non-zero."""
    keys = ('r_series_in', 'r_shunt_in', 'r_series_out', 'r_shunt_out',
            'l_emitter', 'c_series_in', 'c_series_out')
    vals = {k: float(p.get(k) or 0.0) for k in keys}
    vals['l_emitter'] *= 1e-9        # nH -> H
    vals['c_series_in'] *= 1e-12     # pF -> F
    vals['c_series_out'] *= 1e-12
    if not any(vals.values()):
        return dev, vals
    return stabilize(dev, **vals), vals


def _match(kind, gamma, f0, z0, solution=0):
    if kind == 'ideal' or gamma is None:
        return IdealMatch(gamma if gamma is not None else 0j, f0, z0=z0)
    if kind == 'lmatch':
        return LMatch(gamma, f0, z0=z0, solution=int(solution))
    if kind == 'stub_open':
        return SingleStubMatch(gamma, f0, z0=z0, stub_type='open',
                               solution=int(solution))
    if kind == 'stub_short':
        return SingleStubMatch(gamma, f0, z0=z0, stub_type='short',
                               solution=int(solution))
    if kind == 'qwave':
        return QuarterWaveMatch(gamma, f0, z0=z0)
    raise ValueError(f'unknown realization {kind!r}')


def _gamma(mag, ang_deg):
    return float(mag) * np.exp(1j * math.radians(float(ang_deg)))


def _fin(x, default=None):
    """JSON cannot carry NaN/Inf; clamp them to something transportable."""
    if x is None:
        return default
    v = float(np.real(x))
    if not np.isfinite(v):
        return default
    return round(v, 6)


# --------------------------------------------------------------- schematic
def _element_label(kind, value):
    if kind == 'L':
        return _eng(value, 'H')
    if kind == 'C':
        return _eng(value, 'F')
    return str(value)


def _eng(v, unit):
    v = float(v)
    if v == 0:
        return f'0 {unit}'
    for exp, pre in ((-12, 'p'), (-9, 'n'), (-6, 'µ'), (-3, 'm'), (0, '')):
        if abs(v) < 10 ** (exp + 3):
            return f'{v / 10 ** exp:.3g} {pre}{unit}'
    return f'{v:.3g} {unit}'


def _mn_blocks(mn, side, lam0):
    """Schematic blocks for one matching network, ordered from the source
    side toward the device. `side` is 'in' or 'out' (the output network is
    drawn mirrored by the renderer)."""
    if mn is None or isinstance(mn, IdealMatch):
        return [{'type': 'ideal', 'label': 'ideal match'}] if mn else []
    el = mn.elements
    blocks = []
    if isinstance(mn, LMatch):
        if el.get('topology') == 'thru':
            return [{'type': 'wire', 'label': 'thru'}]
        series = el.get('series')
        shunt = el.get('shunt')
        seq = ([('shunt', shunt), ('series', series)]
               if el.get('topology') == 'series-shunt'
               else [('series', series), ('shunt', shunt)])
        for role, item in seq:
            if not item:
                continue
            kind, val = item
            blocks.append({'type': f'{role}_{kind.lower()}',
                           'label': _element_label(kind, val),
                           'sub': 'L' if kind == 'L' else 'C'})
        return blocks
    if isinstance(mn, SingleStubMatch):
        if not el.get('l_stub') and not el.get('d_line'):
            return [{'type': 'wire', 'label': 'thru'}]
        blocks.append({'type': 'stub_' + el.get('stub_type', 'open'),
                       'label': f"{el['l_stub'] * 1e3:.2f} mm",
                       'sub': f"{el['l_stub'] / lam0:.3f} λ" if lam0 else ''})
        blocks.append({'type': 'line',
                       'label': f"{el['d_line'] * 1e3:.2f} mm",
                       'sub': f"{el['d_line'] / lam0:.3f} λ" if lam0 else ''})
        return blocks
    if isinstance(mn, QuarterWaveMatch):
        if not el.get('l_qw'):
            return [{'type': 'wire', 'label': 'thru'}]
        blocks.append({'type': 'line_z', 'label': f"{el['z1']:.1f} Ω",
                       'sub': f"λ/4 = {el['l_qw'] * 1e3:.2f} mm"})
        if el.get('d_line'):
            blocks.append({'type': 'line',
                           'label': f"{el['d_line'] * 1e3:.2f} mm",
                           'sub': f"{el['d_line'] / lam0:.3f} λ" if lam0 else ''})
        return blocks
    return [{'type': 'box', 'label': type(mn).__name__}]


def _schematic(chain, f0, gs, gl, stab_vals, dev_name):
    lam0 = 299792458.0 / f0 if f0 else 0.0
    left = _mn_blocks(chain.input_match, 'in', lam0)
    right = _mn_blocks(chain.output_match, 'out', lam0)
    stab = []
    if stab_vals.get('c_series_in'):
        stab.append(['Cin', _eng(stab_vals['c_series_in'], 'F')])
    if stab_vals.get('r_series_in'):
        stab.append(['Rs,in', f"{stab_vals['r_series_in']:.1f} Ω"])
    if stab_vals.get('r_shunt_in'):
        stab.append(['Rp,in', f"{stab_vals['r_shunt_in']:.1f} Ω"])
    if stab_vals.get('l_emitter'):
        stab.append(['Le', _eng(stab_vals['l_emitter'], 'H')])
    if stab_vals.get('r_shunt_out'):
        stab.append(['Rp,out', f"{stab_vals['r_shunt_out']:.1f} Ω"])
    if stab_vals.get('r_series_out'):
        stab.append(['Rs,out', f"{stab_vals['r_series_out']:.1f} Ω"])
    if stab_vals.get('c_series_out'):
        stab.append(['Cout', _eng(stab_vals['c_series_out'], 'F')])
    return {
        'z0': chain.z0,
        'f0': f0,
        'device': dev_name,
        'gammaS': [float(np.real(gs)), float(np.imag(gs))],
        'gammaL': [float(np.real(gl)), float(np.imag(gl))],
        'input': left,
        'output': right,
        'stab': stab,
    }


# ----------------------------------------------------------------- analysis
def _analyse(p):
    dev_raw = _load(p.get('device'))
    dev, stab_vals = _stabilized(dev_raw, p)
    z0 = float(p.get('z0') or np.real(dev.z0[0, 0]) or 50.0)
    f0 = float(p.get('f0') or dev.f[len(dev.f) // 2])
    f0 = min(max(f0, float(dev.f[0])), float(dev.f[-1]))

    gs = _gamma(p.get('gs_mag', 0.0), p.get('gs_ang', 0.0))
    gl = _gamma(p.get('gl_mag', 0.0), p.get('gl_ang', 0.0))
    chain = AmplifierChain(
        dev,
        input_match=_match(p.get('mn_in', 'lmatch'), gs, f0, z0,
                           p.get('sol_in', 0)),
        output_match=_match(p.get('mn_out', 'lmatch'), gl, f0, z0,
                            p.get('sol_out', 0)))

    net = chain.network
    f = dev.f
    s11, s21, s22 = net.s[:, 0, 0], net.s[:, 1, 0], net.s[:, 1, 1]
    k = metrics.rollett_k(dev)
    mu = metrics.mu_load(dev)
    gt = chain.transducer_gain()
    mag = metrics.max_gain(dev)
    swr_in = np.array([metrics.vswr(v) for v in s11])
    swr_out = np.array([metrics.vswr(v) for v in s22])
    idx = int(np.argmin(np.abs(f - f0)))

    series = {
        'f': [float(v) for v in f],
        's21': [_fin(metrics.db_mag(v), -200) for v in s21],
        's11': [_fin(metrics.db_mag(v), -200) for v in s11],
        's22': [_fin(metrics.db_mag(v), -200) for v in s22],
        'gt': [_fin(metrics.db(v), -200) for v in gt],
        'mag': [_fin(metrics.db(v), -200) for v in mag],
        'k': [_fin(v, 0) for v in k],
        'mu': [_fin(v, 0) for v in mu],
        'swrIn': [min(_fin(v, 99) or 99, 99) for v in swr_in],
        'swrOut': [min(_fin(v, 99) or 99, 99) for v in swr_out],
        'gammaIn': [[float(np.real(v)), float(np.imag(v))]
                    for v in metrics.input_reflection(dev, chain.gamma_l)],
        'gammaOut': [[float(np.real(v)), float(np.imag(v))]
                     for v in metrics.output_reflection(dev, chain.gamma_s)],
        's11c': [[float(np.real(v)), float(np.imag(v))] for v in s11],
    }
    achieved_s = chain.gamma_s[idx]
    achieved_l = chain.gamma_l[idx]
    summary = [
        ['Device', os.path.basename(str(p.get('device')))],
        ['f0', f'{f0 / 1e9:.4g} GHz'],
        ['|S21| chain', f'{metrics.db_mag(s21[idx]):.2f} dB'],
        ['G_T', f'{metrics.db(gt[idx]):.2f} dB'],
        ['MAG/MSG (device)', f'{metrics.db(mag[idx]):.2f} dB'],
        ['|S11| chain', f'{metrics.db_mag(s11[idx]):.2f} dB '
                        f'(SWR {swr_in[idx]:.2f})'],
        ['|S22| chain', f'{metrics.db_mag(s22[idx]):.2f} dB '
                        f'(SWR {swr_out[idx]:.2f})'],
        ['Γ_S achieved', f'{abs(achieved_s):.3f} ∠ '
                         f'{np.degrees(np.angle(achieved_s)):.1f}°'],
        ['Γ_L achieved', f'{abs(achieved_l):.3f} ∠ '
                         f'{np.degrees(np.angle(achieved_l)):.1f}°'],
        ['K / µ', f'{k[idx]:.3f} / {mu[idx]:.3f}'],
        ['Stability', 'unconditional' if bool(
            metrics.is_unconditionally_stable(dev)[idx]) else 'conditional'],
    ]
    warn = []
    if not bool(metrics.is_unconditionally_stable(dev)[idx]):
        warn.append(f'The device is only conditionally stable at f0 '
                    f'(K = {k[idx]:.2f}). Check that Γ_S/Γ_L stay outside '
                    f'the stability circles.')
    gin = metrics.input_reflection(dev, chain.gamma_l)
    gout = metrics.output_reflection(dev, chain.gamma_s)
    if np.max(np.abs(gin)) >= 1.0 or np.max(np.abs(gout)) >= 1.0:
        warn.append('|Γ_in| or |Γ_out| reaches 1 somewhere in the band — '
                    'these terminations can oscillate.')
    kmin = float(np.min(k))
    if kmin < 1.0:
        warn.append(f'Worst-case K over the measured range is {kmin:.2f} '
                    f'(< 1): stabilize before trusting the match.')

    return {
        'series': series,
        'summary': summary,
        'warnings': warn,
        'schematic': _schematic(chain, f0, achieved_s, achieved_l,
                                stab_vals, os.path.basename(str(p.get('device')))),
        'f0': f0,
        'band': [float(f[0]), float(f[-1])],
        'stability': {
            'k': _fin(k[idx]), 'mu': _fin(mu[idx]),
            'kmin': _fin(kmin),
            'uncond': bool(metrics.is_unconditionally_stable(dev).all()),
        },
    }


def _best_match(p):
    """Simultaneous conjugate match at f0, or the stable-region search for
    a conditionally stable device."""
    dev_raw = _load(p.get('device'))
    dev, _ = _stabilized(dev_raw, p)
    f0 = float(p.get('f0') or dev.f[len(dev.f) // 2])
    idx = int(np.argmin(np.abs(dev.f - f0)))
    if bool(metrics.is_unconditionally_stable(dev)[idx]):
        gms, gml = metrics.simultaneous_match(dev)
        gs, gl = gms[idx], gml[idx]
        how = 'simultaneous conjugate match'
    else:
        gs, gl, _gt = metrics.stable_region_match(dev, idx)
        how = 'stable-region gain maximum (K < 1 at f0)'
    return {
        'gs_mag': round(float(abs(gs)), 4),
        'gs_ang': round(float(np.degrees(np.angle(gs))), 2),
        'gl_mag': round(float(abs(gl)), 4),
        'gl_ang': round(float(np.degrees(np.angle(gl))), 2),
        'note': how,
    }


# ---------------------------------------------------------------- optimizer
def _optimize_worker(job_id, p):
    def progress(gen, cost, convergence=None):
        """The optimizer reports (generation, best cost, convergence)."""
        msg = f'gen {gen}: cost {cost:.4g}'
        if convergence is not None:
            msg += f', conv {float(convergence):.3f}'
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job['log'].append(msg)
                del job['log'][:-200]
    try:
        dev_raw = _load(p.get('device'))
        f_lo = float(p['band_lo']) * 1e9
        f_hi = float(p['band_hi']) * 1e9
        kw = dict(target_gain_db=float(p.get('target_gain', 6.0)),
                  max_swr_in=float(p.get('max_swr_in', 2.0)),
                  max_swr_out=float(p.get('max_swr_out', 2.0)),
                  maxiter=int(p.get('iters', 45)),
                  progress=progress)
        if p.get('opt_stab'):
            gs, gl, stab_els, res = optimize_amplifier(
                dev_raw, f_lo, f_hi,
                c_series_in=float(p.get('c_series_in') or 0) * 1e-12,
                c_series_out=float(p.get('c_series_out') or 0) * 1e-12,
                **kw)
        else:
            dev, _ = _stabilized(dev_raw, p)
            gs, gl, res = optimize_match(dev, f_lo, f_hi, **kw)
            stab_els = None
        out = {
            'gs_mag': round(float(abs(gs)), 4),
            'gs_ang': round(float(np.degrees(np.angle(gs))), 2),
            'gl_mag': round(float(abs(gl)), 4),
            'gl_ang': round(float(np.degrees(np.angle(gl))), 2),
            'met': res.get('met'),
            'minGainDb': _fin(res.get('min_gain_db')),
            'maxSwrIn': _fin(res.get('max_swr_in')),
            'maxSwrOut': _fin(res.get('max_swr_out')),
            # only the stabilization search evaluates band stability; None
            # means "not assessed here", which is not the same as unstable
            'uncond': (None if res.get('unconditionally_stable') is None
                       else bool(res['unconditionally_stable'])),
            'minMu': _fin(res.get('min_mu_full_range')),
        }
        if stab_els:
            out['stab'] = {
                'r_series_in': _fin(stab_els.get('r_series_in', 0)),
                'r_shunt_in': _fin(stab_els.get('r_shunt_in', 0)),
                'r_series_out': _fin(stab_els.get('r_series_out', 0)),
                'r_shunt_out': _fin(stab_els.get('r_shunt_out', 0)),
                'l_emitter': _fin((stab_els.get('l_emitter', 0) or 0) * 1e9),
            }
        with _LOCK:
            _JOBS[job_id].update(state='done', result=out)
    except Exception as e:
        with _LOCK:
            _JOBS[job_id].update(state='error', error=str(e))


# ------------------------------------------------------------------- schema
def schema():
    devs = device_files()
    return {
        'title': 'RF amplifier chain',
        'intro': 'Source — input match — device — output match — load. '
                 'Device files come from the project’s Touchstone library '
                 '(devices/), the same ones the S-parameter co-simulation '
                 'uses.',
        'fields': [
            {'id': 'device', 'label': 'Device (.s2p)', 'type': 'select',
             'options': [[d['value'], d['label']] for d in devs],
             'value': devs[0]['value'] if devs else '',
             'meta': {d['value']: d for d in devs},
             'help': 'Two-ports in devices/. Upload more with the '
                     '⇪ button in Simulation → S-parameter devices.'},
            {'id': 'f0', 'label': 'Design frequency', 'type': 'freq',
             'unit': 'GHz', 'value': None},
            {'id': 'z0', 'label': 'System Z₀ (Ω)', 'type': 'number',
             'value': 50, 'step': 1},
            {'group': 'Terminations presented to the device'},
            {'id': 'gs', 'label': 'Γ_S (source side)', 'type': 'gamma'},
            {'id': 'gl', 'label': 'Γ_L (load side)', 'type': 'gamma'},
            {'id': 'mn_in', 'label': 'Input network', 'type': 'select',
             'options': REALIZATIONS, 'value': 'lmatch'},
            {'id': 'mn_out', 'label': 'Output network', 'type': 'select',
             'options': REALIZATIONS, 'value': 'lmatch'},
            {'id': 'sol_in', 'label': 'Input solution', 'type': 'number',
             'value': 0, 'step': 1, 'min': 0, 'max': 3,
             'help': 'Alternative topologies/signs of the same Γ.'},
            {'id': 'sol_out', 'label': 'Output solution', 'type': 'number',
             'value': 0, 'step': 1, 'min': 0, 'max': 3},
            {'group': 'Stabilization (applied before matching)', 'open': False},
            {'id': 'r_series_in', 'label': 'R series in (Ω)', 'type': 'number',
             'value': 0, 'step': 1},
            {'id': 'r_shunt_in', 'label': 'R shunt in (Ω)', 'type': 'number',
             'value': 0, 'step': 1},
            {'id': 'r_series_out', 'label': 'R series out (Ω)', 'type': 'number',
             'value': 0, 'step': 1},
            {'id': 'r_shunt_out', 'label': 'R shunt out (Ω)', 'type': 'number',
             'value': 0, 'step': 1},
            {'id': 'l_emitter', 'label': 'L emitter (nH)', 'type': 'number',
             'value': 0, 'step': 0.1},
            {'id': 'c_series_in', 'label': 'C block in (pF)', 'type': 'number',
             'value': 0, 'step': 1},
            {'id': 'c_series_out', 'label': 'C block out (pF)', 'type': 'number',
             'value': 0, 'step': 1},
            {'group': 'Band targets (for the optimizer)', 'open': False},
            {'id': 'band_lo', 'label': 'Band start (GHz)', 'type': 'number',
             'value': None, 'step': 0.05},
            {'id': 'band_hi', 'label': 'Band stop (GHz)', 'type': 'number',
             'value': None, 'step': 0.05},
            {'id': 'target_gain', 'label': 'Min |S21| (dB)', 'type': 'number',
             'value': 6, 'step': 0.5},
            {'id': 'max_swr_in', 'label': 'Max SWR in', 'type': 'number',
             'value': 2, 'step': 0.1},
            {'id': 'max_swr_out', 'label': 'Max SWR out', 'type': 'number',
             'value': 2, 'step': 0.1},
            {'id': 'opt_stab', 'label': 'Also optimize the stabilization network',
             'type': 'check', 'value': False},
            {'id': 'iters', 'label': 'DE iterations', 'type': 'number',
             'value': 45, 'step': 5, 'min': 5},
        ],
        'actions': [
            {'id': 'analyse', 'label': 'Analyse', 'primary': True,
             'auto': True},
            {'id': 'best', 'label': 'Best match @ f0'},
            {'id': 'optimize', 'label': 'Optimize for band', 'job': True},
        ],
        'panels': [
            # pinned: the synthesized chain stays in view while the plots
            # behind it are switched
            {'id': 'schematic', 'type': 'schematic', 'title': 'Schematic',
             'pin': True},
            {'id': 'summary', 'type': 'table', 'title': 'At f0', 'pin': True},
            {'id': 'gain', 'type': 'chart', 'title': 'Gain & match',
             'x': 'f', 'xlabel': 'GHz', 'xscale': 1e-9, 'ylabel': 'dB',
             'series': [['s21', '|S21|'], ['mag', 'MAG/MSG'],
                        ['s11', '|S11|'], ['s22', '|S22|']]},
            {'id': 'swr', 'type': 'chart', 'title': 'SWR & stability',
             'x': 'f', 'xlabel': 'GHz', 'xscale': 1e-9, 'ylabel': '',
             'series': [['swrIn', 'SWR in'], ['swrOut', 'SWR out'],
                        ['k', 'K'], ['mu', 'µ']]},
            {'id': 'smith', 'type': 'smith', 'title': 'Reflections',
             'series': [['s11c', 'chain S11'], ['gammaIn', 'Γ_in (device)'],
                        ['gammaOut', 'Γ_out (device)']]},
        ],
    }


# ------------------------------------------------------------------ actions
def handle(action, payload):
    if action == 'devices':
        return {'devices': device_files()}
    if action == 'analyse':
        return _analyse(payload)
    if action == 'best':
        return _best_match(payload)
    if action == 'optimize':
        job_id = f'opt{len(_JOBS) + 1}_{int(payload.get("nonce") or 0)}'
        with _LOCK:
            _JOBS[job_id] = {'state': 'running', 'log': [], 'result': None}
        threading.Thread(target=_optimize_worker, args=(job_id, payload),
                         daemon=True).start()
        return {'job': job_id}
    if action == 'job':
        with _LOCK:
            job = _JOBS.get(payload.get('job'))
            if job is None:
                return {'state': 'unknown'}
            return {'state': job['state'], 'log': job['log'][-40:],
                    'result': job.get('result'), 'error': job.get('error')}
    raise KeyError(f'unknown action {action!r}')
