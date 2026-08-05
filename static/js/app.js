/* OpenEMS PCB Studio - state, stackup editor, panels, run control, results. */
'use strict';

const $ = id => document.getElementById(id);

const PKG = { '0402': [1.0, 0.5], '0603': [1.6, 0.8], '0805': [2.0, 1.25] };
const COMP_UNITS = { R: 'Ω', C: 'pF', L: 'nH' };
const DIEL_CHIP = '#4d7a5e';

function defaultProject() {
  return {
    version: 2,
    name: 'patch_antenna',
    board: { width: 60, height: 60 },
    stackup: [
      { id: 'top', name: 'Top', type: 'conductor', thickness: 0.035, fill: false },
      { id: 'core', name: 'FR4 core', type: 'dielectric', thickness: 1.524, er: 3.38, tand: 0.002 },
      { id: 'bot', name: 'Bottom', type: 'conductor', thickness: 0.035, fill: true },
    ],
    shapes: [
      { id: 1, name: 'patch', type: 'rect', layer: 'top', x: 14, y: 10, w: 32, h: 40, priority: 10 },
    ],
    vias: [],
    components: [],
    devices: [],
    ports: [
      { id: 2, number: 1, x: 23.5, y: 29.5, w: 1, h: 1, direction: 'z',
        layerFrom: 'bot', layerTo: 'top', impedance: 50, excite: true },
    ],
    sim: {
      fStart: 1, fStop: 3, points: 401, boundary: 'MUR', endCriteria: -40,
      maxTimesteps: 30000, meshDiv: 20, edgeRes: null, meshMerge: 0.1, airMargin: 25,
      dumpJ: false, dumpFreqs: '', dumpJt: false, jtStart: 0, jtStop: 3, jtSub: 2,
    },
    nextId: 3,
  };
}

/* v1 (single substrate board) -> v2 (stackup) migration */
function migrate(p) {
  if (!p) return defaultProject();
  if (p.stackup) {
    p.vias = p.vias || [];
    p.components = p.components || [];
    p.sim.dumpJ = p.sim.dumpJ || false;
    p.sim.dumpFreqs = p.sim.dumpFreqs || '';
    p.devices = p.devices || [];
    p.sim.dumpJt = p.sim.dumpJt || false;
    if (p.sim.jtStart == null) p.sim.jtStart = 0;
    if (p.sim.jtStop == null) p.sim.jtStop = 3;
    if (!p.sim.jtSub) p.sim.jtSub = 2;
    if (p.sim.meshMerge == null) p.sim.meshMerge = 0.1;
    return p;
  }
  const b = p.board || {};
  const out = defaultProject();
  out.name = p.name || 'untitled';
  out.board = { width: b.width || 60, height: b.height || 60 };
  out.stackup[1] = { id: 'core', name: 'Substrate', type: 'dielectric',
    thickness: b.thickness || 1.524, er: b.er || 4.3, tand: b.tand || 0 };
  out.stackup[2].fill = b.groundPlane !== false;
  out.shapes = (p.shapes || []).map(s => ({
    ...s, type: 'rect', layer: s.layer === 'bottom' ? 'bot' : 'top',
  }));
  out.ports = (p.ports || []).map(q => ({
    ...q,
    layer: q.layer === 'bottom' ? 'bot' : 'top',
    layerFrom: 'bot', layerTo: 'top',
  }));
  out.sim = { ...out.sim, ...(p.sim || {}) };
  out.nextId = p.nextId || 100;
  return out;
}

const app = {
  project: defaultProject(),
  selection: null,
  tool: 'select',
  activeLayer: 'top',
  snapStep: 0.5,
  editor: null,
  meshVisible: false,
  meshData: null,
  gridVisible: true,
  polling: null,
  logOffset: 0,
  currentRunId: null,
  sparams: null,   // parsed results
  tdData: null,    // time-domain port signals
  jview: null,

  conductorLayers() {
    let i = 0;
    return this.project.stackup
      .filter(l => l.type === 'conductor')
      .map(l => ({ ...l, color: LAYER_COLORS[i++ % LAYER_COLORS.length] }));
  },
  layerColor(id) {
    const l = this.conductorLayers().find(l => l.id === id);
    return l ? l.color : '#fff';
  },
  layerName(id) {
    const l = this.project.stackup.find(l => l.id === id);
    return l ? (l.name || id) : id;
  },
  compBody(c) {
    let L, W;
    if (c.package === 'custom') { L = c.len || 1.6; W = c.wid || 0.8; }
    else { [L, W] = PKG[c.package] || PKG['0603']; }
    if ((c.rot || 0) % 180 === 0) return [c.x - L / 2, c.y - W / 2, c.x + L / 2, c.y + W / 2];
    return [c.x - W / 2, c.y - L / 2, c.x + W / 2, c.y + L / 2];
  },
  compLabel(c) { return `${c.ref} ${c.value}${COMP_UNITS[c.ctype]}`; },
  stackupZ() {
    let z = 0;
    const cond = [], diel = [];
    for (const l of [...this.project.stackup].reverse()) {
      if (l.type === 'dielectric') {
        const t = l.thickness || 0;
        diel.push({ id: l.id, z0: z, z1: z + t });
        z += t;
      } else {
        cond.push({ id: l.id, z });
      }
    }
    return { cond, diel, total: z };
  },

  /* ---------- selection / creation ---------- */
  select(kind, id) {
    this.selection = kind ? { kind, id } : null;
    renderObjList();
    renderProps();
    this.editor.render();
  },

  ensureActiveLayer() {
    const layers = this.conductorLayers();
    if (!layers.find(l => l.id === this.activeLayer) && layers.length)
      this.activeLayer = layers[0].id;
    return this.activeLayer;
  },

  createShape(type, geom) {
    const id = this.project.nextId++;
    this.project.shapes.push({
      id, name: `${type}${id}`, type, layer: this.ensureActiveLayer(),
      priority: 10, ...geom,
    });
    this.select('shape', id);
    this.dirty();
  },
  createVia(x, y) {
    const layers = this.conductorLayers();
    const id = this.project.nextId++;
    this.project.vias.push({
      id, x, y, drill: 0.6, pad: 1.2,
      from: layers[layers.length - 1].id, to: layers[0].id,
    });
    this.select('via', id);
    this.dirty();
  },
  createComp(x, y) {
    const id = this.project.nextId++;
    const n = this.project.components.length + 1;
    this.project.components.push({
      id, ref: 'R' + n, ctype: 'R', value: 50, package: '0603',
      x, y, rot: 0, layer: this.ensureActiveLayer(),
    });
    this.select('component', id);
    this.dirty();
  },
  createPort(x, y, w, h) {
    const p = this.project;
    const layers = this.conductorLayers();
    const id = p.nextId++;
    const number = p.ports.length ? Math.max(...p.ports.map(q => q.number)) + 1 : 1;
    p.ports.push({
      id, number, x, y, w, h, direction: 'z',
      layerFrom: layers[layers.length - 1].id, layerTo: layers[0].id,
      layer: this.ensureActiveLayer(),
      impedance: 50, excite: p.ports.length === 0,
    });
    this.select('port', id);
    this.dirty();
  },
  createMslPort(x, y, w, h) {
    const p = this.project;
    const layers = this.conductorLayers();
    const id = p.nextId++;
    const number = p.ports.length ? Math.max(...p.ports.map(q => q.number)) + 1 : 1;
    // propagation points inward, away from the nearest board edge
    const cx = x + w / 2, cy = y + h / 2;
    const dists = [
      [cx, '+x'], [p.board.width - cx, '-x'],
      [cy, '+y'], [p.board.height - cy, '-y'],
    ];
    dists.sort((a, b) => a[0] - b[0]);
    const strip = layers.find(l => l.id === this.activeLayer && l.id) || layers[0];
    const gnd = [...layers].reverse().find(l => l.fill && l.id !== strip.id) ||
      layers.find(l => l.id !== strip.id) || layers[layers.length - 1];
    p.ports.push({
      id, ptype: 'msl', number, x, y, w, h, orient: dists[0][1],
      layerTo: strip.id, layerFrom: gnd.id,
      impedance: 50, excite: p.ports.length === 0,
    });
    this.select('port', id);
    this.dirty();
  },

  deleteSelection() {
    const sel = this.selection;
    if (!sel) return;
    const lists = { shape: 'shapes', via: 'vias', component: 'components', port: 'ports' };
    const key = lists[sel.kind];
    this.project[key] = this.project[key].filter(o => o.id !== sel.id);
    this.select(null);
    this.dirty();
  },

  onObjectChanged(final) {
    renderProps();
    if (final) { renderObjList(); this.dirty(); }
  },

  /* ---------- clipboard ---------- */
  clipboard: null,
  copySelection(cut = false) {
    const sel = this.editor.selectedObj();
    if (!sel) { uiNotice('Nothing selected to copy.', 'warn', 2500); return false; }
    this.clipboard = { kind: sel.kind, data: JSON.parse(JSON.stringify(sel.obj)) };
    if (cut) this.deleteSelection();
    uiNotice((cut ? 'Cut' : 'Copied') + ' — Ctrl+V pastes.', 'info', 2000);
    return true;
  },
  pasteClipboard() {
    const cb = this.clipboard;
    if (!cb) { uiNotice('Clipboard is empty.', 'warn', 2500); return; }
    const p = this.project;
    const obj = JSON.parse(JSON.stringify(cb.data));
    obj.id = p.nextId++;
    const off = Math.max(this.snapStep || 0.5, 1);
    this.editor.translate({ kind: cb.kind, obj }, off, -off);
    if (cb.kind === 'port') {
      obj.number = p.ports.length ? Math.max(...p.ports.map(q => q.number)) + 1 : 1;
      obj.excite = false;
      p.ports.push(obj);
    } else if (cb.kind === 'component') {
      let n = p.components.length + 1;
      while (p.components.some(c => c.ref === obj.ctype + n)) n++;
      obj.ref = obj.ctype + n;
      p.components.push(obj);
    } else if (cb.kind === 'via') {
      p.vias.push(obj);
    } else {
      obj.name = (obj.name || obj.type || 'shape') + '_copy';
      p.shapes.push(obj);
    }
    // keep the clipboard source position so repeated pastes cascade
    cb.data = JSON.parse(JSON.stringify(obj));
    this.select(cb.kind, obj.id);
    this.dirty();
  },

  onCursor(wx, wy) {
    $('cursorPos').textContent = `x ${wx.toFixed(2)}  y ${wy.toFixed(2)} mm`;
    $('zoomInfo').textContent = `${this.editor.view.scale.toFixed(1)} px/mm`;
  },

  dirty() {
    clearTimeout(this._saveT);
    this._saveT = setTimeout(() => {
      try { localStorage.setItem('openems_pcb_project', JSON.stringify(this.project)); } catch (e) { /* ignore */ }
    }, 400);
    if (this.meshVisible) {
      clearTimeout(this._meshT);
      this._meshT = setTimeout(() => this.refreshMesh(), 600);
    }
    updateWarnings();
  },

  async refreshMesh() {
    try {
      const res = await fetch('/api/mesh', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this.project),
      });
      const m = await res.json();
      if (!res.ok) throw new Error(m.error || 'mesh failed');
      this.meshData = m;
      $('meshInfo').textContent =
        `mesh ${m.x.length}×${m.y.length}×${m.z.length} = ${(m.cells / 1000).toFixed(0)}k cells`;
    } catch (e) {
      this.meshData = null;
      $('meshInfo').textContent = 'mesh: ' + e.message;
    }
    this.editor.render();
  },
};

