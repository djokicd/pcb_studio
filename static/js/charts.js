/* S-parameter charts: magnitude (dB), Smith chart, polar plot.
   Dark-mode palette; every chart supports hover tooltips and can be
   re-instantiated on the pop-out modal canvas. */
'use strict';

const SERIES_COLORS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
const CH = {
  surface: '#1a1a19', grid: '#2c2c2a', axis: '#383835',
  muted: '#898781', ink: '#e8e7e0', crosshair: '#52514e',
};

class ChartBase {
  constructor(canvas, tip) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.tip = tip;
    this.data = null;
    this.xv = null;          // zoomed x-range in display units (xzoom charts)
    this.zs = 1;             // radial zoom (panzoom charts)
    this.panXY = [0, 0];
    this._onMove = e => {
      if (this._sel) {
        this._sel[1] = this.mouse(e)[0];
        this.draw();
        this._drawSel();
      } else if (this._pan) {
        this.panXY = [this._pan.p0[0] + e.clientX - this._pan.mx,
                      this._pan.p0[1] + e.clientY - this._pan.my];
        this.draw();
      } else {
        this.onMove(e);
      }
    };
    this._onLeave = () => this.clearHover();
    this._onDown = e => {
      if (e.button !== 0) return;
      if (this.panzoom) {
        this._pan = { mx: e.clientX, my: e.clientY, p0: [...this.panXY] };
      } else if (this.xzoom && this._xmap) {
        this._sel = [this.mouse(e)[0], null];
      }
    };
    this._onUp = () => {
      if (this._sel && this._sel[1] !== null && Math.abs(this._sel[1] - this._sel[0]) > 8) {
        const { lo, hi, l, r } = this._xmap;
        const xAt = px => lo + (Math.max(l, Math.min(r, px)) - l) / (r - l) * (hi - lo);
        const a = xAt(Math.min(...this._sel)), b = xAt(Math.max(...this._sel));
        if (b - a > 1e-9) this.xv = [a, b];
      }
      if (this._sel || this._pan) { this._sel = null; this._pan = null; this.draw(); }
    };
    this._onDbl = () => this.resetView();
    this._onWheel = e => {
      if (!this.data) return;
      e.preventDefault();
      const f = e.deltaY < 0 ? 0.78 : 1.28;
      if (this.panzoom) {
        const r = this.canvas.getBoundingClientRect();
        const q = [e.clientX - r.left, e.clientY - r.top];
        const k = Math.min(25, Math.max(0.4, this.zs / f)) / this.zs;
        this.zs *= k;
        // keep the point under the cursor fixed while zooming
        const base = [r.width / 2, r.height / 2];
        const c = [base[0] + this.panXY[0], base[1] + this.panXY[1]];
        this.panXY = [q[0] + (c[0] - q[0]) * k - base[0], q[1] + (c[1] - q[1]) * k - base[1]];
        this.draw();
      } else if (this.xzoom && this._xmap && this._xfull) {
        const { lo, hi, l, r } = this._xmap;
        const [x] = this.mouse(e);
        const cx = lo + (Math.max(l, Math.min(r, x)) - l) / (r - l) * (hi - lo);
        let nlo = cx - (cx - lo) * f, nhi = cx + (hi - cx) * f;
        nlo = Math.max(this._xfull[0], nlo);
        nhi = Math.min(this._xfull[1], nhi);
        this.xv = (nhi - nlo) >= (this._xfull[1] - this._xfull[0]) * 0.999 ? null : [nlo, nhi];
        this.draw();
      }
    };
    canvas.addEventListener('mousemove', this._onMove);
    canvas.addEventListener('mouseleave', this._onLeave);
    canvas.addEventListener('mousedown', this._onDown);
    window.addEventListener('mouseup', this._onUp);
    canvas.addEventListener('dblclick', this._onDbl);
    canvas.addEventListener('wheel', this._onWheel, { passive: false });
  }
  destroy() {
    this.canvas.removeEventListener('mousemove', this._onMove);
    this.canvas.removeEventListener('mouseleave', this._onLeave);
    this.canvas.removeEventListener('mousedown', this._onDown);
    window.removeEventListener('mouseup', this._onUp);
    this.canvas.removeEventListener('dblclick', this._onDbl);
    this.canvas.removeEventListener('wheel', this._onWheel);
  }
  resetView() {
    this.xv = null;
    this.zs = 1;
    this.panXY = [0, 0];
    this.draw();
  }
  _drawSel() {
    if (!this._sel || this._sel[1] === null || !this._m) return;
    const ctx = this.ctx;
    const a = Math.min(...this._sel), b = Math.max(...this._sel);
    ctx.fillStyle = 'rgba(57,135,229,0.15)';
    ctx.strokeStyle = 'rgba(57,135,229,0.6)';
    const top = this._m.t, bot = (this._h || this.canvas.clientHeight) - this._m.b;
    ctx.fillRect(a, top, b - a, bot - top);
    ctx.strokeRect(a, top, b - a, bot - top);
  }
  setData(data) { this.data = data; this.hover = null; if (this.tip) this.tip.hidden = true; this.draw(); }
  clearHover() { this.hover = null; if (this.tip) this.tip.hidden = true; this.draw(); }
  layout(aspect = 0.62) {
    const dpr = window.devicePixelRatio || 1;
    const wrap = this.canvas.parentElement;
    const w = wrap.clientWidth || this.canvas.clientWidth || 640;
    // user-resizable wraps dictate the height; otherwise derive from width
    const h = wrap.classList.contains('rsz') && wrap.clientHeight > 60
      ? wrap.clientHeight : Math.round(w * aspect);
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w, h };
  }
  mouse(e) {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }
  showTip(e, html) {
    if (!this.tip) return;
    this.tip.innerHTML = html;
    this.tip.hidden = false;
    const wrap = this.canvas.parentElement.getBoundingClientRect();
    let tx = e.clientX - wrap.left + 14;
    if (tx + this.tip.offsetWidth > wrap.width - 4) tx = e.clientX - wrap.left - this.tip.offsetWidth - 14;
    this.tip.style.left = tx + 'px';
    this.tip.style.top = Math.max(0, e.clientY - wrap.top - 24) + 'px';
  }
  ticks(lo, hi, n) {
    const span = hi - lo, raw = span / Math.max(1, n);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 5, 10].map(m => m * mag).find(s => span / s <= n + 1) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
    return out;
  }
}

