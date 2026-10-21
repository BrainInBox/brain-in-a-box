// Brain in a Box: the installer window. Plain JS, no build step.
// The Rust side does the work (see src-tauri/src/lib.rs); this file talks to it
// through window.__TAURI__ and keeps every word the user reads in French.
'use strict';

const MAX_LOG_LINES = 300;
const REPORT_LOG_LINES = 50;
const LOGIN_POLL_MS = 3000;
const HAPPY_MS = 4500;
// After « Réparé ! », long enough to read it.
const FIXED_PAUSE_MS = 2500;
const VOYAGE_DASHBOARD = 'https://dashboard.voyageai.com';

// The same window on a Mac, on Windows and on Linux: only the words for the machine, its
// settings, its command window and what the system may ask during the install differ.
// dev/mock.html sets window.BIBO_OS.
const SYSTEMS = {
  macos: {
    machine: 'Mac', terminal: 'le Terminal', inTerminal: 'dans le Terminal', window: 'fenêtre Terminal', settings: 'Réglages',
    // The brain lives in Documents, a folder macOS guards: refusing breaks the step.
    asks: ['brain', 'macOS va peut-être demander l’accès à ton dossier Documents : clique sur « Autoriser ».'],
  },
  windows: {
    machine: 'PC', terminal: 'PowerShell', inTerminal: 'dans PowerShell', window: 'fenêtre PowerShell', settings: 'Paramètres',
    // winget may need an administrator's yes for Git or Python.
    asks: ['brain', 'Windows va peut-être demander une autorisation pour installer Git ou Python : clique sur « Oui ».'],
  },
  linux: {
    machine: 'PC', terminal: 'le Terminal', inTerminal: 'dans le Terminal', window: 'fenêtre de Terminal', settings: 'Paramètres',
    // The system's packages and Obsidian's are installed as root: the system asks for the password.
    asks: ['root', 'Ton ordinateur va te demander ton mot de passe pour installer des outils du système ou Obsidian : c’est normal, tape-le.'],
  },
};
const SYSTEM = Object.hasOwn(SYSTEMS, window.BIBO_OS || '') ? window.BIBO_OS : detectSystem();
const WINDOWS = SYSTEM === 'windows';
const OS = SYSTEMS[SYSTEM];
// The Linux steps that run as root, after the system's password window.
const ROOT_STEPS = ['paquets', 'obsidian'];

const STEPS = {
  paquets: {
    name: 'Outils de base',
    short: 'les outils de base',
    what: 'git, python3 et curl, des outils du système.',
    running: 'Tape ton mot de passe dans la fenêtre du système.',
    say: ['Ton ordinateur va te demander ton mot de passe.', 'C’est pour installer les outils du système qui manquent.'],
  },
  clt: {
    name: 'Outils Apple',
    short: 'les outils Apple',
    what: 'Les outils de base fournis par Apple.',
    running: 'Clique sur « Installer » dans la fenêtre d’Apple.',
    say: ['Une fenêtre Apple va s’ouvrir : clique sur Installer.', 'Je t’attends. Tu ne la vois pas ? Regarde dans le Dock.'],
  },
  bun: {
    name: 'Bun',
    short: 'Bun',
    what: 'Le moteur de ta mémoire.',
    say: ['J’installe Bun.', 'C’est le moteur de ta mémoire.'],
  },
  claude: {
    name: 'Claude Code',
    short: 'Claude Code',
    what: `Claude, directement sur ton ${OS.machine}.`,
    say: ['J’installe Claude Code.', 'L’outil officiel d’Anthropic.'],
  },
  codex: {
    name: 'Codex',
    short: 'Codex',
    what: `Codex, directement sur ton ${OS.machine}.`,
    say: ['J’installe Codex.', 'L’outil officiel d’OpenAI.'],
  },
  obsidian: {
    name: 'Obsidian',
    short: 'Obsidian',
    what: 'Pour feuilleter ton cerveau.',
    // Linux: its official package, installed as root once the system has the password.
    say: ['J’installe Obsidian.', SYSTEM === 'linux'
      ? 'Téléchargement de 110 Mo, puis ton ordinateur te demande ton mot de passe.'
      : 'Gros téléchargement (230 Mo) : un peu de patience.'],
  },
  brain: {
    name: 'Ton cerveau',
    short: 'ton cerveau',
    // On Windows, install.ps1 brings Python, Git, Bun and Obsidian too.
    what: WINDOWS ? 'Tes notes, ta mémoire, tes automatismes, et Obsidian.' : 'Tes notes, ta mémoire et tes automatismes.',
    say: ['Je prépare ton cerveau.', WINDOWS ? 'C’est l’étape la plus longue : 10 à 20 minutes.' : 'C’est l’étape la plus longue.'],
  },
  // Not a step of its own: the brain step again, with a key, upgrades a brain that
  // only searches by keywords.
  voyage: {
    name: 'Recherche par le sens',
    short: 'la recherche par le sens',
    what: 'Ton cerveau apprend à retrouver tes notes par le sens.',
    say: ['J’active la recherche par le sens.', 'Ton cerveau relit tes notes : quelques minutes.'],
    step: 'brain',
  },
};

const CHECK_STATUS = { ok: 'OK', missing: 'À installer', warn: 'À noter', error: 'Bloquant' };
const STEP_STATUS = { pending: 'En attente', running: 'En cours…', done: 'Fait', failed: 'Échec' };

const state = {
  // agents_offer: whether the window asks who the brain works with (only where the kit lets Codex be
  // chosen), and what it preselects, the preferred one first.
  offer: { open: false, agents: [] },
  // guide_offert: the brain and Claude Code are there, the welcome leads to the guide (G0).
  guide: false,
  // The agents picked on the choice screen, the preferred one first. null: nothing was asked, Claude
  // alone, every screen as before Codex.
  agents: null,
  diag: null,
  // machine_advice, for the choice screen where Codex can be chosen: undefined until asked.
  advice: undefined,
  // « Importer ma mémoire »: the plan the importer made (what it read, the choices, no agreement code).
  importPlan: null,
  // The Voyage key lives only here, in memory, and only once Voyage accepted it.
  key: '',
  // A brain step went through with a key: search by meaning is no longer on offer.
  keyUsed: false,
  // The step ids the install screen goes through.
  plan: [],
  steps: {},
  failed: null,
  logs: [],
  // The install screen asked once for the Claude login (E1): it does not ask again.
  loginAsked: false,
  loginSkipped: false,
  // « Plus tard » at Codex's ChatGPT login.
  codexLoginSkipped: false,
  // How Codex was last seen logged in (codex_login): only "chatgpt" is a login the kit uses.
  codexLogin: '',
  repair: {
    // Plans that set something aside, per step id: « Remettre comme avant » puts it back.
    aside: {},
    // repair_report, fetched ahead: the copy must happen within the click.
    report: '',
    // Who repair_status says would be asked: Claude unless agents.json lists Codex.
    engine: 'claude',
    // Claude in Terminal may be offered (Claude is one of the agents).
    terminal: true,
  },
};

// DOM handles and per-screen timers. screen() resets them.
const ui = {};
const view = { gen: 0, timers: [], rows: {}, log: null, bar: null, polling: null, failure: null, progress: null, onLine: null, asking: null, asked: null, onImport: null };

// ── Helpers ────────────────────────────────────────────────────────────────

function listFr(items) {
  if (items.length < 2) return items.join('');
  return `${items.slice(0, -1).join(', ')} et ${items[items.length - 1]}`;
}

function stepInfo(id) {
  return Object.hasOwn(STEPS, id) ? STEPS[id] : { name: id, short: id, what: '', say: [`J’installe ${id}.`, ''] };
}

// The run_step id behind a row of the install screen.
function stepRun(id) {
  return stepInfo(id).step || id;
}

// The system may ask the user something during this step (OS.asks: the step it comes with, the words).
function systemAsks(id) {
  return OS.asks[0] === 'root' ? ROOT_STEPS.includes(stepRun(id)) : stepRun(id) === OS.asks[0];
}

// "8", "2,3", "182": a decimal only when it says something.
function gb(n) {
  const v = Number(n) || 0;
  const tenth = Math.round(v * 10) / 10;
  return v < 10 && !Number.isInteger(tenth) ? tenth.toFixed(1).replace('.', ',') : String(Math.round(v));
}

function later(fn, ms) {
  view.timers.push(setTimeout(fn, ms));
}

function every(fn, ms) {
  const id = setInterval(fn, ms);
  view.timers.push(id);
}

