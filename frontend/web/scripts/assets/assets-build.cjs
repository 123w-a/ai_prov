// 小膳管家视觉资产切片工具（零依赖：自己解 PNG + zlib，不做任何降采样）
// 用法：
//   node assets-build.cjs scan            只量各元素在稿上的确切 bbox，不落盘
//   node assets-build.cjs cut             切片 + alpha matte + 写 PNG + 写 manifest
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const ROOT = __dirname;
// scripts/assets → 上溯 4 级才是仓库根 D:\develop\ai_prov（assets→scripts→web→frontend→ai_prov）
const SRC = path.resolve(ROOT, '..', '..', '..', '..', '.tmp-img', 'ask-expect.png');

// ---------- PNG 解码 ----------
function decodePng(buf) {
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error('not a png');
  let off = 8, w = 0, h = 0, bitDepth = 0, colorType = 0, idat = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.toString('ascii', off + 4, off + 8);
    const data = buf.subarray(off + 8, off + 8 + len);
    if (type === 'IHDR') {
      w = data.readUInt32BE(0); h = data.readUInt32BE(4);
      bitDepth = data[8]; colorType = data[9];
      if (bitDepth !== 8) throw new Error('only 8-bit supported, got ' + bitDepth);
    } else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    off += 12 + len;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const ch = colorType === 6 ? 4 : colorType === 2 ? 3 : colorType === 0 ? 1 : 0;
  if (!ch) throw new Error('unsupported colorType ' + colorType);
  const stride = w * ch;
  const out = Buffer.alloc(w * h * 4);
  let prev = Buffer.alloc(stride);
  let p = 0;
  for (let y = 0; y < h; y++) {
    const ft = raw[p++];
    const line = Buffer.from(raw.subarray(p, p + stride)); p += stride;
    for (let i = 0; i < stride; i++) {
      const a = i >= ch ? line[i - ch] : 0;
      const b = prev[i];
      const c = i >= ch ? prev[i - ch] : 0;
      let v = line[i];
      if (ft === 1) v += a;
      else if (ft === 2) v += b;
      else if (ft === 3) v += (a + b) >> 1;
      else if (ft === 4) {
        const pa = Math.abs(b - c), pb = Math.abs(a - c), pc = Math.abs(a + b - 2 * c);
        v += (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
      }
      line[i] = v & 0xff;
    }
    for (let x = 0; x < w; x++) {
      const s = x * ch, d = (y * w + x) * 4;
      out[d] = line[s];
      out[d + 1] = ch >= 3 ? line[s + 1] : line[s];
      out[d + 2] = ch >= 3 ? line[s + 2] : line[s];
      out[d + 3] = ch === 4 ? line[s + 3] : 255;
    }
    prev = line;
  }
  return { width: w, height: h, data: out };
}

// ---------- PNG 编码（filter 0，RGBA） ----------
function encodePng(img) {
  const { width: w, height: h, data } = img;
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (w * 4 + 1)] = 0;
    data.copy(raw, y * (w * 4 + 1) + 1, y * w * 4, (y + 1) * w * 4);
  }
  const chunks = [];
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const crcTable = (() => { const t = []; for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  const crc32 = (b) => { let c = 0xffffffff; for (const x of b) c = crcTable[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length, 0);
    const t = Buffer.from(type, 'ascii');
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([t, data])), 0);
    return Buffer.concat([len, t, data, crc]);
  };
  chunks.push(sig, chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0)));
  return Buffer.concat(chunks);
}

