// Tests for the browser logic in web/app.js. Run with: node --test tests/web/
import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { loadApp, fakeDocument } from './load-app.mjs';

const flow = (overrides = {}) => ({
  income_chf: 5000, expenses_chf: 3000, one_off_costs_chf: 0, debt_payments_chf: 0, debt_interest_chf: 0,
  investment_return_chf: 0, investment_contribution_chf: 0, operating_cash_flow_chf: 2000, net_cash_flow_chf: 2000,
  ...overrides,
});

function node(id, parent, branch, month, { cash = 10000, worth = cash, event = 'Monthly update', expenses = 3000 } = {}) {
  return {
    id, parent_id: parent, branch_id: branch, month,
    date: `2026-${String(1 + (month % 12)).padStart(2, '0')}-01`,
    event_summary: event,
    state: {
      cash_chf: cash, net_worth_chf: worth, total_assets_chf: Math.max(cash, worth), total_debt_chf: Math.max(0, cash - worth),
      cash_flow: flow({ expenses_chf: expenses }),
    },
  };
}

// root ─ m1 ─ m2 ─ m3 ─ m4 ─ m5 ─ m6            (Original path)
//              └ a3 ─ a4 ─ a5                    (branch A, "Start a master's")
//                      └ b5 ─ b6                 (branch B, nested in A, "Part-time job")
//                         m4 └ c5                (branch C, "Buy a car")
function graph() {
  return [
    node('root', null, 'root', 0, { event: 'Initial financial state' }),
    node('m1', 'root', 'main', 1), node('m2', 'm1', 'main', 2), node('m3', 'm2', 'main', 3),
    node('m4', 'm3', 'main', 4), node('m5', 'm4', 'main', 5), node('m6', 'm5', 'main', 6),
    node('a3', 'm2', 'A', 3, { event: "Start a master's" }), node('a4', 'a3', 'A', 4), node('a5', 'a4', 'A', 5, { cash: -200 }),
    node('b5', 'a4', 'B', 5, { event: 'Part-time job' }), node('b6', 'b5', 'B', 6),
    node('c5', 'm4', 'C', 5, { event: 'Buy a car' }),
  ];
}

let app, calls, answerConfirm;
beforeEach(() => {
  ({ app, calls, answerConfirm } = loadApp());
  app.state.nodes = new Map(graph().map(item => [item.id, item]));
  app.state.narratives = new Map();
  app.state.resultsView = 'timeline';
  app.state.extraction = { scenario: {}, variables: [] };
});
const ids = () => [...app.state.nodes.keys()].sort();
// Values created inside the app's context have that context's prototypes; compare plain copies.
const plain = value => JSON.parse(JSON.stringify(value));

