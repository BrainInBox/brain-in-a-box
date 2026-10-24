// Bbrain's conversation (bbrain.html): what the user and their assistant said, the box to write in,
// and the only place the user says yes or no before their assistant acts (the engine's "approval")
// or answers its questions ("clarify"). Rust brings this window forward when one comes; closing it
// only hides it. Bbrain's voice and pointer live in the pane of glass (vitre.js); this window does
// not speak. Shares bbrain-commun.js and ui.js (h, button, invoke, errText).
'use strict';

const vue = { session: null, premier: true, bulle: null, brut: '', occupe: false, cartes: new Map() };
const el = {};

function monter() {
  el.etat = h('p', { class: 'bb-etat' }, 'Je me réveille…');
  el.fil = h('main', { class: 'bb-fil', 'aria-live': 'polite' });
  el.champ = h('textarea', { class: 'bb-champ', rows: '2', placeholder: 'Écris-moi ici…', 'aria-label': 'Ton message', onkeydown: touche });
  el.envoi = button('Envoyer', envoyer, { act: 'envoyer' });
  el.stop = button('Arrêter', arreter, { kind: 'secondary', act: 'arreter' });
  el.stop.hidden = true;
  document.body.append(
    h('header', { class: 'bb-tete' },
      h('div', { class: 'bb-avatar', 'aria-hidden': 'true' }, 'B'),
      h('div', {}, h('h1', {}, 'Bbrain'), el.etat)),
    el.fil,
    h('footer', { class: 'bb-pied' },
      h('div', { class: 'bb-ecrire' }, el.champ, el.stop, el.envoi),
      h('p', { class: 'bb-astuce' }, 'Ou maintiens les touches contrôle et option, et parle.')));
}

function etat(texte) {
  el.etat.textContent = texte;
}

function defiler() {
  el.fil.scrollTop = el.fil.scrollHeight;
}

function bulle(qui, texte) {
  const b = h('div', { class: `bb-msg bb-${qui}` }, texte);
  el.fil.append(b);
  defiler();
  return b;
}

// Something went wrong, in Bbrain's words. The exact error, for whoever helps the user, stays
// folded under « Détails », as in the installer (styles.css .log).
function souci(phrase, e) {
  const b = bulle('souci', phrase);
  const detail = e == null ? '' : errText(e);
  if (detail && detail !== phrase) {
    el.fil.append(h('details', { class: 'log bb-details' }, h('summary', {}, 'Détails'), h('pre', { class: 'log-lines' }, detail)));
    defiler();
  }
  return b;
}

function occupe(oui) {
  vue.occupe = oui;
  el.envoi.disabled = oui;
  el.stop.hidden = !oui;
}

function accueil() {
  bulle('bbrain', `${ACCUEIL.titre} ${ACCUEIL.texte}`);
  el.fil.append(h('p', { class: 'bb-promesse' }, ACCUEIL.promesse));
}

// ── Talking ────────────────────────────────────────────────────────────────

function touche(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    envoyer();
  }
}

async function envoyer() {
  const texte = el.champ.value.trim();
  if (!texte || vue.occupe || !vue.session) return;
  el.champ.value = '';
  bulle('moi', texte);
  occupe(true);
  etat('Je réfléchis…');
  try {
    await invoke('bbrain_call', { method: 'prompt.submit', params: { session_id: vue.session, text: consigneEcrite(texte, vue.premier), title_preview: texte } });
    vue.premier = false;
  } catch (e) {
    occupe(false);
    etat('Je t’écoute.');
    souci('Ton message n’est pas parti. Réessaie dans un instant.', e);
  }
}

async function arreter() {
  try {
    await invoke('bbrain_call', { method: 'session.interrupt', params: { session_id: vue.session } });
  } catch (e) {
    souci('Je n’ai pas réussi à m’arrêter. Réessaie.', e);
  }
}

// What the assistant writes, as it comes: without the points, which the pane of glass shows.
function ecrit(texte, fini) {
  if (!vue.bulle) vue.bulle = bulle('bbrain', '');
  vue.brut = texte;
  vue.bulle.textContent = lirePoints(texte).propre || (fini ? '' : '…');
  if (fini && !vue.bulle.textContent) vue.bulle.remove();
  defiler();
}