// ---------- 颜色工具 ----------
const srgbToLin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
function rgb2lab(r, g, b) {
  const R = srgbToLin(r), G = srgbToLin(g), B = srgbToLin(b);
  let x = (R * 0.4124 + G * 0.3576 + B * 0.1805) / 0.95047;
  let y = (R * 0.2126 + G * 0.7152 + B * 0.0722);
  let z = (R * 0.0193 + G * 0.1192 + B * 0.9505) / 1.08883;
  const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const fx = f(x), fy = f(y), fz = f(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}
const dE = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

// ---------- 元素定义（稿坐标 1536×1024；窗口由 assets-build.cjs map 逐区读墨迹图定出） ----------
const S = 1366 / 1536;
const ELEMENTS = [
  { id: 'bowl',         win: [332, 518, 512, 582],  note: '面板左下角的碗（碗口/碗身/筷子/底板/柔影）' },
  { id: 'branch-right', win: [975, 228, 1112, 346], note: '面板右上角那枝叶（同一枝上浅叶与深叶交错）' },
  { id: 'vine-main-bl', win: [262, 936, 354, 1024], note: '主区左下叶（被页底裁断；极淡，单色背景+单独降阈值）', thr: [1.5, 4.5], bg: 'mode', minSize: 8 },
  { id: 'vine-nav-bl',  win: [14, 870, 152, 1024],  note: '导航列左下藤（与主区那枝明暗不同）' },
  { id: 'sprig-top-l',  win: [264, 4, 312, 60],     note: '主区左上、分隔线右侧的小枝' },
  { id: 'icon-1',       win: [308, 702, 372, 766],  note: '示例图标 1（鱼）' },
  { id: 'icon-2',       win: [522, 702, 586, 766],  note: '示例图标 2（碗）' },
  { id: 'icon-3',       win: [736, 702, 800, 766],  note: '示例图标 3（西兰花）' },
  { id: 'icon-4',       win: [950, 702, 1014, 766], note: '示例图标 4（汤碗）' },
  // 右栏三张卡右上角的叶（2026-10-11 用户第二轮指认：这三枚此前是同一份手绘
  // --leaf-sprig 缩到 84×50，与稿的形状/位置都不一致）。三枚分别切：稿里它们
  // 是同一母题的三个实例，逐格墨迹图量到的位置分别是
  //   卡1 x1456..1488 / y40..85（卡顶边 y24）、卡2 x1460..1500 / y351..395
  //   （卡顶边 y339）、卡3 x1460..1500 / y811..851（卡顶边 y799）——
  // 即每张都是"右缘贴卡右边框、自卡顶下移 12~16 稿 px"。窗口右界取 1502，
  // 把卡右边框那条 1~2px 竖线排除在外（否则会被当成叶的一部分切进来）。
  { id: 'card-leaf-1',  win: [1446, 30, 1502, 96],   note: '右栏卡1（按谁的档案来定）右上角叶' },
  { id: 'card-leaf-2',  win: [1450, 341, 1502, 406], note: '右栏卡2（本周）右上角叶' },
  { id: 'card-leaf-3',  win: [1450, 801, 1502, 862], note: '右栏卡3（冰箱）右上角叶' },
];

const pad = (v) => String(v).padStart(4, ' ');

function scan(img) {
  const { width: w, data } = img;
  console.log('稿尺寸 ' + img.width + 'x' + img.height + '  换算 S=' + S.toFixed(6));
  for (const el of ELEMENTS) {
    const [x1, y1, x2, y2] = el.win;
    // 用窗口外圈 12px 估计局部背景
    let br = 0, bg = 0, bb = 0, n = 0;
    for (let y = y1; y < y2; y++) {
      for (let x = x1; x < x2; x++) {
        const onRing = (x - x1 < 12 || x2 - 1 - x < 12 || y - y1 < 12 || y2 - 1 - y < 12);
        if (!onRing) continue;
        const d = (y * w + x) * 4;
        br += data[d]; bg += data[d + 1]; bb += data[d + 2]; n++;
      }
    }
    const bgLab = rgb2lab(br / n, bg / n, bb / n);
    let minX = 1e9, maxX = -1, minY = 1e9, maxY = -1, strong = 0, soft = 0;
    for (let y = y1; y < y2; y++) {
      for (let x = x1; x < x2; x++) {
        const d = (y * w + x) * 4;
        const e = dE(rgb2lab(data[d], data[d + 1], data[d + 2]), bgLab);
        if (e >= 8) {
          strong++;
          if (x < minX) minX = x; if (x > maxX) maxX = x;
          if (y < minY) minY = y; if (y > maxY) maxY = y;
        } else if (e >= 3) soft++;
      }
    }
    const bw = maxX - minX + 1, bh = maxY - minY + 1;
    console.log(`\n[${el.id}] ${el.note}`);
    console.log(`  窗 ${x1},${y1}..${x2},${y2}  背景Lab=${bgLab.map((v) => v.toFixed(1)).join('/')}`);
    if (maxX < 0) { console.log('  未找到前景（整窗都近背景色）'); continue; }
    console.log(`  bbox 稿 x=${pad(minX)}..${pad(maxX)} (w=${bw}) y=${pad(minY)}..${pad(maxY)} (h=${bh})  前景=${strong}px 过渡带=${soft}px`);
    console.log(`  CSS 尺寸 ${(bw * S).toFixed(1)} × ${(bh * S).toFixed(1)} px   （外扩 10px 后 ${((bw + 20) * S).toFixed(1)} × ${((bh + 20) * S).toFixed(1)}）`);
  }
}

// ---------- 局部背景场：8px 网格取"低差异像素"中值 → 空洞就近填充 → 双线性插值 ----------
// 单一众数色在"窗口跨纸底与陶底"或"面板本身有渐变"时会系统性误判，故用局部背景。
const BLK = 8;
function backgroundField(img, x1, y1, x2, y2) {
  const { width: w, data } = img;
  const ww = x2 - x1, hh = y2 - y1;
  const tally = new Map();
  for (let y = y1; y < y2; y += 2) {
    for (let x = x1; x < x2; x += 2) {
      const d = (y * w + x) * 4;
      const key = (data[d] >> 3) + ',' + (data[d + 1] >> 3) + ',' + (data[d + 2] >> 3);
      tally.set(key, (tally.get(key) || 0) + 1);
    }
  }
  let bk = null, bn = -1;
  for (const [k, n] of tally) if (n > bn) { bn = n; bk = k; }
  const [mr, mg, mb] = bk.split(',').map((v) => (+v << 3) + 4);
  const coarse = rgb2lab(mr, mg, mb);
  const cols = Math.ceil(ww / BLK), rows = Math.ceil(hh / BLK);
  const field = new Array(cols * rows).fill(null);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const px = [];
      for (let y = y1 + r * BLK; y < Math.min(y1 + (r + 1) * BLK, y2); y++) {
        for (let x = x1 + c * BLK; x < Math.min(x1 + (c + 1) * BLK, x2); x++) {
          const d = (y * w + x) * 4;
          if (dE(rgb2lab(data[d], data[d + 1], data[d + 2]), coarse) < 8) px.push([data[d], data[d + 1], data[d + 2]]);
        }
      }
      if (px.length >= 6) {
        px.sort((a, b) => (a[0] + a[1] + a[2]) - (b[0] + b[1] + b[2]));
        const m = px[px.length >> 1];
        field[r * cols + c] = rgb2lab(m[0], m[1], m[2]);
      }
    }
  }
  for (let pass = 0; pass < 4; pass++) {
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        if (field[r * cols + c]) continue;
        let acc = null, n = 0;
        for (let dr = -1; dr <= 1; dr++) {
          for (let dc = -1; dc <= 1; dc++) {
            const nr = r + dr, nc = c + dc;
            if (nr < 0 || nc < 0 || nr >= rows || nc >= cols) continue;
            const v = field[nr * cols + nc];
            if (v) { acc = acc ? [acc[0] + v[0], acc[1] + v[1], acc[2] + v[2]] : [v[0], v[1], v[2]]; n++; }
          }
        }
        if (n) field[r * cols + c] = [acc[0] / n, acc[1] / n, acc[2] / n];
      }
    }
  }
  const at = (x, y) => {
    const gx = Math.min(cols - 1, Math.max(0, (x - x1) / BLK - 0.5));
    const gy = Math.min(rows - 1, Math.max(0, (y - y1) / BLK - 0.5));
    const x0 = Math.floor(gx), y0 = Math.floor(gy);
    const x1i = Math.min(cols - 1, x0 + 1), y1i = Math.min(rows - 1, y0 + 1);
    const tx = gx - x0, ty = gy - y0;
    const g = (c, r) => field[r * cols + c] || coarse;
    const a = g(x0, y0), b = g(x1i, y0), cc = g(x0, y1i), d = g(x1i, y1i);
    const L = [];
    for (let k = 0; k < 3; k++) L.push((a[k] * (1 - tx) + b[k] * tx) * (1 - ty) + (cc[k] * (1 - tx) + d[k] * tx) * ty);
    return L;
  };
  return { at, coarse };
}

