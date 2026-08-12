#!/usr/bin/env python3
"""Measure openEMS thread scaling for a project ON THIS MACHINE.

    scripts/bench-threads.py <project> [--threads 1,2,4,8,16] [--ts 8000]

Runs the project's simulation with a fixed number of timesteps (so runs
are comparable) once per thread count, sequentially on an otherwise idle
machine, and prints the wall time and steady-state solver speed of each.

Use it to sanity-check the automatic thread balancing on a new machine:
the runner gives each parallel excitation an even cores/parallel share,
capped at one thread per CELLS_PER_THREAD cells (server.py). If this
benchmark shows your machine saturating earlier or later than that cap,
set "Threads per excitation" in the Run tab accordingly - it overrides
the automatic split.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meshlines import build_mesh          # noqa: E402
from scriptgen import generate_script     # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('project', help='project name (in projects/) or path '
                                    'to a project.json')
    ap.add_argument('--threads', default='1,2,4,8',
                    help='comma-separated thread counts to try')
    ap.add_argument('--ts', type=int, default=8000,
                    help='fixed timestep count per run')
    args = ap.parse_args()

    pf = Path(args.project)
    if not pf.is_file():
        pf = ROOT / 'projects' / args.project / 'project.json'
    model = json.loads(pf.read_text())
    model = model.get('project', model)
    sim = model.setdefault('sim', {})
    # comparable runs: fixed length, no early stop, no field dumps
    sim.update(maxTimesteps=args.ts, endCriteria=-300,
               dumpJ=False, dumpJt=False)

    cells = build_mesh(model)['cells']
    print(f'{pf}: {cells:,} cells, {args.ts} timesteps per run')
    results = []
    for nt in [int(t) for t in args.threads.split(',')]:
        sim['numThreads'] = nt
        with tempfile.TemporaryDirectory(prefix='benchthr_') as d:
            (Path(d) / 'pcb_sim.m').write_text(generate_script(model))
            t0 = time.time()
            r = subprocess.run(['octave', '--no-gui', 'pcb_sim.m'], cwd=d,
                               capture_output=True, text=True)
            dt = time.time() - t0
        out = r.stdout + r.stderr
        ok = 'GUI_MARKER: done' in out
        speeds = re.findall(r'Speed:\s*([\d.]+)', out)
        speed = float(speeds[-1]) if speeds else 0.0
        results.append((nt, dt, speed, ok))
        print(f'  {nt:3d} thread{"s" if nt != 1 else " "}: {dt:7.1f} s'
              f'   {speed:8.2f} MC/s{"" if ok else "   FAILED"}')
    good = [r for r in results if r[3]]
    if good:
        best = max(good, key=lambda r: r[2])
        print(f'fastest: {best[0]} threads ({best[2]:.0f} MC/s) - '
              f'~{max(1, cells // max(1, best[0])):,} cells/thread')


if __name__ == '__main__':
    main()
