"""Minimal RS-274X (Gerber) and Excellon drill parsers for layer import.

Supported Gerber subset: FS (LA/TA), MO, AD with standard apertures
(C/R/O/P), G01/G02/G03 interpolation, D01/D02/D03, G36/G37 regions,
G74/G75 quadrant modes, LP (dark only - clear polarity primitives are
skipped with a warning), G04 comments, M02. Aperture macros (AM) and
step-repeat (SR) are not supported and reported as warnings.

All output geometry is in mm:
  {'type': 'circle', 'cx', 'cy', 'r'}
  {'type': 'rect', 'x', 'y', 'w', 'h'}
  {'type': 'poly', 'pts': [[x, y], ...]}
"""
import math
import re

MAX_SHAPES = 8000


class GerberError(Exception):
    pass


def _r4(v):
    return round(v, 4)


def _stadium(x0, y0, x1, y1, w):
    """Thick line segment as a rounded-end polygon."""
    r = w / 2.0
    ang = math.atan2(y1 - y0, x1 - x0)
    pts = []
    for k in range(9):   # cap at the end point
        a = ang - math.pi / 2 + math.pi * k / 8
        pts.append((x1 + r * math.cos(a), y1 + r * math.sin(a)))
    for k in range(9):   # cap at the start point
        a = ang + math.pi / 2 + math.pi * k / 8
        pts.append((x0 + r * math.cos(a), y0 + r * math.sin(a)))
    return pts


def _ngon(cx, cy, r, n, rot_deg=0.0):
    return [(cx + r * math.cos(math.radians(rot_deg) + 2 * math.pi * k / n),
             cy + r * math.sin(math.radians(rot_deg) + 2 * math.pi * k / n))
            for k in range(max(3, int(n)))]


def _tokenize(text):
    out = []
    text = text.replace('\r', '')
    i = 0
    while i < len(text):
        c = text[i]
        if c == '%':
            j = text.find('%', i + 1)
            if j < 0:
                break
            for cmd in text[i + 1:j].split('*'):
                cmd = cmd.strip()
                if cmd:
                    out.append((True, cmd))
            i = j + 1
        elif c in ' \n\t':
            i += 1
        else:
            j = text.find('*', i)
            if j < 0:
                break
            cmd = text[i:j].strip()
            if cmd:
                out.append((False, cmd))
            i = j + 1
    return out


_WORD_RE = re.compile(
    r'^(?:G(\d+))?'
    r'(?:X([+-]?\d+))?(?:Y([+-]?\d+))?'
    r'(?:I([+-]?\d+))?(?:J([+-]?\d+))?'
    r'(?:D(\d+))?$')


