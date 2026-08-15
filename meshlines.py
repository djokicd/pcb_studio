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

from geometry import (stackup_z, mesh_lines_xy, sim_shapes, comp_lift,
                      comp_element_box, shape_outline, _axis_edges,
                      _mesh_lines, MIN_SOFT_RES)

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


# ------------------------------------------------------------ manual mode
# Density profiles for a manual range. Each is a rule for how the cell
# sizes vary across the range; `ratio` is the growth per cell step.
BIASES = ('uniform', 'start', 'end', 'both', 'center')


def profile_lines(a, b, cells, ratio=1.0, bias='uniform'):
    """Mesh lines across [a, b] for one manual range: exactly `cells`
    cells, so `cells + 1` lines including both ends.

    The profile is one formula - a weight per cell, normalized to the
    span - so every bias grades at the same `ratio` per step and none of
    them can overshoot or leave the range:

      uniform  every cell the same size
      start    cells grow from a  (fine at the low end)
      end      cells grow towards a  (fine at the high end)
      both     fine at both ends, coarsest in the middle
      center   fine in the middle, coarsest at both ends
    """
    a, b = float(a), float(b)
    n = max(1, int(cells))
    span = b - a
    if span <= 0:
        return []
    r = float(ratio or 1.0)
    if bias not in BIASES:
        bias = 'uniform'
    if bias == 'uniform' or abs(r - 1.0) < 1e-9 or n == 1:
        return [a + span * k / n for k in range(n + 1)]
    r = min(max(r, 1.0 / 8.0), 8.0)
    mid = (n - 1) / 2.0
    w = []
    for k in range(n):
        if bias == 'start':
            d = k
        elif bias == 'end':
            d = n - 1 - k
        elif bias == 'both':
            d = min(k, n - 1 - k)        # smallest cells at both ends
        else:                            # center: smallest in the middle
            d = abs(k - mid)
        w.append(r ** d)
    total = sum(w)
    out, pos = [a], a
    for size in w[:-1]:
        pos += span * size / total
        out.append(pos)
    out.append(b)
    return out


def _toward(s, cap, ratio):
    """One grading step from cell size `s` towards `cap` - growing when
    the neighbour is finer than the cap, shrinking when it is coarser."""
    return min(s * ratio, cap) if s < cap else max(s / ratio, cap)


def _relax_gap(a, b, d_left, d_right, cap, ratio):
    """Interior lines for a gap between manual ranges (or out to the
    board edge): cell sizes move geometrically from the neighbouring
    cells towards `cap` and plateau there.

    Unlike the automatic mesher's fill this handles neighbours COARSER
    than the cap - a range with big cells beside a fine minimum density
    has to step down gradually. Clamping to the cap instead put a 17x
    size jump right at the range boundary."""
    gap = b - a
    if gap <= 1e-12:
        return []
    r = max(1.0001, float(ratio))
    sl = max(float(d_left), 1e-4)
    sr = max(float(d_right), 1e-4)
    # both neighbours finer than the cap is the ordinary case the graded
    # fill already solves - and solves better, because it knows how to
    # give up a cell rather than crush a whole sequence into a short gap
    if sl <= cap and sr <= cap:
        return _fill_graded(a, b, sl, sr, cap, r)
    # One cell is acceptable when it is within a grading step of both
    # neighbours and does not badly overrun the density asked for. Inside
    # a ramp stepping DOWN from a coarse range the cells are legitimately
    # bigger than the cap, so the second test follows the neighbours
    # there - otherwise a re-run would chop the ramp up and reopen the
    # junction it was built to close.
    limit = max(cap, min(sl, sr) * r) * 1.3
    if gap <= min(sl, sr) * r * 1.1 and gap <= limit:
        return []
    # Each side runs its own transition to the cap first; whatever span
    # is left in the middle is plateau. Interleaving the two fronts by
    # size instead lets them meet mid-gap at very different sizes - the
    # jump just moves from the range boundary into the middle of the gap.
    def ramp(s):
        out, cur = [], s
        while abs(cur - cap) > 1e-9 and sum(out) < gap and len(out) < 10000:
            cur = _toward(cur, cap, r)
            out.append(cur)
        return out

    lefts, rights = ramp(sl), ramp(sr)
    rest = gap - sum(lefts) - sum(rights)
    plateau = max(0, int(round(rest / cap))) if cap > 0 else 0
    seq = lefts + [cap] * plateau + rights[::-1]
    if not seq:
        return []
    # scaling is uniform, so the grading INSIDE the gap stays exactly at
    # `ratio`; only a gap too short for both ramps ends up compressed,
    # and that shows up honestly in the worst-step statistic
    scale = gap / sum(seq)
    out, pos = [], a
    for s in seq[:-1]:
        pos += s * scale
        if a + 1e-6 < pos < b - 1e-6 and (not out or pos - out[-1] > 1e-6):
            out.append(pos)
    return out


