"""Unit test of the device co-simulation combine step, with synthetic
board data (no octave): two ideal lines whose inner ports are bridged by
an ideal through device must combine into an ideal through network."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server
from helpers import parse_sparams, stackup, sim_settings, rect, lumped_port
import time
from scriptgen import generate_script

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
        r.stage_data.append(server.Runner._new_stage_record(exc, f'Exc {exc}'))
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
    # one stored run per completion, keyed by run id
    assert (pdir / 'runs' / run.name / 'sparams.csv').is_file()
    runs = server._project_runs('autosave_check')
    assert len(runs) == 1 and runs[0]['runId'] == run.name
    assert runs[0]['resultsId'] == f'proj_autosave_check__{run.name}'
    assert server._run_dir(runs[0]['resultsId']) == pdir / 'runs' / run.name
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


def test_copy_results_keeps_stage_j_dumps(tmp_path):
    """Every excitation's processed current-density exports ride along
    into the stored run, so the J viewer works for each stage later."""
    src = tmp_path / 'run_src2'
    (src / 'exc_2').mkdir(parents=True)
    (src / 'sparams.csv').write_text('#freq_Hz\n')
    st = src / 'exc_2'
    (st / 'sparams.csv').write_text('#freq_Hz\n')
    (st / 'jdumps.csv').write_text('Top,0,1.0e9\n')
    (st / 'J_Top_f0.bin').write_bytes(b'\x00' * 16)
    (st / 'jtdumps.csv').write_text('Top,4\n')
    (st / 'J_td_Top.bin').write_bytes(b'\x00' * 16)
    (st / 'Jf_top.h5').write_text('bulky')
    (st / 'et').write_text('engine internals')
    dst = tmp_path / 'results2'
    server._copy_results(src, dst)
    for name in ('sparams.csv', 'jdumps.csv', 'J_Top_f0.bin',
                 'jtdumps.csv', 'J_td_Top.bin'):
        assert (dst / 'exc_2' / name).is_file(), name
    assert not (dst / 'exc_2' / 'Jf_top.h5').exists()
    assert not (dst / 'exc_2' / 'et').exists()


def test_diagnostics_written_and_served(tmp_path, monkeypatch):
    """Run diagnostics (per-stage energy/speed samples, engine facts)
    are persisted at completion and served for past runs; per-stage J
    dumps resolve via ?exc=<port>."""
    monkeypatch.setattr(server, 'SIM_ROOT', tmp_path)
    run = tmp_path / 'run_00000000_000009'
    (run / 'exc_2').mkdir(parents=True)
    (run / 'sparams.csv').write_text('#freq_Hz\n')

    r = server.Runner()
    r._reset()
    r.run_id = run.name
    r.state = 'done'
    r.started_at = 1000.0
    r.mesh_cells = 123456
    r.nrts, r.end_db = 30000, -40.0
    r.stage_data = [
        {'exc': 2, 'label': 'Exc 2', 'samples': [{'ts': 100, 'db': -5.0, 'speed': 80.0}],
         'info': {'dt': '1e-13'}, 'warn': [], 'notConverged': False},
        {'exc': 1, 'label': 'Exc 1', 'samples': [{'ts': 200, 'db': -41.0, 'speed': 90.0}],
         'info': {}, 'warn': ['Warning: x'], 'notConverged': False},
    ]
    r._write_diagnostics_locked()
    assert (run / 'diagnostics.json').is_file()

    # stage dumps for exc 2
    (run / 'exc_2' / 'jdumps.csv').write_text('Top,0,1.0e9\n')
    (run / 'exc_2' / 'J_Top_f0.bin').write_bytes(b'\x00' * 8)

    with server.app.test_client() as c:
        d = c.get(f'/api/results/{run.name}/diagnostics').get_json()
        assert d['meshCells'] == 123456 and d['state'] == 'done'
        assert [s['label'] for s in d['stages']] == ['Exc 2', 'Exc 1']
        assert d['stages'][0]['samples'][0]['db'] == -5.0

        j = c.get(f'/api/results/{run.name}/jdumps').get_json()
        assert j['excs'] == [2]          # stage 2 has its own dumps
        j2 = c.get(f'/api/results/{run.name}/jdumps?exc=2').get_json()
        assert j2['dumps'] == [{'layer': 'Top', 'k': 0, 'freq': 1.0e9}]
        b = c.get(f'/api/results/{run.name}/jdump/Top/0?exc=2')
        assert b.status_code == 200
        assert c.get(f'/api/results/{run.name}/jdump/Top/0').status_code == 404
        assert c.get(f'/api/results/{run.name}/diagnostics?exc=2').status_code == 200
        assert c.get('/api/results/no_such/diagnostics').status_code == 404


def test_timedomain_per_excitation(tmp_path, monkeypatch):
    """Every stage's port u/i signals are reachable via ?exc= and the
    root response lists which stages carry their own."""
    monkeypatch.setattr(server, 'SIM_ROOT', tmp_path)
    run = tmp_path / 'run_00000000_000011'
    (run / 'exc_2').mkdir(parents=True)
    probe = '% time\tvalue\n' + ''.join(f'{k * 1e-12}\t{k * 0.1}\n' for k in range(10))
    for d, scale in ((run, 1.0), (run / 'exc_2', 2.0)):
        (d / 'port_ut1').write_text(probe)
        (d / 'port_it1').write_text(probe)
    with server.app.test_client() as c:
        r = c.get(f'/api/results/{run.name}/timedomain').get_json()
        assert r['excs'] == [2]
        assert r['ports'][0]['n'] == 1 and len(r['ports'][0]['t']) == 10
        r2 = c.get(f'/api/results/{run.name}/timedomain?exc=2').get_json()
        assert r2['ports'][0]['n'] == 1 and r2['excs'] == []
        assert c.get(f'/api/results/{run.name}/timedomain?exc=9').status_code == 400


def test_copy_results_keeps_stage_port_signals(tmp_path):
    src = tmp_path / 'run_src3'
    (src / 'exc_2').mkdir(parents=True)
    (src / 'sparams.csv').write_text('#\n')
    (src / 'exc_2' / 'sparams.csv').write_text('#\n')
    (src / 'exc_2' / 'port_ut1').write_text('%\n0\t0\n')
    (src / 'exc_2' / 'port_it1').write_text('%\n0\t0\n')
    dst = tmp_path / 'results3'
    server._copy_results(src, dst)
    assert (dst / 'exc_2' / 'port_ut1').is_file()
    assert (dst / 'exc_2' / 'port_it1').is_file()


def test_jsteady_superposes_stage_dumps(tmp_path, monkeypatch):
    """The folded steady-state dump is the complex superposition of the
    per-stage fields weighted by the incident waves the device network
    imposes: with board S21=0.8, S31=0.6 and a THRU device on ports 2/3,
    the weights are a=(1, 0.6, 0.8)."""
    import struct
    monkeypatch.setattr(server, 'SIM_ROOT', tmp_path)
    monkeypatch.setattr(server, 'DEV_ROOT', tmp_path / 'devices')
    (tmp_path / 'devices').mkdir()
    (tmp_path / 'devices' / 'thru.s2p').write_text(
        '# GHz S RI R 50\n0.5 0 0 1 0 1 0 0 0\n2.0 0 0 1 0 1 0 0 0\n')

    run = tmp_path / 'run_00000000_000021'
    for sub in ('exc_2', 'exc_3'):
        (run / sub).mkdir(parents=True)
    ports = [{'id': 1, 'number': 1, 'excite': True, 'impedance': 50,
              'x': 0, 'y': 0, 'w': 1, 'h': 1},
             {'id': 2, 'number': 2, 'excite': False, 'impedance': 50,
              'x': 0, 'y': 0, 'w': 1, 'h': 1},
             {'id': 3, 'number': 3, 'excite': False, 'impedance': 50,
              'x': 0, 'y': 0, 'w': 1, 'h': 1}]
    (run / 'project.json').write_text(json.dumps({
        'ports': ports,
        'devices': [{'ref': 'X1', 'file': 'thru.s2p', 'pins': [2, 3]}]}))
    # board matrix at both band edges: S21=0.8, S31=0.6, rest 0
    m = [[0j] * 3 for _ in range(3)]
    m[1][0] = 0.8
    m[2][0] = 0.6
    server._write_touchstone(run / 'board_full.s3p', [0.5e9, 2e9], [m, m])

    def bin_for(jx):
        nx, ny = 2, 2
        vals = [0.0, 1.0, 0.0, 1.0]                     # x coords, y coords
        vals += [jx.real] * 4 + [jx.imag] * 4           # jxr, jxi
        vals += [0.0] * 8                               # jyr, jyi
        return struct.pack('<2i', nx, ny) + struct.pack(f'<{len(vals)}f', *vals)

    for d, val in ((run, 1 + 0j), (run / 'exc_2', 1j), (run / 'exc_3', 2 + 0j)):
        (d / 'jdumps.csv').write_text('top,0,1.0e9\n')
        (d / 'J_top_f0.bin').write_bytes(bin_for(val))

    with server.app.test_client() as c:
        j = c.get(f'/api/results/{run.name}/jdumps').get_json()
        assert j['steady'] is True
        r = c.get(f'/api/results/{run.name}/jsteady/top/0')
        assert r.status_code == 200
        raw = r.data
        nx, ny = struct.unpack_from('<2i', raw, 0)
        vals = struct.unpack_from('<20f', raw, 8)
        jxr, jxi = vals[4:8], vals[8:12]
        # steady = 1*(1) + 0.6*(i) + 0.8*(2) = 2.6 + 0.6i
        for v in jxr:
            assert abs(v - 2.6) < 1e-5
        for v in jxi:
            assert abs(v - 0.6) < 1e-5
        # driving a device pin is rejected
        assert c.get(f'/api/results/{run.name}/jsteady/top/0?drive=2').status_code == 400
        # a run without devices reports no steady view
        assert c.get('/api/results/no_such/jsteady/top/0').status_code == 400


def _fake_solver(script_path, seconds=0.3):
    """A stand-in for octave that emits the GUI markers and exits 0."""
    return (
        'import time\n'
        'print("GUI_MARKER: mesh = 1000 cells", flush=True)\n'
        'print("GUI_MARKER: starting FDTD", flush=True)\n'
        'print("[@ 1s] Timestep 100 || Speed 10.0 MC/s || Energy: -10.0 dB", flush=True)\n'
        f'time.sleep({seconds})\n'
        'print("GUI_MARKER: post-processing", flush=True)\n'
        'print("GUI_MARKER: done", flush=True)\n'
    )


def _parallel_runner(tmp_path, monkeypatch, parallel, seconds=0.3):
    import subprocess as sp
    fake = tmp_path / 'fake_solver.py'
    fake.write_text(_fake_solver(fake, seconds))
    real_popen = sp.Popen
    monkeypatch.setattr(server.subprocess, 'Popen',
                        lambda cmd, **kw: real_popen(['python3', str(fake)], **kw))
    # a synthetic 8-core no-SMT machine: thread splits and core blocks
    # must not depend on the topology of whatever host runs the tests
    monkeypatch.setattr(server, 'CPU_CORES', [[i] for i in range(8)])
    monkeypatch.setattr(server, 'generate_script', lambda m: '% fake')
    monkeypatch.setattr(server, 'merge_excitations', lambda *a, **k: None)
    monkeypatch.setattr(server, 'combine_devices', lambda *a, **k: None)
    model = json.loads(json.dumps(_amp_model()))
    model['sim']['parallel'] = parallel
    return server.Runner(), model


def _amp_model():
    return {
        'name': 'partest',
        'board': {'width': 20, 'height': 10},
        'stackup': stackup(),
        'shapes': [rect('l', 1, 4.6, 18, 0.8)],
        'vias': [], 'components': [], 'devices': [], 'notes': [],
        'ports': [lumped_port(n, 1 + 4 * (n - 1), 4.6, 0.4, 0.8, excite=(n == 1))
                  for n in (1, 2, 3, 4)],
        'sim': dict(sim_settings(), fullS=True),
    }


def _drain(runner, timeout=30):
    t0 = time.time()
    peak = 0
    while time.time() - t0 < timeout:
        st = runner.status()
        peak = max(peak, len(st['running']))
        if st['state'] in ('done', 'error', 'stopped'):
            return st, peak
        time.sleep(0.02)
    raise AssertionError('run did not finish')


def test_excitations_run_sequentially_by_default(tmp_path, monkeypatch):
    r, model = _parallel_runner(tmp_path, monkeypatch, parallel=1)
    r.start(model)
    st, peak = _drain(r)
    assert st['state'] == 'done'
    assert peak == 1, f'expected one solver at a time, saw {peak}'
    assert len(st['stages']) == 4 and all(s['state'] == 'done' for s in st['stages'])


def test_parallel_setting_runs_excitations_side_by_side(tmp_path, monkeypatch):
    r, model = _parallel_runner(tmp_path, monkeypatch, parallel=4)
    r.start(model)
    st, peak = _drain(r)
    assert st['state'] == 'done'
    assert peak == 4, f'expected four solvers at once, saw {peak}'
    assert st['parallel'] == 4
    assert st['percent'] == 100.0


def test_parallel_is_capped_by_the_number_of_excitations(tmp_path, monkeypatch):
    r, model = _parallel_runner(tmp_path, monkeypatch, parallel=16)
    r.start(model)
    st, peak = _drain(r)
    assert st['parallel'] == 4 and peak <= 4


def test_queue_refills_as_slots_free_up(tmp_path, monkeypatch):
    r, model = _parallel_runner(tmp_path, monkeypatch, parallel=2)
    r.start(model)
    st, peak = _drain(r)
    assert st['state'] == 'done'
    assert peak == 2
    assert all(s['state'] == 'done' for s in st['stages'])


def test_status_names_the_project_that_is_running(tmp_path, monkeypatch):
    r, model = _parallel_runner(tmp_path, monkeypatch, parallel=2)
    r.start(model)
    assert r.status()['project'] == 'partest'
    _drain(r)


def test_thread_cap_is_only_emitted_when_asked_for():
    """openEMS benchmarks the mesh and picks its own thread count; the
    script only forces one when the user set a cap."""
    m = _amp_model()
    assert '--numThreads' not in generate_script(m)
    m['sim']['numThreads'] = 3
    assert "RunOpenEMS(Sim_Path, Sim_File, '--numThreads=3');" in generate_script(m)


def test_split_threads_balances_the_cores():
    """Side-by-side big-mesh runs each get an even share; small meshes
    get one core each (openEMS gains nothing from more there); a stage
    launching with few companions takes a larger share."""
    big, small = 1_500_000, 100_000
    # 4-way start on 8 cores: 2 threads each, never 9/1/1/1
    assert server.split_threads(8, big, 4, 0, 4) == 2
    assert server.split_threads(8, big, 4, 3, 1) == 2
    # small meshes: one core per instance regardless of width
    assert server.split_threads(8, small, 4, 0, 4) == 1
    # a stage launching beside a single live run shares halfway
    assert server.split_threads(8, big, 4, 1, 1) == 4
    # last stage alone on the machine
    assert server.split_threads(8, big, 4, 0, 1) == 8
    # never below one thread
    assert server.split_threads(2, big, 4, 0, 4) == 1


def test_split_threads_scales_to_many_core_servers():
    """On a 32-core box the even share alone would oversubscribe medium
    meshes; the per-mesh cap keeps threads at what the cells can feed."""
    # 2-way on 32 cores, 500k cells: fair share is 16, useful is 3
    assert server.split_threads(32, 500_000, 2, 0, 2) == 3
    # a genuinely large mesh takes the full even share
    assert server.split_threads(32, 5_000_000, 4, 0, 4) == 8
    # small meshes still run one thread each, cores notwithstanding
    assert server.split_threads(32, 100_000, 4, 0, 4) == 1
    # 1.5M cells, 2-way: capped by the mesh (10) inside the share (16)
    assert server.split_threads(32, 1_500_000, 2, 0, 2) == 10


def test_parallel_big_mesh_gets_even_thread_split(tmp_path, monkeypatch):
    """The launch pins --numThreads = cores/parallel into each stage's
    script; the openEMS startup benchmark must not be left to race."""
    r, model = _parallel_runner(tmp_path, monkeypatch, parallel=4)
    monkeypatch.setattr(server, 'generate_script',
                        lambda m: 'RunOpenEMS(Sim_Path, Sim_File);')
    monkeypatch.setattr(server, 'build_mesh',
                        lambda m: {'cells': 2_000_000})
    monkeypatch.setattr(server.os, 'cpu_count', lambda: 8)
    r.start(model)
    st, peak = _drain(r)
    assert st['state'] == 'done' and peak == 4
    assert [s['threads'] for s in st['stages']] == [2, 2, 2, 2]
    for st_ in r.stages:
        assert "--numThreads=2" in (st_['dir'] / 'pcb_sim.m').read_text()


def test_parallel_small_mesh_runs_one_thread_each(tmp_path, monkeypatch):
    r, model = _parallel_runner(tmp_path, monkeypatch, parallel=4)
    monkeypatch.setattr(server, 'generate_script',
                        lambda m: 'RunOpenEMS(Sim_Path, Sim_File);')
    monkeypatch.setattr(server, 'build_mesh', lambda m: {'cells': 90_000})
    monkeypatch.setattr(server.os, 'cpu_count', lambda: 8)
    r.start(model)
    st, _ = _drain(r)
    assert [s['threads'] for s in st['stages']] == [1, 1, 1, 1]


def test_explicit_thread_cap_disables_the_auto_split(tmp_path, monkeypatch):
    """threadsPerExc is an override: the runner must not second-guess it."""
    r, model = _parallel_runner(tmp_path, monkeypatch, parallel=4)
    seen = []
    monkeypatch.setattr(server, 'generate_script',
                        lambda m: seen.append((m.get('sim') or {}).get('numThreads'))
                        or 'RunOpenEMS(Sim_Path, Sim_File);')
    monkeypatch.setattr(server, 'build_mesh', lambda m: {'cells': 2_000_000})
    model['sim']['threadsPerExc'] = 3
    r.start(model)
    st, _ = _drain(r)
    assert all(t == 3 for t in seen), seen
    assert all(s['threads'] is None for s in st['stages'])


def test_cpu_topology_lists_physical_cores():
    """Each entry is one physical core's logical siblings; no logical CPU
    appears twice, groups are sorted. FDTD threads are budgeted on these,
    not on hyperthreads - two solver threads on one FPU each run at half
    speed."""
    cores = server.cpu_topology()
    logical = [c for grp in cores for c in grp]
    assert cores and len(logical) == len(set(logical))
    assert all(grp == sorted(grp) for grp in cores)
    assert cores == sorted(cores, key=lambda g: g[0])


def test_fmt_cpuset_compresses_ranges():
    assert server._fmt_cpuset([0, 1, 2, 3]) == '0-3'
    assert server._fmt_cpuset([0, 2, 3, 5]) == '0,2-3,5'
    assert server._fmt_cpuset([4]) == '4'


def test_parallel_stages_get_disjoint_core_sets(monkeypatch, tmp_path):
    """Side-by-side solvers are pinned to DISJOINT physical-core blocks.
    Unpinned, their threads migrate over every core, evict each other's
    working sets, and the instances melt into one oversubscribed pot -
    observed on a 32-CPU server as 19 MC/s per instance where a lone run
    does 100. Pinning also bounds a solver that ignores its thread count:
    it can only ever use its own cores."""
    calls = []

    class FakeProc:
        def __init__(self, cmd):
            calls.append(cmd)
            self.stdout = None

        def poll(self):
            return None

    monkeypatch.setattr(server.subprocess, 'Popen',
                        lambda cmd, **kw: FakeProc(cmd))
    monkeypatch.setattr(server.Runner, '_reader',
                        lambda self, proc, idx=0: None)
    monkeypatch.setattr(server, 'CPU_CORES', [[i] for i in range(8)])

    r = server.Runner()
    r.parallel = 4
    r.auto_split = True
    r.threads = 0
    r.plan_cells = 1_500_000
    r.cpu_free = list(range(8))
    r.cpu_alloc = {}
    for i in range(4):
        r.stages.append({'dir': tmp_path / f's{i}',
                         'script': 'RunOpenEMS(Sim_Path, Sim_File);'})
        r.stage_data.append(r._new_stage_record(i + 1, f'Exc {i + 1}'))
    for i in range(4):
        r._launch_stage_locked(i)

    sets = []
    for cmd, rec in zip(calls, r.stage_data):
        assert cmd[0] == 'taskset' and cmd[1] == '-c'
        cpus = {int(c) for c in cmd[2].split(',')}
        assert len(cpus) == rec['threads'], 'block size matches its threads'
        assert rec['cpus'] is not None
        # the thread flag actually landed in the script it will run
        script = (r.stages[len(sets)]['dir'] / 'pcb_sim.m').read_text()
        assert f"--numThreads={rec['threads']}" in script
        sets.append(cpus)
    union = set().union(*sets)
    assert len(union) == sum(len(s) for s in sets), 'blocks overlap'
    assert r.cpu_free == [], '4 stages x 2 cores cover all 8'


def test_status_serves_one_stage_log_untangled():
    """The combined raw log interleaves parallel solvers (tagged); asking
    for one stage returns that solver's own stream, untagged, with its
    own offsets."""
    r = server.Runner()
    r.stage_data.append(r._new_stage_record(1, 'Exc 1'))
    r.stage_data.append(r._new_stage_record(2, 'Exc 2'))
    r.procs = {0: object(), 1: object()}      # two live solvers -> tags
    r._append_line('Timestep: 100', r.stage_data[0])
    r._append_line('Timestep: 200', r.stage_data[1])
    assert r.status()['lines'] == ['[Exc 1] Timestep: 100',
                                   '[Exc 2] Timestep: 200']
    assert r.status(stage=0)['lines'] == ['Timestep: 100']
    assert r.status(0, '1')['lines'] == ['Timestep: 200']
    st = r.status(0, 'junk')                  # junk falls back to combined
    assert st['lines'][0].startswith('[Exc 1]')
    assert st['stages'][0]['cpus'] is None    # nothing pinned here
