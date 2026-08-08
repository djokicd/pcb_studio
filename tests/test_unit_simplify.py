"""Unit tests for the geometry simplification tool (no octave needed)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gerber import _stadium
from geometry import dp_polyline, dp_ring, resample_polyline, shape_outline
from simplify import simplify_shapes, stadium_params


def _r4(v):
    return round(v, 4)


def _stroke(sid, x0, y0, x1, y1, w=0.8, layer='top'):
    pts = _stadium(x0, y0, x1, y1, w)
    return {'id': sid, 'name': f's{sid}', 'type': 'poly', 'layer': layer,
            'priority': 10, 'meshBbox': True, 'mesh': {},
            'pts': [[_r4(a), _r4(b)] for a, b in pts]}


def _arc_chain(cx, cy, r, a0, a1, n, w=0.8, start_id=1):
    """n stroke polygons sampling an arc - what the old importer emitted."""
    out = []
    prev = None
    for k in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * k / n)
        p = (cx + r * math.cos(a), cy + r * math.sin(a))
        if prev is not None:
            out.append(_stroke(start_id + k, prev[0], prev[1], p[0], p[1], w))
        prev = p
    return out


def test_stadium_params_roundtrip():
    pts = [[_r4(a), _r4(b)] for a, b in _stadium(2, 3, 7, 5, 0.6)]
    sp = stadium_params(pts)
    assert sp is not None
    start, end, w = sp
    assert math.hypot(start[0] - 2, start[1] - 3) < 1e-3
    assert math.hypot(end[0] - 7, end[1] - 5) < 1e-3
    assert abs(w - 0.6) < 1e-3


def test_stadium_params_rejects_arbitrary_poly():
    # an 18-vertex regular polygon is not a stroke outline
    pts = [[10 + 3 * math.cos(2 * math.pi * k / 18),
            10 + 3 * math.sin(2 * math.pi * k / 18)] for k in range(18)]
    assert stadium_params(pts) is None


def test_chain_merges_into_one_trace():
    shapes = _arc_chain(20, 10, 5, 0, 90, 18)
    out, stats = simplify_shapes(shapes, {'tol': 0.02})
    assert stats['traces'] == 1 and stats['strokesMerged'] == 18
    assert len(out) == 1 and out[0]['type'] == 'trace'
    t = out[0]
    assert abs(t['width'] - 0.8) < 1e-3
    assert t['id'] == shapes[0]['id'] and t['layer'] == 'top'
    # decimated but still on the arc within tolerance
    assert 4 <= len(t['pts']) < 19
    for x, y in t['pts']:
        assert abs(math.hypot(x - 20, y - 10) - 5.0) < 0.03


def test_chain_breaks_on_layer_and_gap():
    a = _arc_chain(20, 10, 5, 0, 45, 6, start_id=1)
    b = _arc_chain(40, 10, 5, 90, 135, 6, start_id=50)     # disjoint
    c = [_stroke(99, 1, 1, 4, 1, layer='bot')]             # other layer
    out, stats = simplify_shapes(a + b + c, {'tol': 0.02})
    assert stats['traces'] == 2
    # the lone stroke on 'bot' stays a polygon (nothing to chain with)
    assert sum(1 for s in out if s['type'] == 'poly') == 1


def test_lone_stroke_kept_as_poly():
    out, stats = simplify_shapes([_stroke(1, 2, 2, 8, 2)], {'tol': 0.02})
    assert stats['traces'] == 0
    assert out[0]['type'] == 'poly'


def test_poly_outline_decimated_within_tol():
    ring = [[15 + 6 * math.cos(2 * math.pi * k / 360),
             12 + 6 * math.sin(2 * math.pi * k / 360)] for k in range(360)]
    s = {'id': 7, 'name': 'pour', 'type': 'poly', 'layer': 'top',
         'priority': 10, 'meshBbox': True, 'mesh': {}, 'pts': ring}
    out, stats = simplify_shapes([s], {'tol': 0.05, 'traces': False})
    assert stats['polysSimplified'] == 1
    pts = out[0]['pts']
    assert len(pts) < 90
    for x, y in pts:                       # vertices still on the circle
        assert abs(math.hypot(x - 15, y - 12) - 6.0) < 1e-3


def test_scope_ids_limits_changes():
    shapes = _arc_chain(20, 10, 5, 0, 90, 10)
    keep = [s['id'] for s in shapes[:3]]
    out, stats = simplify_shapes(shapes, {'tol': 0.02, 'ids': keep})
    assert stats['strokesMerged'] == 3
    assert sum(1 for s in out if s['type'] == 'trace') == 1
    assert sum(1 for s in out if s['type'] == 'poly') == len(shapes) - 3


def test_max_seg_resamples_curves():
    shapes = _arc_chain(20, 10, 5, 0, 90, 18)
    out, _ = simplify_shapes(shapes, {'tol': 0.02, 'maxSeg': 0.5})
    pts = out[0]['pts']
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        assert math.hypot(x1 - x0, y1 - y0) <= 0.5 + 1e-6


def test_dp_ring_keeps_small_polys():
    tri = [(0, 0), (4, 0), (2, 3)]
    assert dp_ring(list(tri), 0.1) == list(tri)


def test_resample_polyline_closed():
    sq = [(0, 0), (3, 0), (3, 3), (0, 3)]
    out = resample_polyline(sq, 1.0, closed=True)
    assert len(out) == 12
    assert out[0] == (0, 0)


def test_converted_trace_outline_matches_strokes():
    """The trace outline stays within a few 10s of um of the original
    stroke union: same copper, two tessellations."""
    shapes = _arc_chain(20, 10, 5, 0, 90, 18)
    out, _ = simplify_shapes(shapes, {'tol': 0.02})
    outline = shape_outline(out[0])
    # every outline vertex lies within tol+cap-sag of the swept annulus
    for x, y in outline:
        d = math.hypot(x - 20, y - 10)
        assert 5 - 0.45 < d < 5 + 0.45
