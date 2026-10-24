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
import { GUIDE_SCENARIOS, GUIDE_SIZES } from './scenarios-guide.mjs';

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
  's=manque&go=done&add=done&key=pa-abcdefghijklmnopqrstuvwxyz',
  's=manque&go=done&add=done&key=pa-abcdefghijklmnopqrstuvlimit',
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
  's=manque&go=done&click=next,next,next,skip',
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
  's=installe&go=import-done&click=import,pick',
  's=installe&go=import-done&click=import,pick,size-rapide&sent=1',
  's=installe&go=import-done&click=import,pick&fichiers=3&os=windows',
  's=installe&go=import-done&click=import&colle=1',
  's=installe&go=import-paste&click=import&os=linux',
  's=installe&go=import-done&click=import,pick&imp=partiel',
  's=installe&go=import-done&click=import,pick&imp=suspect',
  's=installe&go=import-done&click=import,pick&imp=rien',
  's=installe&go=import-error&click=import,pick&imp=deja',
  's=installe&go=import-error&click=import,pick&imp=compte',
  's=installe&go=import-error&click=import,pick&imp=auth',
  's=installe&go=import-error&click=import,pick&imp=quota',
  's=installe&go=import-error&click=import&imp=a_jour',
  // A beginner's likeliest stops: not an export, a folder's five files, the AI silent, the sentence pasted back.
  's=installe&go=import-error&click=import,pick&imp=pas_un_zip',
  's=installe&go=import-error&click=import,pick&imp=trop_de_fichiers&os=windows',
  's=installe&go=import-error&click=import,pick&imp=timeout',
  's=installe&go=import-paste&nth=2&click=import&colle=1&imp=phrase',
  's=installe&go=import&nth=2&click=import,pick,back',
  's=installe&go=import&nth=2&click=import,pick,next,stop&pause=import',
  // The model refused to answer (Claude Code would have had another one answer instead).
  's=repair-ok&go=done&error=refused',
  // A beginner's day one: the preview's number on the welcome, the login put off twice (the end asks for
  // it first), a login that never comes (what to do, after 3 min: here 20 s), the guide under a failure.
  's=manque&go=welcome&version=0.1.76&os=windows',
  's=manque&go=done&click=skip&os=windows',
  's=manque&go=done&nth=2&click=skip&jamais=1&hint=20',
  's=login-early&go=done&jamais=1&hint=20',
  's=echec&go=done&fail=brain&os=windows&guide=1',
  // The reviews': back without Claude's login (asked first, then the guide), the subscription's page, a
  // warning on the network at the diagnosis, the folder in full on Windows.
  's=installe&go=welcome&deco=1&os=windows',
  's=installe&go=welcome&deco=1&click=next&os=windows',
  's=manque&go=welcome&click=plan',
  's=manque&go=diag&alerte=network&os=windows',
  's=manque&go=diag&alerte=disk',
  's=installe&go=done&click=vault&os=windows',
  // The second reviews': back on an install that stopped half way, the guide working (D1); Claude Code slow
  // to say its login; no Obsidian under « Voir mes notes »; a disk nearly full on a Mac.
  's=installe&go=welcome&incomplet=1&os=windows',
  's=installe&go=welcome&lent=1&os=windows',
  's=installe&go=done&click=vault&obsidian=absent&os=windows',
  // The diagnosis says it all: the lines for later (« Plus tard »), an account paid per message, a claude.ai
  // account without its plan said, no winget (and the way to the Store), a filter on github.com, a slow network.
  's=manque&go=diag&click=detail&os=windows',
  's=installe&go=diag&compte=api&os=windows',
  's=installe&go=diag&compte=gratuit',
  's=manque&go=diag&winget=absent&os=windows',
  's=manque&go=diag&reseau=filtre&os=windows',
  's=manque&go=diag&reseau=lent&os=windows',
  // Their reviews': a disk nearly full and other orange lines, a slow network and a disk; the end that asks
  // for Claude's login (« Dernière étape »).
  's=manque&go=diag&winget=absent&alerte=disk&os=windows',
  's=manque&go=diag&reseau=lent&alerte=disk&os=windows',
  's=installe&deco=1&go=login&click=check',
  // Their second reviews': what the diagnosis could not read (nothing to do), an account paid per message to
  // the end, « Presque prêt » on a Mac.
  's=manque&go=diag&click=detail&aveugle=1&os=windows',
  's=installe&go=done&compte=api&os=windows',
  's=manque&go=done&click=skip',
];

