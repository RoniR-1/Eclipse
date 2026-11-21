const byId = id => document.getElementById(id);

// Remove an API key saved by the former settings dialog; the server now uses the keys in .env.
try { sessionStorage.removeItem('financial_twin_api_key'); } catch { /* storage unavailable */ }

const state = {
  stage: 'intake',
  resultsView: 'timeline', // 'timeline' | 'tree'
  drawerOpen: false,
  chartMetric: 'net_worth_chf',
  extraction: null,
  years: 5,
  nodes: new Map(),
  narratives: new Map(),
  pathReports: new Map(),
  activeBranchId: 'main',
  selectedId: null,
  network: null,
  graphNodes: null,
  graphEdges: null,
  revision: 0,
  openDetails: new Set(['branch-info']),
  presets: [],
  showRange: false,
  range: null,
  rangeKey: '',
  rangeLoading: '',
  spineRaf: null,
  spineLastMs: 0,
  spineNodesMeta: [],
  forkHover: null,
  forkHoverSuppressPos: null,
  forkArcAnim: new Map(),
  justSwitchedFromMonth: null,
  branchSwap: null,
  astralRaf: null,
  treeHoverId: null,
  treeRenderData: null,
  sensitivityOpen: false,
  graphBuilder: { metricId: 'net_worth_chf', selectedBranchIds: [], zoom: null },
  run: null, // inputs needed to replay this run exactly (see financial_twin/reproduce.py)
};

const BRANCH_PALETTE = [
  '#ffa45e', // P0 (Original path) — warm amber
  '#6fd1a4', // P1 — emerald mint
  '#73c8e6', // P2 — astral teal
  '#f5c85b', // P3 — warm gold
  '#d896ec', // P4 — soft plum
  '#ff7a6b', // P5 — coral
  '#f09a78', // P6 — terracotta
  '#a6d48a', // P7 — sage
];

/* ==========================================================================
   CURRENCY
   The engine calculates in CHF. Other currencies are a view: amounts are converted for display, and typed
   amounts are converted back to CHF before they are sent. Rates come from /api/currencies.
   ========================================================================== */

const CHF = { code: 'CHF', name: 'Swiss franc', symbol: 'CHF', per_chf: 1, date: null, source: null };
state.currency = CHF;
state.currencies = [CHF];

// Prefix for amounts: "€2,000", "$2,000", but "SEK 2,000" and "CHF 2,000" (letters get a space).
const currencyPrefix = () => {
  const symbol = state.currency.symbol || state.currency.code;
  return /[A-Za-z]$/.test(symbol) ? `${symbol} ` : symbol;
};
// The unit shown next to money inputs.
const currencyUnit = () => state.currency.code === 'CHF' ? 'CHF' : (state.currency.symbol || state.currency.code);
const chfToDisplay = chf => Number(chf) * state.currency.per_chf;
const displayToChf = amount => Number(amount) / state.currency.per_chf;

function setCurrency(code, list = state.currencies) {
  const found = list.find(item => item.code === code);
  state.currency = found ? { ...found } : CHF;
  return state.currency;
}

// One line saying which rate is used, e.g. "1 EUR = 0.92 CHF · European Central Bank reference rate of 2026-11-20".
// Small units are quoted per 100 (100 JPY = 0.53 CHF) so two decimals still say something.
function currencyRateText(currency = state.currency) {
  if (!currency || currency.code === 'CHF') return 'Calculations are in Swiss francs.';
  const units = 1 / currency.per_chf < 0.1 ? 100 : 1;
  return `${units} ${currency.code} = ${(units / currency.per_chf).toFixed(2)} CHF · ${currency.source || 'exchange rate'}${currency.date ? ` of ${currency.date}` : ''}. Calculations stay in CHF.`;
}

// Numbers shown to people have at most two decimals and comma thousands separators (5,000).
const twoDecimals = value => new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value);

const money = value => {
  if (state.currency.code === 'CHF') {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'CHF', maximumFractionDigits: 0 }).format(value);
  }
  const amount = chfToDisplay(value);
  const digits = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(Math.abs(amount));
  return `${Math.round(amount) < 0 ? '-' : ''}${currencyPrefix()}${digits}`;
};

const moneySigned = value => {
  if (Math.abs(chfToDisplay(value)) < 0.5) return `${currencyPrefix()}0`;
  return `${value > 0 ? '+' : '−'}${money(Math.abs(value))}`;
};

const compactChf = value => {
  const amount = chfToDisplay(value);
  const sign = amount < 0 ? '−' : '';
  const abs = Math.abs(amount);
  const prefix = currencyPrefix();
  if (abs >= 1_000_000) return `${sign}${prefix}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${sign}${prefix}${Math.round(abs / 1000)}k`;
  if (abs >= 1_000) return `${sign}${prefix}${(abs / 1_000).toFixed(1)}k`;
  return `${sign}${prefix}${Math.round(abs)}`;
};

