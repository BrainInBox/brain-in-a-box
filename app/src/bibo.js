// Bibo's gestures and scenes: arms, props, and what he acts out on each screen (bibo-anim.css draws
// the moves). Loaded after app.js or guide.js, which never wait for it: they work the same without it,
// and their few calls, window.biboCue?.(…), then do nothing (dev/screens.mjs loads neither file).
//
// A scene is a loop that lasts as long as its screen (data-scene on the drawing): the magnifier during
// the diagnosis, the wrench during the install. A gesture is a moment, at most a couple of seconds
// (a cue-… class): out of the box, a photo, a thumbs up. Gestures win over scenes, scenes over moods.
// Only CSS moves: this file adds the parts, sets a class or an attribute, and a timer takes it off.
'use strict';

(() => {
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const INK = '#4b2e2b';
  const PINK = '#f7a9c4';
  const GOLD = '#ffc94a';
  const METAL = '#cfe7ff';
  const STAR = 'M0 -11 C1.6 -3 3 -1.6 11 0 C3 1.6 1.6 3 0 11 C-1.6 3 -3 1.6 -11 0 C-3 -1.6 -1.6 -3 0 -11 Z';

  // Gestures and how long each lasts. With reduced motion, a gesture is its last pose, held 1.5 s.
  const CUES = {
    sortie: 1750,
    coucou: 1200,
    photo: 1600,
    pouce: 1100,
    montre: 1400,
    ampoule: 1700,
    cadenas: 1700,
    haussement: 1100,
    range: 1700,
    confettis: 1900,
  };
  const SCENES = ['loupe', 'bricole', 'casque', 'demande', 'reflechit', 'attend', 'triste', 'cle', 'memoire', 'lit', 'mains', 'cache', 'gratte', 'plisse', 'stop', 'dodo'];
  const STILL_MS = 1500;

  // ── The drawing's new parts ──────────────────────────────────────────────

  const r1 = (n) => Math.round(n * 10) / 10;

  // A hand: palms [cx, cy, r] and fingers [x1, y1, x2, y2, width] around (0, 0), drawn twice like
  // the arms, outlines first then fills, so the pieces merge into one shape.
  const HANDS = {
    mitt: [[[0, 0, 6.5]], []],
    point: [[[0, 0, 6]], [[3, -1.5, 13, -1.5, 4.5]]],
    thumb: [[[0, 0, 6.5]], [[-1, -3, -1, -13, 5]]],
    open: [[[0, 1, 7]], [[-5, -2, -8, -11, 4.5], [0, -3, 0, -13, 4.5], [5, -2, 8, -11, 4.5], [5, 4, 12, 1, 4.5]]],
    big: [[[0, 3, 11]], [[-7, -1, -10, -14, 5.5], [-2.5, -3, -3.5, -17, 5.5], [2.5, -3, 3.5, -17, 5.5], [7, -1, 10, -14, 5.5]]],
    cup: [[[0, 0, 6.5]], [[2, -2, 12, -5, 4.5], [3, 2, 13, 0, 4.5]]],
  };

  function handShapes(kind, at, outline) {
    const [palms, fingers] = HANDS[kind];
    const fill = outline ? `fill="${INK}" stroke="${INK}" stroke-width="6"` : `fill="${PINK}" stroke="none"`;
    return `<g transform="${at}">${palms.map(([x, y, r]) => `<circle cx="${x}" cy="${y}" r="${r}" ${fill}/>`).join('')}${fingers
      .map(([x1, y1, x2, y2, w]) => `<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="${outline ? INK : PINK}" stroke-width="${outline ? w + 6 : w}" stroke-linecap="round"/>`)
      .join('')}</g>`;
  }

  // An arm in one pose: a noodle from the shoulder, its hand, and what it holds (drawn behind the hand).
  function pose(name, d, at, hand, held = '') {
    const [, sx, sy] = d.match(/^M(\d+) (\d+)/);
    return `<g class="b-pose p-${name}">${held}`
      + `<path d="${d}" fill="none" stroke="${INK}" stroke-width="11" stroke-linecap="round"/>${handShapes(hand, at, true)}`
      + `<path d="${d}" fill="none" stroke="${PINK}" stroke-width="5" stroke-linecap="round"/>`
      + `<circle cx="${sx}" cy="${sy}" r="5" fill="${PINK}" stroke="none"/>${handShapes(hand, at, false)}</g>`;
  }

  // The four-pointed star of the sparks; CSS turns and scales the path, the group places it.
  function star(x, y, scale, fill = GOLD, stroke = INK) {
    return `<g transform="translate(${x} ${y}) scale(${scale})"><path d="${STAR}" fill="${fill}" stroke="${stroke}" stroke-width="${r1(2 / scale)}" stroke-linejoin="round"/></g>`;
  }

  // Little strokes (a click, a tic): gold edged in brown, seen on a light window and on a dark one.
  function marks(cls, d) {
    return `<g class="${cls}" fill="none" stroke-linecap="round"><path d="${d}" stroke="${INK}" stroke-width="6"/><path d="${d}" stroke="${GOLD}" stroke-width="2.5"/></g>`;
  }

  function gear(cx, cy, r, teeth) {
    let d = '';
    const step = (2 * Math.PI) / teeth;
    for (let i = 0; i < teeth; i++) {
      const a = i * step;
      const pts = [[a - step * 0.3, r], [a - step * 0.16, r + 4], [a + step * 0.16, r + 4], [a + step * 0.3, r]];
      for (const [t, rr] of pts) d += `${d ? 'L' : 'M'}${r1(cx + rr * Math.cos(t))} ${r1(cy + rr * Math.sin(t))}`;
    }
    const hole = r * 0.38;
    return `${d}Z M${r1(cx + hole)} ${cy} A${r1(hole)} ${r1(hole)} 0 1 0 ${r1(cx - hole)} ${cy} A${r1(hole)} ${r1(hole)} 0 1 0 ${r1(cx + hole)} ${cy} Z`;
  }

  // A golden key along +x from its ring at (0, 0); `at` places it.
  function key(at) {
    return `<g transform="${at}" stroke="${INK}" stroke-linejoin="round">`
      + `<path d="M4 -2.6 H24 Q26 -2.6 26 -0.6 V2.6 H23.5 V8 H19.5 V2.6 H17.5 V6.5 H14 V2.6 H4 Z" fill="${GOLD}" stroke-width="2.5"/>`
      + `<circle r="7.5" fill="${GOLD}" stroke-width="3"/><circle r="2.6" fill="${INK}" stroke="none"/></g>`;
  }

  const CAMERA = `<g class="b-camera">
    <g class="b-photo"><rect x="130" y="88" width="24" height="25" rx="2" fill="#fff" stroke="${INK}" stroke-width="2.5"/>
      <rect x="133" y="91" width="18" height="14" rx="1" fill="#8fd3ff"/><circle cx="142" cy="99" r="4" fill="${PINK}" stroke="${INK}" stroke-width="1.6"/></g>
    <rect x="124" y="81" width="9" height="7" rx="2" fill="#da6f98" stroke="${INK}" stroke-width="2.5"/>
    <rect x="148" y="80" width="11" height="8" rx="2" fill="${GOLD}" stroke="${INK}" stroke-width="2.5"/>
    <rect x="119" y="86" width="46" height="28" rx="6" fill="${METAL}" stroke="${INK}" stroke-width="3"/>
    <circle cx="142" cy="100" r="11" fill="#2b1a1e" stroke="${INK}" stroke-width="3"/>
    <circle cx="142" cy="100" r="6" fill="none" stroke="#6b5470" stroke-width="2"/>
    <circle class="b-iris" cx="142" cy="100" r="11" fill="${INK}"/>
    <circle cx="146" cy="96" r="2.8" fill="#fff"/>
    <g class="b-flash">${star(154, 78, 1.05, '#fff', GOLD)}</g>
    ${marks('b-click', 'M116 82 l-5 -5 M122 77 l-1.5 -7 M112 89 l-7 -1.5')}
  </g>`;

  // The magnifier on the right eye shows that eye, bigger.
  const LOUPE = `<g class="b-loupe">
    <path d="M152 115 L160 122" stroke="${INK}" stroke-width="11" stroke-linecap="round"/>
    <path d="M152 115 L160 122" stroke="#9c6136" stroke-width="5" stroke-linecap="round"/>
    <circle cx="140" cy="102" r="18" fill="#fff"/>
    <g class="b-bigpupil"><circle cx="141" cy="105" r="10.5" fill="#2b1a1e"/><circle cx="145.5" cy="99" r="4" fill="#fff"/><circle cx="137.5" cy="111" r="1.8" fill="#fff"/></g>
    <circle cx="140" cy="102" r="17" fill="${METAL}" opacity=".3"/>
    <path d="M127 99 A13 13 0 0 1 134 89" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round"/>
    <circle cx="140" cy="102" r="18" fill="none" stroke="${INK}" stroke-width="5"/>
    <circle cx="140" cy="102" r="18" fill="none" stroke="#9c6136" stroke-width="1.5"/>
  </g>`;

  // The wrench and the screwdriver point up from the hand at (200, 114), and turn there.
  const TOOLS = `<g class="b-tool">
    <g class="b-wrench"><g transform="translate(200 114) rotate(-8)"><path d="M-3.5 4 L-3.5 -17.3 A8.5 8.5 0 0 1 -3.5 -32.7 L-3.5 -27 L3.5 -27 L3.5 -32.7 A8.5 8.5 0 0 1 3.5 -17.3 L3.5 4 Z" fill="${METAL}" stroke="${INK}" stroke-width="2.5" stroke-linejoin="round"/></g></g>
    <g class="b-screw"><g transform="translate(200 114) rotate(-8)" stroke="${INK}" stroke-linejoin="round">
      <path d="M-1.8 -12 V-29 L0 -33 L1.8 -29 V-12 Z" fill="${METAL}" stroke-width="2"/>
      <rect x="-5.5" y="-14" width="11" height="19" rx="4.5" fill="#ff7fa6" stroke-width="2.5"/>
      <path d="M-1.8 -10 V2 M1.8 -10 V2" stroke="#da6f98" stroke-width="1.6"/></g></g>
  </g>`;

  // Right arm, from the shoulder at (174, 128).
  const ARM_R = [
    pose('wave', 'M174 128 C186 122 194 108 195 96', 'translate(195 91) rotate(12)', 'open'),
    pose('cam', 'M174 128 C180 121 175 115 166 114', 'translate(165 113)', 'mitt', CAMERA),
    pose('loupe', 'M174 128 C176 123 169 121 162 122', 'translate(161 122)', 'mitt', LOUPE),
    pose('tool', 'M174 128 C186 129 195 124 199 117', 'translate(200 114)', 'mitt', TOOLS),
    pose('point', 'M174 128 C184 125 195 127 204 128', 'translate(206 128)', 'point'),
    pose('thumb', 'M174 128 C186 125 192 116 193 107', 'translate(193 103)', 'thumb'),
    pose('key', 'M174 128 C186 121 192 109 192 98', 'translate(192 95)', 'mitt',
      `<g class="b-key">${key('translate(196 87) rotate(-50)')}</g><g class="b-glint b-glint-key">${star(217, 60, 0.6)}</g>`),
    pose('palm', 'M174 128 C188 126 197 115 197 104', 'translate(197 98)', 'open'),
    pose('offer', 'M174 128 C182 140 170 150 157 150', 'translate(154 150) scale(-1 1)', 'cup'),
    pose('sheet', 'M174 128 C182 142 158 152 143 151', 'translate(140 151)', 'mitt'),
    pose('eye', 'M174 128 C190 118 176 106 156 106', 'translate(141 101)', 'big'),
    pose('shrug', 'M174 128 C188 129 199 122 204 113', 'translate(206 107) rotate(25)', 'open'),
    pose('lock', 'M174 128 C192 140 178 170 156 176', 'translate(153 177)', 'mitt', `<g class="b-lockkey">${key('translate(150 177) scale(-1 1)')}</g>`),
  ].join('');

  // Left arm, from the shoulder at (66, 128).
  const ARM_L = [
    pose('chin', 'M66 128 C72 143 94 145 105 138', 'translate(108 136)', 'mitt'),
    pose('head', 'M66 128 C40 120 42 84 69 71', 'translate(73 68)', 'mitt'),
    pose('eye', 'M66 128 C50 118 64 106 84 106', 'translate(99 101)', 'big'),
    pose('offer', 'M66 128 C58 140 70 150 83 150', 'translate(86 150)', 'cup'),
    pose('sheet', 'M66 128 C58 142 82 152 97 151', 'translate(100 151)', 'mitt'),
    pose('shrug', 'M66 128 C52 129 41 122 36 113', 'translate(34 107) rotate(-25)', 'open'),
  ].join('');

  const SHEET = (cls) => `<g class="${cls}"><g class="b-sheet-fold">
    <rect x="106" y="132" width="28" height="35" rx="3" fill="#fff" stroke="${INK}" stroke-width="2.5"/>
    <g fill="none" stroke-linecap="round"><path d="M111 141 h4 M111 149 h4 M111 157 h4" stroke="#da6f98" stroke-width="2.5"/>
    <path d="M118 141 h11 M118 149 h9 M118 157 h11" stroke="${INK}" stroke-width="2"/></g></g></g>`;

  // Inside the brain: the face's other looks.
  const FACE = `
    <g class="b-squint">
      <path d="M83 81 H117 V99 Q100 104.5 83 99 Z" fill="${PINK}" stroke="none"/>
      <path d="M123 81 H157 V99 Q140 104.5 123 99 Z" fill="${PINK}" stroke="none"/>
      <path d="M85 99.5 Q100 104.5 115 99.5" fill="none"/><path d="M125 99.5 Q140 104.5 155 99.5" fill="none"/>
    </g>
    <g class="b-wink"><ellipse cx="100" cy="102" rx="16" ry="18.5" fill="${PINK}" stroke="none"/><path d="M88 104 Q100 112 112 104" fill="none"/></g>
    <g class="b-doubt" fill="none"><path d="M88 83 L111 82"/><path d="M129 74 Q141 65 154 71"/></g>
    <g class="b-serious" fill="none"><path d="M88 79.5 L111 79.5"/><path d="M129 79.5 L152 79.5"/></g>
    <path class="m-flat" d="M113 126 L127 126" fill="none"/>`;

  // Above the brain, moving with it: the hard hat and the sparks of a busy brain.
  const HEAD = `
    <g class="b-hat"><g transform="rotate(-6 120 52)" stroke="${INK}" stroke-width="3" stroke-linejoin="round">
      <path d="M88 52 C88 31 101 22 120 22 C139 22 152 31 152 52 Z" fill="${GOLD}"/>
      <rect x="113" y="22.5" width="14" height="28" rx="5" fill="#ffd97a" stroke-width="2.5"/>
      <rect x="80" y="48" width="80" height="9" rx="4.5" fill="${GOLD}"/></g></g>
    <g class="b-crackle">${star(90, 66, 0.5)}${star(152, 70, 0.45)}${star(121, 54, 0.55)}</g>`;

  // On the box: its progress bar, a padlock, the taped lid of a closed box.
  const BOX = `
    <g class="b-bar"><rect x="74" y="191" width="92" height="8" rx="4" fill="#f2c08a" stroke-width="2"/>
      <rect class="b-bar-fill" x="76" y="193" width="88" height="4" rx="2" fill="#ff7fa6" stroke="none"/></g>
    <g class="b-glint b-glint-bar">${star(166, 186, 0.5)}</g>
    <g class="b-lock"><g class="b-shackle"><path d="M111 170 V161 A9 9 0 0 1 129 161 V170" fill="none" stroke="${INK}" stroke-width="9"/>
      <path d="M111 170 V161 A9 9 0 0 1 129 161 V170" fill="none" stroke="${METAL}" stroke-width="3"/></g>
      <rect x="106" y="167" width="28" height="23" rx="4" fill="${GOLD}"/><circle cx="120" cy="176.5" r="3" fill="${INK}" stroke="none"/>
      <path d="M120 178 V184" stroke-width="3"/></g>
    <g class="b-glint b-glint-lock">${star(142, 158, 0.6)}</g>
    <g class="b-lid"><path class="b-lid-l" d="M46 138 L60 124 L120 124 L120 138 Z" fill="#d8955a"/>
      <path class="b-lid-r" d="M194 138 L180 124 L120 124 L120 138 Z" fill="#d8955a"/>
      <path class="b-tape" d="M112 123 H128 V152 H112 Z" fill="#f2c08a" stroke-width="2.5"/></g>
    ${SHEET('b-sheet')}
    <g class="b-arms"><g class="b-arm-r">${ARM_R}</g><g class="b-arm-l">${ARM_L}</g></g>`;

  // Around the head: the thinking dots, the gears, the light bulb, the question mark.
  const AROUND = `
    <g class="b-dots" fill="${INK}"><circle cx="208.5" cy="20" r="2.4"/><circle cx="215" cy="20" r="2.4"/><circle cx="221.5" cy="20" r="2.4"/></g>
    <g class="b-gears" stroke="${INK}" stroke-width="2.5" stroke-linejoin="round" fill-rule="evenodd">
      <path class="b-gear-1" d="${gear(40, 54, 11, 8)}" fill="${GOLD}"/><path class="b-gear-2" d="${gear(57, 37, 7.5, 6)}" fill="${METAL}"/></g>
    <g class="b-bulb"><g class="b-rays" stroke-linecap="round">
      ${[-150, -118, -90, -62, -30].map((a) => {
        const t = (a * Math.PI) / 180;
        const p = (r) => `${r1(120 + r * Math.cos(t))} ${r1(25 + r * Math.sin(t))}`;
        return `<path d="M${p(17)}L${p(22)}" stroke="${INK}" stroke-width="7"/><path d="M${p(17)}L${p(22)}" stroke="${GOLD}" stroke-width="2.5"/>`;
      }).join('')}</g>
      <circle cx="120" cy="25" r="12" fill="${GOLD}" stroke="${INK}" stroke-width="3"/>
      <ellipse cx="115" cy="20" rx="3" ry="4.5" transform="rotate(30 115 20)" fill="#fff" opacity=".8"/>
      <path d="M114 35 H126 V41 Q126 44 123 44 H117 Q114 44 114 41 Z" fill="${METAL}" stroke="${INK}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M115 39 H125" stroke="${INK}" stroke-width="2"/></g>
    <g class="b-question"><path d="M191 52 C191 41 209 41 209 52 C209 60 200 60 200 69" fill="none" stroke="${INK}" stroke-width="11" stroke-linecap="round"/>
      <path d="M191 52 C191 41 209 41 209 52 C209 60 200 60 200 69" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round"/>
      <circle cx="200" cy="81" r="4.5" fill="#fff" stroke="${INK}" stroke-width="3"/></g>
    <g class="b-glint b-glint-thumb">${star(208, 86, 0.6)}</g>
    ${marks('b-tic', 'M186 82 l-5 -4 M199 76 v-7 M212 82 l5 -4')}`;

  // On the floor, beside the box: the alarm clock, the box of memories, confetti.
  const CARD = '<rect x="13" y="165" width="24" height="16" rx="2.5" fill="#fff" stroke="#4b2e2b" stroke-width="2.2"/><path d="M17.5 170 h15 M17.5 175.5 h9" stroke="#da6f98" stroke-width="2.2" stroke-linecap="round"/>';
  const CONFETTI = ['#f7a9c4', GOLD, '#8fd3ff', '#e7a96b', '#da6f98', '#f7a9c4', GOLD, '#8fd3ff', '#da6f98', '#e7a96b', GOLD, '#8fd3ff'];
  const FLOOR = `
    <g class="b-clock" stroke="${INK}" stroke-linejoin="round" stroke-linecap="round">
      <path d="M17 198 l-4 7 M35 198 l4 7" stroke-width="3"/>
      <circle cx="15" cy="172" r="6" fill="${GOLD}" stroke-width="2.5"/><circle cx="37" cy="172" r="6" fill="${GOLD}" stroke-width="2.5"/>
      <circle cx="26" cy="186" r="14" fill="#fff" stroke-width="3"/>
      <g class="b-hand-h"><path d="M26 186 V178" stroke-width="3"/></g><g class="b-hand-m"><path d="M26 186 V175" stroke-width="2.2"/></g>
      <circle cx="26" cy="186" r="1.8" fill="${INK}" stroke="none"/></g>
    <g class="b-memories" stroke="${INK}" stroke-linejoin="round">
      <path d="M6 180 L12 172 L38 172 L44 180 Z" fill="#9c6136" stroke-width="2.5"/>
      <path class="b-mem-fl" d="M6 180 L12 172 L5 162 L1 168 Z" fill="#d8955a" stroke-width="2.5"/>
      <path class="b-mem-fr" d="M44 180 L38 172 L45 162 L49 168 Z" fill="#d8955a" stroke-width="2.5"/>
      ${[1, 2, 3].map((n) => `<g class="b-card b-card-${n}"><g class="b-card-up"><g class="b-card-spin">${CARD}</g></g></g>`).join('')}
      <rect x="6" y="180" width="38" height="25" rx="4" fill="#e7a96b" stroke-width="2.5"/>
      <path d="M25 199 C18 194 19 187 23 187.5 C24 187.6 24.7 188.4 25 189.5 C25.3 188.4 26 187.6 27 187.5 C31 187 32 194 25 199 Z" fill="#da6f98" stroke-width="1.8"/></g>
    <g class="b-confetti" stroke="${INK}" stroke-width="1.4">${CONFETTI.map((c, i) => `<g class="b-cf"><g class="b-cf-spin">${i % 3 === 2
      ? `<circle cx="120" cy="128" r="2.8" fill="${c}"/>`
      : `<rect x="117.8" y="124.2" width="4.4" height="7.6" rx="1.2" fill="${c}"/>`}</g></g>`).join('')}</g>`;

  function parts(markup) {
    const t = document.createElement('template');
    t.innerHTML = `<svg xmlns="${SVG_NS}">${markup}</svg>`;
    const frag = document.createDocumentFragment();
    frag.append(...t.content.firstElementChild.childNodes);
    return frag;
  }

  function group(cls, attrs = {}) {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', cls);
    for (const [k, v] of Object.entries(attrs)) g.setAttribute(k, v);
    return g;
  }

  // ── One Bibo ──────────────────────────────────────────────────────────────

  const still = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  const all = new Set();
  let count = 0;

  class Bibo {
    constructor(svg) {
      this.svg = svg;
      this.cueName = '';
      this.cueTimer = 0;
      this.stepTimer = 0;
      this.done = null;
      this.lastPhoto = 0;
      this.build();
      all.add(this);
      svg.classList.toggle('b-paused', document.hidden);
    }

    // The new parts go around and on top of BIBO_SVG's, whose classes stay where styles.css expects them.
    build() {
      const { svg } = this;
      const body = svg.querySelector('.b-body');
      const brain = svg.querySelector('.b-brain');
      const front = svg.querySelector('.b-front');
      const clip = `b-clip-${(count += 1)}`;
      svg.prepend(parts(`<defs><clipPath id="${clip}" clipPathUnits="userSpaceOnUse"><rect x="-60" y="-80" width="360" height="219"/></clipPath></defs>`));
      const act = group('b-act');
      body.before(act);
      act.append(body);
      // The brain rises out of the box and sinks back in: below the rim, it is cut off.
      const cut = group('b-cut', { 'clip-path': `url(#${clip})` });
      const rise = group('b-rise');
      brain.before(cut);
      cut.append(rise);
      rise.append(brain, parts(HEAD));
      brain.append(parts(FACE));
      front.before(parts(SHEET('b-sheet-in')));
      body.append(parts(BOX));
      svg.querySelector('.b-bubbles').after(parts(AROUND));
      svg.append(parts(FLOOR));
    }

    // A gesture, from its start; the promise settles when it is over (or replaced).
    cue(name, opts = {}) {
      if (!Object.hasOwn(CUES, name)) return Promise.resolve();
      const now = Date.now();
      // The flash, never more than once a second.
      if (name === 'photo') {
        if (now - this.lastPhoto < 1000) return Promise.resolve();
        this.lastPhoto = now;
      }
      this.endCue();
      if (name === 'montre') this.aim(opts.angle);
      const { svg } = this;
      // The same gesture again starts over: its class off, a style pass, on again.
      void svg.getBoundingClientRect();
      svg.classList.add(`cue-${name}`);
      this.cueName = name;
      const ms = still.matches ? STILL_MS : CUES[name];
      return new Promise((resolve) => {
        this.done = resolve;
        this.cueTimer = setTimeout(() => this.endCue(), ms);
      });
    }

    endCue() {
      clearTimeout(this.cueTimer);
      if (this.cueName) this.svg.classList.remove(`cue-${this.cueName}`);
      this.cueName = '';
      const done = this.done;
      this.done = null;
      if (done) done();
    }

    // A loop for as long as the screen lasts; '' for none. The same name again changes nothing.
    scene(name, opts = {}) {
      const { svg } = this;
      const next = SCENES.includes(name) ? name : '';
      if (next === 'dodo') this.clock(opts.reprise);
      if (next === 'bricole' || next === 'casque') this.progress(opts.progress);
      if ((svg.getAttribute('data-scene') || '') === next) return;
      if (next) svg.setAttribute('data-scene', next);
      else svg.removeAttribute('data-scene');
    }

    rest() {
      this.endCue();
      this.scene('');
    }

    // The bar on the box, 0 to 1.
    progress(p) {
      if (typeof p !== 'number' || !Number.isFinite(p)) return;
      this.svg.style.setProperty('--p', Math.max(0, Math.min(1, p)).toFixed(3));
    }

    // A step done: a spark at the end of the bar.
    stepDone() {
      const { svg } = this;
      clearTimeout(this.stepTimer);
      svg.classList.remove('b-step');
      void svg.getBoundingClientRect();
      svg.classList.add('b-step');
      this.stepTimer = setTimeout(() => svg.classList.remove('b-step'), 700);
    }

    // The alarm clock's hands on the hour the subscription comes back (a Unix time, in seconds).
    // No hour known: no clock, rather than a made-up time.
    clock(unix) {
      const d = unix ? new Date(unix * 1000) : null;
      const known = Boolean(d) && !Number.isNaN(d.getTime());
      this.svg.classList.toggle('b-no-time', !known);
      if (!known) return;
      const h = d.getHours();
      const m = d.getMinutes();
      this.svg.style.setProperty('--h', `${(((h % 12) + m / 60) * 30).toFixed(1)}deg`);
      this.svg.style.setProperty('--m', `${m * 6}deg`);
    }

    // The pointing arm's angle, in degrees from pointing right, clockwise (90: straight down).
    aim(angle) {
      const a = typeof angle === 'number' && Number.isFinite(angle) ? Math.max(-80, Math.min(110, angle)) : 35;
      const t = (a * Math.PI) / 180;
      this.svg.style.setProperty('--point', `${a.toFixed(1)}deg`);
      // The pupils look the same way, as far as the eye allows (app.js's look()).
      this.svg.style.setProperty('--lx', `${(3.5 * Math.cos(t)).toFixed(2)}px`);
      this.svg.style.setProperty('--ly', `${(4 * Math.sin(t)).toFixed(2)}px`);
    }

    // The angle from the right shoulder to the middle of an element on the page.
    angleTo(el) {
      const r = this.svg.getBoundingClientRect();
      const b = el.getBoundingClientRect();
      if (!r.width || !b.width) return undefined;
      const sx = r.left + (r.width * 174) / 240;
      const sy = r.top + (r.height * 128) / 240;
      return (Math.atan2(b.top + b.height / 2 - sy, b.left + b.width / 2 - sx) * 180) / Math.PI;
    }
  }

  // The drawing's parts the rig builds on (dev/screens.mjs checks BIBO_SVG has them): without one, Bibo stays still.
  const PARTS = ['.b-body', '.b-brain', '.b-front', '.b-bubbles'];

  function rig(svg) {
    if (!svg || !PARTS.every((p) => svg.querySelector(p))) return null;
    if (!svg.biboRig) svg.biboRig = new Bibo(svg);
    return svg.biboRig;
  }

  // A hidden window moves nothing.
  document.addEventListener('visibilitychange', () => {
    for (const b of all) b.svg.classList.toggle('b-paused', document.hidden);
  });

  window.Bibo = { rig, CUES: Object.keys(CUES), SCENES: SCENES.slice() };

  // ── The windows' Bibo: what he acts out, read from the screen ────────────

  const hero = document.querySelector('.hero .bibo');
  if (!hero) return;
  const bibo = rig(hero);
  if (!bibo) return;
  const isGuide = document.body.classList.contains('guide');
  const MOODS = ['idle', 'thinking', 'working', 'happy', 'worried'];
  // The screens where Bibo may show the button. Not where the user reads or checks before agreeing
  // (how he sees the screen, the capture about to leave): there, a finger would hurry the user.
  const CALM = ['welcome', 'choice', 'diag', 'done', 'voyage', 'objectif'];
  const POINT_AFTER_MS = 15000;
  const POINT_GAP_MS = 25000;
  // Thinking this long with nothing new to say: he waits, patiently.
  const LONG_MS = 20000;
  // While the other window waits on the user, the raised finger taps again now and then.
  const NUDGE_MS = 7000;
  // A worried look on these screens is a failure: he winces and slumps. Anywhere else it is a word of
  // caution (install first, the same capture again), and the worried face says enough.
  const FAILED = ['erreur', 'abandon', 'arrete', 'choice', 'diag', 'voyage', 'install', 'repair-error', 'repairing', 'proposal', 'terminal', 'done', 'import', 'import-read', 'import-error'];

  // Screen and mood to scene. The first that matches wins: [screen or '*', mood or '*', scene].
  const RULES = [
    // The guide's login: the key, held up with a smile though the invitation is worded as a worry.
    ['connexion', 'worried', 'cle'],
    ['connexion', 'thinking', 'attend'],
    ...FAILED.map((s) => [s, 'worried', 'triste']),
    ['diag', 'thinking', 'loupe'],
    ['install', 'working', 'bricole'],
    ['repairing', 'working', 'casque'],
    ['analyze', 'thinking', 'reflechit'],
    ['import-read', 'thinking', 'lit'],
    ['import-run', 'working', 'memoire'],
    ['login', 'idle', 'cle'],
    ['login', 'thinking', 'attend'],
    // Codex's login from the end, the login a repair or an import asks for: the same wait.
    ['done', 'thinking', 'attend'],
    ['repair-error', 'thinking', 'attend'],
    ['import-error', 'thinking', 'attend'],
    ['voyage', 'idle', 'cle'],
    ['voyage', 'thinking', 'cle'],
    ['import-paste', 'idle', 'mains'],
    ['coller', 'idle', 'mains'],
    ['lecture', 'thinking', 'loupe'],
    ['cacher', '*', 'cache'],
    ['regarde', 'thinking', 'reflechit'],
    ['coince', '*', 'gratte'],
    ['enregistrer', 'thinking', 'lit'],
  ];
  // A few screens keep their own look whatever the mood says.
  const OVERRIDES = { confirmer: 'gratte', 'pas-ici': 'loupe' };

  const now = {
    screen: '',
    mood: '',
    pinned: '',
    pinOpts: {},
    active: Date.now(),
    pointedAt: 0,
    pointTimer: 0,
    blinkTimer: 0,
    longTimer: 0,
    nudgeTimer: 0,
    done: 0,
    cheered: false,
  };

  function moodOf() {
    return MOODS.find((m) => hero.classList.contains(m)) || 'idle';
  }

  function sceneFor(screen, mood) {
    if (now.pinned) return now.pinned;
    if (Object.hasOwn(OVERRIDES, screen)) return OVERRIDES[screen];
    // « Careful » only for a screen Bibo filtered or one that gives orders (their banner is an alert);
    // a plain « Ça coince » is a scratch of the head.
    if (screen === 'bloque') return document.querySelector('.banner[role="alert"]') ? 'stop' : 'gratte';
    // The install screen holds the login card for a while: the key, then waiting for the login.
    if (screen === 'install' && document.querySelector('.login-card')) return mood === 'thinking' ? 'attend' : mood === 'idle' ? 'cle' : '';
    if (screen === 'etape' && lowConfidence()) return 'plisse';
    for (const [s, m, scene] of RULES) if ((s === '*' || s === screen) && (m === '*' || m === mood)) return scene;
    return '';
  }

  // The guide's step with a dotted ring, or none: Claude is not sure where.
  function lowConfidence() {
    return Boolean(document.querySelector('.zoom-pointille')) || !document.querySelector('.zoom, .ring');
  }

  function barProgress() {
    const fill = document.querySelector('.bar-fill');
    const w = fill && parseFloat(fill.style.width);
    return Number.isFinite(w) ? w / 100 : undefined;
  }

  function stepsDone() {
    const done = document.querySelectorAll('.step.is-done').length;
    const more = done > now.done;
    now.done = done;
    return more;
  }

  // `shown`: a screen was shown since (screen() sets data-screen every time, even to the same name),
  // which ends what biboCue pinned on the one before, and `pin` is what biboCue pins on this one.
  function update(shown = false, pin = '') {
    const screen = document.body.dataset.screen || '';
    const mood = moodOf();
    const was = { screen: now.screen, mood: now.mood, scene: hero.getAttribute('data-scene') || '' };
    const turned = mood !== was.mood;
    now.screen = screen;
    now.mood = mood;
    if (shown) now.pinned = pin;
    bibo.scene(sceneFor(screen, mood), { ...(now.pinned ? now.pinOpts : {}), progress: barProgress() });

    if (mood === 'happy' && (turned || shown)) {
      cheer(screen, was.scene, turned);
    } else if (shown && was.scene === 'attend' && was.screen === 'connexion' && mood !== 'worried') {
      // The guide's login went through: it goes straight on, without a word of joy.
      bibo.cue('cadenas');
    } else if (shown && screen === 'proposal' && mood === 'idle' && now.pinned !== 'plisse') {
      bibo.cue('ampoule');
    } else if (shown && (screen === 'pas-ici' || (screen === 'import-done' && mood === 'idle'))) {
      // Not on this window; nothing worth keeping in the import.
      bibo.cue('haussement');
    } else if (mood === 'worried' && ((turned && screen === 'coller') || (shown && screen === 'apercu'))) {
      // Pasted, but no image in it; the same capture as last time.
      bibo.cue('haussement');
    } else if (shown && screen === 'etape' && !lowConfidence()) {
      const target = document.querySelector('.zoom, .ring, .capture');
      bibo.cue('montre', { angle: target ? bibo.angleTo(target) : 70 });
    }
    if (stepsDone() && !shown && (screen === 'install' || screen === 'repairing')) bibo.stepDone();
    if (shown || turned) waitLater();
    if (shown) pointLater();
  }

  // Joy, by what it is about: ready, logged in, saved, or a step that went well.
  function cheer(screen, scene, turned) {
    // The end of the install once (coming back to it from an import is no news), each guide's end, each import.
    if ((screen === 'done' && !now.cheered) || screen === 'fin' || screen === 'import-done') {
      if (screen === 'done') now.cheered = true;
      return bibo.cue('confettis');
    }
    if (scene === 'attend' || scene === 'cle') return bibo.cue('cadenas');
    if (screen === 'enregistre') return bibo.cue('range');
    return turned ? bibo.cue('pouce') : undefined;
  }

  // A new word from Bibo: the raised hand of the step before is over (the install screen stays from
  // one step to the next, so no new screen says it), and the wait starts over.
  function said() {
    if (now.pinned === 'demande') {
      now.pinned = '';
      update();
    }
    waitLater();
  }

  // Each new word from Bibo starts the wait over; thinking long without one, he waits patiently.
  function waitLater() {
    clearTimeout(now.longTimer);
    hero.classList.remove('b-long');
    if (now.mood !== 'thinking') return;
    now.longTimer = setTimeout(() => {
      if (now.mood === 'thinking') hero.classList.add('b-long');
    }, LONG_MS);
  }

  // The finger up again, once, while the other window still waits.
  function nudgeLater() {
    clearTimeout(now.nudgeTimer);
    if (now.pinned !== 'demande' || still.matches) return;
    now.nudgeTimer = setTimeout(() => {
      if (now.pinned !== 'demande') return;
      if (!document.hidden && !bibo.cueName) {
        hero.classList.remove('b-nudge');
        void hero.getBoundingClientRect();
        hero.classList.add('b-nudge');
        setTimeout(() => hero.classList.remove('b-nudge'), 700);
      }
      nudgeLater();
    }, NUDGE_MS);
  }

  // The user has not moved for a while on a screen that waits for a click: Bibo shows the button.
  function pointLater(ms = POINT_AFTER_MS) {
    clearTimeout(now.pointTimer);
    now.pointTimer = setTimeout(pointAtButton, ms);
  }

  // Once per screen at most, and never twice within 25 s.
  function pointAtButton() {
    const idle = Date.now() - now.active;
    if (idle < POINT_AFTER_MS) return pointLater(POINT_AFTER_MS - idle);
    const since = Date.now() - now.pointedAt;
    if (since < POINT_GAP_MS) return pointLater(POINT_GAP_MS - since);
    const btn = document.querySelector('.actions [data-act="next"]:not(:disabled)');
    const busy = bibo.cueName || hero.getAttribute('data-scene') || hero.classList.contains('asleep') || document.hidden;
    if (!btn || busy || now.mood !== 'idle' || !CALM.includes(now.screen)) return undefined;
    now.pointedAt = Date.now();
    return bibo.cue('montre', { angle: bibo.angleTo(btn) });
  }

  // Any move from the user: the pointing stops at once.
  function stir() {
    now.active = Date.now();
    if (bibo.cueName === 'montre') bibo.endCue();
  }

  // The guide has no life of its own (app.js blinks its Bibo): blinking costs nothing.
  function blinkLater() {
    clearTimeout(now.blinkTimer);
    now.blinkTimer = setTimeout(() => {
      if (!document.hidden && !bibo.cueName) {
        hero.classList.add('blink');
        setTimeout(() => hero.classList.remove('blink'), 130);
      }
      blinkLater();
    }, 2500 + Math.random() * 4500);
  }

  // The few moments only the windows know: window.biboCue?.(name, options). A gesture plays once;
  // a scene (dodo with { reprise }, doute for plisse) stays until the screen changes, demande until
  // Bibo's next word. The windows call it before adding their buttons: whatever goes wrong here
  // stays here.
  window.biboCue = (name, opts = {}) => {
    try {
      if (Object.hasOwn(CUES, name)) return bibo.cue(name, opts);
      const scene = name === 'doute' ? 'plisse' : name;
      if (!SCENES.includes(scene)) return Promise.resolve();
      // The screen and the word this comes with: that first, with the pin, for this screen (or word) only.
      const pending = watch.takeRecords();
      if (pending.length) changed(pending, scene);
      if (words.takeRecords().length) waitLater();
      now.pinned = scene;
      now.pinOpts = opts;
      bibo.scene(scene, opts);
      nudgeLater();
    } catch {
      // Bibo stands still; the window goes on.
    }
    return Promise.resolve();
  };

  function changed(records, pin = '') {
    update(records.some((r) => r.attributeName === 'data-screen'), pin);
  }

  const watch = new MutationObserver((records) => changed(records));
  watch.observe(document.body, { attributes: true, attributeFilter: ['data-screen'] });
  watch.observe(hero, { attributes: true, attributeFilter: ['class'] });
  // The bar's width and the steps done, on the screens that have them.
  const screenEl = document.querySelector('.screen');
  if (screenEl) {
    new MutationObserver(() => {
      const scene = hero.getAttribute('data-scene');
      if (scene === 'bricole' || scene === 'casque') bibo.progress(barProgress());
      if (stepsDone()) bibo.stepDone();
    }).observe(screenEl, { subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
  }

  // What Bibo says: each new message.
  const words = new MutationObserver(said);
  const message = document.querySelector('.hero .message');
  if (message) words.observe(message, { childList: true, characterData: true, subtree: true });

  // « C'est fait » on a step (and « Oui, c'est fait »): a thumbs up while the next screen comes.
  document.addEventListener('click', (e) => {
    const btn = e.target instanceof Element ? e.target.closest('.actions [data-act="next"]') : null;
    if (btn && !btn.disabled && (now.screen === 'etape' || now.screen === 'confirmer')) bibo.cue('pouce');
  }, { capture: true });

  for (const type of ['pointermove', 'pointerdown', 'keydown', 'wheel']) document.addEventListener(type, stir, { capture: true, passive: true });
  update();
  bibo.cue('sortie');
  if (isGuide) blinkLater();
})();