// ---------- 提取 + matte ----------
function cutout(img, el) {
  const { width: w, data } = img;
  const [wx1, wy1, wx2, wy2] = el.win;
  const bgField = backgroundField(img, wx1, wy1, wx2, wy2);
  const bgLab = bgField.coarse;
  const [T0, T1] = el.thr || [3, 8];  // 极淡元素可单独降阈值（默认 <3 全透、>8 全不透）
  // 1) 找 bbox
  let minX = 1e9, maxX = -1, minY = 1e9, maxY = -1;
  for (let y = wy1; y < wy2; y++) {
    for (let x = wx1; x < wx2; x++) {
      const d = (y * w + x) * 4;
      if (dE(rgb2lab(data[d], data[d + 1], data[d + 2]), bgField.at(x, y)) >= T1) {
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
    }
  }
  // 2) 外扩 10 源像素，保留柔边
  const EXT = 10;
  minX = Math.max(0, minX - EXT); minY = Math.max(0, minY - EXT);
  maxX = Math.min(img.width - 1, maxX + EXT); maxY = Math.min(img.height - 1, maxY + EXT);
  const cw = maxX - minX + 1, chh = maxY - minY + 1;
  const out = Buffer.alloc(cw * chh * 4);
  const alpha = new Float32Array(cw * chh);
  // 极淡元素用单色背景（分块场会把元素本身当中值吸收掉，反而把它算成背景）
  const useMode = el.bg === 'mode';
  const bogAt = useMode ? () => bgLab : (x, y) => bgField.at(x, y);
  let openingReverted = false;
  for (let y = 0; y < chh; y++) {
    for (let x = 0; x < cw; x++) {
      const d = ((minY + y) * w + (minX + x)) * 4;
      const o = (y * cw + x) * 4;
      const e = dE(rgb2lab(data[d], data[d + 1], data[d + 2]), bogAt(minX + x, minY + y));
      const a = e <= T0 ? 0 : e >= T1 ? 1 : (e - T0) / (T1 - T0);
      alpha[y * cw + x] = a;
      out[o] = data[d]; out[o + 1] = data[d + 1]; out[o + 2] = data[d + 2]; out[o + 3] = Math.round(a * 255);
    }
  }
  // 3) alpha 1px 高斯羽化（3x3，σ≈1）
  const k = [1, 2, 1, 2, 4, 2, 1, 2, 1];
  const blurred = new Float32Array(cw * chh);
  for (let y = 0; y < chh; y++) {
    for (let x = 0; x < cw; x++) {
      let s = 0, wsum = 0;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= cw || ny >= chh) continue;
          const kk = k[(dy + 1) * 3 + (dx + 1)];
          s += alpha[ny * cw + nx] * kk; wsum += kk;
        }
      }
      blurred[y * cw + x] = s / wsum;
    }
  }
  for (let i = 0; i < cw * chh; i++) out[i * 4 + 3] = Math.round(blurred[i] * 255);
  // 4) 颜色外扩：把前景色扩散到透明像素，避免缩放时吸到纸底色（白边）
  for (let pass = 0; pass < 3; pass++) {
    const copy = Buffer.from(out);
    for (let y = 0; y < chh; y++) {
      for (let x = 0; x < cw; x++) {
        const o = (y * cw + x) * 4;
        if (copy[o + 3] > 8) continue;
        let r = 0, g = 0, b = 0, cnt = 0;
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= cw || ny >= chh) continue;
            const no = (ny * cw + nx) * 4;
            if (copy[no + 3] > 8) { r += copy[no]; g += copy[no + 1]; b += copy[no + 2]; cnt++; }
          }
        }
        if (cnt) { out[o] = Math.round(r / cnt); out[o + 1] = Math.round(g / cnt); out[o + 2] = Math.round(b / cnt); }
      }
    }
  }
  // 4) 形态学开运算：先腐蚀再膨胀，专门除掉 1~2px 的细线（面板边框、圆角弧线、页底细线），
  //    叶片/碗身这类 ≥4px 厚的形状不受影响。
  const bin = new Uint8Array(cw * chh);
  for (let i = 0; i < cw * chh; i++) bin[i] = out[i * 4 + 3] >= 128 ? 1 : 0;
  const eroded = new Uint8Array(cw * chh);
  for (let y = 0; y < chh; y++) {
    for (let x = 0; x < cw; x++) {
      if (!bin[y * cw + x]) continue;
      const up = y > 0 ? bin[(y - 1) * cw + x] : 0;
      const dn = y < chh - 1 ? bin[(y + 1) * cw + x] : 0;
      const lf = x > 0 ? bin[y * cw + x - 1] : 0;
      const rt = x < cw - 1 ? bin[y * cw + x + 1] : 0;
      eroded[y * cw + x] = (up && dn && lf && rt) ? 1 : 0;
    }
  }
  const opened = new Uint8Array(cw * chh);
  for (let y = 0; y < chh; y++) {
    for (let x = 0; x < cw; x++) {
      if (!eroded[y * cw + x]) continue;
      opened[y * cw + x] = 1;
      if (y > 0) opened[(y - 1) * cw + x] = 1;
      if (y < chh - 1) opened[(y + 1) * cw + x] = 1;
      if (x > 0) opened[y * cw + x - 1] = 1;
      if (x < cw - 1) opened[y * cw + x + 1] = 1;
    }
  }
  for (let i = 0; i < cw * chh; i++) if (!opened[i]) out[i * 4 + 3] = 0;
  // 安全阀：开运算若把"加权不透明质量"砍掉四成以上，说明这个元素本身就是细笔触（极淡小枝），整步回退
  let preM = 0, postM = 0;
  for (let i = 0; i < cw * chh; i++) {
    const a = alpha[i];
    preM += a;
    if (opened[i]) postM += a;
  }
  if (preM && postM < preM * 0.6) {
    for (let i = 0; i < cw * chh; i++) out[i * 4 + 3] = Math.round(alpha[i] * 255);
    openingReverted = true;
  }

  // 5) 连通域过滤：丢掉窗口里混进来的残线与碎点
  const label = new Int32Array(cw * chh).fill(-1);
  const comps = [];
  for (let i = 0; i < cw * chh; i++) {
    if (label[i] !== -1 || out[i * 4 + 3] < 128) continue;
    const id = comps.length;
    const stack = [i];
    label[i] = id;
    let size = 0, bx1 = 1e9, bx2 = -1, by1 = 1e9, by2 = -1;
    const members = [];
    while (stack.length) {
      const cur = stack.pop();
      const cx = cur % cw, cy = (cur - cx) / cw;
      size++; members.push(cur);
      if (cx < bx1) bx1 = cx; if (cx > bx2) bx2 = cx;
      if (cy < by1) by1 = cy; if (cy > by2) by2 = cy;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nx = cx + dx, ny = cy + dy;
        if (nx < 0 || ny < 0 || nx >= cw || ny >= chh) continue;
        const ni = ny * cw + nx;
        if (label[ni] === -1 && out[ni * 4 + 3] >= 128) { label[ni] = id; stack.push(ni); }
      }
    }
    comps.push({ members, size, bbox: { x: bx1, y: by1, w: bx2 - bx1 + 1, h: by2 - by1 + 1 } });
  }
  const dropped = [];
  const minSize = el.minSize || Math.max(12, Math.round(cw * chh * 0.0004));
  let rx1 = 1e9, ry1 = 1e9, rx2 = -1, ry2 = -1;
  for (const c of comps) {
    const b = c.bbox;
    const touchesL = b.x === 0, touchesR = b.x + b.w === cw, touchesT = b.y === 0, touchesB = b.y + b.h === chh;
    // 混进窗口的边框/分隔线/页底细线：细长，且往往从窗口某一侧进来
    const isFrameLine =
      (touchesT && touchesB && b.w <= 8) || (touchesL && touchesR && b.h <= 8) ||
      (b.h <= 5 && b.w >= 25 && (touchesL || touchesR)) ||
      (b.w <= 5 && b.h >= 25 && (touchesT || touchesB));
    if (c.size < minSize || isFrameLine) {
      for (const m of c.members) out[m * 4 + 3] = 0;
      dropped.push({ size: c.size, bbox: b, why: c.size < minSize ? '碎点' : '边框/直线' });
    } else {
      // 保留域：只在保留像素的范围内对账，主动丢弃的污染像素不进分母
      rx1 = Math.min(rx1, b.x - 2); ry1 = Math.min(ry1, b.y - 2);
      rx2 = Math.max(rx2, b.x + b.w + 2); ry2 = Math.max(ry2, b.y + b.h + 2);
    }
  }
  // 收紧到保留像素的 bbox
  let kx1 = 1e9, kx2 = -1, ky1 = 1e9, ky2 = -1, kept = 0;
  for (let y = 0; y < chh; y++) {
    for (let x = 0; x < cw; x++) {
      if (out[(y * cw + x) * 4 + 3] > 8) {
        kept++;
        if (x < kx1) kx1 = x; if (x > kx2) kx2 = x;
        if (y < ky1) ky1 = y; if (y > ky2) ky2 = y;
      }
    }
  }
  const tight = { x: minX + kx1, y: minY + ky1, w: kx2 - kx1 + 1, h: ky2 - ky1 + 1 };
  const trim = { width: tight.w, height: tight.h, data: Buffer.alloc(tight.w * tight.h * 4) };
  for (let y = 0; y < tight.h; y++) {
    out.copy(trim.data, y * tight.w * 4, ((ky1 + y) * cw + kx1) * 4, ((ky1 + y) * cw + kx1 + tight.w) * 4);
  }
  return { image: trim, bbox: tight, pad: { x: minX, y: minY, w: cw, h: chh }, bgLab, bgField, bogAt, dropped, comps: comps.length, kept, T1, Tmid: (T0 + T1) / 2, openingReverted, roi: { x: rx1, y: ry1, w: rx2 - rx1 + 1, h: ry2 - ry1 + 1 } };
}

