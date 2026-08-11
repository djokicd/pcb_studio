"""Advanced-tools framework + the RF chain tool (no scikit-rf, no octave)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import advtools
from advtools import rfnet
from advtools.rfnet import Frequency, Network, a2s, s2a, s2z, z2s


# ---------------------------------------------------------------- the shim
def _line(z_line=75.0, z_ref=50.0, length=0.02, n=41):
    med = rfnet.DefinedGammaZ0(frequency=Frequency(0.1, 10, n, 'ghz'), z0=z_ref)
    return med.line(length, unit='m', z0=z_line)


def test_conversions_round_trip():
    rng = np.random.default_rng(3)
    s = (rng.normal(size=(7, 2, 2)) + 1j * rng.normal(size=(7, 2, 2))) * 0.3
    for fwd, back in ((s2z, z2s), (s2a, a2s)):
        assert np.allclose(back(fwd(s, 50.0), 50.0), s, atol=1e-10)


def test_ideal_line_is_lossless_and_reciprocal():
    n = _line()
    sv = np.linalg.svd(n.s, compute_uv=False)
    assert np.allclose(sv, 1.0, atol=1e-9)              # unitary => lossless
    assert np.allclose(n.s[:, 0, 1], n.s[:, 1, 0])      # reciprocal


def test_renormalize_survives_the_half_wave_singularity():
    """A half-wave line has S = [[0,-1],[-1,0]]; I - S is singular, so the
    S->Z->S route reports a passive line as a 1.2x amplifier. The power
    -wave form must keep it exactly unitary."""
    n = _line(z_line=52.5657, z_ref=52.5657, length=0.03747405725, n=199)
    n.renormalize(50.0)
    sv = np.linalg.svd(n.s, compute_uv=False)
    assert sv.max() <= 1.0 + 1e-9, f'passivity violated: max sv {sv.max()}'


def test_cascade_matches_abcd_product():
    a, b = _line(60.0), _line(40.0, length=0.01)
    prod = np.matmul(s2a(a.s, 50.0), s2a(b.s, 50.0))
    assert np.allclose((a ** b).s, a2s(prod, 50.0), atol=1e-12)


def test_flip_swaps_ports():
    n = _line(75.0, length=0.013)
    fl = n.flipped()
    assert np.allclose(fl.s[:, 0, 0], n.s[:, 1, 1])
    assert np.allclose(fl.s[:, 1, 0], n.s[:, 0, 1])


def test_reads_the_projects_touchstone_library():
    net = Network(str(Path(__file__).resolve().parent.parent
                      / 'devices' / 'BFG25AWJ.S2P'))
    assert net.nports == 2 and len(net.f) > 5
    assert net.f[0] < net.f[-1]


def test_interpolation_refuses_to_extrapolate():
    n = _line(n=11)
    with pytest.raises(ValueError):
        n.interpolate(Frequency(0.01, 0.05, 5, 'ghz'))


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


def test_every_realization_analyses():
    for kind in ('lmatch', 'stub_open', 'stub_short', 'qwave', 'ideal'):
        res = _analyse(mn_in=kind, mn_out=kind)
        assert len(res['summary']) > 5, kind