describe('branch deletion', () => {
  test('a branch takes its nested branches with it and nothing else', () => {
    assert.deepEqual([...app.branchNodeIds('A')].sort(), ['a3', 'a4', 'a5', 'b5', 'b6']);
    assert.deepEqual([...app.branchNodeIds('B')].sort(), ['b5', 'b6']);
    assert.deepEqual([...app.branchNodeIds('C')], ['c5']);
  });

  test('deleting the original path keeps the shared history other branches grew out of', () => {
    assert.deepEqual([...app.originalPathRemovals()].sort(), ['m5', 'm6']);
    app.state.nodes = new Map(graph().filter(item => ['root', 'main'].includes(item.branch_id)).map(item => [item.id, item]));
    assert.equal(app.originalPathRemovals().size, 7, 'with no other branches the whole projection goes');
  });

  test('cancelling the pop-up changes nothing', async () => {
    answerConfirm(false);
    await app.deleteBranch('A');
    assert.equal(app.state.nodes.size, 13);
    assert.ok(!calls.some(([name]) => name === 'showToast'));
  });

  test('confirming removes the branch and returns to the fork point', async () => {
    await app.deleteBranch('A');
    assert.deepEqual(ids(), ['c5', 'm1', 'm2', 'm3', 'm4', 'm5', 'm6', 'root']);
    assert.equal(app.state.selectedId, 'm2');
    assert.equal(app.state.activeBranchId, 'main');
    const [, options] = calls.find(([name]) => name === 'confirmDialog');
    assert.equal(options.title, "Start a master's");
    assert.match(options.message, /1 branch that split off/);
    assert.equal(options.confirmLabel, 'Delete this path');
    assert.deepEqual(calls.find(([name]) => name === 'showToast'), ['showToast', "Deleted branch: Start a master's"]);
  });

  test('deleting a nested branch returns to its parent branch', async () => {
    await app.deleteBranch('B');
    assert.equal(app.state.selectedId, 'a4');
    assert.equal(app.state.activeBranchId, 'A');
    assert.ok(app.state.nodes.has('a5'));
  });

  test('deleting the original path switches to a remaining branch', async () => {
    await app.deleteBranch('main');
    assert.deepEqual(ids(), ['a3', 'a4', 'a5', 'b5', 'b6', 'c5', 'm1', 'm2', 'm3', 'm4', 'root']);
    assert.equal(app.state.activeBranchId, 'A');
    assert.ok(calls.some(([name, branch]) => name === 'switchActivePath' && branch === 'A'));
  });

  test('deleting the only path clears the projection and goes back to the assumptions', async () => {
    app.state.nodes = new Map(graph().filter(item => ['root', 'main'].includes(item.branch_id)).map(item => [item.id, item]));
    await app.deleteBranch('main');
    assert.equal(app.state.nodes.size, 0);
    assert.ok(calls.some(([name, stage]) => name === 'showStage' && stage === 'tuning'));
  });

  test('a branch that finished loading while the pop-up was open is removed too', async () => {
    const late = node('x7', 'a5', 'X', 6, { event: 'Late branch' });
    answerConfirm(true);
    const pending = app.deleteBranch('A');
    app.state.nodes.set(late.id, late); // arrives before the user confirms
    await pending;
    assert.ok(!app.state.nodes.has('x7'));
  });
});

describe('tree index', () => {
  test('finds forks, branch labels and full lineages', () => {
    const index = app.buildTreeIndex();
    assert.equal(index.branchIds[0], 'main');
    assert.deepEqual([...index.forkNodeIds].sort(), ['a4', 'm2', 'm4']);
    assert.equal(index.branchMeta.get('A').label, "Start a master's");
    assert.equal(index.branchMeta.get('main').label, 'Original path');
    assert.deepEqual(plain(index.branchMeta.get('B').chain.map(item => item.id)), ['root', 'm1', 'm2', 'a3', 'a4', 'b5', 'b6']);
    assert.equal(index.branchMeta.get('C').forkParentId, 'm4');
    assert.equal(index.baselineByMonth.get(4).id, 'm4');
  });

  test('timeline milestones keep quarters, forks, branch starts, the first shortfall and the tip', () => {
    const index = app.buildTreeIndex();
    const chain = index.branchMeta.get('A').chain;
    // m1 starts the Original path, m2 is a fork, a3 starts branch A, a4 is a fork, a5 is the first shortfall and the tip.
    assert.deepEqual(plain(app.getPathMilestones(chain, index).map(item => item.id)), ['root', 'm1', 'm2', 'a3', 'a4', 'a5']);
  });

  test('hiding recaps drops both 6-month and full-year recaps (including non-event fork recaps) while keeping starts, events and tips', () => {
    // Extend main path past month 6 and month 12 to month 18 so m6 (6m) and m12 (Year 1) are pure recaps and m18 is the tip
    let prev = 'm6';
    for (let m = 7; m <= 18; m++) {
      const id = `m${m}`;
      app.state.nodes.set(id, node(id, prev, 'main', m));
      prev = id;
    }
    const index = app.buildTreeIndex();
    const mainChain = index.branchMeta.get('main').chain;
    assert.ok(app.getPathMilestones(mainChain, index).some(n => n.id === 'm6'), 'm6 is shown by default');
    assert.ok(app.getPathMilestones(mainChain, index).some(n => n.id === 'm12'), 'm12 (Year 1) is shown by default');
    app.setHideRecaps(true);
    const compactMain = app.getPathMilestones(mainChain, index).map(n => n.id);
    // m2 and m4 are non-event fork recaps, m6 and m12 are periodic recaps — all hidden, leaving only root, m1 (branch start), and m18 (horizon end)
    assert.deepEqual(plain(compactMain), ['root', 'm1', 'm18']);
    const treeIds = app.getAllGraphMilestones(index).map(n => n.id);
    assert.ok(!treeIds.includes('m6') && !treeIds.includes('m12') && !treeIds.includes('m2') && !treeIds.includes('m4'));
    assert.ok(treeIds.includes('a3') && treeIds.includes('b5') && treeIds.includes('c5'), 'real event nodes stay visible');
    app.setHideRecaps(false);
    assert.ok(app.getAllGraphMilestones(index).some(n => n.id === 'm12'), 're-showing recaps brings yearly recaps back');
  });
});