// ---------- 闸门：切片 alpha 与稿的前景掩膜逐像素对账 ----------
function gate(img, c) {
  const { width: w, data } = img;
  const { x: bx, y: by, w: cw, h: chh } = c.bbox;
  // 只在"保留域"内对账：主动丢弃的边框/渐变污染不进分母
  const rx1 = Math.max(0, c.roi.x - (c.pad.x - bx) * 0 + 0), ry1 = Math.max(0, c.roi.y);
  const rx2 = Math.min(cw, c.roi.x + c.roi.w), ry2 = Math.min(chh, c.roi.y + c.roi.h);
  let inter = 0, uni = 0, fgSrc = 0, fgCut = 0, sx = 0, sy = 0, cx = 0, cy = 0, roiN = 0;
  const histSrc = new Array(8).fill(0), histCut = new Array(8).fill(0);
  for (let y = ry1; y < ry2; y++) {
    for (let x = rx1; x < rx2; x++) {
      const d = ((by + y) * w + (bx + x)) * 4;
      const o = (y * cw + x) * 4;
      // 参照阈值与掩膜判据一致：α>0.5 ⇔ ΔE ≥ (T0+T1)/2，否则极淡元素会被系统性判成失配
      const sFg = dE(rgb2lab(data[d], data[d + 1], data[d + 2]), c.bogAt(bx + x, by + y)) >= c.Tmid;
      const cFg = c.image.data[o + 3] > 127;
      roiN++;
      if (sFg) { fgSrc++; sx += x; sy += y; }
      if (cFg) { fgCut++; cx += x; cy += y; }
      if (sFg && cFg) inter++;
      if (sFg || cFg) uni++;
      const L = rgb2lab(data[d], data[d + 1], data[d + 2])[0];
      histSrc[Math.min(7, Math.max(0, Math.floor(L / 12.5)))]++;
      const Lc = rgb2lab(c.image.data[o], c.image.data[o + 1], c.image.data[o + 2])[0];
      histCut[Math.min(7, Math.max(0, Math.floor(Lc / 12.5)))]++;
    }
  }
  const ns = histSrc.reduce((a, b) => a + b, 0), nc = histCut.reduce((a, b) => a + b, 0);
  let cum = 0, emd = 0;
  for (let i = 0; i < 8; i++) { cum += histSrc[i] / ns - histCut[i] / nc; emd += Math.abs(cum); }
  emd /= 8;
  const shift = (fgSrc && fgCut) ? [cx / fgCut - sx / fgSrc, cy / fgCut - sy / fgSrc] : [0, 0];
  return {
    iou: uni ? inter / uni : 0,
    covDiff: roiN ? Math.abs(fgSrc - fgCut) / roiN : 0,
    centroidShift: shift,
    emd,
  };
}

