"""Shared geometry helpers: stackup z-positions, shape outlines, footprints.

All lengths in mm. Shapes live on conductor layers referenced by layer id.
Shape types:
  rect    {x, y, w, h}
  circle  {cx, cy, r}
  segment {cx, cy, r, a0, a1}          circular sector (pie), angles in deg CCW
  arc     {cx, cy, r0, r1, a0, a1}     annular arc (curved trace)
  poly    {pts: [[x,y], ...]}
  trace   {pts: [[x,y], ...], width, radius}   transmission line: centerline
          polyline stroked to `width`, corners rounded with `radius`
Vias:     {x, y, drill, pad, from, to}  drill/pad are diameters, from/to layer ids
Components: {ctype: R|L|C, value, package: 0402|0603|0805|custom, len, wid,
             x, y (center), rot: 0|90, layer}
"""
import math

# package -> (body length, body width) mm, current flows along the length
PACKAGES = {'0402': (1.0, 0.5), '0603': (1.6, 0.8), '0805': (2.0, 1.25)}

# the comments layer: shapes drawn here are reference geometry only -
# visible in the editor, invisible to mesh, simulation and fabrication
REF_LAYER = '__ref'

# lumped components are modelled slightly ABOVE the copper plane, like a
# real chip part: the element sheet floats at the lift height and vertical
# PEC terminals connect its ends down to the pads. Copper crossing under
# the body (a trace below a resistor) then couples only capacitively
# instead of galvanically shorting the coplanar element sheet.
COMP_LIFT = 0.2


def comp_lift(cond_z, total, layer):
    """(element-plane z, outward sign) for a component on `layer`: the
    body is lifted away from the board (up on the top side, down on the
    bottom side)."""
    z = cond_z[layer]
    sign = 1.0 if z >= total / 2.0 else -1.0
    return z + sign * COMP_LIFT, sign


def sim_shapes(model):
    """The model's shapes that take part in the simulation (i.e. not on
    the reference/comments layer)."""
    return [s for s in model.get('shapes') or [] if s.get('layer') != REF_LAYER]


def stackup_z(stackup):
    """stackup is listed top->bottom. Returns (cond_z, diel_z, total_height)
    with z=0 at the bottom. Conductors are zero-thickness sheets at their
    interface; dielectric thicknesses accumulate."""
    cond_z, diel_z = {}, {}
    z = 0.0
    for layer in reversed(stackup):
        if layer['type'] == 'dielectric':
            t = float(layer['thickness'])
            diel_z[layer['id']] = (z, z + t)
            z += t
        else:
            cond_z[layer['id']] = z
    return cond_z, diel_z, z


def arc_points(cx, cy, r, a0, a1, n=None):
    """Points along a CCW arc from a0 to a1 (degrees)."""
    a0r, a1r = math.radians(a0), math.radians(a1)
    while a1r <= a0r:
        a1r += 2 * math.pi
    span = a1r - a0r
    if n is None:
        n = max(8, int(round(span / (2 * math.pi) * 64)))
    return [(cx + r * math.cos(a0r + span * k / n),
             cy + r * math.sin(a0r + span * k / n)) for k in range(n + 1)]