/* data: {freq[], series: [{label, values(dB)}]} */
class MagChart extends ChartBase {
  draw() {
    if (!this.data) return;
    const { freq, series } = this.data;
    const { w, h } = this.layout();
    const m = { l: 44, r: 14, t: 10, b: 28 };
    const ctx = this.ctx;
    ctx.clearRect(0, 0, w, h);
    const fullLo = freq[0] / 1e9, fullHi = freq[freq.length - 1] / 1e9;
    this._xfull = [fullLo, fullHi];
    const fLo = this.xv ? this.xv[0] : fullLo;
    const fHi = this.xv ? this.xv[1] : fullHi;
    const visible = k => freq[k] / 1e9 >= fLo - 1e-12 && freq[k] / 1e9 <= fHi + 1e-12;
    let vLo = Infinity, vHi = -Infinity;
    for (const s of series) s.values.forEach((v, k) => {
      if (isFinite(v) && visible(k)) { vLo = Math.min(vLo, v); vHi = Math.max(vHi, v); }
    });
    if (!isFinite(vLo)) { vLo = -40; vHi = 0; }
    vLo = Math.floor((vLo - 2) / 5) * 5;
    vHi = Math.ceil((vHi + 2) / 5) * 5;
    const px = f => m.l + (f - fLo) / (fHi - fLo) * (w - m.l - m.r);
    const py = v => m.t + (vHi - v) / (vHi - vLo) * (h - m.t - m.b);
    ctx.font = '11px system-ui';
    ctx.lineWidth = 1;
    for (const v of this.ticks(vLo, vHi, 6)) {
      ctx.strokeStyle = CH.grid;
      ctx.beginPath(); ctx.moveTo(m.l, py(v)); ctx.lineTo(w - m.r, py(v)); ctx.stroke();
      ctx.fillStyle = CH.muted; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText(String(Math.round(v)), m.l - 6, py(v));
    }
    for (const f of this.ticks(fLo, fHi, 6)) {
      ctx.strokeStyle = CH.grid;
      ctx.beginPath(); ctx.moveTo(px(f), m.t); ctx.lineTo(px(f), h - m.b); ctx.stroke();
      ctx.fillStyle = CH.muted; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillText(String(+f.toFixed(3)), px(f), h - m.b + 6);
    }
    ctx.strokeStyle = CH.axis;
    ctx.strokeRect(m.l, m.t, w - m.l - m.r, h - m.t - m.b);
    ctx.fillStyle = CH.muted;
    ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
    ctx.fillText('GHz', w - m.r - 2, h - m.b - 3);
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText('dB', m.l + 4, m.t + 2);
    if (this.hover !== null) {
      const f = freq[this.hover] / 1e9;
      ctx.strokeStyle = CH.crosshair;
      ctx.beginPath(); ctx.moveTo(px(f), m.t); ctx.lineTo(px(f), h - m.b); ctx.stroke();
    }
    const clamp = v => Math.max(vLo, Math.min(vHi, v));
    const co = this.colorOffset || 0;
    ctx.save();
    ctx.beginPath();
    ctx.rect(m.l, m.t, w - m.l - m.r, h - m.t - m.b);
    ctx.clip();
    series.forEach((s, i) => {
      const color = SERIES_COLORS[(s.ci != null ? s.ci : i + co) % SERIES_COLORS.length];
      ctx.strokeStyle = color; ctx.lineWidth = 2;
      ctx.beginPath();
      s.values.forEach((v, k) => {
        const X = px(freq[k] / 1e9), Y = py(clamp(v));
        k ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
      });
      ctx.stroke();
      if (!this.xv && series.length > 1 && series.length <= 4) {
        ctx.fillStyle = color; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
        ctx.fillText(s.label, w - m.r - 4, py(clamp(s.values[s.values.length - 1])) - 3);
      }
      if (this.hover !== null && isFinite(s.values[this.hover])) {
        ctx.fillStyle = color; ctx.strokeStyle = CH.surface; ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(px(freq[this.hover] / 1e9), py(clamp(s.values[this.hover])), 4, 0, 7);
        ctx.fill(); ctx.stroke();
      }
    });
    ctx.restore();
    this._px = px; this._m = m; this._w = w; this._h = h;
    this._xmap = { lo: fLo, hi: fHi, l: m.l, r: w - m.r };
  }
  onMove(e) {
    if (!this.data || !this._m) return;
    const [x] = this.mouse(e);
    const { freq, series } = this.data;
    const m = this._m;
    if (x < m.l || x > this._w - m.r) { this.clearHover(); return; }
    const frac = (x - m.l) / (this._w - m.l - m.r);
    const { lo, hi } = this._xmap;
    const target = (lo + frac * (hi - lo)) * 1e9;
    let idx = -1, best = Infinity;
    freq.forEach((f, k) => {
      if (f / 1e9 < lo - 1e-12 || f / 1e9 > hi + 1e-12) return;
      const d = Math.abs(f - target);
      if (d < best) { best = d; idx = k; }
    });
    if (idx < 0) { this.clearHover(); return; }
    this.hover = idx;
    this.draw();
    const co = this.colorOffset || 0;
    const rows = series.map((s, i) =>
      `<div class="t-row"><span class="swatch" style="background:${SERIES_COLORS[(s.ci != null ? s.ci : i + co) % SERIES_COLORS.length]}"></span>` +
      `${s.label}<span class="t-val">${s.values[idx].toFixed(2)} dB</span></div>`).join('');
    this.showTip(e, `<div class="t-head">${(freq[idx] / 1e9).toFixed(3)} GHz</div>${rows}`);
  }
}

