# The download site

One static page, in French like the app, for people who have never opened a terminal: what Bibo's guide does, download the app, then each step and each piece of software (Windows first). No build, no npm, no third-party script, font or tracker.

- `index.html`, `style.css` (dark only: near-black sections, framed grids, a label in mono capitals over each title), `app.js`, `img/`.
- `fonts/`: Inter (text), Inter Tight (titles) and JetBrains Mono (labels, commands), variable woff2, latin subset (accents, « », ’ and ›), served by the site itself: no request to Google Fonts, so no visitor's address leaves for a third party. They come from Fontsource on npm, through jsDelivr, at an exact version: `@fontsource-variable/inter@5.2.8`, `inter-tight@5.2.7`, `jetbrains-mono@5.2.8`, file `files/<name>-latin-wght-normal.woff2`. Their licence, the SIL Open Font License, sits next to them (`OFL-*.txt`) and the footer links to it. The hero's two fonts are preloaded.
- The animated tree (what Bibo installs) is CSS only, and stops, like Bibo's float, for a visitor who asks for less motion.
- The hero's Bibo is `img/bibo.svg` inline, so that style.css alone brings him out of his box (a shake, the lid pops, he springs out, blinks and waves, as in the app); `app.js` sets `--gx`/`--gy` so his eyes follow the mouse, slowly. Without motion (or without JavaScript), the drawing as it is, looking ahead.
- Every download link points at the release `app-latest`, which `.github/workflows/release.yml` refreshes with each preview: the same files, named without their version (`Brain-in-a-Box_windows-x64-setup.exe`, `_macos-universal.dmg`, `_linux-x86_64.AppImage`, `_linux-amd64.deb`, `SHA256SUMS`). They are in the HTML as served, so they work without JavaScript and without GitHub's API. `app.js` turns the two big buttons (top and bottom) into the visitor's system, and asks the API (60 calls an hour per address) only for what the page says: the preview's number, date and sizes. After a click on a big button, "Ensuite" shows the next three steps for that system. On a phone, the hero says the app installs on a computer and offers to share or copy the link (`navigator.share`, else the clipboard); the download links are hidden. Without JavaScript, the three systems show one under the other, in the install guide as in the block for developers.
- `img/og.jpg` (1200×630, the picture a shared link shows) is drawn by `app/src-tauri/installer/images.py`, with the installers' pictures.
- `vercel.json`: a strict CSP (scripts, styles, images and fonts from the site only, no inline style or script; the only connection is `api.github.com`) and the usual security headers. `.vercelignore` keeps this README and `captures.sh` off the site.

## Publish

Live at https://brain-in-a-box.vercel.app (Vercel project `brain-in-a-box`, deployed from this folder with the CLI). On Vercel, a project with **Root Directory** `site`, framework preset **Other**, no build command, no output directory. Or from this folder: `vercel` (preview), `vercel --prod`.

Locally: `python3 -m http.server -d site 8000`, then http://localhost:8000 (without the headers of `vercel.json`: a CSP mistake shows only on Vercel, so check the console of a preview deployment).

Nothing to redeploy when a new app preview comes out: `app-latest` holds it.

The page quotes Bibo's buttons (« Guide-moi dans un logiciel »…) and shows its screens: publish it only once the app in `app-latest` has them, and its captures are redone from that app (below). The same the day a system's files get signed (`release.yml`): the page's words about Windows' and macOS's warnings change by hand.

- The names it gives Bibo's buttons are checked against `app/src/app.js` (`test_the_buttons_it_names_are_bibos`, in `engine/tests/test_release_workflow.py`): a button renamed in the app fails the test until the page says the new name.
- The guide's section says the capture is made and pasted at each step, as long as `guide.rs` only reads a pasted capture (`test_the_guide_says_how_the_window_is_shown`). The day Bibo sees the window by itself, say that instead, and drop the test.
- Edge's two questions for a file it rarely sees (« Conserver », then « Afficher plus › Conserver quand même ») are written from Edge's dialogs, not filmed by the field test (it marks the file as a browser would, without Edge): check them once on a Windows PC, in Edge, with the file of `app-latest`.

Before publishing, check that the buttons' files are there (a redirect to the file, not a 404):

```bash
for f in windows-x64-setup.exe macos-universal.dmg linux-x86_64.AppImage linux-amd64.deb; do
  curl -sI "https://github.com/BrainInBox/brain-in-a-box/releases/download/app-latest/Brain-in-a-Box_$f" | head -1
done   # HTTP/2 302, four times
```

## Update the captures

They come from the nightly field test (`.github/workflows/terrain.yml`), artifacts `captures-macos-app`, `captures-windows-app` and `captures-linux-app`, and the update's `captures-macos-app-mise-a-jour` and `captures-windows-app-mise-a-jour` (the end once Claude is connected, SmartScreen naming the site's file):

```bash
run=$(gh run list -w terrain.yml -s completed -L 1 --json databaseId -q '.[0].databaseId')
for a in macos-app windows-app linux-app macos-app-mise-a-jour windows-app-mise-a-jour; do
  gh run download "$run" -n "captures-$a" -D "/tmp/captures/${a/-app/}"
done
site/captures.sh /tmp/captures      # needs cwebp (brew install webp)
```

`captures.sh` crops each capture to the window that matters and writes `img/*.webp` (lossless-like, about 300 KB for all). Its table names the source capture (by its screen: the first capture of it), the crop and the output: when a screen of the app changes place, fix its line. Then check the page, and the texts and `alt` that describe the screens.
