"""Unit tests for the Touchstone parser and S-parameter network folding."""
import cmath
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from touchstone import (parse_touchstone, interpolate, connect,
                        TouchstoneError, _solve, _matmul)


S2P_RI = """! test file
# GHz S RI R 50
1.0  0.1 0.0   0.9 0.0   0.02 0.0   0.15 0.0
2.0  0.2 0.0   0.8 0.1   0.03 0.0   0.25 0.0
"""


def test_parse_ri_2port_column_order():
    ts = parse_touchstone(S2P_RI)
    assert ts['nports'] == 2
    assert ts['r'] == 50
    assert ts['freq'] == [1e9, 2e9]
    s = ts['s'][0]
    # 2-port files are S11 S21 S12 S22
    assert s[0][0] == complex(0.1, 0)
    assert s[1][0] == complex(0.9, 0)
    assert s[0][1] == complex(0.02, 0)
    assert s[1][1] == complex(0.15, 0)


def test_parse_ma_and_db():
    ma = "# MHz S MA R 75\n1000 0.5 90 0.5 0 0.5 0 0.5 -90\n"
    ts = parse_touchstone(ma)
    assert ts['r'] == 75
    assert ts['freq'] == [1e9]
    assert abs(ts['s'][0][0][0] - complex(0, 0.5)) < 1e-9
    dbf = "# GHz S DB\n1 -6.0206 0 0 0 0 0 -6.0206 0\n"
    ts2 = parse_touchstone(dbf)
    assert abs(abs(ts2['s'][0][0][0]) - 0.5) < 1e-3


def test_parse_3port_row_major_multiline():
    # 3-port: rows may wrap over lines; row-major order
    vals = ' '.join(f'{0.01 * k} 0' for k in range(9))
    ts = parse_touchstone(f"# GHz S RI\n1 {vals}\n")
    assert ts['nports'] == 3
    assert ts['s'][0][0][0] == complex(0.0, 0)
    assert ts['s'][0][0][1] == complex(0.01, 0)
    assert ts['s'][0][2][2] == complex(0.08, 0)


def test_parse_rejects_garbage():
    with pytest.raises(TouchstoneError):
        parse_touchstone('# GHz S RI\nhello world\n')
    with pytest.raises(TouchstoneError):
        parse_touchstone('# GHz Z RI\n1 0 0\n')


def test_interpolate_and_clamp():
    ts = parse_touchstone(S2P_RI)
    mats, clamped = interpolate(ts, [1.5e9])
    assert not clamped
    assert abs(mats[0][0][0] - complex(0.15, 0)) < 1e-9
    _, clamped2 = interpolate(ts, [0.5e9, 3e9])
    assert clamped2


def test_solve_matmul():
    A = [[complex(2, 0), 0j], [0j, complex(4, 0)]]
    B = [[complex(2, 2)], [complex(8, 0)]]
    X = _solve(A, B)
    assert abs(X[0][0] - complex(1, 1)) < 1e-12
    assert abs(X[1][0] - complex(2, 0)) < 1e-12
    C = _matmul([[1, 2], [3, 4]], [[5, 6], [7, 8]])
    assert C == [[19, 22], [43, 50]]


def test_connect_load_through_line():
    """Load Γ seen through an ideal line with transmission t: S11 = t²Γ."""
    t = cmath.rect(1.0, math.radians(-30))
    gamma = complex(0.4, 0.1)
    board = [[0j, t], [t, 0j]]
    out = connect(board, [[gamma]], [1])
    assert len(out) == 1
    assert abs(out[0][0] - t * t * gamma) < 1e-12


def test_connect_ideal_through_device_bridges_lines():
    """Two separate ideal lines joined by an ideal through 2-port device
    must give an ideal through between the outer ports."""
    thr = [[0j, 1 + 0j], [1 + 0j, 0j]]
    board = [[0j] * 4 for _ in range(4)]
    board[0][1] = board[1][0] = 1 + 0j   # line A: ports 1-2
    board[2][3] = board[3][2] = 1 + 0j   # line B: ports 3-4
    out = connect(board, thr, [1, 2])    # device across board ports 2 and 3
    assert abs(out[0][1] - 1) < 1e-12
    assert abs(out[1][0] - 1) < 1e-12
    assert abs(out[0][0]) < 1e-12


def test_connect_attenuator_device():
    g = 0.5
    pad = [[0j, g + 0j], [g + 0j, 0j]]
    board = [[0j] * 4 for _ in range(4)]
    board[0][1] = board[1][0] = 1 + 0j
    board[2][3] = board[3][2] = 1 + 0j
    out = connect(board, pad, [1, 2])
    assert abs(out[1][0] - g) < 1e-12


def test_connect_reflective_feedback():
    """Mismatched device on a mismatched board port: multiple reflections
    sum to the analytic series g = Scc, d = Sd: X = Sce/(1-Scc*Sd)."""
    scc = complex(0.3, 0)
    sd = complex(0.5, 0)
    t = complex(0.8, 0)
    board = [[0j, t], [t, scc]]
    out = connect(board, [[sd]], [1])
    expected = t * sd / (1 - scc * sd) * t
    assert abs(out[0][0] - expected) < 1e-12


def test_header_info_captured():
    """Leading '!' comments (bias point etc.) are exposed as `info`; the
    real vendor files in devices/ carry their bias condition there."""
    ts = parse_touchstone(
        '! Part: XYZ\n! Bias condition: Vce=1V, Ic=1mA\n'
        '# GHz S RI R 50\n1 0 0\n! trailing comment\n2 0 0\n', 1)
    assert 'Bias condition: Vce=1V, Ic=1mA' in ts['info']
    assert all('trailing' not in l for l in ts['info'])

    from pathlib import Path
    dev = Path(__file__).resolve().parent.parent / 'devices' / 'BFG25AWA.S2P'
    if dev.is_file():
        ts = parse_touchstone(dev.read_text(), 2)
        assert any('Vce=1V, Ic=0.1mA' in l for l in ts['info'])
