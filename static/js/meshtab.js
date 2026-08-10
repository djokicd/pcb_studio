/* Meshing tab: everything about the FDTD mesh in one place.
   A top view of the board with the exact simulated x/y mesh lines
   (fetched from /api/mesh, the same code path the run uses), axis strips
   summarising the whole domain, and mesh-only tools - place a snapped
   point, a single x or y line, or a density range. Objects can be
   picked and marquee-selected for per-object and bulk mesh overrides.
   Reuses the editor's palette (ED) and outline helpers from editor.js. */
'use strict';

/* x items vs y items get their own accents (translucent fills read in
   both themes); user-placed exact lines are a third colour */
const MREG_COLORS = { x: '#e0a040', y: '#3fbf9f' };
const MLINE_COLOR = '#d0567f';
/* canvas furniture: axis strips and the z strip inset the drawing area */
const M_LEFT = 26, M_BOT = 26, M_RIGHT = 96, M_SB = 20;
const M_SNAP_PX = 9;

/* the editor's mesh colour is tuned as a faint overlay; here the mesh IS
   the content, so bump its alpha */
function meshLineColor(alpha) {
  const m = /rgba\(([^)]+)\)/.exec(ED.mesh);
  if (!m) return ED.mesh;
  const p = m[1].split(',').map(parseFloat);
  return `rgba(${p[0]},${p[1]},${p[2]},${alpha || Math.min(0.65, (p[3] ?? 1) * 2.4)})`;
}

class MeshTab {
  constructor(canvas, app) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.app = app;
    this.view = { scale: 8, ox: 60, oy: 60 };
    this.fitted = false;
    this.drag = null;
    this.tool = 'select';
    this.sel = null;          // selected mesh item {kind:'region'|'line'|'point', id}
    this.snap = null;         // live snap preview while a placing tool is active

