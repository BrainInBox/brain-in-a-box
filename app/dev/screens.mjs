// Every screen the window shows, as text, for the mock's scenarios with Claude alone (Codex closed,
// as the app ships): the window's words for Claude-only users stay those of screens-claude.txt.
// Then a few checks of Codex's screens, Codex open in the mock as the tests open it. Then the guide's
// window (guide.html, guide-mock.html's scenarios), as screens-guide.txt holds it.
//
//   node app/dev/screens.mjs           compares with screens-claude.txt and screens-guide.txt, prints what differs, exit 1
//   node app/dev/screens.mjs --write   writes them (a change of these words is on purpose)
//   node app/dev/screens.mjs --app <app.js> --mock <mock.html>   another copy of the window (master's), without the guide
//
// The window's scripts and the mock's fake backend run in a small DOM, on a clock of their own: each timer
// fires in order, nothing waits for real, and a run gives the same screens every time. The mock's own driver
// (&go=, &click=) does the clicking. A screen is its title, text, notes and buttons, as the user reads them.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

// The guide says the time the subscription comes back: the same hour on every machine.
process.env.TZ = 'Europe/Paris';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = path.join(HERE, 'screens-claude.txt');
const GUIDE_GOLDEN = path.join(HERE, 'screens-guide.txt');
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
  // The advice at the diagnosis: ChatGPT seen while Codex is closed, Hermes seen (and read each evening).
  's=manque&go=diag&conseil=chatgpt_bientot&hermes=1',
  's=installe&go=diag&conseil=claude_vu&os=windows',
  's=installe&go=diag&hermes=lu&os=linux',
  // « Importer ma mémoire » from the end: an export read, sized, sent, written; the paste; each stop.
  's=installe&go=import-done&click=import',
  's=installe&go=import-done&click=import,size-rapide&sent=1',
  's=installe&go=import-done&click=import&fichiers=3&os=windows',
  's=installe&go=import-done&click=import,paste&colle=1',
  's=installe&go=import-paste&click=import,paste&os=linux',
  's=installe&go=import-done&click=import&imp=partiel',
  's=installe&go=import-done&click=import&imp=suspect',
  's=installe&go=import-done&click=import&imp=rien',
  's=installe&go=import-error&click=import&imp=deja',
  's=installe&go=import-error&click=import&imp=compte',
  's=installe&go=import-error&click=import&imp=auth',
  's=installe&go=import-error&click=import&imp=quota',
  's=installe&go=import-error&click=import&imp=a_jour',
  // A beginner's likeliest stops: not an export, a folder's five files, the AI silent, the sentence pasted back.
  's=installe&go=import-error&click=import&imp=pas_un_zip',
  's=installe&go=import-error&click=import&imp=trop_de_fichiers&os=windows',
  's=installe&go=import-error&click=import&imp=timeout',
  's=installe&go=import-paste&nth=2&click=import,paste&colle=1&imp=phrase',
  's=installe&go=import&nth=2&click=import,next,back',
  's=installe&go=import&nth=2&click=import,next,next,stop&pause=import',
  // The model refused to answer (Claude Code would have had another one answer instead).
  's=repair-ok&go=done&error=refused',
];

