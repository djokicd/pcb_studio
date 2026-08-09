/* Current density viewer.

   Modes:
     'fd' — frequency-domain phasor: animates Re{J e^{j phi}} over one RF
            period. Arbitrary frequencies are supported by complex linear
            interpolation between the two nearest dumped frequencies.
     'td' — time-domain recording: plays back |J| frames captured during
            the simulation's dump window (like the raw port signals).

   The FDTD mesh is non-uniform, so fields are resampled onto the display
   raster with bilinear interpolation in *physical* coordinates (toggleable;
   off = nearest mesh node, showing the raw cells). Wheel zooms, drag pans,
   double-click resets. snapshot() renders the current state into an
   offscreen canvas (used for GIF export). */
'use strict';

/* sequential blue ramp, dark surface -> light (near-zero recedes into surface) */
const J_RAMP = ['#1a1a19', '#10305c', '#184f95', '#2a78d6', '#5598e7', '#9ec5f4', '#cde2fb'];

function rampColor(t) {
  t = Math.max(0, Math.min(1, t));
  const n = J_RAMP.length - 1;
  const i = Math.min(n - 1, Math.floor(t * n));
  const f = t * n - i;
  const hex = (c, k) => parseInt(c.slice(1 + k * 2, 3 + k * 2), 16);
  return [0, 1, 2].map(k => Math.round(hex(J_RAMP[i], k) + f * (hex(J_RAMP[i + 1], k) - hex(J_RAMP[i], k))));
}

class JView {
  constructor(canvas, infoEl, maxEl) {
    this.canvases = [];
    this.infoEl = infoEl;
    this.maxEl = maxEl;
    this.data = null;
    this.query = '';      // '?exc=N' when viewing a non-primary excitation
    this.logScale = false;  // dB color scale (40 dB below peak) vs sqrt
    this.phase = 0;       // degrees (fd mode)
    this.frame = 0;       // frame index (td mode)
    this.smooth = true;   // bilinear resampling; false = nearest mesh node
    this.playing = false;
    this._raf = null;
    this._rasters = new Map();    // "WxH" -> {ix, fx, iy, fy, buf, img}
    this._fdCache = new Map();    // "runId:layer:k" -> parsed dump
    this.view = { s: 1, x: 0, y: 0 };   // screen-space zoom/pan, shared
    this.overlay = null;
    this.addCanvas(canvas);
  }

  addCanvas(c) {
    this.canvases.push(c);
    this._bindZoom(c);
    this.render();
  }
  removeCanvas(c) { this.canvases = this.canvases.filter(x => x !== c); }