    canvas.addEventListener('mousedown', e => this.onDown(e));
    window.addEventListener('mousemove', e => this.onMove(e));
    window.addEventListener('mouseup', e => this.onUp(e));
    canvas.addEventListener('wheel', e => this.onWheel(e), { passive: false });
    canvas.addEventListener('contextmenu', e => e.preventDefault());
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
  }

  mesh() {
    const p = this.app.project;
    if (!p.mesh) p.mesh = {};
    const m = p.mesh;
    if (!Array.isArray(m.regions)) m.regions = [];
    if (!Array.isArray(m.lines)) m.lines = [];
    if (!Array.isArray(m.points)) m.points = [];
    if (!m.outside) m.outside = { res: null, ratio: null };
    return m;
  }
  regions() { return this.mesh().regions; }
  lines() { return this.mesh().lines; }
  points() { return this.mesh().points; }

  setTool(t) {
    this.tool = t;
    this.snap = null;
    document.querySelectorAll('#meshTools .tool')
      .forEach(b => b.classList.toggle('active', b.dataset.mtool === t));
    this.canvas.style.cursor = t === 'select' ? 'default'
      : t === 'lasso' ? 'cell' : 'crosshair';
    $('meshToolHint').textContent = MESH_TOOL_HINTS[t] || '';
    this.render();
  }

  resize() {
    const r = this.canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, r.width * dpr);
    this.canvas.height = Math.max(1, r.height * dpr);
    this.dpr = dpr;
    this.render();
  }

  size() {
    return [this.canvas.width / (this.dpr || 1), this.canvas.height / (this.dpr || 1)];
  }
  toScreen(x, y) { return [this.view.ox + x * this.view.scale, this.view.oy - y * this.view.scale]; }
  toWorld(sx, sy) { return [(sx - this.view.ox) / this.view.scale, (this.view.oy - sy) / this.view.scale]; }
  eventPos(e) {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  zoomFit() {
    const b = this.app.project.board;
    const r = this.canvas.parentElement.getBoundingClientRect();
    const pad = 34;
    const availW = r.width - M_LEFT - M_RIGHT - pad * 2;
    const availH = r.height - M_BOT - M_SB - pad * 2;
    this.view.scale = Math.max(0.5, Math.min(availW / b.width, availH / b.height));
    this.view.ox = M_LEFT + (availW - b.width * this.view.scale) / 2 + pad;
    this.view.oy = pad + (availH + b.height * this.view.scale) / 2;
    this.fitted = true;
    this.render();
  }

  /* ---- snapping to existing geometry ----
     Candidate corners/edges are cached per geometry version, so hovering
     stays cheap on imported boards with hundreds of shapes. */
  snapData() {
    const ver = this.app._geomVer || 0;
    if (this._snapCache && this._snapCache.ver === ver) return this._snapCache;
    const p = this.app.project;
    const pts = [], xs = new Set(), ys = new Set();
    const addPt = (x, y) => { pts.push([x, y]); xs.add(x); ys.add(y); };
    const b = p.board;
    for (const x of [0, b.width]) xs.add(x);
    for (const y of [0, b.height]) ys.add(y);
    for (const s of p.shapes) {
      if (s.layer === REF_LAYER) continue;
      for (const [x, y] of outlinePts(s)) addPt(x, y);
    }
    for (const v of p.vias) {
      addPt(v.x, v.y);
      xs.add(v.x - v.pad / 2); xs.add(v.x + v.pad / 2);
      ys.add(v.y - v.pad / 2); ys.add(v.y + v.pad / 2);
    }
    for (const c of p.components) {
      const [x0, y0, x1, y1] = this.app.compBody(c);
      addPt(x0, y0); addPt(x1, y0); addPt(x1, y1); addPt(x0, y1);
      addPt(c.x, c.y);
    }
    for (const q of p.ports) {
      addPt(q.x, q.y); addPt(q.x + q.w, q.y);
      addPt(q.x + q.w, q.y + q.h); addPt(q.x, q.y + q.h);
    }
    this._snapCache = { ver, pts, xs: [...xs].sort((a, c) => a - c),
                        ys: [...ys].sort((a, c) => a - c) };
    return this._snapCache;
  }

  /* nearest geometry corner within the pixel tolerance, else null */
  snapPoint(wx, wy) {
    const tol = M_SNAP_PX / this.view.scale;
    let best = null, bd = tol;
    for (const [x, y] of this.snapData().pts) {
      const d = Math.hypot(x - wx, y - wy);
      if (d < bd) { bd = d; best = [x, y]; }
    }
    return best;
  }
  /* nearest geometry coordinate on one axis, else null */
  snapCoord(w, axis, tolWorld) {
    const tol = tolWorld != null ? tolWorld : M_SNAP_PX / this.view.scale;
    const list = axis === 'y' ? this.snapData().ys : this.snapData().xs;
    let best = null, bd = tol;
    for (const v of list) {
      const d = Math.abs(v - w);
      if (d < bd) { bd = d; best = v; }
    }
    return best;
  }

  /* ---- hit testing ----
     The board view picks objects only; ranges and pinned lines belong to
     the axis strips, so a click on the board can never grab a band. */
  objectAt(wx, wy) {
    const p = this.app.project;
    for (const v of p.vias)
      if (Math.hypot(wx - v.x, wy - v.y) <= v.pad / 2) return { kind: 'via', obj: v };
    for (const c of p.components) {
      const [x0, y0, x1, y1] = this.app.compBody(c);
      if (wx >= x0 && wx <= x1 && wy >= y0 && wy <= y1) return { kind: 'component', obj: c };
    }
    for (const q of p.ports)
      if (wx >= q.x && wx <= q.x + q.w && wy >= q.y && wy <= q.y + q.h)
        return { kind: 'port', obj: q };
    for (const s of p.shapes) {
      if (s.layer === REF_LAYER) continue;
      if (pointInPoly(wx, wy, outlinePts(s))) return { kind: 'shape', obj: s };
    }
    return null;
  }

  objBounds(kind, obj) {
    if (kind === 'shape') return boundsOf(outlinePts(obj));
    if (kind === 'via') return [obj.x - obj.pad / 2, obj.y - obj.pad / 2,
                                obj.x + obj.pad / 2, obj.y + obj.pad / 2];
    if (kind === 'component') return this.app.compBody(obj);
    if (kind === 'port') return [obj.x, obj.y, obj.x + obj.w, obj.y + obj.h];
    return null;
  }

  /* the meshed domain shown by the axis strips (mesh extent when known,
     board otherwise) */
  domain() {
    const m = this.app.meshData;
    const b = this.app.project.board;
    if (m && m.x && m.x.length > 1)
      return { x0: m.x[0], x1: m.x[m.x.length - 1], y0: m.y[0], y1: m.y[m.y.length - 1] };
    return { x0: 0, x1: b.width, y0: 0, y1: b.height };
  }
  stripGeom() {
    const [w, h] = this.size();
    const top = 30, bot = h - M_SB - M_BOT;
    return { xs: M_LEFT, xw: Math.max(10, w - M_LEFT - M_RIGHT),
             xy: bot, xh: M_BOT,
             ys: top, yh: Math.max(10, bot - top), yx: 0, yw: M_LEFT };
  }
  /* which strip a screen point is in ('x' bottom, 'y' left), else null */
  stripAt(sx, sy) {
    const g = this.stripGeom();
    if (sy >= g.xy && sy <= g.xy + g.xh && sx >= g.xs) return 'x';
    if (sx >= g.yx && sx <= g.yx + g.yw && sy >= g.ys && sy <= g.ys + g.yh) return 'y';
    return null;
  }
  /* world <-> position along a strip (in canvas pixels) */
  stripPx(axis, w) {
    const g = this.stripGeom(), d = this.domain();
    return axis === 'y'
      ? g.ys + (d.y1 - w) / ((d.y1 - d.y0) || 1) * g.yh
      : g.xs + (w - d.x0) / ((d.x1 - d.x0) || 1) * g.xw;
  }
  stripWorld(axis, px) {
    const g = this.stripGeom(), d = this.domain();
    return axis === 'y'
      ? d.y1 - (px - g.ys) / g.yh * (d.y1 - d.y0)
      : d.x0 + (px - g.xs) / g.xw * (d.x1 - d.x0);
  }
  stripScale(axis) {           // mm per pixel along the strip
    const g = this.stripGeom(), d = this.domain();
    return axis === 'y' ? (d.y1 - d.y0) / g.yh : (d.x1 - d.x0) / g.xw;
  }

  /* the range / line / point under a strip position. Band edges and
     line ticks win over band interiors, so a band can never block the
     thing drawn on top of it. */
  stripItemAt(axis, px) {
    const TOL = 4;
    const mine = o => (o.axis === 'y') === (axis === 'y');
    for (const r of [...this.regions()].reverse()) {
      if (r.off || !mine(r)) continue;
      for (const edge of ['from', 'to'])
        if (Math.abs(px - this.stripPx(axis, r[edge])) <= TOL)
          return { kind: 'region', obj: r, edge };
    }
    for (const l of [...this.lines()].reverse()) {
      if (l.off || !mine(l)) continue;
      if (Math.abs(px - this.stripPx(axis, l.at)) <= TOL)
        return { kind: 'line', obj: l };
    }
    for (const p of [...this.points()].reverse()) {
      if (p.off) continue;
      if (Math.abs(px - this.stripPx(axis, axis === 'y' ? p.y : p.x)) <= TOL)
        return { kind: 'point', obj: p };
    }
    for (const r of [...this.regions()].reverse()) {
      if (r.off || !mine(r)) continue;
      const a = this.stripPx(axis, r.from), b = this.stripPx(axis, r.to);
      if (px > Math.min(a, b) && px < Math.max(a, b))
        return { kind: 'region', obj: r, edge: null };
    }
    return null;
  }

  /* ---- mouse ---- */
  onDown(e) {
    const [sx, sy] = this.eventPos(e);
    const [wx, wy] = this.toWorld(sx, sy);
    if (e.button === 1 || e.button === 2) {
      this.drag = { type: 'pan', sx, sy, ox: this.view.ox, oy: this.view.oy };
      e.preventDefault();
      return;
    }
    if (e.button !== 0) return;

    // the axis strips own the ranges and pinned lines - select, move and
    // resize them there; the board view is only ever about objects
    const strip = this.stripAt(sx, sy);
    if (strip) { this.onStripDown(strip, strip === 'y' ? sy : sx); return; }

    // marquee tools: drag out a shape, then take whatever it encloses
    if (this.tool === 'box' || this.tool === 'lasso') {
      this.sel = null;
      this.drag = this.tool === 'box'
        ? { type: 'box', x0: wx, y0: wy, x1: wx, y1: wy, add: e.shiftKey, touch: e.altKey }
        : { type: 'lasso', pts: [[wx, wy]], add: e.shiftKey, touch: e.altKey };
      renderMeshItems();
      this.render();
      return;
    }
    if (this.tool !== 'select') { this.placeWithTool(wx, wy); return; }

    const hit = this.objectAt(wx, wy);
    if (hit) {
      this.sel = null;
      if (e.shiftKey) {
        const items = this.app.multi.length ? [...this.app.multi]
          : (this.app.selection ? [this.app.selection] : []);
        const at = items.findIndex(m => m.kind === hit.kind && m.id === hit.obj.id);
        if (at >= 0) items.splice(at, 1);
        else items.push({ kind: hit.kind, id: hit.obj.id });
        if (items.length === 1) this.app.select(items[0].kind, items[0].id);
        else this.app.selectMulti(items);
      } else {
        this.app.select(hit.kind, hit.obj.id);
      }
      renderMeshItems();
      this.render();
      return;
    }
    // empty space: marquee-select objects (matching the editor)
    this.sel = null;
    this.drag = { type: 'box', x0: wx, y0: wy, x1: wx, y1: wy, add: e.shiftKey };
    renderMeshItems();
    this.render();
  }

  /* place whatever the active tool makes, snapped to geometry */
  placeWithTool(wx, wy) {
    const m = this.mesh();
    const b = this.app.project.board;
    const id = this.app.project.nextId++;
    if (this.tool === 'point') {
      const p = this.snapPoint(wx, wy) || [wx, wy];
      m.points.push({ id, x: round3(p[0]), y: round3(p[1]) });
      this.sel = { kind: 'point', id };
    } else if (this.tool === 'xline' || this.tool === 'yline') {
      const axis = this.tool === 'yline' ? 'y' : 'x';
      const raw = axis === 'y' ? wy : wx;
      const at = this.snapCoord(raw, axis);
      const lim = axis === 'y' ? b.height : b.width;
      m.lines.push({ id, axis, at: round3(Math.min(Math.max(at == null ? raw : at, 0), lim)) });
      this.sel = { kind: 'line', id };
    } else {
      const axis = this.tool === 'yrange' ? 'y' : 'x';
      const w0 = round3(axis === 'y' ? wy : wx);
      // the resolution is set on mouse-up, once the interval is known
      const r = { id, axis, from: w0, to: w0, res: this.defaultRes() };
      m.regions.push(r);
      this.sel = { kind: 'region', id };
      this.drag = { type: 'redge', r, edge: 'to', created: true };
      renderMeshItems();
      this.render();
      return;                       // resolution commits on mouse-up
    }
    this.setTool('select');
    renderMeshItems();
    this.app.dirty();
    this.app.refreshMesh();
  }

  /* mouse-down inside an axis strip: select what is there and start the
     matching drag (move / resize a range, slide a pinned line), or drag
     out a new range when a range tool is active */
  onStripDown(axis, px) {
    const w = round3(this.stripWorld(axis, px));
    if (this.tool === (axis === 'y' ? 'yrange' : 'xrange')) {
      const r = { id: this.app.project.nextId++, axis, from: w, to: w,
                  res: this.defaultRes() };
      this.mesh().regions.push(r);
      this.sel = { kind: 'region', id: r.id };
      this.app.select(null);
      this.drag = { type: 'sredge', r, edge: 'to', axis, created: true };
      renderMeshItems();
      this.render();
      return;
    }
    const hit = this.stripItemAt(axis, px);
    this.app.select(null);
    if (!hit) { this.sel = null; renderMeshItems(); this.render(); return; }
    this.sel = { kind: hit.kind, id: hit.obj.id };
    if (hit.kind === 'region') {
      this.drag = hit.edge
        ? { type: 'sredge', r: hit.obj, edge: hit.edge, axis }
        : { type: 'srmove', r: hit.obj, axis, w0: w,
            from0: hit.obj.from, to0: hit.obj.to };
    } else {
      this.drag = { type: 'sitem', it: hit, axis };
    }
    renderMeshItems();
    this.render();
  }

  /* world position for a strip drag, snapped to nearby geometry (the
     strip is coarse; the numeric fields remain the precise route) */
  stripDragWorld(axis, px) {
    const w = this.stripWorld(axis, px);
    const snapped = this.snapCoord(w, axis, this.stripScale(axis) * 4);
    return round3(snapped == null ? w : snapped);
  }

  onMove(e) {
    const [sx, sy] = this.eventPos(e);
    const [wx, wy] = this.toWorld(sx, sy);
    const [w, h] = this.size();
    const over = sx >= 0 && sy >= 0 && sx <= w && sy <= h;
    if (over) this.updateStatus(wx, wy);

    if (!this.drag) {
      if (!over) return;
      if (this.tool === 'box' || this.tool === 'lasso') {
        // marquee tools have nothing to preview until the drag starts
        if (this.snap) { this.snap = null; this.render(); }
        return;
      }
      if (this.tool !== 'select') {
        // live snap preview
        const prev = JSON.stringify(this.snap);
        if (this.tool === 'point') {
          const p = this.snapPoint(wx, wy);
          this.snap = { kind: 'point', x: p ? p[0] : wx, y: p ? p[1] : wy, hit: !!p };
        } else if (this.tool === 'xline' || this.tool === 'yline') {
          const axis = this.tool === 'yline' ? 'y' : 'x';
          const v = this.snapCoord(axis === 'y' ? wy : wx, axis);
          this.snap = { kind: 'line', axis, at: v == null ? (axis === 'y' ? wy : wx) : v,
                        hit: v != null };
        } else {
          this.snap = { kind: 'range', axis: this.tool === 'yrange' ? 'y' : 'x',
                        at: this.tool === 'yrange' ? wy : wx, hit: false };
        }
        if (JSON.stringify(this.snap) !== prev) this.render();
        return;
      }
      const strip = this.stripAt(sx, sy);
      if (strip) {
        const hit = this.stripItemAt(strip, strip === 'y' ? sy : sx);
        this.canvas.style.cursor = !hit ? 'default'
          : hit.kind === 'region' && !hit.edge ? 'move'
          : strip === 'y' ? 'ns-resize' : 'ew-resize';
      } else {
        this.canvas.style.cursor = this.objectAt(wx, wy) ? 'pointer' : 'default';
      }
      return;
    }

    const d = this.drag;
    if (d.type === 'pan') {
      this.view.ox = d.ox + (sx - d.sx);
      this.view.oy = d.oy + (sy - d.sy);
    } else if (d.type === 'redge') {
      d.r[d.edge] = round3(d.r.axis === 'y' ? wy : wx);
      renderMeshItems(true);
    } else if (d.type === 'sredge') {
      d.r[d.edge] = this.stripDragWorld(d.axis, d.axis === 'y' ? sy : sx);
      renderMeshItems(true);
    } else if (d.type === 'srmove') {
      const dw = this.stripWorld(d.axis, d.axis === 'y' ? sy : sx) - d.w0;
      d.r.from = round3(d.from0 + dw);
      d.r.to = round3(d.to0 + dw);
      renderMeshItems(true);
    } else if (d.type === 'sitem') {
      const v = this.stripDragWorld(d.axis, d.axis === 'y' ? sy : sx);
      if (d.it.kind === 'point') d.it.obj[d.axis === 'y' ? 'y' : 'x'] = v;
      else d.it.obj.at = v;
      renderMeshItems(true);
    } else if (d.type === 'box') {
      d.x1 = wx; d.y1 = wy;
    } else if (d.type === 'lasso') {
      // sample the path at ~3 screen px so the loop stays light
      const last = d.pts[d.pts.length - 1];
      if (Math.hypot(wx - last[0], wy - last[1]) * this.view.scale > 3) d.pts.push([wx, wy]);
    }
    this.render();
  }

  onUp() {
    const d = this.drag;
    this.drag = null;
    if (!d || d.type === 'pan') return;
    if (d.type === 'box') { this.finishBox(d); return; }
    if (d.type === 'lasso') { this.finishLasso(d); return; }
    if (d.r) {
      const r = d.r;
      if (r.from > r.to) [r.from, r.to] = [r.to, r.from];
      if (d.created) {
        if (r.to - r.from > 0) r.res = this.localRes(r.axis, r.from, r.to);
        else {
          this.mesh().regions = this.regions().filter(q => q.id !== r.id);
          this.sel = null;
        }
        this.setTool('select');
      }
    }
    renderMeshItems();
    this.app.dirty();
    this.app.refreshMesh();
  }

  /* every object the mesh view can select */
  candidates() {
    const p = this.app.project;
    return [
      ...p.shapes.filter(s => s.layer !== REF_LAYER).map(o => ({ kind: 'shape', obj: o })),
      ...p.vias.map(o => ({ kind: 'via', obj: o })),
      ...p.components.map(o => ({ kind: 'component', obj: o })),
      ...p.ports.map(o => ({ kind: 'port', obj: o })),
    ];
  }

  /* commit a selection, optionally merged into the current one */
  applySelection(items, add) {
    if (add) {
      const have = this.app.multi.length ? [...this.app.multi]
        : (this.app.selection ? [this.app.selection] : []);
      for (const it of items)
        if (!have.some(q => q.kind === it.kind && q.id === it.id)) have.push(it);
      items = have;
    }
    if (!items.length) this.app.select(null);
    else if (items.length === 1) this.app.select(items[0].kind, items[0].id);
    else this.app.selectMulti(items);
    this.render();
  }

  finishBox(d) {
    if (Math.hypot(d.x1 - d.x0, d.y1 - d.y0) * this.view.scale < 4) {
      // a click rather than a drag: fall back to picking what is under it
      const hit = this.objectAt(d.x0, d.y0);
      this.applySelection(hit ? [{ kind: hit.kind, id: hit.obj.id }] : [], d.add && !!hit);
      return;
    }
    const r = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1),
               Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
    const items = this.candidates().filter(it => {
      const b = this.objBounds(it.kind, it.obj);
      if (!b) return false;
      return d.touch ? !(b[2] < r[0] || b[0] > r[2] || b[3] < r[1] || b[1] > r[3])
        : (b[0] >= r[0] && b[1] >= r[1] && b[2] <= r[2] && b[3] <= r[3]);
    }).map(it => ({ kind: it.kind, id: it.obj.id }));
    this.applySelection(items, d.add);
  }

  /* freehand loop: an object is taken when its whole bounding box lies
     inside the drawn loop (Alt = anything the loop touches) */
  finishLasso(d) {
    const poly = d.pts;
    if (poly.length < 3) {
      const hit = this.objectAt(poly[0][0], poly[0][1]);
      this.applySelection(hit ? [{ kind: hit.kind, id: hit.obj.id }] : [], d.add && !!hit);
      return;
    }
    const items = this.candidates().filter(it => {
      const b = this.objBounds(it.kind, it.obj);
      if (!b) return false;
      const corners = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];
      const inside = corners.map(c => pointInPoly(c[0], c[1], poly));
      if (!d.touch) return inside.every(Boolean);
      if (inside.some(Boolean)) return true;
      if (poly.some(pt => pt[0] >= b[0] && pt[0] <= b[2] && pt[1] >= b[1] && pt[1] <= b[3]))
        return true;
      // a loop can cross a shape without any vertex or corner landing in it
      for (let i = 0; i < poly.length; i++) {
        const a = poly[i], c = poly[(i + 1) % poly.length];
        for (let k = 0; k < 4; k++)
          if (segSeg(a, c, corners[k], corners[(k + 1) % 4])) return true;
      }
      return false;
    }).map(it => ({ kind: it.kind, id: it.obj.id }));
    this.applySelection(items, d.add);
  }

  onWheel(e) {
    e.preventDefault();
    const [sx, sy] = this.eventPos(e);
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const ns = Math.min(300, Math.max(0.3, this.view.scale * factor));
    const [wx, wy] = this.toWorld(sx, sy);
    this.view.scale = ns;
    this.view.ox = sx - wx * ns;
    this.view.oy = sy + wy * ns;
    this.render();
  }

  defaultRes() {
    const m = this.app.meshData;
    const er = m && m.edgeRes ? m.edgeRes : 1.0;
    return Math.max(0.05, round3(er / 2));
  }

  /* starting resolution for a freshly dragged range: half the mesh that
     is actually there today, so the range visibly refines the interval
     instead of landing coarser than the geometry already forces */
  localRes(axis, lo, hi) {
    const m = this.app.meshData;
    const arr = m && (axis === 'y' ? m.y : m.x);
    const gaps = [];
    if (arr) {
      for (let i = 1; i < arr.length; i++) {
        const mid = (arr[i - 1] + arr[i]) / 2;
        if (mid >= lo && mid <= hi) gaps.push(arr[i] - arr[i - 1]);
      }
    }
    if (!gaps.length) return this.defaultRes();
    gaps.sort((a, b) => a - b);
    return Math.max(0.05, round3(gaps[Math.floor(gaps.length / 2)] / 2));
  }

  updateStatus(wx, wy) {
    const el = $('meshCursor');
    if (!el) return;
    let txt = `x ${wx.toFixed(2)}  y ${wy.toFixed(2)} mm`;
    const m = this.app.meshData;
    const cell = (arr, v) => {
      if (!arr || arr.length < 2 || v < arr[0] || v > arr[arr.length - 1]) return null;
      let i = 1;
      while (i < arr.length - 1 && arr[i] < v) i++;
      return arr[i] - arr[i - 1];
    };
    if (m) {
      const cx = cell(m.x, wx), cy = cell(m.y, wy);
      if (cx != null && cy != null)
        txt += `  ·  cell ${(cx * 1000).toFixed(0)} × ${(cy * 1000).toFixed(0)} µm`;
    }
    el.textContent = txt;
  }

  /* ---- rendering ---- */
  render() {
    const ctx = this.ctx;
    if (!ctx) return;
    const [w, h] = this.size();
    ctx.setTransform(this.dpr || 1, 0, 0, this.dpr || 1, 0, 0);
    ctx.fillStyle = ED.bg;
    ctx.fillRect(0, 0, w, h);

    // the board area is clipped away from the axis strips
    ctx.save();
    ctx.beginPath();
    ctx.rect(M_LEFT, 0, Math.max(0, w - M_LEFT), Math.max(0, h - M_SB - M_BOT));
    ctx.clip();
    this.drawScene(w, h);
    ctx.restore();

    this.drawStripX(w, h);
    this.drawStripY(w, h);
    this.drawZStrip(w, h);
    this.drawAxes();
  }

  drawScene(w, h) {
    const ctx = this.ctx;
    const p = this.app.project;
    const b = p.board;
    const [bx, by] = this.toScreen(0, b.height);
    ctx.fillStyle = ED.substrate;
    ctx.fillRect(bx, by, b.width * this.view.scale, b.height * this.view.scale);

    // geometry, dimmed: the mesh is the subject here
    const layers = this.app.conductorLayers();
    for (let li = layers.length - 1; li >= 0; li--) {
      const layer = layers[li];
      if (layer.fill) {
        ctx.globalAlpha = 0.10;
        ctx.fillStyle = layer.color;
        ctx.fillRect(bx, by, b.width * this.view.scale, b.height * this.view.scale);
        ctx.globalAlpha = 1;
      }
      for (const s of p.shapes.filter(s => s.layer === layer.id)) {
        this.pathOf(outlinePts(s));
        ctx.globalAlpha = 0.30;
        ctx.fillStyle = layer.color;
        ctx.fill();
        ctx.globalAlpha = 1;
      }
    }
    ctx.globalAlpha = 0.6;
    ctx.lineWidth = 1;
    for (const v of p.vias) {
      const [sx, sy] = this.toScreen(v.x, v.y);
      ctx.strokeStyle = ED.via;
      ctx.beginPath(); ctx.arc(sx, sy, v.pad / 2 * this.view.scale, 0, 7); ctx.stroke();
    }
    for (const c of p.components) {
      const [x0, y0, x1, y1] = this.app.compBody(c);
      const [sx, sy] = this.toScreen(x0, y1);
      ctx.strokeStyle = ED.compCap;
      ctx.strokeRect(sx, sy, (x1 - x0) * this.view.scale, (y1 - y0) * this.view.scale);
    }
    for (const q of p.ports) {
      const [sx, sy] = this.toScreen(q.x, q.y + q.h);
      ctx.strokeStyle = ED.port;
      ctx.strokeRect(sx, sy, q.w * this.view.scale, q.h * this.view.scale);
    }
    ctx.globalAlpha = 1;

    // the mesh itself - exact lines from the same code path the run uses
    const m = this.app.meshData;
    if (m && m.x && m.x.length > 1) {
      const [x0s, y1s] = this.toScreen(m.x[0], m.y[0]);
      const [x1s, y0s] = this.toScreen(m.x[m.x.length - 1], m.y[m.y.length - 1]);
      ctx.strokeStyle = meshLineColor();
      ctx.lineWidth = 1;
      for (const x of m.x) {
        const [lx] = this.toScreen(x, 0);
        if (lx < M_LEFT - 2 || lx > w + 2) continue;
        ctx.beginPath(); ctx.moveTo(lx, y0s); ctx.lineTo(lx, y1s); ctx.stroke();
      }
      for (const y of m.y) {
        const [, ly] = this.toScreen(0, y);
        if (ly < -2 || ly > h + 2) continue;
        ctx.beginPath(); ctx.moveTo(x0s, ly); ctx.lineTo(x1s, ly); ctx.stroke();
      }
    }

    ctx.strokeStyle = ED.boardEdge;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(bx, by, b.width * this.view.scale, b.height * this.view.scale);

    this.drawRegions(w, h);
    this.drawUserLines(w, h);
    this.drawSelection();
    this.drawSnapPreview(w, h);
    this.drawBox();
  }

  pathOf(pts) {
    const ctx = this.ctx;
    ctx.beginPath();
    pts.forEach(([x, y], i) => {
      const [sx, sy] = this.toScreen(x, y);
      i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
    });
    ctx.closePath();
  }

  isSel(kind, id) { return this.sel && this.sel.kind === kind && this.sel.id === id; }

  drawRegions(w, h) {
    const ctx = this.ctx;
    for (const r of this.regions()) {
      if (r.off) continue;
      const col = MREG_COLORS[r.axis === 'y' ? 'y' : 'x'];
      const sel = this.isSel('region', r.id);
      const horiz = r.axis === 'y';
      const [a, b] = horiz
        ? [this.toScreen(0, r.from)[1], this.toScreen(0, r.to)[1]]
        : [this.toScreen(r.from, 0)[0], this.toScreen(r.to, 0)[0]];
      const lo = Math.min(a, b), hi = Math.max(a, b);
      ctx.fillStyle = hexRgba(col, sel ? 0.20 : 0.10);
      if (horiz) ctx.fillRect(M_LEFT, lo, w, hi - lo);
      else ctx.fillRect(lo, 0, hi - lo, h);
      ctx.strokeStyle = hexRgba(col, sel ? 0.9 : 0.55);
      ctx.lineWidth = sel ? 1.6 : 1;
      ctx.setLineDash([5, 4]);
      for (const s of [lo, hi]) {
        ctx.beginPath();
        if (horiz) { ctx.moveTo(M_LEFT, s); ctx.lineTo(w, s); }
        else { ctx.moveTo(s, 0); ctx.lineTo(s, h); }
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.fillStyle = hexRgba(col, 0.95);
      ctx.font = '10px system-ui';
      const label = `${r.axis} ≤ ${fmtRes(r.res)}`;
      if (horiz) {
        ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
        ctx.fillText(label, M_LEFT + 6, lo - 2);
      } else {
        ctx.save();
        ctx.translate(lo - 3, 6);
        ctx.rotate(Math.PI / 2);
        ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
        ctx.fillText(label, 0, 0);
        ctx.restore();
      }
    }
  }

  drawUserLines(w, h) {
    const ctx = this.ctx;
    for (const ln of this.lines()) {
      if (ln.off) continue;
      const sel = this.isSel('line', ln.id);
      ctx.strokeStyle = hexRgba(MLINE_COLOR, sel ? 1 : 0.8);
      ctx.lineWidth = sel ? 2 : 1.3;
      ctx.beginPath();
      if (ln.axis === 'y') {
        const [, sy] = this.toScreen(0, ln.at);
        ctx.moveTo(M_LEFT, sy); ctx.lineTo(w, sy);
      } else {
        const [sx] = this.toScreen(ln.at, 0);
        ctx.moveTo(sx, 0); ctx.lineTo(sx, h);
      }
      ctx.stroke();
    }
    for (const pt of this.points()) {
      if (pt.off) continue;
      const sel = this.isSel('point', pt.id);
      const [sx, sy] = this.toScreen(pt.x, pt.y);
      ctx.strokeStyle = hexRgba(MLINE_COLOR, sel ? 1 : 0.8);
      ctx.lineWidth = sel ? 2 : 1.3;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(M_LEFT, sy); ctx.lineTo(w, sy);
      ctx.moveTo(sx, 0); ctx.lineTo(sx, h);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = MLINE_COLOR;
      ctx.beginPath(); ctx.arc(sx, sy, sel ? 4 : 3, 0, 7); ctx.fill();
    }
  }

  drawSnapPreview(w, h) {
    const s = this.snap;
    if (!s || this.tool === 'select' || this.tool === 'box' || this.tool === 'lasso') return;
    const ctx = this.ctx;
    ctx.strokeStyle = hexRgba(s.hit ? MLINE_COLOR : ED.select, 0.75);
    ctx.lineWidth = 1.2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    if (s.kind === 'point') {
      const [sx, sy] = this.toScreen(s.x, s.y);
      ctx.moveTo(M_LEFT, sy); ctx.lineTo(w, sy);
      ctx.moveTo(sx, 0); ctx.lineTo(sx, h);
      ctx.stroke();
      ctx.setLineDash([]);
      if (s.hit) {
        ctx.strokeStyle = MLINE_COLOR;
        ctx.lineWidth = 1.6;
        ctx.strokeRect(sx - 4.5, sy - 4.5, 9, 9);
      }
      return;
    }
    const axis = s.axis;
    if (axis === 'y') {
      const [, sy] = this.toScreen(0, s.at);
      ctx.moveTo(M_LEFT, sy); ctx.lineTo(w, sy);
    } else {
      const [sx] = this.toScreen(s.at, 0);
      ctx.moveTo(sx, 0); ctx.lineTo(sx, h);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  drawBox() {
    const d = this.drag;
    if (d && d.type === 'lasso') { this.drawLasso(d); return; }
    if (!d || d.type !== 'box') return;
    const ctx = this.ctx;
    const [sx, sy] = this.toScreen(Math.min(d.x0, d.x1), Math.max(d.y0, d.y1));
    const w = Math.abs(d.x1 - d.x0) * this.view.scale;
    const h = Math.abs(d.y1 - d.y0) * this.view.scale;
    ctx.fillStyle = ED.selectFill;
    ctx.fillRect(sx, sy, w, h);
    ctx.strokeStyle = ED.select;
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1;
    ctx.strokeRect(sx, sy, w, h);
    ctx.setLineDash([]);
  }

  drawLasso(d) {
    if (d.pts.length < 2) return;
    const ctx = this.ctx;
    ctx.beginPath();
    d.pts.forEach(([x, y], i) => {
      const [sx, sy] = this.toScreen(x, y);
      i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
    });
    ctx.closePath();
    ctx.fillStyle = ED.selectFill;
    ctx.fill();
    ctx.strokeStyle = ED.select;
    ctx.lineWidth = 1.2;
    ctx.stroke();
    // dashed hint of where the loop will close
    const [ax, ay] = this.toScreen(...d.pts[d.pts.length - 1]);
    const [bx, by] = this.toScreen(...d.pts[0]);
    ctx.setLineDash([4, 3]);
    ctx.globalAlpha = 0.6;
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.setLineDash([]);
  }

  drawSelection() {
    const ctx = this.ctx;
    const outline = (kind, obj) => {
      const b = this.objBounds(kind, obj);
      if (!b) return;
      const [sx, sy] = this.toScreen(b[0], b[3]);
      ctx.strokeStyle = ED.select;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.2;
      ctx.strokeRect(sx, sy, (b[2] - b[0]) * this.view.scale, (b[3] - b[1]) * this.view.scale);
      ctx.setLineDash([]);
    };
    const multi = this.app.multiObjs ? this.app.multiObjs() : [];
    if (multi.length) { for (const s of multi) outline(s.kind, s.obj); return; }
    const sel = this.app.selection;
    if (!sel) return;
    const obj = (this.app.project[OBJ_LISTS[sel.kind]] || []).find(o => o.id === sel.id);
    if (obj && sel.kind !== 'note') outline(sel.kind, obj);
  }

  /* x-axis strip along the bottom: the whole meshed domain at a glance -
     mesh-line density, configured ranges and lines, and the part of the
     domain currently visible above */
  drawStripX(w, h) {
    const ctx = this.ctx;
    const g = this.stripGeom(), d = this.domain();
    const m = this.app.meshData;
    const sx = v => g.xs + (v - d.x0) / (d.x1 - d.x0 || 1) * g.xw;
    ctx.fillStyle = ED.overlay;
    ctx.strokeStyle = ED.overlayEdge;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.rect(g.xs, g.xy, g.xw, g.xh); ctx.fill(); ctx.stroke();
    if (m && m.x) {
      ctx.strokeStyle = meshLineColor(0.5);
      for (const v of m.x) {
        const p = sx(v);
        ctx.beginPath(); ctx.moveTo(p, g.xy + g.xh - 7); ctx.lineTo(p, g.xy + g.xh - 1); ctx.stroke();
      }
    }
    for (const r of this.regions()) {
      if (r.off || r.axis === 'y') continue;
      const a = sx(Math.min(r.from, r.to)), b = sx(Math.max(r.from, r.to));
      const sel = this.isSel('region', r.id);
      ctx.fillStyle = hexRgba(MREG_COLORS.x, sel ? 0.55 : 0.32);
      ctx.fillRect(a, g.xy + 2, Math.max(1.5, b - a), g.xh - 11);
      if (sel) {
        ctx.strokeStyle = MREG_COLORS.x;
        ctx.strokeRect(a, g.xy + 2, Math.max(1.5, b - a), g.xh - 11);
      }
    }
    const tick = v => {
      const p = sx(v);
      ctx.beginPath(); ctx.moveTo(p, g.xy + 1); ctx.lineTo(p, g.xy + g.xh - 8); ctx.stroke();
    };
    for (const ln of this.lines()) {
      if (ln.off || ln.axis === 'y') continue;
      ctx.strokeStyle = hexRgba(MLINE_COLOR, this.isSel('line', ln.id) ? 1 : 0.75);
      ctx.lineWidth = this.isSel('line', ln.id) ? 2 : 1.2;
      tick(ln.at);
    }
    for (const pt of this.points()) {
      if (pt.off) continue;
      ctx.strokeStyle = hexRgba(MLINE_COLOR, this.isSel('point', pt.id) ? 1 : 0.75);
      ctx.lineWidth = this.isSel('point', pt.id) ? 2 : 1.2;
      tick(pt.x);
    }
    // viewport bracket
    const [vw0] = this.toWorld(M_LEFT, 0);
    const [vw1] = this.toWorld(w - M_RIGHT, 0);
    ctx.strokeStyle = ED.select;
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.55;
    ctx.strokeRect(Math.max(g.xs, sx(vw0)), g.xy + 0.5,
      Math.min(g.xs + g.xw, sx(vw1)) - Math.max(g.xs, sx(vw0)), g.xh - 1);
    ctx.globalAlpha = 1;
    ctx.fillStyle = ED.text;
    ctx.font = '9px system-ui';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.fillText(`x ${d.x0.toFixed(0)}`, g.xs + 3, g.xy + g.xh / 2);
    ctx.textAlign = 'right';
    ctx.fillText(`${d.x1.toFixed(0)} mm`, g.xs + g.xw - 3, g.xy + g.xh / 2);
  }

  drawStripY(w, h) {
    const ctx = this.ctx;
    const g = this.stripGeom(), d = this.domain();
    const m = this.app.meshData;
    const sy = v => g.ys + (d.y1 - v) / (d.y1 - d.y0 || 1) * g.yh;
    ctx.fillStyle = ED.overlay;
    ctx.strokeStyle = ED.overlayEdge;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.rect(g.yx, g.ys, g.yw, g.yh); ctx.fill(); ctx.stroke();
    if (m && m.y) {
      ctx.strokeStyle = meshLineColor(0.5);
      for (const v of m.y) {
        const p = sy(v);
        ctx.beginPath(); ctx.moveTo(g.yx + 1, p); ctx.lineTo(g.yx + 7, p); ctx.stroke();
      }
    }
    for (const r of this.regions()) {
      if (r.off || r.axis !== 'y') continue;
      const a = sy(Math.max(r.from, r.to)), b = sy(Math.min(r.from, r.to));
      const sel = this.isSel('region', r.id);
      ctx.fillStyle = hexRgba(MREG_COLORS.y, sel ? 0.55 : 0.32);
      ctx.fillRect(g.yx + 8, a, g.yw - 10, Math.max(1.5, b - a));
      if (sel) {
        ctx.strokeStyle = MREG_COLORS.y;
        ctx.strokeRect(g.yx + 8, a, g.yw - 10, Math.max(1.5, b - a));
      }
    }
    const tick = v => {
      const p = sy(v);
      ctx.beginPath(); ctx.moveTo(g.yx + 8, p); ctx.lineTo(g.yx + g.yw - 1, p); ctx.stroke();
    };
    for (const ln of this.lines()) {
      if (ln.off || ln.axis !== 'y') continue;
      ctx.strokeStyle = hexRgba(MLINE_COLOR, this.isSel('line', ln.id) ? 1 : 0.75);
      ctx.lineWidth = this.isSel('line', ln.id) ? 2 : 1.2;
      tick(ln.at);
    }
    for (const pt of this.points()) {
      if (pt.off) continue;
      ctx.strokeStyle = hexRgba(MLINE_COLOR, this.isSel('point', pt.id) ? 1 : 0.75);
      ctx.lineWidth = this.isSel('point', pt.id) ? 2 : 1.2;
      tick(pt.y);
    }
    const [, vw1] = this.toWorld(0, 0);
    const [, vw0] = this.toWorld(0, g.xy);
    ctx.strokeStyle = ED.select;
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.55;
    const a = Math.max(g.ys, sy(vw1)), b = Math.min(g.ys + g.yh, sy(vw0));
    ctx.strokeRect(g.yx + 0.5, a, g.yw - 1, b - a);
    ctx.globalAlpha = 1;
    ctx.fillStyle = ED.text;
    ctx.font = '9px system-ui';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText('y', g.yx + g.yw / 2, g.ys + 3);
  }

  /* stackup cross-section with the z mesh lines */
  drawZStrip(w, h) {
    const m = this.app.meshData;
    if (!m || !m.z || m.z.length < 2) return;
    const ctx = this.ctx;
    const sw = 72, sx = w - sw - 12, sy = 34, sh = h - sy - M_SB - M_BOT - 24;
    if (sh < 60) return;
    const z0 = m.z[0], z1 = m.z[m.z.length - 1];
    const zy = z => sy + (z1 - z) / (z1 - z0) * sh;

    ctx.fillStyle = ED.overlay;
    ctx.strokeStyle = ED.overlayEdge;
    ctx.beginPath();
    ctx.roundRect(sx - 8, sy - 22, sw + 16, sh + 56, 6);
    ctx.fill(); ctx.stroke();

    const zinfo = this.app.stackupZ();
    for (const d of zinfo.diel) {
      ctx.fillStyle = ED.substrate;
      ctx.fillRect(sx, zy(d.z1), sw, Math.max(1, zy(d.z0) - zy(d.z1)));
    }
    ctx.strokeStyle = meshLineColor();
    ctx.lineWidth = 1;
    for (const z of m.z) {
      ctx.beginPath(); ctx.moveTo(sx, zy(z)); ctx.lineTo(sx + sw, zy(z)); ctx.stroke();
    }
    for (const c of zinfo.cond) {
      ctx.strokeStyle = this.app.layerColor(c.id);
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(sx, zy(c.z)); ctx.lineTo(sx + sw, zy(c.z)); ctx.stroke();
    }
    ctx.fillStyle = ED.text;
    ctx.font = '10px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`z mesh (${m.z.length})`, sx + sw / 2, sy - 10);
    ctx.fillText(`z ${z1.toFixed(1)} mm`, sx + sw / 2, sy - 1);
    ctx.textBaseline = 'top';
    ctx.fillText(`z ${z0.toFixed(1)} mm`, sx + sw / 2, sy + sh + 6);
  }

  drawAxes() {
    const ctx = this.ctx;
    const [ox, oy] = this.toScreen(0, 0);
    const [w, h] = this.size();
    if (ox < M_LEFT || ox > w - M_RIGHT || oy > h - M_SB - M_BOT) return;
    ctx.strokeStyle = ED.text;
    ctx.fillStyle = ED.text;
    ctx.lineWidth = 1.2;
    ctx.font = '11px system-ui';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + 30, oy); ctx.stroke();
    ctx.fillText('x', ox + 34, oy);
    ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox, oy - 30); ctx.stroke();
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText('y', ox, oy - 34);
  }
}

