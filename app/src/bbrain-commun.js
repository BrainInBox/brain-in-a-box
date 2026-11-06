// What Bbrain's two windows share, the conversation (bbrain.js) and the pane of glass over the screen
// (vitre.js): Bbrain's first words, the note that tells the user's assistant how to point, the points
// it gives back, and Bbrain's voice. Plain JS, no build step: a classic script loaded before the
// window's own, whose top-level names it sees. It calls nothing of Rust's: each window does.
'use strict';

const ACCUEIL = {
  titre: 'Salut ! Moi c’est Bbrain, ton guide.',
  // The keys by their name: on the pane of glass, nothing else on the screen says which ones.
  texte: 'Maintiens les touches contrôle et option, et parle-moi. Comment tu t’appelles ?',
  promesse: 'Promis : je n’envoie jamais rien sans ton accord.',
};

// Until the guide lives in the engine (its SOUL, its path, PARCOURS-guide.md), the first message of a
// conversation tells the user's assistant who it is for this person, and what they were just told.
const CONTEXTE = `[Bbrain : tu es Bbrain, le guide de cette personne. Tu viens de lui dire « ${ACCUEIL.titre} ${ACCUEIL.texte} ». Tu lui as promis de ne jamais rien envoyer sans son accord : avant de payer, envoyer, supprimer ou publier quoi que ce soit, demande-le-lui avec l’outil clarify. Réponds en français simple, sans jargon, et propose toujours la prochaine étape.]`;

// What the user said, as their assistant reads it: the screen joined if Bbrain has it, how to point on
// it, and to keep it short, since the answer is said aloud.
function consigne(dit, ecran, premier) {
  const debut = premier ? `${CONTEXTE}\n\n` : '';
  const vue = ecran
    ? `L’image jointe est mon écran : ${Math.round(ecran.width)} × ${Math.round(ecran.height)} points, l’origine en haut à gauche. Pour me montrer un endroit, ajoute [POINT:x,y:libellé] à ta réponse, x et y en points de cette image. `
    : '';
  return `${debut}${dit}\n\n[Bbrain : ${vue}Réponds court : ta réponse est dite à voix haute.]`;
}

// A message the user typed in the conversation.
function consigneEcrite(ecrit, premier) {
  return premier ? `${CONTEXTE}\n\n${ecrit}` : ecrit;
}

// [POINT:x,y:libellé], [POINT:x,y], [POINT:x,y:libellé:screen2] or [POINT:none], as Clicky's model
// writes them: the points, and the text without them, as it is shown and said.
const POINT_RE = /\[POINT:\s*(?:none|(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?::\s*([^\]]*?))?)\s*\]/gi;

function lirePoints(texte) {
  const points = [];
  const propre = String(texte || '').replace(POINT_RE, (_, x, y, reste) => {
    if (x != null) {
      const libelle = (reste || '').replace(/:\s*screen\d+\s*$/i, '').trim();
      points.push({ x: Number(x), y: Number(y), libelle });
    }
    return '\u0000';
  });
  // Only the spaces around a tag go (all of them before a full stop, a comma or the end): the
  // model's own spaces stay, the French one before ! ? : ; too, made no-break by nbsp (ui.js).
  const net = propre.replace(/[ \t]*\u0000+[ \t]*(?=[.,]|$)/g, '').replace(/[ \t]*\u0000+[ \t]*/g, ' ');
  return { points, propre: nbsp(net.replace(/[ \t]{2,}/g, ' ').trim()) };
}

// The words of a message, without the note Bbrain added for the assistant.
function sansConsigne(texte) {
  return String(texte || '').replace(/\[Bbrain : [^\]]*(?:\[[^\]]*\][^\]]*)*\]/g, '').trim();
}

// Bbrain's voice: the system's own French voices, Eddy first. Nothing leaves the computer.
const VOIX_PREFEREES = ['Eddy', 'Thomas', 'Amélie', 'Audrey', 'Aurélie', 'Jacques'];

function voixFrancaise() {
  if (!('speechSynthesis' in window)) return null;
  const fr = speechSynthesis.getVoices().filter((v) => /^fr/i.test(v.lang));
  const france = fr.filter((v) => /fr[-_]FR/i.test(v.lang));
  for (const nom of VOIX_PREFEREES) {
    const v = france.find((x) => x.name.startsWith(nom)) || fr.find((x) => x.name.startsWith(nom));
    if (v) return v;
  }
  return france[0] || fr[0] || null;
}

function parler(texte) {
  if (!('speechSynthesis' in window) || !texte) return false;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(texte);
  u.lang = 'fr-FR';
  const v = voixFrancaise();
  if (v) u.voice = v;
  speechSynthesis.speak(u);
  return true;
}

function setTaire() {
  if ('speechSynthesis' in window) speechSynthesis.cancel();
}

// Every frame of Bbrain's engine, to the window's handlers: events by their type, the engine's own
// questions (approval, clarify) by their method. Only those of this conversation.
function suivreMoteur(session, handlers) {
  return window.__TAURI__.event.listen('bbrain', ({ payload: frame }) => {
    if (!frame || typeof frame !== 'object') return;
    const p = frame.params || {};
    if (frame.method === 'event') {
      const de = p.session_id;
      if (de && session() && de !== session()) return;
      const f = handlers[p.type];
      if (f) f(p.payload || {}, p);
      return;
    }
    const f = handlers[`?${frame.method}`];
    if (f && frame.id != null && (!p.session_id || !session() || p.session_id === session())) f(frame.id, p);
  });
}
