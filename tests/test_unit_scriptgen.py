"""Unit tests for the Octave script generator (no octave needed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriptgen import generate_script, ValidationError
from helpers import stackup, sim_settings, rect, lumped_port, msl_port


def base_model(**over):
    m = {
        'board': {'width': 40, 'height': 20},
        'stackup': stackup(),
        'shapes': [rect('tl', 5, 9, 30, 2)],
        'ports': [lumped_port(1, 5, 9, 0.5, 2, excite=True),
                  lumped_port(2, 34.5, 9, 0.5, 2)],
        'sim': sim_settings(),
    }
    m.update(over)
    return m


def test_valid_model_generates():
    s = generate_script(base_model())
    assert 'AddLumpedPort' in s
    assert 'GUI_MARKER: done' in s


def test_mesh_defined_before_ports():
    s = generate_script(base_model())
    assert s.index('DefineRectGrid') < s.index('AddLumpedPort')


def test_no_ports_rejected():
    with pytest.raises(ValidationError):
        generate_script(base_model(ports=[]))


def test_exactly_one_excited_port():
    m = base_model()
    m['ports'][1]['excite'] = True
    with pytest.raises(ValidationError, match='xcited'):
        generate_script(m)


def test_msl_port_needs_absorbing_boundary():
    m = base_model(ports=[msl_port(1, 5, 9, 2, 2, '+x', excite=True)])
    m['sim']['boundary'] = 'PEC'
    with pytest.raises(ValidationError, match='absorbing'):
        generate_script(m)


def test_msl_port_script_content():
    m = base_model(ports=[
        msl_port(1, 5, 9, 2, 2, '+x', excite=True),
        msl_port(2, 33, 9, 2, 2, '-x'),
    ])
    m['sim']['boundary'] = 'PML_8'
    s = generate_script(m)
    assert s.count('AddMSLPort') == 2
    assert "'ExcitePort', true" in s
    assert 'MeasPlaneShift' in s
    assert "'RefImpedance', 50" in s
    # launch corridor: the substrate box must extend beyond the board on
    # both MSL sides (board is 0..40 in x)
    diel_line = next(l for l in s.splitlines() if "AddBox(CSX, 'diel_core'" in l)
    assert '[-' in diel_line, diel_line


def test_component_value_conversion():
    m = base_model(components=[{
        'id': 9, 'ref': 'C1', 'ctype': 'C', 'value': 10, 'package': '0603',
        'x': 20, 'y': 10, 'rot': 0, 'layer': 'top'}])
    s = generate_script(m)
    assert "'C', 1e-11" in s   # 10 pF


def test_dump_freqs_all():
    m = base_model()
    m['sim'].update(dumpJ=True, dumpFreqs='all')
    s = generate_script(m)
    line = next(l for l in s.splitlines() if l.startswith('jfreqs'))
    assert line.count('e+09') + line.count('e+08') >= 21


def test_bad_dump_freq_rejected():
    m = base_model()
    m['sim'].update(dumpJ=True, dumpFreqs='banana')
    with pytest.raises(ValidationError):
        generate_script(m)


def test_capacitor_gets_series_esr_by_default():
    """An ideal lossless C forms an undamped tank with its mounting
    parasitics; the generated element carries a series ESR half."""
    m = base_model(components=[{
        'id': 9, 'ref': 'C1', 'ctype': 'C', 'value': 47, 'package': '0603',
        'x': 20, 'y': 10, 'rot': 0, 'layer': 'top'}])
    s = generate_script(m)
    assert "AddLumpedElement(CSX, 'C1', 0, 'Caps', 1, 'C', 4.7e-11)" in s
    assert "AddLumpedElement(CSX, 'C1_esr', 0, 'Caps', 1, 'R', 0.25)" in s
    # the two element boxes split the gap at its centre and abut there
    lines = [l for l in s.splitlines() if "AddBox(CSX, 'C1" in l]
    assert len(lines) == 2
    import re as _re

    def boxes(line):
        return [[float(v) for v in g.split()]
                for g in _re.findall(r'\[([^\]]+)\]', line)]

    (c_start, c_stop), (r_start, r_stop) = boxes(lines[0]), boxes(lines[1])
    assert c_stop[0] == r_start[0]              # C ends where ESR begins
    assert c_start[1] == r_start[1] and c_stop[1] == r_stop[1]


def test_esr_zero_restores_ideal_element():
    m = base_model(components=[{
        'id': 9, 'ref': 'C1', 'ctype': 'C', 'value': 47, 'package': '0603',
        'x': 20, 'y': 10, 'rot': 0, 'layer': 'top', 'esr': 0}])
    s = generate_script(m)
    assert "'C', 4.7e-11" in s
    assert '_esr' not in s


def test_esr_explicit_and_rotated():
    m = base_model(components=[{
        'id': 9, 'ref': 'L1', 'ctype': 'L', 'value': 10, 'package': '0603',
        'x': 20, 'y': 10, 'rot': 90, 'layer': 'top', 'esr': 1.5}])
    s = generate_script(m)
    assert "AddLumpedElement(CSX, 'L1_esr', 1, 'Caps', 1, 'R', 1.5)" in s


def test_negative_esr_rejected():
    m = base_model(components=[{
        'id': 9, 'ref': 'C1', 'ctype': 'C', 'value': 47, 'package': '0603',
        'x': 20, 'y': 10, 'rot': 0, 'layer': 'top', 'esr': -1}])
    with pytest.raises(ValidationError):
        generate_script(m)
