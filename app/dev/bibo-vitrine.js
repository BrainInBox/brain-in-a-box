// The vitrine (dev/bibo.html): each of Bibo's gestures and scenes on a card, on a loop, with
// « Rejouer ». The cards say when the app or the guide plays it. Needs ui.js and bibo.js first.
'use strict';

const VITRINE = [
  { nom: 'Il sort du carton', quand: 'À l’ouverture de l’app et du guide.', cue: 'sortie' },
  { nom: 'Il inspecte ton ordinateur', quand: 'Le diagnostic.', scene: 'loupe', mood: 'thinking' },
  { nom: 'Il installe, la barre se remplit', quand: 'L’installation : la barre du carton suit la vraie.', scene: 'bricole', mood: 'working', bar: true },
  { nom: 'Regarde l’autre fenêtre', quand: 'Ton ordinateur te demande quelque chose : il attend que tu le fasses.', scene: 'demande', mood: 'working' },
  { nom: 'À toi : ce bouton', quand: 'Un écran attend ton clic et tu ne bouges plus.', cue: 'montre', angle: 40 },
  { nom: 'Il tend la clé', quand: 'Connecter ton compte, vérifier ta clé Voyage.', scene: 'cle' },
  { nom: 'Il patiente', quand: 'Il attend que tu te connectes, ou une réponse qui tarde.', scene: 'attend', mood: 'thinking' },
  { nom: 'C’est connecté !', quand: 'Le compte est connecté, la clé acceptée.', cue: 'cadenas', mood: 'happy' },
  { nom: 'Oups', quand: 'Une étape a échoué.', scene: 'triste', mood: 'worried' },
  { nom: 'Il réfléchit', quand: 'Claude regarde ce qui s’est passé, ou ta fenêtre.', scene: 'reflechit', mood: 'thinking' },
  { nom: 'Il a trouvé', quand: 'Claude a trouvé ce qui coince.', cue: 'ampoule' },
  { nom: 'Il n’est pas sûr', quand: 'Claude n’est pas sûr de lui.', scene: 'plisse' },
  { nom: 'Il répare', quand: 'La réparation.', scene: 'casque', mood: 'working', bar: true },
  { nom: 'C’est prêt !', quand: 'La fin de l’installation, du guide, de l’import.', cue: 'confettis', mood: 'happy' },
  { nom: 'Il lit', quand: 'Il lit ton export de mémoire.', scene: 'lit', mood: 'thinking' },
  { nom: 'Tes souvenirs rentrent dans sa tête', quand: 'L’import de ta mémoire.', scene: 'memoire', mood: 'working' },
  { nom: 'Il prend une photo', quand: 'Le guide capture lui-même ta fenêtre, quand tu le lui demandes (capture automatique).', cue: 'photo' },
  { nom: 'Donne-moi ta capture', quand: 'Le guide attend ta capture, l’import la réponse de ton IA.', scene: 'mains' },
  { nom: 'Il ne regarde pas', quand: 'Tu caches des zones de ta capture.', scene: 'cache' },
  { nom: 'Bien joué', quand: 'Tu as fait l’étape.', cue: 'pouce' },
  { nom: 'Il se gratte la tête', quand: 'Tu es bloqué, ou ça coince.', scene: 'gratte' },
  { nom: 'Je ne vois pas ça', quand: 'Pas sur ta capture, pas d’image collée, rien à garder de l’import.', cue: 'haussement' },
  { nom: 'Méfie-toi', quand: 'La fenêtre donne des ordres, ou demande ce qu’il ne faut pas faire.', scene: 'stop', mood: 'worried' },
  { nom: 'Il dort jusqu’à 14 h 30', quand: 'Ton abonnement est à sa limite : le réveil montre l’heure de reprise.', scene: 'dodo', mood: 'worried' },
  { nom: 'Il range la procédure', quand: 'Le guide a enregistré ce que tu as appris.', cue: 'range', mood: 'happy' },
];

const PAUSE_MS = 1400;
const BAR_MS = 900;

// Today at that hour, as a Unix time: the alarm clock's hour.
function todayAt(hour, minute) {
  const d = new Date();
  d.setHours(hour, minute, 0, 0);
  return Math.floor(d.getTime() / 1000);
}

function card(show) {
  const svg = fromMarkup(BIBO_SVG);
  if (show.mood) svg.classList.replace('idle', show.mood);
  // Still until the card comes into sight.
  svg.classList.add('b-away');
  const bibo = window.Bibo.rig(svg);
  const replay = h('button', { type: 'button', class: 'replay' }, 'Rejouer');
  const el = h('article', { class: 'card' }, h('div', { class: 'stage' }, svg), h('h2', {}, show.nom), h('p', { class: 'when' }, show.quand), replay);
  const run = { seen: false, loop: 0, bar: 0, p: 0.1 };

  const opts = show.scene === 'dodo' ? { reprise: todayAt(14, 30) } : {};
  function scene() {
    if (!show.scene) return;
    bibo.scene('');
    void svg.getBoundingClientRect();
    bibo.scene(show.scene, { ...opts, progress: run.p });
  }

  // A gesture plays, rests a moment, and plays again while the card is in sight.
  function play() {
    clearTimeout(run.loop);
    if (!show.cue || !run.seen) return;
    bibo.cue(show.cue, { angle: show.angle }).then(() => {
      if (bibo.cueName) return;
      run.loop = setTimeout(play, PAUSE_MS);
    });
  }

  // The install bar goes up a step at a time, a spark at each, and starts again.
  function fill() {
    clearTimeout(run.bar);
    if (!show.bar || !run.seen) return;
    run.p = run.p >= 1 ? 0.1 : Math.min(1, run.p + 0.15);
    bibo.progress(run.p);
    if (run.p > 0.1) bibo.stepDone();
    run.bar = setTimeout(fill, BAR_MS);
  }

  replay.addEventListener('click', () => {
    run.p = 0.1;
    scene();
    play();
  });

  scene();
  return {
    el,
    seen(on) {
      if (on === run.seen) return;
      run.seen = on;
      svg.classList.toggle('b-away', !on);
      if (on) {
        play();
        fill();
      } else {
        clearTimeout(run.loop);
        clearTimeout(run.bar);
      }
    },
  };
}

function vitrine() {
  const grid = document.querySelector('.grid');
  const cards = VITRINE.map(card);
  grid.replaceChildren(...cards.map((c) => c.el));
  if (!('IntersectionObserver' in window)) {
    cards.forEach((c) => c.seen(true));
    return;
  }
  // Only the cards in sight play, and none while the page is hidden.
  const byEl = new Map(cards.map((c) => [c.el, c]));
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) byEl.get(e.target).seen(e.isIntersecting && !document.hidden);
  }, { rootMargin: '80px' });
  const watch = () => cards.forEach((c) => {
    io.unobserve(c.el);
    io.observe(c.el);
  });
  watch();
  document.addEventListener('visibilitychange', watch);
}

vitrine();
