"""Unit tests for the polygon union used by the Simplify tool."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polybool import ring_area, union_rings, verify_union


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def circle(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * k / n),
             cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def area_of(outers, holes=()):
    return sum(abs(ring_area(r)) for r in outers) - sum(abs(ring_area(r)) for r in holes)


def test_two_overlapping_rects():
    outers, holes = union_rings([rect(0, 0, 10, 10), rect(5, 5, 15, 15)])
    assert len(outers) == 1 and not holes
    assert abs(area_of(outers) - 175.0) < 1e-6     # 100 + 100 - 25


def test_shared_edge_merges_without_a_seam():
    """Two rectangles that only touch: the shared edge is interior to the
    union and must not survive as boundary (a midpoint-inside test gets
    this wrong, which is why segments are classified from both sides)."""
    outers, holes = union_rings([rect(0, 0, 10, 10), rect(10, 0, 20, 10)])
    assert len(outers) == 1 and not holes
    assert abs(area_of(outers) - 200.0) < 1e-6
    assert not any(abs(x - 10.0) < 1e-9 and 0 < y < 10 for x, y in outers[0])


def test_contained_and_identical():
    for rings, want in (([rect(0, 0, 10, 10), rect(2, 2, 4, 4)], 100.0),
                        ([rect(0, 0, 10, 10), rect(0, 0, 10, 10)], 100.0)):
        outers, holes = union_rings(rings)
        assert len(outers) == 1 and not holes
        assert abs(area_of(outers) - want) < 1e-6


def test_disjoint_stay_separate():
    outers, holes = union_rings([rect(0, 0, 4, 4), rect(6, 6, 10, 10)])
    assert len(outers) == 2 and not holes
    assert abs(area_of(outers) - 32.0) < 1e-6


def test_enclosed_gap_is_reported_as_a_hole():
    """Four bars around a courtyard: the union has a hole, which one
    closed outline cannot express - the caller must be told so it can
    leave the shapes alone instead of filling the gap."""
    rings = [rect(0, 0, 10, 2), rect(0, 8, 10, 10),
             rect(0, 0, 2, 10), rect(8, 0, 10, 10)]
    outers, holes = union_rings(rings)
    assert len(outers) == 1 and len(holes) == 1
    assert abs(abs(ring_area(holes[0])) - 36.0) < 1e-6     # 6 x 6 courtyard


def test_pad_on_a_pour():
    rings = [rect(0, 0, 10, 4), circle(5, 4, 1.5)]
    outers, holes = union_rings(rings)
    assert len(outers) == 1 and not holes
    assert verify_union(rings, outers, holes) == 0.0
    # half the disc sticks out above the pour
    assert 40.0 + math.pi * 1.5 ** 2 / 2 * 0.98 < area_of(outers) < 40.0 + math.pi * 1.5 ** 2 / 2 * 1.02


def test_orientation_outer_ccw():
    outers, _ = union_rings([rect(0, 0, 10, 10), rect(5, 5, 15, 15)])
    assert ring_area(outers[0]) > 0


def test_noisy_duplicate_vertices_still_close():
    """Imported outlines carry sub-micron noise; endpoints that differ by
    a fraction of a micron must weld rather than break the boundary."""
    a = rect(0, 0, 10, 10)
    b = [(10.0000004, 0), (20, 0), (20, 10), (9.9999996, 10.0000003)]
    res = union_rings([a, b])
    assert res is not None
    outers, holes = res
    assert len(outers) == 1 and not holes
    assert abs(area_of(outers) - 200.0) < 1e-3


def test_verify_catches_a_wrong_answer():
    rings = [rect(0, 0, 10, 10), rect(5, 5, 15, 15)]
    outers, holes = union_rings(rings)
    assert verify_union(rings, outers, holes) == 0.0
    # a deliberately wrong "union" must not pass the check
    assert verify_union(rings, [rect(0, 0, 3, 3)], []) > 0.1
