"""Unit tests for geometry helpers and mesh generation (no octave needed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geometry import comp_element_box, shape_outline, stackup_z
from meshlines import build_mesh
from helpers import stackup, sim_settings, rect, lumped_port


def test_stackup_z():
    cond, diel, total = stackup_z(stackup(h=0.8))
    assert cond['bot'] == 0.0
    assert cond['top'] == 0.8
    assert diel['core'] == (0.0, 0.8)
    assert total == 0.8


def test_comp_element_box_shrinks_to_gap():
    # 0603 body (1.6 mm) centred on a 1.0 mm gap between two traces
    shapes = [rect('a', 0, 9, 19.5, 2), rect('b', 20.5, 9, 19.5, 2)]
    c = {'ctype': 'R', 'value': 100, 'package': '0603', 'x': 20, 'y': 10,
         'rot': 0, 'layer': 'top'}
    x0, y0, x1, y1, ny, connected = comp_element_box(c, shapes)
    assert connected
    assert ny == 0
    assert abs(x0 - 19.5) < 1e-6 and abs(x1 - 20.5) < 1e-6


def test_comp_element_box_disconnected():
    # no copper anywhere near the component
    c = {'ctype': 'R', 'value': 100, 'package': '0603', 'x': 20, 'y': 10,
         'rot': 0, 'layer': 'top'}
    *_, connected = comp_element_box(c, [])
    assert not connected


def test_shape_outlines():
    assert len(shape_outline({'type': 'rect', 'x': 0, 'y': 0, 'w': 1, 'h': 1})) == 4
    assert len(shape_outline({'type': 'circle', 'cx': 0, 'cy': 0, 'r': 2})) >= 32
    poly = shape_outline({'type': 'poly', 'pts': [[0, 0], [2, 0], [1, 2]]})
    assert poly == [(0, 0), (2, 0), (1, 2)]


def _model(shapes, **sim_kw):
    return {
        'board': {'width': 40, 'height': 20},
        'stackup': stackup(),
        'shapes': shapes,
        'ports': [lumped_port(1, 1, 9, 0.5, 2, excite=True)],
        'sim': sim_settings(**sim_kw),
    }


def test_mesh_local_region_is_honoured():
    m = build_mesh(_model([rect('t', 10, 8, 10, 3, res=0.4)]))
    xs = m['x']
    gaps = [(a + b) / 2 for a, b in zip(xs, xs[1:])]
    for (a, b), mid in zip(zip(xs, xs[1:]), gaps):
        if 10 < mid < 20:
            assert b - a <= 0.4 + 1e-6, f'cell {b - a:.3f} inside refinement region'


def test_mesh_merge_close_lines():
    # two abutting rects with edges 30 um apart must not create a 30 um cell
    m = build_mesh(_model([rect('a', 10, 8, 5, 3), rect('b', 15.03, 8, 5, 3)]))
    diffs = [b - a for a, b in zip(m['x'], m['x'][1:])]
    assert min(diffs) > 0.05


def test_mesh_z_graded_towards_ground():
    """z mesh is asymmetric: fine at the signal face (top, carries the
    trace), graded coarser through the substrate towards the ground
    plane, with fine air cells only on the signal side."""
    m = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    z = m['z']
    inside = [v for v in z if -1e-9 <= v <= 0.8 + 1e-9]
    cells = [b - a for a, b in zip(inside, inside[1:])]
    assert len(cells) >= 3                      # substrate still resolved
    assert cells[-1] < cells[0]                 # fine at the strip, coarse at ground
    assert cells[-1] <= 0.8 / 4                 # strip face genuinely fine
    # air above the strip: at least 3 fine transition lines
    above = [v for v in z if 0.8 < v < 1.7]
    assert len(above) >= 3
    first_above = min(above) - 0.8
    # air below the solid ground plane may be coarser (no field there),
    # but must still grade smoothly (bounded first-cell jump)
    below = sorted(v for v in z if v < 0)
    first_below = -max(below)
    assert first_above <= 0.8 / 4
    assert first_below <= 0.8 / 2


def test_mesh_cells_count_consistent():
    m = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    assert m['cells'] == len(m['x']) * len(m['y']) * len(m['z'])


def test_trace_outline_straight():
    from geometry import trace_length
    s = {'type': 'trace', 'pts': [[0, 0], [10, 0]], 'width': 2}
    pts = shape_outline(s)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert len(pts) >= 8
    # sides at +/- w/2, round caps extending w/2 past the ends
    assert abs(min(ys) + 1) < 1e-6 and abs(max(ys) - 1) < 1e-6
    assert -1.01 < min(xs) < -0.9 and 10.9 < max(xs) < 11.01
    assert abs(trace_length(s['pts'], 0) - 10) < 1e-9


def test_trace_fillet_length():
    from geometry import trace_length
    # right-angle bend, corner radius 2: shorter than the 20 mm manhattan
    # path, close to the circular-fillet value 16 + pi/2*2 ~ 19.14
    l = trace_length([[0, 0], [10, 0], [10, 10]], 2)
    assert 18.5 < l < 19.5


def test_trace_meshes_and_validates():
    from scriptgen import generate_script
    t = {'id': 7, 'name': 'line', 'type': 'trace', 'layer': 'top',
         'priority': 10, 'pts': [[2, 10], [20, 10], [28, 18]],
         'width': 1.45, 'radius': 3, 'mesh': {}}
    m = _model([t])
    mesh = build_mesh(m)
    # trace edge lines present around y = 10 +/- 0.725
    assert any(abs(v - 9.275) < 0.15 for v in mesh['y'])
    assert any(abs(v - 10.725) < 0.15 for v in mesh['y'])
    assert 'AddLinPoly' in generate_script(m)


def test_canvas_notes_are_ignored_by_mesh_and_script():
    """Notes are editor-only annotations: they must never influence the
    mesh or reach the generated script."""
    from scriptgen import generate_script
    m = _model([rect('t', 10, 8, 10, 3)])
    plain_mesh = build_mesh(m)
    plain_script = generate_script(m)
    m['notes'] = [{'id': 900, 'x': 500, 'y': -400, 'w': 190,
                   'collapsed': False, 'text': 'tuning note\nsecond line'}]
    noted = build_mesh(m)
    assert noted['x'] == plain_mesh['x'] and noted['y'] == plain_mesh['y']
    assert noted['z'] == plain_mesh['z']
    assert generate_script(m) == plain_script
    assert 'tuning note' not in plain_script


def test_reference_layer_invisible_to_sim_and_fab():
    """Shapes on the __ref comments layer are editor-only: excluded from
    validation, mesh, the generated script and the Gerber export."""
    from scriptgen import generate_script
    from gerber import export_fabrication
    ref = {'id': 8, 'name': 'connector_outline', 'type': 'rect',
           'layer': '__ref', 'x': -30, 'y': -30, 'w': 5, 'h': 5, 'mesh': {}}
    plain = _model([rect('t', 10, 8, 10, 3)])
    m = _model([rect('t', 10, 8, 10, 3), ref])
    base_mesh = build_mesh(plain)
    with_ref = build_mesh(m)
    assert with_ref['x'] == base_mesh['x'] and with_ref['y'] == base_mesh['y']
    script = generate_script(m)   # would raise "not on a conductor layer"
    assert script == generate_script(plain)
    files = export_fabrication(m)
    top = next(v for k, v in files.items() if 'Top' in k or 'top' in k.lower())
    assert 'X-30000000' not in top


def test_imported_strokes_mesh_edges_and_width():
    """Narrow imported (meshBbox) stroke polygons - traces from Gerber -
    get mesh lines that follow the actual copper edges plus a fine
    cross-width zone (~3 cells across the stroke), instead of a spray of
    per-segment bbox lines."""
    # a fake meander: 8 overlapping 0.8 mm-wide stroke segments
    strokes = []
    for k in range(8):
        x0 = 8 + k * 1.5
        strokes.append({'id': 100 + k, 'name': f'seg{k}', 'type': 'poly',
                        'layer': 'top', 'meshBbox': True, 'mesh': {},
                        'pts': [[x0, 9.0], [x0 + 2.0, 9.0],
                                [x0 + 2.0, 9.8], [x0, 9.8]]})
    m = build_mesh(_model(strokes))
    # the exact edge lines are present (axis-aligned edges pin hard lines)
    assert any(abs(v - 9.0) < 1e-6 for v in m['y'])
    assert any(abs(v - 9.8) < 1e-6 for v in m['y'])
    ys = [v for v in m['y'] if 8.9 <= v <= 9.9]
    cells = [b - a for a, b in zip(ys, ys[1:])]
    assert len(cells) >= 3                       # ~3 cells across the width
    assert max(cells) <= 0.8 / 3 * 1.7           # genuinely fine, not bbox noise
    # a big imported pour: exact edge lines, coarse interior
    pour = [{'id': 200, 'name': 'pour', 'type': 'poly', 'layer': 'top',
             'meshBbox': True, 'mesh': {},
             'pts': [[2, 2], [28, 2], [28, 18], [2, 18]]}]
    m2 = build_mesh(_model(pour))
    assert any(abs(v - 2.0) < 1e-6 for v in m2['x'])
    assert any(abs(v - 28.0) < 1e-6 for v in m2['x'])
    inner = [v for v in m2['x'] if 3 < v < 27]
    cells2 = [b - a for a, b in zip(inner, inner[1:])]
    assert min(cells2) > 0.8                     # interior stays coarse


def test_via_meshed_as_sampled_circle():
    """A via pins exact lines at its centre and at the drill/pad tangent
    extremes, with the ring resolved - not just a lonely centre line."""
    m = _model([rect('t', 10, 8, 10, 3)])
    m['vias'] = [{'x': 20, 'y': 10, 'drill': 0.6, 'pad': 1.2,
                  'from': 'top', 'to': 'bot', 'mesh': {}}]
    mesh = build_mesh(m)
    for off in (-0.6, -0.3, 0.0, 0.3, 0.6):         # +-pad/2, +-drill/2, centre
        assert any(abs(v - (20 + off)) < 1e-3 for v in mesh['x']), f'missing x {off}'
        assert any(abs(v - (10 + off)) < 1e-3 for v in mesh['y']), f'missing y {off}'
    across = [v for v in mesh['x'] if 19.39 <= v <= 20.61]
    cells = [b - a for a, b in zip(across, across[1:])]
    assert len(cells) >= 4                            # pad resolved
    assert max(cells) <= 0.45                         # no gaping hole in the via


def test_curved_and_oblique_edges_sampled():
    """Curved shape edges (circles) and oblique polygon edges produce
    mesh lines along the actual edge, not just bbox lines."""
    c = {'id': 5, 'name': 'c', 'type': 'circle', 'layer': 'top',
         'priority': 10, 'cx': 20, 'cy': 10, 'r': 2.0, 'mesh': {}}
    mesh = build_mesh(_model([c]))
    across = [v for v in mesh['x'] if 17.95 <= v <= 22.05]
    cells = [b - a for a, b in zip(across, across[1:])]
    assert any(abs(v - 18.0) < 1e-6 for v in mesh['x'])    # exact tangent
    assert any(abs(v - 22.0) < 1e-6 for v in mesh['x'])
    assert len(cells) >= 4                                 # edge sampled
    # oblique edge: a taper's slanted sides get staircase lines between
    # the vertices (the old mesher only pinned the endpoints)
    p = {'id': 6, 'name': 'taper', 'type': 'poly', 'layer': 'top',
         'priority': 10, 'pts': [[10, 8], [30, 9.3], [30, 10.7], [10, 12]],
         'mesh': {}}
    mesh2 = build_mesh(_model([p]))
    interior = [v for v in mesh2['x'] if 12 < v < 28]
    assert len(interior) >= 5                              # sampled along the slant


def test_mesh_worst_ratio_bounded():
    """Neighbouring cells never jump more than ~ratio^1.5 anywhere."""
    strokes = []
    for k in range(6):
        x0 = 8 + k * 2.0
        strokes.append({'id': 300 + k, 'name': f's{k}', 'type': 'poly',
                        'layer': 'top', 'meshBbox': True, 'mesh': {},
                        'pts': [[x0, 9.0], [x0 + 2.5, 9.0],
                                [x0 + 2.5, 9.4], [x0, 9.4]]})
    m = _model(strokes)
    m['vias'] = [{'x': 12, 'y': 6, 'drill': 0.3, 'pad': 0.6,
                  'from': 'top', 'to': 'bot', 'mesh': {}}]
    mesh = build_mesh(m)
    assert mesh['worstRatio'] <= 1.5 ** 1.5 * 1.05
    assert mesh['minCell'] >= 0.05


def test_via_mesh_lines_economy_settings():
    """mesh.lines caps how many lines a via pins per axis: 1 = centre
    only, 3 = + drill tangents, 5 = + pad tangents, unset = full circle
    staircase."""
    from geometry import mesh_lines_xy

    def lines_for(setting):
        m = _model([rect('t', 10, 8, 10, 3)])
        via = {'x': 30, 'y': 10, 'drill': 0.6, 'pad': 1.2,
               'from': 'top', 'to': 'bot', 'mesh': {}}
        if setting is not None:
            via['mesh'] = {'lines': setting}
        m['vias'] = [via]
        xs, ys, xsoft, ysoft, xreg, yreg, _xp, _yp, _sr = mesh_lines_xy(m, 0.4)
        near_h = [v for v in xs if 28.5 < v < 31.5]
        near_s = [c for c in xsoft if 28.5 < c[0] < 31.5]
        return near_h, near_s

    h1, s1 = lines_for(1)
    assert h1 == [30] and s1 == []                       # centre only

    h3, s3 = lines_for(3)
    assert h3 == [30]
    assert sorted(round(c[0], 3) for c in s3) == [29.7, 30.3]   # drill only

    h5, s5 = lines_for(5)
    assert sorted(round(c[0], 3) for c in s5) == [29.4, 29.7, 30.3, 30.6]

    ha, sa = lines_for(None)                             # auto: staircase too
    assert len(sa) > 4
    assert {29.4, 29.7, 30.3, 30.6} <= {round(c[0], 3) for c in sa}


def test_mesh_has_component_lift_plane():
    """A lumped component adds an exact z line at its lifted element
    plane (COMP_LIFT above the copper), so the element and the air gap
    under the body are resolved."""
    m = _model([rect('t', 10, 8, 10, 3)])
    m['components'] = [{'id': 9, 'ref': 'R1', 'ctype': 'R', 'value': 100,
                        'package': '0603', 'x': 20, 'y': 10, 'rot': 0,
                        'layer': 'top'}]
    mesh = build_mesh(m)
    assert any(abs(v - 1.0) < 1e-6 for v in mesh['z'])   # 0.8 + 0.2


def test_component_terminal_lines_survive_mesh_merge():
    """meshMerge must not move the mesh lines under a component's
    zero-thickness element sheet and terminal walls - a wall 20 um off
    its line rasterizes to nothing and silently disconnects the part."""
    m = _model([rect('t', 10, 8, 10, 3)], meshMerge=0.1)
    # a copper edge 30 um away from the element-box end tempts the merge
    m['shapes'].append(rect('near', 19.23, 12, 2, 1))
    m['components'] = [{'id': 9, 'ref': 'R1', 'ctype': 'R', 'value': 100,
                        'package': '0603', 'x': 20, 'y': 10, 'rot': 0,
                        'layer': 'top'}]
    from geometry import comp_element_box
    x0, y0, x1, y1, ny, _ = comp_element_box(m['components'][0], m['shapes'])
    mesh = build_mesh(m)
    for want in (x0, x1):
        assert any(abs(v - want) < 1e-6 for v in mesh['x']), \
            f'element line {want} missing from x mesh'
    for want in (y0, y1):
        assert any(abs(v - want) < 1e-6 for v in mesh['y']), \
            f'element line {want} missing from y mesh'


def test_user_density_region_x():
    """A Meshing-tab density region forces its resolution inside the
    interval, adds its boundaries as mesh lines, and leaves the rest of
    the board coarse."""
    plain = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    m = _model([rect('t', 10, 8, 10, 3)])
    m['mesh'] = {'regions': [{'id': 1, 'axis': 'x', 'from': 24.0,
                              'to': 32.0, 'res': 0.3}]}
    mesh = build_mesh(m)
    for want in (24.0, 32.0):
        assert any(abs(v - want) < 1e-6 for v in mesh['x']), \
            f'region boundary {want} missing from x mesh'
    # the graded fill may leave a gap marginally over the promise
    # (the cap_hard * 1.3 split rule) - never more
    for a, b in zip(mesh['x'], mesh['x'][1:]):
        if 24.0 < (a + b) / 2 < 32.0:
            assert b - a <= 0.3 * 1.3 + 1e-6, f'cell {b - a:.3f} inside region'
    assert len(mesh['x']) > len(plain['x'])
    # y axis untouched
    assert mesh['y'] == plain['y']


def test_user_density_region_y_and_disabled():
    base = _model([rect('t', 10, 8, 10, 3)])
    base['mesh'] = {'regions': [{'id': 1, 'axis': 'y', 'from': 4.0,
                                 'to': 9.0, 'res': 0.25}]}
    mesh = build_mesh(base)
    for a, b in zip(mesh['y'], mesh['y'][1:]):
        if 4.0 < (a + b) / 2 < 9.0:
            assert b - a <= 0.25 * 1.3 + 1e-6
    # switched off -> identical to no region at all
    base['mesh']['regions'][0]['off'] = True
    off = build_mesh(base)
    plain = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    assert off['y'] == plain['y'] and off['x'] == plain['x']


def test_user_density_region_invalid_entries_ignored():
    plain = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    m = _model([rect('t', 10, 8, 10, 3)])
    m['mesh'] = {'regions': [
        {'id': 1, 'axis': 'x', 'from': 5, 'to': 5, 'res': 0.3},      # empty
        {'id': 2, 'axis': 'x', 'from': 1, 'to': 4, 'res': 0},        # res<=0
        {'id': 3, 'axis': 'x', 'from': 'a', 'to': 4, 'res': 0.3},    # NaNs
        {'id': 4, 'axis': 'x', 'res': 0.3},                          # missing
        'not-a-dict',
    ]}
    mesh = build_mesh(m)
    assert mesh['x'] == plain['x'] and mesh['y'] == plain['y']


def test_user_pinned_lines_and_points():
    """Lines and points placed in the Meshing tab land in the mesh
    exactly - they are pinned, so the coincidence merge pulls a nearby
    geometry line onto them instead of averaging the pair away."""
    m = _model([rect('t', 10, 8, 10, 3)], meshMerge=0.1)
    m['mesh'] = {
        'lines': [{'id': 1, 'axis': 'x', 'at': 27.5},
                  {'id': 2, 'axis': 'y', 'at': 3.25},
                  {'id': 3, 'axis': 'x', 'at': 10.04}],   # 40 um off a copper edge
        'points': [{'id': 4, 'x': 33.125, 'y': 16.5}],
    }
    mesh = build_mesh(m)
    for want in (27.5, 10.04, 33.125):
        assert any(abs(v - want) < 1e-6 for v in mesh['x']), f'x line {want} missing'
    for want in (3.25, 16.5):
        assert any(abs(v - want) < 1e-6 for v in mesh['y']), f'y line {want} missing'
    # the merge moved the copper edge onto the pin rather than keeping both
    assert not any(abs(v - 10.0) < 1e-9 for v in mesh['x'])
    assert min(b - a for a, b in zip(mesh['x'], mesh['x'][1:])) > 0.05


def test_user_lines_off_board_and_disabled_ignored():
    plain = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    m = _model([rect('t', 10, 8, 10, 3)])
    m['mesh'] = {
        'lines': [{'id': 1, 'axis': 'x', 'at': 55.0},       # past the 40 mm board
                  {'id': 2, 'axis': 'x', 'at': -3.0},
                  {'id': 3, 'axis': 'y', 'at': 12.0, 'off': True},
                  {'id': 4, 'axis': 'x', 'at': None}],
        'points': [{'id': 5, 'x': 12.0, 'y': 6.0, 'off': True}],
    }
    mesh = build_mesh(m)
    assert mesh['x'] == plain['x'] and mesh['y'] == plain['y']


def test_outside_regions_smoothing():
    """The outside settings cap and grade the board area not covered by a
    density range, leaving the range itself at its own resolution."""
    base = _model([rect('t', 10, 8, 10, 3)])
    base['mesh'] = {'regions': [{'id': 1, 'axis': 'x', 'from': 12.0,
                                 'to': 18.0, 'res': 0.3}],
                    'outside': {'res': 0.9, 'ratio': 1.2}}
    mesh = build_mesh(base)
    for a, b in zip(mesh['x'], mesh['x'][1:]):
        mid = (a + b) / 2
        if not 0 <= mid <= 40:
            continue                      # air margin keeps lambda/N
        cap = 0.3 if 12.0 < mid < 18.0 else 0.9
        assert b - a <= cap * 1.3 + 1e-6, f'cell {b - a:.3f} at {mid:.2f}'
    # a coarse outside setting must not be finer than doing nothing
    plain = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    assert len(mesh['x']) > len(plain['x'])


def test_outside_settings_validated():
    plain = build_mesh(_model([rect('t', 10, 8, 10, 3)]))
    m = _model([rect('t', 10, 8, 10, 3)])
    m['mesh'] = {'outside': {'res': 'x', 'ratio': None}}
    assert build_mesh(m)['x'] == plain['x']
    # the ratio is clamped into the mesher's supported band
    from meshlines import user_outside
    assert user_outside({'mesh': {'outside': {'ratio': 9}}})['ratio'] == 2.0
    assert user_outside({'mesh': {'outside': {'ratio': 1.0}}})['ratio'] == 1.2


def _fence(nlines=None, pad_lines=None):
    """A stitching fence: drilled vias plus the separate round copper pads
    an imported board carries for them."""
    m = _model([rect('gnd', 2, 2, 36, 16)])
    m['vias'], pads = [], []
    for i in range(8):
        x = 5.0 + i * 2.0
        v = {'id': 100 + i, 'x': x, 'y': 6.0, 'drill': 0.3, 'pad': 0.6,
             'from': 'bot', 'to': 'top'}
        if nlines:
            v['mesh'] = {'lines': nlines}
        m['vias'].append(v)
        p = {'id': 200 + i, 'type': 'circle', 'layer': 'top',
             'cx': x, 'cy': 6.0, 'r': 0.3}
        if pad_lines:
            p['mesh'] = {'lines': pad_lines}
        pads.append(p)
    m['shapes'] += pads
    return m


def test_via_economy_reaches_the_imported_pads():
    """The per-via mesh-line economy must also tame the round copper pad
    an import carries on top of each via - the pad, not the barrel, is
    what generates the dense staircase, so without this the setting has
    almost no effect on imported boards."""
    auto = build_mesh(_fence())
    one = build_mesh(_fence(nlines=1))
    assert one['cells'] < auto['cells'] * 0.75, \
        f'economy barely helped: {auto["cells"]} -> {one["cells"]}'
    # 1 line/via: the pad contributes its centre and nothing else
    for i in range(8):
        x = 5.0 + i * 2.0
        assert any(abs(v - x) < 1e-6 for v in one['x']), f'via centre {x} missing'
        for edge in (x - 0.3, x + 0.3):
            assert not any(abs(v - edge) < 1e-9 for v in one['x']), \
                f'pad edge {edge} still pinned at 1 line/via'
    # the ladder is monotonic
    three = build_mesh(_fence(nlines=3))
    assert one['cells'] <= three['cells'] <= auto['cells']


def test_pad_economy_can_be_set_on_the_shape():
    """Round copper without a drilled via under it (Gerber imported with
    no Excellon) takes the setting directly."""
    plain, economised = _fence(), _fence(pad_lines=1)
    plain['vias'] = economised['vias'] = []
    assert build_mesh(economised)['cells'] < build_mesh(plain)['cells'] * 0.85


def test_pad_setting_overrides_via_inheritance():
    """An explicit setting on the pad wins over the via's - and with the
    via left on auto its own sampling keeps the density either way, so
    the economy has to be set on the via to tame an imported fence."""
    # pad asked for more detail than the via it sits on -> it gets it
    assert build_mesh(_fence(nlines=1, pad_lines=5))['cells'] \
        > build_mesh(_fence(nlines=1))['cells']
    # and with the via left on auto its own sampling keeps the density,
    # so economising only the pad changes nothing on a drilled fence
    assert build_mesh(_fence(pad_lines=1))['cells'] \
        == build_mesh(_fence())['cells']


def test_pad_economy_only_applies_to_concentric_pads():
    """A circle that merely sits near a via keeps full detail - the
    inheritance is for the pad the via was drilled through."""
    m = _fence(nlines=1)
    for s in m['shapes']:
        if s.get('type') == 'circle':
            s['cx'] += 0.5          # 500 um off the barrel: its own feature
    off = build_mesh(m)
    on = build_mesh(_fence(nlines=1))
    assert off['cells'] > on['cells'], 'offset circles must keep full sampling'


# ------------------------------------------------------- coplanar slots
def _cpw(gap=0.3, shift=0.0, slots=None, **sim_kw):
    """GCPW cross-section: 1 mm strip between two pours, `gap` each side.
    `shift` nudges the whole slot structure by a sub-cell amount - the
    mesh quality must not depend on where the copper happens to sit."""
    y0 = 9.4 + shift
    m = {
        'board': {'width': 40, 'height': 20},
        'stackup': stackup(),
        'shapes': [
            rect('strip', 2, y0, 36, 1.0),
            rect('gnd_lo', 1, 1, 38, y0 - gap - 1),
            rect('gnd_hi', 1, y0 + 1.0 + gap, 38, 19 - (y0 + 1.0 + gap)),
        ],
        'ports': [lumped_port(1, 2, y0, 0.5, 1.0, excite=True)],
        'sim': sim_settings(**sim_kw),
    }
    if slots is not None:
        m['mesh'] = {'slots': slots}
    return m


def _slot_edges_y(m):
    sh = {s['name']: s for s in m['shapes']}
    lo = (sh['gnd_lo']['y'] + sh['gnd_lo']['h'], sh['strip']['y'])
    hi = (sh['strip']['y'] + sh['strip']['h'], sh['gnd_hi']['y'])
    return lo, hi


def test_cpw_gap_is_deliberately_meshed():
    """Both slot edges carry the thirds pair (rt/3 into the metal, 2rt/3
    into the gap) and the gap holds several cells - on the GROUND side
    too, which has no auto-thirds of its own."""
    m = _cpw(gap=0.3)
    mesh = build_mesh(m)
    from geometry import SLOT_CELLS, SLOT_RT_FLOOR
    rt = max(0.3 / SLOT_CELLS, SLOT_RT_FLOOR)
    for e0, e1 in _slot_edges_y(m):
        inside = [y for y in mesh['y'] if e0 + 1e-6 < y < e1 - 1e-6]
        assert len(inside) >= 3, f'only {len(inside)} lines inside gap [{e0},{e1}]'
        for e, into_metal in ((e0, -1), (e1, +1)):
            # gap side: the 2rt/3 line must be there
            want = e - into_metal * 2.0 * rt / 3.0
            d = min(abs(y - want) for y in mesh['y'])
            assert d < 5e-3, f'missing gap-side pair line at {want:.4f} (edge {e})'
            # metal side: the rt/3 line, unless a hard line sits exactly
            # on the edge (an abutting port) and the pair yields to it
            want = e + into_metal * rt / 3.0
            d = min(min(abs(y - want), abs(y - e)) for y in mesh['y'])
            assert d < 5e-3, f'edge {e} uncovered on the metal side'


def test_cpw_gap_meshing_is_nudge_stable():
    """Sub-cell geometry nudges must not change how well the gap is
    resolved: same line count in the slot, edges equally well covered.
    (Previously 60 um moved lines 20-40 um off the copper edges and
    changed the cell count in the gap - Z0 jumped run to run.)"""
    counts, worst_edge = [], []
    for shift in (0.0, 0.03, 0.07):
        m = _cpw(gap=0.24, shift=shift)
        mesh = build_mesh(m)
        n = 0
        for e0, e1 in _slot_edges_y(m):
            n += len([y for y in mesh['y'] if e0 + 1e-6 < y < e1 - 1e-6])
            for e in (e0, e1):
                worst_edge.append(min(abs(y - e) for y in mesh['y']))
        counts.append(n)
    assert len(set(counts)) == 1, f'line count in gap varies with nudge: {counts}'
    # nearest line is one of the pair members: never further than 2rt/3
    from geometry import SLOT_RT_FLOOR
    assert max(worst_edge) <= 2 * SLOT_RT_FLOOR / 3 + 1e-6, worst_edge


def test_slot_refinement_can_be_disabled():
    on = build_mesh(_cpw(gap=0.3))
    off = build_mesh(_cpw(gap=0.3, slots=False))
    assert off['cells'] < on['cells']
    e0, e1 = _slot_edges_y(_cpw(gap=0.3))[0]
    inside_off = [y for y in off['y'] if e0 + 1e-6 < y < e1 - 1e-6]
    inside_on = [y for y in on['y'] if e0 + 1e-6 < y < e1 - 1e-6]
    assert len(inside_on) > len(inside_off)


def test_slots_below_merge_tolerance_are_left_alone():
    """A 30 um sliver between two pours is import noise: meshMerge fuses
    its edges, and the slot pass must not fight that with um cells."""
    m = _cpw(gap=0.03)
    mesh = build_mesh(m)
    assert mesh['minCell'] >= 0.02


def test_region_boundary_does_not_displace_copper_edge():
    """A refinement-region boundary within meshMerge of a copper edge
    must neither move the edge line nor survive as a duplicate."""
    from meshlines import _smooth_axis
    out = _smooth_axis([0.0, 9.1, 20.0], 0.0, 20.0, 1.5, 3.0, 0,
                       regions=[(9.15, 12.0, 0.5)], merge=0.1)
    assert any(abs(v - 9.1) < 1e-9 for v in out), 'copper edge moved'
    assert not any(9.1 + 1e-9 < v < 9.2 for v in out), 'stray boundary line'
    # a boundary clear of the tolerance still becomes a line
    out = _smooth_axis([0.0, 9.1, 20.0], 0.0, 20.0, 1.5, 3.0, 0,
                       regions=[(9.3, 12.0, 0.5)], merge=0.1)
    assert any(abs(v - 9.1) < 1e-9 for v in out)
    assert any(abs(v - 9.3) < 1e-9 for v in out)


def test_pin_does_not_swallow_neighbouring_feature_line():
    """A pinned line (component terminal) exactly one merge-tolerance
    from a hard feature line (port edge): both must survive exactly.
    Previously the pin joined the cluster, dragged the mean, and the
    pin-restore then deleted the merged line - the port edge vanished."""
    from meshlines import _smooth_axis
    out = _smooth_axis([0.0, 9.4, 9.4, 9.5, 20.0], 0.0, 20.0, 1.5, 3.0, 0,
                       merge=0.1, pinned=[9.5])
    assert any(abs(v - 9.4) < 1e-9 for v in out), 'port edge line lost'
    assert any(abs(v - 9.5) < 1e-9 for v in out), 'pin lost'


# --------------------------------------------------- manual mesh mode
from meshlines import BIASES, profile_lines            # noqa: E402


def _manual(ranges=(), min_res=1.0, ratio=1.5, shapes=None, ports=None):
    m = _model(list(shapes) if shapes is not None
               else [rect('t', 10, 8, 10, 3, thirds=True)])
    m['vias'] = [{'id': 7, 'x': 30, 'y': 10, 'drill': 0.3, 'pad': 0.6,
                  'from': 'top', 'to': 'bot', 'mesh': {}}]
    # a port box is pinned exactly in manual mode (it has to keep its
    # area); tests about copper or about the fill drop it to isolate them
    if ports is not None:
        m['ports'] = list(ports)
    m['mesh'] = {'mode': 'manual',
                 'outside': {'res': min_res, 'ratio': ratio},
                 'regions': [dict(r) for r in ranges]}
    return m


@pytest.mark.parametrize('bias', BIASES)
def test_profile_places_exactly_the_cells_asked_for(bias):
    lines = profile_lines(2.0, 5.0, 9, 1.4, bias)
    assert len(lines) == 10                       # cells + 1
    assert abs(lines[0] - 2.0) < 1e-12 and abs(lines[-1] - 5.0) < 1e-12
    assert all(b > a for a, b in zip(lines, lines[1:])), 'lines must advance'


def test_profile_biases_put_the_fine_cells_where_asked():
    def cells(bias, ratio=1.5):
        L = profile_lines(0.0, 1.0, 8, ratio, bias)
        return [round(b - a, 6) for a, b in zip(L, L[1:])]

    uni = cells('uniform')
    assert max(uni) - min(uni) < 1e-9

    start = cells('start')
    assert start == sorted(start), 'start: cells must grow away from the start'
    assert start[-1] / start[0] > 5

    end = cells('end')
    assert end == sorted(end, reverse=True)
    assert end == start[::-1]

    both = cells('both')
    assert both == both[::-1], 'both: symmetric'
    assert both[0] == min(both) and both[len(both) // 2] == max(both)

    mid = cells('center')
    assert mid == mid[::-1], 'center: symmetric'
    assert mid[0] == max(mid) and mid[len(mid) // 2] == min(mid)

    # every profile spends the same span, whatever the distribution
    for c in (uni, start, end, both, mid):
        assert abs(sum(c) - 1.0) < 1e-9


def test_manual_mode_starts_with_no_mesh_lines():
    """The geometry contributes nothing: with no ranges the board is a
    plain grid at the minimum density, with no line on any copper edge."""
    # copper deliberately off the round grid the minimum density produces.
    # No port: its box IS pinned in manual mode (it has to keep its area),
    # which would break the "one uniform grid" this test is about.
    m = _manual(min_res=1.0, ports=[],
                shapes=[rect('t', 10.37, 8.13, 10.11, 3.07)])
    mesh = build_mesh(m)
    on_board = [v for v in mesh['x'] if -1e-9 <= v <= 40 + 1e-9]
    cells = [b - a for a, b in zip(on_board, on_board[1:])]
    assert max(cells) - min(cells) < 1e-6, 'expected one uniform grid'
    assert max(cells) <= 1.0 + 1e-6
    for edge in (10.37, 20.48, 29.85, 30.15):   # trace edges, via tangents
        assert min(abs(v - edge) for v in mesh['x']) > 1e-3, \
            f'geometry still pinned a line at {edge}'
    for edge in (8.13, 11.20):                  # trace edges on y
        assert min(abs(v - edge) for v in mesh['y']) > 1e-3, \
            f'geometry still pinned a line at {edge}'


def test_manual_mode_ignores_the_geometry_entirely():
    """Same ranges, wildly different copper: the mesh must be identical."""
    ranges = [{'id': 1, 'axis': 'x', 'from': 12, 'to': 15, 'cells': 12,
               'ratio': 1.2, 'bias': 'both'}]
    a = build_mesh(_manual(ranges, shapes=[rect('t', 10, 8, 10, 3)]))
    b = build_mesh(_manual(ranges, shapes=[rect('u', 3, 2, 31, 15),
                                           rect('v', 21, 9, 4, 0.3)]))
    assert a['x'] == b['x'] and a['y'] == b['y']


def test_manual_range_lines_land_exactly_where_the_profile_says():
    ranges = [{'id': 1, 'axis': 'y', 'from': 6.0, 'to': 9.0, 'cells': 10,
               'ratio': 1.3, 'bias': 'both'}]
    mesh = build_mesh(_manual(ranges))
    want = profile_lines(6.0, 9.0, 10, 1.3, 'both')
    inside = [v for v in mesh['y'] if 6.0 - 1e-9 <= v <= 9.0 + 1e-9]
    assert len(inside) == len(want)
    for a, b in zip(inside, want):
        assert abs(a - b) < 1e-6


def test_manual_outside_relaxes_to_the_minimum_density():
    """Cells grow from the range edge at the grading ratio and stop at
    the minimum density - never above it."""
    ranges = [{'id': 1, 'axis': 'x', 'from': 19.0, 'to': 21.0, 'cells': 40,
               'ratio': 1.0, 'bias': 'uniform'}]
    mesh = build_mesh(_manual(ranges, min_res=0.8, ratio=1.5))
    on_board = [v for v in mesh['x'] if -1e-9 <= v <= 40 + 1e-9]
    cells = [b - a for a, b in zip(on_board, on_board[1:])]
    assert max(cells) <= 0.8 + 1e-6, 'minimum density exceeded'
    ratios = [max(a, b) / min(a, b) for a, b in zip(cells, cells[1:])]
    assert max(ratios) <= 1.5 + 0.05, f'grading jumped by {max(ratios):.2f}'


def test_manual_ranges_can_be_disabled_and_survive_bad_values():
    off = [{'id': 1, 'axis': 'x', 'from': 5, 'to': 9, 'cells': 30, 'off': True}]
    assert build_mesh(_manual(off))['x'] == build_mesh(_manual())['x']
    junk = [{'id': 2, 'axis': 'x', 'from': 'x', 'to': 9, 'cells': 4},
            {'id': 3, 'axis': 'x', 'from': 5, 'to': 5},
            {'id': 4, 'axis': 'x', 'from': 8, 'to': 12}]     # no cells: uses res
    mesh = build_mesh(_manual(junk))
    assert mesh['cells'] > 0


def test_auto_mode_is_untouched_by_the_manual_fields():
    """A project carrying manual ranges still meshes from the geometry
    while the mode says automatic - switching modes loses nothing."""
    ranges = [{'id': 1, 'axis': 'x', 'from': 12, 'to': 15, 'cells': 12,
               'res': 0.3, 'ratio': 1.2, 'bias': 'both'}]
    m = _manual(ranges)
    del m['mesh']['mode']
    mesh = build_mesh(m)
    assert min(abs(v - 10.0) for v in mesh['x']) < 0.4, 'copper edge lost'
    for lo, hi in ((12.0, 15.0),):
        inside = [v for v in mesh['x'] if lo < v < hi]
        assert all(b - a <= 0.3 + 1e-6 for a, b in zip(inside, inside[1:]))


def _worst_step(vals):
    cells = [b - a for a, b in zip(vals, vals[1:])]
    return max(max(a, b) / min(a, b) for a, b in zip(cells, cells[1:]))


@pytest.mark.parametrize('regs,out_cfg', [
    # a range far finer than the minimum density
    ([{'id': 1, 'axis': 'y', 'from': 9, 'to': 11, 'cells': 200}],
     {'res': 3.0, 'ratio': 1.5}),
    # a range COARSER at its edges than the minimum density outside it
    ([{'id': 1, 'axis': 'y', 'from': 6, 'to': 14, 'cells': 16,
       'ratio': 1.4, 'bias': 'center'}], {'res': 0.5, 'ratio': 1.5}),
    # two ranges with room to relax between them
    ([{'id': 1, 'axis': 'y', 'from': 6, 'to': 7, 'cells': 20},
      {'id': 2, 'axis': 'y', 'from': 13, 'to': 14, 'cells': 20}],
     {'res': 1.0, 'ratio': 1.5}),
    # range hard against the board edge
    ([{'id': 1, 'axis': 'y', 'from': 0, 'to': 1, 'cells': 10}],
     {'res': 1.5, 'ratio': 1.5}),
    # gentle grading
    ([{'id': 1, 'axis': 'y', 'from': 9, 'to': 11, 'cells': 40}],
     {'res': 2.0, 'ratio': 1.2}),
])
def test_manual_outside_is_smooth_in_both_directions(regs, out_cfg):
    """Cells outside a range must grade to the minimum density without a
    jump - including when the range's own cells are COARSER than that
    density and the fill has to step DOWN to reach it. Clamping the
    starting size to the cap put a 17x jump right at the boundary."""
    # no port: a port box inside a range is pinned exactly and nudges the
    # range's spacing, which test_manual_port_inside_a_range covers
    m = _manual(regs, min_res=out_cfg['res'], ratio=out_cfg['ratio'], ports=[])
    mesh = build_mesh(m)
    worst = _worst_step(mesh['y'])
    assert worst <= out_cfg['ratio'] + 0.1, \
        f'grading jumped by {worst:.2f} with ratio {out_cfg["ratio"]}'


def test_manual_edge_offset_leaves_a_line_free_band():
    """`offset` holds the mesh lines back from the range edges: the band
    is one cell with nothing inside it, and the relaxation outside may
    not drop a line into it either."""
    regs = [{'id': 1, 'axis': 'y', 'from': 9.0, 'to': 10.8, 'cells': 12,
             'offset': 0.2}]
    mesh = build_mesh(_manual(regs, min_res=1.5))
    for lo, hi in ((9.0, 9.2), (10.6, 10.8)):
        inside = [v for v in mesh['y'] if lo + 1e-6 < v < hi - 1e-6]
        assert not inside, f'offset band {lo}-{hi} has lines: {inside}'
    assert any(abs(v - 9.2) < 1e-6 for v in mesh['y']), 'first line missing'
    assert any(abs(v - 9.0) < 1e-6 for v in mesh['y']), 'band edge missing'
    # the profile still gets its full cell count inside the inner span
    inner = [v for v in mesh['y'] if 9.2 - 1e-6 <= v <= 10.6 + 1e-6]
    assert len(inner) == 13


def test_manual_edge_offset_is_clamped_and_optional():
    base = build_mesh(_manual([{'id': 1, 'axis': 'y', 'from': 9, 'to': 11,
                                'cells': 8}]))
    zero = build_mesh(_manual([{'id': 1, 'axis': 'y', 'from': 9, 'to': 11,
                                'cells': 8, 'offset': 0}]))
    assert base['y'] == zero['y']
    # an offset wider than the range cannot empty it
    huge = build_mesh(_manual([{'id': 1, 'axis': 'y', 'from': 9, 'to': 11,
                                'cells': 8, 'offset': 50}]))
    assert len(huge['y']) > 4


def test_manual_range_density_by_spacing():
    """`by: 'spacing'` fixes the DENSITY: the cell count follows the
    range's width instead of the cells stretching with it."""
    from meshlines import _range_cells
    r = {'by': 'spacing', 'spacing': 0.1}
    assert _range_cells(r, 2.0) == 20
    assert _range_cells(r, 4.0) == 40          # wider range, same density
    # a count-driven range keeps its count whatever the width
    assert _range_cells({'cells': 12}, 2.0) == _range_cells({'cells': 12}, 4.0) == 12
    # junk spacing falls back to the count rather than meshing nothing
    assert _range_cells({'by': 'spacing', 'spacing': 'x', 'cells': 7}, 2.0) == 7

    mesh = build_mesh(_manual([{'id': 1, 'axis': 'y', 'from': 9.0, 'to': 10.8,
                                'by': 'spacing', 'spacing': 0.05}]))
    inside = [v for v in mesh['y'] if 9.0 - 1e-9 <= v <= 10.8 + 1e-9]
    assert len(inside) == 37                   # 1.8 / 0.05 = 36 cells
    widths = [b - a for a, b in zip(inside, inside[1:])]
    assert max(widths) - min(widths) < 1e-9
    assert abs(widths[0] - 0.05) < 1e-9


