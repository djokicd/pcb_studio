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


def test_simplify_endpoint():
    from gerber import _stadium
    import math
    m = model()
    strokes = []
    prev = None
    sid = 500
    for k in range(9):
        a = math.radians(90 * k / 8)
        p = (20 + 6 * math.cos(a), 4 + 6 * math.sin(a))
        if prev is not None:
            pts = _stadium(prev[0], prev[1], p[0], p[1], 0.8)
            strokes.append({'id': sid, 'name': f'seg{sid}', 'type': 'poly',
                            'layer': 'top', 'priority': 10, 'meshBbox': True,
                            'mesh': {}, 'pts': [[round(x, 4), round(y, 4)]
                                                for x, y in pts]})
            sid += 1
        prev = p
    m['shapes'] += strokes
    r = client().post('/api/simplify', json={'model': m, 'opts': {'tol': 0.02}})
    assert r.status_code == 200
    data = r.get_json()
    assert data['stats']['traces'] == 1
    assert data['stats']['strokesMerged'] == 8
    assert data['meshBefore']['cells'] > 0 and data['meshAfter']['cells'] > 0
    types = [s['type'] for s in data['shapes']]
    assert types.count('trace') == 1 and types.count('rect') == 1


def test_simplify_endpoint_bad_opts():
    r = client().post('/api/simplify',
                      json={'model': model(), 'opts': {'tol': 'garbage'}})
    assert r.status_code == 400


def test_tools_endpoint_lists_tools():
    c = client()
    r = c.get('/api/tools')
    assert r.status_code == 200
    ids = [t['id'] for t in r.get_json()['tools']]
    assert 'rf_chain' in ids


def test_tool_schema_and_action():
    c = client()
    r = c.post('/api/tools/rf_chain/schema', json={})
    assert r.status_code == 200 and r.get_json()['fields']
    r = c.post('/api/tools/rf_chain/analyse',
                    json={'device': 'BFG25AWJ.S2P', 'f0': 1e9,
                          'gs_mag': 0.3, 'gs_ang': 90,
                          'gl_mag': 0.3, 'gl_ang': 40})
    assert r.status_code == 200
    assert 'series' in r.get_json() and 'schematic' in r.get_json()


def test_unknown_tool_is_404():
    assert client().post('/api/tools/nope/schema', json={}).status_code == 404


def test_bad_tool_input_is_400_not_500():
    r = client().post('/api/tools/rf_chain/analyse',
                      json={'device': 'does_not_exist.s2p'})
    assert r.status_code == 400
    assert 'error' in r.get_json()