const MESH_TOOL_HINTS = {
  select: 'View selects objects (shift adds, drag a box for many) · '
    + 'ranges and pinned lines are selected and dragged in the bottom/left strips',
  box: 'Drag a rectangle around objects · shift adds to the selection, '
    + 'Alt takes everything the box touches',
  lasso: 'Draw a loop around objects · shift adds to the selection, '
    + 'Alt takes everything the loop touches',
  point: 'Click to pin one x and one y line — snaps to nearby geometry corners',
  xline: 'Click to pin a single x mesh line — snaps to nearby copper edges',
  yline: 'Click to pin a single y mesh line — snaps to nearby copper edges',
  xrange: 'Drag across x — on the view or in the bottom strip — to add a density range',
  yrange: 'Drag across y — on the view or in the left strip — to add a density range',
};

/* do segments ab and cd cross? (orientation test, collinear cases fall
   through to "no" - good enough for hit testing a hand-drawn loop) */
function segSeg(a, b, c, d) {
  const o = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  const d1 = o(a, b, c), d2 = o(a, b, d), d3 = o(c, d, a), d4 = o(c, d, b);
  return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0));
}

function round3(v) { return Math.round(v * 1000) / 1000; }
function fmtRes(r) { return r >= 1 ? `${r} mm` : `${Math.round(r * 1000)} µm`; }

