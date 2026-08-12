"""Advanced-tools framework + the RF chain tool (needs scikit-rf, no octave)."""
import sys
from pathlib import Path

import numpy as np
import pytest
import skrf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import advtools
from advtools.rfamp import (IdealMatch, LMatch, QuarterWaveMatch,
                            SingleStubMatch, matching)

F0 = 2.0e9
FREQ = skrf.Frequency(0.1, 10, 199, 'ghz')        # contains 2.0 GHz exactly
IDX0 = int(np.argmin(np.abs(FREQ.f - F0)))
DEVICE = Path(__file__).resolve().parent.parent / 'devices' / 'BFG25AWJ.S2P'


# ------------------------------------------------------- the skrf backend
def test_scikit_rf_is_the_backend():
    """rfamp is the upstream package: it must run on scikit-rf itself, not
    on a local stand-in."""
    assert matching.skrf is skrf
    assert not (Path(advtools.__file__).parent / 'rfnet.py').exists()


def test_reads_the_projects_touchstone_library():
    net = skrf.Network(str(DEVICE))
    assert net.nports == 2 and len(net.f) > 5
    assert net.f[0] < net.f[-1]


@pytest.mark.parametrize('gamma', [
    0.6 * np.exp(1j * np.deg2rad(150)),
    0.6 * np.exp(-1j * np.deg2rad(40)),
    0.3 + 0.4j,
    -0.5 - 0.2j,
    0.05,
])
def test_every_realization_hits_its_target_gamma(gamma):
    """The synthesis is the whole point: at f0 each topology must actually
    present the requested reflection coefficient."""
    for cls in (IdealMatch, LMatch, QuarterWaveMatch, SingleStubMatch):
        mn = cls(gamma, F0)
        got = mn.achieved_gamma(FREQ)[IDX0]
        assert abs(got - gamma) < 2e-3, f'{cls.__name__} @ {gamma}: {got}'


def test_synthesized_networks_are_passive():
    """Lossless L/C and line sections; a singular value above 1 would mean
    the S-parameter bookkeeping (or its renormalization) is wrong."""
    for cls in (LMatch, QuarterWaveMatch, SingleStubMatch):
        net = cls(0.6 * np.exp(1j * np.deg2rad(150)), F0).network(FREQ)
        sv = np.linalg.svd(net.s, compute_uv=False)
        assert sv.max() <= 1.0 + 1e-9, f'{cls.__name__}: max sv {sv.max()}'


# ------------------------------------------------------------- the registry
def test_registry_lists_self_describing_tools():
    tools = advtools.list_tools()
    assert any(t['id'] == 'rf_chain' for t in tools)
    for t in tools:
        assert t['name'] and 'description' in t


def test_unknown_tool_and_action_are_rejected():
    with pytest.raises(KeyError):
        advtools.dispatch('nope', 'schema', {})
    with pytest.raises(KeyError):
        advtools.dispatch('rf_chain', 'no_such_action', {})


def test_schema_is_renderable():
    """The host renders from the schema alone, so every field needs a type
    the host knows and every panel a source of data."""
    s = advtools.dispatch('rf_chain', 'schema', {})
    known = {'number', 'select', 'check', 'freq', 'gamma'}
    for f in s['fields']:
        if 'group' in f:
            continue
        assert f['type'] in known, f
        assert f.get('id') and f.get('label')
    assert {p['type'] for p in s['panels']} <= {'table', 'text', 'chart',
                                                'smith', 'schematic'}
    assert any(a.get('primary') for a in s['actions'])


# ------------------------------------------------------------ the RF tool
def _analyse(**kw):
    p = {'device': 'BFG25AWJ.S2P', 'f0': 1e9, 'gs_mag': 0.4, 'gs_ang': 120,
         'gl_mag': 0.3, 'gl_ang': 45, 'mn_in': 'lmatch', 'mn_out': 'lmatch'}
    p.update(kw)
    return advtools.dispatch('rf_chain', 'analyse', p)