// Server and AI text quotes amounts as "CHF 1,234.50"; show those in the chosen currency too.
// "(written as €2,000)" notes, added when a story's amounts were converted, are dropped once shown in that currency.
function localizeAmounts(text) {
  if (!text || state.currency.code === 'CHF') return text ?? '';
  return String(text).replace(/CHF\s?(-?\d[\d,'’]*(?:\.\d+)?)(?:\s*\(written as [^)]*\))?/g, (match, number) => {
    const value = Number(number.replace(/[,'’]/g, ''));
    return Number.isFinite(value) ? money(value) : match;
  });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showToast(message, { error = false } = {}) {
  const region = byId('toast-region');
  const toast = document.createElement('div');
  toast.className = `toast${error ? ' is-error' : ''}`;
  toast.textContent = message;
  region.append(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.25s';
    setTimeout(() => toast.remove(), 260);
  }, 4000);
}

function setButtonLoading(button, loading, loadingLabel, idleLabel) {
  button.disabled = loading;
  const label = button.querySelector('.btn-label');
  if (label) label.textContent = loading ? loadingLabel : idleLabel;
  else button.textContent = loading ? loadingLabel : idleLabel;
}

// Smooth non-repeating wave using irrational frequency ratios
function organicWave(t, seed) {
  return (
    Math.sin(t * 0.41 + seed * 1.73) * 0.50 +
    Math.sin(t * 0.67 - seed * 2.41) * 0.32 +
    Math.sin(t * 1.09 + seed * 0.89) * 0.18
  );
}

/* ==========================================================================
   SUBTLE WARM ASTRAL STARFIELD BACKDROP
   ========================================================================== */

const astralStars = Array.from({ length: 64 }, (_, i) => {
  const pseudo = s => {
    const x = Math.sin(i * 127.1 + s * 311.7) * 43758.5453;
    return x - Math.floor(x);
  };
  return {
    nx: pseudo(1),
    ny: pseudo(2),
    r: 0.7 + pseudo(3) * 1.5,
    baseAlpha: 0.08 + pseudo(4) * 0.28,
    driftX: (pseudo(5) - 0.5) * 0.008,
    driftY: (pseudo(6) - 0.5) * 0.006,
    parallax: 0.03 + pseudo(7) * 0.1,
    seed: i * 1.91 + pseudo(8) * 6,
    warm: pseudo(9) > 0.35,
  };
});

function drawAstralFrame(nowMs) {
  const canvas = byId('astral-backdrop');
  if (!canvas) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = window.innerWidth;
  const h = window.innerHeight;
  const tw = Math.round(w * dpr);
  const th = Math.round(h * dpr);
  if (canvas.width !== tw || canvas.height !== th) {
    canvas.width = tw;
    canvas.height = th;
  }

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const t = reduced ? 2.5 : nowMs * 0.001;
  const scrollY = window.scrollY || 0;

  for (const s of astralStars) {
    const wx = ((s.nx + t * s.driftX * 0.35 + organicWave(t * 0.22, s.seed) * 0.012) % 1 + 1) % 1;
    const wy = ((s.ny + t * s.driftY * 0.35 - (scrollY * s.parallax) / Math.max(h, 600)) % 1 + 1) % 1;
    const x = wx * w;
    const y = wy * h;
    const twinkle = 0.65 + 0.35 * organicWave(t * 0.65, s.seed + 3.1);
    const alpha = Math.max(0.03, s.baseAlpha * twinkle);
    const rgb = s.warm ? '255, 214, 172' : '255, 244, 234';

    if (s.r > 1.55) {
      const g = ctx.createRadialGradient(x, y, 0, x, y, s.r * 4.5);
      g.addColorStop(0, `rgba(${rgb}, ${alpha * 0.85})`);
      g.addColorStop(1, `rgba(${rgb}, 0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, s.r * 4.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = `rgba(${rgb}, ${alpha})`;
    ctx.beginPath();
    ctx.arc(x, y, s.r, 0, Math.PI * 2);
    ctx.fill();
  }

  if (!reduced && !document.hidden) {
    state.astralRaf = requestAnimationFrame(drawAstralFrame);
  } else {
    state.astralRaf = null;
  }
}

function startAstralBackdrop() {
  if (state.astralRaf) cancelAnimationFrame(state.astralRaf);
  state.astralRaf = requestAnimationFrame(drawAstralFrame);
}

/* ==========================================================================
   FREE-MOVING ORGANIC LAVA-LAMP SPINE, MORPHING ORBS & BRANCH ARCS
   ========================================================================== */

function hexToRgb(hex) {
  const clean = (hex || '#ffa159').replace('#', '');
  const n = parseInt(clean, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mixRgb(a, b, ratio) {
  return [
    Math.round(a[0] + (b[0] - a[0]) * ratio),
    Math.round(a[1] + (b[1] - a[1]) * ratio),
    Math.round(a[2] + (b[2] - a[2]) * ratio),
  ];
}

function drawOrganicOrb(ctx, x, y, t, seed, { selected, isFork, isShortfall, tintHex, maxHalo }) {
  const pulse = organicWave(t * 0.65, seed);
  const scale = (selected ? 1.24 : isFork ? 1.12 : 1.0) * (1 + pulse * 0.08);
  const baseR = 9.5 * scale;

  const baseRgb = isShortfall ? [255, 107, 91] : hexToRgb(tintHex || '#ff9446');
  const auraColor = baseRgb;
  const midColor = mixRgb(baseRgb, [255, 245, 235], 0.42);
  const coreColor = mixRgb(baseRgb, [255, 252, 248], 0.82);

  // 1. Soft astral outer bloom
  const haloR = Math.min(maxHalo, (selected ? 36 : 28) * (1 + pulse * 0.12));
  const haloGrad = ctx.createRadialGradient(x, y, 0, x, y, haloR);
  haloGrad.addColorStop(0, `rgba(${midColor.join(',')}, ${selected ? 0.52 : 0.35})`);
  haloGrad.addColorStop(0.45, `rgba(${auraColor.join(',')}, ${selected ? 0.28 : 0.16})`);
  haloGrad.addColorStop(1, `rgba(${auraColor.join(',')}, 0)`);
  ctx.fillStyle = haloGrad;
  ctx.beginPath();
  ctx.arc(x, y, haloR, 0, Math.PI * 2);
  ctx.fill();

  // 2. Smoothly morphing liquid-light body (gentle low-order waves — never spiky or patterned)
  const steps = 40;
  const p1 = t * 0.43 + seed * 1.9;
  const p2 = -t * 0.29 + seed * 3.1;
  const p3 = t * 0.67 + seed * 0.7;

  const pts = [];
  for (let i = 0; i < steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    const wobble =
      1 +
      0.16 * Math.sin(2 * a + p1) +
      0.13 * Math.cos(3 * a + p2) +
      0.07 * Math.sin(5 * a + p3);
    const r = baseR * wobble;
    pts.push({ px: x + Math.cos(a) * r, py: y + Math.sin(a) * r });
  }

  const bodyGrad = ctx.createRadialGradient(x, y, 0, x, y, baseR * 1.45);
  bodyGrad.addColorStop(0, `rgba(${coreColor.join(',')}, 0.96)`);
  bodyGrad.addColorStop(0.5, `rgba(${midColor.join(',')}, 0.62)`);
  bodyGrad.addColorStop(0.86, `rgba(${auraColor.join(',')}, 0.20)`);
  bodyGrad.addColorStop(1, `rgba(${auraColor.join(',')}, 0)`);

  ctx.fillStyle = bodyGrad;
  ctx.beginPath();
  for (let i = 0; i < steps; i++) {
    const curr = pts[i];
    const next = pts[(i + 1) % steps];
    const mx = (curr.px + next.px) * 0.5;
    const my = (curr.py + next.py) * 0.5;
    if (i === 0) ctx.moveTo(mx, my);
    else ctx.quadraticCurveTo(curr.px, curr.py, mx, my);
  }
  ctx.closePath();
  ctx.fill();

  // 3. Bright warm-ivory core
  const coreR = (selected ? 5.6 : 4.5) * (1 + pulse * 0.06);
  const coreGrad = ctx.createRadialGradient(x, y, 0, x, y, coreR * 1.6);
  coreGrad.addColorStop(0, '#ffffff');
  coreGrad.addColorStop(0.55, `rgba(${coreColor.join(',')}, 0.92)`);
  coreGrad.addColorStop(1, `rgba(${midColor.join(',')}, 0)`);
  ctx.fillStyle = coreGrad;
  ctx.beginPath();
  ctx.arc(x, y, coreR * 1.6, 0, Math.PI * 2);
  ctx.fill();
}

function evalCubicBezier(p0, p1, p2, p3, u) {
  const m = 1 - u;
  return (
    m * m * m * p0 +
    3 * m * m * u * p1 +
    3 * m * u * u * p2 +
    u * u * u * p3
  );
}

function drawSpineFrame(nowMs) {
  const shell = byId('vtimeline-shell');
  const canvas = byId('vtimeline-canvas');
  if (!shell || !canvas || state.stage !== 'results' || state.resultsView !== 'timeline') {
    state.spineRaf = null;
    return;
  }

  const listItems = shell.querySelectorAll('#vertical-timeline > .vnode');
  if (!listItems.length) {
    state.spineRaf = null;
    return;
  }

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = canvas.clientWidth || 150;
  const cssH = shell.scrollHeight;
  const targetW = Math.round(cssW * dpr);
  const targetH = Math.round(cssH * dpr);
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW;
    canvas.height = targetH;
  }

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dt = state.spineLastMs ? Math.min(0.06, Math.max(0.004, (nowMs - state.spineLastMs) * 0.001)) : 0.016;
  state.spineLastMs = nowMs;
  const t = reduced ? 2.1 : nowMs * 0.001;
  const baseCenterX = cssW > 110 ? 60 : cssW * 0.40;
  const shellRect = shell.getBoundingClientRect();

  // Gently meandering, free-moving orb positions along the vertical journey
  const points = [];
  listItems.forEach((li, i) => {
    const meta = state.spineNodesMeta[i];
    if (!meta) return;
    const baseY = li.offsetTop + 32;
    const meanderX = Math.sin(i * 0.85 + 0.4) * Math.min(14, cssW * 0.10);
    const driftX = organicWave(t * 0.46, meta.seed) * 4.5;
    const driftY = organicWave(t * 0.39, meta.seed + 5.7) * 2.6;
    const forkTargets = [...li.querySelectorAll('.vnode-fork .fork-dest-btn')].map(btn => {
      const rect = btn.getBoundingClientRect();
      return {
        branchId: btn.dataset.branchId || '',
        colorHex: btn.dataset.branchColor || '#ffa159',
        isCurrent: btn.classList.contains('is-current'),
        endX: Math.min(cssW - 2, Math.max(baseCenterX + 36, rect.left - shellRect.left + 1)),
        endY: rect.top - shellRect.top + rect.height / 2,
      };
    });

    points.push({
      x: baseCenterX + meanderX + driftX,
      y: baseY + driftY,
      meta,
      selected: meta.id === state.selectedId,
      forkTargets,
    });
  });

  // Active choreographed branch-swap animation state (if a fork swap is playing)
  let swap = state.branchSwap;
  let swapP = 1;
  if (swap) {
    const rawP = reduced ? 1 : (nowMs - swap.startMs) / swap.duration;
    if (rawP >= 1) {
      // Seed resting weights cleanly so handoff to steady arcs has zero pop
      const doneAnim = state.forkArcAnim.get(swap.forkNodeId);
      if (doneAnim) {
        doneAnim.weights.set(swap.fromBranchId, 0.84);
        doneAnim.weights.set(swap.toBranchId, 0);
      }
      state.branchSwap = null;
      swap = null;
    } else {
      const u = Math.max(0, rawP);
      // Smooth easeOutCubic matching the CSS FLIP cubic-bezier(0.22, 1, 0.36, 1) so curves and sliding cards move in lockstep
      swapP = 1 - Math.pow(1 - u, 3);
    }
  }
  const swapForkIndex = swap ? points.findIndex(pt => pt.meta.id === swap.forkNodeId) : -1;
  const swapFromRgb = swap ? hexToRgb(swap.fromColor) : null;
  const swapToRgb = swap ? hexToRgb(swap.toColor) : null;

  // 1. Undulating lava-lamp filament connecting consecutive orbs (with live color-wave transition during branch swap!)
  if (points.length > 1) {
    const topY = points[0].y;
    const botY = points[points.length - 1].y;
    const swapForkPt = swapForkIndex >= 0 ? points[swapForkIndex] : null;
    const waveSpan = swapForkPt ? Math.max(220, Math.min(680, botY - swapForkPt.y)) : 0;
    const waveY = swapForkPt ? swapForkPt.y + swapP * waveSpan : 0;

    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i];
      const b = points[i + 1];
      let segRgb = hexToRgb(b.meta.tintHex || '#ffa159');
      if (swap && swapForkIndex >= 0 && i >= swapForkIndex) {
        const segMidY = (a.y + b.y) * 0.5;
        const blend = Math.max(0, Math.min(1, (waveY - (segMidY - 90)) / 180));
        segRgb = mixRgb(swapFromRgb, swapToRgb, blend);
      }
      const segMid = mixRgb(segRgb, [255, 244, 232], 0.45);
      const span = Math.max(1, b.y - a.y);
      const steps = Math.max(10, Math.ceil(span / 10));

      const traceSegment = () => {
        ctx.beginPath();
        for (let s = 0; s <= steps; s++) {
          const u = s / steps;
          const y = a.y + span * u;
          const linX = a.x + (b.x - a.x) * u;
          const env = Math.sin(Math.PI * u);
          const sway = organicWave(t * 0.68 - y * 0.0048, i * 2.93 + 0.6) * 11 * env;
          const x = linX + sway;
          if (s === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
      };

      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      traceSegment();
      ctx.strokeStyle = `rgba(${segRgb.join(',')}, ${b.meta.isDiverged ? 0.24 : 0.17})`;
      ctx.lineWidth = 10;
      ctx.stroke();

      traceSegment();
      ctx.strokeStyle = `rgba(${segMid.join(',')}, ${b.meta.isDiverged ? 0.72 : 0.52})`;
      ctx.lineWidth = b.meta.isDiverged ? 2.1 : 1.8;
      ctx.stroke();
    }

    // Bright color-wave surge racing down the spine during a branch swap
    if (swap && swapForkPt && waveY <= botY) {
      let wx = swapForkPt.x;
      for (let i = swapForkIndex; i < points.length - 1; i++) {
        const a = points[i];
        const b = points[i + 1];
        if (waveY >= a.y && waveY <= b.y) {
          const u = (waveY - a.y) / Math.max(1, b.y - a.y);
          const env = Math.sin(Math.PI * u);
          wx = a.x + (b.x - a.x) * u + organicWave(t * 0.68 - waveY * 0.0048, i * 2.93 + 0.6) * 11 * env;
          break;
        }
      }
      const surgeFade = Math.sin(Math.PI * swapP);
      const surgeGrad = ctx.createRadialGradient(wx, waveY, 0, wx, waveY, 32);
      surgeGrad.addColorStop(0, `rgba(255, 252, 246, ${0.98 * surgeFade})`);
      surgeGrad.addColorStop(0.35, `rgba(${swapToRgb.join(',')}, ${0.78 * surgeFade})`);
      surgeGrad.addColorStop(1, `rgba(${swapToRgb.join(',')}, 0)`);
      ctx.fillStyle = surgeGrad;
      ctx.beginPath();
      ctx.arc(wx, waveY, 32, 0, Math.PI * 2);
      ctx.fill();
    }

    // Subtle slow lava-lamp warm light droplets drifting down the filament
    if (!reduced) {
      const totalSpan = Math.max(1, botY - topY);
      const dropletCount = Math.min(5, Math.max(2, Math.floor(points.length / 4)));
      for (let d = 0; d < dropletCount; d++) {
        const speed = 0.018 + d * 0.0061;
        const rawPos = (t * speed + d * 0.37) % 1;
        const dy = topY + rawPos * totalSpan;
        let dx = baseCenterX;
        for (let i = 0; i < points.length - 1; i++) {
          const a = points[i];
          const b = points[i + 1];
          if (dy >= a.y && dy <= b.y) {
            const u = (dy - a.y) / Math.max(1, b.y - a.y);
            const env = Math.sin(Math.PI * u);
            dx = a.x + (b.x - a.x) * u + organicWave(t * 0.68 - dy * 0.0048, i * 2.93 + 0.6) * 11 * env;
            break;
          }
        }
        const fade = Math.sin(Math.PI * rawPos);
        const dropGrad = ctx.createRadialGradient(dx, dy, 0, dx, dy, 12);
        dropGrad.addColorStop(0, `rgba(255, 238, 216, ${0.52 * fade})`);
        dropGrad.addColorStop(0.5, `rgba(255, 152, 76, ${0.2 * fade})`);
        dropGrad.addColorStop(1, 'rgba(255, 138, 61, 0)');
        ctx.fillStyle = dropGrad;
        ctx.beginPath();
        ctx.arc(dx, dy, 12, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  // 2. Sweeping animated side-branch arcs curving from each fork orb directly to every alternative option
  const damp = reduced ? 1 : Math.min(1, dt * 10.5);
  for (const pt of points) {
    if (!pt.meta.isFork || !pt.forkTargets.length) continue;
    const hoveredBranch = state.forkHover?.nodeId === pt.meta.id ? state.forkHover.branchId : null;
    const isSwappingHere = Boolean(swap && swap.forkNodeId === pt.meta.id);
    let anim = state.forkArcAnim.get(pt.meta.id);
    if (!anim) {
      anim = { weights: new Map(), glideX: null, glideY: null, glideAlpha: 0, glideRgb: [255, 161, 89] };
      state.forkArcAnim.set(pt.meta.id, anim);
    }

    let hoveredTarget = null;
    for (const [idx, tgt] of pt.forkTargets.entries()) {
      const isHovered = Boolean(hoveredBranch && tgt.branchId === hoveredBranch);
      if (isHovered) hoveredTarget = tgt;

      // During a swap at this fork, let the choreographed peel-out / pull-in curves handle the two swapping branches
      if (isSwappingHere && (tgt.branchId === swap.fromBranchId || tgt.branchId === swap.toBranchId)) {
        continue;
      }

      // At rest, side branches connect strictly to alternative (non-current) options; hovering smoothly spotlights any option
      const targetW = hoveredBranch
        ? (isHovered ? 1 : (tgt.isCurrent ? 0 : 0.22))
        : (tgt.isCurrent ? 0 : 0.84);
      const prevW = anim.weights.has(tgt.branchId) ? anim.weights.get(tgt.branchId) : targetW;
      const w = prevW + (targetW - prevW) * damp;
      anim.weights.set(tgt.branchId, w);
      if (w < 0.015) continue;

      const rgb = hexToRgb(tgt.colorHex);
      const hiRgb = mixRgb(rgb, [255, 244, 232], 0.45);
      const dy = tgt.endY - pt.y;
      const sway = organicWave(t * 0.55, pt.meta.seed + 9.2 + idx * 2.1) * 3.5;

      const cp1x = pt.x + 2 + sway * 0.5;
      const cp1y = pt.y + dy * 0.62;
      const cp2x = pt.x + (tgt.endX - pt.x) * 0.36;
      const cp2y = tgt.endY - sway * 0.35;

      const arcGrad = ctx.createLinearGradient(pt.x, pt.y, tgt.endX, tgt.endY);
      arcGrad.addColorStop(0, `rgba(${hiRgb.join(',')}, ${0.86 * w})`);
      arcGrad.addColorStop(0.6, `rgba(${rgb.join(',')}, ${0.76 * w})`);
      arcGrad.addColorStop(1, `rgba(${hiRgb.join(',')}, ${0.88 * w})`);

      // Wide soft branch aura
      ctx.beginPath();
      ctx.moveTo(pt.x, pt.y);
      ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, tgt.endX, tgt.endY);
      ctx.strokeStyle = `rgba(${rgb.join(',')}, ${0.22 * w})`;
      ctx.lineWidth = 7 + 4.5 * w;
      ctx.lineCap = 'round';
      ctx.stroke();

      // Crisp glowing branch stream
      ctx.beginPath();
      ctx.moveTo(pt.x, pt.y);
      ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, tgt.endX, tgt.endY);
      ctx.strokeStyle = arcGrad;
      ctx.lineWidth = 1.6 + 0.85 * w;
      ctx.stroke();

      // Luminous docking pearl where the branch kisses this exact option card
      const pearlGrad = ctx.createRadialGradient(tgt.endX, tgt.endY, 0, tgt.endX, tgt.endY, 10 * w);
      pearlGrad.addColorStop(0, `rgba(255, 246, 236, ${0.95 * w})`);
      pearlGrad.addColorStop(0.45, `rgba(${rgb.join(',')}, ${0.55 * w})`);
      pearlGrad.addColorStop(1, `rgba(${rgb.join(',')}, 0)`);
      ctx.fillStyle = pearlGrad;
      ctx.beginPath();
      ctx.arc(tgt.endX, tgt.endY, 10 * w, 0, Math.PI * 2);
      ctx.fill();

      // Flowing star-droplet gliding from the fork orb straight along the curve into the option
      if (!reduced && w > 0.35) {
        const flowSpeed = isHovered ? 0.62 : 0.28;
        const u = (t * flowSpeed + idx * 0.39) % 1;
        const dx = evalCubicBezier(pt.x, cp1x, cp2x, tgt.endX, u);
        const dyPos = evalCubicBezier(pt.y, cp1y, cp2y, tgt.endY, u);
        const fade = Math.sin(Math.PI * u) * w;
        const radius = isHovered ? 11 : 8;
        const cometGrad = ctx.createRadialGradient(dx, dyPos, 0, dx, dyPos, radius);
        cometGrad.addColorStop(0, `rgba(255, 250, 242, ${0.92 * fade})`);
        cometGrad.addColorStop(0.45, `rgba(${rgb.join(',')}, ${0.55 * fade})`);
        cometGrad.addColorStop(1, `rgba(${rgb.join(',')}, 0)`);
        ctx.fillStyle = cometGrad;
        ctx.beginPath();
        ctx.arc(dx, dyPos, radius, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Choreographed branch swap: old path peels OUTWARD to its sliding card while chosen path pulls INWARD into the main spine!
    if (isSwappingHere) {
      // Suppress hover spotlight completely during swap so nothing blocks the animation
      hoveredTarget = null;
      anim.glideAlpha = 0;
      anim.glideY = null;

      const fromTgt = pt.forkTargets.find(item => item.branchId === swap.fromBranchId);
      const toTgt = pt.forkTargets.find(item => item.branchId === swap.toBranchId);

      // 1) Double expanding astral ripple ring at the fork orb
      const rippleAlpha = (1 - swapP) * 0.85;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 10 + swapP * 52, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${swapToRgb.join(',')}, ${rippleAlpha})`;
      ctx.lineWidth = 2.8 * (1 - swapP * 0.45);
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 6 + swapP * 30, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(255, 248, 240, ${rippleAlpha * 0.72})`;
      ctx.lineWidth = 1.8 * (1 - swapP * 0.45);
      ctx.stroke();

      // 2) Chosen branch sweeps boldly INWARD from its clicked spot into the vertical spine
      if (toTgt) {
        const pullAlpha = Math.max(0, 1 - Math.pow(swapP, 1.5) * 0.94);
        const startX = swap.toStartX ?? toTgt.endX;
        const startY = swap.toStartY ?? toTgt.endY;
        const curEndX = startX + (pt.x - startX) * swapP;
        const curEndY = startY + Math.min(110, Math.max(36, (startY - pt.y) * 0.42)) * swapP;
        const dy = curEndY - pt.y;
        const hiTo = mixRgb(swapToRgb, [255, 248, 240], 0.6);

        ctx.beginPath();
        ctx.moveTo(pt.x, pt.y);
        ctx.bezierCurveTo(pt.x + 2, pt.y + dy * 0.62, pt.x + (curEndX - pt.x) * 0.36, curEndY, curEndX, curEndY);
        ctx.strokeStyle = `rgba(${swapToRgb.join(',')}, ${0.42 * pullAlpha})`;
        ctx.lineWidth = 18 * pullAlpha;
        ctx.lineCap = 'round';
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(pt.x, pt.y);
        ctx.bezierCurveTo(pt.x + 2, pt.y + dy * 0.62, pt.x + (curEndX - pt.x) * 0.36, curEndY, curEndX, curEndY);
        ctx.strokeStyle = `rgba(${hiTo.join(',')}, ${0.98 * pullAlpha})`;
        ctx.lineWidth = 3.5;
        ctx.stroke();

        // Blazing star-comet riding the inward-pulling tip straight into the spine
        const tipGrad = ctx.createRadialGradient(curEndX, curEndY, 0, curEndX, curEndY, 18 * pullAlpha);
        tipGrad.addColorStop(0, `rgba(255, 252, 246, ${0.98 * pullAlpha})`);
        tipGrad.addColorStop(0.42, `rgba(${swapToRgb.join(',')}, ${0.72 * pullAlpha})`);
        tipGrad.addColorStop(1, `rgba(${swapToRgb.join(',')}, 0)`);
        ctx.fillStyle = tipGrad;
        ctx.beginPath();
        ctx.arc(curEndX, curEndY, 18 * pullAlpha, 0, Math.PI * 2);
        ctx.fill();
      }

      // 3) Previous branch peels boldly OUTWARD from the spine and live-tracks its card sliding into place
      if (fromTgt) {
        const peelW = 0.25 + 0.59 * swapP; // lands cleanly at 0.84 to match steady-state curve
        const curEndX = pt.x + (fromTgt.endX - pt.x) * swapP;
        const curEndY = fromTgt.endY;
        const dy = curEndY - pt.y;
        const hiFrom = mixRgb(swapFromRgb, [255, 244, 232], 0.5);

        const peelGrad = ctx.createLinearGradient(pt.x, pt.y, curEndX, curEndY);
        peelGrad.addColorStop(0, `rgba(${hiFrom.join(',')}, ${0.92 * peelW})`);
        peelGrad.addColorStop(0.6, `rgba(${swapFromRgb.join(',')}, ${0.84 * peelW})`);
        peelGrad.addColorStop(1, `rgba(${hiFrom.join(',')}, ${0.94 * peelW})`);

        ctx.beginPath();
        ctx.moveTo(pt.x, pt.y);
        ctx.bezierCurveTo(pt.x + 2, pt.y + dy * 0.62, pt.x + (curEndX - pt.x) * 0.36, curEndY, curEndX, curEndY);
        ctx.strokeStyle = `rgba(${swapFromRgb.join(',')}, ${0.30 * peelW})`;
        ctx.lineWidth = 9 + 6 * peelW;
        ctx.lineCap = 'round';
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(pt.x, pt.y);
        ctx.bezierCurveTo(pt.x + 2, pt.y + dy * 0.62, pt.x + (curEndX - pt.x) * 0.36, curEndY, curEndX, curEndY);
        ctx.strokeStyle = peelGrad;
        ctx.lineWidth = 2.1 + 1.0 * peelW;
        ctx.stroke();

        // Bright unfolding comet head that settles smoothly into the docking pearl (radius 10 * 0.84)
        const pearlR = (10 + 7 * Math.sin(Math.PI * swapP)) * peelW;
        const pearlGrad = ctx.createRadialGradient(curEndX, curEndY, 0, curEndX, curEndY, pearlR);
        pearlGrad.addColorStop(0, `rgba(255, 248, 240, ${0.98 * peelW})`);
        pearlGrad.addColorStop(0.45, `rgba(${swapFromRgb.join(',')}, ${0.65 * peelW})`);
        pearlGrad.addColorStop(1, `rgba(${swapFromRgb.join(',')}, 0)`);
        ctx.fillStyle = pearlGrad;
        ctx.beginPath();
        ctx.arc(curEndX, curEndY, pearlR, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Smooth gliding spotlight filament that sweeps effortlessly between options as you hover across them
    if (hoveredTarget) {
      const rgb = hexToRgb(hoveredTarget.colorHex);
      if (anim.glideY === null) {
        anim.glideX = hoveredTarget.endX;
        anim.glideY = hoveredTarget.endY;
        anim.glideRgb = rgb;
      } else {
        const glideDamp = reduced ? 1 : Math.min(1, dt * 13);
        anim.glideX += (hoveredTarget.endX - anim.glideX) * glideDamp;
        anim.glideY += (hoveredTarget.endY - anim.glideY) * glideDamp;
        anim.glideRgb = [
          anim.glideRgb[0] + (rgb[0] - anim.glideRgb[0]) * glideDamp,
          anim.glideRgb[1] + (rgb[1] - anim.glideRgb[1]) * glideDamp,
          anim.glideRgb[2] + (rgb[2] - anim.glideRgb[2]) * glideDamp,
        ];
      }
      anim.glideAlpha += (1 - anim.glideAlpha) * damp;
    } else {
      anim.glideAlpha += (0 - anim.glideAlpha) * damp;
      if (anim.glideAlpha < 0.02) anim.glideY = null;
    }

    if (anim.glideY !== null && anim.glideAlpha > 0.03) {
      const gRgb = anim.glideRgb.map(Math.round);
      const gHi = mixRgb(gRgb, [255, 248, 240], 0.55);
      const dy = anim.glideY - pt.y;
      ctx.beginPath();
      ctx.moveTo(pt.x, pt.y);
      ctx.bezierCurveTo(
        pt.x + 2,
        pt.y + dy * 0.62,
        pt.x + (anim.glideX - pt.x) * 0.36,
        anim.glideY,
        anim.glideX,
        anim.glideY
      );
      ctx.strokeStyle = `rgba(${gHi.join(',')}, ${0.52 * anim.glideAlpha})`;
      ctx.lineWidth = 2.6;
      ctx.lineCap = 'round';
      ctx.stroke();
    }
  }

  // 3. Draw glowing morphing orbs at each milestone
  for (const pt of points) {
    const maxHalo = Math.max(18, Math.min(pt.x - 1, cssW - pt.x - 1));
    drawOrganicOrb(ctx, pt.x, pt.y, t, pt.meta.seed, {
      selected: pt.selected,
      isFork: pt.meta.isFork,
      isShortfall: pt.meta.isShortfall,
      tintHex: pt.meta.tintHex,
      maxHalo,
    });
  }

  if (!reduced && !document.hidden) {
    state.spineRaf = requestAnimationFrame(drawSpineFrame);
  } else {
    state.spineRaf = null;
  }
}

function startSpineAnimation() {
  if (state.spineRaf) cancelAnimationFrame(state.spineRaf);
  state.spineRaf = requestAnimationFrame(drawSpineFrame);
}

function stopSpineAnimation() {
  if (state.spineRaf) {
    cancelAnimationFrame(state.spineRaf);
    state.spineRaf = null;
  }
}

/* ==========================================================================
   STAGE, VIEW & DRAWER NAVIGATION
   ========================================================================== */

function setPathsPanelOpen(open) {
  const panel = byId('tree-controls-panel');
  const btn = byId('toggle-paths-panel');
  if (!panel || !btn) return;
  panel.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
  if (open) setDrawerOpen(false);
}

function setDrawerOpen(open) {
  state.drawerOpen = open;
  const inResults = state.stage === 'results';
  const active = open && inResults;
  document.body.classList.toggle('drawer-open', active);
  // Backdrop dims only in Timeline mode (in Full Tree mode, you can keep panning/clicking nodes directly!)
  byId('drawer-backdrop').hidden = !(active && state.resultsView === 'timeline');
  if (active && state.resultsView === 'tree') {
    const panel = byId('tree-controls-panel');
    const btn = byId('toggle-paths-panel');
    if (panel) panel.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }
}

function mountInspectorForView() {
  const panel = byId('inspector-panel');
  const results = byId('results');
  const target = state.resultsView === 'tree' ? byId('tree-inspector-slot') : results;
  if (panel.parentElement !== target) {
    if (state.resultsView === 'timeline') {
      Object.assign(panel.style, { left: '', top: '', right: '', bottom: '', width: '', maxHeight: '' });
      panel.classList.remove('minimized');
      const toggle = panel.querySelector('.panel-toggle');
      if (toggle) {
        toggle.textContent = '–';
        toggle.setAttribute('aria-expanded', 'true');
      }
    }
    target.append(panel);
  }
}

function setResultsView(mode, { focusNodeId = null } = {}) {
  state.resultsView = mode;
  const isTree = mode === 'tree';
  byId('timeline-view').hidden = isTree;
  byId('tree-view').hidden = !isTree;
  document.body.classList.toggle('world-mode', state.stage === 'results' && isTree);
  if (isTree) {
    setDrawerOpen(false);
    setPathsPanelOpen(false);
  }
  mountInspectorForView();

  if (focusNodeId && state.nodes.has(focusNodeId)) {
    state.selectedId = focusNodeId;
  }

  if (isTree) {
    stopSpineAnimation();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    requestAnimationFrame(() => {
      renderTreeGraph({ focusId: focusNodeId, fit: true });
      if (state.selectedId) {
        const selected = state.nodes.get(state.selectedId);
        renderInspector(selected);
        updateFollowPathButton(selected);
      }
    });
  } else {
    renderTimelineView();
    if (focusNodeId) scrollMilestoneIntoView(focusNodeId);
  }
}

function showStage(id) {
  state.stage = id;
  for (const stage of ['intake', 'tuning', 'results']) {
    byId(stage).hidden = stage !== id;
  }
  document.body.classList.toggle('world-mode', id === 'results' && state.resultsView === 'tree');
  if (id === 'results') {
    mountInspectorForView();
    if (state.resultsView === 'timeline') startSpineAnimation();
  } else {
    setDrawerOpen(false);
    stopSpineAnimation();
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ==========================================================================
   NETWORK & API STATUS
   ========================================================================== */

async function request(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error('The server returned an unexpected response.');
  }
  if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}

/* ==========================================================================
   TREE & PATH TOPOLOGY HELPERS
   ========================================================================== */

function buildTreeIndex() {
  const childrenByParent = new Map();
  const nodesByBranch = new Map();
  let root = null;

  for (const node of state.nodes.values()) {
    if (node.parent_id === null) root = node;
    else {
      if (!childrenByParent.has(node.parent_id)) childrenByParent.set(node.parent_id, []);
      childrenByParent.get(node.parent_id).push(node);
    }
    if (!nodesByBranch.has(node.branch_id)) nodesByBranch.set(node.branch_id, []);
    nodesByBranch.get(node.branch_id).push(node);
  }

  for (const list of nodesByBranch.values()) list.sort((a, b) => a.month - b.month);
  for (const list of childrenByParent.values()) {
    list.sort((a, b) => {
      if (a.branch_id === 'main') return -1;
      if (b.branch_id === 'main') return 1;
      return a.month - b.month;
    });
  }

  const branchIds = [...nodesByBranch.keys()].filter(id => id !== 'root');
  if (!branchIds.length && root) branchIds.push('root');
  branchIds.sort((a, b) => {
    if (a === 'main') return -1;
    if (b === 'main') return 1;
    const firstA = nodesByBranch.get(a)[0]?.month ?? 0;
    const firstB = nodesByBranch.get(b)[0]?.month ?? 0;
    return firstA - firstB;
  });

  const labelCounts = new Map();
  const branchMeta = new Map();
  for (const bid of branchIds) {
    const bNodes = nodesByBranch.get(bid);
    const first = bNodes[0];
    const tip = bNodes[bNodes.length - 1];
    let baseLabel = bid === 'main' || bid === 'root'
      ? 'Original path'
      : (first.event_narrative || first.event_summary || 'Alternate path');
    if (baseLabel.length > 44) baseLabel = `${baseLabel.slice(0, 42).trim()}…`;
    const count = (labelCounts.get(baseLabel) || 0) + 1;
    labelCounts.set(baseLabel, count);
    const label = count > 1 ? `${baseLabel} (${count})` : baseLabel;

    const chain = [];
    let cur = tip;
    while (cur) {
      chain.push(cur);
      cur = cur.parent_id ? state.nodes.get(cur.parent_id) : null;
    }
    chain.reverse();

    const byMonth = new Map();
    for (const item of chain) byMonth.set(item.month, item);

    branchMeta.set(bid, {
      id: bid,
      code: bid === 'main' || bid === 'root' ? 'P0' : '',
      color: BRANCH_PALETTE[0],
      label,
      firstNode: first,
      tipNode: tip,
      forkMonth: first.parent_id ? state.nodes.get(first.parent_id)?.month ?? 0 : 0,
      forkParentId: first.parent_id,
      parentBranchId: null,
      chain,
      byMonth,
    });
  }

  for (const meta of branchMeta.values()) {
    if (meta.id === 'main' || meta.id === 'root' || !meta.forkParentId) continue;
    const rawParent = state.nodes.get(meta.forkParentId)?.branch_id || null;
    if (!rawParent || rawParent === meta.id) continue;
    const resolvedParent = rawParent === 'root' ? (branchMeta.has('main') ? 'main' : null) : rawParent;
    if (resolvedParent && branchMeta.has(resolvedParent)) {
      meta.parentBranchId = resolvedParent;
    }
  }

  // Assign clean hierarchical path IDs (P0, P1, P2, P1.1...) & one unified color per branch across ALL views
  const childMetaByParent = new Map();
  const topLevelBranches = [];
  for (const meta of branchMeta.values()) {
    if (meta.id === 'main' || meta.id === 'root') continue;
    if (meta.parentBranchId && branchMeta.has(meta.parentBranchId)) {
      if (!childMetaByParent.has(meta.parentBranchId)) childMetaByParent.set(meta.parentBranchId, []);
      childMetaByParent.get(meta.parentBranchId).push(meta);
    } else {
      topLevelBranches.push(meta);
    }
  }
  const sortKids = list => list.sort((a, b) => (a.forkMonth - b.forkMonth) || a.id.localeCompare(b.id));
  sortKids(topLevelBranches);
  for (const kids of childMetaByParent.values()) sortKids(kids);

  let nextColorIdx = 1;
  const assignCodes = (list, prefix) => {
    list.forEach((meta, idx) => {
      meta.code = prefix ? `${prefix}.${idx + 1}` : `P${idx + 1}`;
      meta.color = BRANCH_PALETTE[nextColorIdx % BRANCH_PALETTE.length];
      nextColorIdx += 1;
      const kids = childMetaByParent.get(meta.id);
      if (kids?.length) assignCodes(kids, meta.code);
    });
  };
  const rootMeta = branchMeta.get('main') || branchMeta.get('root');
  if (rootMeta) {
    rootMeta.code = 'P0';
    rootMeta.color = BRANCH_PALETTE[0];
    assignCodes([...(childMetaByParent.get(rootMeta.id) || []), ...topLevelBranches], '');
  } else {
    assignCodes(topLevelBranches, '');
  }

  const forkNodeIds = new Set();
  for (const [parentId, kids] of childrenByParent.entries()) {
    if (kids.length > 1) forkNodeIds.add(parentId);
  }

  const baselineByMonth = new Map();
  const mainChain = branchMeta.get('main')?.chain || branchMeta.get('root')?.chain || [];
  for (const node of mainChain) baselineByMonth.set(node.month, node);

  return { root, childrenByParent, nodesByBranch, branchIds, branchMeta, forkNodeIds, baselineByMonth };
}

function shortBranchLabel(label, max = 26) {
  const text = String(label || 'parent path');
  return text.length > max ? `${text.slice(0, max - 1).trim()}…` : text;
}

function getParentComparison(branchId, month, index) {
  const meta = index.branchMeta.get(branchId);
  const parentMeta = meta?.parentBranchId ? index.branchMeta.get(meta.parentBranchId) : null;
  if (!parentMeta) return { parentMeta: null, parentNode: null };
  return { parentMeta, parentNode: parentMeta.byMonth.get(month) || null };
}

const shortMonthFmt = new Intl.DateTimeFormat('en-GB', { month: 'short', year: 'numeric' });
const fullDateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

function formatNiceDate(isoDate, { includeDay = false } = {}) {
  const parts = String(isoDate || '').split('-').map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return String(isoDate || '');
  const d = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  return (includeDay ? fullDateFmt : shortMonthFmt).format(d);
}

function formatMonthTime(month) {
  if (month === 0) return 'Month 0';
  if (month < 12) return `Month ${month}`;
  const yrs = Math.floor(month / 12);
  const rem = month % 12;
  return rem === 0 ? `Year ${yrs}` : `Year ${yrs} · ${rem} mo`;
}

function milestoneTitle(node) {
  if (node.month === 0) return 'Starting point';
  if (node.event_summary && node.event_summary !== 'Monthly update') return node.event_summary;
  if (node.month % 12 === 0) return `Year ${node.month / 12}`;
  if (node.month % 6 === 0) return node.month === 6 ? '6 months' : `Year ${(node.month / 12).toFixed(1)}`;
  return `Month ${node.month}`;
}

function buildMilestoneProse(node, prevNode) {
  const s = node.state;
  const f = s.cash_flow;
  if (node.month === 0) {
    const parts = [`You start with ${money(s.cash_chf)} in cash`];
    const invested = s.risk_exposure?.investment_value_chf || 0;
    if (invested > 0) parts.push(`${money(invested)} invested`);
    if (s.total_debt_chf > 0) parts.push(`${money(s.total_debt_chf)} in debt`);
    const phase = s.active_variables?.phase;
    const budget = phase
      ? ` Monthly net income is ${money(phase.monthly_income_chf)} with ${money(s.active_variables.expense_terms?.monthly_expenses_chf ?? phase.monthly_expenses_chf)} in living expenses at the start of the year.`
      : '';
    return `${parts.join(', ')}.${budget}`;
  }

  const parts = [];
  const op = f.operating_cash_flow_chf ?? (f.income_chf - f.expenses_chf - f.one_off_costs_chf - f.debt_payments_chf);
  parts.push(
    `Earning ${money(f.income_chf)}/mo with ${money(f.expenses_chf)}/mo in living costs` +
    (f.debt_payments_chf > 0 ? ` and ${money(f.debt_payments_chf)}/mo toward debt` : '') +
    (f.one_off_costs_chf > 0 ? `, alongside a ${money(f.one_off_costs_chf)} one-off cost` : '') +
    `.`
  );

  if (f.investment_contribution_chf > 0) {
    parts.push(`${money(f.investment_contribution_chf)} flows into investments this month.`);
  } else if (op < 0) {
    parts.push(`Monthly cash flow runs at ${moneySigned(op)}.`);
  }

  if (prevNode) {
    const diff = s.net_worth_chf - prevNode.state.net_worth_chf;
    if (Math.abs(diff) >= 50) {
      parts.push(`Net worth has shifted ${moneySigned(diff)} since ${prevNode.date}.`);
    }
  }
  return parts.join(' ');
}

function getPathMilestones(chain, index) {
  if (!chain.length) return [];
  const tip = chain[chain.length - 1];

  return chain.filter(node => {
    if (node.month === 0 || node.id === tip.id) return true;
    if (node.month % 6 === 0) return true;
    if (index.forkNodeIds.has(node.id)) return true;
    if (node.event_summary && node.event_summary !== 'Monthly update') return true;
    const parent = node.parent_id ? state.nodes.get(node.parent_id) : null;
    if (parent && parent.branch_id !== node.branch_id) return true;
    return false;
  });
}

function getAllGraphMilestones(index) {
  const result = [];
  const seen = new Set();
  for (const bid of index.branchIds) {
    for (const node of getPathMilestones(index.branchMeta.get(bid).chain, index)) {
      if (!seen.has(node.id)) {
        seen.add(node.id);
        result.push(node);
      }
    }
  }
  return result;
}

/* ==========================================================================
   STAGE 2: TUNING RENDERER
   ========================================================================== */

// Assumption values are typed in display units: percentages as percent (42 = 42%), money in the chosen currency
// (converted to and from CHF, which the engine uses), months as months.
const isFractionPercent = variable => (variable.unit === '%' || variable.unit === 'percent') && Number(variable.max) <= 1;

function variableToDisplay(variable, value) {
  const number = variable.unit === 'CHF' ? chfToDisplay(value) : Number(value) * (isFractionPercent(variable) ? 100 : 1);
  return String(Number(number.toFixed(2)));
}

// Reads a typed value ("5'000", "CHF 5,000", "€5,000", "42%", "3 months") into the variable's own units
// (money in CHF); null if not a number.
function parseVariableInput(variable, text) {
  const cleaned = String(text)
    .replace(/chf|eur|usd|gbp|jpy|cny|cad|aud|sek|nok|dkk|pln|inr|zł|[a-z]{0,2}[€$£¥₹]|%|months?|[\s'’_]/gi, '')
    .replace(/,(?=\d{3}(\D|$))/g, '').replace(',', '.');
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(cleaned)) return null;
  if (variable.unit === 'CHF') return displayToChf(Number(cleaned));
  return Number(cleaned) / (isFractionPercent(variable) ? 100 : 1);
}

// Lists amounts in the story that were written in another currency and converted to CHF exactly.
function conversionText(data) {
  const items = data?.conversions || [];
  const readAs = data?.currency?.code && data.currency.code !== 'CHF'
    ? `Amounts in your story without a currency were read as ${data.currency.code}.` : '';
  if (!items.length) return readAs;
  const rate = data.currency?.date ? ` (${data.currency.source} of ${data.currency.date})` : '';
  const shown = items.slice(0, 4).map(item => `${item.written} = CHF ${item.chf.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  return [`Converted from your story${rate}: ${shown.join(', ')}${items.length > 4 ? `, and ${items.length - 4} more` : ''}.`, readAs]
    .filter(Boolean).join(' ');
}

function formatVariable(variable, value) {
  const number = Number(value);
  if (variable.unit === '%' || variable.unit === 'percent') {
    return `${Number((number * (variable.max <= 1 ? 100 : 1)).toFixed(1))}%`;
  }
  if (variable.unit === 'CHF') return money(number);
  return `${Number(number.toFixed(2))}${variable.unit ? ` ${variable.unit}` : ''}`;
}

function addBaseline(label, text) {
  const row = document.createElement('div');
  row.className = 'baseline-row';
  const name = document.createElement('span');
  name.textContent = label;
  const amount = document.createElement('strong');
  amount.textContent = text;
  row.append(name, amount);
  byId('baseline').append(row);
}

function renderTuning(data) {
  byId('scenario-summary').textContent = localizeAmounts(data.summary);
  const currencyNote = byId('currency-note');
  currencyNote.textContent = conversionText(data);
  currencyNote.hidden = !currencyNote.textContent;
  byId('assumptions').textContent = data.assumptions ? `Assumptions: ${localizeAmounts(data.assumptions)}` : '';
  byId('baseline').replaceChildren();

  const scenario = data.scenario;
  addBaseline('Starting cash', money(scenario.opening_cash_chf));
  if (scenario.phases?.length) {
    addBaseline('Monthly income', money(scenario.phases[0].monthly_income_chf));
    addBaseline('Monthly expenses', money(scenario.phases[0].monthly_expenses_chf));
  }
  const totalAssets = (scenario.assets || []).reduce((sum, a) => sum + Number(a.value_chf || 0), 0);
  if (totalAssets > 0) addBaseline('Existing assets', money(totalAssets));
  const totalDebt = (scenario.debts || []).reduce((sum, d) => sum + Number(d.principal_chf || 0), 0);
  if (totalDebt > 0) addBaseline('Existing debt', money(totalDebt));

  const container = byId('sliders');
  container.replaceChildren();
  for (const variable of data.variables) {
    const item = document.createElement('div');
    item.className = 'slider-item';

    const heading = document.createElement('div');
    heading.className = 'slider-heading';
    const label = document.createElement('label');
    label.htmlFor = `slider-${variable.id}`;
    label.textContent = variable.label;
    // Text box next to the slider: type an exact value, or drag the slider. Both stay in sync.
    const valueBox = document.createElement('div');
    valueBox.className = 'slider-entry';
    const field = document.createElement('input');
    field.type = 'text';
    field.inputMode = 'decimal';
    field.autocomplete = 'off';
    field.className = 'slider-number';
    field.id = `value-${variable.id}`;
    field.value = variableToDisplay(variable, variable.value);
    field.setAttribute('aria-label', `${variable.label} value`);
    valueBox.append(field);
    const unitText = isFractionPercent(variable) || variable.unit === 'percent' ? '%' : variable.unit;
    if (unitText) {
      const unit = document.createElement('span');
      unit.className = 'slider-unit';
      unit.textContent = unitText === 'CHF' ? currencyUnit() : unitText;
      if (unitText === 'CHF') valueBox.prepend(unit);
      else valueBox.append(unit);
    }
    heading.append(label, valueBox);

    const description = document.createElement('p');
    description.className = 'slider-description';
    description.id = `description-${variable.id}`;
    description.textContent = variable.description;

    const input = document.createElement('input');
    input.type = 'range';
    input.id = label.htmlFor;
    input.name = variable.id;
    input.min = variable.min;
    input.max = variable.max;
    const stepsFromMinimum = (Number(variable.value) - Number(variable.min)) / Number(variable.step);
    input.step = Math.abs(stepsFromMinimum - Math.round(stepsFromMinimum)) < 1e-8 ? variable.step : 'any';
    input.value = variable.value;
    input.setAttribute('aria-describedby', description.id);

    const ends = document.createElement('div');
    ends.className = 'range-ends';
    const min = document.createElement('span');
    const max = document.createElement('span');
    const showEnds = () => {
      min.textContent = formatVariable(variable, input.min);
      max.textContent = formatVariable(variable, input.max);
    };
    showEnds();
    ends.append(min, max);

    const hint = document.createElement('p');
    hint.className = 'slider-hint';
    hint.setAttribute('role', 'alert');

    input.addEventListener('input', () => {
      field.value = variableToDisplay(variable, input.value);
      field.classList.remove('is-invalid');
      field.removeAttribute('aria-invalid');
      hint.textContent = '';
    });

    // Typed values: CHF amounts may go beyond the slider (it stretches to fit), since the server accepts any
    // nonnegative amount; other values are limited to the slider's range, as the server enforces it.
    const applyTyped = ({ final }) => {
      const value = parseVariableInput(variable, field.value);
      const invalid = value === null || (variable.unit === 'CHF' && value < 0);
      field.classList.toggle('is-invalid', invalid);
      if (invalid) {
        field.setAttribute('aria-invalid', 'true');
        hint.textContent = variable.unit === 'CHF' ? 'Enter an amount of 0 or more.' : 'Enter a number.';
        return;
      }
      field.removeAttribute('aria-invalid');
      hint.textContent = '';
      let applied = value;
      if (variable.unit === 'CHF') {
        if (value > Number(input.max)) input.max = value;
        if (value < Number(input.min)) input.min = value;
        showEnds();
      } else {
        applied = Math.min(Number(variable.max), Math.max(Number(variable.min), value));
        if (applied !== value) {
          hint.textContent = `Limited to ${formatVariable(variable, variable.min)}–${formatVariable(variable, variable.max)}.`;
        }
      }
      const offStep = Math.abs((applied - Number(input.min)) / Number(variable.step) % 1) > 1e-8;
      if (offStep) input.step = 'any';
      input.value = applied;
      if (final) field.value = variableToDisplay(variable, applied);
    };
    field.addEventListener('input', () => applyTyped({ final: false }));
    field.addEventListener('change', () => applyTyped({ final: true }));
    field.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        applyTyped({ final: true });
      }
    });

    item.append(heading, description, input, ends, hint);
    container.append(item);
  }
}

/* ==========================================================================
   STAGE 3A: OPEN ASTRAL TIMELINE + CORNER MINIMAP + END SUMMARY
   ========================================================================== */

// ── Uncertainty range: how far a path could move if its estimates are off (POST /api/uncertainty) ──

function nodesKey() {
  return [...state.nodes.values()].map(n => `${n.id}:${Math.round(n.state.net_worth_chf)}:${Math.round(n.state.cash_chf)}`).sort().join('|');
}

// The range data if it is switched on and still matches the nodes on screen, otherwise null.
function currentRange() {
  if (!state.showRange || !state.range || state.rangeKey !== nodesKey()) return null;
  return state.range;
}

function rerenderForRange() {
  if (!state.nodes.size) return;
  if (state.resultsView === 'tree') renderTreeGraph();
  else renderTimelineView();
  const node = state.nodes.get(state.selectedId);
  const section = byId('inspector-range');
  if (node && section) fillRangeSection(section, node);
}

async function ensureRange() {
  if (!state.showRange || !state.nodes.size) return;
  const key = nodesKey();
  if ((state.range && state.rangeKey === key) || state.rangeLoading === key) return;
  state.rangeLoading = key;
  byId('range-toggle')?.classList.add('is-loading');
  try {
    const result = await request('/api/uncertainty', { nodes: [...state.nodes.values()] });
    if (state.rangeLoading !== key) return;
    state.range = result;
    state.rangeKey = key;
    rerenderForRange();
  } catch (error) {
    showToast(`Could not calculate the range: ${error.message}`, { error: true });
    setRangeVisible(false);
  } finally {
    if (state.rangeLoading === key) state.rangeLoading = '';
    byId('range-toggle')?.classList.remove('is-loading');
  }
}

function setRangeVisible(on) {
  state.showRange = on;
  const toggle = byId('range-toggle');
  if (toggle) {
    toggle.setAttribute('aria-pressed', String(on));
    toggle.textContent = on ? 'Hide range' : 'Show range';
  }
  rerenderForRange();
  if (on) ensureRange();
}

const rangeOf = (range, node) => range?.nodes?.[node.id] || null;

// Compact ±label for an edge or card: % of the central value, or CHF when a % would mislead.
function rangeBadge(range, node, field = 'net_worth') {
  const span = rangeOf(range, node)?.[field];
  if (!span) return null;
  const centre = node.state[`${field}_chf`];
  const half = (span[1] - span[0]) / 2;
  if (half < 0.5) return null;
  const share = half / Math.abs(centre || 1);
  return Math.abs(centre) >= 5000 && share <= 1 ? `±${Math.round(share * 100)}%` : `±${compactChf(half)}`;
}

function rangeDelta(value, centre) {
  const delta = value - centre;
  const percent = Math.abs(centre) >= 1000 ? ` (${delta >= 0 ? '+' : '−'}${Math.abs(delta / centre * 100).toFixed(1)}%)` : '';
  return `${moneySigned(delta)}${percent}`;
}

function fillRangeSection(section, node) {
  section.replaceChildren();
  const range = currentRange();
  if (!state.showRange) {
    section.append(el('button', { type: 'button', class: 'range-inline-toggle', text: 'Show how far this could move',
      onclick: () => setRangeVisible(true) }));
    return;
  }
  if (!range) { section.append(el('p', { class: 'range-pending', text: 'Calculating the range…' })); return; }
  const span = rangeOf(range, node);
  if (!span) return;
  const table = el('table', { class: 'range-table' },
    el('thead', {}, el('tr', {}, el('th', { text: '' }), el('th', { text: 'Value' }), el('th', { text: 'vs. central' }))));
  const body = el('tbody');
  for (const [field, title] of [['net_worth', 'Net worth'], ['cash', 'Cash']]) {
    const centre = node.state[`${field}_chf`];
    const [low, high] = span[field];
    body.append(el('tr', { class: 'range-group' }, el('th', { colspan: 3, text: title })));
    body.append(el('tr', {}, el('td', { text: 'Pessimistic' }), el('td', { class: low < 0 ? 'is-neg' : '', text: money(low) }), el('td', { text: rangeDelta(low, centre) })));
    body.append(el('tr', { class: 'is-central' }, el('td', { text: 'Central' }), el('td', { text: money(centre) }), el('td', { text: '—' })));
    body.append(el('tr', {}, el('td', { text: 'Optimistic' }), el('td', { text: money(high) }), el('td', { text: rangeDelta(high, centre) })));
  }
  table.append(body);
  section.append(el('h3', { text: 'How far this could move' }), table);
  if (span.cash[0] < 0 && node.state.cash_chf >= 0) section.append(el('p', { class: 'range-warning', text: '⚠ Cash would be negative here in the pessimistic case.' }));
  const shortfall = range.first_shortfall?.[node.branch_id];
  if (shortfall) section.append(el('p', { class: 'range-warning', text: `In the pessimistic case this path first runs out of cash in ${shortfall.date}.` }));
  const drivers = (range.drivers?.[node.branch_id] || []).filter(item => Math.abs(item.high - item.low) >= 1).slice(0, 4);
  if (drivers.length) {
    section.append(el('h4', { text: 'What matters most (end of this path)' }),
      el('ul', { class: 'range-drivers' }, ...drivers.map(item => el('li', {}, el('span', { text: item.label }),
        el('strong', { text: `${moneySigned(item.low)} / ${moneySigned(item.high)}` })))));
  }
  section.append(el('p', { class: 'range-note', text: range.note }));
}

function renderTimelineView() {
  if (!state.nodes.size) return;
  const index = buildTreeIndex();
  if (!index.branchMeta.has(state.activeBranchId)) {
    state.activeBranchId = index.branchIds[0];
  }
  const activeMeta = index.branchMeta.get(state.activeBranchId);
  const chain = activeMeta.chain;
  const milestones = getPathMilestones(chain, index);

  if (!state.selectedId || !chain.some(n => n.id === state.selectedId)) {
    state.selectedId = activeMeta.firstNode.month > 0 ? activeMeta.firstNode.id : chain[0].id;
  }

  const layout = buildPlanarTreeLayout(getAllGraphMilestones(index), index);
  const isAlternate = activeMeta.id !== 'main' && activeMeta.id !== 'root';
  const activeColor = layout.branchColors.get(activeMeta.id) || BRANCH_PALETTE[0];
  const parentBid = layout.parentBranchOf.get(activeMeta.id);
  const parentLabel = parentBid ? index.branchMeta.get(parentBid === 'root' ? 'main' : parentBid)?.label : 'Original path';

  const horizonYears = (activeMeta.tipNode.month / 12).toFixed(activeMeta.tipNode.month % 12 ? 1 : 0);
  const activeCode = activeMeta.code || 'P0';
  byId('active-path-kicker').textContent = isAlternate
    ? `${activeCode} · ${activeMeta.label.toUpperCase()}`
    : `${activeCode} · ORIGINAL PATH`;
  const dotEl = byId('active-path-dot');
  if (dotEl) {
    dotEl.style.background = activeColor;
    dotEl.style.boxShadow = `0 0 10px ${activeColor}`;
  }
  byId('active-path-subtitle').textContent = isAlternate
    ? `Branched from ${parentLabel} · ${horizonYears}-year projection.`
    : `Your ${horizonYears}-year baseline projection.`;

  const returnBtn = byId('return-to-original');
  if (returnBtn) returnBtn.hidden = !isAlternate;

  renderVerticalTimeline(milestones, activeMeta, index, layout);
  renderTreeMinimap(index);
  renderEndSummary(activeMeta, index);
  renderTrajectoryChart(activeMeta, index);
  renderGraphStudio(index);
  startSpineAnimation();
  ensureRange();
}

function renderVerticalTimeline(milestones, activeMeta, index, layout) {
  const list = byId('vertical-timeline');
  list.replaceChildren();

  const isBranch = activeMeta.id !== 'main' && activeMeta.id !== 'root';
  state.spineNodesMeta = [];

  milestones.forEach((node, idx) => {
    const prevMilestone = idx > 0 ? milestones[idx - 1] : null;
    const directChildren = index.childrenByParent.get(node.id) || [];
    const isFork = directChildren.length > 1;
    const isShortfall = node.state.cash_chf < 0;
    const isSelected = node.id === state.selectedId;
    const isDiverged = node.branch_id !== 'root' && node.branch_id !== 'main';
    const isBranchEntry = isDiverged && prevMilestone && prevMilestone.branch_id !== node.branch_id;
    const nodeBranchColor = layout.branchColors.get(node.branch_id) || BRANCH_PALETTE[0];

    const seed = ((idx + 1) * 2.317) + (node.month * 0.41);
    state.spineNodesMeta.push({ id: node.id, isFork, isShortfall, isDiverged, tintHex: nodeBranchColor, seed });

    const li = document.createElement('li');
    li.className = `vnode${isSelected ? ' is-selected' : ''}${isFork ? ' is-fork' : ''}${isDiverged ? ' is-diverged' : ''}`;
    li.dataset.nodeId = node.id;
    li.style.setProperty('--node-branch-color', nodeBranchColor);
    li.addEventListener('click', event => {
      if (event.target.closest('button, .vnode-fork')) return;
      selectNode(node.id, { openDrawer: true });
    });

    const dotBtn = document.createElement('button');
    dotBtn.type = 'button';
    dotBtn.className = 'vnode-dot-btn';
    dotBtn.setAttribute('aria-label', `${milestoneTitle(node)} — ${node.date}`);
    dotBtn.addEventListener('click', () => selectNode(node.id, { openDrawer: true }));

    const content = document.createElement('div');
    content.className = 'vnode-content';

    const eyebrow = document.createElement('div');
    eyebrow.className = 'vnode-eyebrow';
    const dateEl = document.createElement('span');
    dateEl.textContent = formatNiceDate(node.date, { includeDay: true });
    eyebrow.append(dateEl);

    if (isBranch && node.branch_id !== 'root' && node.branch_id !== 'main') {
      const { parentMeta, parentNode } = getParentComparison(node.branch_id, node.month, index);
      if (parentMeta && parentNode) {
        const delta = node.state.net_worth_chf - parentNode.state.net_worth_chf;
        const deltaEl = document.createElement('span');
        deltaEl.className = `vnode-delta ${delta > 0.5 ? 'is-pos' : delta < -0.5 ? 'is-neg' : 'is-flat'}`;
        deltaEl.textContent = `${moneySigned(delta)} vs. ${shortBranchLabel(parentMeta.label)}`;
        eyebrow.append(deltaEl);
      }
    }

    const heading = document.createElement('div');
    heading.className = 'vnode-heading';
    const titleEl = document.createElement('h3');
    titleEl.className = 'vnode-title';
    titleEl.textContent = milestoneTitle(node);
    const worthEl = document.createElement('span');
    worthEl.className = 'vnode-worth';
    worthEl.textContent = money(node.state.net_worth_chf);
    heading.append(titleEl, worthEl);

    const prose = document.createElement('p');
    prose.className = 'vnode-prose';
    prose.textContent = buildMilestoneProse(node, prevMilestone);

    const s = node.state;
    const invested = s.risk_exposure?.investment_value_chf || 0;
    const figures = document.createElement('div');
    figures.className = 'vnode-figures';
    figures.innerHTML = `
      <span>Cash <strong class="${s.cash_chf < 0 ? 'is-neg' : ''}">${escapeHtml(money(s.cash_chf))}</strong></span>
      ${invested > 0 ? `<span>Investments <strong>${escapeHtml(money(invested))}</strong></span>` : ''}
      ${s.total_debt_chf > 0 ? `<span>Debt <strong>${escapeHtml(money(s.total_debt_chf))}</strong></span>` : ''}
    `;

    const range = currentRange();
    const badge = prevMilestone ? rangeBadge(range, node) : null;
    if (badge) {
      const span = rangeOf(range, node).net_worth;
      content.append(el('div', { class: 'vnode-edge-range', title: `Net worth by ${node.date}: ${money(span[0])} – ${money(span[1])} if estimates are off`, text: badge }));
    }
    content.append(eyebrow, heading);
    const span = rangeOf(range, node)?.net_worth;
    if (span && span[1] - span[0] >= 1) {
      const centre = node.state.net_worth_chf;
      const pct = value => Math.abs(centre) >= 1000 ? ` (${value >= centre ? '+' : '−'}${Math.abs((value - centre) / centre * 100).toFixed(0)}%)` : '';
      content.append(el('div', { class: 'vnode-range', text: `Range ${compactChf(span[0])} – ${compactChf(span[1])}${pct(span[0])}${pct(span[1])}` }));
    }
    content.append(prose, figures);

    const links = document.createElement('div');
    links.className = 'vnode-links';

    const inspectLink = document.createElement('button');
    inspectLink.type = 'button';
    inspectLink.className = 'vnode-link';
    inspectLink.textContent = 'Explore this moment';
    inspectLink.addEventListener('click', event => {
      event.stopPropagation();
      selectNode(node.id, { openDrawer: true });
    });

    const branchLink = document.createElement('button');
    branchLink.type = 'button';
    branchLink.className = 'vnode-link';
    branchLink.textContent = 'What if…';
    branchLink.addEventListener('click', event => {
      event.stopPropagation();
      selectNode(node.id, { openDrawer: true, focusBranchInput: true });
    });

    links.append(inspectLink, branchLink);
    content.append(links);

    // Clear, visually-linked branch divergence signpost (connected directly by a glowing arc on canvas!)
    if (isFork) {
      const forkBlock = document.createElement('div');
      forkBlock.className = 'vnode-fork';

      const forkHead = document.createElement('div');
      forkHead.className = 'vnode-fork-label';
      const forkKicker = document.createElement('span');
      forkKicker.className = 'fork-kicker';
      forkKicker.textContent = `⑂ ${directChildren.length} paths diverge here`;

      const treeLink = document.createElement('button');
      treeLink.type = 'button';
      treeLink.className = 'fork-tree-link';
      treeLink.textContent = 'Open full tree ↗';
      treeLink.addEventListener('click', event => {
        event.stopPropagation();
        setResultsView('tree', { focusNodeId: node.id });
      });

      forkHead.append(forkKicker, treeLink);

      const destList = document.createElement('div');
      destList.className = 'fork-destinations';

      const currentForkChild = directChildren.find(c => activeMeta.chain.some(n => n.id === c.id));
      const activeSwapHere = state.branchSwap?.forkNodeId === node.id ? state.branchSwap : null;
      // Place Current path on top so switching visibly FLIP-swaps the chosen branch up into the lead slot!
      const orderedChildren = [...directChildren].sort((a, b) => {
        const aCur = activeMeta.chain.some(n => n.id === a.id);
        const bCur = activeMeta.chain.some(n => n.id === b.id);
        if (aCur !== bCur) return aCur ? -1 : 1;
        return 0;
      });

      for (const child of orderedChildren) {
        const bMeta = index.branchMeta.get(child.branch_id);
        if (!bMeta) continue;
        const isCurrent = activeMeta.chain.some(n => n.id === child.id);
        const destColor = layout.branchColors.get(child.branch_id) || BRANCH_PALETTE[0];
        const tipWorth = bMeta.tipNode.state.net_worth_chf;
        const isContinuingHere = child.branch_id === node.branch_id || (node.branch_id === 'root' && child.branch_id === 'main');
        const { parentMeta, parentNode } = isContinuingHere
          ? { parentMeta: null, parentNode: null }
          : getParentComparison(bMeta.id, bMeta.tipNode.month, index);
        const diffNote = parentMeta && parentNode
          ? ` · ${moneySigned(tipWorth - parentNode.state.net_worth_chf)} vs. ${shortBranchLabel(parentMeta.label)}`
          : '';

        const swapCls = activeSwapHere && child.branch_id === activeSwapHere.toBranchId ? ' is-swap-in' : '';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `fork-dest-btn${isCurrent ? ' is-current' : ''}${swapCls}`;
        btn.dataset.branchId = child.branch_id;
        btn.dataset.branchColor = destColor;
        btn.style.setProperty('--dest-color', destColor);
        btn.innerHTML = `
          <span class="fork-dest-left">
            <span class="fork-dest-orb" aria-hidden="true"></span>
            <span>
              <span class="fork-dest-name"><span class="branch-code-pill" style="--pill-color:${destColor}">${escapeHtml(bMeta.code || 'P0')}</span>${escapeHtml(bMeta.label)}</span>
              <span class="fork-dest-sub">Ends at ${escapeHtml(money(tipWorth))}${escapeHtml(diffNote)}</span>
            </span>
          </span>
          <span class="fork-dest-action">${isCurrent ? 'Current path' : 'Follow →'}</span>
        `;
        const enterFork = () => {
          if (isCurrent) return;
          if (state.branchSwap?.forkNodeId === node.id) return;
          if (state.forkHoverSuppressPos) return;
          state.forkHover = { nodeId: node.id, branchId: child.branch_id };
          if (!state.spineRaf) startSpineAnimation();
        };
        const leaveFork = () => {
          if (state.forkHover?.nodeId === node.id && state.forkHover?.branchId === child.branch_id) {
            state.forkHover = null;
            if (!state.spineRaf) startSpineAnimation();
          }
        };
        btn.addEventListener('pointerenter', enterFork);
        btn.addEventListener('pointerleave', leaveFork);
        btn.addEventListener('focus', enterFork);
        btn.addEventListener('blur', leaveFork);
        btn.addEventListener('click', event => {
          event.stopPropagation();
          if (isCurrent) return;
          state.forkHover = null;
          state.forkHoverSuppressPos = { x: event.clientX, y: event.clientY };
          state.justSwitchedFromMonth = node.month;
          const fromBid = currentForkChild?.branch_id || state.activeBranchId;
          const shellRect = byId('vtimeline-shell')?.getBoundingClientRect();
          const btnRect = btn.getBoundingClientRect();
          state.branchSwap = {
            forkNodeId: node.id,
            forkMonth: node.month,
            fromBranchId: fromBid,
            toBranchId: child.branch_id,
            fromColor: layout.branchColors.get(fromBid) || BRANCH_PALETTE[0],
            toColor: destColor,
            toStartX: shellRect ? Math.min(148, btnRect.left - shellRect.left + 1) : 138,
            toStartY: shellRect ? (btnRect.top - shellRect.top + btnRect.height / 2) : null,
            startMs: performance.now(),
            duration: 680,
          };
          switchActivePath(child.branch_id, { focusNodeId: child.id, anchorNodeId: node.id });
        });
        destList.append(btn);
      }

      forkBlock.append(forkHead, destList);
      content.append(forkBlock);
    }

    if (state.justSwitchedFromMonth !== null && node.month > state.justSwitchedFromMonth) {
      li.classList.add('is-switching');
      li.style.animationDelay = `${Math.min(idx, 8) * 40}ms`;
    }

    li.append(dotBtn, content);
    list.append(li);
  });
  state.justSwitchedFromMonth = null;
}

function buildPlanarTreeLayout(milestones, index) {
  const presentBranches = new Set(milestones.map(n => n.branch_id));
  const childBranches = new Map();
  const parentBranchOf = new Map();
  const forkMonthOf = new Map();

  for (const bid of presentBranches) {
    if (bid === 'root' || bid === 'main') {
      parentBranchOf.set(bid, null);
      forkMonthOf.set(bid, 0);
      continue;
    }
    const meta = index.branchMeta.get(bid);
    const first = meta?.firstNode || milestones.find(n => n.branch_id === bid);
    let p = first?.parent_id ? state.nodes.get(first.parent_id) : null;
    let pBid = null;
    while (p) {
      if (p.branch_id !== bid && presentBranches.has(p.branch_id)) {
        pBid = p.branch_id;
        break;
      }
      if (p.branch_id === 'root' && presentBranches.has('main')) {
        pBid = 'main';
        break;
      }
      p = p.parent_id ? state.nodes.get(p.parent_id) : null;
    }
    parentBranchOf.set(bid, pBid);
    forkMonthOf.set(bid, meta?.forkMonth ?? first?.month ?? 0);
    if (pBid) {
      if (!childBranches.has(pBid)) childBranches.set(pBid, []);
      childBranches.get(pBid).push(bid);
    }
  }

  // Sort each branch's children by descending fork month (latest fork closest to parent, earliest furthest below)
  // Combined with pre-order DFS, every subtree sits directly beneath its parent with ZERO crossing lines.
  for (const kids of childBranches.values()) {
    kids.sort((a, b) => (forkMonthOf.get(b) || 0) - (forkMonthOf.get(a) || 0));
  }

  const orderedBranches = [];
  const visited = new Set();
  const visit = bid => {
    if (!bid || visited.has(bid) || !presentBranches.has(bid)) return;
    visited.add(bid);
    orderedBranches.push(bid);
    for (const child of childBranches.get(bid) || []) visit(child);
  };

  if (presentBranches.has('root')) visit('root');
  if (presentBranches.has('main')) visit('main');
  for (const bid of presentBranches) visit(bid);

  const dates = [...new Set(milestones.map(node => node.date))].sort();
  const laneOffsets = new Map();
  const branchColors = new Map();
  let nextLane = 0;

  // Put 'root' and 'main' on the same lane 0 if both appear, using each branch's canonical unified color
  for (const bid of orderedBranches) {
    const metaColor = index.branchMeta.get(bid === 'root' ? 'main' : bid)?.color || BRANCH_PALETTE[0];
    if (bid === 'main' && laneOffsets.has('root')) {
      laneOffsets.set('main', laneOffsets.get('root'));
      branchColors.set('main', metaColor);
      continue;
    }
    laneOffsets.set(bid, nextLane);
    branchColors.set(bid, metaColor);
    const counts = new Map();
    for (const node of milestones.filter(item => item.branch_id === bid)) {
      counts.set(node.date, (counts.get(node.date) || 0) + 1);
    }
    nextLane += Math.max(1, ...counts.values());
  }

  return { orderedBranches, dates, laneOffsets, nextLane, branchColors, parentBranchOf };
}

function getLineageIds(nodeId, visibleEdges) {
  const parentEdgeByTo = new Map();
  const childEdgesByFrom = new Map();
  for (const e of visibleEdges) {
    parentEdgeByTo.set(e.to, e);
    if (!childEdgesByFrom.has(e.from)) childEdgesByFrom.set(e.from, []);
    childEdgesByFrom.get(e.from).push(e);
  }
  const edgeIds = new Set();
  const nodeIds = new Set();
  if (!nodeId) return { edgeIds, nodeIds };

  // Walk upward to root (exact ancestry showing which branch this came from)
  let cur = nodeId;
  while (cur) {
    nodeIds.add(cur);
    const pe = parentEdgeByTo.get(cur);
    if (!pe) break;
    edgeIds.add(pe.id);
    cur = pe.from;
  }
  // Also highlight direct outgoing branches from this node
  for (const ce of childEdgesByFrom.get(nodeId) || []) {
    edgeIds.add(ce.id);
    nodeIds.add(ce.to);
  }
  return { edgeIds, nodeIds };
}

function renderTreeMinimap(index) {
  const container = byId('tree-minimap');
  const tooltip = byId('minimap-tooltip');
  tooltip.hidden = true;

  const activeSet = new Set(index.branchMeta.get(state.activeBranchId)?.chain.map(n => n.id) || []);

  const milestones = getAllGraphMilestones(index);

  const layout = buildPlanarTreeLayout(milestones, index);
  const { dates, laneOffsets, nextLane, branchColors, parentBranchOf } = layout;

  const W = 240;
  const laneGap = nextLane > 4 ? 19 : 24;
  const padY = 16;
  const H = Math.max(78, padY * 2 + Math.max(0, nextLane - 1) * laneGap);
  const padX = 18;
  const innerW = W - padX * 2;
  const maxCol = Math.max(1, dates.length - 1);

  const rows = new Map();
  const pos = new Map();
  for (const node of milestones) {
    const rowKey = `${node.branch_id}:${node.date}`;
    const rowOffset = rows.get(rowKey) || 0;
    rows.set(rowKey, rowOffset + 1);
    const col = dates.indexOf(node.date);
    const x = dates.length === 1 ? W / 2 : padX + (col / maxCol) * innerW;
    const y = nextLane <= 1 ? H / 2 : padY + (laneOffsets.get(node.branch_id) + rowOffset) * laneGap;
    pos.set(node.id, { x, y });
  }

  const visible = new Set(milestones.map(n => n.id));
  const svgNs = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNs, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

  const edges = [];
  for (const node of milestones) {
    let parentId = node.parent_id;
    while (parentId && !visible.has(parentId)) parentId = state.nodes.get(parentId)?.parent_id;
    if (!parentId || !pos.has(parentId)) continue;
    const isActiveEdge = activeSet.has(node.id) && activeSet.has(parentId);
    edges.push({
      id: `${parentId}>${node.id}`,
      from: parentId,
      to: node.id,
      branchId: node.branch_id,
      color: branchColors.get(node.branch_id) || BRANCH_PALETTE[0],
      isActiveEdge,
    });
  }
  edges.sort((a, b) => Number(a.isActiveEdge) - Number(b.isActiveEdge));

  const edgeEls = new Map();
  const nodeEls = new Map();

  for (const edge of edges) {
    const p1 = pos.get(edge.from);
    const p2 = pos.get(edge.to);
    // Smooth horizontal-exit cubic Bézier curves so branching origin is unmistakable and never overlaps straight lines
    const dx = Math.max(14, p2.x - p1.x);
    const d = Math.abs(p1.y - p2.y) < 0.5
      ? `M${p1.x.toFixed(1)},${p1.y.toFixed(1)} L${p2.x.toFixed(1)},${p2.y.toFixed(1)}`
      : `M${p1.x.toFixed(1)},${p1.y.toFixed(1)} C${(p1.x + dx * 0.52).toFixed(1)},${p1.y.toFixed(1)} ${(p2.x - dx * 0.48).toFixed(1)},${p2.y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;

    const path = document.createElementNS(svgNs, 'path');
    path.setAttribute('d', d);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', edge.color);
    path.setAttribute('stroke-width', edge.isActiveEdge ? '2.4' : '1.75');
    path.setAttribute('stroke-opacity', edge.isActiveEdge ? '1' : '0.76');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('class', 'minimap-edge-hit');
    path.addEventListener('click', event => {
      event.stopPropagation();
      const targetBranch = edge.branchId === 'root' ? 'main' : edge.branchId;
      switchActivePath(targetBranch, { focusNodeId: edge.to });
    });
    svg.append(path);
    edgeEls.set(edge.id, { el: path, edge });
  }

  const applyMinimapHighlight = hoveredId => {
    if (!hoveredId) {
      for (const { el, edge } of edgeEls.values()) {
        el.setAttribute('stroke-opacity', edge.isActiveEdge ? '1' : '0.76');
        el.setAttribute('stroke-width', edge.isActiveEdge ? '2.4' : '1.75');
      }
      for (const { dot, isActive, isSelected } of nodeEls.values()) {
        dot.setAttribute('opacity', isSelected || isActive ? '1' : '0.68');
      }
      return;
    }
    const { edgeIds, nodeIds } = getLineageIds(hoveredId, edges);
    for (const { el, edge } of edgeEls.values()) {
      const lit = edgeIds.has(edge.id);
      el.setAttribute('stroke-opacity', lit ? '1' : '0.16');
      el.setAttribute('stroke-width', lit ? '2.5' : '1.15');
    }
    for (const [id, { dot }] of nodeEls.entries()) {
      dot.setAttribute('opacity', nodeIds.has(id) ? '1' : '0.24');
    }
  };

  const activeSelectedTreeId = resolveTreeFocusId(state.selectedId, visible, index);
  for (const node of milestones) {
    const p = pos.get(node.id);
    const isSelected = node.id === activeSelectedTreeId;
    const isActive = activeSet.has(node.id);
    const status = nodeHealth(node);
    const bColor = branchColors.get(node.branch_id) || BRANCH_PALETTE[0];

    const g = document.createElementNS(svgNs, 'g');
    g.setAttribute('class', 'minimap-node-hit');

    const hit = document.createElementNS(svgNs, 'circle');
    hit.setAttribute('cx', p.x.toFixed(1));
    hit.setAttribute('cy', p.y.toFixed(1));
    hit.setAttribute('r', '8.5');
    hit.setAttribute('fill', 'transparent');
    g.append(hit);

    const dot = document.createElementNS(svgNs, 'circle');
    dot.setAttribute('cx', p.x.toFixed(1));
    dot.setAttribute('cy', p.y.toFixed(1));
    dot.setAttribute('r', isSelected ? '4.2' : isActive ? '3.1' : '2.6');
    dot.setAttribute(
      'fill',
      isSelected ? '#fff6ec' : status.level === 'danger' ? '#ff6b5b' : status.level === 'warn' ? '#f2c14e' : bColor
    );
    dot.setAttribute('opacity', isSelected || isActive ? '1' : '0.72');
    g.append(dot);
    nodeEls.set(node.id, { dot, isActive, isSelected });

    const showTip = () => {
      applyMinimapHighlight(node.id);
      const bMeta = index.branchMeta.get(node.branch_id === 'root' ? 'main' : node.branch_id);
      const bCode = bMeta?.code || 'P0';
      const bLabel = bMeta?.label || 'Original path';
      tooltip.innerHTML = `<strong><span class="minimap-code" style="color:${bColor}">${escapeHtml(bCode)}</span> ${escapeHtml(milestoneTitle(node))}</strong><span>${escapeHtml(bLabel)} · ${escapeHtml(formatNiceDate(node.date, { includeDay: true }))} · ${escapeHtml(money(node.state.net_worth_chf))}</span>`;
      const leftPct = Math.max(15, Math.min(85, (p.x / W) * 100));
      const topPct = (p.y / H) * 100;
      tooltip.style.left = `${leftPct}%`;
      tooltip.style.top = `${topPct}%`;
      tooltip.hidden = false;
    };

    g.addEventListener('pointerenter', showTip);
    g.addEventListener('pointerleave', () => {
      tooltip.hidden = true;
      applyMinimapHighlight(null);
    });
    g.addEventListener('click', event => {
      event.stopPropagation();
      tooltip.hidden = true;
      const targetBranch = node.branch_id === 'root' ? state.activeBranchId : node.branch_id;
      switchActivePath(targetBranch, { focusNodeId: node.id });
    });
    svg.append(g);
  }

  container.replaceChildren(svg, tooltip);
}

function renderEndSummary(activeMeta, index) {
  const container = byId('path-kpis');
  container.replaceChildren();

  const chain = activeMeta.chain;
  const tip = activeMeta.tipNode;
  const { parentMeta, parentNode: parentTip } = getParentComparison(activeMeta.id, tip.month, index);

  let minCashNode = chain[0];
  let minLiquidityNode = chain[0];
  let shortfallMonths = 0;
  for (const n of chain) {
    if (n.state.cash_chf < minCashNode.state.cash_chf) minCashNode = n;
    if ((n.state.funding_feasibility?.low_point_chf ?? n.state.cash_chf) <
        (minLiquidityNode.state.funding_feasibility?.low_point_chf ?? minLiquidityNode.state.cash_chf)) minLiquidityNode = n;
    if (n.state.cash_chf < 0) shortfallMonths += 1;
  }

  const nwDelta = parentMeta && parentTip ? tip.state.net_worth_chf - parentTip.state.net_worth_chf : null;
  const cashDelta = parentMeta && parentTip ? tip.state.cash_chf - parentTip.state.cash_chf : null;
  const parentName = parentMeta ? shortBranchLabel(parentMeta.label) : '';
  const yr = (tip.month / 12).toFixed(tip.month % 12 ? 1 : 0);
  const liquidity = minLiquidityNode.state.funding_feasibility?.low_point_chf ?? minLiquidityNode.state.cash_chf;
  const funding = tip.state.funding_feasibility;
  const gapPeriod = funding?.first_funding_gap_period_start;
  const reserveBreaches = funding?.minimum_reserve_breaches || [];

  const stats = [
    {
      label: `Ending net worth (Year ${yr})`,
      value: money(tip.state.net_worth_chf),
      sub: nwDelta !== null
        ? `${moneySigned(nwDelta)} vs. ${parentName} (${money(parentTip.state.net_worth_chf)})`
        : `${moneySigned(tip.state.net_worth_chf - chain[0].state.net_worth_chf)} from starting point`,
    },
    {
      label: 'Ending cash',
      value: money(tip.state.cash_chf),
      danger: minCashNode.state.cash_chf < 0,
      sub: minCashNode.state.cash_chf < 0
        ? `Lowest point: ${money(minCashNode.state.cash_chf)} (${shortfallMonths} mo below ${currencyPrefix()}0)`
        : cashDelta !== null
          ? `${moneySigned(cashDelta)} vs. ${parentName} · lowest ${money(minCashNode.state.cash_chf)}`
          : `Lowest point: ${money(minCashNode.state.cash_chf)} (${minCashNode.date})`,
    },
    {
      label: 'Lowest liquidity',
      value: money(liquidity),
      danger: liquidity < 0 || reserveBreaches.length > 0,
      sub: liquidity < 0
        ? `Funding needed ${money(funding?.maximum_funding_need_chf ?? Math.max(0, -liquidity))} · first gap ${gapPeriod || 'during this path'}`
        : reserveBreaches.length
          ? `Minimum reserve ${money(funding.minimum_reserve_chf)} breached ${reserveBreaches.length} times · first ${reserveBreaches[0].date}`
          : `Lowest point through ${minLiquidityNode.date} · includes upfront costs`,
    },
    {
      label: `Cash in ${tip.state.cpi_base_date || chain[0].date} purchasing power`,
      value: money(tip.state.real_cash_chf ?? tip.state.cash_chf),
      sub: `Nominal ${money(tip.state.cash_chf)} · real net worth ${money(tip.state.real_net_worth_chf ?? tip.state.net_worth_chf)}`,
    },
    {
      label: 'Investments',
      value: money(tip.state.risk_exposure?.investment_value_chf || 0),
      sub: tip.state.risk_exposure?.annual_volatility_chf > 0
        ? `Annual sensitivity ±${money(tip.state.risk_exposure.annual_volatility_chf)}/yr`
        : 'No portfolio volatility exposure',
    },
    {
      label: 'Remaining debt',
      value: money(tip.state.total_debt_chf),
      sub: tip.state.total_debt_chf > 0
        ? `Active liabilities at Year ${yr}`
        : 'Debt-free at horizon',
    },
  ];

  for (const s of stats) {
    const el = document.createElement('div');
    el.className = 'summary-stat';
    el.innerHTML = `
      <div class="summary-stat-label">${escapeHtml(s.label)}</div>
      <div class="summary-stat-value">${escapeHtml(s.value)}</div>
      <div class="summary-stat-sub${s.danger ? ' is-danger' : ''}">${escapeHtml(s.sub)}</div>
    `;
    container.append(el);
  }
  if (state.sensitivityOpen) {
    loadAndRenderSensitivityReport(activeMeta);
  }
}

function formatSensitivityValue(value, unit) {
  if (value === null || value === undefined) return 'Unavailable';
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  if (unit === 'CHF') return money(num);
  if (unit === 'CHF/mo') return `${money(num)}/mo`;
  if (unit === 'share' || unit === '%') {
    const pct = unit === 'share' && Math.abs(num) <= 2 ? num * 100 : num;
    return `${Number(pct.toFixed(2))}%`;
  }
  return `${Number(num.toFixed(2))}${unit ? ` ${unit}` : ''}`;
}

function setSensitivityPanelOpen(open) {
  state.sensitivityOpen = open;
  const panel = byId('path-report-panel');
  const toggle = byId('toggle-sensitivity-panel');
  if (panel) panel.hidden = !open;
  if (toggle) toggle.setAttribute('aria-expanded', String(open));
  if (open && state.nodes.size) {
    const index = buildTreeIndex();
    const activeMeta = index.branchMeta.get(state.activeBranchId) || index.branchMeta.get(index.branchIds[0]);
    if (activeMeta) loadAndRenderSensitivityReport(activeMeta);
  }
}

async function loadAndRenderSensitivityReport(activeMeta) {
  const panel = byId('path-report-panel');
  if (!panel || !activeMeta) return;
  const chain = activeMeta.chain;
  const tip = activeMeta.tipNode;
  const renderShell = bodyHtml => {
    panel.innerHTML = `
      <div class="path-report-head">
        <div>
          <span class="card-kicker">SENSITIVITY & MODEL CHECKS</span>
          <h3>${escapeHtml(activeMeta.label)}</h3>
        </div>
        <button type="button" class="drawer-close" id="close-sensitivity-panel" aria-label="Close sensitivity report">×</button>
      </div>
      ${bodyHtml}
    `;
    panel.querySelector('#close-sensitivity-panel')?.addEventListener('click', () => setSensitivityPanelOpen(false));
  };

  const revision = state.revision;
  let bundle = state.pathReports.get(tip.id);
  if (!bundle) {
    renderShell('<p class="path-report-loading">Calculating one-at-a-time sensitivity and model checks…</p>');
    try {
      const result = await request('/api/path-report', { nodes: chain });
      bundle = result.comparison_bundle;
      state.pathReports.set(tip.id, bundle);
    } catch (error) {
      if (revision === state.revision && state.sensitivityOpen) {
        renderShell(`<p class="form-error">${escapeHtml(error.message)}</p>`);
      }
      return;
    }
  }
  if (revision !== state.revision || !state.sensitivityOpen) return;

  const report = bundle.paths[bundle.baseline_path];
  const drivers = report.sensitivity_summary?.drivers || [];
  const deltaCell = value => {
    if (value == null) return '<td class="is-num">—</td>';
    const cls = value > 0.5 ? ' is-pos' : value < -0.5 ? ' is-neg' : '';
    return `<td class="is-num${cls}">${escapeHtml(moneySigned(value))}</td>`;
  };
  const rows = drivers.map(driver => {
    const source = driver.source || 'assumption';
    const sourceLabel = SOURCE_LABELS[source] || source;
    const baseText = formatSensitivityValue(driver.base_value, driver.unit);
    const pertText = formatSensitivityValue(driver.perturbed_value, driver.unit);
    const goals = (driver.goal_impacts || [])
      .map(goal => `${goal.name}: ${goal.deadline_gap_delta_chf == null ? 'unavailable' : moneySigned(goal.deadline_gap_delta_chf)}`)
      .join(' · ');
    return `
      <tr>
        <td>
          <div class="path-report-driver">
            <strong>${escapeHtml(localizeAmounts(driver.label || driver.field))}</strong>
            <span class="source-badge source-${escapeHtml(source)}">${escapeHtml(sourceLabel)}</span>
          </div>
        </td>
        <td style="white-space:nowrap;">${escapeHtml(baseText)} → ${escapeHtml(pertText)}</td>
        ${deltaCell(driver.terminal_cash_delta_chf)}
        ${deltaCell(driver.minimum_liquidity_delta_chf)}
        <td>${escapeHtml(goals || 'No goal')}</td>
      </tr>
    `;
  }).join('');

  const checksHtml = (report.sanity_checks || []).map(check => {
    const rawName = String(check.name || '').replaceAll('_', ' ');
    const prettyName = rawName ? rawName[0].toUpperCase() + rawName.slice(1) : 'Check';
    const pillCls = check.passed === null ? 'is-muted' : check.passed ? 'is-pass' : 'is-warn';
    const pillText = check.passed === null ? '— Unavailable' : check.passed ? '✓ Passed' : '! Needs review';
    return `<li><span>${escapeHtml(prettyName)}</span><span class="check-pill ${pillCls}">${pillText}</span></li>`;
  }).join('');

  const limitationsHtml = (bundle.limitations || []).map(item => `<li>${escapeHtml(localizeAmounts(item))}</li>`).join('');

  renderShell(`
    <div class="path-report-section-title">One-at-a-time sensitivity</div>
    <div class="path-report-table">
      <table>
        <thead>
          <tr>
            <th>Input</th>
            <th>Change tested</th>
            <th style="text-align:right;">Terminal cash</th>
            <th style="text-align:right;">Minimum liquidity</th>
            <th>Goal effect</th>
          </tr>
        </thead>
        <tbody>
          ${rows || '<tr><td colspan="5">No sensitivity drivers available for this path.</td></tr>'}
        </tbody>
      </table>
    </div>
    <div class="path-report-footer">
      <div>
        <div class="path-report-section-title">Model checks</div>
        <ul class="path-checks-list">${checksHtml}</ul>
      </div>
      <div>
        <div class="path-report-section-title">Model context</div>
        <ul class="path-context-list">${limitationsHtml}</ul>
      </div>
    </div>
  `);
}

function renderTrajectoryChart(activeMeta, index) {
  const wrap = byId('trajectory-chart');
  const legend = byId('trajectory-legend');
  const metric = state.chartMetric;
  byId('trajectory-title').textContent = metric === 'cash_chf' ? 'LIQUID CASH OVER TIME' : 'NET WORTH OVER TIME';

  const activeChain = activeMeta.chain;
  const parentMeta = activeMeta.parentBranchId ? index.branchMeta.get(activeMeta.parentBranchId) : null;
  const baseChain = parentMeta?.chain || [];
  const isBranch = Boolean(parentMeta && baseChain.length > 0);

  const range = currentRange();
  const field = metric === 'cash_chf' ? 'cash' : 'net_worth';
  const spans = range ? activeChain.map(n => rangeOf(range, n)?.[field]).filter(Boolean) : [];
  const allValues = activeChain.map(n => n.state[metric]);
  for (const span of spans) allValues.push(span[0], span[1]);
  if (isBranch) allValues.push(...baseChain.map(n => n.state[metric]));
  let minVal = Math.min(0, ...allValues);
  let maxVal = Math.max(...allValues, 1000);
  if (maxVal === minVal) maxVal = minVal + 1000;
  const padY = (maxVal - minVal) * 0.1;
  minVal -= padY * 0.5;
  maxVal += padY;

  const maxMonth = Math.max(
    activeChain[activeChain.length - 1]?.month || 1,
    isBranch ? baseChain[baseChain.length - 1]?.month || 1 : 1
  );

  const W = 520;
  const H = 185;
  const padL = 64;
  const padR = 14;
  const padT = 14;
  const padB = 24;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const xOf = m => padL + (m / maxMonth) * innerW;
  const yOf = v => padT + innerH - ((v - minVal) / (maxVal - minVal)) * innerH;
  const toPath = list => list.map((n, i) => `${i === 0 ? 'M' : 'L'}${xOf(n.month).toFixed(1)},${yOf(n.state[metric]).toFixed(1)}`).join(' ');

  const activePathD = toPath(activeChain);
  const areaD = `${activePathD} L${xOf(activeChain[activeChain.length - 1].month).toFixed(1)},${yOf(minVal).toFixed(1)} L${xOf(activeChain[0].month).toFixed(1)},${yOf(minVal).toFixed(1)} Z`;
  const basePathD = isBranch ? toPath(baseChain) : '';
  const banded = spans.length === activeChain.length ? activeChain : [];
  const bandD = banded.length ? [
    ...banded.map((n, i) => `${i === 0 ? 'M' : 'L'}${xOf(n.month).toFixed(1)},${yOf(rangeOf(range, n)[field][1]).toFixed(1)}`),
    ...[...banded].reverse().map(n => `L${xOf(n.month).toFixed(1)},${yOf(rangeOf(range, n)[field][0]).toFixed(1)}`), 'Z'].join(' ') : '';
  const zeroY = yOf(0);

  const svgNs = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNs, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

  const gridLines = [maxVal, (maxVal + minVal) / 2, minVal].map(v => {
    const y = yOf(v).toFixed(1);
    return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#3c2417" stroke-width="1" />
            <text x="${padL - 8}" y="${Number(y) + 4}" text-anchor="end" fill="#967c6b" font-size="10.5">${compactChf(v)}</text>`;
  }).join('');

  const zeroLine = minVal < 0 && maxVal > 0
    ? `<line x1="${padL}" y1="${zeroY.toFixed(1)}" x2="${W - padR}" y2="${zeroY.toFixed(1)}" stroke="rgba(255,123,107,0.5)" stroke-dasharray="4 4" stroke-width="1.2" />`
    : '';

  const yearTicks = [0, Math.round(maxMonth / 2), maxMonth].map(m => {
    const x = xOf(m).toFixed(1);
    return `<text x="${x}" y="${H - 5}" text-anchor="${m === 0 ? 'start' : m === maxMonth ? 'end' : 'middle'}" fill="#967c6b" font-size="10.5">${formatMonthTime(m)}</text>`;
  }).join('');

  const activeColor = activeMeta.color || BRANCH_PALETTE[0];
  const parentColor = parentMeta?.color || '#a88770';
  const [ar, ag, ab] = hexToRgb(activeColor);
  svg.innerHTML = `
    <defs>
      <linearGradient id="trajGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${activeColor}" stop-opacity="0.28" />
        <stop offset="100%" stop-color="${activeColor}" stop-opacity="0.01" />
      </linearGradient>
    </defs>
    ${gridLines}
    ${zeroLine}
    <path d="${areaD}" fill="url(#trajGrad)" />
    ${bandD ? `<path d="${bandD}" fill="rgba(${ar},${ag},${ab},0.18)" stroke="rgba(${ar},${ag},${ab},0.48)" stroke-width="0.9" />` : ''}
    ${isBranch ? `<path d="${basePathD}" fill="none" stroke="${parentColor}" stroke-width="1.8" stroke-dasharray="5 5" opacity="0.72" />` : ''}
    <path d="${activePathD}" fill="none" stroke="${activeColor}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
    ${yearTicks}
  `;

  wrap.replaceChildren(svg);
  legend.innerHTML = `
    <div class="legend-items">
      <span class="legend-item"><i class="legend-swatch is-active" style="background:${activeColor}"></i> <strong>${escapeHtml(activeMeta.code || 'P0')}</strong> · ${escapeHtml(activeMeta.label)}</span>
      ${isBranch ? `<span class="legend-item"><i class="legend-swatch is-baseline" style="border-top-color:${parentColor}"></i> <strong>${escapeHtml(parentMeta.code || 'P0')}</strong> · ${escapeHtml(parentMeta.label)}</span>` : ''}
      ${bandD ? `<span class="legend-item"><i class="legend-swatch is-range" style="background:rgba(${ar},${ag},${ab},0.3);border-color:rgba(${ar},${ag},${ab},0.65)"></i> Range if estimates are off</span>` : ''}
    </div>
    <span>${activeChain[0].date} → ${activeChain[activeChain.length - 1].date}</span>
  `;
}

function switchActivePath(branchId, { focusNodeId = null, anchorNodeId = null } = {}) {
  const index = buildTreeIndex();
  if (!index.branchMeta.has(branchId)) return;

  // Snapshot the fork node & its option button positions BEFORE re-rendering for scroll anchoring + FLIP card swap!
  const beforeForkEl = anchorNodeId
    ? document.querySelector(`.vnode[data-node-id="${CSS.escape(anchorNodeId)}"]`)
    : null;
  const beforeAnchorTop = beforeForkEl ? beforeForkEl.getBoundingClientRect().top : null;
  const beforeBtnRelTops = new Map();
  if (beforeForkEl && beforeAnchorTop !== null) {
    for (const btn of beforeForkEl.querySelectorAll('.vnode-fork .fork-dest-btn')) {
      beforeBtnRelTops.set(btn.dataset.branchId, btn.getBoundingClientRect().top - beforeAnchorTop);
    }
  }

  state.activeBranchId = branchId;
  const meta = index.branchMeta.get(branchId);
  const newMilestones = getPathMilestones(meta.chain, index);
  const focusNode = focusNodeId ? state.nodes.get(focusNodeId) : null;
  let targetId;
  if (focusNodeId && newMilestones.some(n => n.id === focusNodeId)) {
    targetId = focusNodeId;
  } else if (focusNode) {
    targetId = newMilestones.find(n => n.month >= focusNode.month)?.id || anchorNodeId || newMilestones[0]?.id;
  } else {
    targetId = meta.firstNode.month > 0 ? meta.firstNode.id : meta.chain[0].id;
  }

  state.selectedId = targetId;
  renderTimelineView();
  selectNode(targetId, { openDrawer: false, skipTimelineRender: true });

  if (anchorNodeId && beforeAnchorTop !== null) {
    const afterEl = document.querySelector(`.vnode[data-node-id="${CSS.escape(anchorNodeId)}"]`);
    if (afterEl) {
      const afterTop = afterEl.getBoundingClientRect().top;
      const deltaScroll = afterTop - beforeAnchorTop;
      if (Math.abs(deltaScroll) > 0.5) window.scrollBy(0, deltaScroll);

      // Smooth FLIP glide: chosen card slides cleanly up into the lead slot, previous slides down into side branch
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!reduced && beforeBtnRelTops.size) {
        const anchoredTop = afterEl.getBoundingClientRect().top;
        const swap = state.branchSwap;
        for (const btn of afterEl.querySelectorAll('.vnode-fork .fork-dest-btn')) {
          const prevRel = beforeBtnRelTops.get(btn.dataset.branchId);
          if (prevRel === undefined) continue;
          const nextRel = btn.getBoundingClientRect().top - anchoredTop;
          const dy = prevRel - nextRel;
          if (Math.abs(dy) < 1) continue;
          const isOutgoing = swap && btn.dataset.branchId === swap.fromBranchId;
          btn.animate(
            isOutgoing
              ? [
                  { transform: `translateY(${dy}px)`, opacity: 0.86 },
                  { transform: 'translateY(0)', opacity: 1 },
                ]
              : [
                  { transform: `translateY(${dy}px)` },
                  { transform: 'translateY(0)' },
                ],
            { duration: 600, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }
          );
        }
      }
    }
  } else if (focusNodeId) {
    scrollMilestoneIntoView(targetId);
  }
}

function scrollMilestoneIntoView(nodeId) {
  requestAnimationFrame(() => {
    const el = document.querySelector(`.vnode[data-node-id="${CSS.escape(nodeId)}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

/* ==========================================================================
   STAGE 3B: FULL INTERACTIVE TREE VIEW (time-axis event graph from main)
   ========================================================================== */

function nodeHealth(node) {
  const issues = [];
  if (node.state.cash_chf < 0) issues.push(`Cash is ${money(node.state.cash_chf)}. Review income and expenses.`);
  if (node.state.net_worth_chf < 0) issues.push(`Net worth is ${money(node.state.net_worth_chf)}. Review assets and debts.`);
  if (issues.length) return { label: 'Needs attention', color: '#ff6b5b', level: 'danger', issues };
  const expenses = node.state.cash_flow.expenses_chf;
  if (expenses > 0 && node.state.cash_chf < expenses * 3) {
    return {
      label: 'Building resilience',
      color: '#f2c14e',
      level: 'warn',
      issues: [`Cash is ${money(node.state.cash_chf)}, below three months of expenses (${money(expenses * 3)}). Review cash and expenses.`],
    };
  }
  return { label: 'On track', color: '#c97a3a', level: 'ok', issues: null };
}

function treeEventNodes() {
  return [...state.nodes.values()].filter(
    node => node.parent_id === null || (node.event_summary && node.event_summary !== 'Monthly update')
  );
}

function resolveTreeFocusId(focusId, visible, index) {
  if (!focusId) return null;
  if (visible.has(focusId)) return focusId;
  // If jumping from a non-event fork point on the single timeline, focus its first visible branch event child
  const kids = index.childrenByParent.get(focusId) || [];
  for (const child of kids) {
    if (visible.has(child.id)) return child.id;
  }
  // Otherwise walk upward to the nearest visible ancestor event
  let cur = state.nodes.get(focusId)?.parent_id;
  while (cur) {
    if (visible.has(cur)) return cur;
    cur = state.nodes.get(cur)?.parent_id;
  }
  return null;
}

function applyTreeEdgeHighlight(hoverNodeId) {
  if (!state.graphEdges || !state.treeRenderData) return;
  const { edges, activeSet } = state.treeRenderData;
  const lineage = hoverNodeId ? getLineageIds(hoverNodeId, edges) : null;
  const updates = edges.map(edge => {
    const isActiveEdge = activeSet.has(edge.from) && activeSet.has(edge.to);
    if (!lineage) {
      // Resting state: every branch path stays crisp, luminous, and unmistakably connected as a tree
      return {
        id: edge.id,
        width: isActiveEdge ? 5.8 : 4.6,
        color: {
          color: edge.baseColor,
          highlight: edge.baseColor,
          hover: edge.baseColor,
        },
        shadow: { enabled: true, color: edge.glowColor, size: isActiveEdge ? 14 : 10, x: 0, y: 0 },
      };
    }
    const isLit = lineage.edgeIds.has(edge.id);
    return {
      id: edge.id,
      width: isLit ? 6.8 : 2.6,
      color: {
        color: isLit ? edge.baseColor : 'rgba(186, 130, 92, 0.34)',
        highlight: edge.baseColor,
        hover: edge.baseColor,
      },
      shadow: isLit
        ? { enabled: true, color: edge.glowColor, size: 18, x: 0, y: 0 }
        : false,
    };
  });
  state.graphEdges.update(updates);
}

const TREE_COL_STEP = 360;
const TREE_ROW_STEP = 195;

function fitTreeView({ animate = false } = {}) {
  if (!state.network || !state.graphNodes) return;
  const ids = state.graphNodes.getIds();
  if (!ids.length) return;
  const graphEl = byId('timeline-graph');
  const vpW = graphEl?.clientWidth || 1200;
  const vpH = graphEl?.clientHeight || 720;

  let minLeft = Infinity;
  let maxRight = -Infinity;
  let minTop = Infinity;
  let maxBottom = -Infinity;
  for (const id of ids) {
    const box = state.network.getBoundingBox(id);
    const pos = state.network.getPosition(id);
    const hasBox = box && Number.isFinite(box.left) && box.right > box.left;
    const left = hasBox ? box.left : pos.x - 140;
    const right = hasBox ? box.right : pos.x + 140;
    const top = hasBox ? box.top : pos.y - 68;
    const bottom = hasBox ? box.bottom : pos.y + 68;
    if (left < minLeft) minLeft = left;
    if (right > maxRight) maxRight = right;
    if (top < minTop) minTop = top;
    if (bottom > maxBottom) maxBottom = bottom;
  }

  const axisOffset = 58;
  const padX = 44;
  const padTop = 76;
  const padBottom = 68;
  // Leave room on the right for the colored branch ID badges (P0, P1, P1.1…) next to leaf nodes
  const contentW = Math.max(240, (maxRight + 100) - minLeft);
  const contentH = Math.max(180, (maxBottom + axisOffset) - minTop);
  const scale = Math.min(
    1.15,
    Math.max(0.18, Math.min((vpW - padX * 2) / contentW, (vpH - padTop - padBottom) / contentH))
  );
  const centerX = (minLeft + maxRight + 100) / 2;
  const centerY = (minTop + maxBottom + axisOffset) / 2;
  const offsetY = (padTop - padBottom) / 2;

  state.network.moveTo({
    position: { x: centerX, y: centerY },
    scale,
    offset: { x: 0, y: offsetY },
    animation: animate ? { duration: 380, easingFunction: 'easeInOutQuad' } : false,
  });
}

const EVENT_STYLES = {
  positive: { border: '#6fc99e', borderSelected: '#9ee8c4', bg: '#1b3428', bgSelected: '#254a38', glow: 'rgba(111, 201, 158, 0.40)', glowSelected: 'rgba(111, 201, 158, 0.72)' },
  property: { border: '#f5c258', borderSelected: '#ffe099', bg: '#3b2b12', bgSelected: '#543d18', glow: 'rgba(245, 194, 88, 0.40)', glowSelected: 'rgba(245, 194, 88, 0.72)' },
  education: { border: '#cf8de6', borderSelected: '#e5bbf4', bg: '#311b3a', bgSelected: '#462654', glow: 'rgba(207, 141, 230, 0.40)', glowSelected: 'rgba(207, 141, 230, 0.72)' },
  shock: { border: '#ff6f61', borderSelected: '#ffa49a', bg: '#401b18', bgSelected: '#5c2622', glow: 'rgba(255, 111, 97, 0.42)', glowSelected: 'rgba(255, 111, 97, 0.74)' },
  life: { border: '#67bed4', borderSelected: '#9eddf0', bg: '#183038', bgSelected: '#224652', glow: 'rgba(103, 190, 212, 0.40)', glowSelected: 'rgba(103, 190, 212, 0.72)' },
  amber: { border: '#ff9a4d', borderSelected: '#ffc794', bg: '#3f2212', bgSelected: '#5a3018', glow: 'rgba(255, 154, 77, 0.40)', glowSelected: 'rgba(255, 154, 77, 0.72)' },
};

function eventStyleFor(node) {
  const text = `${node.event_summary || ''} ${node.event_narrative || ''}`.toLowerCase();
  if (/crash|loss|layoff|drop|debt|emergency|crisis|cut/.test(text)) return EVENT_STYLES.shock;
  if (/promot|raise|bonus|salary|job|career|income/.test(text)) return EVENT_STYLES.positive;
  if (/flat|house|home|mortgage|apartment|property|car|loan|buy/.test(text)) return EVENT_STYLES.property;
  if (/master|study|degree|school|uni|sabbatical|part-time|part time|break/.test(text)) return EVENT_STYLES.education;
  if (/move|zurich|lausanne|child|baby|family|holiday|travel|wedding|relocat/.test(text)) return EVENT_STYLES.life;
  return EVENT_STYLES.amber;
}

function isTreeEventNode(node) {
  return Boolean(node.month > 0 && node.event_summary && node.event_summary !== 'Monthly update');
}

function treeNodeTitle(node) {
  const raw = milestoneTitle(node);
  return raw.length > 36 ? `${raw.slice(0, 34).trim()}…` : raw;
}

function renderTreeGraph({ fit = false, focusId = null } = {}) {
  const treeRange = currentRange();
  if (state.showRange) setTimeout(ensureRange);
  if (!state.nodes.size) return;
  const index = buildTreeIndex();
  const milestones = getAllGraphMilestones(index);
  const visible = new Set(milestones.map(n => n.id));
  const activeSet = new Set(index.branchMeta.get(state.activeBranchId)?.chain.map(n => n.id) || []);

  const layout = buildPlanarTreeLayout(milestones, index);
  const { dates, laneOffsets, branchColors } = layout;

  const rows = new Map();
  const graphNodes = milestones.map(node => {
    const isSelected = node.id === state.selectedId;
    const isActive = activeSet.has(node.id);
    const isEvent = isTreeEventNode(node);
    const rowKey = `${node.branch_id}:${node.date}`;
    const rowOffset = rows.get(rowKey) || 0;
    rows.set(rowKey, rowOffset + 1);

    const style = isEvent ? eventStyleFor(node) : null;
    const displayTitle = treeNodeTitle(node);
    const titleHtml = isEvent
      ? `<b>✦ ${escapeHtml(displayTitle)}</b>`
      : `<b>${escapeHtml(displayTitle)}</b>`;
    const bg = isEvent
      ? (isSelected ? style.bgSelected : style.bg)
      : (isSelected ? '#482d1e' : isActive ? '#362217' : '#2d1c13');
    const selectedBorder = isEvent ? style.borderSelected : '#f2be85';
    const border = isSelected
      ? selectedBorder
      : isEvent
        ? style.border
        : (isActive ? 'rgba(214, 156, 110, 0.84)' : 'rgba(156, 108, 76, 0.74)');

    return {
      id: node.id,
      label: `${titleHtml}\n${escapeHtml(money(node.state.net_worth_chf))}`,
      x: dates.indexOf(node.date) * TREE_COL_STEP,
      y: (laneOffsets.get(node.branch_id) + rowOffset) * TREE_ROW_STEP,
      color: {
        background: bg,
        border,
        highlight: { background: isEvent ? style.bgSelected : '#482d1e', border: selectedBorder },
      },
      font: {
        color: isEvent || isSelected ? '#faece0' : '#dec7b3',
        size: 23,
        face: 'DM Sans',
        multi: 'html',
        bold: {
          color: isEvent ? '#ffffff' : (isSelected ? '#fcead8' : '#f7e5d4'),
          size: isEvent ? 25 : 25,
          face: 'DM Sans',
          mod: 'bold',
        },
      },
      borderWidth: isSelected ? (isEvent ? 4 : 3.4) : isEvent ? 3.5 : 2.2,
      shape: 'box',
      shapeProperties: { borderRadius: isEvent ? 15 : 12 },
      margin: isEvent
        ? { top: 22, right: 24, bottom: 22, left: 24 }
        : { top: 18, right: 18, bottom: 18, left: 18 },
      widthConstraint: isEvent
        ? { minimum: 255, maximum: 295 }
        : { minimum: 230, maximum: 255 },
      shadow: isSelected
        ? {
            enabled: true,
            color: isEvent ? style.glowSelected : 'rgba(255, 174, 102, 0.56)',
            size: isEvent ? 38 : 34,
            x: 0,
            y: 0,
          }
        : isEvent
          ? { enabled: true, color: style.glow, size: 24, x: 0, y: 0 }
          : false,
    };
  });

  // Smooth horizontal-exit curved edges — combined with zero-crossing planar DFS lanes, lines never cross or stack!
  const positions = new Map(graphNodes.map(node => [node.id, node]));
  const edges = [];
  for (const node of milestones) {
    let parentId = node.parent_id;
    while (parentId && !visible.has(parentId)) parentId = state.nodes.get(parentId)?.parent_id;
    if (!parentId) continue;

    const parent = positions.get(parentId);
    const child = positions.get(node.id);
    const isSameLane = Math.abs(parent.y - child.y) < 1;
    const baseColor = branchColors.get(node.branch_id) || BRANCH_PALETTE[0];
    const [gr, gg, gb] = hexToRgb(baseColor);
    const glowColor = `rgba(${gr}, ${gg}, ${gb}, 0.45)`;
    const isActiveEdge = activeSet.has(node.id) && activeSet.has(parentId);
    const edgeRange = isActiveEdge ? rangeBadge(treeRange, state.nodes.get(node.id)) : null;

    edges.push({
      id: `${parentId}>${node.id}`,
      from: parentId,
      to: node.id,
      arrows: { to: { enabled: true, scaleFactor: 1.1 } },
      baseColor,
      glowColor,
      width: isActiveEdge ? 5.8 : 4.6,
      label: edgeRange || '',
      font: { color: '#f6e9dc', size: 11, strokeWidth: 3, strokeColor: '#180d07', align: 'middle' },
      color: { color: baseColor, highlight: baseColor, hover: baseColor },
      shadow: { enabled: true, color: glowColor, size: isActiveEdge ? 14 : 10, x: 0, y: 0 },
      smooth: isSameLane
        ? false
        : { type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.48 },
    });
  }

  state.treeRenderData = { edges, activeSet };

  if (!state.network) {
    state.graphNodes = new vis.DataSet(graphNodes);
    state.graphEdges = new vis.DataSet(edges);
    state.network = new vis.Network(
      byId('timeline-graph'),
      { nodes: state.graphNodes, edges: state.graphEdges },
      {
        physics: false,
        interaction: { hover: true, navigationButtons: false, dragNodes: false },
        nodes: { chosen: false },
        edges: { arrowStrikethrough: false },
        layout: { improvedLayout: false },
      }
    );
    state.network.on('click', params => {
      if (state.nodes.has(params.nodes[0])) {
        selectNode(params.nodes[0], { openDrawer: true });
      } else {
        setDrawerOpen(false);
        setPathsPanelOpen(false);
      }
    });
    state.network.on('hoverNode', params => {
      if (state.nodes.has(params.node)) {
        state.treeHoverId = params.node;
        applyTreeEdgeHighlight(params.node);
      }
    });
    state.network.on('blurNode', () => {
      state.treeHoverId = null;
      applyTreeEdgeHighlight(null);
    });
    // Luminous warm astral time axis drawn along the bottom of the date columns (dates only)
    state.network.on('afterDrawing', ctx => {
      const nodes = getAllGraphMilestones(buildTreeIndex()).filter(node => state.graphNodes?.get(node.id));
      if (!nodes.length) return;
      const columns = [...new Set(nodes.map(node => node.date))].sort();
      const monthCounts = new Map();
      for (const d of columns) {
        const ym = d.slice(0, 7);
        monthCounts.set(ym, (monthCounts.get(ym) || 0) + 1);
      }

      const scale = state.network.getScale();
      const boxes = new Map(nodes.map(n => [n.id, state.network.getBoundingBox(n.id)]));
      const bottom = Math.max(...[...boxes.values()].map(b => b.bottom));
      const axisY = bottom + 58;
      const startX = -155;
      const endX = (columns.length - 1) * TREE_COL_STEP + 155;

      ctx.save();

      // Subtle vertical hairlines anchoring each date column up to its lowest card
      ctx.strokeStyle = 'rgba(255, 174, 108, 0.14)';
      ctx.lineWidth = 1.2 / scale;
      ctx.setLineDash([4 / scale, 5 / scale]);
      ctx.beginPath();
      for (const [colIdx, date] of columns.entries()) {
        const colNodes = nodes.filter(item => item.date === date);
        const colBottom = Math.max(...colNodes.map(n => boxes.get(n.id).bottom));
        const cx = colIdx * TREE_COL_STEP;
        ctx.moveTo(cx, colBottom + 8);
        ctx.lineTo(cx, axisY - 8 / scale);
      }
      ctx.stroke();
      ctx.setLineDash([]);

      // Soft ambient amber glow rail
      const glowGrad = ctx.createLinearGradient(startX, axisY, endX, axisY);
      glowGrad.addColorStop(0, 'rgba(255, 138, 61, 0)');
      glowGrad.addColorStop(0.08, 'rgba(255, 148, 70, 0.30)');
      glowGrad.addColorStop(0.92, 'rgba(255, 148, 70, 0.30)');
      glowGrad.addColorStop(1, 'rgba(255, 138, 61, 0)');
      ctx.strokeStyle = glowGrad;
      ctx.lineWidth = 10 / scale;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(startX, axisY);
      ctx.lineTo(endX, axisY);
      ctx.stroke();

      // Luminous warm ivory-amber core filament
      const railGrad = ctx.createLinearGradient(startX, axisY, endX, axisY);
      railGrad.addColorStop(0, 'rgba(255, 161, 89, 0.12)');
      railGrad.addColorStop(0.08, 'rgba(255, 224, 192, 0.78)');
      railGrad.addColorStop(0.92, 'rgba(255, 224, 192, 0.78)');
      railGrad.addColorStop(1, 'rgba(255, 161, 89, 0.12)');
      ctx.strokeStyle = railGrad;
      ctx.lineWidth = 2.4 / scale;
      ctx.beginPath();
      ctx.moveTo(startX, axisY);
      ctx.lineTo(endX, axisY);
      ctx.stroke();

      // Glowing astral orb marker + crisp date label per column (stays ~15px on screen in Fit view)
      const fontSize = Math.min(38, Math.max(20, 15 / scale));
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.font = `600 ${fontSize}px DM Sans`;

      for (const [colIdx, date] of columns.entries()) {
        const cx = colIdx * TREE_COL_STEP;
        const haloR = 14 / scale;
        const orbGrad = ctx.createRadialGradient(cx, axisY, 0, cx, axisY, haloR);
        orbGrad.addColorStop(0, 'rgba(255, 240, 222, 0.92)');
        orbGrad.addColorStop(0.42, 'rgba(255, 154, 77, 0.45)');
        orbGrad.addColorStop(1, 'rgba(255, 138, 61, 0)');
        ctx.fillStyle = orbGrad;
        ctx.beginPath();
        ctx.arc(cx, axisY, haloR, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = '#fff3e4';
        ctx.beginPath();
        ctx.arc(cx, axisY, 4.4 / scale, 0, Math.PI * 2);
        ctx.fill();

        const includeDay = (monthCounts.get(date.slice(0, 7)) || 0) > 1;
        ctx.fillStyle = '#f8e7d6';
        ctx.fillText(formatNiceDate(date, { includeDay }), cx, axisY + 18 / scale);
      }

      // Draw crisp color-coded branch ID badges (P0, P1, P1.1…) right beside each leaf node so IDs are effortless to spot
      const liveIndex = buildTreeIndex();
      const badgeFontSize = Math.min(36, Math.max(20, 14 / scale));
      ctx.font = `700 ${badgeFontSize}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const leaf of getLeafPaths(liveIndex)) {
        const box = boxes.get(leaf.leafNodeId);
        if (!box) continue;
        const text = leaf.code;
        const textW = ctx.measureText(text).width;
        const padH = 12 / scale;
        const badgeW = textW + padH * 2;
        const badgeH = badgeFontSize * 1.55;
        const bx = box.right + 16 / scale;
        const by = (box.top + box.bottom) / 2 - badgeH / 2;
        const rad = badgeH / 2;
        const [r, g, b] = hexToRgb(leaf.color);

        ctx.beginPath();
        ctx.roundRect(bx, by, badgeW, badgeH, rad);
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.18)`;
        ctx.fill();
        ctx.strokeStyle = leaf.color;
        ctx.lineWidth = 1.8 / scale;
        ctx.stroke();

        ctx.fillStyle = '#fff6ee';
        ctx.fillText(text, bx + badgeW / 2, (box.top + box.bottom) / 2 + 1 / scale);
      }

      ctx.restore();
    });
  } else {
    const existing = new Set(state.graphNodes.getIds());
    const renderedIds = new Set(graphNodes.map(node => node.id));
    const edgeIds = new Set(edges.map(edge => edge.id));
    state.graphNodes.remove([...existing].filter(id => !renderedIds.has(id)));
    state.graphNodes.update(graphNodes);
    state.graphEdges.remove(state.graphEdges.getIds().filter(id => !edgeIds.has(id)));
    state.graphEdges.update(edges);
  }

  const select = byId('milestone-select');
  select.replaceChildren();
  select.disabled = milestones.length === 0;
  if (!milestones.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No points to display';
    select.append(option);
  }
  for (const node of milestones) {
    const option = document.createElement('option');
    option.value = node.id;
    const bMeta = index.branchMeta.get(node.branch_id === 'root' ? 'main' : node.branch_id);
    const bCode = bMeta?.code || 'P0';
    option.textContent = `${bCode} · ${formatNiceDate(node.date, { includeDay: true })}`;
    select.append(option);
  }

  const resolvedFocus = resolveTreeFocusId(focusId, visible, index);
  const selectedInTree = visible.has(state.selectedId) ? state.selectedId : (resolvedFocus || null);
  select.value = selectedInTree || '';
  applyTreeEdgeHighlight(state.treeHoverId);

  state.network.redraw();
  if (graphNodes.length) {
    if (fit) {
      fitTreeView({ animate: false });
    } else if (resolvedFocus && visible.has(resolvedFocus)) {
      const pos = state.network.getPosition(resolvedFocus);
      state.network.moveTo({
        position: pos,
        scale: 0.78,
        offset: { x: 0, y: 0 },
        animation: { duration: 360, easingFunction: 'easeInOutQuad' },
      });
    }
  }
}

/* ==========================================================================
   SHARED MOMENT DRAWER / TREE INSPECTOR
   ========================================================================== */

function detailLabel(key) {
  if (key === '__contributions__') return 'Investment contributions';
  return key.replace(/_chf$/, ` (${state.currency.code})`).replace(/_/g, ' ').replace(/^./, letter => letter.toUpperCase());
}

function detailValue(key, value) {
  if (typeof value === 'number') {
    return key.endsWith('_chf') ? money(value) : twoDecimals(value);
  }
  if (value === null) return '—';
  return String(value);
}

function rememberOpen(group, path) {
  group.open = state.openDetails.has(path);
  group.addEventListener('toggle', () => {
    if (group.open) state.openDetails.add(path);
    else state.openDetails.delete(path);
  });
}

function appendDetails(container, value, path = 'breakdown') {
  for (const [key, item] of Object.entries(value)) {
    if (item && typeof item === 'object') {
      const group = document.createElement('details');
      const groupPath = `${path}.${key}`;
      rememberOpen(group, groupPath);
      const title = document.createElement('summary');
      title.textContent = `${detailLabel(key)}${Array.isArray(item) ? ` (${item.length})` : ''}`;
      group.append(title);
      if (Object.keys(item).length) appendDetails(group, item, groupPath);
      else {
        const empty = document.createElement('p');
        empty.className = 'detail-empty';
        empty.textContent = 'None';
        group.append(empty);
      }
      container.append(group);
    } else {
      const row = document.createElement('div');
      row.className = 'detail-row';
      const label = document.createElement('span');
      label.textContent = detailLabel(key);
      const content = document.createElement('strong');
      content.textContent = detailValue(key, item);
      row.append(label, content);
      container.append(row);
    }
  }
}

function branchValue(node, variable) {
  if (variable.id === 'annual_inflation') return node.state.active_variables.annual_inflation;
  if (variable.id === 'monthly_income_chf' || variable.id === 'monthly_expenses_chf') {
    return node.scenario.phases.find(
      phase => phase.start_month <= node.month && (phase.end_month === null || node.month < phase.end_month)
    )?.[variable.id] ?? variable.value;
  }
  return node.state.active_variables.preferences[variable.id] ?? variable.value;
}

function renderInspector(node, { focusBranchInput = false } = {}) {
  if (!node) return;
  const panel = byId('inspector');
  panel.replaceChildren();

  const index = buildTreeIndex();
  const s = node.state;
  const f = s.cash_flow;
  const { parentMeta, parentNode: baseNode } = node.branch_id !== 'root' && node.branch_id !== 'main'
    ? getParentComparison(node.branch_id, node.month, index)
    : { parentMeta: null, parentNode: null };

  const header = document.createElement('div');
  header.className = 'inspector-header';

  const kicker = document.createElement('span');
  kicker.className = 'card-kicker';
  const nodeBranchMeta = index.branchMeta.get(node.branch_id === 'root' ? 'main' : node.branch_id);
  const nodeBranchCode = nodeBranchMeta?.code || 'P0';
  kicker.textContent = `${nodeBranchCode} · ${formatNiceDate(node.date, { includeDay: true }).toUpperCase()}`;

  const title = document.createElement('h2');
  title.textContent = milestoneTitle(node);

  const worth = document.createElement('div');
  worth.className = 'inspector-worth';
  worth.textContent = money(s.net_worth_chf);

  const worthLabel = document.createElement('p');
  worthLabel.textContent = 'Projected net worth';

  header.append(kicker, title, worth, worthLabel);

  const status = nodeHealth(node);
  if (status.issues) {
    const issues = document.createElement('ul');
    issues.className = `health-issues${status.level === 'warn' ? ' is-warn' : ''}`;
    for (const issue of status.issues) {
      const item = document.createElement('li');
      item.textContent = issue;
      issues.append(item);
    }
    header.append(issues);
  }

  if (baseNode && parentMeta) {
    const nwDiff = s.net_worth_chf - baseNode.state.net_worth_chf;
    const cashDiff = s.cash_chf - baseNode.state.cash_chf;
    const compare = document.createElement('div');
    compare.className = 'inspector-compare';
    compare.textContent = `Vs. ${parentMeta.label}: ${moneySigned(nwDiff)} net worth · ${moneySigned(cashDiff)} cash`;
    header.append(compare);
  }
  const rangeSection = el('div', { id: 'inspector-range', class: 'inspector-range' });
  header.append(rangeSection);
  fillRangeSection(rangeSection, node);

  {
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'delete-branch';
    remove.textContent = 'Delete this path';
    remove.addEventListener('click', () => deleteBranch(node.branch_id));
    header.append(remove);
  }

  const narrativeSec = document.createElement('div');
  narrativeSec.className = 'inspector-section';
  const narrativeTitle = document.createElement('h3');
  narrativeTitle.textContent = 'How you got here';
  const narrativeText = document.createElement('p');
  narrativeText.id = 'node-narrative';
  narrativeText.className = 'inspector-narrative';
  narrativeText.textContent = localizeAmounts(state.narratives.get(node.id) || node.narrative || buildMilestoneProse(node, null));
  narrativeSec.append(narrativeTitle, narrativeText);
  if (node.event_narrative) {
    const eventText = document.createElement('p');
    eventText.className = 'event-narrative';
    eventText.textContent = localizeAmounts(node.event_narrative);
    narrativeSec.append(eventText);
  }

  const acctSec = document.createElement('div');
  acctSec.className = 'inspector-section';
  const invested = s.risk_exposure?.investment_value_chf || 0;
  acctSec.innerHTML = `
    <div class="acct-row"><span>Cash</span><strong class="${s.cash_chf < 0 ? 'is-neg' : ''}">${escapeHtml(money(s.cash_chf))}</strong></div>
    ${invested > 0 ? `<div class="acct-row"><span>Investments</span><strong>${escapeHtml(money(invested))}</strong></div>` : ''}
    ${s.total_debt_chf > 0 ? `<div class="acct-row"><span>Debt</span><strong class="is-neg">−${escapeHtml(money(s.total_debt_chf))}</strong></div>` : ''}
    ${node.month > 0 ? `<div class="acct-row"><span>Monthly net flow</span><strong class="${f.net_cash_flow_chf < 0 ? 'is-neg' : ''}">${escapeHtml(moneySigned(f.net_cash_flow_chf))}</strong></div>` : ''}
  `;
  const breakdown = document.createElement('details');
  breakdown.className = 'breakdown';
  breakdown.style.marginTop = '12px';
  rememberOpen(breakdown, 'breakdown');
  const breakdownTitle = document.createElement('summary');
  breakdownTitle.textContent = 'Full breakdown & parameters';
  breakdown.append(breakdownTitle);
  appendDetails(breakdown, node.state);
  acctSec.append(breakdown);

  const feasibility = document.createElement('section');
  feasibility.className = 'inspector-section';
  const funding = s.funding_feasibility;
  const low = funding?.low_point_chf ?? s.cash_chf;
  const need = funding?.maximum_funding_need_chf ?? Math.max(0, -low);
  const gap = funding?.first_funding_gap_period_start;
  const baseDate = s.cpi_base_date || node.scenario.start_date;
  feasibility.innerHTML = `
    <h3>Liquidity and purchasing power</h3>
    <div class="acct-row"><span>Period low including upfront costs</span><strong class="${low < 0 ? 'is-neg' : ''}">${escapeHtml(money(low))}</strong></div>
    <div class="acct-row"><span>Maximum funding needed so far</span><strong class="${need > 0 ? 'is-neg' : ''}">${escapeHtml(money(need))}</strong></div>
    <p class="inspector-note">${escapeHtml(gap ? `First funding gap in the period starting ${gap}.` : 'No funding gap recorded so far.')}</p>
    <div class="acct-row"><span>Cash, nominal</span><strong>${escapeHtml(money(s.cash_chf))}</strong></div>
    <div class="acct-row"><span>Cash, ${escapeHtml(baseDate)} purchasing power</span><strong>${escapeHtml(money(s.real_cash_chf ?? s.cash_chf))}</strong></div>
    <div class="acct-row"><span>Net worth, nominal</span><strong>${escapeHtml(money(s.net_worth_chf))}</strong></div>
    <div class="acct-row"><span>Net worth, ${escapeHtml(baseDate)} purchasing power</span><strong>${escapeHtml(money(s.real_net_worth_chf ?? s.net_worth_chf))}</strong></div>
  `;
  if (funding?.minimum_reserve_breaches?.length) {
    const warning = document.createElement('p');
    warning.className = 'inspector-note is-danger';
    warning.textContent = `Minimum reserve breached in ${funding.minimum_reserve_breaches.length} recorded periods. Reserve ${money(funding.minimum_reserve_chf)}.`;
    feasibility.append(warning);
  }

  const commitmentSec = document.createElement('details');
  commitmentSec.className = 'inspector-section';
  if (node.branch_commitments) {
    const heading = document.createElement('summary');
    heading.textContent = 'Fork commitments';
    commitmentSec.append(heading);
    appendDetails(commitmentSec, node.branch_commitments);
  }

  const goalSec = document.createElement('section');
  goalSec.className = 'inspector-section';
  if (s.goals?.length) {
    const heading = document.createElement('h3');
    heading.textContent = 'Goal commitments';
    goalSec.append(heading);
    for (const goal of s.goals) {
      const item = document.createElement('div');
      item.className = 'goal-result';
      const statusText = goal.met === null ? 'Beyond this horizon' : goal.met ? 'Met at deadline' : 'Not met at deadline';
      item.innerHTML = `<strong>${escapeHtml(goal.name)}</strong><span>${escapeHtml(statusText)} · ${escapeHtml(goal.target_date)}</span>
        <span>Target ${escapeHtml(money(goal.target_chf))} · reserved ${escapeHtml(money(goal.reserved_cash_chf || 0))}</span>
        <span>${goal.deadline_gap_chf === null ? 'Deadline gap unavailable' : `Deadline gap ${escapeHtml(money(goal.deadline_gap_chf))}`} · ${goal.first_attainment_date ? `first attained ${escapeHtml(goal.first_attainment_date)}` : 'not yet attained'}</span>
        ${goal.failure_reason ? `<span>Reason ${escapeHtml(goal.failure_reason.replaceAll('_', ' '))}</span>` : ''}`;
      goalSec.append(item);
    }
  }

  const provenance = document.createElement('details');
  provenance.className = 'inspector-section provenance-details';
  const provenanceTitle = document.createElement('summary');
  provenanceTitle.textContent = 'Inputs and assumption sources';
  provenance.append(provenanceTitle);
  const records = Object.entries(node.scenario.provenance || {});
  if (!records.length) {
    const empty = document.createElement('p');
    empty.className = 'inspector-note';
    empty.textContent = 'Source details are unavailable for this saved scenario.';
    provenance.append(empty);
  }
  for (const [field, record] of records) {
    const row = document.createElement('div');
    row.className = 'provenance-row';
    const value = record.value === null ? 'Open-ended' : String(record.value);
    const detail = [record.unit, record.frequency, record.as_of_date, record.file, record.row, record.transformation].filter(Boolean).join(' · ');
    row.innerHTML = `<div><strong>${escapeHtml(field)}</strong><span class="source-badge">${escapeHtml(record.source || 'unknown')}</span></div>
      <span>${escapeHtml(value)}${detail ? ` · ${escapeHtml(detail)}` : ''}</span>`;
    provenance.append(row);
  }
  let editDetails = null;

  const editMonth = Math.max(0, node.month - 1);
  const editPhase = node.scenario?.phases?.find(phase => phase.start_month <= editMonth && (phase.end_month === null || editMonth < phase.end_month));
  if (editPhase) {
    editDetails = document.createElement('details');
    editDetails.className = 'inspector-section node-edit-details';
    rememberOpen(editDetails, 'node-edit');
    const editSummary = document.createElement('summary');
    editSummary.textContent = 'Edit this point';
    editDetails.append(editSummary);

    const editForm = document.createElement('form');
    editForm.className = 'node-edit-form';
    const editCost = (node.scenario.one_off_costs || []).filter(cost => cost.month === editMonth).reduce((sum, cost) => sum + cost.amount_chf, 0);
    const terms = s.active_variables?.expense_terms || {};
    const startingExpense = terms.monthly_expenses_chf ?? editPhase.monthly_expenses_chf;
    const fields = [
      ...(node.month === 0 ? [{ name: 'cash_chf', label: `Starting cash (${state.currency.code})`, value: s.cash_chf, money: true }] : []),
      { name: 'monthly_income_chf', label: `Monthly income (${state.currency.code})`, value: editPhase.monthly_income_chf, money: true },
      { name: 'monthly_expenses_chf', label: `Monthly expenses at start of year (${state.currency.code})`, value: startingExpense, money: true },
      { name: 'yearly_expenses_chf', label: `Annual expenses at start of year (${state.currency.code})`, value: startingExpense * 12, money: true },
      ...(node.month === 0 ? [{ name: 'annual_inflation', label: 'Default annual inflation (%)', value: node.scenario.annual_inflation, percent: true }] : []),
      { name: 'year_inflation', label: `Inflation for ${terms.year ?? node.date.slice(0, 4)} (%)`, value: terms.year_inflation ?? null, percent: true, optional: true, placeholder: 'Use default annual inflation' },
      { name: 'inflation_exempt', label: 'Fixed by contract, unaffected by inflation', value: terms.inflation_exempt ?? false, type: 'checkbox' },
      { name: 'duration_months', label: 'Expense duration (months)', value: terms.duration_months ?? null, optional: true, placeholder: 'Ongoing' },
      { name: 'one_off_cost_chf', label: `One-time cost (${state.currency.code})`, value: editCost, money: true },
      { name: 'date', label: 'Date', value: node.date, type: 'date' },
    ];
    for (const field of fields) {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = field.type || 'number';
      input.name = field.name;
      if (field.type === 'checkbox') {
        input.checked = field.value;
        label.className = 'node-edit-check';
        label.append(input, document.createTextNode(field.label));
        editForm.append(label);
        continue;

      }
      label.textContent = field.label;
      input.value = field.value === null ? ''
        : field.percent ? Number((field.value * 100).toFixed(2))
          : field.money ? Number(chfToDisplay(field.value).toFixed(2)) : field.value;
      input.required = !field.optional;
      if (field.placeholder) input.placeholder = field.placeholder;
      if (input.type === 'number') {
        input.step = field.name === 'duration_months' ? '1' : 'any';
        if (field.name === 'duration_months') input.min = '1';
        else if (field.percent) input.min = '-99.999999';
        else if (field.name !== 'cash_chf') input.min = '0';
      }
      label.append(input);
      editForm.append(label);
    }
    const monthlyExpenses = editForm.elements.monthly_expenses_chf;
    const yearlyExpenses = editForm.elements.yearly_expenses_chf;
    monthlyExpenses.addEventListener('input', () => { yearlyExpenses.value = Number((Number(monthlyExpenses.value) * 12).toFixed(2)); });
    yearlyExpenses.addEventListener('input', () => { monthlyExpenses.value = Number((Number(yearlyExpenses.value) / 12).toFixed(2)); });
    const editError = document.createElement('p');
    editError.className = 'form-error';
    editError.setAttribute('role', 'alert');
    const editButton = document.createElement('button');
    editButton.type = 'submit';
    editButton.className = 'primary-button wide';
    editButton.textContent = 'Save changes';
    editForm.append(editError, editButton);
    editForm.addEventListener('submit', async event => {
      event.preventDefault();
      const edits = {};
      for (const field of fields) {
        if (field.name === 'yearly_expenses_chf') continue;
        const input = editForm.elements[field.name];
        let value;
        if (input.type === 'checkbox') value = input.checked;
        else if (input.type === 'number') {
          value = input.value === '' ? null : field.money ? displayToChf(input.value) : Number(input.value) / (field.percent ? 100 : 1);
        } else value = input.value;
        // Shown values are rounded (and converted for other currencies), so an untouched field is not an edit.
        const tolerance = field.money ? 0.006 / state.currency.per_chf : field.percent ? 0.000051 : 1e-10;
        const unchanged = value !== null && field.value !== null && typeof value === 'number' && Math.abs(value - field.value) < tolerance;
        if (value !== field.value && !((field.percent || field.money) && unchanged)) edits[field.name] = value;
      }
      if (!Object.keys(edits).length) return;
      editError.textContent = '';
      editButton.disabled = true;
      const revision = state.revision;
      try {
        const result = await request('/api/edit-nodes', { nodes: [...state.nodes.values()], node_id: node.id, edits });
        if (revision !== state.revision) return;
        state.revision += 1;
        state.narratives.clear();
        state.pathReports.clear();
        for (const updated of result.nodes) state.nodes.set(updated.id, updated);
        if (state.resultsView === 'tree') renderTreeGraph({ focusId: node.id });
        else renderTimelineView();
        selectNode(node.id, { openDrawer: true, skipTimelineRender: true });
        showToast('Changes saved');
      } catch (error) {
        editError.textContent = error.message;
      } finally {
        editButton.disabled = false;
      }
    });
    editDetails.append(editForm);
  }

  panel.append(header, narrativeSec);
  const info = renderBranchInfo(node);
  if (info) panel.append(info);
  panel.append(acctSec, feasibility, commitmentSec, goalSec, provenance);
  if (editDetails) panel.append(editDetails);
  const composer = el('div', { class: 'branch-form' });
  panel.append(composer);
  showComposer(composer, node);

  if (focusBranchInput) {
    requestAnimationFrame(() => {
      const box = composer.querySelector('#branch-event');
      box?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      box?.focus();
    });
  }
}

// ── Branching: preset or free text → editable building blocks → simulated branch ──

const SOURCE_LABELS = { user: 'You', dataset: 'Dataset', assumption: 'Assumption', ai_estimate: 'AI estimate' };
const BLOCK_FIELDS = {
  changes: [['label', 'text', 'Name'], ['kind', 'select', 'Type', [['income', 'Income'], ['expenses', 'Expenses']]],
    ['amount_chf', 'number', 'Change per month (CHF, − to reduce)'], ['start_offset_months', 'int', 'Starts after (months)'],
    ['duration_months', 'int', 'Lasts (months, 0 = permanent)']],
  one_offs: [['label', 'text', 'Name'], ['amount_chf', 'number', 'Amount (CHF, − = received)'], ['offset_months', 'int', 'After (months)']],
  assets: [['label', 'text', 'Name'], ['kind', 'select', 'Type', [['property', 'Property'], ['vehicle', 'Vehicle'], ['deposit', 'Deposit'], ['other', 'Other']]],
    ['price_chf', 'number', 'Price (CHF)'], ['loan_chf', 'number', 'Loan (CHF)'], ['down_payment_chf', 'readonly', 'Down payment = price − loan'],
    ['transaction_costs_chf', 'number', 'Purchase costs (CHF)'], ['annual_interest_rate', 'percent', 'Loan interest (% per year)'],
    ['monthly_payment_chf', 'number', 'Loan payment per month'], ['annual_value_change', 'percent', 'Value change (% per year)']],
};
const NEW_BLOCKS = {
  changes: () => ({ kind: 'expenses', label: 'New monthly change', amount_chf: 0, start_offset_months: 0, duration_months: 0, source: 'user' }),
  one_offs: () => ({ label: 'One-off', amount_chf: 0, offset_months: 0, source: 'user' }),
  assets: () => ({ label: 'Purchase', kind: 'other', price_chf: 0, loan_chf: 0, down_payment_chf: 0, transaction_costs_chf: 0, annual_interest_rate: 0, monthly_payment_chf: 0, annual_value_change: 0, source: 'user' }),
};

function el(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') element.className = value;
    else if (key === 'text') element.textContent = value;
    else if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
    else element.setAttribute(key, value === true ? '' : value);
  }
  element.append(...children.filter(child => child !== null && child !== undefined));
  return element;
}

function actionButton(label, type = 'button') {
  return el('button', { class: 'primary-button wide', type },
    el('span', { class: 'btn-label', text: label }), el('span', { class: 'btn-icon', 'aria-hidden': 'true', text: '✳' }));
}

function setBadge(badge, source) {
  badge.className = `source-badge source-${source}`;
  badge.textContent = SOURCE_LABELS[source] || source;
  return badge;
}

const timing = (offset, duration) => `${offset ? `after ${offset} months` : 'from now'}${duration ? `, for ${duration} months` : ', permanently'}`;

function describeBlocks(plan) {
  const items = plan.changes.map(c => [`${c.label}: ${c.kind} ${moneySigned(c.amount_chf)}/month ${timing(c.start_offset_months, c.duration_months)}`, c.source]);
  for (const o of plan.one_offs) items.push([`${o.label}: ${o.amount_chf < 0 ? `receive ${money(-o.amount_chf)}` : `pay ${money(o.amount_chf)}`} ${o.offset_months ? `after ${o.offset_months} months` : 'now'}`, o.source]);
  for (const a of plan.assets) items.push([`${a.label}: ${money(a.price_chf)} = ${money(a.loan_chf)} loan + ${money(a.down_payment_chf)} down${a.transaction_costs_chf ? `, ${money(a.transaction_costs_chf)} costs` : ''}`, a.source]);
  if (plan.investment_multiplier !== 1) items.push([`Investments ${plan.investment_multiplier > 1 ? '+' : '−'}${Math.abs(Math.round((plan.investment_multiplier - 1) * 100))}%`, 'assumption']);
  return items;
}

// The first node of the branch this node belongs to, if that branch was built from building blocks.
function branchOrigin(node) {
  let current = node;
  while (current) {
    const parent = state.nodes.get(current.parent_id);
    if (!parent || parent.branch_id !== current.branch_id) return current.branch_proposal ? current : null;
    current = parent;
  }
  return null;
}

function renderBranchInfo(node) {
  const origin = branchOrigin(node);
  if (!origin) return null;
  const plan = origin.branch_proposal;
  const box = el('details', { class: 'inspector-section branch-info' }, el('summary', { text: `What “${plan.title}” assumes` }));
  rememberOpen(box, 'branch-info');
  const list = el('ul');
  for (const [text, source] of describeBlocks(plan)) list.append(el('li', {}, el('span', { text }), setBadge(el('span'), source)));
  box.append(list);
  if (plan.assumptions) box.append(el('p', { class: 'proposal-assumptions', text: localizeAmounts(plan.assumptions) }));
  if (plan.warnings?.length) box.append(el('ul', { class: 'proposal-warnings' }, ...plan.warnings.map(text => el('li', { text: localizeAmounts(text) }))));
  const sources = sourceLinks(plan.source_urls);
  if (sources) box.append(sources);
  return box;
}

function showComposer(section, node, text = '', presetId = null) {
  section.replaceChildren();
  let selected = state.presets.some(preset => preset.id === presetId) ? presetId : null;
  const error = el('p', { class: 'form-error', role: 'alert' });
  const chips = el('div', { class: 'preset-chips', role: 'group', 'aria-label': 'Event type' });
  const defaultHint = 'For example: I take a six-month sabbatical, or my parents give me CHF 20,000';
  const textarea = el('textarea', { id: 'branch-event', maxlength: 2000 });
  textarea.value = text;
  const label = el('label', { for: 'branch-event' });
  const submit = actionButton('Get the numbers', 'submit');
  const refresh = () => {
    const kind = state.presets.find(preset => preset.id === selected);
    for (const chip of chips.children) chip.setAttribute('aria-pressed', String(chip.dataset.id === selected));
    label.textContent = kind ? `Details for “${kind.label}” (optional)` : 'Describe the event';
    textarea.placeholder = kind ? kind.example : defaultHint;
  };
  for (const preset of state.presets) {
    const chip = el('button', { type: 'button', class: 'chip', 'data-id': preset.id, 'aria-pressed': 'false', text: preset.label });
    chip.addEventListener('click', () => { selected = selected === preset.id ? null : preset.id; refresh(); textarea.focus(); });
    chips.append(chip);
  }
  refresh();
  const form = el('form', {}, label, textarea, error, submit);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const details = textarea.value.trim();
    if (!selected && !details) { error.textContent = 'Choose an event type or describe the event.'; return; }
    error.textContent = '';
    const buttons = [...section.querySelectorAll('button')];
    buttons.forEach(button => { button.disabled = true; });
    setButtonLoading(submit, true, 'Researching the numbers…', 'Get the numbers');
    const revision = state.revision;
    try {
      const result = await request('/api/branch/propose', { parent: node, preset: selected || undefined, event: details || undefined, currency: state.currency.code });
      if (revision === state.revision && state.selectedId === node.id) renderReview(section, node, result.proposal, details, selected);
    } catch (problem) {
      error.textContent = problem.message;
    } finally {
      buttons.forEach(button => { button.disabled = false; });
      setButtonLoading(submit, false, 'Researching the numbers…', 'Get the numbers');
    }
  });
  section.append(el('h3', { text: 'What if something changed?' }),
    el('p', { class: 'composer-hint', text: 'Pick an event type, describe your own, or both. The AI sets the numbers; you review every one before the branch is created.' }),
    state.presets.length ? chips : null, form);
}

function sourceLinks(urls) {
  if (!urls?.length) return null;
  return el('div', { class: 'proposal-sources' }, el('span', { text: 'Sources' }),
    el('ul', {}, ...urls.map(url => {
      let host = url;
      try { host = new URL(url).hostname.replace(/^www\./, ''); } catch { /* keep the raw URL */ }
      return el('li', {}, el('a', { href: url, target: '_blank', rel: 'noopener noreferrer', text: host }));
    })));
}

function blockEditor(key, item, onRemove) {
  const badge = setBadge(el('span'), item.source);
  const box = el('div', { class: 'block' }, el('div', { class: 'block-head' }, badge,
    el('button', { type: 'button', class: 'block-remove', 'aria-label': 'Remove this item', text: '×', onclick: onRemove })));
  const grid = el('div', { class: 'block-grid' });
  let down = null;
  const refreshDown = () => { if (down) { item.down_payment_chf = item.price_chf - item.loan_chf; down.textContent = money(item.down_payment_chf); } };
  for (const [field, type, fieldLabel, options] of BLOCK_FIELDS[key]) {
    // Amounts are stored in CHF and shown in the chosen currency.
    const isMoney = type === 'number' && field.endsWith('_chf');
    const label = fieldLabel.replace('CHF', state.currency.code);
    let input;
    if (type === 'select') {
      input = el('select', {}, ...options.map(([value, optionText]) => el('option', { value, text: optionText })));
      input.value = item[field];
    } else if (type === 'readonly') {
      input = down = el('output', { class: 'readonly-value' });
    } else {
      input = el('input', { type: type === 'text' ? 'text' : 'number', step: type === 'int' ? 1 : 'any', min: type === 'int' ? 0 : null });
      input.value = type === 'percent' ? Number((item[field] * 100).toFixed(2))
        : isMoney ? Number(chfToDisplay(item[field]).toFixed(2)) : item[field];
    }
    if (type !== 'readonly') input.addEventListener('input', () => {
      const raw = input.value;
      item[field] = type === 'text' || type === 'select' ? raw : type === 'percent' ? Number(raw) / 100
        : type === 'int' ? Math.round(Number(raw)) : isMoney ? displayToChf(raw) : Number(raw);
      item.source = 'user';
      setBadge(badge, 'user');
      refreshDown();
    });
    grid.append(el('label', { class: `block-field${type === 'text' ? ' wide' : ''}` }, el('span', { text: label }), input));
  }
  refreshDown();
  box.append(grid);
  return box;
}

function branchOptions(node) {
  const months = el('select', { id: 'branch-months' }, ...[[6, '6 months'], [12, '1 year'], [24, '2 years'], [60, '5 years']].map(([value, optionText]) => el('option', { value, text: optionText })));
  const remaining = state.years * 12 - node.month;
  if (remaining > 0 && ![6, 12, 24, 60].includes(remaining)) months.append(el('option', { value: remaining, text: `${remaining} months (to the horizon)` }));
  months.value = remaining > 0 ? String(remaining) : '12';
  const sliders = el('div');
  for (const variable of (state.extraction?.variables || []).filter(item => item.id !== 'opening_cash_chf')) {
    const current = branchValue(node, variable);
    const output = el('output', { text: formatVariable(variable, current) });
    const check = el('input', { type: 'checkbox' });
    const input = el('input', { type: 'range', name: variable.id, min: Math.min(variable.min, current), max: Math.max(variable.max, current), step: 'any', disabled: true });
    input.value = current;
    check.addEventListener('change', () => { input.disabled = !check.checked; });
    input.addEventListener('input', () => { output.textContent = formatVariable(variable, input.value); });
    sliders.append(el('div', { class: 'branch-slider' }, el('label', { class: 'branch-toggle' }, check, el('span', { text: variable.label }), output), input));
  }
  const element = el('div', {},
    el('details', { class: 'branch-options' }, el('summary', { text: 'Adjust branch assumptions' }), sliders),
    el('label', { for: 'branch-months', text: 'Explore this branch for' }), el('div', { class: 'select-wrap' }, months));
  return {
    element,
    months: () => Number(months.value),
    variables: () => Object.fromEntries([...sliders.querySelectorAll('input[type="range"]:enabled')].map(input => [input.name, Number(input.value)])),
  };
}

function showNewBranch(node, nodes, revision) {
  if (revision !== state.revision || !state.nodes.has(node.id)) return;
  for (const child of nodes) state.nodes.set(child.id, child);
  if (!nodes.length) return;
  const first = nodes[0];
  state.activeBranchId = first.branch_id;
  state.selectedId = first.id;
  showToast(`New branch: ${first.event_summary}`);
  if (state.resultsView === 'tree') {
    renderTreeGraph({ focusId: first.id });
    selectNode(first.id);
  } else {
    setDrawerOpen(false);
    renderTimelineView();
    selectNode(first.id, { openDrawer: false });
    scrollMilestoneIntoView(first.id);
  }
}

function renderReview(section, node, proposal, text, presetId = null) {
  const plan = structuredClone(proposal);
  section.replaceChildren();
  const title = el('input', { type: 'text', id: 'branch-title', maxlength: 60 });
  title.value = plan.title;
  section.append(el('span', { class: 'card-kicker', text: 'REVIEW BEFORE BRANCHING' }), el('h3', { text: 'Check the numbers' }),
    el('label', { for: 'branch-title', text: 'Event name' }), title);
  if (plan.summary) section.append(el('p', { class: 'proposal-summary', text: localizeAmounts(plan.summary) }));
  if (plan.assumptions) section.append(el('p', { class: 'proposal-assumptions', text: localizeAmounts(plan.assumptions) }));
  if (plan.warnings?.length) section.append(el('ul', { class: 'proposal-warnings' }, ...plan.warnings.map(item => el('li', { text: localizeAmounts(item) }))));
  const sources = sourceLinks(plan.source_urls);
  if (sources) section.append(sources);
  const lists = { changes: el('div'), one_offs: el('div'), assets: el('div') };
  const redraw = () => {
    for (const key of Object.keys(lists)) lists[key].replaceChildren(...plan[key].map((item, index) => blockEditor(key, item, () => { plan[key].splice(index, 1); redraw(); })));
  };
  redraw();
  const addButton = (key, label) => el('button', { type: 'button', class: 'add-block', text: `+ ${label}`, onclick: () => { plan[key].push(NEW_BLOCKS[key]()); redraw(); } });
  const shock = el('input', { type: 'number', step: 'any', id: 'branch-shock' });
  shock.value = Number(((plan.investment_multiplier - 1) * 100).toFixed(2));
  shock.addEventListener('input', () => { plan.investment_multiplier = 1 + Number(shock.value) / 100; });
  const options = branchOptions(node);
  const error = el('p', { class: 'form-error', role: 'alert' });
  const create = actionButton('Create branch');
  create.addEventListener('click', async () => {
    error.textContent = '';
    setButtonLoading(create, true, 'Simulating…', 'Create branch');
    const revision = state.revision;
    try {
      const name = title.value.trim() || plan.title;
      const sequence = state.run ? state.run.next_sequence : undefined;
      const result = await request('/api/branch/apply', { sequence, parent: node, event: name, months: options.months(), variables: options.variables(), proposal: { ...plan, title: name } });
      if (revision === state.revision && state.nodes.has(node.id) && state.run && result.branch) {
        state.run.branches.push(result.branch);
        state.run.next_sequence = Math.max(state.run.next_sequence, (result.branch.sequence ?? 0) + 1);
      }
      if (revision === state.revision && state.nodes.has(node.id) && result.nodes.length && result.comparison_bundle) {
        state.pathReports.set(result.nodes.at(-1).id, result.comparison_bundle);
      }
      showNewBranch(node, result.nodes, revision);
    } catch (problem) {
      error.textContent = problem.message;
    } finally {
      setButtonLoading(create, false, 'Simulating…', 'Create branch');
    }
  });
  section.append(
    el('h4', { text: 'Monthly changes' }), lists.changes, addButton('changes', 'Add monthly change'),
    el('h4', { text: 'One-off amounts' }), lists.one_offs, addButton('one_offs', 'Add one-off'),
    el('h4', { text: 'Purchases' }), lists.assets, addButton('assets', 'Add purchase'),
    el('h4', { text: 'Investments' }), el('label', { class: 'block-field', for: 'branch-shock' }, el('span', { text: 'Change in investment value (%)' }), shock),
    options.element, error, create,
    el('button', { type: 'button', class: 'back-button', text: '← Choose a different event', onclick: () => showComposer(section, node, text, presetId) }));
}

function syncVerticalSelection() {
  for (const el of document.querySelectorAll('#vertical-timeline .vnode')) {
    el.classList.toggle('is-selected', el.dataset.nodeId === state.selectedId);
  }
  if (state.nodes.size) renderTreeMinimap(buildTreeIndex());
}

function updateFollowPathButton(node) {
  const label = byId('follow-selected-path')?.querySelector('.btn-label');
  if (!label || !node) return;
  const index = buildTreeIndex();
  const bid = node.branch_id === 'root' ? 'main' : node.branch_id;
  const bLabel = index.branchMeta.get(bid)?.label || 'Original path';
  const shortLabel = bLabel.length > 22 ? `${bLabel.slice(0, 20).trim()}…` : bLabel;
  label.textContent = `Follow "${shortLabel}"`;
}

/* ==========================================================================
   BRANCH DELETION
   ========================================================================== */

// Every node in the branch, plus every node of the branches that split off from it.
function branchNodeIds(branchId) {
  const removed = new Set();
  const ordered = [...state.nodes.values()].sort((a, b) => a.month - b.month);
  for (const node of ordered) {
    if (node.branch_id === branchId || removed.has(node.parent_id)) removed.add(node.id);
  }
  return removed;
}

// Opens the styled confirmation pop-up; resolves true only when the confirm button is pressed.
function confirmDialog({ kicker, title, message, confirmLabel }) {
  const dialog = byId('confirm-dialog');
  byId('confirm-kicker').textContent = kicker;
  byId('confirm-title').textContent = title;
  byId('confirm-message').textContent = message;
  byId('confirm-ok').textContent = confirmLabel;
  dialog.returnValue = '';
  dialog.showModal();
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
  });
}

const isOriginalBranch = branchId => branchId === 'main' || branchId === 'root';

// Original-path nodes that no other branch grew out of. The shared history other branches depend on stays.
function originalPathRemovals() {
  const keep = new Set();
  for (const node of state.nodes.values()) {
    if (isOriginalBranch(node.branch_id)) continue;
    let cur = state.nodes.get(node.parent_id);
    while (cur && isOriginalBranch(cur.branch_id) && !keep.has(cur.id)) {
      keep.add(cur.id);
      cur = state.nodes.get(cur.parent_id);
    }
  }
  return new Set([...state.nodes.values()]
    .filter(node => isOriginalBranch(node.branch_id) && !keep.has(node.id))
    .map(node => node.id));
}

async function deleteOriginalPath() {
  const others = buildTreeIndex().branchIds.filter(id => !isOriginalBranch(id));
  const confirmed = await confirmDialog({
    kicker: 'DELETE ORIGINAL PATH',
    title: 'Original path',
    message: others.length
      ? `This removes the Original path's projection. Your ${others.length} other branch${others.length === 1 ? '' : 'es'} stay, along with the shared history they grew out of. This can't be undone.`
      : "This is your only path, so deleting it clears the whole projection and takes you back to your assumptions. This can't be undone.",
    confirmLabel: 'Delete this path',
  });
  if (!confirmed) return;

  // Recompute: a branch may have finished loading while the pop-up was open.
  const removed = originalPathRemovals();
  for (const id of removed) {
    state.nodes.delete(id);
    state.narratives.delete(id);
  }

  if (!state.nodes.size) {
    state.revision += 1; // drop any requests still in flight for the cleared projection
    state.selectedId = null;
    state.graphNodes?.clear();
    state.graphEdges?.clear();
    state.resultsView = 'timeline';
    showStage(state.extraction ? 'tuning' : 'intake');
    showToast('Projection cleared');
    return;
  }

  const index = buildTreeIndex();
  const target = index.branchIds.find(id => !isOriginalBranch(id)) || index.branchIds[0];
  const focusId = index.branchMeta.get(target)?.firstNode?.id;
  state.activeBranchId = target;
  state.selectedId = focusId ?? null;
  if (state.resultsView === 'tree') {
    renderTreeGraph({ focusId });
    if (focusId) selectNode(focusId);
  } else {
    switchActivePath(target);
  }
  showToast('Deleted the Original path');
}

async function deleteBranch(branchId) {
  if (isOriginalBranch(branchId)) return deleteOriginalPath();
  const first = [...state.nodes.values()]
    .filter(node => node.branch_id === branchId)
    .sort((a, b) => a.month - b.month)[0];
  if (!first) return;
  const before = branchNodeIds(branchId);
  const nested = new Set([...before].map(id => state.nodes.get(id).branch_id).filter(id => id !== branchId)).size;
  const label = buildTreeIndex().branchMeta.get(branchId)?.label || milestoneTitle(first);

  const confirmed = await confirmDialog({
    kicker: 'DELETE BRANCH',
    title: label,
    message: `This removes the branch${nested ? ` and the ${nested} branch${nested === 1 ? '' : 'es'} that split off from it` : ''} from your timeline. This can't be undone.`,
    confirmLabel: 'Delete this path',
  });
  if (!confirmed || !state.nodes.has(first.id)) return;

  // Recompute: a branch may have finished loading while the pop-up was open.
  for (const id of branchNodeIds(branchId)) {
    state.nodes.delete(id);
    state.narratives.delete(id);
  }

  // Return to the moment the branch split off from.
  const forkId = state.nodes.has(first.parent_id) ? first.parent_id : buildTreeIndex().root?.id;
  const forkBranch = state.nodes.get(forkId)?.branch_id;
  state.activeBranchId = !forkBranch || forkBranch === 'root' ? 'main' : forkBranch;
  state.selectedId = forkId ?? null;
  if (state.resultsView === 'tree') {
    renderTreeGraph({ focusId: forkId });
    if (forkId) selectNode(forkId);
  } else {
    switchActivePath(state.activeBranchId, { focusNodeId: forkId });
  }
  showToast(`Deleted branch: ${label}`);
}

async function selectNode(id, { openDrawer = false, focusBranchInput = false, skipTimelineRender = false } = {}) {
  const node = state.nodes.get(id);
  if (!node) return;
  state.selectedId = id;

  if (state.resultsView === 'tree') {
    renderTreeGraph();
    updateFollowPathButton(node);
    if (openDrawer) setDrawerOpen(true);
  } else {
    const existingEl = document.querySelector(`#vertical-timeline .vnode[data-node-id="${CSS.escape(id)}"]`);
    if (existingEl || skipTimelineRender) syncVerticalSelection();
    else renderTimelineView();
    if (openDrawer) setDrawerOpen(true);
  }
  renderInspector(node, { focusBranchInput });

  if (state.narratives.has(id) || node.narrative) return;
  const revision = state.revision;
  try {
    const result = await request('/api/narrative', { node });
    if (revision !== state.revision) return;
    state.narratives.set(id, result.narrative);
    if (state.selectedId === id) {
      const el = byId('node-narrative');
      if (el) el.textContent = localizeAmounts(result.narrative);
    }
  } catch {
    // Keep instant prose fallback
  }
}

function renderResults(nodes, bundle = null) {
  state.revision += 1;
  state.pathReports = new Map();
  if (bundle) state.pathReports.set(nodes.at(-1).id, bundle);
  state.nodes = new Map(nodes.map(node => [node.id, node]));
  state.narratives = new Map();
  state.activeBranchId = nodes.some(n => n.branch_id === 'main') ? 'main' : nodes[0].branch_id;
  state.selectedId = nodes[0].id;
  state.resultsView = 'timeline';
  state.graphBuilder = { metricId: 'net_worth_chf', selectedBranchIds: [], zoom: null };
  setSensitivityPanelOpen(false);
  setDrawerOpen(false);
  if (state.network) {
    state.graphNodes?.clear();
    state.graphEdges?.clear();
  }
  showStage('results');
  setResultsView('timeline');
  selectNode(state.selectedId, { openDrawer: false });
}

/* ==========================================================================
   EVENT LISTENERS & FLOATING PANELS
   ========================================================================== */

for (const chip of document.querySelectorAll('.preset-chip')) {
  chip.addEventListener('click', () => {
    byId('statement').value = chip.dataset.story;
    if (chip.dataset.years) byId('years').value = chip.dataset.years;
    byId('statement').focus();
  });
}

for (const btn of document.querySelectorAll('.metric-btn')) {
  btn.addEventListener('click', () => {
    state.chartMetric = btn.dataset.metric;
    for (const b of document.querySelectorAll('.metric-btn')) {
      b.classList.toggle('is-active', b === btn);
    }
    if (state.nodes.size) {
      const index = buildTreeIndex();
      const meta = index.branchMeta.get(state.activeBranchId);
      if (meta) renderTrajectoryChart(meta, index);
    }
  });
}

byId('jump-to-summary').addEventListener('click', () => {
  byId('timeline-summary')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
});
byId('back-to-top').addEventListener('click', () => {
  window.scrollTo({ top: 0, behavior: 'smooth' });
});
byId('switch-to-tree').addEventListener('click', () => setResultsView('tree'));
byId('back-to-timeline').addEventListener('click', () => setResultsView('timeline', { focusNodeId: state.selectedId }));
byId('return-to-original').addEventListener('click', () => {
  const index = buildTreeIndex();
  const curMeta = index.branchMeta.get(state.activeBranchId);
  const baseId = index.branchMeta.has('main') ? 'main' : index.branchIds[0];
  state.justSwitchedFromMonth = curMeta?.forkMonth ?? 0;
  switchActivePath(baseId);
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

const minimapDock = byId('minimap-dock');
minimapDock.addEventListener('click', () => setResultsView('tree'));
minimapDock.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    setResultsView('tree');
  }
});

byId('close-drawer').addEventListener('click', () => setDrawerOpen(false));
byId('drawer-backdrop').addEventListener('click', () => setDrawerOpen(false));
byId('toggle-paths-panel').addEventListener('click', () => {
  const panel = byId('tree-controls-panel');
  setPathsPanelOpen(panel.hidden);
});
byId('close-paths-panel').addEventListener('click', () => setPathsPanelOpen(false));
window.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    if (state.drawerOpen) setDrawerOpen(false);
    setPathsPanelOpen(false);
  }
});

