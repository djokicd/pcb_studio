"""Flask endpoint tests via the test client (no octave, no live server)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server
from helpers import stackup, sim_settings, rect, lumped_port

TEST_PROJ = 'pytest_scratch_project'


def client():
    server.app.testing = True
    return server.app.test_client()


def model():
    return {
        'name': TEST_PROJ,
        'board': {'width': 40, 'height': 20},
        'stackup': stackup(),
        'shapes': [rect('tl', 5, 9, 30, 2)],
        'ports': [lumped_port(1, 5, 9, 0.5, 2, excite=True)],
        'sim': sim_settings(),
    }


def test_script_endpoint():
    r = client().post('/api/script', json=model())
    assert r.status_code == 200
    assert 'AddLumpedPort' in r.get_json()['script']


def test_script_endpoint_validation_error():
    bad = model()
    bad['ports'] = []
    r = client().post('/api/script', json=bad)
    assert r.status_code == 400
    assert 'port' in r.get_json()['error'].lower()


def test_mesh_endpoint():
    r = client().post('/api/mesh', json=model())
    assert r.status_code == 200
    m = r.get_json()
    assert m['cells'] == len(m['x']) * len(m['y']) * len(m['z'])


def test_gerber_import_endpoint():
    gbr = '%FSLAX36Y36*%\n%MOMM*%\n%ADD11R,1.5X0.8*%\nD11*\nX2000000Y2000000D03*\nM02*\n'
    r = client().post('/api/import/gerber', json={'content': gbr})
    assert r.status_code == 200
    assert len(r.get_json()['shapes']) == 1


def test_drill_import_endpoint():
    drl = 'M48\nMETRIC\nT1C0.300\n%\nT1\nX15000Y25000\nM30\n'
    r = client().post('/api/import/drill', json={'content': drl})
    assert r.status_code == 200
    assert r.get_json()['vias'] == [{'x': 15.0, 'y': 25.0, 'drill': 0.3}]


def test_project_save_load_delete_roundtrip():
    c = client()
    try:
        r = c.post('/api/projects/save', json={'name': TEST_PROJ, 'project': model()})
        assert r.status_code == 200
        assert r.get_json()['name'] == TEST_PROJ

        listing = c.get('/api/projects').get_json()['projects']
        assert any(p['name'] == TEST_PROJ for p in listing)

        got = c.get(f'/api/projects/{TEST_PROJ}').get_json()
        assert got['project']['board']['width'] == 40
        assert got['resultsId'] is None   # no results attached
    finally:
        c.delete(f'/api/projects/{TEST_PROJ}')
    assert c.get(f'/api/projects/{TEST_PROJ}').status_code == 404


def test_project_bad_name_rejected():
    r = client().post('/api/projects/save', json={'name': '../evil', 'project': model()})
    assert r.get_json().get('name') != '../evil'
    client().delete('/api/projects/evil')


def test_status_endpoint_shape():
    st = client().get('/api/status').get_json()
    for key in ('state', 'percent', 'samples', 'info', 'warnMsgs'):
        assert key in st