def test_analyse_returns_every_series_its_panels_plot():
    res = _analyse()
    schema = advtools.dispatch('rf_chain', 'schema', {})
    for p in schema['panels']:
        for key, _label in p.get('series', []):
            assert key in res['series'], f'panel {p["id"]} plots missing {key}'
    n = len(res['series']['f'])
    for k, v in res['series'].items():
        assert len(v) == n, f'series {k} has {len(v)} points, expected {n}'


def test_analyse_is_json_safe():
    """NaN/Inf would serialize to invalid JSON and break the panels."""
    import json
    txt = json.dumps(_analyse())
    assert 'NaN' not in txt and 'Infinity' not in txt


def test_device_outside_the_library_is_refused():
    with pytest.raises(ValueError):
        _analyse(device='../server.py')
    with pytest.raises(ValueError):
        _analyse(device='no_such_device.s2p')


def test_conditionally_stable_device_is_flagged():
    res = _analyse()
    assert res['stability']['kmin'] < 1.0
    assert any('stab' in w.lower() or 'K' in w for w in res['warnings'])


def test_stabilization_raises_k_and_is_reported():
    raw = _analyse()
    stab = _analyse(r_series_in=20, r_shunt_out=100, c_series_in=47,
                    c_series_out=47)
    assert stab['stability']['kmin'] > raw['stability']['kmin']


def test_best_match_hits_the_requested_gamma():
    """'Best match @ f0' returns Γ values; feeding them back must produce a
    chain whose input is genuinely matched."""
    best = advtools.dispatch('rf_chain', 'best',
                             {'device': 'BFG25AWJ.S2P', 'f0': 1e9,
                              'r_series_in': 20, 'r_shunt_out': 100})
    res = _analyse(r_series_in=20, r_shunt_out=100,
                   gs_mag=best['gs_mag'], gs_ang=best['gs_ang'],
                   gl_mag=best['gl_mag'], gl_ang=best['gl_ang'])
    idx = int(np.argmin(np.abs(np.array(res['series']['f']) - 1e9)))
    assert res['series']['s11'][idx] < -25, 'conjugate match should null S11'


def test_schematic_carries_the_synthesized_elements():
    res = _analyse(mn_in='lmatch', mn_out='stub_open')
    sch = res['schematic']
    assert sch['input'] and sch['output']
    assert all('type' in b and 'label' in b for b in sch['input'])
    kinds = {b['type'] for b in sch['input']}
    assert kinds & {'series_l', 'series_c', 'shunt_l', 'shunt_c'}
    assert {b['type'] for b in sch['output']} & {'stub_open', 'line'}
    assert sch['stab'] == []          # nothing set here
    with_stab = _analyse(r_series_in=20)['schematic']['stab']
    assert any('Rs,in' in row[0] for row in with_stab)


@pytest.mark.parametrize('opt_stab', [False, True])
def test_band_optimizer_reports_a_usable_result(opt_stab):
    """Both search paths must package their result: the Γ-only search does
    not evaluate band stability, and its missing keys used to crash the
    worker instead of simply going unreported."""
    from advtools.tools import rf_chain
    rf_chain._JOBS['pytest'] = {'state': 'running', 'log': [], 'result': None}
    rf_chain._optimize_worker('pytest', {
        'device': 'BFG25AWJ.S2P', 'band_lo': 0.9, 'band_hi': 1.1,
        'iters': 2, 'target_gain': 6, 'opt_stab': opt_stab,
        'c_series_in': 47, 'c_series_out': 47})
    job = rf_chain._JOBS.pop('pytest')
    assert job['state'] == 'done', job.get('error')
    out = job['result']
    assert 0 <= out['gs_mag'] < 1
    assert {'gain', 'swr_in', 'swr_out'} <= set(out['met'])
    assert (out['uncond'] is not None) == opt_stab
    assert ('stab' in out) == opt_stab
    assert job['log'], 'no progress was reported'


def test_every_realization_analyses():
    for kind in ('lmatch', 'stub_open', 'stub_short', 'qwave', 'ideal'):
        res = _analyse(mn_in=kind, mn_out=kind)
        assert len(res['summary']) > 5, kind
