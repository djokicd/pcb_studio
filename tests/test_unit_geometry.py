"""Unit tests for geometry helpers and mesh generation (no octave needed)."""
import sys
from pathlib import Path

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
        xs, ys, xsoft, ysoft, xreg, yreg, _xp, _yp = mesh_lines_xy(m, 0.4)
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