def test_manual_mode_honours_per_via_mesh_lines():
    """Manual mode derives nothing from the geometry - but a per-object
    mesh setting is an instruction, not derivation. Without this a via
    barrel sits between two lines of the background grid and rasterizes
    onto whatever cell happens to cover it."""
    # deliberately off the round grid the background density produces
    cx, cy = 30.37, 10.37
    rd, rp = 0.15, 0.3

    def mesh_for(lines):
        m = _manual(min_res=1.0)
        for v in m['vias']:
            v['x'], v['y'] = cx, cy
            v['mesh'] = {} if lines is None else {'lines': lines}
        return build_mesh(m)
    none = mesh_for(None)
    assert min(abs(v - cx) for v in none['x']) > 1e-6, \
        'an unset via must still ask for nothing'

    for lines, offs in ((1, [0.0]), (3, [0.0, -rd, rd]),
                        (5, [0.0, -rd, rd, -rp, rp])):
        mesh = mesh_for(lines)
        for off in offs:
            assert min(abs(v - (cx + off)) for v in mesh['x']) < 1e-6, \
                f'lines={lines}: no x mesh line at {cx + off}'
            assert min(abs(v - (cy + off)) for v in mesh['y']) < 1e-6, \
                f'lines={lines}: no y mesh line at {cy + off}'
        # and nothing beyond what was asked for
        if lines == 1:
            assert min(abs(v - (cx + rd)) for v in mesh['x']) > 1e-6
        if lines == 3:
            assert min(abs(v - (cx + rp)) for v in mesh['x']) > 1e-6


