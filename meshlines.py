"""Mesh line generation in Python, shared by the /api/mesh preview and the
script generator, so the previewed mesh is exactly what gets simulated.

Strategy: the geometry contributes two classes of lines. HARD lines pin
exact positions (board and axis-aligned copper edges, true corners, via
centres/extremes); near-coincident hard lines are merged (meshMerge) to
avoid accidental micro-cells. SOFT candidates are sampled along curved
and oblique edges (and carry the metal-edge refinement pairs); they are
thinned to the local resolution after the merge, so the staircase follows
the actual copper edge and deliberate fine structure survives. Inside
each remaining gap the fill is GRADED - cell sizes start from the fine
detail at both gap ends and grow geometrically (sim setting "meshRatio",
default 1.5) up to the local cap (edge/region resolution inside the
board, lambda/N outside), so fine zones relax smoothly into the bulk.
The z axis concentrates cells at conductor faces that carry geometry
(the strip side) and grades coarser toward plane-only faces (the bulk
ground side).
"""
import bisect
import math

from geometry import stackup_z, mesh_lines_xy, sim_shapes, comp_lift

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


def _fill_graded(a, b, d_left, d_right, cap, ratio):
    """Interior lines for the gap [a, b]: cell sizes start near d_left /
    d_right at the ends and grow geometrically (factor `ratio`) towards
    the middle, plateauing at `cap`. No cell exceeds cap and adjacent
    cells inside the gap never differ by more than `ratio`."""
    gap = b - a
    cap_hard = cap
    cap = max(min(cap, gap), 1e-4)
    sl = max(min(d_left, cap), 1e-4)
    sr = max(min(d_right, cap), 1e-4)
    # a single cell is acceptable only if it stays within one grading
    # step of BOTH neighbours (a wide gap beside a tiny cell must be
    # subdivided even when it is smaller than the local cap) AND it does
    # not seriously overrun the cap: refinement zones promise a
    # resolution, so a gap over ~1.3x the promise splits - while a gap
    # only marginally over stays single (sliver halves would be worse).
    if gap <= min(sl, sr) * ratio * 1.1 and gap <= cap_hard * 1.3:
        return []
    seq = []                            # (side, size) in build order
    total = 0.0
    while total < gap - 1e-12:
        if sl <= sr:                    # grow the currently smaller side
            sl = min(sl * ratio, cap)
            seq.append(('L', sl))
            total += sl
        else:
            sr = min(sr * ratio, cap)
            seq.append(('R', sr))
            total += sr
    # the last size overshoots the gap; scaling everything down can
    # produce cells far below the neighbouring detail (a hidden size
    # jump at the gap ends). If the overshoot is large, prefer dropping
    # the last size and stretching the rest - but only while the stretch
    # keeps the junction jump (ratio x stretch) within ~ratio^1.5;
    # otherwise scaling down is the lesser evil.
    if len(seq) > 1 and gap / total < 1.0 / math.sqrt(ratio):
        alt_total = total - seq[-1][1]
        alt = gap / alt_total
        if alt <= math.sqrt(ratio) * 1.001 and max(s for _, s in seq[:-1]) * alt <= cap * 1.2:
            seq.pop()
            total = alt_total
    scale = gap / total
    pts = []
    pos = a
    for side, s in seq:
        if side == 'L':
            pos += s * scale
            pts.append(pos)
    pos = b
    for side, s in seq:                 # first-added R is the cell at b
        if side == 'R':
            pos -= s * scale
            pts.append(pos)
    # the two fronts meet in the middle: drop the coincident meeting
    # point and anything landing on the gap ends
    out = []
    for p in sorted(pts):
        if p <= a + 1e-6 or p >= b - 1e-6:
            continue
        if not out or p - out[-1] > 1e-6:
            out.append(p)
    return out


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


def _thin_soft(soft, hard):
    """Thin soft candidates - (pos, res) or (pos, res, priority) - keeping
    a point only when it is at least ~0.7*res away from every hard line
    and every previously kept point. Higher-priority candidates (via
    tangent extremes, other exact feature positions) are placed first, so
    dense generic curve samples cannot crowd them out. What survives is
    an edge-following set of lines spaced at the local resolution - no
    denser than the graded fill they replace."""
    if not soft:
        return []
    kept = sorted(hard)

    def ok(p, d):
        i = bisect.bisect_left(kept, p)
        if i > 0 and p - kept[i - 1] < d:
            return False
        if i < len(kept) and kept[i] - p < d:
            return False
        return True

    tiers = {}
    for c in soft:
        p, res = c[0], c[1]
        prio = c[2] if len(c) > 2 else 0
        tiers.setdefault(prio, []).append((p, res))
    out = []
    for prio in sorted(tiers, reverse=True):
        for p, res in sorted(tiers[prio]):
            if ok(p, 0.7 * res):
                bisect.insort(kept, p)
                out.append(p)
    return out


