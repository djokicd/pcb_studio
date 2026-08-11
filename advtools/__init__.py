"""Advanced tools: self-contained design utilities that live beside the
PCB editor without being part of it.

A tool is a module in `advtools/tools/` exposing

    TOOL    = {'id', 'name', 'description', 'group'?, 'icon'?}
    schema()          -> declarative description of its controls
    handle(action, payload) -> dict           (the tool's actions)

The registry below discovers them, and the server exposes exactly two
endpoints for all of them (`/api/tools`, `/api/tools/<id>/<action>`), so
adding a tool means dropping in one module - no server or menu edits.

The controls and the result panels are declarative on purpose: the
browser side owns a small set of widget and panel renderers (number,
select, range, gamma, ... / chart, smith, schematic, table, text), so a
new tool describes what it wants rather than shipping its own UI.
"""
import importlib
import pkgutil

_CACHE = {}


def _discover():
    if _CACHE:
        return _CACHE
    from . import tools as _pkg
    for mod in pkgutil.iter_modules(_pkg.__path__):
        if mod.name.startswith('_'):
            continue
        try:
            m = importlib.import_module(f'{_pkg.__name__}.{mod.name}')
        except Exception as e:                      # a broken tool must not
            print(f'[advtools] skipping {mod.name}: {e}')   # take the app down
            continue
        meta = getattr(m, 'TOOL', None)
        if not meta or not meta.get('id'):
            continue
        _CACHE[meta['id']] = (meta, m)
    return _CACHE


def list_tools():
    """Metadata for every registered tool, for the menu."""
    out = []
    for meta, _m in _discover().values():
        out.append({'id': meta['id'], 'name': meta.get('name', meta['id']),
                    'description': meta.get('description', ''),
                    'group': meta.get('group', 'Tools'),
                    'icon': meta.get('icon', '')})
    return sorted(out, key=lambda t: (t['group'], t['name']))


def get_tool(tool_id):
    entry = _discover().get(tool_id)
    if not entry:
        raise KeyError(f'unknown tool {tool_id!r}')
    return entry[1]


def dispatch(tool_id, action, payload):
    """Run one action of one tool. 'schema' is handled for every tool."""
    mod = get_tool(tool_id)
    if action == 'schema':
        return mod.schema() if hasattr(mod, 'schema') else {}
    handler = getattr(mod, 'handle', None)
    if handler is None:
        raise KeyError(f'tool {tool_id!r} has no actions')
    return handler(action, payload or {})
