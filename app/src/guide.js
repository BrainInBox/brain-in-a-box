// Bibo's guide (docs/proposals/guide.md, G1 to G10): a small window, above the program it guides in.
// The user pastes a capture of that program's window (or drops the file on Bibo) and says what they
// want to do; their own Claude says the next action, one at a time, pointed on the image. The capture
// goes to Rust as raw bytes and comes back as the exact image Claude will see: the page only shows it,
// until the next one or the end. Every word of Claude's is shown as text, never as HTML.
// Plain JS, no build step; ui.js has what both windows share.
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
const SVG_NS = 'http://www.w3.org/2000/svg';
// G2: the names the « Logiciel » field suggests. Never a piece of a window's title, which may name a client.
const KNOWN = ['Odoo', 'Sage', 'SAP', 'EBP', 'Cegid', 'Dolibarr', 'QuickBooks', 'Pennylane', 'Excel', 'Word', 'Outlook', 'Gmail', 'Google Sheets', 'Shopify', 'WordPress', 'Doctolib'];
// G2: how the system's own tool puts one window in the clipboard, and whether it keeps a file too.
const CAPTURE_HOW = {
  macos: {
    steps: ['Appuie sur Cmd + Ctrl + Maj + 4, puis sur Espace.', 'Clique sur la fenêtre de ton logiciel : l’image va dans le presse-papiers, sans fichier.'],
    file: '',
  },
  windows: {
    steps: ['Appuie sur Windows + Maj + S, puis choisis « Fenêtre » en haut de l’écran.', 'Clique sur la fenêtre de ton logiciel : l’image va dans le presse-papiers.'],
    file: 'L’Outil Capture de Windows 11 en garde aussi une copie dans Images › Captures d’écran : tu peux l’effacer ensuite.',
  },
  linux: {
    steps: ['Appuie sur la touche Impr. écran, puis choisis « Fenêtre ».', 'Clique sur la fenêtre de ton logiciel, puis sur le rond de capture : l’image va dans le presse-papiers.'],
    file: 'L’outil de capture en garde aussi une copie dans Images › Captures d’écran : tu peux l’effacer ensuite.',
  },
}[SYSTEM];
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
  // Claude Code checked this window's life: guide-progress says so once.
  checking: false,
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
  gui.small = h('button', { type: 'button', class: 'tool', title: 'Réduire Bibo', 'data-act': 'reduire', onclick: () => setSize(G.size === 'reduit' ? 'normal' : 'reduit') },
    fromMarkup(SMALL_SVG), h('span', { class: 'sr-only' }, 'Réduire'));
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
  G.gen += 1;
  document.body.dataset.screen = name;
  gui.screen.replaceChildren();
  gui.actions.replaceChildren();
  gui.screen.scrollTop = 0;
  // Reduced and wide are for a step or an image: anywhere else, the window's own size.
  const sized = name === 'etape' || name === 'apercu';
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
  document.body.classList.toggle('reduit', taille === 'reduit');
  document.body.classList.toggle('large', taille === 'large');
  gui.small.setAttribute('aria-pressed', String(taille === 'reduit'));
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
  return button(label, closeGuide, { kind: 'ghost', act: 'stop' });
}

// ── Start: can Claude be asked (§2)? ────────────────────────────────────────

