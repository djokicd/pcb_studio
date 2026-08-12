/* Advanced tools host.

   Tools describe themselves (fields, actions, panels) and this file
   renders them, so a new tool needs no code here. Widgets: number,
   select, check, freq, gamma. Panels: table, text, chart, smith,
   schematic — the last one draws a two-port chain from a list of
   blocks, which is the shape every RF tool's result takes. */
'use strict';

const AdvTools = {
  list: [],
  tool: null,      // {id, schema}
  values: {},
  result: null,
  charts: {},
  job: null,
};

async function loadAdvTools() {
  try {
    const { tools } = await apiJson('/api/tools');
    AdvTools.list = tools || [];
  } catch (e) {
    AdvTools.list = [];
  }
  const holder = $('advToolsMenu');
  if (!holder) return;
  holder.textContent = '';
  if (!AdvTools.list.length) {
    const b = document.createElement('button');
    b.disabled = true;
    b.textContent = 'none installed';
    holder.append(b);
    return;
  }
  let group = null;
  for (const t of AdvTools.list) {
    if (t.group && t.group !== group) {
      group = t.group;
      const lab = document.createElement('div');
      lab.className = 'menu-label';
      lab.textContent = group;
      holder.append(lab);
    }
    const b = document.createElement('button');
    b.textContent = `${t.icon ? t.icon + ' ' : ''}${t.name}…`;
    b.title = t.description || '';
    b.onclick = () => openAdvTool(t.id);
    holder.append(b);
  }
}

/* the panels are canvas-drawn at the width they had when rendered, so a
   window resize has to redraw them */
let advResizeT = null;
window.addEventListener('resize', () => {
  if ($('advModal').hidden || !AdvTools.result) return;
  clearTimeout(advResizeT);
  advResizeT = setTimeout(() => advRelayout(AdvTools.result), 150);
});

async function openAdvTool(id) {
  const modal = $('advModal');
  modal.hidden = false;
  $('advTitle').textContent = 'Loading…';
  for (const id of ['advForm', 'advFoot', 'advPanels', 'advPinned', 'advTabs']) {
    $(id).textContent = '';
  }
  $('advWarn').hidden = true;
  AdvTools.charts = {};
  AdvTools.result = null;
  try {
    const schema = await apiJson(`/api/tools/${id}/schema`, { method: 'POST' });
    AdvTools.tool = { id, schema };
    $('advTitle').textContent = schema.title || id;
    $('advIntro').textContent = schema.intro || '';
    buildAdvForm(schema);
    buildAdvPanels(schema);
    const auto = (schema.actions || []).find(a => a.auto);
    if (auto) runAdvAction(auto.id);
  } catch (e) {
    $('advTitle').textContent = 'Tool failed to load';
    $('advIntro').textContent = e.message;
  }
}

/* ---------- form ----------
   Groups fold away: a tool's schema is long (this one has 24 fields) but
   a given task touches one section of it, and the actions have to stay in
   sight rather than hiding below twenty inputs. Fold state is per tool
   and per group, so the sections you work in stay where you left them. */
function advPref(key, fallback) {
  try {
    const v = localStorage.getItem(`adv.${AdvTools.tool.id}.${key}`);
    return v == null ? fallback : v;
  } catch (e) { return fallback; }
}

function advSetPref(key, value) {
  try { localStorage.setItem(`adv.${AdvTools.tool.id}.${key}`, value); }
  catch (e) { /* private mode: fold state just does not persist */ }
}