def _range_cells(r, span):
    """How many cells a manual range holds. Either given directly, or
    derived from a target spacing - `by: 'spacing'` keeps the DENSITY
    fixed, so resizing the band adds and removes cells instead of
    stretching them."""
    if r.get('by') == 'spacing':
        try:
            step = float(r.get('spacing') or 0)
        except (TypeError, ValueError):
            step = 0.0
        if step > 0 and span > 0:
            return max(1, min(20000, int(round(span / step))))
    try:
        cells = int(r.get('cells') or 0)
    except (TypeError, ValueError):
        cells = 0
    if cells <= 0:
        # no explicit count: fall back to the cell-size field, so a range
        # drawn before the profile was set still meshes
        try:
            res = float(r.get('res') or 0)
        except (TypeError, ValueError):
            res = 0.0
        cells = max(1, int(math.ceil(span / res))) if res > 0 and span > 0 else 4
    return min(cells, 20000)


def manual_ranges(model, axis, limit):
    """Enabled manual ranges on one axis, clipped to [0, limit] and
    normalized to (lo, hi, cells, ratio, bias). Invalid entries drop."""
    out = []
    for r in (model.get('mesh') or {}).get('regions') or []:
        if not isinstance(r, dict) or r.get('off'):
            continue
        if ('y' if r.get('axis') == 'y' else 'x') != axis:
            continue
        try:
            lo, hi = float(r['from']), float(r['to'])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(lo) and math.isfinite(hi)):
            continue
        lo, hi = max(0.0, min(lo, hi)), min(limit, max(lo, hi))
        if hi - lo <= 1e-9:
            continue
        # optional dead band at each edge: the range is placed against a
        # copper edge, but its first mesh line sits `offset` inside it.
        # The band is one cell wide with nothing in it - the outer span
        # still counts as covered, so the relaxation outside cannot drop
        # a line into it.
        out_lo, out_hi = lo, hi
        try:
            off = float(r.get('offset') or 0.0)
        except (TypeError, ValueError):
            off = 0.0
        if off > 0 and math.isfinite(off):
            off = min(off, (hi - lo) / 2.0 - 1e-6)
            if off > 0:
                lo, hi = lo + off, hi - off
        if hi - lo <= 1e-9:
            continue
        cells = _range_cells(r, hi - lo)
        try:
            ratio = float(r.get('ratio') or 1.0)
        except (TypeError, ValueError):
            ratio = 1.0
        bias = r.get('bias') if r.get('bias') in BIASES else 'uniform'
        out.append((lo, hi, cells, ratio, bias, out_lo, out_hi))
    return sorted(out)


# A lumped component asks for more than its own exact lines: the gap it
# bridges carries the field between the two pads, and the copper it lands
# on has to stay where it was drawn or that gap can close on the grid.
COMP_CROSS_CELLS = 2      # across the element's width
COMP_NEAR_CELLS = 3       # across each copper interval around the part
# The finest cell component meshing may create. Boards carry copper
# detail far below anything that matters electrically - a 1 um sliver
# between two imported polygons is not a feature - and resolving it
# costs the whole board: the cell crushes the timestep and needs ~18
# graded cells either side of it to relax back to the bulk.
COMP_MIN_CELL = 0.05