class _Gerber:
    def __init__(self):
        self.scale = 1.0          # file units -> mm
        self.xd = 6               # decimal digits
        self.zeros = 'L'          # leading zeros omitted
        self.digits = 9           # total digits (for trailing-zero mode)
        self.apertures = {}
        self.macros = set()
        self.ap = None
        self.x = 0.0
        self.y = 0.0
        self.g = 1                # 1 line, 2 cw arc, 3 ccw arc
        self.quad = 75            # 74 single, 75 multi quadrant
        self.dark = True
        self.region = None        # list of contours while in G36
        self.shapes = []
        self.warnings = []
        self.skipped_clear = 0

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def coord(self, s):
        if s is None:
            return None
        neg = s.startswith('-')
        digits = s.lstrip('+-')
        if self.zeros == 'T':
            digits = digits.ljust(self.digits, '0')
        v = int(digits or '0') / (10 ** self.xd)
        return -v if neg else v

    def emit(self, shape):
        if not self.dark:
            self.skipped_clear += 1
            return
        if len(self.shapes) >= MAX_SHAPES:
            raise GerberError(f'more than {MAX_SHAPES} primitives - aborting')
        self.shapes.append(shape)

    # ---- extended commands ----------------------------------------
    def ext(self, cmd):
        if cmd.startswith('FS'):
            m = re.match(r'FS([LT])[AI]X(\d)(\d)Y\d\d', cmd)
            if m:
                self.zeros = m.group(1)
                self.digits = int(m.group(2)) + int(m.group(3))
                self.xd = int(m.group(3))
        elif cmd.startswith('MO'):
            self.scale = 25.4 if 'IN' in cmd else 1.0
        elif cmd.startswith('AM'):
            self.macros.add(cmd[2:].split('*')[0])
            self.warn('aperture macros (AM) are not supported - macro flashes skipped')
        elif cmd.startswith('ADD'):
            m = re.match(r'ADD(\d+)([A-Za-z_.$][\w.$]*)(?:,(.*))?$', cmd)
            if not m:
                return
            code = int(m.group(1))
            name = m.group(2)
            params = [float(p) for p in (m.group(3) or '').split('X') if p]
            if name in ('C', 'R', 'O', 'P'):
                self.apertures[code] = (name, params)
            elif name in self.macros:
                self.apertures[code] = ('MACRO', params)
            else:
                self.apertures[code] = ('MACRO', params)
                self.warn(f'unsupported aperture "{name}" - flashes skipped')
        elif cmd.startswith('LP'):
            self.dark = cmd.endswith('D')
        elif cmd.startswith('SR'):
            if cmd not in ('SR', 'SRX1Y1I0J0'):
                self.warn('step-repeat (SR) is not supported - geometry not repeated')
        # IP, LN, TF, TA, TO, TD etc. are ignored

    # ---- drawing ---------------------------------------------------
    def flash(self):
        ap = self.apertures.get(self.ap)
        if ap is None:
            self.warn('flash with unknown aperture skipped')
            return
        kind, p = ap
        s = self.scale
        x, y = self.x * s, self.y * s
        if kind == 'C' and p:
            self.emit({'type': 'circle', 'cx': _r4(x), 'cy': _r4(y), 'r': _r4(p[0] * s / 2)})
        elif kind == 'R' and len(p) >= 2:
            w, h = p[0] * s, p[1] * s
            self.emit({'type': 'rect', 'x': _r4(x - w / 2), 'y': _r4(y - h / 2),
                       'w': _r4(w), 'h': _r4(h)})
        elif kind == 'O' and len(p) >= 2:
            w, h = p[0] * s, p[1] * s
            if w >= h:
                pts = _stadium(x - (w - h) / 2, y, x + (w - h) / 2, y, h)
            else:
                pts = _stadium(x, y - (h - w) / 2, x, y + (h - w) / 2, w)
            self.emit({'type': 'poly', 'pts': [[_r4(a), _r4(b)] for a, b in pts]})
        elif kind == 'P' and len(p) >= 2:
            pts = _ngon(x, y, p[0] * s / 2, int(p[1]), p[2] if len(p) > 2 else 0)
            self.emit({'type': 'poly', 'pts': [[_r4(a), _r4(b)] for a, b in pts]})
        # MACRO: skipped (warned at AD/AM time)

    def arc_points(self, x0, y0, x1, y1, i, j):
        """Sampled points along the current arc (excluding the start)."""
        cw = self.g == 2
        cands = []
        if self.quad == 75:
            cands = [(x0 + i, y0 + j)]
        else:
            for sx in (1, -1):
                for sy in (1, -1):
                    cands.append((x0 + sx * i, y0 + sy * j))
        best = None
        for cx, cy in cands:
            r0 = math.hypot(x0 - cx, y0 - cy)
            r1 = math.hypot(x1 - cx, y1 - cy)
            if r0 < 1e-9 or abs(r0 - r1) > max(r0, r1) * 0.01 + 1e-6:
                continue
            a0 = math.atan2(y0 - cy, x0 - cx)
            a1 = math.atan2(y1 - cy, x1 - cx)
            sweep = a1 - a0
            if cw:
                while sweep >= -1e-12:
                    sweep -= 2 * math.pi
            else:
                while sweep <= 1e-12:
                    sweep += 2 * math.pi
            if self.quad == 74 and abs(sweep) > math.pi / 2 + 1e-6:
                continue
            if best is None or abs(sweep) < abs(best[3]):
                best = (cx, cy, a0, sweep, r0)
        if best is None:
            return [(x1, y1)]
        cx, cy, a0, sweep, r = best
        n = max(2, int(abs(sweep) / math.radians(5)))
        return [(cx + r * math.cos(a0 + sweep * k / n),
                 cy + r * math.sin(a0 + sweep * k / n)) for k in range(1, n + 1)]

    def word(self, cmd):
        if cmd.startswith('G04'):
            return
        if cmd in ('M00', 'M01', 'M02'):
            return
        if cmd == 'G36':
            self.region = [[]]
            return
        if cmd == 'G37':
            if self.region:
                for contour in self.region:
                    if len(contour) >= 3:
                        self.emit({'type': 'poly',
                                   'pts': [[_r4(px * self.scale), _r4(py * self.scale)]
                                           for px, py in contour]})
            self.region = None
            return
        m = _WORD_RE.match(cmd)
        if not m:
            return
        gcode, xs, ys, is_, js_, d = m.groups()
        if gcode is not None:
            gi = int(gcode)
            if gi in (1, 2, 3):
                self.g = gi
            elif gi in (74, 75):
                self.quad = gi
            elif gi in (70, 71):
                self.scale = 25.4 if gi == 70 else 1.0
            elif gi == 36:
                self.region = [[]]
            elif gi == 37:
                return self.word('G37')
        nx = self.coord(xs)
        ny = self.coord(ys)
        i = self.coord(is_) or 0.0
        j = self.coord(js_) or 0.0
        tx = self.x if nx is None else nx
        ty = self.y if ny is None else ny
        if d is not None and int(d) >= 10:
            self.ap = int(d)
            return
        op = int(d) if d is not None else None
        if op == 3:
            self.x, self.y = tx, ty
            self.flash()
            return
        if op == 2:
            if self.region is not None:
                self.region.append([(tx, ty)])
            self.x, self.y = tx, ty
            return
        if op == 1 or (op is None and (nx is not None or ny is not None)):
            pts = ([(tx, ty)] if self.g == 1
                   else self.arc_points(self.x, self.y, tx, ty, i, j))
            if self.region is not None:
                if not self.region[-1]:
                    self.region[-1].append((self.x, self.y))
                self.region[-1] += pts
            else:
                ap = self.apertures.get(self.ap)
                w = 0.0
                if ap and ap[0] == 'C' and ap[1]:
                    w = ap[1][0]
                elif ap and ap[0] in ('R', 'O') and len(ap[1]) >= 2:
                    w = max(ap[1][0], ap[1][1])
                    self.warn('rect/obround aperture strokes approximated as round')
                if w > 1e-9:
                    prev = (self.x, self.y)
                    for p in pts:
                        st = _stadium(prev[0] * self.scale, prev[1] * self.scale,
                                      p[0] * self.scale, p[1] * self.scale, w * self.scale)
                        self.emit({'type': 'poly', 'pts': [[_r4(a), _r4(b)] for a, b in st]})
                        prev = p
                else:
                    self.warn('draw with unset/zero-width aperture skipped')
            self.x, self.y = tx, ty