// The guide in paste mode (guide-mock.html): a whole session to its end and the procedure saved, on each
// system, then each step's variant, each error of §2 and each state that keeps Claude from being asked.
const GUIDE_SCENARIOS = [
  'g=ok&vu=0&go=enregistre',
  'g=ok&go=enregistre&os=windows',
  'g=ok&go=enregistre&os=linux',
  'g=ok&go=apercu&sent=1',
  'g=ok&go=apercu&nth=2&click=cacher&cache=1',
  'g=ok&go=apercu&nth=3&click=cacher,autre&cache=1&taille=1',
  'g=ok&go=apercu&depose=1',
  'g=ok&go=apercu&nth=2&pause=1&click=arreter',
  'g=ok&go=objectif&nth=2&click=comment',
  'g=ok&go=abandon&click=bloque,next,bloque,next,bloque',
  'g=pointille&go=etape',
  'g=bas&go=etape',
  'g=ambigu&go=etape&nth=2',
  'g=pas-ici&go=etape',
  'g=bloque&go=etape',
  'g=pas-fait&go=etape&nth=2',
  'g=pas-fait&go=etape&nth=2&click=reessaie',
  'g=sensible&go=etape&nth=4',
  'g=suspect&go=bloque',
  'g=clickfix&go=bloque',
  'g=refus&go=apercu&nth=2&cache=1',
  'g=quota&go=erreur',
  'g=presque-limite&go=etape',
  'g=hors-ligne&go=etape',
  'g=delai&go=etape',
  'g=init&go=erreur',
  'g=image-non-vue&go=etape',
  'g=noire&go=apercu',
  'g=petit&go=apercu&os=windows',
  'g=budget&go=erreur',
  'g=budget-etape&go=abandon',
  'g=colle&go=apercu',
  'g=deja&go=coller',
  'g=remote&go=enregistre',
  'g=refusee&go=enregistre',
  'g=auth&go=objectif',
  'g=billed&go=facture',
  'g=vieux&go=vieux',
  'g=arrete&go=arrete',
  'g=installer&go=installer',
  // The review's (a beginner's stops): the capture of before pasted again, the filter alone, the limit
  // today or tomorrow, a client's name in a step to save, back from the first capture, the reduced
  // mode kept from a step to the next and its note, no image in what was pasted, a JPEG dropped.
  'g=ok&go=apercu&nth=2&meme=1',
  'g=filtre&go=bloque',
  'g=quota&go=erreur&reprise=7200',
  'g=quota&go=erreur&reprise=86400',
  'g=ambigu&go=enregistrer',
  'g=ok&go=objectif&nth=2&click=back',
  'g=pointille&go=etape&click=reduire',
  'g=ok&go=etape&nth=2&click=reduire',
  'g=ok&go=apercu&vide=1',
  'g=ok&go=apercu&vide=1&os=windows',
  'g=ok&go=erreur&depose=jpeg',
  'g=ok&go=etape&nth=2&lache=1',
];

// The guide's window sizes, as the page asks Rust for them, in turn: the image made wide to hide a zone
// and kept so while zones are drawn, the reduced mode kept from a step to the next.
const GUIDE_SIZES = {
  'g=ok&go=apercu&nth=2&click=cacher&cache=1': 'large',
  'g=ok&go=etape&nth=2&click=reduire': 'reduit normal reduit',
};