def comp_mesh_cfg(model):
    """(level, cells) for component meshing. Levels, cheapest first:

      off   nothing beyond the exact positions the part cannot exist
            without (element sheet, terminals, ESR junction)
      gap   + the copper inside the element's own span, resolved. Keeps
            a narrow pad gap from closing on the grid, which is the
            failure that silently merges the copper either side of a part
      near  + every copper edge within a gap width of the part, each
            interval resolved. Thorough and expensive: each line runs the
            width of the board, so on a dense board this multiplies
    """
    m = model.get('mesh') or {}
    level = m.get('compMesh')
    if level not in ('off', 'gap', 'near'):
        level = 'off' if m.get('autoComp') is False else 'gap'
    try:
        cells = int(m.get('compCells') or COMP_NEAR_CELLS)
    except (TypeError, ValueError):
        cells = COMP_NEAR_CELLS
    try:
        floor = float(m.get('compMin') or COMP_MIN_CELL)
    except (TypeError, ValueError):
        floor = COMP_MIN_CELL
    return level, max(1, min(cells, 12)), max(1e-4, floor)


def manual_component_lines(model, axis, limit):
    """Lines that make a lumped component simulate properly in manual
    mode, over and above the exact positions it cannot exist without.

    All of a part's lines come from ONE anchor set per axis - the
    element ends, the ESR junction, and the copper edges that actually
    face the part - with the intervals between anchors subdivided.
    Two independent line families (a fixed gap split laid over raw
    copper edges) land arbitrarily close to each other: measured on the
    GCPW termination board they produced 23 um hairline cells inside
    the element gap, which crush the timestep and drag a skirt of
    graded lines across the whole board."""
    level, cells, floor = comp_mesh_cfg(model)
    if level == 'off':
        return []
    out = []
    shapes = model.get('shapes') or []
    edges_cache = None
    for c in model.get('components') or []:
        x0, y0, x1, y1, ny, connected = comp_element_box(c, shapes)
        if not connected:
            continue
        lo, hi = (y0, y1) if axis == 'y' else (x0, x1)
        c_lo, c_hi = (x0, x1) if axis == 'y' else (y0, y1)
        if hi - lo <= 1e-9:
            continue
        # the series axis carries the gap; the other one the width. The
        # ESR split joins two element sheets at the gap centre, so the
        # centre is an anchor, not a derived line.
        series = (ny == 1) == (axis == 'y')
        anchors = [lo, hi] + ([round((lo + hi) / 2.0, 6)] if series else [])
        n_base = cells if series else COMP_CROSS_CELLS
        # On a rectilinear grid every line runs the whole width of the
        # board, so lines that buy nothing cost everywhere. Across the
        # part (the axis with no gap in it) the element box edges plus a
        # centre cell are all the sheet needs, so the copper sweep is
        # confined to the axis the current actually crosses unless the
        # level asks for the whole neighbourhood.
        if series or level == 'near':
            # Copper edges by the part, so its pads keep their shape - a
            # pad edge with no line near it moves to the nearest one and
            # a narrow pad gap can close, merging the copper either side.
            # Pinning them is not enough on its own: an edge pinned with
            # a single cell beside it models that gap worse than not
            # pinning it at all (measured: 2 ohm the WRONG way on a
            # terminated GCPW line), so each interval is resolved too.
            if edges_cache is None:
                ex, ey = [], []
                for s in sim_shapes(model):
                    _axis_edges(shape_outline(s), s.get('layer'), ex, ey,
                                min_len=0.05)
                edges_cache = (ex, ey)
            # the sweep reaches one element-span past the ends: the first
            # copper gap beyond each end is the part's connection to the
            # rest of the circuit (pad to trace, pad to pour), and it
            # closes on a coarse grid exactly like the element gap does.
            # 'near' widens this to the cross axis too.
            margin = (hi - lo) if series or level == 'near' else 0.0
            # Only edges that actually FACE the part count: their extent
            # on the other axis has to overlap the element box. Without
            # that check a pour edge eight millimetres away - same layer,
            # same x - injects lines straight into the element gap.
            near = [pos for pos, e0, e1, _side, lay in
                    (edges_cache[1] if axis == 'y' else edges_cache[0])
                    if lay == c.get('layer')
                    and lo - margin <= pos <= hi + margin
                    and e0 <= c_hi + margin + 1e-9
                    and e1 >= c_lo - margin - 1e-9]
            # copper detail below the floor is not a feature worth a
            # cell, and an edge a hair from a structural anchor IS that
            # anchor - fuse both before they turn into hairline cells
            for p in _cluster_pins(near, floor):
                if min(abs(p - q) for q in anchors) > floor:
                    anchors.append(p)
        anchors = sorted(anchors)
        out += anchors
        for a, b in zip(anchors, anchors[1:]):
            # never subdivide past the floor, however many cells asked
            n = min(n_base, max(1, int((b - a) / floor)))
            for k in range(1, n):
                out.append(round(a + (b - a) * k / n, 6))
    return [v for v in out if math.isfinite(v) and -1e-9 <= v <= limit + 1e-9]