def parse_gerber(text):
    g = _Gerber()
    for is_ext, cmd in _tokenize(text):
        if is_ext:
            g.ext(cmd)
        else:
            g.word(cmd)
    if g.skipped_clear:
        g.warn(f'{g.skipped_clear} clear-polarity (LPC) primitives skipped - '
               'plane clearances are NOT cut out')
    if not g.shapes:
        raise GerberError('no supported geometry found in file')
    xs, ys = [], []
    for s in g.shapes:
        if s['type'] == 'circle':
            xs += [s['cx'] - s['r'], s['cx'] + s['r']]
            ys += [s['cy'] - s['r'], s['cy'] + s['r']]
        elif s['type'] == 'rect':
            xs += [s['x'], s['x'] + s['w']]
            ys += [s['y'], s['y'] + s['h']]
        else:
            xs += [p[0] for p in s['pts']]
            ys += [p[1] for p in s['pts']]
    return {'shapes': g.shapes, 'warnings': g.warnings,
            'bbox': [min(xs), min(ys), max(xs), max(ys)]}


def parse_excellon(text):
    tools = {}
    vias = []
    warnings = []
    unit = 1.0        # mm
    fixed_div = 1000  # implied-decimal divisor
    cur = None
    header = True
    for raw in text.replace('\r', '').split('\n'):
        line = raw.strip()
        if not line or line.startswith(';'):
            continue
        up = line.upper()
        if up.startswith('M48'):
            header = True
            continue
        if up in ('%', 'M95'):
            header = False
            continue
        if 'METRIC' in up:
            unit, fixed_div = 1.0, 1000
            continue
        if 'INCH' in up:
            unit, fixed_div = 25.4, 10000
            continue
        m = re.match(r'T(\d+)C([\d.]+)', up)
        if m:
            tools[int(m.group(1))] = float(m.group(2)) * unit
            continue
        if header:
            continue
        m = re.fullmatch(r'T(\d+)', up)
        if m:
            cur = tools.get(int(m.group(1)))
            continue
        if up.startswith('G85') or ('G85' in up):
            if 'slots (G85) skipped' not in warnings:
                warnings.append('slots (G85) skipped')
            continue
        m = re.match(r'X(-?[\d.]+)Y(-?[\d.]+)', up)
        if m:
            def val(s):
                if '.' in s:
                    return float(s) * unit
                return int(s) / fixed_div * unit
            if cur is None:
                if 'hit before tool selection skipped' not in warnings:
                    warnings.append('hit before tool selection skipped')
                continue
            if len(vias) >= MAX_SHAPES:
                raise GerberError(f'more than {MAX_SHAPES} drill hits - aborting')
            vias.append({'x': _r4(val(m.group(1))), 'y': _r4(val(m.group(2))),
                         'drill': _r4(cur)})
    if not vias:
        raise GerberError('no drill hits found in file')
    xs = [v['x'] for v in vias]
    ys = [v['y'] for v in vias]
    return {'vias': vias, 'warnings': warnings,
            'bbox': [min(xs), min(ys), max(xs), max(ys)]}