const EVENEMENTS = {
  'message.start': () => {
    vue.premier = false;
    vue.bulle = null;
    vue.brut = '';
    occupe(true);
    etat('Je réfléchis…');
  },
  'thinking.delta': () => etat('Je réfléchis…'),
  'tool.start': () => etat('Je m’en occupe…'),
  'message.delta': (p) => ecrit(vue.brut + (p.text || ''), false),
  'message.complete': (p) => {
    ecrit(p.text != null ? String(p.text) : vue.brut, true);
    vue.bulle = null;
    vue.brut = '';
    occupe(false);
    etat('Je t’écoute.');
  },
  error: (p) => {
    occupe(false);
    etat('Je t’écoute.');
    souci('J’ai eu un souci de mon côté. Réessaie dans un instant.', p.message);
  },
  // What the user said aloud, written by the engine: the pane of glass sends it.
  'voice.transcript': (p) => {
    if (p.text && !p.no_speech_limit) bulle('moi', p.text);
  },
  'request.cancel': (p) => retirer(p.id, 'Question retirée : j’ai continué sans.'),
  'bbrain.closed': () => perdu(),
  '?approval': (id, p) => carteAccord(id, p),
  '?clarify': (id, p) => carteQuestions(id, p),
};

// ── Asked before acting ────────────────────────────────────────────────────

function repondre(id, result, carte, dit) {
  for (const b of carte.querySelectorAll('button, input')) b.disabled = true;
  vue.cartes.delete(String(id));
  carte.classList.add('bb-repondue');
  carte.append(h('p', { class: 'bb-dit' }, dit));
  invoke('bbrain_reply', { id, result }).catch((e) => souci('Ta réponse ne m’est pas arrivée. Redis-la-moi par écrit.', e));
  defiler();
}

function retirer(id, pourquoi) {
  const carte = vue.cartes.get(String(id));
  if (!carte) return;
  vue.cartes.delete(String(id));
  for (const b of carte.querySelectorAll('button, input')) b.disabled = true;
  carte.classList.add('bb-repondue');
  carte.append(h('p', { class: 'bb-dit' }, pourquoi));
}

// What the engine finds risky, in plain French: its own description is a short English label
// (approval_detection.py), kept for nobody but the command's readers.
const RISQUES = [
  [/delete|remove|erase|wipe|rm\b|rmdir|shred|truncate/i, 'Ça supprime des fichiers, et une suppression ne se défait pas.'],
  [/permission|chmod|chown|acl/i, 'Ça change qui peut lire ou modifier des fichiers.'],
  [/kill|stop|shutdown|reboot/i, 'Ça arrête des programmes ou l’ordinateur.'],
  [/sudo|root|admin|privilege/i, 'Ça demande les droits d’administrateur de ton Mac.'],
  [/curl|wget|remote|download|pipe/i, 'Ça lance un programme venu d’internet.'],
  [/secret|ssh|key|\.env|credential|password/i, 'Ça touche à tes clés ou à tes mots de passe.'],
];

function enClair(p) {
  const dit = `${p.description || ''} ${p.command || ''}`;
  const trouve = RISQUES.find(([motif]) => motif.test(dit));
  return trouve ? trouve[1] : 'Ça peut changer des choses sur ton ordinateur.';
}

// A command the engine finds risky: what it is in plain words, the command itself for those who
// read it, and the user's yes or no. Yes holds for this command only.
function carteAccord(id, p) {
  const carte = h('section', { class: 'bb-carte', 'data-carte': 'accord' },
    h('h2', {}, 'Avant d’agir, j’ai besoin de ton accord'),
    h('p', {}, enClair(p)),
    p.command ? h('pre', { class: 'bb-commande' }, p.command) : null);
  const oui = button('Oui, vas-y', () => repondre(id, { choice: 'once' }, carte, 'Tu as dit oui, pour cette fois.'), { act: 'oui' });
  const non = button('Non', () => repondre(id, { choice: 'deny' }, carte, 'Tu as dit non : rien n’a été fait.'), { kind: 'secondary', act: 'non' });
  carte.append(h('div', { class: 'bb-choix' }, non, oui));
  vue.cartes.set(String(id), carte);
  el.fil.append(carte);
  etat('J’attends ta réponse.');
  defiler();
}

// One to five questions, with or without choices. A choice answers at once when it is the only
// question and takes one answer; otherwise every answer is kept until « Répondre ». The engine
// marks its advised choice « (Recommended) »: shown here in French, and answered without it.
const CONSEILLE = /\s*\(Recommended\)\s*$/i;

