"""Model-level geometry simplification.

Imported fabrication data arrives as "stroke soup": every Gerber draw -
and every ~5 degree sample of every arc - is a separate rounded-end
outline polygon, and filled regions carry the full arc tessellation.
That is faithful but hostile to a rectilinear mesh and to the editor.

This module cleans a shape list up:
  * chains of stroke (stadium) polygons are recognised, validated by
    regenerating the stadium from the reconstructed centerline segment,
    and merged into native `trace` shapes (centerline + width) - the
    mesher then follows one clean pair of copper edges instead of
    hundreds of overlapping outlines;
  * centerlines and polygon outlines are decimated with Douglas-Peucker
    to a caller-chosen tolerance, so densely sampled curves become sparse
    chords that stay within `tol` of the original copper edge;
  * optionally, curved lines are resampled to an even vertex spacing
    (`maxSeg`), which bounds the chord length of every curve.

All lengths in mm. Shapes not addressed by the options pass through
untouched and order is preserved.
"""
import math

from geometry import REF_LAYER, dp_polyline, dp_ring, resample_polyline, shape_outline
from gerber import _stadium
from polybool import _Grid, point_in_ring, ring_area, union_rings, verify_union

# a union whose sampled membership disagrees with the inputs by more than
# this is rejected: the originals are kept rather than risk rewriting copper
_UNION_MISMATCH = 2e-3
# shape kinds an overlap merge may consume. Traces are excluded by
# default: they carry their width parametrically and the mesher refines
# ACROSS them along their whole run, which a merged polygon would lose.
_MERGEABLE = ('rect', 'circle', 'poly', 'segment', 'arc')

# geometry rounded to 0.1 um on import; chain joints must agree to ~1 um
_JOIN_TOL = 1.5e-3
_WIDTH_TOL = 1e-3


def _mid(p, q):
    return ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)


def stadium_params(pts, tol=2e-3):
    """(start, end, width) when `pts` is the outline of a single thick
    line segment as produced by gerber._stadium(), else None. Validated
    by regenerating the stadium from the reconstructed parameters and
    requiring pointwise agreement, so arbitrary 18-vertex polygons are
    never misread as strokes."""
    if len(pts) != 18:
        return None
    p = [(float(a), float(b)) for a, b in pts]
    end = _mid(p[0], p[8])
    start = _mid(p[9], p[17])
    w = math.hypot(p[0][0] - p[8][0], p[0][1] - p[8][1])
    if w < 1e-6 or math.hypot(end[0] - start[0], end[1] - start[1]) < 1e-9:
        return None
    ref = _stadium(start[0], start[1], end[0], end[1], w)
    dev = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(p, ref))
    if dev > max(tol, w * 0.02):
        return None
    return start, end, w


def _outline(s):
    try:
        pts = [(float(x), float(y)) for x, y in shape_outline(s)]
    except Exception:
        return None
    return pts if len(pts) >= 3 else None