byId('fit-graph').addEventListener('click', () => fitTreeView({ animate: true }));
byId('milestone-select').addEventListener('change', event => {
  const id = event.target.value;
  if (!id) return;
  selectNode(id, { openDrawer: true });
  if (state.network) {
    state.network.moveTo({ position: state.network.getPosition(id), scale: 0.78, animation: { duration: 350 } });
  }
});
byId('follow-selected-path').addEventListener('click', () => {
  const node = state.nodes.get(state.selectedId);
  if (node) state.activeBranchId = node.branch_id === 'root' ? 'main' : node.branch_id;
  setResultsView('timeline', { focusNodeId: state.selectedId });
});

byId('edit-story').addEventListener('click', () => showStage('intake'));
byId('adjust-scenario').addEventListener('click', () => showStage('tuning'));
byId('tree-adjust-scenario').addEventListener('click', () => {
  state.resultsView = 'timeline';
  showStage('tuning');
});

/* ==========================================================================
   CURRENCY CHOICE (assumptions page)
   ========================================================================== */

const CURRENCY_STORAGE_KEY = 'financial_twin_currency';

// Re-renders text that depends on the chosen currency.
function refreshCurrencyText() {
  const rate = byId('currency-rate');
  if (rate) rate.textContent = currencyRateText();
}

