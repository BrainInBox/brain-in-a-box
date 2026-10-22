// The download page. Without this script everything still works: the links download the newest
// preview from app-latest, and the three systems are shown one after the other.
'use strict';

const REPO = 'BrainInBox/brain-in-a-box';
const API = `https://api.github.com/repos/${REPO}/releases?per_page=30`;
const RELEASE_PAGE = `https://github.com/${REPO}/releases/tag/`;
// The release .github/workflows/release.yml refreshes with each preview: the same files, named
// without their version. The buttons point there, whether GitHub's API answers or not.
const LATEST = `https://github.com/${REPO}/releases/download/app-latest/`;
const TAG = /^app-v(\d+)\.(\d+)\.(\d+)-preview\.(\d+)$/;
// How each file of an app preview ends (release.yml); under app-latest, Brain-in-a-Box_ and this.
const FILES = {
  mac: 'macos-universal.dmg',
  windows: 'windows-x64-setup.exe',
  appimage: 'linux-x86_64.AppImage',
  deb: 'linux-amd64.deb',
};
const MAIN = {
  windows: { label: 'Télécharger pour Windows', file: 'windows', meta: 'Windows 10 ou 11 · 64 bits · gratuit' },
  mac: { label: 'Télécharger pour Mac', file: 'mac', meta: 'macOS 13 ou plus récent · puce Apple et Intel · gratuit' },
  linux: { label: 'Télécharger pour Linux', file: 'appimage', meta: 'AppImage · Ubuntu 22.04+, Debian 12 · x86-64' },
};
// The address a phone sends to the computer.
const HOME = 'https://brain-in-a-box.vercel.app/';

document.documentElement.classList.add('js');

function detectSystem() {
  const ua = navigator.userAgent;
  const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
  // An iPad asks for the desktop site with a Mac's user agent.
  if (/Android|iPhone|iPad|iPod/i.test(ua) || (/Mac/i.test(platform) && navigator.maxTouchPoints > 1)) return 'mobile';
  if (/Win/i.test(platform) || /Windows/.test(ua)) return 'windows';
  if (/Mac/i.test(platform) || /Mac OS X/.test(ua)) return 'mac';
  if (/Linux|X11/i.test(platform) || /Linux/.test(ua)) return 'linux';
  return 'unknown';
}

// ── The newest app preview, from GitHub's public API: its number, date and sizes ──
// Only what the page says: the links never depend on it (60 calls an hour per address).

function compareTags(a, b) {
  for (let i = 0; i < 4; i++) {
    if (a.n[i] !== b.n[i]) return b.n[i] - a.n[i];
  }
  return 0;
}

async function newestPreview() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const res = await fetch(API, {
      signal: ctrl.signal,
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      headers: { Accept: 'application/vnd.github+json' },
    });
    if (!res.ok) return null;
    const releases = await res.json();
    if (!Array.isArray(releases)) return null;
    const previews = releases
      .filter((r) => r && !r.draft && typeof r.tag_name === 'string' && TAG.test(r.tag_name))
      .map((r) => ({ r, n: TAG.exec(r.tag_name).slice(1).map(Number) }))
      .sort(compareTags);
    if (!previews.length) return null;
    const { r, n } = previews[0];
    const sizes = {};
    for (const [kind, suffix] of Object.entries(FILES)) {
      const asset = (r.assets || []).find((a) => a && typeof a.name === 'string' && a.name.endsWith(`_${suffix}`));
      if (asset) sizes[kind] = Number(asset.size) || 0;
    }
    const page = typeof r.html_url === 'string' && r.html_url === RELEASE_PAGE + encodeURIComponent(r.tag_name) ? r.html_url : null;
    return { number: n[3], date: r.published_at, sizes, page };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function megabytes(bytes) {
  const mb = bytes / 1e6;
  // "2,6 Mo", "8 Mo", "82 Mo"
  return mb < 10 ? `${mb.toFixed(1).replace('.0', '').replace('.', ',')} Mo` : `${Math.round(mb)} Mo`;
}

function frenchDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' }).format(d);
}

