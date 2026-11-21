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
