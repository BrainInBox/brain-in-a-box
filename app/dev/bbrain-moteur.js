// A fake Bbrain bridge for the windows' mock pages (bbrain-mock.html, vitre-mock.html):
// window.__TAURI__ as lib.rs gives it to Bbrain's two windows (the commands bbrain_start, bbrain_call,
// bbrain_reply, bbrain_shot, bbrain_open; the events bbrain, bbrain-session, bbrain-touches), answered
// with the frames a live Hermes engine sent (bbrain-trames.js). Each message picks its turn by its
// words, as the fake model did; a turn that asks first waits for bbrain_reply before going on.
// What the windows asked is kept in window.__bbrain.journal, for the checks.
'use strict';

(() => {
  const ecoutes = new Map();
  const journal = [];
  const attente = new Map();
  let lecture = Promise.resolve();
  let enCours = 0;

  function emettre(nom, payload) {
    for (const f of ecoutes.get(nom) || []) f({ event: nom, payload });
  }

  // One frame after the other, as the engine streams them.
  function jouer(trames) {
    enCours++;
    lecture = lecture
      .then(async () => {
        for (const t of trames) {
          const delta = t.params && t.params.type === 'message.delta';
          await new Promise((r) => setTimeout(r, delta ? 15 : 40));
          emettre('bbrain', t);
        }
      })
      .finally(() => { enCours--; });
    return lecture;
  }

  // The turn a message plays, by its words without Bbrain's notes (they name « supprimer » too).
  function tour(texte) {
    const dit = String(texte || '').replace(/\[Bbrain : [^\]]*(?:\[[^\]]*\][^\]]*)*\]/g, '').toLowerCase();
    if (/appelle/.test(dit)) return 'prenom';
    if (/montre|cliquer/.test(dit)) return 'montre';
    if (/supprime/.test(dit) && /\boui\b|finalement/.test(dit)) return 'encore';
    if (/supprime|range/.test(dit)) return 'supprime';
    if (/choisir|question|commencer/.test(dit)) return 'question';
    return null;
  }

  function lancer(nom) {
    const t = TRAMES.tours[nom];
    const question = t.avant.find((f) => f.method === 'approval' || f.method === 'clarify');
    if (question) attente.set(question.id, t.apres);
    jouer(t.avant);
  }

  const panne = { encore: new URLSearchParams(location.search).get('panne') === '1' };

  const COMMANDES = {
    bbrain_start: async () => {
      if (panne.encore) {
        panne.encore = false;
        throw 'Hermes n’a pas démarré. Réessaie dans un instant.';
      }
      return TRAMES.session;
    },
    bbrain_open: async () => null,
    bbrain_shot: async () => ({ path: '/Users/awa/Library/Caches/bbrain/ecran.png', width: 1280, height: 720, dx: 0, dy: 0 }),
    bbrain_call: async ({ method, params }) => {
      if (method === 'prompt.submit') {
        const nom = tour(params.text);
        if (nom) lancer(nom);
        return { status: 'streaming' };
      }
      if (method === 'image.attach') return { attached: true };
      if (method === 'voice.toggle') return { enabled: true };
      if (method === 'voice.record') return { status: params.action === 'start' ? 'recording' : 'stopped' };
      if (method === 'session.interrupt') return { status: 'interrupted' };
      throw `Bbrain ne peut pas demander ça : ${method}`;
    },
    bbrain_reply: async ({ id }) => {
      const suite = attente.get(id);
      if (!suite) throw 'Cette question n’attend plus de réponse.';
      attente.delete(id);
      jouer(suite);
      return null;
    },
  };

  window.__TAURI__ = {
    core: {
      invoke(cmd, args = {}) {
        journal.push({ cmd, ...args });
        const f = COMMANDES[cmd];
        return f ? f(args) : Promise.reject(`commande inconnue : ${cmd}`);
      },
    },
    event: {
      listen(nom, f) {
        if (!ecoutes.has(nom)) ecoutes.set(nom, []);
        ecoutes.get(nom).push(f);
        return Promise.resolve(() => ecoutes.set(nom, ecoutes.get(nom).filter((g) => g !== f)));
      },
    },
  };

  window.__bbrain = { journal, emettre, libre: () => enCours === 0, session: () => TRAMES.session };
})();
