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
        # every external port's own reflection is exported too
        assert abs(r['refl'][1]) < 1e-9       # S11
        assert abs(r['refl'][4]) < 1e-9       # S44
    # artifacts exist
    assert (run / 'board_full.s4p').is_file()
    assert (run / 'combined.s2p').is_file()
    assert (run / 'sparams_board.csv').is_file()


def test_merge_excitations(tmp_path):
    """User-selected subset of excitations (no devices): the per-stage
    columns are merged into one sparams.csv, primary excitation first."""
    run = tmp_path / 'run_00000000_000001'
    run.mkdir()
    cols = {
        1: {1: 0.1 + 0j, 2: 0.5 + 0j, 3: 0.2 + 0j},
        3: {1: 0.2 + 0j, 2: 0.4 + 0j, 3: 0.15 + 0j},
    }
    stages = []
    d = run / 'exc_3'
    write_stage(d, 3, cols[3])
    stages.append({'dir': d, 'exc': 3})
    write_stage(run, 1, cols[1])
    stages.append({'dir': run, 'exc': 1})

    model = {'ports': [{'number': n, 'impedance': 50, 'excite': n in (1, 3)}
                       for n in (1, 2, 3)]}
    server.merge_excitations(run, model, stages)

    rows = parse_sparams(run / 'sparams.csv')
    for r in rows:
        assert abs(r['s'][1] - 0.1) < 1e-9      # S11 (primary column first)
        assert abs(r['s'][2] - 0.5) < 1e-9      # S21
        assert abs(r['s'][3] - 0.2) < 1e-9      # S31
        assert abs(r['refl'][1] - 0.1) < 1e-9   # S11
        assert abs(r['refl'][3] - 0.15) < 1e-9  # S33 from the exc-3 stage
        assert abs(r['zin'] - 50) < 1e-9        # primary-stage Zin kept
    assert (run / 'sparams_primary.csv').is_file()


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


def test_smatrix_endpoint_exposes_raw_matrix(tmp_path, monkeypatch):
    """The raw-data viewer gets the full N×N board matrix, the folded
    network, and the list of per-excitation stages."""
    run = tmp_path / 'run_00000000_000002'
    (run / 'exc_2').mkdir(parents=True)
    (run / 'exc_3').mkdir(parents=True)
    for d, exc in ((run / 'exc_2', 2), (run / 'exc_3', 3)):
        write_stage(d, exc, {1: 0.1 + 0j, 2: 0.2 + 0j, 3: 0.3 + 0j})
    (run / 'board_full.s3p').write_text(
        '! test\n# GHz S RI R 50\n'
        + '\n'.join(f'{f} ' + ' '.join(['0.5 0.25'] * 9) for f in (1, 2)) + '\n')
    (run / 'combined.s2p').write_text(
        '# GHz S RI R 50\n1 0 0 1 0 1 0 0 0\n2 0 0 1 0 1 0 0 0\n')
    monkeypatch.setattr(server, 'SIM_ROOT', tmp_path)

    with server.app.test_client() as c:
        d = c.get(f'/api/results/{run.name}/smatrix').get_json()
        keys = {x['key']: x for x in d['datasets']}
        assert keys['board']['nports'] == 3
        assert len(keys['board']['entries']) == 9          # every S_ij present
        assert keys['board']['entries']['S23']['re'] == [0.5, 0.5]
        assert keys['combined']['nports'] == 2
        assert d['stages'] == [2, 3]
        # per-excitation raw csv is served verbatim
        r = c.get(f'/api/results/{run.name}/stage/3')
        assert r.status_code == 200 and b'S33_re' in r.data
        assert c.get('/api/results/no_such_run/smatrix').status_code == 404