function buildAdvForm(schema) {
  const box = $('advForm');
  box.textContent = '';
  AdvTools.values = {};
  AdvTools.freqFields = new Set();
  let form = null;
  const newForm = (parent) => {
    form = document.createElement('div');
    form.className = 'form';
    (parent || box).append(form);
  };
  newForm();
  for (const f of schema.fields || []) {
    if (f.group) {
      const sec = document.createElement('details');
      sec.className = 'adv-group';
      const key = 'group.' + f.group;
      sec.open = advPref(key, f.open === false ? '0' : '1') === '1';
      sec.ontoggle = () => advSetPref(key, sec.open ? '1' : '0');
      const sum = document.createElement('summary');
      sum.textContent = f.group;
      sec.append(sum);
      box.append(sec);
      newForm(sec);
      continue;
    }
    const label = document.createElement('label');
    if (f.help) label.title = f.help;
    label.append(f.label || f.id);
    if (f.type === 'gamma') {
      // two inputs: magnitude and angle, stored as <id>_mag / <id>_ang
      const wrap = document.createElement('span');
      wrap.className = 'adv-gamma';
      const mag = numField(`${f.id}_mag`, f.mag ?? 0, 0.01, 0, 0.999);
      const ang = numField(`${f.id}_ang`, f.ang ?? 0, 1, -360, 360);
      mag.title = '|Γ|';
      ang.title = '∠Γ in degrees';
      wrap.append(mag, ang);
      label.append(wrap);
    } else if (f.type === 'select') {
      const sel = document.createElement('select');
      for (const [v, t] of f.options || []) {
        const o = document.createElement('option');
        o.value = v; o.textContent = t;
        sel.append(o);
      }
      sel.value = f.value ?? '';
      AdvTools.values[f.id] = sel.value;
      sel.onchange = () => { AdvTools.values[f.id] = sel.value; advFieldChanged(f); };
      label.append(sel);
      if (f.meta) AdvTools.meta = f.meta;
    } else if (f.type === 'check') {
      const inp = document.createElement('input');
      inp.type = 'checkbox';
      inp.checked = !!f.value;
      AdvTools.values[f.id] = inp.checked;
      inp.onchange = () => { AdvTools.values[f.id] = inp.checked; };
      label.classList.add('check');
      label.append(inp);
    } else {
      const inp = numField(f.id, f.value, f.step ?? 1, f.min, f.max);
      // frequency fields are edited in GHz but travel in Hz
      if (f.type === 'freq') { inp.dataset.freq = '1'; AdvTools.freqFields.add(f.id); }
      label.append(inp);
    }
    form.append(label);
  }

  // the actions live outside the scrolling field list, so they are
  // reachable whichever section you are editing
  const foot = $('advFoot');
  foot.textContent = '';
  const bar = document.createElement('div');
  bar.className = 'adv-actions';
  for (const a of schema.actions || []) {
    const b = document.createElement('button');
    b.textContent = a.label || a.id;
    if (a.primary) b.className = 'primary';
    b.onclick = () => runAdvAction(a.id);
    b.id = 'advAct_' + a.id;
    bar.append(b);
  }
  foot.append(bar);
  const note = document.createElement('p');
  note.className = 'muted mini-note';
  note.id = 'advNote';
  foot.append(note);

  // frequency fields left open by the tool follow the selected file, so
  // the first analysis lands mid-band instead of on an edge
  if (AdvTools.meta && AdvTools.values.f0 == null) advFieldChanged({ id: 'device' });
}

function numField(id, value, step, min, max) {
  const inp = document.createElement('input');
  inp.type = 'number';
  inp.step = String(step ?? 1);
  if (min != null) inp.min = String(min);
  if (max != null) inp.max = String(max);
  inp.value = value == null ? '' : value;
  inp.dataset.fid = id;
  AdvTools.values[id] = value == null ? null : Number(value);
  inp.onchange = () => {
    const v = parseFloat(inp.value);
    AdvTools.values[id] = isFinite(v) ? v : null;
  };
  return inp;
}