def circle_points(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * k / n),
             cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def trace_centerline(pts, radius):
    """Centerline of a trace: the drawn polyline with interior corners
    rounded by `radius` (quadratic-bezier fillet, sampled ~15 deg/step).
    Mirrors traceCenterline() in static/js/editor.js - keep in sync."""
    pts = [(float(p[0]), float(p[1])) for p in pts]
    r = float(radius or 0)
    if r <= 0 or len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = out[-1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        l1 = math.hypot(bx - ax, by - ay)
        l2 = math.hypot(cx - bx, cy - by)
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        u1 = ((bx - ax) / l1, (by - ay) / l1)
        u2 = ((cx - bx) / l2, (cy - by) / l2)
        cross = u1[0] * u2[1] - u1[1] * u2[0]
        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        turn = math.acos(dot)
        if abs(cross) < 1e-9 or turn < 1e-3:
            out.append((bx, by))
            continue
        # tangent length of a circular fillet, clamped to the half-segments
        t = min(r * math.tan(turn / 2), l1 * 0.5, l2 * 0.5)
        p1 = (bx - u1[0] * t, by - u1[1] * t)
        p2 = (bx + u2[0] * t, by + u2[1] * t)
        n = max(2, int(math.ceil(math.degrees(turn) / 15.0)))
        for k in range(n + 1):
            s_ = k / n
            omu = 1 - s_
            out.append((omu * omu * p1[0] + 2 * omu * s_ * bx + s_ * s_ * p2[0],
                        omu * omu * p1[1] + 2 * omu * s_ * by + s_ * s_ * p2[1]))
    out.append(pts[-1])
    return out


def trace_length(pts, radius):
    cl = trace_centerline(pts, radius)
    return sum(math.hypot(cl[i + 1][0] - cl[i][0], cl[i + 1][1] - cl[i][1])
               for i in range(len(cl) - 1))


def stroke_outline(cl, width):
    """Closed polygon outlining the polyline `cl` stroked to `width`:
    mitered joins along both sides plus semicircular end caps. Assumes
    shallow join angles (sharp corners are pre-filleted by
    trace_centerline). Mirrors strokeOutline() in editor.js."""
    w2 = float(width) / 2.0
    cl = [(float(p[0]), float(p[1])) for p in cl]
    # drop zero-length segments
    pts = [cl[0]]
    for p in cl[1:]:
        if math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 1e-9:
            pts.append(p)
    if len(pts) < 2:
        raise ValueError('trace needs at least two distinct points')
    dirs = []
    for i in range(len(pts) - 1):
        dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        l = math.hypot(dx, dy)
        dirs.append((dx / l, dy / l))
    normals = [(-d[1], d[0]) for d in dirs]
    left, right = [], []
    for i, p in enumerate(pts):
        if i == 0:
            n = normals[0]
        elif i == len(pts) - 1:
            n = normals[-1]
        else:
            mx, my = normals[i - 1][0] + normals[i][0], normals[i - 1][1] + normals[i][1]
            ml = math.hypot(mx, my)
            if ml < 1e-9:               # 180 deg reversal - fall back
                n = normals[i]
            else:
                mx, my = mx / ml, my / ml
                # miter scale, limited to 4x half-width for sharp angles
                scale = 1.0 / max(mx * normals[i][0] + my * normals[i][1], 0.25)
                n = (mx * scale, my * scale)
        left.append((p[0] + n[0] * w2, p[1] + n[1] * w2))
        right.append((p[0] - n[0] * w2, p[1] - n[1] * w2))

    def cap(center, n_from, n_to, direction):
        # semicircle from center+n_from*w2 to center+n_to*w2, bulging along
        # `direction` (unit vector pointing away from the line)
        a0 = math.atan2(n_from[1], n_from[0])
        out = []
        for k in range(1, 8):
            a = a0 + direction * math.pi * k / 8
            out.append((center[0] + w2 * math.cos(a), center[1] + w2 * math.sin(a)))
        return out

    # end cap: rotate from left normal to right normal going around the tip
    end_cap = cap(pts[-1], normals[-1], (-normals[-1][0], -normals[-1][1]), -1)
    start_cap = cap(pts[0], (-normals[0][0], -normals[0][1]), normals[0], -1)
    return left + end_cap + right[::-1] + start_cap


def dp_polyline(pts, tol):
    """Douglas-Peucker decimation of an open polyline: drops vertices that
    deviate from the kept chords by at most `tol`. Endpoints are kept."""
    pts = list(pts)
    if tol <= 0 or len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 - i0 < 2:
            continue
        x0, y0 = pts[i0]
        dx, dy = pts[i1][0] - x0, pts[i1][1] - y0
        ln = math.hypot(dx, dy)
        best, bd = -1, tol
        for k in range(i0 + 1, i1):
            px, py = pts[k][0] - x0, pts[k][1] - y0
            d = math.hypot(px, py) if ln < 1e-12 else abs(px * dy - py * dx) / ln
            if d > bd:
                best, bd = k, d
        if best >= 0:
            keep[best] = True
            stack += [(i0, best), (best, i1)]
    return [p for p, k in zip(pts, keep) if k]


def dp_ring(pts, tol):
    """Douglas-Peucker for a closed outline (no repeated end point). The
    ring is anchored at two mutually distant vertices and each half is
    decimated as an open polyline."""
    pts = list(pts)
    n = len(pts)
    if tol <= 0 or n < 5:
        return pts

    def far(i):
        return max(range(n), key=lambda k: (pts[k][0] - pts[i][0]) ** 2
                   + (pts[k][1] - pts[i][1]) ** 2)

    a = far(0)
    b = far(a)
    if a > b:
        a, b = b, a
    out = dp_polyline(pts[a:b + 1], tol)[:-1] \
        + dp_polyline(pts[b:] + pts[:a + 1], tol)[:-1]
    return out if len(out) >= 3 else pts


def resample_polyline(pts, max_seg, closed=False):
    """Subdivide segments longer than `max_seg` into equal parts, so a
    sparse chain of chords gets evenly spaced vertices again."""
    pts = list(pts)
    if max_seg <= 0 or len(pts) < 2:
        return pts
    src = pts + ([pts[0]] if closed else [])
    out = [src[0]]
    for (x0, y0), (x1, y1) in zip(src, src[1:]):
        n = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / max_seg - 1e-9)))
        for k in range(1, n + 1):
            out.append((x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n))
    return out[:-1] if closed else out