describe('health flags', () => {
  test('negative cash or net worth needs attention and says why', () => {
    const status = app.nodeHealth(node('n', null, 'main', 1, { cash: -500, worth: -100 }));
    assert.equal(status.level, 'danger');
    assert.equal(status.issues.length, 2);
    assert.match(status.issues[0], /Cash is .*500/);
  });

  test('less than three months of expenses is a warning', () => {
    const status = app.nodeHealth(node('n', null, 'main', 1, { cash: 8000, expenses: 3000 }));
    assert.equal(status.level, 'warn');
    assert.equal(app.nodeHealth(node('n', null, 'main', 1, { cash: 9000, expenses: 3000 })).level, 'ok');
  });
});

describe('formatting', () => {
  test('variables show their unit', () => {
    assert.equal(app.formatVariable({ unit: '%', max: 1 }, 0.45), '45%');
    assert.equal(app.formatVariable({ unit: '%', max: 30 }, 4), '4%');
    assert.match(app.formatVariable({ unit: 'CHF' }, 5000), /^CHF\s5.000$/);
    assert.equal(app.formatVariable({ unit: 'months' }, 3), '3 months');
  });

  test('signed and compact amounts', () => {
    assert.match(app.moneySigned(1500), /^\+CHF\s1.500$/);
    assert.match(app.moneySigned(-1500), /^−CHF\s1.500$/);
    assert.equal(app.moneySigned(0.2), 'CHF 0');
    assert.equal(app.compactChf(2_500_000), 'CHF 2.5M');
    assert.equal(app.compactChf(-45_400), '−CHF 45k');
    assert.equal(app.compactChf(1_250), 'CHF 1.3k');
    assert.equal(app.compactChf(999.6), 'CHF 1000');
  });

  test('event text is escaped before it is put into HTML', () => {
    assert.equal(app.escapeHtml('<img src=x onerror="alert(1)">&'), '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;');
  });

  test('milestone titles', () => {
    assert.equal(app.milestoneTitle(node('n', null, 'root', 0)), 'Starting point');
    assert.equal(app.milestoneTitle(node('n', null, 'A', 7, { event: 'Buy a car' })), 'Buy a car');
    assert.equal(app.milestoneTitle(node('n', null, 'main', 24)), 'Year 2');
    assert.equal(app.milestoneTitle(node('n', null, 'main', 6)), '6 months');
    assert.equal(app.milestoneTitle(node('n', null, 'main', 18)), 'Year 1.5');
    assert.equal(app.milestoneTitle(node('n', null, 'main', 15)), 'Month 15');
    assert.equal(app.milestoneTitle(node('n', null, 'main', 7, { cash: -1 })), 'Month 7');
    assert.equal(app.milestoneTitle(node('n', null, 'main', 7)), 'Month 7');
  });
});

