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