function carteQuestions(id, p) {
  const questions = Array.isArray(p.questions) ? p.questions : [];
  const reponses = {};
  const carte = h('section', { class: 'bb-carte', 'data-carte': 'questions' });
  const valider = button('Répondre', () => repondre(id, { answers: reponses, outcome: 'submitted' }, carte, 'Merci, c’est noté.'), { act: 'repondre' });
  const passer = button('Passer', () => repondre(id, { answers: Object.fromEntries(questions.map((q) => [q.qid, null])), outcome: 'cancelled' }, carte, 'Tu as passé cette question.'), { kind: 'ghost', act: 'passer' });
  const pret = () => { valider.disabled = questions.some((q) => !reponses[q.qid] || reponses[q.qid].length === 0); };
  for (const q of questions) {
    const plusieurs = Boolean(q.multi_select);
    const choix = (Array.isArray(q.choices) ? q.choices : []).map((c) => {
      const nom = String(c).replace(CONSEILLE, '');
      const b = button(CONSEILLE.test(String(c)) ? `${nom} (conseillé)` : nom, () => {
        autre.value = '';
        if (plusieurs) {
          const pris = new Set(Array.isArray(reponses[q.qid]) ? reponses[q.qid] : []);
          if (pris.has(nom)) pris.delete(nom);
          else pris.add(nom);
          reponses[q.qid] = [...pris];
          b.classList.toggle('bb-choisi', pris.has(nom));
          return pret();
        }
        reponses[q.qid] = nom;
        for (const x of choix) x.classList.toggle('bb-choisi', x === b);
        if (questions.length === 1) return repondre(id, { answers: reponses, outcome: 'submitted' }, carte, `Tu as répondu : ${nom}`);
        pret();
      }, { kind: 'secondary', small: true, act: 'choix' });
      return b;
    });
    const autre = h('input', { class: 'bb-autre', type: 'text', placeholder: choix.length ? 'Autre réponse…' : 'Ta réponse…', 'aria-label': q.question });
    autre.addEventListener('input', () => {
      if (autre.value.trim()) reponses[q.qid] = autre.value.trim();
      else delete reponses[q.qid];
      for (const x of choix) x.classList.remove('bb-choisi');
      pret();
    });
    autre.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !valider.disabled) valider.click();
    });
    carte.append(h('h2', {}, q.question), choix.length ? h('div', { class: 'bb-options' }, choix) : null, autre);
  }
  carte.append(h('div', { class: 'bb-choix' }, passer, valider));
  pret();
  vue.cartes.set(String(id), carte);
  el.fil.append(carte);
  etat('J’attends ta réponse.');
  defiler();
}

// ── The engine ─────────────────────────────────────────────────────────────

function perdu() {
  occupe(false);
  vue.session = null;
  etat('J’ai perdu le fil.');
  bulle('souci', 'J’ai perdu le fil.');
  const encore = button('Me reconnecter', () => { encore.remove(); demarrer(); }, { kind: 'secondary', small: true, act: 'reconnecter' });
  el.fil.append(encore);
  defiler();
}

async function demarrer() {
  etat('Je me réveille…');
  try {
    nouvelle(await invoke('bbrain_start'));
    etat('Je t’écoute.');
    el.champ.focus();
  } catch (e) {
    etat('Je n’arrive pas à démarrer.');
    souci('Je n’arrive pas à démarrer. Réessaie dans un instant.', e);
    const encore = button('Réessayer', () => { encore.remove(); demarrer(); }, { kind: 'secondary', small: true, act: 'reessayer' });
    el.fil.append(encore);
  }
}

// A new conversation (Bbrain's engine restarted, by this window or the other): the guide's context
// goes again with the first message.
function nouvelle(session) {
  if (!session || session === vue.session) return;
  vue.session = session;
  vue.premier = true;
}

function start() {
  monter();
  if (!window.__TAURI__) {
    bulle('souci', 'Ouvre l’app Brain in a Box : Bbrain vit dedans.');
    return;
  }
  accueil();
  suivreMoteur(() => vue.session, EVENEMENTS).catch(() => {});
  window.__TAURI__.event.listen('bbrain-session', ({ payload }) => nouvelle(payload)).catch(() => {});
  demarrer();
}

start();