describe('typed assumption values', () => {
  const percent = { unit: '%', min: 0, max: 1, step: 0.05 };
  const chf = { unit: 'CHF', min: 0, max: 50000, step: 100 };
  const months = { unit: 'months', min: 0, max: 24, step: 1 };

  test('values are shown in display units', () => {
    assert.equal(app.variableToDisplay(percent, 0.45), '45');
    assert.equal(app.variableToDisplay(percent, 0.0425), '4.25');
    assert.equal(app.variableToDisplay(chf, 5000), '5000');
    assert.equal(app.variableToDisplay(months, 3), '3');
  });

  test('typed text is read back into the variable\'s units', () => {
    assert.equal(app.parseVariableInput(percent, '42'), 0.42);
    assert.equal(app.parseVariableInput(percent, ' 42 % '), 0.42);
    assert.equal(app.parseVariableInput(percent, '4,5'), 0.045, 'decimal comma');
    assert.equal(app.parseVariableInput(chf, "5'000"), 5000, 'Swiss thousands separator');
    assert.equal(app.parseVariableInput(chf, 'CHF 12’500.50'), 12500.5);
    assert.equal(app.parseVariableInput(chf, '1,250,000'), 1250000, 'comma thousands separators');
    assert.equal(app.parseVariableInput(months, '6 months'), 6);
    assert.equal(app.parseVariableInput(chf, '-200'), -200, 'parsed, then rejected by the box for CHF');
  });

  test('text that is not a number is rejected', () => {
    for (const text of ['', 'abc', '5k', '1.2.3', '--5', '50%%x']) {
      assert.equal(app.parseVariableInput(chf, text), null, text);
    }
  });

  test('display and parsing round-trip', () => {
    for (const [variable, value] of [[percent, 0.35], [percent, 0.07], [chf, 4380], [months, 9]]) {
      assert.equal(app.parseVariableInput(variable, app.variableToDisplay(variable, value)), value);
    }
  });
});

describe('assumption sliders with text boxes', () => {
  const variables = [
    { id: 'monthly_income_chf', label: 'Monthly income', description: '', unit: 'CHF', min: 0, max: 7500, step: 100, value: 5000 },
    { id: 'savings_rate', label: 'Investment allocation', description: '', unit: '%', min: 0, max: 1, step: 0.05, value: 0.45 },
  ];
  function setup() {
    const document = fakeDocument();
    const { app: tuningApp } = loadApp({ document });
    tuningApp.renderTuning({ summary: '', assumptions: '', scenario: { opening_cash_chf: 1, phases: [] }, variables });
    const sliders = document.getElementById('sliders');
    const parts = id => ({
      slider: sliders.find(node => node.name === id),
      box: sliders.find(node => node.id === `value-${id}`),
      hint: sliders.children[variables.findIndex(v => v.id === id)].find(node => node.classList?.contains('slider-hint')),
    });
    return { income: parts('monthly_income_chf'), rate: parts('savings_rate') };
  }
  const type = (box, text, event = 'input') => { box.value = text; box.dispatch(event); };

  test('boxes start with the current values in display units and are not submitted themselves', () => {
    const { income, rate } = setup();
    assert.equal(income.box.value, '5000');
    assert.equal(rate.box.value, '45');
    assert.equal(income.box.name, undefined, 'only the slider is part of the form data');
  });

  test('dragging the slider updates the box', () => {
    const { rate } = setup();
    rate.slider.value = '0.6';
    rate.slider.dispatch('input');
    assert.equal(rate.box.value, '60');
  });

  test('typing updates the slider, including values between its steps', () => {
    const { rate } = setup();
    type(rate.box, '42');
    assert.equal(Number(rate.slider.value), 0.42);
    assert.equal(rate.slider.step, 'any');
  });

  test('a CHF amount beyond the slider stretches the slider', () => {
    const { income } = setup();
    type(income.box, "12'000", 'change');
    assert.equal(Number(income.slider.max), 12000);
    assert.equal(Number(income.slider.value), 12000);
    assert.equal(income.box.value, '12000');
  });

  test('a percentage beyond its range is limited, with a note', () => {
    const { rate } = setup();
    type(rate.box, '150', 'change');
    assert.equal(Number(rate.slider.value), 1);
    assert.equal(rate.box.value, '100');
    assert.match(rate.hint.textContent, /Limited to/);
  });

  test('invalid text is flagged and leaves the slider unchanged', () => {
    const { income } = setup();
    type(income.box, 'lots');
    assert.ok(income.box.classList.contains('is-invalid'));
    assert.equal(income.box.attributes['aria-invalid'], 'true');
    assert.equal(Number(income.slider.value), 5000);
    type(income.box, '-5');
    assert.match(income.hint.textContent, /0 or more/);
    type(income.box, '5200');
    assert.ok(!income.box.classList.contains('is-invalid'), 'fixing the value clears the flag');
    assert.equal(Number(income.slider.value), 5200);
  });
});

