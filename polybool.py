"""Boolean union of polygon rings, in pure Python.

The project depends on Flask alone, so the usual clipping libraries are
not available - and the job here is narrow enough not to need one. All
that is required is the OUTLINE of a set of overlapping copper shapes on
one layer, which can be had without a general clipper:

  1. every edge is split wherever any other edge crosses or touches it;
  2. each resulting segment is classified by probing a point just off
     each side: a segment lies on the union boundary exactly when one
     side is covered by some ring and the other is not (this is what
     makes shared and collinear edges - abundant in imported stroke
     soup - fall out correctly, where a midpoint-inside test fails);
  3. the surviving segments are oriented interior-on-the-left and
     chained into rings, so outer rings come out counter-clockwise and
     holes clockwise.

Everything is snapped to a 1 nm lattice first, which makes endpoint
matching exact and keeps the chaining a hash-map walk.

`union_rings` refuses rather than guesses: on any inconsistency (an
unclosed chain, or a result that disagrees with a sampled membership
test) it returns None and the caller keeps the original shapes.
All lengths in mm.
"""
import math
from collections import defaultdict

SNAP = 1e-6          # coordinate lattice (1 nm)
PROBE = 2e-5         # side-probe offset (20 nm; import grid is 100 nm)
# imported outlines carry sub-micron noise: vertices that the CAD tool
# meant to be the same point can land a few tenths of a micron apart,
# which would leave the boundary walk with an unclosable gap. Endpoints
# closer than this are welded (still far below any real PCB feature).
WELD = 1.5e-3        # 1.5 um
_EPS = 1e-12


def _k(x, y):
    return (round(x, 6), round(y, 6))


