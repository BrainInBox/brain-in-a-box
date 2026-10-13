---
name: html-artifact
description: Build one self-contained .html file that opens offline in any browser, to explain, plan, report, review or diagram something. Use when the user asks for an HTML page or file, a visual explainer of how something works, a plan, spec, status or incident report to read or share, a walkthrough of a PR or a diff, an architecture, process or concept diagram, options compared side by side, or a small editor whose result they copy back. Even if they never say "HTML". Not for a short answer that fits in the chat. Produces a local file.
license: MIT
---

# HTML Artifact

Make one self-contained `.html` file (no build step, no internet, no library)
whenever the result is something a person should *read, share or play with*:
an explainer, a plan, a status or incident report, a code review, a diagram,
a few design options side by side, or a small editor that copies its result back.

HTML beats Markdown once a document needs color, layout, diagrams, tables, code
or interaction. It opens in any browser, offline, with a double-click, and it
stays readable past 100 lines. Use this skill when the user asks for an HTML
file, or for a *visual explainer*, a *plan / PR / report write-up*, a
*diagram*, an *options comparison* or a *prototype* of an interaction, even
when they don't say "HTML". A question with a short answer stays in the chat.

Not the right tool for an editable whiteboard drawing (`.excalidraw`),
generative or animated art, or a real website that belongs in a codebase. If
another installed skill covers that, use it.

## Files in this skill

This skill's folder is `${CLAUDE_SKILL_DIR}` (normally
`~/.claude/skills/html-artifact`). Paths below are relative to it. Read them
with the Read tool when the step calls for them.

References:

- `references/house-style.md`: colors, fonts, cards, tables, code blocks,
  diffs. **Read it before writing any artifact.**
- `references/examples.md`: the 20 example pages from Anthropic's
  html-effectiveness gallery, and which one fits which job.
- `references/svg-diagrams.md`: hand-drawn inline SVG (arrows, nodes,
  decision diamonds, keeping coordinates tidy). Read it for any diagram.
- `references/concept-archetypes.md`: the 9-color educational palette and
  diagram shapes (timeline, tree, 2x2, layers, before/after, hub and spoke,
  cross-section). Read it for science, physical or teaching visuals.
- `references/dark-tech.md`: the dark "infra" look for cloud and software
  architecture diagrams.
- `references/throwaway-editors.md`: single-file editors and a copy button
  that works from a file opened on disk.
- `references/fidelity-and-verify.md`: how polished to make it, how to lay
  out several options, and how to check the result with a screenshot.

Templates (Read one, then build from it):

- `templates/base.html`: page scaffold with the house style.
- `templates/diagram.html`: diagram page, light (teaching) or dark (infra).
- `templates/editor.html`: editor skeleton (state, render, copy out).

Scripts (run with Bash; on Windows that is Git Bash, which Claude Code uses):

- `scripts/fetch-examples.sh`: downloads the 20 example pages once (needs git
  and internet the first time) and prints the folder they are in.
- `scripts/preview.sh shot <file.html>`: screenshots the page with a headless
  Chrome or Edge (Quick Look on a Mac that has neither) and prints the PNG
  path. `preview.sh open <file.html>` opens it in the user's browser.

## Workflow

1. **Pick the mode.** Match the request to one row of the table below:
   explainer, plan, report, code review, diagram, variants or editor. The mode
   decides the template, the references and the example to read.
2. **Read the matching example first, every time.** The gallery pages are the
   ground truth this skill is built on. A full example shows density, spacing
   and structure that no summary can.
   ```bash
   bash "${CLAUDE_SKILL_DIR}/scripts/fetch-examples.sh"
   ```
   It prints the examples folder. Read the file the table names for your mode,
   two if you combine modes. If the download fails (no internet, no git), go
   on with the references alone and tell the user you worked without the
   examples.
3. **Decide fidelity.** Quick throwaway or polished deliverable? See
   `references/fidelity-and-verify.md`. Don't over-polish a quick comparison;
   don't ship a sloppy report.
4. **Start from a template and the house style.** Read `templates/base.html`
   (or `diagram.html` / `editor.html`) and `references/house-style.md`. Reuse
   the `:root` tokens, never invent a new palette. Follow the structure of the
   example you read, adapted to the content. Don't copy it word for word.
5. **Write the file** with the Write tool. Save it where the user asked;
   otherwise in the current folder, with a short descriptive name
   (`checkout-plan.html`). Keep everything inline: one `<style>` in `<head>`,
   at most one `<script>` before `</body>`. No `<link>`, no web fonts, no CDN,
   no `<img src>` pointing to the internet. Graphics are inline SVG or CSS.
6. **Keep JavaScript optional.** Prefer none. When you need it, keep it small
   and plain, and make the page readable with JavaScript off (native
   `<details>`, anchor links, one tab or node active by default).