def test_run_status_keeps_stage_samples_separate():
    """Each excitation restarts at timestep 0, so its samples/info/warnings
    must land in its own record instead of one concatenated series."""
    r = server.Runner()
    r._reset()
    r.nrts, r.end_db = 30000, -40.0
    for exc in (2, 1):
        r.stages.append({'exc': exc})
        r.stage_data.append({'exc': exc, 'label': f'Exc {exc}', 'samples': [],
                             'info': {}, 'warn': [], 'notConverged': False})
        r.state = 'running'
        r._parse(f'Timestep: 100 || Speed: 50.0 MC/s [@1s] Energy: ~0 (-{exc}.00dB)')
        r._parse(f'Timestep: 200 || Speed: 60.0 MC/s [@2s] Energy: ~0 (-{exc * 5}.00dB)')
    r._parse('Warning: something odd')

    st = r.status()
    assert st['stageCount'] == 2 and st['stageIdx'] == 1
    labels = [s['label'] for s in st['stages']]
    assert labels == ['Exc 2', 'Exc 1']
    # two samples each, not four in one series
    assert [len(s['samples']) for s in st['stages']] == [2, 2]
    assert st['stages'][0]['samples'][0]['db'] == -2.0     # exc 2 record
    assert st['stages'][1]['samples'][0]['db'] == -1.0     # exc 1 record
    # the warning went to the stage in progress only
    assert st['stages'][0]['warn'] == []
    assert len(st['stages'][1]['warn']) == 1
    assert st['samples'] is st['stages'][-1]['samples']    # top level = current


def test_copy_results_handles_stage_dirs(tmp_path):
    """Saving a multi-excitation run must not choke on the exc_* stage
    directories, and keeps each stage's raw sparams.csv."""
    src = tmp_path / 'run_src'
    src.mkdir()
    (src / 'sparams.csv').write_text('#freq_Hz\n')
    (src / 'Jf_top.h5').write_text('bulky')
    (src / 'exc_2').mkdir()
    (src / 'exc_2' / 'sparams.csv').write_text('#freq_Hz\n')
    (src / 'exc_2' / 'et').write_text('binary noise')
    dst = tmp_path / 'results'
    server._copy_results(src, dst)
    assert (dst / 'sparams.csv').is_file()
    assert (dst / 'exc_2' / 'sparams.csv').is_file()
    assert not (dst / 'Jf_top.h5').exists()        # bulky dumps skipped
    assert not (dst / 'exc_2' / 'et').exists()     # stage internals skipped


def test_autosave_attaches_results_on_completion(tmp_path, monkeypatch):
    """When a run finishes, its results are written to the project dir on
    disk immediately — no browser involvement."""
    monkeypatch.setattr(server, 'SIM_ROOT', tmp_path / 'sims')
    monkeypatch.setattr(server, 'PROJ_ROOT', tmp_path / 'projects')
    run = tmp_path / 'sims' / 'run_00000000_000003'
    run.mkdir(parents=True)
    (run / 'sparams.csv').write_text('#freq_Hz\n1e9,0,0\n')
    r = server.Runner()
    r._reset()
    r.run_id = run.name
    r.base_model = {'name': 'autosave_check', 'ports': []}
    r._autosave_locked()
    pdir = tmp_path / 'projects' / 'autosave_check'
    assert (pdir / 'project.json').is_file()
    assert (pdir / 'results' / 'sparams.csv').is_file()
    # a later manual save must win over the autosaved design
    (pdir / 'project.json').write_text('{"name": "edited"}')
    r._autosave_locked()
    assert 'edited' in (pdir / 'project.json').read_text()
    # unnamed project: nothing attached, no crash
    r.base_model = {'name': '', 'ports': []}
    r._autosave_locked()


def test_runs_listing_and_run_project(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'SIM_ROOT', tmp_path)
    d = tmp_path / 'run_20260101_000000'
    d.mkdir()
    (d / 'sparams.csv').write_text('#freq_Hz\n')
    (d / 'project.json').write_text('{"name": "listed_proj", "ports": []}')
    (tmp_path / 'run_20260101_000001').mkdir()   # failed run: no results
    with server.app.test_client() as c:
        runs = c.get('/api/runs').get_json()['runs']
        assert [r['runId'] for r in runs] == \
            ['run_20260101_000001', 'run_20260101_000000']   # newest first
        assert runs[1]['hasResults'] and runs[1]['project'] == 'listed_proj'
        assert not runs[0]['hasResults'] and runs[0]['project'] is None
        pj = c.get('/api/runs/run_20260101_000000/project').get_json()
        assert pj['project']['name'] == 'listed_proj'
        assert c.get('/api/runs/run_20260101_000001/project').status_code == 404