function showVersion(release) {
  const date = frenchDate(release.date);
  const version = document.getElementById('version');
  version.textContent = `Préversion n°\u00a0${release.number}${date ? `, du ${date}` : ''}`;
  if (release.page) {
    const link = version.nextElementSibling;
    if (link && link.tagName === 'A') link.href = release.page;
  }
}

// The big buttons (top of the page, and the last call at the bottom) are for the visitor's system.
function mainButtons(system, release) {
  if (system === 'mobile') {
    // Neither the app nor the command line installs on a phone: the link goes to the computer.
    for (const el of document.querySelectorAll('[data-dl-main], [data-top-dl], .dl-actions, [data-dl-main-meta], .dl-others, .dl-version, [data-dl-warn], .dl-privacy')) el.hidden = true;
    for (const msg of document.querySelectorAll('[data-dl-mobile], [data-phone]')) msg.hidden = false;
    return;
  }
  const main = MAIN[system];
  if (!main) return;
  const size = release && release.sizes[main.file];
  for (const label of document.querySelectorAll('[data-dl-main-label]')) label.textContent = main.label;
  for (const el of document.querySelectorAll('[data-dl-main-meta]')) el.textContent = size ? `${main.meta} · ${megabytes(size)}` : main.meta;
  for (const btn of document.querySelectorAll('[data-dl-main]')) btn.href = `${LATEST}Brain-in-a-Box_${FILES[main.file]}`;
  // The others under the button: the one the button already gives goes away.
  const own = document.querySelector(`.dl-others li[data-for="${system}"]`);
  if (own) own.hidden = true;
  document.getElementById('dl-others-label').textContent = 'Autres systèmes\u00a0:';
}

// ── Once the download has started: what comes next, for this system ───────────

function nextSteps(system) {
  const next = document.querySelector('[data-next]');
  if (!next || !MAIN[system]) return;
  for (const list of next.querySelectorAll('[data-next-for]')) list.hidden = list.dataset.nextFor !== system;
  for (const btn of document.querySelectorAll('[data-dl-main]')) {
    btn.addEventListener('click', () => {
      next.hidden = false;
      for (const warn of document.querySelectorAll('[data-dl-warn]')) warn.hidden = true;
      // The steps come into view, under the button or from the bottom of the page; the download goes on.
      next.focus({ preventScroll: true });
      const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      next.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: btn.closest('.hero') ? 'nearest' : 'center' });
    });
  }
}

// ── On a phone: send the link to oneself ─────────────────────────────────────

function phoneLink() {
  const done = document.querySelector('[data-copy-done]');
  const share = document.querySelector('[data-share]');
  const copy = document.querySelector('[data-copy-link]');
  const say = (text) => { if (done) done.textContent = text; };
  if (share && navigator.share) {
    share.hidden = false;
    share.addEventListener('click', async () => {
      try {
        await navigator.share({ title: 'Brain in a Box', text: 'Bibo, à installer sur mon ordinateur\u00a0:', url: HOME });
      } catch {
        // Closed without sharing: nothing to say.
      }
    });
  }
  if (copy) {
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(HOME);
        say('Lien copié. Colle-le dans un message pour toi (WhatsApp, e-mail), puis ouvre-le sur ton ordinateur.');
      } catch {
        say('Je n’ai pas pu copier. Recopie l’adresse ci-dessus, ou fais-en une capture.');
      }
    });
  }
}

// ── Tabs: the links to each system become tabs ────────────────────────────
// Two sets: the install guide (#installer-mac…) and the command line (#cli-mac…).

