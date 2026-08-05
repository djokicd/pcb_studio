#!/usr/bin/env python3
"""OpenEMS PCB Studio backend.

Serves the editor UI, generates Octave scripts, runs simulations via
Octave/openEMS as a subprocess and reports progress parsed from the
solver output.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from gerber import parse_gerber, parse_excellon, GerberError
from scriptgen import generate_script, ValidationError, dump_layers
from meshlines import build_mesh

BASE_DIR = Path(__file__).resolve().parent
SIM_ROOT = BASE_DIR / 'sims'
SIM_ROOT.mkdir(exist_ok=True)
PROJ_ROOT = BASE_DIR / 'projects'
PROJ_ROOT.mkdir(exist_ok=True)

MAX_LOG_LINES = 5000

app = Flask(__name__, static_folder='static', static_url_path='')

TIMESTEP_RE = re.compile(r'Timestep:\s*(\d+)')
ENERGY_RE = re.compile(r'\((-?\s*\d+(?:\.\d+)?)\s*dB\)')
SPEED_RE = re.compile(r'Speed:\s*([\d.]+)\s*MC/s')
WALL_RE = re.compile(r'\[@\s*(\d+)s\]')


class Runner:
    """Manages a single active simulation subprocess."""

    def __init__(self):
        self.lock = threading.Lock()
        self._reset()

    def _reset(self):
        self.run_id = None
        self.state = 'idle'      # idle|starting|running|post|done|error|stopped
        self.percent = 0.0
        self.log = []
        self.samples = []        # [{t, ts, speed, db}] parsed engine status lines
        self.mesh_cells = None
        self.error = None
        self.proc = None
        self.started_at = None
        self.nrts = 1
        self.end_db = -40.0
        self.not_converged = False
        self.info = {}           # parsed engine facts (version, dt, ...)
        self.warn_msgs = []      # solver warnings, deduped

    def status(self, offset=0):
        with self.lock:
            offset = max(0, min(int(offset), len(self.log)))
            return {
                'runId': self.run_id,
                'state': self.state,
                'percent': round(self.percent, 1),
                'error': self.error,
                'lines': self.log[offset:],
                'nextOffset': len(self.log),
                'elapsed': round(time.time() - self.started_at, 1) if self.started_at else 0,
                'samples': self.samples,
                'nrts': self.nrts,
                'endDb': self.end_db,
                'meshCells': self.mesh_cells,
                'notConverged': self.not_converged,
                'info': self.info,
                'warnMsgs': self.warn_msgs,
            }

    def start(self, model):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                raise RuntimeError('A simulation is already running')
            script = generate_script(model)  # raises ValidationError
            run_id = time.strftime('run_%Y%m%d_%H%M%S')
            sim_dir = SIM_ROOT / run_id
            sim_dir.mkdir(parents=True, exist_ok=True)
            (sim_dir / 'pcb_sim.m').write_text(script)

            self._reset()
            self.run_id = run_id
            self.state = 'starting'
            self.started_at = time.time()
            sim = model.get('sim') or {}
            self.nrts = max(1, int(sim.get('maxTimesteps') or 30000))
            self.end_db = float(sim.get('endCriteria') or -40.0)

            self.proc = subprocess.Popen(
                ['stdbuf', '-oL', '-eL', 'octave', '--no-gui', '--no-window-system', 'pcb_sim.m'],
                cwd=sim_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        return run_id

    def stop(self):
        with self.lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return False
            self.state = 'stopped'
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.25)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True

    # ---- internal ------------------------------------------------------
    def _append_line(self, line):
        line = line.rstrip()
        if not line:
            return
        self.log.append(line)
        if len(self.log) > MAX_LOG_LINES:
            del self.log[:len(self.log) - MAX_LOG_LINES]
        self._parse(line)

    INFO_PATTERNS = [
        ('version', re.compile(r'openEMS \d+bit -- version (\S+)')),
        ('engine', re.compile(r'Create FDTD operator \((.+)\)')),
        ('threads', re.compile(r'Best performance found using (\d+) threads')),
        ('fdtdSize', re.compile(r'FDTD simulation size:\s*(\d+x\d+x\d+)')),
        ('dt', re.compile(r'FDTD timestep is:\s*([0-9.eE+-]+)\s*s')),
        ('nyquist', re.compile(r'Nyquist rate:\s*(\d+)\s*timesteps')),
        ('excitationTs', re.compile(r'Excitation signal length is:\s*(\d+)\s*timesteps')),
        ('excitationS', re.compile(r'Excitation signal length is:.*\(([0-9.eE+-]+)s\)')),
        ('maxTs', re.compile(r'Max. number of timesteps:\s*(\d+)')),
        ('finalSpeed', re.compile(r'^Speed:\s*([\d.]+)\s*MCells/s')),
        ('runTime', re.compile(r'Time for \d+ iterations with [\d.]+ cells\s*:\s*([\d.]+)\s*sec')),
    ]

    def _parse(self, line):
        for key, rx in self.INFO_PATTERNS:
            m = rx.search(line)
            if m:
                self.info[key] = m.group(1)
        low = line.lower()
        if 'warning' in low and 'gui_marker' not in low:
            msg = line.strip()
            if msg not in self.warn_msgs and len(self.warn_msgs) < 20:
                self.warn_msgs.append(msg)
        elif self.warn_msgs and line.startswith('\t'):
            # openEMS continues warnings on indented lines
            if len(self.warn_msgs[-1]) < 400:
                self.warn_msgs[-1] += ' ' + line.strip()
        if 'GUI_MARKER: starting FDTD' in line:
            self.state = 'running'
        elif 'GUI_MARKER: mesh' in line:
            m = re.search(r'=\s*(\d+)\s*cells', line)
            if m:
                self.mesh_cells = int(m.group(1))
        elif 'Max. number of timesteps was reached before the end-criteria' in line:
            self.not_converged = True
        elif 'GUI_MARKER: post-processing' in line:
            self.state = 'post'
            self.percent = max(self.percent, 97.0)
        elif 'GUI_MARKER: done' in line:
            self.percent = 100.0
        elif self.state in ('starting', 'running'):
            pct = None
            sample = {}
            m = TIMESTEP_RE.search(line)
            if m:
                self.state = 'running'
                sample['ts'] = int(m.group(1))
                pct = 100.0 * sample['ts'] / self.nrts
            m = ENERGY_RE.search(line)
            if m and self.end_db < 0:
                db = float(m.group(1).replace(' ', ''))
                sample['db'] = db
                if db < 0:
                    pct = max(pct or 0.0, 100.0 * db / self.end_db)
            if 'ts' in sample:
                m = SPEED_RE.search(line)
                if m:
                    sample['speed'] = float(m.group(1))
                m = WALL_RE.search(line)
                if m:
                    sample['t'] = int(m.group(1))
                self.samples.append(sample)
                if len(self.samples) > 2000:
                    del self.samples[:1000]
            if pct is not None:
                self.percent = min(96.0, max(self.percent, pct))

    def _reader(self, proc):
        buf = b''
        stream = proc.stdout
        while True:
            chunk = stream.read(256)
            if not chunk:
                break
            buf += chunk
            # openEMS updates the timestep line with \r; treat it as a newline
            parts = re.split(rb'[\r\n]', buf)
            buf = parts.pop()
            with self.lock:
                for p in parts:
                    self._append_line(p.decode('utf-8', 'replace'))
        rc = proc.wait()
        with self.lock:
            if buf:
                self._append_line(buf.decode('utf-8', 'replace'))
            if self.state == 'stopped':
                pass
            elif rc == 0 and self.percent >= 100.0:
                self.state = 'done'
            else:
                self.state = 'error'
                self.error = f'Octave exited with code {rc}'
                tail = [l for l in self.log[-15:] if 'error' in l.lower()]
                if tail:
                    self.error += ': ' + tail[-1]


runner = Runner()


class TestRunner:
    """Runs the verification suite (unit tier via pytest, benchmark cases
    via scriptgen + octave) in a background thread and reports structured
    reference-vs-obtained metrics."""

    def __init__(self):
        self.lock = threading.Lock()
        self._reset()

    def _reset(self):
        self.state = 'idle'      # idle|running|done
        self.current = None
        self.unit = None         # {status, passed, failed, detail}
        self.results = {}        # case_id -> {status, metrics, elapsed, error}
        self.queue = []
        self.started_at = None

    def status(self):
        with self.lock:
            return {
                'state': self.state,
                'current': self.current,
                'unit': self.unit,
                'results': self.results,
                'queue': self.queue,
                'elapsed': round(time.time() - self.started_at, 1) if self.started_at else 0,
            }

    def start(self, run_unit, case_ids):
        with self.lock:
            if self.state == 'running':
                raise RuntimeError('tests are already running')
            if runner.state in ('starting', 'running', 'post'):
                raise RuntimeError('a simulation is running - wait for it to finish')
            self._reset()
            self.state = 'running'
            self.started_at = time.time()
            self.queue = list(case_ids)
            if run_unit:
                self.unit = {'status': 'running'}
            for cid in case_ids:
                self.results[cid] = {'status': 'queued'}
        threading.Thread(target=self._worker, args=(run_unit, case_ids), daemon=True).start()

    def _worker(self, run_unit, case_ids):
        if run_unit:
            self._run_unit()
        sys.path.insert(0, str(BASE_DIR / 'tests'))
        try:
            from cases import CASES
            from helpers import run_sim
        except Exception as e:
            with self.lock:
                for cid in case_ids:
                    self.results[cid] = {'status': 'error', 'error': f'cannot load cases: {e}'}
                self.state = 'done'
                self.current = None
            return
        for cid in case_ids:
            case = CASES.get(cid)
            if case is None:
                with self.lock:
                    self.results[cid] = {'status': 'error', 'error': 'unknown case'}
                continue
            with self.lock:
                self.current = cid
                self.results[cid] = {'status': 'running'}
            t0 = time.time()
            try:
                rows = run_sim(case['build'](), f'gui_{cid}', timeout=600)
                metrics = case['evaluate'](rows)
                ok = all(m['pass'] for m in metrics)
                with self.lock:
                    self.results[cid] = {
                        'status': 'pass' if ok else 'fail',
                        'metrics': metrics,
                        'elapsed': round(time.time() - t0, 1),
                    }
            except Exception as e:
                with self.lock:
                    self.results[cid] = {
                        'status': 'error',
                        'error': str(e)[-600:],
                        'elapsed': round(time.time() - t0, 1),
                    }
        with self.lock:
            self.state = 'done'
            self.current = None

    def _run_unit(self):
        try:
            res = subprocess.run(
                [sys.executable or 'python3', '-m', 'pytest', '-m', 'not sim', '--tb=line'],
                cwd=BASE_DIR, capture_output=True, text=True, timeout=180)
            out = res.stdout + res.stderr
            m = re.search(r'(\d+) passed', out)
            f = re.search(r'(\d+) failed', out)
            with self.lock:
                self.unit = {
                    'status': 'pass' if res.returncode == 0 else 'fail',
                    'passed': int(m.group(1)) if m else 0,
                    'failed': int(f.group(1)) if f else 0,
                    'detail': out[-1500:] if res.returncode != 0 else '',
                }
        except Exception as e:
            with self.lock:
                self.unit = {'status': 'error', 'detail': str(e)}


test_runner = TestRunner()


@app.get('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.post('/api/script')
def api_script():
    try:
        script = generate_script(request.get_json(force=True))
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'script': script})


@app.post('/api/run')
def api_run():
    try:
        run_id = runner.start(request.get_json(force=True))
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    return jsonify({'runId': run_id})


@app.get('/api/status')
def api_status():
    return jsonify(runner.status(request.args.get('offset', 0)))


@app.post('/api/stop')
def api_stop():
    return jsonify({'stopped': runner.stop()})


@app.post('/api/mesh')
def api_mesh():
    try:
        mesh = build_mesh(request.get_json(force=True))
    except (ValidationError, KeyError, TypeError, ValueError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(mesh)


def _safe_project_name(name):
    safe = re.sub(r'[^\w.-]+', '_', (name or '').strip()).strip('._')
    return safe[:60] or None


def _run_dir(run_id):
    """Resolve a results id: run_* under sims/, proj_* under projects/."""
    if re.fullmatch(r'run_[0-9_]+', run_id):
        d = SIM_ROOT / run_id
        return d if d.is_dir() else None
    m = re.fullmatch(r'proj_([\w.-]+)', run_id)
    if m:
        d = PROJ_ROOT / m.group(1) / 'results'
        return d if d.is_dir() else None
    return None


def _read_probe(path):
    """openEMS probe file: comment lines then two whitespace-separated
    columns (time, value)."""
    t, v = [], []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith(('%', '#')):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                t.append(float(parts[0]))
                v.append(float(parts[1]))
            except ValueError:
                continue
    return t, v


@app.get('/api/results/<run_id>/timedomain')
def api_timedomain(run_id):
    """Port voltage/current signals u(t), i(t) recorded by the ports."""
    sim_dir = _run_dir(run_id)
    if sim_dir is None:
        return jsonify({'error': 'bad run id'}), 400
    # lumped ports write port_ut{N}; MSL ports write port_ut{N}A/B/C probes
    # around the measurement plane (B sits on it) and port_it{N}A/B either
    # side of it (calcPort averages them)
    variants = {}
    for ut in sim_dir.glob('port_ut*'):
        m = re.fullmatch(r'port_ut_?(\d+)([ABC])?', ut.name)
        if m:
            variants.setdefault(int(m.group(1)), {})[m.group(2) or ''] = ut
    ports = []
    for n in sorted(variants):
        v = variants[n]
        ut = v.get('') or v.get('B') or v.get('A')
        if ut is None:
            continue
        t, u = _read_probe(ut)
        i_vals = []
        if '' in v:   # lumped port
            for it_name in (f'port_it_{n}', f'port_it{n}'):
                it_path = sim_dir / it_name
                if it_path.is_file():
                    i_vals = _read_probe(it_path)[1]
                    break
        else:         # MSL port: average the probes straddling the plane

            acc = []
            for sfx in ('A', 'B'):
                it_path = sim_dir / f'port_it{n}{sfx}'
                if it_path.is_file():
                    acc.append(_read_probe(it_path)[1])
            if acc:
                nmin = min(len(a) for a in acc)
                i_vals = [sum(a[k] for a in acc) / len(acc) for k in range(nmin)]
        ports.append({'n': n, 't': t, 'u': u, 'i': i_vals})
    if not ports:
        return jsonify({'error': 'no time-domain data for this run'}), 404
    # decimate to a plottable size on a common stride
    nmax = max(len(p['t']) for p in ports)
    stride = max(1, nmax // 1500)
    out = []
    for p in ports:
        out.append({
            'n': p['n'],
            't': p['t'][::stride],
            'u': p['u'][::stride],
            'i': p['i'][::stride],
        })
    return jsonify({'ports': out})


@app.get('/api/results/<run_id>/sparams.csv')
def api_results(run_id):
    d = _run_dir(run_id)
    if d is None or not (d / 'sparams.csv').is_file():
        return jsonify({'error': 'no results for this run'}), 404
    return send_from_directory(d, 'sparams.csv')


@app.get('/api/results/<run_id>/jdumps')
def api_jdumps(run_id):
    d = _run_dir(run_id)
    if d is None or not (d / 'jdumps.csv').is_file():
        return jsonify({'dumps': []})
    dumps = []
    for line in (d / 'jdumps.csv').read_text().strip().splitlines():
        parts = line.split(',')
        if len(parts) == 3 and (d / f'J_{re.sub(r"[^A-Za-z0-9]", "_", parts[0])}_f{parts[1]}.bin').is_file():
            dumps.append({'layer': parts[0], 'k': int(parts[1]), 'freq': float(parts[2])})
    return jsonify({'dumps': dumps})


@app.get('/api/results/<run_id>/jdump/<layer>/<int:k>')
def api_jdump(run_id, layer, k):
    d = _run_dir(run_id)
    fname = f'J_{re.sub(r"[^A-Za-z0-9]", "_", layer)}_f{k}.bin'
    if d is None or not (d / fname).is_file():
        return jsonify({'error': 'no such dump'}), 404
    return send_from_directory(d, fname, mimetype='application/octet-stream')


@app.get('/api/tests')
def api_tests_list():
    sys.path.insert(0, str(BASE_DIR / 'tests'))
    try:
        from cases import CASES
    except Exception as e:
        return jsonify({'error': f'cannot load test cases: {e}'}), 500
    return jsonify({'cases': [
        {'id': cid, 'title': c['title'], 'desc': c['desc'], 'minutes': c['minutes']}
        for cid, c in CASES.items()]})


@app.post('/api/tests/run')
def api_tests_run():
    data = request.get_json(force=True)
    try:
        test_runner.start(bool(data.get('unit')), list(data.get('cases') or []))
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    return jsonify({'started': True})


@app.get('/api/tests/status')
def api_tests_status():
    return jsonify(test_runner.status())


@app.get('/api/projects')
def api_projects_list():
    out = []
    for d in sorted(PROJ_ROOT.iterdir()):
        pj = d / 'project.json'
        if d.is_dir() and pj.is_file():
            out.append({
                'name': d.name,
                'mtime': pj.stat().st_mtime,
                'hasResults': (d / 'results' / 'sparams.csv').is_file(),
            })
    out.sort(key=lambda p: -p['mtime'])
    return jsonify({'projects': out})


@app.post('/api/projects/save')
def api_projects_save():
    data = request.get_json(force=True)
    safe = _safe_project_name(data.get('name'))
    if not safe:
        return jsonify({'error': 'invalid project name'}), 400
    project = data.get('project')
    if not isinstance(project, dict):
        return jsonify({'error': 'missing project data'}), 400
    pdir = PROJ_ROOT / safe
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / 'project.json').write_text(json.dumps(project, indent=1))
    copied = False
    run_id = data.get('runId')
    if run_id and re.fullmatch(r'run_[0-9_]+', str(run_id)):
        src = SIM_ROOT / run_id
        if src.is_dir() and (src / 'sparams.csv').is_file():
            dst = pdir / 'results'
            if dst.is_dir():
                shutil.rmtree(dst)
            dst.mkdir()
            for f in src.iterdir():
                # skip bulky raw field dumps - the GUI uses the .bin exports
                if f.suffix in ('.h5', '.vtr'):
                    continue
                shutil.copy2(f, dst / f.name)
            copied = True
    return jsonify({'name': safe, 'resultsSaved': copied})


@app.get('/api/projects/<name>')
def api_projects_get(name):
    safe = _safe_project_name(name)
    pdir = PROJ_ROOT / (safe or '')
    if not safe or not (pdir / 'project.json').is_file():
        return jsonify({'error': 'no such project'}), 404
    project = json.loads((pdir / 'project.json').read_text())
    has_results = (pdir / 'results' / 'sparams.csv').is_file()
    return jsonify({'project': project, 'resultsId': f'proj_{safe}' if has_results else None})


@app.delete('/api/projects/<name>')
def api_projects_delete(name):
    safe = _safe_project_name(name)
    pdir = PROJ_ROOT / (safe or '')
    if not safe or not (pdir / 'project.json').is_file():
        return jsonify({'error': 'no such project'}), 404
    shutil.rmtree(pdir)
    return jsonify({'deleted': safe})


@app.post('/api/import/gerber')
def api_import_gerber():
    data = request.get_json(force=True)
    try:
        return jsonify(parse_gerber(data.get('content') or ''))
    except GerberError as e:
        return jsonify({'error': str(e)}), 400


@app.post('/api/import/drill')
def api_import_drill():
    data = request.get_json(force=True)
    try:
        return jsonify(parse_excellon(data.get('content') or ''))
    except GerberError as e:
        return jsonify({'error': str(e)}), 400


@app.get('/api/results/<run_id>/jtdumps')
def api_jtdumps(run_id):
    d = _run_dir(run_id)
    if d is None or not (d / 'jtdumps.csv').is_file():
        return jsonify({'dumps': []})
    dumps = []
    for line in (d / 'jtdumps.csv').read_text().strip().splitlines():
        parts = line.split(',')
        if len(parts) == 2 and (d / f'J_td_{re.sub(r"[^A-Za-z0-9]", "_", parts[0])}.bin').is_file():
            dumps.append({'layer': parts[0], 'frames': int(parts[1])})
    return jsonify({'dumps': dumps})


@app.get('/api/results/<run_id>/jtdump/<layer>')
def api_jtdump(run_id, layer):
    d = _run_dir(run_id)
    fname = f'J_td_{re.sub(r"[^A-Za-z0-9]", "_", layer)}.bin'
    if d is None or not (d / fname).is_file():
        return jsonify({'error': 'no such dump'}), 404
    return send_from_directory(d, fname, mimetype='application/octet-stream')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8036))
    print(f'OpenEMS PCB Studio: http://localhost:{port}')
    app.run(host='127.0.0.1', port=port, threaded=True)