/* selecting a device re-ranges f0 and the band to that file */
function advFieldChanged(f) {
  if (f.id !== 'device' || !AdvTools.meta) return;
  const m = AdvTools.meta[AdvTools.values.device];
  if (!m) return;
  const set = (id, ghz) => {
    const el = $('advForm').querySelector(`[data-fid="${id}"]`);
    if (el) { el.value = ghz; AdvTools.values[id] = ghz; }
  };
  const lo = m.f0 / 1e9, hi = m.f1 / 1e9;
  set('f0', +((lo + hi) / 2).toFixed(4));
  set('band_lo', +(lo + (hi - lo) * 0.25).toFixed(4));
  set('band_hi', +(lo + (hi - lo) * 0.75).toFixed(4));
}

/* ---------- panels ----------
   A panel marked `pin` sits above the tab strip and is always visible
   (the schematic: it is the answer, the plots are the evidence). The rest
   are tabs, so one plot gets the whole area instead of four sharing it —
   with an "All" tab that keeps the old side-by-side grid. */
function buildAdvPanels(schema) {
  const host = $('advPanels');
  const pinned = $('advPinned');
  const tabs = $('advTabs');
  host.textContent = '';
  pinned.textContent = '';
  tabs.textContent = '';
  const tabbed = [];
  for (const p of schema.panels || []) {
    const card = document.createElement('div');
    card.className = 'adv-card';
    card.id = 'advCard_' + p.id;
    const h = document.createElement('h3');
    h.textContent = p.title || p.id;
    card.append(h);
    const body = document.createElement('div');
    body.id = 'advPanel_' + p.id;
    body.className = 'adv-body adv-' + p.type;
    card.append(body);
    if (p.pin) { pinned.append(card); continue; }
    host.append(card);
    tabbed.push(p);
  }
  AdvTools.tabbed = tabbed;
  tabs.hidden = tabbed.length < 2;
  if (tabs.hidden) { host.classList.remove('single'); return; }
  for (const p of tabbed) {
    const b = document.createElement('button');
    b.textContent = p.title || p.id;
    b.dataset.panel = p.id;
    b.onclick = () => showAdvPanel(p.id);
    tabs.append(b);
  }
  const all = document.createElement('button');
  all.textContent = 'All';
  all.dataset.panel = '*';
  all.title = 'Show every panel at once';
  all.onclick = () => showAdvPanel('*');
  tabs.append(all);
  const want = advPref('panel', tabbed[0].id);
  showAdvPanel(tabbed.some(p => p.id === want) || want === '*'
    ? want : tabbed[0].id);
}

function showAdvPanel(id) {
  const host = $('advPanels');
  AdvTools.panel = id;
  advSetPref('panel', id);
  host.classList.toggle('single', id !== '*');
  for (const p of AdvTools.tabbed || []) {
    const card = $('advCard_' + p.id);
    if (card) card.hidden = id !== '*' && p.id !== id;
  }
  for (const b of $('advTabs').children) {
    b.classList.toggle('on', b.dataset.panel === id);
  }
  // A canvas in a hidden card measured zero, so the charts that just
  // became visible have to be redrawn. Once now (each redraw releases the
  // inline width it was holding its card open with) and once after the
  // frame settles, so the last measurement is of the final layout.
  if (AdvTools.result) {
    advRelayout(AdvTools.result);
    requestAnimationFrame(() => advRelayout(AdvTools.result));
  }
}

function renderAdvPanels(res) {
  const schema = AdvTools.tool.schema;
  for (const p of schema.panels || []) {
    const el = $('advPanel_' + p.id);
    if (!el) continue;
    if (p.type === 'table') renderAdvTable(el, res[p.id]);
    else if (p.type === 'text') el.textContent = res[p.id] || '';
    else if (p.type === 'schematic') drawSchematic(el, res.schematic);
    else if (p.type === 'chart') renderAdvChart(el, p, res.series);
    else if (p.type === 'smith') renderAdvSmith(el, p, res.series);
  }
  // The charts size themselves from their container and write it as an
  // inline width, but the first draw happens before the results column
  // has grown its scrollbar - so measure again once layout has settled.
  advRelayout(res);
  requestAnimationFrame(() => advRelayout(res));

  const warn = $('advWarn');
  const list = res.warnings || [];
  warn.hidden = !list.length;
  warn.textContent = '';
  for (const w of list) {
    const d = document.createElement('div');
    d.textContent = w;
    warn.append(d);
  }
}