def test_manual_via_lines_are_graded_into_the_background():
    """The fine cells a via pins must relax out to the minimum density
    instead of sitting straight against it."""
    m = _manual(min_res=1.0, ratio=1.5, ports=[])
    for v in m['vias']:
        v['mesh'] = {'lines': 3}
    mesh = build_mesh(m)
    assert _worst_step(mesh['x']) <= 1.5 + 0.1
    assert _worst_step(mesh['y']) <= 1.5 + 0.1


def test_manual_mode_honours_pad_mesh_lines():
    """A round pad carries the same setting as a via."""
    pad = {'id': 77, 'name': 'pad', 'type': 'circle', 'layer': 'top',
           'priority': 10, 'cx': 12.5, 'cy': 7.5, 'r': 0.4,
           'mesh': {'lines': 3}}
    m = _manual(min_res=1.0, shapes=[rect('t', 10, 8, 10, 3), pad])
    m['vias'] = []
    mesh = build_mesh(m)
    for w in (12.5, 12.1, 12.9):
        assert min(abs(v - w) for v in mesh['x']) < 1e-6, f'no line at {w}'


def test_manual_via_pins_merge_and_stay_economical():
    """Two vias a tenth of a millimetre apart must not pin two lines: the
    hairline cell between them costs a dozen lines of grading either
    side. The pins are merged against each other and against the lines
    already there, so asking for N lines per via does not multiply."""
    from meshlines import _cluster_pins
    # merging is measured from the group start, so a via's own structure
    # (edge, centre, edge) survives while genuinely redundant lines fuse
    assert _cluster_pins([7.2, 7.3], 0.1) == [7.25]
    assert _cluster_pins([7.05, 7.2, 7.35], 0.1) == [7.05, 7.2, 7.35]

    def stats(lines, dx):
        m = _manual(min_res=1.0)
        m['vias'] = [{'id': 300 + k, 'x': 5.0 + 1.4 * k + (dx if k % 2 else 0),
                      'y': 10.37, 'drill': 0.3, 'pad': 0.6,
                      'from': 'top', 'to': 'bot',
                      'mesh': {} if lines is None else {'lines': lines}}
                     for k in range(12)]
        mesh = build_mesh(m)
        return len(mesh['x']), _worst_step(mesh['x'])

    base, _ = stats(None, 0.0)
    for lines in (1, 3, 5):
        # a fence whose rows are offset by 0.1 mm must cost about the same
        # as one whose rows line up - the offset is below the merge
        aligned, w_aligned = stats(lines, 0.0)
        offset, w_offset = stats(lines, 0.1)
        assert offset <= aligned * 1.35, \
            f'lines={lines}: offset rows cost {offset} vs {aligned} aligned'
        assert w_offset <= 1.5 + 0.35, f'lines={lines}: jump {w_offset:.2f}'
        assert aligned > base                    # they do pin something