// Where Codex can be chosen (&porte=1, as the tests open it): what some screens must say, or not.
const has = (shots, start, text) => shots.some((s) => s.startsWith(`== ${start}`) && s.includes(text));
const last = (shots, start) => shots.filter((s) => s.startsWith(`== ${start}`)).pop() || '';
// The main button of that screen, the last time it showed ('' without one).
const mainOf = (shots, start) => shots.mains[shots.findLastIndex((s) => s.startsWith(`== ${start}`))] ?? '';
// What Bibo says on a screen: his title and his words, the two lines after the screen's name.
const bibo = (shot) => shot.split('\n').slice(1, 3).join('\n');
const CODEX_CHECKS = [
  {
    name: 'Codex on an API key, login put off: the end connects it, never opens the onboarding',
    query: 's=manque&porte=1&choix=codex&codex=billed&click=skip&go=done',
    ok: (shots) => {
      const done = last(shots, 'done');
      return done.includes('[Connecter Codex à mon compte ChatGPT]') && !done.includes('[Présente-toi à')
        && done.includes('clé API') && !done.includes('il te le demandera au premier lancement');
    },
  },
  {
    name: 'Codex not logged in, login put off: the end connects it first, and says to pick ChatGPT, never a key',
    query: 's=manque&porte=1&choix=codex&click=skip&go=done',
    ok: (shots) => {
      const done = last(shots, 'done');
      return done.includes('[Connecter Codex à mon compte ChatGPT]') && !done.includes('[Présente-toi à')
        && done.includes('Sign in with ChatGPT');
    },
  },
  {
    name: 'Claude preferred, both logins put off: the end connects Claude first, never opens the onboarding, the note says Codex\'s state',
    query: 's=manque&porte=1&choix=both&resume=claude&codex=billed&click=skip,skip&go=done',
    ok: (shots) => {
      const done = last(shots, 'done');
      return done.includes('Presque prêt') && done.includes('[Connecter mon compte Claude]') && !done.includes('[Présente-toi à') && done.includes('clé API');
    },
  },
  {
    name: 'A workspace account at the login: the wait ends and says why',
    query: 's=manque&porte=1&choix=codex&after=managed&go=done',
    ok: (shots) => has(shots, 'login', 'compte ChatGPT d’entreprise') && has(shots, 'login', 'espace personnel'),
  },
  {
    name: 'Windows, Codex\'s system settings open to other accounts: no login button, only later',
    query: 's=manque&porte=1&choix=codex&codex=system&os=windows&go=login',
    ok: (shots) => {
      const login = last(shots, 'login');
      return login.includes('ProgramData') && login.includes('[Plus tard]') && !login.includes('[Connecter Codex à mon compte ChatGPT]');
    },
  },
  {
    name: 'An API key at the login: one button, no command to type',
    query: 's=manque&porte=1&choix=codex&codex=billed&go=login',
    ok: (shots) => {
      const login = last(shots, 'login');
      return !login.includes('codex logout') && login.includes('ta connexion remplacera la clé') && login.includes('[Changer d’IA]');
    },
  },
  {
    name: 'Codex alone, a step fails before the brain: Codex can help once the brain is in, no login offered',
    query: 's=echec&porte=1&choix=codex&go=done',
    ok: (shots) => has(shots, 'install', 'Codex pourra m’aider une fois ton cerveau installé')
      && !shots.some((s) => s.includes('Oups, ça a coincé.') && s.includes('[Connecter Codex à mon compte ChatGPT]')),
  },
  {
    name: 'The AI changed from the diagnosis: Codex, then Claude, and the diagnosis is Claude\'s alone',
    query: 's=manque&porte=1&choix=codex,claude&click=change&go=diag&nth=2',
    ok: (shots, claude) => {
      const alone = claude.get('s=manque&go=done').find((s) => s.startsWith('== diag\nJ’ai fait le tour'));
      // Where Codex can be chosen, the advice is on the choice screen, not at the diagnosis.
      return Boolean(alone) && last(shots, 'diag') === alone.replace(/\nMon conseil[^\n]*/, '').replace('\n[Tout installer]', '\n[Changer d’IA]\n[Tout installer]');
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
    name: 'Codex\'s login that never comes: the wait, without Claude\'s hint (a code for its command window)',
    query: 's=manque&porte=1&choix=codex&go=done&jamais=1&hint=20',
    ok: (shots) => has(shots, 'login', 'J’attends ta connexion') && !shots.some((s) => s.includes('Si ton navigateur affiche un code')),
  },
  {
    name: 'Nothing to click at the end (Codex on settings no login fixes): Bibo may doze off, the control of the check that he stays awake',
    query: 's=manque&porte=1&choix=codex&codex=system&os=windows&click=skip&go=done',
    ok: (shots) => mainOf(shots, 'done') === '' && shots.asleep,
  },
  {
    name: 'Codex chosen on a new PC: its login is for later, as Claude\'s, never « un point à vérifier »',
    query: 's=manque&porte=1&choix=codex&click=detail&go=diag&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      return bibo(diag).startsWith('J’ai fait le tour\u00a0: ton PC est prêt.') && diag.includes('Connexion à Codex\u00a0: Plus tard');
    },
  },
  {
    name: 'One account rule everywhere: a personal ChatGPT plan, and both accounts for both',
    query: 's=manque&porte=1&go=choice',
    ok: (shots) => has(shots, 'welcome', 'abonnement ChatGPT personnel (pas un compte d’entreprise)')
      && has(shots, 'choice', 'abonnement ChatGPT personnel (pas un compte d’entreprise)') && has(shots, 'choice', 'Il te faut les deux comptes.'),
  },
];