def _smooth_axis(fixed, lo, hi, edge_res, max_res, margin, regions=(), merge=0.0,
                 ratio=1.5, soft=(), pinned=(), outside=None, uspans=()):
    fixed = list(fixed)
    span_lo, span_hi = min(fixed), max(fixed)
    # pinned positions must survive EXACTLY: zero-thickness structures
    # (component element sheets, their vertical terminals) only rasterize
    # on their precise mesh line, and coplanar slot edges set the gap
    # width. Pins are excluded from the clustering itself - a pin inside
    # a cluster would drag the mean and then vanish into it, taking a
    # real feature line (a port edge 100 um from a component terminal)
    # along. They rejoin afterwards; only non-pinned lines within the
    # merge tolerance of a pin are pulled onto it.
    pinset = {round(p, 6) for p in pinned} if pinned else set()
    if pinset:
        fixed = [v for v in fixed if round(v, 6) not in pinset]
    lines = _merge_close(fixed, merge) if merge > 0 else _dedupe(fixed)
    if pinset:
        tol = max(merge, 1e-4)
        lines = [v for v in lines
                 if all(abs(v - pv) > tol - 1e-12 for pv in pinset)]
        lines = _dedupe(lines + sorted(pinset))
    # region boundaries must be mesh lines themselves, otherwise a fine
    # region inside a large gap never splits it (the gap midpoint decides
    # the fill resolution and may lie outside the region). They join
    # AFTER the coincidence merge: an auxiliary boundary participating in
    # the clustering would drag nearby copper edges off their true
    # position (a refinement band ending 50 um from a ground edge must
    # not move that edge). A boundary already within the merge tolerance
    # of an existing line is dropped - that line bounds the region well
    # enough.
    bnd_tol = max(merge, 1e-4)
    for v in sorted({round(b, 6) for rlo, rhi, _res in regions
                     for b in (rlo, rhi) if span_lo < b < span_hi}):
        i = bisect.bisect_left(lines, v)
        near = min([abs(lines[j] - v) for j in (i - 1, i)
                    if 0 <= j < len(lines)] or [1e9])
        if near > bnd_tol:
            bisect.insort(lines, v)
    # deliberate fine structure (curve samples, edge-refinement pairs) is
    # inserted after the coincidence merge, so meshMerge cannot collapse
    # it; candidates duplicating a hard line are dropped instead
    if soft:
        lines = _dedupe(lines + _thin_soft(soft, lines))

    # "outside" settings apply to the board area NOT covered by a
    # user-defined density region: the user pins the density that matters
    # and lets everything else relax at its own (usually coarser, gentler)
    # cap and grading ratio
    out_res = (outside or {}).get('res')
    out_ratio = (outside or {}).get('ratio')

    def in_user_span(mid):
        return any(a - 1e-9 <= mid <= b + 1e-9 for a, b in uspans)

    def fill_pass(lines):
        # per-gap fill cap: edge resolution on the board, lambda/N
        # outside, refinement regions finer still
        caps, ratios = [], []
        for a, b in zip(lines, lines[1:]):
            mid = 0.5 * (a + b)
            on_board = lo - 1e-9 <= mid <= hi + 1e-9
            res = edge_res if on_board else max_res
            free = on_board and not in_user_span(mid)
            if free and out_res:
                res = out_res
            ratios.append(out_ratio if (free and out_ratio) else ratio)
            for rlo, rhi, rres in regions:
                if rlo - 1e-9 <= mid <= rhi + 1e-9:
                    res = min(res, rres)
            caps.append(res)
        # the geometric detail present at each line: the smaller of the
        # neighbouring gaps (each limited by its own cap). Graded fills
        # start from this size, so fine features relax smoothly into the
        # bulk.
        widths = [b - a for a, b in zip(lines, lines[1:])]
        detail = []
        for j in range(len(lines)):
            cand = []
            if j > 0:
                cand.append(min(widths[j - 1], caps[j - 1]))
            if j < len(widths):
                cand.append(min(widths[j], caps[j]))
            detail.append(min(cand))
        out = [lines[0]]
        for j, (a, b) in enumerate(zip(lines, lines[1:])):
            out += _fill_graded(a, b, detail[j], detail[j + 1], caps[j], ratios[j])
            out.append(b)
        return _dedupe(out)

    # iterate: a single pass computes details from the input gaps only,
    # so a gap it fills can still jump against the fill of its neighbour;
    # re-running on the output sees the actual cells and closes those
    # junction violations. Each pass's new lines can expose fresh
    # junctions, so loop until stable (geometric convergence, a few
    # passes in practice).
    out = lines
    for _ in range(8):
        new = fill_pass(out)
        if len(new) == len(out):
            break
        out = new
    if margin > 0:
        first_cell = out[1] - out[0] if len(out) > 1 else edge_res
        last_cell = out[-1] - out[-2] if len(out) > 1 else edge_res
        out = list(reversed(_grade_out(out[0], first_cell, max_res, margin, -1, ratio))) \
            + out + _grade_out(out[-1], last_cell, max_res, margin, +1, ratio)
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


