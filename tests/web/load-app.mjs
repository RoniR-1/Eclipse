// Loads web/app.js in an isolated Node context with stand-in browser objects, so its logic can be tested
// without a browser. Returns the app's internal functions and state, plus spies for UI side effects.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const EXPORTS = [
  'state', 'branchNodeIds', 'originalPathRemovals', 'isOriginalBranch', 'deleteBranch', 'buildRunFile',
  'buildTreeIndex', 'getPathMilestones', 'nodeHealth', 'formatVariable', 'milestoneTitle', 'money',
  'moneySigned', 'compactChf', 'escapeHtml', 'variableToDisplay', 'parseVariableInput', 'renderTuning',
  'setCurrency', 'localizeAmounts', 'currencyRateText', 'conversionText', 'chfToDisplay', 'displayToChf',
];

// An object that accepts any property access, assignment or call, standing in for DOM elements.
function stub() {
  return new Proxy(function () {}, {
    get: (_, key) => {
      if (key === Symbol.toPrimitive) return () => 0;
      if (key === 'then') return undefined; // not a promise
      if (key === Symbol.iterator) return function* () {};
      return stub();
    },
    apply: () => stub(),
    set: () => true,
  });
}

export function loadApp({ document: customDocument } = {}) {
  const source = readFileSync(new URL('../../web/app.js', import.meta.url), 'utf8');
  const context = {
    console, Intl, URL, Blob, structuredClone, setTimeout, clearTimeout, Math, Date, JSON,
    document: customDocument || {
      getElementById: () => stub(),
      querySelector: () => stub(),
      querySelectorAll: () => [],
      createElement: () => stub(),
      addEventListener() {},
      body: stub(),
      hidden: false,
    },
    window: { addEventListener() {}, scrollTo() {}, innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1 },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    requestAnimationFrame: () => 0,
    cancelAnimationFrame() {},
    performance: { now: () => 0 },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    CSS: { escape: value => value },
    vis: {},
  };
  vm.createContext(context);
  vm.runInContext(`${source}\n;globalThis.__app = { ${EXPORTS.join(', ')} };`, context, { filename: 'web/app.js' });

  // Replace UI side effects with spies so state logic can be checked on its own.
  const calls = [];
  const spy = name => (...args) => { calls.push([name, ...args]); };
  for (const name of ['switchActivePath', 'renderTreeGraph', 'selectNode', 'showToast', 'showStage']) {
    context[name] = spy(name);
  }
  let confirmAnswer = true;
  context.confirmDialog = async options => { calls.push(['confirmDialog', options]); return confirmAnswer; };

  return {
    app: context.__app,
    calls,
    answerConfirm(value) { confirmAnswer = value; },
  };
}

// A minimal DOM that keeps state: enough to exercise renderTuning's slider and text-box wiring.
export class FakeElement {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.textContent = '';
    this.value = '';
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name),
      toggle: (name, force = !classes.has(name)) => { if (force) classes.add(name); else classes.delete(name); return force; },
    };
  }
  set className(names) { names.split(/\s+/).filter(Boolean).forEach(name => this.classList.add(name)); }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  dispatch(type, extra = {}) { for (const listener of this.listeners[type] || []) listener({ type, target: this, preventDefault() {}, ...extra }); }
  *walk() { for (const child of this.children) { yield child; if (child instanceof FakeElement) yield* child.walk(); } }
  find(predicate) { for (const node of this.walk()) if (predicate(node)) return node; return null; }
}

export function fakeDocument() {
  const byId = new Map();
  return {
    getElementById: id => { if (!byId.has(id)) byId.set(id, new FakeElement()); return byId.get(id); },
    querySelector: () => new FakeElement(),
    querySelectorAll: () => [],
    createElement: tag => new FakeElement(tag),
    addEventListener() {},
    body: new FakeElement('body'),
  };
}
