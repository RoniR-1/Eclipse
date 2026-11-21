const byId = id => document.getElementById(id);
const state = { apiKey: '', extraction: null, years: 10, nodes: new Map(), narratives: new Map(), selectedId: null, network: null, graphNodes: null, graphEdges: null, revision: 0, presets: [], openDetails: new Set(['breakdown', 'branch-info']) };
const money = value => new Intl.NumberFormat('en-CH', { style: 'currency', currency: 'CHF', maximumFractionDigits: 0 }).format(value);

function showStage(id) {
  for (const stage of ['intake', 'tuning', 'results']) byId(stage).hidden = stage !== id;
  document.body.classList.toggle('world-mode', id === 'results');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function request(path, body) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let data;
  try { data = await response.json(); } catch { throw new Error('The server returned an unexpected response.'); }
  if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}

function formatVariable(variable, value) {
  const number = Number(value);
  if (variable.unit === '%' || variable.unit === 'percent') return `${Number((number * (variable.max <= 1 ? 100 : 1)).toFixed(1))}%`;
  if (variable.unit === 'CHF') return money(number);
  return `${Number(number.toFixed(2))}${variable.unit ? ` ${variable.unit}` : ''}`;
}

function addBaseline(label, value) {
  const row = document.createElement('div');
  row.className = 'baseline-row';
  const name = document.createElement('span');
  name.textContent = label;
  const amount = document.createElement('strong');
  amount.textContent = money(value);
  row.append(name, amount);
  byId('baseline').append(row);
}