MagChart.prototype.xzoom = true;

/* data: {freq[], series: [{label, re[], im[], ci}], z0} — reflection traces */
class SmithChart extends ChartBase {
  draw() {
    if (!this.data) return;
    const { freq, series } = this.data;
    const { w, h } = this.layout(0.8);
    const ctx = this.ctx;
    ctx.clearRect(0, 0, w, h);
    const R = (Math.min(w, h) / 2 - 16) * this.zs;
    const cx = w / 2 + this.panXY[0], cy = h / 2 + this.panXY[1];
    const X = g => cx + g * R, Y = g => cy - g * R;

    ctx.save();
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.clip();
    ctx.lineWidth = 1;
    ctx.strokeStyle = CH.grid;
    for (const rn of [0.2, 0.5, 1, 2, 5]) {
      ctx.beginPath();
      ctx.arc(X(rn / (rn + 1)), cy, R / (rn + 1), 0, 7);
      ctx.stroke();
    }
    for (const xn of [0.2, 0.5, 1, 2, 5]) {
      for (const sgn of [1, -1]) {
        ctx.beginPath();
        ctx.arc(X(1), cy - sgn * R / xn, R / xn, 0, 7);
        ctx.stroke();
      }
    }
    ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy); ctx.stroke();
    ctx.restore();
    ctx.strokeStyle = CH.axis; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.stroke();
    ctx.fillStyle = CH.muted; ctx.font = '10px system-ui';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (const rn of [0, 0.2, 0.5, 1, 2, 5]) ctx.fillText(String(rn), X((rn - 1) / (rn + 1)) + 7, cy + 3);

