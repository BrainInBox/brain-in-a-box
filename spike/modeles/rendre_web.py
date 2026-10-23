"""Rend les pages de ecrans_web.py hors écran, par un Chromium sans tête (Edge sur la VM, Chrome
ailleurs) : <id>.png (1280x800) et <id>.json (les éléments numérotés et la vérité, lus dans le DOM
rendu). Aucun réseau : la page est un fichier, et toute résolution de nom est refusée.

    python3 -I rendre_web.py <navigateur> <dossier des pages> <dossier de sortie>
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

W, H = 1280, 800


def chromium(nav, profil, *args):
    base = [nav, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
            "--window-size=%d,%d" % (W, H), "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--host-resolver-rules=MAP * ~NOTFOUND", "--user-data-dir=" + profil, "--lang=fr-FR",
            "--virtual-time-budget=1500"]
    return subprocess.run(base + list(args), capture_output=True, timeout=120)


def main(nav, pages, sortie):
    sortie.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profil:
        for html in sorted(pages.glob("*.html")):
            k = html.stem
            url = html.resolve().as_uri()
            png = sortie / (k + ".png")
            chromium(nav, profil, "--screenshot=" + str(png.resolve()), url)
            dom = chromium(nav, profil, "--dump-dom", url).stdout.decode("utf-8", "replace")
            m = re.search(r'<script type="application/json" id="bibo-elements">(.*?)</script>', dom, re.S)
            if not m or not png.is_file():
                raise SystemExit("%s : rendu raté" % k)
            info = json.loads(m.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
            if png.read_bytes()[16:24] != W.to_bytes(4, "big") + H.to_bytes(4, "big"):
                raise SystemExit("%s : la capture n'est pas en %dx%d" % (k, W, H))
            (sortie / (k + ".json")).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
            print(k, len(info["elements"]), "éléments, vérité", info["verite"], info["zones"])


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