// Claude alone, the window as shipped: what the reviews of the beginner's screens asked, said where a
// change of words in the golden file would not say it.
const CLAUDE_CHECKS = [
  {
    name: 'Back without Claude\'s login: the welcome asks for it, the guide comes once it is there',
    query: 's=installe&go=welcome&deco=1&click=next&os=windows',
    ok: (shots) => shots[0].includes('Il manque ton compte Claude') && shots[0].includes('[Connecter mon compte Claude]')
      && !shots[0].includes('[Guide-moi dans un logiciel]') && last(shots, 'welcome').includes('[Guide-moi dans un logiciel]'),
  },
  {
    name: 'No subscription yet: the welcome opens its page, and says to come back',
    query: 's=manque&go=welcome&click=plan',
    ok: (shots) => shots.links.join() === 'https://claude.ai/upgrade' && has(shots, 'welcome', 'choisis Claude Pro, puis reviens ici'),
  },
  {
    name: 'A warning on the network at the diagnosis: unfolded, not « prêt », « Revérifier » kept',
    query: 's=manque&go=diag&alerte=network&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      return diag.includes('un point à vérifier') && diag.includes('claude.ai ne répond pas') && diag.includes('essaie depuis chez toi') && diag.includes('[Masquer le détail]')
        && diag.includes('[Revérifier]') && !diag.includes('est prêt');
    },
  },
  {
    name: 'Windows: « Voir mes notes » says the folder in full, never masked to « ~ »',
    query: 's=installe&go=done&click=vault&os=windows',
    ok: (shots) => last(shots, 'done').includes('Tes notes sont dans le dossier C:\\Users\\aminata\\Documents\\Brain.')
      && !shots.some((s) => s.includes('~\\Documents')),
  },
  {
    name: '« Presque prêt »: one button, the subscription\'s page, nothing that takes a bank card',
    query: 's=manque&go=done&click=skip&os=windows',
    ok: (shots) => {
      const done = last(shots, 'done');
      // The account's steps first: the import's sentence waits (its screen says it), its link stays (it asks for
      // the account when it needs it, and terrain_app.py goes through it without one).
      return done.includes('[Connecter mon compte Claude]') && done.includes('[Je n’ai pas encore Claude Pro]') && !done.includes('[Recherche par le sens]')
        && !done.includes('J’attends ta connexion') && !done.includes('Tu utilisais ChatGPT ou Claude') && done.includes('[Importer ma mémoire]')
        && done.includes('[Voir mes notes]') && done.includes('Pour me retrouver plus tard');
    },
  },
  {
    name: '« Presque prêt » on a Mac fits: the account\'s steps beside a small Bibo, as the login\'s screen; the end that is ready keeps him big',
    query: 's=manque&go=done&click=skip',
    ok: (shots) => {
      const i = shots.findLastIndex((s) => s.startsWith('== done'));
      return shots[i].includes('Presque prêt') && shots[i].includes('glisse d’abord l’icône') && shots.bigs[i] === false
        && shots.bigs[shots.findIndex((s) => s.startsWith('== welcome'))] === true;
    },
  },
  {
    name: 'An account paid per message to the end: « Presque prêt », who to ask, never the guide it refuses (nor as the main button)',
    query: 's=installe&go=done&compte=api&os=windows',
    ok: (shots) => {
      const done = last(shots, 'done');
      return done.includes('Presque prêt\u00a0: il manque ton abonnement Claude') && done.includes('la personne qui t’a conseillé Bibo')
        && !done.includes('[Guide-moi dans un logiciel]') && !done.includes('Je peux te guider') && mainOf(shots, 'done') === 'Présente-toi à Claude (15 min)';
    },
  },
  {
    name: 'What the diagnosis could not read (the version, the free space): said, nothing to do, never « un point à vérifier » nor how to make room',
    query: 's=manque&go=diag&click=detail&aveugle=1&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      return bibo(diag).startsWith('J’ai fait le tour\u00a0: ton PC est prêt.') && diag.includes('Espace disque\u00a0: À savoir')
        && diag.includes('Version de Windows\u00a0: À savoir') && !diag.includes('Corbeille') && !diag.includes('orange');
    },
  },
  {
    name: 'Claude\'s login: its command window stays open, and the hint says what to do with a code',
    query: 's=login-early&go=done&jamais=1&hint=20',
    ok: (shots) => has(shots, 'install', 'laisse-la ouverte') && has(shots, 'install', 'Si ton navigateur affiche un code')
      && has(shots, 'install', 'Cmd + V'),
  },
  {
    name: 'Windows: the authorization is said in Python\'s section and in Obsidian\'s, not between',
    query: 's=manque&go=done&os=windows',
    ok: (shots) => has(shots, 'install', 'pour installer Git ou Python') && has(shots, 'install', 'pour installer Obsidian')
      && !shots.some((s) => s.includes('(3 sur 13)') && s.includes('autorisation')),
  },
  {
    name: 'The computer kept awake while a step runs, not said again under a failure',
    query: 's=echec&go=done&fail=brain&os=windows&guide=1',
    ok: (shots) => has(shots, 'install', 'sur le chargeur') && shots.filter((s) => s.includes('Oups, ça a coincé')).every((s) => !s.includes('jusqu’à la fin')),
  },
  {
    name: 'D1, back on an install that stopped half way (guide_offert "dossier"): not finished, « Finir l’installation » first, the guide already there, Claude\'s login not read again',
    query: 's=installe&go=welcome&incomplet=1&os=windows',
    ok: (shots) => {
      const welcome = last(shots, 'welcome');
      // Not only the memory may be missing (the hooks, the skills, the choice of the AI): nothing more is named.
      return welcome.includes('Ton installation n’est pas finie. Je peux déjà te guider dans un logiciel.')
        && !welcome.includes('Pour la finir') && mainOf(shots, 'welcome') === 'Finir l’installation' && welcome.includes('[Guide-moi dans un logiciel]') && shots.auths === 0;
    },
  },
  {
    name: 'D1 under a failure: the folder there and Claude logged in, the guide is offered',
    query: 's=echec&go=done&fail=brain&os=windows&guide=1',
    ok: (shots) => has(shots, 'install', '[Essayer le guide quand même]'),
  },
  {
    name: '« Plus tard » once for Claude\'s login: the end says what is missing, never asks « Plus tard » again',
    query: 's=manque&go=done&click=skip&os=windows',
    ok: (shots) => last(shots, 'done').includes('Presque prêt') && !shots.some((s) => s.startsWith('== login') && s.includes('[Plus tard]')),
  },
  {
    name: 'Bibo stays awake where a click is awaited: the welcome, the end that is ready',
    query: 's=manque&go=welcome&version=0.1.76&os=windows',
    ok: (shots) => mainOf(shots, 'welcome') === 'Vérifier mon PC' && !shots.asleep,
  },
  {
    name: 'Bibo stays awake on the end that is ready, the guide its main button',
    query: 's=installe&go=done',
    ok: (shots) => mainOf(shots, 'done') === 'Guide-moi dans un logiciel' && !shots.asleep,
  },
  {
    name: 'The diagnosis says how long and how much to download (diagnose.rs\' headline), never « 10 à 20 minutes »',
    query: 's=manque&go=diag&click=detail&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      return diag.includes('environ 15\u00a0minutes et 900\u00a0Mo à télécharger') && !shots.some((s) => s.startsWith('== diag') && s.includes('10 à 20 minutes'))
        && diag.includes('Connexion à Claude\u00a0: Plus tard');
    },
  },
  {
    name: 'An orange line unfolds the list: an account paid per message, said with who to ask, « Revérifier » there',
    query: 's=installe&go=diag&compte=api&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      // All is installed, the account is not a subscription: never « a tout ce qu'il faut », nor the advice of a subscription (advice.rs: claude_vu).
      return diag.includes('[Masquer le détail]') && diag.includes('payé à chaque message') && diag.includes('[Revérifier]') && bibo(diag).includes('Lis la ligne en orange')
        && !diag.includes('a tout ce qu’il faut') && diag.includes('Tu utilises déjà Claude') && !diag.includes('connecté à ton compte Claude');
    },
  },
  {
    name: 'A claude.ai account without its plan said: unfolded, the price of Claude Pro',
    query: 's=installe&go=diag&compte=gratuit',
    ok: (shots) => last(shots, 'diag').includes('[Masquer le détail]') && last(shots, 'diag').includes('Claude Pro (environ 20\u00a0€ par mois)'),
  },
  {
    name: 'No winget: one point, the list unfolded, its line says how to open the Store and what to do there (no button open_link would refuse); Obsidian\'s line follows from it',
    query: 's=manque&go=diag&winget=absent&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      const line = diag.split('\n').find((l) => l.startsWith('Outil d’installation de Microsoft')) || '';
      return bibo(diag).startsWith('J’ai fait le tour\u00a0: un point à vérifier.') && diag.includes('[Masquer le détail]') && diag.includes('[Revérifier]')
        && line.includes('Ouvre le Microsoft Store\u00a0: dans le menu Démarrer (Start), tape «\u00a0Microsoft Store\u00a0».') && line.includes('App Installer')
        && diag.includes('Obsidian (pour lire tes notes)\u00a0: À savoir') && !diag.includes('[Ouvrir le Microsoft Store') && !diag.includes('puis tape');
    },
  },
  {
    name: 'A filter on github.com: blocked, the headline says the red line tells what to do',
    query: 's=manque&go=diag&reseau=filtre&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      return diag.includes('Je ne peux pas installer pour l’instant.') && diag.includes('Une chose bloque l’installation') && diag.includes('bloque github.com')
        && mainOf(shots, 'diag') === 'Revérifier';
    },
  },
  {
    name: 'A slow network: how long first, the Wi-Fi once, install now or wait, « Revérifier »; never « 10 à 20 minutes »',
    query: 's=manque&go=diag&reseau=lent&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      const said = bibo(diag);
      return said.includes('un point à vérifier.\nTa connexion est lente. J’installe') && said.includes('prévois environ 2\u00a0h\u00a040') && said.includes('attends d’avoir un Wi-Fi')
        && said.endsWith('Tu peux installer maintenant et laisser ton PC allumé, ou attendre un réseau plus rapide.')
        && diag.includes('[Revérifier]') && !diag.includes('10 à 20')
        // How long and the Wi-Fi once on the screen: the network's line only says it is slow.
        && diag.split('Wi-Fi').length === 2 && diag.split('prévois').length === 2 && diag.includes('github.com et claude.ai répondent, mais lentement.');
    },
  },
  {
    name: 'A slow network and a disk nearly full: both counted, the slowness said, the disk\'s advice on its line, the Wi-Fi once',
    query: 's=manque&go=diag&reseau=lent&alerte=disk&os=windows',
    ok: (shots) => {
      const diag = last(shots, 'diag');
      const said = bibo(diag);
      return said.startsWith('J’ai fait le tour\u00a0: deux points à vérifier.\nTa connexion est lente.') && said.includes('Lis les lignes en orange')
        && diag.split('Wi-Fi').length === 2 && diag.includes('c’est juste. Pour faire de la place, vide la Corbeille (Recycle Bin)');
    },
  },
  {
    name: 'A disk nearly full and no winget: both counted (not Obsidian\'s line, which follows from winget\'s), Bibo sends to them, the time before the choice',
    query: 's=manque&go=diag&winget=absent&alerte=disk&os=windows',
    ok: (shots) => {
      const said = bibo(last(shots, 'diag'));
      return said.startsWith('J’ai fait le tour\u00a0: deux points à vérifier.\nJ’installe') && said.includes('Lis les lignes en orange, ci-dessous.')
        && said.indexOf('J’installe') < said.indexOf('Tu peux installer quand même');
    },
  },
  {
    name: 'No « 10 à 20 minutes » from the welcome to the end on a PC: the diagnosis says how long',
    query: 's=manque&go=done&os=windows',
    ok: (shots) => !shots.some((s) => s.includes('10 à 20')) && has(shots, 'welcome', 'combien de temps'),
  },
  {
    name: 'No « 10 à 20 minutes » on a Mac either, whose diagnosis says « environ 25 minutes »',
    query: 's=manque&go=done',
    ok: (shots) => !shots.some((s) => s.includes('10 à 20')) && has(shots, 'diag', 'environ 25\u00a0minutes'),
  },
  {
    name: 'Back, Claude Code slow to say its login: the welcome within 3 s, the guide offered, never « Il manque ton compte »',
    query: 's=installe&go=welcome&lent=1&os=windows',
    ok: (shots) => {
      const first = shots.findIndex((s) => s.startsWith('== welcome') && s.includes('\n['));
      return first >= 0 && shots.at[first] <= 3100 && shots[first].includes('[Guide-moi dans un logiciel]')
        && !shots.some((s) => s.includes('Il manque ton compte'));
    },
  },
  {
    name: '« Voir mes notes » without Obsidian: the folder all the same, and where to open it',
    query: 's=installe&go=done&click=vault&obsidian=absent&os=windows',
    ok: (shots) => {
      const done = last(shots, 'done');
      return done.includes('Obsidian n’est pas encore installé') && done.includes('C:\\Users\\aminata\\Documents\\Brain')
        && done.includes('Explorateur de fichiers (File Explorer)') && done.includes('obsidian.md');
    },
  },
  {
    name: 'A disk nearly full at the diagnosis: what to do, the Trash named as the Mac names it',
    query: 's=manque&go=diag&alerte=disk',
    ok: (shots) => last(shots, 'diag').includes('vide la Corbeille (Trash) ou supprime de gros fichiers'),
  },
  {
    name: 'The import says what is masked, and what may pass',
    query: 's=installe&go=import-done&click=import&colle=1',
    ok: (shots) => shots.some((s) => s.includes('Un code glissé dans une phrase peut passer')),
  },
];