# ---------------------------------------------------------------------------
# Fabrication export: RS-274X (one file per conductor layer + board
# outline) and an Excellon drill file. The generated Gerbers use filled
# G36/G37 regions for every shape outline (matching exactly what the
# simulation sees via geometry.shape_outline), circle flashes for via
# pads, and a full-board region for plane layers. Coordinates are
# emitted in 4.6 mm format; the drill file uses metric decimal
# coordinates, which round-trips through parse_excellon above.
# ---------------------------------------------------------------------------

def _gc(v):
    """mm -> RS-274X 4.6 integer coordinate."""
    return str(int(round(float(v) * 1e6)))


def _region(pts):
    out = ['G36*']
    x0, y0 = pts[0]
    out.append(f'X{_gc(x0)}Y{_gc(y0)}D02*')
    for x, y in list(pts[1:]) + [pts[0]]:
        out.append(f'X{_gc(x)}Y{_gc(y)}D01*')
    out.append('G37*')
    return out


def _via_span(v, cond_z):
    lo, hi = sorted([cond_z[v['from']], cond_z[v['to']]])
    return lo - 1e-9, hi + 1e-9


def gerber_layer(model, layer_id):
    """RS-274X content for one conductor layer of the model."""
    from geometry import shape_outline, stackup_z
    cond_z, _diel, _tot = stackup_z(model['stackup'])
    layer = next(l for l in model['stackup'] if l.get('id') == layer_id)
    board = model['board']
    out = [f'%TF.FileFunction,Copper,{layer_id}*%',
           '%FSLAX46Y46*%', '%MOMM*%', '%LPD*%', 'G01*']
    # one circle aperture per distinct via pad diameter on this layer
    z = cond_z[layer_id]
    pads = sorted({round(float(v['pad']), 4) for v in model.get('vias') or []
                   if _via_span(v, cond_z)[0] <= z <= _via_span(v, cond_z)[1]})
    dcode = {}
    for i, dia in enumerate(pads):
        dcode[dia] = 10 + i
        out.append('%%ADD%dC,%.4f*%%' % (dcode[dia], dia))
    if layer.get('fill'):
        out += _region([(0, 0), (board['width'], 0),
                        (board['width'], board['height']), (0, board['height'])])
    for s in model.get('shapes') or []:
        if s.get('layer') != layer_id:
            continue
        out += _region(shape_outline(s))
    for v in model.get('vias') or []:
        lo, hi = _via_span(v, cond_z)
        if lo <= z <= hi:
            out.append('D%d*' % dcode[round(float(v['pad']), 4)])
            out.append(f'X{_gc(v["x"])}Y{_gc(v["y"])}D03*')
    out.append('M02*')
    return '\n'.join(out) + '\n'


def gerber_outline(model):
    """Board outline as a thin drawn rectangle (Edge-Cuts style)."""
    b = model['board']
    out = ['%TF.FileFunction,Profile,NP*%',
           '%FSLAX46Y46*%', '%MOMM*%', '%LPD*%', 'G01*',
           '%ADD10C,0.1000*%', 'D10*',
           f'X{_gc(0)}Y{_gc(0)}D02*',
           f'X{_gc(b["width"])}Y{_gc(0)}D01*',
           f'X{_gc(b["width"])}Y{_gc(b["height"])}D01*',
           f'X{_gc(0)}Y{_gc(b["height"])}D01*',
           f'X{_gc(0)}Y{_gc(0)}D01*',
           'M02*']
    return '\n'.join(out) + '\n'


def excellon_drill(model):
    """Excellon (PTH) drill file for the model's vias."""
    vias = model.get('vias') or []
    tools = sorted({round(float(v['drill']), 3) for v in vias})
    out = ['M48', 'METRIC,TZ']
    for i, dia in enumerate(tools, 1):
        out.append(f'T{i}C{dia:.3f}')
    out.append('%')
    for i, dia in enumerate(tools, 1):
        out.append(f'T{i}')
        for v in vias:
            if round(float(v['drill']), 3) == dia:
                out.append(f'X{float(v["x"]):.3f}Y{float(v["y"]):.3f}')
    out.append('M30')
    return '\n'.join(out) + '\n'


def export_fabrication(model):
    """{filename: text} for the whole model: one .gbr per conductor
    layer, the board outline, and the drill file (when vias exist)."""
    import re as _re
    files = {}
    for l in model.get('stackup') or []:
        if l.get('type') != 'conductor':
            continue
        name = _re.sub(r'[^\w.-]+', '_', l.get('name') or l['id']).strip('_') or l['id']
        files[f'{name}.gbr'] = gerber_layer(model, l['id'])
    files['outline.gbr'] = gerber_outline(model)
    if model.get('vias'):
        files['drill.drl'] = excellon_drill(model)
    return files