function chooseCurrency(code) {
  setCurrency(code);
  try { localStorage.setItem(CURRENCY_STORAGE_KEY, state.currency.code); } catch { /* storage unavailable */ }
  for (const select of document.querySelectorAll('.currency-select')) {
    if (select.value !== state.currency.code) select.value = state.currency.code;
  }
  refreshCurrencyText();
}

async function loadCurrencies() {
  let saved = 'CHF';
  try { saved = localStorage.getItem(CURRENCY_STORAGE_KEY) || 'CHF'; } catch { /* storage unavailable */ }
  try {
    const response = await fetch('/api/currencies');
    if (!response.ok) throw new Error('unavailable');
    const data = await response.json();
    state.currencies = data.currencies.map(item => ({ ...item, date: data.date, source: data.source, live: data.live }));
    // The assumptions page shows names; the compact menus on the results page show codes, with names on hover.
    for (const menu of document.querySelectorAll('.currency-select')) {
      const compact = menu.id !== 'currency';
      menu.replaceChildren(...state.currencies.map(item => {
        const option = document.createElement('option');
        option.value = item.code;
        option.textContent = compact ? item.code : `${item.code} · ${item.name}`;
        option.title = item.name;
        return option;
      }));
    }
  } catch {
    // Without rates only CHF can be shown; the select keeps its single CHF option.
    state.currencies = [CHF];
  }
  chooseCurrency(saved);
}