/* ---- side panel: ranges, lines and points ---- */
function renderMeshItems(live) {
  const holder = $('meshItemList');
  if (!holder || !app.meshTab) return;
  const mt = app.meshTab;
  const regions = mt.regions(), lines = mt.lines(), points = mt.points();
  if (live) {
    // update values in place - rebuilding would kill an in-flight drag
    for (const r of regions) {
      const row = holder.querySelector(`[data-mid="region-${r.id}"]`);
      if (!row) continue;
      row.querySelector('.mr-from').value = r.from;
      row.querySelector('.mr-to').value = r.to;
    }
    for (const l of lines) {
      const row = holder.querySelector(`[data-mid="line-${l.id}"]`);
      if (row) row.querySelector('.mr-at').value = l.at;
    }
    for (const p of points) {
      const row = holder.querySelector(`[data-mid="point-${p.id}"]`);
      if (!row) continue;
      row.querySelector('.mr-px').value = p.x;
      row.querySelector('.mr-py').value = p.y;
    }
    return;
  }
  holder.textContent = '';
  const total = regions.length + lines.length + points.length;
  if (!total) {
    const p = document.createElement('p');
    p.className = 'muted mini-note';
    p.textContent = 'Nothing pinned yet — pick a tool above and click or drag on the view.';
    holder.append(p);
    return;
  }
  const commit = () => {
    renderMeshItems();
    app.dirty();
    app.refreshMesh();
    mt.render();
  };
  const num = (cls, val, title, cb) => {
    const i = document.createElement('input');
    i.type = 'number'; i.step = '0.1'; i.className = cls; i.value = val; i.title = title;
    i.onchange = () => { cb(parseFloat(i.value)); commit(); };
    return i;
  };
  const mkRow = (kind, o, col) => {
    const row = document.createElement('div');
    row.className = 'mregion' + (mt.isSel(kind, o.id) ? ' sel' : '') + (o.off ? ' off' : '');
    row.dataset.mid = `${kind}-${o.id}`;
    row.style.setProperty('--mr-col', col);
    const on = document.createElement('input');
    on.type = 'checkbox';
    on.checked = !o.off;
    on.title = 'Enable / disable without deleting';
    on.onchange = () => { o.off = !on.checked; commit(); };
    row.append(on);
    row.addEventListener('click', e => {
      if (e.target.closest('input, select, button')) return;
      mt.sel = { kind, id: o.id };
      app.select(null);
      renderMeshItems();
      mt.render();
    });
    return row;
  };
  const delBtn = (list, o, kindKey) => {
    const del = document.createElement('button');
    del.className = 'mr-del';
    del.textContent = '×';
    del.title = 'Delete';
    del.onclick = () => {
      const m = mt.mesh();
      m[kindKey] = list.filter(q => q.id !== o.id);
      if (mt.isSel(kindKey === 'regions' ? 'region' : kindKey === 'lines' ? 'line' : 'point', o.id))
        mt.sel = null;
      commit();
    };
    return del;
  };

  for (const r of regions) {
    const col = MREG_COLORS[r.axis === 'y' ? 'y' : 'x'];
    const row = mkRow('region', r, col);
    const axis = document.createElement('select');
    for (const a of ['x', 'y']) {
      const o = document.createElement('option');
      o.value = a; o.textContent = a;
      axis.append(o);
    }
    axis.value = r.axis === 'y' ? 'y' : 'x';
    axis.onchange = () => { r.axis = axis.value; commit(); };
    const from = num('mr-from', r.from, 'Interval start (mm)', v => { if (isFinite(v)) r.from = v; });
    const to = num('mr-to', r.to, 'Interval end (mm)', v => { if (isFinite(v)) r.to = v; });
    const res = num('mr-res', r.res, 'Maximum cell size inside the interval (mm)',
      v => { if (isFinite(v) && v > 0) r.res = v; });
    res.min = '0.01';
    const dash = document.createElement('span'); dash.textContent = '–';
    const at = document.createElement('span'); at.textContent = '≤'; at.title = 'resolution';
    row.append(axis, from, dash, to, at, res, delBtn(regions, r, 'regions'));
    holder.append(row);
  }
  for (const l of lines) {
    const row = mkRow('line', l, MLINE_COLOR);
    const tag = document.createElement('span');
    tag.className = 'mr-tag';
    tag.textContent = `${l.axis === 'y' ? 'y' : 'x'} line`;
    const at = num('mr-at', l.at, 'Exact mesh-line position (mm)', v => { if (isFinite(v)) l.at = v; });
    row.append(tag, at, delBtn(lines, l, 'lines'));
    holder.append(row);
  }
  for (const p of points) {
    const row = mkRow('point', p, MLINE_COLOR);
    const tag = document.createElement('span');
    tag.className = 'mr-tag';
    tag.textContent = 'point';
    const px = num('mr-px', p.x, 'Pinned x line (mm)', v => { if (isFinite(v)) p.x = v; });
    const py = num('mr-py', p.y, 'Pinned y line (mm)', v => { if (isFinite(v)) p.y = v; });
    row.append(tag, px, py, delBtn(points, p, 'points'));
    holder.append(row);
  }
}

