"""Unit tests for the Gerber / Excellon parsers (no octave needed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gerber import parse_gerber, parse_excellon, GerberError

GERBER = """%FSLAX36Y36*%
%MOMM*%
%ADD10C,0.25*%
%ADD11R,1.5X0.8*%
%ADD12O,2.0X1.0*%
G01*
D10*
X2000000Y2000000D02*
X10000000Y2000000D01*
D11*
X12000000Y6000000D03*
D12*
X14000000Y2000000D03*
G36*
X16000000Y2000000D02*
X20000000Y2000000D01*
X20000000Y5000000D01*
X16000000Y5000000D01*
X16000000Y2000000D01*
G37*
M02*
"""


def test_gerber_basic():
    r = parse_gerber(GERBER)
    types = sorted(s['type'] for s in r['shapes'])
    assert types.count('rect') == 1          # R flash
    assert types.count('trace') == 1         # stroked draw -> centerline
    assert types.count('poly') >= 2          # obround flash, region
    tr = next(s for s in r['shapes'] if s['type'] == 'trace')
    assert tr['pts'] == [[2.0, 2.0], [10.0, 2.0]] and tr['width'] == 0.25
    x0, y0, x1, y1 = r['bbox']
    assert x0 < 2 and 19.9 < x1 <= 20.1
    assert not r['warnings']


def test_gerber_rect_flash_geometry():
    r = parse_gerber(GERBER)
    rects = [s for s in r['shapes'] if s['type'] == 'rect']
    assert rects[0]['w'] == 1.5 and rects[0]['h'] == 0.8
    assert abs(rects[0]['x'] - (12 - 0.75)) < 1e-6


def test_gerber_arc_stroke():
    src = GERBER.replace('M02*', 'G75*\nG03*\nD10*\nX4000000Y8000000D02*\nX8000000Y8000000I2000000J0D01*\nM02*')
    r = parse_gerber(src)
    # the CCW half-circle chains into ONE trace whose centerline samples
    # the arc: points stay on the r=2 circle around (6, 8) within the
    # 10 um decimation tolerance
    import math
    traces = [s for s in r['shapes'] if s['type'] == 'trace']
    arc = next(t for t in traces if len(t['pts']) > 2)
    assert arc['pts'][0] == [4.0, 8.0] and arc['pts'][-1] == [8.0, 8.0]
    assert len(arc['pts']) >= 6
    for x, y in arc['pts']:
        assert abs(math.hypot(x - 6, y - 8) - 2.0) < 0.02


def test_gerber_inch_units():
    src = GERBER.replace('%MOMM*%', '%MOIN*%')
    r = parse_gerber(src)
    assert r['bbox'][2] > 400   # 20 file units are now inches -> mm


def test_gerber_lpc_warning():
    # only the region block is clear-polarity; earlier primitives stay dark
    src = GERBER.replace('G36*', '%LPC*%\nG36*')
    r = parse_gerber(src)
    assert any('clear-polarity' in w for w in r['warnings'])
    assert len(r['shapes']) >= 3   # dark primitives survived


def test_gerber_empty_rejected():
    with pytest.raises(GerberError):
        parse_gerber('%FSLAX36Y36*%\n%MOMM*%\nM02*\n')


DRILL = """M48
METRIC
T1C0.300
T2C0.600
%
T1
X15000Y25000
X20000Y25000
T2
X30.5Y12.25
M30
"""


def test_excellon_metric():
    d = parse_excellon(DRILL)
    assert [v['drill'] for v in d['vias']] == [0.3, 0.3, 0.6]
    assert d['vias'][0]['x'] == 15.0        # fixed 3.3 format
    assert d['vias'][2]['x'] == 30.5        # decimal format


def test_excellon_inch():
    src = DRILL.replace('METRIC', 'INCH').replace('X30.5Y12.25', 'X1.0Y0.5')
    d = parse_excellon(src)
    assert abs(d['vias'][2]['x'] - 25.4) < 1e-6
    assert abs(d['vias'][0]['x'] - 15000 / 10000 * 25.4) < 1e-3


def test_excellon_empty_rejected():
    with pytest.raises(GerberError):
        parse_excellon('M48\nMETRIC\n%\nM30\n')


# ---------------- fabrication export ----------------

def _model_for_export():
    return {
        'name': 'export_check',
        'board': {'width': 20, 'height': 10},
        'stackup': [
            {'id': 'top', 'name': 'Top Cu', 'type': 'conductor', 'thickness': 0.035, 'fill': False},
            {'id': 'core', 'name': 'core', 'type': 'dielectric', 'thickness': 0.8, 'er': 4.3},
            {'id': 'bot', 'name': 'Bottom', 'type': 'conductor', 'thickness': 0.035, 'fill': True},
        ],
        'shapes': [
            {'id': 1, 'name': 't', 'type': 'rect', 'layer': 'top',
             'x': 2, 'y': 4, 'w': 10, 'h': 1.5},
            {'id': 2, 'name': 'l', 'type': 'trace', 'layer': 'top',
             'pts': [[3, 7], [9, 7]], 'width': 1.0, 'radius': 0},
        ],
        'vias': [{'id': 3, 'x': 5, 'y': 5, 'drill': 0.6, 'pad': 1.2,
                  'from': 'bot', 'to': 'top'}],
    }


def test_export_round_trips_through_own_parsers():
    """The generated RS-274X and Excellon files must re-import through
    parse_gerber / parse_excellon with the original geometry."""
    from gerber import export_fabrication, parse_excellon
    files = export_fabrication(_model_for_export())
    assert set(files) == {'Top_Cu.gbr', 'Bottom.gbr', 'outline.gbr', 'drill.drl'}

    r = parse_gerber(files['Top_Cu.gbr'])
    assert not r['warnings']
    # two regions (rect + stroked trace) and one via pad flash
    polys = [s for s in r['shapes'] if s['type'] == 'poly']
    circles = [s for s in r['shapes'] if s['type'] == 'circle']
    assert len(polys) == 2 and len(circles) == 1
    xs = [p[0] for p in polys[0]['pts']]
    ys = [p[1] for p in polys[0]['pts']]
    assert min(xs) == 2 and max(xs) == 12 and min(ys) == 4 and max(ys) == 5.5
    assert circles[0]['cx'] == 5 and abs(circles[0]['r'] - 0.6) < 1e-9

    # plane layer: full-board region plus the via pad
    rb = parse_gerber(files['Bottom.gbr'])
    xs = [p[0] for s in rb['shapes'] if s['type'] == 'poly' for p in s['pts']]
    assert min(xs) == 0 and max(xs) == 20

    rd = parse_excellon(files['drill.drl'])
    assert not rd['warnings']
    assert len(rd['vias']) == 1
    v = rd['vias'][0]
    assert v['x'] == 5 and v['y'] == 5 and v['drill'] == 0.6


def test_export_via_pads_only_on_crossed_layers():
    """A blind via between mid layers must not flash pads elsewhere."""
    from gerber import export_fabrication
    m = _model_for_export()
    m['stackup'].insert(2, {'id': 'mid', 'name': 'Mid', 'type': 'conductor',
                            'thickness': 0.035, 'fill': False})
    m['stackup'].insert(3, {'id': 'core2', 'name': 'core2', 'type': 'dielectric',
                            'thickness': 0.5, 'er': 4.3})
    m['vias'][0]['from'] = 'bot'
    m['vias'][0]['to'] = 'mid'
    files = export_fabrication(m)
    assert 'D03*' not in files['Top_Cu.gbr']      # top not crossed
    assert 'D03*' in files['Mid.gbr']
    assert 'D03*' in files['Bottom.gbr']


AM_HEAD = "%FSLAX46Y46*%\n%MOMM*%\n"


def test_macro_roundrect_pad_flash():
    """KiCad-style RoundRect pad macros (the default SMD pad) import as
    one rounded-rectangle polygon per flash."""
    import math
    src = (AM_HEAD
           + "%AMRoundRect*\n0 comment*\n"
           + "4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*\n"
           + "1,1,$1+$1,$2,$3*\n1,1,$1+$1,$4,$5*\n1,1,$1+$1,$6,$7*\n1,1,$1+$1,$8,$9*\n"
           + "20,1,$1+$1,$2,$3,$4,$5,0*\n20,1,$1+$1,$4,$5,$6,$7,0*\n"
           + "20,1,$1+$1,$6,$7,$8,$9,0*\n20,1,$1+$1,$8,$9,$2,$3,0*%\n"
           + "%ADD14RoundRect,0.135X-0.615X-0.365X0.615X-0.365X0.615X0.365X-0.615X0.365X0*%\n"
           + "D14*\nX10000000Y5000000D03*\nM02*\n")
    r = parse_gerber(src)
    assert not r['warnings']
    assert len(r['shapes']) == 1
    s = r['shapes'][0]
    assert s['type'] == 'poly'
    xs = [p[0] for p in s['pts']]
    ys = [p[1] for p in s['pts']]
    assert abs(min(xs) - 9.25) < 1e-3 and abs(max(xs) - 10.75) < 1e-3
    assert abs(min(ys) - 4.5) < 1e-3 and abs(max(ys) - 5.5) < 1e-3
    # corners are rounded: no vertex reaches the sharp corner
    d = min(math.hypot(px - 10.75, py - 5.5) for px, py in s['pts'])
    assert 0.03 < d < 0.09


def test_macro_assignment_and_rotated_rect():
    """$n=expr assignments and the rotation argument work; a single
    primitive is emitted without hull merging."""
    src = (AM_HEAD
           + "%AMROT*\n$4=$3x1*\n21,1,$1,$2,0,0,$4*%\n"
           + "%ADD15ROT,2.0X1.0X90*%\nD15*\nX0Y0D03*\nM02*\n")
    r = parse_gerber(src)
    assert not r['warnings']
    s = r['shapes'][0]
    assert len(s['pts']) == 4
    xs = [p[0] for p in s['pts']]
    ys = [p[1] for p in s['pts']]
    # the 2.0 x 1.0 rect is rotated by $4 = $3 = 90 deg -> bbox swaps
    assert abs(max(xs) - min(xs) - 1.0) < 1e-6
    assert abs(max(ys) - min(ys) - 2.0) < 1e-6


def test_macro_exposure_off_and_unknown_primitive_warn():
    src = (AM_HEAD
           + "%AMCUT*\n21,1,2,1,0,0,0*\n21,0,1,0.5,0,0,0*\n7,0,0,1,0.8,0.1,45*%\n"
           + "%ADD16CUT*%\nD16*\nX0Y0D03*\nM02*\n")
    r = parse_gerber(src)
    assert any('exposure-off' in w for w in r['warnings'])
    assert any('thermal' in w for w in r['warnings'])
    assert len(r['shapes']) == 1          # the exposed rect still lands


def test_macro_concave_outline_not_hulled():
    """A single outline primitive keeps its exact (possibly concave)
    shape - only multi-primitive flashes are hull-merged."""
    src = (AM_HEAD
           + "%AMLSHAPE*\n4,1,6,0,0,2,0,2,1,1,1,1,2,0,2,0,0,0*%\n"
           + "%ADD17LSHAPE*%\nD17*\nX0Y0D03*\nM02*\n")
    r = parse_gerber(src)
    assert not r['warnings']
    s = r['shapes'][0]
    assert len(s['pts']) == 6
    assert [1.0, 1.0] in [[round(a, 4), round(b, 4)] for a, b in s['pts']]


def test_unknown_aperture_still_warns():
    src = AM_HEAD + "%ADD18WEIRD,1.0*%\nD18*\nX0Y0D03*\nM02*\n%ADD10C,0.2*%\nD10*\nX0Y0D03*\n"
    r = parse_gerber(src)
    assert any('unsupported aperture' in w for w in r['warnings'])
