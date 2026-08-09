"""Generate openEMS Octave scripts from the GUI's project model (v2).

Model overview (mm / GHz):
  board:    {width, height}
  stackup:  top->bottom list of layers:
            {id, name, type: 'conductor'|'dielectric', thickness,
             er, tand (dielectric), fill (conductor: full copper plane)}
  shapes:   rect/circle/segment/arc/poly on conductor layers (see geometry.py)
  vias:     cylindrical barrels + pads between two conductor layers
  components: lumped R/L/C elements (0402/0603/0805/custom footprints)
  ports:    lumped ports; direction z spans layerFrom->layerTo, x/y are
            in-plane sheet ports on `layer`
  sim:      sweep, boundaries, mesh and dump settings

The mesh is fully computed in Python (meshlines.py) and emitted as explicit
line vectors, so the GUI mesh preview matches the simulation exactly.
"""
import math
import os
import re
from pathlib import Path

from geometry import (PACKAGES, sim_shapes, stackup_z, shape_outline, comp_body,
                      comp_element_box, circle_points)
from meshlines import build_mesh

C0 = 299792458.0
EPS0 = 8.8541878128e-12

# default series resistance of lumped elements (ohms), used when the
# component has no explicit ESR. Typical for 0402-0805 MLCC / chip
# inductors at RF; more importantly, a nonzero ESR damps the resonator
# every ideal C or L forms with its mounting-loop parasitics - lossless
# elements trap the excitation's residual energy and the FDTD energy
# decay plateaus instead of reaching the end criteria.
DEFAULT_ESR = {'C': 0.25, 'L': 0.3}


class ValidationError(Exception):
    pass


def find_matlab_paths():
    candidates = []
    env = os.environ.get('OPENEMS_MATLAB_ROOT')
    if env:
        candidates.append(env)
    candidates += [os.path.expanduser('~/opt/openEMS/share'), '/usr/local/share', '/usr/share']
    for cand in candidates:
        root = Path(cand)
        oem, csx = root / 'openEMS' / 'matlab', root / 'CSXCAD' / 'matlab'
        if oem.is_dir() and csx.is_dir():
            return [str(oem), str(csx)]
    return []


def _fmt(v):
    return f'{float(v):.9g}'


def _vec(vals):
    return '[' + ' '.join(_fmt(v) for v in vals) + ']'


def _ident(s):
    return re.sub(r'[^A-Za-z0-9]', '_', str(s)) or 'x'


def _num(x, name, positive=False):
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ValidationError(f'{name} is not a number')
    if positive and v <= 0:
        raise ValidationError(f'{name} must be > 0')
    return v