describe('run file', () => {
  test('preserves ordered edits and branches for replay', () => {
    const operations = [{ type: 'edit', node_id: 'root', edits: { cash_chf: 12000 } },
      { type: 'branch', branch: { sequence: 0, parent_id: 'root' } }];
    app.state.run = { source: 'simulate', seed: null, start_date: '2026-01-01', years: 5,
      scenario: {}, variables: {}, branches: [operations[1].branch], operations, environment: {} };
    assert.deepEqual(plain(app.buildRunFile().operations), operations);
  });
  test('records inputs, units and the values on screen, without deleted nodes', async () => {
    app.state.run = { source: 'test', seed: 42, start_date: '2026-01-01', years: 5, scenario: null, variables: {},
                      branches: [{ sequence: 4 }], next_sequence: 5, environment: { python: '3', numpy: '2' } };
    await app.deleteBranch('C');
    const run = app.buildRunFile();
    assert.equal(run.format, 'financial-twin-run/1');
    assert.deepEqual(plain(run.units), { currency: 'CHF', time_step: 'month', horizon_years: 5 });
    assert.equal(run.seed, 42);
    assert.deepEqual(plain(run.kept_node_ids), ids());
    assert.ok(!run.kept_node_ids.includes('c5'));
    const a5 = run.expected.find(item => item.id === 'a5');
    assert.deepEqual(plain(a5), { id: 'a5', branch_id: 'A', month: 5, date: '2026-06-01', cash_chf: -200, net_worth_chf: -200,
                           total_assets_chf: -200, total_debt_chf: 0, net_cash_flow_chf: 2000 });
  });
});

test('editing a goal updates its amount, deadline and priority for the next simulation', () => {
  const document = fakeDocument();
  const { app: tuningApp } = loadApp({ document });
  const goal = { name: 'Reserve', target_chf: 10000, target_month: 12, target_date: '2027-01-01', priority: 'medium' };
  tuningApp.renderTuning({ summary: '', assumptions: '', scenario: { opening_cash_chf: 100, phases: [], goals: [goal] }, variables: [] });
  const editor = document.getElementById('goal-editor');
  const inputs = [...editor.walk()].filter(item => item.tagName === 'INPUT');
  const priority = editor.find(item => item.tagName === 'SELECT');
  inputs[0].value = '15000'; inputs[0].dispatch('change');
  inputs[1].value = '24'; inputs[1].dispatch('change');
  priority.value = 'high'; priority.dispatch('change');
  assert.equal(goal.target_chf, 15000);
  assert.equal(goal.target_month, 24);
  assert.equal(goal.target_date, undefined);
  assert.equal(goal.priority, 'high');
});

test('input sources distinguish AI interpretation from ordinary assumptions', () => {
  assert.equal(app.inputSource({ source: 'assumption', transformation: 'model interpretation of story' }), 'ai_estimate');
  assert.equal(app.inputSource({ source: 'assumption', transformation: 'illustrative goal assumption' }), 'assumption');
  assert.equal(app.inputSource({ source: 'dataset' }), 'dataset');
  assert.equal(app.inputSource({ source: 'user' }), 'user');
});

test('goal edits recalculate and display the resulting outcome', async () => {
  const document = fakeDocument();
  const fetch = async path => ({ ok: true, json: async () => path === '/api/simulate'
    ? { goals: [{ name: 'Reserve', met: false, deadline_gap_chf: 500 }] } : {} });
  const { app: tuningApp } = loadApp({ document, fetch });
  const goal = { name: 'Reserve', target_chf: 10000, target_month: 12, priority: 'medium' };
  tuningApp.renderTuning({ summary: '', assumptions: '', scenario: { opening_cash_chf: 100, phases: [], goals: [goal] }, variables: [] });
  const editor = document.getElementById('goal-editor');
  const target = [...editor.walk()].find(item => item.tagName === 'INPUT');
  target.value = '15000';
  target.dispatch('change');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.ok([...editor.walk()].some(item => /not met at deadline, gap CHF\s500/.test(item.textContent || '')));
});