def manual_pins(model, axis, limit):
    """Lines individual objects need in manual mode, as (soft, hard).

    SOFT - vias and round pads that were told to pin 1 / 3 / 5 lines.
    The geometry derives nothing on its own here, but a per-object mesh
    setting is an instruction rather than a derivation. `mesh.lines`
    unset means the object asks for nothing. These merge against each
    other and against the lines already present.

    HARD - the exact positions structures cannot exist without. A
    component's element sheet and its vertical terminals are zero
    -thickness: they only rasterize where a mesh line already is, so a
    terminal 20 um off its line silently disconnects the part and the
    simulation quietly models nothing. Ports are the same story - their
    box has to keep its true area or the measured impedance is wrong.
    These are placed exactly and never merged away.
    """
    out = []
    hard = []
    key = 'y' if axis == 'y' else 'x'
    shapes = model.get('shapes') or []
    for c in model.get('components') or []:
        x0, y0, x1, y1, ny, _conn = comp_element_box(c, shapes)
        lo, hi = (y0, y1) if axis == 'y' else (x0, x1)
        hard += [lo, hi]
        # the ESR split joins two element sheets at the gap centre, a
        # zero-width junction that needs its own exact line
        if (ny == 1) == (axis == 'y'):
            hard.append(round((lo + hi) / 2.0, 6))
    for p in model.get('ports') or []:
        try:
            lo = float(p[key])
            hi = lo + float(p['h' if axis == 'y' else 'w'])
        except (KeyError, TypeError, ValueError):
            continue
        hard += [lo, hi]

    def add(c, half_list, n):
        out.append(c)
        if n >= 3 and half_list:
            out.extend((c - half_list[0], c + half_list[0]))
        if n >= 5 and len(half_list) > 1:
            out.extend((c - half_list[1], c + half_list[1]))

    for v in model.get('vias') or []:
        n = _mesh_lines(v)
        if not n:
            continue
        try:
            c = float(v['y' if axis == 'y' else 'x'])
            halves = [float(v['drill']) / 2.0, float(v['pad']) / 2.0]
        except (KeyError, TypeError, ValueError):
            continue
        add(c, halves, n)
    for s in sim_shapes(model):
        n = _mesh_lines(s)
        if not n or s.get('type') != 'circle':
            continue
        try:
            c = float(s['cy' if axis == 'y' else 'cx'])
            halves = [float(s['r'])]
        except (KeyError, TypeError, ValueError):
            continue
        add(c, halves, n)
    ok = lambda v: math.isfinite(v) and -1e-9 <= v <= limit + 1e-9
    # Structural pins (element ends, ESR junctions, port edges) are exact
    # or the structure vanishes. Component RESOLUTION lines are not: they
    # exist to give the copper around a part cells, and one that lands a
    # hair from a structural pin - or from another part's line, two
    # sweeps covering the same copper - would live beside it as a
    # micron-wide cell, since pins never displace pins. Cluster them
    # against each other and absorb them into any structural pin within
    # the component floor.
    struct = _dedupe([h for h in hard if ok(h)])
    floor = comp_mesh_cfg(model)[2]
    comp = _cluster_pins(
        [c for c in manual_component_lines(model, axis, limit) if ok(c)],
        floor)
    for v in comp:
        i = bisect.bisect_left(struct, v)
        near = min([abs(struct[j] - v) for j in (i - 1, i)
                    if 0 <= j < len(struct)] or [1e9])
        if near > floor:
            bisect.insort(struct, v)
    return [c for c in out if ok(c)], struct


