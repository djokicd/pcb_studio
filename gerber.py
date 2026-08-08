"""Minimal RS-274X (Gerber) and Excellon drill parsers for layer import.

Supported Gerber subset: FS (LA/TA), MO, AD with standard apertures
(C/R/O/P), aperture macros (AM) with the standard primitives (circle 1,
vector line 2/20, centre/lower-left rect 21/22, outline 4, polygon 5)
including $n parameters, arithmetic and assignments - this is how KiCad
et al. flash component pads (rounded/chamfered rectangles). Macro
flashes with several exposed primitives are merged into one polygon via
their convex hull (exact for the standard convex pad shapes); a single
primitive is emitted as-is, so concave custom outlines survive. Moire/
thermal primitives (6/7) and exposure-off cutouts are skipped with a
warning. Also: G01/G02/G03 interpolation, D01/D02/D03, G36/G37 regions,
G74/G75 quadrant modes, LP (dark only - clear polarity primitives are
skipped with a warning), G04 comments, M02. Step-repeat (SR) is not
supported and reported as a warning.

All output geometry is in mm:
  {'type': 'circle', 'cx', 'cy', 'r'}
  {'type': 'rect', 'x', 'y', 'w', 'h'}
  {'type': 'poly', 'pts': [[x, y], ...]}
  {'type': 'trace', 'pts': [[x, y], ...], 'width'}   chained D01 draws

Consecutive stroked draws (including the arc tessellation) are chained
into a single centerline trace per run of the pen, lightly decimated
(Douglas-Peucker, 10 um) - one shape per drawn line instead of a stroke
polygon per segment.
"""
import math
import re

from geometry import dp_polyline

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


def _rot(pts, deg):
    """Rotate points CCW about the macro origin (per the AM spec)."""
    if abs(deg) < 1e-12:
        return list(pts)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def _hull(pts):
    """Convex hull (Andrew monotone chain)."""
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in pts))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out

    lower = half(pts)
    upper = half(reversed(pts))
    return lower[:-1] + upper[:-1]


class _MacroExpr:
    """Evaluator for AM macro expressions: numbers, $n variables,
    + - x X / and parentheses."""

    def __init__(self, text, env):
        self.t = text
        self.i = 0
        self.env = env

    def peek(self):
        while self.i < len(self.t) and self.t[self.i] in ' \t':
            self.i += 1
        return self.t[self.i] if self.i < len(self.t) else ''

    def expr(self):
        v = self.term()
        while True:
            c = self.peek()
            if c == '+':
                self.i += 1
                v += self.term()
            elif c == '-':
                self.i += 1
                v -= self.term()
            else:
                return v

    def term(self):
        v = self.factor()
        while True:
            c = self.peek()
            if c and c in 'xX':
                self.i += 1
                v *= self.factor()
            elif c == '/':
                self.i += 1
                d = self.factor()
                v = v / d if d else 0.0
            else:
                return v

    def factor(self):
        c = self.peek()
        if c == '-':
            self.i += 1
            return -self.factor()
        if c == '+':
            self.i += 1
            return self.factor()
        if c == '(':
            self.i += 1
            v = self.expr()
            if self.peek() == ')':
                self.i += 1
            return v
        if c == '$':
            self.i += 1
            j = self.i
            while j < len(self.t) and self.t[j].isdigit():
                j += 1
            n = int(self.t[self.i:j] or '0')
            self.i = j
            return float(self.env.get(n, 0.0))
        j = self.i
        while j < len(self.t) and (self.t[j].isdigit() or self.t[j] == '.'):
            j += 1
        if j == self.i:
            raise GerberError(f'bad macro expression: {self.t!r}')
        v = float(self.t[self.i:j])
        self.i = j
        return v