    series.forEach((s, si) => {
      const color = SERIES_COLORS[(s.ci != null ? s.ci : si) % SERIES_COLORS.length];
      ctx.strokeStyle = color; ctx.lineWidth = 2;
      ctx.beginPath();
      for (let k = 0; k < freq.length; k++) {
        const x = X(s.re[k]), y = Y(s.im[k]);
        k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.stroke();
      // start / stop markers
      ctx.fillStyle = color;
      ctx.fillRect(X(s.re[0]) - 3, Y(s.im[0]) - 3, 6, 6);
      ctx.beginPath();
      ctx.arc(X(s.re[freq.length - 1]), Y(s.im[freq.length - 1]), 3.5, 0, 7);
      ctx.fill();
    });
    ctx.fillStyle = CH.muted; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
    const names = series.map(s => s.label).join(', ');
    ctx.fillText(`${names}  (■ ${(freq[0] / 1e9).toFixed(2)} GHz, ● ${(freq[freq.length - 1] / 1e9).toFixed(2)} GHz)`, 6, h - 4);
    if (this.hover) {
      const { si, k } = this.hover;
      const s = series[si];
      if (s) {
        ctx.fillStyle = '#fff'; ctx.strokeStyle = CH.surface; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(X(s.re[k]), Y(s.im[k]), 4.5, 0, 7);
        ctx.fill(); ctx.stroke();
      }
    }
    this._geo = { X, Y };
  }
  onMove(e) {
    if (!this.data || !this._geo) return;
    const [mx, my] = this.mouse(e);
    const { freq, series, z0 } = this.data;
    let hit = null, best = 14 * 14;
    series.forEach((s, si) => {
      for (let k = 0; k < freq.length; k++) {
        const dx = this._geo.X(s.re[k]) - mx, dy = this._geo.Y(s.im[k]) - my;
        const d = dx * dx + dy * dy;
        if (d < best) { best = d; hit = { si, k }; }
      }
    });
    if (!hit) { this.clearHover(); return; }
    this.hover = hit;
    this.draw();
    const s = series[hit.si];
    const g = { re: s.re[hit.k], im: s.im[hit.k] };
    const mag = Math.hypot(g.re, g.im);
    const ang = Math.atan2(g.im, g.re) * 180 / Math.PI;
    // Z = z0 (1+G)/(1-G)
    const d = (1 - g.re) ** 2 + g.im ** 2;
    const zr = z0 * (1 - g.re * g.re - g.im * g.im) / d;
    const zi = z0 * 2 * g.im / d;
    const color = SERIES_COLORS[(s.ci != null ? s.ci : hit.si) % SERIES_COLORS.length];
    this.showTip(e,
      `<div class="t-head">${(freq[hit.k] / 1e9).toFixed(3)} GHz</div>` +
      `<div class="t-row"><span class="swatch" style="background:${color}"></span>${s.label}</div>` +
      `<div class="t-row">&Gamma; = ${mag.toFixed(3)} &ang; ${ang.toFixed(1)}&deg;</div>` +
      `<div class="t-row">Z = ${zr.toFixed(1)} ${zi >= 0 ? '+' : '−'} j${Math.abs(zi).toFixed(1)} &Omega;</div>`);
  }
}

