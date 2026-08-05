"""End-to-end device co-simulation test (octave): a microstrip line with a
gap, the two gap pads fed by lumped ports, and an ideal through 2-port
device connected across them in circuit space. The combined external
result must approximate the continuous line: high S21, low S11.

Replicates exactly what the server's batch runner does: one excitation run
per port, then combine_devices().
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server
from scriptgen import generate_script
from helpers import (stackup, sim_settings, rect, lumped_port, msl_port,
                     parse_sparams, db, WORK)

pytestmark = pytest.mark.sim


def _model():
    return {
        'board': {'width': 30, 'height': 16},
        'stackup': stackup(er=4.6, h=0.8),
        'shapes': [rect('tl1', 5, 7.275, 9.5, 1.45, thirds=True),
                   rect('tl2', 15.5, 7.275, 9.5, 1.45, thirds=True)],
        'ports': [
            msl_port(1, 5, 7.275, 4, 1.45, '+x', excite=True),
            lumped_port(2, 14, 7.275, 0.5, 1.45),   # left gap pad
            lumped_port(3, 15.5, 7.275, 0.5, 1.45),  # right gap pad
            msl_port(4, 21, 7.275, 4, 1.45, '-x'),
        ],
        'devices': [{'ref': 'U1', 'file': 'test_through.s2p', 'pins': [2, 3]}],
        'sim': sim_settings(boundary='PML_8', airMargin=25,
                            endCriteria=-40, maxTimesteps=60000),
    }


def test_cosim_through_device_restores_line():
    (server.DEV_ROOT / 'test_through.s2p').write_text(
        '# GHz S RI R 50\n0.5 0 0 1 0 1 0 0 0\n10 0 0 1 0 1 0 0 0\n')
    try:
        model = _model()
        devices = server.validate_devices(model)
        run_dir = WORK / 'cosim'
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)

        stages = []
        for n in (2, 3, 4):
            m2 = json.loads(json.dumps(model))
            for q in m2['ports']:
                q['excite'] = (q['number'] == n)
            d = run_dir / f'exc_{n}'
            d.mkdir()
            (d / 'pcb_sim.m').write_text(generate_script(m2))
            stages.append({'dir': d, 'exc': n})
        (run_dir / 'pcb_sim.m').write_text(generate_script(model))
        stages.append({'dir': run_dir, 'exc': 1})

        for st in stages:
            res = subprocess.run(['octave', '--no-gui', 'pcb_sim.m'],
                                 cwd=st['dir'], capture_output=True,
                                 text=True, timeout=420)
            log = res.stdout + res.stderr
            assert 'GUI_MARKER: done' in log, \
                f'excitation {st["exc"]} failed:\n{log[-1500:]}'

        server.combine_devices(run_dir, model, stages, devices)
        rows = parse_sparams(run_dir / 'sparams.csv')
        # external ports are 1 and 4
        mid = min(rows, key=lambda r: abs(r['f'] - 2e9))
        s11, s41 = db(mid['s'][1]), db(mid['s'][4])
        assert s41 > -2.5, f'combined S41 {s41:.1f} dB - device link too lossy'
        assert s11 < -10, f'combined S11 {s11:.1f} dB - bad match through device'
        # sanity: without the device the gap blocks transmission
        raw = parse_sparams(run_dir / 'sparams_board.csv')
        raw_mid = min(raw, key=lambda r: abs(r['f'] - 2e9))
        assert db(raw_mid['s'][4]) < -8, 'gap alone should block the line'
    finally:
        (server.DEV_ROOT / 'test_through.s2p').unlink()