// ── The screens bibo.js names, and the ones the windows show ───────────────

// In bibo.js: RULES' screens, the lists FAILED and CALM, OVERRIDES' keys, and each « screen === '…' ».
function biboScreens(text) {
  const block = (name, open, close) => {
    const from = text.indexOf(`const ${name} = ${open}`);
    if (from < 0) throw new Error(`bibo.js has no ${name}: this check reads it`);
    return text.slice(from, text.indexOf(close, from));
  };
  const names = new Set();
  for (const m of block('RULES', '[', '\n  ];').matchAll(/^\s*\['([\w-]+)'/gm)) names.add(m[1]);
  for (const list of ['FAILED', 'CALM']) for (const m of block(list, '[', '];').matchAll(/'([\w-]+)'/g)) names.add(m[1]);
  for (const m of block('OVERRIDES', '{', '};').matchAll(/(?:'([\w-]+)'|(\w+)):/g)) names.add(m[1] || m[2]);
  for (const m of text.matchAll(/screen === '([\w-]+)'/g)) names.add(m[1]);
  names.delete('*');
  return names;
}

// The names in each screen(…) of app.js and guide.js (a choice of two included), and app.js' install
// screen under its names (renderInstall).
const shownScreens = new Set(['install']);
for (const file of ['app.js', 'guide.js']) {
  const text = fs.readFileSync(path.join(HERE, '..', 'src', file), 'utf8');
  for (const call of text.matchAll(/\b(?:screen|renderInstall)\(([^)\n]*)\)/g)) {
    for (const m of call[1].matchAll(/'([\w-]+)'/g)) shownScreens.add(m[1]);
  }
}

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
  // As the browser's: the attributes disabled and hidden are the properties too (h() sets them so).
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'disabled' || k === 'hidden') this[k] = true; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; if (k === 'disabled' || k === 'hidden') this[k] = false; }
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
  // When each screen first showed, in the run's own milliseconds, and its main button then.
  shots.at = [];
  shots.mains = [];
  // Whether it showed in the big layout (Bibo big, the whole centered: the welcome, the end).
  shots.bigs = [];
  // The install bar's widths, each new one in turn (bibo.js draws the same one on Bibo's box).
  const bars = [];
  const snap = () => {
    const name = doc.body.dataset.screen;
    if (!name) return;
    const fill = doc.body.querySelector('.bar-fill');
    if (name === 'install' && fill && fill.style.width && fill.style.width !== bars[bars.length - 1]) bars.push(fill.style.width);
    // The guide's window made wide or reduced: said next to the screen's name.
    const size = ['large', 'reduit'].find((c) => doc.body.classList.contains(c));
    const text = `== ${name}${size ? ` (${size})` : ''}${visible(doc.body).replace(/\n\s*\n/g, '\n')}`;
    if (!shots.includes(text)) {
      shots.push(text);
      shots.at.push(t.now());
      shots.mains.push(doc.body.querySelector('.actions')?.querySelector('[data-act="next"]')?.textContent ?? '');
      shots.bigs.push(doc.body.classList.contains('big'));
    }
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
  shots.bars = bars.map((w) => parseFloat(w));
  shots.links = ctx.__liens || [];
  // The links steps.rs would refuse (off its LINK_HOSTS): a button that can never open.
  shots.refusedLinks = ctx.__refusLiens || [];
  // How many times the window read Claude's login, and whether Bibo dozed off by the end (app.js' own sleep).
  shots.auths = ctx.__auths || 0;
  shots.asleep = Boolean(doc.body.querySelector('.hero')?.children[0]?.classList.contains('asleep'));
  // The mock's copy of diagnose.rs' measures (sizes, work, speed, the two formats).
  shots.mesures = ctx.__mesures || null;
  return shots;
}

