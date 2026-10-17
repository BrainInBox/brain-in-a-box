// Every screen the window shows, as text, for the mock's scenarios with Claude alone (Codex closed,
// as the app ships): the window's words for Claude-only users stay those of screens-claude.txt.
//
//   node app/dev/screens.mjs           compares with screens-claude.txt, prints what differs, exit 1
//   node app/dev/screens.mjs --write   writes screens-claude.txt (a change of these words is on purpose)
//   node app/dev/screens.mjs --app <app.js> --mock <mock.html>   another copy of the window (master's)
//
// app.js and mock.html's fake backend run in a small DOM, on a clock of their own: each timer fires in
// order, nothing waits for real, and a run gives the same screens every time. The mock's own driver
// (&go=, &click=) does the clicking. A screen is its title, text, notes and buttons, as the user reads them.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = path.join(HERE, 'screens-claude.txt');
const RUN_MS = 120_000;

// Claude alone: a fresh install on each system, a Voyage key, already installed (and the end's two
// buttons), keywords, blocked, an early login, failures, then the repair with Claude, its errors and its states.
const SCENARIOS = [
  's=manque&go=done',
  's=manque&go=done&os=windows',
  's=manque&go=done&os=linux',
  's=manque&go=done&key=pa-abcdefghijklmnopqrstuvwxyz',
  's=manque&go=done&key=pa-abcdefghijklmnopqrstuvlimit',
  's=installe&go=done',
  's=installe&go=done&os=windows',
  's=installe&go=done&press=1',
  's=installe&go=done&click=vault&os=linux',
  's=motscles&go=done&add=diag',
  's=motscles&go=done&add=done&os=linux',
  's=bloque&go=diag',
  's=login-early&go=done',
  's=echec&go=done',
  's=echec&go=done&os=linux',
  's=echec&go=done&fail=brain&os=windows',
  's=manque&go=login&nth=1&click=next,next,next,skip',
  's=repair-ok&go=done',
  's=repair-user-action&go=done',
  's=repair-low&go=done',
  's=repair-round2&go=done',
  's=repair-fail&go=done',
  's=repair-regression&go=done',
  's=repair-quota&go=done',
  's=repair-auth&go=done',
  's=repair-suspect&go=done',
  's=repair-offline&go=done',
  's=repair-terminal&go=done&click=terminal',
  's=repair-not-logged&go=done',
  's=repair-ok&go=done&error=timeout',
  's=repair-ok&go=done&error=init_invalid',
  's=repair-ok&go=done&error=invalid_verdict',
  's=repair-ok&go=done&error=api',
  's=repair-ok&go=done&error=busy',
  's=repair-ok&go=done&repair=exhausted',
  's=repair-ok&go=done&repair=unsupported',
  's=repair-ok&go=done&repair=disabled',
  's=repair-ok&go=done&repair=not_logged_in',
  's=repair-fail&go=done&click=next,next,next,next,terminal',
  's=repair-quota&go=done&os=windows',
  's=repair-fail&go=done&os=linux',
  's=repair-ok&go=analyze&pause=analyze&click=stop',
  's=repair-auth&go=done&os=linux',
];

// ── A small DOM: what app.js and the mock's driver use ─────────────────────

