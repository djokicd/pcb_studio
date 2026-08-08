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

from geometry import dp_polyline, dp_ring, resample_polyline
from gerber import _stadium

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


def simplify_shapes(shapes, opts=None):
    """Returns (new_shapes, stats). Options (all optional):
      ids      list of shape ids to touch (None = all shapes)
      traces   merge stroke-polygon chains into trace shapes (default on)
      polys    decimate polygon outlines (default on)
      tol      Douglas-Peucker tolerance in mm (default 0.02)
      maxSeg   resample curves to at most this chord length, 0 = off
    """
    o = opts or {}
    try:
        tol = max(0.0, float(o.get('tol', 0.02) or 0.0))
        max_seg = max(0.0, float(o.get('maxSeg', 0) or 0.0))
    except (TypeError, ValueError):
        raise ValueError('tolerance and sampling step must be numbers')
    do_traces = o.get('traces', True)
    do_polys = o.get('polys', True)
    ids = o.get('ids')
    ids = None if ids is None else {int(i) for i in ids}

    def in_scope(s):
        return ids is None or s.get('id') in ids

    def vcount(ss):
        return sum(len(s.get('pts') or []) for s in ss)

    stats = {'shapes': len(shapes), 'vertices': vcount(shapes),
             'traces': 0, 'strokesMerged': 0, 'polysSimplified': 0}
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

    stats['shapesAfter'] = len(out)
    stats['verticesAfter'] = vcount(out)
    return out, stats
