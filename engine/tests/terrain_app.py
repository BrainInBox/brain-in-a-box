#!/usr/bin/env python3
"""Field test "app": the desktop app as a user gets it, clicked through in its real window.

On a fresh GitHub VM (terrain.yml, job app-macos), the robot gets the app the way a user does,
installs it, opens it and clicks through its real window, Bienvenue to the final screen, with a
capture of every screen. Then it checks the brain works and opens the app a second time: it must
say everything is already installed.

    python3 -I engine/tests/terrain.py app --vm --parcours app --source release --captures DIR
    python3 -I engine/tests/terrain.py app --vm --parcours app --source build --fichier APP.dmg --captures DIR
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
GitHub's macOS runners allow. Windows: later.

This module imports nothing of terrain.py: terrain.py passes itself in (kit), so a failure is
always terrain.py's own Echec, even when terrain.py runs as __main__.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
import uuid
from pathlib import Path

REPO_SLUG = "BrainInBox/brain-in-a-box"
PREVIEW_RE = re.compile(r"^app-v(\d+)\.(\d+)\.(\d+)-preview\.(\d+)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCES = ("release", "build")
# The marker the robot adds to the sources it hands the app on a branch: the app used them.
MARQUEUR = ".terrain-ref"

POLL = 2.0
CAPTURE_ATTENTE = 120       # a capture every 2 min while a screen waits
CLIC_RELANCE = 20           # a click that changed nothing is tried again after this
CLIC_ESSAIS = 3
INCONNU_MAX = 180           # a screen the robot does not know, for this long: a failure
DELAI_FENETRE = 120
DELAI_PREMIER = 55 * 60     # Bienvenue to the final screen (the brain step alone may take 40 min)
DELAI_RELANCE = 5 * 60

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

    def __init__(self, fenetre=False, titre="", texte="", boutons=(), textes=(), dialogues=(), lecture=""):
        self.fenetre, self.titre, self.texte = fenetre, norm(titre), norm(texte)
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
    web = None
    for f in fenetres or []:
        for chemin, node in _aplatir(f.get("arbre") or {}):
            if node.get("r") == "AXWebArea":
                web = node
                break
        if web:
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
                 textes=[t for _, t in textes], dialogues=dialogues, lecture=lecture)


def dialogue_depuis_arbre(processus, arbre):
    flat = _aplatir(arbre or {})
    return {"processus": processus,
            "textes": [norm(_texte(n)) for _, n in flat if n.get("r") in ("AXStaticText", "AXTextArea") and _texte(n).strip()],
            "boutons": [norm(n.get("n") or n.get("d") or "") for _, n in flat if n.get("r") == "AXButton"
                        and (n.get("n") or n.get("d"))]}


# The system asks on the app's behalf (its folder Documents, mostly): the app's own window tells the
# user to accept (« clique sur « Autoriser » »). The robot accepts only a request naming the app.
AUTORISER = ("Allow", "Autoriser", "OK")


def bouton_autoriser(dialogue):
    texte = " ".join(dialogue.get("textes") or [])
    if not re.search(r"Brain in a Box|brain-in-a-box", texte):
        return None
    return next((b for b in AUTORISER if b in (dialogue.get("boutons") or [])), None)


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

    def marquer_telecharge(self, fichier):
        """Marks the file as a browser does once it downloaded it."""
        raise NotImplementedError

    def installer(self, fichier, details):
        """Installs the app from the downloaded file as a user does; returns notes (list)."""
        raise NotImplementedError

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
  var out = req.op === 'cliquer' ? cliquer(se, req) : lire(se, req);
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

function noeud(el, depth, budget) {
  budget.left -= 1;
  var n = {r: str(prop(el, 'role')), s: str(prop(el, 'subrole')), n: str(prop(el, 'name')), v: str(prop(el, 'value')), k: []};
  if (n.r === 'AXButton') {
    n.d = str(prop(el, 'description'));
    n.e = prop(el, 'enabled') !== false;
  }
  // A text has no children; a button's label is its name (its text only when it has none).
  var leaf = n.r === 'AXStaticText' || (n.r === 'AXButton' && (n.n || n.d));
  if (!leaf && depth < 40 && budget.left > 0) {
    var kids = [];
    try { kids = el.uiElements(); } catch (e) {}
    for (var i = 0; i < kids.length && budget.left > 0; i++) n.k.push(noeud(kids[i], depth + 1, budget));
  }
  return n;
}

function proc(se, name) {
  try {
    var ps = se.processes.whose({name: name});
    if (ps.length > 0) return ps[0];
  } catch (e) {}
  return null;
}

function fenetres(p, budget) {
  var out = [];
  var ws = [];
  try { ws = p.windows(); } catch (e) {}
  for (var i = 0; i < ws.length; i++) out.push({titre: str(prop(ws[i], 'name')), arbre: noeud(ws[i], 0, {left: budget})});
  return out;
}

function lire(se, req) {
  var res = {app: null, dialogues: [], devant: ''};
  var p = proc(se, req.processus);
  if (p) res.app = fenetres(p, 900);
  try { res.devant = se.processes.whose({frontmost: true})[0].name(); } catch (e) {}
  var names = (req.dialogues || []).slice();
  // Whatever came to the front over the app is shown too: an unknown dialog gets a capture.
  if (res.devant && res.devant !== req.processus && (req.ignorer || []).indexOf(res.devant) < 0 &&
      names.indexOf(res.devant) < 0) names.push(res.devant);
  names.forEach(function (name) {
    var d = proc(se, name);
    if (d) fenetres(d, 200).forEach(function (f) { res.dialogues.push({processus: name, titre: f.titre, arbre: f.arbre}); });
  });
  return res;
}

function chercher(el, label, depth) {
  if (depth > 40) return null;
  var kids = [];
  try { kids = el.uiElements(); } catch (e) { return null; }
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (str(prop(k, 'role')) === 'AXButton' && (norm(prop(k, 'name')) === label || norm(prop(k, 'description')) === label)) return k;
    var found = chercher(k, label, depth + 1);
    if (found) return found;
  }
  return null;
}

function cliquer(se, req) {
  var p = proc(se, req.processus);
  if (!p) return {ok: false, pourquoi: 'processus'};
  var label = norm(req.libelle);
  var ws = [];
  try { ws = p.windows(); } catch (e) {}
  for (var i = 0; i < ws.length; i++) {
    var b = chercher(ws[i], label, 0);
    if (!b) continue;
    if (prop(b, 'enabled') === false) return {ok: false, pourquoi: 'inactif'};
    try { b.actions.byName('AXPress').perform(); return {ok: true, comment: 'AXPress'}; } catch (e) {}
    try { se.click(b); return {ok: true, comment: 'click'}; } catch (e) { return {ok: false, pourquoi: String(e)}; }
  }
  return {ok: false, pourquoi: 'absent'};
}
'''


class PiloteMac(Pilote):
    """System Events (the accessibility tree WebKit gives the app's page) and screencapture.
    GitHub's macOS runners grant both to /bin/bash and /usr/bin/osascript (TCC)."""

    machine = "Mac"
    suffixe = "_macos-universal.dmg"
    processus = "brain-in-a-box"
    app = Path("/Applications/Brain in a Box.app")
    DIALOGUES = ("UserNotificationCenter", "CoreServicesUIAgent", "SecurityAgent", "universalAccessAuthWarn")
    # In front, yet no dialog: the desktop itself.
    IGNORER = ("Finder", "Dock", "SystemUIServer", "WindowManager", "ControlCenter", "loginwindow")

    def __init__(self, travail):
        self.travail = Path(travail)
        self.script = self.travail / "pilote-mac.js"
        self.script.write_text(JXA, encoding="utf-8")
        self.dernier_arbre = None

    def _run(self, argv, timeout=120, env=None):
        try:
            p = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ErreurPilote("%s n'a pas fini en %d s" % (Path(str(argv[0])).name, timeout))
        except OSError as e:
            raise ErreurPilote("%s ne se lance pas (%s)" % (Path(str(argv[0])).name, e.strerror or type(e).__name__))
        return p.returncode, p.stdout.decode("utf-8", "replace")

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

    def installer(self, fichier, details):
        """The DMG opened, the app dragged into Applications, then Gatekeeper: macOS refuses the
        unsigned preview (seen, expected), and the user opens it anyway (quarantine lifted)."""
        notes = []
        if self.app.exists():
            raise ErreurPilote("%s existe déjà : la VM n'est pas neuve" % self.app)
        mnt = Path(tempfile.mkdtemp(prefix="dmg-", dir=str(self.travail)))
        rc, out = self._run(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
                             "-mountpoint", mnt, fichier], timeout=300)
        if rc != 0:
            raise ErreurPilote("le DMG ne s'ouvre pas (hdiutil attach : %s)" % out.strip()[-200:])
        try:
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
        else:
            notes.append("Gatekeeper désactivé sur la VM : refus de l'app non constaté (%s)" % status.strip())

        # « Ouvrir quand même » in Réglages > Confidentialité et sécurité.
        rc, out = self._run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", self.app], timeout=60)
        if rc != 0 or self.quarantaine(self.app):
            raise ErreurPilote("la quarantaine de l'app ne s'enlève pas : %s" % out.strip()[-200:])
        return notes

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
        return ecran_depuis_arbre(fen, [d for d in dialogues if d["textes"] or d["boutons"]])

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