// ---------- 粗粒度墨迹图：把窗口切成 cell×cell 的格子，打印每格相对局部背景的 ΔE 档 ----------
function inkMap(img, x1, y1, x2, y2, cell) {
  const { width: w, data } = img;
  // 用整窗众数色当背景（元素占比 <50% 时比外圈更稳，且不受"元素撑满窗口"的影响）
  const tally = new Map();
  for (let y = y1; y < y2; y += 2) {
    for (let x = x1; x < x2; x += 2) {
      const d = (y * w + x) * 4;
      const key = (data[d] >> 3) + ',' + (data[d + 1] >> 3) + ',' + (data[d + 2] >> 3);
      tally.set(key, (tally.get(key) || 0) + 1);
    }
  }
  let bestKey = null, bestN = -1;
  for (const [k, n] of tally) if (n > bestN) { bestN = n; bestKey = k; }
  const [br, bg, bb] = bestKey.split(',').map((v) => (+v << 3) + 4);
  const bgLab = rgb2lab(br, bg, bb);
  console.log(`# 窗口 ${x1},${y1}..${x2},${y2}  cell=${cell}  众数背景 rgb(${br},${bg},${bb}) Lab=${bgLab.map((v) => v.toFixed(1)).join('/')}`);
  const cols = Math.ceil((x2 - x1) / cell), rows = Math.ceil((y2 - y1) / cell);
  // 行首标注 y 坐标，列标每 10 格标一次 x 坐标
  let head = '     ';
  for (let c = 0; c < cols; c++) head += (c % 10 === 0) ? String((x1 + c * cell)).padStart(4, ' ').slice(-1) : ' ';
  console.log(head + '   （列首数字=该处 x 的百位/十位，按 10 格一标）');
  for (let r = 0; r < rows; r++) {
    let line = String(y1 + r * cell).padStart(4, ' ') + ' ';
    for (let c = 0; c < cols; c++) {
      let mx = 0, n = 0;
      for (let y = y1 + r * cell; y < Math.min(y1 + (r + 1) * cell, y2); y++) {
        for (let x = x1 + c * cell; x < Math.min(x1 + (c + 1) * cell, x2); x++) {
          const d = (y * w + x) * 4;
          const e = dE(rgb2lab(data[d], data[d + 1], data[d + 2]), bgLab);
          if (e > mx) mx = e; n++;
        }
      }
      line += mx < 3 ? '.' : mx < 8 ? '-' : mx < 16 ? '+' : mx < 30 ? '#' : '@';
    }
    console.log(line);
  }
  console.log('图例  . <3 背景   - 3~8 极浅    + 8~16 浅   # 16~30 中   @ >30 重');
}

