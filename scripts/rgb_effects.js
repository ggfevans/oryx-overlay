// SPDX-License-Identifier: GPL-2.0-or-later
//
// rgb_effects.js: QMK RGB Matrix effects, ported to JavaScript for previews.
//
// A line-by-line port of quantum/rgb_matrix/animations/*.h and the effect
// runners from QMK Firmware (as shipped in ZSA's fork), plus the lib8tion
// helpers they use. Copyright the QMK contributors, GPL-2.0-or-later. Because
// this file derives from that code it carries the same licence; the rest of
// oryx-overlay is MIT.
//
// Integer behaviour is emulated exactly (uint8/int8/uint16 wrap-around, C
// truncating division), so a frame matches the keyboard for the same inputs.
// tests/test_rgb_effects.py checks that against the C source.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.OryxRGB = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---- integer helpers --------------------------------------------------
  const u8 = (v) => v & 0xff;
  const u16 = (v) => v & 0xffff;
  const i8 = (v) => ((v & 0xff) ^ 0x80) - 0x80;
  const i16 = (v) => ((v & 0xffff) ^ 0x8000) - 0x8000;
  const div = (a, b) => Math.trunc(a / b); // C integer division

  // ---- lib8tion ---------------------------------------------------------
  const scale8 = (i, s) => (u8(i) * u8(s)) >> 8;
  const scale16by8 = (i, s) => div(u16(i) * u8(s), 256);
  const qadd8 = (a, b) => Math.min(255, u8(a) + u8(b));
  const qsub8 = (a, b) => Math.max(0, u8(a) - u8(b));
  const abs8 = (v) => { const x = i8(v); return x < 0 ? i8(-x) : x; }; // int8 in, int8 out
  const B_M16 = [0, 49, 49, 41, 90, 27, 117, 10];
  function sin8(theta) {
    theta = u8(theta);
    let offset = theta;
    if (theta & 0x40) offset = u8(255 - offset);
    offset &= 0x3f;
    let secoffset = offset & 0x0f;
    if (theta & 0x40) secoffset++;
    const s2 = (offset >> 4) * 2;
    const b = B_M16[s2], m16 = B_M16[s2 + 1];
    const mx = (m16 * secoffset) >> 4;
    let y = i8(mx + b);
    if (theta & 0x80) y = i8(-y);
    return u8(y + 128);
  }
  const cos8 = (theta) => sin8(u8(theta) + 64);
  function sqrt16(x) {
    x = u16(x);
    if (x <= 1) return x;
    let low = 1, hi = x > 7904 ? 255 : (x >> 5) + 8, mid;
    do {
      mid = (low + hi) >> 1;
      if (u16(mid * mid) > x) hi = u8(mid - 1);
      else { if (mid === 255) return 255; low = u8(mid + 1); }
    } while (hi >= low);
    return u8(low - 1);
  }
  function atan2_8(dy, dx) {
    dy = i16(dy); dx = i16(dx);
    if (dy === 0) return dx >= 0 ? 0 : 128;
    const absY = dy > 0 ? dy : -dy;
    let a;
    if (dx >= 0) a = i8(32 - div(32 * (dx - absY), dx + absY));
    else a = i8(96 - div(32 * (dx + absY), absY - dx));
    return dy < 0 ? u8(-a) : u8(a);
  }

  // quantum/color.c hsv_to_rgb_impl (no CIE curve)
  function hsv2rgb(h, s, v) {
    h = u8(h); s = u8(s); v = u8(v);
    if (s === 0) return [v, v, v];
    const region = div(h * 6, 255);
    const rem = u8((h * 2 - region * 85) * 3);
    const p = u8((v * (255 - s)) >> 8);
    const q = u8((v * (255 - ((s * rem) >> 8))) >> 8);
    const t = u8((v * (255 - ((s * (255 - rem)) >> 8))) >> 8);
    switch (region) {
      case 6: case 0: return [v, t, p];
      case 1: return [q, v, p];
      case 2: return [p, v, t];
      case 3: return [p, q, v];
      case 4: return [t, p, v];
      default: return [v, p, q];
    }
  }

  // ---- effect maths (one function per QMK *_math) -------------------------
  // i-runner:     (hsv, i, time, E)            time = u8(scale16by8(timer, qadd8(speed/4, 1)))
  // dxdy-runner:  (hsv, dx, dy, time, E)       time = u8(scale16by8(timer, speed/2))
  // dist-runner:  (hsv, dx, dy, dist, time, E)
  // sincos-runner:(hsv, sinP, cosP, i, time, E) note: QMK passes (cos, sin) into (sin, cos)
  const M = {
    breathing: (c, i, t) => [c[0], c[1], scale8(u8(abs8(sin8(div(t, 2)) - 128) * 2), c[2])],
    band_sat: (c, i, t, E) => { const s = c[1] - Math.abs(scale8(E.pt[i].x, 228) + 28 - t) * 8; return [c[0], scale8(s < 0 ? 0 : s, c[1]), c[2]]; },
    band_val: (c, i, t, E) => { const v = c[2] - Math.abs(scale8(E.pt[i].x, 228) + 28 - t) * 8; return [c[0], c[1], scale8(v < 0 ? 0 : v, c[2])]; },
    cycle_all: (c, i, t) => [t, c[1], c[2]],
    cycle_left_right: (c, i, t, E) => [u8(E.pt[i].x - t), c[1], c[2]],
    cycle_up_down: (c, i, t, E) => [u8(E.pt[i].y - t), c[1], c[2]],
    rainbow_moving_chevron: (c, i, t, E) => [u8(c[0] + abs8(E.pt[i].y - E.cy) + (E.pt[i].x - t)), c[1], c[2]],
    hue_breathing: (c, i, t) => [u8(c[0] + scale8(u8(abs8(sin8(div(t, 2)) - 128) * 2), 12)), c[1], c[2]],
    hue_pendulum: (c, i, t, E) => [u8(c[0] + scale8(u8(abs8(sin8(t) + E.pt[i].x - 128) * 2), 12)), c[1], c[2]],
    hue_wave: (c, i, t, E) => [u8(c[0] + scale8(u8(abs8(E.pt[i].x - t)), 24)), c[1], c[2]],
    riverflow: (c, i, t, E) => { const tt = u8(scale16by8(u16(E.timer + i * 315), div(E.speed, 8))); return [c[0], c[1], scale8(u8(abs8(sin8(tt) - 128) * 2), c[2])]; },
    starlight_smooth: (c, i, t, E) => {
      if (E.st.phase[i] === 0) E.st.phase[i] = Math.floor(Math.random() * 0x7fffffff) % 255;
      return [c[0], c[1], scale8(u8(abs8(sin8(div(t + E.st.phase[i], 2)) - 128) * 2), c[2])];
    },
    // dx/dy
    band_pinwheel_sat: (c, dx, dy, t) => [c[0], scale8(u8(c[1] - t - atan2_8(dy, dx) * 3), c[1]), c[2]],
    band_pinwheel_val: (c, dx, dy, t) => [c[0], c[1], scale8(u8(c[2] - t - atan2_8(dy, dx) * 3), c[2])],
    cycle_out_in_dual: (c, dx, dy, t, E) => { dx = i16(div(E.cx, 2) - abs8(dx)); const d = sqrt16(dx * dx + dy * dy); return [u8(3 * d + t), c[1], c[2]]; },
    cycle_pinwheel: (c, dx, dy, t) => [u8(atan2_8(dy, dx) + t), c[1], c[2]],
    // dx/dy/dist
    band_spiral_sat: (c, dx, dy, d, t) => [c[0], scale8(u8(c[1] + d - t - atan2_8(dy, dx)), c[1]), c[2]],
    band_spiral_val: (c, dx, dy, d, t) => [c[0], c[1], scale8(u8(c[2] + d - t - atan2_8(dy, dx)), c[2])],
    cycle_out_in: (c, dx, dy, d, t) => [u8(div(3 * d, 2) + t), c[1], c[2]],
    cycle_spiral: (c, dx, dy, d, t) => [u8(d - t - atan2_8(dy, dx)), c[1], c[2]],
    // sin/cos
    dual_beacon: (c, sn, cs, i, t, E) => [u8(c[0] + div((E.pt[i].y - E.cy) * cs + (E.pt[i].x - E.cx) * sn, 128)), c[1], c[2]],
    rainbow_beacon: (c, sn, cs, i, t, E) => [u8(c[0] + div((E.pt[i].y - E.cy) * 2 * cs + (E.pt[i].x - E.cx) * 2 * sn, 128)), c[1], c[2]],
    rainbow_pinwheels: (c, sn, cs, i, t, E) => [u8(c[0] + div((E.pt[i].y - E.cy) * 3 * cs + (56 - abs8(E.pt[i].x - E.cx)) * 3 * sn, 128)), c[1], c[2]],
    // reactive (offset)
    solid_reactive_simple: (c, off) => [c[0], c[1], scale8(u8(257 - off), c[2])],
    solid_reactive: (c, off) => [u8(c[0] + scale8(u8(255 - off), 64)), c[1], c[2]],
    // reactive splash (dx, dy, dist, tick)
    wide: (c, dx, dy, d, tk) => { let e = u16(tk + d * 5); if (e > 255) e = 255; return [c[0], c[1], qadd8(c[2], 255 - e)]; },
    cross: (c, dx, dy, d, tk) => {
      let e = u16(tk + d);
      dx = Math.abs(dx); dy = Math.abs(dy);
      dx = dx * 16 > 255 ? 255 : dx * 16;
      dy = dy * 16 > 255 ? 255 : dy * 16;
      e = u16(e + (dx > dy ? dy : dx));
      if (e > 255) e = 255;
      return [c[0], c[1], qadd8(c[2], 255 - e)];
    },
    nexus: (c, dx, dy, d, tk, E) => {
      let e = u16(tk - d);
      if (e > 255) e = 255;
      if (d > 72) e = 255;
      if ((dx > 8 || dx < -8) && (dy > 8 || dy < -8)) e = 255;
      return [u8(E.hsv[0] + div(dy, 4)), c[1], qadd8(c[2], 255 - e)];
    },
    splash: (c, dx, dy, d, tk) => { let e = u16(tk - d); if (e > 255) e = 255; return [u8(c[0] + e), c[1], qadd8(c[2], 255 - e)]; },
    solid_splash: (c, dx, dy, d, tk) => { let e = u16(tk - d); if (e > 255) e = 255; return [c[0], c[1], qadd8(c[2], 255 - e)]; },
  };

  // ---- runners ------------------------------------------------------------
  function runI(E, fn) {
    const t = u8(scale16by8(E.timer, qadd8(div(E.speed, 4), 1)));
    for (let i = 0; i < E.n; i++) E.set(i, hsv2rgb(...fn(E.hsv, i, t, E)));
  }
  function runDxDy(E, fn) {
    const t = u8(scale16by8(E.timer, div(E.speed, 2)));
    for (let i = 0; i < E.n; i++) E.set(i, hsv2rgb(...fn(E.hsv, i16(E.pt[i].x - E.cx), i16(E.pt[i].y - E.cy), t, E)));
  }
  function runDist(E, fn) {
    const t = u8(scale16by8(E.timer, div(E.speed, 2)));
    for (let i = 0; i < E.n; i++) {
      const dx = i16(E.pt[i].x - E.cx), dy = i16(E.pt[i].y - E.cy);
      E.set(i, hsv2rgb(...fn(E.hsv, dx, dy, sqrt16(dx * dx + dy * dy), t, E)));
    }
  }
  function runSinCos(E, fn) {
    const t = u16(scale16by8(E.timer, div(E.speed, 4)));
    const cosV = i8(cos8(t) - 128), sinV = i8(sin8(t) - 128);
    for (let i = 0; i < E.n; i++) E.set(i, hsv2rgb(...fn(E.hsv, cosV, sinV, i, u8(t), E)));
  }
  function runReactive(E, fn) {
    const maxTick = div(65535, qadd8(E.speed, 1));
    const h = E.hits;
    for (let i = 0; i < E.n; i++) {
      let tick = maxTick;
      for (let j = h.count - 1; j >= 0; j--) {
        if (h.index[j] === i && h.tick[j] < tick) { tick = h.tick[j]; break; }
      }
      const off = u16(scale16by8(tick, qadd8(E.speed, 1)));
      E.set(i, hsv2rgb(...fn(E.hsv, off, E)));
    }
  }
  function runSplash(E, start, fn) {
    const h = E.hits;
    for (let i = 0; i < E.n; i++) {
      let c = [E.hsv[0], E.hsv[1], 0];
      for (let j = start; j < h.count; j++) {
        const dx = i16(E.pt[i].x - h.x[j]), dy = i16(E.pt[i].y - h.y[j]);
        const d = sqrt16(dx * dx + dy * dy);
        const tk = u16(scale16by8(h.tick[j], qadd8(E.speed, 1)));
        c = fn(c, dx, dy, d, tk, E);
      }
      c[2] = scale8(c[2], E.hsv[2]);
      E.set(i, hsv2rgb(...c));
    }
  }
  const last = (E) => qsub8(E.hits.count, 1);

  const rand8 = () => (Math.random() * 256) | 0;
  const rand8max = (n) => (rand8() * n) >> 8;
  const rand8mm = (a, b) => a + rand8max(b - a);

  // ---- effect table: key = info.json rgb_matrix.animations name ------------
  // Order is QMK's rgb_matrix_effects.inc order, i.e. the RGB Mode cycle order.
  const EFFECTS = [
    ["solid_color", "Solid Color", {}, (E) => { const c = hsv2rgb(...E.hsv); for (let i = 0; i < E.n; i++) E.set(i, c); }],
    ["alphas_mods", "Alphas / Mods", {}, (E) => {
      const a = hsv2rgb(...E.hsv), b = hsv2rgb(u8(E.hsv[0] + E.speed), E.hsv[1], E.hsv[2]);
      for (let i = 0; i < E.n; i++) E.set(i, E.flags[i] & 0x01 ? b : a);
    }],
    ["gradient_up_down", "Gradient Up/Down", {}, (E) => {
      const sc = scale8(64, E.speed);
      for (let i = 0; i < E.n; i++) E.set(i, hsv2rgb(u8(E.hsv[0] + sc * (E.pt[i].y >> 4)), E.hsv[1], E.hsv[2]));
    }],
    ["gradient_left_right", "Gradient Left/Right", {}, (E) => {
      const sc = scale8(64, E.speed);
      for (let i = 0; i < E.n; i++) E.set(i, hsv2rgb(u8(E.hsv[0] + ((sc * E.pt[i].x) >> 5)), E.hsv[1], E.hsv[2]));
    }],
    ["breathing", "Breathing", {}, (E) => runI(E, M.breathing)],
    ["band_sat", "Band Saturation", {}, (E) => runI(E, M.band_sat)],
    ["band_val", "Band Brightness", {}, (E) => runI(E, M.band_val)],
    ["band_pinwheel_sat", "Pinwheel Saturation", {}, (E) => runDxDy(E, M.band_pinwheel_sat)],
    ["band_pinwheel_val", "Pinwheel Brightness", {}, (E) => runDxDy(E, M.band_pinwheel_val)],
    ["band_spiral_sat", "Spiral Saturation", {}, (E) => runDist(E, M.band_spiral_sat)],
    ["band_spiral_val", "Spiral Brightness", {}, (E) => runDist(E, M.band_spiral_val)],
    ["cycle_all", "Cycle All", {}, (E) => runI(E, M.cycle_all)],
    ["cycle_left_right", "Cycle Left/Right", {}, (E) => runI(E, M.cycle_left_right)],
    ["cycle_up_down", "Cycle Up/Down", {}, (E) => runI(E, M.cycle_up_down)],
    ["rainbow_moving_chevron", "Rainbow Chevron", {}, (E) => runI(E, M.rainbow_moving_chevron)],
    ["cycle_out_in", "Cycle Out/In", {}, (E) => runDist(E, M.cycle_out_in)],
    ["cycle_out_in_dual", "Cycle Out/In Dual", {}, (E) => runDxDy(E, M.cycle_out_in_dual)],
    ["cycle_pinwheel", "Cycle Pinwheel", {}, (E) => runDxDy(E, M.cycle_pinwheel)],
    ["cycle_spiral", "Cycle Spiral", {}, (E) => runDist(E, M.cycle_spiral)],
    ["dual_beacon", "Dual Beacon", {}, (E) => runSinCos(E, M.dual_beacon)],
    ["rainbow_beacon", "Rainbow Beacon", {}, (E) => runSinCos(E, M.rainbow_beacon)],
    ["rainbow_pinwheels", "Rainbow Pinwheels", {}, (E) => runSinCos(E, M.rainbow_pinwheels)],
    ["flower_blooming", "Flower Blooming", {}, (E) => {
      const t = u8(scale16by8(E.timer, qadd8(div(E.speed, 10), 1)));
      for (let i = 0; i < E.n; i++) {
        const p = E.pt[i], low = p.y > E.cy;
        const rgb = hsv2rgb(u8(p.x * 3 - p.y * 3 + (low ? t : -t)), E.hsv[1], E.hsv[2]);
        E.set(i, low ? [rgb[2], rgb[1], rgb[0]] : rgb);
      }
    }],
    ["raindrops", "Raindrops", { random: true }, (E, init) => {
      const paint = (i) => {
        let dH = div((E.hsv[0] + 180) % 360 - E.hsv[0], 4);
        if (dH > 127) dH -= 256; else if (dH < -127) dH += 256;
        E.set(i, hsv2rgb(u8(E.hsv[0] + dH * (rand8() & 3)), E.hsv[1], E.hsv[2]));
      };
      if (init) for (let i = 0; i < E.n; i++) paint(i);
      else if (scale16by8(E.timer, qadd8(E.speed, 16)) % 10 === 0) paint(rand8max(E.n));
    }],
    ["jellybean_raindrops", "Jellybean Raindrops", { random: true }, (E, init) => {
      const paint = (i) => E.set(i, hsv2rgb(rand8(), rand8mm(127, 255), E.hsv[2]));
      if (init) for (let i = 0; i < E.n; i++) paint(i);
      else if (scale16by8(E.timer, qadd8(E.speed, 16)) % 5 === 0) paint(rand8max(E.n));
    }],
    ["hue_breathing", "Hue Breathing", {}, (E) => runI(E, M.hue_breathing)],
    ["hue_pendulum", "Hue Pendulum", {}, (E) => runI(E, M.hue_pendulum)],
    ["hue_wave", "Hue Wave", {}, (E) => runI(E, M.hue_wave)],
    ["pixel_rain", "Pixel Rain", { random: true }, (E, init) => {
      if (init) E.st.timer = E.timer;
      if (E.timer - E.st.timer > 320 - E.speed) {
        E.st.timer = E.timer;
        const i = rand8max(E.n);
        E.set(i, rand8() & 2 ? [0, 0, 0] : hsv2rgb(rand8(), rand8mm(127, 255), E.hsv[2]));
      }
    }],
    ["pixel_flow", "Pixel Flow", { random: true }, (E, init) => {
      const pick = () => (rand8() & 2 ? [0, 0, 0] : hsv2rgb(rand8(), rand8mm(127, 255), E.hsv[2]));
      if (init) { E.st.led = Array.from({ length: E.n }, pick); E.st.wait = 0; }
      if (E.st.wait > E.timer) return;
      for (let i = 0; i < E.n; i++) E.set(i, E.st.led[i]);
      E.st.led.shift(); E.st.led.push(pick());
      E.st.wait = E.timer + div(3000, scale16by8(qadd8(E.speed, 16), 16));
    }],
    ["pixel_fractal", "Pixel Fractal", { random: true }, (E, init) => {
      const R = E.rows, C = E.cols, mid = C < 2 ? 1 : div(C, 2);
      if (init) { E.st.led = Array.from({ length: R }, () => new Array(mid).fill(false)); E.st.wait = 0; for (let i = 0; i < E.n; i++) E.set(i, [0, 0, 0]); }
      if (!(E.timer > E.st.wait)) return;
      const on = hsv2rgb(...E.hsv), off = [0, 0, 0];
      const put = (r, c, v) => { const i = E.co[r][c]; if (i !== undefined) E.set(i, v ? on : off); };
      for (let h = 0; h < R; h++) {
        const row = E.st.led[h];
        for (let l = 0; l < mid - 1; l++) { put(h, l, row[l]); put(h, C - 1 - l, row[l]); row[l] = row[l + 1]; }
        put(h, mid - 1, row[mid - 1]); put(h, C - mid, row[mid - 1]);
        row[mid - 1] = !(rand8() & 3);
      }
      E.st.wait = E.timer + div(3000, scale16by8(qadd8(E.speed, 16), 16));
    }],
    ["typing_heatmap", "Typing Heatmap", { reactive: true }, (E, init) => {
      if (init) { E.st.fb = E.fb(); E.st.dec = E.timer; }
      const dec = E.timer - E.st.dec >= 25;
      if (dec) E.st.dec = E.timer;
      for (let r = 0; r < E.rows; r++) for (let c = 0; c < E.cols; c++) {
        const i = E.co[r][c];
        if (i === undefined) continue;
        const val = E.st.fb[r][c];
        E.set(i, hsv2rgb(u8(170 - qsub8(val, 85)), E.hsv[1], scale8(u8((qadd8(170, val) - 170) * 3), E.hsv[2])));
        if (dec) E.st.fb[r][c] = qsub8(val, 1);
      }
    }],
    ["digital_rain", "Digital Rain", { random: true }, (E, init) => {
      const V = E.hsv[2], green = (V * 3) >> 2, boostMax = (V * 3) >> 2, decayTicks = V ? div(255, V) : 255;
      if (init) { E.st.fb = E.fb(); E.st.drop = 0; E.st.decay = 0; for (let i = 0; i < E.n; i++) E.set(i, [0, 0, 0]); }
      const fb = E.st.fb;
      E.st.decay = u8(E.st.decay + 1);
      for (let c = 0; c < E.cols; c++) for (let r = 0; r < E.rows; r++) {
        if (r === 0 && E.st.drop === 0 && Math.random() < 1 / 24) fb[r][c] = V;
        else if (fb[r][c] > 0 && fb[r][c] < V && E.st.decay === decayTicks) fb[r][c]--;
        const i = E.co[r][c];
        if (i === undefined) continue;
        if (fb[r][c] > green) {
          const b = u8(div(boostMax * (fb[r][c] - green), V - green || 1));
          E.set(i, [b, V, b]);
        } else E.set(i, [0, u8(div(V * fb[r][c], green || 1)), 0]);
      }
      if (E.st.decay === decayTicks) E.st.decay = 0;
      if (++E.st.drop > 28) {
        E.st.drop = 0;
        for (let r = E.rows - 1; r > 0; r--) for (let c = 0; c < E.cols; c++) {
          if (r === E.rows - 1 && fb[r][c] === V) fb[r][c]--;
          if (fb[r - 1][c] >= V) { fb[r - 1][c] = V - 1; fb[r][c] = V; }
        }
      }
    }],
    ["solid_reactive_simple", "Reactive Simple", { reactive: true }, (E) => runReactive(E, M.solid_reactive_simple)],
    ["solid_reactive", "Reactive", { reactive: true }, (E) => runReactive(E, M.solid_reactive)],
    ["solid_reactive_wide", "Reactive Wide", { reactive: true }, (E) => runSplash(E, last(E), M.wide)],
    ["solid_reactive_multiwide", "Reactive Multi-Wide", { reactive: true }, (E) => runSplash(E, 0, M.wide)],
    ["solid_reactive_cross", "Reactive Cross", { reactive: true }, (E) => runSplash(E, last(E), M.cross)],
    ["solid_reactive_multicross", "Reactive Multi-Cross", { reactive: true }, (E) => runSplash(E, 0, M.cross)],
    ["solid_reactive_nexus", "Reactive Nexus", { reactive: true }, (E) => runSplash(E, last(E), M.nexus)],
    ["solid_reactive_multinexus", "Reactive Multi-Nexus", { reactive: true }, (E) => runSplash(E, 0, M.nexus)],
    ["splash", "Splash", { reactive: true }, (E) => runSplash(E, last(E), M.splash)],
    ["multisplash", "Multi-Splash", { reactive: true }, (E) => runSplash(E, 0, M.splash)],
    ["solid_splash", "Solid Splash", { reactive: true }, (E) => runSplash(E, last(E), M.solid_splash)],
    ["solid_multisplash", "Solid Multi-Splash", { reactive: true }, (E) => runSplash(E, 0, M.solid_splash)],
    ["starlight_smooth", "Starlight Smooth", { random: true }, (E, init) => { if (init) E.st.phase = new Array(E.n).fill(0); runI(E, M.starlight_smooth); }],
    ...[["starlight", "Starlight", 0], ["starlight_dual_sat", "Starlight Dual Sat", 1], ["starlight_dual_hue", "Starlight Dual Hue", 2]].map(([key, label, kind]) => [key, label, { random: true }, (E, init) => {
      const paint = (i) => {
        const t = u16(scale16by8(E.timer, div(E.speed, 8)));
        const c = [E.hsv[0], E.hsv[1], scale8(u8(abs8(sin8(t) - 128) * 2), E.hsv[2])];
        if (kind === 2) c[0] = u8(c[0] + rand8max(31));
        if (kind === 1) c[1] = u8(c[1] + rand8max(31));
        E.set(i, hsv2rgb(...c));
      };
      if (init) for (let i = 0; i < E.n; i++) paint(i);
      else if (scale16by8(E.timer, qadd8(E.speed, 5)) % 5 === 0) paint(rand8max(E.n));
    }]),
    ["riverflow", "Riverflow", {}, (E) => runI(E, M.riverflow)],
  ].map(([key, label, opts, run]) => ({ key, label, reactive: !!opts.reactive, random: !!opts.random, run }));
  const BY_KEY = Object.fromEntries(EFFECTS.map((e) => [e.key, e]));

  // ---- engine ---------------------------------------------------------------
  // leds: [{x, y, flags, matrix: [row, col]}] from info.json rgb_matrix.layout.
  function createEngine({ leds, center, matrix }) {
    const n = leds.length;
    const rows = matrix ? matrix[0] : 1, cols = matrix ? matrix[1] : n;
    const co = Array.from({ length: rows }, () => new Array(cols));
    leds.forEach((l, i) => { if (l.matrix && co[l.matrix[0]]) co[l.matrix[0]][l.matrix[1]] = i; });
    const buf = new Uint8Array(n * 3);
    const E = {
      n, rows, cols, co,
      pt: leds.map((l) => ({ x: u8(l.x), y: u8(l.y) })),
      flags: leds.map((l) => l.flags || 0),
      cx: u8(center ? center[0] : 112), cy: u8(center ? center[1] : 32),
      hsv: [0, 255, 255], speed: 127, timer: 0, st: {},
      hits: { count: 0, x: [], y: [], index: [], tick: [] },
      set(i, rgb) { buf[i * 3] = rgb[0]; buf[i * 3 + 1] = rgb[1]; buf[i * 3 + 2] = rgb[2]; },
      fb: () => Array.from({ length: rows }, () => new Array(cols).fill(0)),
    };
    let effect = BY_KEY.solid_color, init = true, acc = 0;
    const HITS = 8;

    function tick(dt) {
      E.timer = E.timer + dt;
      // Mirrors rgb_matrix.c exactly, quirk included: an expired hit lowers the count
      // without compacting the arrays, so the newest entry drops out. Keeping QMK's
      // behaviour is the point of this port.
      const h = E.hits, count = h.count;
      for (let i = 0; i < count; i++) {
        if (65535 - dt < h.tick[i]) { h.count--; continue; }
        h.tick[i] += dt;
      }
      effect.run(E, init);
      init = false;
    }

    return {
      effects: EFFECTS,
      get effect() { return effect.key; },
      setEffect(key) { effect = BY_KEY[key] || BY_KEY.solid_color; init = true; E.st = {}; buf.fill(0); },
      setConfig({ h, s, v, speed }) {
        if (h !== undefined) E.hsv[0] = u8(h);
        if (s !== undefined) E.hsv[1] = u8(s);
        if (v !== undefined) E.hsv[2] = u8(v);
        if (speed !== undefined) E.speed = u8(speed);
      },
      // A key event on the LED under that key (QMK records presses and releases).
      hit(i) {
        const h = E.hits;
        if (h.count + 1 > HITS) {
          for (const k of ["x", "y", "tick", "index"]) h[k] = h[k].slice(1).concat([0]);
          h.count = HITS - 1;
        }
        h.x[h.count] = E.pt[i].x; h.y[h.count] = E.pt[i].y; h.index[h.count] = i; h.tick[h.count] = 0; h.count++;
        if (effect.key === "typing_heatmap" && E.st.fb) heat(i);
      },
      // Advance by ms of wall time; effects run every 16 ms like RGB_MATRIX_LED_FLUSH_LIMIT.
      step(ms) {
        acc += Math.min(ms, 250);
        while (acc >= 16) { tick(16); acc -= 16; }
        return buf;
      },
      frame: () => buf,
      // Test hook: render one frame at an exact timer with given hits.
      renderAt(timer, hits) {
        E.timer = timer;
        if (hits) E.hits = JSON.parse(JSON.stringify(hits));
        effect.run(E, init); init = false;
        return Array.from(buf);
      },
    };

    function heat(i) {
      const [row, col] = leds[i].matrix, fb = E.st.fb;
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const j = co[r][c];
        if (j === undefined) continue;
        if (r === row && c === col) fb[r][c] = qadd8(fb[r][c], 32);
        else {
          const dx = E.pt[i].x - E.pt[j].x, dy = E.pt[i].y - E.pt[j].y;
          const d = sqrt16(dx * dx + dy * dy);
          if (d <= 40) fb[r][c] = qadd8(fb[r][c], Math.min(16, qsub8(40, d)));
        }
      }
    }
  }

  return { createEngine, EFFECTS, hsv2rgb, _lib: { sin8, cos8, sqrt16, atan2_8, scale8, scale16by8, abs8 } };
});