class Node_ {
  constructor() { this.childNodes = []; this.parent = null; }
  get children() { return this.childNodes.filter((c) => c instanceof El); }
  adopt(n) {
    if (typeof n === 'string') n = new Text_(n);
    if (n.parent) n.remove();
    n.parent = this;
    return n;
  }
  append(...ns) { for (const n of ns.flat()) if (n != null) this.childNodes.push(this.adopt(n)); }
  replaceChildren(...ns) { for (const c of this.childNodes) c.parent = null; this.childNodes = []; this.append(...ns); }
  remove() { if (this.parent) { this.parent.childNodes = this.parent.childNodes.filter((c) => c !== this); this.parent = null; } }
  before(...ns) { this.insert(0, ns); }
  after(...ns) { this.insert(1, ns); }
  insert(offset, ns) {
    const p = this.parent;
    if (!p) return;
    const add = ns.flat().filter((n) => n != null).map((n) => p.adopt(n));
    p.childNodes.splice(p.childNodes.indexOf(this) + offset, 0, ...add);
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  set textContent(t) { this.replaceChildren(String(t)); }
}

class Text_ extends Node_ {
  constructor(t) { super(); this.t = String(t); }
  get textContent() { return this.t; }
  set textContent(t) { this.t = String(t); }
}

class ClassList {
  constructor(el) { this.el = el; }
  get list() { return (this.el.attrs.class || '').split(/\s+/).filter(Boolean); }
  set list(l) { this.el.attrs.class = l.join(' '); }
  add(...c) { this.list = [...new Set([...this.list, ...c])]; }
  remove(...c) { this.list = this.list.filter((x) => !c.includes(x)); }
  contains(c) { return this.list.includes(c); }
  toggle(c, on) { const want = on === undefined ? !this.contains(c) : on; if (want) this.add(c); else this.remove(c); return want; }
  replace(a, b) { if (!this.contains(a)) return false; this.list = this.list.map((x) => (x === a ? b : x)); return true; }
}

class El extends Node_ {
  constructor(tag, doc) {
    super();
    this.tagName = tag.toUpperCase();
    this.doc = doc;
    this.attrs = {};
    this.listeners = {};
    this.classList = new ClassList(this);
    this.style = { setProperty() {}, removeProperty() {} };
    Object.assign(this, { disabled: false, hidden: false, open: false, value: '', scrollTop: 0, scrollHeight: 0, clientHeight: 0 });
    this.dataset = new Proxy({}, {
      get: (_, k) => this.attrs[`data-${String(k)}`],
      set: (_, k, v) => { this.attrs[`data-${String(k)}`] = String(v); return true; },
    });
  }
  get className() { return this.attrs.class || ''; }
  set className(v) { this.attrs.class = v; }
  get id() { return this.attrs.id || ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'disabled') this.disabled = true; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  removeEventListener() {}
  dispatchEvent(e) { for (const f of this.listeners[e.type] || []) f(e); return true; }
  click() { if (!this.disabled) this.dispatchEvent({ type: 'click', target: this, preventDefault() {} }); }
  focus() { this.doc.activeElement = this; }
  scrollIntoView() {}
  select() {}
  // Only Bibo's SVG comes from markup: an element stands for it.
  set innerHTML(_) { this.content = { firstElementChild: new El('svg', this.doc) }; }
  matches(sel) { return sel.split(',').some((s) => matchOne(this, s.trim())); }
  closest(sel) { for (let e = this; e instanceof El; e = e.parent) if (e.matches(sel)) return e; return null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => { for (const c of n.children) { if (c.matches(sel)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

// Tag, .class, #id, [attr] and [attr=value], :not(…) and :disabled: the selectors in use.
function matchOne(el, sel) {
  const nots = [];
  const rest = sel.replace(/:not\(([^)]*)\)/g, (_, s) => { nots.push(s); return ''; });
  if (nots.some((n) => (n === ':disabled' ? el.disabled : matchOne(el, n)))) return false;
  const tag = rest.match(/^[a-z]+/i);
  if (tag && el.tagName !== tag[0].toUpperCase()) return false;
  for (const m of rest.matchAll(/\.([\w-]+)/g)) if (!el.classList.contains(m[1])) return false;
  for (const m of rest.matchAll(/#([\w-]+)/g)) if (el.id !== m[1]) return false;
  for (const m of rest.matchAll(/\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]/g)) {
    const v = el.getAttribute(m[1]);
    if (v === null || (m[2] !== undefined && v !== m[2])) return false;
  }
  return true;
}

// ── A clock of its own ─────────────────────────────────────────────────────

function clock() {
  let now = 0;
  let order = 0;
  let ids = 0;
  const timers = new Map();
  const add = (fn, ms, args, every) => {
    const id = ++ids;
    const wait = Math.max(0, Number(ms) || 0);
    timers.set(id, { at: now + (every ? Math.max(1, wait) : wait), order: ++order, fn, args, every: every ? Math.max(1, wait) : 0 });
    return id;
  };
  const next = () => {
    let best = null;
    for (const [id, t] of timers) if (!best || t.at < best[1].at || (t.at === best[1].at && t.order < best[1].order)) best = [id, t];
    return best;
  };
  return {
    now: () => now,
    setTimeout: (fn, ms, ...args) => add(fn, ms, args, false),
    setInterval: (fn, ms, ...args) => add(fn, ms, args, true),
    clear: (id) => timers.delete(id),
    // Fires the next timer due before `limit`; false when there is none.
    fire(limit) {
      const best = next();
      if (!best || best[1].at > limit) return false;
      const [id, t] = best;
      now = t.at;
      if (t.every) Object.assign(t, { at: now + t.every, order: ++order });
      else timers.delete(id);
      t.fn(...t.args);
      return true;
    },
  };
}

// Math.random, the same draws each run: Bibo blinks at random.
function seeded() {
  let x = 0x2545f491;
  const m = Object.create(Math);
  m.random = () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return ((x >>> 0) % 1e9) / 1e9; };
  return m;
}

// ── One run ────────────────────────────────────────────────────────────────

function visible(n) {
  if (n instanceof Text_) return n.t;
  if (n.hidden || ['SVG', 'PRE'].includes(n.tagName) || n.classList.contains('chrono-time')) return '';
  const inner = n.childNodes.map(visible).join('');
  if (n.tagName === 'BUTTON') return `\n[${inner}${n.disabled ? ' (inactif)' : ''}]`;
  return ['P', 'LI', 'SUMMARY', 'H1', 'H2', 'BLOCKQUOTE', 'DIV'].includes(n.tagName) ? `\n${inner}` : inner;
}

async function screens(appText, mockText, query) {
  const t = clock();
  const doc = new El('#document');
  doc.doc = doc;
  doc.body = new El('body', doc);
  doc.documentElement = new El('html', doc);
  doc.documentElement.append(doc.body);
  doc.append(doc.documentElement);
  doc.activeElement = doc.body;
  doc.createElement = (tag) => new El(tag, doc);
  doc.execCommand = () => true;
  const START = Date.UTC(2026, 9, 13, 13, 30);
  class FakeDate extends Date {
    constructor(...a) { super(...(a.length ? a : [START + t.now()])); }
    static now() { return START + t.now(); }
  }
  const ctx = {
    console: { log() {}, warn() {}, error: (...a) => process.stderr.write(`${a.join(' ')}\n`) },
    setTimeout: t.setTimeout, clearTimeout: t.clear, setInterval: t.setInterval, clearInterval: t.clear,
    requestAnimationFrame: (f) => t.setTimeout(f, 16), cancelAnimationFrame: t.clear,
    Date: FakeDate, Math: seeded(), URLSearchParams,
    document: doc, Element: El, Event: class { constructor(type) { this.type = type; } },
    navigator: { userAgent: 'Macintosh', clipboard: { writeText: async () => {} } },
    location: { search: `?${query}` },
    addEventListener() {}, getSelection: () => '',
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(mockText.match(/<script>\n([\s\S]*?)<\/script>/)[1], ctx, { filename: 'mock.html' });
  vm.runInContext(appText, ctx, { filename: 'app.js' });

  // Each screen once, in the order it first shows: a login polled ten times is one screen.
  const shots = [];
  const snap = () => {
    const name = doc.body.dataset.screen;
    if (!name) return;
    const text = `== ${name}${visible(doc.body).replace(/\n\s*\n/g, '\n')}`;
    if (!shots.includes(text)) shots.push(text);
  };
  // Promises settle between two timers, as in the window.
  const settle = () => new Promise((r) => setImmediate(r));
  await settle();
  snap();
  while (t.fire(RUN_MS)) {
    await settle();
    snap();
  }
  return shots;
}

// screens-claude.txt: each scenario as the numbers of its screens, then every screen once.
function render(got) {
  const all = [];
  const lines = ["# The window's screens with Claude alone (app/dev/screens.mjs --write): each scenario, the numbers", '# of the screens it shows in their order, then the screens.', ''];
  for (const [q, shots] of got) {
    const nums = shots.map((s) => (all.includes(s) ? all.indexOf(s) : all.push(s) - 1) + 1);
    lines.push(`### ${q}`, nums.join(' '), '');
  }
  all.forEach((s, i) => lines.push(`#${i + 1}`, s, ''));
  return lines.join('\n');
}

function parse(text) {
  const screens = new Map();
  for (const part of text.split(/^#(?=\d+\n)/m).slice(1)) {
    const nl = part.indexOf('\n');
    screens.set(part.slice(0, nl), part.slice(nl + 1).replace(/\n+$/, ''));
  }
  const scenarios = new Map();
  for (const m of text.matchAll(/^### (.*)\n(.*)$/gm)) scenarios.set(m[1], m[2].split(' ').map((n) => screens.get(n) ?? `(no screen #${n})`));
  return scenarios;
}

const args = process.argv.slice(2);
const opt = (name, fallback) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : fallback; };
const appText = fs.readFileSync(opt('--app', path.join(HERE, '..', 'src', 'app.js')), 'utf8');
const mockText = fs.readFileSync(opt('--mock', path.join(HERE, 'mock.html')), 'utf8');
const got = new Map();
for (const q of SCENARIOS) got.set(q, await screens(appText, mockText, q));

if (args.includes('--write')) {
  fs.writeFileSync(GOLDEN, render(got));
  console.log(`${got.size} scenarios written to ${path.relative(process.cwd(), GOLDEN)}`);
  process.exit(0);
}
const want = parse(fs.readFileSync(GOLDEN, 'utf8'));
let differ = 0;
for (const [q, shots] of got) {
  const w = want.get(q) || ['(scenario missing from screens-claude.txt)'];
  const i = shots.findIndex((s, k) => s !== w[k]);
  if (i < 0 && shots.length === w.length) continue;
  differ += 1;
  const at = i < 0 ? shots.length : i;
  console.log(`DIFF ${q}, screen ${at + 1}:\n--- expected\n${w[at] ?? '(none)'}\n--- shown\n${shots[at] ?? '(none)'}\n`);
}
if (want.size !== got.size) {
  differ += 1;
  console.log(`screens-claude.txt has ${want.size} scenarios, this script runs ${got.size}`);
}
console.log(differ ? `${differ} of ${got.size} scenarios differ` : `${got.size} scenarios, the same screens`);
process.exit(differ ? 1 : 0);
