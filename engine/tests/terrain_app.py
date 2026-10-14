#!/usr/bin/env python3
"""Field test "app": the desktop app as a user gets it, clicked through in its real window.

On a fresh GitHub VM (terrain.yml, jobs app-macos and app-windows), the robot gets the app the
way a user does, installs it, opens it and clicks through its real window, Bienvenue to the final
screen, with a capture of every screen. Then it checks the brain works and opens the app a second
time: it must say everything is already installed.

    python3 -I engine/tests/terrain.py app --vm --parcours app --source release --captures DIR
    python3 -I engine/tests/terrain.py app --vm --parcours app --source build --fichier APP.dmg --captures DIR
    python -I engine/tests/terrain.py app --vm --parcours app --source build --fichier SETUP.exe --stub DIR ...
    python3 -I engine/tests/terrain_app.py source --ref <ref>       # for the workflow: release or build

Where the app comes from: on master (the scheduled night), the newest app-v*-preview.* release,
downloaded with its SHA256SUMS and its attestation checked, as a careful user would. A branch has
no preview: the workflow builds the app from the tested commit (as release.yml does) and the app
installs the brain from that commit's sources (BIAB_ARCHIVE), not from master's zip.

The robot never gives the app a token: the app and everything it starts (the kit's installers,
gbrain, Bun) get a fresh user's environment. GH_TOKEN, read-only, only lists the releases and
checks the attestation. At the Claude login the robot clicks « Plus tard »: the real Claude is
the job `claude`'s business.

A driver (Pilote) reads the window (Bibo's title and text, the buttons), clicks a button by its
label, captures the screen. PiloteMac: System Events (accessibility) and screencapture, which
GitHub's macOS runners allow. PiloteWindows: UI Automation and a capture of the screen, through
pilote_windows.cs compiled on the VM by Windows' own .NET Framework; the installer's pages are
clicked the same way. On Windows the VM is first made a PC without Python (poste-neuf.ps1): the
app has to bring it.

This module imports nothing of terrain.py: terrain.py passes itself in (kit), so a failure is
always terrain.py's own Echec, even when terrain.py runs as __main__.
"""
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_SLUG = "BrainInBox/brain-in-a-box"
PREVIEW_RE = re.compile(r"^app-v(\d+)\.(\d+)\.(\d+)-preview\.(\d+)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCES = ("release", "build")
# The marker the robot adds to the sources it hands the app on a branch: the app used them.
MARQUEUR = ".terrain-ref"

POLL = 0.5
LECTURE_PLEINE = 15         # between two quick reads of Bibo's words, a full read of the window at least this often
CAPTURE_ATTENTE = 120       # a capture every 2 min while a screen waits
CLIC_RELANCE = 20           # a click that changed nothing is tried again after this
CLIC_ESSAIS = 3
INCONNU_MAX = 180           # a screen the robot does not know, for this long: a failure
DELAI_FENETRE = 120
DELAI_PREMIER = 75 * 60     # Bienvenue to the final screen (Apple's tools up to 45 min, the brain step 40)
DELAI_RELANCE = 5 * 60
# How every click is done: the mouse on the button, as a user clicks. Anything else (AXPress,
# Invoke) proves no user could have clicked there: the scenario fails, once the walk is over.
CLIC_SOURIS = "clic souris"

# Bibo's titles (app/src/app.js), after norm(): the screen they stand for.
ECRANS = (
    ("bienvenue", "Salut, moi c'est Bibo"),
    ("diagnostic-en-cours", "Je regarde ton "),
    ("diagnostic-impossible", "Je n'ai pas réussi à examiner"),
    ("bloque", "Je ne peux pas installer"),
    ("deja-installe", "Tout est déjà installé"),
    ("diagnostic", "J'ai fait le tour"),
    ("voyage", "Une option avant de commencer"),
    ("verif-compte", "Je vérifie ton compte Claude"),
    ("connexion-installation", "Connecte ton compte Claude maintenant"),
    ("connexion", "Dernière étape : ton compte Claude"),
    ("installe", "C'est installé"),
    ("fini", "Ton second cerveau est prêt"),
    ("echec", "Oups, ça a coincé"),
    ("hors-app", "Ouvre l'app Brain in a Box"),
)
# The install screen: Bibo's title while a step runs (STEPS[id].say[0]).
ETAPES = {
    "J'installe Claude Code.": "claude",
    "Une fenêtre Apple va s'ouvrir : clique sur Installer.": "clt",
    "J'installe Bun.": "bun",
    "J'installe Obsidian.": "obsidian",
    "Je prépare ton cerveau.": "brain",
    "J'active la recherche par le sens.": "voyage",
}
# Screens that end the run, whatever the robot was doing.
ARRETS = {"diagnostic-impossible", "bloque", "echec", "hors-app"}
# Buttons that only come with a failure (the repair screens have many titles).
BOUTONS_ECHEC = {"Réessayer", "Copier le rapport", "Revérifier"}


class ErreurPilote(Exception):
    """The driver could not do what was asked: the scenario turns it into a failure of its step."""


def norm(text):
    """The words as Bibo shows them, comparable: no-break spaces, curly apostrophes, … made plain."""
    s = unicodedata.normalize("NFC", str(text or ""))
    s = s.translate({0xA0: " ", 0x202F: " ", 0x2007: " ", 0x2018: "'", 0x2019: "'", 0x2026: "..."})
    return re.sub(r"\s+", " ", s).strip()


def slug(text, cap=40):
    s = unicodedata.normalize("NFKD", norm(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:cap].strip("-") or "ecran"


def nom_capture(n, ecran):
    """NN-<écran>.png: the captures sort in the order they were taken."""
    return "%02d-%s.png" % (n, slug(ecran))


def choisir_source(ref):
    """master (the scheduled night, or a run by hand on master): the published preview. Elsewhere,
    no preview exists for that commit: the app is built from it."""
    return "release" if ref == "master" else "build"


# --------------------------------------------------------------------------- the window, as data

class Ecran:
    """What the robot sees: Bibo's title and text, the buttons (label, enabled), every text."""

    def __init__(self, fenetre=False, titre="", texte="", boutons=(), textes=(), dialogues=(), lecture="", statut=None,
                 partiel=False):
        self.fenetre, self.titre, self.texte = fenetre, norm(titre), norm(texte)
        # Bibo's words only (a quick read saw them change): the full read comes next.
        self.partiel = partiel
        # (window index, child indices from the window) of Bibo's status region: a quick read's path.
        self.statut = statut
        self.boutons = [(norm(b), bool(on)) for b, on in boutons]
        self.textes = [norm(x) for x in textes if norm(x)]
        self.dialogues = list(dialogues)
        self.lecture = lecture

    def bouton(self, libelle):
        """True if the button is there and enabled, False if there but disabled, None if absent."""
        for b, on in self.boutons:
            if b == norm(libelle):
                return on
        return None

    def resume(self):
        return {"titre": self.titre, "texte": self.texte, "boutons": [b for b, _ in self.boutons],
                "textes": self.textes[:40]}


def cle_ecran(ecran):
    """(screen, step id or None) of what the window shows."""
    if not ecran.fenetre:
        return "sans-fenetre", None
    if not ecran.titre:
        return "chargement", None
    if ecran.titre in ETAPES:
        return "etape-" + ETAPES[ecran.titre], ETAPES[ecran.titre]
    for key, start in ECRANS:
        if ecran.titre.startswith(norm(start)):
            return key, None
    if any(ecran.bouton(b) is not None for b in BOUTONS_ECHEC):
        return "echec", None
    return "inconnu", None


def _aplatir(node, chemin=(), out=None):
    """[(path of child indices, node)] in document order."""
    out = [] if out is None else out
    out.append((chemin, node))
    for i, kid in enumerate(node.get("k") or []):
        _aplatir(kid, chemin + (i,), out)
    return out


def _texte(node):
    return node.get("v") if isinstance(node.get("v"), str) and node.get("v").strip() else node.get("n") or ""


def ecran_depuis_arbre(fenetres, dialogues=()):
    """The Ecran of the app's accessibility tree (PiloteMac's dump: r role, s subrole, n name,
    v value, d description, e enabled, k children). Bibo speaks in the status region (role=status:
    AXApplicationStatus); without it, the first text of the page and its neighbours stand in."""
    web, chemin_statut = None, None
    for wi, f in enumerate(fenetres or []):
        flat = _aplatir(f.get("arbre") or {})
        web = next((node for _, node in flat if node.get("r") == "AXWebArea"), None)
        if web:
            chemin_statut = next(((wi, c) for c, node in flat if node.get("s") == "AXApplicationStatus"), None)
            break
    if web is None:
        return Ecran(fenetre=bool(fenetres), dialogues=dialogues)
    flat = _aplatir(web)
    textes = [(c, _texte(n)) for c, n in flat if n.get("r") == "AXStaticText" and _texte(n).strip()]
    boutons = [(n.get("n") or n.get("d") or _texte(n), n.get("e", True) is not False)
               for c, n in flat if n.get("r") == "AXButton"]
    statut = next((c for c, n in flat if n.get("s") == "AXApplicationStatus"), None)
    if statut is not None:
        dits = [t for c, t in textes if c[:len(statut)] == statut]
        lecture = "statut"
    elif textes:
        # The hero comes first in the page: its title, and the texts that share its grandparent.
        haut = textes[0][0][:-2]
        dits = [t for c, t in textes if c[:len(haut)] == haut]
        lecture = "repli"
    else:
        dits, lecture = [], "vide"
    return Ecran(fenetre=True, titre=dits[0] if dits else "", texte=" ".join(dits[1:]), boutons=boutons,
                 textes=[t for _, t in textes], dialogues=dialogues, lecture=lecture, statut=chemin_statut)


def dits_rapides(textes):
    """(title, text) of a quick read: the texts of Bibo's region, in order."""
    dits = [norm(t) for t in textes or [] if isinstance(t, str) and norm(t)]
    return (dits[0], " ".join(dits[1:])) if dits else None


def dialogue_depuis_arbre(processus, arbre):
    flat = _aplatir(arbre or {})
    return {"processus": processus,
            "textes": [norm(_texte(n)) for _, n in flat if n.get("r") in ("AXStaticText", "AXTextArea") and _texte(n).strip()],
            "boutons": [norm(n.get("n") or n.get("d") or "") for _, n in flat if n.get("r") == "AXButton"
                        and (n.get("n") or n.get("d"))]}


# The one request the app announces (« macOS va peut-être demander l'accès à ton dossier Documents :
# clique sur « Autoriser » »): its folder Documents, where the brain lives. The robot accepts that
# one only. Any other dialog naming the app (Contacts, Automation, a password, a refusal) is a new
# thing asked of users: the night fails on it, with its words, and never answers it.
AUTORISER = ("Allow", "Autoriser", "OK")
NOMME_APP = re.compile(r"Brain in a Box|brain-in-a-box")
DOCUMENTS = re.compile(r"\bDocuments\b")
JAMAIS = ("SecurityAgent",)     # a password asked: never typed, never clicked
# Apple's window for its command line tools (the app's step « Outils Apple »): the user clicks
# « Installer », accepts the license, and closes it once the tools are in (« Terminé »).
OUTILS_APPLE = "Install Command Line Developer Tools"
APPLE_SUITE = (("Install", "Installer"), ("Agree", "Accepter"))
APPLE_FIN = ("Done", "Terminé", "OK")
APPLE_FAIT = re.compile(r"(?i)\binstall(ed|é)\b|a été installé")


def _texte_dialogue(dialogue):
    return " ".join(dialogue.get("textes") or [])


def bouton_autoriser(dialogue):
    """The button that accepts the request the app announces (its folder Documents), or None."""
    texte = _texte_dialogue(dialogue)
    if dialogue.get("processus") in JAMAIS or not NOMME_APP.search(texte) or not DOCUMENTS.search(texte):
        return None
    return next((b for b in AUTORISER if b in (dialogue.get("boutons") or [])), None)


def reponse_dialogue(dialogue):
    """What a user does with a system dialog: ("cliquer", label), ("echec", why) or None (leave it)."""
    texte, boutons = _texte_dialogue(dialogue), dialogue.get("boutons") or []
    if dialogue.get("processus") == OUTILS_APPLE:
        for labels in APPLE_SUITE:
            label = next((b for b in labels if b in boutons), None)
            if label:
                return ("cliquer", label)
        fin = next((b for b in APPLE_FIN if b in boutons), None)
        if fin and APPLE_FAIT.search(texte):
            return ("cliquer", fin)
        if fin:
            return ("echec", "la fenêtre d'Apple pour ses outils dit : « %s »" % texte[:300])
        return None     # downloading, installing: the user waits
    bouton = bouton_autoriser(dialogue)
    if bouton:
        return ("cliquer", bouton)
    if NOMME_APP.search(texte):
        return ("echec", "demande système inattendue au nom de l'app (%s) : « %s »" % (dialogue.get("processus"), texte[:300]))
    return None


def deja_presents(textes, labels):
    """[(label, detail)] of the diagnosis' lines in `labels` that say OK: on the VM before the app."""
    out = []
    for i, x in enumerate(textes or []):
        if x in labels and i + 1 < len(textes) and textes[i + 1] == ": OK":
            out.append((x, textes[i + 2] if i + 2 < len(textes) else ""))
    return out


def etapes_manquees(vus):
    """Why the install plan was not all seen, or None: every step gets a screen of its own and its
    capture. Bibo says « Étape i sur n » on each step but Apple's tools, which counts in n all the same."""
    etapes = sorted(cle for cle in vus if cle.startswith("etape-"))
    if not etapes:
        return "aucun écran d'étape d'installation n'a été vu (pas de capture)"
    totals = [int(m.group(1)) for cle in etapes for m in [re.search(r"Étape \d+ sur (\d+)", vus[cle].texte)] if m]
    total = max(totals) if totals else len(etapes)
    if len(etapes) >= total:
        return None
    return "%d étape(s) sur %d jamais vue(s) à l'écran, donc sans capture (vues : %s)" % (
        total - len(etapes), total, ", ".join(etapes))


# --------------------------------------------------------------------------- the release, as data

def derniere_preview(releases):
    """The newest app preview of a REST listing (as release.yml prunes them: by creation date)."""
    best = None
    for r in releases or []:
        if not isinstance(r, dict) or r.get("draft") or not r.get("prerelease"):
            continue
        m = PREVIEW_RE.match(str(r.get("tag_name") or ""))
        if not m:
            continue
        key = (str(r.get("created_at") or ""), int(m.group(4)))
        if best is None or key > best[0]:
            best = (key, r)
    return best[1] if best else None


def asset(release, suffixe, tag):
    """(name, url, size, digest) of the release's file ending with suffixe, only from this
    repository's release download URLs."""
    prefix = "https://github.com/%s/releases/download/%s/" % (REPO_SLUG, tag)
    found = [a for a in release.get("assets") or [] if isinstance(a, dict) and str(a.get("name", "")).endswith(suffixe)]
    if len(found) != 1:
        return None
    a = found[0]
    url = str(a.get("browser_download_url") or "")
    if not url.startswith(prefix) or "/" in url[len(prefix):]:
        return None
    return a["name"], url, int(a.get("size") or 0), str(a.get("digest") or "")


def somme_attendue(sums, name):
    """The sha256 SHA256SUMS gives for name (sha256sum's format), or None."""
    for line in str(sums).splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and SHA256_RE.match(parts[0]):
            return parts[0]
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def env_utilisateur(home, extra=None):
    """What an app opened from the Finder gets from launchd: no token, no CI variable, the
    system's PATH. extra: BIAB_ARCHIVE on a branch."""
    user = os.environ.get("USER") or Path(home).name
    env = {"HOME": str(home), "USER": user, "LOGNAME": user, "SHELL": "/bin/zsh",
           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": os.environ.get("TMPDIR") or "/tmp/"}
    if os.environ.get("__CF_USER_TEXT_ENCODING"):
        env["__CF_USER_TEXT_ENCODING"] = os.environ["__CF_USER_TEXT_ENCODING"]
    env.update(extra or {})
    return env


# --------------------------------------------------------------------------- drivers

class Pilote:
    """What the walk through the window needs from a system. machine: the word the app uses
    (« Vérifier mon Mac »); suffixe: the release file for this system."""

    machine = "?"
    suffixe = None
    # The diagnosis' lines for what a fresh machine lacks and the app installs: OK on the VM, the
    # app's step for it went untested, and the results say so.
    neufs = ()

    def _run(self, argv, timeout=120, env=None, creationflags=0):
        try:
            p = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout, env=env, creationflags=creationflags)
        except subprocess.TimeoutExpired:
            raise ErreurPilote("%s n'a pas fini en %d s" % (Path(str(argv[0])).name, timeout))
        except OSError as e:
            raise ErreurPilote("%s ne se lance pas (%s)" % (Path(str(argv[0])).name, e.strerror or type(e).__name__))
        return p.returncode, p.stdout.decode("utf-8", "replace")

    def marquer_telecharge(self, fichier):
        """Marks the file as a browser does once it downloaded it."""
        raise NotImplementedError

    def installer(self, fichier, details, capturer=None):
        """Installs the app from the downloaded file as a user does; returns notes (list).
        capturer(name): a capture of the screen now, for an installer with screens of its own."""
        raise NotImplementedError

    def environnement(self, home, extra=None):
        """What the app gets when the user opens it."""
        return env_utilisateur(home, extra)

    def outils(self, home):
        """[(name, [places])]: what the app's steps install apart from the brain, found at one place."""
        raise NotImplementedError

    def ecarter_la_nuit(self):
        """Keeps the nightly the install registered out of the checks' way; returns what it did, or None."""
        return None

    def rendre_la_nuit(self):
        """Gives the nightly back to the scheduler (what ecarter_la_nuit did, undone); returns what it did."""
        return None

    def nouvel_ecran(self):
        """A new screen in the app's window: nothing of the last one's clicks counts for it."""

    def noter_clic(self, ecran, bouton, comment):
        """A click outside the app's window (an installer, a system dialog), kept as Parcours keeps its own."""
        self.__dict__.setdefault("clics_installation", []).append({"ecran": ecran, "bouton": bouton, "comment": comment})

    def ouvrir(self, env):
        raise NotImplementedError

    def vivante(self):
        """The app's process is running."""
        raise NotImplementedError

    def fermer(self):
        """Quits the app as a user does; returns how."""
        raise NotImplementedError

    def lire(self):
        """The Ecran now (Ecran(fenetre=False) when the app has no window)."""
        raise NotImplementedError

    def rapide(self):
        """(title, text) of Bibo in a cheap read, or None when the driver has none: then every
        reading of the window is a full one."""
        return None

    def cliquer(self, libelle, processus=None):
        """Clicks the enabled button with this label (in the app, or in a system dialog's process)."""
        raise NotImplementedError

    def capturer(self, chemin):
        raise NotImplementedError

    def arbre(self):
        """The raw accessibility tree, kept next to the captures when the run fails."""
        return None

    def attendre(self, predicat, delai, horloge=time.monotonic, dormir=time.sleep, pas=1.0):
        fin = horloge() + delai
        while True:
            v = predicat()
            if v or horloge() >= fin:
                return v
            dormir(pas)


JXA = r'''
function run(argv) {
  var req = JSON.parse(argv[0]);
  var se = Application('System Events');
  var out = req.op === 'cliquer' ? cliquer(se, req) : req.op === 'rapide' ? rapide(se, req) : lire(se, req);
  return enc(out);
}

// ASCII only on the way out: osascript's output encoding never matters.
function enc(o) {
  return JSON.stringify(o).replace(/[\u007f-\uffff]/g, function (c) {
    return '\\u' + ('000' + c.charCodeAt(0).toString(16)).slice(-4);
  });
}

function prop(el, name) {
  try { var v = el[name](); return v === undefined ? null : v; } catch (e) { return null; }
}

function str(v) { return typeof v === 'string' ? v : ''; }

function norm(s) {
  return str(s).normalize('NFC').replace(/[\u00a0\u202f\u2007]/g, ' ').replace(/[\u2018\u2019]/g, "'")
    .replace(/\s+/g, ' ').trim();
}

// One Apple event for a property of all the children at once; per child when the batch fails
// (an element without that property can fail the whole batch).
function batch(spec, name, count) {
  try {
    var v = spec[name]();
    if (v && v.length === count) return v;
  } catch (e) {}
  return null;
}

// A text has no children; a button's label is its name (its text only when it has none).
function feuille(n) {
  return n.r === 'AXStaticText' || (n.r === 'AXButton' && (n.n || n.d));
}

function enfants(el, depth, budget) {
  var out = [];
  var spec = el.uiElements;
  var count = 0;
  try { count = spec.length; } catch (e) { return out; }
  if (!count || depth > 40) return out;
  var roles = batch(spec, 'role', count), subs = batch(spec, 'subrole', count);
  var names = batch(spec, 'name', count), values = batch(spec, 'value', count);
  for (var i = 0; i < count && budget.left > 0; i++) {
    budget.left -= 1;
    var k = spec[i];
    var n = {r: str(roles ? roles[i] : prop(k, 'role')), s: str(subs ? subs[i] : prop(k, 'subrole')),
             n: str(names ? names[i] : prop(k, 'name')), v: str(values ? values[i] : prop(k, 'value')), k: []};
    if (n.r === 'AXButton') {
      n.d = str(prop(k, 'description'));
      n.e = prop(k, 'enabled') !== false;
    }
    if (!feuille(n)) n.k = enfants(k, depth + 1, budget);
    out.push(n);
  }
  return out;
}

function fenetres(p, budget) {
  var out = [];
  var ws = p.windows;
  var count = 0;
  try { count = ws.length; } catch (e) {}
  for (var i = 0; i < count; i++) {
    var w = ws[i];
    var arbre = {r: str(prop(w, 'role')), s: str(prop(w, 'subrole')), n: str(prop(w, 'name')), v: '', k: []};
    arbre.k = enfants(w, 1, {left: budget});
    out.push({titre: arbre.n, arbre: arbre});
  }
  return out;
}

function lire(se, req) {
  var res = {app: null, dialogues: [], devant: ''};
  var running = [];
  try { running = se.processes.name(); } catch (e) {}
  if (running.indexOf(req.processus) >= 0) res.app = fenetres(se.processes.byName(req.processus), 900);
  try { res.devant = se.processes.whose({frontmost: true}).name()[0] || ''; } catch (e) {}
  var names = (req.dialogues || []).slice();
  // Whatever came to the front over the app is shown too: an unknown dialog gets a capture.
  if (res.devant && res.devant !== req.processus && (req.ignorer || []).indexOf(res.devant) < 0 &&
      names.indexOf(res.devant) < 0) names.push(res.devant);
  names.forEach(function (name) {
    if (running.indexOf(name) < 0) return;
    fenetres(se.processes.byName(name), 200).forEach(function (f) {
      res.dialogues.push({processus: name, titre: f.titre, arbre: f.arbre});
    });
  });
  return res;
}

// Bibo's words only, from the path of the last full read: the texts of its region's paragraphs.
function rapide(se, req) {
  try {
    var el = se.processes.byName(req.processus).windows[req.fenetre];
    req.chemin.forEach(function (i) { el = el.uiElements[i]; });
    if (str(el.subrole()) !== 'AXApplicationStatus') return {ok: false};
    var out = [];
    [el.uiElements.uiElements.value(), el.uiElements.uiElements.name()].some(function (rows) {
      (rows || []).forEach(function (row) {
        (row || []).forEach(function (x) { if (typeof x === 'string' && x.trim()) out.push(x); });
      });
      return out.length > 0;
    });
    return {ok: true, textes: out};
  } catch (e) {
    return {ok: false};
  }
}

function chercher(el, label, depth) {
  var spec = el.uiElements;
  var count = 0;
  try { count = spec.length; } catch (e) { return null; }
  if (!count || depth > 40) return null;
  var roles = batch(spec, 'role', count), names = batch(spec, 'name', count);
  for (var i = 0; i < count; i++) {
    var k = spec[i];
    var role = str(roles ? roles[i] : prop(k, 'role'));
    if (role === 'AXStaticText') continue;
    if (role === 'AXButton') {
      if (norm(names ? names[i] : prop(k, 'name')) === label || norm(prop(k, 'description')) === label) return k;
      continue;
    }
    var found = chercher(k, label, depth + 1);
    if (found) return found;
  }
  return null;
}

// The mouse on the middle of the button, pressed and released, as a user clicks: its app made
// active, the button scrolled into view (as a user scrolls), inside its window, and nothing of
// another program over it there (the accessibility hit test, as WindowFromPoint on Windows). A
// null when done, else why not.
function souris(se, p, w, b) {
  try { p.frontmost = true; } catch (e) {}
  try { b.actions.byName('AXScrollToVisible').perform(); } catch (e) {}
  delay(0.3);
  var pos = prop(b, 'position'), size = prop(b, 'size'), wp = prop(w, 'position'), wsz = prop(w, 'size');
  if (!pos || !size || !(size[0] > 0 && size[1] > 0)) return 'pas de position';
  var x = pos[0] + size[0] / 2, y = pos[1] + size[1] / 2;
  if (wp && wsz && (x < wp[0] || y < wp[1] || x > wp[0] + wsz[0] || y > wp[1] + wsz[1])) return 'hors de sa fenetre';
  ObjC.import('ApplicationServices');
  var hit = Ref(), pid = Ref();
  if ($.AXUIElementCopyElementAtPosition($.AXUIElementCreateSystemWide(), x, y, hit) !== 0 ||
      $.AXUIElementGetPid(hit[0], pid) !== 0) return 'rien sous le point du bouton';
  if (pid[0] !== p.unixId()) {
    var dessus = '?';
    try { dessus = se.processes.whose({unixId: pid[0]}).name()[0] || '?'; } catch (e) {}
    return 'un autre programme est sur le bouton (' + dessus + ')';
  }
  ObjC.import('CoreGraphics');
  var pt = $.CGPointMake(x, y);
  // kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp (a single click: field
  // kCGMouseEventClickState 1), at the HID level (kCGHIDEventTap).
  [5, 1, 2].forEach(function (type) {
    var ev = $.CGEventCreateMouseEvent($(), type, pt, 0);
    if (type !== 5) $.CGEventSetIntegerValueField(ev, 1, 1);
    $.CGEventPost(0, ev);
    delay(type === 5 ? 0.15 : 0.08);
  });
  return null;
}

function cliquer(se, req) {
  var running = [];
  try { running = se.processes.name(); } catch (e) {}
  if (running.indexOf(req.processus) < 0) return {ok: false, pourquoi: 'processus'};
  var p = se.processes.byName(req.processus);
  var label = norm(req.libelle);
  var ws = p.windows;
  var count = 0;
  try { count = ws.length; } catch (e) {}
  for (var i = 0; i < count; i++) {
    var b = chercher(ws[i], label, 0);
    if (!b) continue;
    if (prop(b, 'enabled') === false) return {ok: false, pourquoi: 'inactif'};
    var pourquoi = souris(se, p, ws[i], b);
    if (!pourquoi) return {ok: true, comment: 'clic souris'};
    // Pressed through accessibility so the walk goes on and shows the rest: the scenario fails all the same.
    try { b.actions.byName('AXPress').perform(); return {ok: true, comment: 'AXPress (pas de clic souris : ' + pourquoi + ')'}; } catch (e) {}
    return {ok: false, pourquoi: 'ni souris ni AXPress (' + pourquoi + ')'};
  }
  return {ok: false, pourquoi: 'absent'};
}
'''


class PiloteMac(Pilote):
    """System Events (the accessibility tree WebKit gives the app's page) and screencapture.
    GitHub's macOS runners grant both to /bin/bash and /usr/bin/osascript (TCC)."""

    machine = "Mac"
    suffixe = "_macos-universal.dmg"
    neufs = ("Outils Apple (git, python3)",)
    processus = "brain-in-a-box"
    app = Path("/Applications/Brain in a Box.app")
    DIALOGUES = ("UserNotificationCenter", "CoreServicesUIAgent", "SecurityAgent", "universalAccessAuthWarn", OUTILS_APPLE)
    GATEKEEPER = "CoreServicesUIAgent"
    FERMER_REFUS = ("Done", "Terminé", "OK")
    # In front, yet no dialog: the desktop itself.
    IGNORER = ("Finder", "Dock", "SystemUIServer", "WindowManager", "ControlCenter", "loginwindow")

    def __init__(self, travail):
        self.travail = Path(travail)
        self.script = self.travail / "pilote-mac.js"
        self.script.write_text(JXA, encoding="utf-8")
        self.dernier_arbre = None
        self.statut = None

    def _osa(self, req, timeout=90):
        rc, out = self._run(["/usr/bin/osascript", "-l", "JavaScript", self.script, json.dumps(req)], timeout=timeout)
        if rc != 0:
            raise ErreurPilote("System Events ne répond pas (osascript code %d : %s)" % (rc, out.strip()[-300:]))
        try:
            return json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ErreurPilote("réponse illisible de System Events : %s" % out.strip()[-200:])

    def marquer_telecharge(self, fichier):
        # What Safari writes on a download: flags;hex time;agent;event id.
        value = "0083;%08x;Safari;%s" % (int(time.time()), str(uuid.uuid4()).upper())
        rc, out = self._run(["/usr/bin/xattr", "-w", "com.apple.quarantine", value, fichier], timeout=60)
        if rc != 0:
            raise ErreurPilote("xattr n'a pas posé la quarantaine : %s" % out.strip()[-200:])
        return value

    def quarantaine(self, chemin):
        rc, out = self._run(["/usr/bin/xattr", "-p", "com.apple.quarantine", chemin], timeout=60)
        return out.strip() if rc == 0 else None

    def monte(self, fichier):
        """Where the image of fichier is mounted, or None."""
        rc, out = self._run(["/usr/bin/hdiutil", "info", "-plist"], timeout=60)
        try:
            info = plistlib.loads(out.encode("utf-8")[out.encode("utf-8").index(b"<?xml"):]) if rc == 0 else {}
        except (ValueError, plistlib.InvalidFileException):
            return None
        want = os.path.realpath(str(fichier))
        for image in info.get("images") or []:
            if os.path.realpath(str(image.get("image-path") or "")) != want:
                continue
            for entity in image.get("system-entities") or []:
                if entity.get("mount-point"):
                    return Path(entity["mount-point"])
        return None

    def fenetres_finder(self):
        rc, out = self._run(["/usr/bin/osascript", "-e", 'tell application "System Events" to get name of windows of '
                             'process "Finder"'], timeout=30)
        return out.strip() if rc == 0 else ""

    def installer(self, fichier, details, capturer=None):
        """The DMG opened as a double click opens it (the Finder shows its window, captured), the app
        copied into Applications, then Gatekeeper: macOS refuses the unsigned preview (its dialog
        captured and closed, as a user reads it), and the robot lifts the quarantine where a user
        clicks « Ouvrir quand même » in Réglages."""
        notes = []
        if self.app.exists():
            raise ErreurPilote("%s existe déjà : la VM n'est pas neuve" % self.app)
        rc, out = self._run(["/usr/bin/open", fichier], timeout=120)
        mnt = self.attendre(lambda: self.monte(fichier), 120)
        if not mnt:
            raise ErreurPilote("le DMG ne s'ouvre pas (open code %d : %s)" % (rc, out.strip()[-200:]))
        details["dmg_monte"] = str(mnt)
        try:
            if not self.attendre(lambda: mnt.name in self.fenetres_finder(), 30):
                notes.append("le Finder n'a pas montré la fenêtre du DMG (capture du bureau quand même)")
            if capturer:
                details["capture_dmg"] = capturer("dmg")
            dans = mnt / self.app.name
            if not dans.is_dir():
                raise ErreurPilote("le DMG ne contient pas %s" % self.app.name)
            if not (mnt / "Applications").is_symlink():
                raise ErreurPilote("le DMG n'a pas le raccourci Applications où glisser l'app")
            details["quarantaine_dans_le_dmg"] = bool(self.quarantaine(dans))
            rc, out = self._run(["/usr/bin/ditto", dans, self.app], timeout=300)
            if rc != 0:
                raise ErreurPilote("copie dans Applications impossible : %s" % out.strip()[-200:])
        finally:
            self._run(["/usr/bin/hdiutil", "detach", mnt, "-force"], timeout=120)
        rc, out = self._run(["/usr/bin/codesign", "-dv", self.app], timeout=60)
        details["signature"] = next((l.split("=", 1)[1] for l in out.splitlines() if l.startswith("Signature=")), "?")

        if not self.quarantaine(self.app):
            # A Finder copy from a quarantined image keeps the mark: a plain copy may not.
            mark = self.quarantaine(fichier)
            if not mark:
                raise ErreurPilote("le DMG téléchargé n'a plus sa quarantaine")
            self._run(["/usr/bin/xattr", "-w", "com.apple.quarantine", mark, self.app], timeout=60)
            notes.append("quarantaine reportée sur l'app par le robot (la copie ne l'avait pas gardée)")
        details["quarantaine_app"] = bool(self.quarantaine(self.app))

        rc, status = self._run(["/usr/sbin/spctl", "--status"], timeout=60)
        details["gatekeeper"] = status.strip()
        if "disabled" in status:
            # A Mac as users have it: Gatekeeper on. The runner image may ship it off.
            self._run(["/usr/bin/sudo", "-n", "/usr/sbin/spctl", "--master-enable"], timeout=60)
            rc, status = self._run(["/usr/sbin/spctl", "--status"], timeout=60)
            details["gatekeeper"] = "réactivé par le robot : " + status.strip()
        if "enabled" in status:
            rc, out = self._run(["/usr/sbin/spctl", "--assess", "--type", "execute", "-vv", self.app], timeout=120)
            details["gatekeeper_verdict"] = out.strip()[-300:]
            if rc == 0 or "rejected" not in out:
                raise ErreurPilote("Gatekeeper accepte l'app (spctl code %d) : une préversion non signée doit être "
                                   "refusée, le terrain est à mettre à jour si l'app est signée" % rc)
            notes.extend(self.refus(details, capturer))
        else:
            notes.append("Gatekeeper désactivé sur la VM : refus de l'app non constaté (%s)" % status.strip())

        # « Ouvrir quand même » in Réglages > Confidentialité et sécurité.
        rc, out = self._run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", self.app], timeout=60)
        if rc != 0 or self.quarantaine(self.app):
            raise ErreurPilote("la quarantaine de l'app ne s'enlève pas : %s" % out.strip()[-200:])
        notes.append("quarantaine levée par le robot, là où l'utilisateur clique « Ouvrir quand même » dans Réglages")
        return notes

    def refus(self, details, capturer=None):
        """The app opened once as a double click opens it, still quarantined: macOS must refuse it
        with its dialog, which is captured and closed (« Terminé »), and the app must not start."""
        self._run(["/usr/bin/open", self.app], timeout=120, env=env_utilisateur(Path.home()))
        refus = lambda: next((d for d in self.lire().dialogues if d["processus"] == self.GATEKEEPER), None)
        dialogue = self.attendre(refus, 60)
        if self.vivante():
            raise ErreurPilote("macOS a laissé s'ouvrir la préversion non signée (pas de refus de Gatekeeper)")
        if not dialogue:
            return ["aucun dialogue de refus de Gatekeeper en 60 s : refus constaté par spctl seulement"]
        details["gatekeeper_dialogue"] = {"textes": dialogue["textes"][:6], "boutons": dialogue["boutons"]}
        if capturer:
            details["capture_gatekeeper"] = capturer("gatekeeper")
        bouton = next((b for b in self.FERMER_REFUS if b in dialogue["boutons"]), None)
        if not bouton:
            raise ErreurPilote("le refus de Gatekeeper n'offre pas « Terminé » : %s" % ", ".join(dialogue["boutons"]))
        self.noter_clic("gatekeeper", bouton, self.cliquer(bouton, processus=self.GATEKEEPER))
        if not self.attendre(lambda: refus() is None, 30):
            raise ErreurPilote("le refus de Gatekeeper reste à l'écran après « %s »" % bouton)
        return []

    def outils(self, home):
        home = Path(home)
        return [("Claude Code", [home / ".local" / "bin" / "claude"]), ("Bun", [home / ".bun" / "bin" / "bun"]),
                ("Obsidian", [Path(d) / "Obsidian.app" / "Contents" / "MacOS" for d in ("/Applications", home / "Applications")])]

    def ouvrir(self, env):
        rc, out = self._run(["/usr/bin/open", self.app], timeout=120, env=env)
        if rc != 0:
            raise ErreurPilote("open ne lance pas l'app : %s" % out.strip()[-200:])

    def vivante(self):
        return self._run(["/usr/bin/pgrep", "-x", self.processus], timeout=30)[0] == 0

    def fermer(self):
        # Cmd+Q, through System Events: the robot sends no Apple event to the app itself (that
        # would need an Automation consent, a dialog of its own).
        script = ('tell application "System Events" to tell process "%s"\n set frontmost to true\n delay 1\n'
                  ' keystroke "q" using command down\nend tell' % self.processus)
        self._run(["/usr/bin/osascript", "-e", script], timeout=60)
        if self.attendre(lambda: not self.vivante(), 20):
            return "Cmd+Q"
        self._run(["/usr/bin/pkill", "-x", self.processus], timeout=30)
        if self.attendre(lambda: not self.vivante(), 20):
            return "SIGTERM (Cmd+Q sans effet)"
        raise ErreurPilote("l'app ne se ferme pas")

    def lire(self):
        res = self._osa({"op": "lire", "processus": self.processus, "dialogues": list(self.DIALOGUES),
                         "ignorer": list(self.IGNORER)})
        self.dernier_arbre = res
        fen = [f for f in res.get("app") or [] if isinstance(f, dict)]
        dialogues = [dialogue_depuis_arbre(d.get("processus"), d.get("arbre")) for d in res.get("dialogues") or []
                     if isinstance(d, dict)]
        e = ecran_depuis_arbre(fen, [d for d in dialogues if d["textes"] or d["boutons"]])
        self.statut = e.statut
        return e

    def rapide(self):
        if not getattr(self, "statut", None):
            return None
        try:
            res = self._osa({"op": "rapide", "processus": self.processus, "fenetre": self.statut[0],
                             "chemin": list(self.statut[1])}, timeout=30)
        except ErreurPilote:
            return None
        return dits_rapides(res.get("textes")) if res.get("ok") else None

    def cliquer(self, libelle, processus=None):
        res = self._osa({"op": "cliquer", "processus": processus or self.processus, "libelle": libelle})
        if not res.get("ok"):
            raise ErreurPilote("pas de clic sur « %s » (%s)" % (libelle, res.get("pourquoi")))
        return res.get("comment")

    def capturer(self, chemin):
        rc, out = self._run(["/usr/sbin/screencapture", "-x", "-t", "png", chemin], timeout=60)
        if rc != 0 or not Path(chemin).is_file() or Path(chemin).stat().st_size == 0:
            raise ErreurPilote("screencapture n'a rien écrit (code %d : %s)" % (rc, out.strip()[-200:]))

    def arbre(self):
        return self.dernier_arbre


# UI Automation's control types, as the roles of the tree ecran_depuis_arbre() reads (macOS's words).
UIA_ROLES = {"Button": "AXButton", "Text": "AXStaticText", "Document": "AXWebArea", "Window": "AXWindow",
             "CheckBox": "AXCheckBox", "List": "AXList", "Hyperlink": "AXLink", "Edit": "AXTextField",
             "Image": "AXImage"}
# Bibo's region (role=status): a status bar for UI Automation, or a group that says it is one.
STATUTS_UIA = ("status", "statut", "status bar", "barre d'état", "barre d'etat")
# The window's own controls (minimize, close, the system menu): never the app's buttons.
UIA_CADRE = ("TitleBar", "MenuBar")


def arbre_uia(node):
    """The tree PiloteWindows' helper reads (t control type, lt its localized name, n name, e enabled,
    x toggle state, l aria-live, k children), in the shape of PiloteMac's (r, s, n, v, e, k). A text
    with a name is a leaf, as a button is; Bibo's region gets the subrole macOS gives it."""
    out = _uia(node)
    page = next((n for _, n in _aplatir(out) if n["r"] == "AXWebArea"), out)
    if not any(n.get("s") for _, n in _aplatir(page)):
        # No status bar: the first live region of the page (aria-live) is Bibo's, it comes first.
        live = next((n for _, n in _aplatir(page) if n.get("l") and n.get("r") not in ("AXStaticText", "AXButton")),
                    None)
        if live is not None:
            live["s"] = "AXApplicationStatus"
    return out


def _uia(node):
    node = node if isinstance(node, dict) else {}
    t = str(node.get("t") or "")
    name = str(node.get("n") or "")
    role = UIA_ROLES.get(t, "AXGroup")
    if role == "AXWebArea" and node.get("c") and node.get("a") != "RootWebArea":
        # WebView2's own browser frame holds empty views that call themselves documents (class
        # WebView, ContentsWebView, HubWebView); the page is Chromium's root, which has no class.
        role = "AXGroup"
    if role == "AXStaticText" and not name.strip():
        role = "AXGroup"     # a paragraph without a name of its own: its texts are below
    statut = t == "StatusBar" or norm(node.get("lt")).lower() in STATUTS_UIA
    out = {"r": role, "s": "AXApplicationStatus" if statut else "", "n": name,
           "v": name if role == "AXStaticText" else "", "d": "", "e": node.get("e", True) is not False, "k": []}
    if node.get("l"):
        out["l"] = node["l"]
    if "x" in node:
        out["x"] = node["x"]
    if role not in ("AXStaticText", "AXButton", "AXCheckBox"):
        out["k"] = [_uia(k) for k in node.get("k") or [] if isinstance(k, dict) and str(k.get("t")) not in UIA_CADRE]
    return out


def nom_programme(path):
    """« SmartScreen » of C:\\Windows\\System32\\SmartScreen.exe, whatever system reads it."""
    name = re.split(r"[\\/]", str(path or ""))[-1]
    return re.sub(r"(?i)\.exe$", "", name) or "?"


def norm_win(text):
    """A Win32 label without its access key (« &Suivant > » shows « Suivant > »)."""
    return norm(str(text or "").replace("&", ""))


# The installer's buttons (NSIS, in French as tauri.windows.conf.json asks; English in case).
BOUTONS_NSIS = {"suivant": ("Suivant >", "Next >"), "installer": ("Installer", "Install"),
                "fermer": ("Fermer", "Terminer", "Finish"), "annuler": ("Annuler", "Cancel"),
                "parcourir": ("Parcourir...", "Browse..."),
                "details": ("Plus d'infos", "Afficher les détails", "Show details"),
                "precedent": ("< Précédent", "< Back")}
# The finish page's box that starts the app: the robot starts it itself, from the Start menu.
LANCER = ("Lancer", "Exécuter", "Run", "Launch")


def page_installeur(fenetres):
    """{cle, boutons {label: enabled}, cases {label: state}, textes} of the installer's windows."""
    boutons, cases, textes = {}, {}, []
    for f in fenetres or []:
        for _, n in _aplatir(_uia(f.get("arbre") or {})):
            label = norm_win(n.get("n"))
            if n["r"] == "AXButton" and label:
                boutons[label] = boutons.get(label, False) or n["e"]
            elif n["r"] == "AXCheckBox" and label:
                cases[label] = n.get("x")
            elif n["r"] == "AXStaticText" and label and not label.startswith("Nullsoft Install System"):
                textes.append(label)
    page = {"boutons": boutons, "cases": cases, "textes": textes}
    page["cle"] = cle_installeur(page)
    return page


def _nsis(page, key):
    return next((label for label in BOUTONS_NSIS[key] if label in page["boutons"]), None)


def cle_installeur(page):
    b = page["boutons"]
    if _nsis(page, "fermer"):
        return "fin"
    if not any(_nsis(page, k) for k in ("suivant", "installer", "annuler")):
        return "message"
    if _nsis(page, "parcourir"):
        return "dossier"
    suivant = _nsis(page, "suivant")
    if _nsis(page, "details") or (suivant and not b[suivant] and not _nsis(page, "installer")):
        return "installe" if suivant and b[suivant] else "installation"
    precedent = _nsis(page, "precedent")
    if suivant and not (precedent and b[precedent]):
        return "bienvenue"
    return "page"


def action_installeur(page):
    """("cliquer", label), ("cocher", label, 0|1), ("message", text) or None (wait)."""
    if page["cle"] == "message":
        return ("message", " ".join(page["textes"])[:300])
    if page["cle"] == "fin":
        for label, state in page["cases"].items():
            if label.startswith(LANCER) and state == 1:
                return ("cocher", label, 0)
        label = _nsis(page, "fermer")
        return ("cliquer", label) if page["boutons"][label] else None
    for key in ("installer", "suivant"):
        label = _nsis(page, key)
        if label and page["boutons"][label]:
            return ("cliquer", label)
    return None


def env_depuis_bloc(entrees):
    """{name: value} of an environment block's NAME=value strings (the =C: ones are cmd's, not variables)."""
    out = {}
    for e in entrees:
        name, sep, value = e.partition("=")
        if sep and name:
            out[name] = value
    return out


def env_app_windows(session, stub=None, extra=None):
    """The session's environment, the Microsoft Store's python.exe first on the PATH as on a PC
    without Python (stub: poste-neuf.ps1's), plus extra (BIAB_ARCHIVE on a branch)."""
    env = dict(session)
    key = next((k for k in env if k.upper() == "PATH"), "Path")
    if stub:
        env[key] = str(stub) + (";" + env[key] if env.get(key) else "")
    env.update(extra or {})
    return env


def env_session():
    """What Windows gives a program started from the Start menu: the system's and the user's
    variables as the registry holds them (CreateEnvironmentBlock, nothing of this process)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    uenv = ctypes.WinDLL("userenv", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.OpenProcessToken.restype = wintypes.BOOL
    uenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.BOOL]
    uenv.CreateEnvironmentBlock.restype = wintypes.BOOL
    uenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008 | 0x0002, ctypes.byref(token)):   # QUERY | DUPLICATE
        raise ErreurPilote("OpenProcessToken : erreur %d" % ctypes.get_last_error())
    try:
        block = ctypes.c_void_p()
        if not uenv.CreateEnvironmentBlock(ctypes.byref(block), token, False):
            raise ErreurPilote("CreateEnvironmentBlock : erreur %d" % ctypes.get_last_error())
        try:
            entrees, at = [], block.value
            while True:
                s = ctypes.wstring_at(at)
                if not s:
                    break
                entrees.append(s)
                at += len(s.encode("utf-16-le", "surrogatepass")) + 2
            return env_depuis_bloc(entrees)
        finally:
            uenv.DestroyEnvironmentBlock(block)
    finally:
        k32.CloseHandle(token)


PRODUIT = "Brain in a Box"
SANS_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DELAI_INSTALLEUR = 10 * 60
DELAI_FERMETURE = 3 * 60    # « Fermer » clicked once, as a user does: the installer then quits by itself
DESINSTALLATION = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                   r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")

# Windows' scheduled tasks catch up a start missed before they existed: BrainNightly may start as
# soon as the brain step registers it. Disabled, as the other Windows jobs do, and waited for.
PS_NUIT = r'''
$ErrorActionPreference = 'Stop'
$t = @(Get-ScheduledTask -TaskName 'Brain*' -ErrorAction SilentlyContinue)
$out = [ordered]@{ taches = @($t | ForEach-Object { [ordered]@{ nom = $_.TaskName; etat = [string]$_.State } }) }
$t | Disable-ScheduledTask | Out-Null
$svc = New-Object -ComObject Schedule.Service
$svc.Connect()
function Running { @($svc.GetRunningTasks(1) | Where-Object { $_.Name -like 'Brain*' } | ForEach-Object { $_.Name }) }
$out.tournaient = @(Running)
$start = Get-Date
while (@(Running).Count -and ((Get-Date) - $start).TotalMinutes -lt 20) { Start-Sleep -Seconds 5 }
$out.attente_s = [int]((Get-Date) - $start).TotalSeconds
$out.tournent_encore = @(Running)
$out | ConvertTo-Json -Depth 4 -Compress
'''
PS_RENDRE = r'''
$ErrorActionPreference = 'Stop'
Get-ScheduledTask -TaskName 'Brain*' | Enable-ScheduledTask | Out-Null
@{ taches = @(Get-ScheduledTask -TaskName 'Brain*' | ForEach-Object { [ordered]@{ nom = $_.TaskName; etat = [string]$_.State } }) } |
  ConvertTo-Json -Depth 4 -Compress
'''

# What a user clicks to open a downloaded setup.exe all the same: SmartScreen's window, or the
# Attachment Manager's question when SmartScreen is off.
SMARTSCREEN_PLUS = ("More info", "Informations complémentaires", "Plus d'infos", "Plus d'informations")
SMARTSCREEN_OUVRIR = ("Run anyway", "Exécuter quand même")
AVERTISSEMENT_OUVRIR = ("Run", "Exécuter")
DELAI_SHELL = 5 * 60


def ouvrir_par_le_shell(fichier, out):
    """fichier opened as a double click opens it (ShellExecuteEx): the shell reads its Mark of the
    Web and asks SmartScreen first, holding the call until the question is answered, hence a thread
    of its own. out gets ok, erreur and handle (the process). The installer gets this process's
    environment: the job's tokens were taken out of it before (scenario)."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(Info)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    ctypes.WinDLL("ole32").CoInitializeEx(None, 0x2 | 0x4)     # apartment threaded, no OLE1 DDE
    info = Info(cbSize=ctypes.sizeof(Info), fMask=0x40 | 0x100, lpVerb="open", lpFile=str(fichier),
                lpDirectory=str(Path(fichier).parent), nShow=1)     # NOCLOSEPROCESS | NOASYNC, SW_SHOWNORMAL
    out["ok"] = bool(shell32.ShellExecuteExW(ctypes.byref(info)))
    out["erreur"] = ctypes.get_last_error()
    out["handle"] = info.hProcess


class ProcessusShell:
    """A process the shell started, as subprocess.Popen shows one: pid, poll(), kill(), returncode."""

    def __init__(self, handle):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.GetProcessId.argtypes = [wintypes.HANDLE]
        self.k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.handle, self.returncode = handle, None
        self.pid = self.k32.GetProcessId(handle)

    def poll(self):
        if self.returncode is None:
            code = self.wintypes.DWORD()
            if self.k32.GetExitCodeProcess(self.handle, self.ctypes.byref(code)) and code.value != 259:    # STILL_ACTIVE
                self.returncode = code.value
        return self.returncode

    def kill(self):
        self.k32.TerminateProcess(self.handle, 1)


def question_securite(res, moi):
    """(window, who, buttons, links, texts) of the question Windows asks before opening a downloaded
    file: SmartScreen's window in front, or the Attachment Manager's dialog, which the shell shows
    from the process that opened the file (moi). None when there is none."""
    d = res.get("devant") if isinstance(res.get("devant"), dict) else {}
    fen = [f for f in res.get("fenetres") or [] if isinstance(f, dict) and f.get("classe") == "#32770"]
    if nom_programme(d.get("exe")).lower() == "smartscreen":
        h, qui, arbre = d.get("h"), "SmartScreen", d.get("arbre")
    elif fen:
        h, qui, arbre = fen[0].get("h"), nom_programme(moi), fen[0].get("arbre")
    else:
        return None
    flat = [n for _, n in _aplatir(arbre_uia(arbre))]
    noms = lambda role: [norm_win(n["n"]) for n in flat if n["r"] == role and norm_win(n["n"])]
    return h, qui, noms("AXButton"), noms("AXLink"), noms("AXStaticText")


class PiloteWindows(Pilote):
    """UI Automation (the accessibility tree Windows gives every window, WebView2's page included)
    and a capture of the whole screen, through pilote_windows.cs, compiled on the VM by the .NET
    Framework's own csc.exe. The installer: the NSIS setup.exe, clicked through page by page."""

    machine = "PC"
    suffixe = "_windows-x64-setup.exe"
    neufs = ("Python (pour les automatismes)", "Git (l'historique de tes notes)")
    # In front, yet no dialog: the desktop, the taskbar, a console.
    IGNORER = ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "ConsoleWindowClass",
               "CASCADIA_HOSTING_WINDOW_CLASS", "Windows.UI.Core.CoreWindow")
    SOURCE = Path(__file__).resolve().with_name("pilote_windows.cs")

    def __init__(self, travail, stub=None, outil=None, horloge=time.monotonic, dormir=time.sleep):
        self.travail = Path(travail)
        self.stub = stub
        self.horloge, self.dormir = horloge, dormir
        self.outil = outil or self._compiler()
        self.proc = None            # the app, as the robot opened it
        self.exe = None             # where the installer put it (the Start menu shortcut's target)
        self.hwnd = None            # its window
        self.vues = set()           # windows already brought to the front once
        self.dialogues_h = {}       # process of a dialog -> its window
        self.dernier_arbre = None
        self.clic_prec = (None, 0)  # ((window, label), attempts in a row)

    def _compiler(self):
        fw = Path(os.environ.get("WINDIR") or r"C:\Windows") / "Microsoft.NET" / "Framework64" / "v4.0.30319"
        csc = fw / "csc.exe"
        if not csc.is_file():
            raise ErreurPilote("csc.exe absent (%s) : pas de .NET Framework 4 sur ce PC" % csc)
        refs = []
        for name in ("UIAutomationClient.dll", "UIAutomationTypes.dll", "WindowsBase.dll", "System.Drawing.dll"):
            found = next((d / name for d in (fw / "WPF", fw) if (d / name).is_file()), None)
            if found is None:
                raise ErreurPilote("%s absent du .NET Framework (%s)" % (name, fw))
            refs.append(found)
        exe = self.travail / "pilote-windows.exe"
        rc, out = self._run([csc, "/nologo", "/target:exe", "/optimize+", "/out:%s" % exe]
                            + ["/r:%s" % r for r in refs] + [self.SOURCE], timeout=300)
        if rc != 0 or not exe.is_file():
            raise ErreurPilote("le pilote Windows ne compile pas (csc code %d : %s)" % (rc, out.strip()[-500:]))
        return exe

    def _outil(self, args, timeout=120):
        # No console window of its own: it would take the foreground from the window to click.
        rc, out = self._run([self.outil] + [str(a) for a in args], timeout=timeout, creationflags=SANS_CONSOLE)
        try:
            res = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ErreurPilote("réponse illisible du pilote Windows (code %d) : %s" % (rc, out.strip()[-300:]))
        if rc != 0 or not isinstance(res, dict):
            raise ErreurPilote("le pilote Windows a fini avec le code %d : %s" % (rc, out.strip()[-300:]))
        return res

    def environnement(self, home, extra=None):
        return env_app_windows(env_session(), self.stub, extra)

    def outils(self, home):
        home = Path(home)
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        pf = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
        python = sorted(local.glob("Programs/Python/Python3*/python.exe"))
        return [("Claude Code", [home / ".local" / "bin" / "claude.exe"]), ("Bun", [home / ".bun" / "bin" / "bun.exe"]),
                ("Obsidian", [local / "Programs" / "Obsidian" / "Obsidian.exe", local / "Obsidian" / "Obsidian.exe",
                              pf / "Obsidian" / "Obsidian.exe"]),
                ("Python de winget", python or [local / "Programs" / "Python" / "Python3xx" / "python.exe"])]

    def marquer_telecharge(self, fichier):
        # What a browser writes on a download: the Mark of the Web (zone 3, Internet), which the
        # shell and SmartScreen read when the file is opened.
        mark = "[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=https://github.com/%s/releases\r\n" % REPO_SLUG
        try:
            with open(str(fichier) + ":Zone.Identifier", "w", encoding="ascii", newline="") as f:
                f.write(mark)
        except OSError as e:
            raise ErreurPilote("pas de Mark of the Web sur %s (%s)" % (Path(fichier).name, e.strerror or type(e).__name__))
        return "Zone.Identifier : ZoneId=3 (Internet)"

    def nouvel_ecran(self):
        self.clic_prec = (None, 0)

    @staticmethod
    def desinstallations():
        """[{ruche, cle, desinstalleur}] of the uninstall entries named Brain in a Box."""
        import winreg
        out = []
        for hive, ruche in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            for path in DESINSTALLATION:
                try:
                    root = winreg.OpenKey(hive, path)
                except OSError:
                    continue
                with root:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(root, sub) as k:
                                if winreg.QueryValueEx(k, "DisplayName")[0] != PRODUIT:
                                    continue
                                out.append({"ruche": ruche, "cle": sub,
                                            "desinstalleur": str(winreg.QueryValueEx(k, "UninstallString")[0]).strip('"')})
                        except OSError:
                            continue
        return out

    def _devant(self, h):
        """A window seen for the first time is brought to the front, once: opened from the robot's
        process, it may sit behind the others, where a user would have it in front."""
        if h and h not in self.vues:
            self.vues.add(h)
            try:
                self._outil(["devant", h], timeout=30)
            except ErreurPilote:
                pass

    def installer(self, fichier, details, capturer=None):
        """setup.exe opened as a double click opens it (SmartScreen's question answered as a user
        does, if Windows asks it), every page captured and clicked as a user does, the defaults kept
        but the box that starts the app (the robot opens it from the Start menu, with a session's
        environment). Then: installed for this user alone, no administrator."""
        if self.desinstallations():
            raise ErreurPilote("%s est déjà installé : la VM n'est pas neuve" % PRODUIT)
        # A user's desktop: the windows already open there (the runner's own console) minimized.
        details["bureau_range"] = self._outil(["ranger"]).get("fenetres")
        lance, notes = self.lancer_setup(fichier, details, capturer)
        proc = ProcessusShell(lance["handle"])
        pages = details["installeur"] = []
        try:
            self.assistant(proc, pages, capturer)
        finally:
            if proc.poll() is None:
                proc.kill()
        if proc.returncode != 0:
            raise ErreurPilote("l'installeur a fini avec le code %s" % proc.returncode)
        return notes + self.verifier_installation(details)

    def lancer_setup(self, fichier, details, capturer=None, ouvrir=ouvrir_par_le_shell):
        """setup.exe opened by the shell, in a thread; meanwhile the question Windows asks first
        (SmartScreen), captured and answered: « Informations complémentaires », then « Exécuter quand
        même ». Returns (what ShellExecuteEx gave, notes)."""
        out = {}
        th = threading.Thread(target=ouvrir, args=(fichier, out), daemon=True)
        th.start()
        start = self.horloge()
        vues = details["avant_installeur"] = []
        essais = {}
        while th.is_alive():
            if self.horloge() - start >= DELAI_SHELL:
                raise ErreurPilote("setup.exe ne s'ouvre pas en %d min (%s)" % (DELAI_SHELL // 60, "question de sécurité "
                                   "sans réponse : « %s »" % " ".join(vues[-1]["textes"])[:200] if vues else "rien à l'écran"))
            q = question_securite(self._outil(["lire", os.getpid()]), sys.executable)
            if q:
                h, qui, boutons, liens, textes = q
                cle = [qui, sorted(boutons + liens)]
                if not vues or vues[-1]["cle"] != cle:
                    self._devant(h)
                    vues.append({"cle": cle, "qui": qui, "textes": textes[:8], "boutons": boutons, "liens": liens,
                                 "capture": capturer(qui.lower()) if capturer else None})
                label = next((b for b in SMARTSCREEN_OUVRIR + AVERTISSEMENT_OUVRIR if b in boutons), None) or \
                    next((b for b in SMARTSCREEN_PLUS if b in liens + boutons), None)
                n, at = essais.get(label, (0, None))
                if label and (at is None or self.horloge() - at >= CLIC_RELANCE):
                    if n >= CLIC_ESSAIS:
                        raise ErreurPilote("le clic sur « %s » (%s) ne change rien (%d essais)" % (label, qui, n))
                    done = self._clic(h, label)
                    if not done.get("ok"):
                        raise ErreurPilote("pas de clic sur « %s » (%s : %s)" % (label, qui, done.get("pourquoi")))
                    self.noter_clic(qui.lower(), label, done.get("comment"))
                    essais[label] = (n + 1, self.horloge())
            th.join(0.05)
            self.dormir(POLL)
        if not out.get("ok") or not out.get("handle"):
            raise ErreurPilote("setup.exe ne s'ouvre pas par le shell (ShellExecuteEx, erreur %s)" % out.get("erreur"))
        if vues:
            return out, []
        return out, ["SmartScreen ne s'est pas affiché : setup.exe porte pourtant la Mark of the Web et a été ouvert "
                     "par le shell (désactivé sur cette image ? constat, pas un échec)"]

    def assistant(self, proc, pages, capturer=None):
        """The installer's pages until it quits. pages: what each showed and what was clicked."""
        start = self.horloge()
        prev, clic, message, ferme = None, None, None, None
        while proc.poll() is None:
            now = self.horloge()
            if now - start >= DELAI_INSTALLEUR:
                raise ErreurPilote("l'installeur n'a pas fini en %d min (page « %s »)" % (
                    DELAI_INSTALLEUR // 60, pages[-1]["page"] if pages else "aucune"))
            res = self._outil(["lire", proc.pid])
            self.dernier_arbre = res
            fen = [f for f in res.get("fenetres") or [] if isinstance(f, dict)]
            if not fen:
                self.dormir(POLL)
                continue
            self._devant(fen[0].get("h"))
            page = page_installeur(fen)
            sig = (page["cle"], sorted(page["boutons"].items()), sorted(page["cases"].items()))
            if sig != prev:
                prev, clic, message = sig, None, None
                pages.append({"page": page["cle"], "a_s": int(now - start), "boutons": sorted(page["boutons"]),
                              "cases": page["cases"], "textes": page["textes"][:12], "clics": [],
                              "capture": capturer("installeur-" + page["cle"]) if capturer else None})
            act = action_installeur(page)
            if ferme is not None and act == ("cliquer", _nsis(page, "fermer")):
                # Clicked once: the installer makes the desktop shortcut and quits, a user waits for it.
                if now - ferme >= DELAI_FERMETURE:
                    raise ErreurPilote("l'installeur est toujours ouvert %d s après « %s »" % (now - ferme, act[1]))
                if capturer and not pages[-1].get("attente") and now - ferme >= 30:
                    pages[-1]["attente"] = capturer("installeur-fermeture")
            elif act and act[0] == "message":
                # A message box of the installer (an error): it gets a few seconds to go away.
                message = message if message is not None else now
                if now - message >= 10:
                    raise ErreurPilote("l'installeur affiche : « %s »" % act[1])
            elif act and (clic is None or now - clic[0] >= CLIC_RELANCE):
                if clic is not None and clic[1] >= CLIC_ESSAIS:
                    raise ErreurPilote("le clic sur « %s » ne change rien dans l'installeur (%d essais)" % (act[1], clic[1]))
                h = fen[0].get("h")
                done = self._outil(["cocher", h, act[1], act[2]]) if act[0] == "cocher" else self._clic(h, act[1])
                if not done.get("ok"):
                    raise ErreurPilote("pas de clic sur « %s » dans l'installeur (%s)" % (act[1], done.get("pourquoi")))
                pages[-1]["clics"].append(act[1] if act[0] == "cliquer" else "%s : %s" % (act[1], "coché" if act[2] else "décoché"))
                self.noter_clic("installeur-" + page["cle"], act[1], done.get("comment"))
                clic = (now, (clic[1] if clic else 0) + 1)
                if page["cle"] == "fin" and act[0] == "cliquer":
                    ferme = now
            self.dormir(POLL)
        if ferme is not None and pages:
            pages[-1]["quitte_apres_s"] = int(self.horloge() - ferme)
        return pages

    def verifier_installation(self, details):
        notes = []
        mine = self.desinstallations()
        details["desinstallation"] = [{k: v for k, v in e.items() if k != "desinstalleur"} for e in mine]
        if [e for e in mine if e["ruche"] == "HKLM"]:
            raise ErreurPilote("installée pour tous les utilisateurs du PC (HKLM), pas pour l'utilisateur seul")
        if len(mine) != 1:
            raise ErreurPilote("%d entrée(s) de désinstallation pour l'utilisateur, 1 attendue" % len(mine))
        local = os.path.normcase(os.environ.get("LOCALAPPDATA") or "")
        dossier = os.path.dirname(mine[0]["desinstalleur"])
        if not local or not os.path.normcase(dossier).startswith(local + os.sep):
            raise ErreurPilote("installée dans %s, hors des programmes de l'utilisateur" % dossier)
        menu = Path(os.environ.get("APPDATA") or "") / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        lnk = next(iter(sorted(menu.rglob(PRODUIT + ".lnk"))), None) if menu.is_dir() else None
        if lnk is None:
            raise ErreurPilote("pas de raccourci %s dans le menu Démarrer" % PRODUIT)
        cible = self._outil(["raccourci", lnk])
        exe = str(cible.get("cible") or "")
        if os.path.normcase(os.path.dirname(exe)) != os.path.normcase(dossier) or not os.path.isfile(exe):
            raise ErreurPilote("le raccourci du menu Démarrer mène à « %s », pas à l'app installée" % exe)
        self.exe = exe
        bureau = Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop" / (PRODUIT + ".lnk")
        details.update(installee_dans=dossier, raccourci_menu=str(lnk), raccourci_bureau=bureau.is_file(),
                       app=os.path.basename(exe))
        return notes

    def ouvrir(self, env):
        """As a click on its Start menu entry: the app, from its folder, with the session's environment."""
        if not self.exe:
            raise ErreurPilote("l'app n'est pas installée")
        try:
            self.proc = subprocess.Popen([self.exe], env=env, cwd=os.path.dirname(self.exe), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        except OSError as e:
            raise ErreurPilote("l'app ne se lance pas (%s)" % (e.strerror or type(e).__name__))
        self.hwnd = None

    def vivante(self):
        return self.proc is not None and self.proc.poll() is None

    def fermer(self):
        """The window's X (WM_CLOSE), as a user quits; the process killed only if it stays."""
        if self.vivante() and self.hwnd is None:
            self.lire()
        if self.vivante() and self.hwnd:
            res = self._outil(["fermer", self.hwnd], timeout=60)
            if self.attendre(lambda: not self.vivante(), 20, self.horloge, self.dormir):
                return "fenêtre fermée (%s)" % res.get("comment")
        if self.vivante():
            self._run(["taskkill", "/PID", self.proc.pid, "/T", "/F"], timeout=60)
            if self.attendre(lambda: not self.vivante(), 20, self.horloge, self.dormir):
                return "taskkill (la fenêtre ne s'est pas fermée)"
            raise ErreurPilote("l'app ne se ferme pas")
        return "déjà fermée"

    def lire(self):
        if not self.vivante():
            return Ecran()
        res = self._outil(["lire", self.proc.pid])
        self.dernier_arbre = res
        fen, dialogues = [], []
        for f in res.get("fenetres") or []:
            if not isinstance(f, dict):
                continue
            rect = f.get("rect") if isinstance(f.get("rect"), list) and len(f["rect"]) == 4 else [0, 0, 100, 100]
            if rect[2] < 50 or rect[3] < 50:
                continue    # the event window Tauri keeps (16 x 16): nothing a user sees
            arbre = arbre_uia(f.get("arbre"))
            if f.get("classe") == "#32770":
                # A message box of the app itself.
                self.dialogues_h[self.exe_nom()] = f.get("h")
                dialogues.append(dialogue_depuis_arbre(self.exe_nom(), arbre))
            else:
                fen.append({"titre": f.get("titre"), "arbre": arbre, "h": f.get("h")})
        d = res.get("devant")
        if isinstance(d, dict) and d.get("classe") not in self.IGNORER:
            name = nom_programme(d.get("exe"))
            self.dialogues_h[name] = d.get("h")
            dialogues.append(dialogue_depuis_arbre(name, arbre_uia(d.get("arbre"))))
        if fen:
            page = [f for f in fen if any(n.get("r") == "AXWebArea" for _, n in _aplatir(f["arbre"]))]
            self.hwnd = (page or fen)[0]["h"]
            self._devant(self.hwnd)
        return ecran_depuis_arbre(fen, [d for d in dialogues if d["textes"] or d["boutons"]])

    def exe_nom(self):
        return nom_programme(self.exe or "brain-in-a-box.exe")

    def _clic(self, h, libelle):
        """The mouse on the button, as a user clicks; a second try in a row by Invoke, as a screen
        reader presses it, a third by the mouse again."""
        key = (h, libelle)
        n = self.clic_prec[1] + 1 if self.clic_prec[0] == key else 1
        self.clic_prec = (key, n)
        return self._outil(["cliquer", h, libelle, "invoke" if n % 2 == 0 else "souris"])

    def cliquer(self, libelle, processus=None):
        h = self.dialogues_h.get(processus) if processus else self.hwnd
        if not h:
            raise ErreurPilote("pas de fenêtre où cliquer « %s »" % libelle)
        res = self._clic(h, libelle)
        if not res.get("ok"):
            raise ErreurPilote("pas de clic sur « %s » (%s)" % (libelle, res.get("pourquoi")))
        return res.get("comment")

    def capturer(self, chemin):
        res = self._outil(["capturer", chemin], timeout=60)
        if not res.get("ok") or not Path(chemin).is_file() or Path(chemin).stat().st_size == 0:
            raise ErreurPilote("pas de capture de l'écran (%s)" % (res.get("pourquoi") or "rien d'écrit"))

    def arbre(self):
        return self.dernier_arbre

    def ecarter_la_nuit(self):
        rc, out = self._run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-Command", PS_NUIT], timeout=25 * 60, creationflags=SANS_CONSOLE)
        try:
            res = json.loads(out[out.index("{"):])
        except ValueError:
            raise ErreurPilote("les tâches Brain* ne se désactivent pas (code %d : %s)" % (rc, out.strip()[-300:]))
        if res.get("tournent_encore"):
            raise ErreurPilote("la vraie nuit tourne encore après 20 min : %s" % ", ".join(res["tournent_encore"]))
        return res

    def rendre_la_nuit(self):
        rc, out = self._run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-Command", PS_RENDRE], timeout=120, creationflags=SANS_CONSOLE)
        try:
            return json.loads(out[out.index("{"):])
        except ValueError:
            raise ErreurPilote("les tâches Brain* ne se réactivent pas (code %d : %s)" % (rc, out.strip()[-300:]))


# --------------------------------------------------------------------------- the walk through the window

class Parcours:
    """Plays the screens of the app's window as a user who wants it all installed, skips the Voyage
    key and the Claude login. Every new screen, or install step, gets a capture and a step name
    (t), every 2 min of waiting another capture. A failure names the screen and quotes Bibo."""

    def __init__(self, t, pilote, dossier, horloge=time.monotonic, dormir=time.sleep, echeance=None):
        self.t, self.p, self.dossier = t, pilote, Path(dossier)
        self.horloge, self.dormir = horloge, dormir
        # The scenario's own end (horloge's time), before its job step's timeout kills it: a walk
        # still going then fails by itself, naming its screen, with a capture.
        self.echeance = echeance
        self.n = 0
        self.captures = []
        self.ecrans = []        # [{"ecran", "debut_s", "duree_s"}]
        self.dialogues = []     # system dialogs seen: [{"processus", "textes", "boutons", "capture", "reponse"}]
        self.ouverts = []       # the dialogs of the last reading
        self.dernier = Ecran()  # the last screen where Bibo said something
        self.debut = horloge()
        self.lectures = []      # how long each full reading of the window took, ms
        self.rapides = []       # and each quick one (Bibo's words only)
        self.precedent = None   # the last full reading, kept while Bibo says the same
        self.plein_a = 0.0
        self.forcer = True      # the next reading is a full one (after a launch, a missing button)
        self.clics = []         # [{"a_s", "ecran", "bouton", "comment"}]

    def secondes(self):
        return int(self.horloge() - self.debut)

    def capturer(self, ecran):
        self.n += 1
        nom = nom_capture(self.n, ecran)
        self.p.capturer(self.dossier / nom)
        self.captures.append(nom)
        return nom

    def lire(self):
        """The window now. While Bibo says the same, a quick read of its words stands for the full
        one (a short install step is not missed), a full one at least every LECTURE_PLEINE s."""
        start = self.horloge()
        prec = self.precedent
        if prec is not None and not self.forcer and start - self.plein_a < LECTURE_PLEINE:
            vite = self.p.rapide()
            if vite is not None:
                self.rapides.append(int((self.horloge() - start) * 1000))
                if vite == (prec.titre, prec.texte):
                    return prec
                # New words: the screen is captured now, its buttons read right after.
                self.forcer = True
                self.dernier = Ecran(fenetre=True, titre=vite[0], texte=vite[1], partiel=True)
                return self.dernier
        start = self.horloge()
        e = self.p.lire()
        self.lectures.append(int((self.horloge() - start) * 1000))
        self.plein_a, self.forcer = self.horloge(), False
        self.precedent = e if e.fenetre and e.titre else None
        if e.titre:
            self.dernier = e
        self.ouverts = e.dialogues
        for d in e.dialogues:
            self.dialogue(d)
        return e

    def dialogue(self, d):
        """A system dialog: captured once per state (its first words and its buttons); answered as a
        user does (reponse_dialogue), a button at most CLIC_ESSAIS times, CLIC_RELANCE apart."""
        key = (d.get("processus"), tuple(d.get("textes") or ())[:1], tuple(sorted(d.get("boutons") or ())))
        seen = next((x for x in self.dialogues if x["cle"] == key), None)
        if not seen:
            seen = {"cle": key, "processus": d.get("processus"), "textes": (d.get("textes") or [])[:10],
                    "boutons": d.get("boutons") or [], "reponse": None, "essais": 0, "clic_a": None}
            self.dialogues.append(seen)
            seen["capture"] = self.capturer("dialogue-" + str(d.get("processus") or "systeme"))
        what = reponse_dialogue(d)
        if what and what[0] == "echec":
            self.echouer(what[1])
        now = self.horloge()
        if not what or seen["essais"] >= CLIC_ESSAIS or (seen["clic_a"] is not None and now - seen["clic_a"] < CLIC_RELANCE):
            return
        bouton = what[1]
        seen["essais"] += 1
        seen["clic_a"] = now
        self.forcer = True
        try:
            how = self.p.cliquer(bouton, processus=d.get("processus"))
            seen["reponse"] = bouton
            self.clics.append({"a_s": self.secondes(), "ecran": "dialogue-" + str(d.get("processus")), "bouton": bouton,
                               "comment": how})
        except ErreurPilote as e:
            seen["reponse"] = "échec du clic : %s" % e

    def entrer(self, cle, prefixe):
        if self.ecrans and self.ecrans[-1]["duree_s"] is None:
            self.ecrans[-1]["duree_s"] = self.secondes() - self.ecrans[-1]["debut_s"]
        self.ecrans.append({"ecran": prefixe + cle, "debut_s": self.secondes(), "duree_s": None})
        self.p.nouvel_ecran()
        # A failure keeps the step it happened in (app-etape-brain, not app-echec).
        if cle not in ARRETS:
            self.t("app-" + prefixe + cle)
        self.capturer(prefixe + cle)

    def raison(self, message):
        e = self.dernier
        out = "%s ; Bibo : « %s »" % (message, (e.titre + (" " + e.texte if e.texte else ""))[:300]) if e.titre \
            else "%s (Bibo n'a rien dit à l'écran)" % message
        for d in self.ouverts:
            out += " ; dialogue système ouvert (%s) : « %s »" % (d.get("processus"), " ".join(d.get("textes") or [])[:200])
        return out

    def echouer(self, message):
        self.t.check(False, message)

    def reaction(self, cle, e, relance):
        """The label to click on this screen, None to wait. Fails on a screen that should not be."""
        if cle in ARRETS:
            self.echouer({"echec": "l'installation a échoué dans l'app",
                          "bloque": "l'app refuse d'installer (diagnostic en rouge)",
                          "diagnostic-impossible": "l'app n'a pas pu faire son diagnostic",
                          "hors-app": "la fenêtre ne parle pas à l'app (pas de __TAURI__)"}[cle])
        if cle == "bienvenue":
            return "Vérifier mon %s" % self.p.machine
        if cle == "connexion":
            return "Plus tard"
        if relance:
            if cle == "deja-installe":
                return "Continuer"
            if cle in ("diagnostic", "voyage", "connexion-installation") or cle.startswith("etape-"):
                self.echouer("au deuxième lancement, l'app veut encore installer")
            return None
        if cle == "deja-installe":
            self.echouer("au premier lancement, l'app dit que tout est déjà installé : la VM n'est pas neuve")
        return {"diagnostic": "Tout installer", "voyage": "Passer (recherche par mots-clés)",
                "connexion-installation": "Plus tard"}.get(cle)

    def avancer(self, but, delai, relance=False):
        """Until the screen `but`, within delai seconds. Returns {screen: first Ecran of it}."""
        prefixe = "relance-" if relance else ""
        debut = self.horloge()
        fin = debut + delai if self.echeance is None else min(debut + delai, self.echeance)
        cle_prec, depuis, capture_prec, clic = None, 0.0, 0.0, None
        vus = {}
        while True:
            e = self.lire()
            cle, _ = cle_ecran(e)
            now = self.horloge()
            if cle != cle_prec:
                cle_prec, depuis, capture_prec, clic = cle, now, now, None
                self.entrer(cle, prefixe)
            elif now - capture_prec >= CAPTURE_ATTENTE:
                capture_prec = now
                self.capturer("%s%s-%dmin" % (prefixe, cle, int((now - depuis) // 60)))
            if cle not in vus or (vus[cle].partiel and not e.partiel):
                vus[cle] = e
            if cle == but and not e.partiel:
                self.ecrans[-1]["duree_s"] = 0
                return vus
            label = None if e.partiel else self.reaction(cle, e, relance)
            state = e.bouton(label) if label else None
            if label and not state:
                # The page's accessibility tree may show the words before the buttons: read it all again.
                self.forcer = True
            if state:
                if clic is None or now - clic[0] >= CLIC_RELANCE:
                    if clic is not None and clic[1] >= CLIC_ESSAIS:
                        self.echouer("le clic sur « %s » ne change rien (%d essais)" % (label, clic[1]))
                    how = self.p.cliquer(label)
                    self.clics.append({"a_s": self.secondes(), "ecran": prefixe + cle, "bouton": label, "comment": how})
                    clic = (now, (clic[1] if clic else 0) + 1)
            elif label and now - depuis >= CLIC_RELANCE * CLIC_ESSAIS:
                self.echouer("l'écran « %s » n'offre pas le bouton « %s »%s" % (cle, label, " (désactivé)" if state is False else ""))
            if cle in ("inconnu", "chargement", "sans-fenetre") and now - depuis >= INCONNU_MAX:
                self.echouer("écran « %s » depuis %d s" % (cle, int(now - depuis)))
            if now >= fin:
                self.echouer("toujours sur l'écran « %s » après %d min%s" % (
                    cle, int((now - debut) // 60), "" if fin == debut + delai else " (fin du temps du scénario)"))
            self.dormir(POLL)

    def lancer(self, env, prefixe=""):
        self.t("app-%slancement" % prefixe)
        self.precedent, self.forcer = None, True
        self.p.ouvrir(env)
        if not self.p.attendre(lambda: self.lire().fenetre, DELAI_FENETRE, self.horloge, self.dormir):
            self.capturer(prefixe + "sans-fenetre")
            self.echouer("aucune fenêtre %d s après l'ouverture de l'app" % DELAI_FENETRE)

    def premier_lancement(self, env):
        """Bienvenue to the final screen; returns how long it took, in seconds."""
        self.lancer(env)
        start = self.horloge()
        vus = self.avancer("fini", DELAI_PREMIER)
        for must in ("bienvenue", "diagnostic"):
            self.t.check(must in vus, "le parcours n'est pas passé par l'écran « %s »" % must)
        for b in ("Personnaliser mon cerveau (15 min)", "Ouvrir mon cerveau dans Obsidian"):
            self.t.check(vus["fini"].bouton(b) is not None, "l'écran final n'a pas le bouton « %s »" % b)
        self.t.details["diagnostic"] = {"bibo": vus["diagnostic"].texte, "textes": vus["diagnostic"].textes[:60]}
        for label, detail in deja_presents(vus["diagnostic"].textes, self.p.neufs):
            self.t.notes.append("déjà sur la VM avant l'app : %s (%s), son installation par l'app n'est pas testée"
                                % (label, detail))
        manque = etapes_manquees(vus)
        self.t.check(not manque, manque)
        return int(self.horloge() - start)

    def relance(self, env):
        """The app opened again: it must say everything is installed, and get to the end."""
        self.lancer(env, "relance-")
        vus = self.avancer("fini", DELAI_RELANCE, relance=True)
        self.t.check("deja-installe" in vus, "au deuxième lancement, l'app n'a pas dit « Tout est déjà installé »")
        return vus

    def sur_echec(self):
        """A last capture and the raw tree next to it, for whoever reads the failure."""
        try:
            self.capturer("echec")
        except Exception:
            pass
        tree = self.p.arbre()
        if tree is not None:
            try:
                with open(self.dossier / ("%02d-echec-arbre.json" % self.n), "w", encoding="utf-8") as f:
                    json.dump(tree, f, ensure_ascii=False, indent=1)
            except OSError:
                pass

    def bilan(self):
        if self.ecrans and self.ecrans[-1]["duree_s"] is None:
            self.ecrans[-1]["duree_s"] = self.secondes() - self.ecrans[-1]["debut_s"]
        mean = lambda xs: int(sum(xs) / len(xs)) if xs else None
        return {"captures": self.captures, "ecrans": self.ecrans, "clics": self.clics,
                "dialogues_systeme": [{k: v for k, v in d.items() if k != "clic_a"} for d in self.dialogues],
                "bibo": {"titre": self.dernier.titre, "texte": self.dernier.texte},
                "dernier_ecran": dict(self.dernier.resume(), lecture=self.dernier.lecture), "lecture_fenetre_ms": {"pleine": mean(self.lectures), "rapide": mean(self.rapides),
                                                           "lectures": [len(self.lectures), len(self.rapides)]}}


# --------------------------------------------------------------------------- getting the app

def _get(url, token=None, timeout=60):
    if token and not url.startswith("https://api.github.com/"):
        raise ValueError("the token only goes to api.github.com")
    headers = {"User-Agent": "brain-in-a-box-terrain", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def telecharger(url, dest, timeout=600):
    """A release file, as a browser fetches it: no token (urllib would carry it along the redirect)."""
    with _get(url, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


# What makes release.yml publish a preview (its `on.push.paths`): the newest commit touching one
# of them on the tested master must be in the preview the night downloads.
PUBLIE = ("app", ".github/workflows/release.yml", ".github/ci/app-release-plan.py")
RETARD_MAX_H = 24


def _api(path, token):
    with _get("https://api.github.com/repos/%s/%s" % (REPO_SLUG, path), token) as r:
        return json.loads(r.read().decode("utf-8"))


def retard_preview(t, token, rel, sha, maintenant=None):
    """None when the preview holds master's last change to the app; else what it misses. Fails when
    that change is older than RETARD_MAX_H: release.yml did not publish it, the night tests an old app."""
    if not sha:
        return "commit testé inconnu : fraîcheur de la préversion non vérifiée"
    try:
        derniers = [c for path in PUBLIE for c in _api("commits?sha=%s&path=%s&per_page=1" % (sha, path), token)[:1]]
        if not derniers:
            return None
        dernier = max(derniers, key=lambda c: c["commit"]["committer"]["date"])
        etat = _api("compare/%s...%s" % (dernier["sha"], rel["tag_name"]), token).get("status")
    except (OSError, ValueError, KeyError, TypeError) as e:
        t.check(False, "l'API ne dit pas si la préversion est à jour (%s)" % type(e).__name__)
    if etat in ("identical", "ahead"):
        return None
    quand = datetime.strptime(dernier["commit"]["committer"]["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    heures = ((maintenant or datetime.now(timezone.utc)) - quand).total_seconds() / 3600
    pourquoi = "la préversion %s n'a pas le dernier changement de l'app sur master (%s, il y a %d h) : release.yml " \
               "ne l'a pas publié" % (rel["tag_name"], dernier["sha"][:7], heures)
    t.check(heures < RETARD_MAX_H, pourquoi)
    return pourquoi + " (pas encore ?)"


def depuis_release(t, pilote, dossier, token, details, sha=None):
    """The newest preview, its SHA256SUMS and its attestation, as a careful user checks them; and it
    must hold the tested master's last change to the app."""
    t("app-telechargement")
    t.check(token, "pas de GH_TOKEN pour lire les releases et vérifier l'attestation")
    try:
        with _get("https://api.github.com/repos/%s/releases?per_page=50" % REPO_SLUG, token) as r:
            releases = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        t.check(False, "l'API des releases ne répond pas (%s)" % type(e).__name__)
    rel = derniere_preview(releases if isinstance(releases, list) else [])
    t.check(rel, "aucune préversion app-v*-preview.* publiée")
    tag = rel["tag_name"]
    details.update(release=tag, release_commit=rel.get("target_commitish"), release_creee=rel.get("created_at"))
    retard = retard_preview(t, token, rel, sha)
    if retard:
        t.notes.append(retard)
    fichier = asset(rel, pilote.suffixe, tag)
    sums = asset(rel, "SHA256SUMS", tag)
    t.check(fichier, "la préversion %s n'a pas de fichier *%s" % (tag, pilote.suffixe))
    t.check(sums, "la préversion %s n'a pas de SHA256SUMS" % tag)
    name, url, size, digest = fichier
    dest = Path(dossier) / name
    try:
        telecharger(url, dest)
        sums_path = Path(tempfile.mkdtemp(prefix="sums-")) / "SHA256SUMS"
        telecharger(sums[1], sums_path)
    except OSError as e:
        t.check(False, "téléchargement de %s impossible (%s)" % (name, type(e).__name__))
    t.check(not size or dest.stat().st_size == size, "%s : %d octets reçus, %d annoncés" % (name, dest.stat().st_size, size))

    t("app-verification")
    got = sha256(dest)
    want = somme_attendue(sums_path.read_text(encoding="utf-8", errors="replace"), name)
    t.check(want, "SHA256SUMS n'a pas de ligne pour %s" % name)
    t.check(got == want, "%s ne correspond pas à SHA256SUMS" % name)
    t.check(not digest or digest == "sha256:" + got, "%s ne correspond pas à l'empreinte de GitHub" % name)
    details["sha256"] = got
    gh = shutil.which("gh")
    t.check(gh, "gh absent : l'attestation ne peut pas être vérifiée")
    # What gh needs to start, and the token; nothing else of the job (Windows: its folders, its root).
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SystemRoot", "TEMP", "TMP")
           if os.environ.get(k)}
    env["GH_TOKEN"] = token
    p = subprocess.run([gh, "attestation", "verify", str(dest), "-R", REPO_SLUG, "--signer-workflow",
                        REPO_SLUG + "/.github/workflows/release.yml", "--source-ref", "refs/heads/master"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=180)
    said = p.stdout.decode("utf-8", "replace")
    t.check(p.returncode == 0, "l'attestation de %s ne se vérifie pas : %s" % (name, said.strip()[-200:]))
    details["attestation"] = "vérifiée (release.yml, refs/heads/master)"
    return dest


def depuis_build(t, fichier, dossier, details):
    """The app the workflow built from the tested commit, put where a download lands."""
    t("app-construction")
    t.check(fichier and Path(fichier).is_file(),
            "pas d'app construite (%s) : voir l'étape de construction du job" % (fichier or "aucun fichier"))
    dest = Path(dossier) / Path(fichier).name
    shutil.copyfile(fichier, dest)
    details["sha256"] = sha256(dest)
    return dest


def archive_testee(kit, t, opts, sha):
    """The tested commit's sources, as GitHub zips a branch, with a marker the robot checks after."""
    work = kit.workdir(opts, "app-sources")
    marker = work / MARQUEUR
    marker.write_text(sha + "\n", encoding="utf-8")
    zip_path = work / "brain-in-a-box-terrain.zip"
    rc, out = kit.run(["git", "-C", kit.REPO, "archive", "--format=zip", "--prefix=brain-in-a-box-terrain/",
                       "--add-file=%s" % marker, "-o", zip_path, sha], timeout=300)
    t.check(rc == 0, "git archive des sources testées : %s" % out.strip()[-200:])
    return zip_path


# --------------------------------------------------------------------------- the scenario

def arguments(p):
    p.add_argument("--source", default="", help="release (master) ou build (une branche)")
    p.add_argument("--fichier", default="", help="build : l'app construite par le workflow")
    p.add_argument("--captures", default="", help="le dossier des captures (défaut : <home>/terrain-captures)")
    p.add_argument("--stub", default="", help="Windows : le dossier du faux python.exe du Microsoft Store "
                                              "(.github/ci/poste-neuf.ps1), en tête du PATH de l'app")
    p.add_argument("--delai-min", type=int, default=0, help="le temps du scénario, en minutes, avant que le délai "
                                                            "de son étape du job ne le tue (0 : sans limite)")


def pilote_du_systeme(travail, opts):
    if sys.platform == "darwin":
        return PiloteMac(travail)
    if os.name == "nt":
        return PiloteWindows(travail, stub=getattr(opts, "stub", "") or None)
    raise ErreurPilote("pas encore de pilote de fenêtre pour %s" % sys.platform)


def outils(t, home, pilote):
    """What the app's steps put on the machine, apart from the brain."""
    t("app-outils")
    for name, places in pilote.outils(home):
        shown = str(places[0]).replace(str(home), "~")
        t.check(any(Path(p).exists() for p in places), "%s n'est pas installé (%s)" % (name, shown))


def journal_installation(home, lignes=80):
    """The end of the kit's install log (install.sh, install.ps1): why the brain step stopped, which
    the app shows in its « Détails » only and the VM takes away with it."""
    path = Path(home) / ".brain-in-a-box" / "install.log"
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace").splitlines()[-lignes:]
    except OSError:
        return ["(pas de %s)" % path.name]


def clics_sans_souris(clics):
    """A reason when a click was not the mouse's (AXPress, Invoke, Toggle), else None."""
    autres = [c for c in clics if c.get("comment") != CLIC_SOURIS]
    if not autres:
        return None
    return "%d clic(s) sans la souris, qu'un utilisateur n'aurait pas pu faire (bouton couvert ? fenêtre derrière ?) : %s" % (
        len(autres), " ; ".join("« %s » (%s) : %s" % (c.get("bouton"), c.get("ecran"), c.get("comment")) for c in autres[:5]))


def scenario(t, opts, kit, pilote=None, horloge=time.monotonic, dormir=time.sleep):
    """terrain.py's scenario `app` (VM only). pilote, horloge, dormir: the tests' stand-ins."""
    # Read-only, for the releases and the attestation: never for the app nor anything after.
    token = os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GITHUB_TOKEN", None)
    home = Path(opts.home)
    captures = Path(opts.captures) if getattr(opts, "captures", "") else home / "terrain-captures"
    captures.mkdir(parents=True, exist_ok=True)
    source = getattr(opts, "source", "")
    delai = getattr(opts, "delai_min", 0) or 0
    details = t.details
    details["source"] = source
    parcours = None
    try:
        t("app-preparation")
        t.check(source in SOURCES, "source inconnue « %s » (release ou build)" % source)
        pilote = pilote or pilote_du_systeme(kit.workdir(opts, "app"), opts)
        parcours = Parcours(t, pilote, captures, horloge, dormir, echeance=horloge() + delai * 60 if delai else None)
        downloads = home / "Downloads"
        downloads.mkdir(exist_ok=True)
        extra = {}
        if source == "release":
            fichier = depuis_release(t, pilote, downloads, token, details, kit.tested_ref())
            details["sources_du_cerveau"] = "zip master de GitHub (comme un utilisateur)"
        else:
            fichier = depuis_build(t, opts.fichier, downloads, details)
            sha = kit.tested_ref()
            t.check(sha, "commit testé inconnu")
            extra["BIAB_ARCHIVE"] = str(archive_testee(kit, t, opts, sha))
            details["sources_du_cerveau"] = "commit testé %s (BIAB_ARCHIVE)" % sha[:12]
        details["fichier"] = fichier.name
        details["quarantaine_posee"] = pilote.marquer_telecharge(fichier)

        t("app-installation")
        t.notes.extend(pilote.installer(fichier, details, parcours.capturer))
        env = pilote.environnement(home, extra)

        details["duree_parcours_s"] = parcours.premier_lancement(env)
        t("app-fermeture")
        details["fermeture"] = pilote.fermer()
        t("app-nuit")
        nuit = pilote.ecarter_la_nuit()
        if nuit:
            details["nuit_reelle"] = nuit

        outils(t, home, pilote)
        t("app-sources")
        marker = home / ".brain-in-a-box" / "src" / MARQUEUR
        if source == "build":
            t.check(marker.is_file() and marker.read_text(encoding="utf-8").strip() == kit.tested_ref(),
                    "l'app n'a pas installé les sources du commit testé (BIAB_ARCHIVE ignoré ?)")
        else:
            t.check(not marker.exists(), "des sources du terrain traînent dans ~/.brain-in-a-box/src")
        kit.check_install(t, opts)

        parcours.relance(env)
        t("app-fermeture-relance")
        pilote.fermer()
        t("app-clics")
        sans = clics_sans_souris(getattr(pilote, "clics_installation", []) + parcours.clics)
        t.check(not sans, sans)

        # The nightly the app's install registered, started by the scheduler as on the first night.
        t("app-nuit-rendue")
        rendue = pilote.rendre_la_nuit()
        if rendue:
            details["nuit_rendue"] = rendue
        kit.run_nightly(t, opts)
        t.resume = "app %s installée, %d écrans cliqués à la souris jusqu'au cerveau en %d min, vérifications conformes, " \
                   "deuxième lancement : déjà installé, une nuit lancée par le planificateur" % (
                       details.get("release") or "construite", len(parcours.ecrans), details["duree_parcours_s"] // 60)
    except ErreurPilote as e:
        if parcours:
            parcours.sur_echec()
            details["install_log"] = journal_installation(home)
        t.check(False, parcours.raison(str(e)) if parcours else str(e))
    except kit.Echec as e:
        if parcours:
            parcours.sur_echec()
            details["install_log"] = journal_installation(home)
            raise kit.Echec(parcours.raison(str(e))) from None
        raise
    finally:
        if parcours:
            details.update(parcours.bilan())
        if getattr(pilote, "clics_installation", None):
            details["clics_installation"] = pilote.clics_installation


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "source" and argv[1] == "--ref":
        print("source=" + choisir_source(argv[2]))
        return 0
    print("usage : terrain_app.py source --ref <ref>   (le scénario : terrain.py app)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
