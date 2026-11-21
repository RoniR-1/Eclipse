const byId = id => document.getElementById(id);
const state = { apiKey: '', extraction: null, years: 10, nodes: new Map(), narratives: new Map(), selectedId: null, network: null, graphNodes: null, graphEdges: null, revision: 0, openDetails: new Set(['breakdown']) };
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
  if (node.state.cash_chf < 0 || node.state.net_worth_chf < 0) return { label: 'Needs attention', color: '#ff6b5b' };
  const expenses = node.state.cash_flow.expenses_chf;
  if (expenses > 0 && node.state.cash_chf < expenses * 3) return { label: 'Building resilience', color: '#f2c14e' };
  return { label: 'On track', color: '#7fc48a' };
}

function milestoneNodes() {
  const nodes = [...state.nodes.values()];
  const parentIds = new Set(nodes.map(node => node.parent_id).filter(Boolean));
  return nodes.filter(node => node.month === 0 || node.month % 12 === 0 || node.event_summary !== 'Monthly update' || !parentIds.has(node.id) || (node.parent_id && state.nodes.get(node.parent_id)?.branch_id !== node.branch_id));
}

function renderGraph(fit = false) {
  const milestones = milestoneNodes();
  const visible = new Set(milestones.map(node => node.id));
  const lanes = new Map([['root', 0], ['main', 0]]);
  for (const node of milestones) if (!lanes.has(node.branch_id)) lanes.set(node.branch_id, lanes.size - 1);
  const months = [...new Set(milestones.map(node => node.month))].sort((a, b) => a - b);
  const graphNodes = milestones.map(node => {
    const status = health(node);
    return {
      id: node.id,
      label: `${node.date} · Year ${(node.month / 12).toFixed(node.month % 12 ? 1 : 0)}\n${milestoneTitle(node)}\n${money(node.state.net_worth_chf)}\n● ${status.label}`,
      x: months.indexOf(node.month) * 260,
      y: lanes.get(node.branch_id) * 190,
      color: { background: node.id === state.selectedId ? '#4a2614' : '#2b1910', border: status.color, highlight: { background: '#4a2614', border: status.color } },
      font: { color: '#f6e9dc', size: 14, face: 'DM Sans', multi: false },
      borderWidth: node.id === state.selectedId ? 3 : 1.5,
      shape: 'box', margin: 16, widthConstraint: { minimum: 170, maximum: 210 },
    };
  });
  const edges = [];
  for (const node of milestones) {
    let parentId = node.parent_id;
    while (parentId && !visible.has(parentId)) parentId = state.nodes.get(parentId)?.parent_id;
    if (parentId) edges.push({ id: `${parentId}>${node.id}`, from: parentId, to: node.id, arrows: 'to', color: { color: '#8a5a3c', highlight: '#ff9a4d' }, smooth: { type: 'cubicBezier', roundness: 0.4 } });
  }
  if (!state.network) {
    state.graphNodes = new vis.DataSet(graphNodes);
    state.graphEdges = new vis.DataSet(edges);
    state.network = new vis.Network(byId('timeline-graph'), { nodes: state.graphNodes, edges: state.graphEdges }, {
      physics: false, interaction: { hover: true, navigationButtons: false },
      nodes: { chosen: false }, edges: { width: 2, arrowStrikethrough: false },
      layout: { improvedLayout: false },
    });
    state.network.on('click', params => { if (params.nodes.length) selectNode(params.nodes[0]); });
    let drag = null;
    state.network.on('dragStart', params => {
      if (!params.nodes.length) { drag = null; return; }
      const children = new Map();
      for (const edge of state.graphEdges.get()) {
        if (!children.has(edge.from)) children.set(edge.from, []);
        children.get(edge.from).push(edge.to);
      }
      const dragged = new Set(params.nodes);
      const followers = new Set();
      const stack = [...params.nodes];
      while (stack.length) {
        for (const child of children.get(stack.pop()) || []) {
          if (dragged.has(child) || followers.has(child)) continue;
          followers.add(child);
          stack.push(child);
        }
      }
      const anchor = params.nodes[0];
      drag = { anchor, origin: state.network.getPosition(anchor), followers: state.network.getPositions([...followers]) };
    });
    state.network.on('dragging', () => {
      if (!drag) return;
      const current = state.network.getPosition(drag.anchor);
      const dx = current.x - drag.origin.x;
      const dy = current.y - drag.origin.y;
      for (const [id, start] of Object.entries(drag.followers)) state.network.moveNode(id, start.x + dx, start.y + dy);
    });
    state.network.on('dragEnd', () => { drag = null; });
  } else {
    // Keep every node that is already on the canvas where it currently is (including nodes the user dragged);
    // only place new nodes, shifted by however far their nearest existing ancestor has moved from its default spot.
    const existing = new Set(state.graphNodes.getIds());
    const positions = state.network.getPositions([...existing]);
    const defaults = new Map(graphNodes.map(node => [node.id, { x: node.x, y: node.y }]));
    const parentOf = new Map(edges.map(edge => [edge.to, edge.from]));
    for (const node of graphNodes) {
      if (existing.has(node.id)) {
        node.x = positions[node.id].x;
        node.y = positions[node.id].y;
        continue;
      }
      let anchor = parentOf.get(node.id);
      while (anchor && !existing.has(anchor)) anchor = parentOf.get(anchor);
      if (anchor && defaults.has(anchor)) {
        node.x += positions[anchor].x - defaults.get(anchor).x;
        node.y += positions[anchor].y - defaults.get(anchor).y;
      }
    }
    const edgeIds = new Set(edges.map(edge => edge.id));
    state.graphNodes.remove([...existing].filter(id => !visible.has(id)));
    state.graphNodes.update(graphNodes);
    state.graphEdges.remove(state.graphEdges.getIds().filter(id => !edgeIds.has(id)));
    state.graphEdges.update(edges);
  }
  const select = byId('milestone-select');
  select.replaceChildren();
  for (const node of milestones) {
    const option = document.createElement('option');
    option.value = node.id;
    option.textContent = `${node.branch_id === 'root' || node.branch_id === 'main' ? 'Original path' : `Branch ${lanes.get(node.branch_id)}`} · ${node.date} — ${milestoneTitle(node)} — ${money(node.state.net_worth_chf)}`;
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
  panel.replaceChildren();
  const header = document.createElement('div');
  header.className = 'inspector-header';
  const kicker = document.createElement('span');
  kicker.className = 'card-kicker';
  kicker.textContent = `${node.date} · YEAR ${(node.month / 12).toFixed(node.month % 12 ? 1 : 0)}`;
  const title = document.createElement('h2');
  title.textContent = milestoneTitle(node);
  const status = document.createElement('span');
  status.className = 'health-label';
  status.style.color = health(node).color;
  status.textContent = `● ${health(node).label}`;
  const worth = document.createElement('div');
  worth.className = 'inspector-worth';
  worth.textContent = money(node.state.net_worth_chf);
  const worthLabel = document.createElement('p');
  worthLabel.textContent = 'Projected net worth';
  header.append(kicker, title, status, worth, worthLabel);
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
  const form = document.createElement('form');
  form.id = 'branch-form';
  form.className = 'branch-form';
  form.innerHTML = '<h3>What if something changed?</h3><label for="branch-event">Add an event or decision</label><textarea id="branch-event" required maxlength="2000" placeholder="For example, buy a house or change jobs"></textarea><details class="branch-options"><summary>Adjust branch assumptions</summary><div id="branch-sliders"></div></details><label for="branch-months">Explore this branch for</label><select id="branch-months"><option value="12">1 year</option><option value="60">5 years</option><option value="120">10 years</option><option value="240">20 years</option></select><p class="form-error" id="branch-error" role="alert"></p><button class="primary-button wide" type="submit">Diverge / Add event <span aria-hidden="true">✳</span></button>';
  panel.append(header, narrative, breakdown, form);
  const remaining = state.years * 12 - node.month;
  if (remaining > 0 && ![12, 60, 120, 240].includes(remaining)) {
    const option = document.createElement('option');
    option.value = remaining;
    option.textContent = `${remaining} months (to the original horizon)`;
    byId('branch-months').append(option);
  }
  byId('branch-months').value = remaining > 0 ? String(remaining) : '60';
  for (const variable of state.extraction.variables.filter(item => item.id !== 'opening_cash_chf')) {
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
    byId('branch-sliders').append(item);
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    const errorBox = byId('branch-error');
    errorBox.textContent = '';
    button.disabled = true;
    button.textContent = 'Exploring…';
    const revision = state.revision;
    try {
      const variables = Object.fromEntries([...form.querySelectorAll('input[type="range"]:enabled')].map(input => [input.name, Number(input.value)]));
      const result = await request('/api/branch', { parent: node, event: byId('branch-event').value.trim(), months: Number(byId('branch-months').value), variables, api_key: state.apiKey || undefined });
      if (revision !== state.revision) return;
      for (const child of result.nodes) state.nodes.set(child.id, child);
      if (result.nodes.length) {
        const first = result.nodes[0];
        state.selectedId = first.id;
        renderGraph(true);
        selectNode(first.id);
      }
    } catch (error) { errorBox.textContent = error.message; }
    finally { button.disabled = false; button.textContent = 'Diverge / Add event ✳'; }
  });
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
  state.selectedId = nodes[0].id;
  renderGraph(true);
  selectNode(state.selectedId);
}

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