// ---------- 切片预览拼图：把每个资产按 2× 画在棋盘底上，用来看形是否完整、有没有混进边框、边缘有没有纸底残留 ----------
function preview(outDir) {
  const CELL = 8, SCALE = 2, ROWS = 2;
  const order = [
    ['bowl'], ['branch-right'], ['vine-main-bl'], ['vine-nav-bl'],
    ['sprig-top-l'], ['icon-1'], ['icon-2'], ['icon-3'], ['icon-4'],
  ];
  const items = order.map(([id]) => {
    const img = decodePng(fs.readFileSync(path.join(outDir, id + '.png')));
    return { id, img, w: img.width * SCALE, h: img.height * SCALE };
  });
  const split = 4;
  const row1 = items.slice(0, split), row2 = items.slice(split);
  const W = Math.max(row1.reduce((a, i) => a + i.w + 24, 24), row2.reduce((a, i) => a + i.w + 24, 24));
  const h1 = Math.max(...row1.map((i) => i.h)) + 24;
  const H = h1 + Math.max(...row2.map((i) => i.h)) + 40;
  const out = Buffer.alloc(W * H * 4);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const o = (y * W + x) * 4;
      const dark = ((x / CELL) | 0) % 2 === ((y / CELL) | 0) % 2;
      const v = dark ? 198 : 232;
      out[o] = v; out[o + 1] = v - 6; out[o + 2] = v - 14; out[o + 3] = 255;
    }
  }
  const draw = (it, ox, oy) => {
    const { img } = it;
    for (let y = 0; y < it.h; y++) {
      for (let x = 0; x < it.w; x++) {
        // 双线性取样：看的就是放大后的边缘质量
        const fx = x / SCALE, fy = y / SCALE;
        const x0 = Math.floor(fx), y0 = Math.floor(fy);
        const x1 = Math.min(img.width - 1, x0 + 1), y1 = Math.min(img.height - 1, y0 + 1);
        const tx = fx - x0, ty = fy - y0;
        const at = (px, py) => { const d = (py * img.width + px) * 4; return [img.data[d], img.data[d + 1], img.data[d + 2], img.data[d + 3]]; };
        const p00 = at(x0, y0), p10 = at(x1, y0), p01 = at(x0, y1), p11 = at(x1, y1);
        const mix = (k) => (p00[k] * (1 - tx) + p10[k] * tx) * (1 - ty) + (p01[k] * (1 - tx) + p11[k] * tx) * ty;
        const a = mix(3) / 255;
        if (a <= 0.004) continue;
        const D = ((oy + y) * W + (ox + x)) * 4;
        for (let k = 0; k < 3; k++) out[D + k] = Math.round(mix(k) * a + out[D + k] * (1 - a));
      }
    }
  };
  let ox = 24;
  for (const it of row1) { draw(it, ox, 12); ox += it.w + 24; }
  ox = 24;
  for (const it of row2) { draw(it, ox, h1 + 12); ox += it.w + 24; }
  const p = path.resolve(ROOT, '..', '..', '..', '..', '.tmp-img', 'assets-preview.png');
  fs.writeFileSync(p, encodePng({ width: W, height: H, data: out }));
  console.log(`预览拼图（2× 棋盘底，上排：bowl / branch-right / vine-main-bl / vine-nav-bl；下排：sprig-top-l / icon-1..4）`);
  console.log(p);
}

