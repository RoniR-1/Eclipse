const byId = id => document.getElementById(id);

const SESSION_KEY_STORAGE = 'financial_twin_api_key';

const state = {
  apiKey: (() => { try { return sessionStorage.getItem(SESSION_KEY_STORAGE) || ''; } catch { return ''; } })(),
  serverHasApiKey: false,
  stage: 'intake',
  resultsView: 'timeline', // 'timeline' | 'tree'
  drawerOpen: false,
  chartMetric: 'net_worth_chf',
  extraction: null,
  years: 5,
  nodes: new Map(),
  narratives: new Map(),
  activeBranchId: 'main',
  selectedId: null,
  network: null,
  graphNodes: null,
  graphEdges: null,
  revision: 0,
  openDetails: new Set(),
  spineRaf: null,
  spineNodesMeta: [],
  astralRaf: null,
  treeHoverId: null,
  treeRenderData: null,
};

const BRANCH_PALETTE = [
  '#ffa159', // Original path — warm amber
  '#f0cb88', // Soft champagne gold
  '#f08e6e', // Warm terracotta
  '#dc8ba6', // Dusty rose-copper
  '#9ec4a3', // Soft sage
  '#c9a3dc', // Muted warm lavender
];

const money = value =>
  new Intl.NumberFormat('en-CH', { style: 'currency', currency: 'CHF', maximumFractionDigits: 0 }).format(value);

const moneySigned = value => {
  if (Math.abs(value) < 0.5) return 'CHF 0';
  return `${value > 0 ? '+' : '−'}${money(Math.abs(value))}`;
};