test('structured comparison labels its baseline and reports signed cash differences', () => {
  const document = fakeDocument();
  const { app: comparisonApp } = loadApp({ document });
  const container = document.getElementById('project-result');
  comparisonApp.renderStructuredComparison(container, { horizon_months: 60, comparison_bundle: {
    baseline_path: 'Work', paths: { Work: { metrics: { closing_cash_chf: 100, closing_net_worth_chf: 120, min_cash_chf: 10 } },
      Study: { metrics: { closing_cash_chf: 80, closing_net_worth_chf: 90, min_cash_chf: -5 } } }, limitations: [] } });
  assert.match(container.children[0].textContent, /Baseline: Work/);
  assert.match(container.children[3].textContent, /cash difference −CHF\s20/);
});

test('comparison presents dated shortfall, sustained break-even and sensitivity drivers', () => {
  const document = fakeDocument();
  const { app: comparisonApp } = loadApp({ document });
  const container = document.getElementById('project-result');
  const rows = values => values.map(([date, cash_chf, net_worth_chf]) => ({ date, cash_chf, net_worth_chf }));
  comparisonApp.renderStructuredComparison(container, { horizon_months: 3,
    paths: { Work: { timeline: rows([['2026-01', 100, 100], ['2026-02', 120, 120], ['2026-03', 140, 140], ['2026-04', 160, 160]]) },
      Study: { timeline: rows([['2026-01', 100, 100], ['2026-02', 80, 80], ['2026-03', 130, 145], ['2026-04', 170, 175]]) } },
    comparison_bundle: { baseline_path: 'Work', comparisons: { Study: { largest_cash_gap_chf: -40,
      largest_cash_gap_date: '2026-02', break_even_date: '2026-03' } }, paths: {
      Work: { metrics: { closing_cash_chf: 160, closing_net_worth_chf: 160, min_cash_chf: 100 } },
      Study: { metrics: { closing_cash_chf: 170, closing_net_worth_chf: 175, min_cash_chf: 80 },
        sensitivity_summary: { drivers: [{ label: 'Tuition', unit: 'CHF', terminal_cash_delta_chf: -15 }] } } }, limitations: [] } });
  assert.match(container.children[4].textContent, /−CHF\s40 on 2026-02/);
  assert.match(container.children[4].textContent, /break-even 2026-03/);
  assert.match(container.children[6].textContent, /Tuition \(input change unavailable\) changes terminal cash by −CHF\s15/);
});

