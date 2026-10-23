#!/usr/bin/env python3
"""The installers install.ps1 pins (Git for Windows, Python), against their latest release.

When winget cannot bring Git or Python, install.ps1 runs their official installers at the versions it
pins, with their SHA-256: a newer release, a security fix among them, reaches nobody until the pins move.
Red (exit 1) once a newer release has been out for DELAI days, the delay the kit gives a release before
taking it; younger, a warning that says from which day. Python: the newest of the pinned minor only
(3.14.x), a new minor being a choice of its own. A source it cannot read is red too: nothing was checked.
Run every night by terrain.yml (job epingles), whose report then opens the issue "terrain".

    python3 -I .github/ci/epingles.py [--resume <file>]     # GH_TOKEN, when set, for GitHub's API
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

INSTALL_PS1 = Path(__file__).resolve().parent.parent.parent / "install.ps1"
DELAI = 14
GIT_API = "https://api.github.com/repos/git-for-windows/git/releases/latest"
PYTHON_API = "https://www.python.org/api/v2/downloads/release/?is_published=true"
NAMES = {"Git": "Git for Windows", "Python": "Python"}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "brain-in-a-box/epingles"})
    token = os.environ.get("GH_TOKEN", "")
    if token and url.startswith("https://api.github.com/"):
        # Unredirected: urllib would carry it along a redirect to any other host.
        req.add_unredirected_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def pins(text):
    """{"Git": "2.56.0.2", "Python": "3.14.8"}, as install.ps1's $OFFICIAL_INSTALLERS sets them."""
    return dict(re.findall(r'"(Git|Python)"\s*=\s*@\{\s*Version\s*=\s*"(\d+(?:\.\d+){2,3})"', text))


def vtuple(version):
    return tuple(int(x) for x in version.split("."))


def git_version(tag):
    """v2.56.0.windows.3 -> 2.56.0.3; the first build of a Git version has no fourth number (v2.57.0.windows.1 -> 2.57.0)."""
    m = re.fullmatch(r"v(\d+\.\d+\.\d+)\.windows\.(\d+)", tag or "")
    if not m:
        return None
    return m.group(1) if m.group(2) == "1" else "%s.%s" % m.groups()


def when(stamp):
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def latest_git(fetch, pinned):
    r = fetch(GIT_API)
    version = git_version(r["tag_name"])
    if not version:
        raise ValueError("dernière version illisible (%s)" % r["tag_name"])
    return version, when(r["published_at"]), r.get("html_url") or GIT_API


def latest_python(fetch, pinned):
    minor = vtuple(pinned)[:2]
    best = None
    for r in fetch(PYTHON_API):
        m = re.fullmatch(r"Python (\d+\.\d+\.\d+)", r.get("name") or "")
        if not m or r.get("pre_release") or vtuple(m.group(1))[:2] != minor:
            continue
        if best is None or vtuple(m.group(1)) > vtuple(best[0]):
            best = (m.group(1), when(r["release_date"]), "https://www.python.org/downloads/release/python-%s/"
                    % m.group(1).replace(".", ""))
    if best is None:
        raise ValueError("aucune version %d.%d publiée" % minor)
    return best


def check(name, pinned, latest, now):
    """(level, line): level 0 up to date, 1 newer but not yet due, 2 newer and due."""
    version, published, url = latest
    label = NAMES[name]
    if vtuple(version) <= vtuple(pinned):
        return 0, "%s : %s, à jour (dernière : %s du %s)" % (label, pinned, version, published.date())
    due = published + dt.timedelta(days=DELAI)
    if now < due:
        return 1, ("::warning::%s %s est sortie le %s ; install.ps1 épingle %s : à monter à partir du %s (délai de %d jours) %s"
                   % (label, version, published.date(), pinned, due.date(), DELAI, url))
    return 2, ("::error::%s %s est sortie le %s, il y a %d jours ; install.ps1 épingle encore %s : monter $OFFICIAL_INSTALLERS "
               "(version, URL et SHA-256 x64 et ARM64, chacune relevée à deux sources) %s"
               % (label, version, published.date(), (now - published).days, pinned, url))


def main(argv=None, fetch=fetch_json, now=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--install-ps1", default=str(INSTALL_PS1))
    p.add_argument("--resume", help="fichier où ajouter le tableau (GITHUB_STEP_SUMMARY)")
    opts = p.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    with open(opts.install_ps1, encoding="utf-8") as f:
        found = pins(f.read())
    worst, lines = 0, []
    for name, latest in (("Git", latest_git), ("Python", latest_python)):
        if name not in found:
            worst, line = 2, "::error::install.ps1 n'épingle plus %s ($OFFICIAL_INSTALLERS) : ce contrôle ne sait plus quoi comparer" % name
        else:
            try:
                level, line = check(name, found[name], latest(fetch, found[name]), now)
            except Exception as e:   # network, an API that changed: nothing checked
                level, line = 2, "::error::%s : impossible de lire %s : %s" % (
                    NAMES[name], GIT_API if name == "Git" else PYTHON_API, e)
            worst = max(worst, level)
        lines.append(line)
        print(line)
    if opts.resume:
        with open(opts.resume, "a", encoding="utf-8") as f:
            f.write("### Installeurs épinglés par install.ps1\n\n" + "".join("- %s\n" % re.sub(r"^::\w+::", "", l) for l in lines) + "\n")
    return 1 if worst == 2 else 0


if __name__ == "__main__":
    sys.exit(main())