/* ---------- design rule warnings ---------- */
function bboxOf(pts) {
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
function rectsOverlap(a, b) {
  return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
}
function validateProject() {
  const w = [];
  const p = app.project;
  const B = [0, 0, p.board.width, p.board.height];
  const inside = bb => bb[0] >= -1e-6 && bb[1] >= -1e-6 && bb[2] <= B[2] + 1e-6 && bb[3] <= B[3] + 1e-6;
  for (const s of p.shapes) {
    if (!inside(bboxOf(outlinePts(s)))) w.push(`"${s.name || s.type}" extends outside the board outline`);
  }
  for (const v of p.vias) {
    const r = v.pad / 2;
    if (!inside([v.x - r, v.y - r, v.x + r, v.y + r])) w.push('a via extends outside the board outline');
  }
  for (const c of p.components) {
    if (!inside(app.compBody(c))) w.push(`${c.ref} extends outside the board outline`);
  }
  const copperOn = (layerId, bb) => {
    const layer = p.stackup.find(l => l.id === layerId);
    if (layer && layer.fill) return true;
    return p.shapes.some(s => s.layer === layerId && rectsOverlap(bb, bboxOf(outlinePts(s))));
  };
  for (const c of p.components) {
    const [x0, y0, x1, y1] = app.compBody(c);
    const ny = (c.rot || 0) % 180 === 0 ? 0 : 1;
    const [d0, d1, t0, t1] = ny === 0 ? [x0, x1, y0, y1] : [y0, y1, x0, x1];
    let lo = d0, hi = d1;
    const spans = [];
    for (const s of p.shapes) {
      if (s.layer !== c.layer) continue;
      const bb = bboxOf(outlinePts(s));
      const [sd0, sd1, st0, st1] = ny === 0 ? [bb[0], bb[2], bb[1], bb[3]] : [bb[1], bb[3], bb[0], bb[2]];
      if (st1 <= t0 + 1e-9 || st0 >= t1 - 1e-9) continue;
      spans.push([sd0, sd1]);
    }
    for (let it = 0; it < 4; it++) {
      for (const [sd0, sd1] of spans) {
        if (sd0 <= lo + 1e-9 && sd1 > lo) lo = Math.min(sd1, d1);
        if (sd1 >= hi - 1e-9 && sd0 < hi) hi = Math.max(sd0, d0);
      }
    }
    if (!(lo > d0 + 1e-9 && hi < d1 - 1e-9))
      w.push(`${c.ref}: body ends do not touch copper — the R/L/C will not be connected`);
  }
  for (const dev of p.devices || []) {
    const lib = (app.devLib || []).find(l => l.file === dev.file);
    if (!dev.file) { w.push(`${dev.ref}: no touchstone file selected`); continue; }
    if (!lib) { w.push(`${dev.ref}: file "${dev.file}" not in the server library`); continue; }
    const pins = dev.pins || [];
    if (pins.some(x => x == null) || new Set(pins).size !== lib.nports)
      w.push(`${dev.ref}: assign ${lib.nports} distinct ports to the device pins`);
    for (const n of pins) {
      const q = p.ports.find(q => q.number === n);
      if (q && q.excite) w.push(`${dev.ref}: excited port ${n} cannot be a device pin`);
      if (q && Math.abs((q.impedance || 50) - lib.r) > lib.r * 0.01)
        w.push(`${dev.ref}: port ${n} impedance must be ${lib.r} Ω (touchstone reference)`);
    }
    if (p.sim.fStart * 1e9 < lib.fmin * 0.999 || p.sim.fStop * 1e9 > lib.fmax * 1.001)
      w.push(`${dev.ref}: sweep exceeds the touchstone range `
        + `(${(lib.fmin / 1e9).toFixed(2)}–${(lib.fmax / 1e9).toFixed(2)} GHz) — data will be clamped`);
  }
  const s = p.sim;
  if (s.fStart && s.fStop && s.fStart < s.fStop / 10) {
    w.push(`Very wide band (${s.fStart}–${s.fStop} GHz): the pulse then contains near-DC energy `
      + `that neither the boundaries nor the ports can absorb — the energy decay will plateau. `
      + `Use f start ≥ ${(s.fStop / 10).toFixed(1)} GHz, or expect a non-converged run.`);
  }
  if (s.endCriteria != null && s.endCriteria < -50) {
    w.push(`End criteria ${s.endCriteria} dB is below the typical numerical floor (≈ −50 dB) — `
      + `the run will likely stop at the timestep limit instead. −40 dB is usually sufficient.`);
  }
  for (const q of p.ports) {
    const bb = [q.x, q.y, q.x + q.w, q.y + q.h];
    if (q.ptype === 'msl') {
      if (!['MUR', 'PML_8'].includes(p.sim.boundary))
        w.push(`MSL port ${q.number} needs an absorbing boundary (MUR or PML-8), not ${p.sim.boundary}`);
      const gnd = p.stackup.find(l => l.id === q.layerFrom);
      if (gnd && !gnd.fill)
        w.push(`MSL port ${q.number}: ground layer "${app.layerName(q.layerFrom)}" is not a full plane`);
      if (!copperOn(q.layerTo, bb))
        w.push(`MSL port ${q.number} does not touch copper on its strip layer`);
      continue;
    }
    if (!inside(bb)) w.push(`Port ${q.number} extends outside the board outline`);
    if (q.direction === 'z') {
      for (const lid of [q.layerFrom, q.layerTo]) {
        if (lid && !copperOn(lid, bb))
          w.push(`Port ${q.number} does not touch copper on layer "${app.layerName(lid)}"`);
      }
    }
  }
  return w;
}
function updateWarnings() {
  const bar = $('warnBar');
  const w = validateProject();
  bar.hidden = w.length === 0;
  bar.innerHTML = w.slice(0, 5).map(t => `<div>${t}</div>`).join('') +
    (w.length > 5 ? `<div>… and ${w.length - 5} more</div>` : '');
  return w;
}

/* ---------- in-app dialogs (native alert/confirm are suppressed in some
   embedded browsers, so never rely on them) ---------- */
function uiNotice(msg, kind = 'info', ms = 5000) {
  const t = document.createElement('div');
  t.className = 'toast' + (kind !== 'info' ? ' ' + kind : '');
  t.textContent = msg;
  $('toasts').append(t);
  setTimeout(() => t.remove(), ms);
}
function uiConfirm(msg, okLabel = 'OK') {
  return new Promise(res => {
    $('uicMsg').textContent = msg;
    $('uicOk').textContent = okLabel;
    $('uicInput').hidden = true;
    $('uiConfirmWrap').hidden = false;
    const done = v => {
      $('uiConfirmWrap').hidden = true;
      $('uicOk').onclick = $('uicCancel').onclick = null;
      res(v);
    };
    $('uicOk').onclick = () => done(true);
    $('uicCancel').onclick = () => done(false);
  });
}
function uiPrompt(msg, value = '', okLabel = 'OK') {
  return new Promise(res => {
    $('uicMsg').textContent = msg;
    $('uicOk').textContent = okLabel;
    const inp = $('uicInput');
    inp.hidden = false;
    inp.value = value;
    $('uiConfirmWrap').hidden = false;
    inp.focus();
    const done = v => {
      $('uiConfirmWrap').hidden = true;
      inp.hidden = true;
      $('uicOk').onclick = $('uicCancel').onclick = inp.onkeydown = null;
      res(v);
    };
    inp.onkeydown = e => { if (e.key === 'Enter') done(inp.value.trim() || null); };
    $('uicOk').onclick = () => done(inp.value.trim() || null);
    $('uicCancel').onclick = () => done(null);
  });
}

/* ---------- object list ---------- */
function renderObjList() {
  const ul = $('objList');
  ul.innerHTML = '';
  const MAX_LIST = 300;
  let count = 0;
  const total = app.project.shapes.length + app.project.vias.length +
    app.project.components.length + app.project.ports.length;
  const add = (kind, obj, color, label, tag) => {
    if (++count > MAX_LIST) return;
    const li = document.createElement('li');
    if (app.selection && app.selection.kind === kind && app.selection.id === obj.id) li.classList.add('selected');
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.style.background = color;
    const name = document.createElement('span');
    name.textContent = label;
    const t = document.createElement('span');
    t.className = 'tag';
    t.textContent = tag;
    li.append(chip, name, t);
    li.onclick = () => app.select(kind, obj.id);
    ul.append(li);
  };
  // ports/components/vias first so a large import cannot push them off the list
  for (const p of app.project.ports)
    add('port', p, p.ptype === 'msl' ? '#0ca378' : '#0ca30c',
      `${p.ptype === 'msl' ? 'MSL port' : 'Port'} ${p.number}${p.excite ? ' *' : ''}`,
      (p.ptype === 'msl' ? (p.orient || '+x') : p.direction) + (p.excite ? ' exc' : ''));
  for (const c of app.project.components)
    add('component', c, '#b9b9b3', app.compLabel(c), c.package);
  for (const v of app.project.vias)
    add('via', v, '#c3c2b7', `via (${v.x.toFixed(1)}, ${v.y.toFixed(1)})`, `${app.layerName(v.from)}→${app.layerName(v.to)}`);
  for (const s of app.project.shapes)
    add('shape', s, app.layerColor(s.layer), s.name || s.type, app.layerName(s.layer));
  if (total > MAX_LIST) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = `… ${total - MAX_LIST} more objects (select in the editor)`;
    ul.append(li);
  }
  if (typeof renderDevices === 'function' && $('devList')) renderDevices();
}

/* ---------- properties panel ---------- */
function fld(label, input) {
  const l = document.createElement('label');
  l.append(document.createTextNode(label + ' '), input);
  return l;
}
function numIn(value, step, onchange) {
  const i = document.createElement('input');
  i.type = 'number'; i.step = step; i.value = value;
  i.addEventListener('change', () => onchange(parseFloat(i.value)));
  return i;
}
function textIn(value, onchange) {
  const i = document.createElement('input');
  i.type = 'text'; i.value = value;
  i.addEventListener('change', () => onchange(i.value));
  return i;
}
function selIn(options, value, onchange) {
  const s = document.createElement('select');
  for (const [v, txt] of options) {
    const o = document.createElement('option');
    o.value = v; o.textContent = txt;
    s.append(o);
  }
  s.value = value;
  s.addEventListener('change', () => onchange(s.value));
  return s;
}
function layerSel(value, onchange) {
  return selIn(app.conductorLayers().map(l => [l.id, l.name || l.id]), value, onchange);
}