def user_regions(model):
    """User-defined refinement intervals from the Meshing tab:
    [(lo, hi, res, axis), ...] with invalid or disabled entries dropped.
    Each becomes a (lo, hi, res) cap interval on its axis, and its
    boundaries become mesh lines (both handled by _smooth_axis)."""
    out = []
    for r in (model.get('mesh') or {}).get('regions') or []:
        if not isinstance(r, dict) or r.get('off'):
            continue
        try:
            lo, hi = float(r['from']), float(r['to'])
            res = float(r['res'])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (lo, hi, res)):
            continue
        if not (res > 0) or not (min(lo, hi) < max(lo, hi)):
            continue
        axis = 'y' if r.get('axis') == 'y' else 'x'
        out.append((min(lo, hi), max(lo, hi), res, axis))
    return out


def user_lines(model, width, height):
    """Exact mesh lines the user placed in the Meshing tab: single x/y
    lines plus both coordinates of each snapped point. Returns (xs, ys);
    positions off the board are dropped (they would stretch the domain).
    These become hard AND pinned lines, so a deliberate line survives the
    coincidence merge exactly - a nearby geometry line is pulled onto it
    rather than averaged with it."""
    mesh = model.get('mesh') or {}
    xs, ys = [], []

    def add(axis, v, limit):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        if not math.isfinite(v) or v < -1e-9 or v > limit + 1e-9:
            return
        (ys if axis == 'y' else xs).append(min(max(v, 0.0), limit))

    for ln in mesh.get('lines') or []:
        if not isinstance(ln, dict) or ln.get('off'):
            continue
        axis = 'y' if ln.get('axis') == 'y' else 'x'
        add(axis, ln.get('at'), height if axis == 'y' else width)
    for pt in mesh.get('points') or []:
        if not isinstance(pt, dict) or pt.get('off'):
            continue
        add('x', pt.get('x'), width)
        add('y', pt.get('y'), height)
    return xs, ys


def user_outside(model):
    """Meshing-tab settings for the board area outside every density
    region: {'res': mm, 'ratio': grading} with unset/invalid keys absent."""
    src = (model.get('mesh') or {}).get('outside') or {}
    out = {}
    try:
        res = float(src.get('res'))
        if math.isfinite(res) and res > 0:
            out['res'] = res
    except (TypeError, ValueError):
        pass
    try:
        ratio = float(src.get('ratio'))
        if math.isfinite(ratio):
            out['ratio'] = min(2.0, max(1.2, ratio))
    except (TypeError, ValueError):
        pass
    return out