/* ---- side panel: per-object and bulk mesh overrides ---- */
function renderMeshObjPanel() {
  const panel = $('meshObjPanel');
  if (!panel) return;
  const body = $('meshObjForm');
  body.textContent = '';
  const multi = (app.multiObjs ? app.multiObjs() : [])
    .filter(s => s.kind !== 'note' && !(s.kind === 'shape' && s.obj.layer === REF_LAYER));
  const sel = app.selection;
  const single = sel ? (app.project[OBJ_LISTS[sel.kind]] || []).find(o => o.id === sel.id) : null;
  const usable = single && sel.kind !== 'note'
    && !(sel.kind === 'shape' && single.layer === REF_LAYER);
  if (!multi.length && !usable) { panel.hidden = true; return; }
  panel.hidden = false;

  const items = multi.length ? multi : [{ kind: sel.kind, obj: single }];
  const commit = () => { app.dirty(); app.refreshMesh(); };
  const fld = (label, input) => {
    const l = document.createElement('label');
    l.append(label, input);
    return l;
  };

  const title = document.createElement('p');
  title.className = 'muted mini-note';
  if (items.length === 1) {
    const names = { shape: 'Shape', via: 'Via', component: 'Component', port: 'Port' };
    const o = items[0].obj;
    title.textContent = `${names[items[0].kind] || items[0].kind} “${o.name || o.ref || ('#' + o.id)}”`;
  } else {
    const by = {};
    for (const it of items) by[it.kind] = (by[it.kind] || 0) + 1;
    title.textContent = `${items.length} objects selected — `
      + Object.entries(by).map(([k, n]) => `${n} ${k}${n > 1 ? 's' : ''}`).join(', ')
      + '. Changes apply to all of them.';
  }
  body.append(title);

  const form = document.createElement('div');
  form.className = 'form';
  const vias = items.filter(i => i.kind === 'via');
  const shapes = items.filter(i => i.kind === 'shape');
  const bulk = items.length > 1;

  // local resolution: immediate for one object, apply/clear for many
  const resI = document.createElement('input');
  resI.type = 'number'; resI.step = '0.05'; resI.min = '0.01';
  resI.title = 'Mesh resolution at these objects, overriding the global edge resolution';
  if (bulk) {
    const vals = new Set(items.map(i => (i.obj.mesh || {}).res ?? ''));
    resI.placeholder = vals.size === 1 ? String([...vals][0] || 'default') : 'mixed';
    resI.value = '';
  } else {
    resI.placeholder = 'default';
    resI.value = (items[0].obj.mesh || {}).res ?? '';
    resI.onchange = () => {
      const o = items[0].obj;
      o.mesh = o.mesh || {};
      const v = parseFloat(resI.value);
      o.mesh.res = isFinite(v) && v > 0 ? v : null;
      commit();
    };
  }
  form.append(fld('Local resolution (mm)', resI));

  if (bulk) {
    const rowBtns = document.createElement('div');
    rowBtns.className = 'mbulk-row';
    const apply = document.createElement('button');
    apply.className = 'lr-btn';
    apply.textContent = 'apply';
    apply.title = 'Set this resolution on every selected object';
    apply.onclick = () => {
      const v = parseFloat(resI.value);
      if (!(isFinite(v) && v > 0)) return;
      for (const it of items) { it.obj.mesh = it.obj.mesh || {}; it.obj.mesh.res = v; }
      commit();
      renderMeshObjPanel();
    };
    const clear = document.createElement('button');
    clear.className = 'lr-btn';
    clear.textContent = 'clear';
    clear.title = 'Back to the global resolution for every selected object';
    clear.onclick = () => {
      for (const it of items) { it.obj.mesh = it.obj.mesh || {}; it.obj.mesh.res = null; }
      resI.value = '';
      commit();
      renderMeshObjPanel();
    };
    rowBtns.append(apply, clear);
    form.append(rowBtns);
  }

  if (shapes.length) {
    const thS = document.createElement('select');
    const opts = bulk ? [['', '— keep —'], ['auto', 'auto'], ['on', 'on'], ['off', 'off']]
      : [['auto', 'auto (on for narrow shapes)'], ['on', 'on'], ['off', 'off']];
    for (const [v, t] of opts) {
      const o = document.createElement('option');
      o.value = v; o.textContent = t;
      thS.append(o);
    }
    const cur = o => o.mesh && o.mesh.thirds === true ? 'on'
      : o.mesh && o.mesh.thirds === false ? 'off' : 'auto';
    thS.value = bulk ? '' : cur(shapes[0].obj);
    thS.title = 'Metal-edge 1/3–2/3 refinement: replaces each edge line with a '
      + 'pair 1/3 inside / 2/3 outside (captures the edge singularity). '
      + '"auto" enables it for transmission-line features — traces and shapes '
      + 'narrower than 3 mm — and leaves pads/planes coarse.';
    thS.onchange = () => {
      if (bulk && !thS.value) return;
      for (const it of shapes) {
        it.obj.mesh = it.obj.mesh || {};
        it.obj.mesh.thirds = thS.value === 'auto' ? null : thS.value === 'on';
      }
      commit();
    };
    form.append(fld(bulk ? `Edge refinement (${shapes.length})` : 'Edge refinement', thS));
  }

  // round pads take the same economy ladder as vias (and inherit it from
  // the via they sit on), so both are driven by one control
  const pads = shapes.filter(i => i.obj.type === 'circle');
  if (vias.length || pads.length) {
    const targets = [...vias, ...pads];
    const linesSel = document.createElement('select');
    const opts = bulk ? [['keep', '— keep —'], ...VIA_LINES_OPTIONS] : VIA_LINES_OPTIONS;
    for (const [v, t] of opts) {
      const o = document.createElement('option');
      o.value = v; o.textContent = t;
      linesSel.append(o);
    }
    linesSel.value = bulk ? 'keep' : String((targets[0].obj.mesh || {}).lines || '');
    linesSel.title = 'How many mesh lines this via or round pad may pin per '
      + 'axis. Fewer lines mean fewer cells — for stitching-via fences the '
      + 'barrel position matters far more than its roundness. A pad sitting '
      + 'on a via follows that via unless set here.';
    linesSel.onchange = () => {
      if (bulk && linesSel.value === 'keep') return;
      for (const it of targets) {
        it.obj.mesh = it.obj.mesh || {};
        it.obj.mesh.lines = linesSel.value ? parseInt(linesSel.value, 10) : null;
      }
      commit();
    };
    const what = [vias.length && `${vias.length} via${vias.length > 1 ? 's' : ''}`,
                  pads.length && `${pads.length} pad${pads.length > 1 ? 's' : ''}`]
      .filter(Boolean).join(' + ');
    form.append(fld(bulk ? `Mesh lines (${what})` : 'Mesh lines', linesSel));
  }
  body.append(form);
}