function renderProps() {
  const body = $('propsBody');
  const sel = app.editor && app.editor.selectedObj();
  if (!sel) {
    body.innerHTML = '<p class="muted">Nothing selected.<br>Click an object or draw a new one.</p>';
    return;
  }
  const { kind, obj } = sel;
  body.innerHTML = '';
  const upd = () => { app.editor.render(); renderObjList(); app.dirty(); };
  const rerender = () => { renderProps(); upd(); };

  const title = document.createElement('div');
  title.className = 'title';
  const chip = document.createElement('span');
  chip.className = 'chip';
  const heads = {
    shape: () => [app.layerColor(obj.layer), obj.name || obj.type],
    via: () => ['#c3c2b7', 'Via'],
    component: () => ['#b9b9b3', app.compLabel(obj)],
    port: () => obj.ptype === 'msl'
      ? ['#0ca378', `MSL port ${obj.number}`]
      : ['#0ca30c', `Lumped port ${obj.number}`],
  };
  const [color, txt] = heads[kind]();
  chip.style.background = color;
  const h = document.createElement('b');
  h.textContent = txt;
  title.append(chip, h);
  body.append(title);

  const form = document.createElement('div');
  form.className = 'form';
  const F = (label, input) => form.append(fld(label, input));

  if (kind === 'shape') {
    F('Name', textIn(obj.name || '', v => { obj.name = v; upd(); }));
    F('Layer', layerSel(obj.layer, v => { obj.layer = v; upd(); }));
    const t = obj.type || 'rect';
    if (t === 'rect') {
      F('x (mm)', numIn(obj.x, 0.1, v => { obj.x = v; upd(); }));
      F('y (mm)', numIn(obj.y, 0.1, v => { obj.y = v; upd(); }));
      F('Width (mm)', numIn(obj.w, 0.1, v => { obj.w = Math.max(0.01, v); upd(); }));
      F('Height (mm)', numIn(obj.h, 0.1, v => { obj.h = Math.max(0.01, v); upd(); }));
    } else if (t === 'circle') {
      F('Center x', numIn(obj.cx, 0.1, v => { obj.cx = v; upd(); }));
      F('Center y', numIn(obj.cy, 0.1, v => { obj.cy = v; upd(); }));
      F('Radius (mm)', numIn(obj.r, 0.1, v => { obj.r = Math.max(0.01, v); upd(); }));
    } else if (t === 'segment') {
      F('Center x', numIn(obj.cx, 0.1, v => { obj.cx = v; upd(); }));
      F('Center y', numIn(obj.cy, 0.1, v => { obj.cy = v; upd(); }));
      F('Radius (mm)', numIn(obj.r, 0.1, v => { obj.r = Math.max(0.01, v); upd(); }));
      F('Start angle (°)', numIn(obj.a0, 1, v => { obj.a0 = v; upd(); }));
      F('End angle (°)', numIn(obj.a1, 1, v => { obj.a1 = v; upd(); }));
    } else if (t === 'arc') {
      F('Center x', numIn(obj.cx, 0.1, v => { obj.cx = v; upd(); }));
      F('Center y', numIn(obj.cy, 0.1, v => { obj.cy = v; upd(); }));
      F('Inner radius', numIn(obj.r0, 0.1, v => { obj.r0 = Math.max(0.01, v); upd(); }));
      F('Outer radius', numIn(obj.r1, 0.1, v => { obj.r1 = Math.max(obj.r0 + 0.01, v); upd(); }));
      F('Start angle (°)', numIn(obj.a0, 1, v => { obj.a0 = v; upd(); }));
      F('End angle (°)', numIn(obj.a1, 1, v => { obj.a1 = v; upd(); }));
    } else if (t === 'poly') {
      const p = document.createElement('p');
      p.className = 'muted';
      p.textContent = `${obj.pts.length} vertices — drag the white handles to edit.`;
      form.append(p);
    }
    F('Priority', numIn(obj.priority ?? 10, 1, v => { obj.priority = Math.round(v); upd(); }));
  } else if (kind === 'via') {
    F('x (mm)', numIn(obj.x, 0.1, v => { obj.x = v; upd(); }));
    F('y (mm)', numIn(obj.y, 0.1, v => { obj.y = v; upd(); }));
    F('Drill ⌀ (mm)', numIn(obj.drill, 0.05, v => { obj.drill = Math.max(0.05, v); upd(); }));
    F('Pad ⌀ (mm)', numIn(obj.pad, 0.05, v => { obj.pad = Math.max(obj.drill, v); upd(); }));
    F('From layer', layerSel(obj.from, v => { obj.from = v; upd(); }));
    F('To layer', layerSel(obj.to, v => { obj.to = v; upd(); }));
  } else if (kind === 'component') {
    F('Reference', textIn(obj.ref, v => { obj.ref = v; upd(); }));
    F('Type', selIn([['R', 'Resistor'], ['C', 'Capacitor'], ['L', 'Inductor']], obj.ctype,
      v => { obj.ctype = v; rerender(); }));
    F(`Value (${COMP_UNITS[obj.ctype]})`, numIn(obj.value, 0.1, v => { obj.value = v; upd(); }));
    F('Package', selIn([['0402', '0402'], ['0603', '0603'], ['0805', '0805'], ['custom', 'custom']],
      obj.package, v => { obj.package = v; rerender(); }));
    if (obj.package === 'custom') {
      F('Body length', numIn(obj.len || 1.6, 0.1, v => { obj.len = Math.max(0.1, v); upd(); }));
      F('Body width', numIn(obj.wid || 0.8, 0.1, v => { obj.wid = Math.max(0.1, v); upd(); }));
    }
    F('Rotation', selIn([['0', '0° (x)'], ['90', '90° (y)']], String(obj.rot || 0),
      v => { obj.rot = parseInt(v, 10); upd(); }));
    F('Layer', layerSel(obj.layer, v => { obj.layer = v; upd(); }));
    F('Center x', numIn(obj.x, 0.1, v => { obj.x = v; upd(); }));
    F('Center y', numIn(obj.y, 0.1, v => { obj.y = v; upd(); }));
  } else if (kind === 'port' && obj.ptype === 'msl') {
    F('Port number', numIn(obj.number, 1, v => { obj.number = Math.max(1, Math.round(v)); upd(); }));
    F('Direction (into board)', selIn([['+x', '+x →'], ['-x', '-x ←'], ['+y', '+y ↑'], ['-y', '-y ↓']],
      obj.orient || '+x', v => { obj.orient = v; upd(); }));
    F('Strip layer', layerSel(obj.layerTo, v => { obj.layerTo = v; upd(); }));
    F('Ground layer', layerSel(obj.layerFrom, v => { obj.layerFrom = v; upd(); }));
    F('Ref. impedance (Ω)', numIn(obj.impedance, 1, v => { obj.impedance = v; upd(); }));
    const note = document.createElement('p');
    note.className = 'muted';
    note.textContent = 'The strip is extended from the port to the absorbing '
      + 'boundary automatically; the S-parameter reference plane sits at the '
      + 'port’s inner edge. Match the port width to the line width.';
    form.append(note);
    const excI0 = document.createElement('input');
    excI0.type = 'checkbox'; excI0.checked = !!obj.excite;
    excI0.addEventListener('change', () => {
      if (excI0.checked) app.project.ports.forEach(q => { q.excite = (q === obj); });
      else obj.excite = false;
      rerender();
    });
    const excL0 = document.createElement('label');
    excL0.className = 'check';
    excL0.append(excI0, document.createTextNode(' Excited port (source)'));
    form.append(excL0);
    F('x (mm)', numIn(obj.x, 0.1, v => { obj.x = v; upd(); }));
    F('y (mm)', numIn(obj.y, 0.1, v => { obj.y = v; upd(); }));
    F('Width (mm)', numIn(obj.w, 0.1, v => { obj.w = Math.max(0.01, v); upd(); }));
    F('Height (mm)', numIn(obj.h, 0.1, v => { obj.h = Math.max(0.01, v); upd(); }));
  } else if (kind === 'port') {
    F('Port number', numIn(obj.number, 1, v => { obj.number = Math.max(1, Math.round(v)); upd(); }));
    F('Direction', selIn([['x', 'x (in-plane)'], ['y', 'y (in-plane)'], ['z', 'z (vertical)']],
      obj.direction, v => { obj.direction = v; rerender(); }));
    if (obj.direction === 'z') {
      F('From layer', layerSel(obj.layerFrom, v => { obj.layerFrom = v; upd(); }));
      F('To layer', layerSel(obj.layerTo, v => { obj.layerTo = v; upd(); }));
    } else {
      F('On layer', layerSel(obj.layer || app.activeLayer, v => { obj.layer = v; upd(); }));
    }
    F('Impedance (Ω)', numIn(obj.impedance, 1, v => { obj.impedance = v; upd(); }));
    const excI = document.createElement('input');
    excI.type = 'checkbox'; excI.checked = !!obj.excite;
    excI.addEventListener('change', () => {
      if (excI.checked) app.project.ports.forEach(q => { q.excite = (q === obj); });
      else obj.excite = false;
      rerender();
    });
    const excL = document.createElement('label');
    excL.className = 'check';
    excL.append(excI, document.createTextNode(' Excited port (source)'));
    form.append(excL);
    F('x (mm)', numIn(obj.x, 0.1, v => { obj.x = v; upd(); }));
    F('y (mm)', numIn(obj.y, 0.1, v => { obj.y = v; upd(); }));
    F('Width (mm)', numIn(obj.w, 0.1, v => { obj.w = Math.max(0.01, v); upd(); }));
    F('Height (mm)', numIn(obj.h, 0.1, v => { obj.h = Math.max(0.01, v); upd(); }));
  }
  body.append(form);

  // advanced per-object meshing
  const mh = document.createElement('h3');
  mh.style.marginTop = '14px';
  mh.textContent = 'Meshing';
  const mform = document.createElement('div');
  mform.className = 'form';
  obj.mesh = obj.mesh || {};
  const resI = numIn(obj.mesh.res ?? '', 0.05, v => {
    obj.mesh.res = isFinite(v) && v > 0 ? v : null;
    upd();
  });
  resI.placeholder = 'default';
  mform.append(fld('Local resolution (mm)', resI));
  if (kind === 'shape') {
    const thI = document.createElement('input');
    thI.type = 'checkbox';
    thI.checked = !!obj.mesh.thirds;
    thI.addEventListener('change', () => { obj.mesh.thirds = thI.checked; upd(); });
    const thL = document.createElement('label');
    thL.className = 'check';
    thL.title = 'Replace the metal edge line with lines at 1/3 inside / 2/3 outside the edge (captures the edge singularity)';
    thL.append(thI, document.createTextNode(' Edge refinement (1/3–2/3 rule)'));
    mform.append(thL);
  }
  body.append(mh, mform);

  const del = document.createElement('button');
  del.className = 'danger';
  del.textContent = 'Delete';
  del.onclick = () => app.deleteSelection();
  body.append(del);
}

/* ---------- stackup editor ---------- */
function renderStackup() {
  const list = $('stackupList');
  list.innerHTML = '';
  const st = app.project.stackup;
  st.forEach((layer, idx) => {
    const row = document.createElement('div');
    row.className = 'layer-row';
    const top = document.createElement('div');
    top.className = 'lr-top';
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.style.background = layer.type === 'conductor' ? app.layerColor(layer.id) : DIEL_CHIP;
    const name = textIn(layer.name || layer.id, v => { layer.name = v; stackChanged(); });
    name.className = 'lr-name';
    const typeS = selIn([['conductor', 'conductor'], ['dielectric', 'dielectric']], layer.type, v => {
      if (v === layer.type) return;
      if (layer.type === 'conductor' && layerInUse(layer.id)) {
        uiNotice('Layer is in use by shapes/ports/vias - move them first.', 'warn');
        renderStackup();
        return;
      }
      layer.type = v;
      if (v === 'dielectric') { layer.er = layer.er || 4.3; layer.tand = layer.tand || 0.02; layer.thickness = layer.thickness || 0.2; }
      else { layer.fill = false; layer.thickness = layer.thickness || 0.035; }
      stackChanged();
    });
    typeS.className = 'mini';
    const up = mkBtn('↑', () => { if (idx > 0) { st.splice(idx - 1, 0, st.splice(idx, 1)[0]); stackChanged(); } });
    const dn = mkBtn('↓', () => { if (idx < st.length - 1) { st.splice(idx + 1, 0, st.splice(idx, 1)[0]); stackChanged(); } });
    const rm = mkBtn('×', () => {
      if (layer.type === 'conductor' && layerInUse(layer.id)) { uiNotice('Layer is in use - move or delete its objects first.', 'warn'); return; }
      st.splice(idx, 1);
      stackChanged();
    });
    top.append(chip, name, typeS);
    if (layer.type === 'conductor') {
      const imp = mkBtn('⇪', () => {
        app._importLayer = layer.id;
        $('gerberInput').click();
      });
      imp.title = 'Import a Gerber file onto this layer';
      top.append(imp);
    }
    top.append(up, dn, rm);
    row.append(top);

    const fields = document.createElement('div');
    fields.className = 'lr-fields';
    const add = (txt, input) => {
      const sp = document.createElement('span');
      sp.append(document.createTextNode(txt + ' '), input);
      fields.append(sp);
    };
    if (layer.type === 'dielectric') {
      add('thk (mm)', numIn(layer.thickness, 0.01, v => { layer.thickness = Math.max(0.001, v); stackChanged(); }));
      add('εr', numIn(layer.er, 0.01, v => { layer.er = Math.max(1, v); stackChanged(); }));
      add('tanδ', numIn(layer.tand ?? 0, 0.001, v => { layer.tand = Math.max(0, v); stackChanged(); }));
    } else {
      add('thk (mm)', numIn(layer.thickness ?? 0.035, 0.005, v => { layer.thickness = v; stackChanged(); }));
      const fill = document.createElement('input');
      fill.type = 'checkbox'; fill.checked = !!layer.fill;
      fill.addEventListener('change', () => { layer.fill = fill.checked; stackChanged(); });
      add('full plane', fill);
    }
    row.append(fields);
    list.append(row);
  });
}
function mkBtn(txt, onclick) {
  const b = document.createElement('button');
  b.className = 'lr-btn';
  b.textContent = txt;
  b.onclick = onclick;
  return b;
}
function layerInUse(id) {
  const p = app.project;
  return p.shapes.some(s => s.layer === id) ||
    p.components.some(c => c.layer === id) ||
    p.vias.some(v => v.from === id || v.to === id) ||
    p.ports.some(q => q.layer === id || q.layerFrom === id || q.layerTo === id);
}
function stackChanged() {
  renderStackup();
  updateLayerSelect();
  renderObjList();
  renderProps();
  app.editor.render();
  app.dirty();
}
function updateLayerSelect() {
  const sel = $('activeLayer');
  const layers = app.conductorLayers();
  sel.innerHTML = '';
  for (const l of layers) {
    const o = document.createElement('option');
    o.value = l.id; o.textContent = l.name || l.id;
    sel.append(o);
  }
  app.ensureActiveLayer();
  sel.value = app.activeLayer;
}

