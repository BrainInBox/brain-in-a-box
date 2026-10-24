// Bbrain's pane of glass (vitre.html): a clear window over the whole main screen, which every click
// goes through. Bbrain draws there what it shows: its pointer, which flies to the place the user's
// assistant names with [POINT:x,y:libellé], its words in a bubble, said aloud, and « Je t'écoute »
// while Control and Option are held. What the user says becomes text in the engine (voice.record,
// then voice.transcript); this window sends it with the screen, for the assistant to see where to
// point. It never answers what the engine asks before acting: the conversation (bbrain.js) does.
// Shares bbrain-commun.js and ui.js (h, invoke).
'use strict';

const vue = { session: null, premier: true, brut: '', ecran: null, parle: false, ou: null, vol: null, fondu: null, suite: [] };
const el = {};

// Bbrain's pointer: its tip at (5, 3) of the drawing.
const POINTEUR_SVG = '<svg viewBox="0 0 32 32" width="32" height="32" aria-hidden="true"><path d="M5 3 L27 14 L17.5 17.5 L13 28 Z" fill="#c23a6e" stroke="#fff" stroke-width="2.5" stroke-linejoin="round"/></svg>';
const POINTE = { x: 5, y: 3 };

function monter() {
  el.pointeur = h('div', { class: 'vi-pointeur' });
  el.pointeur.innerHTML = POINTEUR_SVG;
  el.libelle = h('span', { class: 'vi-libelle' });
  el.pointeur.append(el.libelle);
  el.texte = h('p', { class: 'vi-texte' });
  el.bulle = h('div', { class: 'vi-bulle', role: 'status', 'aria-live': 'polite' }, h('strong', {}, 'Bbrain'), el.texte);
  el.ecoute = h('div', { class: 'vi-ecoute' }, h('span', { class: 'vi-point-rouge' }), h('span', { class: 'vi-ecoute-texte' }, 'Je t’écoute…'));
  document.body.append(el.pointeur, el.bulle, el.ecoute);
}

function taille() {
  return { w: window.innerWidth, h: window.innerHeight };
}