function advRelayout(res) {
  for (const [id, ch] of Object.entries(AdvTools.charts)) {
    const card = $('advCard_' + id);
    if (!ch || !ch.draw || (card && card.hidden)) continue;   // measures 0
    // the chart writes its pixel width back as an inline style; left in
    // place it props the flex card open and the next measurement reads
    // the old width, so hand the layout back to CSS before re-measuring
    ch.canvas.style.width = '100%';
    ch.draw();
  }
  const el = $('advPanel_schematic');
  if (el && res && res.schematic) drawSchematic(el, res.schematic);
}

function renderAdvTable(el, rows) {
  el.textContent = '';
  if (!rows || !rows.length) return;
  const t = document.createElement('table');
  t.className = 'runinfo';
  const tb = document.createElement('tbody');
  for (const [k, v] of rows) {
    const tr = document.createElement('tr');
    const a = document.createElement('td'); a.textContent = k;
    const b = document.createElement('td'); b.textContent = v;
    tr.append(a, b); tb.append(tr);
  }
  t.append(tb); el.append(t);
}

function advCanvas(el) {
  el.textContent = '';
  const wrap = document.createElement('div');
  // 'rsz' makes the chart take its height from the wrapper instead of
  // deriving it from the width, so a tabbed panel fills the whole area
  wrap.className = 'plotwrap rsz';
  const cv = document.createElement('canvas');
  const tip = document.createElement('div');
  tip.className = 'chart-tip';
  tip.hidden = true;
  wrap.append(cv, tip);
  el.append(wrap);
  return [cv, tip];
}

function renderAdvChart(el, p, series) {
  if (!series || !series[p.x]) return;
  const [cv, tip] = advCanvas(el);
  const ch = new MagChart(cv, tip);
  const list = (p.series || []).filter(([k]) => series[k]).map(([k, label], i) => ({
    label, values: series[k], ci: i,
  }));
  ch.setData({ freq: series[p.x], series: list, unit: p.ylabel || '' });
  ch.draw();
  AdvTools.charts[p.id] = ch;
}

function renderAdvSmith(el, p, series) {
  if (!series) return;
  const [cv, tip] = advCanvas(el);
  const ch = new SmithChart(cv, tip);
  const list = (p.series || []).filter(([k]) => series[k]).map(([k, label], i) => ({
    label, ci: i,
    re: series[k].map(v => v[0]),
    im: series[k].map(v => v[1]),
  }));
  ch.setData({ freq: series.f, z0: 50, series: list });
  ch.draw();
  AdvTools.charts[p.id] = ch;
}

/* ---------- schematic ----------
   A chain drawn left to right: source, the input network's blocks, the
   device, the output network's blocks (mirrored), load. Series parts sit
   in the line; shunt parts and stubs hang from it to ground. */
const SCH_H = 190;