def shape_outline(s):
    """Closed polygon outline (list of (x, y), not repeated at the end)."""
    t = s.get('type', 'rect')
    if t == 'rect':
        x, y, w, h = float(s['x']), float(s['y']), float(s['w']), float(s['h'])
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if t == 'circle':
        return circle_points(float(s['cx']), float(s['cy']), float(s['r']), 64)
    if t == 'segment':
        pts = arc_points(float(s['cx']), float(s['cy']), float(s['r']),
                         float(s['a0']), float(s['a1']))
        return [(float(s['cx']), float(s['cy']))] + pts
    if t == 'arc':
        outer = arc_points(float(s['cx']), float(s['cy']), float(s['r1']),
                           float(s['a0']), float(s['a1']))
        inner = arc_points(float(s['cx']), float(s['cy']), float(s['r0']),
                           float(s['a0']), float(s['a1']))
        return outer + inner[::-1]
    if t == 'poly':
        return [(float(p[0]), float(p[1])) for p in s['pts']]
    if t == 'trace':
        cl = trace_centerline(s['pts'], s.get('radius') or 0)
        return stroke_outline(cl, float(s['width']))
    raise ValueError(f'unknown shape type: {t}')


def comp_body(c):
    """Component body box (x0, y0, x1, y1) and current direction ny (0=x, 1=y)."""
    if c.get('package') == 'custom':
        L, W = float(c.get('len', 1.6)), float(c.get('wid', 0.8))
    else:
        L, W = PACKAGES.get(c.get('package', '0603'), PACKAGES['0603'])
    x, y = float(c['x']), float(c['y'])
    if int(c.get('rot', 0)) % 180 == 0:
        return x - L / 2, y - W / 2, x + L / 2, y + W / 2, 0
    return x - W / 2, y - L / 2, x + W / 2, y + L / 2, 1


