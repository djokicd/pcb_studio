"""Unit test of the device co-simulation combine step, with synthetic
board data (no octave): two ideal lines whose inner ports are bridged by
an ideal through device must combine into an ideal through network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server
from helpers import parse_sparams

FREQS = [1e9, 2e9]


def write_stage(d, exc, columns):
    """columns: {port: complex S_port,exc} written like a run's sparams.csv."""
    d.mkdir(parents=True, exist_ok=True)
    ports = sorted(columns)
    header = '#freq_Hz' + ''.join(f',S{p}{exc}_re,S{p}{exc}_im' for p in ports)
    header += ',Zin_re,Zin_im'
    lines = [header]
    for f in FREQS:
        row = [f'{f:e}']
        for p in ports:
            v = columns[p]
            row.append(f'{v.real:e},{v.imag:e}')
        row.append('5e1,0e0')
        lines.append(','.join(row))
    (d / 'sparams.csv').write_text('\n'.join(lines) + '\n')


def test_combine_through_device(tmp_path):
    # board: line A connects ports 1-2 perfectly, line B connects 3-4
    # (all other couplings zero)
    run = tmp_path / 'run_00000000_000000'
    run.mkdir()
    cols = {
        1: {1: 0j, 2: 1 + 0j, 3: 0j, 4: 0j},
        2: {1: 1 + 0j, 2: 0j, 3: 0j, 4: 0j},
        3: {1: 0j, 2: 0j, 3: 0j, 4: 1 + 0j},
        4: {1: 0j, 2: 0j, 3: 1 + 0j, 4: 0j},
    }
    stages = []
    for exc in (2, 3, 4):
        d = run / f'exc_{exc}'
        write_stage(d, exc, cols[exc])
        stages.append({'dir': d, 'exc': exc})
    write_stage(run, 1, cols[1])
    stages.append({'dir': run, 'exc': 1})

    # ideal through 2-port device bridging board ports 2 and 3
    (server.DEV_ROOT / 'unit_through.s2p').write_text(
        '# GHz S RI R 50\n0.5 0 0 1 0 1 0 0 0\n10 0 0 1 0 1 0 0 0\n')
    model = {
        'ports': [{'number': n, 'impedance': 50, 'excite': n == 1} for n in (1, 2, 3, 4)],
        'devices': [{'ref': 'U1', 'file': 'unit_through.s2p', 'pins': [2, 3]}],
    }
    try:
        devices = server.validate_devices(model)
        server.combine_devices(run, model, stages, devices)
    finally:
        (server.DEV_ROOT / 'unit_through.s2p').unlink()

    rows = parse_sparams(run / 'sparams.csv')
    # external ports 1 and 4: ideal through expected
    for r in rows:
        assert abs(r['s'][1]) < 1e-9          # S11
        assert abs(r['s'][4] - 1) < 1e-9      # S41
    # artifacts exist
    assert (run / 'board_full.s4p').is_file()
    assert (run / 'combined.s2p').is_file()
    assert (run / 'sparams_board.csv').is_file()


def test_validate_devices_rejects_bad_mapping(tmp_path):
    (server.DEV_ROOT / 'unit_thr2.s2p').write_text(
        '# GHz S RI R 50\n1 0 0 1 0 1 0 0 0\n')
    base = {
        'ports': [{'number': n, 'impedance': 50, 'excite': n == 1} for n in (1, 2, 3)],
    }
    import pytest
    from scriptgen import ValidationError
    try:
        m = dict(base, devices=[{'ref': 'U1', 'file': 'unit_thr2.s2p', 'pins': [2]}])
        with pytest.raises(ValidationError, match='distinct pin'):
            server.validate_devices(m)
        m = dict(base, devices=[{'ref': 'U1', 'file': 'unit_thr2.s2p', 'pins': [1, 2]}])
        with pytest.raises(ValidationError, match='excited'):
            server.validate_devices(m)
        m = dict(base, devices=[{'ref': 'U1', 'file': 'missing.s2p', 'pins': [2, 3]}])
        with pytest.raises(ValidationError, match='not found'):
            server.validate_devices(m)
    finally:
        (server.DEV_ROOT / 'unit_thr2.s2p').unlink()