def validate(model):
    board = model.get('board') or {}
    _num(board.get('width'), 'board.width', positive=True)
    _num(board.get('height'), 'board.height', positive=True)

    stackup = model.get('stackup') or []
    if not stackup:
        raise ValidationError('Stackup is empty')
    ids = [l.get('id') for l in stackup]
    if len(set(ids)) != len(ids):
        raise ValidationError('Stackup layer ids are not unique')
    conductors = {l['id'] for l in stackup if l.get('type') == 'conductor'}
    if not conductors:
        raise ValidationError('Stackup needs at least one conductor layer')
    if not any(l.get('type') == 'dielectric' for l in stackup):
        raise ValidationError('Stackup needs at least one dielectric layer')
    for l in stackup:
        if l.get('type') == 'dielectric':
            _num(l.get('thickness'), f'layer {l.get("name", l["id"])} thickness', positive=True)
            _num(l.get('er'), f'layer {l.get("name", l["id"])} er', positive=True)

    sim = model.get('sim') or {}
    fstart = _num(sim.get('fStart'), 'sim.fStart', positive=True)
    fstop = _num(sim.get('fStop'), 'sim.fStop', positive=True)
    if fstop <= fstart:
        raise ValidationError('sim.fStop must be greater than sim.fStart')

    for i, s in enumerate(sim_shapes(model)):
        name = s.get('name') or f'shape {i + 1}'
        if s.get('layer') not in conductors:
            raise ValidationError(f'{name}: not on a conductor layer')
        try:
            pts = shape_outline(s)
        except (KeyError, ValueError, TypeError) as e:
            raise ValidationError(f'{name}: bad geometry ({e})')
        if len(pts) < 3:
            raise ValidationError(f'{name}: needs at least 3 points')

    for i, v in enumerate(model.get('vias') or []):
        _num(v.get('drill'), f'via {i + 1} drill', positive=True)
        _num(v.get('pad'), f'via {i + 1} pad', positive=True)
        if float(v['pad']) < float(v['drill']):
            raise ValidationError(f'via {i + 1}: pad diameter smaller than drill')
        if v.get('from') not in conductors or v.get('to') not in conductors:
            raise ValidationError(f'via {i + 1}: from/to must be conductor layers')
        if v.get('from') == v.get('to'):
            raise ValidationError(f'via {i + 1}: from and to are the same layer')

    for i, c in enumerate(model.get('components') or []):
        ref = c.get('ref') or f'component {i + 1}'
        if c.get('ctype') not in ('R', 'L', 'C'):
            raise ValidationError(f'{ref}: type must be R, L or C')
        _num(c.get('value'), f'{ref} value', positive=True)
        if c.get('layer') not in conductors:
            raise ValidationError(f'{ref}: not on a conductor layer')
        if c.get('package') not in list(PACKAGES) + ['custom']:
            raise ValidationError(f'{ref}: unknown package')
        if c.get('package') == 'custom':
            _num(c.get('len'), f'{ref} length', positive=True)
            _num(c.get('wid'), f'{ref} width', positive=True)
        if c.get('esr') not in (None, ''):
            _num(c.get('esr'), f'{ref} ESR')
            if float(c['esr']) < 0:
                raise ValidationError(f'{ref}: ESR must not be negative')

    ports = model.get('ports') or []
    if not ports:
        raise ValidationError('At least one lumped port is required')
    excited = [p for p in ports if p.get('excite')]
    if len(excited) != 1:
        raise ValidationError(f'Exactly one port must be excited ({len(excited)} are)')
    sim_boundary = (model.get('sim') or {}).get('boundary') or 'MUR'
    for i, p in enumerate(ports):
        n = f'port {p.get("number", i + 1)}'
        for k in ('x', 'y', 'w', 'h'):
            _num(p.get(k), f'{n}.{k}')
        _num(p.get('impedance', 50), f'{n} impedance', positive=True)
        if p.get('ptype') == 'msl':
            if p.get('orient', '+x') not in ('+x', '-x', '+y', '-y'):
                raise ValidationError(f'{n}: invalid MSL orientation')
            if p.get('layerFrom') not in conductors or p.get('layerTo') not in conductors:
                raise ValidationError(f'{n}: strip/ground must be conductor layers')
            if p.get('layerFrom') == p.get('layerTo'):
                raise ValidationError(f'{n}: strip and ground layer are the same')
            if sim_boundary not in ('MUR', 'PML_8'):
                raise ValidationError(
                    f'{n}: MSL ports need an absorbing boundary (MUR or PML-8), '
                    f'the simulation uses {sim_boundary}')
            continue
        d = p.get('direction', 'z')
        if d not in ('x', 'y', 'z'):
            raise ValidationError(f'{n}: invalid direction')
        if d == 'z':
            if p.get('layerFrom') not in conductors or p.get('layerTo') not in conductors:
                raise ValidationError(f'{n}: layerFrom/layerTo must be conductor layers')
            if p.get('layerFrom') == p.get('layerTo'):
                raise ValidationError(f'{n}: layerFrom and layerTo are the same')
        else:
            if p.get('layer') not in conductors:
                raise ValidationError(f'{n}: in-plane port must sit on a conductor layer')