SmithChart.prototype.panzoom = true;

/* data: {freq[], series: [{label, re[], im[]}]} — polar |S| dB vs phase */
class PolarChart extends ChartBase {
  draw() {
    if (!this.data) return;
    const { freq, series } = this.data;
    const { w, h } = this.layout(0.8);
    const ctx = this.ctx;
    ctx.clearRect(0, 0, w, h);
    const R = (Math.min(w, h) / 2 - 24) * this.zs;
    const cx = w / 2 + this.panXY[0], cy = h / 2 + this.panXY[1];
    let dbMin = Infinity;
    const dbOf = (s, k) => 20 * Math.log10(Math.max(Math.hypot(s.re[k], s.im[k]), 1e-12));
    for (const s of series) for (let k = 0; k < freq.length; k++) dbMin = Math.min(dbMin, dbOf(s, k));
    dbMin = Math.max(-60, Math.floor(dbMin / 10) * 10);
    const rOf = db => R * Math.max(0, (db - dbMin) / (0 - dbMin));
    ctx.lineWidth = 1;
    ctx.font = '10px system-ui';
    for (let db = dbMin; db <= 0; db += 10) {
      ctx.strokeStyle = db === 0 ? CH.axis : CH.grid;
      ctx.beginPath(); ctx.arc(cx, cy, rOf(db), 0, 7); ctx.stroke();
      ctx.fillStyle = CH.muted; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
      if (db > dbMin) ctx.fillText(db + '', cx + 3, cy - rOf(db) - 1);
    }
    for (let a = 0; a < 360; a += 30) {
      const rad = a * Math.PI / 180;
      ctx.strokeStyle = CH.grid;
      ctx.beginPath(); ctx.moveTo(cx, cy);
      ctx.lineTo(cx + R * Math.cos(rad), cy - R * Math.sin(rad)); ctx.stroke();
      ctx.fillStyle = CH.muted; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(a + '°', cx + (R + 12) * Math.cos(rad), cy - (R + 12) * Math.sin(rad));
    }
    const pt = (s, k) => {
      const db = dbOf(s, k), ph = Math.atan2(s.im[k], s.re[k]);
      return [cx + rOf(db) * Math.cos(ph), cy - rOf(db) * Math.sin(ph)];
    };
    series.forEach((s, i) => {
      const color = SERIES_COLORS[(s.ci != null ? s.ci : i + 1) % SERIES_COLORS.length];
      ctx.strokeStyle = color; ctx.lineWidth = 2;
      ctx.beginPath();
      for (let k = 0; k < freq.length; k++) {
        const [x, y] = pt(s, k);
        k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.stroke();
      const [x0, y0] = pt(s, 0);
      ctx.fillStyle = color;
      ctx.fillRect(x0 - 3, y0 - 3, 6, 6);
      const [x1, y1] = pt(s, freq.length - 1);
      ctx.beginPath(); ctx.arc(x1, y1, 3.5, 0, 7); ctx.fill();
    });
    ctx.fillStyle = CH.muted; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
    ctx.fillText(`radial: |S| dB (${dbMin}..0)  ■ start ● stop`, 6, h - 4);
    if (this.hover) {
      const { si, k } = this.hover;
      const [x, y] = pt(series[si], k);
      ctx.fillStyle = '#fff'; ctx.strokeStyle = CH.surface; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7); ctx.fill(); ctx.stroke();
    }
    this._pt = pt;
  }
  onMove(e) {
    if (!this.data || !this._pt) return;
    const [mx, my] = this.mouse(e);
    const { freq, series } = this.data;
    let hit = null, best = 14 * 14;
    series.forEach((s, si) => {
      for (let k = 0; k < freq.length; k++) {
        const [x, y] = this._pt(s, k);
        const d = (x - mx) ** 2 + (y - my) ** 2;
        if (d < best) { best = d; hit = { si, k }; }
      }
    });
    if (!hit) { this.clearHover(); return; }
    this.hover = hit;
    this.draw();
    const s = series[hit.si];
    const db = 20 * Math.log10(Math.max(Math.hypot(s.re[hit.k], s.im[hit.k]), 1e-12));
    const ph = Math.atan2(s.im[hit.k], s.re[hit.k]) * 180 / Math.PI;
    this.showTip(e,
      `<div class="t-head">${(freq[hit.k] / 1e9).toFixed(3)} GHz</div>` +
      `<div class="t-row"><span class="swatch" style="background:${SERIES_COLORS[(series[hit.si].ci != null ? series[hit.si].ci : hit.si + 1) % SERIES_COLORS.length]}"></span>` +
      `${s.label}<span class="t-val">${db.toFixed(2)} dB &ang; ${ph.toFixed(1)}&deg;</span></div>`);
  }
}