/* ---------- board & sim forms ---------- */
const SIM_FIELDS = ['fStart', 'fStop', 'points', 'endCriteria', 'maxTimesteps', 'meshDiv', 'edgeRes', 'meshMerge', 'airMargin', 'jtStart', 'jtStop'];

function formsFromModel() {
  $('b_width').value = app.project.board.width;
  $('b_height').value = app.project.board.height;
  for (const f of SIM_FIELDS) $('s_' + f).value = app.project.sim[f] ?? '';
  $('s_boundary').value = app.project.sim.boundary;
  $('s_dumpJ').checked = !!app.project.sim.dumpJ;
  $('s_dumpFreqs').value = app.project.sim.dumpFreqs || '';
  $('s_dumpJt').checked = !!app.project.sim.dumpJt;
  $('s_jtSub').value = String(app.project.sim.jtSub || 2);
  $('projName').value = app.project.name || '';
}

function bindForms() {
  $('b_width').addEventListener('change', e => { app.project.board.width = parseFloat(e.target.value); app.editor.render(); app.dirty(); });
  $('b_height').addEventListener('change', e => { app.project.board.height = parseFloat(e.target.value); app.editor.render(); app.dirty(); });
  for (const f of SIM_FIELDS)
    $('s_' + f).addEventListener('change', e => {
      const v = e.target.value;
      app.project.sim[f] = v === '' ? null : parseFloat(v);
      app.dirty();
    });
  $('s_boundary').addEventListener('change', e => { app.project.sim.boundary = e.target.value; app.dirty(); });
  $('s_dumpJ').addEventListener('change', e => { app.project.sim.dumpJ = e.target.checked; app.dirty(); });
  $('s_dumpFreqs').addEventListener('change', e => { app.project.sim.dumpFreqs = e.target.value; app.dirty(); });
  $('s_dumpJt').addEventListener('change', e => { app.project.sim.dumpJt = e.target.checked; app.dirty(); });
  $('s_jtSub').addEventListener('change', e => { app.project.sim.jtSub = parseInt(e.target.value, 10); app.dirty(); });
  $('projName').addEventListener('change', e => { app.project.name = e.target.value.trim() || 'untitled'; app.dirty(); });
}

/* ---------- tabs ---------- */
function initTabs() {
  document.querySelectorAll('.tab').forEach(btn =>
    btn.addEventListener('click', () => showTab(btn.dataset.tab)));
}
function showTab(name) {
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tabpage').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
}

/* ---------- helpers ---------- */
function download(name, text, type = 'application/octet-stream') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
async function apiJson(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

/* ---------- main-area view switching ---------- */
function showView(name) {
  document.querySelectorAll('.viewtab').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  document.querySelectorAll('#center .view').forEach(v => v.classList.toggle('active', v.id === name + 'View'));
  if (name === 'editor') {
    app.editor.resize();
  } else if (name === 'tests') {
    initTestsView();
  } else {
    // canvases had zero width while hidden - render now that they are visible
    renderCharts();
    if (app.jview) app.jview.render();
  }
}

/* ---------- run control ---------- */
function setRunUI(state, percent, elapsed, stageInfo) {
  $('runState').textContent = state + (percent ? ` — ${percent}%` : '')
    + (stageInfo ? ` · ${stageInfo}` : '');
  $('runElapsed').textContent = elapsed ? `${elapsed}s` : '';
  $('progFill').style.width = (percent || 0) + '%';
  $('progFill').style.background = state === 'error' ? '#e66767' : state === 'done' ? '#0ca30c' : '#3987e5';
  const running = ['starting', 'running', 'post'].includes(state);
  $('btnRun').disabled = running;
  $('btnStop').disabled = !running;
}

function fmtCount(n) {
  if (n == null) return '–';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e4) return (n / 1e3).toFixed(0) + 'k';
  return String(n);
}

function updateRunStats(st) {
  app._lastRunStat = st;
  const last = st.samples && st.samples.length ? st.samples[st.samples.length - 1] : null;
  $('stTs').textContent = last && last.ts != null ? `${fmtCount(last.ts)} / ${fmtCount(st.nrts)}` : '–';
  $('stSpeed').textContent = last && last.speed != null ? last.speed.toFixed(0) : '–';
  $('stEnergy').textContent = last && last.db != null ? `${last.db.toFixed(1)} / ${st.endDb}` : '–';
  $('stCells').textContent = fmtCount(st.meshCells);
  // ETA from progress so far
  const running = ['starting', 'running', 'post'].includes(st.state);
  $('runEta').textContent = running && st.percent > 3 && st.elapsed > 2
    ? `~${Math.max(1, Math.round(st.elapsed * (100 - st.percent) / st.percent))}s left` : '';
  // solver warnings
  const warn = $('runWarn');
  const msgs = st.warnMsgs || [];
  warn.hidden = msgs.length === 0;
  warn.innerHTML = msgs.map(m => `<div>${m.replace(/</g, '&lt;')}</div>`).join('');
  // engine facts
  const info = st.info || {};
  const rows = [];
  const add = (label, v) => { if (v != null) rows.push([label, v]); };
  add('openEMS', info.version);
  add('Engine', info.engine);
  add('Threads', info.threads);
  add('FDTD size', info.fdtdSize);
  if (info.dt) add('Timestep', `${(parseFloat(info.dt) * 1e12).toFixed(3)} ps`);
  add('Nyquist rate', info.nyquist && `${fmtCount(+info.nyquist)} ts`);
  if (info.excitationTs) {
    add('Excitation', `${fmtCount(+info.excitationTs)} ts` +
      (info.excitationS ? ` (${(parseFloat(info.excitationS) * 1e9).toFixed(2)} ns)` : ''));
  }
  add('Run time', info.runTime && `${info.runTime} s`);
  add('Final speed', info.finalSpeed && `${info.finalSpeed} MCells/s`);
  $('runInfo').querySelector('tbody').innerHTML =
    rows.map(([k, v]) => `<tr><td>${k}</td><td>${String(v).replace(/</g, '&lt;')}</td></tr>`).join('');
  drawRunChart($('runChart'), st.samples || [], st.nrts, st.endDb);
  drawSpeedChart($('speedChart'), st.samples || [], st.nrts);
}

/* solver throughput over the run: MCells/s vs timestep */
function drawSpeedChart(canvas, samples, nrts) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || canvas.parentElement.clientWidth || 280;
  const h = Math.round(w * 0.38);
  canvas.width = w * dpr; canvas.height = h * dpr;
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const m = { l: 34, r: 8, t: 8, b: 16 };
  const pts = samples.filter(s => s.speed != null && s.ts != null);
  const vMax = Math.max(50, ...pts.map(s => s.speed)) * 1.15;
  const xHi = Math.max(nrts || 1, pts.length ? pts[pts.length - 1].ts : 1);
  const px = t => m.l + t / xHi * (w - m.l - m.r);
  const py = v => m.t + (1 - v / vMax) * (h - m.t - m.b);
  ctx.font = '10px system-ui';
  ctx.lineWidth = 1;
  for (const f of [0.5, 1]) {
    const v = Math.round(vMax * f / 1.15);
    ctx.strokeStyle = '#2c2c2a';
    ctx.beginPath(); ctx.moveTo(m.l, py(v)); ctx.lineTo(w - m.r, py(v)); ctx.stroke();
    ctx.fillStyle = '#898781';
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(String(v), m.l - 4, py(v));
  }
  ctx.strokeStyle = '#383835';
  ctx.strokeRect(m.l, m.t, w - m.l - m.r, h - m.t - m.b);
  ctx.fillStyle = '#898781';
  ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
  ctx.fillText('MCells/s vs timestep', w - m.r - 2, h - m.b - 2);
  if (pts.length) {
    ctx.strokeStyle = '#d95926';
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    pts.forEach((s, k) => {
      const X = px(s.ts), Y = py(Math.min(s.speed, vMax));
      k ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
    });
    ctx.stroke();
  }
}

/* live energy-decay chart: energy dB vs timestep, with the end-criteria target */
function drawRunChart(canvas, samples, nrts, endDb) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || canvas.parentElement.clientWidth || 280;
  const h = Math.round(w * 0.55);
  canvas.width = w * dpr; canvas.height = h * dpr;
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const m = { l: 34, r: 8, t: 8, b: 20 };
  const pts = samples.filter(s => s.db != null && s.ts != null);
  const yLo = Math.min(endDb - 5, pts.length ? Math.min(...pts.map(s => s.db)) - 3 : endDb - 5);
  const yHi = 0;
  const xHi = Math.max(nrts || 1, pts.length ? pts[pts.length - 1].ts : 1);
  const px = t => m.l + t / xHi * (w - m.l - m.r);
  const py = v => m.t + (yHi - v) / (yHi - yLo) * (h - m.t - m.b);
  ctx.font = '10px system-ui';
  ctx.lineWidth = 1;
  for (const v of [0, -20, -40, -60, -80].filter(v => v >= yLo)) {
    ctx.strokeStyle = '#2c2c2a';
    ctx.beginPath(); ctx.moveTo(m.l, py(v)); ctx.lineTo(w - m.r, py(v)); ctx.stroke();
    ctx.fillStyle = '#898781';
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(String(v), m.l - 4, py(v));
  }
  ctx.strokeStyle = '#383835';
  ctx.strokeRect(m.l, m.t, w - m.l - m.r, h - m.t - m.b);
  // end-criteria target
  ctx.strokeStyle = '#0ca30c';
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(m.l, py(endDb)); ctx.lineTo(w - m.r, py(endDb)); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#0ca30c';
  ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
  ctx.fillText(`target ${endDb} dB`, m.l + 4, py(endDb) - 2);
  ctx.fillStyle = '#898781';
  ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
  ctx.fillText('timesteps', w - m.r - 2, h - m.b - 2);
  if (pts.length) {
    ctx.strokeStyle = '#3987e5';
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach((s, k) => {
      const X = px(s.ts), Y = py(Math.max(yLo, Math.min(yHi, s.db)));
      k ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
    });
    ctx.stroke();
  }
}
function appendLog(lines) {
  if (!lines.length) return;
  const log = $('runLog');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
  log.textContent += lines.join('\n') + '\n';
  if (log.textContent.length > 120000) log.textContent = log.textContent.slice(-120000);
  if (atBottom) log.scrollTop = log.scrollHeight;
}