def comp_element_box(c, shapes):
    """Lumped-element box for a component: the body box shrunk (along the
    current direction) to the copper-free gap it bridges.

    openEMS distributes the R/L/C uniformly over the box; any part of the
    box overlapped by copper is shorted by the metal, which would scale
    the effective value by gap/body-length. Shrinking the box to the gap
    keeps the nominal value, with the caps landing exactly on the copper
    edges. Returns (x0, y0, x1, y1, ny, connected) where `connected` is
    False when no copper touches one (or both) ends of the box."""
    x0, y0, x1, y1, ny = comp_body(c)
    if ny == 0:
        d0, d1, t0, t1 = x0, x1, y0, y1
    else:
        d0, d1, t0, t1 = y0, y1, x0, x1
    lo_cov, hi_cov = d0, d1   # copper coverage growing inwards from each end
    spans = []
    for s in shapes:
        if s.get('layer') != c.get('layer'):
            continue
        pts = shape_outline(s)
        sx = [p[0] for p in pts]
        sy = [p[1] for p in pts]
        if ny == 0:
            sd0, sd1, st0, st1 = min(sx), max(sx), min(sy), max(sy)
        else:
            sd0, sd1, st0, st1 = min(sy), max(sy), min(sx), max(sx)
        if st1 <= t0 + 1e-9 or st0 >= t1 - 1e-9:
            continue   # no transverse overlap
        spans.append((sd0, sd1))
    for _ in range(4):   # chains of abutting copper extend coverage iteratively
        for sd0, sd1 in spans:
            if sd0 <= lo_cov + 1e-9 and sd1 > lo_cov:
                lo_cov = min(sd1, d1)
            if sd1 >= hi_cov - 1e-9 and sd0 < hi_cov:
                hi_cov = max(sd0, d0)
    connected = lo_cov > d0 + 1e-9 and hi_cov < d1 - 1e-9
    if connected and lo_cov < hi_cov - 1e-6:
        d0, d1 = lo_cov, hi_cov
    if ny == 0:
        return d0, y0, d1, y1, ny, connected
    return x0, d0, x1, d1, ny, connected


def _obj_mesh(obj):
    """(local res, thirds) - thirds is tri-state: True/False/None(auto)."""
    m = obj.get('mesh') or {}
    res = m.get('res')
    try:
        res = float(res) if res else None
    except (TypeError, ValueError):
        res = None
    thirds = m.get('thirds')
    return res, (None if thirds is None else bool(thirds))


# narrowest dimension below which a shape counts as a transmission-line
# feature and gets the metal-edge refinement by default
AUTO_THIRDS_MAX_DIM = 3.0

# vertices turning more than this are geometric corners that pin exact
# mesh lines on both axes; gentler turns are samples of a smooth curve
# (all tessellations used here stay at or below 30 deg per step)
CORNER_TURN_DEG = 35.0

# floor for curve-sampling resolution, so hairline features cannot crush
# the FDTD timestep with micron-spaced lines
MIN_SOFT_RES = 0.1