def dump_layers(model):
    """Conductor layers that carry copper (candidates for J dumps)."""
    used = {s.get('layer') for s in sim_shapes(model)}
    used |= {c.get('layer') for c in model.get('components') or []}
    used |= {l['id'] for l in model.get('stackup') or []
             if l.get('type') == 'conductor' and l.get('fill')}
    order = [l['id'] for l in model.get('stackup') or [] if l.get('type') == 'conductor']
    return [lid for lid in order if lid in used]


ALL_DUMP_POINTS = 21


def dump_freqs_hz(model):
    """Dump frequencies in Hz. dumpFreqs accepts a comma list in GHz,
    'all' (ALL_DUMP_POINTS evenly across the sweep), or empty (f0)."""
    sim = model.get('sim') or {}
    fstart, fstop = float(sim['fStart']) * 1e9, float(sim['fStop']) * 1e9
    raw = sim.get('dumpFreqs')
    if isinstance(raw, str):
        raw = [s for s in re.split(r'[,;\s]+', raw) if s]
    freqs = []
    for f in raw or []:
        if str(f).lower() == 'all':
            n = ALL_DUMP_POINTS
            freqs += [fstart + (fstop - fstart) * k / (n - 1) for k in range(n)]
            continue
        try:
            freqs.append(float(f) * 1e9)
        except ValueError:
            raise ValidationError(f'Bad dump frequency: {f} (use GHz values or "all")')
    if not freqs:
        freqs = [0.5 * (fstart + fstop)]
    freqs = sorted(set(freqs))
    if len(freqs) > 41:
        raise ValidationError('Too many dump frequencies (max 41)')
    return freqs