function borne(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

// ── The pointer ────────────────────────────────────────────────────────────

function placer(p) {
  el.pointeur.style.transform = `translate(${p.x - POINTE.x}px, ${p.y - POINTE.y}px)`;
}

// Flies along an arc, as a hand would, then the bubble follows. Instant the first time.
function voler(vers, libelle) {
  const de = vue.ou;
  vue.ou = vers;
  el.libelle.textContent = libelle || '';
  el.pointeur.classList.add('vi-visible');
  if (vue.vol) vue.vol.cancel();
  if (!de || !el.pointeur.animate) {
    placer(vers);
    return Promise.resolve();
  }
  const dist = Math.hypot(vers.x - de.x, vers.y - de.y);
  const haut = Math.min(160, dist * 0.35);
  const ctrl = { x: (de.x + vers.x) / 2, y: Math.min(de.y, vers.y) - haut };
  const images = [];
  for (let i = 0; i <= 24; i++) {
    const t = i / 24;
    const x = (1 - t) ** 2 * de.x + 2 * (1 - t) * t * ctrl.x + t ** 2 * vers.x;
    const y = (1 - t) ** 2 * de.y + 2 * (1 - t) * t * ctrl.y + t ** 2 * vers.y;
    images.push({ transform: `translate(${x - POINTE.x}px, ${y - POINTE.y}px)` });
  }
  placer(vers);
  vue.vol = el.pointeur.animate(images, { duration: borne(dist * 0.9, 350, 900), easing: 'ease-in-out' });
  return vue.vol.finished.catch(() => {});
}

// ── The bubble ─────────────────────────────────────────────────────────────

// Next to the pointer, inside the screen, below its label and never over it, nor over the places
// Bbrain is about to show: on its right and below, else on its left, else above.
function poserBulle() {
  const { w, h: haut } = taille();
  const b = el.bulle.getBoundingClientRect();
  const p = vue.ou || { x: w / 2, y: haut * 0.7 };
  const l = el.libelle.textContent ? el.libelle.getBoundingClientRect() : null;
  const dessous = l ? Math.max(p.y + 22, l.bottom + 10) : p.y + 22;
  const dessus = p.y - b.height - 16;
  const droite = p.x + 28;
  const gauche = p.x - b.width - 20;
  const suite = (vue.suite || []).map(surLaVitre);
  const libre = ([x, y]) => x >= 16 && y >= 16 && x + b.width <= w - 16 && y + b.height <= haut - 16
    && !suite.some((s) => s.x > x - 40 && s.x < x + b.width + 40 && s.y > y - 24 && s.y < y + b.height + 24);
  const choix = [[droite, dessous], [gauche, dessous], [droite, dessus], [gauche, dessus]].find(libre);
  let [x, y] = choix || [droite, dessous];
  if (!choix && x + b.width > w - 16) x = gauche;
  if (!choix && y + b.height > haut - 16) y = dessus;
  el.bulle.style.left = `${borne(x, 16, w - b.width - 16)}px`;
  el.bulle.style.top = `${borne(y, 16, haut - b.height - 16)}px`;
}

function dire(texte, { voix = false, dit = texte } = {}) {
  clearTimeout(vue.fondu);
  el.texte.textContent = texte;
  el.bulle.classList.add('vi-visible');
  poserBulle();
  if (voix) parler(dit);
  const mots = texte.split(/\s+/).length;
  vue.fondu = setTimeout(cacher, Math.max(7000, mots * 450 + 5000));
}

function cacher() {
  el.bulle.classList.remove('vi-visible');
  el.pointeur.classList.remove('vi-visible');
}

// ── Listening ──────────────────────────────────────────────────────────────

function ecouter(at, texte) {
  const { w, h: haut } = taille();
  const p = at ? { x: at[0], y: at[1] } : { x: w / 2, y: haut / 2 };
  el.ecoute.querySelector('.vi-ecoute-texte').textContent = texte;
  el.ecoute.style.left = `${borne(p.x + 18, 8, w - 180)}px`;
  el.ecoute.style.top = `${borne(p.y + 18, 8, haut - 48)}px`;
  el.ecoute.classList.add('vi-visible');
  if (!vue.ou) vue.ou = p;
}

function plusEcouter() {
  el.ecoute.classList.remove('vi-visible');
}

async function appeler(method, params) {
  return invoke('bbrain_call', { method, params: { session_id: vue.session, ...params } });
}

async function touches({ payload }) {
  if (!vue.session) return;
  if (payload.down) {
    setTaire();
    clearTimeout(vue.fondu);
    el.bulle.classList.remove('vi-visible');
    ecouter(payload.at, 'Je t’écoute…');
    try {
      await appeler('voice.toggle', { action: 'on' });
      const r = await appeler('voice.record', { action: 'start' });
      if (r && r.status === 'busy') throw Object.assign(new Error('Le micro est déjà pris. Réessaie dans un instant.'), { bbrain: true });
    } catch (e) {
      plusEcouter();
      dire(e && e.bbrain ? e.message : 'Je n’arrive pas à t’entendre. Écris-moi dans la fenêtre Bbrain.');
    }
    return;
  }
  if (!el.ecoute.classList.contains('vi-visible')) return;
  ecouter(payload.at, 'Je réfléchis…');
  appeler('voice.record', { action: 'stop' }).catch(() => {});
}

// What the user said, with their screen if Bbrain may see it.
async function envoyerVoix(texte) {
  ecouter(null, 'Je regarde…');
  let ecran = await invoke('bbrain_shot').catch(() => null);
  if (ecran) {
    try {
      await appeler('image.attach', { path: ecran.path });
    } catch {
      ecran = null;
    }
  }
  vue.ecran = ecran;
  vue.parle = true;
  try {
    await appeler('prompt.submit', { text: consigne(texte, ecran, vue.premier), title_preview: texte });
    vue.premier = false;
    ecouter(null, 'Je réfléchis…');
  } catch (e) {
    plusEcouter();
    dire('Ton message n’est pas parti. Redis-le-moi, ou écris-moi dans la fenêtre Bbrain.');
  }
}

// ── The answer ─────────────────────────────────────────────────────────────

// Where a point of the screen falls on the pane of glass, which may start below the menu bar.
function surLaVitre(p) {
  const dx = (vue.ecran && vue.ecran.dx) || 0;
  const dy = (vue.ecran && vue.ecran.dy) || 0;
  const { w, h: haut } = taille();
  return { x: borne(p.x - dx, 0, w - 1), y: borne(p.y - dy, 0, haut - 1) };
}

async function montrer(points, propre, voix) {
  vue.suite = points.slice(1);
  if (points.length) await voler(surLaVitre(points[0]), points[0].libelle);
  else if (!vue.ou) await voler({ x: taille().w / 2, y: taille().h * 0.62 }, '');
  else el.pointeur.classList.add('vi-visible');
  dire(propre, { voix });
  // The next places, one after the other, while Bbrain speaks.
  for (const p of points.slice(1)) {
    await new Promise((r) => setTimeout(r, 2600));
    if (vue.suite[0] !== p) return;
    vue.suite.shift();
    await voler(surLaVitre(p), p.libelle);
    poserBulle();
  }
}

const EVENEMENTS = {
  'message.start': () => {
    vue.premier = false;
    vue.brut = '';
  },
  'message.delta': (p) => { vue.brut += p.text || ''; },
  'message.complete': (p) => {
    plusEcouter();
    const { points, propre } = lirePoints(p.text != null ? String(p.text) : vue.brut);
    const voix = vue.parle;
    vue.parle = false;
    vue.brut = '';
    if (propre || points.length) montrer(points, propre, voix);
  },
  'voice.transcript': (p) => {
    if (p.no_speech_limit) {
      plusEcouter();
      dire('Je n’ai rien entendu. Maintiens contrôle et option pendant que tu parles.', { voix: true });
      return;
    }
    if (p.text) envoyerVoix(p.text);
    else plusEcouter();
  },
  error: (p) => {
    plusEcouter();
    dire('J’ai eu un souci de mon côté. Réessaie dans un instant.');
  },
  'bbrain.closed': () => {
    plusEcouter();
    vue.session = null;
    dire('J’ai perdu le fil. La fenêtre Bbrain te propose de me reconnecter.');
  },
  // The user's turn now: no « Je réfléchis… » left on screen.
  '?approval': () => {
    plusEcouter();
    dire('Avant d’agir, j’ai besoin de ton accord : je t’ai ouvert la fenêtre Bbrain.', { voix: vue.parle });
  },
  '?clarify': () => {
    plusEcouter();
    dire('J’ai une question pour toi : regarde la fenêtre Bbrain.', { voix: vue.parle });
  },
};

function bienvenue() {
  const { w, h: haut } = taille();
  voler({ x: w / 2 - 40, y: haut * 0.62 }, '').then(() => {
    dire(`${ACCUEIL.titre} ${ACCUEIL.texte} ${ACCUEIL.promesse}`, { voix: true, dit: `${ACCUEIL.titre} ${ACCUEIL.texte}` });
  });
}

async function start() {
  monter();
  if (!window.__TAURI__) return;
  const ev = window.__TAURI__.event;
  suivreMoteur(() => vue.session, EVENEMENTS).catch(() => {});
  ev.listen('bbrain-touches', touches).catch(() => {});
  ev.listen('bbrain-session', ({ payload }) => {
    if (payload && payload !== vue.session) {
      vue.session = payload;
      vue.premier = true;
    }
  }).catch(() => {});
  window.addEventListener('resize', () => el.bulle.classList.contains('vi-visible') && poserBulle());
  try {
    const session = await invoke('bbrain_start');
    if (session !== vue.session) {
      vue.session = session;
      vue.premier = true;
    }
    bienvenue();
  } catch (e) {
    dire('Je n’arrive pas à démarrer. La fenêtre Bbrain te propose de réessayer.');
  }
}

start();