def _bb(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bb_hit(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _seg_cross(a, b, c, d):
    o = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _contains(outer, inner):
    """Is `inner` wholly inside `outer`? Vertex containment plus an edge
    -crossing check, so a shape poking out through an edge is not taken
    for contained."""
    if not all(point_in_ring(p[0], p[1], outer) for p in inner):
        return False
    n, m = len(inner), len(outer)
    for i in range(n):
        a, b = inner[i], inner[(i + 1) % n]
        for j in range(m):
            if _seg_cross(a, b, outer[j], outer[(j + 1) % m]):
                return False
    return True


def _overlaps(p, q):
    """Do two rings share area? (Containment is handled separately, so a
    vertex-inside test plus edge crossings is enough.)"""
    if any(point_in_ring(v[0], v[1], q) for v in p):
        return True
    if any(point_in_ring(v[0], v[1], p) for v in q):
        return True
    n, m = len(p), len(q)
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        for j in range(m):
            if _seg_cross(a, b, q[j], q[(j + 1) % m]):
                return True
    return False


def _try_union(indices, items):
    """Union of the given cluster members, or None when it may not be
    used: a hole cannot be expressed as one closed outline, and filling
    one would short whatever the gap was there to separate."""
    rings = [items[i][1] for i in indices]
    res = union_rings(rings)
    if res is None or res[1]:
        return None
    outers, _holes = res
    if verify_union(rings, outers, []) > _UNION_MISMATCH:
        return None
    return outers


def _merge_groups(members, items, max_pairs=400):
    """Yield (member indices, union outline) for every part of a cluster
    that can be merged. Whole-cluster first; when that would create holes
    (a ground pour around its slots, typically) fall back to merging
    whatever pairs stay hole-free, which still buries the seams between
    abutting pads and strokes."""
    whole = _try_union(members, items)
    if whole is not None:
        yield (list(members), whole)
        return
    groups = {i: [i] for i in members}
    rings = {i: items[i][1] for i in members}
    tries = 0
    changed = True
    while changed and tries < max_pairs:
        changed = False
        keys = sorted(groups)
        for a in keys:
            if a not in groups:
                continue
            for b in keys:
                if b <= a or b not in groups or a not in groups:
                    continue
                if not _bb_hit(_bb(rings[a]), _bb(rings[b])):
                    continue
                if not _overlaps(rings[a], rings[b]):
                    continue
                tries += 1
                if tries > max_pairs:
                    break
                res = union_rings([rings[a], rings[b]])
                if res is None or res[1] or len(res[0]) != 1:
                    continue
                if verify_union([rings[a], rings[b]], res[0], []) > _UNION_MISMATCH:
                    continue
                rings[a] = res[0][0]
                groups[a] = groups[a] + groups[b]
                del groups[b]
                del rings[b]
                changed = True
    for a, member_ids in groups.items():
        if len(member_ids) > 1:
            yield (member_ids, [rings[a]])


def merge_overlaps(shapes, dedupe=True, union=True, traces=False,
                   tol=0.0, stats=None):
    """Remove shapes made redundant by a bigger one and merge the rest of
    each overlapping group into a single outline, per layer.

    Overlapping copper on one layer is electrically just its union, but
    every buried edge still pins mesh lines - so this is a pure win for
    the mesh as long as the geometry is preserved exactly, which each
    merge is checked for before it is accepted.
    """
    st = stats if stats is not None else {}
    st.setdefault('contained', 0)
    st.setdefault('merged', 0)
    st.setdefault('mergedInto', 0)
    st.setdefault('mergeSkipped', 0)
    if not (dedupe or union):
        return list(shapes), st

    kinds = _MERGEABLE + (('trace',) if traces else ())
    idx = {id(s): i for i, s in enumerate(shapes)}
    groups = {}
    for s in shapes:
        if s.get('layer') == REF_LAYER or s.get('type', 'rect') not in kinds:
            continue
        ring = _outline(s)
        if ring:
            groups.setdefault(s.get('layer'), []).append((s, ring, _bb(ring)))

    drop, replace = set(), {}
    for layer, items in groups.items():
        # index by bbox so only plausible neighbours are compared
        span = max(max(b[2] - b[0], b[3] - b[1]) for _s, _r, b in items)
        grid = _Grid(max(span, 1e-3))
        for i, (_s, _r, b) in enumerate(items):
            grid.add(b, i)

        alive = set(range(len(items)))
        if dedupe:
            # bigger area first: a pad inside a pour is dropped, not vice versa
            order = sorted(range(len(items)),
                           key=lambda i: -abs(ring_area(items[i][1])))
            for i in order:
                if i not in alive:
                    continue
                si, ri, bi = items[i]
                for j in grid.query(bi):
                    if j == i or j not in alive:
                        continue
                    sj, rj, bj = items[j]
                    if not _bb_hit(bi, bj):
                        continue
                    if abs(ring_area(rj)) - abs(ring_area(ri)) > 1e-12:
                        continue          # only ever drop the smaller one
                    if _contains(ri, rj):
                        alive.discard(j)
                        drop.add(id(sj))
                        st['contained'] += 1
        if not union:
            continue

        # cluster what still overlaps
        parent = {i: i for i in alive}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for i in sorted(alive):
            si, ri, bi = items[i]
            for j in grid.query(bi):
                if j <= i or j not in alive:
                    continue
                if not _bb_hit(bi, items[j][2]):
                    continue
                if _overlaps(ri, items[j][1]):
                    a, b = find(i), find(j)
                    if a != b:
                        parent[a] = b
        clusters = {}
        for i in sorted(alive):
            clusters.setdefault(find(i), []).append(i)

        for members in clusters.values():
            if len(members) < 2:
                continue
            for group, outers in _merge_groups(members, items):
                head = items[group[0]][0]
                prio = max(items[i][0].get('priority', 10) for i in group)
                local = [items[i][0].get('mesh', {}).get('res') for i in group]
                local = [v for v in local if v]
                mesh = {'res': min(local)} if local else (head.get('mesh') or {})
                made = []
                for k, ring in enumerate(outers):
                    pts = dp_ring(ring, tol) if tol > 0 and len(ring) > 4 else ring
                    made.append({
                        'id': head.get('id') if k == 0 else None,
                        'name': head.get('name') or 'merged',
                        'type': 'poly', 'layer': layer, 'priority': prio,
                        'mesh': dict(mesh),
                        'pts': [[round(x, 4), round(y, 4)] for x, y in pts],
                    })
                replace[id(head)] = made
                for i in group[1:]:
                    drop.add(id(items[i][0]))
                st['merged'] += len(group)
                st['mergedInto'] += len(made)
            st['mergeSkipped'] += sum(
                1 for i in members
                if id(items[i][0]) not in drop and id(items[i][0]) not in replace)

    out = []
    for s in shapes:
        if id(s) in replace:
            out.extend(replace[id(s)])
        elif id(s) not in drop:
            out.append(s)
    return out, st


def simplify_shapes(shapes, opts=None):
    """Returns (new_shapes, stats). Options (all optional):
      ids      list of shape ids to touch (None = all shapes)
      traces   merge stroke-polygon chains into trace shapes (default on)
      polys    decimate polygon outlines (default on)
      tol      Douglas-Peucker tolerance in mm (default 0.02)
      maxSeg   resample curves to at most this chord length, 0 = off
      dedupe   drop shapes wholly covered by another on the same layer
      union    merge the remaining overlapping shapes into one outline
      unionTraces  let transmission lines take part in the merge (off:
               a trace keeps its width and its across-the-line mesh
               refinement, which a merged polygon would lose)
    """
    o = opts or {}
    try:
        tol = max(0.0, float(o.get('tol', 0.02) or 0.0))
        max_seg = max(0.0, float(o.get('maxSeg', 0) or 0.0))
    except (TypeError, ValueError):
        raise ValueError('tolerance and sampling step must be numbers')
    do_traces = o.get('traces', True)
    do_polys = o.get('polys', True)
    do_dedupe = bool(o.get('dedupe', False))
    do_union = bool(o.get('union', False))
    union_traces = bool(o.get('unionTraces', False))
    ids = o.get('ids')
    ids = None if ids is None else {int(i) for i in ids}

    def in_scope(s):
        return ids is None or s.get('id') in ids

    def vcount(ss):
        return sum(len(s.get('pts') or []) for s in ss)

    stats = {'shapes': len(shapes), 'vertices': vcount(shapes),
             'traces': 0, 'strokesMerged': 0, 'polysSimplified': 0,
             'contained': 0, 'merged': 0, 'mergedInto': 0, 'mergeSkipped': 0}
    out = []
    chain = None   # {'head': shape, 'pts': [...], 'w': width, 'n': count}

    def flush():
        nonlocal chain
        if not chain:
            return
        if chain['n'] < 2:
            # a lone stroke (single straight draw or an obround flash):
            # converting it buys nothing - the stroke polygon already
            # meshes with hard edge lines and a cross-width zone
            out.append(chain['head'])
            chain = None
            return
        head = chain['head']
        pts = dp_polyline(chain['pts'], tol)
        if max_seg > 0:
            pts = resample_polyline(pts, max_seg)
        t = {'id': head.get('id'), 'name': head.get('name') or 'trace',
             'type': 'trace', 'layer': head.get('layer'),
             'priority': head.get('priority', 10),
             'mesh': head.get('mesh') or {},
             'pts': [[round(x, 4), round(y, 4)] for x, y in pts],
             'width': round(chain['w'], 4), 'radius': 0}
        out.append(t)
        stats['traces'] += 1
        stats['strokesMerged'] += chain['n']
        chain = None

    for s in shapes:
        if do_traces and in_scope(s) and s.get('type') == 'poly':
            sp = stadium_params(s.get('pts') or [])
            if sp:
                start, end, w = sp
                if (chain
                        and s.get('layer') == chain['head'].get('layer')
                        and s.get('priority', 10) == chain['head'].get('priority', 10)
                        and abs(w - chain['w']) < _WIDTH_TOL
                        and math.hypot(start[0] - chain['pts'][-1][0],
                                       start[1] - chain['pts'][-1][1]) < _JOIN_TOL):
                    chain['pts'].append(end)
                    chain['n'] += 1
                else:
                    flush()
                    chain = {'head': s, 'pts': [start, end], 'w': w, 'n': 1}
                continue
        flush()
        if (do_polys and in_scope(s) and s.get('type') == 'poly'
                and tol > 0 and len(s.get('pts') or []) > 4):
            pts = dp_ring([(float(a), float(b)) for a, b in s['pts']], tol)
            if max_seg > 0:
                pts = resample_polyline(pts, max_seg, closed=True)
            if len(pts) != len(s['pts']):
                s = dict(s, pts=[[round(x, 4), round(y, 4)] for x, y in pts])
                stats['polysSimplified'] += 1
        out.append(s)
    flush()

    # overlap cleanup runs last: the stroke chains must become traces
    # first, or a merge would swallow them into one shapeless blob
    if do_dedupe or do_union:
        scope = out if ids is None else [s for s in out if in_scope(s)]
        merged, _ = merge_overlaps(scope, dedupe=do_dedupe, union=do_union,
                                   traces=union_traces, tol=tol, stats=stats)
        if ids is None:
            out = merged
        else:
            keep = {id(s) for s in scope}
            rebuilt, done = [], False
            for s in out:
                if id(s) in keep:
                    if not done:
                        rebuilt.extend(merged)
                        done = True
                else:
                    rebuilt.append(s)
            out = rebuilt

    # a merge can split one group into several outlines; every shape must
    # still carry a unique id for the editor to select and undo it
    used = {s.get('id') for s in out if s.get('id') is not None}
    nid = (max(used) if used else 0) + 1
    for s in out:
        if s.get('id') is None:
            while nid in used:
                nid += 1
            s['id'] = nid
            used.add(nid)

    stats['shapesAfter'] = len(out)
    stats['verticesAfter'] = vcount(out)
    return out, stats