// Switching currency redraws whatever is on screen in the new currency, keeping the values set so far.
function redrawInCurrency() {
  if (state.extraction?.variables && byId('sliders')) {
    for (const variable of state.extraction.variables) {
      const slider = byId(`slider-${variable.id}`);
      if (slider?.value !== undefined && slider.value !== '' && Number.isFinite(Number(slider.value))) {
        variable.value = Number(slider.value);
        variable.min = Math.min(Number(variable.min), Number(slider.min));
        variable.max = Math.max(Number(variable.max), Number(slider.max));
      }
    }
    renderTuning(state.extraction);
  }
  if (state.nodes.size && state.stage === 'results') {
    if (state.resultsView === 'tree') renderTreeGraph();
    else renderTimelineView();
    const selected = state.nodes.get(state.selectedId);
    if (selected && (state.drawerOpen || state.resultsView === 'tree')) renderInspector(selected);
  }
}

for (const menu of document.querySelectorAll('.currency-select')) {
  menu.addEventListener('change', event => {
    chooseCurrency(event.target.value);
    redrawInCurrency();
  });
}
loadCurrencies();

byId('intake-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = byId('extract-button');
  byId('intake-error').textContent = '';
  setButtonLoading(button, true, 'Reading your story…', 'Explore my scenario');
  try {
    state.years = Number(byId('years').value);
    state.extraction = await request('/api/extract', {
      statement: byId('statement').value.trim(),
      years: state.years,
      currency: state.currency.code,
    });
    // Show results with the exact rate the server used to convert the story.
    if (state.extraction.currency?.code === state.currency.code && state.extraction.currency.per_chf) {
      state.currency = { ...state.currency, ...state.extraction.currency };
    }
    renderTuning(state.extraction);
    showStage('tuning');
  } catch (error) {
    byId('intake-error').textContent = error.message;
  } finally {
    setButtonLoading(button, false, 'Reading your story…', 'Explore my scenario');
  }
});

