// Bibo's guide (docs/proposals/guide.md, G1 to G10): a small window, above the program it guides in.
// The user shows Bibo their program's window once (a click in it on Windows and X11, the system's
// picker on macOS 14 and later), says what they want to do, and Bibo takes that window in photo at
// each step. Where Bibo cannot see a window (macOS 13, Wayland, a window that draws black), the user
// pastes a capture or drops its file instead. Either way the image goes to Rust and comes back as the
// exact image Claude will see: the page only shows it, until the next one or the end. Their own Claude
// says the next action, one at a time, pointed on the image. Every word of Claude's is shown as text,
// never as HTML. Plain JS, no build step; ui.js has what both windows share.
'use strict';

const SYSTEM = ['macos', 'windows', 'linux'].includes(window.BIBO_OS) ? window.BIBO_OS : detectSystem();
const MACHINE = SYSTEM === 'macos' ? 'Mac' : 'PC';
const PASTE_KEYS = SYSTEM === 'macos' ? 'Cmd + V' : 'Ctrl + V';
const LOGIN_POLL_MS = 3000;
const HAPPY_MS = 4500;
const PRIVACY_URL = 'https://claude.ai/settings/data-privacy-controls';
// shot.rs: the grey band under the image, its code inside; Claude's zones never reach it.
const BAND = 28;
const OBJECTIF_MAX = 300;
const REMARQUE_MAX = 200;
const LOGICIEL_MAX = 40;
const MIN_ZONE = 4;
// The looks left in a session are said from there on, not before (point 20 of the review).
const LEFT_SHOWN = 10;
const SVG_NS = 'http://www.w3.org/2000/svg';
// G2: the names the « Logiciel » field suggests. Never a piece of a window's title, which may name a client.
const KNOWN = ['Odoo', 'Sage', 'SAP', 'EBP', 'Cegid', 'Dolibarr', 'QuickBooks', 'Pennylane', 'Excel', 'Word', 'Outlook', 'Gmail', 'Google Sheets', 'Shopify', 'WordPress', 'Doctolib'];
// A browser's window: the program is the page, so the browser's name is never the program's (R5).
const BROWSER = /\b(?:chrome|chromium|edge|firefox|safari|opera|brave|vivaldi)\b/i;
// Programs that run in a browser: its tabs and address bar leave with the capture (point 19).
const WEB = ['Odoo', 'Dolibarr', 'Pennylane', 'Gmail', 'Google Sheets', 'Shopify', 'WordPress', 'Doctolib'];
// G2: how the system's own tool copies a window, key by key (each key with its symbol: « Maj » is ⇧
// on the keyboard, and Windows' own words in English too), whether it keeps a file, how to start over.
const CAPTURE_HOW = {
  macos: {
    steps: ['Appuie en même temps sur ⌘ Cmd, ⌃ Ctrl, ⇧ Maj et 4, puis sur la barre d’espace : un petit appareil photo apparaît.', 'Clique sur la fenêtre de ton logiciel : l’image est copiée, sans fichier.'],
    other: '',
    file: '',
    oops: 'Tu t’es trompé ? Appuie sur Échap (esc) et recommence.',
  },
  windows: {
    steps: ['Appuie en même temps sur ⊞ Windows, ⇧ Maj (Shift) et S. L’écran devient plus sombre : c’est normal.', 'Glisse la souris pour encadrer la fenêtre de ton logiciel, puis lâche : l’image est copiée.'],
    other: 'Autre façon : clique dans la fenêtre de ton logiciel, puis appuie en même temps sur Alt et Impr. écran (PrtSc).',
    file: 'L’Outil Capture (Snipping Tool) en garde aussi une copie dans Images › Captures d’écran (Pictures › Screenshots) : tu peux l’effacer ensuite.',
    oops: 'Tu t’es trompé ? Appuie sur Échap (Esc) et recommence.',
  },
  linux: {
    steps: ['Appuie sur la touche Impr. écran (Print Screen), puis choisis « Fenêtre ».', 'Clique sur la fenêtre de ton logiciel, puis sur le rond de capture : l’image va dans le presse-papiers.'],
    other: '',
    file: 'L’outil de capture en garde aussi une copie dans Images › Captures d’écran : tu peux l’effacer ensuite.',
    oops: 'Tu t’es trompé ? Appuie sur Échap et recommence.',
  },
}[SYSTEM];
// What to say when the clipboard holds no new capture: the likeliest slip on each system.
const SLIP = {
  macos: 'As-tu appuyé aussi sur ⌃ Ctrl ? Sans lui, la capture va sur le Bureau : glisse ce fichier ici, ou refais ⌘ Cmd + ⌃ Ctrl + ⇧ Maj + 4 puis Espace.',
  windows: 'Refais ⊞ Windows + ⇧ Maj + S, encadre la fenêtre de ton logiciel, puis colle ici avec Ctrl + V.',
  linux: 'Refais la capture avec la touche Impr. écran, en mode « Fenêtre », puis colle-la ici.',
}[SYSTEM];
// Where a window waits when it is minimized.
const DOCK = SYSTEM === 'macos' ? 'dans le Dock' : 'dans la barre des tâches';
// What goes where, for whoever decides at work (point 19): the same facts as G1, in one block to paste.
const COMPANY_NOTE = [
  'Le guide de Bibo (Brain in a Box) : ce qui part, et où',
  '',
  '- Quand je le lui demande, Bibo prend une image de la seule fenêtre de logiciel que j’ai choisie (ou de la capture que je colle) et l’envoie à Claude, l’IA de l’entreprise Anthropic, avec mon propre abonnement Claude, comme un message que j’enverrais à Claude.',
  '- Avant chaque envoi, je vois l’image exacte qui part, et je peux y cacher des zones (noms, montants).',
  '- Anthropic garde ces échanges 30 jours, ou 5 ans si j’ai accepté que mes conversations servent à améliorer Claude. Le réglage : https://claude.ai/settings/data-privacy-controls',
  '- Bibo n’enregistre rien sur l’ordinateur : ni l’image, ni la réponse de Claude. Si je le choisis, seule la procédure va dans mes notes, en texte, sans image.',
  '- Brain in a Box ne fournit pas Claude et ne reçoit aucune image. Son code est ouvert : https://github.com/BrainInBox/brain-in-a-box',
].join('\n');
const STUCK = ['Je ne trouve pas le bouton', 'Un message d’erreur s’affiche', 'Ce n’est pas le bon écran'];
const PIN_SVG = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M7 2.5h6l-1 5 3 3v1.5H5V10.5l3-3z"/><path d="M10 12v5.5"/></svg>';
const SMALL_SVG = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><rect x="2.5" y="3.5" width="15" height="13" rx="2"/><rect x="9.5" y="10" width="6" height="4.5" rx="1"/></svg>';

const G = {
  gen: 0,
  status: null,
  logiciel: '',
  objectif: '',
  // « J'ai déjà appris ça avec toi le 12/10 », for the first look of the session.
  learned: null,
  learnedShown: false,
  // What the next look asks: { demande, remarque }.
  next: null,
  // The preview: the exact image the next look sends, its size (band left out), its alerts.
  apercu: null,
  // The hidden zones as drawn, in the image's pixels: Rust keeps them while the window's size holds (R7).
  zones: [],
  // The last question sent, for « Réessayer ».
  envoi: null,
  // The image the last look was asked on, and its size.
  look: null,
  // The step shown, and the image it points on.
  step: null,
  stepLook: null,
  // An answer held while the user says whether they did click (G7).
  held: null,
  // Steps this session: « Regarde à nouveau » goes on (fait) or starts (debut).
  steps: 0,
  hinted: false,
  pinned: true,
  size: 'normal',
  // The reduced mode, as the user chose it: each step keeps it (R3, small laptops).
  reduit: false,
  // Claude Code checked this window's life: guide-progress says so once.
  checking: false,
  // How Bibo takes the capture himself, as the status says: 'clic', 'selecteur', or null (paste only).
  auto: null,
  // How long Rust waits for the click (capture/mod.rs), counted down on the screen.
  secondes: 45,
  // The window chosen (Rust keeps it, and checks it is still the one before each photo).
  fenetre: null,
  // The user pasted instead, or the window drew black: captures are pasted until a window is chosen.
  collage: false,
  // The objective was given: Rust's session started.
  goal: false,
  // A capture pasted before the objective: its preview comes once the objective is given.
  early: false,
  // Rust waits for a click or the system's picker: leaving the screen stops it.
  waiting: false,
};
const gui = {};

// ── Frame: Bibo, the message, the screen, the buttons ──────────────────────