function setupTabs(nav, preferred) {
  const prefix = nav.dataset.tabs;
  const section = nav.closest('section');
  const panels = [];
  const tabs = [];
  nav.setAttribute('role', 'tablist');
  for (const link of [...nav.querySelectorAll('a[href^="#"]')]) {
    const panel = document.getElementById(link.hash.slice(1));
    if (!panel) continue;
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.id = `tab-${panel.id}`;
    tab.textContent = link.textContent;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', panel.id);
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', tab.id);
    panel.tabIndex = 0;
    link.replaceWith(tab);
    tabs.push(tab);
    panels.push(panel);
  }
  if (!tabs.length) return;

  // A tab the visitor picks goes in the address too: a link to that system
  // (#installer-windows) must still change something when clicked later.
  function select(id, { focus = false, remember = false } = {}) {
    if (remember) history.replaceState(null, '', `#${id}`);
    tabs.forEach((tab, i) => {
      const on = panels[i].id === id;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
      panels[i].hidden = !on;
      if (on && focus) tab.focus();
    });
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => select(panels[i].id, { remember: true }));
    tab.addEventListener('keydown', (e) => {
      const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -i, End: tabs.length - 1 - i };
      if (!(e.key in moves)) return;
      e.preventDefault();
      const next = (i + moves[e.key] + tabs.length) % tabs.length;
      select(panels[next].id, { focus: true, remember: true });
    });
  });

  // A link to one system (#installer-windows) opens its tab, then shows it.
  function fromHash() {
    const id = location.hash.slice(1);
    if (!panels.some((p) => p.id === id)) return false;
    select(id);
    section.scrollIntoView();
    return true;
  }
  window.addEventListener('hashchange', fromHash);
  if (!fromHash()) select(`${prefix}-${preferred}`);
}

// A link to a question of the FAQ (#verifier) opens it.
function openLinkedDetails() {
  const el = location.hash.length > 1 && document.getElementById(location.hash.slice(1));
  if (el && el.tagName === 'DETAILS') el.open = true;
}

// ── A copy button on each command ──────────────────────────────────────────

function addCopyButtons() {
  if (!navigator.clipboard) return;
  for (const pre of document.querySelectorAll('pre.cmd')) {
    const code = pre.querySelector('code');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'copy';
    btn.textContent = 'Copier';
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(code.textContent);
        btn.textContent = 'Copié';
      } catch {
        btn.textContent = 'Sélectionne le texte';
      }
      setTimeout(() => { btn.textContent = 'Copier'; }, 2000);
    });
    // Outside the <pre>, which scrolls sideways: the button stays put above the command.
    const wrap = document.createElement('div');
    wrap.className = 'cmd-wrap';
    pre.before(wrap);
    wrap.append(pre, btn);
  }
}

// ── The hero's Bibo looks at the pointer ───────────────────────────────────
// As in the app (app/src/app.js, aim()): the pupils lean towards the pointer, all the way once it is two
// Bibos away; style.css eases them there. Not on a touch screen, nor for a visitor who asks for less motion.

function followPointer() {
  const bibo = document.querySelector('.hero-bibo');
  if (!bibo || !window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
  const still = window.matchMedia('(prefers-reduced-motion: reduce)');
  const look = (x, y) => {
    bibo.style.setProperty('--gx', `${x.toFixed(2)}px`);
    bibo.style.setProperty('--gy', `${y.toFixed(2)}px`);
  };
  let x = 0;
  let y = 0;
  let frame = 0;
  const aim = () => {
    frame = 0;
    const r = bibo.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) return;
    // The eyes sit in the middle, at y 102 of the viewBox (14 26 212 212).
    const dx = x - (r.left + r.width / 2);
    const dy = y - (r.top + (r.height * 76) / 212);
    const d = Math.hypot(dx, dy) || 1;
    const k = Math.min(1, d / (2 * r.width));
    look(k * ((3.5 * dx) / d - 1), k * ((4 * dy) / d - 2));
  };
  document.addEventListener('pointermove', (e) => {
    if (still.matches || e.pointerType !== 'mouse') return;
    x = e.clientX;
    y = e.clientY;
    if (!frame) frame = requestAnimationFrame(aim);
  }, { passive: true });
  // The pointer left the window: eyes back to the middle.
  document.addEventListener('mouseout', (e) => {
    if (!e.relatedTarget) look(0, 0);
  });
}

async function start() {
  const system = detectSystem();
  followPointer();
  // A phone, or a system not recognized: Windows first, as most of the people Bibo is for.
  for (const nav of document.querySelectorAll('[data-tabs]')) setupTabs(nav, MAIN[system] ? system : 'windows');
  addCopyButtons();
  openLinkedDetails();
  window.addEventListener('hashchange', openLinkedDetails);
  mainButtons(system, null);
  nextSteps(system);
  phoneLink();
  const release = await newestPreview();
  if (!release) return;
  showVersion(release);
  mainButtons(system, release);
}

start();