/* ---- wiring (called once from app init) ---- */
function initMeshTools() {
  document.querySelectorAll('#meshTools .tool').forEach(btn =>
    btn.addEventListener('click', () => app.meshTab.setTool(btn.dataset.mtool)));
  app.meshTab.setTool('select');

  const selectKind = (list, kind) => {
    const items = (app.project[list] || [])
      .filter(o => kind !== 'shape' || o.layer !== REF_LAYER)
      .map(o => ({ kind, id: o.id }));
    if (!items.length) { uiNotice(`No ${kind}s in this project.`, 'warn', 3000); return; }
    if (items.length === 1) app.select(items[0].kind, items[0].id);
    else app.selectMulti(items);
    app.meshTab.render();
  };
  $('meshFit').addEventListener('click', () => app.meshTab.zoomFit());
  $('meshRetry').addEventListener('click', () => {
    app._meshRetried = false;
    app.refreshMesh();
  });
  // recover automatically when the page comes back to life with no mesh
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && app.currentView === 'mesh' && !app.meshData) {
      app._meshRetried = false;
      app.refreshMesh();
    }
  });
  $('meshSelVias').addEventListener('click', () => selectKind('vias', 'via'));
  $('meshSelShapes').addEventListener('click', () => selectKind('shapes', 'shape'));
  $('meshSelNone').addEventListener('click', () => {
    app.select(null);
    app.meshTab.sel = null;
    renderMeshItems();
    app.meshTab.render();
  });

  for (const [id, key] of [['mo_res', 'res'], ['mo_ratio', 'ratio']]) {
    $(id).addEventListener('change', e => {
      const v = parseFloat(e.target.value);
      app.meshTab.mesh().outside[key] = isFinite(v) && v > 0 ? v : null;
      app.dirty();
      app.refreshMesh();
    });
  }
}

/* keep the outside-smoothing inputs in sync with the loaded project */
function meshFormsFromModel() {
  if (!app.meshTab) return;
  const o = app.meshTab.mesh().outside;
  $('mo_res').value = o.res ?? '';
  $('mo_ratio').value = o.ratio ?? '';
}
