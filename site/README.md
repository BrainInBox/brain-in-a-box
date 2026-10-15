# The download site

One static page, in French like the app: download the app, then a guide to each step and each piece of software. No build, no npm, no third-party script, no tracker, system fonts.

- `index.html`, `style.css` (the app's colours and type, dark mode included), `app.js`, `img/`.
- `app.js` asks GitHub's public API for the releases, keeps the newest `app-v<version>-preview.<n>` and points every download link at its files, found by their suffix (`_macos-universal.dmg`, `_windows-x64-setup.exe`, `_linux-x86_64.AppImage`, `_linux-amd64.deb`, `SHA256SUMS`). The big button is for the visitor's system. If the call fails (no network, the API's 60 calls an hour per address), the links stay on the Releases page and the page says which file to take. Without JavaScript, the three systems show one under the other.
- `vercel.json`: a strict CSP (scripts, styles and images from the site only; the only connection is `api.github.com`) and the usual security headers. `.vercelignore` keeps this README and `captures.sh` off the site.

## Publish

On Vercel, a project with **Root Directory** `site`, framework preset **Other**, no build command, no output directory. Or from this folder: `vercel` (preview), `vercel --prod`.

Locally: `python3 -m http.server -d site 8000`, then http://localhost:8000 (without the headers of `vercel.json`).

Nothing to redeploy when a new app preview comes out: the page finds it by itself.

## Update the captures

They come from the nightly field test (`.github/workflows/terrain.yml`), artifacts `captures-macos-app`, `captures-windows-app` and `captures-linux-app`:

```bash
run=$(gh run list -w terrain.yml -s completed -L 1 --json databaseId -q '.[0].databaseId')
for os in macos windows linux; do gh run download "$run" -n "captures-$os-app" -D "/tmp/captures/$os"; done
site/captures.sh /tmp/captures      # needs cwebp (brew install webp)
```

`captures.sh` crops each capture to the window that matters and writes `img/*.webp` (lossless-like, about 300 KB for all). Its table names the source capture, the crop and the output: when a screen of the app changes place, or a capture is renumbered, fix its line. Then check the page, and the texts and `alt` that describe the screens.