async function startRun() {
  const warnings = updateWarnings();
  if (warnings.length &&
      !(await uiConfirm('Design warnings:\n\n- ' + warnings.join('\n- ') + '\n\nRun anyway?', 'Run anyway'))) return;
  try {
    const { runId } = await apiJson('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(app.project),
    });
    app.currentRunId = runId;
    app.logOffset = 0;
    $('runLog').textContent = '';
    $('results').hidden = true;
    $('noResults').hidden = false;
    $('jCard').hidden = true;
    $('tdCard').hidden = true;
    $('btnResults').hidden = true;
    showTab('run');
    showView('editor');
    setRunUI('starting', 0, 0);
    updateRunStats({ samples: [], nrts: app.project.sim.maxTimesteps || 30000, endDb: app.project.sim.endCriteria || -40 });
    poll();
  } catch (e) {
    uiNotice('Cannot start simulation:\n' + e.message, 'err', 8000);
  }
}
function poll() {
  clearTimeout(app.polling);
  app.polling = setTimeout(async () => {
    try {
      const st = await apiJson(`/api/status?offset=${app.logOffset}`);
      app.logOffset = st.nextOffset;
      appendLog(st.lines);
      setRunUI(st.state, st.percent, st.elapsed, st.stageInfo);
      updateRunStats(st);
      if (['starting', 'running', 'post'].includes(st.state)) poll();
      else if (st.state === 'done') {
        if (st.notConverged) {
          $('runState').textContent = 'done — NOT converged';
          uiNotice('The run hit the timestep limit before reaching the end criteria — '
            + 'residual energy stayed in the domain. S-parameters may show ripple and the '
            + 'lowest frequencies may be unreliable. See the design warnings for likely causes '
            + '(very wide band or a too-strict end criteria).', 'warn', 12000);
        }
        loadResults(st.runId);
      }
      else if (st.state === 'error') {
        appendLog(['', '*** ' + (st.error || 'simulation failed') + ' ***']);
        $('rawLog').open = true;
      }
    } catch (e) {
      appendLog(['[gui] status poll failed: ' + e.message]);
      poll();
    }
  }, 800);
}
async function stopRun() {
  try { await apiJson('/api/stop', { method: 'POST' }); } catch (e) { /* ignore */ }
}