async function boot() {
  const gen = screen('charge');
  say('thinking', 'Je prépare le guide…', 'Je vérifie Claude Code sur ton ' + MACHINE + '.');
  gui.screen.append(h('div', { class: 'loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Un instant.'));
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
  if (!s.cerveau || s.ia === 'absent') return showNeedsInstall();
  if (s.ia !== 'ready') return showUnavailable(s.ia);
  if (!s.vu) return showHow(true);
  showGoal();
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
  screen('comment');
  say('idle', 'Comment je regarde ton écran', first ? 'Avant de commencer, quatre choses à savoir.' : '');
  const setting = button('Voir mon réglage', () => invoke('open_link', { url: PRIVACY_URL }).catch(() => {}), { kind: 'secondary', small: true, act: 'reglage' });
  gui.screen.append(h('ul', { class: 'bullets how' },
    h('li', {}, 'Je ne vois que les captures que tu colles ici, rien d’autre de ton écran.'),
    h('li', {}, 'L’image part chez Anthropic, l’entreprise qui fait Claude, avec ton abonnement, comme un message que tu enverrais à Claude. Anthropic la garde 30 jours, ou 5 ans si tu as accepté que tes conversations servent à améliorer Claude.', h('br'), setting),
    h('li', {}, 'Bibo n’enregistre rien sur ton ordinateur : ni l’image, ni ce que Claude répond.'),
    h('li', {}, 'Ton logiciel montre des clients ou des montants ? Tu peux cacher des zones avant l’envoi. Demande à ton entreprise si tu as le droit d’envoyer ces écrans.')));
  if (first) {
    const ok = button('J’ai compris', async () => {
      ok.disabled = true;
      try {
        await invoke('guide_compris');
      } catch {
        // The flag is a comfort: without it, these lines show again next time.
      }
      showGoal();
    }, { act: 'next' });
    gui.actions.append(button('Pas maintenant', closeGuide, { kind: 'ghost', act: 'stop' }), ok);
  } else {
    gui.actions.append(button('Retour', () => showGoal(), { act: 'next' }));
  }
  focusMain();
}

// ── G3. The objective (the program's name too: a pasted capture does not say it) ──

function showGoal(said = '') {
  const gen = screen('objectif');
  say('idle', 'Qu’est-ce que tu veux faire ?', said || 'Dis-moi dans quel logiciel, et ce que tu veux y faire : je te guide une étape à la fois.');
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
  gui.screen.append(
    h('div', { class: 'card form' },
      field(h('label', { for: 'guide-logiciel' }, 'Ton logiciel'), soft, list),
      field(label, goal, note('Par exemple : « Faire une facture à un client », « Donner un accès à ma comptable ».')),
      warn),
    h('p', { class: 'note remind' }, 'Je ne vois que ce que tu colles ici. L’image part chez Anthropic, Bibo n’en garde rien. ',
      button('Comment je regarde ton écran', () => showHow(false), { kind: 'ghost', small: true, act: 'comment' })));
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
    G.logiciel = d.logiciel;
    G.learned = d.deja_appris ? `J’ai déjà appris ça avec toi le ${d.deja_appris} : je m’en sers.` : null;
    askCapture({ demande: 'debut' });
  }
  soft.focus();
}

// ── G2, paste mode: the capture, from the clipboard or a dropped file ───────

const PASTE_TITLES = {
  debut: 'Montre-moi ta fenêtre.',
  fait: 'Montre-moi où tu en es.',
  bloque: 'Montre-moi ce que tu vois.',
};

function askCapture(next) {
  G.next = next;
  screen('coller');
  const where = G.logiciel ? of(G.logiciel) : 'de ton logiciel';
  say('idle', PASTE_TITLES[next.demande] || PASTE_TITLES.debut, `Fais une capture de la fenêtre ${where}, puis colle-la ici.`);
  if (next.demande === 'debut' && G.learned) gui.screen.append(h('p', { class: 'learned' }, G.learned));
  const zone = h('textarea', { class: 'paste-zone', rows: '3', 'aria-label': 'Colle ta capture ici', spellcheck: 'false', placeholder: `Clique ici, puis ${PASTE_KEYS}` });
  // Only an image is taken: whatever gets typed goes away.
  zone.addEventListener('input', () => { zone.value = ''; });
  gui.screen.append(
    h('ol', { class: 'plan how-to' }, ...CAPTURE_HOW.steps.map((s) => h('li', {}, s)), h('li', {}, `Reviens ici et appuie sur ${PASTE_KEYS}.`)),
    zone,
    note('Tu peux aussi glisser une image PNG sur cette fenêtre.'));
  if (CAPTURE_HOW.file) gui.screen.append(note(CAPTURE_HOW.file));
  if (next.demande === 'debut' && G.status && G.status.capture && G.status.capture.mode === 'collage') {
    gui.screen.append(note(`Sur ce ${MACHINE}, je ne sais pas encore voir tes fenêtres tout seul : la capture, c’est toi qui la fais.`));
  }
  gui.actions.append(stopButton());
  if (G.step) gui.actions.append(button('Retour', () => showAnswer(G.step, G.stepLook), { kind: 'secondary', act: 'back' }));
  zone.focus();
}