const compactChf = value => {
  const sign = value < 0 ? '−' : '';
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${sign}CHF ${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${sign}CHF ${Math.round(abs / 1000)}k`;
  if (abs >= 1_000) return `${sign}CHF ${(abs / 1_000).toFixed(1)}k`;
  return `${sign}CHF ${Math.round(abs)}`;
};

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

function drawOrganicOrb(ctx, x, y, t, seed, { selected, isFork, isShortfall, maxHalo }) {
  const pulse = organicWave(t * 0.65, seed);
  const scale = (selected ? 1.24 : isFork ? 1.12 : 1.0) * (1 + pulse * 0.08);
  const baseR = 9.5 * scale;

  const auraColor = isShortfall ? [255, 107, 91] : [255, 148, 70];
  const midColor = isShortfall ? [255, 178, 166] : [255, 210, 164];
  const coreColor = isShortfall ? [255, 242, 238] : [255, 248, 238];

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
  const cssW = canvas.clientWidth || 136;
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
  const t = reduced ? 2.1 : nowMs * 0.001;
  const baseCenterX = cssW * 0.44;
  const shellRect = shell.getBoundingClientRect();

  // Gently meandering, free-moving orb positions along the vertical journey
  const points = [];
  listItems.forEach((li, i) => {
    const meta = state.spineNodesMeta[i];
    if (!meta) return;
    const baseY = li.offsetTop + 32;
    const meanderX = Math.sin(i * 0.85 + 0.4) * Math.min(14, cssW * 0.11);
    const driftX = organicWave(t * 0.46, meta.seed) * 4.5;
    const driftY = organicWave(t * 0.39, meta.seed + 5.7) * 2.6;
    const forkEl = li.querySelector('.vnode-fork .fork-destinations');
    const forkTargetY = forkEl ? (forkEl.getBoundingClientRect().top - shellRect.top + 26) : null;

    points.push({
      x: baseCenterX + meanderX + driftX,
      y: baseY + driftY,
      meta,
      selected: meta.id === state.selectedId,
      forkTargetY,
    });
  });

  // 1. Undulating lava-lamp filament connecting consecutive orbs
  if (points.length > 1) {
    const drawFilament = (lineWidth, strokeStyle) => {
      ctx.beginPath();
      for (let i = 0; i < points.length - 1; i++) {
        const a = points[i];
        const b = points[i + 1];
        const span = Math.max(1, b.y - a.y);
        const steps = Math.max(10, Math.ceil(span / 10));
        for (let s = 0; s <= steps; s++) {
          if (i > 0 && s === 0) continue;
          const u = s / steps;
          const y = a.y + span * u;
          const linX = a.x + (b.x - a.x) * u;
          const env = Math.sin(Math.PI * u);
          const sway = organicWave(t * 0.68 - y * 0.0048, i * 2.93 + 0.6) * 11 * env;
          const x = linX + sway;
          if (i === 0 && s === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
      }
      ctx.strokeStyle = strokeStyle;
      ctx.lineWidth = lineWidth;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.stroke();
    };

    const topY = points[0].y;
    const botY = points[points.length - 1].y;
    const glowGrad = ctx.createLinearGradient(0, topY, 0, botY);
    glowGrad.addColorStop(0, 'rgba(255, 162, 92, 0.22)');
    glowGrad.addColorStop(0.5, 'rgba(255, 138, 61, 0.16)');
    glowGrad.addColorStop(1, 'rgba(255, 120, 50, 0.09)');

    const coreGrad = ctx.createLinearGradient(0, topY, 0, botY);
    coreGrad.addColorStop(0, 'rgba(255, 228, 198, 0.65)');
    coreGrad.addColorStop(0.5, 'rgba(255, 168, 102, 0.50)');
    coreGrad.addColorStop(1, 'rgba(255, 138, 61, 0.28)');

    drawFilament(10, glowGrad);
    drawFilament(1.85, coreGrad);

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

  // 2. Sweeping luminous branch arcs physically curving from each fork orb into its divergence block!
  for (const pt of points) {
    if (!pt.meta.isFork || pt.forkTargetY === null) continue;
    const endX = cssW + 2;
    const endY = pt.forkTargetY;
    const midY = pt.y + (endY - pt.y) * 0.58;
    const sway = organicWave(t * 0.55, pt.meta.seed + 9.2) * 5;

    const cp1x = pt.x + 8;
    const cp1y = midY + sway;
    const cp2x = pt.x + (endX - pt.x) * 0.45;
    const cp2y = endY - 4 - sway * 0.5;

    const arcGrad = ctx.createLinearGradient(pt.x, pt.y, endX, endY);
    arcGrad.addColorStop(0, 'rgba(255, 224, 190, 0.78)');
    arcGrad.addColorStop(0.65, 'rgba(255, 154, 77, 0.58)');
    arcGrad.addColorStop(1, 'rgba(255, 138, 61, 0.32)');

    // Wide soft branch glow
    ctx.beginPath();
    ctx.moveTo(pt.x, pt.y);
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, endX, endY);
    ctx.strokeStyle = 'rgba(255, 146, 68, 0.16)';
    ctx.lineWidth = 8;
    ctx.lineCap = 'round';
    ctx.stroke();

    // Crisp glowing branch stream
    ctx.beginPath();
    ctx.moveTo(pt.x, pt.y);
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, endX, endY);
    ctx.strokeStyle = arcGrad;
    ctx.lineWidth = 1.9;
    ctx.stroke();
  }

  // 3. Draw glowing morphing orbs at each milestone
  for (const pt of points) {
    const maxHalo = Math.max(18, Math.min(pt.x - 1, cssW - pt.x - 1));
    drawOrganicOrb(ctx, pt.x, pt.y, t, pt.meta.seed, {
      selected: pt.selected,
      isFork: pt.meta.isFork,
      isShortfall: pt.meta.isShortfall,
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

function setDrawerOpen(open) {
  state.drawerOpen = open;
  const active = open && state.stage === 'results' && state.resultsView === 'timeline';
  document.body.classList.toggle('drawer-open', active);
  byId('drawer-backdrop').hidden = !active;
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
  if (isTree) setDrawerOpen(false);
  mountInspectorForView();

  if (focusNodeId && state.nodes.has(focusNodeId)) {
    state.selectedId = focusNodeId;
  }

  if (isTree) {
    stopSpineAnimation();
    requestAnimationFrame(() => {
      renderTreeGraph({ focusId: focusNodeId || state.selectedId, fit: !focusNodeId });
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

function updateApiStatusUi() {
  const connected = Boolean(state.apiKey || state.serverHasApiKey);
  byId('api-status-dot')?.classList.toggle('is-connected', connected);
  const note = byId('api-status-note');
  if (!note) return;
  if (state.apiKey) note.textContent = 'Using your API key for this browser session.';
  else if (state.serverHasApiKey) note.textContent = 'Server .env API key is configured. Enter one above to override for this session.';
  else note.textContent = 'A key entered here stays in this browser session.';
}

async function checkServerConfig() {
  try {
    const res = await fetch('/api/config');
    if (res.ok) {
      const data = await res.json();
      state.serverHasApiKey = Boolean(data.has_api_key);
    }
  } catch {
    // Ignore
  }
  updateApiStatusUi();
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

    branchMeta.set(bid, {
      id: bid,
      label,
      firstNode: first,
      tipNode: tip,
      forkMonth: first.parent_id ? state.nodes.get(first.parent_id)?.month ?? 0 : 0,
      forkParentId: first.parent_id,
      chain,
    });
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
  if (node.month % 3 === 0) {
    const yrs = Math.floor(node.month / 12);
    const rem = node.month % 12;
    return yrs === 0 ? `Month ${rem}` : `Year ${yrs}, Month ${rem}`;
  }
  if (node.state.cash_chf < 0) return 'Cash shortfall';
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
  let firstBreachId = null;
  for (let i = 1; i < chain.length; i++) {
    if (chain[i].state.cash_chf < 0 && chain[i - 1].state.cash_chf >= 0) {
      firstBreachId = chain[i].id;
      break;
    }
  }

  return chain.filter(node => {
    if (node.month === 0 || node.id === tip.id) return true;
    if (node.month % 3 === 0) return true;
    if (index.forkNodeIds.has(node.id)) return true;
    if (node.event_summary && node.event_summary !== 'Monthly update') return true;
    if (node.id === firstBreachId) return true;
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
  byId('scenario-summary').textContent = data.summary;
  byId('assumptions').textContent = data.assumptions ? `Assumptions: ${data.assumptions}` : '';
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
    const output = document.createElement('output');
    output.className = 'slider-value';
    output.htmlFor = label.htmlFor;
    output.textContent = formatVariable(variable, variable.value);
    heading.append(label, output);

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
    input.addEventListener('input', () => {
      output.textContent = formatVariable(variable, input.value);
    });

    const ends = document.createElement('div');
    ends.className = 'range-ends';
    const min = document.createElement('span');
    const max = document.createElement('span');
    min.textContent = formatVariable(variable, variable.min);
    max.textContent = formatVariable(variable, variable.max);
    ends.append(min, max);

    item.append(heading, description, input, ends);
    container.append(item);
  }
}

/* ==========================================================================
   STAGE 3A: OPEN ASTRAL TIMELINE + CORNER MINIMAP + END SUMMARY
   ========================================================================== */

function renderTimelineView() {
  if (!state.nodes.size) return;
  const index = buildTreeIndex();
  if (!index.branchMeta.has(state.activeBranchId)) {
    state.activeBranchId = index.branchIds[0];
  }
  const activeMeta = index.branchMeta.get(state.activeBranchId);
  const chain = activeMeta.id === 'main' || activeMeta.id === 'root'
    ? activeMeta.chain
    : index.nodesByBranch.get(activeMeta.id);
  const milestones = getPathMilestones(chain, index);

  if (!state.selectedId || !chain.some(n => n.id === state.selectedId)) {
    state.selectedId = activeMeta.firstNode.month > 0 ? activeMeta.firstNode.id : chain[0].id;
  }

  const horizonYears = (activeMeta.tipNode.month / 12).toFixed(activeMeta.tipNode.month % 12 ? 1 : 0);
  byId('active-path-kicker').textContent = activeMeta.label.toUpperCase();
  byId('active-path-subtitle').textContent = activeMeta.id === 'main' || activeMeta.id === 'root'
    ? `Your ${horizonYears}-year baseline projection (every 3 months).`
    : `Diverged at ${formatMonthTime(activeMeta.forkMonth)} · compared against your Original path.`;

  renderVerticalTimeline(milestones, activeMeta, index);
  renderTreeMinimap(index);
  renderEndSummary(activeMeta, index);
  renderTrajectoryChart(activeMeta, index);
  startSpineAnimation();
}

function renderVerticalTimeline(milestones, activeMeta, index) {
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

    const seed = ((idx + 1) * 2.317) + (node.month * 0.41);
    state.spineNodesMeta.push({ id: node.id, isFork, isShortfall, seed });

    const li = document.createElement('li');
    li.className = `vnode${isSelected ? ' is-selected' : ''}${isFork ? ' is-fork' : ''}`;
    li.dataset.nodeId = node.id;
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
    dateEl.textContent = `${node.date} · ${formatMonthTime(node.month)}`;
    eyebrow.append(dateEl);

    if (isBranch && node.branch_id !== 'root' && node.branch_id !== 'main') {
      const base = index.baselineByMonth.get(node.month);
      if (base) {
        const delta = node.state.net_worth_chf - base.state.net_worth_chf;
        const deltaEl = document.createElement('span');
        deltaEl.className = `vnode-delta ${delta > 0.5 ? 'is-pos' : delta < -0.5 ? 'is-neg' : 'is-flat'}`;
        deltaEl.textContent = `${moneySigned(delta)} vs. original`;
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

    content.append(eyebrow, heading, prose, figures);

    if (isShortfall) {
      const warn = document.createElement('p');
      warn.className = 'vnode-shortfall';
      warn.textContent = `Cash shortfall of ${money(Math.abs(s.cash_chf))} under current assumptions.`;
      content.append(warn);
    }

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

      for (const child of directChildren) {
        const bMeta = index.branchMeta.get(child.branch_id);
        if (!bMeta) continue;
        const isCurrent = activeMeta.chain.some(n => n.id === child.id);
        const tipWorth = bMeta.tipNode.state.net_worth_chf;
        const baseTip = index.baselineByMonth.get(bMeta.tipNode.month);
        const diffNote = bMeta.id !== 'main' && bMeta.id !== 'root' && baseTip
          ? ` · ${moneySigned(tipWorth - baseTip.state.net_worth_chf)} vs. original`
          : '';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `fork-dest-btn${isCurrent ? ' is-current' : ''}`;
        btn.innerHTML = `
          <span class="fork-dest-left">
            <span class="fork-dest-orb" aria-hidden="true"></span>
            <span>
              <span class="fork-dest-name">${escapeHtml(bMeta.label)}</span>
              <span class="fork-dest-sub">Ends at ${escapeHtml(money(tipWorth))}${escapeHtml(diffNote)}</span>
            </span>
          </span>
          <span class="fork-dest-action">${isCurrent ? 'Current path' : 'Follow →'}</span>
        `;
        btn.addEventListener('click', event => {
          event.stopPropagation();
          switchActivePath(child.branch_id, { focusNodeId: child.id });
        });
        destList.append(btn);
      }

      forkBlock.append(forkHead, destList);
      content.append(forkBlock);
    }

    li.append(dotBtn, content);
    list.append(li);
  });
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

  // Put 'root' and 'main' on the same lane 0 if both appear
  let colorIndex = 0;
  for (const bid of orderedBranches) {
    if (bid === 'main' && laneOffsets.has('root')) {
      laneOffsets.set('main', laneOffsets.get('root'));
      branchColors.set('main', BRANCH_PALETTE[0]);
      continue;
    }
    laneOffsets.set(bid, nextLane);
    branchColors.set(bid, bid === 'root' || bid === 'main' ? BRANCH_PALETTE[0] : BRANCH_PALETTE[Math.max(1, colorIndex) % BRANCH_PALETTE.length]);
    colorIndex += 1;
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

  // Follow the exact same event-only tree structure as the Full Tree View (falling back to start/end if no events)
  let milestones = treeEventNodes();
  if (milestones.length <= 1) {
    for (const bid of index.branchIds) {
      const tip = index.branchMeta.get(bid)?.tipNode;
      if (tip && !milestones.some(n => n.id === tip.id)) milestones.push(tip);
    }
  }

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
    path.setAttribute('stroke-width', edge.isActiveEdge ? '2.2' : '1.4');
    path.setAttribute('stroke-opacity', edge.isActiveEdge ? '0.96' : '0.48');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('class', 'minimap-edge-hit');
    path.addEventListener('click', () => {
      const targetBranch = edge.branchId === 'root' ? 'main' : edge.branchId;
      switchActivePath(targetBranch, { focusNodeId: edge.to });
    });
    svg.append(path);
    edgeEls.set(edge.id, { el: path, edge });
  }

  const applyMinimapHighlight = hoveredId => {
    if (!hoveredId) {
      for (const { el, edge } of edgeEls.values()) {
        el.setAttribute('stroke-opacity', edge.isActiveEdge ? '0.96' : '0.48');
        el.setAttribute('stroke-width', edge.isActiveEdge ? '2.2' : '1.4');
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
      const bLabel = index.branchMeta.get(node.branch_id === 'root' ? 'main' : node.branch_id)?.label || 'Original path';
      const pBid = parentBranchOf.get(node.branch_id);
      const pLabel = pBid ? index.branchMeta.get(pBid === 'root' ? 'main' : pBid)?.label : null;
      const origin = pLabel ? `From ${pLabel} · ` : '';
      tooltip.innerHTML = `<strong>${escapeHtml(milestoneTitle(node))}</strong><span>${escapeHtml(origin)}${escapeHtml(bLabel)} · ${escapeHtml(node.date)} · ${escapeHtml(money(node.state.net_worth_chf))}</span>`;
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
    g.addEventListener('click', () => {
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
  const baseTip = index.baselineByMonth.get(tip.month);
  const isBranch = activeMeta.id !== 'main' && activeMeta.id !== 'root';

  let minCashNode = chain[0];
  let shortfallMonths = 0;
  for (const n of chain) {
    if (n.state.cash_chf < minCashNode.state.cash_chf) minCashNode = n;
    if (n.state.cash_chf < 0) shortfallMonths += 1;
  }

  const nwDelta = isBranch && baseTip ? tip.state.net_worth_chf - baseTip.state.net_worth_chf : null;
  const cashDelta = isBranch && baseTip ? tip.state.cash_chf - baseTip.state.cash_chf : null;
  const yr = (tip.month / 12).toFixed(tip.month % 12 ? 1 : 0);

  const stats = [
    {
      label: `Ending net worth (Year ${yr})`,
      value: money(tip.state.net_worth_chf),
      sub: nwDelta !== null
        ? `${moneySigned(nwDelta)} vs. Original path (${money(baseTip.state.net_worth_chf)})`
        : `${moneySigned(tip.state.net_worth_chf - chain[0].state.net_worth_chf)} from starting point`,
    },
    {
      label: 'Ending cash',
      value: money(tip.state.cash_chf),
      danger: minCashNode.state.cash_chf < 0,
      sub: minCashNode.state.cash_chf < 0
        ? `Cash dips to ${money(minCashNode.state.cash_chf)} (${shortfallMonths} mo in shortfall)`
        : cashDelta !== null
          ? `${moneySigned(cashDelta)} vs. Original · lowest ${money(minCashNode.state.cash_chf)}`
          : `Lowest point: ${money(minCashNode.state.cash_chf)} (${minCashNode.date})`,
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
}

function renderTrajectoryChart(activeMeta, index) {
  const wrap = byId('trajectory-chart');
  const legend = byId('trajectory-legend');
  const metric = state.chartMetric;
  byId('trajectory-title').textContent = metric === 'cash_chf' ? 'LIQUID CASH OVER TIME' : 'NET WORTH OVER TIME';

  const activeChain = activeMeta.chain;
  const baseChain = index.branchMeta.get('main')?.chain || [];
  const isBranch = activeMeta.id !== 'main' && activeMeta.id !== 'root' && baseChain.length > 0;

  const allValues = activeChain.map(n => n.state[metric]);
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

  svg.innerHTML = `
    <defs>
      <linearGradient id="trajGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#ff8a3d" stop-opacity="0.30" />
        <stop offset="100%" stop-color="#ff8a3d" stop-opacity="0.01" />
      </linearGradient>
    </defs>
    ${gridLines}
    ${zeroLine}
    <path d="${areaD}" fill="url(#trajGrad)" />
    ${isBranch ? `<path d="${basePathD}" fill="none" stroke="#a88770" stroke-width="1.8" stroke-dasharray="5 5" opacity="0.85" />` : ''}
    <path d="${activePathD}" fill="none" stroke="#ffa159" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
    ${yearTicks}
  `;

  wrap.replaceChildren(svg);
  legend.innerHTML = `
    <div class="legend-items">
      <span class="legend-item"><i class="legend-swatch is-active"></i> ${escapeHtml(activeMeta.label)}</span>
      ${isBranch ? '<span class="legend-item"><i class="legend-swatch is-baseline"></i> Original path</span>' : ''}
    </div>
    <span>${activeChain[0].date} → ${activeChain[activeChain.length - 1].date}</span>
  `;
}

function switchActivePath(branchId, { focusNodeId = null } = {}) {
  const index = buildTreeIndex();
  if (!index.branchMeta.has(branchId)) return;
  state.activeBranchId = branchId;
  const meta = index.branchMeta.get(branchId);
  const targetId = focusNodeId && meta.chain.some(n => n.id === focusNodeId)
    ? focusNodeId
    : (meta.firstNode.month > 0 ? meta.firstNode.id : meta.chain[0].id);

  state.selectedId = targetId;
  renderTimelineView();
  selectNode(targetId, { openDrawer: false });
  if (focusNodeId) scrollMilestoneIntoView(targetId);
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

function applyTreeEdgeHighlight(focusNodeId) {
  if (!state.graphEdges || !state.treeRenderData) return;
  const { edges, activeSet } = state.treeRenderData;
  const lineage = focusNodeId ? getLineageIds(focusNodeId, edges) : null;
  const updates = edges.map(edge => {
    const isLit = lineage ? lineage.edgeIds.has(edge.id) : (activeSet.has(edge.from) && activeSet.has(edge.to));
    const isDimmed = Boolean(lineage && !isLit);
    return {
      id: edge.id,
      width: isLit ? 3.2 : isDimmed ? 1.2 : 2,
      color: {
        color: isDimmed ? 'rgba(138, 90, 60, 0.25)' : edge.baseColor,
        highlight: edge.baseColor,
        hover: edge.baseColor,
      },
    };
  });
  state.graphEdges.update(updates);
}

function renderTreeGraph({ fit = false, focusId = null } = {}) {
  if (!state.nodes.size) return;
  const index = buildTreeIndex();
  const milestones = treeEventNodes();
  const visible = new Set(milestones.map(n => n.id));
  const activeSet = new Set(index.branchMeta.get(state.activeBranchId)?.chain.map(n => n.id) || []);

  const layout = buildPlanarTreeLayout(milestones, index);
  const { dates, laneOffsets, branchColors, parentBranchOf } = layout;

  const rows = new Map();
  const graphNodes = milestones.map(node => {
    const status = nodeHealth(node);
    const isSelected = node.id === state.selectedId;
    const isActive = activeSet.has(node.id);
    const bColor = branchColors.get(node.branch_id) || BRANCH_PALETTE[0];
    const rowKey = `${node.branch_id}:${node.date}`;
    const rowOffset = rows.get(rowKey) || 0;
    rows.set(rowKey, rowOffset + 1);

    // On the first node of a non-main branch, show a quiet origin cue so parentage is immediately clear
    const isBranchOrigin = index.branchMeta.get(node.branch_id)?.firstNode?.id === node.id && node.branch_id !== 'main' && node.branch_id !== 'root';
    const parentBid = parentBranchOf.get(node.branch_id);
    const parentLabel = parentBid ? index.branchMeta.get(parentBid === 'root' ? 'main' : parentBid)?.label : null;
    const originLine = isBranchOrigin && parentLabel
      ? `↳ from ${parentLabel.length > 24 ? `${parentLabel.slice(0, 22).trim()}…` : parentLabel}\n`
      : '';

    const flags = [
      node.state.cash_chf < 0 ? 'Negative cash' : '',
      node.state.net_worth_chf < 0 ? 'Negative net worth' : '',
      status.level === 'warn' ? 'Low cash reserve' : '',
    ].filter(Boolean);
    const statusSuffix = flags.length ? `\n● ${status.label}\n${flags.join('\n')}` : '';

    const phase = node.state.active_variables?.phase;
    const initialValues = node.parent_id === null
      ? `\nCash ${money(node.state.cash_chf)}${phase ? `\nIncome ${money(phase.monthly_income_chf)}/mo\nExpenses ${money(node.state.active_variables.expense_terms?.monthly_expenses_chf ?? phase.monthly_expenses_chf)}/mo` : ''}`
      : '';

    return {
      id: node.id,
      label: `${originLine}${milestoneTitle(node)}\n${money(node.state.net_worth_chf)}${initialValues}${statusSuffix}`,
      x: dates.indexOf(node.date) * 310,
      y: (laneOffsets.get(node.branch_id) + rowOffset) * 165,
      color: {
        background: isSelected ? '#4a2614' : isActive ? '#341e13' : '#27160e',
        border: status.issues ? status.color : isSelected ? '#fff0de' : bColor,
        highlight: { background: '#4a2614', border: status.issues ? status.color : '#fff0de' },
      },
      font: { color: '#f6e9dc', size: 14, face: 'DM Sans', multi: false },
      borderWidth: isSelected ? 3 : isBranchOrigin ? 2.2 : 1.6,
      shape: 'box',
      margin: 16,
      widthConstraint: { minimum: 175, maximum: 220 },
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
    const isActiveEdge = activeSet.has(node.id) && activeSet.has(parentId);

    edges.push({
      id: `${parentId}>${node.id}`,
      from: parentId,
      to: node.id,
      arrows: { to: { enabled: true, scaleFactor: 0.75 } },
      baseColor,
      width: isActiveEdge ? 3 : 2,
      color: { color: baseColor, highlight: baseColor, hover: baseColor },
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
      if (state.nodes.has(params.nodes[0])) selectNode(params.nodes[0]);
    });
    state.network.on('hoverNode', params => {
      if (state.nodes.has(params.node)) {
        state.treeHoverId = params.node;
        applyTreeEdgeHighlight(params.node);
      }
    });
    state.network.on('blurNode', () => {
      state.treeHoverId = null;
      const focus = state.graphNodes?.get(state.selectedId) ? state.selectedId : null;
      applyTreeEdgeHighlight(focus);
    });
    // Time axis drawn along the bottom of the date columns
    state.network.on('afterDrawing', ctx => {
      const nodes = treeEventNodes().filter(node => state.graphNodes?.get(node.id));
      if (!nodes.length) return;
      const columns = [...new Set(nodes.map(node => node.date))].sort();
      const scale = state.network.getScale();
      const bottom = Math.max(...nodes.map(node => state.network.getBoundingBox(node.id).bottom));
      const axisY = bottom + 28;

      ctx.save();
      ctx.strokeStyle = 'rgba(246, 233, 220, 0.38)';
      ctx.lineWidth = 1 / scale;
      ctx.beginPath();
      ctx.moveTo(-115, axisY);
      ctx.lineTo((columns.length - 1) * 310 + 115, axisY);
      for (const colIdx of columns.keys()) {
        ctx.moveTo(colIdx * 310, axisY);
        ctx.lineTo(colIdx * 310, axisY + 6 / scale);
      }
      ctx.stroke();

      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = '#d2b8a3';
      ctx.font = `${Math.min(14, 14 / scale)}px DM Sans`;
      for (const [colIdx, date] of columns.entries()) {
        const node = nodes.find(item => item.date === date);
        ctx.fillText(`${date} · ${formatMonthTime(node.month)}`, colIdx * 310, axisY + 10 / scale);
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
    option.textContent = 'No events to display';
    select.append(option);
  }
  for (const node of milestones) {
    const option = document.createElement('option');
    option.value = node.id;
    const status = nodeHealth(node);
    const bLabel = index.branchMeta.get(node.branch_id === 'root' ? 'main' : node.branch_id)?.label || 'Original path';
    option.textContent = `${bLabel} · ${node.date} — ${milestoneTitle(node)} — ${money(node.state.net_worth_chf)}${status.issues ? ` — ${status.label}` : ''}`;
    select.append(option);
  }

  const resolvedFocus = resolveTreeFocusId(focusId, visible, index);
  const selectedInTree = visible.has(state.selectedId) ? state.selectedId : (resolvedFocus || null);
  select.value = selectedInTree || '';
  applyTreeEdgeHighlight(state.treeHoverId || selectedInTree);

  state.network.redraw();
  if (resolvedFocus && visible.has(resolvedFocus)) {
    const pos = state.network.getPosition(resolvedFocus);
    const width = byId('timeline-graph').clientWidth || 1000;
    state.network.moveTo({
      position: pos,
      scale: 0.8,
      offset: { x: -width * 0.25, y: 0 },
      animation: { duration: 360, easingFunction: 'easeInOutQuad' },
    });
  } else if (fit && milestones.length) {
    const first = graphNodes.find(node => node.id === milestones[0].id);
    const width = byId('timeline-graph').clientWidth || 1000;
    state.network.moveTo({
      position: { x: first?.x ?? 0, y: first?.y ?? 0 },
      scale: 0.8,
      offset: { x: -width * 0.25, y: 0 },
    });
  }
}

/* ==========================================================================
   SHARED MOMENT DRAWER / TREE INSPECTOR
   ========================================================================== */

function detailLabel(key) {
  if (key === '__contributions__') return 'Investment contributions';
  return key.replace(/_chf$/, ' (CHF)').replace(/_/g, ' ').replace(/^./, letter => letter.toUpperCase());
}

function detailValue(key, value) {
  if (typeof value === 'number') {
    return key.endsWith('_chf') ? money(value) : new Intl.NumberFormat('en-CH', { maximumFractionDigits: 4 }).format(value);
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
  const baseNode = node.branch_id !== 'root' && node.branch_id !== 'main'
    ? index.baselineByMonth.get(node.month)
    : null;

  const header = document.createElement('div');
  header.className = 'inspector-header';

  const kicker = document.createElement('span');
  kicker.className = 'card-kicker';
  kicker.textContent = `${node.date} · ${formatMonthTime(node.month).toUpperCase()}`;

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

  if (baseNode) {
    const nwDiff = s.net_worth_chf - baseNode.state.net_worth_chf;
    const cashDiff = s.cash_chf - baseNode.state.cash_chf;
    const compare = document.createElement('div');
    compare.className = 'inspector-compare';
    compare.textContent = `Vs. Original path: ${moneySigned(nwDiff)} net worth · ${moneySigned(cashDiff)} cash`;
    header.append(compare);
  }

  const narrativeSec = document.createElement('div');
  narrativeSec.className = 'inspector-section';
  const narrativeTitle = document.createElement('h3');
  narrativeTitle.textContent = 'How you got here';
  const narrativeText = document.createElement('p');
  narrativeText.id = 'node-narrative';
  narrativeText.className = 'inspector-narrative';
  narrativeText.textContent = state.narratives.get(node.id) || node.narrative || buildMilestoneProse(node, null);
  narrativeSec.append(narrativeTitle, narrativeText);
  if (node.event_narrative) {
    const eventText = document.createElement('p');
    eventText.className = 'event-narrative';
    eventText.textContent = node.event_narrative;
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

  const editForm = document.createElement('form');
  editForm.className = 'branch-form node-edit-form';
  const editMonth = Math.max(0, node.month - 1);
  const editPhase = node.scenario.phases.find(phase => phase.start_month <= editMonth && (phase.end_month === null || editMonth < phase.end_month));
  const editCost = node.scenario.one_off_costs.filter(cost => cost.month === editMonth).reduce((sum, cost) => sum + cost.amount_chf, 0);
  const terms = s.active_variables.expense_terms || {};
  const startingExpense = terms.monthly_expenses_chf ?? editPhase.monthly_expenses_chf;
  const fields = [
    ...(node.month === 0 ? [{ name: 'cash_chf', label: 'Starting cash (CHF)', value: s.cash_chf }] : []),
    { name: 'monthly_income_chf', label: 'Monthly income (CHF)', value: editPhase.monthly_income_chf },
    { name: 'monthly_expenses_chf', label: 'Monthly expenses at start of year (CHF)', value: startingExpense },
    { name: 'yearly_expenses_chf', label: 'Annual expenses at start of year (CHF)', value: startingExpense * 12 },
    ...(node.month === 0 ? [{ name: 'annual_inflation', label: 'Default annual inflation (%)', value: node.scenario.annual_inflation, percent: true }] : []),
    { name: 'year_inflation', label: `Inflation for ${terms.year ?? node.date.slice(0, 4)} (%)`, value: terms.year_inflation ?? null, percent: true, optional: true, placeholder: 'Use default annual inflation' },
    { name: 'inflation_exempt', label: 'Fixed by contract, unaffected by inflation', value: terms.inflation_exempt ?? false, type: 'checkbox' },
    { name: 'duration_months', label: 'Expense duration (months)', value: terms.duration_months ?? null, optional: true, placeholder: 'Ongoing' },
    { name: 'one_off_cost_chf', label: 'One-time cost (CHF)', value: editCost },
    { name: 'date', label: 'Date', value: node.date, type: 'date' },
  ];
  const editTitle = document.createElement('h3');
  editTitle.textContent = 'Edit this point';
  editForm.append(editTitle);
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
    input.value = field.value === null ? '' : field.percent ? Number((field.value * 100).toFixed(8)) : field.value;
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
  monthlyExpenses.addEventListener('input', () => { yearlyExpenses.value = Number(monthlyExpenses.value) * 12; });
  yearlyExpenses.addEventListener('input', () => { monthlyExpenses.value = Number(yearlyExpenses.value) / 12; });
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
      else if (input.type === 'number') value = input.value === '' ? null : Number(input.value) / (field.percent ? 100 : 1);
      else value = input.value;
      if (value !== field.value && !(field.percent && value !== null && field.value !== null && Math.abs(value - field.value) < 1e-10)) edits[field.name] = value;
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
      for (const updated of result.nodes) state.nodes.set(updated.id, updated);
      if (state.resultsView === 'tree') renderTreeGraph({ focusId: node.id });
      else renderTimelineView();
      selectNode(node.id);
      showToast('Changes saved');
    } catch (error) {
      editError.textContent = error.message;
    } finally {
      editButton.disabled = false;
    }
  });

  const form = document.createElement('form');
  form.id = 'branch-form';
  form.className = 'branch-form';
  form.innerHTML = `
    <h3>What if something changed?</h3>
    <label for="branch-event">Add an event or decision</label>
    <textarea id="branch-event" required maxlength="2000" placeholder="For example, buy a flat, start a master's, or change jobs"></textarea>
    <details class="branch-options">
      <summary>Adjust branch assumptions</summary>
      <div id="branch-sliders"></div>
    </details>
    <label for="branch-months">Explore this branch for</label>
    <div class="select-wrap">
      <select id="branch-months">
        <option value="3">3 months</option>
        <option value="6">6 months</option>
        <option value="12">1 year</option>
        <option value="24">2 years</option>
        <option value="60">5 years</option>
      </select>
    </div>
    <p class="form-error" id="branch-error" role="alert"></p>
    <button class="primary-button wide" type="submit">
      <span class="btn-label">Diverge / Add event</span>
      <span class="btn-icon" aria-hidden="true">✳</span>
    </button>
  `;

  panel.append(header, editForm, narrativeSec, acctSec, form);

  const remaining = state.years * 12 - node.month;
  const monthSelect = form.querySelector('#branch-months');
  if (remaining > 0 && ![3, 6, 12, 24, 60].includes(remaining)) {
    const option = document.createElement('option');
    option.value = remaining;
    option.textContent = `${remaining} months (to 5-year horizon)`;
    monthSelect.append(option);
  }
  monthSelect.value = remaining > 0 ? String(remaining) : '12';

  const slidersRoot = form.querySelector('#branch-sliders');
  for (const variable of (state.extraction?.variables || []).filter(item => item.id !== 'opening_cash_chf')) {
    const item = document.createElement('div');
    item.className = 'branch-slider';
    const label = document.createElement('label');
    label.className = 'branch-toggle';
    const check = document.createElement('input');
    check.type = 'checkbox';
    const text = document.createElement('span');
    text.textContent = variable.label;
    const output = document.createElement('output');
    const current = branchValue(node, variable);
    output.textContent = formatVariable(variable, current);
    label.append(check, text, output);

    const input = document.createElement('input');
    input.type = 'range';
    input.name = variable.id;
    input.min = Math.min(variable.min, current);
    input.max = Math.max(variable.max, current);
    input.step = 'any';
    input.value = current;
    input.disabled = true;
    check.addEventListener('change', () => { input.disabled = !check.checked; });
    input.addEventListener('input', () => { output.textContent = formatVariable(variable, input.value); });
    item.append(label, input);
    slidersRoot.append(item);
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const errorBox = form.querySelector('#branch-error');
    errorBox.textContent = '';
    setButtonLoading(button, true, 'Exploring…', 'Diverge / Add event');
    const revision = state.revision;
    try {
      const variables = Object.fromEntries(
        [...form.querySelectorAll('input[type="range"]:enabled')].map(input => [input.name, Number(input.value)])
      );
      const result = await request('/api/branch', {
        parent: node,
        event: form.querySelector('#branch-event').value.trim(),
        months: Number(monthSelect.value),
        variables,
        api_key: state.apiKey || undefined,
      });
      if (revision !== state.revision) return;
      for (const child of result.nodes) state.nodes.set(child.id, child);
      if (result.nodes.length) {
        const first = result.nodes[0];
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
    } catch (error) {
      errorBox.textContent = error.message;
    } finally {
      setButtonLoading(button, false, 'Exploring…', 'Diverge / Add event');
    }
  });

  if (focusBranchInput) {
    requestAnimationFrame(() => {
      const box = form.querySelector('#branch-event');
      box?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      box?.focus();
    });
  }
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

async function selectNode(id, { openDrawer = false, focusBranchInput = false } = {}) {
  const node = state.nodes.get(id);
  if (!node) return;
  state.selectedId = id;

  if (state.resultsView === 'tree') {
    renderTreeGraph();
    updateFollowPathButton(node);
  } else {
    const existingEl = document.querySelector(`#vertical-timeline .vnode[data-node-id="${CSS.escape(id)}"]`);
    if (existingEl) syncVerticalSelection();
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
      if (el) el.textContent = result.narrative;
    }
  } catch {
    // Keep instant prose fallback
  }
}