(async () => {
  const mode = process.argv[2] || 'scan';
  const img = decodePng(fs.readFileSync(SRC));
  if (mode === 'preview') {
    preview(path.resolve(ROOT, '..', '..', 'src', 'next', 'assets', 'hero'));
    return;
  }
  if (mode === 'map') {
    const [x1, y1, x2, y2, cell] = process.argv.slice(3).map(Number);
    inkMap(img, x1, y1, x2, y2, cell || 10);
    return;
  }
  if (mode === 'scan') { scan(img); return; }
  if (mode !== 'cut') throw new Error('unknown mode ' + mode);
  const outDir = path.join(ROOT, '..', '..', 'src', 'next', 'assets', 'hero');
  fs.mkdirSync(outDir, { recursive: true });
  const manifest = [];
  console.log('元素            CSS宽x高(稿px)   文件               字节   maskIoU  覆盖率差  质心偏移   直方EMD  丢弃');
  const fails = [];
  for (const el of ELEMENTS) {
    const c = cutout(img, el);
    const file = el.id + '.png';
    const png = encodePng(c.image);
    fs.writeFileSync(path.join(outDir, file), png);
    const g = gate(img, c);
    manifest.push({
      id: el.id, file, note: el.note,
      designBbox: c.bbox,
      cssWidth: +(c.bbox.w * S).toFixed(2), cssHeight: +(c.bbox.h * S).toFixed(2),
      bytes: png.length,
      gate: { maskIoU: +g.iou.toFixed(4), coverageDiff: +g.covDiff.toFixed(4), centroidShift: [+g.centroidShift[0].toFixed(2), +g.centroidShift[1].toFixed(2)], labHistEMD: +g.emd.toFixed(4) },
    });
    const bad = [];
    if (g.iou < 0.9) bad.push('IoU<0.90');
    if (g.covDiff > 0.08) bad.push('覆盖率差>8%');
    if (Math.hypot(g.centroidShift[0], g.centroidShift[1]) > 1.5) bad.push('质心>1.5px');
    if (g.emd > 0.12) bad.push('EMD>0.12');
    if (png.length > 80 * 1024) bad.push('>80KB');
    if (bad.length) fails.push(el.id + ': ' + bad.join('/'));
    console.log(`${el.id.padEnd(15)} ${(c.bbox.w + 'x' + c.bbox.h).padEnd(15)} ${file.padEnd(20)}${String(png.length).padStart(6)}  ` +
      `${g.iou.toFixed(3).padStart(6)}  ${(g.covDiff * 100).toFixed(1).padStart(7)}%  ` +
      `${(g.centroidShift[0].toFixed(1) + ',' + g.centroidShift[1].toFixed(1)).padStart(9)}  ${g.emd.toFixed(4).padStart(7)}  ` +
      `${c.dropped.length}域`);
  }
  fs.writeFileSync(path.join(outDir, 'manifest.json'), JSON.stringify({ scale: S, source: 'ask-expect.png 1536x1024', howTo: 'node frontend/web/scripts/assets/assets-build.cjs cut', elements: manifest }, null, 2));
  const total = manifest.reduce((a, m) => a + m.bytes, 0);
  console.log(`\n共 ${manifest.length} 个资产，合计 ${(total / 1024).toFixed(1)} KB（预算 ≤500KB）`);
  console.log(fails.length ? '闸门未过：\n  ' + fails.join('\n  ') : '闸门全过（IoU≥0.90 / 覆盖率差≤8% / 质心≤1.5px / EMD≤0.12 / 单件≤80KB）');
})();