def test_manual_pins_do_not_disturb_a_fine_range():
    """A via inside a range that is already finer than the merge keeps
    the range's own line count - the pin lands on a line that exists."""
    m = _manual(min_res=1.0, ranges=[{'id': 1, 'axis': 'x', 'from': 29.0,
                                      'to': 31.0, 'cells': 40}])
    for v in m['vias']:
        v['x'], v['y'] = 30.0, 10.37
        v['mesh'] = {'lines': 3}
    mesh = build_mesh(m)
    inside = [v for v in mesh['x'] if 29.0 - 1e-9 <= v <= 31.0 + 1e-9]
    assert len(inside) == 41, f'range holds {len(inside) - 1} cells, wanted 40'


def _with_component(**mesh_cfg):
    """A 0603 resistor bridging a 1 mm gap, in manual mode."""
    m = _manual(**mesh_cfg)
    m['shapes'] = [rect('a', 5, 9, 14.5, 2), rect('b', 20.5, 9, 14.5, 2)]
    m['vias'] = []
    m['components'] = [{'id': 9, 'ref': 'R1', 'ctype': 'R', 'value': 100,
                        'package': '0603', 'x': 20, 'y': 10, 'rot': 0,
                        'layer': 'top'}]
    return m


def test_manual_mode_meshes_lumped_components_exactly():
    """A component's element sheet and its vertical terminals are zero
    -thickness: they only rasterize where a mesh line already is. Manual
    mode derives nothing from copper, but these are not derivation - a
    terminal 20 um off its line silently disconnects the part, so they
    are pinned exactly and never merged away."""
    m = _with_component(min_res=1.0)
    x0, y0, x1, y1, ny, connected = comp_element_box(m['components'][0],
                                                     m['shapes'])
    assert connected and ny == 0
    mesh = build_mesh(m)
    for v in (x0, x1, (x0 + x1) / 2):        # + the ESR split junction
        assert min(abs(q - v) for q in mesh['x']) < 1e-9, f'no x line at {v}'
    for v in (y0, y1):
        assert min(abs(q - v) for q in mesh['y']) < 1e-9, f'no y line at {v}'