function drawSchematic(el, sch) {
  el.textContent = '';
  if (!sch) return;
  const cv = document.createElement('canvas');
  const wrap = document.createElement('div');
  wrap.className = 'adv-sch';
  wrap.append(cv);
  el.append(wrap);
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(560, el.clientWidth || 560);
  // below the minimum the wrapper scrolls; squeezing 560 px of symbols
  // into 300 would only make them unreadable
  cv.style.width = w + 'px';
  cv.style.height = SCH_H + 'px';
  cv.width = w * dpr;
  cv.height = SCH_H * dpr;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const cs = getComputedStyle(document.body);
  const ink = cs.getPropertyValue('--text').trim() || '#e8e7e0';
  const muted = cs.getPropertyValue('--muted').trim() || '#898781';
  const accent = cs.getPropertyValue('--accent').trim() || '#3987e5';

  const cells = [{ type: 'source', label: `${sch.z0 || 50} Ω` }]
    .concat(sch.input || [])
    .concat([{ type: 'device', label: sch.device || 'device', stab: sch.stab }])
    .concat((sch.output || []).slice().reverse())
    .concat([{ type: 'load', label: `${sch.z0 || 50} Ω` }]);

  const y = 74;                       // the signal line
  const gnd = 150;
  const pad = 16;
  const cw = (w - pad * 2) / cells.length;
  ctx.strokeStyle = ink;
  ctx.fillStyle = ink;
  ctx.lineWidth = 1.4;
  ctx.font = '11px system-ui';
  ctx.textAlign = 'center';

  // ground rail
  ctx.strokeStyle = muted;
  ctx.beginPath(); ctx.moveTo(pad, gnd); ctx.lineTo(w - pad, gnd); ctx.stroke();
  ctx.strokeStyle = ink;

  cells.forEach((c, i) => {
    const x0 = pad + i * cw, xc = x0 + cw / 2, x1 = x0 + cw;
    // the through line
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    const t = c.type || '';
    if (t === 'source' || t === 'load') {
      ctx.strokeStyle = muted;
      ctx.beginPath(); ctx.arc(xc, y, 13, 0, 7); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(xc, y + 13); ctx.lineTo(xc, gnd); ctx.stroke();
      drawGnd(ctx, xc, gnd, muted);
      ctx.strokeStyle = ink;
      ctx.fillStyle = muted;
      ctx.fillText(t === 'source' ? 'source' : 'load', xc, y - 22);
      ctx.fillStyle = ink;
      ctx.fillText(c.label, xc, y + 32);
    } else if (t === 'device') {
      ctx.fillStyle = accent;
      ctx.globalAlpha = 0.14;
      ctx.fillRect(xc - 34, y - 22, 68, 44);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = accent;
      ctx.strokeRect(xc - 34, y - 22, 68, 44);
      ctx.strokeStyle = ink;
      ctx.fillStyle = ink;
      ctx.fillText(c.label, xc, y + 4);
      ctx.fillStyle = muted;
      ctx.fillText('device', xc, y - 30);
      (c.stab || []).forEach((s, k) => {
        ctx.fillText(`${s[0]} ${s[1]}`, xc, gnd + 14 + k * 12);
      });
      ctx.fillStyle = ink;
    } else if (t.startsWith('series_')) {
      drawSeries(ctx, xc, y, t.endsWith('_l') ? 'L' : 'C', ink);
      label2(ctx, xc, y, c.label, c.sub, ink, muted);
    } else if (t.startsWith('shunt_')) {
      ctx.beginPath(); ctx.moveTo(xc, y); ctx.lineTo(xc, y + 26); ctx.stroke();
      drawShunt(ctx, xc, y + 26, t.endsWith('_l') ? 'L' : 'C', ink);
      ctx.beginPath(); ctx.moveTo(xc, y + 52); ctx.lineTo(xc, gnd); ctx.stroke();
      drawGnd(ctx, xc, gnd, muted);
      ctx.fillStyle = ink;
      ctx.fillText(c.label, xc, y - 12);
    } else if (t === 'line' || t === 'line_z') {
      ctx.fillStyle = muted;
      ctx.globalAlpha = 0.18;
      ctx.fillRect(xc - 26, y - 7, 52, 14);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = ink;
      ctx.strokeRect(xc - 26, y - 7, 52, 14);
      label2(ctx, xc, y, c.label, c.sub, ink, muted);
    } else if (t.startsWith('stub_')) {
      ctx.beginPath(); ctx.moveTo(xc, y); ctx.lineTo(xc, y + 30); ctx.stroke();
      ctx.strokeRect(xc - 7, y + 30, 14, 30);
      if (t === 'stub_short') {
        ctx.beginPath(); ctx.moveTo(xc, y + 60); ctx.lineTo(xc, gnd); ctx.stroke();
        drawGnd(ctx, xc, gnd, muted);
      } else {
        ctx.strokeStyle = muted;
        ctx.beginPath(); ctx.moveTo(xc - 9, y + 62); ctx.lineTo(xc + 9, y + 62); ctx.stroke();
        ctx.strokeStyle = ink;
      }
      ctx.fillStyle = ink;
      ctx.fillText(c.label, xc, y - 12);
      ctx.fillStyle = muted;
      ctx.fillText(t === 'stub_short' ? 'short stub' : 'open stub', xc, y - 24);
      if (c.sub) ctx.fillText(c.sub, xc, gnd + 14);
      ctx.fillStyle = ink;
    } else if (t === 'ideal' || t === 'wire') {
      ctx.fillStyle = muted;
      ctx.fillText(c.label || 'thru', xc, y - 12);
      ctx.fillStyle = ink;
    }
  });

  // terminations actually presented to the device
  ctx.fillStyle = muted;
  ctx.textAlign = 'left';
  const gs = sch.gammaS || [0, 0], gl = sch.gammaL || [0, 0];
  const pol = g => `${Math.hypot(g[0], g[1]).toFixed(3)} ∠ `
    + `${(Math.atan2(g[1], g[0]) * 180 / Math.PI).toFixed(1)}°`;
  ctx.fillText(`Γ_S = ${pol(gs)}`, pad, 20);
  ctx.textAlign = 'right';
  ctx.fillText(`Γ_L = ${pol(gl)}`, w - pad, 20);
  ctx.textAlign = 'center';
  if (sch.f0) {
    ctx.fillText(`synthesized at ${(sch.f0 / 1e9).toFixed(4)} GHz`, w / 2, SCH_H - 6);
  }
}

