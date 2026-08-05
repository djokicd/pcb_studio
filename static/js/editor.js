/* Canvas editor: 2D top view of the PCB, world units mm, y-axis up.
   Shape types: rect, circle, segment (pie), arc (annular), poly.
   Plus vias, components and lumped ports. */
'use strict';

const LAYER_COLORS = ['#e07840', '#4a90d9', '#c9b458', '#9085e9', '#d55181', '#199e70'];
const LAYER_COLORS_DEFAULT = [...LAYER_COLORS];
/* editor canvas palettes; ED is mutated by applyTheme()/applyColorPrefs() */
const ED_THEMES = {
  dark: {
    port: '#0ca30c', msl: '#0ca378', pin: '#9085e9',
    substrate: '#1f4030', boardEdge: '#5c8a6e',
    grid: '#20201f', gridMajor: '#2c2c2a', select: '#ffffff', bg: '#141413',
    via: '#c3c2b7', comp: '#3a3a38', compCap: '#b9b9b3',
    mesh: 'rgba(120,180,255,0.20)', text: '#898781', ink: '#e8e7e0',
    measure: '#f2c94c', overlay: 'rgba(13,13,13,0.82)',
    overlayEdge: 'rgba(255,255,255,0.14)', selectFill: 'rgba(57,135,229,0.12)',
  },
  light: {
    port: '#0a8a0a', msl: '#0a8a66', pin: '#6a5bd8',
    substrate: '#dcebdd', boardEdge: '#7fae8e',
    grid: '#e9e7df', gridMajor: '#d9d7cc', select: '#1c1c1a', bg: '#f7f6f1',
    via: '#6f6e66', comp: '#c9c7bd', compCap: '#6f6e66',
    mesh: 'rgba(40,110,220,0.30)', text: '#77756d', ink: '#1c1c1a',
    measure: '#a9770a', overlay: 'rgba(255,255,255,0.90)',
    overlayEdge: 'rgba(0,0,0,0.15)', selectFill: 'rgba(47,111,202,0.10)',
  },
};
const ED = { ...ED_THEMES.dark };

function hexRgba(hex, a) {
  const v = parseInt(hex.slice(1), 16);
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
}