def manual_windows(model, axis, limit, fringe, min_res):
    """Automatic refinement windows around ports and lumped components
    in manual mode, as (lo, hi, res) spans for the graded fill.

    Manual meshing is for COPPER - the user draws ranges over the
    geometry they know. Ports and lumped components are not copper:
    a port's probes read the fields in the cells around its box, and
    coarse cells there bias the measured impedance the same way in
    every mesh mode; a part's pads live or die by the cells around the
    element. So their neighbourhoods are refined automatically - the
    same fringe-length window and resolution the automatic mesher uses
    - wherever the user has not put a range of their own (a range keeps
    the exact cells it was given)."""
    out = []
    key = 'y' if axis == 'y' else 'x'
    spans = []
    for p in model.get('ports') or []:
        try:
            lo = float(p[key])
            hi = lo + float(p['h' if axis == 'y' else 'w'])
            size = max(float(p['w']), float(p['h']))
        except (KeyError, TypeError, ValueError):
            continue
        spans.append((lo, hi, size))
    shapes = model.get('shapes') or []
    if comp_mesh_cfg(model)[0] != 'off':
        for c in model.get('components') or []:
            x0, y0, x1, y1, _ny, connected = comp_element_box(c, shapes)
            if not connected:
                continue
            lo, hi = (y0, y1) if axis == 'y' else (x0, x1)
            spans.append((lo, hi, max(x1 - x0, y1 - y0)))
    for lo, hi, size in spans:
        fr = fringe or max(size, 1.0)
        res = min(min_res, max(fr / 3.0, MIN_SOFT_RES))
        a, b = max(0.0, lo - fr), min(float(limit), hi + fr)
        if b - a > 1e-9 and hi > -1e-9 and lo < limit + 1e-9:
            out.append((a, b, res))
    return out


def _cluster_pins(vals, tol):
    """Group object-pinned lines that land within `tol` of each other and
    replace each group by its mean.

    Grouping is measured from the FIRST member, not the running last one:
    a chain-merge would swallow a whole via - drill edge, centre, other
    edge are each a tolerance apart - and collapse the barrel to a single
    line. Measuring from the group start keeps the structure and only
    fuses lines that are genuinely redundant, which is what two vias a
    tenth of a millimetre apart produce."""
    if tol <= 0:
        return _dedupe(vals)
    out, group = [], []
    for v in sorted(vals):
        if group and v - group[0] > tol:
            out.append(round(sum(group) / len(group), 6))
            group = []
        group.append(v)
    if group:
        out.append(round(sum(group) / len(group), 6))
    return _dedupe(out)