function label2(ctx, x, y, a, b, ink, muted) {
  ctx.fillStyle = ink;
  ctx.fillText(a || '', x, y - 14);
  if (b && b !== 'L' && b !== 'C') {
    ctx.fillStyle = muted;
    ctx.fillText(b, x, y + 26);
  }
  ctx.fillStyle = ink;
}

function drawGnd(ctx, x, y, muted) {
  ctx.strokeStyle = muted;
  for (let k = 0; k < 3; k++) {
    const half = 9 - k * 3;
    ctx.beginPath();
    ctx.moveTo(x - half, y + k * 4); ctx.lineTo(x + half, y + k * 4);
    ctx.stroke();
  }
}

function drawSeries(ctx, x, y, kind, ink) {
  ctx.strokeStyle = ink;
  if (kind === 'C') {
    ctx.clearRect(x - 5, y - 12, 10, 24);
    ctx.beginPath();
    ctx.moveTo(x - 4, y - 11); ctx.lineTo(x - 4, y + 11);
    ctx.moveTo(x + 4, y - 11); ctx.lineTo(x + 4, y + 11);
    ctx.stroke();
  } else {
    ctx.clearRect(x - 22, y - 12, 44, 12);
    ctx.beginPath();
    for (let k = 0; k < 4; k++) ctx.arc(x - 15 + k * 10, y, 5, Math.PI, 0);
    ctx.stroke();
  }
}

function drawShunt(ctx, x, y, kind, ink) {
  ctx.strokeStyle = ink;
  if (kind === 'C') {
    ctx.beginPath();
    ctx.moveTo(x - 11, y + 9); ctx.lineTo(x + 11, y + 9);
    ctx.moveTo(x - 11, y + 17); ctx.lineTo(x + 11, y + 17);
    ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y + 9); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x, y + 17); ctx.lineTo(x, y + 26); ctx.stroke();
  } else {
    ctx.beginPath();
    for (let k = 0; k < 4; k++) ctx.arc(x, y + 4 + k * 6, 5, -Math.PI / 2, Math.PI / 2);
    ctx.stroke();
  }
}