// What differs between the mock's measures (window.__mesures, on a Mac, a PC and Linux) and diagnose.rs'.
function measuresDiffer(rust, got) {
  const faults = [];
  const body = (name) => (rust.match(new RegExp(`fn ${name}\\([^)]*\\)[^{]*\\{([\\s\\S]*?)\\n\\}`)) || [])[1] || '';
  const pieces = [...body('piece_mb').matchAll(/"(\w+)"(?: if cfg!\((?:target_os = "(\w+)"|(\w+))\))? => (\d+),/g)];
  const work = [...body('minutes').matchAll(/((?:"\w+"(?: \| )?)+) => (\d+),/g)].flatMap((m) => [...m[1].matchAll(/"(\w+)"/g)].map((x) => [x[1], Number(m[2])]));
  const usual = Number((rust.match(/const USUAL_BYTES_PER_S: f64 = ([\d_.]+);/) || [])[1]?.replaceAll('_', ''));
  const table = (fn) => {
    const m = rust.match(new RegExp(`let \\w+ = \\|n\\| ${fn}\\(n\\)\\.replace\\('\\\\u\\{a0\\}', "_"\\);\\s*assert_eq!\\(\\[([^\\]]*)\\], \\[([^\\]]*)\\]\\);`));
    return m ? [[...m[1].matchAll(/\((\d+)\)/g)].map((x) => Number(x[1])), [...m[2].matchAll(/"([^"]*)"/g)].map((x) => x[1])] : [[], []];
  };
  if (!pieces.length || !work.length || !usual) return ['diagnose.rs\' piece_mb, minutes or USUAL_BYTES_PER_S not found'];
  for (const [system, q] of [['macos', 's=manque&go=done'], ['windows', 's=manque&go=done&os=windows'], ['linux', 's=echec&go=done&os=linux']]) {
    const m = got.get(q)?.mesures;
    if (!m) {
      faults.push(`no window.__mesures in ${q}`);
      continue;
    }
    const rustMb = {};
    for (const [, id, os, cfgWord, mb] of pieces) {
      const on = os || (cfgWord === 'windows' ? 'windows' : cfgWord);
      if (!on || on === system) rustMb[id] ??= Number(mb);
    }
    for (const id of new Set([...Object.keys(rustMb), ...Object.keys(m.MB)])) {
      if (rustMb[id] !== m.MB[id]) faults.push(`${system}: ${id} downloads ${m.MB[id]} Mo, diagnose.rs ${rustMb[id]}`);
    }
    for (const [id, n] of work) if ((m.WORK[id] ?? 1) !== n) faults.push(`${id}'s work: ${m.WORK[id] ?? 1} min, diagnose.rs ${n}`);
    for (const id of Object.keys(m.WORK)) if (!work.some(([w]) => w === id)) faults.push(`${id}'s work: ${m.WORK[id]} min, diagnose.rs 1`);
    if (m.USUAL !== usual) faults.push(`the usual speed: ${m.USUAL}, diagnose.rs ${usual}`);
    for (const [fn, mine] of [['fmt_minutes', m.fmtMinutes], ['fmt_mb', m.fmtMb]]) {
      const [ins, outs] = table(fn);
      if (!ins.length || ins.length !== outs.length) faults.push(`diagnose.rs' test of ${fn} not found`);
      ins.forEach((n, i) => {
        const said = mine(n).replace(/\u00a0/g, '_');
        if (said !== outs[i]) faults.push(`${fn}(${n}): « ${said} », diagnose.rs « ${outs[i]} »`);
      });
    }
  }
  return [...new Set(faults)];
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
    fs.writeFileSync(GUIDE_GOLDEN, render(guide, "The guide's screens"));
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
  for (const c of CLAUDE_CHECKS) {
    const shots = got.get(c.query) || await screens(mockText, appScripts, c.query);
    if (c.ok(shots)) continue;
    differ += 1;
    console.log(`FAIL ${c.name}\n  ${c.query}\n${shots.slice(-3).join('\n\n')}\n`);
  }
}
differ += compare(GOLDEN, want, got);
// The mock's LINK_HOSTS are steps.rs' own: the guard under it holds only then.
const hostsOf = (m) => (m ? [...m[1].matchAll(/["']([^"']+)["']/g)].map((x) => x[1]).join() : '');
const rustHosts = hostsOf(fs.readFileSync(path.join(HERE, '..', 'src-tauri', 'src', 'steps.rs'), 'utf8').match(/const LINK_HOSTS: &\[&str\] = &\[([^\]]*)\]/));
if (!rustHosts || rustHosts !== hostsOf(mockText.match(/const LINK_HOSTS = \[([^\]]*)\]/))) {
  differ += 1;
  console.log(`FAIL mock.html's LINK_HOSTS are not steps.rs' (${rustHosts})`);
}
// The mock counts what diagnose.rs counts: each piece's download and each step's work (piece_mb, minutes), the usual
// speed, and its two formats give what diagnose.rs' own test says (durations_and_sizes_read_simply).
const diagnoseRs = fs.readFileSync(path.join(HERE, '..', 'src-tauri', 'src', 'diagnose.rs'), 'utf8');
for (const fault of measuresDiffer(diagnoseRs, got)) {
  differ += 1;
  console.log(`FAIL mock.html's measures are not diagnose.rs': ${fault}`);
}
// « 10 à 20 minutes » nowhere: not in the install's Details (steps.rs' log), not on the site that sends here.
// The diagnosis says how long, from what is left and the network.
for (const file of ['app/src-tauri/src/steps.rs', 'site/index.html']) {
  if (!fs.readFileSync(path.join(HERE, '..', '..', file), 'utf8').includes('10 à 20')) continue;
  differ += 1;
  console.log(`FAIL ${file} still says « 10 à 20 minutes »: the diagnosis says how long`);
}
// A link the window asks for opens: steps.rs' open_link refuses any other host, and the button would only ever
// say « Je n'ai pas pu l'ouvrir » (the Store's page, once).
for (const [q, shots] of got) {
  if (!shots.refusedLinks.length) continue;
  differ += 1;
  console.log(`FAIL ${q}: open_link refuses ${shots.refusedLinks.join(', ')} (steps.rs' LINK_HOSTS)`);
}
// Windows' install is Claude Code, then the brain for most of the time: the bar moves with each of
// install.ps1's sections (« ▸ … (n sur 13)… »), never back, from 0 to 100. A Mac's moves with its steps.
for (const [q, least] of [['s=manque&go=done&os=windows', 6], ['s=manque&go=done', 5]]) {
  const bars = got.get(q).bars;
  const forward = bars.every((w, i) => i === 0 || w > bars[i - 1]);
  if (forward && bars.length >= least && bars[0] === 0 && bars[bars.length - 1] === 100) continue;
  differ += 1;
  console.log(`FAIL ${q}: the install bar goes ${bars.join(' → ')} %, not forward from 0 to 100 in ${least} moves at least`);
}
// bibo.js (not loaded here) builds on these parts of the drawing: one renamed, and Bibo stands still.
for (const part of ['b-body', 'b-brain', 'b-front', 'b-bubbles']) {
  if (uiText.includes(`class="${part}"`)) continue;
  differ += 1;
  console.log(`FAIL BIBO_SVG has no class="${part}", which bibo.js needs`);
}
// bibo.js acts out the screens it names: a screen renamed in app.js or guide.js, and its scene is lost
// without a word. Each name it uses must be one the windows show.
for (const name of biboScreens(fs.readFileSync(path.join(HERE, '..', 'src', 'bibo.js'), 'utf8'))) {
  if (shownScreens.has(name)) continue;
  differ += 1;
  console.log(`FAIL bibo.js names the screen « ${name} », which neither app.js nor guide.js shows`);
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
console.log(differ ? `${differ} differ or fail` : `${got.size} scenarios, the same screens; ${args.includes('--app') ? 0 : CLAUDE_CHECKS.length} checks of the beginner's screens and ${args.includes('--app') ? 0 : CODEX_CHECKS.length} of Codex's pass; ${guide.size} scenarios of the guide, the same screens`);
process.exit(differ ? 1 : 0);