def generate_script(model):
    validate(model)

    board = model['board']
    stackup = model['stackup']
    sim = model.get('sim') or {}
    shapes = sim_shapes(model)
    vias = model.get('vias') or []
    comps = model.get('components') or []
    ports = model.get('ports') or []

    W, H = float(board['width']), float(board['height'])
    cond_z, diel_z, total_h = stackup_z(stackup)

    fstart, fstop = float(sim['fStart']) * 1e9, float(sim['fStop']) * 1e9
    f0 = 0.5 * (fstart + fstop)
    points = int(sim.get('points') or 401)
    end_db = float(sim.get('endCriteria') or -40.0)
    nrts = int(sim.get('maxTimesteps') or 30000)
    boundary = sim.get('boundary') or 'MUR'
    if boundary not in ('MUR', 'PML_8', 'PEC', 'PMC'):
        raise ValidationError('sim.boundary must be MUR, PML_8, PEC or PMC')

    mesh = build_mesh(model)
    exc_idx = next(int(p.get('number', i + 1)) for i, p in enumerate(ports) if p.get('excite'))

    matlab_paths = find_matlab_paths()
    addpath = '\n'.join(f"addpath('{p}');" for p in matlab_paths) or (
        "% openEMS Octave interface not found automatically - fix these paths:\n"
        "addpath('/path/to/openEMS/matlab');\naddpath('/path/to/CSXCAD/matlab');")

    L = []
    a = L.append
    a('%% openEMS PCB simulation -- generated by OpenEMS PCB Studio')
    a('% All geometry in mm. Run with: octave pcb_sim.m')
    a('close all; clear; clc;')
    a('')
    a(addpath)
    a('')
    a('physical_constants;')
    a('unit = 1e-3;  % drawing unit: mm')
    a('')
    a(f'f_start = {_fmt(fstart)};')
    a(f'f_stop  = {_fmt(fstop)};')
    a('f0 = 0.5*(f_start+f_stop);')
    a('fc = 0.5*(f_stop-f_start);')
    a('')
    a(f"FDTD = InitFDTD('NrTS', {nrts}, 'EndCriteria', {_fmt(10 ** (end_db / 10.0))});")
    a('FDTD = SetGaussExcite(FDTD, f0, fc);')
    a("BC = {'%s' '%s' '%s' '%s' '%s' '%s'};" % ((boundary,) * 6))
    a('FDTD = SetBoundaryCond(FDTD, BC);')
    a('')
    a('CSX = InitCSX();')
    a('')

    # sides carrying MSL ports: substrate and ground must extend to the
    # domain boundary there so the port launches into the real line
    # cross-section (the feed would otherwise hang in air)
    msl_sides = {p.get('orient', '+x') for p in ports if p.get('ptype') == 'msl'}
    msl_gnd_layers = {p['layerFrom'] for p in ports if p.get('ptype') == 'msl'}

    def _extend(x0, y0, x1, y1):
        """Grow a full-board box to the domain edge on every MSL side."""
        if '+x' in msl_sides:
            x0 = mesh['x'][0]
        if '-x' in msl_sides:
            x1 = mesh['x'][-1]
        if '+y' in msl_sides:
            y0 = mesh['y'][0]
        if '-y' in msl_sides:
            y1 = mesh['y'][-1]
        return x0, y0, x1, y1

    a('%% --- stackup ----------------------------------------------------')
    for l in stackup:
        lid = _ident(l['id'])
        if l['type'] == 'dielectric':
            er = float(l['er'])
            tand = float(l.get('tand') or 0.0)
            kappa = tand * 2 * math.pi * f0 * EPS0 * er
            z0, z1 = diel_z[l['id']]
            bx0, by0, bx1, by1 = _extend(0.0, 0.0, W, H)
            a(f"CSX = AddMaterial(CSX, 'diel_{lid}');  % {l.get('name', lid)}")
            a(f"CSX = SetMaterialProperty(CSX, 'diel_{lid}', 'Epsilon', {_fmt(er)}, 'Kappa', {_fmt(kappa)});")
            a(f"CSX = AddBox(CSX, 'diel_{lid}', 1, [{_fmt(bx0)} {_fmt(by0)} {_fmt(z0)}], "
              f"[{_fmt(bx1)} {_fmt(by1)} {_fmt(z1)}]);")
        else:
            z = cond_z[l['id']]
            a(f"CSX = AddMetal(CSX, 'cond_{lid}');  % {l.get('name', lid)} @ z={_fmt(z)}")
            if l.get('fill'):
                bx0, by0, bx1, by1 = (_extend(0.0, 0.0, W, H)
                                      if l['id'] in msl_gnd_layers else (0.0, 0.0, W, H))
                a(f"CSX = AddBox(CSX, 'cond_{lid}', 10, [{_fmt(bx0)} {_fmt(by0)} {_fmt(z)}], "
                  f"[{_fmt(bx1)} {_fmt(by1)} {_fmt(z)}]);")
    a('')

    if shapes:
        a('%% --- copper shapes ----------------------------------------------')
        for s in shapes:
            lid = _ident(s['layer'])
            z = cond_z[s['layer']]
            prio = int(s.get('priority', 10))
            name = s.get('name') or s.get('type', 'shape')
            if s.get('type', 'rect') == 'rect':
                x, y, w, h = (float(s[k]) for k in ('x', 'y', 'w', 'h'))
                a(f"CSX = AddBox(CSX, 'cond_{lid}', {prio}, "
                  f"[{_fmt(x)} {_fmt(y)} {_fmt(z)}], [{_fmt(x + w)} {_fmt(y + h)} {_fmt(z)}]);  % {name}")
            else:
                pts = shape_outline(s)
                row_x = ' '.join(_fmt(p[0]) for p in pts)
                row_y = ' '.join(_fmt(p[1]) for p in pts)
                a(f'pgon = [{row_x}; {row_y}];  % {name}')
                a(f"CSX = AddLinPoly(CSX, 'cond_{lid}', {prio}, 2, {_fmt(z)}, pgon, 0);")
        a('')

    if vias:
        a('%% --- vias -------------------------------------------------------')
        a("CSX = AddMetal(CSX, 'via_metal');")
        for i, v in enumerate(vias):
            x, y = float(v['x']), float(v['y'])
            z_ends = sorted([cond_z[v['from']], cond_z[v['to']]])
            a(f"CSX = AddCylinder(CSX, 'via_metal', 12, [{_fmt(x)} {_fmt(y)} {_fmt(z_ends[0])}], "
              f"[{_fmt(x)} {_fmt(y)} {_fmt(z_ends[1])}], {_fmt(float(v['drill']) / 2)});  % via {i + 1} barrel")
            pad_pts = circle_points(x, y, float(v['pad']) / 2, 32)
            row_x = ' '.join(_fmt(p[0]) for p in pad_pts)
            row_y = ' '.join(_fmt(p[1]) for p in pad_pts)
            a(f'pad = [{row_x}; {row_y}];  % via {i + 1} pad')
            for lid, z in cond_z.items():
                if z_ends[0] - 1e-9 <= z <= z_ends[1] + 1e-9:
                    a(f"CSX = AddLinPoly(CSX, 'cond_{_ident(lid)}', 12, 2, {_fmt(z)}, pad, 0);  % pad on {lid}")
        a('')

    if comps:
        a('%% --- discrete components (lumped elements) ----------------------')
        a('% element boxes are shrunk to the copper-free gap they bridge, so')
        a('% the nominal R/L/C applies across the gap (copper overlapping the')
        a('% box would otherwise short part of the distributed element).')
        a('% C and L carry a series ESR in the second half of the gap: an')
        a('% ideal lossless element forms an undamped resonator with the')
        a('% mounting-loop parasitics (the tank rings forever after the')
        a('% excitation and the energy decay never reaches the end criteria)')
        unit_scale = {'R': 1.0, 'C': 1e-12, 'L': 1e-9}
        for i, c in enumerate(comps):
            ref = _ident(c.get('ref') or f"{c['ctype']}{i + 1}")
            x0, y0, x1, y1, ny, connected = comp_element_box(c, shapes)
            z = cond_z[c['layer']]
            val = float(c['value']) * unit_scale[c['ctype']]
            esr = c.get('esr')
            esr = float(esr) if esr not in (None, '') else DEFAULT_ESR.get(c['ctype'], 0.0)
            note = '' if connected else '  % WARNING: ends do not touch copper'
            if esr > 0 and c['ctype'] in ('C', 'L'):
                # series connection: two lumped-element sheets split at the
                # gap centre (a guaranteed mesh line), joined by their caps
                if ny == 0:
                    xm = round((x0 + x1) / 2.0, 6)
                    halves = [(x0, y0, xm, y1), (xm, y0, x1, y1)]
                else:
                    ym = round((y0 + y1) / 2.0, 6)
                    halves = [(x0, y0, x1, ym), (x0, ym, x1, y1)]
                a(f"CSX = AddLumpedElement(CSX, '{ref}', {ny}, 'Caps', 1, '{c['ctype']}', {_fmt(val)});")
                a(f"CSX = AddBox(CSX, '{ref}', 20, [{_fmt(halves[0][0])} {_fmt(halves[0][1])} {_fmt(z)}], "
                  f"[{_fmt(halves[0][2])} {_fmt(halves[0][3])} {_fmt(z)}]);{note}")
                a(f"CSX = AddLumpedElement(CSX, '{ref}_esr', {ny}, 'Caps', 1, 'R', {_fmt(esr)});")
                a(f"CSX = AddBox(CSX, '{ref}_esr', 20, [{_fmt(halves[1][0])} {_fmt(halves[1][1])} {_fmt(z)}], "
                  f"[{_fmt(halves[1][2])} {_fmt(halves[1][3])} {_fmt(z)}]);")
            else:
                a(f"CSX = AddLumpedElement(CSX, '{ref}', {ny}, 'Caps', 1, '{c['ctype']}', {_fmt(val)});")
                a(f"CSX = AddBox(CSX, '{ref}', 20, [{_fmt(x0)} {_fmt(y0)} {_fmt(z)}], "
                  f"[{_fmt(x1)} {_fmt(y1)} {_fmt(z)}]);{note}")
        a('')

    a('%% --- mesh (pre-computed, matches the GUI preview) ---------------')
    a(f'% resolution: {_fmt(mesh["edgeRes"])} mm on the board, {_fmt(mesh["maxRes"])} mm in air')
    a(f'mesh.x = {_vec(mesh["x"])};')
    a(f'mesh.y = {_vec(mesh["y"])};')
    a(f'mesh.z = {_vec(mesh["z"])};')
    a('CSX = DefineRectGrid(CSX, unit, mesh);')
    a(f"disp('GUI_MARKER: mesh {len(mesh['x'])}x{len(mesh['y'])}x{len(mesh['z'])} = {mesh['cells']} cells');")
    a('')

    a('%% --- ports ------------------------------------------------------')
    a('port = {};')
    dir_vec = {'x': '[1 0 0]', 'y': '[0 1 0]', 'z': '[0 0 1]'}
    for i, p in enumerate(ports):
        x, y = float(p['x']), float(p['y'])
        w, h = float(p['w']), float(p['h'])
        num = int(p.get('number', i + 1))
        r = float(p.get('impedance', 50))
        excite = 1 if p.get('excite') else 0
        if p.get('ptype') == 'msl':
            # matched microstrip-line port: the strip runs from the domain
            # boundary (absorbed there) to the port's inner edge, where the
            # de-embedded S-parameter reference plane sits
            orient = p.get('orient', '+x')
            z_strip = cond_z[p['layerTo']]
            z_gnd = cond_z[p['layerFrom']]
            ev = '[0 0 -1]' if z_gnd < z_strip else '[0 0 1]'
            if orient in ('+x', '-x'):
                dom = mesh['x'][0] if orient == '+x' else mesh['x'][-1]
                inner = x + w if orient == '+x' else x
                start = (dom, y, z_strip)
                stop = (inner, y + h, z_gnd)
                length = abs(inner - dom)
                dirn = 0
            else:
                dom = mesh['y'][0] if orient == '+y' else mesh['y'][-1]
                inner = y + h if orient == '+y' else y
                start = (x, dom, z_strip)
                stop = (x + w, inner, z_gnd)
                length = abs(inner - dom)
                dirn = 1
            margin = float(sim.get('airMargin', 20.0))
            feed_shift = min(margin / 3.0, length / 4.0)
            # S-parameter reference plane exactly at the port's inner edge
            # (a mesh line always exists there, so the snap is exact)
            meas_shift = length
            opts = f", 'MeasPlaneShift', {_fmt(meas_shift)}"
            if excite:
                opts += f", 'ExcitePort', true, 'FeedShift', {_fmt(feed_shift)}"
            a(f"[CSX, port{{{num}}}] = AddMSLPort(CSX, 30, {num}, 'cond_{_ident(p['layerTo'])}', "
              f'[{_fmt(start[0])} {_fmt(start[1])} {_fmt(start[2])}], '
              f'[{_fmt(stop[0])} {_fmt(stop[1])} {_fmt(stop[2])}], '
              f'{dirn}, {ev}{opts});  % MSL port {num} ({orient})')
            continue
        d = p.get('direction', 'z')
        if d == 'z':
            z0, z1 = sorted([cond_z[p['layerFrom']], cond_z[p['layerTo']]])
        else:
            z0 = z1 = cond_z[p['layer']]
        a(f'[CSX, port{{{num}}}] = AddLumpedPort(CSX, 5, {num}, {_fmt(r)}, '
          f'[{_fmt(x)} {_fmt(y)} {_fmt(z0)}], [{_fmt(x + w)} {_fmt(y + h)} {_fmt(z1)}], '
          f'{dir_vec[d]}, {excite});')
    a('')

    jlayers = dump_layers(model) if sim.get('dumpJ') else []
    jfreqs = dump_freqs_hz(model) if jlayers else []
    if jlayers:
        a('%% --- current density dumps (frequency domain, rot(H)) -----------')
        a(f'jfreqs = {_vec(jfreqs)};')
        for lid in jlayers:
            nm = _ident(lid)
            z = cond_z[lid]
            a(f"CSX = AddDump(CSX, 'Jf_{nm}', 'DumpType', 13, 'DumpMode', 2, 'FileType', 1, 'Frequency', jfreqs);")
            a(f"CSX = AddBox(CSX, 'Jf_{nm}', 0, [0 0 {_fmt(z)}], [{_fmt(W)} {_fmt(H)} {_fmt(z)}]);")
        a('')

    jtlayers = dump_layers(model) if sim.get('dumpJt') else []
    if jtlayers:
        t0 = float(sim.get('jtStart') or 0.0) * 1e-9
        t1 = float(sim.get('jtStop') or 3.0) * 1e-9
        if t1 <= t0:
            raise ValidationError('J(t) recording: stop time must be after start time')
        # frames land at the engine's Nyquist-linked dump interval (tens
        # of ps), not every timestep, so even a whole-run window stays
        # manageable; the export decimates to <=160 frames either way
        if t1 - t0 > 500e-9:
            raise ValidationError('J(t) recording window too long (max 500 ns)')
        sub = int(sim.get('jtSub') or 2)
        if sub not in (1, 2, 4):
            raise ValidationError('J(t) subsampling must be 1, 2 or 4')
        subopt = f", 'SubSampling', '{sub},{sub},1'" if sub > 1 else ''
        a('%% --- current density recording (time domain, rot(H)) ------------')
        a(f'% active window {_fmt(t0 * 1e9)}..{_fmt(t1 * 1e9)} ns, every timestep')
        for lid in jtlayers:
            nm = _ident(lid)
            z = cond_z[lid]
            a(f"CSX = AddDump(CSX, 'Jt_{nm}', 'DumpType', 3, 'DumpMode', 2, 'FileType', 1, "
              f"'StartTime', {_fmt(t0)}, 'StopTime', {_fmt(t1)}{subopt});")
            a(f"CSX = AddBox(CSX, 'Jt_{nm}', 0, [0 0 {_fmt(z)}], [{_fmt(W)} {_fmt(H)} {_fmt(z)}]);")
        a('')

    a('%% --- run --------------------------------------------------------')
    a("Sim_Path = '.'; Sim_File = 'pcb_sim.xml';")
    a('WriteOpenEMS([Sim_Path filesep Sim_File], FDTD, CSX);')
    a("disp('GUI_MARKER: starting FDTD');")
    a('RunOpenEMS(Sim_Path, Sim_File);')
    a('')
    a('%% --- post-processing --------------------------------------------')
    a("disp('GUI_MARKER: post-processing');")
    a(f'freq = linspace(f_start, f_stop, {points});')
    for i, p in enumerate(ports):
        num = int(p.get('number', i + 1))
        r = float(p.get('impedance', 50))
        a(f"port{{{num}}} = calcPort(port{{{num}}}, Sim_Path, freq, 'RefImpedance', {_fmt(r)});")
    a(f'exc = {exc_idx};  % excited port')
    a('')
    header = 'freq_Hz'
    for i, p in enumerate(ports):
        num = int(p.get('number', i + 1))
        header += f',S{num}{exc_idx}_re,S{num}{exc_idx}_im'
    header += ',Zin_re,Zin_im'
    a("fid = fopen('sparams.csv', 'w');")
    a(f"fprintf(fid, '#{header}\\n');")
    a('Zin = port{exc}.uf.tot ./ port{exc}.if.tot;')
    args = 'freq(k)'
    for i, p in enumerate(ports):
        num = int(p.get('number', i + 1))
        a(f'S{num} = port{{{num}}}.uf.ref ./ port{{exc}}.uf.inc;')
        args += f', real(S{num}(k)), imag(S{num}(k))'
    args += ', real(Zin(k)), imag(Zin(k))'
    fmt = '%e' + ',%e,%e' * len(ports) + ',%e,%e\\n'
    a('for k = 1:numel(freq)')
    a(f"  fprintf(fid, '{fmt}', {args});")
    a('end')
    a('fclose(fid);')
    a('')
    if jlayers:
        a('%% export current density dumps as flat binaries for the GUI')
        a("jd = fopen('jdumps.csv', 'w');")
        for lid in jlayers:
            nm = _ident(lid)
            a('try')
            a(f"  [jf, jm] = ReadHDF5Dump('Jf_{nm}.h5');")
            a('  jx = jm.lines{1}; jy = jm.lines{2};')
            a('  if max(abs(jx)) < 1, jx = jx/unit; jy = jy/unit; end  % meters -> mm')
            a('  for k = 1:numel(jf.FD.frequency)')
            a('    J = squeeze(jf.FD.values{k});')
            a(f"    fb = fopen(sprintf('J_{nm}_f%d.bin', k), 'w');")
            a("    fwrite(fb, [size(J,1) size(J,2)], 'int32');")
            a("    fwrite(fb, jx, 'single'); fwrite(fb, jy, 'single');")
            a("    fwrite(fb, real(J(:,:,1)), 'single'); fwrite(fb, imag(J(:,:,1)), 'single');")
            a("    fwrite(fb, real(J(:,:,2)), 'single'); fwrite(fb, imag(J(:,:,2)), 'single');")
            a('    fclose(fb);')
            a(f"    fprintf(jd, '{lid},%d,%e\\n', k, jf.FD.frequency(k));")
            a('  end')
            a('catch err')
            a(f"  disp(['GUI_MARKER: jdump {lid} failed: ' err.message]);")
            a('end')
        a('fclose(jd);')
        a('')
    if jtlayers:
        a('%% export time-domain current density as |J| frame stacks for the GUI')
        a("jtd = fopen('jtdumps.csv', 'w');")
        for lid in jtlayers:
            nm = _ident(lid)
            a('try')
            a(f"  [jt, jtm] = ReadHDF5Dump('Jt_{nm}.h5');")
            a('  xt = jtm.lines{1}; yt = jtm.lines{2};')
            a('  if max(abs(xt)) < 1, xt = xt/unit; yt = yt/unit; end  % meters -> mm')
            a('  NF = numel(jt.TD.values);')
            a('  sel = 1:max(1, ceil(NF/160)):NF;')
            a(f"  fb = fopen('J_td_{nm}.bin', 'w');")
            a("  fwrite(fb, [numel(xt) numel(yt) numel(sel)], 'int32');")
            a("  fwrite(fb, xt, 'single'); fwrite(fb, yt, 'single');")
            a("  fwrite(fb, jt.TD.time(sel), 'single');")
            a('  for k = sel')
            a('    Jk = squeeze(jt.TD.values{k});')
            a("    fwrite(fb, sqrt(Jk(:,:,1).^2 + Jk(:,:,2).^2), 'single');")
            a('  end')
            a('  fclose(fb);')
            a(f"  fprintf(jtd, '{lid},%d\\n', numel(sel));")
            a('catch err')
            a(f"  disp(['GUI_MARKER: jtdump {lid} failed: ' err.message]);")
            a('end')
        a('fclose(jtd);')
        a('')
    a("disp('GUI_MARKER: done');")
    a('')
    return '\n'.join(L) + '\n'