function renderTuning(data) {
  byId('scenario-summary').textContent = data.summary;
  byId('assumptions').textContent = data.assumptions ? `Assumptions: ${data.assumptions}` : ''; 
  byId('baseline').replaceChildren();
  const scenario = data.scenario;
  addBaseline('Starting cash', scenario.opening_cash_chf);
  if (scenario.phases?.length) {
    addBaseline('Monthly income', scenario.phases[0].monthly_income_chf);
    addBaseline('Monthly expenses', scenario.phases[0].monthly_expenses_chf);
  }
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
    label.title = variable.description;
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
    input.title = variable.description;
    input.value = variable.value;
    input.setAttribute('aria-describedby', description.id);
    input.addEventListener('input', () => { output.textContent = formatVariable(variable, input.value); });
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

function milestoneTitle(node) {
  if (node.month === 0) return 'Starting point';
  return node.event_summary && node.event_summary !== 'Monthly update' ? node.event_summary : 'Financial check-in';
}

function health(node) {
  const issues = [];
  if (node.state.cash_chf < 0) issues.push(`Cash is ${money(node.state.cash_chf)}. Review income and expenses.`);
  if (node.state.net_worth_chf < 0) issues.push(`Net worth is ${money(node.state.net_worth_chf)}. Review assets and debts.`);
  if (issues.length) return { label: 'Needs attention', color: '#ff6b5b', issues };
  const expenses = node.state.cash_flow.expenses_chf;
  if (expenses > 0 && node.state.cash_chf < expenses * 3) return { label: 'Building resilience', color: '#f2c14e', issues: [`Cash is ${money(node.state.cash_chf)}, below three months of expenses (${money(expenses * 3)}). Review cash and expenses.`] };
  return { label: 'On track', color: '#7fc48a' };
}

function milestoneNodes() {
  return [...state.nodes.values()].filter(node => node.month !== 0 && node.event_summary && node.event_summary !== 'Monthly update');
}

function renderGraph(fit = false) {
  const milestones = milestoneNodes();
  const visible = new Set(milestones.map(node => node.id));
  const lanes = new Map([['root', 0], ['main', 0]]);
  for (const node of milestones) if (!lanes.has(node.branch_id)) lanes.set(node.branch_id, lanes.size - 1);
  const dates = [...new Set(milestones.map(node => node.date))].sort();
  const rows = new Map();
  const laneOffsets = new Map();
  let nextLane = 0;
  for (const branch of lanes.keys()) {
    laneOffsets.set(branch, nextLane);
    const counts = new Map();
    for (const node of milestones.filter(item => item.branch_id === branch)) counts.set(node.date, (counts.get(node.date) || 0) + 1);
    nextLane += Math.max(1, ...counts.values());
  }
  const graphNodes = milestones.map(node => {
    const status = health(node);
    const rowKey = `${node.branch_id}:${node.date}`;
    const rowOffset = rows.get(rowKey) || 0;
    rows.set(rowKey, rowOffset + 1);
    return {
      id: node.id,
      label: `${milestoneTitle(node)}\n${money(node.state.net_worth_chf)}\n● ${status.label}${node.state.cash_chf < 0 ? '\nNegative cash' : ''}${node.state.net_worth_chf < 0 ? '\nNegative net worth' : ''}${status.label === 'Building resilience' ? '\nLow cash reserve' : ''}`,
      x: dates.indexOf(node.date) * 300,
      y: (laneOffsets.get(node.branch_id) + rowOffset) * 160,
      color: { background: node.id === state.selectedId ? '#4a2614' : '#2b1910', border: status.color, highlight: { background: '#4a2614', border: status.color } },
      font: { color: '#f6e9dc', size: 14, face: 'DM Sans', multi: false },
      borderWidth: node.id === state.selectedId ? 3 : 1.5,
      shape: 'box', margin: 16, widthConstraint: { minimum: 170, maximum: 210 },
    };
  });
  const positions = new Map(graphNodes.map(node => [node.id, node]));
  const edges = [];
  for (const node of milestones) {
    let parentId = node.parent_id;
    while (parentId && !visible.has(parentId)) parentId = state.nodes.get(parentId)?.parent_id;
    if (!parentId) continue;
    const parent = positions.get(parentId);
    const child = positions.get(node.id);
    const direction = Math.sign(child.x - parent.x) || 1;
    const exitX = parent.x + direction * 150;
    const entryX = child.x === parent.x ? exitX : child.x - direction * 150;
    const bends = [];
    if (exitX === entryX) {
      if (parent.y !== child.y) bends.push([exitX, parent.y], [entryX, child.y]);
    } else {
      const channelY = child.y - 80;
      bends.push([exitX, parent.y], [exitX, channelY], [entryX, channelY], [entryX, child.y]);
    }
    let from = parentId;
    for (const [index, [x, y]] of bends.entries()) {
      const id = JSON.stringify([parentId, node.id, index]);
      graphNodes.push({ id, x, y, shape: 'dot', size: 0.01, borderWidth: 0, color: 'transparent' });
      edges.push({ id, from, to: id });
      from = id;
    }
    edges.push({ id: JSON.stringify([parentId, node.id]), from, to: node.id, arrows: 'to' });
  }
  if (!state.network) {
    state.graphNodes = new vis.DataSet(graphNodes);
    state.graphEdges = new vis.DataSet(edges);
    state.network = new vis.Network(byId('timeline-graph'), { nodes: state.graphNodes, edges: state.graphEdges }, {
      physics: false, interaction: { hover: true, navigationButtons: false, dragNodes: false },
      nodes: { chosen: false }, edges: { smooth: false, color: { color: '#8a5a3c', highlight: '#ff9a4d' }, width: 2, arrowStrikethrough: false },
      layout: { improvedLayout: false },
    });
    state.network.on('click', params => { if (state.nodes.has(params.nodes[0])) selectNode(params.nodes[0]); });
    state.network.on('afterDrawing', ctx => {
      const nodes = milestoneNodes();
      const columns = [...new Set(nodes.map(node => node.date))].sort();
      if (!nodes.length) return;
      const scale = state.network.getScale();
      const bottom = Math.max(...nodes.map(node => state.network.getBoundingBox(node.id).bottom));
      const axisY = bottom + 24;
      ctx.save();
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.55)';
      ctx.lineWidth = 1 / scale;
      ctx.beginPath();
      ctx.moveTo(-110, axisY);
      ctx.lineTo((columns.length - 1) * 300 + 110, axisY);
      for (const index of columns.keys()) {
        ctx.moveTo(index * 300, axisY);
        ctx.lineTo(index * 300, axisY + 6 / scale);
      }
      ctx.stroke();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = '#f6e9dc';
      ctx.font = `${Math.min(14, 14 / scale)}px DM Sans`;
      for (const [index, date] of columns.entries()) {
        const node = nodes.find(item => item.date === date);
        ctx.fillText(`${date} · Year ${(node.month / 12).toFixed(1)}`, index * 300, axisY + 10 / scale);
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
    const status = health(node);
    option.textContent = `${node.branch_id === 'root' || node.branch_id === 'main' ? 'Original path' : `Branch ${lanes.get(node.branch_id)}`} · ${node.date} — ${milestoneTitle(node)} — ${money(node.state.net_worth_chf)}${status.issues ? ` — ${status.label}: ${status.issues.join('; ')}` : ''}`;
    select.append(option);
  }
  select.value = state.selectedId || '';
  if (fit) {
    const selected = graphNodes.find(node => node.id === state.selectedId);
    // Put the focused node about a quarter of the way across the screen, so the future to its right stays in view.
    const width = byId('timeline-graph').clientWidth;
    state.network.moveTo({ position: { x: selected?.x ?? 0, y: selected?.y ?? 0 }, scale: 0.8, offset: { x: -width * 0.25, y: 0 } });
  }
}

function detailLabel(key) {
  return key.replace(/_chf$/, ' (CHF)').replace(/_/g, ' ').replace(/^./, letter => letter.toUpperCase());
}

function detailValue(key, value) {
  if (typeof value === 'number') return key.endsWith('_chf') ? money(value) : new Intl.NumberFormat('en-CH', { maximumFractionDigits: 4 }).format(value);
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
      else { const empty = document.createElement('p'); empty.className = 'detail-empty'; empty.textContent = 'None'; group.append(empty); }
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
  if (variable.id === 'monthly_income_chf' || variable.id === 'monthly_expenses_chf') return node.scenario.phases.find(phase => phase.start_month <= node.month && (phase.end_month === null || node.month < phase.end_month))?.[variable.id] ?? variable.value;
  return node.state.active_variables.preferences[variable.id] ?? variable.value;
}

function renderInspector(node) {
  const panel = byId('inspector');
  const nodeHealth = health(node);
  panel.replaceChildren();
  const header = document.createElement('div');
  header.className = 'inspector-header';
  const kicker = document.createElement('span');
  kicker.className = 'card-kicker';
  kicker.textContent = `${node.date} · YEAR ${(node.month / 12).toFixed(1)}`;
  const title = document.createElement('h2');
  title.textContent = milestoneTitle(node);
  const status = document.createElement('span');
  status.className = 'health-label';
  status.style.color = nodeHealth.color;
  status.textContent = `● ${nodeHealth.label}`;
  const worth = document.createElement('div');
  worth.className = 'inspector-worth';
  worth.textContent = money(node.state.net_worth_chf);
  const worthLabel = document.createElement('p');
  worthLabel.textContent = 'Projected net worth';
  header.append(kicker, title, status, worth, worthLabel);
  if (nodeHealth.issues) {
    const issues = document.createElement('ul');
    issues.className = 'health-issues';
    for (const issue of nodeHealth.issues) {
      const item = document.createElement('li');
      item.textContent = issue;
      issues.append(item);
    }
    header.append(issues);
  }
  const narrative = document.createElement('div');
  narrative.className = 'narrative';
  const narrativeTitle = document.createElement('h3');
  narrativeTitle.textContent = 'How you got here';
  const narrativeText = document.createElement('p');
  narrativeText.id = 'node-narrative';
  narrativeText.textContent = state.narratives.get(node.id) || node.narrative || 'Preparing your summary…';
  narrative.append(narrativeTitle, narrativeText);
  if (node.event_narrative) {
    const eventText = document.createElement('p');
    eventText.className = 'event-narrative';
    eventText.textContent = node.event_narrative;
    narrative.append(eventText);
  }
  const breakdown = document.createElement('details');
  breakdown.className = 'breakdown';
  rememberOpen(breakdown, 'breakdown');
  const breakdownTitle = document.createElement('summary');
  breakdownTitle.textContent = 'Financial breakdown & active parameters';
  breakdown.append(breakdownTitle);
  appendDetails(breakdown, node.state);
  panel.append(header, narrative);
  const info = renderBranchInfo(node);
  if (info) panel.append(info);
  panel.append(breakdown);
  const composer = document.createElement('div');
  composer.className = 'branch-form';
  panel.append(composer);
  showComposer(composer, node);
}

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

function setBadge(badge, source) {
  badge.className = `source-badge source-${source}`;
  badge.textContent = SOURCE_LABELS[source] || source;
  return badge;
}

const signedMoney = value => `${value < 0 ? '−' : '+'}${money(Math.abs(value))}`;
const timing = (offset, duration) => `${offset ? `after ${offset} months` : 'from now'}${duration ? `, for ${duration} months` : ', permanently'}`;

function describeBlocks(plan) {
  const items = plan.changes.map(c => [`${c.label}: ${c.kind} ${signedMoney(c.amount_chf)}/month ${timing(c.start_offset_months, c.duration_months)}`, c.source]);
  for (const o of plan.one_offs) items.push([`${o.label}: ${o.amount_chf < 0 ? `receive ${money(-o.amount_chf)}` : `pay ${money(o.amount_chf)}`} ${o.offset_months ? `after ${o.offset_months} months` : 'now'}`, o.source]);
  for (const a of plan.assets) items.push([`${a.label}: ${money(a.price_chf)} = ${money(a.loan_chf)} loan + ${money(a.down_payment_chf)} down${a.transaction_costs_chf ? `, ${money(a.transaction_costs_chf)} costs` : ''}`, a.source]);
  if (plan.investment_multiplier !== 1) items.push([`Investments ${plan.investment_multiplier > 1 ? '+' : '−'}${Math.abs(Math.round((plan.investment_multiplier - 1) * 100))}%`, 'assumption']);
  return items;
}

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
  const box = el('details', { class: 'branch-info' }, el('summary', { text: `What “${plan.title}” assumes` }));
  rememberOpen(box, 'branch-info');
  const list = el('ul');
  for (const [text, source] of describeBlocks(plan)) list.append(el('li', {}, el('span', { text }), setBadge(el('span'), source)));
  box.append(list);
  if (plan.assumptions) box.append(el('p', { class: 'proposal-assumptions', text: plan.assumptions }));
  if (plan.warnings?.length) box.append(el('ul', { class: 'proposal-warnings' }, ...plan.warnings.map(text => el('li', { text }))));
  return box;
}

function showComposer(section, node, text = '') {
  section.replaceChildren();
  const error = el('p', { class: 'form-error', role: 'alert' });
  const chips = el('div', { class: 'preset-chips' });
  const textarea = el('textarea', { id: 'branch-event', maxlength: 2000, placeholder: 'For example: I take a six-month sabbatical, or my parents give me CHF 20,000' });
  textarea.value = text;
  const submit = el('button', { class: 'primary-button wide', type: 'submit' }, 'Break it down ', el('span', { 'aria-hidden': 'true', text: '✳' }));
  const run = async body => {
    error.textContent = '';
    const buttons = [...section.querySelectorAll('button')];
    buttons.forEach(button => { button.disabled = true; });
    if (body.event) submit.firstChild.textContent = 'Reading your event… ';
    const revision = state.revision;
    try {
      const result = await request('/api/branch/propose', { parent: node, ...body, api_key: state.apiKey || undefined });
      if (revision === state.revision && state.selectedId === node.id) renderReview(section, node, result.proposal, textarea.value);
    } catch (problem) { error.textContent = problem.message; }
    finally { buttons.forEach(button => { button.disabled = false; }); submit.firstChild.textContent = 'Break it down '; }
  };
  for (const preset of state.presets) chips.append(el('button', { type: 'button', class: 'chip', text: preset.label, onclick: () => run({ preset: preset.id }) }));
  const form = el('form', { onsubmit: event => { event.preventDefault(); if (textarea.value.trim()) run({ event: textarea.value.trim() }); } },
    el('label', { for: 'branch-event', text: 'Or describe it in your own words' }), textarea, error, submit);
  section.append(el('h3', { text: 'What if something changed?' }),
    el('p', { class: 'composer-hint', text: 'Pick a common event or describe your own. You review every number before the branch is created.' }),
    state.presets.length ? chips : null, form);
}

function blockEditor(key, item, onRemove) {
  const badge = setBadge(el('span'), item.source);
  const box = el('div', { class: 'block' }, el('div', { class: 'block-head' }, badge,
    el('button', { type: 'button', class: 'block-remove', 'aria-label': 'Remove this item', text: '×', onclick: onRemove })));
  const grid = el('div', { class: 'block-grid' });
  let down = null;
  const refreshDown = () => { if (down) { item.down_payment_chf = item.price_chf - item.loan_chf; down.textContent = money(item.down_payment_chf); } };
  for (const [field, type, label, options] of BLOCK_FIELDS[key]) {
    let input;
    if (type === 'select') {
      input = el('select', {}, ...options.map(([value, text]) => el('option', { value, text })));
      input.value = item[field];
    } else if (type === 'readonly') {
      input = down = el('output', { class: 'readonly-value' });
    } else {
      input = el('input', { type: type === 'text' ? 'text' : 'number', step: type === 'int' ? 1 : 'any', min: type === 'int' ? 0 : null });
      input.value = type === 'percent' ? Number((item[field] * 100).toFixed(3)) : item[field];
    }
    if (type !== 'readonly') input.addEventListener('input', () => {
      const raw = input.value;
      item[field] = type === 'text' || type === 'select' ? raw : type === 'percent' ? Number(raw) / 100 : type === 'int' ? Math.round(Number(raw)) : Number(raw);
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
  const months = el('select', { id: 'branch-months' }, ...[[12, '1 year'], [60, '5 years'], [120, '10 years'], [240, '20 years']].map(([value, text]) => el('option', { value, text })));
  const remaining = state.years * 12 - node.month;
  if (remaining > 0 && ![12, 60, 120, 240].includes(remaining)) months.append(el('option', { value: remaining, text: `${remaining} months (to the original horizon)` }));
  months.value = remaining > 0 ? String(remaining) : '60';
  const sliders = el('div');
  for (const variable of state.extraction.variables.filter(item => item.id !== 'opening_cash_chf')) {
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
    el('label', { for: 'branch-months', text: 'Explore this branch for' }), months);
  return {
    element,
    months: () => Number(months.value),
    variables: () => Object.fromEntries([...sliders.querySelectorAll('input[type="range"]:enabled')].map(input => [input.name, Number(input.value)])),
  };
}

function renderReview(section, node, proposal, text) {
  const plan = structuredClone(proposal);
  section.replaceChildren();
  const title = el('input', { type: 'text', id: 'branch-title', maxlength: 60 });
  title.value = plan.title;
  section.append(el('span', { class: 'card-kicker', text: 'REVIEW BEFORE BRANCHING' }), el('h3', { text: 'Check the numbers' }),
    el('label', { for: 'branch-title', text: 'Event name' }), title);
  if (plan.summary) section.append(el('p', { class: 'proposal-summary', text: plan.summary }));
  if (plan.assumptions) section.append(el('p', { class: 'proposal-assumptions', text: plan.assumptions }));
  if (plan.warnings?.length) section.append(el('ul', { class: 'proposal-warnings' }, ...plan.warnings.map(item => el('li', { text: item }))));
  const lists = { changes: el('div'), one_offs: el('div'), assets: el('div') };
  const redraw = () => {
    for (const key of Object.keys(lists)) lists[key].replaceChildren(...plan[key].map((item, index) => blockEditor(key, item, () => { plan[key].splice(index, 1); redraw(); })));
  };
  redraw();
  const addButton = (key, label) => el('button', { type: 'button', class: 'text-button add-block', text: `+ ${label}`, onclick: () => { plan[key].push(NEW_BLOCKS[key]()); redraw(); } });
  const shock = el('input', { type: 'number', step: 'any', id: 'branch-shock' });
  shock.value = Number(((plan.investment_multiplier - 1) * 100).toFixed(2));
  shock.addEventListener('input', () => { plan.investment_multiplier = 1 + Number(shock.value) / 100; });
  const options = branchOptions(node);
  const error = el('p', { class: 'form-error', role: 'alert' });
  const create = el('button', { type: 'button', class: 'primary-button wide' }, 'Create branch ', el('span', { 'aria-hidden': 'true', text: '✳' }));
  create.addEventListener('click', async () => {
    error.textContent = '';
    create.disabled = true;
    create.firstChild.textContent = 'Simulating… ';
    const revision = state.revision;
    try {
      const name = title.value.trim() || plan.title;
      const result = await request('/api/branch/apply', { parent: node, event: name, months: options.months(), variables: options.variables(), proposal: { ...plan, title: name } });
      if (revision !== state.revision) return;
      for (const child of result.nodes) state.nodes.set(child.id, child);
      if (result.nodes.length) {
        state.selectedId = result.nodes[0].id;
        renderGraph(true);
        selectNode(result.nodes[0].id);
      }
    } catch (problem) { error.textContent = problem.message; }
    finally { create.disabled = false; create.firstChild.textContent = 'Create branch '; }
  });
  section.append(
    el('h4', { text: 'Monthly changes' }), lists.changes, addButton('changes', 'Add monthly change'),
    el('h4', { text: 'One-off amounts' }), lists.one_offs, addButton('one_offs', 'Add one-off'),
    el('h4', { text: 'Purchases' }), lists.assets, addButton('assets', 'Add purchase'),
    el('h4', { text: 'Investments' }), el('label', { class: 'block-field', for: 'branch-shock' }, el('span', { text: 'Change in investment value (%)' }), shock),
    options.element, error, create,
    el('button', { type: 'button', class: 'text-button back-button', text: '← Choose a different event', onclick: () => showComposer(section, node, text) }));
}

async function selectNode(id) {
  const node = state.nodes.get(id);
  if (!node) return;
  state.selectedId = id;
  renderGraph();
  renderInspector(node);
  if (state.narratives.has(id) || node.narrative) return;
  const revision = state.revision;
  try {
    const result = await request('/api/narrative', { node });
    if (revision !== state.revision) return;
    state.narratives.set(id, result.narrative);
    if (state.selectedId === id) byId('node-narrative').textContent = result.narrative;
  } catch (error) {
    if (state.selectedId === id) byId('node-narrative').textContent = `Summary unavailable. ${error.message}`;
  }
}

function renderResults(nodes) {
  state.revision += 1;
  state.nodes = new Map(nodes.map(node => [node.id, node]));
  state.narratives = new Map();
  state.selectedId = milestoneNodes()[0]?.id ?? null;
  renderGraph(true);
  if (state.selectedId) selectNode(state.selectedId);
  else {
    const panel = byId('inspector');
    panel.replaceChildren();
    const empty = document.createElement('p');
    empty.className = 'inspector-empty';
    empty.textContent = 'There are no events to display in this timeline.';
    panel.append(empty);
  }
}

fetch('/api/branch/presets').then(response => response.json()).then(data => { state.presets = data.presets || []; }).catch(() => {});

byId('fit-graph').addEventListener('click', () => state.network?.fit({ animation: true }));
byId('milestone-select').addEventListener('change', event => selectNode(event.target.value));

byId('open-settings').addEventListener('click', () => { byId('api-key').value = state.apiKey; byId('settings-dialog').showModal(); });
byId('settings-form').addEventListener('submit', event => { if (event.submitter?.value === 'save') state.apiKey = byId('api-key').value.trim(); });
byId('edit-story').addEventListener('click', () => showStage('intake'));
byId('adjust-scenario').addEventListener('click', () => showStage('tuning'));
byId('intake-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = byId('extract-button');
  byId('intake-error').textContent = '';
  button.disabled = true;
  button.textContent = 'Reading your story…';
  try {
    state.years = Number(byId('years').value);
    state.extraction = await request('/api/extract', { statement: byId('statement').value.trim(), years: state.years, api_key: state.apiKey || undefined });
    renderTuning(state.extraction);
    showStage('tuning');
  } catch (error) { byId('intake-error').textContent = error.message; }
  finally { button.disabled = false; button.textContent = 'Explore my scenario ✳'; }
});
byId('simulate-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = byId('simulate-button');
  byId('simulate-error').textContent = '';
  button.disabled = true;
  button.textContent = 'Simulating…';
  try {
    const variables = Object.fromEntries([...new FormData(event.currentTarget)].map(([key, value]) => [key, Number(value)]));
    const result = await request('/api/simulate', { scenario: state.extraction.scenario, years: state.years, variables });
    showStage('results');
    renderResults(result.nodes);
  } catch (error) { byId('simulate-error').textContent = error.message; }
  finally { button.disabled = false; button.textContent = 'Simulate future ✳'; }
});
byId('test-data').addEventListener('click', async () => {
  const button = byId('test-data');
  button.disabled = true;
  try {
    const result = await request('/api/test', { years: state.years });
    state.years = result.years;
    state.extraction = result.extraction;
    renderTuning(state.extraction);
    showStage('results');
    renderResults(result.nodes);
  } catch (error) { alert(`Could not generate test data: ${error.message}`); }
  finally { button.disabled = false; }
});

function keepPanelOnScreen(panel) {
  if (!panel.style.left) return;
  const box = panel.getBoundingClientRect();
  const bar = panel.querySelector('.panel-bar').offsetHeight;
  panel.style.left = `${Math.min(Math.max(parseFloat(panel.style.left), 0), window.innerWidth - box.width)}px`;
  panel.style.top = `${Math.min(Math.max(parseFloat(panel.style.top), 0), window.innerHeight - bar)}px`;
  // The details panel scrolls internally, so let it grow only down to the bottom of the window.
  if (panel.classList.contains('inspector')) panel.style.maxHeight = `${Math.max(window.innerHeight - parseFloat(panel.style.top) - 12, bar)}px`;
}

for (const panel of document.querySelectorAll('.floating-panel')) {
  const bar = panel.querySelector('.panel-bar');
  const toggle = panel.querySelector('.panel-toggle');
  toggle.addEventListener('click', () => {
    const minimized = panel.classList.toggle('minimized');
    toggle.textContent = minimized ? '+' : '–';
    toggle.setAttribute('aria-expanded', String(!minimized));
    toggle.setAttribute('aria-label', minimized ? 'Restore panel' : 'Minimize panel');
  });
  bar.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('button')) return;
    const box = panel.getBoundingClientRect();
    const offsetX = event.clientX - box.left;
    const offsetY = event.clientY - box.top;
    // Switch from the CSS corner anchoring to explicit coordinates so the panel follows the pointer.
    Object.assign(panel.style, { left: `${box.left}px`, top: `${box.top}px`, right: 'auto', bottom: 'auto', width: `${box.width}px` });
    bar.setPointerCapture(event.pointerId);
    bar.classList.add('dragging');
    const move = moveEvent => {
      panel.style.left = `${moveEvent.clientX - offsetX}px`;
      panel.style.top = `${moveEvent.clientY - offsetY}px`;
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
window.addEventListener('resize', () => document.querySelectorAll('.floating-panel').forEach(keepPanelOnScreen));