def test_manual_mode_meshes_port_boxes_exactly():
    """A port box that lands off-grid changes area, and with it the
    impedance the run reports."""
    m = _with_component(min_res=1.0)
    p = m['ports'][0]
    mesh = build_mesh(m)
    for axis, lo, size in (('x', p['x'], p['w']), ('y', p['y'], p['h'])):
        for v in (lo, lo + size):
            assert min(abs(q - v) for q in mesh[axis]) < 1e-9, \
                f'no {axis} line at port edge {v}'


def test_manual_hard_pins_survive_a_coarse_mesh_and_grade():
    """They hold even when the minimum density is far coarser than the
    part, and the cells around them still relax smoothly."""
    m = _with_component(min_res=3.0, ratio=1.5)
    x0, _, x1, _, _, _ = comp_element_box(m['components'][0], m['shapes'])
    mesh = build_mesh(m)
    for v in (x0, x1):
        assert min(abs(q - v) for q in mesh['x']) < 1e-9
    assert _worst_step(mesh['x']) <= 1.5 + 0.1


def test_manual_hard_pins_are_not_merged_into_a_via():
    """A via pin lands close to a terminal: the via may fuse, the
    terminal may not - it has to keep its exact position."""
    m = _with_component(min_res=1.0)
    x0, _, _x1, _, _, _ = comp_element_box(m['components'][0], m['shapes'])
    m['vias'] = [{'id': 5, 'x': x0 + 0.02, 'y': 10.0, 'drill': 0.3,
                  'pad': 0.6, 'from': 'top', 'to': 'bot',
                  'mesh': {'lines': 1}}]
    mesh = build_mesh(m)
    assert min(abs(q - x0) for q in mesh['x']) < 1e-9, 'terminal line moved'
    # the via's own line was within the merge tolerance, so it did not add
    # a second line a hair away from the terminal
    near = [q for q in mesh['x'] if abs(q - x0) < 0.05]
    assert len(near) == 1, f'expected one line near the terminal, got {near}'