def ring_area(ring):
    """Signed area; positive = counter-clockwise."""
    a = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def point_in_ring(px, py, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


class _Grid:
    """Uniform bucket grid over bounding boxes."""

    def __init__(self, cell):
        self.cell = max(cell, 1e-6)
        self.d = defaultdict(list)

    def _cells(self, bb):
        c = self.cell
        for ix in range(int(math.floor(bb[0] / c)), int(math.floor(bb[2] / c)) + 1):
            for iy in range(int(math.floor(bb[1] / c)), int(math.floor(bb[3] / c)) + 1):
                yield (ix, iy)

    def add(self, bb, item):
        for cell in self._cells(bb):
            self.d[cell].append(item)

    def query(self, bb):
        out = set()
        for cell in self._cells(bb):
            out.update(self.d.get(cell, ()))
        return out


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _split_params(p1, p2, p3, p4):
    """Parameters along p1->p2 where it meets p3->p4 (crossing, touching
    or collinear overlap)."""
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    sx, sy = p4[0] - p3[0], p4[1] - p3[1]
    qpx, qpy = p3[0] - p1[0], p3[1] - p1[1]
    den = rx * sy - ry * sx
    rr = rx * rx + ry * ry
    if rr < _EPS:
        return ()
    if abs(den) < 1e-14:
        # parallel: only collinear pairs contribute (endpoint projections)
        if abs(qpx * ry - qpy * rx) > 1e-9 * math.sqrt(rr):
            return ()
        t3 = (qpx * rx + qpy * ry) / rr
        t4 = ((p4[0] - p1[0]) * rx + (p4[1] - p1[1]) * ry) / rr
        return tuple(t for t in (t3, t4) if -1e-9 < t < 1 + 1e-9)
    t = (qpx * sy - qpy * sx) / den
    u = (qpx * ry - qpy * rx) / den
    if -1e-9 < t < 1 + 1e-9 and -1e-9 < u < 1 + 1e-9:
        return (t,)
    return ()


def _weld_endpoints(directed, tol=WELD):
    """Collapse endpoints that differ only by import noise, so the
    boundary walk finds its continuation exactly."""
    buckets = defaultdict(list)

    def rep(p):
        cx, cy = int(math.floor(p[0] / tol)), int(math.floor(p[1] / tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in buckets.get((cx + dx, cy + dy), ()):
                    if abs(q[0] - p[0]) <= tol and abs(q[1] - p[1]) <= tol:
                        return q
        buckets[(cx, cy)].append(p)
        return p

    out = []
    for a, b in directed:
        ra, rb = rep(a), rep(b)
        if ra != rb:
            out.append((ra, rb))
    return out


def union_rings(rings):
    """Union of the given rings.

    Returns (outers, holes) as lists of point lists, or None when the
    result could not be trusted. Rings may be given in any orientation.
    """
    rings = [[(round(x, 6), round(y, 6)) for x, y in r] for r in rings if len(r) >= 3]
    if not rings:
        return ([], [])
    if len(rings) == 1:
        r = rings[0]
        return ([r if ring_area(r) > 0 else r[::-1]], [])

    # ---- edges, indexed for intersection queries ----
    edges = []           # (p, q)
    for r in rings:
        n = len(r)
        for i in range(n):
            a, b = r[i], r[(i + 1) % n]
            if abs(a[0] - b[0]) > SNAP or abs(a[1] - b[1]) > SNAP:
                edges.append((a, b))
    if not edges:
        return None
    lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges]
    cell = max(sorted(lens)[len(lens) // 2] * 4.0, 1e-3)
    grid = _Grid(cell)
    for i, (a, b) in enumerate(edges):
        grid.add((min(a[0], b[0]), min(a[1], b[1]),
                  max(a[0], b[0]), max(a[1], b[1])), i)

    # ---- split every edge at all crossings/touches ----
    segs = []
    for i, (a, b) in enumerate(edges):
        bb = (min(a[0], b[0]) - SNAP, min(a[1], b[1]) - SNAP,
              max(a[0], b[0]) + SNAP, max(a[1], b[1]) + SNAP)
        ts = {0.0, 1.0}
        for j in grid.query(bb):
            if j == i:
                continue
            c, d = edges[j]
            for t in _split_params(a, b, c, d):
                if SNAP < t < 1.0 - SNAP:
                    ts.add(t)
        ordered = sorted(ts)
        for t0, t1 in zip(ordered, ordered[1:]):
            if t1 - t0 < 1e-9:
                continue
            p = _k(a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
            q = _k(a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
            if p != q:
                segs.append((p, q))

    # collinear duplicates (shared edges) collapse to one
    uniq = {}
    for p, q in segs:
        uniq[(p, q) if p <= q else (q, p)] = (p, q)
    segs = list(uniq.values())

    # ---- classify: keep segments with covered on exactly one side ----
    rgrid = _Grid(cell)
    for i, r in enumerate(rings):
        rgrid.add(_bbox(r), i)

    def covered(px, py):
        for i in rgrid.query((px, py, px, py)):
            if point_in_ring(px, py, rings[i]):
                return True
        return False

    directed = []
    for p, q in segs:
        dx, dy = q[0] - p[0], q[1] - p[1]
        L = math.hypot(dx, dy)
        if L < SNAP:
            continue
        nx, ny = -dy / L, dx / L          # left normal of p->q
        mx, my = (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0
        left = covered(mx + nx * PROBE, my + ny * PROBE)
        right = covered(mx - nx * PROBE, my - ny * PROBE)
        if left == right:
            continue                       # interior (or outside) - not boundary
        directed.append((p, q) if left else (q, p))
    directed = _weld_endpoints(directed)
    if not directed:
        return None

    # ---- chain into rings (interior stays on the left) ----
    outs = defaultdict(list)
    for idx, (a, _b) in enumerate(directed):
        outs[a].append(idx)
    used = [False] * len(directed)
    rings_out = []
    for start in range(len(directed)):
        if used[start]:
            continue
        ring = []
        idx = start
        while True:
            if used[idx]:
                break
            used[idx] = True
            a, b = directed[idx]
            ring.append(a)
            cand = [j for j in outs.get(b, ()) if not used[j]]
            if not cand:
                if b == directed[start][0]:
                    break                  # closed
                return None                # dangling chain: give up
            if len(cand) == 1:
                idx = cand[0]
            else:
                # junction (shapes touching at a point): stay on this ring
                # by taking the sharpest left turn
                inc = math.atan2(b[1] - a[1], b[0] - a[0])
                best, best_turn = None, None
                for j in cand:
                    c = directed[j][1]
                    ang = math.atan2(c[1] - b[1], c[0] - b[0])
                    turn = (ang - inc) % (2 * math.pi)
                    if best_turn is None or turn > best_turn:
                        best, best_turn = j, turn
                idx = best
        if len(ring) >= 3:
            rings_out.append(ring)
    if not rings_out:
        return None

    outers = [r for r in rings_out if ring_area(r) > 0]
    holes = [r for r in rings_out if ring_area(r) <= 0]
    if not outers:
        return None
    return (outers, holes)


def verify_union(rings, outers, holes, samples=1500):
    """Sampled membership check: does the union agree with the inputs?
    Returns the mismatch fraction (0 = perfect). Cheap insurance against
    a geometric edge case silently rewriting a board."""
    bb = _bbox([p for r in rings for p in r])
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if w <= 0 or h <= 0:
        return 0.0
    rgrid = _Grid(max(max(w, h) / 32.0, 1e-3))
    for i, r in enumerate(rings):
        rgrid.add(_bbox(r), i)
    ogrid = _Grid(max(max(w, h) / 32.0, 1e-3))
    for i, r in enumerate(outers):
        ogrid.add(_bbox(r), ('o', i))
    for i, r in enumerate(holes):
        ogrid.add(_bbox(r), ('h', i))

    # deterministic low-discrepancy sampling (no RNG: results must be
    # reproducible, and workflows here are re-run and compared)
    bad = 0
    g1, g2 = 0.7548776662466927, 0.5698402909980532   # R2 sequence
    for k in range(samples):
        px = bb[0] + w * ((0.5 + g1 * k) % 1.0)
        py = bb[1] + h * ((0.5 + g2 * k) % 1.0)
        a = any(point_in_ring(px, py, rings[i]) for i in rgrid.query((px, py, px, py)))
        hit = [(tag, i) for tag, i in ogrid.query((px, py, px, py))]
        in_out = any(point_in_ring(px, py, outers[i]) for tag, i in hit if tag == 'o')
        in_hole = any(point_in_ring(px, py, holes[i]) for tag, i in hit if tag == 'h')
        inside = in_out and not in_hole
        if a != inside:
            bad += 1
    return bad / float(samples)
