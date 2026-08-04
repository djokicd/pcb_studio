"""Mesh line generation in Python, shared by the /api/mesh preview and the
script generator, so the previewed mesh is exactly what gets simulated.

Strategy: fixed lines at every geometry edge, uniform fill inside each gap
(edge_res inside the board region, max_res outside), geometrically graded
lines through the air margins.
"""
import math

from geometry import stackup_z, mesh_lines_xy

C0 = 299792458.0


def _dedupe(vals, tol=1e-4):
    out = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(round(v, 6))
    return out


def _merge_close(vals, tol):
    """Cluster lines closer than tol and replace each cluster by its mean.
    Prevents near-coincident geometry edges (e.g. a component body edge
    75 um from a trace edge) from creating tiny cells that crush the
    FDTD timestep."""
    if tol <= 0:
        return _dedupe(vals)
    vals = sorted(vals)
    out = []
    cluster = [vals[0]]
    for v in vals[1:]:
        if v - cluster[-1] <= tol:
            cluster.append(v)
        else:
            out.append(round(sum(cluster) / len(cluster), 6))
            cluster = [v]
    out.append(round(sum(cluster) / len(cluster), 6))
    return out


def _fill_gap(a, b, res):
    """Uniform interior lines so no cell exceeds res."""
    gap = b - a
    n = max(1, int(math.ceil(gap / res - 1e-9)))
    return [a + gap * k / n for k in range(1, n)]


def _grade_out(start, res0, res_max, margin, direction, ratio=1.4):
    """Lines growing geometrically away from `start` covering `margin`."""
    sizes = []
    s = max(res0, 1e-3)
    total = 0.0
    while total < margin - 1e-9:
        s = min(s * ratio, res_max)
        sizes.append(s)
        total += s
    if not sizes:
        return []
    scale = margin / total
    lines, pos = [], start
    for s in sizes:
        pos += direction * s * scale
        lines.append(pos)
    return lines


def _smooth_axis(fixed, lo, hi, edge_res, max_res, margin, regions=(), merge=0.0):
    # region boundaries must be mesh lines themselves, otherwise a fine
    # region inside a large gap never splits it (the gap midpoint decides
    # the fill resolution and may lie outside the region)
    fixed = list(fixed)
    span_lo, span_hi = min(fixed), max(fixed)
    for rlo, rhi, _res in regions:
        if span_lo < rlo < span_hi:
            fixed.append(rlo)
        if span_lo < rhi < span_hi:
            fixed.append(rhi)
    lines = _merge_close(fixed, merge) if merge > 0 else _dedupe(fixed)
    out = [lines[0]]
    for a, b in zip(lines, lines[1:]):
        mid = 0.5 * (a + b)
        res = edge_res if lo - 1e-9 <= mid <= hi + 1e-9 else max_res
        for rlo, rhi, rres in regions:
            if rlo - 1e-9 <= mid <= rhi + 1e-9:
                res = min(res, rres)
        out += _fill_gap(a, b, res)
        out.append(b)
    if margin > 0:
        first_cell = out[1] - out[0] if len(out) > 1 else edge_res
        last_cell = out[-1] - out[-2] if len(out) > 1 else edge_res
        out = list(reversed(_grade_out(out[0], first_cell, max_res, margin, -1))) \
            + out + _grade_out(out[-1], last_cell, max_res, margin, +1)
    return _dedupe(out)


def resolutions(model):
    """(edge_res, max_res) in mm."""
    sim = model.get('sim') or {}
    fstop = float(sim['fStop']) * 1e9
    er_max = max([float(l.get('er', 1)) for l in model.get('stackup', [])
                  if l.get('type') == 'dielectric'] or [1.0])
    lam_min_mm = C0 / fstop / math.sqrt(er_max) * 1e3
    max_res = lam_min_mm / float(sim.get('meshDiv') or 20.0)
    edge_res = sim.get('edgeRes')
    edge_res = float(edge_res) if edge_res else max_res
    return edge_res, max_res


def build_mesh(model):
    """Returns {'x': [...], 'y': [...], 'z': [...], 'cells': int} in mm."""
    board = model['board']
    sim = model.get('sim') or {}
    W, H = float(board['width']), float(board['height'])
    margin = float(sim.get('airMargin', 20.0))
    edge_res, max_res = resolutions(model)

    merge = sim.get('meshMerge')
    merge = 0.1 if merge is None else max(0.0, float(merge))
    # fringing length scale: total dielectric height of the stackup
    _, _diel, _total = stackup_z(model.get('stackup') or [])
    xs, ys, xreg, yreg = mesh_lines_xy(model, edge_res, fringe=_total or None)
    x = _smooth_axis(xs, 0.0, W, edge_res, max_res, margin, xreg, merge)
    y = _smooth_axis(ys, 0.0, H, edge_res, max_res, margin, yreg, merge)

    # z: conductor sheets + dielectric interfaces, each dielectric subdivided
    cond_z, diel_z, total = stackup_z(model.get('stackup') or [])
    zf = set(cond_z.values()) | {0.0, total}
    z = []
    boundaries = _dedupe(list(zf | {z0 for z0, _ in diel_z.values()}
                              | {z1 for _, z1 in diel_z.values()}))
    for a, b in zip(boundaries, boundaries[1:]):
        res = min(edge_res, max((b - a) / 3.0, 1e-3))
        z.append(a)
        z += _fill_gap(a, b, res)
    z.append(boundaries[-1])
    # mirror the outermost dielectric's cell size into the air above/below:
    # the microstrip fringing field lives within ~one substrate height of the
    # outer conductors, and coarse first air cells bias Z0 low
    if diel_z:
        spans = sorted(diel_z.values())
        b_thk = spans[0][1] - spans[0][0]
        t_thk = spans[-1][1] - spans[-1][0]
        z += [total + t_thk * k / 3.0 for k in (1, 2, 3)]
        z += [-b_thk * k / 3.0 for k in (1, 2, 3)]
    z = _smooth_axis(z, 0.0, total, edge_res, max_res, margin)

    return {'x': x, 'y': y, 'z': z, 'cells': len(x) * len(y) * len(z),
            'edgeRes': edge_res, 'maxRes': max_res}
