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
    assert types.count('poly') >= 3          # stroke, obround, region
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
    # the CCW half-circle is sampled into many stadium polygons
    assert len(r['shapes']) > 10


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