function renderResults(nodes) {
  state.revision += 1;
  state.nodes = new Map(nodes.map(node => [node.id, node]));
  state.narratives = new Map();
  state.activeBranchId = nodes.some(n => n.branch_id === 'main') ? 'main' : nodes[0].branch_id;
  state.selectedId = nodes[0].id;
  state.resultsView = 'timeline';
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
byId('open-tree-from-minimap').addEventListener('click', () => setResultsView('tree'));
byId('back-to-timeline').addEventListener('click', () => setResultsView('timeline', { focusNodeId: state.selectedId }));

byId('toggle-minimap').addEventListener('click', () => {
  const dock = byId('minimap-dock');
  const btn = byId('toggle-minimap');
  const collapsed = dock.classList.toggle('is-collapsed');
  btn.textContent = collapsed ? '+' : '–';
  btn.setAttribute('aria-expanded', String(!collapsed));
});

byId('close-drawer').addEventListener('click', () => setDrawerOpen(false));
byId('drawer-backdrop').addEventListener('click', () => setDrawerOpen(false));
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && state.drawerOpen) setDrawerOpen(false);
});

byId('fit-graph').addEventListener('click', () => state.network?.fit({ animation: true }));
byId('milestone-select').addEventListener('change', event => {
  const id = event.target.value;
  selectNode(id);
  if (state.network && id) {
    state.network.moveTo({ position: state.network.getPosition(id), scale: 0.85, animation: { duration: 350 } });
  }
});
byId('follow-selected-path').addEventListener('click', () => {
  const node = state.nodes.get(state.selectedId);
  if (node) state.activeBranchId = node.branch_id === 'root' ? 'main' : node.branch_id;
  setResultsView('timeline', { focusNodeId: state.selectedId });
});