def _macro_eval(text, env):
    return _MacroExpr(text, env).expr()


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
        self.macro_defs = {}      # name -> list of body statements
        self._am = None           # macro name while collecting its body
        self.ap = None
        self.x = 0.0
        self.y = 0.0
        self.g = 1                # 1 line, 2 cw arc, 3 ccw arc
        self.quad = 75            # 74 single, 75 multi quadrant
        self.dark = True
        self.region = None        # list of contours while in G36
        self.trace = None         # {'w', 'pts', 'dark'} while chaining draws
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

    def flush_trace(self):
        """Close the current draw chain and emit it as a trace shape."""
        t, self.trace = self.trace, None
        if not t or len(t['pts']) < 2:
            return
        if not t['dark']:
            self.skipped_clear += 1
            return
        if len(self.shapes) >= MAX_SHAPES:
            raise GerberError(f'more than {MAX_SHAPES} primitives - aborting')
        pts = [t['pts'][0]]
        for p in t['pts'][1:]:
            if math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 1e-9:
                pts.append(p)
        if len(pts) < 2:
            # a zero-length draw: the traditional way to flash a dot
            self.shapes.append({'type': 'circle', 'cx': _r4(pts[0][0]),
                                'cy': _r4(pts[0][1]), 'r': _r4(t['w'] / 2)})
            return
        pts = dp_polyline(pts, 0.01)
        self.shapes.append({'type': 'trace',
                            'pts': [[_r4(x), _r4(y)] for x, y in pts],
                            'width': _r4(t['w'])})

    # ---- extended commands ----------------------------------------
    def ext(self, cmd):
        # while an AM definition is open, statements starting with a
        # digit, '$' or '0' (primitives, assignments, comments) belong to
        # the macro body; any named command ends the collection
        if self._am is not None:
            if cmd[:1] in '0123456789$':
                if not cmd.startswith('0'):        # '0 ...' is a comment
                    self.macro_defs[self._am].append(cmd)
                return
            self._am = None
        if cmd.startswith('FS'):
            m = re.match(r'FS([LT])[AI]X(\d)(\d)Y\d\d', cmd)
            if m:
                self.zeros = m.group(1)
                self.digits = int(m.group(2)) + int(m.group(3))
                self.xd = int(m.group(3))
        elif cmd.startswith('MO'):
            self.scale = 25.4 if 'IN' in cmd else 1.0
        elif cmd.startswith('AM'):
            self._am = cmd[2:].split('*')[0]
            self.macro_defs[self._am] = []
        elif cmd.startswith('ADD'):
            m = re.match(r'ADD(\d+)([A-Za-z_.$][\w.$]*)(?:,(.*))?$', cmd)
            if not m:
                return
            code = int(m.group(1))
            name = m.group(2)
            params = [float(p) for p in (m.group(3) or '').split('X') if p]
            if name in ('C', 'R', 'O', 'P'):
                self.apertures[code] = (name, params)
            elif name in self.macro_defs:
                self.apertures[code] = ('AM', (name, params))
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
        elif kind == 'AM':
            self.flash_macro(*p)
        # MACRO (unknown aperture): skipped (warned at AD time)

    def flash_macro(self, name, params):
        """Flash an AM macro aperture at the current position: evaluate
        its primitives, then emit one shape (a single primitive as-is,
        several merged via their convex hull - exact for the standard
        convex pad shapes)."""
        env = {i + 1: v for i, v in enumerate(params)}
        prims = []                       # (code, pts) with exposure on
        for stmt in self.macro_defs.get(name, ()):
            if stmt.startswith('$'):     # variable assignment $n=expr
                m = re.match(r'\$(\d+)\s*=\s*(.+)$', stmt)
                if m:
                    env[int(m.group(1))] = _macro_eval(m.group(2), env)
                continue
            try:
                args = [_macro_eval(a, env) for a in stmt.split(',')]
            except (GerberError, ValueError):
                self.warn(f'macro "{name}": unreadable statement skipped')
                continue
            code = int(args[0])
            a = args[1:]
            if code in (6, 7):
                self.warn(f'macro "{name}": moire/thermal primitive skipped')
                continue
            if not a:
                continue
            if a[0] == 0:                # exposure off = cutout
                self.warn(f'macro "{name}": exposure-off cutout ignored')
                continue
            if code == 1 and len(a) >= 4:
                pts = _rot(_ngon(a[2], a[3], a[1] / 2, 32),
                           a[4] if len(a) > 4 else 0)
            elif code in (2, 20) and len(a) >= 6:
                w2 = a[1] / 2
                dx, dy = a[4] - a[2], a[5] - a[3]
                ln = math.hypot(dx, dy) or 1.0
                nx, ny = -dy / ln * w2, dx / ln * w2
                pts = _rot([(a[2] + nx, a[3] + ny), (a[4] + nx, a[5] + ny),
                            (a[4] - nx, a[5] - ny), (a[2] - nx, a[3] - ny)],
                           a[6] if len(a) > 6 else 0)
            elif code == 21 and len(a) >= 5:
                w2, h2 = a[1] / 2, a[2] / 2
                cx, cy = a[3], a[4]
                pts = _rot([(cx - w2, cy - h2), (cx + w2, cy - h2),
                            (cx + w2, cy + h2), (cx - w2, cy + h2)],
                           a[5] if len(a) > 5 else 0)
            elif code == 22 and len(a) >= 5:
                w, h = a[1], a[2]
                x0, y0 = a[3], a[4]
                pts = _rot([(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)],
                           a[5] if len(a) > 5 else 0)
            elif code == 4 and len(a) >= 4:
                n = int(a[1])
                coords = a[2:2 + 2 * (n + 1)]
                pts = list(zip(coords[0::2], coords[1::2]))
                if len(pts) > 1 and pts[0] == pts[-1]:
                    pts = pts[:-1]
                pts = _rot(pts, a[2 + 2 * (n + 1)] if len(a) > 2 + 2 * (n + 1) else 0)
            elif code == 5 and len(a) >= 5:
                pts = _ngon(a[2], a[3], a[4] / 2, int(a[1]),
                            a[5] if len(a) > 5 else 0)
            else:
                self.warn(f'macro "{name}": primitive {code} skipped')
                continue
            if len(pts) >= 3:
                prims.append((code, pts))
        if not prims:
            self.warn(f'macro "{name}": flash produced no copper')
            return
        s = self.scale
        x, y = self.x * s, self.y * s
        if len(prims) == 1:
            pts = prims[0][1]
        else:
            pts = _hull(p for _c, ps in prims for p in ps)
        self.emit({'type': 'poly',
                   'pts': [[_r4(x + px * s), _r4(y + py * s)] for px, py in pts]})

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
            self.flush_trace()
            return
        if cmd == 'G36':
            self.flush_trace()
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
            self.flush_trace()
            self.ap = int(d)
            return
        op = int(d) if d is not None else None
        if op == 3:
            self.flush_trace()
            self.x, self.y = tx, ty
            self.flash()
            return
        if op == 2:
            self.flush_trace()
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
                    s = self.scale
                    wmm = w * s
                    prev = (self.x * s, self.y * s)
                    t = self.trace
                    if not (t and abs(t['w'] - wmm) < 1e-9 and t['dark'] == self.dark
                            and math.hypot(t['pts'][-1][0] - prev[0],
                                           t['pts'][-1][1] - prev[1]) < 1e-6):
                        self.flush_trace()
                        self.trace = {'w': wmm, 'pts': [prev], 'dark': self.dark}
                    self.trace['pts'] += [(p[0] * s, p[1] * s) for p in pts]
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
    g.flush_trace()
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
        elif s['type'] == 'trace':
            r = s['width'] / 2
            xs += [p[0] - r for p in s['pts']] + [p[0] + r for p in s['pts']]
            ys += [p[1] - r for p in s['pts']] + [p[1] + r for p in s['pts']]
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
        # the reference/comments layer is never fabricated
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