7. **Look at it.** Take a screenshot and Read the PNG:
   ```bash
   bash "${CLAUDE_SKILL_DIR}/scripts/preview.sh" shot <file.html>
   ```
   Fix what you see, then shoot again. Required for SVG diagrams, where
   hand-placed coordinates drift (overlapping boxes, arrows pointing at
   nothing). Fallbacks if no browser is found: `references/fidelity-and-verify.md`.
8. **Hand it over.** Open it for the user unless they said not to:
   ```bash
   bash "${CLAUDE_SKILL_DIR}/scripts/preview.sh" open <file.html>
   ```
   Give the full path so they can find it again, and mention any buttons
   (Copy, Reset).

## Core principles

**One design system, driven by tokens.** Warm paper (`--ivory`), near-black
ink (`--slate`), one terracotta accent (`--clay`), olive for success and
additions, a warm gray ramp. The same meaning everywhere: **clay = focus,
olive = success / added, rust = error / removed, oat = neutral fill,
gray-500 = secondary text and arrows.** Use colors only as `var(--…)`.

**Three fonts by role.** Serif (Georgia stack) for headings, sans (system-ui)
for body text, mono for every label, code, metric, eyebrow and path. All built
into the operating system, nothing to load. This split is the house signature.

**Self-contained, always.** The file must render offline when double-clicked.
Inline the style and script, draw graphics as inline SVG or CSS, never point
to a remote file. That is what makes it easy to share, and it keeps the
reader's browser from calling anyone.

**Works without JavaScript.** Most great artifacts have none. When
interaction is the point (sliders, drag, editors), the page still shows its
content without it, and the copy button works from a file opened on disk
(see `references/throwaway-editors.md`).

**Interactive artifacts end with an export.** An editor is only useful if it
hands its result back: a Copy as Markdown / JSON / diff / prompt button that
puts the result on the clipboard, ready to paste into the next message.

## Quick reference: mode → what to build

| Request | Mode | Template | Example to read | Key reference |
|---|---|---|---|---|
| "explain how X works" | explainer | base | `14-research-feature-explainer.html` | house-style, svg-diagrams |
| "write up the plan / spec" | plan | base | `16-implementation-plan.html` | house-style |
| "status / incident report" | report | base | `11-status-report.html`, `12-incident-report.html` | house-style |
| "review this PR / diff" | code review | base | `03-code-review-pr.html`, `17-pr-writeup.html` | house-style (diff section) |
| "diagram the architecture / pipeline" | infra diagram | diagram | `13-flowchart-diagram.html`, `04-code-understanding.html` | dark-tech, svg-diagrams |
| "diagram this concept / process" (science, physical, teaching) | concept diagram | diagram | `13-flowchart-diagram.html`, `10-svg-illustrations.html` | concept-archetypes, svg-diagrams |
| "show me N takes / compare options" | variants | base | `01-exploration-code-approaches.html`, `02-exploration-visual-designs.html` | fidelity-and-verify |
| "let me tune / triage / edit X and copy it out" | editor | editor | `18-editor-triage-board.html`, `19-editor-feature-flags.html`, `20-editor-prompt-tuner.html` | throwaway-editors |

## Pitfalls

- **Skipping the example.** Reading the matching gallery page before writing
  is the biggest quality lever. The references are the map; the examples are
  the territory. Writing from a vague memory of "good HTML" is how the output
  turns generic.
- **Inventing a palette.** Reuse the `:root` tokens from `house-style.md`. A
  new color scheme per file breaks the consistency that makes these look pro.
- **Reaching for a library.** No Mermaid, D3, Tailwind CDN, Prism or web
  fonts. Diagrams are hand-written SVG; syntax colors are hand-placed
  `<span>`s; the token block does the theming.
- **Skipping the screenshot on diagrams.** Hand-computed SVG coordinates are
  the #1 source of broken output: arrows landing in empty space, overlapping
  boxes, text spilling out. Look at the screenshot and fix before saying done.
- **Pasting outside text raw.** Text from a PR, a web page, a log or an email
  can contain `<`, `&` or even `<script>`. Escape it in the HTML (`&lt;`,
  `&amp;`) and set it from JavaScript with `textContent`, never `innerHTML`.
- **A JavaScript export where a static `<pre>` would do.** If the result is
  one snippet, a block the user can select and copy is the most reliable
  export.
- **Content that only exists in JavaScript.** If the text is built inside a
  `render()` call, the page is blank with JavaScript off. Put the real
  content in the HTML; use JavaScript to enhance it.

---

Adapted from https://github.com/anthropics/html-effectiveness (MIT). Also
draws on MIT-licensed work by Nous Research, Cocoon AI and Lex Christopherson.
Notices in `LICENSE`.