byId('open-settings').addEventListener('click', () => {
  byId('api-key').value = state.apiKey;
  updateApiStatusUi();
  byId('settings-dialog').showModal();
});
byId('settings-form').addEventListener('submit', event => {
  if (event.submitter?.value === 'save') {
    state.apiKey = byId('api-key').value.trim();
    try {
      if (state.apiKey) sessionStorage.setItem(SESSION_KEY_STORAGE, state.apiKey);
      else sessionStorage.removeItem(SESSION_KEY_STORAGE);
    } catch {
      // Ignore
    }
    updateApiStatusUi();
  }
});

byId('edit-story').addEventListener('click', () => showStage('intake'));
byId('adjust-scenario').addEventListener('click', () => showStage('tuning'));
byId('tree-adjust-scenario').addEventListener('click', () => {
  state.resultsView = 'timeline';
  showStage('tuning');
});

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
      api_key: state.apiKey || undefined,
    });
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
  setButtonLoading(button, true, 'Simulating…', 'Simulate future');
  try {
    const variables = Object.fromEntries([...new FormData(event.currentTarget)].map(([key, value]) => [key, Number(value)]));
    const result = await request('/api/simulate', {
      scenario: state.extraction.scenario,
      years: state.years,
      variables,
    });
    renderResults(result.nodes);
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
    renderTuning(state.extraction);
    renderResults(result.nodes);
  } catch (error) {
    showToast(`Could not generate test data: ${error.message}`, { error: true });
  } finally {
    button.disabled = false;
  }
});