# --------------------------------------------------------------------------- the walk through the window

class Parcours:
    """Plays the screens of the app's window as a user who wants it all installed, skips the Voyage
    key and the Claude login. Every new screen, or install step, gets a capture and a step name
    (t), every 2 min of waiting another capture. A failure names the screen and quotes Bibo."""

    def __init__(self, t, pilote, dossier, horloge=time.monotonic, dormir=time.sleep):
        self.t, self.p, self.dossier = t, pilote, Path(dossier)
        self.horloge, self.dormir = horloge, dormir
        self.n = 0
        self.captures = []
        self.ecrans = []        # [{"ecran", "debut_s", "duree_s"}]
        self.dialogues = []     # system dialogs seen: [{"processus", "textes", "boutons", "capture", "reponse"}]
        self.ouverts = []       # the dialogs of the last reading
        self.dernier = Ecran()  # the last screen where Bibo said something
        self.debut = horloge()
        self.lectures = []      # how long each reading of the window took, ms

    def secondes(self):
        return int(self.horloge() - self.debut)

    def capturer(self, ecran):
        self.n += 1
        nom = nom_capture(self.n, ecran)
        self.p.capturer(self.dossier / nom)
        self.captures.append(nom)
        return nom

    def lire(self):
        start = self.horloge()
        e = self.p.lire()
        self.lectures.append(int((self.horloge() - start) * 1000))
        if e.titre:
            self.dernier = e
        self.ouverts = e.dialogues
        for d in e.dialogues:
            self.dialogue(d)
        return e

    def dialogue(self, d):
        """A system dialog: captured once; accepted (at most CLIC_ESSAIS times, CLIC_RELANCE apart)
        when it asks on the app's behalf, as the app's own window tells the user to."""
        key = (d.get("processus"), tuple(d.get("textes") or ())[:2])
        seen = next((x for x in self.dialogues if (x["processus"], tuple(x["textes"])[:2]) == key), None)
        if not seen:
            seen = {"processus": d.get("processus"), "textes": (d.get("textes") or [])[:10],
                    "boutons": d.get("boutons") or [], "reponse": None, "essais": 0, "clic_a": None}
            self.dialogues.append(seen)
            seen["capture"] = self.capturer("dialogue-" + str(d.get("processus") or "systeme"))
        bouton = bouton_autoriser(d)
        now = self.horloge()
        if not bouton or seen["essais"] >= CLIC_ESSAIS or (seen["clic_a"] is not None and now - seen["clic_a"] < CLIC_RELANCE):
            return
        seen["essais"] += 1
        seen["clic_a"] = now
        try:
            self.p.cliquer(bouton, processus=d.get("processus"))
            seen["reponse"] = bouton
        except ErreurPilote as e:
            seen["reponse"] = "échec du clic : %s" % e

    def entrer(self, cle, prefixe):
        if self.ecrans and self.ecrans[-1]["duree_s"] is None:
            self.ecrans[-1]["duree_s"] = self.secondes() - self.ecrans[-1]["debut_s"]
        self.ecrans.append({"ecran": prefixe + cle, "debut_s": self.secondes(), "duree_s": None})
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
        fin = self.horloge() + delai
        cle_prec, depuis, capture_prec, clic = None, 0.0, 0.0, None
        vus = {}
        while True:
            e = self.lire()
            cle, _ = cle_ecran(e)
            now = self.horloge()
            if cle != cle_prec:
                cle_prec, depuis, capture_prec, clic = cle, now, now, None
                vus.setdefault(cle, e)
                self.entrer(cle, prefixe)
            elif now - capture_prec >= CAPTURE_ATTENTE:
                capture_prec = now
                self.capturer("%s%s-%dmin" % (prefixe, cle, int((now - depuis) // 60)))
            if cle == but:
                self.ecrans[-1]["duree_s"] = 0
                return vus
            label = self.reaction(cle, e, relance)
            state = e.bouton(label) if label else None
            if state:
                if clic is None or now - clic[0] >= CLIC_RELANCE:
                    if clic is not None and clic[1] >= CLIC_ESSAIS:
                        self.echouer("le clic sur « %s » ne change rien (%d essais)" % (label, clic[1]))
                    self.p.cliquer(label)
                    clic = (now, (clic[1] if clic else 0) + 1)
            elif label and now - depuis >= CLIC_RELANCE * CLIC_ESSAIS:
                self.echouer("l'écran « %s » n'offre pas le bouton « %s »%s" % (cle, label, " (désactivé)" if state is False else ""))
            if cle in ("inconnu", "chargement", "sans-fenetre") and now - depuis >= INCONNU_MAX:
                self.echouer("écran « %s » depuis %d s" % (cle, int(now - depuis)))
            if now >= fin:
                self.echouer("toujours sur l'écran « %s » après %d min" % (cle, int(delai // 60)))
            self.dormir(POLL)

    def lancer(self, env, prefixe=""):
        self.t("app-%slancement" % prefixe)
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
        if "diagnostic" in vus:
            self.t.details["diagnostic"] = {"bibo": vus["diagnostic"].texte, "textes": vus["diagnostic"].textes[:40]}
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
        mean = int(sum(self.lectures) / len(self.lectures)) if self.lectures else None
        return {"captures": self.captures, "ecrans": self.ecrans,
                "dialogues_systeme": [{k: v for k, v in d.items() if k != "clic_a"} for d in self.dialogues],
                "bibo": {"titre": self.dernier.titre, "texte": self.dernier.texte},
                "dernier_ecran": self.dernier.resume(), "lecture_fenetre_ms": mean}


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


def depuis_release(t, pilote, dossier, token, details):
    """The newest preview, its SHA256SUMS and its attestation, as a careful user checks them."""
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
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "GH_TOKEN": token}
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


def pilote_du_systeme(travail):
    if sys.platform == "darwin":
        return PiloteMac(travail)
    raise ErreurPilote("pas encore de pilote de fenêtre pour %s" % sys.platform)


def outils(t, home):
    """What the app's steps put on the Mac, apart from the brain."""
    t("app-outils")
    claude = Path(home) / ".local" / "bin" / "claude"
    t.check(claude.exists(), "Claude Code n'est pas installé (~/.local/bin/claude)")
    t.check((Path(home) / ".bun" / "bin" / "bun").is_file(), "Bun n'est pas installé (~/.bun/bin/bun)")
    t.check(any((Path(d) / "Obsidian.app" / "Contents" / "MacOS").is_dir()
                for d in ("/Applications", Path(home) / "Applications")), "Obsidian n'est pas installé")


def scenario(t, opts, kit, pilote=None, horloge=time.monotonic, dormir=time.sleep):
    """terrain.py's scenario `app` (VM only). pilote, horloge, dormir: the tests' stand-ins."""
    # Read-only, for the releases and the attestation: never for the app nor anything after.
    token = os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GITHUB_TOKEN", None)
    home = Path(opts.home)
    captures = Path(opts.captures) if getattr(opts, "captures", "") else home / "terrain-captures"
    captures.mkdir(parents=True, exist_ok=True)
    source = getattr(opts, "source", "")
    details = t.details
    details["source"] = source
    parcours = None
    try:
        t("app-preparation")
        t.check(source in SOURCES, "source inconnue « %s » (release ou build)" % source)
        pilote = pilote or pilote_du_systeme(kit.workdir(opts, "app"))
        parcours = Parcours(t, pilote, captures, horloge, dormir)
        downloads = home / "Downloads"
        downloads.mkdir(exist_ok=True)
        extra = {}
        if source == "release":
            fichier = depuis_release(t, pilote, downloads, token, details)
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
        t.notes.extend(pilote.installer(fichier, details))
        env = env_utilisateur(home, extra)

        details["duree_parcours_s"] = parcours.premier_lancement(env)
        t("app-fermeture")
        details["fermeture"] = pilote.fermer()

        outils(t, home)
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
        t.resume = "app %s installée, %d écrans cliqués jusqu'au cerveau en %d min, vérifications conformes, " \
                   "deuxième lancement : déjà installé" % (
                       details.get("release") or "construite", len(parcours.ecrans), details["duree_parcours_s"] // 60)
    except ErreurPilote as e:
        if parcours:
            parcours.sur_echec()
        t.check(False, parcours.raison(str(e)) if parcours else str(e))
    except kit.Echec as e:
        if parcours:
            parcours.sur_echec()
            raise kit.Echec(parcours.raison(str(e))) from None
        raise
    finally:
        if parcours:
            details.update(parcours.bilan())


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
