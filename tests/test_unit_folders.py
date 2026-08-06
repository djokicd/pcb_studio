"""Unit tests for the project folder overlay (multi-level, named, tagged)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'PROJ_ROOT', tmp_path)
    monkeypatch.setattr(server, 'FOLDERS_FILE', tmp_path / 'folders.json')
    (tmp_path / 'projA').mkdir()
    (tmp_path / 'projA' / 'project.json').write_text('{"name": "projA"}')


def test_folder_tree_lifecycle(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with server.app.test_client() as c:
        top = c.post('/api/folders', json={'name': 'RF', 'tags': ['filters', ' wip ', 'filters']}).get_json()
        assert top['parent'] is None
        assert top['tags'] == ['filters', 'wip']          # trimmed, deduped
        sub = c.post('/api/folders', json={'name': 'Edge-coupled', 'parent': top['id']}).get_json()
        assert sub['parent'] == top['id']

        # assign a project into the subfolder
        r = c.post('/api/folders/assign', json={'project': 'projA', 'folder': sub['id']})
        assert r.status_code == 200
        d = c.get('/api/folders').get_json()
        assert d['assign']['projA'] == sub['id']
        assert len(d['folders']) == 2

        # a folder cannot be moved into its own subtree
        r = c.post(f'/api/folders/{top["id"]}', json={'parent': sub['id']})
        assert r.status_code == 400

        # rename + retag
        r = c.post(f'/api/folders/{sub["id"]}', json={'name': 'Coupled', 'tags': ['x']}).get_json()
        assert r['name'] == 'Coupled' and r['tags'] == ['x']

        # deleting the subfolder moves its project up to the parent
        c.delete(f'/api/folders/{sub["id"]}')
        d = c.get('/api/folders').get_json()
        assert d['assign']['projA'] == top['id']
        assert [f['id'] for f in d['folders']] == [top['id']]

        # deleting the top folder moves the project to the root
        c.delete(f'/api/folders/{top["id"]}')
        d = c.get('/api/folders').get_json()
        assert d['assign'] == {} and d['folders'] == []


def test_folder_assign_validation(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with server.app.test_client() as c:
        assert c.post('/api/folders/assign',
                      json={'project': 'nope', 'folder': None}).status_code == 404
        assert c.post('/api/folders/assign',
                      json={'project': 'projA', 'folder': 'f_missing'}).status_code == 404
        assert c.post('/api/folders', json={'name': '  '}).status_code == 400
        # assignments to vanished projects are filtered from the listing
        f = c.post('/api/folders', json={'name': 'Keep'}).get_json()
        c.post('/api/folders/assign', json={'project': 'projA', 'folder': f['id']})
        import shutil
        shutil.rmtree(tmp_path / 'projA')
        assert c.get('/api/folders').get_json()['assign'] == {}