def build_mesh(model):
    """Returns {'x': [...], 'y': [...], 'z': [...], 'cells': int} in mm."""
    board = model['board']
    sim = model.get('sim') or {}
    W, H = float(board['width']), float(board['height'])
    margin = float(sim.get('airMargin', 20.0))
    edge_res, max_res = resolutions(model)

    merge = sim.get('meshMerge')
    merge = 0.1 if merge is None else max(0.0, float(merge))
    ratio = sim.get('meshRatio')
    ratio = 1.5 if not ratio else min(2.0, max(1.2, float(ratio)))
    # fringing length scale: total dielectric height of the stackup
    _, _diel, _total = stackup_z(model.get('stackup') or [])
    xs, ys, xsoft, ysoft, xreg, yreg, xpin, ypin, slot_res = mesh_lines_xy(
        model, edge_res, fringe=_total or None)
    xreg, yreg = list(xreg), list(yreg)
    xspan, yspan = [], []
    for lo_u, hi_u, res_u, axis_u in user_regions(model):
        if axis_u == 'y':
            yreg.append((lo_u, hi_u, res_u))
            yspan.append((lo_u, hi_u))
        else:
            xreg.append((lo_u, hi_u, res_u))
            xspan.append((lo_u, hi_u))
    ux, uy = user_lines(model, W, H)
    xs, ys = list(xs) + ux, list(ys) + uy
    xpin, ypin = list(xpin) + ux, list(ypin) + uy
    outside = user_outside(model)
    x = _smooth_axis(xs, 0.0, W, edge_res, max_res, margin, xreg, merge, ratio,
                     xsoft, xpin, outside, xspan)
    y = _smooth_axis(ys, 0.0, H, edge_res, max_res, margin, yreg, merge, ratio,
                     ysoft, ypin, outside, yspan)

    # z: conductor sheets + dielectric interfaces. Field detail lives at
    # the conductor faces that carry geometry (strips, ports, pads); plane
    # -only faces (the bulk ground) get by with coarser first cells, so
    # each dielectric is graded fine->coarse from the signal side.
    cond_z, diel_z, total = stackup_z(model.get('stackup') or [])
    signal_ids = ({s.get('layer') for s in sim_shapes(model)}
                  | {c.get('layer') for c in model.get('components') or []}
                  | {p.get('layer') for p in model.get('ports') or []}
                  | {p.get('layerTo') for p in model.get('ports') or []}
                  | {v.get('from') for v in model.get('vias') or []}
                  | {v.get('to') for v in model.get('vias') or []})
    signal_z = {round(z0, 6) for lid, z0 in cond_z.items() if lid in signal_ids}

    def z_detail(zb, thickness):
        """First-cell size at a dielectric face: fine where geometry sits,
        moderate at plain interfaces/planes - never above edge_res. On a
        board with coplanar slots the signal-face cells additionally match
        the slot resolution: the gap field dives into the substrate at the
        edge-singularity scale, and a coarse first z cell there reads the
        slot capacitance high (Z0 low, eeff high). Measured on the GCPW
        benchmark: halving the substrate z cells moved Z0 by +1.5 ohm."""
        d = min(edge_res, max(thickness / (6.0 if round(zb, 6) in signal_z
                                           else 3.0), 1e-3))
        if slot_res and round(zb, 6) in signal_z:
            d = min(d, max(slot_res, 1e-3))
        return d

    zf = set(cond_z.values()) | {0.0, total}
    boundaries = _dedupe(list(zf | {z0 for z0, _ in diel_z.values()}
                              | {z1 for _, z1 in diel_z.values()}))
    z = [boundaries[0]]
    for a, b in zip(boundaries, boundaries[1:]):
        t = b - a
        z += _fill_graded(a, b, z_detail(a, t), z_detail(b, t),
                          min(edge_res, max(t / 2.0, 1e-3)), ratio)
        z.append(b)
    # lumped components float above the copper plane; their element plane
    # needs an exact z line (and the air gap under the body gets a cell)
    lifts = []
    for c in model.get('components') or []:
        if c.get('layer') in cond_z:
            zl, _ = comp_lift(cond_z, total, c['layer'])
            lifts.append(zl)
    z += lifts
    # air above/below: continue from the outer face's first-cell size,
    # growing geometrically (the microstrip fringing field lives within
    # ~one substrate height of the outer conductors; coarse first air
    # cells bias Z0 low). Ladder steps landing next to a component lift
    # plane are skipped - the plane is an exact line and a rung a few um
    # away would leave a sliver cell that crushes the timestep.
    if diel_z:
        spans = sorted(diel_z.values())
        b_thk = spans[0][1] - spans[0][0]
        t_thk = spans[-1][1] - spans[-1][0]
        for face, thk, dirn in ((total, t_thk, +1), (0.0, b_thk, -1)):
            s = z_detail(face, thk)
            pos = face
            for k in range(3):
                pos += dirn * s * ratio ** k
                if all(abs(pos - zl) > 0.4 * s for zl in lifts):
                    z.append(pos)
    z = _smooth_axis(z, 0.0, total, edge_res, max_res, margin, (), 0.0, ratio)

    mesh = {'x': x, 'y': y, 'z': z, 'cells': len(x) * len(y) * len(z),
            'edgeRes': edge_res, 'maxRes': max_res}
    # quality stats for the preview status bar
    worst = 1.0
    min_cell = float('inf')
    for ax in ('x', 'y', 'z'):
        cells = [b - a for a, b in zip(mesh[ax], mesh[ax][1:])]
        if cells:
            min_cell = min(min_cell, min(cells))
            for c1, c2 in zip(cells, cells[1:]):
                worst = max(worst, c1 / c2, c2 / c1)
    mesh['minCell'] = round(min_cell, 4) if min_cell < float('inf') else None
    mesh['worstRatio'] = round(worst, 2)
    return mesh