/* ---------- results ---------- */
async function loadResults(runId) {
  app.currentRunId = runId;
  try {
    const res = await fetch(`/api/results/${runId}/sparams.csv`);
    if (!res.ok) throw new Error('no results file');
    const text = await res.text();
    const lines = text.trim().split('\n');
    const header = lines[0].replace(/^#/, '').split(',');
    const rows = lines.slice(1).map(l => l.split(',').map(Number));
    const freq = rows.map(r => r[0]);
    const cols = [];
    for (let c = 1; c < header.length - 2; c += 2) {
      cols.push({
        label: header[c].replace(/_re$/, ''),
        re: rows.map(r => r[c]),
        im: rows.map(r => r[c + 1]),
      });
    }
    app.sparams = { freq, cols };
    app.tdData = null;
    try {
      const td = await apiJson(`/api/results/${runId}/timedomain`);
      if (td.ports && td.ports.length) {
        app.tdData = td;
        // currents start hidden unless the user has toggled them before
        for (const p of td.ports) {
          if (resultsPrefs.hidden[`td:i${p.n}`] === undefined)
            resultsPrefs.hidden[`td:i${p.n}`] = true;
        }
      }
    } catch (e) { /* no TD data - card stays hidden */ }
    $('results').hidden = false;
    $('noResults').hidden = true;
    $('btnResults').hidden = false;
    $('csvLink').href = `/api/results/${runId}/sparams.csv`;
    // extra downloads when a device co-simulation produced them
    const extras = $('csvLink').parentElement;
    extras.querySelectorAll('.xlink').forEach(el => el.remove());
    const nAll = app.project.ports.length;
    const nExt = nAll - (app.project.devices || [])
      .reduce((a, d) => a + (d.pins ? d.pins.length : 0), 0);
    for (const name of [`board_full.s${nAll}p`, `combined.s${nExt}p`, 'sparams_board.csv']) {
      const r = await fetch(`/api/results/${runId}/file/${name}`, { method: 'GET' });
      if (r.ok) {
        const a = document.createElement('a');
        a.className = 'xlink';
        a.style.marginLeft = '10px';
        a.href = `/api/results/${runId}/file/${name}`;
        a.download = name;
        a.textContent = name;
        extras.append(a);
      }
    }
    await loadJDumps(runId);
    showView('results');
  } catch (e) {
    appendLog(['[gui] failed to load results: ' + e.message]);
  }
}

function excitedZ0() {
  const p = app.project.ports.find(q => q.excite);
  return p ? (p.impedance || 50) : 50;
}
function splitSparams() {
  const { cols } = app.sparams;
  const refl = cols.find(c => c.label.length >= 3 && c.label[1] === c.label[2]) || cols[0];
  const trans = cols.filter(c => c !== refl);
  return { refl, trans };
}
const dB = (re, im) => re.map((r, k) => 20 * Math.log10(Math.max(Math.hypot(r, im[k]), 1e-12)));

let reflChart = null, transChart = null, tdChart = null;

/* items: [{label, key, ci}] — clicking a chip toggles that series on/off */
function buildLegend(el, items) {
  el.innerHTML = '';
  if (items.length < 2) return;
  for (const it of items) {
    const chip = document.createElement('button');
    chip.className = 'item legend-chip' + (resultsPrefs.hidden[it.key] ? ' off' : '');
    chip.title = 'Click to show / hide this trace';
    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = SERIES_COLORS[it.ci % SERIES_COLORS.length];
    chip.append(sw, document.createTextNode(it.label));
    chip.onclick = () => {
      resultsPrefs.hidden[it.key] = !resultsPrefs.hidden[it.key];
      saveResultsPrefs();
      renderCharts();
    };
    el.append(chip);
  }
}

function makeReflChart(canvas, tip) {
  const { freq } = app.sparams;
  const { refl } = splitSparams();
  const view = $('reflView').value;
  let ch;
  if (view === 'smith') {
    ch = new SmithChart(canvas, tip);
    ch.setData({ freq, label: refl.label, re: refl.re, im: refl.im, z0: excitedZ0() });
  } else {
    ch = new MagChart(canvas, tip);
    ch.setData({ freq, series: [{ label: refl.label, values: dB(refl.re, refl.im) }] });
  }
  return ch;
}
function makeTransChart(canvas, tip) {
  const { freq } = app.sparams;
  const { trans } = splitSparams();
  const view = $('transView').value;
  const visible = trans
    .map((c, i) => ({ ...c, ci: i + 1 }))
    .filter(c => !resultsPrefs.hidden[`trans:${c.label}`]);
  let ch;
  if (view === 'polar') {
    ch = new PolarChart(canvas, tip);
    ch.setData({ freq, series: visible });
  } else {
    ch = new MagChart(canvas, tip);
    ch.setData({ freq, series: visible.map(c => ({ label: c.label, values: dB(c.re, c.im), ci: c.ci })) });
  }
  return ch;
}

/* every port contributes u(t) [V] and i(t) plotted as i·Z0 so both signal
   kinds share one honest axis; the tooltip shows the raw current */
function tdSignals() {
  const out = [];
  app.tdData.ports.forEach((p, idx) => {
    const z0 = (app.project.ports.find(q => q.number === p.n) || {}).impedance || 50;
    if (p.u.length) {
      out.push({ key: `td:u${p.n}`, label: `u${p.n}(t)`, ci: 2 * idx,
        values: p.u, raw: null });
    }
    if (p.i.length) {
      out.push({ key: `td:i${p.n}`, label: `i${p.n}·Z₀`, ci: 2 * idx + 1,
        values: p.i.map(v => v * z0), raw: p.i, rawUnit: 'A' });
    }
  });
  return out;
}
function makeTdChart(canvas, tip) {
  const ch = new TimeChart(canvas, tip);
  ch.setData({
    t: app.tdData.ports[0].t,
    unit: 'V',
    series: tdSignals().filter(s => !resultsPrefs.hidden[s.key]),
  });
  return ch;
}

/* ---------- results pane configuration ---------- */
const resultsPrefs = (() => {
  let p = { refl: true, trans: true, td: true, j: true, cols: 'auto', order: [], sizes: {}, hidden: {} };
  try { p = { ...p, ...JSON.parse(localStorage.getItem('openems_results_prefs') || '{}') }; } catch (e) { /* defaults */ }
  p.hidden = p.hidden || {};
  return p;
})();
function saveResultsPrefs() {
  try { localStorage.setItem('openems_results_prefs', JSON.stringify(resultsPrefs)); } catch (e) { /* ignore */ }
}
function applyResultsConfig() {
  const grid = document.querySelector('.rgrid');
  grid.classList.toggle('cols1', resultsPrefs.cols === '1');
  grid.classList.toggle('cols2', resultsPrefs.cols === '2');
  // saved card order, then any cards not in the saved list
  if (resultsPrefs.order && resultsPrefs.order.length) {
    const rest = [...grid.children].map(c => c.id).filter(id => !resultsPrefs.order.includes(id));
    for (const id of [...resultsPrefs.order, ...rest]) {
      const el = $(id);
      if (el && el.parentElement === grid) grid.append(el);
    }
  }
  // saved card sizes (set by dragging the resize corner)
  for (const [id, sz] of Object.entries(resultsPrefs.sizes || {})) {
    const el = $(id);
    if (el && sz) {
      if (sz.w) el.style.width = sz.w;
      if (sz.h) el.style.height = sz.h;
    }
  }
  const { trans } = app.sparams ? splitSparams() : { trans: [] };
  $('reflCard').hidden = !resultsPrefs.refl || !app.sparams;
  $('transCard').hidden = !resultsPrefs.trans || trans.length === 0;
  $('tdCard').hidden = !resultsPrefs.td || !app.tdData;
  $('jCard').hidden = !resultsPrefs.j || !((app.jdumps || []).length || (app.jtdumps || []).length);
}

function renderCharts() {
  if (!app.sparams) return;
  const { trans } = splitSparams();
  applyResultsConfig();
  if (!$('reflCard').hidden) {
    if (reflChart) reflChart.destroy();
    reflChart = makeReflChart($('reflCanvas'), $('reflTip'));
    buildLegend($('reflLegend'), []);
  }
  if (!$('transCard').hidden && trans.length) {
    if (transChart) transChart.destroy();
    transChart = makeTransChart($('transCanvas'), $('transTip'));
    buildLegend($('transLegend'),
      trans.map((c, i) => ({ label: c.label, key: `trans:${c.label}`, ci: i + 1 })));
  }
  if (!$('tdCard').hidden && app.tdData) {
    if (tdChart) tdChart.destroy();
    tdChart = makeTdChart($('tdCanvas'), $('tdTip'));
    buildLegend($('tdLegend'), tdSignals().map(s => ({ label: s.label, key: s.key, ci: s.ci })));
  }
}

/* ---------- export ---------- */
function exportPNG(canvasId) {
  const canvas = $(canvasId);
  canvas.toBlob(blob => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${app.project.name || 'results'}_${canvasId.replace('Canvas', '')}.png`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, 'image/png');
}
function exportCSVFor(kind) {
  const name = app.project.name || 'results';
  if (kind === 'refl' || kind === 'trans') {
    if (!app.sparams) return;
    const { refl, trans } = splitSparams();
    const cols = kind === 'refl' ? [refl] : trans;
    if (!cols.length) return;
    let out = 'freq_Hz' + cols.map(c => `,${c.label}_re,${c.label}_im,${c.label}_dB`).join('') + '\n';
    app.sparams.freq.forEach((f, k) => {
      out += f.toExponential(6);
      for (const c of cols) {
        const mag = Math.max(Math.hypot(c.re[k], c.im[k]), 1e-12);
        out += `,${c.re[k].toExponential(6)},${c.im[k].toExponential(6)},${(20 * Math.log10(mag)).toFixed(4)}`;
      }
      out += '\n';
    });
    download(`${name}_${kind}.csv`, out, 'text/csv');
  } else if (kind === 'td') {
    if (!app.tdData) return;
    const ports = app.tdData.ports;
    let out = 't_s' + ports.map(p => `,u${p.n}_V,i${p.n}_A`).join('') + '\n';
    ports[0].t.forEach((tv, k) => {
      out += tv.toExponential(6) +
        ports.map(p => `,${(p.u[k] ?? 0).toExponential(6)},${(p.i[k] ?? 0).toExponential(6)}`).join('') + '\n';
    });
    download(`${name}_timedomain.csv`, out, 'text/csv');
  }
}

/* ---------- current density ---------- */
async function loadJDumps(runId) {
  app.jdumps = [];
  app.jtdumps = [];
  try {
    const { dumps } = await apiJson(`/api/results/${runId}/jdumps`);
    app.jdumps = dumps;
  } catch (e) { /* none */ }
  try {
    const { dumps } = await apiJson(`/api/results/${runId}/jtdumps`);
    app.jtdumps = dumps;
  } catch (e) { /* none */ }
  const any = app.jdumps.length || app.jtdumps.length;
  $('jCard').hidden = !any;
  if (!any) return;
  // mode availability
  const modeSel = $('jMode');
  modeSel.querySelector('[value=fd]').disabled = !app.jdumps.length;
  modeSel.querySelector('[value=td]').disabled = !app.jtdumps.length;
  if (modeSel.options[modeSel.selectedIndex].disabled)
    modeSel.value = app.jdumps.length ? 'fd' : 'td';
  const layers = [...new Set([...app.jdumps.map(d => d.layer), ...app.jtdumps.map(d => d.layer)])];
  const jl = $('jLayer');
  jl.innerHTML = '';
  for (const l of layers) {
    const o = document.createElement('option');
    o.value = l; o.textContent = app.layerName(l);
    jl.append(o);
  }
  updateJFreqs();
}
function jSlider(mode) {
  const sl = $('jPhase');
  if (mode === 'td') {
    const nf = app.jview.data && app.jview.data.mode === 'td' ? app.jview.data.frames.length : 1;
    sl.max = String(Math.max(1, nf - 1));
    sl.step = '1';
    sl.value = String(app.jview.frame || 0);
  } else {
    sl.max = '360';
    sl.step = '2';
    sl.value = String(Math.round(app.jview.phase || 0));
  }
}
function updateJFreqs() {
  const mode = $('jMode').value;
  const layer = $('jLayer').value;
  const jf = $('jFreq');
  const jn = $('jFreqNum');
  jf.innerHTML = '';
  jf.hidden = mode === 'td';
  jn.hidden = mode === 'td';
  if (mode === 'fd') {
    const dumps = app.jdumps.filter(d => d.layer === layer).sort((a, b) => a.freq - b.freq);
    for (const d of dumps) {
      const o = document.createElement('option');
      o.value = d.k; o.textContent = (d.freq / 1e9).toFixed(3) + ' GHz';
      jf.append(o);
    }
    if (dumps.length) {
      jn.min = (dumps[0].freq / 1e9).toFixed(3);
      jn.max = (dumps[dumps.length - 1].freq / 1e9).toFixed(3);
      const cur = parseFloat(jn.value);
      if (!isFinite(cur) || cur < parseFloat(jn.min) || cur > parseFloat(jn.max))
        jn.value = (dumps[0].freq / 1e9).toFixed(3);
      jn.title = dumps.length > 1
        ? `Arbitrary frequency ${jn.min}–${jn.max} GHz (interpolated between dumped points)`
        : 'Only one dumped frequency — dump more (e.g. "all") for interpolation';
    }
  }
  loadJFrame();
}
function jOverlay(layer) {
  return [
    { pts: [[0, 0], [app.project.board.width, 0], [app.project.board.width, app.project.board.height], [0, app.project.board.height]] },
    ...app.project.shapes.filter(s => s.layer === layer).map(s => ({ pts: outlinePts(s) })),
  ];
}
async function loadJFrame() {
  const mode = $('jMode').value;
  const layer = $('jLayer').value;
  app.jview.pause();
  $('jPlay').innerHTML = '&#9654;';
  try {
    if (mode === 'td') {
      if (!app.jtdumps.find(d => d.layer === layer)) return;
      await app.jview.loadTD(app.currentRunId, layer, jOverlay(layer));
    } else {
      const dumps = app.jdumps.filter(d => d.layer === layer);
      if (!dumps.length) return;
      let f = parseFloat($('jFreqNum').value) * 1e9;
      if (!isFinite(f)) f = dumps[0].freq;
      await app.jview.loadFD(app.currentRunId, layer, f, dumps, jOverlay(layer));
      $('jFreqNum').value = (app.jview.data.freqHz / 1e9).toFixed(3);
    }
    jSlider(mode);
  } catch (e) {
    $('jInfo').textContent = 'failed: ' + e.message;
  }
}

/* GIF export of the current-density animation (phasor period or J(t)) */
async function exportJGif() {
  const jv = app.jview;
  if (!jv || !jv.data) return;
  const btn = $('jGif');
  btn.disabled = true;
  const wasPlaying = jv.playing;
  jv.pause();
  const saved = { phase: jv.phase, frame: jv.frame };
  try {
    const mode = jv.data.mode;
    let steps;
    if (mode === 'fd') {
      steps = Array.from({ length: 36 }, (_, i) => i * 10);          // one RF period
    } else {
      const n = jv.data.frames.length;
      const stride = Math.max(1, Math.ceil(n / 100));
      steps = Array.from({ length: Math.ceil(n / stride) }, (_, i) => i * stride);
    }
    const { palette, quantize } = buildJGifPalette();
    const frames = [];
    let width = 0, height = 0;
    for (let i = 0; i < steps.length; i++) {
      if (mode === 'fd') jv.phase = steps[i]; else jv.frame = steps[i];
      const cv = jv.snapshot(480);
      width = cv.width; height = cv.height;
      frames.push(quantize(cv.getContext('2d').getImageData(0, 0, width, height)));
      $('jInfo').textContent = `GIF: frame ${i + 1}/${steps.length}…`;
      if (i % 4 === 3) await new Promise(requestAnimationFrame);
    }
    $('jInfo').textContent = 'GIF: encoding…';
    await new Promise(requestAnimationFrame);
    const bytes = encodeGif({ width, height, palette, frames, delayCs: mode === 'fd' ? 6 : 5 });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([bytes], { type: 'image/gif' }));
    const tag = mode === 'fd'
      ? (jv.data.freqHz / 1e9).toFixed(3).replace('.', 'p') + 'GHz'
      : 'Jt';
    a.download = `${app.project.name || 'results'}_J_${jv.data.layer}_${tag}.gif`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    uiNotice('GIF export failed: ' + e.message, 'err', 8000);
  } finally {
    jv.phase = saved.phase;
    jv.frame = saved.frame;
    btn.disabled = false;
    jv.render();
    if (wasPlaying) { jv.play(); $('jPlay').innerHTML = '&#10074;&#10074;'; }
  }
}

/* ---------- fabrication data import (Gerber / Excellon) ---------- */
function importOffsetFor(bbox) {
  // one shared offset per project so all imported layers/drills stay aligned
  const p = app.project;
  if (!p.importOffset) {
    p.importOffset = {
      x: bbox[0] < 2 ? Math.round((2 - bbox[0]) * 1000) / 1000 : 0,
      y: bbox[1] < 2 ? Math.round((2 - bbox[1]) * 1000) / 1000 : 0,
    };
  }
  return p.importOffset;
}
function expandBoardTo(bbox, off) {
  const p = app.project;
  p.board.width = Math.max(p.board.width, Math.ceil(bbox[2] + off.x + 2));
  p.board.height = Math.max(p.board.height, Math.ceil(bbox[3] + off.y + 2));
}

async function importGerberFile(layerId, file) {
  try {
    const res = await apiJson('/api/import/gerber', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: await file.text() }),
    });
    const off = importOffsetFor(res.bbox);
    const base = file.name.replace(/\.[^.]*$/, '');
    let n = 0;
    for (const sh of res.shapes) {
      const s = { ...sh, id: app.project.nextId++, layer: layerId, priority: 10,
                  name: `${base}_${++n}`, meshBbox: true };
      if (s.type === 'rect') { s.x += off.x; s.y += off.y; }
      else if (s.type === 'circle') { s.cx += off.x; s.cy += off.y; }
      else if (s.type === 'poly') s.pts = s.pts.map(([a, b]) => [a + off.x, b + off.y]);
      app.project.shapes.push(s);
    }
    expandBoardTo(res.bbox, off);
    formsFromModel();
    stackChanged();
    app.editor.zoomFit();
    uiNotice(`Imported ${res.shapes.length} shapes from "${file.name}" onto layer "${app.layerName(layerId)}".`);
    if (res.warnings.length)
      uiNotice('Gerber import warnings:\n- ' + res.warnings.join('\n- '), 'warn', 10000);
  } catch (e) {
    uiNotice('Gerber import failed: ' + e.message, 'err', 8000);
  }
}

async function importDrillFile(file) {
  try {
    const res = await apiJson('/api/import/drill', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: await file.text() }),
    });
    const layers = app.conductorLayers();
    if (layers.length < 2) throw new Error('need at least two conductor layers for PTH vias');
    const off = importOffsetFor(res.bbox);
    for (const v of res.vias) {
      app.project.vias.push({
        id: app.project.nextId++,
        x: Math.round((v.x + off.x) * 1000) / 1000,
        y: Math.round((v.y + off.y) * 1000) / 1000,
        drill: v.drill,
        pad: Math.round(v.drill * 1.6 * 100) / 100,
        from: layers[layers.length - 1].id,
        to: layers[0].id,
      });
    }
    expandBoardTo(res.bbox, off);
    formsFromModel();
    stackChanged();
    app.editor.zoomFit();
    uiNotice(`Imported ${res.vias.length} PTH vias from "${file.name}".`);
    if (res.warnings.length)
      uiNotice('Drill import warnings:\n- ' + res.warnings.join('\n- '), 'warn', 10000);
  } catch (e) {
    uiNotice('Drill import failed: ' + e.message, 'err', 8000);
  }
}

/* ---------- S-parameter devices (touchstone) ---------- */
async function loadDevLib() {
  try {
    app.devLib = (await apiJson('/api/devices')).devices.filter(d => !d.error);
  } catch (e) {
    app.devLib = [];
  }
  renderDevices();
}

function renderDevices() {
  const wrap = $('devList');
  wrap.innerHTML = '';
  const lib = app.devLib || [];
  const portNums = app.project.ports.map(p => p.number).sort((a, b) => a - b);
  for (const dev of app.project.devices) {
    const row = document.createElement('div');
    row.className = 'dev-row';
    const top = document.createElement('div');
    top.className = 'dr-top';
    const ref = textIn(dev.ref || 'Q1', v => { dev.ref = v; app.dirty(); });
    const fileSel = selIn(
      [['', '— pick file —'], ...lib.map(l => [l.file, `${l.file} (${l.nports}p)`])],
      dev.file || '',
      v => {
        dev.file = v;
        const meta = lib.find(l => l.file === v);
        dev.pins = new Array(meta ? meta.nports : 0).fill(null);
        renderDevices();
        app.dirty();
      });
    const del = mkBtn('×', () => {
      app.project.devices = app.project.devices.filter(d => d !== dev);
      renderDevices();
      app.dirty();
    });
    top.append(ref, fileSel, del);
    row.append(top);
    const meta = lib.find(l => l.file === dev.file);
    if (meta) {
      const pins = document.createElement('div');
      pins.className = 'dev-pins';
      pins.append(document.createTextNode('pins → ports: '));
      for (let k = 0; k < meta.nports; k++) {
        const sel = selIn(
          [['', '—'], ...portNums.map(n => [String(n), 'P' + n])],
          dev.pins && dev.pins[k] != null ? String(dev.pins[k]) : '',
          v => { dev.pins[k] = v ? parseInt(v, 10) : null; app.dirty(); });
        sel.title = `Device port ${k + 1}`;
        pins.append(sel);
      }
      row.append(pins);
      const info = document.createElement('div');
      info.className = 'dev-meta';
      info.textContent = `${(meta.fmin / 1e9).toFixed(2)}–${(meta.fmax / 1e9).toFixed(2)} GHz, `
        + `${meta.points} pts, ${meta.r} Ω reference`;
      row.append(info);
    }
    wrap.append(row);
  }
}

async function uploadTouchstone(file) {
  try {
    const res = await apiJson('/api/devices/upload', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: file.name, content: await file.text() }),
    });
    uiNotice(`Uploaded "${res.file}" (${res.nports}-port, `
      + `${(res.fmin / 1e9).toFixed(2)}–${(res.fmax / 1e9).toFixed(2)} GHz).`);
    await loadDevLib();
  } catch (e) {
    uiNotice('Upload failed: ' + e.message, 'err', 8000);
  }
}

/* ---------- verification tests ---------- */
let testsCases = null;
let testsPollT = null;

async function initTestsView() {
  if (!testsCases) {
    try {
      testsCases = (await apiJson('/api/tests')).cases;
    } catch (e) {
      $('testsCases').innerHTML = `<p class="muted">Failed to load test cases: ${e.message}</p>`;
      return;
    }
  }
  refreshTestsStatus();
}

async function refreshTestsStatus() {
  try {
    applyTestsStatus(await apiJson('/api/tests/status'));
  } catch (e) { /* server gone - ignore */ }
}

function chip(status, extra = '') {
  const s = document.createElement('span');
  s.className = 'tstatus ' + (status || '');
  s.textContent = (status || 'idle') + (extra ? ` ${extra}` : '');
  return s;
}

function applyTestsStatus(st) {
  const running = st.state === 'running';
  $('testsRunAll').disabled = running;
  $('testsRunUnit').disabled = running;
  // unit tier card
  const uc = $('testsUnit');
  uc.hidden = !st.unit;
  if (st.unit) {
    const el = $('testsUnitStatus');
    el.className = 'tstatus ' + st.unit.status;
    el.textContent = st.unit.status === 'running' ? 'running…'
      : `${st.unit.status} — ${st.unit.passed ?? 0} passed` +
        (st.unit.failed ? `, ${st.unit.failed} failed` : '');
    $('testsUnitDetail').hidden = !st.unit.detail;
    $('testsUnitDetail').textContent = st.unit.detail || '';
  }
  renderTestsCases(st.results || {}, running);
  if (running && !testsPollT) {
    testsPollT = setTimeout(() => { testsPollT = null; refreshTestsStatus(); }, 1200);
  }
  if (!running && st.state === 'done' && !applyTestsStatus._toasted) {
    applyTestsStatus._toasted = true;
    const vals = Object.values(st.results || {});
    const bad = vals.filter(r => r.status === 'fail' || r.status === 'error').length;
    if (vals.length) {
      uiNotice(bad ? `Verification finished: ${bad} of ${vals.length} benchmark(s) FAILED.`
        : `Verification finished: all ${vals.length} benchmark(s) passed.`, bad ? 'warn' : 'info', 8000);
    }
  }
  if (running) applyTestsStatus._toasted = false;
}

function renderTestsCases(results, running) {
  const wrap = $('testsCases');
  wrap.innerHTML = '';
  for (const c of testsCases || []) {
    const res = results[c.id] || {};
    const card = document.createElement('div');
    card.className = 'card';
    const head = document.createElement('div');
    head.className = 'card-head';
    const title = document.createElement('span');
    title.textContent = c.title;
    head.append(title, chip(res.status || 'idle', res.elapsed ? `(${res.elapsed}s)` : ''));
    const btn = document.createElement('button');
    btn.className = 'mini-btn';
    btn.textContent = `Run (~${c.minutes} min)`;
    btn.disabled = !!running;
    btn.onclick = () => startTests(false, [c.id]);
    head.append(btn);
    card.append(head);
    const desc = document.createElement('p');
    desc.className = 'tdesc';
    desc.textContent = c.desc;
    card.append(desc);
    if (res.metrics) {
      const tbl = document.createElement('table');
      tbl.className = 'tmetrics';
      tbl.innerHTML = '<thead><tr><th>Metric</th><th>Obtained</th>'
        + '<th>Accepted range</th><th>Reference</th><th></th></tr></thead>';
      const tb = document.createElement('tbody');
      for (const m of res.metrics) {
        const tr = document.createElement('tr');
        const fmt = v => v <= -100 ? '−∞' : String(v);
        tr.innerHTML =
          `<td>${m.label}</td>` +
          `<td class="num ${m.pass ? 'ok' : 'bad'}">${m.value} ${m.unit}</td>` +
          `<td class="num">${fmt(m.lo)} … ${fmt(m.hi)} ${m.unit}</td>` +
          `<td>${m.ref}</td>` +
          `<td class="${m.pass ? 'ok' : 'bad'}">${m.pass ? '✓' : '✗'}</td>`;
        tb.append(tr);
      }
      tbl.append(tb);
      card.append(tbl);
    }
    if (res.error) {
      const pre = document.createElement('pre');
      pre.className = 'log';
      pre.style.height = 'auto';
      pre.style.maxHeight = '160px';
      pre.textContent = res.error;
      card.append(pre);
    }
    wrap.append(card);
  }
}

async function startTests(unit, caseIds) {
  try {
    await apiJson('/api/tests/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ unit, cases: caseIds }),
    });
    refreshTestsStatus();
  } catch (e) {
    uiNotice('Cannot start tests: ' + e.message, 'err', 6000);
  }
}

/* ---------- server-side projects ---------- */
async function saveProjectToServer() {
  let name = app.project.name;
  if (!name) {
    name = await uiPrompt('Project name to save as:', '', 'Save');
    if (!name) return;
    app.project.name = name;
    formsFromModel();
  }
  try {
    const runId = app.currentRunId && String(app.currentRunId).startsWith('run_')
      ? app.currentRunId : null;
    const res = await apiJson('/api/projects/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, project: app.project, runId }),
    });
    if (res.name !== app.project.name) {
      app.project.name = res.name;
      formsFromModel();
    }
    app.dirty();
    uiNotice(`Saved "${res.name}" on the server`
      + (res.resultsSaved ? ' together with the latest results.'
        : runId ? ' (results could not be attached).' : ' (run a simulation to attach results).'));
  } catch (e) {
    uiNotice('Save failed: ' + e.message, 'err', 8000);
  }
}

async function openProjectsModal() {
  const modal = $('projModal');
  const list = $('projList');
  list.innerHTML = '<li class="muted">loading…</li>';
  modal.hidden = false;
  let projects = [];
  try {
    projects = (await apiJson('/api/projects')).projects;
  } catch (e) {
    list.innerHTML = '<li class="muted">failed to list projects</li>';
    return;
  }
  list.innerHTML = '';
  if (!projects.length) {
    list.innerHTML = '<li class="muted">No projects on the server yet — use Save.</li>';
    return;
  }
  for (const pr of projects) {
    const li = document.createElement('li');
    const nm = document.createElement('span');
    nm.className = 'pname';
    nm.textContent = pr.name;
    li.append(nm);
    if (pr.hasResults) {
      const b = document.createElement('span');
      b.className = 'badge';
      b.textContent = 'results';
      li.append(b);
    }
    const meta = document.createElement('span');
    meta.className = 'pmeta';
    meta.textContent = new Date(pr.mtime * 1000).toLocaleString();
    const del = document.createElement('button');
    del.className = 'pdel';
    del.textContent = '✕';
    del.title = 'Delete this project from the server';
    del.onclick = async e => {
      e.stopPropagation();
      if (!(await uiConfirm(`Delete project "${pr.name}" (and its stored results) from the server?`, 'Delete'))) return;
      try {
        await apiJson(`/api/projects/${encodeURIComponent(pr.name)}`, { method: 'DELETE' });
        openProjectsModal();
      } catch (err) { uiNotice('Delete failed: ' + err.message, 'err'); }
    };
    li.append(meta, del);
    li.onclick = async () => {
      modal.hidden = true;
      try {
        const data = await apiJson(`/api/projects/${encodeURIComponent(pr.name)}`);
        data.project.name = pr.name;
        loadProject(data.project);
        if (data.resultsId) {
          app.currentRunId = data.resultsId;
          await loadResults(data.resultsId);
          uiNotice(`Opened "${pr.name}" with its stored results.`);
        } else {
          uiNotice(`Opened "${pr.name}".`);
        }
      } catch (err) { uiNotice('Open failed: ' + err.message, 'err', 8000); }
    };
    list.append(li);
  }
}

/* ---------- export / save / open ---------- */
async function exportScript() {
  try {
    const { script } = await apiJson('/api/script', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(app.project),
    });
    download((app.project.name || 'pcb_sim') + '.m', script, 'text/plain');
  } catch (e) {
    uiNotice('Cannot generate script:\n' + e.message, 'err', 8000);
  }
}
function loadProject(p) {
  app.project = migrate(p);
  app.selection = null;
  if (!app.project.nextId) {
    const ids = ['shapes', 'vias', 'components', 'ports']
      .flatMap(k => app.project[k].map(o => o.id || 0));
    app.project.nextId = (ids.length ? Math.max(...ids) : 0) + 1;
  }
  formsFromModel();
  renderStackup();
  updateLayerSelect();
  renderObjList();
  renderProps();
  app.editor.zoomFit();
  app.dirty();
  if (app.meshVisible) app.refreshMesh();
}

/* ---------- init ---------- */
window.addEventListener('DOMContentLoaded', () => {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('openems_pcb_project')); } catch (e) { /* ignore */ }

  app.editor = new Editor($('editor'), app);
  app.jview = new JView($('jCanvas'), $('jInfo'), $('jMax'));
  Modal.init();
  initTabs();
  bindForms();
  loadProject(saved || defaultProject());
  requestAnimationFrame(() => app.editor.zoomFit());

  // tools
  const setTool = t => {
    if (app.tool === 'poly' && t !== 'poly') app.editor.cancelPoly();
    app.tool = t;
    document.querySelectorAll('#toolButtons .tool').forEach(b => b.classList.toggle('active', b.dataset.tool === t));
  };
  document.querySelectorAll('#toolButtons .tool').forEach(btn =>
    btn.addEventListener('click', () => setTool(btn.dataset.tool)));
  $('activeLayer').addEventListener('change', e => { app.activeLayer = e.target.value; });
  $('snapSel').addEventListener('change', e => { app.snapStep = parseFloat(e.target.value); });
  $('btnFit').addEventListener('click', () => app.editor.zoomFit());
  $('btnGrid').addEventListener('click', () => {
    app.gridVisible = !app.gridVisible;
    $('btnGrid').classList.toggle('active', app.gridVisible);
    app.editor.render();
  });
  document.querySelectorAll('.viewtab').forEach(btn =>
    btn.addEventListener('click', () => showView(btn.dataset.view)));

  // resizable right panel
  const uiPrefs = (() => {
    try { return JSON.parse(localStorage.getItem('openems_ui') || '{}'); } catch (e) { return {}; }
  })();
  if (uiPrefs.rightW) $('right').style.width = uiPrefs.rightW + 'px';
  $('rightSplit').addEventListener('mousedown', e => {
    e.preventDefault();
    $('rightSplit').classList.add('active');
    const move = ev => {
      const w = Math.min(640, Math.max(240, window.innerWidth - ev.clientX));
      $('right').style.width = w + 'px';
    };
    const up = () => {
      $('rightSplit').classList.remove('active');
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      uiPrefs.rightW = parseInt($('right').style.width, 10) || 320;
      try { localStorage.setItem('openems_ui', JSON.stringify(uiPrefs)); } catch (err) { /* ignore */ }
      if (app._lastRunStat) updateRunStats(app._lastRunStat);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
  $('btnResults').addEventListener('click', () => showView('results'));
  $('testsRunAll').addEventListener('click', () => startTests(false, (testsCases || []).map(c => c.id)));
  $('testsRunUnit').addEventListener('click', () => startTests(true, []));
  $('btnAddDevice').addEventListener('click', () => {
    app.project.devices.push({ id: app.project.nextId++,
      ref: 'Q' + (app.project.devices.length + 1), file: '', pins: [] });
    renderDevices();
    app.dirty();
  });
  $('btnUploadTs').addEventListener('click', () => $('tsInput').click());
  $('tsInput').addEventListener('change', async e => {
    const f = e.target.files[0];
    if (f) await uploadTouchstone(f);
    e.target.value = '';
  });
  loadDevLib();
  $('jScaleBar').style.background = `linear-gradient(to right, ${J_RAMP.join(', ')})`;
  $('btnMesh').addEventListener('click', () => {
    app.meshVisible = !app.meshVisible;
    $('btnMesh').classList.toggle('active', app.meshVisible);
    if (app.meshVisible) app.refreshMesh();
    else { $('meshInfo').textContent = ''; app.editor.render(); }
  });

  // top bar
  $('btnNew').addEventListener('click', async () => {
    if (!(await uiConfirm('Start a new empty project?\n\nThe current project is replaced (save it as JSON first if you want to keep it).', 'New project'))) return;
    const p = defaultProject();
    p.name = '';   // shows as "Unsaved" until saved or opened from a file
    p.shapes = []; p.ports = []; p.vias = []; p.components = [];
    loadProject(p);
    uiNotice('New empty project started.');
  });
  $('btnSave').addEventListener('click', saveProjectToServer);
  $('btnExportCfg').addEventListener('click', () => {
    download((app.project.name || 'unsaved') + '.json',
      JSON.stringify(app.project, null, 2), 'application/json');
  });
  $('btnOpen').addEventListener('click', openProjectsModal);
  $('projClose').addEventListener('click', () => { $('projModal').hidden = true; });
  $('projModal').addEventListener('click', e => {
    if (e.target === $('projModal')) $('projModal').hidden = true;
  });
  $('projImport').addEventListener('click', () => {
    $('projModal').hidden = true;
    $('fileInput').click();
  });
  $('fileInput').addEventListener('change', async e => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const p = JSON.parse(await f.text());
      p.name = f.name.replace(/\.json$/i, '');   // project name = file name
      loadProject(p);
    } catch (err) { uiNotice('Not a valid project file: ' + err.message, 'err', 8000); }
    e.target.value = '';
  });
  $('btnExport').addEventListener('click', exportScript);
  $('btnRun').addEventListener('click', startRun);
  $('btnStop').addEventListener('click', stopRun);

  // fabrication data import
  $('gerberInput').addEventListener('change', async e => {
    const f = e.target.files[0];
    if (f && app._importLayer) await importGerberFile(app._importLayer, f);
    e.target.value = '';
  });
  $('btnDrill').addEventListener('click', () => $('drillInput').click());
  $('drillInput').addEventListener('change', async e => {
    const f = e.target.files[0];
    if (f) await importDrillFile(f);
    e.target.value = '';
  });

  // results controls
  $('reflView').addEventListener('change', renderCharts);
  $('transView').addEventListener('change', renderCharts);
  $('reflPop').addEventListener('click', () =>
    Modal.open('Reflection', (c, t) => makeReflChart(c, t)));
  $('transPop').addEventListener('click', () =>
    Modal.open('Transmission', (c, t) => makeTransChart(c, t)));
  $('tdPop').addEventListener('click', () =>
    Modal.open('Time domain', (c, t) => makeTdChart(c, t)));
  $('jInterp').addEventListener('change', e => {
    app.jview.smooth = e.target.checked;
    app.jview.render();
  });

  // results pane configuration + export
  for (const key of ['refl', 'trans', 'td', 'j']) {
    const cb = $('cfg_' + key);
    cb.checked = resultsPrefs[key];
    cb.addEventListener('change', () => {
      resultsPrefs[key] = cb.checked;
      saveResultsPrefs();
      renderCharts();
      if (app.jview && app.jview.data) app.jview.render();
    });
  }
  $('rCols').value = resultsPrefs.cols;
  $('rCols').addEventListener('change', e => {
    resultsPrefs.cols = e.target.value;
    saveResultsPrefs();
    renderCharts();
    if (app.jview && app.jview.data) app.jview.render();
  });
  document.querySelectorAll('[data-png]').forEach(btn =>
    btn.addEventListener('click', () => exportPNG(btn.dataset.png)));
  document.querySelectorAll('[data-csv]').forEach(btn =>
    btn.addEventListener('click', () => exportCSVFor(btn.dataset.csv)));

  // user-resizable plot wraps: re-render the owning chart while dragging
  let rszT = null;
  const rszObs = new ResizeObserver(() => {
    clearTimeout(rszT);
    rszT = setTimeout(() => {
      if (!$('results').hidden) {
        renderCharts();
        if (app.jview && app.jview.data) app.jview.render();
      }
    }, 80);
  });
  document.querySelectorAll('.plotwrap.rsz').forEach(el => rszObs.observe(el));

  // persist card sizes set via the resize corner
  const rgrid = document.querySelector('.rgrid');
  let cardT = null;
  const cardObs = new ResizeObserver(() => {
    clearTimeout(cardT);
    cardT = setTimeout(() => {
      let changed = false;
      for (const el of rgrid.children) {
        if (el.style.width || el.style.height) {
          resultsPrefs.sizes[el.id] = { w: el.style.width, h: el.style.height };
          changed = true;
        }
      }
      if (changed) saveResultsPrefs();
    }, 300);
  });
  [...rgrid.children].forEach(c => cardObs.observe(c));

  // drag the grip to rearrange cards
  let dragCard = null;
  document.querySelectorAll('.rgrid .grip').forEach(grip => {
    const card = grip.closest('.card');
    grip.addEventListener('dragstart', e => {
      dragCard = card;
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.id);
      e.dataTransfer.setDragImage(card, 24, 24);
    });
    grip.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      dragCard = null;
      resultsPrefs.order = [...rgrid.children].map(c => c.id);
      saveResultsPrefs();
      renderCharts();
      if (app.jview && app.jview.data) app.jview.render();
    });
  });
  rgrid.addEventListener('dragover', e => {
    if (!dragCard) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const target = e.target.closest('.card');
    if (!target || target === dragCard) return;
    const r = target.getBoundingClientRect();
    const before = (e.clientX - r.left) / r.width < 0.5;
    rgrid.insertBefore(dragCard, before ? target : target.nextSibling);
  });
  rgrid.addEventListener('drop', e => e.preventDefault());

  $('rReset').addEventListener('click', () => {
    resultsPrefs.order = [];
    resultsPrefs.sizes = {};
    saveResultsPrefs();
    for (const id of ['reflCard', 'transCard', 'tdCard', 'jCard']) {
      const el = $(id);
      if (!el) continue;
      el.style.width = '';
      el.style.height = '';
      rgrid.append(el);
    }
    renderCharts();
    if (app.jview && app.jview.data) app.jview.render();
  });
  $('jMode').addEventListener('change', updateJFreqs);
  $('jLayer').addEventListener('change', updateJFreqs);
  $('jFreq').addEventListener('change', () => {
    const layer = $('jLayer').value;
    const d = app.jdumps.find(q => q.layer === layer && q.k === parseInt($('jFreq').value, 10));
    if (d) $('jFreqNum').value = (d.freq / 1e9).toFixed(3);
    loadJFrame();
  });
  $('jFreqNum').addEventListener('change', () => {
    const layer = $('jLayer').value;
    const f = parseFloat($('jFreqNum').value) * 1e9;
    const exact = app.jdumps.find(q => q.layer === layer && Math.abs(q.freq - f) < 1e3);
    if (exact) $('jFreq').value = String(exact.k);
    loadJFrame();
  });
  $('jGif').addEventListener('click', exportJGif);
  $('jPlay').addEventListener('click', () => {
    if (app.jview.playing) { app.jview.pause(); $('jPlay').innerHTML = '&#9654;'; }
    else { app.jview.play(); $('jPlay').innerHTML = '&#10074;&#10074;'; }
  });
  $('jPhase').addEventListener('input', e => {
    app.jview.pause();
    $('jPlay').innerHTML = '&#9654;';
    const v = parseFloat(e.target.value);
    if ($('jMode').value === 'td') app.jview.setFrame(v);
    else app.jview.setPhase(v);
  });
  app.jview.onFrame = (mode, v) => { $('jPhase').value = v; };
  $('jPop').addEventListener('click', () =>
    Modal.open('Current density', canvas => {
      app.jview.addCanvas(canvas);
      return { destroy: () => app.jview.removeCanvas(canvas) };
    }));

  // stackup
  $('addCond').addEventListener('click', () => {
    const id = 'L' + app.project.nextId++;
    app.project.stackup.unshift({ id, name: 'Conductor', type: 'conductor', thickness: 0.035, fill: false });
    stackChanged();
  });
  $('addDiel').addEventListener('click', () => {
    const id = 'L' + app.project.nextId++;
    app.project.stackup.push({ id, name: 'Dielectric', type: 'dielectric', thickness: 0.2, er: 4.3, tand: 0.02 });
    stackChanged();
  });

  // keyboard
  window.addEventListener('keydown', e => {
    if (e.target.matches('input, select, textarea')) return;
    if ((e.ctrlKey || e.metaKey) && !e.altKey) {
      const k = e.key.toLowerCase();
      if (k === 'c') { app.copySelection(false); e.preventDefault(); }
      else if (k === 'x') { app.copySelection(true); e.preventDefault(); }
      else if (k === 'v') { app.pasteClipboard(); e.preventDefault(); }
      else if (k === 'd') { if (app.copySelection(false)) app.pasteClipboard(); e.preventDefault(); }
      return;   // never treat ctrl-combos as tool shortcuts
    }
    const nudge = app.snapStep || 0.1;
    const sel = app.editor.selectedObj();
    const move = (dx, dy) => {
      if (!sel) return;
      app.editor.translate(sel, dx, dy);
      app.onObjectChanged(true);
      app.editor.render();
      e.preventDefault();
    };
    switch (e.key) {
      case 'v': case 'V': setTool('select'); break;
      case 'r': case 'R': setTool('rect'); break;
      case 'c': case 'C': setTool('circle'); break;
      case 'p': case 'P': setTool('port'); break;
      case 'm': case 'M': $('btnMesh').click(); break;
      case 'g': case 'G': $('btnGrid').click(); break;
      case 'f': case 'F': app.editor.zoomFit(); break;
      case 'Enter': if (app.tool === 'poly') { app.editor.finishPoly(); e.preventDefault(); } break;
      case 'Escape':
        if (app.editor.pendingPoly) app.editor.cancelPoly();
        else app.select(null);
        break;
      case 'Delete': case 'Backspace': app.deleteSelection(); e.preventDefault(); break;
      case 'ArrowLeft': move(-nudge, 0); break;
      case 'ArrowRight': move(nudge, 0); break;
      case 'ArrowUp': move(0, nudge); break;
      case 'ArrowDown': move(0, -nudge); break;
    }
  });

  // resume polling if a run is active
  apiJson('/api/status?offset=0').then(st => {
    if (['starting', 'running', 'post'].includes(st.state)) {
      app.logOffset = 0;
      showTab('run');
      setRunUI(st.state, st.percent, st.elapsed);
      poll();
    }
  }).catch(() => {});
});
