// The download page. Without this script everything still works: the links go to
// the Releases page and the three systems are shown one after the other.
'use strict';

const REPO = 'BrainInBox/brain-in-a-box';
const API = `https://api.github.com/repos/${REPO}/releases?per_page=30`;
const DOWNLOADS = `https://github.com/${REPO}/releases/download/`;
const TAG = /^app-v(\d+)\.(\d+)\.(\d+)-preview\.(\d+)$/;
// How each file of an app preview is named (.github/workflows/release.yml).
const FILES = {
  mac: '_macos-universal.dmg',
  windows: '_windows-x64-setup.exe',
  appimage: '_linux-x86_64.AppImage',
  deb: '_linux-amd64.deb',
  sums: 'SHA256SUMS',
};
const MAIN = {
  mac: { label: 'Télécharger pour Mac', file: 'mac', meta: 'macOS 13 ou plus récent · puce Apple et Intel' },
  windows: { label: 'Télécharger pour Windows', file: 'windows', meta: 'Windows 10 ou 11 · 64 bits' },
  linux: { label: 'Télécharger pour Linux', file: 'appimage', meta: 'AppImage · Ubuntu 22.04+, Debian 12 · x86-64' },
};

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

// ── The newest app preview, from GitHub's public API ───────────────────────

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
    const prefix = DOWNLOADS + encodeURIComponent(r.tag_name) + '/';
    const files = {};
    for (const [kind, suffix] of Object.entries(FILES)) {
      const asset = (r.assets || []).find((a) => a && typeof a.name === 'string' && a.name.endsWith(suffix)
        && typeof a.browser_download_url === 'string' && a.browser_download_url.startsWith(prefix));
      if (asset) files[kind] = { url: asset.browser_download_url, name: asset.name, size: Number(asset.size) || 0 };
    }
    return { version: `${n[0]}.${n[1]}.${n[2]}-preview.${n[3]}`, date: r.published_at, files };
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

function fillLinks(release) {
  for (const a of document.querySelectorAll('[data-dl]')) {
    const file = release.files[a.dataset.dl];
    if (file) a.href = file.url;
  }
  for (const el of document.querySelectorAll('[data-file]')) {
    const file = release.files[el.dataset.file];
    if (file) el.textContent = file.name;
  }
  const deb = release.files.deb;
  for (const cmd of deb ? document.querySelectorAll('[data-deb-cmd]') : []) {
    cmd.textContent = cmd.textContent.replace(/\.\/\S+\.deb$/, `./${deb.name}`);
  }
  const date = frenchDate(release.date);
  document.getElementById('version').textContent = `Préversion ${release.version}${date ? `, du ${date}` : ''}`;
}

function heroButton(system, release) {
  const main = MAIN[system];
  if (system === 'mobile') {
    document.getElementById('dl-main').hidden = true;
    document.getElementById('dl-main-meta').hidden = true;
    document.getElementById('dl-mobile').hidden = false;
    return;
  }
  if (!main) return;
  const file = release && release.files[main.file];
  document.getElementById('dl-main-label').textContent = main.label;
  // Without the API's answer the button opens the Releases page: say which file to take there.
  document.getElementById('dl-main-meta').textContent = file
    ? `${main.meta} · ${megabytes(file.size)}`
    : `${main.meta} · sur GitHub, prends le fichier qui finit par ${FILES[main.file]}`;
  if (file) document.getElementById('dl-main').href = file.url;
  // The others under the button: the one the button already gives goes away.
  const own = document.querySelector(`.dl-others li[data-for="${system}"]`);
  if (own) own.hidden = true;
  document.getElementById('dl-others-label').textContent = 'Autres systèmes\u00a0:';
}

// ── Install tabs: the links to each system become tabs ─────────────────────

function setupTabs(preferred) {
  const nav = document.querySelector('[data-tabs]');
  if (!nav) return;
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
    document.getElementById('installer').scrollIntoView();
    return true;
  }
  window.addEventListener('hashchange', fromHash);
  if (!fromHash()) select(`installer-${preferred}`);
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

async function start() {
  const system = detectSystem();
  setupTabs(MAIN[system] ? system : 'mac');
  addCopyButtons();
  openLinkedDetails();
  window.addEventListener('hashchange', openLinkedDetails);
  heroButton(system, null);
  const release = await newestPreview();
  if (!release) return;
  fillLinks(release);
  heroButton(system, release);
}

start();