// "0:42"
function clock(ms) {
  const sec = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

// The app's sentences start with a capital; after « Il pense que : » they read on.
function lowerFirst(text) {
  return /^(Voyage|Bun|Obsidian|Claude|Codex|ChatGPT|OpenAI|Apple|Mac|PC|Windows|PowerShell|Linux)\b/.test(text) ? text : text.charAt(0).toLowerCase() + text.slice(1);
}

// No space inside: the patterns that keep a label (« token: … ») mask a mask into itself.
const MASKED = '[masqué]';
// The key shapes of src-tauri/src/redact.rs: the install log reaches the window as
// the programs printed it, and from there the report and the clipboard.
const SECRET_PATTERNS = [
  [/\b(?:pa|al)-[A-Za-z0-9_-]{16,}/g, MASKED],
  [/sk-ant-[\w-]+/g, MASKED],
  [/\bsk-[A-Za-z0-9_-]{20,}/g, MASKED],
  [/\bgh[pousr]_\w{20,}/g, MASKED],
  [/\bgithub_pat_\w{20,}/g, MASKED],
  [/\bglpat-[\w-]{20,}/g, MASKED],
  [/\bnpm_[A-Za-z0-9]{36}/g, MASKED],
  [/AKIA[0-9A-Z]{16}/g, MASKED],
  [/xox[abposr]-\S+/g, MASKED],
  [/\bbearer\s+\S+/gi, `Bearer ${MASKED}`],
  [/(api[_-]?key|token|secret|password|authorization)(["']?\s*[:=]\s*)\S+/gi, `$1$2${MASKED}`],
  [/(https?:\/\/)[^/\s:@]+:[^@\s]+@/g, `$1${MASKED}@`],
  [/-----BEGIN [A-Z ]*KEY-----[\s\S]*?(?:-----END [A-Z ]*KEY-----|$)/g, MASKED],
];

// Masks anything that looks like a key or a token, and the user's name in paths.
function scrub(text) {
  let out = text;
  if (state.key) out = out.split(state.key).join(MASKED);
  for (const [re, by] of SECRET_PATTERNS) out = out.replace(re, by);
  return out.replace(/\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s:'"<>]+/gi, '~').replace(/(?:\/Users|\/var\/home|\/home)\/[^/\s:'"<>]+/g, '~');
}

// ── Frame: Bibo, the message, the screen, the buttons ──────────────────────

function mount() {
  ui.bibo = fromMarkup(BIBO_SVG);
  ui.title = h('p', { class: 'say-title' });
  ui.text = h('p', { class: 'say-text' });
  ui.message = h('div', { class: 'message', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' }, ui.title, ui.text);
  ui.screen = h('main', { class: 'screen' });
  ui.actions = h('footer', { class: 'actions' });
  document.body.replaceChildren(h('div', { class: 'app' }, h('header', { class: 'hero' }, ui.bibo, ui.message), ui.screen, ui.actions));
}

let moodTimer = 0;

function say(mood, title, text = '') {
  clearTimeout(moodTimer);
  setMood(mood);
  rouse();
  // Step messages and errors come from outside: masked like the log.
  ui.title.textContent = nbsp(scrub(title));
  ui.text.textContent = nbsp(scrub(text));
  ui.text.hidden = !text;
  // Joy is short-lived: endless jumping would distract.
  if (mood === 'happy') moodTimer = setTimeout(() => setMood('idle'), HAPPY_MS);
}

function screen(name, { big = false } = {}) {
  view.gen += 1;
  view.timers.forEach(clearTimeout);
  view.timers = [];
  view.rows = {};
  view.log = null;
  view.bar = null;
  view.polling = null;
  view.failure = null;
  view.progress = null;
  view.onLine = null;
  view.asking = null;
  view.asked = null;
  view.onImport = null;
  document.body.dataset.screen = name;
  document.body.classList.toggle('big', big);
  ui.screen.replaceChildren();
  ui.actions.replaceChildren();
  ui.screen.scrollTop = 0;
  return view.gen;
}

function focusMain() {
  const btn = ui.actions.querySelector('[data-act="next"]:not(:disabled)');
  if (btn) btn.focus({ preventScroll: true });
}

// ── Bibo, alive ────────────────────────────────────────────────────────────
// On top of its mood, Bibo blinks, follows the pointer with its eyes, hops when
// the pointer comes by, giggles when clicked, and dozes off when nothing happens
// on a screen that waits for the user. Just for fun: nothing depends on it.

// dev/mock.html shortens it to try it out (&sleep=3).
const SLEEP_MS = Number(window.BIBO_SLEEP_MS) || 45000;
const CALM_SCREENS = ['welcome', 'choice', 'diag', 'done'];
const HOP_GAP_MS = 1500;

const life = {
  mood: 'idle',
  asleep: false,
  active: Date.now(),
  hopAt: 0,
  x: 0,
  y: 0,
  frame: 0,
  sleepTimer: 0,
  blinkTimer: 0,
};

function animateBibo() {
  ui.bibo.addEventListener('pointerenter', onHover);
  ui.bibo.addEventListener('click', onPoke);
  document.addEventListener('pointermove', onPointerMove, { passive: true });
  for (const type of ['pointerdown', 'keydown', 'wheel']) document.addEventListener(type, stir, { capture: true, passive: true });
  // The pointer left the window: eyes back to the middle.
  document.addEventListener('mouseout', (e) => {
    if (!e.relatedTarget) look(0, 0);
  });
  sleepLater(SLEEP_MS);
  blinkLater();
}

function setMood(mood) {
  ui.bibo.classList.replace(life.mood, mood);
  life.mood = mood;
}

function calm() {
  return CALM_SCREENS.includes(document.body.dataset.screen);
}

// A class for a moment: blink, hop, giggle, waking.
function flash(cls, ms) {
  if (ui.bibo.classList.contains(cls)) return;
  ui.bibo.classList.add(cls);
  setTimeout(() => ui.bibo.classList.remove(cls), ms);
}

function blinkLater(ms = 2000 + Math.random() * 4500) {
  clearTimeout(life.blinkTimer);
  life.blinkTimer = setTimeout(() => {
    if (!life.asleep) flash('blink', 130);
    // Now and then, twice in a row.
    blinkLater(Math.random() < 0.2 ? 300 : undefined);
  }, ms);
}

// The user did something: a sleeping Bibo wakes up, the countdown starts over.
function stir() {
  life.active = Date.now();
  if (life.asleep) wake(true);
}

// say() has news: Bibo is up for it, no yawning.
// A yawn under way stops too: its mouth would clash with the new mood's.
function rouse() {
  ui.bibo.classList.remove('waking');
  life.active = Date.now();
  if (life.asleep) wake(false);
}

function sleepLater(ms) {
  clearTimeout(life.sleepTimer);
  life.sleepTimer = setTimeout(doze, ms);
}

// Never while Bibo thinks, works or worries: idle, on a calm screen, only.
function doze() {
  const left = life.active + SLEEP_MS - Date.now();
  if (left > 0) {
    sleepLater(left);
  } else if (life.mood !== 'idle' || !calm()) {
    sleepLater(SLEEP_MS);
  } else {
    // Until stir() or rouse(): no timer meanwhile.
    life.asleep = true;
    ui.bibo.classList.add('asleep');
  }
}

function wake(yawn) {
  life.asleep = false;
  ui.bibo.classList.remove('asleep');
  if (yawn) flash('waking', 1200);
  sleepLater(SLEEP_MS);
}

function onPointerMove(e) {
  // The engine repeats a still pointer's position when the page changes under it: not the user.
  if (e.clientX === life.x && e.clientY === life.y) return;
  stir();
  life.x = e.clientX;
  life.y = e.clientY;
  if (!life.frame) life.frame = requestAnimationFrame(aim);
}

// The pupils lean towards the pointer, all the way once it is two Bibos away.
// All the way means 3.5 × 4 units from the middle of the eye, where a rim of
// white stays inside the outline; at rest they sit 1, 2 units off the middle.
// A mood that looks somewhere (thinking, working, worried) wins in the CSS.
function aim() {
  life.frame = 0;
  const r = ui.bibo.getBoundingClientRect();
  const dx = life.x - (r.left + r.width / 2);
  // The eyes sit at y 102 of 240.
  const dy = life.y - (r.top + r.height * 0.425);
  const d = Math.hypot(dx, dy) || 1;
  const k = Math.min(1, d / (2 * r.width));
  look(k * ((3.5 * dx) / d - 1), k * ((4 * dy) / d - 2));
}

function look(x, y) {
  ui.bibo.style.setProperty('--gx', `${x.toFixed(2)}px`);
  ui.bibo.style.setProperty('--gy', `${y.toFixed(2)}px`);
}

// Happy already jumps, and a worried Bibo has nothing to jump about.
function onHover() {
  const now = Date.now();
  if (life.asleep || now - life.hopAt < HOP_GAP_MS) return;
  if (life.mood === 'happy' || life.mood === 'worried' || ui.bibo.classList.contains('waking')) return;
  life.hopAt = now;
  flash('hop', 950);
}

// On a sleeping Bibo, the click only wakes it up: pointerdown got there first.
function onPoke() {
  if (life.mood === 'worried' || ui.bibo.classList.contains('waking')) return;
  flash('giggle', 800);
}

// ── 1. Welcome ─────────────────────────────────────────────────────────────

function showWelcome() {
  screen('welcome', { big: true });
  if (state.guide) {
    // Back with everything installed: the guide first, the check of the machine still there.
    say('idle', 'Content de te revoir !', 'Ton second cerveau est installé. Je peux aussi te guider pas à pas dans un logiciel : tu me montres ta fenêtre, je te dis où cliquer.');
    const check = state.offer.open ? button('Choisir mon IA', showChoice, { kind: 'secondary', act: 'check' })
      : button(`Vérifier mon ${OS.machine}`, runDiagnosis, { kind: 'secondary', act: 'check' });
    const guide = button('Guide-moi dans un logiciel', () => openGuide(guide), { act: 'next' });
    ui.actions.append(check, guide);
    return;
  }
  if (state.offer.open) {
    // Codex can be chosen here: the AI is not named yet.
    say('idle', 'Salut, moi c’est Bibo !', 'Je vais installer ton second cerveau pour ton IA, Claude ou Codex. 10 à 20 minutes, tu n’as aucune commande à taper.');
    ui.screen.append(
      h('ol', { class: 'plan' },
        h('li', {}, 'Tu choisis ton IA : Claude, Codex ou les deux.'),
        h('li', {}, `Je regarde ce qui manque sur ton ${OS.machine}, je l’installe, puis tu connectes ton compte (2 min).`),
        h('li', {}, 'J’installe le reste tout seul. Si ça coince, ton IA m’aide à réparer.')),
      h('p', { class: 'note' }, `Il te faut un abonnement Claude Pro ou Max, ou ${CHATGPT_PLAN}.`));
    ui.actions.append(button('Choisir mon IA', showChoice, { act: 'next' }));
    return;
  }
  say('idle', 'Salut, moi c’est Bibo !', 'Je vais installer ton second cerveau pour Claude. 10 à 20 minutes, tu n’as aucune commande à taper.');
  ui.screen.append(
    h('ol', { class: 'plan' },
      h('li', {}, `Je regarde ce qui manque sur ton ${OS.machine}.`),
      h('li', {}, 'J’installe Claude, puis tu connectes ton compte (2 min).'),
      h('li', {}, 'J’installe le reste tout seul. Si ça coince, Claude m’aide à réparer.')),
    h('p', { class: 'note' }, 'Il te faut un abonnement Claude Pro ou Max.'));
  // No autofocus here: a focus ring on the very first screen looks like a glitch.
  ui.actions.append(button(`Vérifier mon ${OS.machine}`, runDiagnosis, { act: 'next' }));
}

// ── 1b. Which AI (only where the kit lets Codex be chosen) ────────────────
// Asked before the diagnosis: what it checks and the steps it plans depend on it. The choice holds
// in Rust for the session (choose_agents); the brain step has the installer record it in agents.json.
// « Changer d'IA », on the diagnosis and the accounts' screens, comes back here.

// The account Codex needs, said the same way everywhere: the kit takes a personal plan only (codex.rs).
const CHATGPT_PLAN = 'un abonnement ChatGPT personnel (pas un compte d’entreprise)';

const CHOICES = { claude: ['claude'], codex: ['codex'], both: null };

// 'claude', 'codex' or 'both': the offer's preselection, or the choice made earlier in the session.
function preselected() {
  const agents = state.agents || state.offer.agents;
  if (!Array.isArray(agents) || agents.length === 0) return 'claude';
  return agents.length > 1 ? 'both' : agents[0] === 'codex' ? 'codex' : 'claude';
}

// The preselected answer is the main button; one click chooses and goes on.
function choiceButton(id, label, onClick, pre) {
  const btn = button(label, onClick, { kind: id === pre ? 'primary' : 'secondary', act: id === pre ? 'next' : 'choose' });
  btn.dataset.agent = id;
  return btn;
}

function showChoice() {
  const gen = screen('choice');
  say('idle', 'Avec quelle IA veux-tu travailler ?', 'Ton cerveau marche avec Claude, avec Codex, ou avec les deux.');
  const advice = h('div', { class: 'advice-slot' }, adviceLines(state.advice));
  if (state.advice === undefined) {
    state.advice = null;
    invoke('machine_advice').then((a) => {
      state.advice = a;
      if (gen === view.gen) advice.replaceChildren(...adviceLines(a));
    }).catch(() => {});
  }
  ui.screen.append(
    advice,
    h('ul', { class: 'choices card' },
      h('li', {}, h('p', { class: 'choice-name' }, 'Claude'), h('p', { class: 'note' }, 'Avec ton abonnement Claude (Pro ou Max). Claude Code, l’outil d’Anthropic.')),
      h('li', {}, h('p', { class: 'choice-name' }, 'Codex'), h('p', { class: 'note' }, `Avec ${CHATGPT_PLAN}. Codex, l’outil d’OpenAI : ton résumé de chaque soir est écrit chez OpenAI.`)),
      h('li', {}, h('p', { class: 'choice-name' }, 'Les deux'), h('p', { class: 'note' }, 'L’un écrit le résumé de chaque soir, l’autre prend le relais s’il ne répond pas. Il te faut les deux comptes.'))),
    h('p', { class: 'note' }, 'Tu pourras changer d’avis : rouvre l’app et refais ce choix.'));
  const pre = preselected();
  ui.actions.append(
    choiceButton('claude', 'Claude', () => chooseAgents(CHOICES.claude), pre),
    choiceButton('codex', 'Codex', () => chooseAgents(CHOICES.codex), pre),
    choiceButton('both', 'Les deux', showPreferred, pre));
  focusMain();
}

// Both: which one writes the evening's summary, said both ways (§4.1): each reads the other's sessions.
function showPreferred() {
  screen('choice');
  say('idle', 'Lequel écrit le résumé de chaque soir ?', 'L’autre prend le relais s’il ne répond pas.');
  const why = (x, y) => `Le résumé du soir est écrit par ${x} : il lit aussi tes sessions avec ${y}. Si ${x} ne répond pas, ${y} prend le relais et lit tout.`;
  ui.screen.append(
    h('ul', { class: 'choices card' },
      h('li', {}, h('p', { class: 'choice-name' }, 'Claude'), h('p', { class: 'note' }, why('Claude', 'Codex'))),
      h('li', {}, h('p', { class: 'choice-name' }, 'Codex'), h('p', { class: 'note' }, why('Codex', 'Claude')))));
  const agents = state.agents || state.offer.agents;
  const pre = Array.isArray(agents) && agents.length > 1 && agents[0] === 'codex' ? 'codex' : 'claude';
  ui.actions.append(
    button('Retour', showChoice, { kind: 'ghost', act: 'back' }),
    choiceButton('claude', 'Claude', () => chooseAgents(['claude', 'codex']), pre),
    choiceButton('codex', 'Codex', () => chooseAgents(['codex', 'claude']), pre));
  focusMain();
}

async function chooseAgents(agents) {
  for (const b of ui.actions.querySelectorAll('button')) b.disabled = true;
  try {
    await invoke('choose_agents', { agents });
  } catch (e) {
    say('worried', 'Je n’ai pas pu noter ton choix.', errText(e));
    for (const b of ui.actions.querySelectorAll('button')) b.disabled = false;
    return;
  }
  state.agents = agents;
  runDiagnosis();
}

// The agents of this install, the preferred one first: Claude alone when nothing was asked.
function chosenAgents() {
  return state.agents || ['claude'];
}

function chose(engine) {
  return chosenAgents().includes(engine);
}

// ── 2. Diagnosis ───────────────────────────────────────────────────────────

async function runDiagnosis() {
  const gen = screen('diag');
  say('thinking', `Je regarde ton ${OS.machine}…`, 'Ça prend quelques secondes.');
  ui.screen.append(h('p', { class: 'loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Vérification en cours'));
  let diag;
  try {
    diag = await invoke('diagnose_machine');
  } catch (e) {
    if (gen !== view.gen) return;
    screen('diag');
    say('worried', `Je n’ai pas réussi à examiner ton ${OS.machine}.`, errText(e));
    ui.actions.append(button('Réessayer', runDiagnosis, { act: 'next' }));
    focusMain();
    return;
  }
  if (gen !== view.gen) return;
  if (!diag || typeof diag !== 'object') diag = {};
  diag.checks = Array.isArray(diag.checks) ? diag.checks : [];
  diag.todo = Array.isArray(diag.todo) ? diag.todo : [];
  state.diag = diag;
  showDiagnosis();
}

function sysLine(d) {
  const mac = d.arch === 'arm64' ? 'Puce Apple' : d.arch === 'x86_64' ? 'Intel' : d.arch;
  // On a PC the processor only matters when it is not the usual one.
  const arch = SYSTEM === 'macos' ? mac : (d.arch === 'arm64' ? 'Processeur ARM' : '');
  const parts = [d.os, arch];
  if (d.ram_gb > 0) parts.push(`${gb(d.ram_gb)} Go de mémoire`);
  if (d.disk_free_gb > 0) parts.push(`${gb(d.disk_free_gb)} Go libres`);
  return parts.filter(Boolean).join(' · ');
}

// « Mon conseil » (advice.rs, in the kit's words), and a word on Hermes when it is there.
function adviceLines(a) {
  if (!a || typeof a !== 'object' || !a.text) return [];
  return [h('div', { class: 'advice' },
    h('p', {}, h('strong', {}, 'Mon conseil : '), String(a.text)),
    a.hermes ? h('p', { class: 'note' }, String(a.hermes)) : null)];
}

function checkList(checks) {
  return h('ul', { class: 'checks card' }, checks.map((c) => {
    const status = Object.hasOwn(CHECK_STATUS, c.status) ? c.status : 'warn';
    return h('li', { class: `check is-${status}` },
      icon(status),
      h('span', { class: 'check-label' }, String(c.label), h('span', { class: 'sr-only' }, ` : ${CHECK_STATUS[status]}`)),
      h('span', { class: 'check-detail' }, String(c.detail || '')));
  }));
}

// Back to the choice of the AI, where the kit lets Codex be chosen; nothing otherwise.
function changeButton(small = false) {
  return state.offer.open ? button('Changer d’IA', showChoice, { kind: small ? 'secondary' : 'ghost', small, act: 'change' }) : null;
}

function showDiagnosis() {
  const d = state.diag;
  screen('diag');
  const line = sysLine(d);
  if (line) ui.screen.append(h('p', { class: 'sys' }, line));
  // Where Codex can be chosen, the advice comes with that choice instead.
  if (!state.offer.open) ui.screen.append(...adviceLines(d.advice));
  ui.screen.append(checkList(d.checks));
  const change = changeButton();
  if (change) ui.actions.append(change);

  if (!d.can_install) {
    say('worried', 'Je ne peux pas installer pour l’instant.', 'Règle d’abord ce qui est en rouge, puis clique sur Revérifier.');
    ui.actions.append(button('Revérifier', runDiagnosis, { act: 'next' }));
  } else if (d.todo.length === 0 && canAddMeaning()) {
    say('happy', 'Tout est déjà installé !', 'Ton cerveau cherche par mots-clés : tu peux lui apprendre à chercher par le sens.');
    ui.actions.append(
      button('Activer la recherche par le sens', () => addMeaning(showDiagnosis), { kind: 'secondary', act: 'voyage' }),
      button('Continuer', checkLogin, { act: 'next' }));
  } else if (d.todo.length === 0) {
    say('happy', 'Tout est déjà installé !', `Ton ${OS.machine} a tout ce qu’il faut.`);
    ui.actions.append(button('Continuer', checkLogin, { act: 'next' }));
  } else {
    say('idle', 'J’ai fait le tour.', `Il manque ${listFr(d.todo.map((id) => stepInfo(id).short))}. Je m’en occupe.`);
    ui.actions.append(
      button('Revérifier', runDiagnosis, { kind: 'secondary', act: 'recheck' }),
      button('Tout installer', startInstall, { act: 'next' }));
  }
  focusMain();
}

function startInstall() {
  const d = state.diag;
  state.plan = d.todo;
  state.steps = {};
  state.failed = null;
  if (d.todo.includes('brain') && keywordsOnly()) showVoyage();
  else runInstall(0);
}

function keywordsOnly() {
  const voyage = state.diag.checks.find((c) => c.id === 'voyage');
  return Boolean(voyage) && voyage.status === 'warn';
}

// The brain is there (before the install, or thanks to it) but finds notes by keywords only.
function canAddMeaning() {
  const d = state.diag;
  if (!d || state.keyUsed || !keywordsOnly()) return false;
  return !d.todo.includes('brain') || state.steps.brain === 'done';
}

// state.steps stays as it is: canAddMeaning() still needs to know the brain step went through.
function addMeaning(back) {
  state.plan = ['voyage'];
  state.failed = null;
  showVoyage(back);
}

// ── 3. Voyage key (optional) ───────────────────────────────────────────────

const KEY_VERDICT = {
  ok: ['happy', 'Clé acceptée !', 'Clé valide'],
  invalid: ['worried', 'Cette clé ne marche pas.', 'Clé refusée'],
  limited: ['worried', 'Voyage bride ta clé.', 'Clé bridée'],
  offline: ['worried', 'Je n’arrive pas à joindre Voyage.', 'Pas de réponse'],
};

// `back`: the screen to return to when the brain is already installed and the key
// is all this visit is about. Without it, this screen sits inside the install.
function showVoyage(back = null) {
  const gen = screen('voyage');
  state.key = '';
  const intro = back
    ? () => say('idle', 'Activons la recherche par le sens', 'Avec une clé Voyage AI, ton cerveau retrouve tes notes par le sens, pas seulement par mots-clés.')
    : () => say('idle', 'Une option avant de commencer', 'Ton cerveau retrouve tes notes par mots-clés. Avec une clé Voyage AI, il les retrouve aussi par le sens.');
  intro();
  // Bibo is giving a verdict on a key: editing the field takes it back.
  let verdict = false;

  const input = h('input', {
    type: 'password', id: 'voyage-key', class: 'input', placeholder: 'pa-…', maxlength: 300,
    autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', 'aria-describedby': 'key-msg',
  });
  const msg = h('p', { id: 'key-msg', class: 'key-msg', 'aria-live': 'polite' });
  const verify = button('Vérifier', check, { kind: 'secondary', act: 'verify' });
  const next = button(back ? 'Activer avec cette clé' : 'Continuer avec cette clé', () => leave(true), { act: 'next' });
  next.disabled = true;

  function reset() {
    state.key = '';
    next.disabled = true;
    msg.className = 'key-msg';
    msg.textContent = '';
    if (verdict) {
      verdict = false;
      intro();
    }
  }

  async function check() {
    if (verify.disabled) return;
    const key = input.value.trim();
    reset();
    if (!key) {
      msg.textContent = 'Colle d’abord ta clé dans le champ.';
      input.focus();
      return;
    }
    verify.disabled = true;
    msg.textContent = 'Je vérifie…';
    say('thinking', 'Je vérifie ta clé auprès de Voyage…');
    let r;
    try {
      r = await invoke('check_voyage_key', { key });
    } catch (e) {
      r = { status: 'offline', message: errText(e) };
    }
    if (gen !== view.gen) return;
    verify.disabled = false;
    // The user may have edited the field while we waited: that answer is stale.
    if (input.value.trim() !== key) {
      intro();
      return;
    }
    const status = Object.hasOwn(KEY_VERDICT, r.status) ? r.status : 'invalid';
    const [mood, title, short] = KEY_VERDICT[status];
    verdict = true;
    say(mood, title, String(r.message || ''));
    msg.className = `key-msg is-${status}`;
    msg.textContent = short;
    if (status === 'ok') {
      state.key = key;
      next.disabled = false;
      next.focus();
    }
  }

  function leave(withKey) {
    if (!withKey) state.key = '';
    input.value = '';
    if (withKey || !back) runInstall(0);
    else back();
  }

  async function openDashboard() {
    try {
      await invoke('open_link', { url: VOYAGE_DASHBOARD });
    } catch (e) {
      if (gen !== view.gen) return;
      verdict = true;
      say('worried', errText(e), 'Ouvre toi-même dashboard.voyageai.com dans ton navigateur.');
    }
  }

  input.addEventListener('input', reset);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') check();
  });

  ui.screen.append(
    h('ol', { class: 'vsteps card' },
      h('li', {},
        h('p', {}, 'Crée un compte sur Voyage AI.'),
        button('Ouvrir Voyage AI', openDashboard, { kind: 'secondary', small: true, act: 'open-voyage' })),
      h('li', {},
        h('p', {}, 'Ajoute une carte bancaire (menu Billing). Sans carte, Voyage te bride : ton cerveau serait trop lent.'),
        h('p', { class: 'note' }, 'La part gratuite suffit : un cerveau perso en utilise moins de 1 %. Au-delà, Voyage facture au mois.')),
      h('li', {},
        h('p', {}, 'Crée une clé (menu API Keys). Elle commence par pa-. Colle-la ici.'))),
    h('div', { class: 'keybox card' },
      h('label', { for: 'voyage-key' }, 'Ta clé Voyage'),
      h('div', { class: 'keyrow' }, input, verify),
      msg));
  ui.actions.append(
    button(back ? 'Retour' : 'Passer (recherche par mots-clés)', () => leave(false), { kind: 'ghost', act: 'skip' }),
    next);
  input.focus({ preventScroll: true });
}

// ── 4. Install ─────────────────────────────────────────────────────────────

function stepIcon(s) {
  if (s === 'running') return h('span', { class: 'spinner', 'aria-hidden': 'true' });
  return icon({ pending: 'pending', done: 'ok', failed: 'error' }[s]);
}

function paintStep(id, s, since) {
  state.steps[id] = s;
  const row = view.rows[id];
  if (!row) return;
  row.li.className = `step is-${s}`;
  if (s === 'running') row.li.setAttribute('aria-current', 'step');
  else row.li.removeAttribute('aria-current');
  row.icon.replaceChildren(stepIcon(s));
  const info = stepInfo(id);
  row.what.textContent = nbsp(s === 'running' && info.running ? info.running : info.what);
  row.status.textContent = STEP_STATUS[s];
  row.since = s === 'running' ? since : 0;
}

function paintProgress() {
  if (!view.bar) return;
  const { plan } = state;
  const done = plan.filter((id) => state.steps[id] === 'done').length;
  view.bar.style.width = `${plan.length ? (100 * done) / plan.length : 100}%`;
}

function tickElapsed() {
  for (const row of Object.values(view.rows)) {
    if (!row.since) continue;
    const min = Math.floor((Date.now() - row.since) / 60000);
    row.status.textContent = min < 1 ? STEP_STATUS.running : `En cours · ${min} min`;
  }
}

// `name`: the repair shows its relaunch on this same screen, under another name.
function renderInstall(name = 'install') {
  const gen = screen(name);
  const list = h('ol', { class: 'steps card' });
  for (const id of state.plan) {
    const info = stepInfo(id);
    const row = {
      icon: h('span', { class: 'step-icon' }),
      what: h('span', { class: 'step-what' }),
      status: h('span', { class: 'step-status' }),
      since: 0,
    };
    row.li = h('li', { class: 'step' }, row.icon, h('span', { class: 'step-text' }, h('span', { class: 'step-name' }, info.name), row.what), row.status);
    view.rows[id] = row;
    list.append(row.li);
    paintStep(id, state.steps[id] || 'pending');
  }
  view.bar = h('span', { class: 'bar-fill' });
  ui.screen.append(h('div', { class: 'bar', 'aria-hidden': 'true' }, view.bar), list);
  if (state.plan.some(systemAsks)) ui.screen.append(h('p', { class: 'note' }, OS.asks[1]));
  ui.screen.append(logPanel());
  paintProgress();
  every(tickElapsed, 10000);
  return gen;
}

// `result`: what the last step said, when a repair ran it.
async function runInstall(from, result = '') {
  const gen = renderInstall();
  const { plan } = state;
  for (let i = from; i < plan.length; i++) {
    if (!state.loginAsked && !state.loginSkipped && chose('claude') && claudeInstalled()) {
      await askLoginInline(gen);
      if (gen !== view.gen) return;
    }
    const id = plan[i];
    const info = stepInfo(id);
    const run = stepRun(id);
    paintStep(id, 'running', Date.now());
    // The Apple step's second line is an instruction: no step count in front of it.
    const where = plan.length > 1 && id !== 'clt' ? `Étape ${i + 1} sur ${plan.length}. ` : '';
    say('working', info.say[0], where + info.say[1]);
    // Bibo raises a hand: a window of the system may show up (bibo.js, absent from dev/screens.mjs).
    if (id === 'clt' || systemAsks(id)) window.biboCue?.('demande');

    const key = run === 'brain' && state.key ? state.key : null;
    let r;
    try {
      r = await invoke('run_step', { id: run, voyageKey: key });
    } catch (e) {
      r = { id: run, ok: false, message: `Erreur inattendue : ${errText(e)}` };
    }
    // On failure the key stays in memory for « Réessayer ».
    if (!r || !r.ok) {
      paintStep(id, 'failed');
      state.failed = { id, message: String((r && r.message) || 'Cette étape a échoué.') };
      showFailure(i);
      return;
    }
    if (run === 'brain') state.key = '';
    if (key) state.keyUsed = true;
    delete state.repair.aside[run];
    result = String(r.message || '');
    paintStep(id, 'done');
    paintProgress();
  }
  state.key = '';
  state.failed = null;
  // Adding Voyage ends on what the brain step says: it knows whether Voyage took the key.
  if (plan.includes('voyage')) {
    say('happy', 'C’est fait !', result);
    later(checkLogin, 3000);
  } else {
    say('happy', 'C’est installé !', 'Plus qu’une petite étape.');
    later(checkLogin, 1600);
  }
}

// Claude Code is on the Mac: found by the diagnosis, or installed by this run.
function claudeInstalled() {
  if (state.steps.claude === 'done') return true;
  const c = state.diag && state.diag.checks.find((x) => x.id === 'claude');
  return Boolean(c) && c.status === 'ok';
}

// E1. Connected now, Claude can help repair a step that fails later. Inside the
// install screen, without screen(): the steps stay in view.
async function askLoginInline(gen) {
  state.loginAsked = true;
  say('thinking', 'Je vérifie ton compte Claude…');
  let logged = false;
  try {
    logged = await invoke('claude_logged_in');
  } catch {
    logged = false;
  }
  if (logged || gen !== view.gen) return;

  await new Promise((resolve) => {
    const waiting = h('p', { class: 'loading', hidden: true }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'J’attends ta connexion…');
    const connect = button('Connecter mon compte Claude', open, { act: 'next' });
    const card = h('div', { class: 'login-card card' },
      h('ol', { class: 'plan' },
        h('li', {}, `Clique sur le bouton : une ${OS.window} s’ouvre.`),
        h('li', {}, 'Ton navigateur te demande de te connecter à Claude.'),
        h('li', {}, 'Reviens ici : je continue tout seul.')),
      h('p', { class: 'note' }, 'Tu te connectes directement chez Anthropic : je ne vois jamais ton mot de passe.'),
      waiting);

    // « Plus tard » may come while the Terminal opens: nothing must follow it.
    let over = false;

    function finish() {
      over = true;
      stopPolling();
      card.remove();
      ui.actions.replaceChildren();
      resolve();
    }

    async function open() {
      connect.disabled = true;
      try {
        await invoke('open_claude_login');
      } catch (e) {
        if (over || gen !== view.gen) return;
        say('worried', 'La fenêtre ne s’est pas ouverte.', errText(e));
        connect.disabled = false;
        return;
      }
      if (over || gen !== view.gen) return;
      say('thinking', 'J’attends que tu te connectes…', `Suis les étapes ${OS.inTerminal}.`);
      waiting.hidden = false;
      connect.textContent = 'Rouvrir la fenêtre';
      connect.className = 'btn btn-secondary';
      connect.disabled = false;
      poll(() => {
        say('happy', 'C’est connecté !', 'Claude est prêt à m’aider. Je continue.');
        waiting.hidden = true;
        ui.actions.replaceChildren();
        later(finish, 2000);
      });
    }

    say('idle', 'Connecte ton compte Claude maintenant', 'Comme ça, si une étape coince, Claude pourra m’aider à la réparer.');
    ui.screen.querySelector('.steps').after(card);
    ui.actions.append(button('Plus tard', finish, { kind: 'ghost', act: 'skip' }), connect);
    card.scrollIntoView({ block: 'nearest' });
    focusMain();
  });
}

// ── 4b. Repair with Claude (or Codex) ─────────────────────────────────────
// E2 is the failure under the install screen. One click asks Claude (E3), the app
// shows what it understood (E4), a second click repairs (E5) and the app says how
// it went (E6). Errors on Claude's side are E7. The plan stays in Rust: the window
// only sends its id back. When agents.json lists Codex, Rust names who is asked and
// who answered (`engine`), and the same screens say Codex where they said Claude.

// The words for each AI that may repair. Claude's are the ones the window always had.
const AIS = {
  claude: {
    name: 'Claude', product: 'Claude Code', plan: 'ton abonnement Claude', forfait: 'Ton forfait Claude',
    account: 'Connecte ton compte Claude', why: 'version trop ancienne ou compte non pris en charge',
  },
  codex: {
    name: 'Codex', product: 'Codex', plan: 'ton abonnement ChatGPT personnel', forfait: 'Ton forfait ChatGPT',
    account: 'Connecte Codex à ton compte ChatGPT', why: 'version trop ancienne, réglages ou compte non pris en charge',
  },
};

function ai(engine) {
  return AIS[Object.hasOwn(AIS, engine || '') ? engine : 'claude'];
}

// E2: what the AI can do about the failure, from repair_status. The states missing
// here get the plain screen.
function repairNote(what, a) {
  const notes = {
    ready: `${a.name} peut regarder ce qui s’est passé et me dire comment réparer. Il lit le journal de cette étape, jamais tes clés ni tes fichiers personnels. Ça utilise un peu de ${a.plan}.`,
    not_logged_in: `${a.account} : il pourra m’aider à réparer.`,
    offline: `Pas d’internet : ni moi ni ${a.name} ne pouvons avancer. Vérifie le Wi-Fi ou le VPN, puis réessaie.`,
    // Two analyses for this step, four since the app opened, or a repair undone here.
    exhausted: `${a.name} a déjà fait tous les essais de réparation prévus.`,
    // Too old, logged in without a Pro or Max subscription, paused, or it did not start as required.
    unsupported: `Ton ${a.product} ne peut pas réparer depuis l’app (${a.why}).`,
    disabled: 'Cette version de l’app ne répare pas toute seule.',
    // agents.json says nothing the kit can trust: no AI is asked.
    agents: 'Je ne sais plus avec quelle IA tu travailles : ton choix est illisible. Je ne demande rien, ni à Claude ni à Codex. Copie le rapport et envoie-le à l’équipe.',
    // Codex, before the brain step installed the kit's list of its features.
    not_yet: `${a.name} pourra m’aider une fois ton cerveau installé. Réessaie, ou copie le rapport.`,
  };
  return Object.hasOwn(notes, what || '') ? notes[what] : null;
}
// Where Claude in Terminal (plan B) is offered on the failure screen.
const TERMINAL_STATES = ['exhausted', 'unsupported', 'disabled'];
const TERMINAL_NOTE = `Claude peut t’aider ${OS.inTerminal} : je t’explique comment avant de l’ouvrir.`;

const REPAIR_STAGES = {
  collecting: `Je rassemble ce qu’il lui faut sur ton ${OS.machine}…`,
  asking: 'Claude lit le dossier…',
  asking_claude: 'Claude lit le dossier…',
  asking_codex: 'Codex lit le dossier…',
};

// E7. Nothing was changed in any of these.
function repairError(kind, a) {
  const errors = {
    quota: [`${a.forfait} est à sa limite pour le moment.`, 'Il se recharge tout seul en quelques heures. Rien n’a été modifié.'],
    auth: [`${a.name} doit te reconnecter.`, `${a.account}, puis redemande-lui. Rien n’a été modifié.`],
    timeout: [`${a.name} a mis trop de temps : je l’ai arrêté.`, 'Rien n’a été modifié.'],
    init_invalid: [`${a.name} ne s’est pas lancé comme prévu.`, 'Par prudence, je n’ai rien changé.'],
    invalid_verdict: [`${a.name} n’a pas répondu comme prévu.`, 'Par prudence, je n’ai rien changé.'],
    refused: [`${a.name} n’a pas voulu analyser ce dossier.`, 'Rien n’a été modifié.'],
    api: [`Le service de ${a.name} a eu un souci.`, 'Rien n’a été modifié. Réessaie dans un moment.'],
    busy: ['Une autre étape est déjà en cours.', 'Attends qu’elle finisse, puis réessaie.'],
  };
  return Object.hasOwn(errors, kind || '') ? errors[kind] : null;
}

function copyButton() {
  const copy = button('Copier le rapport', () => copyReport(copy), { kind: 'secondary', act: 'copy' });
  return copy;
}

// The main button steps aside for the one Claude offers.
function demote(btn) {
  btn.className = 'btn btn-secondary';
  btn.dataset.act = 'retry';
}

function clearRepairNotes() {
  for (const el of ui.screen.querySelectorAll('.repair-note')) el.remove();
}

// Above the install log, where the failure is.
function addNote(el) {
  view.log.det.before(el);
}

function terminalButton(index) {
  return button(`Réparer ${OS.inTerminal} avec Claude`, () => showTerminalHelp(index), { kind: 'secondary', act: 'terminal' });
}

// `intro`: [mood, title, text] instead of « Oups », when coming back from a repair.
async function showFailure(index, intro = null) {
  const { id, message } = state.failed;
  const step = stepRun(id);
  if (intro) say(...intro);
  else say('worried', 'Oups, ça a coincé.', message);
  ui.actions.replaceChildren();
  clearRepairNotes();
  // Search by meaning is a bonus: the brain keeps working by keywords without it.
  if (state.plan.includes('voyage')) {
    ui.actions.append(button('Plus tard', () => { state.key = ''; checkLogin(); }, { kind: 'ghost', act: 'skip' }));
  }
  const retry = button('Réessayer', () => runInstall(index), { act: 'next' });
  ui.actions.append(copyButton(), retry);
  focusMain();
  refreshRepairReport();
  undoCard(step);

  // The answer takes a few seconds (network, claude auth status): the buttons work meanwhile.
  const ask = {};
  view.failure = ask;
  let status = null;
  try {
    status = await invoke('repair_status', { step });
  } catch {
    status = null;
  }
  if (view.failure !== ask) return;
  const what = status && status.state;
  // Named only when agents.json lists Codex: without a name, Claude as always.
  if (status && typeof status === 'object') {
    state.repair.engine = ai(status.engine) === AIS.codex ? 'codex' : 'claude';
    state.repair.terminal = status.engine || what === 'agents' ? Boolean(status.terminal_fallback) : true;
  }
  const a = ai(state.repair.engine);
  const note = repairNote(what, a);
  if (!note) return;
  const terminal = TERMINAL_STATES.includes(what) && status.terminal_fallback;
  const offer = h('div', { class: 'repair-offer card repair-note' },
    h('p', {}, note),
    terminal ? h('p', {}, TERMINAL_NOTE) : null,
    what === 'ready' ? sentPanel(step, a) : null);
  addNote(offer);
  // The user may have moved on to another button meanwhile: only the one that steps aside hands its focus over.
  const refocus = document.activeElement === retry || document.activeElement === document.body;
  if (what === 'ready') {
    demote(retry);
    ui.actions.append(button(`Demander à ${a.name} de réparer`, () => runRepair(index), { act: 'next' }));
  } else if (what === 'not_logged_in') {
    demote(retry);
    const connect = connectButton(a, () => connectForRepair(index, connect, a));
    ui.actions.append(connect);
  } else if (terminal) {
    retry.before(terminalButton(index));
  }
  if (refocus) focusMain();
}

// « Voir exactement ce que j'envoie »: the dossier itself, built when opened.
function sentPanel(step, a) {
  const pre = h('pre', { class: 'log-lines', tabindex: '0', 'aria-label': `Ce que j’envoie à ${a.name}` }, 'Je prépare le dossier…');
  const det = h('details', { class: 'log sent' }, h('summary', {}, 'Voir exactement ce que j’envoie'), pre);
  let asked = false;
  det.addEventListener('toggle', async () => {
    if (!det.open || asked) return;
    asked = true;
    let text = '';
    try {
      text = await invoke('repair_preview', { step });
    } catch {
      text = '';
    }
    // Rust masked it already; the window masks what it knows too, like the log.
    pre.textContent = text ? scrub(String(text)) : 'Rien à envoyer : l’étape n’est plus en échec.';
  });
  return det;
}

// Claude's account, or Codex's ChatGPT one (§4.3).
function connectButton(a, onClick) {
  return button(a === AIS.codex ? 'Connecter Codex à mon compte ChatGPT' : 'Connecter mon compte Claude', onClick, { act: 'next' });
}

// Once connected, the failure screen comes back with what Claude (or Codex) can do now.
async function connectForRepair(index, btn, a = AIS.claude) {
  const gen = view.gen;
  const engine = a === AIS.codex ? 'codex' : 'claude';
  btn.disabled = true;
  try {
    await invoke(LOGINS[engine].open);
  } catch (e) {
    if (gen !== view.gen) return;
    say('worried', 'La fenêtre ne s’est pas ouverte.', errText(e));
    btn.disabled = false;
    return;
  }
  if (gen !== view.gen) return;
  say('thinking', 'J’attends que tu te connectes…', `Suis les étapes ${OS.inTerminal}.`);
  btn.textContent = 'Rouvrir la fenêtre';
  btn.className = 'btn btn-secondary';
  btn.disabled = false;
  poll(() => {
    say('happy', 'C’est connecté !', `${a.name} est prêt à m’aider.`);
    btn.remove();
    clearRepairNotes();
    later(() => showFailure(index), 2000);
  }, engine, engine === 'codex' ? () => {
    // Logged in, but not with a personal ChatGPT plan: the failure screen says what Codex can do now.
    btn.remove();
    clearRepairNotes();
    showFailure(index);
  } : null, 'logged_out');
}

// The install screen again, the failed step in red, then E2.
function backToFailure(index, intro = null) {
  renderInstall();
  showFailure(index, intro);
}

// Fetched ahead of « Copier le rapport »: an await before the copy would lose the click.
async function refreshRepairReport() {
  try {
    state.repair.report = String((await invoke('repair_report')) || '');
  } catch {
    // The last one stays.
  }
}

// E3. One click, one analysis: Claude reads, the app waits.
async function runRepair(index) {
  const { id } = state.failed;
  const step = stepRun(id);
  const gen = screen('analyze');
  view.asking = state.repair.engine;
  say('thinking', `${ai(view.asking).name} regarde ce qui s’est passé…`, 'En général moins d’une minute. Il ne modifie rien.');
  const started = Date.now();
  const time = h('span', { class: 'chrono-time' }, '0:00');
  view.progress = h('span', { class: 'chrono-stage' }, REPAIR_STAGES.collecting);
  ui.screen.append(
    h('div', { class: 'repair-card card' },
      h('p', { class: 'card-title' }, 'Ce que je lui montre :'),
      h('ul', { class: 'bullets' },
        h('li', {}, `L’étape « ${stepInfo(id).name} » et son message d’erreur.`),
        h('li', {}, 'Les dernières lignes du journal, clés masquées.'),
        h('li', {}, `L’état de ton ${OS.machine} : versions, place libre, connexion.`))),
    h('div', { class: 'chrono card' },
      h('span', { class: 'spinner', 'aria-hidden': 'true' }),
      h('span', { class: 'chrono-text' }, time, view.progress)));
  every(() => { time.textContent = clock(Date.now() - started); }, 1000);

  const stop = button('Arrêter', async () => {
    stop.disabled = true;
    say('thinking', `J’arrête ${ai(view.asking).name}…`);
    try {
      await invoke('repair_cancel');
    } catch {
      // The analysis ends by itself at its time limit.
    }
  }, { kind: 'secondary', act: 'stop' });
  ui.actions.append(stop);

  let plan = null;
  let err = null;
  try {
    plan = await invoke('repair_analyze', { step });
  } catch (e) {
    err = e;
  }
  if (gen !== view.gen) return;
  refreshRepairReport();
  if (plan && typeof plan === 'object') showProposal(index, plan);
  else showRepairError(index, err);
}

function onRepairProgress(event) {
  const stage = event && event.payload;
  if (!view.progress || !Object.hasOwn(REPAIR_STAGES, stage)) return;
  view.progress.textContent = nbsp(REPAIR_STAGES[stage]);
  // Named when agents.json lists Codex: who reads now. The first one asked answers for an error.
  const engine = { asking_claude: 'claude', asking_codex: 'codex' }[stage];
  if (!engine) return;
  if (!view.asked) view.asked = engine;
  if (engine !== view.asking) {
    view.asking = engine;
    say('thinking', `${ai(engine).name} regarde ce qui s’est passé…`, 'En général moins d’une minute. Il ne modifie rien.');
  }
}

// E7. Most errors go back to E2 with their own words: it offers what is still possible
// (another analysis, or Claude in Terminal once repair.rs rules the in-app one out).
// Asking again right away would only hit the limit again, or the same expired login.
function showRepairError(index, err) {
  const kind = err && typeof err === 'object' ? String(err.kind || '') : '';
  const a = ai(view.asked || state.repair.engine);
  if (kind === 'cancelled') {
    backToFailure(index, ['idle', 'Arrêté.', 'Rien n’a été modifié.']);
    return;
  }
  // The failure screen says why Claude cannot look now.
  if (kind === 'unavailable' || kind === 'no_failure') {
    backToFailure(index);
    return;
  }
  const [title, text] = repairError(kind, a) || [`Je n’ai pas pu demander à ${a.name}.`, `${errText(err)} Rien n’a été modifié.`];
  if (kind !== 'quota' && kind !== 'auth') {
    backToFailure(index, ['worried', title, text]);
    return;
  }
  renderInstall('repair-error');
  say('worried', title, text);
  if (kind === 'quota') window.biboCue?.('dodo');
  const retry = button('Réessayer', () => runInstall(index), { act: 'next' });
  ui.actions.append(copyButton(), retry);
  if (kind === 'auth') {
    demote(retry);
    const connect = connectButton(a, () => connectForRepair(index, connect, a));
    ui.actions.append(connect);
  }
  focusMain();
}

function said(text, a) {
  return h('blockquote', { class: 'said' }, h('p', { class: 'said-who' }, `${a.name} dit :`), h('p', {}, `« ${text} »`));
}

function settingsButton(pane) {
  const gen = view.gen;
  const btn = button(`Ouvrir les ${OS.settings}`, async () => {
    btn.disabled = true;
    try {
      await invoke('open_settings', { pane });
    } catch (e) {
      if (gen === view.gen) say('worried', `Les ${OS.settings} ne se sont pas ouverts.`, errText(e));
    }
    later(() => { btn.disabled = false; }, 2000);
  }, { kind: 'secondary', small: true, act: 'settings' });
  return btn;
}

function planCard(title, items) {
  return h('div', { class: 'repair-card card' }, h('p', { class: 'card-title' }, title), h('ul', { class: 'bullets' }, items));
}

// E4: what Claude understood, in the app's words, and what a click on « Réparer » does.
function showProposal(index, plan) {
  screen('proposal');
  const remedies = Array.isArray(plan.remedies) ? plan.remedies : [];
  const actions = Array.isArray(plan.actions) ? plan.actions : [];
  const cause = String(plan.cause || '');
  const explained = typeof plan.explication === 'string' && plan.explication ? plan.explication : '';
  // Who answered: named when agents.json lists Codex, Claude otherwise.
  const a = ai(plan.engine);

  // E4b. A suspect log: nothing the AI says is shown, nothing is offered.
  if (plan.suspect || (!remedies.length && !actions.length)) {
    say('worried', `${a.name} n’a pas trouvé de réparation sûre.`,
      plan.suspect ? 'Le journal contient des choses inhabituelles : je préfère ne rien réparer tout seul.' : cause);
    if (explained && !plan.suspect) ui.screen.append(said(explained, a));
    ui.screen.append(h('p', { class: 'note' }, `Rien n’a été modifié. Copie le rapport et envoie-le à l’équipe : il contient déjà l’analyse de ${a.name}.`));
    ui.actions.append(copyButton(), button('Réessayer', () => runInstall(index), { act: 'next' }));
    focusMain();
    return;
  }

  if (plan.confiance === 'basse') {
    say('idle', `${a.name} n’est pas sûr de lui.`, `Il pense que : ${lowerFirst(cause)}`);
    window.biboCue?.('doute');
  } else {
    say('idle', `${a.name} a trouvé ce qui coince.`, cause);
  }
  if (explained) ui.screen.append(said(explained, a));
  // The user's part comes first: « Réparer » relaunches the step straight away.
  if (actions.length) {
    ui.screen.append(planCard(remedies.length ? 'Avant de cliquer sur « Réparer » :' : 'Ce que toi tu dois faire :',
      actions.map((a) => h('li', { class: a.pane ? 'with-btn' : null },
        h('span', {}, String(a.text)),
        a.pane ? settingsButton(String(a.pane)) : null))));
  }
  if (remedies.length) {
    const card = planCard('Ce que je vais faire :', remedies.map((r) => h('li', {}, String(r.text))));
    if (remedies.some((r) => String(r.id).startsWith('mettre_de_cote_'))) {
      card.append(h('p', { class: 'note' }, 'Rien n’est effacé : je le range dans un dossier à part. Si ça ne marche pas, tu pourras le remettre comme avant.'));
    }
    ui.screen.append(card);
  }
  ui.actions.append(
    button('Annuler', () => backToFailure(index), { kind: 'ghost', act: 'cancel' }),
    copyButton(),
    button(remedies.length ? 'Réparer' : 'C’est fait, relance l’étape', () => applyRepair(index, plan), { act: 'next' }));
  focusMain();
}

// E5: the install screen, the step running again.
async function applyRepair(index, plan) {
  const { id } = state.failed;
  const info = stepInfo(id);
  const run = stepRun(id);
  const gen = renderInstall('repairing');
  paintStep(id, 'running', Date.now());
  const relaunch = () => say('working', `Je relance l’étape « ${info.name} »…`, info.say[1]);
  if ((plan.remedies || []).length) {
    say('working', 'Je répare…', 'Ensuite je relance l’étape.');
    // The remedies log « → … » (and « ✗ … » when one fails): anything else is the step again.
    view.onLine = (line) => {
      if (/^[→✗]/.test(line)) return;
      view.onLine = null;
      relaunch();
    };
  } else {
    relaunch();
  }

  const key = run === 'brain' && state.key ? state.key : null;
  let outcome;
  try {
    outcome = await invoke('repair_apply', { planId: String(plan.plan_id || ''), voyageKey: key });
  } catch (e) {
    outcome = { outcome: 'refused', message: `Erreur inattendue : ${errText(e)}` };
  }
  if (gen !== view.gen) return;
  view.onLine = null;
  refreshRepairReport();
  showOutcome(index, plan, outcome && typeof outcome === 'object' ? outcome : {}, key);
}

// E6: the app's own checks decide, never Claude.
function showOutcome(index, plan, o, key) {
  const { id } = state.failed;
  const run = stepRun(id);
  const kind = String(o.outcome || 'refused');
  const aside = (state.repair.aside[run] || []).filter((p) => p !== plan.plan_id);
  if (o.undoable) aside.push(plan.plan_id);
  state.repair.aside[run] = aside;

  if (kind === 'fixed') {
    paintStep(id, 'done');
    paintProgress();
    if (run === 'brain') {
      const withoutKey = (plan.remedies || []).some((r) => r.id === 'relancer_sans_cle_voyage');
      if (key && !withoutKey) state.keyUsed = true;
      state.key = '';
    }
    delete state.repair.aside[run];
    state.failed = null;
    const cause = String(o.cause || '');
    say('happy', 'Réparé !', cause ? `C’était : ${lowerFirst(cause)} Je continue l’installation.` : 'Je continue l’installation.');
    later(() => runInstall(index + 1, String(o.message || '')), FIXED_PAUSE_MS);
    return;
  }

  paintStep(id, 'failed');
  if (kind === 'refused') {
    showFailure(index, ['worried', 'Je n’ai rien fait.', String(o.message || '')]);
    return;
  }
  // The step ran again: its new sentence is the failure now.
  if (kind !== 'remedy_failed' && o.message) state.failed.message = String(o.message);
  const retry = button('Réessayer', () => runInstall(index), { act: 'next' });

  if (kind === 'guard_restored' || kind === 'regression') {
    // E6d: undone, and Claude is not asked again for this step. The guard's sentence
    // may say where a copy of the user's files is; a regression's only lists checks.
    if (kind === 'guard_restored') {
      const message = String(o.message || '');
      say('worried', 'J’ai arrêté la réparation.', /rapport/.test(message) ? message : `${message} Copie le rapport pour l’équipe.`);
    } else if (o.undoable) {
      say('worried', 'Je n’ai pas pu tout remettre comme avant.', 'La réparation abîmait autre chose. Clique sur « Remettre comme avant », puis copie le rapport pour l’équipe.');
    } else {
      say('worried', 'J’ai tout remis comme avant.', 'La réparation abîmait autre chose, alors je l’ai annulée. Copie le rapport pour l’équipe.');
    }
    undoCard(run);
    ui.actions.append(copyButton(), retry);
  } else if (o.retry) {
    // E6b: one more round, asked as the last one was offered.
    const next = ai(state.repair.engine);
    const failed = kind === 'remedy_failed';
    say('worried', failed ? 'Je n’ai pas pu réparer.' : 'Ça n’a toujours pas marché.',
      `${failed ? `${String(o.message || '')} ` : ''}${next.name} peut essayer une autre piste. C’est son dernier essai sur cette étape.`);
    undoCard(run);
    ui.actions.append(copyButton(), button(`Redemander à ${next.name} (dernier essai)`, () => runRepair(index), { act: 'next' }));
  } else {
    // E6c. Claude in Terminal only for whom chose Claude.
    say('worried', 'Je n’ai pas réussi à réparer tout seul.', `Rien n’est cassé. Copie le rapport et envoie-le à l’équipe : il contient déjà l’analyse de ${ai(plan.engine).name}.`);
    undoCard(run);
    ui.actions.append(copyButton(), ...(state.repair.terminal ? [terminalButton(index)] : []), retry);
  }
  focusMain();
}

// « Remettre comme avant », while something of this step is set aside.
function undoCard(step) {
  const plans = state.repair.aside[step] || [];
  if (!plans.length || !view.log) return;
  const btn = button('Remettre comme avant', () => undoRepair(step, btn, card), { kind: 'secondary', small: true, act: 'undo' });
  const card = h('div', { class: 'undo card repair-note' },
    h('p', {}, 'J’ai mis de côté une installation abîmée, sans rien effacer. Tu peux la remettre comme avant.'),
    btn);
  addNote(card);
}

// Latest plan first: each one put back what it moved.
async function undoRepair(step, btn, card) {
  const gen = view.gen;
  btn.disabled = true;
  let answer = '';
  let ok = true;
  for (const planId of [...(state.repair.aside[step] || [])].reverse()) {
    try {
      answer = String(await invoke('repair_undo', { planId }));
      state.repair.aside[step] = state.repair.aside[step].filter((p) => p !== planId);
    } catch (e) {
      answer = errText(e);
      ok = false;
      break;
    }
  }
  refreshRepairReport();
  if (gen !== view.gen) return;
  if (ok) {
    card.remove();
    // Rust's sentence says what came back where.
    say('idle', 'C’est fait.', answer);
  } else {
    btn.disabled = false;
    say('worried', 'Je n’ai pas pu tout remettre.', answer);
  }
}

// Plan B: Claude in Terminal, interactive. Said first, in plain words, then opened.
function showTerminalHelp(index) {
  const { id } = state.failed;
  const step = stepRun(id);
  const gen = screen('terminal');
  say('idle', `Claude peut t’aider ${OS.inTerminal}`, 'Avant de l’ouvrir, voici comment ça va se passer.');
  // Claude Code's own questions are in English: a French non-technician needs the two words.
  ui.screen.append(
    h('ol', { class: 'plan' },
      h('li', {}, `Une ${OS.window} s’ouvre, avec Claude dedans.`),
      h('li', {}, 'Claude lit ce qui s’est passé, t’explique le problème avec des mots simples et te propose des étapes.'),
      h('li', {}, 'Avant chaque commande, il te demande ton accord. Lis ce qu’il propose, puis réponds. Tu peux toujours dire non.'),
      h('li', {}, 'Suis ses étapes. Quand il a fini, reviens ici et clique sur « Vérifier ».')),
    h('p', { class: 'note' }, 'Ses questions s’affichent en anglais : « Yes » veut dire oui, « No » veut dire non. Choisis avec les flèches du clavier, puis appuie sur Entrée. Évite « Yes, and don’t ask again… » : il ne te demanderait plus ton accord.'),
    h('p', { class: 'note' }, 'Ça utilise ton abonnement Claude. Ne lui donne jamais de mot de passe ni de clé.'));
  const back = button('Retour', () => backToFailure(index), { kind: 'ghost', act: 'back' });
  const open = button(`Ouvrir ${OS.terminal} avec Claude`, () => start(open), { act: 'next' });
  ui.actions.append(back, open);
  focusMain();

  // A double click would open two Terminal windows: the button rests meanwhile.
  async function start(btn) {
    btn.disabled = true;
    try {
      await invoke('open_repair_terminal', { step });
    } catch (e) {
      if (gen !== view.gen) return;
      // Refused too when the log looks written to steer Claude: then the team should see the report.
      say('worried', `Je n’ai pas ouvert ${OS.terminal}.`, errText(e));
      if (!ui.actions.querySelector('[data-act="copy"]')) btn.before(copyButton());
      btn.disabled = false;
      return;
    }
    if (gen !== view.gen) return;
    say('idle', `Claude t’attend ${OS.inTerminal}`, 'Suis ses étapes. Quand il a fini, reviens ici et clique sur « Vérifier ».');
    const again = button(`Rouvrir ${OS.terminal}`, () => start(again), { kind: 'secondary', act: 'reopen' });
    ui.actions.replaceChildren(back, again, button('Vérifier', () => runInstall(index), { act: 'next' }));
    later(() => { again.disabled = false; }, 3000);
    again.disabled = true;
    focusMain();
  }
}

function logPanel() {
  const pre = h('pre', { class: 'log-lines', tabindex: '0', 'aria-label': 'Journal de l’installation' });
  const det = h('details', { class: 'log' }, h('summary', {}, 'Détails'), pre);
  det.addEventListener('toggle', () => {
    paintLog(true);
    if (det.open) pre.scrollIntoView({ block: 'nearest' });
  });
  view.log = { det, pre };
  return det;
}

function paintLog(force) {
  const log = view.log;
  if (!log || !log.det.open) return;
  const { pre } = log;
  const atBottom = force || pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
  pre.textContent = state.logs.length ? state.logs.map((l) => l.line).join('\n') : 'Rien pour l’instant.';
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

let logQueued = false;

// On Windows, install.ps1's sections arrive as « ▸ … » lines (steps.rs): the running
// row says the latest. A Mac's installers print nothing of the kind.
function showSection(step, line) {
  if (!WINDOWS || !line.startsWith('▸ ')) return;
  for (const [id, row] of Object.entries(view.rows)) {
    if (stepRun(id) === step && state.steps[id] === 'running') row.what.textContent = nbsp(line.slice(2));
  }
}

function onLog(event) {
  const p = event && event.payload;
  if (!p) return;
  const line = scrub(String(p.line ?? ''));
  const step = String(p.step ?? '');
  state.logs.push({ step, line });
  showSection(step, line);
  if (view.onLine) view.onLine(line);
  if (state.logs.length > MAX_LOG_LINES) state.logs.splice(0, state.logs.length - MAX_LOG_LINES);
  if (logQueued) return;
  logQueued = true;
  requestAnimationFrame(() => {
    logQueued = false;
    paintLog(false);
  });
}

function buildReport() {
  const d = state.diag;
  const out = ['Brain in a Box : rapport d’installation', `Date : ${new Date().toISOString()}`];
  if (d) {
    out.push(`${OS.machine} : ${d.os} (${d.arch}), ${gb(d.ram_gb)} Go de mémoire, ${gb(d.disk_free_gb)} Go libres, en ligne : ${d.online ? 'oui' : 'non'}`);
    out.push('', 'Diagnostic :');
    for (const c of d.checks) out.push(`- [${c.status}] ${c.label} : ${c.detail}`);
    const steps = state.plan.map((id) => `${id} ${state.steps[id] || 'pending'}`);
    if (steps.length) out.push('', `Étapes : ${steps.join(', ')}`);
  }
  if (state.failed) out.push('', `Échec à l’étape « ${state.failed.id} » : ${state.failed.message}`);
  if (state.repair.report) out.push('', state.repair.report);
  const tail = state.logs.slice(-REPORT_LOG_LINES);
  out.push('', `Journal (${tail.length} dernières lignes) :`);
  for (const l of tail) out.push(`[${l.step}] ${l.line}`);
  return scrub(out.join('\n'));
}

async function copyReport(btn) {
  const text = buildReport();
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch {
    copied = legacyCopy(text);
  }
  if (copied) {
    btn.textContent = 'Rapport copié';
    later(() => { btn.textContent = 'Copier le rapport'; }, 2500);
    return;
  }
  // Last resort: show it, selected, for Cmd+C (Ctrl+C). A second click reuses the same box.
  let area = ui.screen.querySelector('textarea.report');
  if (!area) {
    area = h('textarea', { class: 'report', readonly: true, 'aria-label': 'Rapport d’installation' });
    ui.screen.append(area);
  }
  area.value = text;
  area.focus();
  area.select();
}

function legacyCopy(text) {
  const area = h('textarea', { class: 'sr-only', readonly: true });
  area.value = text;
  document.body.append(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

// ── 5. Accounts ───────────────────────────────────────────────────────────
// Each agent chosen, the preferred one first: Claude's account, then (or instead) Codex's ChatGPT one.
// With Claude alone, the screens of before Codex.

const LOGINS = {
  claude: { check: 'claude_logged_in', open: 'open_claude_login', checking: 'Je vérifie ton compte Claude…' },
  codex: { check: 'codex_login', open: 'open_codex_login', checking: 'Je vérifie la connexion de Codex…' },
};

// Claude: true or false. Codex: its state (codex_login), "chatgpt" when logged in with a ChatGPT
// account, the only login the kit uses.
async function loginState(engine) {
  let said;
  try {
    said = await invoke(LOGINS[engine].check);
  } catch {
    said = engine === 'codex' ? 'unknown' : false;
  }
  if (engine === 'codex') state.codexLogin = said;
  return said;
}

// Codex logged in, but in a way the kit does not use (§4.3): waiting for its login would never end.
const CODEX_NOT_USED = ['billed', 'managed', 'system'];

function loggedIn(engine, said) {
  return engine === 'codex' ? said === 'chatgpt' : Boolean(said);
}

function skippedLogin(engine) {
  return engine === 'codex' ? state.codexLoginSkipped : state.loginSkipped;
}

async function checkLogin() {
  const gen = screen('login');
  const left = [];
  for (const engine of chosenAgents()) {
    say('thinking', LOGINS[engine].checking);
    if (!ui.screen.querySelector('.loading')) {
      ui.screen.append(h('p', { class: 'loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Un instant'));
    }
    const said = await loginState(engine);
    if (gen !== view.gen) return;
    // « Plus tard » holds for the whole session: coming back from the Voyage screen
    // must not ask again.
    if (loggedIn(engine, said)) {
      if (engine === 'claude') state.loginSkipped = false;
      else state.codexLoginSkipped = false;
    } else if (!skippedLogin(engine)) {
      left.push({ engine, said });
    }
  }
  nextLogin(left);
}

// The first account still to connect, then the next one, then the end.
function nextLogin(left) {
  if (!left.length) showDone();
  else showLogin(left[0].engine, left.slice(1), left[0].said);
}

function showLogin(engine = 'claude', rest = [], said = '') {
  const gen = screen('login');
  // Another account to connect after this one: not the last step yet.
  const last = !rest.length;
  const waiting = h('p', { class: 'loading', hidden: true }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'J’attends ta connexion…');
  const codex = engine === 'codex';
  const connect = connectButton(codex ? AIS.codex : AIS.claude, open);
  const next = () => nextLogin(rest);

  async function open() {
    connect.disabled = true;
    try {
      await invoke(LOGINS[engine].open);
    } catch (e) {
      if (gen !== view.gen) return;
      say('worried', 'La fenêtre ne s’est pas ouverte.', errText(e));
      connect.disabled = false;
      return;
    }
    if (gen !== view.gen) return;
    say('thinking', 'J’attends que tu te connectes…', `Suis les étapes ${OS.inTerminal}.`);
    waiting.hidden = false;
    connect.textContent = 'Rouvrir la fenêtre';
    connect.className = 'btn btn-secondary';
    connect.disabled = false;
    poll(next, engine, codex ? (now) => showLogin(engine, rest, now) : null, said);
  }

  if (!codex) {
    say('idle', last ? 'Dernière étape : ton compte Claude' : 'Encore une étape : ton compte Claude', 'Connecte-toi pour que Claude puisse travailler avec ton cerveau.');
    ui.screen.append(
      h('ol', { class: 'plan' },
        h('li', {}, `Clique sur le bouton : une ${OS.window} s’ouvre.`),
        h('li', {}, 'Suis ses étapes. Ton navigateur te demande de te connecter à Claude.'),
        h('li', {}, 'Reviens ici : je vois tout seul quand c’est bon.')),
      h('p', { class: 'note' }, 'Tu te connectes directement chez Anthropic : je ne vois jamais ton mot de passe. Il faut un abonnement Claude Pro ou Max.'),
      waiting);
    switchAi('Pas d’abonnement Claude Pro ou Max ? Change d’IA.');
  } else if (said === 'system') {
    // No login fixes settings of this system another account may change (codex.rs, Windows).
    say('worried', `Codex suit des réglages de ce ${OS.machine} qu’un autre compte peut modifier.`,
      'Le dossier ProgramData\\OpenAI\\Codex n’est pas protégé : je ne me sers pas de Codex tant que c’est le cas. La personne qui gère ce PC peut le protéger.');
    switchAi('Pour avancer sans Codex, change d’IA.');
  } else {
    // Bibo never logs anyone out (§4.3): an API key or a workspace account stays, and is not used.
    // Codex's login rewrites auth.json with the ChatGPT account: the key goes without a logout.
    if (said === 'billed') {
      say('worried', 'Codex est connecté avec une clé API.', 'Elle serait facturée : je ne m’en sers pas. Clique sur le bouton et connecte-toi avec ChatGPT : ta connexion remplacera la clé.');
    } else if (said === 'managed') {
      say('worried', 'Codex est connecté avec un compte ChatGPT d’entreprise.', 'Je ne m’en sers pas : il faut ton abonnement ChatGPT personnel. Reconnecte-toi en choisissant ton espace personnel.');
    } else {
      say('idle', last ? 'Dernière étape : ton compte ChatGPT' : 'Encore une étape : ton compte ChatGPT', 'Connecte Codex à ton compte ChatGPT pour qu’il puisse travailler avec ton cerveau.');
    }
    ui.screen.append(
      h('ol', { class: 'plan' },
        h('li', {}, `Clique sur le bouton : une ${OS.window} s’ouvre.`),
        h('li', {}, 'Suis ses étapes. Ton navigateur te demande de te connecter à ChatGPT.'),
        h('li', {}, 'Reviens ici : je vois tout seul quand c’est bon.')),
      h('p', { class: 'note' }, `Tu te connectes directement chez OpenAI : je ne vois jamais ton mot de passe. Il faut ${CHATGPT_PLAN}. Jamais de clé API : elle serait facturée.`),
      waiting);
    switchAi('Pas d’abonnement ChatGPT personnel ? Change d’IA.');
  }
  ui.actions.append(
    button('Plus tard', () => {
      if (codex) state.codexLoginSkipped = true;
      else state.loginSkipped = true;
      next();
    }, { kind: 'ghost', act: 'skip' }));
  if (said !== 'system') ui.actions.append(connect);
  focusMain();
}

// On an account's screen, where the kit lets Codex be chosen: another AI, for whoever has no such account.
function switchAi(sentence) {
  const change = changeButton(true);
  if (change) ui.screen.append(h('div', { class: 'upgrade' }, h('p', { class: 'note' }, sentence), change));
}

// One polling loop at a time, until the login, stopPolling() or the next screen. With `onOther`,
// Codex's login turning into one the kit does not use (from `from`) ends the wait too.
function poll(onLogged, engine = 'claude', onOther = null, from = '') {
  if (view.polling) return;
  const gen = view.gen;
  const loop = {};
  view.polling = loop;
  const tick = async () => {
    const said = await loginState(engine);
    const logged = loggedIn(engine, said);
    if (gen !== view.gen || view.polling !== loop) return;
    if (!logged && onOther && said !== from && CODEX_NOT_USED.includes(said)) {
      view.polling = null;
      onOther(said);
      return;
    }
    if (!logged) {
      later(tick, LOGIN_POLL_MS);
      return;
    }
    view.polling = null;
    onLogged();
  };
  later(tick, LOGIN_POLL_MS);
}

function stopPolling() {
  view.polling = null;
}

// ── 6. Done ────────────────────────────────────────────────────────────────

function showDone() {
  const gen = screen('done', { big: true });
  const names = chosenAgents().map((e) => ai(e).name);
  const first = names[0];
  say('happy', 'Ton second cerveau est prêt !', names.length > 1
    ? `${listFr(names)} retiennent maintenant ce que tu leur apprends.`
    : `${first} retient maintenant ce que tu lui apprends.`);
  // In the screen rather than with the buttons: three of them would not fit on one
  // line. Above « Dernière chose », which leads to the main button.
  if (canAddMeaning()) {
    ui.screen.append(h('div', { class: 'upgrade' },
      h('p', { class: 'note' }, 'Ton cerveau retrouve tes notes par mots-clés. Il peut aussi les retrouver par le sens.'),
      button('Activer la recherche par le sens', () => addMeaning(showDone), { kind: 'secondary', small: true, act: 'voyage' })));
  }
  ui.screen.append(h('div', { class: 'upgrade' },
    h('p', { class: 'note' }, 'Tu utilisais ChatGPT ou Claude ? Apporte ce qu’ils savent de toi.'),
    button('Importer ma mémoire', showImport, { kind: 'secondary', small: true, act: 'import' })));
  // The guide works with Claude (docs/proposals/guide.md): Codex is not shown images yet.
  if (chose('claude')) {
    const guide = button('Essayer le guide', () => openGuide(guide), { kind: 'secondary', small: true, act: 'guide' });
    ui.screen.append(h('div', { class: 'upgrade' },
      h('p', { class: 'note' }, 'Nouveau : je peux te guider pas à pas dans n’importe quel logiciel.'), guide));
  }
  // The preferred one does the onboarding. Codex only on the login the kit uses (§4.3): logged in
  // another way it would bill a key, or offer one on its first screen, so its login comes first.
  const codexOff = chose('codex') && state.codexLogin !== 'chatgpt';
  const loginFirst = codexOff && chosenAgents()[0] === 'codex';
  const who = names.length > 1 ? first : 'lui';
  ui.screen.append(h('p', { class: 'note' }, loginFirst
    ? `Dernière chose : connecte Codex à ton compte ChatGPT, puis présente-toi à ${who}. Il te pose quelques questions pour te connaître.`
    : `Dernière chose : présente-toi à ${who}. Il te pose quelques questions pour te connaître.`));
  if (state.loginSkipped) {
    ui.screen.append(h('p', { class: 'note' }, 'Pense à connecter ton compte Claude : il te le demandera au premier lancement.'));
  }
  if (codexOff) ui.screen.append(h('p', { class: 'note' }, codexLoginNote(state.codexLogin)));

  // A double click would open two Terminal windows, or Obsidian twice: the
  // button rests a few seconds.
  async function act(btn, cmd, title, text) {
    btn.disabled = true;
    try {
      await invoke(cmd);
      if (gen === view.gen) say('happy', title, text);
    } catch (e) {
      if (gen === view.gen) say('worried', 'Ça n’a pas marché.', errText(e));
    }
    later(() => { btn.disabled = false; }, 3000);
  }

  const vault = button('Ouvrir mon cerveau dans Obsidian', () => act(vault, 'open_vault', 'J’ouvre Obsidian…', 'Ton cerveau est dans Documents › Brain.'), { kind: 'secondary', act: 'vault' });
  ui.actions.append(vault);
  if (loginFirst) {
    // Settings of the system no login fixes: nothing more to offer here.
    if (state.codexLogin !== 'system') {
      const connect = connectButton(AIS.codex, () => connectThenDone(connect));
      ui.actions.append(connect);
    }
  } else {
    const onboard = button('Personnaliser mon cerveau (15 min)', () => act(onboard, 'start_onboarding', 'C’est parti !', `Une ${OS.window} s’ouvre : réponds aux questions de ${first}.`), { act: 'next' });
    ui.actions.append(onboard);
  }
  focusMain();
}

// The guide's own window, above the program it guides in; this one goes to the Dock or the taskbar.
async function openGuide(btn) {
  const gen = view.gen;
  btn.disabled = true;
  try {
    await invoke('guide_open');
  } catch (e) {
    if (gen === view.gen) say('worried', 'Le guide ne s’est pas ouvert.', errText(e));
  }
  later(() => { btn.disabled = false; }, 3000);
}

// Why Codex is not used yet, under the end's message.
function codexLoginNote(said) {
  if (said === 'billed') return 'Codex est encore connecté avec une clé API, qui serait facturée : je ne m’en sers pas. Connecte-le à ton compte ChatGPT : ta connexion remplacera la clé.';
  if (said === 'managed') return 'Codex est connecté avec un compte ChatGPT d’entreprise : je ne m’en sers pas. Connecte-le avec ton abonnement ChatGPT personnel.';
  if (said === 'system') return `Codex suit des réglages de ce ${OS.machine} qu’un autre compte peut modifier : je ne m’en sers pas tant que ce n’est pas réglé.`;
  return 'Pense à connecter Codex à ton compte ChatGPT. S’il te le demande au premier lancement, choisis « Sign in with ChatGPT », jamais une clé API : elle serait facturée.';
}

// Codex's login from the end: once it is the one the kit uses (or another one), the end again.
async function connectThenDone(btn) {
  const gen = view.gen;
  btn.disabled = true;
  try {
    await invoke(LOGINS.codex.open);
  } catch (e) {
    if (gen !== view.gen) return;
    say('worried', 'La fenêtre ne s’est pas ouverte.', errText(e));
    btn.disabled = false;
    return;
  }
  if (gen !== view.gen) return;
  say('thinking', 'J’attends que tu te connectes…', `Suis les étapes ${OS.inTerminal}.`);
  btn.textContent = 'Rouvrir la fenêtre';
  btn.className = 'btn btn-secondary';
  btn.disabled = false;
  poll(showDone, 'codex', showDone, state.codexLogin);
}

// ── 7. Importer ma mémoire ────────────────────────────────────────────────
// What ChatGPT or Claude knows of the user, from their export or from the answer to a sentence pasted
// there, sorted by the brain's AI without any tool into one new note (import.rs runs the kit's
// importer-memoire). The window gives no path: the export comes from the system's picker, opened by
// Rust. The importer says what it read and what would leave before anything does; the agreement codes
// stay in Rust, the window only says how many requests.

const MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
const SIZES = { rapide: 'Rapide', normal: 'Normal', complet: 'Complet', choisi: 'Sur mesure' };
const CATEGORIES = [['identite', 'Qui tu es'], ['preference', 'Tes préférences'], ['projet', 'Tes projets'], ['personne', 'Les personnes'], ['outil', 'Tes outils'], ['decision', 'Tes décisions']];
const CHATGPT_URL = 'https://chatgpt.com';
const CLAUDE_URL = 'https://claude.ai';

// "2026-03-02" → "2 mars 2026".
function dayFr(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  if (!m || Number(m[2]) < 1 || Number(m[2]) > 12) return String(iso || '');
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

function count(n, one, many = `${one}s`) {
  const k = Number(n) || 0;
  return `${k} ${k > 1 ? many : one}`;
}

// The AI that sorts: the brain's preferred one (the importer names it again in its plan).
function importAi(plan = null) {
  const id = plan && ['claude', 'codex'].includes(plan.ia) ? plan.ia : (state.agents || state.offer.agents || ['claude'])[0];
  return ai(id);
}

// Where the AI sorts what leaves: not on this computer.
function importAiHome(a) {
  return a === AIS.codex ? 'OpenAI' : 'Anthropic';
}

function privacyNote(a) {
  return h('p', { class: 'note' }, `Ce que tu importes est lu sur ton ${OS.machine} et ses secrets masqués. Puis ${a.name} le trie, chez ${importAiHome(a)}, avec ${a.plan} : rien ne part ailleurs. Le résultat est une nouvelle note de ton cerveau, que tu relis.`);
}

// A browser that opens the .zip by itself (Safari does) leaves a folder: zipped again, it is read.
const FOLDER_TIP = {
  macos: 'Tu as un dossier au lieu d’un .zip (Safari l’ouvre tout seul) ? Clic droit sur le dossier › Compresser, puis choisis ce .zip.',
  // Its label changes with Windows 11's builds (« Compresser dans le fichier ZIP », « Compresser vers › Fichier ZIP »).
  windows: 'Tu as un dossier au lieu d’un .zip ? Clic droit sur le dossier, compresse-le en fichier ZIP, puis choisis ce .zip.',
  linux: 'Tu as un dossier au lieu d’un .zip ? Clic droit sur le dossier › Compresser, puis choisis ce .zip.',
}[SYSTEM];
const SEVERAL = SYSTEM === 'macos' ? 'Cmd-clic' : 'Ctrl-clic';

function exportHelp() {
  return h('details', { class: 'log help' },
    h('summary', {}, 'Comment obtenir mon export ?'),
    h('ul', { class: 'bullets' },
      h('li', {}, 'ChatGPT : Paramètres › Contrôles de données › Exporter les données. Un e-mail t’envoie un lien, valable 24 h : télécharge le fichier .zip.'),
      h('li', {}, `Claude : Paramètres › Confidentialité › Exporter les données, sur le site ou l’app de bureau. Un e-mail t’envoie tes fichiers .zip : choisis-les tous d’un coup (${SEVERAL} pour en prendre plusieurs).`),
      h('li', {}, FOLDER_TIP),
      h('li', {}, 'Pour les souvenirs de ChatGPT, le copier-coller marche mieux : son export ne les contient souvent plus.')));
}

async function showImport() {
  const gen = screen('import');
  say('thinking', 'Je prépare l’import…');
  try {
    await invoke('import_check');
  } catch (e) {
    if (gen === view.gen) showImportError(e);
    return;
  }
  if (gen === view.gen) showImportChoice();
}

function showImportChoice(intro = null) {
  screen('import');
  const a = importAi();
  if (intro) say(...intro);
  else say('idle', 'D’où vient ta mémoire ?', `ChatGPT ou Claude savent déjà des choses sur toi. ${a.name} en tire l’essentiel, je le range dans ton cerveau.`);
  ui.screen.append(
    h('ul', { class: 'choices card' },
      h('li', {}, h('p', { class: 'choice-name' }, 'Ton fichier d’export (.zip)'), h('p', { class: 'note' }, 'ChatGPT et Claude t’envoient tes conversations dans un fichier. Le plus complet.')),
      h('li', {}, h('p', { class: 'choice-name' }, 'Un copier-coller (2 min)'), h('p', { class: 'note' }, 'Tu demandes à ChatGPT ou à Claude ce qu’il sait de toi, et tu colles sa réponse ici.'))),
    exportHelp(),
    privacyNote(a));
  ui.actions.append(
    button('Retour', showDone, { kind: 'ghost', act: 'back' }),
    button('Je copie-colle une réponse', () => showPaste(), { kind: 'secondary', act: 'paste' }),
    button('Choisir mon fichier d’export', pickExport, { act: 'next' }));
  focusMain();
}

function onImportProgress(event) {
  const p = event && event.payload;
  if (p && typeof p === 'object' && view.onImport) view.onImport(p);
}

// The picker, then the reading: the same screen, Bibo says which.
async function pickExport() {
  const gen = screen('import-read');
  say('idle', 'Choisis ton fichier d’export', `Dans la fenêtre qui s’ouvre. Pour Claude, choisis tous ses fichiers .zip d’un coup (${SEVERAL} pour en prendre plusieurs).`);
  const bar = h('span', { class: 'bar-fill' });
  const where = h('p', { class: 'note' });
  const reading = h('div', { class: 'reading' }, h('div', { class: 'bar', 'aria-hidden': 'true' }, bar), where);
  const stop = button('Arrêter', () => {
    stop.disabled = true;
    invoke('import_cancel').catch(() => {});
  }, { kind: 'secondary', act: 'stop' });
  // The picker closes on the first line of the reading.
  let started = false;
  view.onImport = (p) => {
    if (p.etape !== 'lecture') return;
    if (!started) {
      started = true;
      say('thinking', 'Je lis ton export…', 'Rien ne part pendant cette lecture.');
      ui.screen.append(reading);
      ui.actions.append(stop);
    }
    const total = Number(p.total) || 0;
    bar.style.width = `${total ? Math.min(100, (100 * (Number(p.fait) || 0)) / total) : 0}%`;
    where.textContent = Number(p.fichiers) > 1 ? nbsp(`Fichier ${p.fichier} sur ${p.fichiers}`) : '';
  };
  let plan;
  try {
    plan = await invoke('import_pick');
  } catch (e) {
    if (gen === view.gen) showImportError(e);
    return;
  }
  if (gen !== view.gen) return;
  if (!plan || typeof plan !== 'object') showImportChoice();
  else showEstimate(plan);
}

function showPaste(intro = null) {
  const gen = screen('import-paste');
  if (intro) say(...intro);
  else say('idle', 'Demande-lui ce qu’il sait de toi', 'Trois étapes, deux minutes. Rien ne part avant que tu valides.');
  const phrase = h('p', { class: 'phrase', tabindex: '0' }, 'Je prépare la phrase…');
  let text = '';
  const copy = button('Copier la phrase', async () => {
    if (!text) return;
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      ok = legacyCopy(text);
    }
    copy.textContent = ok ? 'Phrase copiée' : 'Sélectionne-la et copie-la';
    later(() => { copy.textContent = 'Copier la phrase'; }, 2500);
  }, { kind: 'secondary', small: true, act: 'copy-phrase' });
  copy.disabled = true;
  const open = (url) => async () => {
    try {
      await invoke('open_link', { url });
    } catch (e) {
      if (gen === view.gen) say('worried', errText(e), `Ouvre toi-même ${url.replace('https://', '')} dans ton navigateur.`);
    }
  };
  const area = h('textarea', { class: 'paste', maxlength: 200000, 'aria-label': 'La réponse de ChatGPT ou de Claude', placeholder: 'Colle sa réponse ici', spellcheck: 'false' });
  const next = button('Continuer', () => readPaste(area.value), { act: 'next' });
  next.disabled = true;
  area.addEventListener('input', () => { next.disabled = !area.value.trim(); });
  ui.screen.append(
    h('ol', { class: 'vsteps card' },
      h('li', {}, h('p', {}, 'Copie cette phrase.'), phrase, copy),
      h('li', {},
        h('p', {}, 'Colle-la dans une nouvelle conversation, chez celui qui te connaît.'),
        h('div', { class: 'row' },
          button('Ouvrir ChatGPT', open(CHATGPT_URL), { kind: 'secondary', small: true, act: 'open-chatgpt' }),
          button('Ouvrir Claude', open(CLAUDE_URL), { kind: 'secondary', small: true, act: 'open-claude' }))),
      h('li', {},
        h('p', {}, 'Copie sa réponse avec le bouton Copier de son bloc de code, et colle-la ici.'),
        area,
        h('p', { class: 'note' }, 'S’il dit qu’il en reste, écris-lui « Continue » et colle la suite à la fin.'))),
    privacyNote(importAi()));
  ui.actions.append(button('Retour', () => showImportChoice(), { kind: 'ghost', act: 'back' }), next);
  invoke('import_phrase').then((p) => {
    if (gen !== view.gen) return;
    text = String(p || '');
    phrase.textContent = text;
    copy.disabled = !text;
  }).catch((e) => {
    if (gen === view.gen) showImportError(e);
  });
}

async function readPaste(text) {
  const gen = screen('import-read');
  say('thinking', 'Je lis sa réponse…', 'Rien ne part pendant cette lecture.');
  ui.screen.append(h('p', { class: 'loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Un instant'));
  let plan;
  try {
    plan = await invoke('import_paste', { text });
  } catch (e) {
    if (gen === view.gen) showImportError(e);
    return;
  }
  if (gen === view.gen) showEstimate(plan);
}

// What the importer read, in words.
function readLines(plan) {
  const out = [];
  if (plan.source === 'collage') {
    out.push(`Sa réponse : ${count(plan.souvenirs, 'ligne')}.`);
  } else {
    const days = Array.isArray(plan.jours) && plan.jours.length === 2 ? `, du ${dayFr(plan.jours[0])} au ${dayFr(plan.jours[1])}` : '';
    if (plan.conversations) out.push(`${count(plan.conversations, 'conversation')}${days}.`);
    if (plan.souvenirs) out.push(`${count(plan.souvenirs, 'souvenir')} enregistré${plan.souvenirs > 1 ? 's' : ''}.`);
    if (plan.projets) out.push(`${count(plan.projets, 'projet')}.`);
  }
  if (plan.deja) out.push(`${count(plan.deja, 'élément')} déjà importé${plan.deja > 1 ? 's' : ''} avant : laissé${plan.deja > 1 ? 's' : ''} de côté.`);
  out.push(`Secrets masqués avant tout envoi : ${Number(plan.masques) || 0}.`);
  if (plan.ecartes) out.push(`Passages laissés de côté (journaux, code, collages de machine) : ${plan.ecartes}.`);
  return out;
}

function showEstimate(plan) {
  const gen = screen('import-plan');
  state.importPlan = plan;
  const a = importAi(plan);
  const choices = (Array.isArray(plan.choix) ? plan.choix : []).filter((c) => c && Number(c.appels) > 0);
  if (!choices.length) {
    showImportError({ code: 'interne', message: 'L’import n’a rien proposé.' });
    return;
  }
  let chosen = choices.find((c) => c.appels === plan.defaut) || choices[choices.length - 1];
  say('idle', 'Voilà ce que j’ai lu', `${a.name} va en tirer ce qui compte pour toi. Choisis combien de demandes il fait.`);
  const warnings = [
    plan.caches ? 'Du texte invisible a été retiré : il cachait peut-être des consignes pour une IA.' : null,
    plan.inconnues ? 'Certaines parties avaient une forme que je ne connais pas : lues au mieux ou laissées.' : null,
  ].filter(Boolean);
  const sizes = h('div', { class: 'sizes', role: 'group', 'aria-label': 'Nombre de demandes' });
  const detail = h('p', { class: 'note' });
  const go = button('', () => runImport(chosen.appels), { act: 'next' });
  const pre = h('pre', { class: 'log-lines', tabindex: '0', 'aria-label': `Ce que j’envoie à ${a.name}` });
  const sent = h('details', { class: 'log sent' }, h('summary', {}, 'Voir exactement ce que j’envoie'), pre);
  let shown = 0;
  const paintSent = async () => {
    if (!sent.open || shown === chosen.appels) return;
    const n = chosen.appels;
    shown = n;
    pre.textContent = 'Je prépare le texte…';
    let text = '';
    try {
      text = String(await invoke('import_preview', { appels: n }));
    } catch (e) {
      text = errText(e);
    }
    if (gen === view.gen && shown === n) pre.textContent = text;
  };
  sent.addEventListener('toggle', paintSent);
  const paint = () => {
    for (const b of sizes.querySelectorAll('button')) {
      const on = Number(b.dataset.appels) === chosen.appels;
      b.className = `btn btn-small ${on ? 'btn-primary' : 'btn-secondary'}`;
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    const convs = plan.source === 'collage' ? '' : `${count(chosen.conversations, 'conversation')} parmi les plus récentes, `;
    detail.textContent = nbsp(`${count(chosen.appels, 'demande')} : ${convs}environ ${chosen.minutes || chosen.appels} min.`);
    go.textContent = nbsp(`Lancer (${count(chosen.appels, 'demande')})`);
    paintSent();
  };
  if (choices.length > 1) {
    for (const c of choices) {
      const b = button(`${SIZES[c.nom] || c.nom} : ${count(c.appels, 'demande')}`, () => { chosen = c; paint(); }, { kind: 'secondary', small: true, act: `size-${c.nom}` });
      b.dataset.appels = String(c.appels);
      sizes.append(b);
    }
  }
  ui.screen.append(
    h('div', { class: 'repair-card card' },
      h('p', { class: 'card-title' }, plan.source === 'collage' ? 'La réponse collée' : `Ton export ${plan.source === 'claude' ? 'Claude' : 'ChatGPT'}`),
      h('ul', { class: 'bullets' }, readLines(plan).map((l) => h('li', {}, l)), warnings.map((w) => h('li', { class: 'warn' }, w)))),
    ...(choices.length > 1 ? [sizes] : []),
    detail,
    h('p', { class: 'note' }, `Ce qui part : ${String(plan.vers || '')}.`),
    h('p', { class: 'note' }, `Ça ne te coûte rien de plus, mais ${String(plan.usage || 'ça compte dans ta limite d’usage')}, à peu près comme une longue question par demande.`),
    sent);
  paint();
  ui.actions.append(
    button('Annuler', () => {
      state.importPlan = null;
      invoke('import_forget').catch(() => {});
      showImportChoice(['idle', 'C’est annulé.', 'Rien n’est parti. D’où vient ta mémoire ?']);
    }, { kind: 'ghost', act: 'back' }),
    go);
  focusMain();
}

async function runImport(n) {
  const gen = screen('import-run');
  const a = importAi(state.importPlan);
  const per = 'Tu peux utiliser ton ordinateur, mais laisse cette fenêtre ouverte jusqu’à la fin.';
  say('working', `${a.name} trie ta mémoire…`, `Demande 1 sur ${n}. ${per}`);
  const started = Date.now();
  const time = h('span', { class: 'chrono-time' }, '0:00');
  const stage = h('span', { class: 'chrono-stage' }, `Environ ${n} min en tout`);
  const bar = h('span', { class: 'bar-fill' });
  ui.screen.append(
    h('div', { class: 'chrono card' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', { class: 'chrono-text' }, time, stage)),
    h('div', { class: 'bar', 'aria-hidden': 'true' }, bar),
    h('p', { class: 'note' }, `${a.name} n’a aucun outil : il lit, il répond, il ne touche à rien. Je vérifie chaque fait avant de l’écrire.`));
  every(() => { time.textContent = clock(Date.now() - started); }, 1000);
  const stop = button('Arrêter', () => {
    stop.disabled = true;
    say('thinking', `J’arrête ${a.name}…`);
    invoke('import_cancel').catch(() => {});
  }, { kind: 'secondary', act: 'stop' });
  ui.actions.append(stop);
  view.onImport = (p) => {
    if (p.etape === 'tri') {
      const k = Number(p.demande) || 1;
      const of = Number(p.sur) || n;
      bar.style.width = `${(100 * (k - 1)) / of}%`;
      if (!stop.disabled) say('working', `${a.name} trie ta mémoire…`, `Demande ${k} sur ${of}. ${per}`);
    } else if (p.etape === 'ecriture') {
      // Writing the note: stopping now could leave half of it, Rust lets it finish.
      stop.disabled = true;
      bar.style.width = '100%';
      stage.textContent = nbsp('J’écris la note');
      say('working', 'Je range tout dans ton cerveau…', 'Une nouvelle note, et une ligne dans ta mémoire.');
    }
  };
  let res;
  try {
    res = await invoke('import_run', { appels: n });
  } catch (e) {
    if (gen === view.gen) showImportError(e);
    return;
  }
  if (gen === view.gen) showImportResult(res);
}

function showImportResult(r) {
  screen('import-done');
  const pasted = Boolean(state.importPlan && state.importPlan.source === 'collage');
  state.importPlan = null;
  const faits = Number(r && r.faits) || 0;
  if (!r || !r.note || !faits) {
    say('idle', 'Je n’ai rien trouvé de durable à garder.', 'Rien n’a été écrit dans ton cerveau.');
    ui.actions.append(button('Terminé', showDone, { act: 'next' }));
    focusMain();
    return;
  }
  const name = String(r.note).replace(/^Imports\//, '').replace(/\.md$/, '');
  say('happy', 'C’est rangé !', `${count(faits, 'fait')} dans Imports › ${name}.`);
  const par = r.par_categorie && typeof r.par_categorie === 'object' ? r.par_categorie : {};
  const jetes = Object.values(r.jetes && typeof r.jetes === 'object' ? r.jetes : {}).reduce((x, y) => x + (Number(y) || 0), 0);
  // [text, warning]
  const notes = [
    r.partiel ? [`Import partiel : ${count(r.demandes, 'demande')} sur ${r.sur}. Plus tard, reviens sur « Importer ma mémoire » et ${pasted ? 'colle la même réponse' : 'choisis le même fichier'} : je reprendrai là où je me suis arrêté.`, true] : null,
    r.suspect ? ['Attention : une partie ressemblait à des consignes pour une IA. Elles n’ont pas été suivies : relis bien la note.', true] : null,
    jetes ? [`${count(jetes, 'fait')} laissé${jetes > 1 ? 's' : ''} de côté (sans source, numéro, adresse ou consigne pour une IA).`, false] : null,
    r.avis ? [String(r.avis), true] : null,
  ].filter(Boolean);
  ui.screen.append(
    h('div', { class: 'repair-card card' },
      h('p', { class: 'card-title' }, 'Ce que je retiens de toi'),
      h('ul', { class: 'bullets' }, CATEGORIES.filter(([k]) => Number(par[k]) > 0).map(([k, label]) => h('li', {}, `${label} : ${par[k]}`)))),
    ...notes.map(([t, warn]) => h('p', { class: warn ? 'note warn' : 'note' }, t)),
    h('p', { class: 'note' }, 'Relis-les : corrige ou supprime ce qui est faux. Rien d’autre n’a changé dans ton cerveau.'));
  const open = button('Voir la note', async () => {
    open.disabled = true;
    try {
      await invoke('import_open_note');
      say('happy', 'J’ouvre la note…', 'Elle est dans Obsidian, dossier Imports.');
    } catch (e) {
      say('worried', 'Ça n’a pas marché.', errText(e));
    }
    later(() => { open.disabled = false; }, 3000);
  }, { act: 'next' });
  ui.actions.append(button('Terminé', showDone, { kind: 'secondary', act: 'done' }), open);
  focusMain();
}

// Each stop, one sentence and what to do next. Nothing was written unless the result said so.
function showImportError(e) {
  const code = e && typeof e === 'object' ? String(e.code || '') : '';
  const message = e && typeof e === 'object' && e.message ? String(e.message) : errText(e);
  const a = importAi(state.importPlan);
  if (code === 'arrete') {
    showImportChoice(['idle', 'Arrêté.', 'Rien n’a été écrit dans ton cerveau.']);
    return;
  }
  if (code === 'phrase') {
    showPaste(['worried', 'Tu as collé ma phrase.', 'À l’étape 3, colle plutôt la réponse de ChatGPT ou de Claude.']);
    return;
  }
  screen('import-error');
  const back = () => button('Retour', () => showImportChoice(), { kind: 'ghost', act: 'back' });
  if (code === 'a_jour') {
    say('idle', 'Ton cerveau doit d’abord être mis à jour', 'Il a été installé avant l’import de mémoire. 1 à 5 minutes, et rien de tes notes ne change.');
    ui.actions.append(button('Retour', showDone, { kind: 'ghost', act: 'back' }), button('Mettre à jour mon cerveau', updateBrain, { act: 'next' }));
  } else if (code === 'deja') {
    say('idle', 'C’est déjà dans ton cerveau.', message);
    ui.actions.append(back(), button('Terminé', showDone, { act: 'next' }));
  } else if (code === 'auth') {
    say('worried', `${a.name} n’est pas connecté à ton abonnement.`, 'Connecte-le, puis relance l’import. Rien n’est parti.');
    const engine = a === AIS.codex ? 'codex' : 'claude';
    const connect = connectButton(a, async () => {
      connect.disabled = true;
      try {
        await invoke(LOGINS[engine].open);
      } catch (err) {
        say('worried', 'La fenêtre ne s’est pas ouverte.', errText(err));
        connect.disabled = false;
        return;
      }
      say('thinking', 'J’attends que tu te connectes…', `Suis les étapes ${OS.inTerminal}.`);
      poll(() => {
        if (state.importPlan) showEstimate(state.importPlan);
        else showImportChoice();
      }, engine);
    });
    ui.actions.append(back(), connect);
  } else if (code === 'quota') {
    say('worried', `${a.forfait} est à sa limite pour le moment.`, 'Il se recharge tout seul en quelques heures : reviens alors sur « Importer ma mémoire ». Rien n’a été écrit.');
    window.biboCue?.('dodo');
    ui.actions.append(button('Terminé', showDone, { act: 'next' }));
  } else if (['timeout', 'api', 'invalid', 'start', 'unsupported'].includes(code)) {
    // The AI, not the file: the plan is still there, trying again sends nothing more than agreed.
    const slow = ['timeout', 'api', 'invalid'].includes(code);
    say('worried', slow ? `${a.name} ne répond pas pour l’instant.` : `${a.product} ne démarre pas comme il faut.`,
      `${slow ? '' : 'Il se met à jour tout seul en général. '}Rien n’a été écrit : réessaie dans un moment.`);
    ui.actions.append(back(), button('Réessayer', () => {
      if (state.importPlan) showEstimate(state.importPlan);
      else showImportChoice();
    }, { act: 'next' }));
  } else if (code === 'agents') {
    say('worried', 'Je ne sais plus avec quelle IA tu travailles.', 'Ton choix est illisible : je ne demande rien, ni à Claude ni à Codex. Rien n’a été écrit.');
    ui.actions.append(button('Terminé', showDone, { act: 'next' }));
  } else if (code === 'occupe' || code === 'en_cours') {
    say('worried', 'Un import est déjà en cours.', 'Attends qu’il finisse, puis réessaie.');
    ui.actions.append(back());
  } else if (code === 'cerveau_absent' || code === 'python') {
    say('worried', 'Je ne peux pas importer pour l’instant.', message);
    ui.actions.append(button('Retour', showDone, { act: 'next' }));
  } else {
    say('worried', 'Je ne peux pas importer ça.', message);
    if (['pas_un_zip', 'pas_un_export', 'illisible', 'usage'].includes(code)) ui.screen.append(h('p', { class: 'note' }, FOLDER_TIP));
    ui.actions.append(button('Terminé', showDone, { kind: 'ghost', act: 'done' }), button('Essayer autre chose', () => showImportChoice(), { act: 'next' }));
  }
  focusMain();
}

// A brain of before the import: the brain step again brings the importer, then the end.
function updateBrain() {
  state.plan = ['brain'];
  state.steps = {};
  state.failed = null;
  runInstall(0);
}

// ── Start ──────────────────────────────────────────────────────────────────

// WebKit's right-click menu offers « Reload »: mid-install, that would drop the
// window back to the welcome screen while the step keeps running. The menu stays
// where it is useful: pasting the key, copying selected text.
function guardContextMenu(e) {
  const editable = e.target instanceof Element && e.target.closest('input, textarea');
  if (!editable && !String(window.getSelection() || '').trim()) e.preventDefault();
}

function start() {
  document.addEventListener('contextmenu', guardContextMenu);
  mount();
  animateBibo();
  if (!window.__TAURI__) {
    say('worried', 'Ouvre l’app Brain in a Box.', 'Cette page ne marche que dans l’app.');
    return;
  }
  window.__TAURI__.event.listen('step-log', onLog).catch(() => {});
  window.__TAURI__.event.listen('repair-progress', onRepairProgress).catch(() => {});
  window.__TAURI__.event.listen('import-progress', onImportProgress).catch(() => {});
  Promise.all([readOffer(), readGuide()]).then(showWelcome);
}

// Nothing to run: whether the brain and Claude Code are on the disk.
async function readGuide() {
  try {
    state.guide = (await invoke('guide_offert')) === true;
  } catch {
    state.guide = false;
  }
}

// Closed (as shipped), or unreadable: nothing is asked, every screen as before Codex.
async function readOffer() {
  try {
    const o = await invoke('agents_offer');
    const agents = o && Array.isArray(o.agents) ? o.agents.filter((a) => a === 'claude' || a === 'codex') : [];
    state.offer = { open: Boolean(o && o.open === true), agents };
  } catch {
    state.offer = { open: false, agents: [] };
  }
}

start();