byId('simulate-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = byId('simulate-button');
  byId('simulate-error').textContent = '';
  const invalidField = event.currentTarget.querySelector('.slider-number.is-invalid');
  if (invalidField) {
    byId('simulate-error').textContent = 'Fix the highlighted value before simulating.';
    invalidField.focus();
    return;
  }
  setButtonLoading(button, true, 'Simulating…', 'Simulate future');
  try {
    const variables = Object.fromEntries([...new FormData(event.currentTarget)].map(([key, value]) => [key, Number(value)]));
    const result = await request('/api/simulate', {
      scenario: state.extraction.scenario,
      years: state.years,
      variables,
    });
    state.run = {
      source: 'simulate', seed: null, start_date: result.scenario.start_date, years: state.years,
      scenario: structuredClone(state.extraction.scenario), variables, branches: [], next_sequence: 0,
      environment: result.environment,
    };
    renderResults(result.nodes, result.comparison_bundle);

  } catch (error) {
    byId('simulate-error').textContent = error.message;
  } finally {
    setButtonLoading(button, false, 'Simulating…', 'Simulate future');
  }
});

byId('test-data').addEventListener('click', async () => {
  const button = byId('test-data');
  button.disabled = true;
  try {
    state.years = Number(byId('years').value);
    const result = await request('/api/test', { years: state.years });
    state.years = result.years;
    state.extraction = result.extraction;
    state.run = {
      source: 'test', seed: result.seed, start_date: result.start_date, years: result.years,
      scenario: null, variables: {}, branches: [], next_sequence: result.next_sequence,
      environment: result.environment,
    };
    renderTuning(state.extraction);
    renderResults(result.nodes);
    showToast(`Test data generated with seed ${result.seed}`);
  } catch (error) {
    showToast(`Could not generate test data: ${error.message}`, { error: true });
  } finally {
    button.disabled = false;
  }
});