// Floating panel drag & minimize (Full Tree View)
function keepPanelOnScreen(panel) {
  if (!panel.style.left || !document.body.classList.contains('world-mode')) return;
  const parent = panel.offsetParent || document.body;
  const bounds = parent.getBoundingClientRect();
  const box = panel.getBoundingClientRect();
  const bar = panel.querySelector('.panel-bar')?.offsetHeight || 40;
  panel.style.left = `${Math.min(Math.max(parseFloat(panel.style.left), 0), Math.max(0, bounds.width - box.width))}px`;
  panel.style.top = `${Math.min(Math.max(parseFloat(panel.style.top), 0), Math.max(0, bounds.height - bar))}px`;
  if (panel.id === 'inspector-panel') {
    panel.style.maxHeight = `${Math.max(bounds.height - parseFloat(panel.style.top) - 14, bar)}px`;
  }
}

for (const panel of document.querySelectorAll('.floating-panel')) {
  const bar = panel.querySelector('.panel-bar');
  const toggle = panel.querySelector('.panel-toggle');
  if (!bar || !toggle) continue;

  toggle.addEventListener('click', () => {
    const minimized = panel.classList.toggle('minimized');
    toggle.textContent = minimized ? '+' : '–';
    toggle.setAttribute('aria-expanded', String(!minimized));
    toggle.setAttribute('aria-label', minimized ? 'Restore panel' : 'Minimize panel');
  });

  bar.addEventListener('pointerdown', event => {
    if (!document.body.classList.contains('world-mode')) return;
    if (event.button !== 0 || event.target.closest('button')) return;
    const parent = panel.offsetParent || document.body;
    const parentBox = parent.getBoundingClientRect();
    const box = panel.getBoundingClientRect();
    const offsetX = event.clientX - box.left;
    const offsetY = event.clientY - box.top;
    Object.assign(panel.style, {
      left: `${box.left - parentBox.left}px`,
      top: `${box.top - parentBox.top}px`,
      right: 'auto',
      bottom: 'auto',
    });
    bar.setPointerCapture(event.pointerId);
    bar.classList.add('dragging');
    const move = moveEvent => {
      panel.style.left = `${moveEvent.clientX - parentBox.left - offsetX}px`;
      panel.style.top = `${moveEvent.clientY - parentBox.top - offsetY}px`;
      keepPanelOnScreen(panel);
    };
    const stop = () => {
      bar.classList.remove('dragging');
      bar.removeEventListener('pointermove', move);
      bar.removeEventListener('pointerup', stop);
      bar.removeEventListener('pointercancel', stop);
    };
    bar.addEventListener('pointermove', move);
    bar.addEventListener('pointerup', stop);
    bar.addEventListener('pointercancel', stop);
  });
}

window.addEventListener('resize', () => {
  document.querySelectorAll('.floating-panel').forEach(keepPanelOnScreen);
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    startAstralBackdrop();
    if (state.stage === 'results' && state.resultsView === 'timeline') startSpineAnimation();
  }
});

checkServerConfig();
startAstralBackdrop();