function arcPts(cx, cy, r, a0, a1, n) {
  while (a1 <= a0) a1 += 360;
  n = n || Math.max(8, Math.round((a1 - a0) / 360 * 64));
  const pts = [];
  for (let k = 0; k <= n; k++) {
    const a = (a0 + (a1 - a0) * k / n) * Math.PI / 180;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}

function outlinePts(s) {
  switch (s.type || 'rect') {
    case 'rect':
      return [[s.x, s.y], [s.x + s.w, s.y], [s.x + s.w, s.y + s.h], [s.x, s.y + s.h]];
    case 'circle':
      return arcPts(s.cx, s.cy, s.r, 0, 360, 48).slice(0, -1);
    case 'segment':
      return [[s.cx, s.cy]].concat(arcPts(s.cx, s.cy, s.r, s.a0, s.a1));
    case 'arc': {
      const outer = arcPts(s.cx, s.cy, s.r1, s.a0, s.a1);
      const inner = arcPts(s.cx, s.cy, s.r0, s.a0, s.a1).reverse();
      return outer.concat(inner);
    }
    case 'poly':
      return s.pts.map(p => [p[0], p[1]]);
  }
  return [];
}

function pointInPoly(px, py, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i], [xj, yj] = pts[j];
    if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function boundsOf(pts) {
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

class Editor {
  constructor(canvas, app) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.app = app;
    this.view = { scale: 8, ox: 60, oy: 60 };
    this.drag = null;
    this.pendingPoly = null;   // [[x,y],...] while the polygon tool collects points
    this.mouse = null;
    this.measure = null;       // {x0,y0,x1,y1} of the measure tool

    canvas.addEventListener('mousedown', e => this.onDown(e));
    canvas.addEventListener('dblclick', e => this.onDblClick(e));
    window.addEventListener('mousemove', e => this.onMove(e));
    window.addEventListener('mouseup', e => this.onUp(e));
    canvas.addEventListener('wheel', e => this.onWheel(e), { passive: false });
    canvas.addEventListener('contextmenu', e => e.preventDefault());

    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
  }

  resize() {
    const r = this.canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, r.width * dpr);
    this.canvas.height = Math.max(1, r.height * dpr);
    this.dpr = dpr;
    this.render();
  }

  toScreen(x, y) { return [this.view.ox + x * this.view.scale, this.view.oy - y * this.view.scale]; }
  toWorld(sx, sy) { return [(sx - this.view.ox) / this.view.scale, (this.view.oy - sy) / this.view.scale]; }
  eventPos(e) {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }
  snap(v) {
    const s = this.app.snapStep;
    return s > 0 ? Math.round(v / s) * s : Math.round(v * 1000) / 1000;
  }
  /* axis-aware grid snap: the snap mode restricts snapping to x or y ticks */
  snapAxis(v, axis) {
    const s = this.app.snapStep;
    const mode = this.app.snapMode || 'xy';
    const on = s > 0 && (mode === 'xy' || mode === axis);
    return on ? Math.round(v / s) * s : Math.round(v * 1000) / 1000;
  }
  /* point snap: object corners win (when enabled), then the grid */
  snapPt(wx, wy) {
    if (this.app.snapCorners) {
      const c = this.nearestCorner(wx, wy, 9 / this.view.scale);
      if (c) return c;
    }
    return [this.snapAxis(wx, 'x'), this.snapAxis(wy, 'y')];
  }
  nearestCorner(wx, wy, tol) {
    let best = null, bd = tol;
    let n = 0;
    const consider = (x, y) => {
      const d = Math.hypot(x - wx, y - wy);
      if (d < bd) { bd = d; best = [x, y]; }
    };
    for (const { kind, obj } of this.objects()) {
      if (++n > 4000) break;
      if (kind === 'via') { consider(obj.x, obj.y); continue; }
      if (kind === 'component') {
        const [x0, y0, x1, y1] = this.app.compBody(obj);
        consider(x0, y0); consider(x1, y0); consider(x1, y1); consider(x0, y1);
        consider(obj.x, obj.y);
        continue;
      }
      if (kind === 'port') {
        consider(obj.x, obj.y); consider(obj.x + obj.w, obj.y);
        consider(obj.x + obj.w, obj.y + obj.h); consider(obj.x, obj.y + obj.h);
        continue;
      }
      for (const [x, y] of outlinePts(obj)) consider(x, y);
    }
    return best;
  }

  zoomFit() {
    const b = this.app.project.board;
    const r = this.canvas.parentElement.getBoundingClientRect();
    const pad = 60;
    this.view.scale = Math.max(0.5, Math.min((r.width - pad * 2) / b.width, (r.height - pad * 2) / b.height));
    this.view.ox = (r.width - b.width * this.view.scale) / 2;
    this.view.oy = (r.height + b.height * this.view.scale) / 2;
    this.render();
  }

  /* ---- object access ---- */
  objects() {
    const p = this.app.project;
    const layerRank = {};
    (this.app.conductorLayers() || []).forEach((l, i) => { layerRank[l.id] = i; });
    const shapes = [...p.shapes].sort((a, b) => (layerRank[b.layer] ?? 0) - (layerRank[a.layer] ?? 0));
    return [
      ...p.components.map(obj => ({ kind: 'component', obj })),
      ...p.ports.map(obj => ({ kind: 'port', obj })),
      ...p.vias.map(obj => ({ kind: 'via', obj })),
      ...shapes.reverse().map(obj => ({ kind: 'shape', obj })),  // topmost layer first
    ];
  }

  hitTest(wx, wy) {
    for (const it of this.objects()) {
      const { kind, obj } = it;
      if (kind === 'shape' && pointInPoly(wx, wy, outlinePts(obj))) return it;
      if (kind === 'via' && Math.hypot(wx - obj.x, wy - obj.y) <= obj.pad / 2) return it;
      if (kind === 'component') {
        const [x0, y0, x1, y1] = this.app.compBody(obj);
        if (wx >= x0 && wx <= x1 && wy >= y0 && wy <= y1) return it;
      }
      if (kind === 'port' &&
          wx >= obj.x && wx <= obj.x + obj.w && wy >= obj.y && wy <= obj.y + obj.h) return it;
    }
    return null;
  }

  selectedObj() {
    const sel = this.app.selection;
    if (!sel) return null;
    const lists = { shape: 'shapes', via: 'vias', component: 'components', port: 'ports' };
    const obj = this.app.project[lists[sel.kind]].find(o => o.id === sel.id);
    return obj ? { kind: sel.kind, obj } : null;
  }

  selBounds(sel) {
    const { kind, obj } = sel;
    if (kind === 'shape') return boundsOf(outlinePts(obj));
    if (kind === 'via') return [obj.x - obj.pad / 2, obj.y - obj.pad / 2, obj.x + obj.pad / 2, obj.y + obj.pad / 2];
    if (kind === 'component') { const [a, b, c, d] = this.app.compBody(obj); return [a, b, c, d]; }
    return [obj.x, obj.y, obj.x + obj.w, obj.y + obj.h];
  }

  translate(sel, dx, dy) {
    const { kind, obj } = sel;
    if (kind === 'shape') {
      const t = obj.type || 'rect';
      if (t === 'rect') { obj.x += dx; obj.y += dy; }
      else if (t === 'poly') obj.pts = obj.pts.map(p => [p[0] + dx, p[1] + dy]);
      else { obj.cx += dx; obj.cy += dy; }
    } else if (kind === 'port') { obj.x += dx; obj.y += dy; }
    else { obj.x += dx; obj.y += dy; }
  }

  /* ---- handles ---- */
  handles() {
    const s = this.selectedObj();
    if (!s) return [];
    const { kind, obj } = s;
    if ((kind === 'shape' && (obj.type || 'rect') === 'rect') || kind === 'port') {
      const [x0s, y0s] = this.toScreen(obj.x, obj.y + obj.h);
      const [x1s, y1s] = this.toScreen(obj.x + obj.w, obj.y);
      const cx = (x0s + x1s) / 2, cy = (y0s + y1s) / 2;
      return [
        { id: 'nw', x: x0s, y: y0s }, { id: 'n', x: cx, y: y0s }, { id: 'ne', x: x1s, y: y0s },
        { id: 'w', x: x0s, y: cy }, { id: 'e', x: x1s, y: cy },
        { id: 'sw', x: x0s, y: y1s }, { id: 's', x: cx, y: y1s }, { id: 'se', x: x1s, y: y1s },
      ];
    }
    if (kind === 'shape' && obj.type === 'circle') {
      const [hx, hy] = this.toScreen(obj.cx + obj.r, obj.cy);
      return [{ id: 'radius', x: hx, y: hy }];
    }
    if (kind === 'shape' && obj.type === 'poly') {
      return obj.pts.map((p, i) => {
        const [sx, sy] = this.toScreen(p[0], p[1]);
        return { id: 'v' + i, x: sx, y: sy };
      });
    }
    return [];
  }
  handleAt(sx, sy) {
    return this.handles().find(h => Math.abs(h.x - sx) <= 6 && Math.abs(h.y - sy) <= 6) || null;
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
    const tool = this.app.tool;

    if (tool === 'select') {
      const h = this.handleAt(sx, sy);
      if (h) {
        const sel = this.selectedObj();
        this.drag = { type: 'handle', handle: h.id, sel, orig: JSON.parse(JSON.stringify(sel.obj)) };
        return;
      }
      const hit = this.hitTest(wx, wy);
      if (hit) {
        let sels;
        if (this.app.isMulti(hit.kind, hit.obj.id)) {
          sels = this.app.multiObjs();   // drag the whole multi-selection
        } else {
          this.app.select(hit.kind, hit.obj.id);
          sels = [hit];
        }
        this.drag = { type: 'move', sels, wx, wy };
      } else {
        // marquee: select every object fully inside the dragged box
        this.drag = { type: 'box', x0: wx, y0: wy, x1: wx, y1: wy };
      }
      this.render();
      return;
    }

    if (tool === 'measure') {
      const [x, y] = this.snapPt(wx, wy);
      this.measure = { x0: x, y0: y, x1: x, y1: y };
      this.drag = { type: 'measure' };
      this.render();
      return;
    }

    if (tool === 'poly') {
      const [x, y] = this.snapPt(wx, wy);
      if (!this.pendingPoly) this.pendingPoly = [];
      this.pendingPoly.push([x, y]);
      this.render();
      return;
    }

    if (tool === 'via') { const [x, y] = this.snapPt(wx, wy); this.app.createVia(x, y); return; }
    if (tool === 'comp') { const [x, y] = this.snapPt(wx, wy); this.app.createComp(x, y); return; }

    // drag-to-draw tools: rect, circle, segment, arc, port
    const [x0, y0] = this.snapPt(wx, wy);
    this.drag = { type: 'draw', tool, x0, y0, x1: x0, y1: y0 };
  }

  onDblClick(e) {
    if (this.app.tool === 'poly') this.finishPoly();
  }

  finishPoly() {
    if (this.pendingPoly && this.pendingPoly.length >= 3) {
      // drop the duplicate point a double-click adds
      const pts = this.pendingPoly;
      const n = pts.length;
      if (n > 3 && pts[n - 1][0] === pts[n - 2][0] && pts[n - 1][1] === pts[n - 2][1]) pts.pop();
      this.app.createShape('poly', { pts });
    }
    this.pendingPoly = null;
    this.render();
  }
  cancelPoly() { this.pendingPoly = null; this.render(); }

  onMove(e) {
    const [sx, sy] = this.eventPos(e);
    const [wx, wy] = this.toWorld(sx, sy);
    this.mouse = [wx, wy];
    this.app.onCursor(wx, wy);

    if (!this.drag) {
      if (this.app.tool === 'select') {
        const h = this.handleAt(sx, sy);
        this.canvas.style.cursor = h ? 'pointer' : (this.hitTest(wx, wy) ? 'move' : 'default');
      } else {
        this.canvas.style.cursor = 'crosshair';
      }
      if (this.pendingPoly) this.render();
      return;
    }

    const d = this.drag;
    if (d.type === 'pan') {
      this.view.ox = d.ox + (sx - d.sx);
      this.view.oy = d.oy + (sy - d.sy);
    } else if (d.type === 'move') {
      // translate by snapped delta from the original grab point
      const bx = this.snapAxis(d.wx, 'x'), by = this.snapAxis(d.wy, 'y');
      const nx = this.snapAxis(wx, 'x'), nyy = this.snapAxis(wy, 'y');
      const ddx = nx - bx - (d.movedX || 0), ddy = nyy - by - (d.movedY || 0);
      for (const s of d.sels) this.translate(s, ddx, ddy);
      d.movedX = nx - bx; d.movedY = nyy - by;
      this.app.onObjectChanged(false);
    } else if (d.type === 'handle') {
      this.applyHandle(d, wx, wy);
      this.app.onObjectChanged(false);
    } else if (d.type === 'draw') {
      [d.x1, d.y1] = this.snapPt(wx, wy);
    } else if (d.type === 'box') {
      d.x1 = wx; d.y1 = wy;
    } else if (d.type === 'measure') {
      [this.measure.x1, this.measure.y1] = this.snapPt(wx, wy);
    }
    this.render();
  }

  applyHandle(d, wx, wy) {
    const { kind, obj } = d.sel;
    if (d.handle === 'radius') {
      obj.r = Math.max(0.05, this.snap(Math.hypot(wx - obj.cx, wy - obj.cy)));
      return;
    }
    if (d.handle.startsWith('v')) {
      const i = parseInt(d.handle.slice(1), 10);
      obj.pts[i] = this.snapPt(wx, wy);
      return;
    }
    const o = d.orig;
    let x0 = o.x, y0 = o.y, x1 = o.x + o.w, y1 = o.y + o.h;
    if (d.handle.includes('w')) x0 = this.snapAxis(wx, 'x');
    if (d.handle.includes('e')) x1 = this.snapAxis(wx, 'x');
    if (d.handle.includes('s')) y0 = this.snapAxis(wy, 'y');
    if (d.handle.includes('n')) y1 = this.snapAxis(wy, 'y');
    obj.x = Math.min(x0, x1); obj.y = Math.min(y0, y1);
    obj.w = Math.max(0.05, Math.abs(x1 - x0));
    obj.h = Math.max(0.05, Math.abs(y1 - y0));
  }

  onUp(e) {
    const d = this.drag;
    this.drag = null;
    if (!d) return;
    if (d.type === 'draw') this.finishDraw(d);
    else if (d.type === 'move' || d.type === 'handle') this.app.onObjectChanged(true);
    else if (d.type === 'box') this.finishBox(d);
    this.render();
  }

  finishBox(d) {
    if (Math.hypot(d.x1 - d.x0, d.y1 - d.y0) * this.view.scale < 4) {
      this.app.select(null);   // just a click on empty space
      return;
    }
    const r = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1),
               Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
    const items = this.objects().filter(it => {
      const b = this.selBounds(it);
      return b[0] >= r[0] && b[1] >= r[1] && b[2] <= r[2] && b[3] <= r[3];
    }).map(it => ({ kind: it.kind, id: it.obj.id }));
    if (!items.length) this.app.select(null);
    else if (items.length === 1) this.app.select(items[0].kind, items[0].id);
    else this.app.selectMulti(items);
  }

  finishDraw(d) {
    const dx = d.x1 - d.x0, dy = d.y1 - d.y0;
    const dist = Math.hypot(dx, dy);
    if (d.tool === 'rect' || d.tool === 'port' || d.tool === 'mslport') {
      const x = Math.min(d.x0, d.x1), y = Math.min(d.y0, d.y1);
      const w = Math.abs(dx), h = Math.abs(dy);
      if (w < 0.05 || h < 0.05) return;
      if (d.tool === 'rect') this.app.createShape('rect', { x, y, w, h });
      else if (d.tool === 'mslport') this.app.createMslPort(x, y, w, h);
      else this.app.createPort(x, y, w, h);
      return;
    }
    if (dist < 0.05) return;
    const r = this.snap(dist) || dist;
    const ang = Math.atan2(dy, dx) * 180 / Math.PI;
    if (d.tool === 'circle') {
      this.app.createShape('circle', { cx: d.x0, cy: d.y0, r });
    } else if (d.tool === 'segment') {
      this.app.createShape('segment', { cx: d.x0, cy: d.y0, r, a0: Math.round(ang - 45), a1: Math.round(ang + 45) });
    } else if (d.tool === 'arc') {
      this.app.createShape('arc', {
        cx: d.x0, cy: d.y0, r0: Math.max(0.05, this.snap(r * 0.65)), r1: r,
        a0: Math.round(ang - 30), a1: Math.round(ang + 30),
      });
    }
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
    this.app.onCursor(wx, wy);
    this.render();
  }

  /* ---- rendering ---- */
  pathOf(pts) {
    const ctx = this.ctx;
    ctx.beginPath();
    pts.forEach(([x, y], i) => {
      const [sx, sy] = this.toScreen(x, y);
      i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
    });
    ctx.closePath();
  }

  render() {
    const ctx = this.ctx;
    const w = this.canvas.width / this.dpr, h = this.canvas.height / this.dpr;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = ED.bg;
    ctx.fillRect(0, 0, w, h);
    if (this.app.gridVisible !== false) this.drawGrid(w, h);

    const p = this.app.project;
    const b = p.board;
    const [bx, by] = this.toScreen(0, b.height);
    ctx.fillStyle = ED.substrate;
    ctx.fillRect(bx, by, b.width * this.view.scale, b.height * this.view.scale);
    ctx.strokeStyle = ED.boardEdge;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(bx, by, b.width * this.view.scale, b.height * this.view.scale);

    // conductor fills + shapes, bottom of stackup first
    const layers = this.app.conductorLayers();
    for (let li = layers.length - 1; li >= 0; li--) {
      const layer = layers[li];
      const color = layer.color;
      const isActive = layer.id === this.app.activeLayer;
      if (layer.fill) {
        ctx.globalAlpha = 0.16;
        ctx.fillStyle = color;
        ctx.fillRect(bx, by, b.width * this.view.scale, b.height * this.view.scale);
        ctx.globalAlpha = 1;
      }
      for (const s of p.shapes.filter(s => s.layer === layer.id)) {
        this.pathOf(outlinePts(s));
        ctx.globalAlpha = isActive ? 0.72 : 0.45;
        ctx.fillStyle = color;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }

    for (const v of p.vias) this.drawVia(v);
    for (const c of p.components) this.drawComp(c);
    for (const pt of p.ports) this.drawPort(pt);

    this.drawMesh();
    this.drawDragPreview();
    this.drawPendingPoly();
    this.drawSelection();
    this.drawBox();
    this.drawMeasure();
    this.drawAxes();
    this.drawZStrip(w, h);
  }

  drawBox() {
    const d = this.drag;
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

  drawMeasure() {
    const m = this.measure;
    if (!m) return;
    const ctx = this.ctx;
    const [x0, y0] = this.toScreen(m.x0, m.y0);
    const [x1, y1] = this.toScreen(m.x1, m.y1);
    ctx.strokeStyle = ED.measure;
    ctx.fillStyle = ED.measure;
    ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    // dx / dy legs
    ctx.setLineDash([3, 3]);
    ctx.globalAlpha = 0.6;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y0); ctx.lineTo(x1, y1); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.setLineDash([]);
    for (const [px, py] of [[x0, y0], [x1, y1]]) {
      ctx.beginPath(); ctx.arc(px, py, 3, 0, 7); ctx.fill();
    }
    const dx = m.x1 - m.x0, dy = m.y1 - m.y0;
    const len = Math.hypot(dx, dy);
    const txt = `L ${len.toFixed(3)} mm   Δx ${dx.toFixed(3)}   Δy ${dy.toFixed(3)}`;
    ctx.font = '11px system-ui';
    const tw = ctx.measureText(txt).width;
    const mx = (x0 + x1) / 2, my = (y0 + y1) / 2 - 14;
    ctx.fillStyle = ED.overlay;
    ctx.strokeStyle = ED.overlayEdge;
    ctx.beginPath();
    ctx.roundRect(mx - tw / 2 - 6, my - 9, tw + 12, 18, 4);
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = ED.measure;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(txt, mx, my);
  }

  /* stackup cross-section with the z mesh lines, shown while mesh preview is on */
  drawZStrip(w, h) {
    const m = this.app.meshData;
    if (!this.app.meshVisible || !m || !m.z || m.z.length < 2) return;
    const ctx = this.ctx;
    const sw = 72, sx = w - sw - 12, sy = 34, sh = h - sy - 44;
    const z0 = m.z[0], z1 = m.z[m.z.length - 1];
    const zy = z => sy + (z1 - z) / (z1 - z0) * sh;   // z up

    ctx.fillStyle = ED.overlay;
    ctx.strokeStyle = ED.overlayEdge;
    ctx.beginPath();
    ctx.roundRect(sx - 8, sy - 22, sw + 16, sh + 56, 6);
    ctx.fill(); ctx.stroke();

    // dielectric slabs + conductor sheets from the stackup
    const zinfo = this.app.stackupZ();
    for (const d of zinfo.diel) {
      ctx.fillStyle = ED.substrate;
      ctx.fillRect(sx, zy(d.z1), sw, Math.max(1, zy(d.z0) - zy(d.z1)));
    }
    // z mesh lines
    ctx.strokeStyle = ED.mesh;
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

  drawGrid(w, h) {
    const ctx = this.ctx;
    const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100];
    const step = steps.find(s => s * this.view.scale >= 14) || 100;
    const [wx0] = this.toWorld(0, 0);
    const [wx1, wy0] = this.toWorld(w, h);
    const wy1 = this.toWorld(0, 0)[1];
    ctx.lineWidth = 1;
    for (let x = Math.floor(wx0 / step) * step; x <= wx1; x += step) {
      const [sx] = this.toScreen(x, 0);
      ctx.strokeStyle = Math.abs(x % (step * 5)) < 1e-9 ? ED.gridMajor : ED.grid;
      ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, h); ctx.stroke();
    }
    for (let y = Math.floor(wy0 / step) * step; y <= wy1; y += step) {
      const [, sy] = this.toScreen(0, y);
      ctx.strokeStyle = Math.abs(y % (step * 5)) < 1e-9 ? ED.gridMajor : ED.grid;
      ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(w, sy); ctx.stroke();
    }
  }

  drawMesh() {
    const m = this.app.meshData;
    if (!m || !this.app.meshVisible) return;
    const ctx = this.ctx;
    const [x0s, y1s] = this.toScreen(m.x[0], m.y[0]);
    const [x1s, y0s] = this.toScreen(m.x[m.x.length - 1], m.y[m.y.length - 1]);
    ctx.strokeStyle = ED.mesh;
    ctx.lineWidth = 1;
    for (const x of m.x) {
      const [sx] = this.toScreen(x, 0);
      ctx.beginPath(); ctx.moveTo(sx, y0s); ctx.lineTo(sx, y1s); ctx.stroke();
    }
    for (const y of m.y) {
      const [, sy] = this.toScreen(0, y);
      ctx.beginPath(); ctx.moveTo(x0s, sy); ctx.lineTo(x1s, sy); ctx.stroke();
    }
  }

  drawVia(v) {
    const ctx = this.ctx;
    const [sx, sy] = this.toScreen(v.x, v.y);
    ctx.fillStyle = ED.via;
    ctx.beginPath(); ctx.arc(sx, sy, v.pad / 2 * this.view.scale, 0, 7); ctx.fill();
    ctx.fillStyle = ED.bg;
    ctx.beginPath(); ctx.arc(sx, sy, v.drill / 2 * this.view.scale, 0, 7); ctx.fill();
    ctx.strokeStyle = ED.text;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(sx, sy, v.pad / 2 * this.view.scale, 0, 7); ctx.stroke();
  }

  drawComp(c) {
    const ctx = this.ctx;
    const [x0, y0, x1, y1] = this.app.compBody(c);
    const [sx, sy] = this.toScreen(x0, y1);
    const w = (x1 - x0) * this.view.scale, h = (y1 - y0) * this.view.scale;
    const horiz = (c.rot || 0) % 180 === 0;
    ctx.fillStyle = ED.comp;
    ctx.fillRect(sx, sy, w, h);
    ctx.fillStyle = ED.compCap;
    const cap = 0.22 * (horiz ? w : h);
    if (horiz) {
      ctx.fillRect(sx, sy, cap, h);
      ctx.fillRect(sx + w - cap, sy, cap, h);
    } else {
      ctx.fillRect(sx, sy, w, cap);
      ctx.fillRect(sx, sy + h - cap, w, cap);
    }
    ctx.strokeStyle = ED.text;
    ctx.lineWidth = 1;
    ctx.strokeRect(sx, sy, w, h);
    if (Math.max(w, h) > 26) {
      ctx.fillStyle = ED.ink;
      ctx.font = '10px system-ui';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.app.compLabel(c), sx + w / 2, sy + h / 2 - (horiz ? 0 : 0));
    }
  }

  drawPort(p) {
    const ctx = this.ctx;
    const [sx, sy] = this.toScreen(p.x, p.y + p.h);
    const w = p.w * this.view.scale, h = p.h * this.view.scale;
    const msl = p.ptype === 'msl';
    const pin = this.app.devicePin ? this.app.devicePin(p.number) : null;
    ctx.fillStyle = pin ? hexRgba(ED.pin, 0.32)
      : msl ? hexRgba(ED.msl, 0.30) : hexRgba(ED.port, 0.30);
    ctx.fillRect(sx, sy, w, h);
    ctx.strokeStyle = pin ? ED.pin : msl ? ED.msl : ED.port;
    ctx.lineWidth = p.excite ? 2 : 1;
    ctx.strokeRect(sx, sy, w, h);
    if (pin && h > 10) {
      ctx.fillStyle = ED.pin;
      ctx.font = `${Math.max(9, Math.min(11, h * 0.35))}px system-ui`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(`${pin.ref}.${pin.name}`, sx + w / 2, sy + h + 3);
    }
    if (msl) {
      // chevrons pointing along the propagation direction (into the board)
      const cx = sx + w / 2, cy = sy + h / 2;
      const s = Math.max(4, Math.min(9, Math.min(w, h) * 0.3));
      const dirs = { '+x': 0, '-x': Math.PI, '+y': -Math.PI / 2, '-y': Math.PI / 2 };
      const a = dirs[p.orient || '+x'];
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(a);
      ctx.beginPath();
      for (const off of [-s * 0.6, s * 0.6]) {
        ctx.moveTo(off - s / 2, -s);
        ctx.lineTo(off + s / 2, 0);
        ctx.lineTo(off - s / 2, s);
      }
      ctx.stroke();
      ctx.restore();
    }
    ctx.fillStyle = pin ? ED.pin : msl ? ED.msl : ED.port;
    ctx.font = `${Math.max(10, Math.min(13, h * 0.5))}px system-ui`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(`${msl ? 'M' : 'P'}${p.number}${p.excite ? '*' : ''}`,
      sx + w / 2, msl ? sy - 7 : sy + h / 2);
  }

  drawDragPreview() {
    const d = this.drag;
    if (!d || d.type !== 'draw') return;
    const ctx = this.ctx;
    const color = d.tool === 'port' ? ED.port
      : d.tool === 'mslport' ? ED.msl
      : (this.app.layerColor(this.app.activeLayer) || ED.select);
    ctx.strokeStyle = color;
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    if (d.tool === 'rect' || d.tool === 'port' || d.tool === 'mslport') {
      const [sx, sy] = this.toScreen(Math.min(d.x0, d.x1), Math.max(d.y0, d.y1));
      ctx.strokeRect(sx, sy, Math.abs(d.x1 - d.x0) * this.view.scale, Math.abs(d.y1 - d.y0) * this.view.scale);
    } else {
      const r = Math.hypot(d.x1 - d.x0, d.y1 - d.y0) * this.view.scale;
      const [sx, sy] = this.toScreen(d.x0, d.y0);
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, 7); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(sx, sy);
      const [ex, ey] = this.toScreen(d.x1, d.y1);
      ctx.lineTo(ex, ey); ctx.stroke();
    }
    ctx.setLineDash([]);
  }

  drawPendingPoly() {
    if (!this.pendingPoly || !this.pendingPoly.length) return;
    const ctx = this.ctx;
    const color = this.app.layerColor(this.app.activeLayer) || '#fff';
    ctx.strokeStyle = color;
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    this.pendingPoly.forEach(([x, y], i) => {
      const [sx, sy] = this.toScreen(x, y);
      i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
    });
    if (this.mouse) {
      const [sx, sy] = this.toScreen(this.snap(this.mouse[0]), this.snap(this.mouse[1]));
      ctx.lineTo(sx, sy);
    }
    ctx.stroke();
    ctx.setLineDash([]);
    for (const [x, y] of this.pendingPoly) {
      const [sx, sy] = this.toScreen(x, y);
      ctx.fillStyle = color;
      ctx.fillRect(sx - 2.5, sy - 2.5, 5, 5);
    }
  }

  drawSelection() {
    const ctx = this.ctx;
    const outline = sel => {
      const [x0, y0, x1, y1] = this.selBounds(sel);
      const [sx, sy] = this.toScreen(x0, y1);
      ctx.strokeStyle = ED.select;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.2;
      ctx.strokeRect(sx, sy, (x1 - x0) * this.view.scale, (y1 - y0) * this.view.scale);
      ctx.setLineDash([]);
    };
    const multi = this.app.multiObjs ? this.app.multiObjs() : [];
    if (multi.length) {
      for (const s of multi) outline(s);
      return;
    }
    const s = this.selectedObj();
    if (!s) return;
    outline(s);
    for (const hd of this.handles()) {
      ctx.fillStyle = ED.select;
      ctx.strokeStyle = ED.bg;
      ctx.fillRect(hd.x - 3.5, hd.y - 3.5, 7, 7);
      ctx.strokeRect(hd.x - 3.5, hd.y - 3.5, 7, 7);
    }
  }

  drawAxes() {
    const ctx = this.ctx;
    const [ox, oy] = this.toScreen(0, 0);
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