PolarChart.prototype.panzoom = true;

/* data: {t[] (s), series: [{label, values[]}], unit: 'V'|'A'} — raw port
   signals in the time domain */
class TimeChart extends ChartBase {
  draw() {
    if (!this.data) return;
    const { t, series, unit } = this.data;
    const { w, h } = this.layout();
    const m = { l: 52, r: 14, t: 10, b: 28 };
    const ctx = this.ctx;
    ctx.clearRect(0, 0, w, h);
    const fullLo = t[0] * 1e9, fullHi = t[t.length - 1] * 1e9;   // ns
    this._xfull = [fullLo, fullHi];
    const tLo = this.xv ? this.xv[0] : fullLo;
    const tHi = this.xv ? this.xv[1] : fullHi;
    const visible = k => t[k] * 1e9 >= tLo - 1e-12 && t[k] * 1e9 <= tHi + 1e-12;
    let vLo = Infinity, vHi = -Infinity;
    for (const s of series) s.values.forEach((v, k) => {
      if (isFinite(v) && visible(k)) { vLo = Math.min(vLo, v); vHi = Math.max(vHi, v); }
    });
    if (!isFinite(vLo)) { vLo = -1; vHi = 1; }
    const pad = (vHi - vLo) * 0.08 || 1;
    vLo -= pad; vHi += pad;
    const px = x => m.l + (x - tLo) / (tHi - tLo) * (w - m.l - m.r);
    const py = v => m.t + (vHi - v) / (vHi - vLo) * (h - m.t - m.b);
    const fmtV = v => Math.abs(v) >= 1e-2 || v === 0 ? +v.toPrecision(3) : v.toExponential(1);
    ctx.font = '11px system-ui';
    ctx.lineWidth = 1;
    for (const v of this.ticks(vLo, vHi, 5)) {
      ctx.strokeStyle = CH.grid;
      ctx.beginPath(); ctx.moveTo(m.l, py(v)); ctx.lineTo(w - m.r, py(v)); ctx.stroke();
      ctx.fillStyle = CH.muted; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText(String(fmtV(v)), m.l - 6, py(v));
    }
    for (const x of this.ticks(tLo, tHi, 7)) {
      ctx.strokeStyle = CH.grid;
      ctx.beginPath(); ctx.moveTo(px(x), m.t); ctx.lineTo(px(x), h - m.b); ctx.stroke();
      ctx.fillStyle = CH.muted; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillText(String(+x.toFixed(2)), px(x), h - m.b + 6);
    }
    // zero line
    if (vLo < 0 && vHi > 0) {
      ctx.strokeStyle = CH.axis;
      ctx.beginPath(); ctx.moveTo(m.l, py(0)); ctx.lineTo(w - m.r, py(0)); ctx.stroke();
    }
    ctx.strokeStyle = CH.axis;
    ctx.strokeRect(m.l, m.t, w - m.l - m.r, h - m.t - m.b);
    ctx.fillStyle = CH.muted;
    ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
    ctx.fillText('ns', w - m.r - 2, h - m.b - 3);
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText(unit, m.l + 4, m.t + 2);
    if (this.hover !== null) {
      ctx.strokeStyle = CH.crosshair;
      ctx.beginPath(); ctx.moveTo(px(t[this.hover] * 1e9), m.t);
      ctx.lineTo(px(t[this.hover] * 1e9), h - m.b); ctx.stroke();
    }
    ctx.save();
    ctx.beginPath();
    ctx.rect(m.l, m.t, w - m.l - m.r, h - m.t - m.b);
    ctx.clip();
    series.forEach((s, i) => {
      const color = SERIES_COLORS[(s.ci != null ? s.ci : i) % SERIES_COLORS.length];
      ctx.strokeStyle = color; ctx.lineWidth = 1.6;
      ctx.beginPath();
      let started = false;
      s.values.forEach((v, k) => {
        if (!isFinite(v)) return;
        const X = px(t[k] * 1e9), Y = py(Math.max(vLo, Math.min(vHi, v)));
        started ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
        started = true;
      });
      ctx.stroke();
      if (this.hover !== null && isFinite(s.values[this.hover])) {
        ctx.fillStyle = color; ctx.strokeStyle = CH.surface; ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(px(t[this.hover] * 1e9), py(Math.max(vLo, Math.min(vHi, s.values[this.hover]))), 4, 0, 7);
        ctx.fill(); ctx.stroke();
      }
    });
    ctx.restore();
    this._px = px; this._m = m; this._w = w; this._h = h;
    this._xmap = { lo: tLo, hi: tHi, l: m.l, r: w - m.r };
  }
  onMove(e) {
    if (!this.data || !this._m) return;
    const [x] = this.mouse(e);
    const { t, series, unit } = this.data;
    const m = this._m;
    if (x < m.l || x > this._w - m.r) { this.clearHover(); return; }
    const frac = (x - m.l) / (this._w - m.l - m.r);
    const { lo, hi } = this._xmap;
    const target = (lo + frac * (hi - lo)) * 1e-9;
    let idx = -1, best = Infinity;
    t.forEach((tv, k) => {
      if (tv * 1e9 < lo - 1e-12 || tv * 1e9 > hi + 1e-12) return;
      const d = Math.abs(tv - target);
      if (d < best) { best = d; idx = k; }
    });
    if (idx < 0) { this.clearHover(); return; }
    this.hover = idx;
    this.draw();
    const rows = series.map((s, i) => {
      const val = s.raw
        ? (s.raw[idx] != null ? `${s.raw[idx].toExponential(3)} ${s.rawUnit || 'A'}` : '—')
        : (s.values[idx] != null ? `${s.values[idx].toExponential(3)} ${unit}` : '—');
      return `<div class="t-row"><span class="swatch" style="background:${SERIES_COLORS[(s.ci != null ? s.ci : i) % SERIES_COLORS.length]}"></span>` +
        `${s.label}<span class="t-val">${val}</span></div>`;
    }).join('');
    this.showTip(e, `<div class="t-head">${(t[idx] * 1e9).toFixed(3)} ns</div>${rows}`);
  }
}

/* ---- pop-out modal ---- */
TimeChart.prototype.xzoom = true;

const Modal = {
  active: null,
  open(title, build) {
    const modal = document.getElementById('modal');
    document.getElementById('modalTitle').textContent = title;
    modal.hidden = false;
    const canvas = document.getElementById('modalCanvas');
    const tip = document.getElementById('modalTip');
    this.close();
    this.active = build(canvas, tip);   // returns {destroy()} or chart instance
  },
  close() {
    if (this.active) {
      if (this.active.destroy) this.active.destroy();
      this.active = null;
    }
  },
  init() {
    const modal = document.getElementById('modal');
    document.getElementById('modalClose').addEventListener('click', () => { modal.hidden = true; this.close(); });
    modal.addEventListener('click', e => {
      if (e.target === modal) { modal.hidden = true; this.close(); }
    });
    window.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !modal.hidden) { modal.hidden = true; this.close(); }
    });
  },
};