def outline_edge_lines(pts, res, hx, hy, sx, sy, corners=True):
    """Turn a closed outline into mesh-line candidates. Axis-aligned edges
    pin exact (hard) lines in hx/hy. Oblique and curved edges are sampled
    every ~res of length into sx/sy as (pos, res) soft candidates - the
    mesher thins them to the local resolution, so the resulting staircase
    follows the actual copper edge instead of its bounding box. With
    corners=True, vertices turning more than CORNER_TURN_DEG pin exact
    lines on both axes; imported stroke soup passes corners=False so its
    thousands of cap/miter joints stay soft and thin into smooth bands."""
    clean = []
    for p in pts:
        if not clean or math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-9:
            clean.append(p)
    if len(clean) > 1 and math.hypot(clean[0][0] - clean[-1][0],
                                     clean[0][1] - clean[-1][1]) < 1e-9:
        clean.pop()
    n = len(clean)
    if n < 2:
        return
    step = max(res, 1e-3)
    for i in range(n):
        x0, y0 = clean[i]
        x1, y1 = clean[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        if abs(dx) < 1e-6:
            hx.append(x0)
        elif abs(dy) < 1e-6:
            hy.append(y0)
        else:
            m = max(1, int(math.ceil(ln / step)))
            for k in range(m + 1):
                t = k / m
                sx.append((x0 + dx * t, res))
                sy.append((y0 + dy * t, res))
    if not corners:
        return
    cos_lim = math.cos(math.radians(CORNER_TURN_DEG))
    for i in range(n):
        xa, ya = clean[i - 1]
        xb, yb = clean[i]
        xc, yc = clean[(i + 1) % n]
        l1 = math.hypot(xb - xa, yb - ya)
        l2 = math.hypot(xc - xb, yc - yb)
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        dot = ((xb - xa) * (xc - xb) + (yb - ya) * (yc - yb)) / (l1 * l2)
        if dot < cos_lim:
            hx.append(xb)
            hy.append(yb)


def _coalesce(regs):
    """Merge overlapping (lo, hi, res) intervals, keeping the finest res."""
    out = []
    for lo, hi, res in sorted(regs):
        if out and lo <= out[-1][1] + 1e-9:
            out[-1][1] = max(out[-1][1], hi)
            out[-1][2] = min(out[-1][2], res)
        else:
            out.append([lo, hi, res])
    return [tuple(r) for r in out]


def mesh_lines_xy(model, edge_res, fringe=None):
    """Mesh-line candidates implied by the geometry.

    Returns (xs, ys, xsoft, ysoft, xregions, yregions):
      xs/ys       exact lines: board edges, axis-aligned copper edges, true
                  corners, circle/via centres and tangent extremes, ports,
                  components
      xsoft/ysoft (pos, res) candidates sampled along curved and oblique
                  edges plus the metal-edge refinement pairs; the mesher
                  thins them to the local resolution and drops duplicates
                  of exact lines (after the coincidence merge, so meshMerge
                  cannot collapse deliberate fine structure)
      regions     (lo, hi, res) intervals where gap filling must be at
                  least as fine as res
    """
    board = model['board']
    xs = [0.0, float(board['width'])]
    ys = [0.0, float(board['height'])]
    xsoft, ysoft = [], []
    xreg, yreg = [], []
    # cross-width fine zones of narrow shapes are collected apart and
    # coalesced: hundreds of overlapping stroke segments become a few
    # merged zones, each contributing a single boundary-line pair
    nreg_x, nreg_y = [], []

    def region(res, x0, x1, y0, y1):
        if res:
            xreg.append((x0, x1, res))
            yreg.append((y0, y1, res))

    for s in sim_shapes(model):
        pts = shape_outline(s)
        pxs = [p[0] for p in pts]
        pys = [p[1] for p in pts]
        bx0, bx1, by0, by1 = min(pxs), max(pxs), min(pys), max(pys)
        w, h = bx1 - bx0, by1 - by0
        res, thirds = _obj_mesh(s)
        t = s.get('type', 'rect')
        if thirds is None:
            # auto: transmission-line features (narrow rects, straight
            # two-point traces) get the edge refinement by default. The
            # rule replaces the bbox-extreme edges with a 1/3-2/3 pair,
            # which is only meaningful when the bbox edge IS the copper
            # edge - curved/multi-segment traces take the per-segment
            # cross-zone path instead. Pads, planes and imported geometry
            # stay coarse unless explicitly enabled.
            straight = t != 'trace' or len(s.get('pts') or []) == 2
            thirds = (t in ('rect', 'trace') and straight
                      and not s.get('meshBbox')
                      and min(w, h) <= AUTO_THIRDS_MAX_DIM)
        region(res, bx0, bx1, by0, by1)
        # the feature dimension that must stay resolved: the conductor
        # cross-section, not the bounding box (a long diagonal trace has a
        # huge bbox but a small width)
        if t == 'trace':
            dim = float(s['width'])
        elif t == 'circle':
            dim = 2 * float(s['r'])
        elif t == 'arc':
            dim = float(s['r1']) - float(s['r0'])
        else:
            dim = min(w, h)
        rs = res or min(edge_res, max(dim / 3.0, MIN_SOFT_RES))
        hx, hy, sx, sy = [], [], [], []
        # corner pinning only for hand-drawn geometry: imported stroke
        # soup would pin a line at every cap/miter joint
        outline_edge_lines(pts, rs, hx, hy, sx, sy,
                           corners=not s.get('meshBbox'))
        if t == 'circle':
            hx += [bx0, bx1, float(s['cx'])]
            hy += [by0, by1, float(s['cy'])]
        if thirds:
            # replace the bbox-extreme edges with the 1/3-2/3 metal-edge
            # pair (line inside the metal at edge+res/3, outside at
            # edge-2res/3); interior lines - slots, bends, vertices - are
            # kept. The rule only converges with a FINE local resolution:
            # coarse edge cells make a thin PEC strip electrically wider
            # (several ohms of Z0 error), so default to an eighth of the
            # smallest feature dimension.
            feat = min(max(w, 1e-3), max(h, 1e-3))
            rt = min(res, feat) if res else min(edge_res, max(feat / 8.0, MIN_SOFT_RES))
            eps = 1e-6
            hx = [v for v in hx if bx0 + eps < v < bx1 - eps]
            hy = [v for v in hy if by0 + eps < v < by1 - eps]
            sx = [c for c in sx if bx0 + 0.9 * rt < c[0] < bx1 - 0.9 * rt]
            sy = [c for c in sy if by0 + 0.9 * rt < c[0] < by1 - 0.9 * rt]
            # pair tolerance 2rt/3: when a hard line already sits on the
            # edge (an abutting port), the rt/3 pair line yields to it
            # instead of creating a sliver cell beside it
            for e, di in ((bx0, +1), (bx1, -1)):
                sx += [(e + di * rt / 3.0, rt * 2 / 3.0), (e - di * 2.0 * rt / 3.0, rt * 2 / 3.0)]
            for e, di in ((by0, +1), (by1, -1)):
                sy += [(e + di * rt / 3.0, rt * 2 / 3.0), (e - di * 2.0 * rt / 3.0, rt * 2 / 3.0)]
            # resolve the cross-section of narrow strips: fine cells across
            # the thin dimension(s) and a medium band one fringe-length
            # (~substrate height) beyond the edges, where the microstrip
            # fringing field concentrates; long dimensions stay global
            fr = fringe or 4 * rt
            if w <= 8 * rt:
                xreg.append((bx0 - 2 * rt, bx1 + 2 * rt, rt))
                xreg.append((bx0 - fr, bx1 + fr, max(rt, fr / 3.0)))
            if h <= 8 * rt:
                yreg.append((by0 - 2 * rt, by1 + 2 * rt, rt))
                yreg.append((by0 - fr, by1 + fr, max(rt, fr / 3.0)))
        elif t == 'trace':
            # a curved or multi-segment transmission line: refine ACROSS
            # the conductor along its whole run without refining along
            # it. Per centerline segment: a straight axis-aligned run
            # spans only the width in the cross axis; bend samples are
            # compact in both axes and get both. The zones coalesce into
            # smooth bands that follow the line. The band reaches half a
            # width past the copper edges, so a coplanar-waveguide slot
            # beside the trace is resolved with the same fine cells
            # instead of one graded jump.
            w2 = float(s['width']) / 2.0
            half = w2 + min(w2, 1.0)
            cl = trace_centerline(s['pts'], s.get('radius') or 0)
            for (ax0, ay0), (ax1, ay1) in zip(cl, cl[1:]):
                sx0, sx1 = min(ax0, ax1) - half, max(ax0, ax1) + half
                sy0, sy1 = min(ay0, ay1) - half, max(ay0, ay1) + half
                if sx1 - sx0 <= AUTO_THIRDS_MAX_DIM:
                    nreg_x.append((sx0, sx1, rs))
                if sy1 - sy0 <= AUTO_THIRDS_MAX_DIM:
                    nreg_y.append((sy0, sy1, rs))
        elif dim <= AUTO_THIRDS_MAX_DIM:
            # narrow feature without edge refinement (imported strokes,
            # small pads, drawn circles): edge lines alone would leave a
            # single cell across the conductor, so add a cross-width fine
            # zone (~3 cells). Per-axis: a straight run refines across its
            # width only; bends and round features refine both.
            if w <= AUTO_THIRDS_MAX_DIM:
                nreg_x.append((bx0, bx1, rs))
            if h <= AUTO_THIRDS_MAX_DIM:
                nreg_y.append((by0, by1, rs))
        xs += hx
        ys += hy
        xsoft += sx
        ysoft += sy

    for v in model.get('vias') or []:
        x, y = float(v['x']), float(v['y'])
        rd, rp = float(v['drill']) / 2, float(v['pad']) / 2
        res, _ = _obj_mesh(v)
        # per-via economy setting mesh.lines: how many mesh lines the via
        # may pin per axis. None/0 = auto (full round staircase),
        # 5 = centre + drill and pad tangent extremes, 3 = centre + drill
        # extremes, 1 = centre line only. A fence of 100+ stitching vias
        # meshed in full detail dominates the cell budget of a board, and
        # for stitching the barrel position matters far more than its
        # roundness.
        nlines = (v.get('mesh') or {}).get('lines')
        try:
            nlines = int(nlines) if nlines else None
        except (TypeError, ValueError):
            nlines = None
        # a via is a cylinder: pin the centre (hard) and put the tangent
        # extremes plus samples of the drill/pad circles in the soft set,
        # so the staircase is round (~3 cells across the drill) instead of
        # a blocky cross. Soft, not hard: meshMerge must not average the
        # deliberate pad/drill ring away (it is often < 0.1 mm), and the
        # lines of near-coincident via columns thin against each other
        # instead of stacking up.
        xs.append(x)
        ys.append(y)
        # resolve the PAD with ~3 cells; the drill still pins its tangent
        # extremes. Drill-based resolution would put micro-cells around
        # every stitching via - a fence of 100+ small vias is common and
        # the barrels carry current fine with pad-scale cells (a per-via
        # mesh.res override remains available for critical signal vias).
        rv = res or min(edge_res, max(2 * rp / 3.0, MIN_SOFT_RES))
        tol = rv
        if nlines != 1:
            # tangent extremes at high priority (exact feature positions)
            xsoft += [(x - rd, tol, 1), (x + rd, tol, 1)]
            ysoft += [(y - rd, tol, 1), (y + rd, tol, 1)]
            if nlines != 3:
                xsoft += [(x - rp, tol, 1), (x + rp, tol, 1)]
                ysoft += [(y - rp, tol, 1), (y + rp, tol, 1)]
        if nlines is None:
            # full detail: generic circle samples fill in between the
            # extremes where room remains
            for r in {rd, rp}:
                # multiple of 4: vertices land exactly on the tangent
                # extremes, so no near-vertical edge straddles them
                n = max(16, 4 * int(math.ceil(math.pi * r / (2 * max(rv, 1e-3)))))
                outline_edge_lines(circle_points(x, y, r, n), tol, xs, ys, xsoft, ysoft)
        region(res, x - rp, x + rp, y - rp, y + rp)
    shapes = model.get('shapes') or []
    # component element boxes and their vertical terminals are zero-
    # thickness structures: they only rasterize when the mesh has lines
    # at their EXACT positions. These coordinates are returned as pinned
    # lines that the coincidence merge must not move - a terminal wall
    # 20 um off its mesh line silently disconnects the component.
    xpin, ypin = [], []
    for c in model.get('components') or []:
        x0, y0, x1, y1, _ny, _conn = comp_element_box(c, shapes)
        xs += [x0, x1, (x0 + x1) / 2]
        ys += [y0, y1, (y0 + y1) / 2]
        xpin += [x0, x1]
        ypin += [y0, y1]
        # the ESR split joins two element sheets at the gap centre - a
        # zero-width junction that needs its exact line as well
        if _ny == 0:
            xpin.append(round((x0 + x1) / 2.0, 6))
        else:
            ypin.append(round((y0 + y1) / 2.0, 6))
        res, _ = _obj_mesh(c)
        region(res, x0, x1, y0, y1)
    for p in model.get('ports') or []:
        x0, y0 = float(p['x']), float(p['y'])
        x1, y1 = x0 + float(p['w']), y0 + float(p['h'])
        xs += [x0, x1]
        ys += [y0, y1]
        res, _ = _obj_mesh(p)
        region(res, x0, x1, y0, y1)
        if not res:
            # the port's voltage/current probes need locally resolved fields:
            # coarse neighbouring cells systematically bias the measured
            # impedance. Refine one fringe-length around the port.
            fr = fringe or max(x1 - x0, y1 - y0, 1.0)
            rp = min(edge_res, max(fr / 3.0, MIN_SOFT_RES))
            xreg.append((x0 - fr, x1 + fr, rp))
            yreg.append((y0 - fr, y1 + fr, rp))

    xreg += _coalesce(nreg_x)
    yreg += _coalesce(nreg_y)
    return xs, ys, xsoft, ysoft, xreg, yreg, xpin, ypin