function waitingForCapture() {
  return ['coller', 'apercu', 'cacher'].includes(document.body.dataset.screen);
}

async function onPaste(e) {
  if (!waitingForCapture()) return;
  e.preventDefault();
  const gen = G.gen;
  let bytes = null;
  try {
    bytes = await imageOf(e.clipboardData);
  } catch {
    bytes = null;
  }
  if (gen !== G.gen) return;
  if (!bytes) {
    say('worried', 'Je ne trouve pas d’image dans ce que tu as collé.', 'Refais la capture de ta fenêtre, puis colle-la ici.');
    return;
  }
  const read = reading();
  let a;
  try {
    a = await invoke('guide_coller', bytes);
  } catch (err) {
    if (read !== G.gen) return;
    return showError(err);
  }
  if (read !== G.gen) return;
  showPreview(a);
}

// A dropped file is read in Rust, which says what it found: the page never has a path.
function onDropped(event) {
  if (!waitingForCapture()) return;
  const r = event.payload || {};
  if (r.Ok) showPreview(r.Ok);
  else showError(r.Err);
}

function reading() {
  const gen = screen('lecture');
  say('thinking', 'Je lis ton image…');
  gui.screen.append(h('div', { class: 'loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Un instant.'));
  return gen;
}

// The clipboard's image: a PNG as it is, another kind (a screenshot in JPEG or TIFF) redrawn as one.
async function imageOf(data) {
  const files = [];
  for (const item of (data && data.items) || []) {
    if (item.kind === 'file' && /^image\//.test(item.type)) {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  if (!files.length) for (const f of (data && data.files) || []) if (/^image\//.test(f.type)) files.push(f);
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

function showPreview(a, said = null) {
  G.apercu = a;
  // A new window size: Rust forgot the zones (R7).
  if (!a.zones) G.zones = [];
  const gen = screen('apercu');
  say('idle', said ? said.title : 'Voici ce que Claude va voir.', said ? said.text : 'Vérifie qu’il n’y a rien que tu ne veux pas envoyer : tu peux cacher des zones.');
  const fig = shotFigure(a.apercu, a, null, true);
  gui.screen.append(fig);
  if (a.noir) gui.screen.append(note('La capture est presque noire : ta fenêtre est peut-être réduite ou cachée.', 'warn'));
  if (a.petit) gui.screen.append(note('Cette fenêtre est toute petite : c’est la bonne ?', 'warn'));
  if (G.zones.length) gui.screen.append(note(G.zones.length > 1 ? `${G.zones.length} zones cachées, gardées pour les prochaines captures de cette fenêtre.` : '1 zone cachée, gardée pour les prochaines captures de cette fenêtre.'));
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
  gui.actions.append(
    button('Autre capture', () => askCapture(G.next), { kind: 'secondary', act: 'autre' }),
    button('Cacher une zone', showHide, { kind: 'secondary', act: 'cacher' }),
    button('Envoyer à Claude', () => send(envoiOf(G.next)), { act: 'next' }));
  focusMain();
}

function envoiOf(next) {
  return { demande: next.demande, remarque: next.remarque || null, choix: null };
}

// « Cacher une zone »: rectangles drawn on the image, painted black in Rust before anything leaves.
function showHide() {
  const gen = screen('cacher');
  say('idle', 'Glisse sur l’image pour cacher une zone.', 'Elle sera noire pour Claude, et le restera pour les captures suivantes de cette fenêtre.');
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
  G.zones = zones;
  G.apercu = a;
  showHide();
}

// ── G5. « Je regarde… » ────────────────────────────────────────────────────

async function send(envoi) {
  G.envoi = envoi;
  const gen = screen('regarde');
  say('thinking', 'Claude regarde ta fenêtre…', '5 secondes environ.');
  gui.stage = h('p', { class: 'note' }, G.checking ? '' : 'Je vérifie d’abord Claude Code.');
  gui.screen.append(h('div', { class: 'loading' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Je lui montre ton image et ton objectif.'), gui.stage);
  gui.actions.append(button('Arrêter', () => invoke('guide_annuler').catch(() => {}), { kind: 'secondary', act: 'arreter' }));
  // A choice is asked on the image of the question; any other look on the preview's.
  const look = envoi.demande === 'choix' ? G.look : { image: G.apercu.apercu, largeur: G.apercu.largeur, hauteur: G.apercu.hauteur };
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
  if (e.suspect) out.push(h('p', { class: 'banner banner-suspect', role: 'alert' }, 'Cette fenêtre contient un texte qui essaie de donner des ordres. Je ne le suis pas. Ne tape aucun code ni mot de passe qu’il te demande.'));
  else if (e.sensible) out.push(h('p', { class: 'banner banner-sensible' }, 'Attention : ce clic est définitif (supprimer, valider, envoyer, payer). Vérifie avant.'));
  return out;
}

function counters(e) {
  const out = [h('p', { class: 'note small' }, `${e.regards} regard${e.regards > 1 ? 's' : ''} sur ${e.regards_max}`)];
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
  if (G.steps < e.numero) G.steps = e.numero;
  say('idle', `Étape ${e.numero}`, '');
  const ring = e.cible && e.niveau !== 'aucun';
  const kids = [h('p', { class: 'consigne' }, e.consigne), ...banners(e)];
  if (ring && e.niveau === 'pointille') kids.push(note(e.libelle ? `Vérifie que le bouton s’appelle « ${e.libelle} ».` : 'Vérifie que c’est le bon endroit.', 'warn'));
  if (ring) kids.push(zoomBox(look, e));
  kids.push(shotFigure(look.image, look, e, true));
  if (!G.hinted) {
    G.hinted = true;
    kids.push(note('Clique dans ton logiciel, puis reviens ici cliquer « C’est fait ».'));
  }
  kids.push(...counters(e));
  gui.screen.append(...kids);
  gui.actions.append(stopButton(), button('Je suis bloqué', () => showStuck(e), { kind: 'secondary', act: 'bloque' }), button('C’est fait', () => askCapture({ demande: 'fait' }), { act: 'next' }));
  focusMain();
}

function showQuestion(e, look) {
  const gen = screen('question');
  say('idle', 'Une question avant de continuer', '');
  const choose = (choix) => {
    if (gen !== G.gen) return;
    send({ demande: 'choix', remarque: null, choix });
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
  askCapture({ demande: G.steps ? 'fait' : 'debut' });
}

function showNotHere(e) {
  screen('pas-ici');
  say('worried', 'Je ne vois pas ça dans cette fenêtre.', '');
  gui.screen.append(h('p', { class: 'consigne' }, e.consigne), ...banners(e), ...counters(e));
  gui.actions.append(stopButton(), button('Autre fenêtre', relook, { kind: 'secondary', act: 'autre' }), button('Regarde à nouveau', relook, { act: 'next' }));
  focusMain();
}

function showBlocked(e) {
  screen('bloque');
  say('worried', e.suspect ? 'Méfie-toi de cet écran.' : 'Ça coince.', '');
  gui.screen.append(h('p', { class: 'consigne' }, e.consigne), ...banners(e), ...counters(e));
  gui.actions.append(stopButton(), button('Je suis bloqué', () => showStuck(e), { kind: 'secondary', act: 'bloque' }), button('Regarde à nouveau', relook, { act: 'next' }));
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
  say('idle', 'Qu’est-ce qui coince ?', 'Dis-le-moi si tu veux, puis montre-moi ta fenêtre.');
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
  gui.actions.append(button('Retour', () => showAnswer(G.step, G.stepLook), { kind: 'ghost', act: 'back' }), button('Regarde mon écran', () => {
    const remarque = [picked, free.value.trim()].filter(Boolean).join(' : ').slice(0, REMARQUE_MAX);
    askCapture({ demande: 'bloque', remarque });
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
  gui.screen.append(h('div', { class: 'card form' },
    field(h('label', { for: 'guide-titre' }, 'Le titre'), title, note('Vérifie qu’aucun nom de client n’est dans le titre.', 'warn')),
    field(h('label', { for: 'guide-etapes' }, `Les étapes dans ${d.logiciel || 'ton logiciel'}, une par ligne`), steps,
      d.retire ? note('J’ai retiré les nombres et les adresses.') : null),
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

function reprise(unix) {
  if (!unix) return '';
  const d = new Date(unix * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// What Bibo says of an error, as words only.
function errorWords(e) {
  const kind = (e && e.kind) || '';
  switch (kind) {
    case 'quota': return { title: e.reprise ? `Ton abonnement est à sa limite jusqu’à ${reprise(e.reprise)}.` : 'Ton abonnement est à sa limite pour l’instant.', text: 'Le guide reprendra quand la limite sera passée.' };
    case 'hors_ligne': return { title: 'Pas d’internet : je ne peux pas demander à Claude.', text: 'Vérifie ton Wi-Fi, puis réessaie.' };
    case 'api': return { title: 'Claude ne répond pas pour l’instant.', text: 'Réessaie dans un moment.' };
    case 'delai': return { title: 'Claude met trop de temps.', text: 'Tu peux réessayer : ça compte un regard.' };
    case 'image_non_vue':
    case 'invalide': return { title: 'Claude n’a pas bien vu l’image.', text: 'Tu peux réessayer : ça compte un regard.' };
    case 'refus': return { title: 'Claude n’a pas voulu analyser cette capture.', text: 'Cache la zone suspecte, ou passe à un autre écran.' };
    case 'lancement': return { title: 'Claude ne s’est pas lancé comme prévu : je préfère arrêter le guide.', text: 'Relance l’app pour réessayer.' };
    case 'image_illisible': return { title: 'Je n’arrive pas à lire cette image.', text: 'Refais une capture de la fenêtre seule, puis colle-la ici.' };
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
  if (kind === 'sans_capture') return askCapture(G.next || { demande: G.steps ? 'fait' : 'debut' });
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
  const retry = (label = 'Réessayer') => button(label, () => send(G.envoi), { act: 'next' });
  if (['delai', 'image_non_vue', 'invalide', 'api', 'hors_ligne'].includes(kind) && G.envoi) {
    gui.actions.append(stopButton(), retry());
  } else if (kind === 'refus') {
    gui.actions.append(stopButton(), button('Regarde à nouveau', () => askCapture(G.next || { demande: 'debut' }), { kind: 'secondary', act: 'autre' }),
      button('Cacher une zone', showHide, { act: 'next' }));
  } else if (kind === 'image_illisible') {
    gui.actions.append(stopButton(), button('Réessayer', () => askCapture(G.next || { demande: 'debut' }), { act: 'next' }));
  } else if (kind === 'budget') {
    gui.actions.append(button('Fermer le guide', closeGuide, { kind: 'ghost', act: 'stop' }), button('Nouvel objectif', () => showGoal(), { act: 'next' }));
  } else if (kind === 'occupe' && G.apercu) {
    gui.actions.append(button('Revenir à l’aperçu', () => showPreview(G.apercu), { act: 'next' }));
  } else {
    gui.actions.append(button('Fermer le guide', closeGuide, { kind: kind === 'quota' || kind === 'lancement' ? 'primary' : 'ghost', act: kind === 'quota' || kind === 'lancement' ? 'next' : 'stop' }));
    if (G.apercu && !['quota', 'lancement'].includes(kind)) gui.actions.append(button('Revenir à l’aperçu', () => showPreview(G.apercu), { act: 'next' }));
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

function start() {
  document.addEventListener('contextmenu', guardContextMenu);
  mount();
  if (!window.__TAURI__) {
    say('worried', 'Ouvre l’app Brain in a Box.', 'Cette page ne marche que dans l’app.');
    return;
  }
  document.addEventListener('paste', onPaste);
  window.__TAURI__.event.listen('guide-progress', onProgress).catch(() => {});
  window.__TAURI__.event.listen('guide-depose', onDropped).catch(() => {});
  boot();
}

start();
