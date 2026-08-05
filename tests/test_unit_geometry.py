"""Unit tests for geometry helpers and mesh generation (no octave needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geometry import comp_element_box, shape_outline, stackup_z
from meshlines import build_mesh
from helpers import stackup, sim_settings, rect, lumped_port


def test_stackup_z():
    cond, diel, total = stackup_z(stackup(h=0.8))
    assert cond['bot'] == 0.0
    assert cond['top'] == 0.8
    assert diel['core'] == (0.0, 0.8)
    assert total == 0.8


def test_comp_element_box_shrinks_to_gap():
    # 0603 body (1.6 mm) centred on a 1.0 mm gap between two traces
    shapes = [rect('a', 0, 9, 19.5, 2), rect('b', 20.5, 9, 19.5, 2)]
    c = {'ctype': 'R', 'value': 100, 'package': '0603', 'x': 20, 'y': 10,
         'rot': 0, 'layer': 'top'}
    x0, y0, x1, y1, ny, connected = comp_element_box(c, shapes)
    assert connected
    assert ny == 0
    assert abs(x0 - 19.5) < 1e-6 and abs(x1 - 20.5) < 1e-6


def test_comp_element_box_disconnected():
    # no copper anywhere near the component
    c = {'ctype': 'R', 'value': 100, 'package': '0603', 'x': 20, 'y': 10,
         'rot': 0, 'layer': 'top'}
    *_, connected = comp_element_box(c, [])
    assert not connected


def test_shape_outlines():
    assert len(shape_outline({'type': 'rect', 'x': 0, 'y': 0, 'w': 1, 'h': 1})) == 4
    assert len(shape_outline({'type': 'circle', 'cx': 0, 'cy': 0, 'r': 2})) >= 32
    poly = shape_outline({'type': 'poly', 'pts': [[0, 0], [2, 0], [1, 2]]})
    assert poly == [(0, 0), (2, 0), (1, 2)]


def _model(shapes, **sim_kw):
    return {
        'board': {'width': 40, 'height': 20},
        'stackup': stackup(),
        'shapes': shapes,
        'ports': [lumped_port(1, 1, 9, 0.5, 2, excite=True)],
        'sim': sim_settings(**sim_kw),
    }


def test_mesh_local_region_is_honoured():
    m = build_mesh(_model([rect('t', 10, 8, 10, 3, res=0.4)]))
    xs = m['x']
    gaps = [(a + b) / 2 for a, b in zip(xs, xs[1:])]
    for (a, b), mid in zip(zip(xs, xs[1:]), gaps):
        if 10 < mid < 20:
            assert b - a <= 0.4 + 1e-6, f'cell {b - a:.3f} inside refinement region'


def test_mesh_merge_close_lines():
    # two abutting rects with edges 30 um apart must not create a 30 um cell
    m = build_mesh(_model([rect('a', 10, 8, 5, 3), rect('b', 15.03, 8, 5, 3)]))
    diffs = [b - a for a, b in zip(m['x'], m['x'][1:])]
    assert min(diffs) > 0.05


def test_mesh_z_air_cells_mirrored():
    m = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    z = m['z']
    # substrate is 0..0.8; expect fine mirrored lines just above/below
    above = [v for v in z if 0.8 < v < 1.7]
    below = [v for v in z if -0.9 < v < 0]
    assert len(above) >= 3
    assert len(below) >= 3


def test_mesh_cells_count_consistent():
    m = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    assert m['cells'] == len(m['x']) * len(m['y']) * len(m['z'])