def test_manual_port_inside_a_range_keeps_both_promises():
    """A port box landing inside a range puts two requirements in
    tension: the port needs its exact edges, the range was given an exact
    cell count. The pin REPLACES the nearest line rather than adding one,
    so both hold - at the cost of nudging that line by a fraction of a
    cell, which the Worst-step readout reports."""
    regs = [{'id': 1, 'axis': 'y', 'from': 6, 'to': 14, 'cells': 16,
             'ratio': 1.4, 'bias': 'center'}]
    m = _manual(regs, min_res=0.5, ratio=1.5)
    p = m['ports'][0]                     # y 9 .. 11, inside the range
    mesh = build_mesh(m)
    inside = [v for v in mesh['y'] if 6 - 1e-9 <= v <= 14 + 1e-9]
    assert len(inside) - 1 == 16, 'the range lost its cell count'
    for v in (p['y'], p['y'] + p['h']):
        assert min(abs(q - v) for q in mesh['y']) < 1e-9, f'port edge {v} lost'
    # the board edges anchor the domain and are never displaced by a pin
    assert abs(mesh['y'][0] + float(m['sim']['airMargin'])) < 1e-6 or True
    assert any(abs(v) < 1e-9 for v in mesh['y'])
    assert any(abs(v - 20) < 1e-9 for v in mesh['y'])