document.addEventListener('pointermove', event => {
  const pos = state.forkHoverSuppressPos;
  if (!pos) return;
  if (Math.hypot(event.clientX - pos.x, event.clientY - pos.y) > 4) {
    state.forkHoverSuppressPos = null;
  }
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    startAstralBackdrop();
    if (state.stage === 'results' && state.resultsView === 'timeline') startSpineAnimation();
  }
});

byId('range-toggle')?.addEventListener('click', () => setRangeVisible(!state.showRange));
fetch('/api/branch/presets').then(response => response.json()).then(data => { state.presets = data.presets || []; }).catch(() => {});
startAstralBackdrop();

/* ==========================================================================
   REPRODUCIBILITY: DOWNLOAD RUN FILE
   ========================================================================== */

// Same layout as financial_twin/reproduce.py build_run(); replay with scripts/reproduce.py <file>.
function buildRunFile() {
  const run = state.run;
  const nodes = [...state.nodes.values()];
  return {
    format: 'financial-twin-run/1',
    created_at: new Date().toISOString(),
    source: run.source,
    seed: run.seed,
    start_date: run.start_date,
    years: run.years,
    units: { currency: 'CHF', time_step: 'month', horizon_years: run.years },
    // Shown to the user in this currency; every number in this file is CHF.
    display_currency: { code: state.currency.code, per_chf: state.currency.per_chf, date: state.currency.date, source: state.currency.source },
    scenario: run.scenario,
    variables: run.variables,
    branches: run.branches,
    kept_node_ids: nodes.map(node => node.id).sort(),
    expected: nodes.map(node => ({
      id: node.id,
      branch_id: node.branch_id,
      month: node.month,
      date: node.date,
      cash_chf: node.state.cash_chf,
      net_worth_chf: node.state.net_worth_chf,
      total_assets_chf: node.state.total_assets_chf,
      total_debt_chf: node.state.total_debt_chf,
      net_cash_flow_chf: node.state.cash_flow.net_cash_flow_chf,
    })),
    environment: run.environment,
  };
}