def manual_axis(ranges, limit, min_res, ratio, margin, pins=(), merge=0.0,
                windows=()):
    """One axis of a manual mesh: the ranges' own lines, the board edges,
    a graded fill relaxing to `min_res` everywhere else, and the air
    margin. Nothing is derived from the geometry - with no ranges at all
    this is just the board at the minimum density. `windows` are
    (lo, hi, res) spans - port and component neighbourhoods - where the
    fill relaxes to `res` instead of `min_res`; a user range overlapping
    a window still owns its cells."""
    lines = [0.0, float(limit)]
    covered = []
    for lo, hi, cells, r, bias, out_lo, out_hi in ranges:
        lines += profile_lines(lo, hi, cells, r, bias)
        if out_lo < lo:                 # edges of the line-free offset band
            lines.append(out_lo)
        if out_hi > hi:
            lines.append(out_hi)
        if covered and out_lo <= covered[-1][1] + 1e-9:
            covered[-1][1] = max(covered[-1][1], out_hi)
        else:
            covered.append([out_lo, out_hi])
    def inside(v):
        return any(lo - 1e-9 <= v <= hi + 1e-9 for lo, hi in covered)

    # window boundaries join as ordinary lines, so every fill gap lies
    # wholly in or out of a window from the FIRST pass - a gap straddling
    # the boundary would be filled coarse first and re-chopped fine on a
    # later pass, leaving a mismatched junction at the window's edge.
    # A boundary is only a hint of where the fill target changes: one
    # that would land against an existing line or a pin is redundant
    # there, and inserting it anyway leaves a hairline cell behind.
    soft, hard = pins if pins else ((), ())
    for w_lo, w_hi, w_res in windows:
        tol = max(merge, 0.25 * w_res)
        for v in (w_lo, w_hi):
            if not (1e-9 < v < limit - 1e-9) or inside(v):
                continue
            if min((abs(v - q) for q in lines), default=1e9) <= tol:
                continue
            if min((abs(v - float(h)) for h in hard), default=1e9) <= tol:
                continue
            lines.append(v)
    lines = _dedupe(lines, 1e-6)

    # Structures that only exist where a line is (component sheets and
    # terminals, port boxes) are placed EXACTLY - no merging, no
    # thinning, no snapping to something nearby. A pin may displace an
    # ordinary line that would otherwise sit a hair away from it, or the
    # range line it stands in for (so the range keeps the cell count it
    # was given); it may never displace ANOTHER PIN - a component
    # terminal and a port edge a tenth of a millimetre apart both have
    # to be there, and letting them replace each other silently dropped
    # one of the two structures.
    hard_set = sorted({round(float(h), 6) for h in hard})
    if hard_set:
        def pin_dist(v):
            i = bisect.bisect_left(hard_set, v)
            return min([abs(hard_set[j] - v) for j in (i - 1, i)
                        if 0 <= j < len(hard_set)] or [1e9])

        keep = []
        for k, v in enumerate(lines):
            if abs(v) < 1e-9 or abs(v - limit) < 1e-9:
                keep.append(v)             # the board edges anchor the domain
                continue
            d = pin_dist(v)
            if d <= 1e-9:
                continue                   # the pin itself, re-added below
            local = min(v - lines[k - 1] if k else 1e9,
                        lines[k + 1] - v if k + 1 < len(lines) else 1e9)
            # Outside a range the merge tolerance decides. Inside one the
            # yardstick is the range's OWN cell - a range asked for 50 um
            # cells is not "near-coincident" at 50 um, and judging it by
            # the 0.1 mm merge would quietly delete two of its lines.
            if (d <= merge if not inside(v) else d <= local * 0.6):
                continue                   # displaced by the pin
            keep.append(v)
        lines = keep
        for pv in hard_set:
            bisect.insort(lines, pv)
    # Everything else joins last and is counted against what is already
    # there: two vias a tenth of a millimetre apart fuse into one line,
    # and a pin landing on a line that exists is simply that line. Left
    # unchecked each near-miss became a hairline cell that then needed a
    # dozen lines of grading either side of it.
    if soft:
        for pv in _cluster_pins([float(p) for p in soft], merge):
            i = bisect.bisect_left(lines, pv)
            near = min([abs(lines[j] - pv) for j in (i - 1, i)
                        if 0 <= j < len(lines)] or [1e9])
            if near > merge:
                bisect.insort(lines, pv)

    # the fill target for an interval: min_res in the bulk, a window's
    # own resolution inside a port/component neighbourhood (the finest
    # one wins where windows overlap)
    def cap_at(a, b):
        m = 0.5 * (a + b)
        cap = min_res
        for w_lo, w_hi, w_res in windows:
            if w_lo - 1e-9 <= m <= w_hi + 1e-9:
                cap = min(cap, w_res)
        return cap

    # fill the gaps between ranges (and out to the board edges): cells
    # start from the neighbouring range's OWN cell - not clamped to the
    # minimum density - and grade towards it from there, so the junction
    # at a range boundary is smooth whichever side is coarser
    def fill_pass(lines, first):
        widths = [b - a for a, b in zip(lines, lines[1:])]
        out = [lines[0]]
        for j, (a, b) in enumerate(zip(lines, lines[1:])):
            if inside(0.5 * (a + b)):
                out.append(b)           # a range owns this interval
                continue
        # the neighbouring cell to grade away from. A gap a range owns
        # keeps its own size, however coarse; any other neighbour ends up
        # at most its fill target wide, so clamp it there - but a NARROW
        # one (two via pins 0.15 mm apart) stays narrow and must be
        # graded from, or the fine cells sit straight against the bulk.
            # On the first pass a wide neighbour is still unfilled and
            # will end up at most its own cap - and right at our junction
            # it grades down to about OUR target, so estimating it any
            # coarser would make this gap ramp against a cell that will
            # not exist and dump the misfit as hairline cells at a pin.
            # Once filled, every width is a real cell - clamping then
            # would throw away the ramp stepping down from a coarse range.
            cap = cap_at(a, b)

            def detail(k):
                if not (0 <= k < len(widths)):
                    return min_res
                w = widths[k]
                if first and not inside(0.5 * (lines[k] + lines[k + 1])):
                    return min(w, cap_at(lines[k], lines[k + 1]),
                               cap * ratio)
                return w
            dl = detail(j - 1) if j > 0 else min_res
            dr = detail(j + 1) if j + 1 < len(widths) else min_res
            out += _relax_gap(a, b, dl, dr, cap, ratio)
            out.append(b)
        return _dedupe(out)

    # one pass sizes every gap from the gaps it started with, so a gap
    # that ends up split leaves its neighbour mismatched. Re-running on
    # the result sees the real cells and closes those junctions.
    out = lines
    for k in range(8):
        new = fill_pass(out, k == 0)
        if len(new) == len(out):
            break
        out = new
    if margin > 0:
        first = out[1] - out[0] if len(out) > 1 else min_res
        last = out[-1] - out[-2] if len(out) > 1 else min_res
        out = list(reversed(_grade_out(out[0], first, min_res, margin, -1, ratio))) \
            + out + _grade_out(out[-1], last, min_res, margin, +1, ratio)
    return _dedupe(out)


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