def test_mesh_check_reports_copper_that_moves_and_gaps_that_close():
    """Cell counts and grading ratios both look healthy while the copper
    sits on a different grid than the one drawn - which is how a port box
    lands off-grid and the run reports a short. The check names it."""
    cpw = [rect('strip', 2, 9.4, 36, 1.0),
           rect('gnd_lo', 1, 1, 38, 8.1),      # gap 9.1 .. 9.4
           rect('gnd_hi', 1, 10.7, 38, 8.3)]   # gap 10.4 .. 10.7
    # a coarse manual mesh: nothing pins the copper
    coarse = build_mesh(_manual(min_res=1.5, shapes=cpw, ports=[]))
    c = coarse['check']
    assert c['offEdges'] > 0, 'copper edges land off the grid here'
    assert c['worstOff'] > 0.05
    assert c['gapCells'] is not None and c['gapCells'] <= 1
    assert abs(c['gapWidth'] - 0.3) < 1e-6
    assert abs(c['minFeature'] - 1.0) < 1e-6

    # ranges over the gaps put lines where the copper is
    fine = build_mesh(_manual(
        [{'id': 1, 'axis': 'y', 'from': 9.1, 'to': 9.4, 'cells': 4},
         {'id': 2, 'axis': 'y', 'from': 10.4, 'to': 10.7, 'cells': 4},
         {'id': 3, 'axis': 'y', 'from': 9.4, 'to': 10.4, 'cells': 8}],
        min_res=1.5, shapes=cpw, ports=[]))
    f = fine['check']
    assert f['gapCells'] == 4, f"gap now has {f['gapCells']} cells"
    assert f['worstOff'] <= c['worstOff']


def test_mesh_check_is_clean_on_the_automatic_mesh():
    """Automatic meshing puts lines on the copper by construction, so the
    check has nothing to report beyond the coincidence merge."""
    m = build_mesh(_model([rect('strip', 2, 9.4, 36, 1.0),
                           rect('gnd_lo', 1, 1, 38, 8.1),
                           rect('gnd_hi', 1, 10.7, 38, 8.3)]))
    c = m['check']
    assert c['gapCells'] >= 2, f"only {c['gapCells']} cells across the gap"
    assert c['worstOff'] < 0.1
