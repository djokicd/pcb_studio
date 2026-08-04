"""Shared geometry helpers: stackup z-positions, shape outlines, footprints.

All lengths in mm. Shapes live on conductor layers referenced by layer id.
Shape types:
  rect    {x, y, w, h}
  circle  {cx, cy, r}
  segment {cx, cy, r, a0, a1}          circular sector (pie), angles in deg CCW
  arc     {cx, cy, r0, r1, a0, a1}     annular arc (curved trace)
  poly    {pts: [[x,y], ...]}
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
            if t in ('rect', 'poly'):
                xs += [v for v in pxs if bx0 + eps < v < bx1 - eps]
                ys += [v for v in pys if by0 + eps < v < by1 - eps]
            else:
                xs.append(float(s['cx']))
                ys.append(float(s['cy']))
        elif t in ('rect', 'poly'):
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