/* ---------- actions ---------- */
async function runAdvAction(actionId) {
  const tool = AdvTools.tool;
  if (!tool) return;
  const act = (tool.schema.actions || []).find(a => a.id === actionId) || {};
  const note = $('advNote');
  const btn = $('advAct_' + actionId);
  if (btn) btn.disabled = true;
  note.textContent = act.job ? 'Running…' : 'Computing…';
  try {
    const payload = { ...AdvTools.values, nonce: Date.now() };
    if (payload.f0 != null) payload.f0 = payload.f0 * 1e9;
    const res = await apiJson(`/api/tools/${tool.id}/${actionId}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (act.job && res.job) { pollAdvJob(res.job, actionId); return; }
    const needsAnalysis = applyAdvResult(res, actionId);
    if (needsAnalysis && actionId !== 'analyse') await runAdvAction('analyse');
    note.textContent = res.note || '';
  } catch (e) {
    note.textContent = 'Failed: ' + e.message;
  } finally {
    if (btn && !act.job) btn.disabled = false;
  }
}

function applyAdvResult(res, actionId) {
  // an action that returns field values feeds them back into the form
  let touched = false;
  for (const k of Object.keys(res)) {
    const el = $('advForm').querySelector(`[data-fid="${k}"]`);
    if (el && typeof res[k] === 'number') {
      // the wire carries Hz; the field is in GHz
      const v = AdvTools.freqFields.has(k) ? res[k] / 1e9 : res[k];
      el.value = v;
      AdvTools.values[k] = v;
      touched = true;
    }
  }
  if (res.stab) {
    for (const [k, v] of Object.entries(res.stab)) {
      const el = $('advForm').querySelector(`[data-fid="${k}"]`);
      if (el) { el.value = v; AdvTools.values[k] = v; touched = true; }
    }
  }
  if (res.series) { AdvTools.result = res; renderAdvPanels(res); }
  // whether the caller should now re-analyse; the caller awaits that, so
  // its own status line is written last instead of being overwritten by
  // a stray analysis finishing afterwards
  return touched && !res.series;
}

async function pollAdvJob(jobId, actionId) {
  const note = $('advNote');
  const btn = $('advAct_' + actionId);
  try {
    const st = await apiJson(`/api/tools/${AdvTools.tool.id}/job`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job: jobId }),
    });
    const tail = (st.log || []).slice(-3).join(' · ');
    if (st.state === 'running') {
      note.textContent = 'Optimizing… ' + tail;
      setTimeout(() => pollAdvJob(jobId, actionId), 700);
      return;
    }
    if (btn) btn.disabled = false;
    if (st.state === 'error') { note.textContent = 'Failed: ' + st.error; return; }
    const r = st.result || {};
    const needsAnalysis = applyAdvResult(r, actionId);
    const met = r.met || {};
    const ok = Object.values(met).every(Boolean);
    const verdict = (ok ? 'Targets met' : 'Targets NOT fully met')
      + (r.minGainDb != null ? ` · min |S21| ${r.minGainDb.toFixed(2)} dB` : '')
      + (r.maxSwrIn != null ? ` · SWR in ≤ ${r.maxSwrIn.toFixed(2)}` : '')
      + (r.maxSwrOut != null ? ` · SWR out ≤ ${r.maxSwrOut.toFixed(2)}` : '')
      + (r.uncond == null ? ''      // band stability was not part of this search
         : r.uncond ? ' · unconditionally stable over the measured range'
                    : ' · NOT unconditionally stable');
    // re-analyse first, then report: the analysis clears the note
    if (needsAnalysis) await runAdvAction('analyse');
    note.textContent = verdict;
    note.classList.toggle('adv-bad', !ok);
  } catch (e) {
    if (btn) btn.disabled = false;
    note.textContent = 'Failed: ' + e.message;
  }
}
