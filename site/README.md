# The download site

One static page, in French like the app: download the app, then a guide to each step and each piece of software. No build, no npm, no third-party script, font or tracker.

- `index.html`, `style.css` (dark only: near-black sections, framed grids, a label in mono capitals over each title), `app.js`, `img/`.
- `fonts/`: Inter (text), Inter Tight (titles) and JetBrains Mono (labels, commands), variable woff2, latin subset (accents, « », ’ and ›), served by the site itself: no request to Google Fonts, so no visitor's address leaves for a third party. They come from Fontsource on npm, through jsDelivr, at an exact version: `@fontsource-variable/inter@5.2.8`, `inter-tight@5.2.7`, `jetbrains-mono@5.2.8`, file `files/<name>-latin-wght-normal.woff2`. Their licence, the SIL Open Font License, sits next to them (`OFL-*.txt`) and the footer links to it. The hero's two fonts are preloaded.
- The animated tree (what Bibo installs) is CSS only, and stops, like Bibo's float, for a visitor who asks for less motion.
- The hero's Bibo is `img/bibo.svg` inline, so that style.css alone brings him out of his box (a shake, the lid pops, he springs out, blinks and waves, as in the app); without motion, the drawing as it is.
- `app.js` asks GitHub's public API for the releases, keeps the newest `app-v<version>-preview.<n>` and points every download link at its files, found by their suffix (`_macos-universal.dmg`, `_windows-x64-setup.exe`, `_linux-x86_64.AppImage`, `_linux-amd64.deb`, `SHA256SUMS`). The two big buttons (top and bottom of the page) are for the visitor's system, and the white bar at the very top names that preview and links to its release page. If the call fails (no network, the API's 60 calls an hour per address), the links stay on the Releases page, searched for `préversion` (the word in every app preview's title, set by `release.yml`), and the page says which file to take. Without JavaScript, the three systems show one under the other, in the install guide as in the command-line block.
- `vercel.json`: a strict CSP (scripts, styles, images and fonts from the site only, no inline style or script; the only connection is `api.github.com`) and the usual security headers. `.vercelignore` keeps this README and `captures.sh` off the site.

## Publish

Live at https://brain-in-a-box.vercel.app (Vercel project `brain-in-a-box`, deployed from this folder with the CLI). On Vercel, a project with **Root Directory** `site`, framework preset **Other**, no build command, no output directory. Or from this folder: `vercel` (preview), `vercel --prod`.

Locally: `python3 -m http.server -d site 8000`, then http://localhost:8000 (without the headers of `vercel.json`: a CSP mistake shows only on Vercel, so check the console of a preview deployment).

Nothing to redeploy when a new app preview comes out: the page finds it by itself.

Before publishing, check that the fallback link still lists the previews (GitHub's release search does not match every word: `app-v` finds nothing):

```bash
curl -sL 'https://github.com/BrainInBox/brain-in-a-box/releases?q=pr%C3%A9version&expanded=true' | grep -c 'releases/tag/app-v'   # 1 or more
```

## Update the captures

They come from the nightly field test (`.github/workflows/terrain.yml`), artifacts `captures-macos-app`, `captures-windows-app` and `captures-linux-app`:

```bash
run=$(gh run list -w terrain.yml -s completed -L 1 --json databaseId -q '.[0].databaseId')
for os in macos windows linux; do gh run download "$run" -n "captures-$os-app" -D "/tmp/captures/$os"; done
site/captures.sh /tmp/captures      # needs cwebp (brew install webp)
```

`captures.sh` crops each capture to the window that matters and writes `img/*.webp` (lossless-like, about 300 KB for all). Its table names the source capture, the crop and the output: when a screen of the app changes place, or a capture is renumbered, fix its line. Then check the page, and the texts and `alt` that describe the screens.