describe('custom comparison graphs', () => {
  test('leaf paths match tree leaves with unified codes, colors and lineages', () => {
    const index = app.buildTreeIndex();
    const leaves = app.getLeafPaths(index);
    assert.deepEqual(plain(leaves.map(l => l.id)), ['main', 'A', 'B', 'C']);
    assert.deepEqual(plain(leaves.map(l => l.code)), ['P0', 'P1', 'P1.1', 'P2']);
    assert.equal(leaves[0].label, 'Original path');
    assert.equal(leaves[0].leafNodeId, 'm6');
    for (const leaf of leaves) {
      assert.equal(leaf.color, index.branchMeta.get(leaf.id).color, 'graph color matches canonical branch color');
    }
    const nested = leaves.find(l => l.id === 'B');
    assert.equal(nested.leafNodeId, 'b6');
    assert.match(nested.trail, /Start a master's → Part-time job/);
    assert.deepEqual(plain(nested.chain.map(n => n.id)), ['root', 'm1', 'm2', 'a3', 'a4', 'b5', 'b6']);
  });

  test('niceTicks produces clean ordered ticks spanning the data range', () => {
    const { min, max, ticks } = app.niceTicks(-1200, 94500, 5);
    assert.ok(min <= -1200 && max >= 94500);
    assert.ok(ticks.length >= 3);
    assert.ok(ticks.includes(0));
  });
});

describe('display currency', () => {
  const EUR = { code: 'EUR', name: 'Euro', symbol: '€', per_chf: 1.25, date: '2026-11-20', source: 'European Central Bank reference rate' };
  const SEK = { code: 'SEK', name: 'Swedish krona', symbol: 'SEK', per_chf: 12, date: '2026-11-20', source: 'ECB' };
  const chf = { unit: 'CHF', min: 0, max: 50000, step: 100 };
  beforeEach(() => { app.setCurrency('EUR', [EUR, SEK]); });
  const same = (actual, expected) => assert.equal(actual, expected);

  test('amounts are shown converted from CHF', () => {
    app.setCurrency('CHF');
    assert.match(app.money(5000), /^CHF\s5,000$/, 'comma thousands separators, not apostrophes');
    app.setCurrency('EUR', [EUR, SEK]);
    same(app.money(8000), '€10,000');
    same(app.money(-800), '-€1,000');
    same(app.moneySigned(800), '+€1,000');
    same(app.moneySigned(0.2), '€0');
    same(app.compactChf(2_000_000), '€2.5M');
    same(app.formatVariable(chf, 4000), '€5,000');
    app.setCurrency('SEK', [EUR, SEK]);
    same(app.compactChf(1000), 'SEK 12k', 'letter codes get a space');
  });

  test('typed amounts are read in the chosen currency and stored in CHF', () => {
    assert.equal(app.parseVariableInput(chf, '€5,000'), 4000);
    assert.equal(app.parseVariableInput(chf, '5000 EUR'), 4000);
    assert.equal(app.variableToDisplay(chf, 4000), '5000');
    assert.equal(app.parseVariableInput({ unit: '%', max: 1 }, '40%'), 0.4, 'percentages are not converted');
  });

  test('amounts quoted in server text are shown in the chosen currency', () => {
    same(app.localizeAmounts('Cash changes by CHF -800 to CHF 8,000.'), 'Cash changes by -€1,000 to €10,000.');
    same(app.localizeAmounts('You have CHF 12,000.00 (written as €15,000) saved.'), 'You have €15,000 saved.');
    app.setCurrency('CHF');
    same(app.localizeAmounts('You have CHF 12,000.00 (written as €15,000).'), 'You have CHF 12,000.00 (written as €15,000).');
  });

  test('the rate and the converted story amounts are stated', () => {
    assert.equal(app.currencyRateText(EUR),
      '1 EUR = 0.80 CHF · European Central Bank reference rate of 2026-11-20. Calculations stay in CHF.');
    assert.equal(app.currencyRateText({ code: 'JPY', per_chf: 189.04, source: 'ECB', date: '2026-11-20' }),
      '100 JPY = 0.53 CHF · ECB of 2026-11-20. Calculations stay in CHF.', 'small units are quoted per 100');
    assert.equal(app.currencyRateText({ code: 'CHF' }), 'Calculations are in Swiss francs.');
    same(app.conversionText({ currency: EUR, conversions: [{ written: '€15,000', chf: 12000 }] }),
      'Converted from your story (European Central Bank reference rate of 2026-11-20): €15,000 = CHF 12,000.00. '
      + 'Amounts in your story without a currency were read as EUR.');
    same(app.conversionText({ conversions: [] }), '');
    same(app.conversionText({ currency: EUR, conversions: [] }), 'Amounts in your story without a currency were read as EUR.');
    same(app.conversionText({ currency: { code: 'CHF' }, conversions: [] }), '');
  });

  test('the run file keeps CHF numbers and records the display currency', () => {
    app.state.run = { source: 'test', seed: 1, start_date: '2026-01-01', years: 5, scenario: null, variables: {},
                      branches: [], next_sequence: 0, environment: {} };
    const run = app.buildRunFile();
    assert.equal(run.units.currency, 'CHF');
    assert.deepEqual(plain(run.display_currency), { code: 'EUR', per_chf: 1.25, date: '2026-11-20', source: EUR.source });
    app.setCurrency('CHF');
  });
});

describe('theme switching', () => {
  test('toggling light mode updates state and keeps unified branch palette colors', () => {
    assert.equal(app.state.theme, 'dark');
    const darkPalette = app.getBranchPalette();
    app.setTheme('light');
    assert.equal(app.state.theme, 'light');
    const lightPalette = app.getBranchPalette();
    assert.notEqual(lightPalette[0], darkPalette[0]);
    const index = app.buildTreeIndex();
    const leaves = app.getLeafPaths(index);
    assert.equal(leaves[0].color, lightPalette[0]);
    for (const leaf of leaves) {
      assert.equal(leaf.color, index.branchMeta.get(leaf.id).color);
    }
    app.setTheme('dark');
    assert.equal(app.state.theme, 'dark');
  });
});
