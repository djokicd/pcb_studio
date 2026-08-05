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
    m = obj.get('mesh') or {}
    res = m.get('res')
    try:
        res = float(res) if res else None
    except (TypeError, ValueError):
        res = None
    return res, bool(m.get('thirds'))


def mesh_lines_xy(model, edge_res, fringe=None):
    """Fixed mesh lines and per-object refinement implied by the geometry.

    Returns (xs, ys, xregions, yregions); regions are (lo, hi, res) intervals
    where gap filling must be at least as fine as `res` (from per-object
    mesh.res overrides). Shapes with mesh.thirds get their straight bbox
    edges replaced by the metal-edge 1/3–2/3 line pair.
    """
    board = model['board']
    xs = [0.0, float(board['width'])]
    ys = [0.0, float(board['height'])]
    xreg, yreg = [], []

    def region(res, x0, x1, y0, y1):
        if res:
            xreg.append((x0, x1, res))
            yreg.append((y0, y1, res))

    def thirds_pair(edge, di, res):
        # metal-edge refinement: replace the edge line with lines at
        # edge + di*res/3 (inside the metal) and edge - di*2res/3 (outside)
        return [edge + di * res / 3.0, edge - di * 2.0 * res / 3.0]

    for s in model.get('shapes') or []:
        pts = shape_outline(s)
        pxs = [p[0] for p in pts]
        pys = [p[1] for p in pts]
        bx0, bx1, by0, by1 = min(pxs), max(pxs), min(pys), max(pys)
        res, thirds = _obj_mesh(s)
        region(res, bx0, bx1, by0, by1)
        if s.get('meshBbox'):
            # imported geometry (e.g. Gerber): bbox lines only, otherwise
            # thousands of stroke vertices would explode the mesh
            xs += [bx0, bx1]
            ys += [by0, by1]
            continue
        t = s.get('type', 'rect')
        if thirds:
            # replace the metal bbox edges with the 1/3-2/3 pair; interior
            # points (poly vertices, centre lines) are kept. The rule only
            # converges with a FINE local resolution: coarse edge cells make
            # a thin PEC strip electrically wider (several ohms of Z0 error),
            # so default to a quarter of the smallest feature dimension.
            feat = min(max(bx1 - bx0, 1e-3), max(by1 - by0, 1e-3))
            rt = min(res, feat) if res else min(edge_res, feat / 8.0)
            xs += thirds_pair(bx0, +1, rt) + thirds_pair(bx1, -1, rt)
            ys += thirds_pair(by0, +1, rt) + thirds_pair(by1, -1, rt)
            # resolve the cross-section of narrow strips: fine cells across
            # the thin dimension(s) and a medium band one fringe-length
            # (~substrate height) beyond the edges, where the microstrip
            # fringing field concentrates; long dimensions stay global
            fr = fringe or 4 * rt
            if bx1 - bx0 <= 8 * rt:
                xreg.append((bx0 - 2 * rt, bx1 + 2 * rt, rt))
                xreg.append((bx0 - fr, bx1 + fr, max(rt, fr / 3.0)))
            if by1 - by0 <= 8 * rt:
                yreg.append((by0 - 2 * rt, by1 + 2 * rt, rt))
                yreg.append((by0 - fr, by1 + fr, max(rt, fr / 3.0)))
            eps = 1e-6
            if t in ('rect', 'poly', 'trace'):
                xs += [v for v in pxs if bx0 + eps < v < bx1 - eps]
                ys += [v for v in pys if by0 + eps < v < by1 - eps]
            else:
                xs.append(float(s['cx']))
                ys.append(float(s['cy']))
        elif t in ('rect', 'poly', 'trace'):
            xs += pxs
            ys += pys
        else:  # curved shapes: bounding box + centre lines
            xs += [bx0, bx1, float(s['cx'])]
            ys += [by0, by1, float(s['cy'])]

    for v in model.get('vias') or []:
        x, y = float(v['x']), float(v['y'])
        rd, rp = float(v['drill']) / 2, float(v['pad']) / 2
        xs += [x - rp, x - rd, x, x + rd, x + rp]
        ys += [y - rp, y - rd, y, y + rd, y + rp]
        res, _ = _obj_mesh(v)
        region(res, x - rp, x + rp, y - rp, y + rp)
    shapes = model.get('shapes') or []
    for c in model.get('components') or []:
        x0, y0, x1, y1, _ny, _conn = comp_element_box(c, shapes)
        xs += [x0, x1, (x0 + x1) / 2]
        ys += [y0, y1, (y0 + y1) / 2]
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
            rp = min(edge_res, fr / 3.0)
            xreg.append((x0 - fr, x1 + fr, rp))
            yreg.append((y0 - fr, y1 + fr, rp))

    return xs, ys, xreg, yreg
