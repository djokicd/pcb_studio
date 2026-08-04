/* Minimal GIF89a encoder (256-colour global palette, infinite loop).
   LZW packing follows the standard GIF flavour (variable code size,
   LSB-first bit order, 255-byte sub-blocks). No dependencies. */
'use strict';

function _lzwEncode(minCodeSize, indices, out) {
  let bitBuf = 0, bitCnt = 0;
  const bytes = [];
  const emit = code => {
    bitBuf |= code << bitCnt;
    bitCnt += curSize;
    while (bitCnt >= 8) {
      bytes.push(bitBuf & 255);
      bitBuf >>= 8;
      bitCnt -= 8;
    }
  };
  const CLEAR = 1 << minCodeSize;
  const EOI = CLEAR + 1;
  let curSize = minCodeSize + 1;
  let nextCode = EOI + 1;
  let table = new Map();
  emit(CLEAR);
  let prev = indices[0];
  for (let i = 1; i < indices.length; i++) {
    const k = indices[i];
    const key = (prev << 8) | k;
    const found = table.get(key);
    if (found !== undefined) {
      prev = found;
      continue;
    }
    emit(prev);
    if (nextCode === 4096) {
      emit(CLEAR);
      nextCode = EOI + 1;
      curSize = minCodeSize + 1;
      table = new Map();
    } else {
      if (nextCode >= (1 << curSize)) curSize++;
      table.set(key, nextCode++);
    }
    prev = k;
  }
  emit(prev);
  emit(EOI);
  if (bitCnt > 0) bytes.push(bitBuf & 255);
  // pack into <=255-byte sub-blocks
  for (let i = 0; i < bytes.length; i += 255) {
    const n = Math.min(255, bytes.length - i);
    out.push(n);
    for (let j = 0; j < n; j++) out.push(bytes[i + j]);
  }
  out.push(0);   // block terminator
}

/**
 * frames: array of Uint8Array palette indices (width*height each)
 * palette: array of 256 [r,g,b]
 * delayCs: inter-frame delay in 1/100 s
 * Returns a Uint8Array with the complete GIF file.
 */
function encodeGif({ width, height, palette, frames, delayCs = 6 }) {
  const out = [];
  const u16 = v => { out.push(v & 255, (v >> 8) & 255); };
  const str = s => { for (const ch of s) out.push(ch.charCodeAt(0)); };

  str('GIF89a');
  u16(width); u16(height);
  out.push(0xF7, 0, 0);            // GCT present, 256 entries, 8-bit colour
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = palette[i] || [0, 0, 0];
    out.push(r, g, b);
  }
  // NETSCAPE looping extension (loop forever)
  out.push(0x21, 0xFF, 0x0B);
  str('NETSCAPE2.0');
  out.push(3, 1); u16(0); out.push(0);

  for (const frame of frames) {
    out.push(0x21, 0xF9, 4, 0);    // graphic control: no disposal, no transparency
    u16(delayCs);
    out.push(0, 0);
    out.push(0x2C);                // image descriptor
    u16(0); u16(0); u16(width); u16(height);
    out.push(0);                   // no local colour table
    out.push(8);                   // LZW min code size
    _lzwEncode(8, frame, out);
  }
  out.push(0x3B);                  // trailer
  return new Uint8Array(out);
}

/* Fixed palette for current-density frames: 250 ramp steps + overlay tones.
   Returns {palette, quantize(imageData) -> Uint8Array of indices}. */
function buildJGifPalette() {
  const palette = [];
  for (let i = 0; i < 250; i++) palette.push(rampColor(i / 249));
  palette.push([255, 255, 255], [220, 220, 218], [180, 180, 178],
    [140, 140, 138], [100, 100, 98], [26, 26, 25]);
  const cache = new Map();
  const nearest = (r, g, b) => {
    const key = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3);
    let idx = cache.get(key);
    if (idx !== undefined) return idx;
    let best = Infinity;
    idx = 0;
    for (let i = 0; i < palette.length; i++) {
      const [pr, pg, pb] = palette[i];
      const d = (pr - r) * (pr - r) + (pg - g) * (pg - g) + (pb - b) * (pb - b);
      if (d < best) { best = d; idx = i; }
    }
    cache.set(key, idx);
    return idx;
  };
  const quantize = img => {
    const n = img.width * img.height;
    const px = img.data;
    const out = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      out[i] = nearest(px[i * 4], px[i * 4 + 1], px[i * 4 + 2]);
    }
    return out;
  };
  return { palette, quantize };
}
