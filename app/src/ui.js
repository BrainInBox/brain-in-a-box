// Bibo's two windows, the installer (app.js) and the guide (guide.js), share these: Bibo himself,
// the status icons, and the few helpers that build a screen. Plain JS, no build step: a classic
// script loaded before the window's own, whose top-level names it sees.
'use strict';

function detectSystem() {
  const ua = navigator.userAgent;
  if (/Windows/.test(ua)) return 'windows';
  return /Linux/.test(ua) && !/Android/.test(ua) ? 'linux' : 'macos';
}

const ICONS = {
  ok: '<svg viewBox="0 0 20 20"><circle class="ic-bg" cx="10" cy="10" r="9"/><path class="ic-fg" d="M6 10.4l2.7 2.6L14 7.6"/></svg>',
  missing: '<svg viewBox="0 0 20 20"><circle class="ic-ring" cx="10" cy="10" r="8.2"/><path class="ic-line" d="M10 5.8v7M6.9 10.2L10 13.2l3.1-3"/></svg>',
  warn: '<svg viewBox="0 0 20 20"><circle class="ic-bg" cx="10" cy="10" r="9"/><path class="ic-fg" d="M10 5.6v5"/><circle class="ic-dot" cx="10" cy="14" r="1.3"/></svg>',
  error: '<svg viewBox="0 0 20 20"><circle class="ic-bg" cx="10" cy="10" r="9"/><path class="ic-fg" d="M7 7l6 6M13 7l-6 6"/></svg>',
  pending: '<svg viewBox="0 0 20 20"><circle class="ic-ring" cx="10" cy="10" r="8.2"/></svg>',
};