  _bindZoom(cv) {
    if (cv._jviewBound) return;
    cv._jviewBound = true;
    cv.style.cursor = 'grab';
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
      const ns = Math.min(20, Math.max(1, this.view.s * f));
      const k = ns / this.view.s;
      this.view.x = mx - (mx - this.view.x) * k;
      this.view.y = my - (my - this.view.y) * k;
      this.view.s = ns;
      if (this.view.s === 1) { this.view.x = this.view.y = 0; }
      this.render();
    }, { passive: false });
    cv.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      const start = { mx: e.clientX, my: e.clientY, vx: this.view.x, vy: this.view.y };
      cv.style.cursor = 'grabbing';
      const move = ev => {
        this.view.x = start.vx + (ev.clientX - start.mx);
        this.view.y = start.vy + (ev.clientY - start.my);
        this.render();
      };
      const up = () => {
        cv.style.cursor = 'grab';
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
      };
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    });
    cv.addEventListener('dblclick', () => {
      this.view = { s: 1, x: 0, y: 0 };
      this.render();
    });
  }

  async _fetchBuf(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error('failed to fetch dump');
    return res.arrayBuffer();
  }

  /* fetch + parse one FD dump (cached) */
  async _fetchFD(runId, layer, k) {
    const key = `${runId}${this.query}:${layer}:${k}`;
    if (this._fdCache.has(key)) return this._fdCache.get(key);
    const buf = await this._fetchBuf(`/api/results/${runId}/jdump/${encodeURIComponent(layer)}/${k}${this.query}`);
    const iv = new Int32Array(buf, 0, 2);
    const nx = iv[0], ny = iv[1];
    let off = 8;
    const f32 = n => { const a = new Float32Array(buf, off, n); off += n * 4; return a; };
    const d = { nx, ny, x: f32(nx), y: f32(ny),
      jxr: f32(nx * ny), jxi: f32(nx * ny), jyr: f32(nx * ny), jyi: f32(nx * ny) };
    if (this._fdCache.size > 50) this._fdCache.clear();
    this._fdCache.set(key, d);
    return d;
  }

  /* frequency-domain phasor at an arbitrary frequency: complex linear
     interpolation between the two nearest dumped frequencies.
     dumps: [{k, freq}] available for this layer, sorted by freq. */
  async loadFD(runId, layer, freqHz, dumps, overlay) {
    const sorted = [...dumps].sort((a, b) => a.freq - b.freq);
    freqHz = Math.max(sorted[0].freq, Math.min(sorted[sorted.length - 1].freq, freqHz));
    let lo = sorted[0], hi = sorted[sorted.length - 1];
    for (const d of sorted) { if (d.freq <= freqHz + 1) lo = d; }
    for (let i = sorted.length - 1; i >= 0; i--) { if (sorted[i].freq >= freqHz - 1) hi = sorted[i]; }
    const a = await this._fetchFD(runId, layer, lo.k);
    let d;
    let interp = false;
    if (hi.k === lo.k || hi.freq - lo.freq < 1) {
      d = a;
      freqHz = lo.freq;
    } else {
      const b = await this._fetchFD(runId, layer, hi.k);
      const w = (freqHz - lo.freq) / (hi.freq - lo.freq);
      const n = a.nx * a.ny;
      const mix = (u, v) => {
        const out = new Float32Array(n);
        for (let i = 0; i < n; i++) out[i] = u[i] * (1 - w) + v[i] * w;
        return out;
      };
      d = { nx: a.nx, ny: a.ny, x: a.x, y: a.y,
        jxr: mix(a.jxr, b.jxr), jxi: mix(a.jxi, b.jxi),
        jyr: mix(a.jyr, b.jyr), jyi: mix(a.jyi, b.jyi) };
      interp = w > 1e-6 && w < 1 - 1e-6;
    }
    // envelope max over sampled phases for a stable color scale
    let max = 0;
    for (let p = 0; p < 12; p++) {
      const c = Math.cos(p * Math.PI / 6), s = Math.sin(p * Math.PI / 6);
      for (let i = 0; i < d.nx * d.ny; i++) {
        const jx = d.jxr[i] * c - d.jxi[i] * s;
        const jy = d.jyr[i] * c - d.jyi[i] * s;
        const m = jx * jx + jy * jy;
        if (m > max) max = m;
      }
    }
    this.data = { mode: 'fd', ...d, max: Math.sqrt(max), freqHz, layer, interp };
    this._after(overlay);
  }

  /* kept for compatibility: exact dumped frequency */
  async load(runId, layer, k, freqHz, overlay) {
    return this.loadFD(runId, layer, freqHz, [{ k, freq: freqHz }], overlay);
  }

  /* time-domain |J| frame stack */
  async loadTD(runId, layer, overlay) {
    const buf = await this._fetchBuf(`/api/results/${runId}/jtdump/${encodeURIComponent(layer)}${this.query}`);
    const iv = new Int32Array(buf, 0, 3);
    const nx = iv[0], ny = iv[1], nf = iv[2];
    let off = 12;
    const f32 = n => { const a = new Float32Array(buf, off, n); off += n * 4; return a; };
    const x = f32(nx), y = f32(ny), t = f32(nf);
    const frames = [];
    let max = 0;
    for (let k = 0; k < nf; k++) {
      const fr = f32(nx * ny);
      frames.push(fr);
      for (let i = 0; i < fr.length; i++) if (fr[i] > max) max = fr[i];
    }
    this.data = { mode: 'td', nx, ny, x, y, t, frames, max: max || 1, layer };
    this.frame = 0;
    this._after(overlay);
  }

  _after(overlay) {
    this.overlay = overlay || null;
    this._mag = new Float32Array(this.data.nx * this.data.ny);
    this._rasters = new Map();
    if (this.maxEl) this.maxEl.textContent = `${this.data.max.toExponential(2)} A/m`;
    this.render();
  }

  /* per-pixel lookup tables mapping raster columns/rows to mesh intervals */
  _lut(W, H) {
    const { nx, ny, x, y } = this.data;
    const x0 = x[0], x1 = x[nx - 1], y0 = y[0], y1 = y[ny - 1];
    const ix = new Int32Array(W), fx = new Float32Array(W);
    let i = 0;
    for (let px = 0; px < W; px++) {
      const xm = x0 + (px + 0.5) / W * (x1 - x0);
      while (i < nx - 2 && x[i + 1] < xm) i++;
      ix[px] = i;
      fx[px] = Math.max(0, Math.min(1, (xm - x[i]) / (x[i + 1] - x[i])));
    }
    const iy = new Int32Array(H), fy = new Float32Array(H);
    // raster row 0 is the top of the image = max y, so scan mesh intervals descending
    let j = ny - 2;
    for (let py = 0; py < H; py++) {
      const ym = y1 - (py + 0.5) / H * (y1 - y0);
      while (j > 0 && y[j] > ym) j--;
      iy[py] = j;
      fy[py] = Math.max(0, Math.min(1, (ym - y[j]) / (y[j + 1] - y[j])));
    }
    return { ix, fx, iy, fy };
  }

  /* fill this._mag (sqrt-normalised 0..1) for the current phase/frame */
  _computeMag() {
    const d = this.data;
    const { nx, ny } = d;
    const mag = this._mag;
    if (d.mode === 'fd') {
      const c = Math.cos(this.phase * Math.PI / 180), s = Math.sin(this.phase * Math.PI / 180);
      const inv = 1 / (d.max || 1);
      // sqrt scale for visibility; log mode maps a 40 dB range below the
      // peak onto the ramp (reveals weak return currents)
      const log = this.logScale;
      for (let i = 0; i < nx * ny; i++) {
        const jx = d.jxr[i] * c - d.jxi[i] * s;
        const jy = d.jyr[i] * c - d.jyi[i] * s;
        const r = Math.sqrt(jx * jx + jy * jy) * inv;
        mag[i] = log ? (r > 1e-12 ? Math.max(0, 1 + Math.log10(r) / 2) : 0)
                     : Math.sqrt(r);
      }
    } else {
      const fr = d.frames[Math.min(this.frame, d.frames.length - 1)];
      const inv = 1 / d.max;
      const log = this.logScale;
      for (let i = 0; i < nx * ny; i++) {
        const r = fr[i] * inv;
        mag[i] = log ? (r > 1e-12 ? Math.max(0, 1 + Math.log10(r) / 2) : 0)
                     : Math.sqrt(r);
      }
    }
  }

  /* paint the current mag field into a cached raster of the given size */
  _rasterize(W, H) {
    const key = `${W}x${H}`;
    let st = this._rasters.get(key);
    if (!st) {
      st = this._lut(W, H);
      st.buf = document.createElement('canvas');
      st.buf.width = W; st.buf.height = H;
      st.img = st.buf.getContext('2d').createImageData(W, H);
      this._rasters.set(key, st);
    }
    const { nx } = this.data;
    const mag = this._mag;
    const { ix, fx, iy, fy, img } = st;
    const px = img.data;
    const smooth = this.smooth;
    let o = 0;
    for (let r = 0; r < H; r++) {
      const j = iy[r], fyy = fy[r], jn = j * nx, jn1 = (j + 1) * nx;
      for (let q = 0; q < W; q++) {
        const i = ix[q], fxx = fx[q];
        const t = smooth
          ? (mag[jn + i] * (1 - fxx) + mag[jn + i + 1] * fxx) * (1 - fyy)
          + (mag[jn1 + i] * (1 - fxx) + mag[jn1 + i + 1] * fxx) * fyy
          : mag[(fyy > 0.5 ? jn1 : jn) + i + (fxx > 0.5 ? 1 : 0)];
        const [R, G, B] = rampColor(t);
        px[o] = R; px[o + 1] = G; px[o + 2] = B; px[o + 3] = 255;
        o += 4;
      }
    }
    st.buf.getContext('2d').putImageData(img, 0, 0);
    return st.buf;
  }

  _drawOverlay(ctx, fit, ox, oy, ih, lineWidth) {
    if (!this.overlay) return;
    const { x, y } = this.data;
    ctx.strokeStyle = 'rgba(255,255,255,0.30)';
    ctx.lineWidth = lineWidth;
    for (const ol of this.overlay) {
      ctx.beginPath();
      ol.pts.forEach(([wx, wy], k) => {
        const X = ox + (wx - x[0]) * fit, Y = oy + ih - (wy - y[0]) * fit;
        k ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
      });
      ctx.closePath();
      ctx.stroke();
    }
  }

  render() {
    if (!this.data) return;
    this._computeMag();
    const d = this.data;
    const { nx, ny, x, y } = d;
    const xr = x[nx - 1] - x[0], yr = y[ny - 1] - y[0];
    for (const cv of this.canvases) {
      const wrap = cv.parentElement;
      const cw = (wrap && wrap.clientWidth) || cv.clientWidth || 400;
      if (cw < 10) continue;
      const ch = wrap && wrap.classList.contains('rsz') && wrap.clientHeight > 60
        ? wrap.clientHeight : Math.max(60, Math.round(cw * yr / xr));
      const fit = Math.min(cw / xr, ch / yr);
      const iw = Math.max(8, xr * fit), ih = Math.max(8, yr * fit);
      const ox = (cw - iw) / 2, oy = (ch - ih) / 2;
      const W = Math.min(560, Math.round(iw)), H = Math.max(40, Math.round(W * yr / xr));
      const buf = this._rasterize(W, H);

      const dpr = window.devicePixelRatio || 1;
      if (cv.width !== cw * dpr || cv.height !== ch * dpr) {
        cv.width = cw * dpr; cv.height = ch * dpr;
        cv.style.height = ch + 'px';
      }
      const ctx = cv.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = '#1a1a19';
      ctx.fillRect(0, 0, cw, ch);
      const v = this.view;
      ctx.translate(v.x, v.y);
      ctx.scale(v.s, v.s);
      ctx.imageSmoothingEnabled = this.smooth;
      ctx.drawImage(buf, ox, oy, iw, ih);
      this._drawOverlay(ctx, fit, ox, oy, ih, 1 / v.s);
    }
    if (this.infoEl) {
      if (d.mode === 'fd') {
        this.infoEl.textContent =
          `${(d.freqHz / 1e9).toFixed(3)} GHz${d.interp ? ' (interpolated)' : ''}` +
          ` · φ ${String(Math.round(this.phase)).padStart(3, '0')}°`;
      } else {
        const k = Math.min(this.frame, d.t.length - 1);
        this.infoEl.textContent =
          `t = ${(d.t[k] * 1e9).toFixed(3)} ns · frame ${k + 1}/${d.frames.length}`;
      }
    }
  }

  /* render the current phase/frame at full extent (no zoom) into a fresh
     canvas of the given pixel width; used for GIF export */
  snapshot(W) {
    if (!this.data) return null;
    this._computeMag();
    const { nx, ny, x, y } = this.data;
    const xr = x[nx - 1] - x[0], yr = y[ny - 1] - y[0];
    const H = Math.max(24, Math.round(W * yr / xr));
    const buf = this._rasterize(W, H);
    const out = document.createElement('canvas');
    out.width = W; out.height = H;
    const ctx = out.getContext('2d');
    ctx.drawImage(buf, 0, 0);
    this._drawOverlay(ctx, W / xr, 0, 0, H, 1);
    return out;
  }

  setPhase(deg) { this.phase = ((deg % 360) + 360) % 360; this.render(); }
  setFrame(k) {
    if (!this.data || this.data.mode !== 'td') return;
    this.frame = Math.max(0, Math.min(this.data.frames.length - 1, Math.round(k)));
    this.render();
  }

  play() {
    if (this.playing || !this.data) return;
    this.playing = true;
    let last = performance.now();
    let acc = 0;
    const step = now => {
      if (!this.playing) return;
      if (this.data.mode === 'fd') {
        this.phase = (this.phase + (now - last) * 0.09) % 360;   // ~4 s per period
        this.render();
        if (this.onFrame) this.onFrame('fd', this.phase);
      } else {
        acc += (now - last) * 0.02;   // ~20 frames/s
        if (acc >= 1) {
          this.frame = (this.frame + Math.floor(acc)) % this.data.frames.length;
          acc = 0;
          this.render();
          if (this.onFrame) this.onFrame('td', this.frame);
        }
      }
      last = now;
      this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }

  pause() {
    this.playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }
}