function mount() {
  gui.bibo = fromMarkup(BIBO_SVG);
  gui.title = h('p', { class: 'say-title' });
  gui.text = h('p', { class: 'say-text' });
  gui.message = h('div', { class: 'message', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' }, gui.title, gui.text);
  gui.pin = h('button', { type: 'button', class: 'tool', 'aria-pressed': 'true', title: 'Toujours au-dessus de tes fenêtres', onclick: togglePin },
    fromMarkup(PIN_SVG), h('span', { class: 'sr-only' }, 'Toujours au-dessus'));
  // Its word next to its icon: an icon alone says nothing to someone who never saw it (point 20).
  gui.small = h('button', { type: 'button', class: 'tool tool-labelled', title: 'Réduire Bibo', 'data-act': 'reduire', onclick: () => {
    G.reduit = G.size !== 'reduit';
    setSize(G.reduit ? 'reduit' : 'normal');
  } }, fromMarkup(SMALL_SVG), h('span', { class: 'tool-label' }, 'Réduire'));
  gui.small.hidden = true;
  gui.screen = h('main', { class: 'screen' });
  gui.actions = h('footer', { class: 'actions' });
  document.body.replaceChildren(h('div', { class: 'app' },
    h('header', { class: 'hero' }, gui.bibo, gui.message, h('div', { class: 'tools' }, gui.small, gui.pin)), gui.screen, gui.actions));
}

let moodTimer = 0;

function say(mood, title, text = '') {
  clearTimeout(moodTimer);
  gui.bibo.classList.remove('idle', 'thinking', 'happy', 'worried');
  gui.bibo.classList.add(mood);
  gui.title.textContent = nbsp(title);
  gui.text.textContent = nbsp(text);
  gui.text.hidden = !text;
  if (mood === 'happy') moodTimer = setTimeout(() => say('idle', gui.title.textContent, gui.text.textContent), HAPPY_MS);
}

function screen(name) {
  // Away from the wait for a click or the system's picker (a paste, the list, the end): Rust stops waiting.
  if (G.waiting) {
    G.waiting = false;
    invoke('guide_annuler').catch(() => {});
  }
  G.gen += 1;
  document.body.dataset.screen = name;
  gui.screen.replaceChildren();
  gui.actions.replaceChildren();
  gui.screen.scrollTop = 0;
  // Reduced and wide are for a step or an image: anywhere else, the window's own size.
  const sized = ['etape', 'apercu', 'cacher'].includes(name);
  gui.small.hidden = name !== 'etape';
  if (!sized && G.size !== 'normal') setSize('normal');
  return G.gen;
}

function focusMain() {
  const btn = gui.actions.querySelector('[data-act="next"]:not(:disabled)');
  if (btn) btn.focus({ preventScroll: true });
}

function note(text, cls = '') {
  return h('p', { class: `note${cls ? ` ${cls}` : ''}` }, text);
}

function spinner(text) {
  return h('div', { class: 'loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), text);
}

// A label over its field.
function field(...kids) {
  return h('div', { class: 'field' }, ...kids);
}

// « de Sage », « d'Odoo ».
function of(name) {
  return /^[aeiouyàâéèêëîïôûü]/i.test(name) ? `d’${name}` : `de ${name}`;
}

// Sizes and the pin are set in Rust: the page has no window permission.
function setSize(taille) {
  G.size = taille;
  const reduced = taille === 'reduit';
  document.body.classList.toggle('reduit', reduced);
  document.body.classList.toggle('large', taille === 'large');
  gui.small.setAttribute('aria-pressed', String(reduced));
  gui.small.setAttribute('title', reduced ? 'Agrandir Bibo' : 'Réduire Bibo');
  gui.small.querySelector('.tool-label').textContent = reduced ? 'Agrandir' : 'Réduire';
  invoke('guide_taille', { taille }).catch(() => {});
}

function togglePin() {
  G.pinned = !G.pinned;
  gui.pin.setAttribute('aria-pressed', String(G.pinned));
  invoke('guide_epingler', { epingle: G.pinned }).catch(() => {});
}

// « Fermer le guide »: the images go from the page, then the window and the session in Rust.
async function closeGuide() {
  forget();
  try {
    await invoke('guide_fermer');
  } catch (e) {
    say('worried', 'Je n’ai pas pu fermer le guide.', errText(e));
  }
}

function forget() {
  G.apercu = null;
  G.look = null;
  G.step = null;
  G.stepLook = null;
  G.held = null;
  for (const img of document.querySelectorAll('img')) img.removeAttribute('src');
  for (const z of document.querySelectorAll('.zoom')) z.style.backgroundImage = 'none';
}

function stopButton(label = 'Arrêter le guide') {
  return button(label, label === 'Arrêter le guide' ? confirmStop : closeGuide, { kind: 'ghost', act: 'stop' });
}

// Once a step was shown, « Arrêter le guide » asks first: it sits next to « Je suis bloqué » (point 20).
function confirmStop() {
  if (!G.steps) return closeGuide();
  const mood = ['idle', 'thinking', 'happy', 'worried'].find((m) => gui.bibo.classList.contains(m)) || 'idle';
  const was = { title: gui.title.textContent, text: gui.text.hidden ? '' : gui.text.textContent, buttons: [...gui.actions.childNodes] };
  say('worried', 'Arrêter le guide ?', 'On perdra où on en est.');
  gui.actions.replaceChildren(
    button('Oui, arrêter', closeGuide, { kind: 'ghost', act: 'stop' }),
    button('Non, je continue', () => {
      say(mood, was.title, was.text);
      gui.actions.replaceChildren(...was.buttons);
      focusMain();
    }, { act: 'next' }));
  focusMain();
}

// Copied in the clipboard; false when the webview would not.
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // The old way, below.
  }
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

// ── Start: can Claude be asked (§2)? ────────────────────────────────────────

async function boot() {
  const gen = screen('charge');
  say('thinking', 'Je prépare le guide…', 'Je vérifie Claude Code sur ton ' + MACHINE + '.');
  gui.screen.append(spinner('Un instant.'));
  let s;
  try {
    s = await invoke('guide_status');
  } catch (e) {
    if (gen !== G.gen) return;
    screen('erreur');
    say('worried', 'Je n’ai pas pu préparer le guide.', errText(e));
    gui.actions.append(stopButton('Fermer le guide'), button('Réessayer', boot, { act: 'next' }));
    return;
  }
  if (gen !== G.gen) return;
  G.status = s;
  const c = s.capture || {};
  G.auto = c.mode === 'auto' && ['clic', 'selecteur'].includes(c.via) ? c.via : null;
  G.secondes = Number(c.secondes) > 0 ? Number(c.secondes) : G.secondes;
  if (!s.cerveau || s.ia === 'absent') return showNeedsInstall();
  if (s.ia !== 'ready') return showUnavailable(s.ia);
  if (!s.vu) return showHow(true);
  firstScreen();
}

// Where Bibo sees windows, the window first (G2): the program's name comes from it (R5).
function firstScreen() {
  if (autoMode() && !G.fenetre) return showChoose({ demande: 'debut' });
  showGoal();
}

// Bibo can take the photo himself, and the user did not turn to pasting.
function autoMode() {
  return Boolean(G.auto && !G.collage);
}

function autoShot() {
  return Boolean(autoMode() && G.fenetre);
}

function showNeedsInstall() {
  screen('installer');
  say('worried', 'Installe d’abord ton cerveau : c’est l’affaire de 15 minutes.', 'Le guide se sert de ton cerveau et de Claude Code, que l’installation met sur ton ' + MACHINE + '.');
  gui.actions.append(button('Ouvrir l’installation', closeGuide, { act: 'next' }));
  focusMain();
}

// Claude Code is there but cannot be asked: what stops it, and what the user can do (§2).
function showUnavailable(ia) {
  if (ia === 'not_logged_in') return showLogin();
  if (ia === 'unsupported') {
    screen('vieux');
    say('worried', 'Ton Claude Code est trop ancien pour le guide.', 'Une fenêtre noire va s’ouvrir et le mettre à jour : attends qu’elle dise que c’est fini, puis reviens ici.');
    const update = button('Mettre à jour', async () => {
      update.disabled = true;
      try {
        await invoke('open_claude_update');
        gui.actions.replaceChildren(stopButton('Fermer le guide'), button('C’est fini, on y va', boot, { act: 'next' }));
      } catch (e) {
        say('worried', 'La fenêtre ne s’est pas ouverte.', errText(e));
        update.disabled = false;
      }
    }, { act: 'next' });
    gui.actions.append(stopButton('Fermer le guide'), update);
    focusMain();
    return;
  }
  screen(ia === 'billed' ? 'facture' : 'arrete');
  if (ia === 'billed') {
    say('worried', 'Claude est connecté avec une clé qui serait facturée : je ne m’en sers pas.', 'Le guide passe seulement par ton abonnement Claude.');
  } else {
    say('worried', 'Claude ne s’est pas lancé comme prévu : je préfère arrêter le guide.', 'Relance l’app pour réessayer.');
  }
  gui.actions.append(button('Fermer le guide', closeGuide, { kind: 'secondary', act: 'next' }));
  focusMain();
}

// R26: the login opens a terminal, once; the window waits for it.
function showLogin(after = boot) {
  const gen = screen('connexion');
  let polling = false;
  say('worried', 'Connecte ton compte Claude pour que je puisse te guider.', 'Une fenêtre noire va s’ouvrir, une seule fois : suis ce qu’elle dit, puis reviens ici.');
  const go = button('Connecter mon compte Claude', async () => {
    go.disabled = true;
    try {
      await invoke('open_claude_login');
    } catch (e) {
      if (gen !== G.gen) return;
      say('worried', 'La fenêtre ne s’est pas ouverte.', errText(e));
      go.disabled = false;
      return;
    }
    if (gen !== G.gen) return;
    say('thinking', 'J’attends que tu te connectes…', 'Suis les étapes dans la fenêtre noire.');
    go.textContent = 'Rouvrir la fenêtre';
    go.className = 'btn btn-secondary';
    go.disabled = false;
    // « Rouvrir la fenêtre » opens it again: still one wait.
    if (polling) return;
    polling = true;
    const tick = async () => {
      let logged = false;
      try {
        logged = await invoke('claude_logged_in');
      } catch {
        logged = false;
      }
      if (gen !== G.gen) return;
      if (logged) after();
      else setTimeout(tick, LOGIN_POLL_MS);
    };
    setTimeout(tick, LOGIN_POLL_MS);
  }, { act: 'next' });
  gui.actions.append(stopButton('Fermer le guide'), go);
  focusMain();
}

// ── G1. « Comment je regarde ton écran » ────────────────────────────────────

function showHow(first) {
  const gen = screen('comment');
  say('idle', 'Comment je regarde ton écran', first ? 'Avant de commencer, quatre choses à savoir.' : '');
  const setting = button('Voir mon réglage', () => invoke('open_link', { url: PRIVACY_URL }).catch(() => {}), { kind: 'secondary', small: true, act: 'reglage' });
  const company = button('Copier une note pour mon entreprise', async () => {
    const copied = await copyText(COMPANY_NOTE);
    if (gen !== G.gen) return;
    company.textContent = nbsp(copied ? 'Note copiée : colle-la dans un message' : 'Je n’ai pas pu la copier');
  }, { kind: 'secondary', small: true, act: 'note-entreprise' });
  gui.screen.append(h('ul', { class: 'bullets how' },
    h('li', {}, autoMode()
      ? 'Je ne vois que la fenêtre que tu choisis, et seulement quand tu me le demandes : jamais le reste de ton écran.'
      : 'Je ne vois que les captures que tu colles ici, rien d’autre de ton écran.'),
    h('li', {}, 'L’image part chez Anthropic, l’entreprise qui fait Claude, avec ton abonnement, comme un message que tu enverrais à Claude. Anthropic la garde 30 jours, ou 5 ans si tu as accepté que tes conversations servent à améliorer Claude.', h('br'), setting),
    h('li', {}, 'Bibo n’enregistre rien sur ton ordinateur : ni l’image, ni ce que Claude répond.'),
    h('li', {}, 'Pour essayer sans risque, ouvre la société de démonstration de ton logiciel. Sinon, cache noms et montants avec « Cacher une zone ». Si ces données ne sont pas à toi, vérifie que tu as le droit de les montrer.', h('br'), company)));
  if (first) {
    const ok = button('J’ai compris', async () => {
      ok.disabled = true;
      try {
        await invoke('guide_compris');
      } catch {
        // The flag is a comfort: without it, these lines show again next time.
      }
      firstScreen();
    }, { act: 'next' });
    gui.actions.append(button('Pas maintenant', closeGuide, { kind: 'ghost', act: 'stop' }), ok);
  } else {
    gui.actions.append(button('Retour', () => showGoal(), { act: 'next' }));
  }
  focusMain();
}

// ── G2. The window: a click in it, the system's picker, or a line of the list ──

// The program's name as the « Logiciel » field suggests it (R5): a known name in the window's title or
// its program, else the program's own name; nothing for a browser, whose page is the program.
function guessProgram(f) {
  const app = (f && f.app) || '';
  const words = ` ${(f && f.titre) || ''} ${app} `.toLowerCase();
  const known = KNOWN.find((k) => new RegExp(`[^\\p{L}\\p{N}]${k.toLowerCase()}[^\\p{L}\\p{N}]`, 'u').test(words));
  if (known) return known;
  if (!app || BROWSER.test(app)) return '';
  return app.slice(0, LOGICIEL_MAX);
}

// A pasted capture is taken here too: someone who already has one goes on with it.
function pasteZone(placeholder, rows = 2) {
  const zone = h('textarea', { class: `paste-zone${rows < 3 ? ' paste-small' : ''}`, rows: String(rows), 'aria-label': 'Colle ta capture ici', spellcheck: 'false', placeholder });
  // Only an image is taken: whatever gets typed goes away.
  zone.addEventListener('input', () => { zone.value = ''; });
  return zone;
}

// « Montre-moi ta fenêtre » (G2). A click: Rust waits for the window that comes to the front, the
// time counted down here. The picker (macOS 14+): it opens on the button. `said` replaces Bibo's words
// after a miss; `wait` false waits for « Réessayer » first.
function showChoose(next = G.next || { demande: 'debut' }, said = null, wait = true) {
  G.next = next;
  const gen = screen('choisir');
  const zone = pasteZone(`Tu as déjà une capture ? Clique ici, puis ${PASTE_KEYS}`);
  if (G.auto === 'selecteur') {
    say(said ? 'worried' : 'idle', 'Montre-moi ta fenêtre.', said || 'Clique sur « Choisir la fenêtre » : ton Mac te montre tes fenêtres. Clique sur celle de ton logiciel, puis sur le bouton qui apparaît au milieu.');
    const pick = button('Choisir la fenêtre', () => pickWindow(gen, pick), { act: 'next' });
    gui.screen.append(note('Pendant le guide, une petite icône de partage reste en haut de ton écran : c’est normal.'), zone);
    gui.actions.append(stopButton(), pick);
    zone.focus();
    return;
  }
  say(said ? 'worried' : 'idle', 'Montre-moi ta fenêtre.', said || 'Clique une fois dans la fenêtre de ton logiciel : je la reconnais tout seul.');
  const left = h('span', { class: 'chrono-time' }, `${G.secondes} s`);
  if (wait) gui.screen.append(h('p', { class: 'countdown' }, 'Temps pour cliquer : ', left));
  gui.screen.append(note(`Ton logiciel est réduit ? Clique sur son icône ${DOCK}, puis dans sa fenêtre.`), zone);
  gui.actions.append(stopButton(), button('Choisir dans une liste', showList, { kind: 'secondary', act: 'liste' }));
  if (!wait) gui.actions.append(button('Réessayer', () => showChoose(next), { act: 'next' }));
  zone.focus();
  if (wait) waitClick(gen, left);
}

async function waitClick(gen, left) {
  let n = G.secondes;
  const timer = setInterval(() => {
    if (gen !== G.gen) return clearInterval(timer);
    n = Math.max(0, n - 1);
    left.textContent = `${n} s`;
  }, 1000);
  G.waiting = true;
  let f;
  try {
    f = await invoke('guide_attendre_clic');
  } catch (e) {
    clearInterval(timer);
    if (gen !== G.gen) return;
    G.waiting = false;
    return chooseFailed(e);
  }
  clearInterval(timer);
  if (gen !== G.gen) return;
  G.waiting = false;
  showThisOne(f);
}

// macOS 14 and later: the system's picker, then the window it gave (its name only from macOS 15.2).
async function pickWindow(gen, pick) {
  pick.disabled = true;
  say('idle', 'Choisis ta fenêtre.', 'Clique sur la fenêtre de ton logiciel, puis sur le bouton qui apparaît au milieu.');
  G.waiting = true;
  let f;
  try {
    f = await invoke('guide_choisir', { id: null });
  } catch (e) {
    if (gen !== G.gen) return;
    G.waiting = false;
    return chooseFailed(e);
  }
  if (gen !== G.gen) return;
  G.waiting = false;
  chosen(f);
}

function windowName(f) {
  return [f.app, f.titre].filter(Boolean).join(' — ');
}

// « C'est bien celle-ci ? »: the clicked window, before it is kept.
function showThisOne(f) {
  if (f.terminal) return showTerminal();
  screen('fenetre');
  say('idle', 'C’est bien celle-ci ?', '');
  gui.screen.append(h('div', { class: 'card window-card' }, h('p', { class: 'window-app' }, f.app || 'Ta fenêtre'), f.titre ? note(f.titre) : null));
  const yes = button('Oui, c’est elle', () => {
    yes.disabled = true;
    chooseWindow(f.id);
  }, { act: 'next' });
  gui.actions.append(stopButton(), button('Non, une autre', () => showChoose(G.next), { kind: 'secondary', act: 'autre-fenetre' }), yes);
  focusMain();
}

async function chooseWindow(id) {
  const gen = G.gen;
  let f;
  try {
    f = await invoke('guide_choisir', { id });
  } catch (e) {
    if (gen !== G.gen) return;
    return chooseFailed(e);
  }
  if (gen !== G.gen) return;
  chosen(f);
}

// The window is kept for the session: its program fills the « Logiciel » field, and a session going on
// looks at it straight away.
function chosen(f) {
  G.fenetre = f;
  G.collage = false;
  G.logiciel = guessProgram(f) || G.logiciel;
  if (G.goal) return lookAuto(G.next || { demande: G.steps ? 'fait' : 'debut' });
  showGoal();
}

// The keyboard's way, and a screen reader's (critique 11): the open windows, Bibo's left out.
async function showList() {
  const gen = screen('liste');
  say('idle', 'Choisis ta fenêtre dans la liste.', `Les fenêtres ouvertes sur ton ${MACHINE}, la dernière utilisée en haut.`);
  gui.screen.append(spinner('Un instant.'));
  let list;
  try {
    list = await invoke('guide_fenetres');
  } catch (e) {
    if (gen !== G.gen) return;
    return chooseFailed(e);
  }
  if (gen !== G.gen) return;
  gui.screen.replaceChildren();
  const lines = list.map((f, i) => {
    const why = f.terminal ? 'terminal : je n’y guide pas' : f.reduite ? 'réduite : ouvre-la d’abord' : '';
    return h('li', {}, h('button', { type: 'button', class: 'window-line', 'data-act': `fenetre-${i + 1}`, disabled: Boolean(why), onclick: () => chooseWindow(f.id) },
      h('strong', {}, f.app || 'Sans nom'), f.titre ? ` — ${f.titre}` : '', why ? h('span', { class: 'window-why' }, ` (${why})`) : null));
  });
  if (lines.length) gui.screen.append(h('ul', { class: 'windows' }, ...lines));
  else gui.screen.append(note('Je ne vois aucune fenêtre ouverte. Ouvre ton logiciel, puis clique « Actualiser ».'));
  gui.actions.append(stopButton(), button('Retour', () => showChoose(G.next), { kind: 'secondary', act: 'back' }), button('Actualiser', showList, { kind: 'secondary', act: 'actualiser' }));
  const first = gui.screen.querySelector('.window-line:not(:disabled)');
  if (first) first.focus({ preventScroll: true });
}

function showTerminal() {
  screen('terminal');
  say('worried', 'Je ne guide pas dans un terminal.', 'Pour ça, demande directement à Claude. Ici, montre-moi plutôt la fenêtre de ton logiciel.');
  gui.actions.append(stopButton(), button('Choisir une autre fenêtre', () => showChoose(G.next), { act: 'next' }));
  focusMain();
}

// What went wrong while the window was chosen, and the way on: try again, the list, or a paste.
function chooseFailed(e) {
  const kind = (e && e.kind) || '';
  const next = G.next || { demande: 'debut' };
  if (kind === 'annule') return;
  if (kind === 'terminal') return showTerminal();
  if (kind === 'aucun_clic') {
    return showChoose(next, G.auto === 'selecteur'
      ? 'Tu n’as pas choisi de fenêtre. Clique sur « Choisir la fenêtre » pour recommencer.'
      : 'Je n’ai pas vu de clic. Clique sur « Réessayer », puis une fois dans la fenêtre de ton logiciel.', false);
  }
  if (kind === 'selecteur_ferme') return showChoose(next, 'Tu as fermé la fenêtre de choix. Clique sur « Choisir la fenêtre » pour recommencer.');
  if (kind === 'fenetre_introuvable') return showChoose(next, 'Je ne trouve plus cette fenêtre : elle s’est peut-être fermée. Clique dans la fenêtre de ton logiciel.');
  if (kind === 'selecteur_refuse' || kind === 'capture_indisponible') {
    // This run of the app, Bibo cannot see windows here (macOS refused, X11 lost its compositor): a paste.
    G.auto = null;
    return askCapture(next, { title: `Je ne peux pas voir tes fenêtres tout seul sur ce ${MACHINE}.`, text: 'Fais une capture et colle-la ici : je te montre comment.' });
  }
  showError(e);
}

// ── G3. The objective (the program's name too: a pasted capture does not say it) ──

function showGoal(said = '') {
  const gen = screen('objectif');
  const win = autoShot() ? G.fenetre : null;
  say('idle', 'Qu’est-ce que tu veux faire ?', said || (win
    ? 'Dis-moi ce que tu veux faire dans ton logiciel : je te guide une étape à la fois.'
    : 'Dis-moi dans quel logiciel, et ce que tu veux y faire : je te guide une étape à la fois.'));
  G.goal = false;
  G.steps = 0;
  G.hinted = false;
  G.step = null;
  G.next = null;
  const list = h('datalist', { id: 'guide-connus' }, ...KNOWN.map((k) => h('option', { value: k })));
  const soft = h('input', { id: 'guide-logiciel', class: 'input', type: 'text', maxlength: LOGICIEL_MAX, list: 'guide-connus', autocomplete: 'off', spellcheck: 'false', placeholder: 'Par exemple : Odoo, Sage, Excel' });
  soft.value = G.logiciel;
  const label = h('label', { for: 'guide-objectif' });
  const goal = h('textarea', { id: 'guide-objectif', class: 'input goal', rows: '3', maxlength: OBJECTIF_MAX });
  goal.value = G.objectif;
  const start = button('Continuer', () => begin(), { act: 'next' });
  const warn = h('p', { class: 'key-msg', role: 'alert' });
  const refresh = () => {
    G.logiciel = soft.value.trim();
    G.objectif = goal.value.trim();
    label.textContent = nbsp(`Qu’est-ce que tu veux faire dans ${G.logiciel || 'ce logiciel'} ?`);
    start.disabled = !G.logiciel || !G.objectif;
  };
  soft.addEventListener('input', refresh);
  goal.addEventListener('input', refresh);
  refresh();
  const how = button('Comment je regarde ton écran', () => showHow(false), { kind: 'ghost', small: true, act: 'comment' });
  if (win) {
    gui.screen.append(h('div', { class: 'card window-card' }, h('p', { class: 'note' }, 'Ta fenêtre :'), h('p', { class: 'window-app' }, windowName(win) || 'celle que tu as choisie'),
      button('Autre fenêtre', () => showChoose({ demande: 'debut' }), { kind: 'secondary', small: true, act: 'autre-fenetre' })));
  }
  gui.screen.append(
    h('div', { class: 'card form' },
      field(h('label', { for: 'guide-logiciel' }, 'Ton logiciel'), soft, list),
      field(label, goal, note('Par exemple : « Faire une facture à un client », « Donner un accès à ma comptable ».')),
      warn),
    win ? h('p', { class: 'note remind' }, 'Je ne regarde que cette fenêtre, et seulement quand tu me le demandes. L’image part chez Anthropic, Bibo n’en garde rien. ', how)
      : h('p', { class: 'note remind' }, 'Je ne vois que ce que tu colles ici. L’image part chez Anthropic, Bibo n’en garde rien. ', how));
  gui.actions.append(stopButton(), start);
  async function begin() {
    start.disabled = true;
    let d;
    try {
      d = await invoke('guide_commencer', { logiciel: soft.value, objectif: goal.value });
    } catch (e) {
      if (gen !== G.gen) return;
      warn.textContent = nbsp(e && e.kind === 'objectif_vide' ? 'Dis-moi ce que tu veux faire.' : errorWords(e).title);
      start.disabled = false;
      return;
    }
    if (gen !== G.gen) return;
    G.goal = true;
    G.logiciel = d.logiciel;
    G.learned = d.deja_appris ? `J’ai déjà appris ça avec toi le ${d.deja_appris} : je m’en sers.` : null;
    G.learnedShown = false;
    // A capture pasted before the objective: Rust kept it, its preview now.
    if (G.early && G.apercu) {
      G.early = false;
      G.next = { demande: 'debut' };
      return showPreview(G.apercu);
    }
    capture({ demande: 'debut' });
  }
  // The program filled in from the window: straight to what to do in it.
  (soft.value ? goal : soft).focus();
}

// ── G2. A new capture: the photo of the chosen window, or a paste ───────────

function capture(next) {
  if (autoShot()) return lookAuto(next);
  if (autoMode()) return showChoose(next);
  askCapture(next);
}

// « Regarde » (G4, G7): Rust checks the window is still the one chosen, then takes it in photo.
async function lookAuto(next) {
  G.next = next;
  const gen = screen('photo');
  say('thinking', 'Je prends ta fenêtre en photo…');
  gui.screen.append(spinner('Un instant.'));
  window.biboCue?.('photo');
  let a;
  try {
    a = await invoke('guide_regarder');
  } catch (e) {
    if (gen !== G.gen) return;
    return shotFailed(e, next);
  }
  if (gen !== G.gen) return;
  showPreview(a);
}

// No photo: the window minimized, closed, or drawing black (a protected program, or one run as
// administrator). Each says what to do; a black one goes to the paste, for the rest of the session.
function shotFailed(e, next) {
  const kind = (e && e.kind) || '';
  if (kind === 'capture_noire') {
    G.collage = true;
    return askCapture(next, { title: 'Ta fenêtre sort toute noire.', text: 'Ton logiciel se protège, ou il est lancé en mode administrateur : je ne peux pas le prendre en photo moi-même. Fais une capture et colle-la ici.' });
  }
  if (kind === 'capture_indisponible') return chooseFailed(e);
  if (kind === 'sans_fenetre') return showChoose(next);
  if (kind === 'fenetre_reduite') {
    screen('reduite');
    say('worried', 'Ta fenêtre est réduite.', `Ouvre-la en grand : clique sur son icône ${DOCK}. Puis reviens ici.`);
    gui.actions.append(stopButton(), button('Coller une capture', () => turnToPaste(next), { kind: 'secondary', act: 'coller' }),
      button('Regarde à nouveau', () => lookAuto(next), { act: 'next' }));
    focusMain();
    return;
  }
  if (kind === 'fenetre_introuvable') {
    G.fenetre = null;
    screen('introuvable');
    say('worried', 'Je ne trouve plus ta fenêtre.', 'Elle est peut-être fermée. Rouvre ton logiciel, puis montre-moi sa fenêtre.');
    gui.actions.append(stopButton(), button('Coller une capture', () => turnToPaste(next), { kind: 'secondary', act: 'coller' }),
      button('Choisir la fenêtre', () => showChoose(next), { act: 'next' }));
    focusMain();
    return;
  }
  showError(e);
}

// The user turns to pasting: for the rest of the session, until a window is chosen again.
function turnToPaste(next) {
  G.collage = true;
  askCapture(next);
}

// ── G2, paste mode: the capture, from the clipboard or a dropped file ───────

const PASTE_TITLES = {
  debut: 'Montre-moi ta fenêtre.',
  fait: 'Montre-moi où tu en es.',
  bloque: 'Montre-moi ce que tu vois.',
};

// `said` replaces Bibo's words: why the capture is pasted here (a window drawing black…).
function askCapture(next, said = null) {
  G.next = next;
  screen('coller');
  const where = G.logiciel ? of(G.logiciel) : 'de ton logiciel';
  if (said) say('worried', said.title, said.text);
  else say('idle', PASTE_TITLES[next.demande] || PASTE_TITLES.debut, `Fais une capture de la fenêtre ${where}, puis colle-la ici.`);
  if (next.demande === 'debut' && G.learned && !G.learnedShown) {
    G.learnedShown = true;
    gui.screen.append(h('p', { class: 'learned' }, G.learned));
  }
  const zone = pasteZone(`Clique ici, puis ${PASTE_KEYS}`, 3);
  gui.screen.append(
    h('ol', { class: 'plan how-to' }, ...CAPTURE_HOW.steps.map((s) => h('li', {}, s)), h('li', {}, `Reviens ici et appuie sur ${PASTE_KEYS}.`)),
    zone,
    note(CAPTURE_HOW.oops),
    note('Tu peux aussi glisser le fichier de ta capture sur cette fenêtre.'));
  if (CAPTURE_HOW.other) gui.screen.append(note(CAPTURE_HOW.other));
  if (CAPTURE_HOW.file) gui.screen.append(note(CAPTURE_HOW.file));
  if (next.demande === 'debut' && !said && G.status && G.status.capture && G.status.capture.mode === 'collage') {
    gui.screen.append(note(`Sur ce ${MACHINE}, je ne sais pas encore voir tes fenêtres tout seul : la capture, c’est toi qui la fais.`));
  }
  gui.actions.append(stopButton());
  if (G.step) gui.actions.append(button('Retour', () => showAnswer(G.step, G.stepLook), { kind: 'secondary', act: 'back' }));
  // The first capture: the objective or the program can still be put right.
  else if (next.demande === 'debut' && G.goal) gui.actions.append(button('Retour', () => showGoal(), { kind: 'secondary', act: 'back' }));
  // Bibo can take the photo here: back to it.
  if (G.auto) gui.actions.append(button('Choisir ma fenêtre', () => showChoose(next), { kind: 'secondary', act: 'auto' }));
  zone.focus();
}

// Where a capture pasted or dropped is taken: the paste screen, the preview, the choice of a window,
// and a step, where it stands for « C'est fait » (point 20).
function waitingForCapture() {
  return ['coller', 'apercu', 'cacher', 'choisir', 'etape'].includes(document.body.dataset.screen);
}

async function onPaste(e) {
  if (!waitingForCapture()) return;
  e.preventDefault();
  const gen = G.gen;
  // The screen it came on: the preview or « Cacher une zone » stay, a step goes on to its capture.
  const from = document.body.dataset.screen;
  let bytes = null;
  try {
    bytes = await imageOf(e.clipboardData);
  } catch {
    bytes = null;
  }
  if (gen !== G.gen) return;
  // WebKitGTK hands a page no image of the clipboard, only files: on Linux, Rust reads GTK's clipboard.
  const fromSystem = !bytes && SYSTEM === 'linux';
  if (!bytes && !fromSystem) {
    // A step: some text pasted by mistake, nothing to say.
    if (from !== 'etape') say('worried', 'Je ne trouve pas d’image dans ce que tu as collé.', SLIP);
    return;
  }
  const back = G.next;
  const read = reading();
  let a;
  try {
    a = fromSystem ? await invoke('guide_coller_presse_papiers') : await invoke('guide_coller', bytes);
  } catch (err) {
    if (read !== G.gen) return;
    if (err && err.kind === 'sans_image') {
      if (from === 'etape') return showAnswer(G.step, G.stepLook);
      if (from === 'cacher') showHide();
      else if (from === 'apercu') showPreview(G.apercu);
      else if (from === 'choisir') showChoose(back, null, false);
      else askCapture(back);
      say('worried', 'Je ne trouve pas d’image dans ce que tu as collé.', SLIP);
      return;
    }
    return showError(err);
  }
  if (read !== G.gen) return;
  took(a, from);
}

// A dropped file is read in Rust and waits there: the page takes it only while it waits for a capture,
// and never has a path. Dropped on a question or an error, it changes nothing.
async function onDropped(event) {
  if (!waitingForCapture()) return;
  const from = document.body.dataset.screen;
  const r = event.payload || {};
  if (!('Ok' in r)) return showError(r.Err);
  const read = reading();
  let a;
  try {
    a = await invoke('guide_prendre_depot');
  } catch (err) {
    if (read !== G.gen) return;
    return showError(err);
  }
  if (read !== G.gen) return;
  took(a, from);
}

// A capture the user gave: pasted instead of choosing a window, it stands for the session's way; on a
// step, it is the capture of « C'est fait »; before the objective, its preview waits for it.
function took(a, from) {
  if (from === 'choisir') G.collage = true;
  if (from === 'etape') G.next = { demande: 'fait' };
  if (!G.goal) {
    G.apercu = a;
    G.zones = a.cachees || [];
    G.early = true;
    return showGoal();
  }
  showPreview(a);
}

function reading() {
  const gen = screen('lecture');
  say('thinking', 'Je lis ton image…');
  gui.screen.append(spinner('Un instant.'));
  return gen;
}

// The clipboard's image: a PNG as it is, another kind (a screenshot in JPEG or TIFF) redrawn as one.
async function imageOf(data) {
  const files = [];
  // Array.from: a DataTransferItemList is array-like everywhere, iterable not in every webview.
  for (const item of Array.from((data && data.items) || [])) {
    if (item.kind === 'file' && /^image\//.test(item.type)) {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  if (!files.length) for (const f of Array.from((data && data.files) || [])) if (/^image\//.test(f.type)) files.push(f);
  const file = files.find((f) => f.type === 'image/png') || files[0];
  if (!file) return null;
  if (file.type === 'image/png') return new Uint8Array(await file.arrayBuffer());
  const bmp = await createImageBitmap(file);
  // shot.rs refuses more anyway: no giant canvas for it.
  if (bmp.width > 8000 || bmp.height > 8000) return null;
  const c = document.createElement('canvas');
  c.width = bmp.width;
  c.height = bmp.height;
  c.getContext('2d').drawImage(bmp, 0, 0);
  bmp.close();
  const png = await new Promise((resolve) => c.toBlob(resolve, 'image/png'));
  c.width = 0;
  c.height = 0;
  return png ? new Uint8Array(await png.arrayBuffer()) : null;
}

// ── G4. « Voici ce que Claude va voir » ────────────────────────────────────

// A program in a browser: its tabs and the page's address leave with the window (point 19).
function inBrowser() {
  const app = (autoShot() && G.fenetre.app) || '';
  return BROWSER.test(app) || WEB.some((w) => w.toLowerCase() === G.logiciel.toLowerCase());
}

function showPreview(a, said = null) {
  G.apercu = a;
  // Rust keeps the zones, for each size of capture (R7): the page shows its list.
  G.zones = a.cachees || [];
  const gen = screen('apercu');
  const auto = autoShot();
  if (said) say('idle', said.title, said.text);
  // The capture an answer came for, again: the clipboard still held it, or the window did not change.
  else if (a.meme && auto) say('worried', 'Ta fenêtre n’a pas changé depuis la dernière photo.', 'As-tu fait l’étape dans ton logiciel ? Fais-la, puis reprends la photo.');
  else if (a.meme) say('worried', 'C’est la même capture que la dernière fois.', SLIP);
  else say('idle', 'Voici ce que Claude va voir.', 'Vérifie qu’il n’y a rien que tu ne veux pas envoyer : tu peux cacher des zones.');
  if (G.next && G.next.demande === 'debut' && G.learned && !G.learnedShown) {
    G.learnedShown = true;
    gui.screen.append(h('p', { class: 'learned' }, G.learned));
  }
  const fig = shotFigure(a.apercu, a, null, true);
  gui.screen.append(fig, note('Clique sur l’image pour la voir en grand.', 'small'));
  if (a.noir) gui.screen.append(note('La capture est presque noire : ta fenêtre est peut-être réduite ou cachée.', 'warn'));
  if (a.petit) gui.screen.append(note('Cette fenêtre est toute petite : c’est la bonne ?', 'warn'));
  if (a.ailleurs) gui.screen.append(note('Cette capture n’a pas la même taille que celle où tu as caché des zones : elles ne s’appliquent pas ici. Cache-les à nouveau si besoin.', 'warn'));
  if (G.zones.length) gui.screen.append(note(G.zones.length > 1 ? `${G.zones.length} zones cachées, gardées pour tes prochaines captures de la même taille.` : '1 zone cachée, gardée pour tes prochaines captures de la même taille.'));
  if (inBrowser() && !G.zones.length && G.next && G.next.demande === 'debut') gui.screen.append(note('Tes onglets et l’adresse du site, en haut de la fenêtre, partent aussi : cache-les avec « Cacher une zone » si besoin.'));
  const sent = h('details', { class: 'log sent' }, h('summary', {}, 'Voir exactement ce que j’envoie'));
  let filled = false;
  sent.addEventListener('toggle', async () => {
    if (!sent.open || filled) return;
    filled = true;
    let text;
    try {
      text = await invoke('guide_envoi_texte', { envoi: envoiOf(G.next) });
    } catch (e) {
      text = errorWords(e).title;
    }
    if (gen !== G.gen) return;
    sent.append(h('p', { class: 'note' }, 'L’image ci-dessus, puis ce texte :'), h('pre', { class: 'log-lines' }, text));
  });
  gui.screen.append(sent, note('Elle part chez Anthropic avec ton abonnement Claude. Bibo n’en garde rien.'));
  if (a.meme) {
    gui.actions.append(
      button('Envoyer quand même', () => send(envoiOf(G.next)), { kind: 'secondary', act: 'envoyer' }),
      button('Cacher une zone', showHide, { kind: 'secondary', act: 'cacher' }),
      button(auto ? 'Reprendre la photo' : 'Refaire la capture', () => capture(G.next), { act: 'next' }));
  } else {
    gui.actions.append(
      auto ? button('Autre fenêtre', () => showChoose(G.next), { kind: 'secondary', act: 'autre-fenetre' })
        : button('Autre capture', () => askCapture(G.next), { kind: 'secondary', act: 'autre' }),
      button('Cacher une zone', showHide, { kind: 'secondary', act: 'cacher' }),
      button('Envoyer à Claude', () => send(envoiOf(G.next)), { act: 'next' }));
  }
  focusMain();
}

// A look names the image the user saw: Rust sends it only if it is still the one.
function envoiOf(next) {
  return { demande: next.demande, remarque: next.remarque || null, choix: null, capture: G.apercu.capture };
}

// « Cacher une zone »: rectangles drawn on the image, painted black in Rust before anything leaves.
function showHide() {
  const gen = screen('cacher');
  // A precise rectangle wants the image big: a client's line is 4 px high at 370.
  if (G.size !== 'large') setSize('large');
  say('idle', 'Glisse sur l’image pour cacher une zone.', 'Elle sera noire pour Claude, et le restera pour tes captures suivantes de la même taille.');
  const fig = shotFigure(G.apercu.apercu, G.apercu, null, false);
  const img = fig.querySelector('img');
  const layer = h('div', { class: 'draw' });
  fig.append(layer);
  let start = null;
  let box = null;
  layer.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    const r = img.getBoundingClientRect();
    start = { x: ev.clientX - r.left, y: ev.clientY - r.top, r };
    box = h('div', { class: 'draw-box' });
    layer.append(box);
    if (layer.setPointerCapture) layer.setPointerCapture(ev.pointerId);
    move(ev);
  });
  const corners = (ev) => {
    const x = Math.max(0, Math.min(start.r.width, ev.clientX - start.r.left));
    const y = Math.max(0, Math.min(start.r.height, ev.clientY - start.r.top));
    return { left: Math.min(x, start.x), top: Math.min(y, start.y), width: Math.abs(x - start.x), height: Math.abs(y - start.y) };
  };
  function move(ev) {
    if (!start) return;
    const c = corners(ev);
    for (const k of ['left', 'top', 'width', 'height']) box.style[k] = `${c[k]}px`;
  }
  layer.addEventListener('pointermove', move);
  layer.addEventListener('pointerup', (ev) => {
    if (!start) return;
    const c = corners(ev);
    const scale = G.apercu.largeur / (start.r.width || 1);
    start = null;
    const x = Math.round(c.left * scale);
    const y = Math.round(c.top * scale);
    const zone = {
      x, y,
      largeur: Math.min(G.apercu.largeur - x, Math.round(c.width * scale)),
      hauteur: Math.min(G.apercu.hauteur - y, Math.round(c.height * scale)),
    };
    if (x >= G.apercu.largeur || y >= G.apercu.hauteur || zone.largeur < MIN_ZONE || zone.hauteur < MIN_ZONE) {
      box.remove();
      return;
    }
    hide([...G.zones, zone], gen);
  });
  const undo = button('Annuler la dernière', () => hide(G.zones.slice(0, -1), gen), { kind: 'secondary', act: 'annuler' });
  undo.disabled = !G.zones.length;
  gui.screen.append(fig, note(G.zones.length ? `${G.zones.length} zone${G.zones.length > 1 ? 's' : ''} cachée${G.zones.length > 1 ? 's' : ''}.` : 'Aucune zone cachée pour l’instant.'));
  gui.actions.append(undo, button('J’ai fini', () => showPreview(G.apercu), { act: 'next' }));
}

async function hide(zones, gen) {
  let a;
  try {
    a = await invoke('guide_cacher', { zones });
  } catch (e) {
    if (gen !== G.gen) return;
    return showError(e);
  }
  if (gen !== G.gen) return;
  G.apercu = a;
  G.zones = a.cachees || [];
  showHide();
}

// ── G5. « Je regarde… » ────────────────────────────────────────────────────

async function send(envoi) {
  G.envoi = envoi;
  const gen = screen('regarde');
  say('thinking', 'Claude regarde ta fenêtre…', 'Environ 10 secondes.');
  gui.stage = h('p', { class: 'note' }, G.checking ? '' : 'Je vérifie d’abord Claude Code.');
  gui.screen.append(spinner('Je lui montre ton image et ton objectif.'), gui.stage);
  gui.actions.append(button('Arrêter', () => invoke('guide_annuler').catch(() => {}), { kind: 'secondary', act: 'arreter' }));
  // A choice is asked on the image of the question; any other look on the preview's.
  const look = envoi.demande === 'choix' ? G.look : { image: G.apercu.apercu, largeur: G.apercu.largeur, hauteur: G.apercu.hauteur, capture: G.apercu.capture };
  let e;
  try {
    e = await invoke('guide_envoyer', { envoi });
  } catch (err) {
    if (gen !== G.gen) return;
    return showError(err);
  }
  if (gen !== G.gen) return;
  G.look = look;
  if (e.a_confirmer) return showConfirm(e);
  showAnswer(e, look);
}

function onProgress(event) {
  G.checking = true;
  if (document.body.dataset.screen !== 'regarde' || !gui.stage) return;
  gui.stage.textContent = nbsp(event.payload === 'checking' ? 'Je vérifie d’abord Claude Code.' : 'Claude regarde ta fenêtre.');
}

// ── G6. One step, and its variants ──────────────────────────────────────────

function showAnswer(e, look) {
  G.step = e;
  G.stepLook = look;
  if (e.etat === 'etape') return showStep(e, look);
  if (e.etat === 'question') return showQuestion(e, look);
  if (e.etat === 'pas_ici') return showNotHere(e);
  if (e.etat === 'termine') return showEnd(e);
  showBlocked(e);
}

function banners(e) {
  const out = [];
  // Bibo's filter alone (Claude saw nothing wrong): no word of orders on the screen, a plain warning.
  if (e.filtre) out.push(h('p', { class: 'banner banner-sensible', role: 'alert' }, 'Si ton écran te le demande lui-même, ne le fais pas non plus : c’est souvent une arnaque.'));
  else if (e.suspect) out.push(h('p', { class: 'banner banner-suspect', role: 'alert' }, 'Cette fenêtre contient un texte qui essaie de donner des ordres. Je ne le suis pas. Ne tape aucun code ni mot de passe qu’il te demande.'));
  else if (e.sensible) out.push(h('p', { class: 'banner banner-sensible' }, 'Attention : ce clic est définitif (supprimer, valider, envoyer, payer). Vérifie avant.'));
  return out;
}

function counters(e) {
  const out = [];
  // No Opus for this account: Sonnet answers, and never gets a full ellipse (decision 8, Claude Pro).
  if (e.prudent) out.push(note('Mode prudent : avec ton abonnement, je vois un peu moins précisément. Je montre l’endroit en pointillés : vérifie le nom du bouton avant de cliquer.', 'warn prudent'));
  // The looks left, only once they are few: a count from the start says nothing to act on.
  const left = Math.max(0, e.regards_max - e.regards);
  if (left <= LEFT_SHOWN) {
    out.push(note(left ? `Encore ${left} capture${left > 1 ? 's' : ''} possible${left > 1 ? 's' : ''} pour cette séance.` : 'Plus de capture possible pour cette séance : pour continuer, commence un nouvel objectif.', 'small'));
  }
  const u = e.usage;
  if (u && u.presque) {
    const five = u.cinq_heures != null && u.cinq_heures >= 0.8;
    const share = Math.round(100 * (five ? u.cinq_heures : u.sept_jours));
    out.push(note(`Tu as utilisé ${share} % de ton forfait ${five ? 'des 5 dernières heures' : 'de la semaine'}.`, 'warn'));
  }
  return out;
}

function showStep(e, look) {
  screen('etape');
  if (G.reduit && G.size !== 'reduit') setSize('reduit');
  if (G.steps < e.numero) G.steps = e.numero;
  say('idle', `Étape ${e.numero}`, `Fais-le dans ${G.logiciel || 'ton logiciel'}, puis reviens cliquer « C’est fait ».`);
  const ring = e.cible && e.niveau !== 'aucun';
  const kids = [h('p', { class: 'consigne' }, e.consigne), ...banners(e)];
  if (ring && e.niveau === 'pointille') kids.push(note(e.libelle ? `Vérifie que le bouton s’appelle « ${e.libelle} ».` : 'Vérifie que c’est le bon endroit.', 'warn'));
  // No ring: say why, or the user looks for one on the image.
  if (!ring && !e.suspect) {
    kids.push(note(`Je ne suis pas sûr de l’endroit exact, alors je ne l’entoure pas : cherche ${e.libelle ? `« ${e.libelle} »` : 'ce que dit la phrase ci-dessus'} sur ton écran.`, 'warn'));
  }
  if (ring) kids.push(zoomBox(look, e));
  kids.push(shotFigure(look.image, look, e, true));
  if (!G.hinted) {
    G.hinted = true;
    kids.push(note(autoShot()
      ? 'Quand tu cliques « C’est fait », je reprends moi-même la photo de ta fenêtre.'
      : `Tu peux aussi coller ici ta nouvelle capture (${PASTE_KEYS}) : ça vaut « C’est fait ».`));
  }
  kids.push(...counters(e));
  gui.screen.append(...kids);
  gui.actions.append(stopButton(), button('Je suis bloqué', () => showStuck(e), { kind: 'secondary', act: 'bloque' }), button('C’est fait', () => capture({ demande: 'fait' }), { act: 'next' }));
  focusMain();
}

function showQuestion(e, look) {
  const gen = screen('question');
  say('idle', 'Une question avant de continuer', '');
  const choose = (choix) => {
    if (gen !== G.gen) return;
    send({ demande: 'choix', remarque: null, choix, capture: look.capture });
  };
  gui.screen.append(
    h('p', { class: 'consigne' }, e.question || e.consigne),
    ...banners(e),
    h('div', { class: 'choices-row' },
      ...e.choix.map((c, i) => button(c, () => choose(c), { kind: 'secondary', act: `choix-${i + 1}` })),
      button('Aucun', () => choose('Aucun'), { kind: 'ghost', act: 'aucun' })),
    shotFigure(look.image, look, null, true),
    ...counters(e));
  gui.actions.append(stopButton());
}

function relook() {
  capture({ demande: G.steps ? 'fait' : 'debut' });
}

function relookButton() {
  return button(autoShot() ? 'Regarde à nouveau' : 'Nouvelle capture', relook, { act: 'next' });
}

function showNotHere(e) {
  screen('pas-ici');
  say('worried', 'Je ne vois pas ça dans cette fenêtre.', '');
  gui.screen.append(h('p', { class: 'consigne' }, e.consigne), ...banners(e), ...counters(e));
  gui.actions.append(stopButton());
  if (autoShot()) gui.actions.append(button('Choisir une autre fenêtre', () => showChoose({ demande: G.steps ? 'fait' : 'debut' }), { kind: 'secondary', act: 'autre-fenetre' }));
  gui.actions.append(relookButton());
  focusMain();
}

function showBlocked(e) {
  screen('bloque');
  say('worried', e.filtre ? 'Je préfère ne pas te guider là-dessus.' : e.suspect ? 'Méfie-toi de cet écran.' : 'Ça coince.', '');
  gui.screen.append(h('p', { class: 'consigne' }, e.consigne), ...banners(e), ...counters(e));
  gui.actions.append(stopButton(), button('Je suis bloqué', () => showStuck(e), { kind: 'secondary', act: 'bloque' }), relookButton());
  focusMain();
}

// G7: Claude sees nothing done. « Oui, c'est fait » goes on with its answer, « Je réessaie » shows the step again.
function showConfirm(e) {
  const gen = screen('confirmer');
  const was = G.step;
  say('worried', 'On dirait que rien n’a changé.', was && was.libelle ? `Tu as bien cliqué sur « ${was.libelle} » ?` : 'Tu as bien fait l’étape ?');
  const decide = async (faite) => {
    let v;
    try {
      v = await invoke('guide_trancher', { faite });
    } catch (err) {
      if (gen !== G.gen) return;
      return showError(err);
    }
    if (gen !== G.gen) return;
    showAnswer(v, faite ? G.look : G.stepLook);
  };
  if (was) gui.screen.append(h('p', { class: 'consigne' }, was.consigne));
  gui.actions.append(button('Je réessaie', () => decide(false), { kind: 'secondary', act: 'reessaie' }), button('Oui, c’est fait', () => decide(true), { act: 'next' }));
  focusMain();
}

// ── G8. « Je suis bloqué » ─────────────────────────────────────────────────

function showStuck(e) {
  // The third time on the same step: better to stop than to spend the subscription going round.
  if (e.blocages >= 2) return showGiveUp();
  screen('coince');
  say('idle', 'Qu’est-ce qui coince ?', autoShot() ? 'Dis-le-moi si tu veux : je reprends ensuite la photo de ta fenêtre.' : 'Dis-le-moi si tu veux, puis montre-moi ta fenêtre.');
  let picked = '';
  const chips = STUCK.map((s) => {
    const b = h('button', { type: 'button', class: 'chip', 'aria-pressed': 'false' }, s);
    b.addEventListener('click', () => {
      picked = picked === s ? '' : s;
      for (const c of chips) c.setAttribute('aria-pressed', String(c === b && picked === s));
    });
    return b;
  });
  const free = h('textarea', { class: 'input goal', rows: '2', maxlength: REMARQUE_MAX, 'aria-label': 'Ce qui se passe, avec tes mots (facultatif)', placeholder: 'Avec tes mots, si tu veux' });
  gui.screen.append(h('div', { class: 'chips' }, ...chips), free);
  gui.actions.append(button('Retour', () => showAnswer(G.step, G.stepLook), { kind: 'ghost', act: 'back' }), button('Continuer', () => {
    const remarque = [picked, free.value.trim()].filter(Boolean).join(' : ').slice(0, REMARQUE_MAX);
    capture({ demande: 'bloque', remarque });
  }, { act: 'next' }));
  focusMain();
}

function showGiveUp(said = 'Tu peux demander à quelqu’un, ou recommencer avec un objectif plus précis.') {
  screen('abandon');
  say('worried', 'On n’y arrive pas ensemble.', said);
  gui.actions.append(button('Arrêter', closeGuide, { kind: 'secondary', act: 'stop' }), button('Nouvel objectif', () => showGoal(), { act: 'next' }));
  focusMain();
}

// ── G9. The end, and G10, the procedure in the brain ───────────────────────

function showEnd(e) {
  screen('fin');
  say('happy', 'Bravo, c’est fait !', e.recapitulatif.length ? 'Voici ce qu’on a fait ensemble.' : '');
  if (e.recapitulatif.length) gui.screen.append(h('ol', { class: 'recap' }, ...e.recapitulatif.map((s) => h('li', {}, s))));
  gui.actions.append(button('Fermer le guide', closeGuide, { kind: 'ghost', act: 'stop' }), button('Nouvel objectif', () => showGoal(), { kind: 'secondary', act: 'objectif' }));
  if (e.recapitulatif.length) gui.actions.append(button('Enregistrer cette procédure', showDraft, { act: 'next' }));
  focusMain();
}

async function showDraft() {
  const gen = screen('enregistrer');
  say('thinking', 'Je prépare la procédure…');
  let d;
  try {
    d = await invoke('guide_brouillon');
  } catch (e) {
    if (gen !== G.gen) return;
    return showError(e);
  }
  if (gen !== G.gen) return;
  say('idle', 'Je l’écris dans ton cerveau ?', 'Relis et corrige si tu veux : la prochaine fois, je m’en servirai.');
  const title = h('input', { id: 'guide-titre', class: 'input', type: 'text', maxlength: '80', spellcheck: 'false' });
  title.value = d.titre;
  const steps = h('textarea', { id: 'guide-etapes', class: 'input steps-edit', rows: String(Math.min(10, d.etapes.length + 1)) }, d.etapes.join('\n'));
  steps.value = d.etapes.join('\n');
  const said = h('p', { class: 'key-msg', role: 'alert' });
  // A step may come from Claude's sentence, which can name the client (rule 6): both fields are read.
  gui.screen.append(h('div', { class: 'card form' },
    field(h('label', { for: 'guide-titre' }, 'Le titre'), title),
    field(h('label', { for: 'guide-etapes' }, `Les étapes dans ${d.logiciel || 'ton logiciel'}, une par ligne`), steps,
      d.retire ? note('J’ai retiré les nombres et les adresses.') : null),
    note('Vérifie qu’aucun nom de client n’est dans le titre ni dans les étapes.', 'warn'),
    said));
  const save = button('Enregistrer dans mon cerveau', async () => {
    save.disabled = true;
    let r;
    try {
      r = await invoke('guide_enregistrer', { note: { titre: title.value, etapes: steps.value.split('\n') } });
    } catch (e) {
      if (gen !== G.gen) return;
      said.textContent = nbsp(saveRefused(e));
      save.disabled = false;
      return;
    }
    if (gen !== G.gen) return;
    showSaved(r);
  }, { act: 'next' });
  gui.actions.append(button('Retour', () => showEnd(G.step), { kind: 'ghost', act: 'back' }), save);
  focusMain();
}

function saveRefused(e) {
  if (e && e.kind === 'procedure_refusee') {
    return e.etape ? `L’étape ${e.etape} contient un lien, un numéro ou une commande : retire-les.` : 'Le titre contient un lien, un numéro ou une commande : retire-les.';
  }
  if (e && e.kind === 'procedure_vide') return 'Il n’y a plus d’étape à enregistrer.';
  if (e && e.kind === 'sans_cerveau') return 'Je ne trouve pas ton cerveau : il est peut-être dans un autre dossier.';
  return errorWords(e).title;
}

function showSaved(r) {
  screen('enregistre');
  say('happy', `Enregistré dans ${r.dossier}.`, 'Je m’en servirai la prochaine fois.');
  gui.screen.append(note(r.remote ? `Cette note est sauvegardée avec le reste de ton cerveau, et envoyée chaque nuit sur ${r.remote}.` : 'Cette note est sauvegardée avec le reste de ton cerveau.'));
  gui.actions.append(button('Fermer le guide', closeGuide, { kind: 'ghost', act: 'stop' }), button('Nouvel objectif', () => showGoal(), { act: 'next' }));
  focusMain();
}

// ── The capture, its ring and its zoom ─────────────────────────────────────

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

// The image Claude saw, the ellipse drawn on it in its own pixels: no screen, no scale to know (§4.6).
function shotFigure(image, size, e, zoomable) {
  const img = h('img', { class: 'shot-img', src: image, alt: 'Ta capture, telle que Claude la voit', draggable: 'false' });
  const fig = h('figure', { class: 'shot' }, img);
  if (zoomable) {
    fig.classList.add('zoomable');
    fig.addEventListener('click', () => setSize(G.size === 'large' ? 'normal' : 'large'));
    fig.setAttribute('title', 'Cliquer pour agrandir ou réduire');
  }
  const z = e && e.cible;
  if (z && e.niveau !== 'aucun') {
    const ring = svgEl('svg', { class: `ring ring-${e.niveau}`, viewBox: `0 0 ${size.largeur} ${size.hauteur + BAND}`, preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' });
    const shape = { cx: z.x + z.largeur / 2, cy: z.y + z.hauteur / 2, rx: (z.largeur / 2) * Math.SQRT2 + 4, ry: (z.hauteur / 2) * Math.SQRT2 + 4 };
    ring.append(svgEl('ellipse', { ...shape, class: 'halo' }), svgEl('ellipse', shape));
    fig.append(ring);
    fig.append(h('p', { class: 'sr-only' }, e.niveau === 'plein'
      ? `Sur l’image, « ${e.libelle} » est entouré en rouge.`
      : `Sur l’image, la zone à vérifier est entourée en pointillés.`));
  }
  return fig;
}

// The target, closer: the same image, at its own size, centered on the target (§G6).
function zoomBox(look, e) {
  const z = e.cible;
  const box = h('div', { class: `zoom zoom-${e.niveau}` }, h('span', { class: 'zoom-ring', 'aria-hidden': 'true' }),
    h('p', { class: 'sr-only' }, 'Le même endroit, en plus grand.'));
  box.style.backgroundImage = `url("${look.image}")`;
  const vars = { '--iw': look.largeur, '--ih': look.hauteur + BAND, '--zx': z.x + z.largeur / 2, '--zy': z.y + z.hauteur / 2, '--zw': z.largeur, '--zh': z.hauteur };
  for (const [k, v] of Object.entries(vars)) box.style.setProperty(k, String(v));
  return box;
}

// ── Errors (§2) ─────────────────────────────────────────────────────────────

// When the limit ends: the hour today, else its day too (the 7-day window may end next week).
function reprise(unix) {
  if (!unix) return '';
  const d = new Date(unix * 1000);
  const hour = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(d) - day(new Date())) / 86400000);
  if (days <= 0) return hour;
  if (days === 1) return `demain ${hour}`;
  const when = days < 7 ? { weekday: 'long' } : { weekday: 'long', day: 'numeric', month: 'long' };
  return `${d.toLocaleDateString('fr-FR', when)} ${hour}`;
}

// The brain's summary at noon runs on the same subscription (critique 10): a limit reached at
// lunchtime is no mystery.
const NOON = 'Le résumé de midi de ton cerveau compte aussi dans cette limite.';

// What Bibo says of an error, as words only.
function errorWords(e) {
  const kind = (e && e.kind) || '';
  switch (kind) {
    case 'quota': return { title: e.reprise ? `Ton abonnement est à sa limite jusqu’à ${reprise(e.reprise)}.` : 'Ton abonnement est à sa limite pour l’instant.', text: `${e.reprise ? 'Reviens après cette heure et relance le guide.' : 'Reviens plus tard et relance le guide.'} ${NOON}` };
    case 'hors_ligne': return { title: 'Pas d’internet : je ne peux pas demander à Claude.', text: 'Vérifie ton Wi-Fi, puis réessaie.' };
    case 'api': return { title: 'Claude ne répond pas pour l’instant.', text: 'Réessaie dans un moment.' };
    case 'delai': return { title: 'Claude met trop de temps.', text: 'Tu peux réessayer : ça compte une capture de plus.' };
    case 'image_non_vue':
    case 'invalide': return { title: 'Claude n’a pas bien vu l’image.', text: 'Tu peux réessayer : ça compte une capture de plus.' };
    case 'modele_absent': return { title: 'Ton forfait Claude ne donne pas accès au modèle du guide.', text: 'Le guide a besoin de Claude Opus ou de Claude Sonnet dans Claude Code : regarde ton abonnement sur claude.ai.' };
    case 'refus': return { title: 'Claude n’a pas voulu analyser cette capture.', text: 'Quelque chose sur cette capture l’a gêné (un code, un mot de passe, un message d’alerte ?). Cache-le, ou montre un autre écran.' };
    case 'lancement': return { title: 'Claude ne s’est pas lancé comme prévu : je préfère arrêter le guide.', text: 'Relance l’app pour réessayer.' };
    case 'image_illisible': return { title: 'Je n’arrive pas à lire cette image.', text: 'Refais une capture de la fenêtre seule, puis colle-la ici.' };
    case 'pas_png': return { title: 'Je ne lis que les captures d’écran au format PNG.', text: 'Ouvre ce fichier, copie l’image, puis colle-la ici.' };
    case 'budget': return { title: 'On a fait beaucoup d’étapes.', text: 'Pour ménager ton abonnement, recommence une séance.' };
    case 'sans_capture': return { title: 'Il me faut une nouvelle capture.', text: 'Montre-moi ta fenêtre telle qu’elle est maintenant.' };
    case 'sans_seance': return { title: 'Le guide est reparti de zéro.', text: 'Redis-moi ce que tu veux faire.' };
    case 'occupe': return { title: 'Un instant : je finis ce que je faisais.', text: '' };
    case 'annule': return { title: 'J’ai arrêté.', text: 'Tu peux renvoyer la même capture, ou en faire une autre.' };
    case 'interne': return { title: 'Ça n’a pas marché.', text: e.message || '' };
    default: return { title: 'Ça n’a pas marché.', text: errText(e) };
  }
}

async function showError(e) {
  const kind = (e && e.kind) || '';
  if (kind === 'indisponible') return e.ia === 'absent' ? showNeedsInstall() : showUnavailable(e.ia);
  if (kind === 'connexion') return showLogin(() => (G.apercu ? showPreview(G.apercu) : boot()));
  if (kind === 'budget' && e.quoi === 'etape') return showGiveUp();
  if (kind === 'sans_seance') return showGoal(errorWords(e).text);
  if (kind === 'sans_capture') return capture(G.next || { demande: G.steps ? 'fait' : 'debut' });
  // The window's own errors (capture/): each its screen.
  if (['capture_noire', 'fenetre_reduite', 'fenetre_introuvable', 'sans_fenetre'].includes(kind)) return shotFailed(e, G.next || { demande: G.steps ? 'fait' : 'debut' });
  if (['terminal', 'aucun_clic', 'selecteur_ferme', 'selecteur_refuse', 'capture_indisponible'].includes(kind)) return chooseFailed(e);
  const w = errorWords(e);
  // Stopped, or refused: the same capture again, its preview with the next look's band.
  if ((kind === 'annule' || kind === 'refus') && G.apercu) {
    const gen = G.gen;
    try {
      const a = await invoke('guide_cacher', { zones: G.zones });
      if (gen !== G.gen) return;
      if (kind === 'annule') return showPreview(a, w);
      G.apercu = a;
    } catch (err) {
      if (gen !== G.gen) return;
      return showError(err);
    }
  }
  screen('erreur');
  say(kind === 'annule' ? 'idle' : 'worried', w.title, w.text);
  // Bibo sleeps until the hour the subscription comes back (bibo.js, absent from dev/screens.mjs).
  if (kind === 'quota') window.biboCue?.('dodo', { reprise: e.reprise });
  const retry = (label = 'Réessayer') => button(label, () => send(G.envoi), { act: 'next' });
  if (['delai', 'image_non_vue', 'invalide', 'api', 'hors_ligne'].includes(kind) && G.envoi) {
    gui.actions.append(stopButton(), retry());
  } else if (kind === 'refus') {
    gui.actions.append(stopButton(), button(autoShot() ? 'Reprendre la photo' : 'Nouvelle capture', () => capture(G.next || { demande: 'debut' }), { kind: 'secondary', act: 'autre' }),
      button('Cacher une zone', showHide, { act: 'next' }));
  } else if (kind === 'image_illisible' || kind === 'pas_png') {
    gui.actions.append(stopButton(), button('Réessayer', () => askCapture(G.next || { demande: 'debut' }), { act: 'next' }));
  } else if (kind === 'budget') {
    gui.actions.append(button('Fermer le guide', closeGuide, { kind: 'ghost', act: 'stop' }), button('Nouvel objectif', () => showGoal(), { act: 'next' }));
  } else if (kind === 'occupe' && G.apercu) {
    gui.actions.append(button('Revenir à l’aperçu', () => showPreview(G.apercu), { act: 'next' }));
  } else {
    // Nothing to do here but close: the limit, a bad start, no model of the guide for this account.
    const closing = ['quota', 'lancement', 'modele_absent'].includes(kind);
    gui.actions.append(button('Fermer le guide', closeGuide, { kind: closing ? 'primary' : 'ghost', act: closing ? 'next' : 'stop' }));
    if (G.apercu && !closing) gui.actions.append(button('Revenir à l’aperçu', () => showPreview(G.apercu), { act: 'next' }));
  }
  focusMain();
}

// ── Start ──────────────────────────────────────────────────────────────────

// The webview's right-click menu offers « Reload », which would drop the page's session: it stays
// where it is useful, to paste in the zone or a field.
function guardContextMenu(e) {
  const editable = e.target instanceof Element && e.target.closest('input, textarea');
  if (!editable && !String(window.getSelection() || '').trim()) e.preventDefault();
}

// WebKit (macOS, Linux) offers Paste outside a text field only if the page asks for it: a step takes a
// capture too.
function allowPaste(e) {
  if (waitingForCapture()) e.preventDefault();
}

function start() {
  document.addEventListener('contextmenu', guardContextMenu);
  mount();
  if (!window.__TAURI__) {
    say('worried', 'Ouvre l’app Brain in a Box.', 'Cette page ne marche que dans l’app.');
    return;
  }
  document.addEventListener('beforepaste', allowPaste);
  document.addEventListener('paste', onPaste);
  window.__TAURI__.event.listen('guide-progress', onProgress).catch(() => {});
  window.__TAURI__.event.listen('guide-depose', onDropped).catch(() => {});
  boot();
}

start();