// Bibo, inline so CSS can animate its parts. src/bibo.svg is the same drawing, still, for the icon.
// Decorative: the screen's title and text say everything, so screen readers skip the drawing.
const BIBO_SVG = `
<svg class="bibo idle" viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <ellipse class="b-shadow" cx="120" cy="211" rx="76" ry="7" fill="#3b2418" opacity=".16"/>
  <g class="b-hop">
    <g class="b-body" stroke="#4b2e2b" stroke-width="3" stroke-linejoin="round" stroke-linecap="round">
      <path class="b-inside" d="M46 138 L60 124 L180 124 L194 138 Z" fill="#9c6136"/>
      <path class="b-flap-l" d="M46 138 L60 124 L36 104 L20 119 Z" fill="#d8955a"/>
      <path class="b-flap-r" d="M194 138 L180 124 L204 104 L220 119 Z" fill="#d8955a"/>
      <g class="b-brain">
        <path d="M70 142 C62 128 58 110 66 96 C62 78 72 64 88 62 C92 46 112 40 120 48 C128 40 148 46 152 62 C168 64 178 78 174 96 C182 110 178 128 170 142 Z" fill="#f7a9c4"/>
        <g fill="none" stroke="#da6f98">
          <path d="M120 50 C116 56 124 61 120 68"/>
          <path d="M96 58 C102 53 110 55 112 62"/>
          <path d="M128 62 C130 55 138 53 144 58"/>
          <path d="M70 90 C70 80 77 74 85 74"/>
          <path d="M170 90 C170 80 163 74 155 74"/>
        </g>
        <ellipse cx="97" cy="51" rx="6" ry="3" transform="rotate(-24 97 51)" fill="#fff" opacity=".7" stroke="none"/>
        <g class="b-cheeks" fill="#ff7fa6" opacity=".55" stroke="none">
          <ellipse cx="79" cy="119" rx="8" ry="5"/>
          <ellipse cx="161" cy="119" rx="8" ry="5"/>
        </g>
        <g class="b-eyes">
          <ellipse cx="100" cy="102" rx="14" ry="16.5" fill="#fff"/>
          <ellipse cx="140" cy="102" rx="14" ry="16.5" fill="#fff"/>
          <g class="b-pupil" stroke="none">
            <ellipse cx="101" cy="104" rx="8.5" ry="10.5" fill="#2b1a1e"/>
            <ellipse cx="141" cy="104" rx="8.5" ry="10.5" fill="#2b1a1e"/>
            <circle cx="104.5" cy="99" r="3.4" fill="#fff"/>
            <circle cx="144.5" cy="99" r="3.4" fill="#fff"/>
            <circle cx="98" cy="109.5" r="1.6" fill="#fff"/>
            <circle cx="138" cy="109.5" r="1.6" fill="#fff"/>
          </g>
        </g>
        <g class="b-lids" fill="none">
          <path d="M88 103 Q100 112 112 103"/>
          <path d="M128 103 Q140 112 152 103"/>
        </g>
        <g class="b-glee" fill="none">
          <path d="M89 107 Q100 96 111 107"/>
          <path d="M129 107 Q140 96 151 107"/>
        </g>
        <g class="b-brows" fill="none">
          <path d="M87 81 L110 75"/>
          <path d="M130 75 L153 81"/>
        </g>
        <path class="m-smile" d="M113 124 Q120 131 127 124" fill="none"/>
        <g class="m-open">
          <path d="M110 121 Q120 139 130 121 Z" fill="#7a2340"/>
          <ellipse cx="120" cy="127.5" rx="4.5" ry="2.5" fill="#f27b9b" stroke="none"/>
        </g>
        <ellipse class="m-o" cx="120" cy="125" rx="3.5" ry="4.5" fill="#4b2e2b" stroke="none"/>
        <path class="m-wobble" d="M111 126 Q115.5 122 120 126 Q124.5 130 129 126" fill="none"/>
      </g>
      <rect class="b-front" x="46" y="138" width="148" height="66" rx="7" fill="#e7a96b"/>
      <rect x="50" y="141.5" width="140" height="5" rx="2.5" fill="#f2c08a" stroke="none"/>
      <path class="b-heart" d="M120 188 C105 178 107 164 115.5 165 C118 165.4 119.5 167 120 169.5 C120.5 167 122 165.4 124.5 165 C133 164 135 178 120 188 Z" fill="#f7a9c4" stroke-width="2.5"/>
    </g>
    <path class="b-drop" d="M172 60 C172 60 164 70 164 75 A8 8 0 0 0 180 75 C180 70 172 60 172 60 Z" fill="#8fd3ff" stroke="#4b2e2b" stroke-width="2.5" stroke-linejoin="round"/>
    <g class="b-bubbles" fill="#fff" stroke="#4b2e2b" stroke-width="2.5">
      <circle cx="186" cy="56" r="4"/>
      <circle cx="199" cy="40" r="6.5"/>
      <circle cx="215" cy="20" r="10"/>
    </g>
  </g>
  <g class="b-sparks" fill="#ffc94a" stroke="#4b2e2b" stroke-width="2" stroke-linejoin="round">
    <g transform="translate(30 62)"><path d="M0 -11 C1.6 -3 3 -1.6 11 0 C3 1.6 1.6 3 0 11 C-1.6 3 -3 1.6 -11 0 C-3 -1.6 -1.6 -3 0 -11 Z"/></g>
    <g transform="translate(212 64) scale(.8)"><path d="M0 -11 C1.6 -3 3 -1.6 11 0 C3 1.6 1.6 3 0 11 C-1.6 3 -3 1.6 -11 0 C-3 -1.6 -1.6 -3 0 -11 Z"/></g>
    <g transform="translate(18 172) scale(.7)"><path d="M0 -11 C1.6 -3 3 -1.6 11 0 C3 1.6 1.6 3 0 11 C-1.6 3 -3 1.6 -11 0 C-3 -1.6 -1.6 -3 0 -11 Z"/></g>
    <g transform="translate(224 176) scale(.9)"><path d="M0 -11 C1.6 -3 3 -1.6 11 0 C3 1.6 1.6 3 0 11 C-1.6 3 -3 1.6 -11 0 C-3 -1.6 -1.6 -3 0 -11 Z"/></g>
  </g>
  <g class="b-zzz" fill="#cfe7ff" stroke="#4b2e2b" stroke-width="2" stroke-linejoin="round">
    <g transform="translate(172 64) scale(.9)"><path d="M-7 -7.5 H7 V-4.2 L-1.6 4.2 H7 V7.5 H-7 V4.2 L1.6 -4.2 H-7 Z"/></g>
    <g transform="translate(191 41) scale(1.2)"><path d="M-7 -7.5 H7 V-4.2 L-1.6 4.2 H7 V7.5 H-7 V4.2 L1.6 -4.2 H-7 Z"/></g>
    <g transform="translate(213 14) scale(1.55)"><path d="M-7 -7.5 H7 V-4.2 L-1.6 4.2 H7 V7.5 H-7 V4.2 L1.6 -4.2 H-7 Z"/></g>
  </g>
</svg>`;

// French typography: a no-break space before ! ? : ; and inside « », and the curly
// apostrophe, also in the sentences that come from Rust.
function nbsp(text) {
  return text.replace(/ ([!?:;»])/g, ' $1').replace(/« /g, '« ').replace(/(\p{L})'(?=\p{L})/gu, '$1’');
}

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(typeof kid === 'string' ? nbsp(kid) : kid);
  }
  return el;
}

// Only ever fed the constant markup above, never data from outside.
function fromMarkup(markup) {
  const t = document.createElement('template');
  t.innerHTML = markup.trim();
  return t.content.firstElementChild;
}

function icon(kind) {
  const known = Object.hasOwn(ICONS, kind) ? kind : 'warn';
  const el = fromMarkup(ICONS[known]);
  el.setAttribute('class', `ic ic-${known}`);
  el.setAttribute('aria-hidden', 'true');
  el.setAttribute('focusable', 'false');
  return el;
}

function button(label, onClick, { kind = 'primary', act, small = false } = {}) {
  const cls = `btn btn-${kind}${small ? ' btn-small' : ''}`;
  return h('button', { type: 'button', class: cls, 'data-act': act, onclick: onClick }, label);
}

function invoke(cmd, args) {
  return window.__TAURI__.core.invoke(cmd, args);
}

function errText(e) {
  if (typeof e === 'string') return e;
  return (e && e.message) || 'Erreur inconnue.';
}