def mesh_check(model, mesh):
    """Does this mesh actually represent the board that was drawn?

    FDTD rasterizes copper onto the grid: an edge with no mesh line near
    it moves to the nearest one, so the simulated conductor is not the
    one on screen. A gap crossed by a single cell carries no field
    detail at all. Neither shows up in the cell count or the grading
    ratio, so they are reported here:

      offEdges / worstOff  copper edges that move, and by how far (mm)
      gapCells / gapWidth  the coplanar gap resolved by the fewest cells
      minFeature           the narrowest copper the grid has to carry
    """
    from geometry import _axis_edges, _find_slots, shape_outline

    out = {'offEdges': 0, 'worstOff': 0.0, 'movedBy': None,
           'gapCells': None, 'gapWidth': None, 'minFeature': None}
    ex, ey = [], []
    for s in sim_shapes(model):
        _axis_edges(shape_outline(s), s.get('layer'), ex, ey)
    if not (ex or ey):
        return out

    for edges, axis in ((ex, 'x'), (ey, 'y')):
        lines = mesh[axis]
        if len(lines) < 2:
            continue
        for pos, _lo, _hi, _side, _lay in edges:
            i = bisect.bisect_left(lines, pos)
            near = [lines[j] for j in (i - 1, i) if 0 <= j < len(lines)]
            if not near:
                continue
            best = min(near, key=lambda v: abs(v - pos))
            d = abs(best - pos)
            # a cell edge that lands within a whisker of the copper is
            # exact for our purposes; anything else moves the conductor
            if d > 1e-6:
                out['offEdges'] += 1
                out['worstOff'] = max(out['worstOff'], d)

    # narrowest coplanar gap and how many cells cross it
    for edges, axis in ((ex, 'x'), (ey, 'y')):
        lines = mesh[axis]
        for lo, hi, _ov in _find_slots(edges, 0.0, 5.0):
            n = len([v for v in lines if lo - 1e-9 <= v <= hi + 1e-9]) - 1
            n = max(n, 0)
            if out['gapCells'] is None or n < out['gapCells']:
                out['gapCells'] = n
                out['gapWidth'] = round(hi - lo, 4)

    # the narrowest copper the mesh has to carry at all
    for s in sim_shapes(model):
        pts = shape_outline(s)
        if not pts:
            continue
        w = max(p[0] for p in pts) - min(p[0] for p in pts)
        h = max(p[1] for p in pts) - min(p[1] for p in pts)
        d = min(w, h)
        if d > 1e-6 and (out['minFeature'] is None or d < out['minFeature']):
            out['minFeature'] = round(d, 4)
    out['worstOff'] = round(out['worstOff'], 4)
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

    # Manual mode: the geometry contributes NOTHING to x/y. The mesh is
    # the ranges the user placed (each with its own line count and
    # density profile) plus a graded relaxation to the minimum density
    # everywhere else. z still follows the stackup - conductor faces are
    # not optional, a sheet only rasterizes on its own mesh line.
    manual = (model.get('mesh') or {}).get('mode') == 'manual'
    if manual:
        out_cfg = user_outside(model)
        min_res = out_cfg.get('res') or max_res
        m_ratio = out_cfg.get('ratio') or ratio
        x = manual_axis(manual_ranges(model, 'x', W), W, min_res, m_ratio,
                        margin, manual_pins(model, 'x', W), merge,
                        manual_windows(model, 'x', W, _total, min_res))
        y = manual_axis(manual_ranges(model, 'y', H), H, min_res, m_ratio,
                        margin, manual_pins(model, 'y', H), merge,
                        manual_windows(model, 'y', H, _total, min_res))
        edge_res = max_res = min_res
        slot_res = None
    else:
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
    mesh['check'] = mesh_check(model, mesh)
    return mesh