// Where Codex can be chosen (&porte=1, as the tests open it): what some screens must say, or not.
const has = (shots, start, text) => shots.some((s) => s.startsWith(`== ${start}`) && s.includes(text));
const last = (shots, start) => shots.filter((s) => s.startsWith(`== ${start}`)).pop() || '';
const CODEX_CHECKS = [
  {
    name: 'Codex on an API key, login put off: the end connects it, never opens the onboarding',
    query: 's=manque&porte=1&choix=codex&codex=billed&click=skip,skip&go=done',
    ok: (shots) => {
      const done = last(shots, 'done');
      return done.includes('[Connecter Codex à mon compte ChatGPT]') && !done.includes('[Personnaliser mon cerveau (15 min)]')
        && done.includes('clé API') && !done.includes('il te le demandera au premier lancement');
    },
  },
  {
    name: 'Codex not logged in, login put off: the end connects it first, and says to pick ChatGPT, never a key',
    query: 's=manque&porte=1&choix=codex&click=skip,skip&go=done',
    ok: (shots) => {
      const done = last(shots, 'done');
      return done.includes('[Connecter Codex à mon compte ChatGPT]') && !done.includes('[Personnaliser mon cerveau (15 min)]')
        && done.includes('Sign in with ChatGPT');
    },
  },
  {
    name: 'Claude preferred, Codex put off: the onboarding is Claude\'s, the note says Codex\'s state',
    query: 's=manque&porte=1&choix=both&resume=claude&codex=billed&click=skip,skip,skip,skip&go=done',
    ok: (shots) => {
      const done = last(shots, 'done');
      return done.includes('[Personnaliser mon cerveau (15 min)]') && done.includes('clé API');
    },
  },
  {
    name: 'A workspace account at the login: the wait ends and says why',
    query: 's=manque&porte=1&choix=codex&after=managed&click=skip&go=done',
    ok: (shots) => has(shots, 'login', 'compte ChatGPT d’entreprise') && has(shots, 'login', 'espace personnel'),
  },
  {
    name: 'Windows, Codex\'s system settings open to other accounts: no login button, only later',
    query: 's=manque&porte=1&choix=codex&codex=system&os=windows&click=skip&go=login',
    ok: (shots) => {
      const login = last(shots, 'login');
      return login.includes('ProgramData') && login.includes('[Plus tard]') && !login.includes('[Connecter Codex à mon compte ChatGPT]');
    },
  },
  {
    name: 'An API key at the login: one button, no command to type',
    query: 's=manque&porte=1&choix=codex&codex=billed&click=skip&go=login',
    ok: (shots) => {
      const login = last(shots, 'login');
      return !login.includes('codex logout') && login.includes('ta connexion remplacera la clé') && login.includes('[Changer d’IA]');
    },
  },
  {
    name: 'Codex alone, a step fails before the brain: Codex can help once the brain is in, no login offered',
    query: 's=echec&porte=1&choix=codex&click=skip&go=done',
    ok: (shots) => has(shots, 'install', 'Codex pourra m’aider une fois ton cerveau installé')
      && !shots.some((s) => s.includes('Oups, ça a coincé.') && s.includes('[Connecter Codex à mon compte ChatGPT]')),
  },
  {
    name: 'The AI changed from the diagnosis: Codex, then Claude, and the diagnosis is Claude\'s alone',
    query: 's=manque&porte=1&choix=codex,claude&click=change&go=diag&nth=2',
    ok: (shots, claude) => {
      const alone = claude.get('s=manque&go=done').find((s) => s.startsWith('== diag\nJ’ai fait le tour.'));
      // Where Codex can be chosen, the advice is on the choice screen, not at the diagnosis.
      return Boolean(alone) && last(shots, 'diag') === alone.replace(/\nMon conseil[^\n]*/, '').replace('\n[Revérifier]', '\n[Changer d’IA]\n[Revérifier]');
    },
  },
  {
    name: 'Where Codex can be chosen, the advice may name it, on the choice screen',
    query: 's=manque&porte=1&conseil=codex&go=choice&at=.advice',
    ok: (shots) => has(shots, 'choice', 'Mon conseil') && has(shots, 'choice', 'ton cerveau peut travailler avec Codex')
      && !shots.some((s) => s.startsWith('== diag') && s.includes('Mon conseil')),
  },
  {
    name: 'Codex closed: the advice at the diagnosis never offers Codex, Hermes is not installed',
    query: 's=manque&conseil=chatgpt_bientot&hermes=1&go=diag',
    ok: (shots) => has(shots, 'diag', 'Codex, l’IA de ChatGPT, arrive bientôt ici') && has(shots, 'diag', 'je ne l’installe pas')
      && !shots.some((s) => s.includes('peut travailler avec Codex')),
  },
  {
    name: 'One account rule everywhere: a personal ChatGPT plan, and both accounts for both',
    query: 's=manque&porte=1&go=choice',
    ok: (shots) => has(shots, 'welcome', 'abonnement ChatGPT personnel (pas un compte d’entreprise)')
      && has(shots, 'choice', 'abonnement ChatGPT personnel (pas un compte d’entreprise)') && has(shots, 'choice', 'Il te faut les deux comptes.'),
  },
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
  // As the browser's: an array or null would be appended as its text ("[object …]", "null").
  append(...ns) {
    for (const n of ns) {
      if (n == null || Array.isArray(n)) throw new TypeError(`append(${n === null ? 'null' : Array.isArray(n) ? 'an array' : n}): the window would show it as text`);
      this.childNodes.push(this.adopt(n));
    }
  }
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
  // The guide's image, 370 wide: where a zone drawn on it lands.
  getBoundingClientRect() { return { left: 0, top: 0, width: 370, height: 239, right: 370, bottom: 239 }; }
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

// The guide's reduced mode hides what styles.css says it hides: its own rules, applied here as written.
const STYLES = fs.readFileSync(path.join(HERE, '..', 'src', 'styles.css'), 'utf8');
// A selector list split on its commas, those inside :not(…) aside.
const selectors = (list) => list.split(/,(?![^(]*\))/).map((sel) => sel.trim()).filter(Boolean);
const REDUCED = [...STYLES.matchAll(/^(body\.guide\.reduit [^{}]+)\{\s*display: none;\s*\}/gm)]
  .flatMap((m) => selectors(m[1])).filter((sel) => sel.startsWith('body.guide.reduit ')).map((sel) => sel.replace(/^body\.guide\.reduit /, ''));
function hiddenWhenReduced(n) {
  if (!n.doc || !n.doc.body || !n.doc.body.classList.contains('reduit')) return false;
  return REDUCED.some((sel) => {
    const child = sel.match(/^(\S+) > :not\((.*)\)$/);
    if (child) return n.parent instanceof El && n.parent.matches(child[1]) && !child[2].split(',').some((k) => matchOne(n, k.trim()));
    const parts = sel.split(/\s+/);
    if (!matchOne(n, parts[parts.length - 1])) return false;
    let at = n.parent;
    for (const up of parts.slice(0, -1).reverse()) {
      while (at instanceof El && !matchOne(at, up)) at = at.parent;
      if (!(at instanceof El)) return false;
      at = at.parent;
    }
    return true;
  });
}

function visible(n) {
  if (n instanceof Text_) return n.t;
  if (n.hidden || ['SVG', 'PRE'].includes(n.tagName) || n.classList.contains('chrono-time') || hiddenWhenReduced(n)) return '';
  const inner = n.childNodes.map(visible).join('');
  if (n.tagName === 'BUTTON') return `\n[${inner}${n.disabled ? ' (inactif)' : ''}]`;
  return ['P', 'LI', 'SUMMARY', 'H1', 'H2', 'BLOCKQUOTE', 'DIV'].includes(n.tagName) ? `\n${inner}` : inner;
}

// The mock's fake backend, then the window's scripts in order, as their page loads them.
async function screens(mockText, scripts, query) {
  const t = clock();
  const doc = new El('#document');
  doc.doc = doc;
  doc.body = new El('body', doc);
  doc.documentElement = new El('html', doc);
  doc.documentElement.append(doc.body);
  doc.append(doc.documentElement);
  doc.activeElement = doc.body;
  doc.createElement = (tag) => new El(tag, doc);
  doc.createElementNS = (_, tag) => new El(tag, doc);
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
  for (const [name, text] of scripts) vm.runInContext(text, ctx, { filename: name });

  // Each screen once, in the order it first shows: a login polled ten times is one screen.
  const shots = [];
  const snap = () => {
    const name = doc.body.dataset.screen;
    if (!name) return;
    // The guide's window made wide or reduced: said next to the screen's name.
    const size = ['large', 'reduit'].find((c) => doc.body.classList.contains(c));
    const text = `== ${name}${size ? ` (${size})` : ''}${visible(doc.body).replace(/\n\s*\n/g, '\n')}`;
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
  // Where the run stopped, though its screen may have been seen before; the guide's window sizes asked,
  // and its looks refused for their capture.
  shots.last = doc.body.dataset.screen;
  shots.sizes = (ctx.__tailles || []).join(' ');
  shots.refused = ctx.__refused || 0;
  return shots;
}

// screens-claude.txt: each scenario as the numbers of its screens, then every screen once.
function render(got, whose = "The window's screens with Claude alone") {
  const all = [];
  const lines = [`# ${whose} (app/dev/screens.mjs --write): each scenario, the numbers`, '# of the screens it shows in their order, then the screens.', ''];
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

// Each scenario as its golden file has it: what differs, a scenario missing on either side.
function compare(file, want, got) {
  let differ = 0;
  for (const [q, shots] of got) {
    const w = want.get(q) || [`(scenario missing from ${path.basename(file)})`];
    const i = shots.findIndex((s, k) => s !== w[k]);
    if (i < 0 && shots.length === w.length) continue;
    differ += 1;
    const at = i < 0 ? shots.length : i;
    console.log(`DIFF ${q}, screen ${at + 1}:\n--- expected\n${w[at] ?? '(none)'}\n--- shown\n${shots[at] ?? '(none)'}\n`);
  }
  if (want.size !== got.size) {
    differ += 1;
    console.log(`${path.basename(file)} has ${want.size} scenarios, this script runs ${got.size}`);
  }
  return differ;
}

const args = process.argv.slice(2);
const opt = (name, fallback) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : fallback; };
const appText = fs.readFileSync(opt('--app', path.join(HERE, '..', 'src', 'app.js')), 'utf8');
const mockText = fs.readFileSync(opt('--mock', path.join(HERE, 'mock.html')), 'utf8');
const uiText = fs.readFileSync(path.join(HERE, '..', 'src', 'ui.js'), 'utf8');
// An app.js of before ui.js (master's) has the helpers itself.
const appScripts = /^function h\(/m.test(appText) ? [['app.js', appText]] : [['ui.js', uiText], ['app.js', appText]];
const got = new Map();
for (const q of SCENARIOS) got.set(q, await screens(mockText, appScripts, q));
// The guide's window, on this copy only (master's has none).
const guide = new Map();
if (!args.includes('--app')) {
  const guideMock = fs.readFileSync(path.join(HERE, 'guide-mock.html'), 'utf8');
  const guideScripts = [['ui.js', uiText], ['guide.js', fs.readFileSync(path.join(HERE, '..', 'src', 'guide.js'), 'utf8')]];
  for (const q of GUIDE_SCENARIOS) guide.set(q, await screens(guideMock, guideScripts, q));
}

if (args.includes('--write')) {
  fs.writeFileSync(GOLDEN, render(got));
  console.log(`${got.size} scenarios written to ${path.relative(process.cwd(), GOLDEN)}`);
  if (guide.size) {
    fs.writeFileSync(GUIDE_GOLDEN, render(guide, "The guide's screens, paste mode"));
    console.log(`${guide.size} scenarios written to ${path.relative(process.cwd(), GUIDE_GOLDEN)}`);
  }
  process.exit(0);
}
const want = parse(fs.readFileSync(GOLDEN, 'utf8'));
let differ = 0;
// The Codex screens' checks, on this copy of the window only (another app.js is master's).
if (!args.includes('--app')) {
  for (const c of CODEX_CHECKS) {
    const shots = await screens(mockText, appScripts, c.query);
    if (c.ok(shots, want)) continue;
    differ += 1;
    console.log(`FAIL ${c.name}\n  ${c.query}\n${shots.slice(-3).join('\n\n')}\n`);
  }
}
differ += compare(GOLDEN, want, got);
// bibo.js (not loaded here) builds on these parts of the drawing: one renamed, and Bibo stands still.
for (const part of ['b-body', 'b-brain', 'b-front', 'b-bubbles']) {
  if (uiText.includes(`class="${part}"`)) continue;
  differ += 1;
  console.log(`FAIL BIBO_SVG has no class="${part}", which bibo.js needs`);
}
if (guide.size) {
  // A scenario that does not get to its screen is a failure, whatever the golden file says.
  for (const [q, shots] of guide) {
    const to = new URLSearchParams(q).get('go');
    if (shots.refused) {
      differ += 1;
      console.log(`FAIL ${q}: ${shots.refused} look(s) sent with another capture than the one shown`);
    }
    if (q in GUIDE_SIZES && shots.sizes !== GUIDE_SIZES[q]) {
      differ += 1;
      console.log(`FAIL ${q}: the window's sizes « ${shots.sizes} », not « ${GUIDE_SIZES[q]} »`);
    }
    if (shots.last === to) continue;
    differ += 1;
    console.log(`FAIL ${q} does not end on « ${to} »:\n${shots.slice(-2).join('\n\n')}\n`);
  }
  differ += compare(GUIDE_GOLDEN, fs.existsSync(GUIDE_GOLDEN) ? parse(fs.readFileSync(GUIDE_GOLDEN, 'utf8')) : new Map(), guide);
}
console.log(differ ? `${differ} differ or fail` : `${got.size} scenarios, the same screens; ${args.includes('--app') ? 0 : CODEX_CHECKS.length} checks of Codex's screens pass; ${guide.size} scenarios of the guide, the same screens`);
process.exit(differ ? 1 : 0);