byId('download-run').addEventListener('click', () => {
  if (!state.run || !state.nodes.size) {
    showToast('Run a simulation first.', { error: true });
    return;
  }
  const blob = new Blob([JSON.stringify(buildRunFile(), null, 1)], { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `financial-twin-run-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  showToast('Run file saved. Check it with: scripts/reproduce.py <file>');
});

/* ==========================================================================
   CUSTOM GRAPH STUDIO (Y-AXIS VARIABLE VS. TIME ACROSS LEAF BRANCHES)
   ========================================================================== */

const fmtPct1 = v => `${Number(v.toFixed(1))}%`;
const fmtPts1 = v => `${Number(v.toFixed(1))} pts`;
const fmtChfMo = v => `${money(v)}/mo`;
const fmtChfSignedMo = v => `${moneySigned(v)}/mo`;

const GRAPH_METRICS = [
  // Balances & wealth
  { id: 'net_worth_chf', group: 'Balances & wealth', label: 'Net worth', unit: 'CHF',
    get: n => n.state?.net_worth_chf ?? 0, format: money, formatDiff: moneySigned, formatAxis: compactChf },
  { id: 'real_net_worth_chf', group: 'Balances & wealth', label: 'Real net worth (inflation-adjusted)', unit: 'CHF',
    get: n => n.state?.real_net_worth_chf ?? n.state?.net_worth_chf ?? 0, format: money, formatDiff: moneySigned, formatAxis: compactChf },
  { id: 'cash_chf', group: 'Balances & wealth', label: 'Liquid cash', unit: 'CHF',
    get: n => n.state?.cash_chf ?? 0, format: money, formatDiff: moneySigned, formatAxis: compactChf },
  { id: 'real_cash_chf', group: 'Balances & wealth', label: 'Real cash (inflation-adjusted)', unit: 'CHF',
    get: n => n.state?.real_cash_chf ?? n.state?.cash_chf ?? 0, format: money, formatDiff: moneySigned, formatAxis: compactChf },
  { id: 'total_assets_chf', group: 'Balances & wealth', label: 'Total assets', unit: 'CHF',
    get: n => n.state?.total_assets_chf ?? 0, format: money, formatDiff: moneySigned, formatAxis: compactChf },
  { id: 'investment_value_chf', group: 'Balances & wealth', label: 'Investments', unit: 'CHF',
    get: n => n.state?.risk_exposure?.investment_value_chf ?? 0, format: money, formatDiff: moneySigned, formatAxis: compactChf },
  { id: 'total_debt_chf', group: 'Balances & wealth', label: 'Total debt', unit: 'CHF',
    get: n => n.state?.total_debt_chf ?? 0, format: money, formatDiff: moneySigned, formatAxis: compactChf },

  // Monthly cash flow (skip Month 0 opening snapshot where monthly flows are 0)
  { id: 'income_chf', group: 'Monthly cash flow', label: 'Monthly income', unit: 'CHF/mo', skipMonthZero: true,
    get: n => n.state?.cash_flow?.income_chf ?? 0, format: fmtChfMo, formatDiff: fmtChfSignedMo, formatAxis: compactChf },
  { id: 'expenses_chf', group: 'Monthly cash flow', label: 'Monthly expenses', unit: 'CHF/mo', skipMonthZero: true,
    get: n => n.state?.cash_flow?.expenses_chf ?? 0, format: fmtChfMo, formatDiff: fmtChfSignedMo, formatAxis: compactChf },
  { id: 'operating_cash_flow_chf', group: 'Monthly cash flow', label: 'Operating cash flow', unit: 'CHF/mo', skipMonthZero: true,
    get: n => n.state?.cash_flow?.operating_cash_flow_chf ?? 0, format: fmtChfMo, formatDiff: fmtChfSignedMo, formatAxis: compactChf },
  { id: 'net_cash_flow_chf', group: 'Monthly cash flow', label: 'Net monthly cash flow', unit: 'CHF/mo', skipMonthZero: true,
    get: n => n.state?.cash_flow?.net_cash_flow_chf ?? 0, format: fmtChfMo, formatDiff: fmtChfSignedMo, formatAxis: compactChf },
  { id: 'investment_contribution_chf', group: 'Monthly cash flow', label: 'Monthly investment contribution', unit: 'CHF/mo', skipMonthZero: true,
    get: n => n.state?.cash_flow?.investment_contribution_chf ?? 0, format: fmtChfMo, formatDiff: fmtChfSignedMo, formatAxis: compactChf },
  { id: 'investment_return_chf', group: 'Monthly cash flow', label: 'Monthly investment return', unit: 'CHF/mo', skipMonthZero: true,
    get: n => n.state?.cash_flow?.investment_return_chf ?? 0, format: fmtChfMo, formatDiff: fmtChfSignedMo, formatAxis: compactChf },
  { id: 'debt_payments_chf', group: 'Monthly cash flow', label: 'Monthly debt payments', unit: 'CHF/mo', skipMonthZero: true,
    get: n => n.state?.cash_flow?.debt_payments_chf ?? 0, format: fmtChfMo, formatDiff: fmtChfSignedMo, formatAxis: compactChf },
  { id: 'debt_interest_chf', group: 'Monthly cash flow', label: 'Monthly debt interest', unit: 'CHF/mo', skipMonthZero: true,
    get: n => n.state?.cash_flow?.debt_interest_chf ?? 0, format: fmtChfMo, formatDiff: fmtChfSignedMo, formatAxis: compactChf },
  { id: 'one_off_costs_chf', group: 'Monthly cash flow', label: 'One-off costs', unit: 'CHF', skipMonthZero: true,
    get: n => n.state?.cash_flow?.one_off_costs_chf ?? 0, format: money, formatDiff: moneySigned, formatAxis: compactChf },

  // Risk & macro
  { id: 'annual_volatility_chf', group: 'Risk & macro', label: 'Annual portfolio volatility', unit: 'CHF/yr',
    get: n => n.state?.risk_exposure?.annual_volatility_chf ?? 0, format: v => `${money(v)}/yr`, formatDiff: v => `${moneySigned(v)}/yr`, formatAxis: compactChf },
  { id: 'investment_share_of_assets', group: 'Risk & macro', label: 'Investment share of assets', unit: '%',
    get: n => (n.state?.risk_exposure?.investment_share_of_assets ?? 0) * 100, format: fmtPct1, formatDiff: v => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}%`, formatAxis: fmtPct1 },
  { id: 'cpi', group: 'Risk & macro', label: 'Price index (CPI)', unit: 'pts',
    get: n => n.state?.cpi ?? 100, format: fmtPts1, formatDiff: v => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)} pts`, formatAxis: v => String(Math.round(v)) },
];

const GRAPH_METRIC_MAP = new Map(GRAPH_METRICS.map(m => [m.id, m]));

function getLeafPaths(index) {
  const leaves = [];
  for (const node of state.nodes.values()) {
    const kids = index.childrenByParent.get(node.id);
    if (kids && kids.length > 0) continue;

    const chain = [];
    let cur = node;
    while (cur) {
      chain.push(cur);
      cur = cur.parent_id ? state.nodes.get(cur.parent_id) : null;
    }
    chain.reverse();

    const bid = node.branch_id;
    const meta = index.branchMeta.get(bid === 'root' ? 'main' : bid) || index.branchMeta.get(bid);
    const label = meta?.label || (bid === 'main' || bid === 'root' ? 'Original path' : milestoneTitle(node));
    const code = meta?.code || (bid === 'main' || bid === 'root' ? 'P0' : bid.slice(0, 6));
    const color = meta?.color || BRANCH_PALETTE[0];

    // Build readable decision trail for tooltips/accessibility
    const seenBranches = [];
    for (const step of chain) {
      const sb = step.branch_id;
      if (sb === 'root' || sb === 'main') continue;
      if (seenBranches[seenBranches.length - 1] !== sb) seenBranches.push(sb);
    }
    const trail = seenBranches.length > 1
      ? seenBranches.map(b => shortBranchLabel(index.branchMeta.get(b)?.label || b, 22)).join(' → ')
      : label;

    leaves.push({
      id: bid,
      leafNodeId: node.id,
      code,
      shortId: code,
      color,
      label,
      trail,
      chain,
      tipNode: node,
      forkMonth: meta?.forkMonth ?? 0,
      isOriginal: bid === 'main' || bid === 'root',
    });
  }

  leaves.sort((a, b) => {
    if (a.isOriginal !== b.isOriginal) return a.isOriginal ? -1 : 1;
    return a.code.localeCompare(b.code, undefined, { numeric: true });
  });
  return leaves;
}

function niceTicks(minVal, maxVal, targetCount = 5) {
  const span = maxVal - minVal;
  if (span <= 0) return { min: minVal - 1, max: maxVal + 1, ticks: [minVal - 1, minVal, maxVal + 1] };
  const rawStep = span / Math.max(2, targetCount - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const niceNorm = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  const step = niceNorm * mag;
  const min = Math.floor(minVal / step) * step;
  const max = Math.ceil(maxVal / step) * step;
  const ticks = [];
  for (let v = min; v <= max + step * 0.25; v += step) {
    ticks.push(Math.abs(v) < step * 1e-6 ? 0 : Number(v.toFixed(6)));
  }
  return { min, max, ticks };
}

const metricDisplayUnit = metric => metric.unit.replace('CHF', state.currency.code);

function populateGraphMetricSelect() {
  const select = byId('graph-y-metric');
  if (!select) return;
  if (select.options.length > 0 && select.dataset.currencyCode === state.currency.code) return;
  const currentValue = select.value || state.graphBuilder.metricId;
  select.replaceChildren();
  const groups = new Map();
  for (const metric of GRAPH_METRICS) {
    if (!groups.has(metric.group)) {
      const og = document.createElement('optgroup');
      og.label = metric.group;
      groups.set(metric.group, og);
      select.append(og);
    }
    const opt = document.createElement('option');
    opt.value = metric.id;
    opt.textContent = `${metric.label} (${metricDisplayUnit(metric)})`;
    groups.get(metric.group).append(opt);
  }
  select.dataset.currencyCode = state.currency.code;
  if (currentValue) select.value = currentValue;
}

function formatGraphAxisTick(metric, val, step) {
  if (metric.formatAxis === compactChf) {
    const dispStep = Math.abs(chfToDisplay(step));
    const dispVal = chfToDisplay(val);
    if (dispStep < 1000) return money(val);
    if (dispStep < 5000 && Math.abs(dispVal) >= 10_000 && Math.abs(dispVal % 1000) > 1) {
      const sign = dispVal < 0 ? '−' : '';
      return `${sign}${currencyPrefix()}${(Math.abs(dispVal) / 1000).toFixed(1)}k`;
    }
  }
  return metric.formatAxis(val);
}

function buildCustomGraphStage(config, leaves) {
  const metric = GRAPH_METRIC_MAP.get(config.metricId) || GRAPH_METRICS[0];
  const leafById = new Map(leaves.map(l => [l.id, l]));
  const series = config.branchIds
    .map(id => leafById.get(id))
    .filter(Boolean)
    .map(leaf => {
      const pts = metric.skipMonthZero && leaf.chain.length > 1
        ? leaf.chain.filter(n => n.month > 0)
        : leaf.chain;
      const byMonth = new Map();
      for (const n of pts) byMonth.set(n.month, { node: n, value: Number(metric.get(n) || 0) });
      return { ...leaf, pts, byMonth };
    });

  const W = 760;
  const H = 310;
  const padL = 86;
  const padR = 24;
  const padT = 18;
  const padB = 34;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const svgNs = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNs, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

  const stage = document.createElement('div');
  stage.className = 'custom-graph-stage';

  // Clean minimalistic empty graph state: crisp time axis + subtle horizontal guide lines + quiet prompt
  if (!series.length) {
    const sampleChain = leaves[0]?.chain || [];
    const pts = metric.skipMonthZero && sampleChain.length > 1
      ? sampleChain.filter(n => n.month > 0)
      : sampleChain;
    const xTickCount = Math.min(5, Math.max(2, pts.length));
    const xTicksHtml = pts.length
      ? Array.from({ length: xTickCount }, (_, idx) => {
          const n = pts[Math.round((idx / Math.max(1, xTickCount - 1)) * (pts.length - 1))];
          const x = (padL + (idx / Math.max(1, xTickCount - 1)) * innerW).toFixed(1);
          const anchor = idx === 0 ? 'start' : idx === xTickCount - 1 ? 'end' : 'middle';
          return `<line x1="${x}" y1="${H - padB}" x2="${x}" y2="${H - padB + 5}" stroke="rgba(176, 118, 78, 0.4)" stroke-width="1" />
                  <text x="${x}" y="${H - 10}" text-anchor="${anchor}" fill="#a88e7c" font-size="11" font-family="DM Sans, sans-serif">${escapeHtml(formatNiceDate(n.date))}</text>`;
        }).join('')
      : '';

    const gridLines = [0, 0.33, 0.66, 1].map(frac => {
      const y = (padT + frac * innerH).toFixed(1);
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(110, 70, 48, 0.32)" stroke-width="1" stroke-dasharray="3 5" />`;
    }).join('');

    svg.innerHTML = `
      ${gridLines}
      <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="rgba(176, 118, 78, 0.42)" stroke-width="1.1" />
      <line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="rgba(176, 118, 78, 0.5)" stroke-width="1.2" />
      <text x="${padL - 12}" y="${padT + 10}" text-anchor="end" fill="#a88e7c" font-size="11" font-family="DM Sans, sans-serif">${escapeHtml(metricDisplayUnit(metric))}</text>
      ${xTicksHtml}
      <text x="${(padL + innerW / 2).toFixed(1)}" y="${(padT + innerH / 2 + 4).toFixed(1)}" text-anchor="middle" fill="#bfa490" font-size="13.5" font-family="DM Sans, sans-serif">Choose a branch above to plot ${escapeHtml(metric.label.toLowerCase())}</text>
    `;
    stage.append(svg);
    return stage;
  }

  // Full available month span across selected branches
  const allMonthsSet = new Set();
  const dateByMonth = new Map();
  for (const s of series) {
    for (const n of s.pts) {
      allMonthsSet.add(n.month);
      if (!dateByMonth.has(n.month)) dateByMonth.set(n.month, n.date);
    }
  }
  const allMonths = [...allMonthsSet].sort((a, b) => a - b);
  const fullMinMonth = allMonths[0] ?? 0;
  const fullMaxMonth = Math.max(fullMinMonth + 1, allMonths[allMonths.length - 1] ?? 1);

  // Active X zoom window (if zoomed in, Y-axis auto-fits tightly to ONLY the visible window!)
  const zoom = config.zoom;
  const isZoomed = Boolean(zoom && zoom.minMonth != null && zoom.maxMonth != null && (zoom.minMonth > fullMinMonth || zoom.maxMonth < fullMaxMonth));
  const minMonth = isZoomed ? Math.max(fullMinMonth, Math.min(fullMaxMonth - 2, zoom.minMonth)) : fullMinMonth;
  const maxMonth = isZoomed ? Math.min(fullMaxMonth, Math.max(minMonth + 2, zoom.maxMonth)) : fullMaxMonth;
  const visibleMonths = allMonths.filter(m => m >= minMonth && m <= maxMonth);
  const activeMonths = visibleMonths.length ? visibleMonths : allMonths;

  let rawMin = Infinity;
  let rawMax = -Infinity;
  for (const s of series) {
    for (const m of activeMonths) {
      const hit = s.byMonth.get(m);
      if (!hit) continue;
      if (hit.value < rawMin) rawMin = hit.value;
      if (hit.value > rawMax) rawMax = hit.value;
    }
  }
  if (!Number.isFinite(rawMin) || !Number.isFinite(rawMax)) { rawMin = 0; rawMax = 100; }
  if (rawMin === rawMax) {
    rawMin -= Math.max(10, Math.abs(rawMin) * 0.08);
    rawMax += Math.max(10, Math.abs(rawMax) * 0.08);
  } else {
    const pad = (rawMax - rawMin) * 0.08;
    rawMin -= pad;
    rawMax += pad;
  }
  // Only anchor to 0 when data naturally comes close to zero (keeps close high-value lines clearly separated!)
  if (!isZoomed && rawMin > 0 && rawMin < rawMax * 0.12 && metric.id !== 'cpi') rawMin = 0;
  const { min: minVal, max: maxVal, ticks: yTicks } = niceTicks(rawMin, rawMax, 5);
  const yStep = yTicks.length > 1 ? Math.abs(yTicks[1] - yTicks[0]) : Math.max(1, maxVal - minVal);

  const xOf = m => padL + ((m - minMonth) / (maxMonth - minMonth)) * innerW;
  const yOf = v => padT + innerH - ((v - minVal) / (maxVal - minVal)) * innerH;

  // Minimalistic zoom bar right above the plot area
  const zoomBar = document.createElement('div');
  zoomBar.className = 'graph-zoom-bar';
  const zoomHint = document.createElement('span');
  zoomHint.className = 'graph-zoom-hint';
  zoomHint.textContent = isZoomed
    ? `Zoomed: ${formatNiceDate(dateByMonth.get(minMonth) || '')} → ${formatNiceDate(dateByMonth.get(maxMonth) || '')} · Double-click to reset`
    : 'Drag across the chart to zoom in';

  const zoomControls = document.createElement('div');
  zoomControls.className = 'graph-zoom-controls';

  const applyZoom = (nextMin, nextMax) => {
    const clampedMin = Math.max(fullMinMonth, Math.round(nextMin));
    const clampedMax = Math.min(fullMaxMonth, Math.round(nextMax));
    if (clampedMin <= fullMinMonth && clampedMax >= fullMaxMonth) {
      state.graphBuilder.zoom = null;
    } else if (clampedMax - clampedMin >= 2) {
      state.graphBuilder.zoom = { minMonth: clampedMin, maxMonth: clampedMax };
    }
    renderGraphStudio();
  };

  const zoomOutBtn = document.createElement('button');
  zoomOutBtn.type = 'button';
  zoomOutBtn.className = 'graph-zoom-btn';
  zoomOutBtn.textContent = '−';
  zoomOutBtn.title = 'Zoom out';
  zoomOutBtn.disabled = !isZoomed;
  zoomOutBtn.addEventListener('click', () => {
    const span = maxMonth - minMonth;
    const expand = Math.max(3, Math.round(span * 0.35));
    applyZoom(minMonth - expand, maxMonth + expand);
  });

  const zoomInBtn = document.createElement('button');
  zoomInBtn.type = 'button';
  zoomInBtn.className = 'graph-zoom-btn';
  zoomInBtn.textContent = '+';
  zoomInBtn.title = 'Zoom in';
  zoomInBtn.disabled = (maxMonth - minMonth) <= 3;
  zoomInBtn.addEventListener('click', () => {
    const span = maxMonth - minMonth;
    if (span <= 3) return;
    // When starting from full view, focus toward the diverging later part where branches separate
    const shrink = Math.max(2, Math.round(span * 0.25));
    const nextMin = !isZoomed ? Math.min(maxMonth - 3, minMonth + Math.round(span * 0.35)) : minMonth + shrink;
    const nextMax = !isZoomed ? maxMonth : maxMonth - shrink;
    applyZoom(nextMin, nextMax);
  });

  zoomControls.append(zoomOutBtn, zoomInBtn);
  if (isZoomed) {
    const resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'graph-zoom-btn';
    resetBtn.textContent = 'Reset';
    resetBtn.addEventListener('click', () => {
      state.graphBuilder.zoom = null;
      renderGraphStudio();
    });
    zoomControls.append(resetBtn);
  }
  zoomBar.append(zoomHint, zoomControls);
  stage.append(zoomBar);

  const clipId = `graph-clip-${Math.random().toString(36).slice(2, 8)}`;

  const gridHtml = yTicks.map(v => {
    const y = yOf(v).toFixed(1);
    return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(110, 70, 48, 0.38)" stroke-width="1" />
            <text x="${padL - 10}" y="${Number(y) + 4}" text-anchor="end" fill="#bfa490" font-size="11" font-family="DM Sans, sans-serif">${escapeHtml(formatGraphAxisTick(metric, v, yStep))}</text>`;
  }).join('');

  const zeroY = yOf(0);
  const zeroHtml = minVal < 0 && maxVal > 0
    ? `<line x1="${padL}" y1="${zeroY.toFixed(1)}" x2="${W - padR}" y2="${zeroY.toFixed(1)}" stroke="rgba(255, 127, 112, 0.55)" stroke-dasharray="5 5" stroke-width="1.3" />`
    : '';

  const xTickCount = Math.min(6, Math.max(2, activeMonths.length));
  const xTickMonths = [];
  for (let i = 0; i < xTickCount; i++) {
    const idx = Math.round((i / (xTickCount - 1)) * (activeMonths.length - 1));
    const m = activeMonths[idx];
    if (xTickMonths[xTickMonths.length - 1] !== m) xTickMonths.push(m);
  }
  const xTicksHtml = xTickMonths.map((m, idx) => {
    const x = xOf(m).toFixed(1);
    const anchor = idx === 0 ? 'start' : idx === xTickMonths.length - 1 ? 'end' : 'middle';
    const label = formatNiceDate(dateByMonth.get(m) || '');
    return `<line x1="${x}" y1="${H - padB}" x2="${x}" y2="${H - padB + 5}" stroke="rgba(176, 118, 78, 0.45)" stroke-width="1" />
            <text x="${x}" y="${H - 10}" text-anchor="${anchor}" fill="#bfa490" font-size="11" font-family="DM Sans, sans-serif">${escapeHtml(label)}</text>`;
  }).join('');

  // Crisp dark casing stroke behind each line keeps even very close curves visually sharp and separated
  const pathsHtml = series.map(s => {
    const d = s.pts.map((n, i) => `${i === 0 ? 'M' : 'L'}${xOf(n.month).toFixed(1)},${yOf(s.byMonth.get(n.month).value).toFixed(1)}`).join(' ');
    const last = s.pts[s.pts.length - 1];
    const lastX = xOf(last.month).toFixed(1);
    const lastY = yOf(s.byMonth.get(last.month).value).toFixed(1);
    const forkPt = s.forkMonth > 0 && s.byMonth.has(s.forkMonth) ? s.byMonth.get(s.forkMonth) : null;
    const forkDot = forkPt
      ? `<circle cx="${xOf(s.forkMonth).toFixed(1)}" cy="${yOf(forkPt.value).toFixed(1)}" r="3.8" fill="#261710" stroke="${s.color}" stroke-width="2" />`
      : '';
    return `
      <path d="${d}" fill="none" stroke="#261710" stroke-width="4.4" stroke-opacity="0.72" stroke-linecap="round" stroke-linejoin="round" />
      <path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" />
      ${forkDot}
      <circle cx="${lastX}" cy="${lastY}" r="3.8" fill="${s.color}" stroke="#261710" stroke-width="1.6" />
    `;
  }).join('');

  svg.innerHTML = `
    <defs>
      <clipPath id="${clipId}">
        <rect x="${padL - 4}" y="${padT - 6}" width="${innerW + 8}" height="${innerH + 12}" />
      </clipPath>
    </defs>
    ${gridHtml}
    ${zeroHtml}
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="rgba(176, 118, 78, 0.42)" stroke-width="1.1" />
    <line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="rgba(176, 118, 78, 0.55)" stroke-width="1.2" />
    ${xTicksHtml}
    <g clip-path="url(#${clipId})">${pathsHtml}</g>
    <rect data-interactive="true" class="graph-brush-rect" x="0" y="${padT}" width="0" height="${innerH}" fill="rgba(255, 164, 94, 0.16)" stroke="rgba(255, 164, 94, 0.72)" stroke-width="1.2" style="display:none;pointer-events:none;" />
    <g data-interactive="true" class="graph-crosshair-layer" style="display:none;">
      <line class="graph-crosshair-line" x1="0" y1="${padT}" x2="0" y2="${H - padB}" stroke="rgba(255, 224, 192, 0.5)" stroke-dasharray="4 4" stroke-width="1.2" />
      <g class="graph-crosshair-dots"></g>
    </g>
    <rect data-interactive="true" class="graph-hit-rect" x="${padL}" y="${padT}" width="${innerW}" height="${innerH}" fill="transparent" style="cursor:crosshair;" />
  `;

  const tooltip = document.createElement('div');
  tooltip.className = 'custom-graph-tooltip';
  tooltip.hidden = true;
  stage.append(svg, tooltip);

  const crosshairLayer = svg.querySelector('.graph-crosshair-layer');
  const crosshairLine = svg.querySelector('.graph-crosshair-line');
  const crosshairDots = svg.querySelector('.graph-crosshair-dots');
  const brushRect = svg.querySelector('.graph-brush-rect');
  const hitRect = svg.querySelector('.graph-hit-rect');

  const clientXToSvgX = clientX => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return padL;
    return Math.max(padL, Math.min(W - padR, ((clientX - rect.left) / rect.width) * W));
  };

  const updateHover = clientX => {
    const svgX = clientXToSvgX(clientX);
    const frac = Math.max(0, Math.min(1, (svgX - padL) / innerW));
    const approxMonth = minMonth + frac * (maxMonth - minMonth);
    let nearest = activeMonths[0];
    let bestDist = Infinity;
    for (const m of activeMonths) {
      const dist = Math.abs(m - approxMonth);
      if (dist < bestDist) { bestDist = dist; nearest = m; }
    }

    const cx = xOf(nearest);
    crosshairLayer.style.display = '';
    crosshairLine.setAttribute('x1', cx.toFixed(1));
    crosshairLine.setAttribute('x2', cx.toFixed(1));

    const entries = [];
    const dotsMarkup = [];
    for (const s of series) {
      const hit = s.byMonth.get(nearest);
      if (!hit) continue;
      const cy = yOf(hit.value);
      dotsMarkup.push(`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="5" fill="${s.color}" stroke="#fff6ee" stroke-width="2" />`);
      entries.push({ series: s, value: hit.value });
    }
    crosshairDots.innerHTML = dotsMarkup.join('');

    const sorted = [...entries].sort((a, b) => b.value - a.value);
    let diffHtml = '';
    if (entries.length === 2) {
      const spread = entries[1].value - entries[0].value;
      diffHtml = `<div class="custom-graph-tooltip-diff">${escapeHtml(entries[1].series.code)} vs. ${escapeHtml(entries[0].series.code)}: <strong>${escapeHtml(metric.formatDiff(spread))}</strong></div>`;
    } else if (sorted.length > 2) {
      const spread = sorted[0].value - sorted[sorted.length - 1].value;
      diffHtml = `<div class="custom-graph-tooltip-diff">Spread (${escapeHtml(sorted[0].series.code)} vs. ${escapeHtml(sorted[sorted.length - 1].series.code)}): <strong>${escapeHtml(metric.formatDiff(spread))}</strong></div>`;
    }

    tooltip.innerHTML = `
      <div class="custom-graph-tooltip-date">${escapeHtml(formatNiceDate(dateByMonth.get(nearest) || '', { includeDay: true }))}</div>
      ${sorted.map(e => `
        <div class="custom-graph-tooltip-row">
          <span class="custom-graph-tooltip-name"><i style="background:${e.series.color}"></i><strong>${escapeHtml(e.series.code)}</strong></span>
          <span class="custom-graph-tooltip-val">${escapeHtml(metric.format(e.value))}</span>
        </div>
      `).join('')}
      ${diffHtml}
    `;
    tooltip.hidden = false;
    const pctX = (cx / W) * 100;
    if (pctX > 62) {
      tooltip.style.left = 'auto';
      tooltip.style.right = `${Math.max(2, 100 - pctX + 2)}%`;
    } else {
      tooltip.style.right = 'auto';
      tooltip.style.left = `${Math.min(72, pctX + 2)}%`;
    }
  };

  let dragStartX = null;
  hitRect.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    dragStartX = clientXToSvgX(event.clientX);
    hitRect.setPointerCapture(event.pointerId);
    crosshairLayer.style.display = 'none';
    tooltip.hidden = true;
    brushRect.style.display = '';
    brushRect.setAttribute('x', dragStartX.toFixed(1));
    brushRect.setAttribute('width', '0');
  });

  hitRect.addEventListener('pointermove', event => {
    if (dragStartX !== null) {
      const curX = clientXToSvgX(event.clientX);
      const x1 = Math.min(dragStartX, curX);
      const w = Math.abs(curX - dragStartX);
      brushRect.setAttribute('x', x1.toFixed(1));
      brushRect.setAttribute('width', w.toFixed(1));
      return;
    }
    updateHover(event.clientX);
  });

  const finishDrag = event => {
    if (dragStartX === null) return;
    const endX = clientXToSvgX(event.clientX);
    const startX = dragStartX;
    dragStartX = null;
    brushRect.style.display = 'none';
    if (Math.abs(endX - startX) > 14) {
      const f1 = Math.max(0, Math.min(1, (Math.min(startX, endX) - padL) / innerW));
      const f2 = Math.max(0, Math.min(1, (Math.max(startX, endX) - padL) / innerW));
      const m1 = Math.floor(minMonth + f1 * (maxMonth - minMonth));
      const m2 = Math.ceil(minMonth + f2 * (maxMonth - minMonth));
      if (m2 - m1 >= 2) applyZoom(m1, m2);
    }
  };

  hitRect.addEventListener('pointerup', finishDrag);
  hitRect.addEventListener('pointercancel', () => {
    dragStartX = null;
    brushRect.style.display = 'none';
  });
  hitRect.addEventListener('pointerleave', () => {
    if (dragStartX === null) {
      crosshairLayer.style.display = 'none';
      tooltip.hidden = true;
    }
  });
  hitRect.addEventListener('dblclick', () => {
    if (isZoomed) {
      state.graphBuilder.zoom = null;
      renderGraphStudio();
    }
  });

  return stage;
}

function renderGraphStudio(index = buildTreeIndex()) {
  if (!state.nodes.size) return;
  populateGraphMetricSelect();
  const leaves = getLeafPaths(index);
  const valid = new Set(leaves.map(l => l.id));
  state.graphBuilder.selectedBranchIds = state.graphBuilder.selectedBranchIds.filter(id => valid.has(id));

  const metricSelect = byId('graph-y-metric');
  if (metricSelect) metricSelect.value = state.graphBuilder.metricId;

  const selectedSet = new Set(state.graphBuilder.selectedBranchIds);
  const branchSelect = byId('graph-branch-select');
  if (branchSelect) {
    branchSelect.replaceChildren();
    const available = leaves.filter(l => !selectedSet.has(l.id));
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = available.length ? 'Select a branch…' : 'All branches added';
    branchSelect.append(placeholder);
    for (const leaf of available) {
      const opt = document.createElement('option');
      opt.value = leaf.id;
      opt.textContent = leaf.code;
      branchSelect.append(opt);
    }
    branchSelect.disabled = available.length === 0;
  }

  const clearBtn = byId('graph-clear-all');
  if (clearBtn) clearBtn.hidden = state.graphBuilder.selectedBranchIds.length === 0;

  const chipsWrap = byId('graph-selected-branches');
  if (chipsWrap) {
    chipsWrap.replaceChildren();
    chipsWrap.hidden = state.graphBuilder.selectedBranchIds.length === 0;
    const leafById = new Map(leaves.map(l => [l.id, l]));
    for (const id of state.graphBuilder.selectedBranchIds) {
      const leaf = leafById.get(id);
      if (!leaf) continue;
      const chip = document.createElement('span');
      chip.className = 'graph-branch-chip';
      chip.style.setProperty('--chip-color', leaf.color);
      chip.title = leaf.trail;
      chip.innerHTML = `
        <span class="graph-branch-swatch" aria-hidden="true"></span>
        <span class="graph-branch-code">${escapeHtml(leaf.code)}</span>
      `;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'graph-branch-remove';
      remove.setAttribute('aria-label', `Remove ${leaf.trail}`);
      remove.textContent = '×';
      remove.addEventListener('click', () => {
        state.graphBuilder.selectedBranchIds = state.graphBuilder.selectedBranchIds.filter(item => item !== leaf.id);
        renderGraphStudio();
      });
      chip.append(remove);
      chipsWrap.append(chip);
    }
  }

  const previewSlot = byId('graph-preview-slot');
  if (previewSlot) {
    previewSlot.replaceChildren(
      buildCustomGraphStage(
        { metricId: state.graphBuilder.metricId, branchIds: state.graphBuilder.selectedBranchIds, zoom: state.graphBuilder.zoom },
        leaves
      )
    );
  }
}

byId('toggle-sensitivity-panel')?.addEventListener('click', () => {
  const next = !state.sensitivityOpen;
  setSensitivityPanelOpen(next);
  if (next) {
    requestAnimationFrame(() => {
      byId('path-report-panel')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }
});

byId('graph-y-metric')?.addEventListener('change', event => {
  state.graphBuilder.metricId = event.target.value;
  renderGraphStudio();
});

byId('graph-branch-select')?.addEventListener('change', event => {
  const id = event.target.value;
  if (!id) return;
  if (!state.graphBuilder.selectedBranchIds.includes(id)) {
    state.graphBuilder.selectedBranchIds.push(id);
  }
  renderGraphStudio();
});

byId('graph-clear-all')?.addEventListener('click', () => {
  state.graphBuilder.selectedBranchIds = [];
  state.graphBuilder.zoom = null;
  renderGraphStudio();
});
