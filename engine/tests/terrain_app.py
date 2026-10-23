#!/usr/bin/env python3
"""Field test "app": the desktop app as a user gets it, clicked through in its real window.

On a fresh GitHub VM (terrain.yml, jobs app-macos, app-windows and app-linux), the robot gets the app the
way a user does, installs it, opens it and clicks through its real window, Bienvenue to the final
screen, with a capture of every screen. Bibo's advice must be at the diagnosis (on the choice screen
where Codex is open). At the end, the robot goes into « Importer ma mémoire », to the copy-paste that
shows the sentence to paste, and back: nothing is sent, no AI is asked. Then it checks the brain works
and opens the app a second time: it must say everything is already installed. Then, Claude alone, a
third time: the welcome's « Guide-moi dans un logiciel », and the guide's window walked to the procedure
in the brain with a fake Claude (engine/tests/fake_claude.py) put where the app finds Claude Code: two
made-up captures pasted as a user pastes them, the step's red ellipse found on a capture of the screen,
no image of the guide left on the disk. Linux plays an account without Opus: Sonnet, the cautious mode.

The path "app-codex" does the same with Codex chosen alone, where the robot opened Codex in its copy of
the kit, as the other benches open it (test-hooks.sh, codex_captures.open_gate): the tested commit's
sources with _agents.py's gate open on every system, handed to the app (BIAB_ARCHIVE, on master too) and
put where the app keeps the sources it installs from (~/.brain-in-a-box/src), whose _agents.py the app
reads its gate in before any install. The app itself is the one users get, its own gate closed: on the
path "app", Claude alone, no choice may be asked.

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
app has to bring it. PiloteLinux: AT-SPI (the accessibility tree WebKitGTK gives the page, read by
the system's Python), the mouse through xdotool, ImageMagick's capture of the X screen (Xvfb with
a window manager, the job's desktop); the AppImage as a user runs it from ~/Applications, on an
Ubuntu without git nor curl, as Ubuntu's desktop comes: the app has to bring them.

This module imports nothing of terrain.py: terrain.py passes itself in (kit), so a failure is
always terrain.py's own Echec, even when terrain.py runs as __main__.
"""
import base64
import hashlib
import json
import ntpath
import os
import plistlib
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.request
import uuid
import zipfile
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
DELAI_DEVANT = 5            # Bibo back in front after macOS's picker
DELAI_PREMIER = 75 * 60     # Bienvenue to the final screen (Apple's tools up to 45 min, the brain step 40)
DELAI_RELANCE = 5 * 60
# How every click is done: the mouse on the button, as a user clicks, maybe followed by where, in
# parentheses (macOS's picker). Anything else (AXPress, Invoke) proves no user could have clicked there:
# the scenario fails, once the walk is over.
CLIC_SOURIS = "clic souris"

# Bibo's titles (app/src/app.js), after norm(): the screen they stand for.
ECRANS = (
    ("bienvenue", "Salut, moi c'est Bibo"),
    # Opened again with the brain and Claude Code there: the guide first (G0, docs/proposals/guide.md), or
    # Claude's login first when it was put off.
    ("bienvenue-retour", "Content de te revoir"),
    ("choix", "Avec quelle IA veux-tu travailler"),
    ("choix-resume", "Lequel écrit le résumé de chaque soir"),
    ("diagnostic-en-cours", "Je regarde ton "),
    ("diagnostic-impossible", "Je n'ai pas réussi à examiner"),
    ("bloque", "Je ne peux pas installer"),
    ("deja-installe", "Tout est déjà installé"),
    ("diagnostic", "J'ai fait le tour"),
    # Search by meaning, an option offered at the end only: never on the way to the brain.
    ("voyage", "Activons la recherche par le sens"),
    ("verif-compte", "Je vérifie ton compte Claude"),
    ("verif-compte-codex", "Je vérifie la connexion de Codex"),
    ("connexion-installation", "Connecte ton compte Claude maintenant"),
    ("connexion", "Dernière étape : ton compte Claude"),
    ("connexion-chatgpt", "Dernière étape : ton compte ChatGPT"),
    ("codex-cle-api", "Codex est connecté avec une clé API"),
    ("codex-entreprise", "Codex est connecté avec un compte ChatGPT d'entreprise"),
    ("codex-systeme", "Codex suit des réglages de ce "),
    ("installe", "C'est installé"),
    ("fini", "Ton second cerveau est prêt"),
    # The end, Claude's login put off (as the robot does: no account on these VMs): it asks for it first.
    ("fini", "Presque prêt"),
    ("import-verif", "Je prépare l'import"),
    ("import-choix", "D'où vient ta mémoire"),
    ("import-collage", "Demande-lui ce qu'il sait de toi"),
    ("import-a-jour", "Ton cerveau doit d'abord être mis à jour"),
    ("import-erreur", "Je ne peux pas importer"),
    ("echec", "Oups, ça a coincé"),
    ("hors-app", "Ouvre l'app Brain in a Box"),
)
# The install screen: Bibo's title while a step runs (STEPS[id].say[0]).
ETAPES = {
    "J'installe Claude Code.": "claude",
    "J'installe Codex.": "codex",
    "Une fenêtre Apple va s'ouvrir : clique sur Installer.": "clt",
    "Ton ordinateur va te demander ton mot de passe.": "paquets",
    "J'installe Bun.": "bun",
    "J'installe Obsidian.": "obsidian",
    "Je prépare ton cerveau.": "brain",
    "J'active la recherche par le sens.": "voyage",
}
# Screens that end the run, whatever the robot was doing.
ARRETS = {"diagnostic-impossible", "bloque", "echec", "hors-app", "import-a-jour", "import-erreur"}
# Bibo's advice (app.js, adviceLines): at the diagnosis, on the choice screen where Codex is open.
CONSEIL = "Mon conseil"
# The sentence to paste in ChatGPT or Claude, as the kit's importer-memoire.py starts it.
PHRASE = "Je change d'outil"
# The copy-paste screen asks the kit for its sentence when it shows: given this long to come.
DELAI_PHRASE = 60
DELAI_IMPORT = 3 * 60
# The returning welcome's way to the guide: offered, not taken (no AI is asked on these VMs).
GUIDE = "Guide-moi dans un logiciel"
CONNECTER = "Connecter mon compte Claude"
# Buttons that only come with a failure (the repair screens have many titles).
BOUTONS_ECHEC = {"Réessayer", "Copier le rapport", "Revérifier"}
# The screens each path must never see: Codex's on the path of Claude alone (the app users get keeps it
# closed), Claude's where Codex is chosen alone; a Codex logged in otherwise than with ChatGPT on neither.
HORS_PARCOURS = {
    False: {"choix", "choix-resume", "verif-compte-codex", "connexion-chatgpt", "codex-cle-api", "codex-entreprise", "codex-systeme"},
    True: {"choix-resume", "verif-compte", "connexion-installation", "connexion", "codex-cle-api", "codex-entreprise", "codex-systeme"},
}
# _agents.py's gate open on every system, as the other benches open it (codex_captures.OPEN_GATE).
PORTE_OUVERTE = 'CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})'


def ouvrir_porte(text):
    """_agents.py's text with Codex open on every system, whatever its gate says; None when the gate's
    line moved."""
    opened, n = re.subn(r"^CODEX_OUVERT = frozenset\(.*\)$", lambda m: PORTE_OUVERTE, text, flags=re.M)
    return opened if n == 1 else None


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
                 partiel=False, places=None, zone=None):
        self.fenetre, self.titre, self.texte = fenetre, norm(titre), norm(texte)
        # Where each button is on the screen ({label: [x, y, w, h]}) and the screen's usable area (the taskbar
        # left out), when the driver can tell (Windows): a user must see the main button whole.
        self.places = {norm(b): r for b, r in (places or {}).items()}
        self.zone = zone
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
    places = {n.get("n") or n.get("d") or _texte(n): n["b"] for c, n in flat if n.get("r") == "AXButton" and rect_valide(n.get("b"))}
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
                 textes=[t for _, t in textes], dialogues=dialogues, lecture=lecture, statut=chemin_statut, places=places)


def rect_valide(r):
    return isinstance(r, list) and len(r) == 4 and all(isinstance(v, int) for v in r) and r[2] > 0 and r[3] > 0


# The main button of the screens where a user must find it at once, as the app labels it (machine: Mac or
# PC), the first one there of its candidates. The night's Windows VM has 1024 x 768 with its taskbar, as an
# old laptop: the window has to fit.
PRINCIPAUX = {
    "bienvenue": ("Vérifier mon {machine}", "Choisir mon IA"),
    "bienvenue-retour": ("Guide-moi dans un logiciel", "Connecter mon compte Claude"),
    "diagnostic": ("Tout installer",),
    "connexion-installation": ("Connecter mon compte Claude",),
    "connexion": ("Connecter mon compte Claude",),
    "connexion-chatgpt": ("Connecter Codex à mon compte ChatGPT",),
    "fini": ("Guide-moi dans un logiciel", "Connecter mon compte Claude", "Connecter Codex à mon compte ChatGPT"),
}
# The diagnosis' lines, folded under this button: the robot unfolds them, to read what the VM had.
DETAIL = "Voir le détail"


def cache_du_bouton(ecran, libelle):
    """How much of the button the usable area of the screen leaves out ("" when a user sees it whole), or
    None when the driver gives no places (macOS, Linux) or the button is not there."""
    r, zone = ecran.places.get(norm(libelle)), ecran.zone
    if not rect_valide(r) or not rect_valide(zone):
        return None
    x, y, w, h = r
    zx, zy, zw, zh = zone
    cotes = [("en haut", zy - y), ("en bas", (y + h) - (zy + zh)), ("à gauche", zx - x), ("à droite", (x + w) - (zx + zw))]
    return ", ".join("%d px %s" % (n, ou) for ou, n in cotes if n > 0)


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


def conseil(ecran):
    """Bibo's advice on that screen (its sentence after « Mon conseil : »), or ""."""
    textes = ecran.textes
    for i, x in enumerate(textes):
        if x.startswith(CONSEIL):
            rest = x[len(CONSEIL):].lstrip(" :")
            return rest or (textes[i + 1] if i + 1 < len(textes) else "")
    return ""


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

    def _run(self, argv, timeout=120, env=None, creationflags=0, entree=None):
        """entree: bytes for its stdin (a PNG's base64), else it gets the robot's."""
        try:
            p = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout, env=env, creationflags=creationflags,
                               **({"input": entree} if entree is not None else {}))
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

    def codex_installe(self, home):
        """[places]: where the app's step puts Codex on this system."""
        return codex_installe(home, windows=False)

    def ecarter_la_nuit(self):
        """Keeps the nightly the install registered out of the checks' way; returns what it did, or None."""
        return None

    # A second click on the app while it is open (a window slow to show): the second one must go by itself.
    # Windows only (lib.rs, already_open): macOS brings an open app back by itself, Linux is not checked.
    ouvrir_encore = None
    # The driver gives each button's place and the screen's usable area (Windows): the main buttons must be
    # measured, not skipped without a word when the tree stops giving them.
    mesure_places = False

    def poste_neuf(self):
        """Makes the VM the machine users have, right before the app's first start (what its steps must
        bring, taken away); returns what it did, or None."""
        return None

    def constats(self, t, details):
        """Checks of this system once the app installed everything (Linux: what ran as root)."""

    def journaux(self):
        """On a failure, what the system logged that the window does not show: {name: [lines]}."""
        return {}

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

    # The app's window read and clicked: its installer's (None, the only one until the guide), or the
    # one with this title (GUIDE_TITRE).
    fenetre_titre = None

    def taper(self, texte):
        """Types text in that window, as a user does on the keyboard (ASCII)."""
        raise NotImplementedError

    def tabulation(self):
        """Tab, to the next field."""
        raise NotImplementedError

    def coller_image(self, png):
        """A PNG in the clipboard, as a capture tool puts one (from memory, no file), then Cmd+V or Ctrl+V
        in that window."""
        raise NotImplementedError

    # The guide's walk on this system: an account without Opus (Sonnet, the cautious mode) or not.
    guide_prudent = False

    def logiciel(self, dossier):
        """The user's program the guide is walked in, its window Bibo takes in photo itself (Logiciel), not
        open yet; None where the robot pastes the captures (Linux)."""
        return None

    # The programs whose windows in front are the user's, not a dialog over the app (the guide's program).
    ignores = ()

    def cerveau(self, home):
        """Where the app put the brain."""
        return Path(home) / "Documents" / "Brain"

    def racines_traces(self, home):
        """Where the guide could have left an image: the home, the temporary folders."""
        return [Path(home), Path(tempfile.gettempdir())]

    def app_devant(self):
        """(whether the app is in front, its window with the keyboard; the name of the one in front), where the
        driver can tell (macOS); (None, None) elsewhere."""
        return None, None

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
  var out = req.op === 'cliquer' ? cliquer(se, req) : req.op === 'rapide' ? rapide(se, req) :
            req.op === 'clavier' ? clavier(se, req) : req.op === 'cadre' ? cadre(se, req) :
            req.op === 'boutons' ? boutonsSelecteur(se) : req.op === 'souris' ? pointeur(req) :
            req.op === 'devant' ? devant(se) : lire(se, req);
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

// The app in front, the one the keyboard types in.
function devant(se) {
  var name = '';
  try { name = se.processes.whose({frontmost: true}).name()[0] || ''; } catch (e) {}
  return {ok: true, devant: name};
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
  var hit = Ref(), pid = Ref(), win = Ref(), wpid = Ref();
  if ($.AXUIElementCopyElementAtPosition($.AXUIElementCreateSystemWide(), x, y, hit) !== 0) return 'rien sous le point du bouton';
  var qui = $.AXUIElementGetPid(hit[0], pid) === 0 ? pid[0] : -1;
  // A page's element may answer for WebKit's web content process: its window says whose it is.
  if (qui !== p.unixId() && $.AXUIElementCopyAttributeValue(hit[0], $('AXWindow'), win) === 0 &&
      $.AXUIElementGetPid(ObjC.castRefToObject(win[0]), wpid) === 0) qui = wpid[0];
  if (qui !== p.unixId()) {
    var dessus = '?';
    try { dessus = se.processes.whose({unixId: qui}).name()[0] || '?'; } catch (e) {}
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
    if (req.fenetre && norm(prop(ws[i], 'name')) !== norm(req.fenetre)) continue;
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

// The guide's program (the user's): the place and size of its window, by a part of its title.
function cadre(se, req) {
  try {
    var ws = se.processes.byName(req.processus).windows();
    for (var i = 0; i < ws.length; i++) {
      if (norm(prop(ws[i], 'name')).indexOf(norm(req.titre)) >= 0) return {pos: ws[i].position(), size: ws[i].size()};
    }
  } catch (e) { return {erreur: String(e)}; }
  return {};
}

// macOS's picker: the buttons ControlCenter lays over the screen, its menu bar left out.
function boutonsSelecteur(se) {
  var out = [];
  function walk(el, depth) {
    var ks = [];
    try { ks = el.uiElements(); } catch (e) { return; }
    for (var i = 0; i < ks.length; i++) {
      if (prop(ks[i], 'role') === 'AXButton') out.push({n: str(prop(ks[i], 'name')), d: str(prop(ks[i], 'description')), pos: prop(ks[i], 'position'), size: prop(ks[i], 'size')});
      if (depth < 8) walk(ks[i], depth + 1);
    }
  }
  try {
    var ws = se.processes.byName('ControlCenter').windows();
    for (var i = 0; i < ws.length; i++) walk(ws[i], 0);
  } catch (e) {}
  return {boutons: out.filter(function (b) { return b.pos && b.size && b.pos[1] > 70; })};
}

// The pointer moved there, and clicked when asked, as the mouse does (kCGHIDEventTap).
function pointeur(req) {
  ObjC.import('CoreGraphics');
  var pt = $.CGPointMake(req.x, req.y);
  (req.clic ? [5, 1, 2] : [5]).forEach(function (type) {
    var ev = $.CGEventCreateMouseEvent($(), type, pt, 0);
    if (type !== 5) $.CGEventSetIntegerValueField(ev, 1, 1);
    $.CGEventPost(0, ev);
    delay(type === 5 ? 0.3 : 0.08);
  });
  return {ok: true};
}

// The keyboard, as a user types in the window: text, Tab, or a PNG put in the clipboard (from its
// base64, no file) then Cmd+V. The app is brought to the front and the window raised first.
function clavier(se, req) {
  var running = [];
  try { running = se.processes.name(); } catch (e) {}
  if (running.indexOf(req.processus) < 0) return {ok: false, pourquoi: 'processus'};
  var p = se.processes.byName(req.processus);
  try { p.frontmost = true; } catch (e) {}
  if (req.fenetre) {
    try { p.windows.byName(req.fenetre).actions.byName('AXRaise').perform(); } catch (e) { return {ok: false, pourquoi: 'fenetre'}; }
  }
  delay(0.4);
  if (req.png) {
    ObjC.import('AppKit');
    var pb = $.NSPasteboard.generalPasteboard;
    pb.clearContents;
    var data = $.NSData.alloc.initWithBase64EncodedStringOptions($(req.png), 0);
    if (!pb.setDataForType(data, $('public.png'))) return {ok: false, pourquoi: 'presse-papiers'};
    delay(0.2);
    se.keystroke('v', {using: 'command down'});
  } else if (req.touche === 'tab') {
    se.keyCode(48);
  } else {
    se.keystroke(req.texte);
  }
  delay(0.3);
  return {ok: true};
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

    def logiciel(self, dossier):
        return LogicielMac(self, dossier)

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
            if self.attendre(lambda: mnt.name in self.fenetres_finder(), 30):
                time.sleep(3)   # the Finder draws the icons after the window
            else:
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

    def _lancer(self, argv, env=None):
        """A command left running (open waits for the answer to Gatekeeper's dialog)."""
        try:
            return subprocess.Popen([str(a) for a in argv], env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ErreurPilote("%s ne se lance pas (%s)" % (Path(str(argv[0])).name, e.strerror or type(e).__name__))

    def refus(self, details, capturer=None):
        """The app opened once as a double click opens it, still quarantined: macOS must refuse it
        with its dialog, which is captured and closed (« Terminé »), and the app must not start."""
        lanceur = self._lancer(["/usr/bin/open", self.app], env=env_utilisateur(Path.home()))
        try:
            return self._refus(details, capturer)
        finally:
            try:
                lanceur.wait(30)
            except subprocess.TimeoutExpired:
                lanceur.kill()

    def _refus(self, details, capturer):
        refus = lambda: next((d for d in self.lire().dialogues if d["processus"] == self.GATEKEEPER), None)
        # The app's process may exist while macOS asks (a GitHub VM showed it): it must not get a window.
        dialogue = self.attendre(refus, 60)
        if not dialogue:
            if self.lire().fenetre:
                raise ErreurPilote("macOS a laissé s'ouvrir la préversion non signée (pas de refus de Gatekeeper)")
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
        if not self.attendre(lambda: not self.vivante(), 20):
            raise ErreurPilote("l'app tourne encore après le refus de Gatekeeper")
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
                         "ignorer": list(self.IGNORER) + list(self.ignores)})
        self.dernier_arbre = res
        fen = [f for f in res.get("app") or [] if isinstance(f, dict)]
        if self.fenetre_titre:
            fen = [f for f in fen if norm(f.get("titre")) == norm(self.fenetre_titre)]
        dialogues = [dialogue_depuis_arbre(d.get("processus"), d.get("arbre")) for d in res.get("dialogues") or []
                     if isinstance(d, dict)]
        e = ecran_depuis_arbre(fen, [d for d in dialogues if d["textes"] or d["boutons"]])
        # A quick read follows the place of the last full one among all the app's windows: not here.
        self.statut = None if self.fenetre_titre else e.statut
        return e

    def rapide(self):
        if not getattr(self, "statut", None) or self.fenetre_titre:
            return None
        try:
            res = self._osa({"op": "rapide", "processus": self.processus, "fenetre": self.statut[0],
                             "chemin": list(self.statut[1])}, timeout=30)
        except ErreurPilote:
            return None
        return dits_rapides(res.get("textes")) if res.get("ok") else None

    def cliquer(self, libelle, processus=None):
        res = self._osa({"op": "cliquer", "processus": processus or self.processus, "libelle": libelle,
                         "fenetre": None if processus else self.fenetre_titre})
        if not res.get("ok"):
            raise ErreurPilote("pas de clic sur « %s » (%s)" % (libelle, res.get("pourquoi")))
        return res.get("comment")

    def _clavier(self, **req):
        res = self._osa(dict(req, op="clavier", processus=self.processus, fenetre=self.fenetre_titre))
        if not res.get("ok"):
            raise ErreurPilote("le clavier n'atteint pas la fenêtre (%s)" % res.get("pourquoi"))

    def app_devant(self):
        nom = self._osa({"op": "devant"}, timeout=30).get("devant") or ""
        return nom == self.processus, nom

    def taper(self, texte):
        self._clavier(texte=texte)

    def tabulation(self):
        self._clavier(touche="tab")

    def coller_image(self, png):
        self._clavier(png=base64.b64encode(png).decode("ascii"))

    def racines_traces(self, home):
        out = Pilote.racines_traces(self, home)
        rc, said = self._run(["/usr/bin/getconf", "DARWIN_USER_CACHE_DIR"], timeout=30)
        return out + ([Path(said.strip())] if rc == 0 and said.strip() else [])

    def capturer(self, chemin):
        rc, out = self._run(["/usr/sbin/screencapture", "-x", "-t", "png", chemin], timeout=60)
        if rc != 0 or not Path(chemin).is_file() or Path(chemin).stat().st_size == 0:
            raise ErreurPilote("screencapture n'a rien écrit (code %d : %s)" % (rc, out.strip()[-200:]))

    def arbre(self):
        return self.dernier_arbre

    # In the system's log, who speaks of the capture and of the picker: the app, ControlCenter (the picker's
    # buttons), screencaptureui, replayd, ScreenCaptureKit.
    JOURNAL = ('process == "%s" OR process == "ControlCenter" OR process == "screencaptureui" OR process == "replayd" '
               'OR subsystem == "com.apple.ScreenCaptureKit"')

    def journaux(self):
        """The system's log of the last 5 minutes, the app's and the picker's: its last 400 lines."""
        rc, text = self._run(["/usr/bin/log", "show", "--last", "5m", "--style", "compact", "--info",
                              "--predicate", self.JOURNAL % self.processus], timeout=180)
        return {"log_show": text.splitlines()[-400:] if rc == 0 else ["code %d" % rc] + text.splitlines()[-20:]}


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
    if rect_valide(node.get("b")):
        out["b"] = node["b"]
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

# A PC as users have it has no Git: terrain.yml asks for the runner's to go (TERRAIN_SANS_GIT, where
# .github/ci/sans-git.ps1 writes its proof), once the harness no longer needs it, before the app's
# first start. The app's brain step (install.ps1) then has to bring it through winget.
SANS_GIT = "TERRAIN_SANS_GIT"
SANS_GIT_PS1 = Path(__file__).resolve().parent.parent.parent / ".github" / "ci" / "sans-git.ps1"
# The diagnosis' line for Git (app/src-tauri/src/diagnose.rs) and its word for what is missing (app.js).
LIGNE_GIT = "Git (l'historique de tes notes)"
A_INSTALLER = ": À installer"


def racines_git(environ=None):
    """Where Git for Windows installs, for all users or for one (install.ps1's Find-Git looks there)."""
    environ = os.environ if environ is None else environ
    out = []
    for var, sous in (("ProgramFiles", "Git"), ("ProgramFiles(x86)", "Git"), ("LOCALAPPDATA", "Programs\\Git")):
        if environ.get(var):
            out.append(ntpath.join(environ[var], sous))
    return out


def sans_git_path(path, racines):
    """A Windows PATH without the folders of a Git install (racines: racines_git())."""
    tops = [ntpath.normcase(r.rstrip("\\/")) for r in racines]

    def git(d):
        d = ntpath.normcase(d.strip().strip('"').rstrip("\\/"))
        return any(d == r or d.startswith(r + "\\") for r in tops)
    return ";".join(d for d in path.split(";") if d and not git(d))


def statut_diagnostic(textes, ligne):
    """What the app's diagnosis said of one line (« : OK », « : À installer »...), or None."""
    return next((textes[i + 1] for i, x in enumerate(textes[:-1]) if x == ligne), None)

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


def smartscreen(fenetre):
    """True for SmartScreen's window (« Windows protected your PC »): its program, its title, or, as
    a GitHub VM showed it (a frame of explorer's, without a title), a part of it named so."""
    if nom_programme(fenetre.get("exe")).lower() == "smartscreen" or "smartscreen" in str(fenetre.get("titre") or "").lower():
        return True
    return any("smartscreen" in (str(n.get("n") or "") + " " + str(n.get("a") or "")).lower()
               for _, n in _aplatir(fenetre.get("arbre") or {}))


# Windows that may hold SmartScreen's question: UWP frames, which show other programs' content.
CADRES_UWP = ("ApplicationFrameWindow",)


def question(fenetre, qui):
    """(window, who, buttons, links, texts) of a question Windows asks before opening a downloaded file."""
    flat = [n for _, n in _aplatir(arbre_uia(fenetre.get("arbre")))]
    noms = lambda role: [norm_win(n["n"]) for n in flat if n["r"] == role and norm_win(n["n"])]
    return fenetre.get("h"), qui, noms("AXButton"), noms("AXLink"), noms("AXStaticText")


class PiloteWindows(Pilote):
    """UI Automation (the accessibility tree Windows gives every window, WebView2's page included)
    and a capture of the whole screen, through pilote_windows.cs, compiled on the VM by the .NET
    Framework's own csc.exe. The installer: the NSIS setup.exe, clicked through page by page."""

    machine = "PC"
    suffixe = "_windows-x64-setup.exe"
    mesure_places = True
    neufs = ("Python (pour les automatismes)", LIGNE_GIT)
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
        for name in ("UIAutomationClient.dll", "UIAutomationTypes.dll", "WindowsBase.dll", "System.Drawing.dll",
                     "System.Windows.Forms.dll"):
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

    def _outil(self, args, timeout=120, entree=None):
        # No console window of its own: it would take the foreground from the window to click.
        rc, out = self._run([self.outil] + [str(a) for a in args], timeout=timeout, creationflags=SANS_CONSOLE, entree=entree)
        try:
            res = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ErreurPilote("réponse illisible du pilote Windows (code %d) : %s" % (rc, out.strip()[-300:]))
        if rc != 0 or not isinstance(res, dict):
            raise ErreurPilote("le pilote Windows a fini avec le code %d : %s" % (rc, out.strip()[-300:]))
        return res

    def environnement(self, home, extra=None):
        return env_app_windows(env_session(), self.stub, extra)

    def logiciel(self, dossier):
        return LogicielWindows(self, dossier)

    def outils(self, home):
        home = Path(home)
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        pf = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
        python = sorted(local.glob("Programs/Python/Python3*/python.exe"))
        return [("Claude Code", [home / ".local" / "bin" / "claude.exe"]), ("Bun", [home / ".bun" / "bin" / "bun.exe"]),
                ("Obsidian", [local / "Programs" / "Obsidian" / "Obsidian.exe", local / "Obsidian" / "Obsidian.exe",
                              pf / "Obsidian" / "Obsidian.exe"]),
                ("Python de winget", python or [local / "Programs" / "Python" / "Python3xx" / "python.exe"])]

    def codex_installe(self, home):
        return codex_installe(home, windows=True)

    def poste_neuf(self):
        """No Git, when terrain.yml asks for it (SANS_GIT): sans-git.ps1 uninstalls the runner's and
        proves nothing finds it any more; this process's PATH loses its folders first, so nothing the
        robot starts from here finds it either. The app's environment is read from the registry after."""
        preuve = os.environ.get(SANS_GIT)
        if not preuve:
            return None
        os.environ["PATH"] = sans_git_path(os.environ.get("PATH", ""), racines_git())
        rc, out = self._run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                             SANS_GIT_PS1, "-Preuve", preuve], timeout=15 * 60, creationflags=SANS_CONSOLE)
        lignes = [l.strip() for l in out.splitlines() if l.strip()]
        if rc != 0:
            raise ErreurPilote("Git n'a pas quitté la VM (sans-git.ps1, code %d) : %s" % (rc, " ".join(lignes[-4:])[-500:]))
        return {"sans_git": lignes[-6:]}

    def constats(self, t, details):
        """The app saw no Git before it installed one: its diagnosis said « À installer » for Git."""
        if not os.environ.get(SANS_GIT):
            return
        t("app-git-diagnostic")
        statut = statut_diagnostic((details.get("diagnostic") or {}).get("textes") or [], LIGNE_GIT)
        t.check(statut is not None, "le diagnostic de l'app ne dit rien de Git")
        t.check(statut == A_INSTALLER, "avant d'installer, le diagnostic de l'app disait Git « %s » : la VM avait "
                                       "encore Git" % statut.lstrip(": "))

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
                                   "sans réponse : « %s »" % " ".join(vues[-1]["textes"])[:200] if vues else
                                   "aucune question lue à l'écran ; fenêtres : %s" % ", ".join(
                                       "%s (%s)" % (f.get("titre"), nom_programme(f.get("exe")))
                                       for f in (self.dernier_arbre or {}).get("visibles") or [] if f.get("titre"))[:300]))
            # SmartScreen's window, in front or not; else the Attachment Manager's question, which the
            # shell shows from the process that opened the file (this one).
            q, res, visibles = None, {}, self._outil(["fenetres"]).get("fenetres") or []
            for f in visibles:
                if isinstance(f, dict) and (smartscreen(f) or f.get("classe") in CADRES_UWP):
                    res = self._outil(["arbre", f.get("h")])
                    if smartscreen(res):
                        q = question(res, "SmartScreen")
                        break
            if not q:
                res = self._outil(["lire", os.getpid()])
                fen = [f for f in res.get("fenetres") or [] if isinstance(f, dict) and f.get("classe") == "#32770"]
                q = question(fen[0], nom_programme(sys.executable)) if fen else None
            self.dernier_arbre = dict(res, visibles=visibles)
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
                # A new page: its « Suivant > » is a first click (the mouse), not a retry of the last page's.
                prev, clic, message = sig, None, None
                self.nouvel_ecran()
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

    def ouvrir_encore(self, env, delai=30):
        """The app opened a second time while its window is there: seconds the second process took to go
        by itself, or None when it stayed (then killed, with everything it started)."""
        debut = self.horloge()
        try:
            proc = subprocess.Popen([self.exe], env=env, cwd=os.path.dirname(self.exe), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        except OSError as e:
            raise ErreurPilote("l'app ne se lance pas une deuxième fois (%s)" % (e.strerror or type(e).__name__))
        if self.attendre(lambda: proc.poll() is not None, delai, self.horloge, self.dormir):
            return round(self.horloge() - debut, 1)
        self._run(["taskkill", "/PID", proc.pid, "/T", "/F"], timeout=60)
        return None

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
            elif not self.fenetre_titre or norm(f.get("titre")) == norm(self.fenetre_titre):
                fen.append({"titre": f.get("titre"), "arbre": arbre, "h": f.get("h")})
        d = res.get("devant")
        if isinstance(d, dict) and d.get("classe") not in self.IGNORER and nom_programme(d.get("exe")) not in self.ignores:
            name = nom_programme(d.get("exe"))
            self.dialogues_h[name] = d.get("h")
            dialogues.append(dialogue_depuis_arbre(name, arbre_uia(d.get("arbre"))))
        if fen:
            page = [f for f in fen if any(n.get("r") == "AXWebArea" for _, n in _aplatir(f["arbre"]))]
            self.hwnd = (page or fen)[0]["h"]
            self._devant(self.hwnd)
        e = ecran_depuis_arbre(fen, [d for d in dialogues if d["textes"] or d["boutons"]])
        e.zone = res.get("zone") if rect_valide(res.get("zone")) else None
        return e

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

    def _clavier(self, args, entree=None):
        if not self.hwnd:
            self.lire()
        if not self.hwnd:
            raise ErreurPilote("pas de fenêtre où taper")
        res = self._outil([args[0], self.hwnd] + list(args[1:]), entree=entree)
        if not res.get("ok"):
            raise ErreurPilote("le clavier n'atteint pas la fenêtre (%s)" % res.get("pourquoi"))

    def cerveau(self, home):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                d = winreg.QueryValueEx(k, "BRAIN_DIR")[0]
            if d:
                return Path(d)
        except OSError:
            pass
        return Pilote.cerveau(self, home)

    def taper(self, texte):
        self._clavier(["taper", texte])

    def tabulation(self):
        self._clavier(["touche", "tab"])

    def coller_image(self, png):
        self._clavier(["coller"], entree=base64.b64encode(png))

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


# --------------------------------------------------------------------------- Linux

# The helper PiloteLinux runs with the system's Python and its AT-SPI bindings (python3-gi,
# gir1.2-atspi-2.0): the app's accessibility tree, a button's place, a button pressed through
# AT-SPI. One JSON request in argv, one JSON answer on stdout, ASCII only.
ATSPI = r'''
import json, sys, unicodedata
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

OBJ = "\ufffc"     # a child element inside a text: the text is not a leaf


def norm(s):
    s = unicodedata.normalize("NFC", s or "")
    for a, b in (("\u00a0", " "), ("\u202f", " "), ("\u2019", "'"), ("\u2018", "'")):
        s = s.replace(a, b)
    return " ".join(s.split())


def text(o):
    try:
        if o.get_text_iface() is None:
            return ""
        return Atspi.Text.get_text(o, 0, Atspi.Text.get_character_count(o)) or ""
    except Exception:
        return ""


def rect(o):
    try:
        if o.get_component_iface() is None:
            return None
        r = Atspi.Component.get_extents(o, Atspi.CoordType.SCREEN)
        return [r.x, r.y, r.width, r.height]
    except Exception:
        return None


def node(o, depth, budget):
    budget[0] -= 1
    role = o.get_role_name() or ""
    n = {"r": role, "n": o.get_name() or "", "k": []}
    try:
        attrs = o.get_attributes() or {}
    except Exception:
        attrs = {}
    if attrs.get("live"):
        n["l"] = attrs["live"]
    if role in ("push button", "toggle button", "frame", "document web", "document text", "dialog", "alert"):
        n["x"] = rect(o)
    if role in ("push button", "toggle button"):
        n["e"] = o.get_state_set().contains(Atspi.StateType.ENABLED)
        return n
    t = text(o)
    if t.strip() and OBJ not in t:
        n["v"] = t
        return n
    if depth < 40:
        try:
            count = o.get_child_count()
        except Exception:
            return n    # gone while read: the page changed under the reading (the guide replaces its screen)
        for i in range(count):
            if budget[0] <= 0:
                break
            try:
                kid = o.get_child_at_index(i)
                n["k"].append(node(kid, depth + 1, budget) if kid is not None else {"r": "", "n": "", "k": []})
            except Exception:
                n["k"].append({"r": "", "n": "", "k": []})
    return n


def apps(name):
    desk = Atspi.get_desktop(0)
    out = []
    for i in range(desk.get_child_count()):
        a = desk.get_child_at_index(i)
        if a is not None and (name is None or (a.get_name() or "") == name):
            out.append(a)
    return out


def windows(app):
    return [app.get_child_at_index(i) for i in range(app.get_child_count())]


def lire(req):
    res = {"app": [], "dialogues": []}
    mine = apps(req["app"])
    for a in mine[-1:]:
        for w in windows(a):
            res["app"].append({"titre": w.get_name() or "", "arbre": node(w, 0, [1500])})
    for a in apps(None):
        if a in mine:
            continue
        for w in windows(a):
            if w is not None and w.get_role_name() in ("dialog", "alert"):
                res["dialogues"].append({"processus": a.get_name() or "", "arbre": node(w, 0, [300])})
    return res


def rapide(req):
    try:
        el = windows(apps(req["app"])[-1])[req["fenetre"]]
        for i in req["chemin"]:
            el = el.get_child_at_index(i)
        if el.get_role_name() != "status bar":
            return {"ok": False}
        return {"ok": True, "textes": [text(el.get_child_at_index(i)) for i in range(el.get_child_count())]}
    except Exception:
        return {"ok": False}


def chercher(o, label, depth=0):
    if o is None or depth > 40:
        return None
    if o.get_role_name() in ("push button", "toggle button"):
        return o if norm(o.get_name()) == label else None
    for i in range(o.get_child_count()):
        found = chercher(o.get_child_at_index(i), label, depth + 1)
        if found is not None:
            return found
    return None


def document(o, depth=0):
    if o is None or depth > 40:
        return None
    if o.get_role_name() in ("document web", "document text"):
        return o
    for i in range(o.get_child_count()):
        found = document(o.get_child_at_index(i), depth + 1)
        if found is not None:
            return found
    return None


def bouton(req):
    label = norm(req["libelle"])
    for a in apps(req["app"])[-1:]:
        for w in windows(a):
            if req.get("fenetre") and norm(w.get_name()) != norm(req["fenetre"]):
                continue
            b = chercher(w, label)
            if b is None:
                continue
            if not b.get_state_set().contains(Atspi.StateType.ENABLED):
                return {"ok": False, "pourquoi": "inactif"}
            if req.get("presser"):
                try:
                    return {"ok": bool(Atspi.Action.do_action(b, 0))}
                except Exception as e:
                    return {"ok": False, "pourquoi": "action AT-SPI : %s" % e}
            try:
                Atspi.Component.scroll_to(b, Atspi.ScrollType.ANYWHERE)
            except Exception:
                pass
            doc = document(w)
            return {"ok": True, "bouton": rect(b), "page": rect(doc) if doc is not None else None}
    return {"ok": False, "pourquoi": "absent"}


req = json.loads(sys.argv[1])
out = {"lire": lire, "rapide": rapide, "bouton": bouton}[req["op"]](req)
sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")
'''

# AT-SPI's roles, as the roles of the tree ecran_depuis_arbre() reads (macOS's words). The AppImage
# built on 22.04 reaches Ubuntu 24.04's AT-SPI with some roles one below their own: its page reads
# « document text » (« document web »), a paragraph « footer », a landmark « article ».
ATSPI_ROLES = {"frame": "AXWindow", "dialog": "AXWindow", "alert": "AXWindow", "document web": "AXWebArea",
               "document text": "AXWebArea", "push button": "AXButton", "toggle button": "AXCheckBox"}
DECALES = {"document text": "document web", "footer": "paragraph", "article": "landmark"}


def roles_decales(node):
    """{role read: role meant} of DECALES found in a helper tree, when its page reads « document text »."""
    vus = {str(n.get("r") or "") for _, n in _aplatir(node if isinstance(node, dict) else {})}
    return {r: DECALES[r] for r in sorted(vus) if r in DECALES} if "document text" in vus else {}
# The diagnosis' lines: WebKitGTK gives a check's label, its hidden status and its detail as one
# text, a line each; macOS and Windows give the status apart, as the robot reads it.
STATUT_CHECK = re.compile(r"^(.*\S)\s+(: (?:OK|À installer|À noter|Bloquant))$")


def lignes(text):
    """The lines of a text, a check's status apart."""
    out = []
    for line in str(text or "").split("\n"):
        line = norm(line)
        if not line:
            continue
        m = STATUT_CHECK.match(line)
        out.extend([m.group(1), m.group(2)] if m else [line])
    return out


def arbre_atspi(node):
    """PiloteLinux's helper tree (r AT-SPI role, n name, v text, e enabled, x screen rect, l aria-live,
    k children) in the shape of PiloteMac's (r, s, n, v, e, k). A node keeps its place among its
    siblings (a quick read follows the path of the last full one); a text becomes a group of its lines.
    Bibo's region is the status bar, or else the first live region of the page."""
    out = _atspi(node)
    page = next((n for _, n in _aplatir(out) if n["r"] == "AXWebArea"), out)
    if not any(n.get("s") for _, n in _aplatir(page)):
        live = next((n for _, n in _aplatir(page) if n.get("l") and n["r"] not in ("AXStaticText", "AXButton")), None)
        if live is not None:
            live["s"] = "AXApplicationStatus"
    return out


def _atspi(node):
    node = node if isinstance(node, dict) else {}
    role = str(node.get("r") or "")
    out = {"r": ATSPI_ROLES.get(role, "AXGroup"), "s": "AXApplicationStatus" if role == "status bar" else "",
           "n": str(node.get("n") or ""), "v": "", "d": "", "e": node.get("e", True) is not False, "k": []}
    if node.get("l"):
        out["l"] = node["l"]
    if isinstance(node.get("v"), str):
        out["k"] = [{"r": "AXStaticText", "s": "", "n": "", "v": l, "d": "", "e": True, "k": []} for l in lignes(node["v"])]
    elif out["r"] not in ("AXButton", "AXCheckBox"):
        out["k"] = [_atspi(k) for k in node.get("k") or []]
    return out


# Ubuntu's PATH for a desktop session (/etc/environment), what an app opened from the desktop gets.
PATH_UBUNTU = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin"


def env_linux(home, display, uid, extra=None):
    """What an app started from an Ubuntu desktop gets: the session's display and buses (the user's
    systemd and the accessibility bus answer there), the system's PATH; no token, no CI variable."""
    user = os.environ.get("USER") or Path(home).name
    run = "/run/user/%d" % uid
    env = {"HOME": str(home), "USER": user, "LOGNAME": user, "SHELL": "/bin/bash", "PATH": PATH_UBUNTU,
           "LANG": "C.UTF-8", "DISPLAY": display, "XDG_RUNTIME_DIR": run,
           "DBUS_SESSION_BUS_ADDRESS": "unix:path=%s/bus" % run, "XDG_SESSION_TYPE": "x11"}
    env.update(extra or {})
    return env


# What every Ubuntu 24.04 desktop has (ubuntu-24.04.4-desktop-amd64.manifest) and the AppImage leaves to
# the system (linuxdeploy's exclusions, seen with ldd in its files): EGL, GL and GBM with Mesa's drivers,
# X11's libraries, fonts and text shaping. The runner's image is a server's, without libEGL: those it
# lacks are installed before the app starts, said in the results. One the app needs and no desktop has
# is not here: the app then does not start, as it would not on a desktop.
BUREAU = ("libegl1", "libgl1", "libgles2", "libgbm1", "libegl-mesa0", "libglx-mesa0", "libgl1-mesa-dri", "libdrm2",
          "libx11-6", "libx11-xcb1", "libxcb1", "libexpat1", "libfontconfig1", "libfreetype6", "libfribidi0",
          "libharfbuzz0b", "fonts-dejavu-core")
# The packages a fresh Ubuntu desktop does not have and the app's « Outils de base » step installs.
SANS_PAQUETS = ("git", "curl")
# Removed with them: git's manual, which a desktop without git has not either. The runner's image has
# git and git-man from a PPA whose source it then drops: Ubuntu's git needs Ubuntu's git-man, apt
# would not replace the PPA's by itself (« held broken packages », seen on the VM).
AUSSI_RETIRES = ("git-man",)
TITRE_FENETRE = "Brain in a Box"
# The app's own name for AT-SPI (its program's name).
NOM_ATSPI = "brain-in-a-box"


def fusermount_setuid(path=PATH_UBUNTU):
    """The fusermount* of the PATH that are setuid root, in its order: the AppImage's runtime
    (type2-runtime) mounts it with one of them. A desktop's is fuse3's fusermount3."""
    out = []
    for d in path.split(os.pathsep):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if n.startswith("fusermount") and st.st_uid == 0 and st.st_mode & stat.S_ISUID:
                out.append(p)
    return out


def place_xwininfo(out):
    """[x, y, width, height] of a window from xwininfo's answer, or None."""
    want = ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height")
    found = dict((k.strip(), v.strip()) for k, _, v in (l.partition(":") for l in str(out).splitlines()))
    try:
        return [int(found[k]) for k in want]
    except (KeyError, ValueError):
        return None


def commandes_root(journal):
    """The commands pkexec ran as root, from its journal (journalctl -o json: one entry a line, its
    MESSAGE « … [COMMAND=…] », the command's lines included, as text or as bytes)."""
    out = []
    for line in str(journal or "").splitlines():
        try:
            message = json.loads(line).get("MESSAGE")
        except (ValueError, AttributeError):
            continue
        if isinstance(message, list):
            message = bytes(b & 0xff for b in message if isinstance(b, int)).decode("utf-8", "replace")
        m = re.search(r"\[COMMAND=(.*)\]\s*$", message, re.S) if isinstance(message, str) else None
        if m:
            out.append(m.group(1))
    return out


# What root may run, as the app is built: its two scripts, their labels ("$0"), the packages of their list.
ROOT_RS = Path(__file__).resolve().parents[2] / "app" / "src-tauri" / "src" / "root.rs"
# The polkit rule that stands in for the password (terrain.yml) logs the window's text under this tag.
FENETRE = "fenetre-mot-de-passe"


def chaine_rust(texte, debut):
    """(value, end) of the Rust string literal of `texte` whose opening quote is at `debut`."""
    out, i = [], debut + 1
    while texte[i] != '"':
        c = texte[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        e = texte[i + 1]
        if e == "\n":  # a backslash at the end of a line: the line break and the next line's indent go
            i += 2
            while texte[i] in " \t\r\n":
                i += 1
        elif e == "u":
            fin = texte.index("}", i)
            out.append(chr(int(texte[i + 3:fin], 16)))
            i = fin + 1
        elif e == "x":
            out.append(chr(int(texte[i + 2:i + 4], 16)))
            i += 4
        else:
            out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'"}[e])
            i += 2
    return "".join(out), i + 1


def scripts_root(texte):
    """root.rs's string constants by name, and PACKAGES, read from its source."""
    out = {m.group(1): chaine_rust(texte, m.end() - 1)[0]
           for m in re.finditer(r'^(?:pub )?const ([A-Z0-9_]+): &str = "', texte, re.M)}
    m = re.search(r'^pub const PACKAGES: &\[&str\] = &\[([^\]]*)\];', texte, re.M)
    out["PACKAGES"] = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    return out


def forme_root(cmd, s, home):
    """("paquets", [packages]) or ("obsidian", its path) when the command pkexec ran is one of
    root.rs's, to the letter: its script, its label, then arguments it accepts (packages of its
    list; Obsidian's package downloaded by the app in ~/.brain-in-a-box). None for anything else."""
    paquets = "/bin/sh -c %s %s " % (s["APT_INSTALL"], s["PACKAGES_LABEL"])
    if cmd.startswith(paquets):
        noms = cmd[len(paquets):].split(" ")
        return ("paquets", noms) if all(n in s["PACKAGES"] for n in noms) else None
    deb = "/bin/sh -c %s %s " % (s["APT_INSTALL_DEB"], s["OBSIDIAN_LABEL"])
    chemin = cmd[len(deb):]
    dossier = re.escape(posixpath.join(str(home), ".brain-in-a-box"))
    if cmd.startswith(deb) and re.fullmatch(dossier + r"/\.biab-[0-9a-f]{16}/obsidian\.deb", chemin):
        return ("obsidian", chemin)
    return None


def ligne_mot_de_passe(cmd):
    """The command as pkexec 124 shows it in the system's password window ($(cmdline_short)):
    past 80 bytes, its first 38, « ... » and its last 37."""
    b = cmd.encode("utf-8")
    return cmd if len(b) <= 80 else (b[:38] + b" ... " + b[-37:]).decode("utf-8", "replace")


class PiloteLinux(Pilote):
    """AT-SPI (the accessibility tree WebKitGTK gives the app's page, through the system's Python),
    xdotool for the mouse and the keyboard, ImageMagick's import for a capture of the X screen. The
    desktop is the job's: Xvfb, a window manager, the user's session bus (terrain.yml)."""

    machine = "PC"
    suffixe = "_linux-x86_64.AppImage"
    neufs = ("Outils de base (git, python3, curl)",)

    def __init__(self, travail, display=None, horloge=time.monotonic, dormir=time.sleep):
        self.travail = Path(travail)
        self.helper = self.travail / "pilote-atspi.py"
        self.helper.write_text(ATSPI, encoding="ascii")
        self.display = display or os.environ.get("DISPLAY") or ":99"
        self.horloge, self.dormir = horloge, dormir
        self.exe = None
        self.proc = None
        self.statut = None
        self.dernier_arbre = None
        self.roles_decales = {}
        self.debut = time.time()

    def _env_outils(self):
        """The robot's tools reach the same display and the same buses as the app."""
        return env_linux(Path.home(), self.display, os.getuid())

    def _helper(self, req, timeout=60):
        rc, out = self._run(["/usr/bin/python3", "-I", self.helper, json.dumps(req)], timeout=timeout, env=self._env_outils())
        try:
            res = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ErreurPilote("réponse illisible d'AT-SPI (code %d) : %s" % (rc, out.strip()[-300:]))
        if rc != 0 or not isinstance(res, dict):
            raise ErreurPilote("AT-SPI ne répond pas (code %d) : %s" % (rc, out.strip()[-300:]))
        return res

    def _x(self, *args, timeout=30):
        return self._run(["xdotool"] + [str(a) for a in args], timeout=timeout, env=self._env_outils())

    def marquer_telecharge(self, fichier):
        # What Chrome and Firefox leave on a download on Linux: where it came from.
        url = "https://github.com/%s/releases" % REPO_SLUG
        try:
            os.setxattr(str(fichier), "user.xdg.origin.url", url.encode("ascii"))
        except OSError as e:
            return "pas d'attribut d'origine (%s)" % (e.strerror or type(e).__name__)
        return "user.xdg.origin.url=%s" % url

    def installer(self, fichier, details, capturer=None):
        """The AppImage put in ~/Applications and allowed to run as a program (Files' « Exécutable
        comme un programme »). Its runtime mounts it with the system's fusermount3 (fuse3, on every
        Ubuntu desktop since 22.04), and it loads the desktop's graphics and X11 libraries (BUREAU):
        a VM without them gets them, and the results say so."""
        notes = []
        apps = Path.home() / "Applications"
        apps.mkdir(exist_ok=True)
        dest = apps / Path(fichier).name
        if dest.exists():
            raise ErreurPilote("%s existe déjà : la VM n'est pas neuve" % dest)
        shutil.move(str(fichier), str(dest))
        with open(dest, "rb") as f:
            head = f.read(11)
        if head[:4] != b"\x7fELF" or head[8:11] != b"AI\x02":
            raise ErreurPilote("%s n'est pas une AppImage (type 2)" % dest.name)
        os.chmod(dest, 0o755)
        fusermount = fusermount_setuid()
        if not any(p.endswith("/fusermount3") for p in fusermount):
            rc, out = self._run(["sudo", "-n", "apt-get", "install", "-y", "-q", "fuse3"], timeout=600,
                                env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
            fusermount = fusermount_setuid()
            if rc != 0 or not any(p.endswith("/fusermount3") for p in fusermount):
                raise ErreurPilote("pas de fusermount3 setuid root, même après apt-get install fuse3 : %s" % out.strip()[-300:])
            notes.append("pas de fusermount3 sur la VM, qu'un bureau Ubuntu a (fuse3) : installé par le robot")
            details["fuse"] = "fuse3 installé par le robot (%s)" % ", ".join(fusermount)
        else:
            details["fuse"] = "déjà là (%s)" % ", ".join(fusermount)
        manque = [p for p in BUREAU if self._run(["dpkg-query", "-W", "-f=${Status}", p], timeout=60)[1].strip()
                  != "install ok installed"]
        details["bureau"] = manque or "déjà là"
        if manque:
            env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
            self._run(["sudo", "-n", "apt-get", "update", "-q"], timeout=900, env=env)
            rc, out = self._run(["sudo", "-n", "apt-get", "install", "-y", "-q", "--no-install-recommends"] + manque,
                                timeout=900, env=env)
            if rc != 0:
                raise ErreurPilote("les bibliothèques d'un bureau Ubuntu ne s'installent pas : %s" % out.strip()[-300:])
            notes.append("VM de serveur : bibliothèques qu'un bureau Ubuntu 24.04 a et que l'AppImage laisse au système, "
                         "installées par le robot : %s" % ", ".join(manque))
        self.exe = dest
        details["installee_dans"] = str(dest).replace(str(Path.home()), "~")
        return notes

    def poste_neuf(self):
        """Ubuntu's desktop has neither git nor curl: taken away, as Ubuntu comes (apt, not hidden: the
        app's step puts them back with apt). The runner's image is not a desktop kept up to date: its
        package lists are newer than what it has, and apt, asked by the app for the newest git or curl,
        may refuse (« held broken packages ») where a desktop would not. So the lists are refreshed as
        the app will refresh them, and once removed, the app's own command is simulated as root: one apt
        would refuse goes back at the very version the VM had, said in the results with apt's reasons."""
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        sudo_apt = ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get"]
        out = {"gardes": {}}
        self._run(sudo_apt + ["update", "-q"], timeout=900, env=env)
        # At their newest first: the versions they go back to, if they must, are in the lists.
        rc, said = self._run(sudo_apt + ["install", "-y", "-q", "--only-upgrade"] + list(SANS_PAQUETS), timeout=900, env=env)
        out["mis_a_jour"] = rc == 0
        versions = {}
        for p in SANS_PAQUETS:
            rc, said = self._run(["dpkg-query", "-W", "-f=${Version}", p], timeout=60)
            if rc == 0 and said.strip():
                versions[p] = said.strip()
        rc, said = self._run(sudo_apt + ["remove", "-y", "-q"] + list(SANS_PAQUETS + AUSSI_RETIRES), timeout=600, env=env)
        if rc != 0:
            raise ErreurPilote("apt-get remove %s : %s" % (" ".join(SANS_PAQUETS + AUSSI_RETIRES), said.strip()[-300:]))
        out["aussi"] = sorted(set(re.findall(r"^Removing (\S+) ", said, re.M)) - set(SANS_PAQUETS))
        retires = list(SANS_PAQUETS)
        rc, said = self._run(sudo_apt + ["-s", "-q", "install"] + retires, timeout=300, env=env)
        if rc != 0:
            for p in SANS_PAQUETS:
                rc, said = self._run(sudo_apt + ["-s", "-q", "install", p], timeout=300, env=env)
                if rc == 0:
                    continue
                out["gardes"][p] = [l.strip() for l in said.splitlines() if l.strip()][-6:]
                rc, said = self._run(sudo_apt + ["install", "-y", "-q", "%s=%s" % (p, versions.get(p, ""))], timeout=600,
                                     env=env) if p in versions else (1, "pas de version d'avant")
                if rc == 0:
                    retires.remove(p)
                else:
                    # Gone for good: the app's step will meet apt's refusal, and say it.
                    del out["gardes"][p]
                    out.setdefault("sans_retour", {})[p] = [l.strip() for l in said.splitlines() if l.strip()][-3:]
        left = [p for p in retires if shutil.which(p, path=PATH_UBUNTU)]
        if left:
            raise ErreurPilote("toujours là après apt-get remove : %s" % ", ".join(left))
        out["retires"] = retires
        if out["gardes"]:
            out["note"] = "gardé sur la VM, apt n'y installerait pas sa version courante : %s ; son installation par l'app " \
                          "n'est pas testée" % ", ".join(out["gardes"])
        return out

    def environnement(self, home, extra=None):
        return env_linux(home, self.display, os.getuid(), extra)

    def outils(self, home):
        home = Path(home)
        return [("Claude Code", [home / ".local" / "bin" / "claude"]), ("Bun", [home / ".bun" / "bin" / "bun"]),
                ("Obsidian", [Path("/opt/Obsidian/obsidian")])] + [
                    ("%s (paquet du système)" % p, [Path("/usr/bin") / p]) for p in SANS_PAQUETS]

    def constats(self, t, details):
        """What ran as root: through pkexec only (the system's password window), root.rs's two
        scripts to the letter, with arguments they accept, and nothing else; the packages the VM
        was made to lack among them, and Obsidian's package once; each named in the window."""
        if getattr(self, "roles_decales", None):
            details["roles_atspi_decales"] = self.roles_decales
            t.notes.append("rôles d'accessibilité décalés d'un cran (%s) : un lecteur d'écran les annonce ainsi"
                           % ", ".join("« %s » pour « %s »" % kv for kv in sorted(self.roles_decales.items())))
        t("app-root")
        since = "@%d" % int(self.debut)
        rc, out = self._run(["sudo", "-n", "journalctl", "-t", "pkexec", "-o", "json", "--no-pager", "--since", since], timeout=60)
        t.check(rc == 0, "journalctl ne dit pas ce que pkexec a lancé : %s" % out.strip()[-200:])
        cmds = commandes_root(out)
        details["root"] = [c[:160] for c in cmds]
        retires = (details.get("poste_neuf") or {}).get("retires") or []
        t.check(retires, "rien n'a été retiré de la VM : l'étape « Outils de base » (pkexec, apt) n'a pas été testée")
        try:
            with open(ROOT_RS, encoding="utf-8") as f:
                scripts = scripts_root(f.read())
        except OSError as e:
            scripts = {}
            details["root_rs"] = str(e)
        t.check(all(k in scripts for k in ("APT_INSTALL", "APT_INSTALL_DEB", "PACKAGES_LABEL", "OBSIDIAN_LABEL")) and scripts["PACKAGES"],
                "les scripts de root.rs sont illisibles : rien à quoi comparer ce que root a lancé")
        rc, said = self._run(["sudo", "-n", "journalctl", "-t", FENETRE, "-o", "cat", "--no-pager", "--since", since], timeout=60)
        fenetres = set(said.splitlines()) if rc == 0 else set()
        details["fenetre_mot_de_passe"] = sorted(fenetres)
        paquets, obsidian = set(), []
        for c in cmds:
            forme = forme_root(c, scripts, Path.home())
            t.check(forme, "pkexec a lancé autre chose que les scripts de root.rs : %s" % c[:200])
            ligne = ligne_mot_de_passe(c)
            t.check(ligne in fenetres, "la fenêtre du mot de passe n'a pas montré « %s » (journal : %s)" % (ligne, sorted(fenetres)[:4]))
            if forme[0] == "paquets":
                paquets.update(forme[1])
                t.check(ligne.startswith("/bin/sh -c # Brain in a Box installe") and ligne.endswith(forme[1][-1]),
                        "la fenêtre du mot de passe ne nomme pas l'app et les paquets : %s" % ligne)
            else:
                obsidian.append(forme[1])
                t.check("Obsidian" in ligne and ligne.endswith("obsidian.deb"),
                        "la fenêtre du mot de passe ne nomme pas Obsidian : %s" % ligne)
        manque = [p for p in retires if p not in paquets]
        t.check(not manque, "pkexec n'a pas installé %s, que la VM n'avait pas" % ", ".join(manque))
        t.check(len(obsidian) == 1, "le paquet d'Obsidian installé par pkexec %d fois (une attendue)" % len(obsidian))

    def journaux(self):
        """apt's own logs, pkexec's and polkit's journal since the job began, what the kernel denied
        (AppArmor), the confinement of the app's processes, the app's own output."""
        since = "@%d" % int(self.debut)
        out = {}
        for name, argv in (("apt_term_log", ["sudo", "-n", "tail", "-n", "60", "/var/log/apt/term.log"]),
                           ("apt_history_log", ["sudo", "-n", "tail", "-n", "30", "/var/log/apt/history.log"]),
                           ("pkexec", ["sudo", "-n", "journalctl", "-t", "pkexec", "-o", "cat", "--no-pager", "--since", since]),
                           ("polkit", ["sudo", "-n", "journalctl", "-u", "polkit", "-o", "cat", "--no-pager", "--since", since]),
                           ("noyau", ["sudo", "-n", "journalctl", "-k", "-g", "apparmor|audit", "-o", "cat", "--no-pager",
                                      "--since", since]),
                           ("apt_simule", ["sudo", "-n", "apt-get", "-s", "-q", "install"] + list(SANS_PAQUETS))):
            try:
                rc, text = self._run(argv, timeout=60)
            except ErreurPilote as e:
                rc, text = -1, str(e)
            out[name] = text.splitlines()[-60:]
        rc, pids = self._run(["pgrep", "-f", "brain-in-a-box|Brain-in-a-Box"], timeout=30)
        labels = []
        for pid in pids.split():
            try:
                with open("/proc/%s/attr/current" % pid, encoding="utf-8", errors="replace") as f:
                    label = f.read().strip()
                with open("/proc/%s/comm" % pid, encoding="utf-8", errors="replace") as f:
                    labels.append("%s %s : %s" % (pid, f.read().strip(), label))
            except OSError:
                continue
        out["confinement"] = labels
        try:
            with open(self.travail / "app.log", encoding="utf-8", errors="replace") as f:
                out["app_log"] = f.read().splitlines()[-60:]
        except OSError:
            out["app_log"] = []
        return out

    def ouvrir(self, env):
        """As a double click in Files: the AppImage itself, the session's environment."""
        if not self.exe:
            raise ErreurPilote("l'app n'est pas installée")
        log = open(self.travail / "app.log", "ab")
        try:
            self.proc = subprocess.Popen([str(self.exe)], env=env, cwd=env.get("HOME"), stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=log, start_new_session=True)
        except OSError as e:
            raise ErreurPilote("l'app ne se lance pas (%s)" % (e.strerror or type(e).__name__))
        finally:
            log.close()

    def vivante(self):
        return self.proc is not None and self.proc.poll() is None

    def fenetre(self):
        """(X window, its place on the screen and its size) of the app, or None. xwininfo's absolute
        place: xdotool's counts the window manager's frame twice."""
        rc, out = self._x("search", "--onlyvisible", "--name", "^%s$" % re.escape(self.fenetre_titre or TITRE_FENETRE))
        wid = next((w for w in out.split() if w.isdigit()), None) if rc == 0 else None
        if not wid:
            return None
        rc, out = self._run(["xwininfo", "-id", wid], timeout=30, env=self._env_outils())
        return (wid, place_xwininfo(out)) if rc == 0 and place_xwininfo(out) else None

    def fermer(self):
        """Alt+F4 in its window, as a user quits (the window manager closes it); killed only if it stays."""
        if self.vivante():
            f = self.fenetre()
            if f:
                self._x("windowactivate", "--sync", f[0], "key", "--clearmodifiers", "alt+F4")
                if self.attendre(lambda: not self.vivante(), 20, self.horloge, self.dormir):
                    return "Alt+F4"
            try:
                os.killpg(self.proc.pid, 15)
            except OSError:
                pass
            if self.attendre(lambda: not self.vivante(), 20, self.horloge, self.dormir):
                return "SIGTERM (Alt+F4 sans effet)"
            raise ErreurPilote("l'app ne se ferme pas")
        return "déjà fermée"

    def lire(self):
        if not self.vivante():
            return Ecran()
        try:
            res = self._helper({"op": "lire", "app": NOM_ATSPI})
        except ErreurPilote:
            # An element gone between two calls of AT-SPI: the page changed under the reading, read again.
            self.dormir(0.5)
            res = self._helper({"op": "lire", "app": NOM_ATSPI})
        self.dernier_arbre = res
        for f in res.get("app") or []:
            if isinstance(f, dict):
                self.roles_decales.update(roles_decales(f.get("arbre")))
        fen = [{"titre": f.get("titre"), "arbre": arbre_atspi(f.get("arbre"))} for f in res.get("app") or [] if isinstance(f, dict)]
        if self.fenetre_titre:
            fen = [f for f in fen if norm(f["titre"]) == norm(self.fenetre_titre)]
        dialogues = [dialogue_depuis_arbre(d.get("processus"), arbre_atspi(d.get("arbre"))) for d in res.get("dialogues") or []
                     if isinstance(d, dict)]
        e = ecran_depuis_arbre(fen, [d for d in dialogues if d["textes"] or d["boutons"]])
        self.statut = None if self.fenetre_titre else e.statut
        return e

    def rapide(self):
        if not self.statut or self.fenetre_titre:
            return None
        try:
            res = self._helper({"op": "rapide", "app": NOM_ATSPI, "fenetre": self.statut[0], "chemin": list(self.statut[1])},
                               timeout=30)
        except ErreurPilote:
            return None
        return dits_rapides(res.get("textes")) if res.get("ok") else None

    def sous_la_souris(self, wid):
        """True when the window under the pointer holds the app's (the window manager's frame does)."""
        rc, out = self._x("getmouselocation", "--shell")
        geo = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        dessous = geo.get("WINDOW", "")
        if dessous == wid:
            return True
        if not dessous.isdigit():
            return False
        rc, tree = self._run(["xwininfo", "-tree", "-id", dessous], timeout=30, env=self._env_outils())
        return rc == 0 and re.search(r"\b0x%x\b" % int(wid), tree) is not None

    def cliquer(self, libelle, processus=None):
        """The mouse on the middle of the button, as a user clicks: the window to the front, the button
        scrolled into view, its place in the window from AT-SPI (relative to the page: WebKitGTK's
        screen places leave out the window manager's frame), then nothing else under the pointer."""
        res = self._helper({"op": "bouton", "app": NOM_ATSPI, "libelle": libelle, "fenetre": self.fenetre_titre})
        if not res.get("ok"):
            raise ErreurPilote("pas de clic sur « %s » (%s)" % (libelle, res.get("pourquoi")))
        pourquoi = self._souris(res)
        if pourquoi is None:
            return CLIC_SOURIS
        res = self._helper({"op": "bouton", "app": NOM_ATSPI, "libelle": libelle, "presser": True, "fenetre": self.fenetre_titre})
        if not res.get("ok"):
            raise ErreurPilote("pas de clic sur « %s » (ni souris ni AT-SPI : %s)" % (libelle, pourquoi))
        return "action AT-SPI (pas de clic souris : %s)" % pourquoi

    def _souris(self, res):
        f = self.fenetre()
        b, page = res.get("bouton"), res.get("page")
        if not f:
            return "pas de fenêtre X"
        if not (isinstance(b, list) and isinstance(page, list) and b[2] > 0 and b[3] > 0):
            return "pas de position"
        wid, (wx, wy, ww, wh) = f
        x, y = wx + b[0] - page[0] + b[2] // 2, wy + b[1] - page[1] + b[3] // 2
        if not (wx <= x < wx + ww and wy <= y < wy + wh):
            return "hors de sa fenêtre"
        self._x("windowactivate", "--sync", wid)
        self._x("mousemove", "--sync", x, y)
        if not self.sous_la_souris(wid):
            return "un autre programme est sur le bouton"
        rc, out = self._x("click", "1")
        return None if rc == 0 else "xdotool click : %s" % out.strip()[-100:]

    guide_prudent = True

    def racines_traces(self, home):
        return Pilote.racines_traces(self, home) + [Path("/tmp")]

    def _devant(self):
        f = self.fenetre()
        if not f:
            raise ErreurPilote("pas de fenêtre X où taper")
        self._x("windowactivate", "--sync", f[0])
        return f[0]

    def taper(self, texte):
        self._devant()
        rc, out = self._x("type", "--delay", "40", "--", texte, timeout=60)
        if rc != 0:
            raise ErreurPilote("xdotool type : %s" % out.strip()[-100:])

    def tabulation(self):
        self._devant()
        self._x("key", "--clearmodifiers", "Tab")

    def coller_image(self, png):
        """xclip holds the image as the clipboard's owner, as a capture tool does, until the next copy."""
        if not shutil.which("xclip"):
            rc, out = self._run(["sudo", "-n", "apt-get", "install", "-y", "-q", "xclip"], timeout=600,
                                env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
            if rc != 0:
                raise ErreurPilote("xclip ne s'installe pas : %s" % out.strip()[-200:])
        try:
            p = subprocess.Popen(["xclip", "-selection", "clipboard", "-t", "image/png", "-i"], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self._env_outils())
            p.communicate(png, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ErreurPilote("xclip : %s" % e)
        if p.returncode != 0:
            raise ErreurPilote("xclip n'a pas pris l'image (code %d)" % p.returncode)
        self._devant()
        self._x("key", "--clearmodifiers", "ctrl+v")

    def capturer(self, chemin):
        # 8-bit RGB always (ImageMagick would write a palette for a screen of few colours): the guide's ring is read in it.
        rc, out = self._run(["import", "-window", "root", "png24:%s" % chemin], timeout=60, env=self._env_outils())
        if rc != 0 or not Path(chemin).is_file() or Path(chemin).stat().st_size == 0:
            raise ErreurPilote("pas de capture de l'écran (import code %d : %s)" % (rc, out.strip()[-200:]))

    def arbre(self):
        return self.dernier_arbre


# --------------------------------------------------------------------------- the walk through the window

class Parcours:
    """Plays the screens of the app's window as a user who wants it all installed, skips the Voyage
    key and the Claude login. Every new screen, or install step, gets a capture and a step name
    (t), every 2 min of waiting another capture. A failure names the screen and quotes Bibo.
    codex: the user chooses Codex alone, and skips its ChatGPT login."""

    def __init__(self, t, pilote, dossier, horloge=time.monotonic, dormir=time.sleep, echeance=None, codex=False):
        self.t, self.p, self.dossier = t, pilote, Path(dossier)
        self.codex = codex
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
        # « Importer ma mémoire » from the end: "aller" to the copy-paste, "retour" back to the end, else None.
        self.marche = None
        self.arrive = horloge()  # when the current screen showed
        self.principaux = {}    # {screen: {"bouton", "place", "zone"}}: the main buttons measured, whole on the screen

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

    def entrer(self, cle, prefixe, capture=True):
        if self.ecrans and self.ecrans[-1]["duree_s"] is None:
            self.ecrans[-1]["duree_s"] = self.secondes() - self.ecrans[-1]["debut_s"]
        self.ecrans.append({"ecran": prefixe + cle, "debut_s": self.secondes(), "duree_s": None})
        self.arrive = self.horloge()
        self.p.nouvel_ecran()
        # A failure keeps the step it happened in (app-etape-brain, not app-echec).
        if cle not in ARRETS:
            self.t("app-" + prefixe + cle)
        if capture:
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
                          "hors-app": "la fenêtre ne parle pas à l'app (pas de __TAURI__)",
                          "import-a-jour": "l'app ne trouve pas l'import dans le cerveau qu'elle vient d'installer",
                          "import-erreur": "« Importer ma mémoire » s'arrête sur une erreur"}[cle])
        if cle in HORS_PARCOURS[self.codex]:
            self.echouer("écran « %s » : %s" % (cle, "Codex est choisi seul" if self.codex else
                                                 "l'app livrée garde Codex fermé, elle ne demande pas quelle IA"))
        if cle in ("bienvenue", "bienvenue-retour"):
            return "Choisir mon IA" if self.codex else "Vérifier mon %s" % self.p.machine
        if cle == "choix":
            # The advice comes a moment after the screen (machine_advice): read before choosing.
            if not conseil(e) and self.horloge() - self.arrive < DELAI_PHRASE / 2:
                return None
            return "Codex"
        if self.marche == "aller" and cle == "fini":
            return "Importer ma mémoire"
        if cle == "import-choix":
            return "Je copie-colle (2 min)" if self.marche == "aller" else "Retour"
        if cle == "import-collage":
            return "Retour"
        if cle in ("connexion", "connexion-chatgpt"):
            return "Plus tard"
        if relance:
            if cle == "deja-installe":
                return "Continuer"
            if cle in ("diagnostic", "voyage", "connexion-installation") or cle.startswith("etape-"):
                self.echouer("au deuxième lancement, l'app veut encore installer")
            return None
        if cle == "deja-installe":
            self.echouer("au premier lancement, l'app dit que tout est déjà installé : la VM n'est pas neuve")
        if cle == "voyage":
            self.echouer("l'app demande une clé Voyage avant la fin de l'installation : c'est une option de la fin")
        if cle == "diagnostic" and e.bouton(DETAIL) is not None:
            return DETAIL
        return {"diagnostic": "Tout installer", "connexion-installation": "Plus tard"}.get(cle)

    def avancer(self, but, delai, relance=False, prefixe=None):
        """Until the screen `but`, within delai seconds. Returns {screen: first Ecran of it}."""
        prefixe = prefixe if prefixe is not None else "relance-" if relance else ""
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
            # The fullest read of a screen: the diagnosis once its lines are unfolded.
            if cle not in vus or (vus[cle].partiel and not e.partiel) or (
                    cle == "diagnostic" and not e.partiel and len(e.textes) > len(vus[cle].textes)):
                vus[cle] = e
            if not e.partiel:
                self.mesurer(prefixe + cle, cle, e)
            if cle == but and not e.partiel:
                self.ecrans[-1]["duree_s"] = 0
                return vus
            label = None if e.partiel else self.reaction(cle, e, relance)
            state = e.bouton(label) if label else None
            if label and not state:
                # The page's accessibility tree may show the words before the buttons: read it all again.
                self.forcer = True
            if state:
                # Another button of the same screen (« Voir le détail », then « Tout installer »): clicked now.
                if clic is not None and clic[2] != label:
                    clic = None
                if clic is None or now - clic[0] >= CLIC_RELANCE:
                    if clic is not None and clic[1] >= CLIC_ESSAIS:
                        self.echouer("le clic sur « %s » ne change rien (%d essais)" % (label, clic[1]))
                    how = self.p.cliquer(label)
                    self.clics.append({"a_s": self.secondes(), "ecran": prefixe + cle, "bouton": label, "comment": how})
                    clic = (now, (clic[1] if clic else 0) + 1, label)
            elif label and now - depuis >= CLIC_RELANCE * CLIC_ESSAIS:
                self.echouer("l'écran « %s » n'offre pas le bouton « %s »%s" % (cle, label, " (désactivé)" if state is False else ""))
            if cle in ("inconnu", "chargement", "sans-fenetre") and now - depuis >= INCONNU_MAX:
                self.echouer("écran « %s » depuis %d s" % (cle, int(now - depuis)))
            if now >= fin:
                self.echouer("toujours sur l'écran « %s » après %d min%s" % (
                    cle, int((now - debut) // 60), "" if fin == debut + delai else " (fin du temps du scénario)"))
            self.dormir(POLL)

    def mesurer(self, nom, cle, e):
        """The screen's main button, once it is in the tree: a user sees it whole, inside the screen's usable
        area (a window taller than a laptop's screen hid it under the taskbar)."""
        libelle = next((b for b in (x.format(machine=self.p.machine) for x in PRINCIPAUX.get(cle, ()))
                        if e.bouton(b) is not None), None)
        if not libelle or nom in self.principaux:
            return
        cache = cache_du_bouton(e, libelle)
        if cache is None:
            return
        self.principaux[nom] = {"bouton": libelle, "place": e.places.get(norm(libelle)), "zone": e.zone}
        if cache:
            self.capturer(nom + "-bouton-cache")
            self.echouer("écran « %s » : le bouton « %s » sort de l'écran utile (%s) : un utilisateur ne le voit pas en entier"
                         % (nom, libelle, cache))

    def lancer(self, env, prefixe=""):
        self.t("app-%slancement" % prefixe)
        self.precedent, self.forcer = None, True
        self.p.ouvrir(env)
        if not self.p.attendre(lambda: self.lire().fenetre, DELAI_FENETRE, self.horloge, self.dormir):
            self.capturer(prefixe + "sans-fenetre")
            self.echouer("aucune fenêtre %d s après l'ouverture de l'app" % DELAI_FENETRE)
        if not prefixe and self.p.ouvrir_encore:
            # The user double-clicks again: still one Bibo, the first one.
            pris = self.p.ouvrir_encore(env)
            self.t.details["deuxieme_ouverture_s"] = pris
            self.t.check(pris is not None, "l'app ouverte une deuxième fois reste ouverte : deux Bibo à la fois")
            self.t.check(self.p.vivante(), "l'app ouverte une deuxième fois a fermé la première")

    def importer(self):
        """« Importer ma mémoire » from the end: its choice, the copy-paste showing the sentence to paste,
        and back to the end. Nothing is sent: no AI is asked on these VMs."""
        self.marche = "aller"
        vus = self.avancer("import-collage", DELAI_IMPORT)
        self.t.check("import-choix" in vus, "« Importer ma mémoire » n'a pas mené à « D'où vient ta mémoire ? »")
        phrase = self.phrase()
        self.t.check(phrase, "l'écran du copier-coller ne montre pas la phrase à coller (%d s)" % DELAI_PHRASE)
        self.t.details["import"] = {"phrase": phrase[:120], "choix": vus["import-choix"].texte}
        self.marche = "retour"
        self.avancer("fini", DELAI_IMPORT)
        self.marche = None

    def phrase(self):
        """The sentence to paste, once the window shows it: the window asks the kit for it."""
        fin = self.horloge() + DELAI_PHRASE
        while True:
            self.forcer = True
            e = self.lire()
            found = next((x for x in e.textes if PHRASE in x), None)
            if found or self.horloge() >= fin or (not e.partiel and cle_ecran(e)[0] != "import-collage"):
                return found
            self.dormir(POLL)

    def premier_lancement(self, env):
        """Bienvenue to the final screen, then « Importer ma mémoire » there and back; returns how long the
        install took, in seconds."""
        self.lancer(env)
        start = self.horloge()
        vus = self.avancer("fini", DELAI_PREMIER)
        for must in ("bienvenue", "choix", "diagnostic", "etape-codex", "connexion-chatgpt") if self.codex else ("bienvenue", "diagnostic"):
            self.t.check(must in vus, "le parcours n'est pas passé par l'écran « %s »" % must)
        if self.p.mesure_places:
            for cle in ("diagnostic",) if self.codex else ("bienvenue", "diagnostic"):
                self.t.check(cle in self.principaux, "écran « %s » : le bouton principal n'a pas été mesuré (le pilote ne donne "
                                                      "plus la place des boutons ou la zone utile de l'écran)" % cle)
        fini = vus["fini"]
        if self.codex:
            self.t.check("etape-claude" not in vus, "l'app a installé Claude Code alors que seul Codex est choisi")
            self.t.check(fini.texte.startswith("Codex retient"), "l'écran final ne nomme pas Codex : « %s »" % fini.texte)
            # The robot put the ChatGPT login off: Codex is used on that login only (§4.3), so it comes first.
            self.t.check(fini.bouton("Présente-toi à Codex (15 min)") is None,
                         "l'écran final propose de se présenter à un Codex qui n'est pas connecté à ChatGPT")
            self.t.check(any("Sign in with ChatGPT" in x for x in fini.textes), "l'écran final ne dit pas de choisir « Sign in with ChatGPT »")
            boutons = ("Connecter Codex à mon compte ChatGPT", "Voir mes notes")
        else:
            # Claude alone: the words of before Codex (all of them: app/dev/screens.mjs, in the tests).
            self.t.check("pour Claude" in vus["bienvenue"].texte, "la bienvenue ne parle pas de Claude : « %s »" % vus["bienvenue"].texte)
            # The robot put Claude's login off: the end is not « prêt », it asks for the login first, and
            # opens nothing of Claude Code before it.
            self.t.check(fini.titre.startswith("Presque prêt"), "l'écran final se dit prêt sans compte Claude : « %s »" % fini.titre)
            self.t.check(fini.bouton("Présente-toi à Claude (15 min)") is None,
                         "l'écran final propose de se présenter à Claude, qui n'est pas connecté")
            boutons = ("Connecter mon compte Claude", "Voir mes notes")
        for b in boutons + ("Importer ma mémoire",):
            self.t.check(fini.bouton(b) is not None, "l'écran final n'a pas le bouton « %s »" % b)
        # Bibo's advice: at the diagnosis, or where Codex is open on the choice screen (the app's own words).
        ou = "choix" if self.codex else "diagnostic"
        self.t.check(conseil(vus[ou]), "%s ne donne pas « %s »" % ("l'écran du choix" if self.codex else "le diagnostic", CONSEIL))
        self.t.details["conseil"] = conseil(vus[ou])
        self.t.details["diagnostic"] = {"bibo": vus["diagnostic"].texte, "textes": vus["diagnostic"].textes[:60]}
        for label, detail in deja_presents(vus["diagnostic"].textes, self.p.neufs):
            self.t.notes.append("déjà sur la VM avant l'app : %s (%s), son installation par l'app n'est pas testée"
                                % (label, detail))
        manque = etapes_manquees(vus)
        self.t.check(not manque, manque)
        took = int(self.horloge() - start)
        self.importer()
        return took

    def relance(self, env):
        """The app opened again: it must say everything is installed, and get to the end."""
        self.lancer(env, "relance-")
        vus = self.avancer("fini", DELAI_RELANCE, relance=True)
        self.t.check("deja-installe" in vus, "au deuxième lancement, l'app n'a pas dit « Tout est déjà installé »")
        if self.codex:
            self.t.check("choix" in vus, "au deuxième lancement, l'app n'a pas redemandé quelle IA")
        else:
            # Claude Code and the brain are there now, Claude's login still put off (no account on these VMs):
            # the welcome asks for it, the guide needs it.
            retour = vus.get("bienvenue-retour")
            self.t.check(retour is not None, "au deuxième lancement, l'app ne dit pas « Content de te revoir »")
            self.t.check(retour is None or (retour.bouton(CONNECTER) is not None and retour.bouton(GUIDE) is None),
                         "au deuxième lancement, sans compte Claude, l'accueil ne demande pas « %s » avant le guide" % CONNECTER)
        return vus

    # ── The guide (§9): a third launch, its window walked to the procedure in the brain ──

    def guide(self, env, images, prudent=False, logiciel=None):
        """From the welcome's « Guide-moi dans un logiciel » to the procedure in the brain, as a user: G1
        read, the window shown to Bibo, the goal typed, its photo sent, the step and its ellipse, the step
        done in the program, « C'est fait », a second photo, the end, the procedure saved, the guide closed.
        logiciel: the user's program open on the screen (Logiciel), whose window Bibo takes in photo itself
        (a click in it on Windows, the system's picker on macOS); None: the captures `images` are pasted
        instead ({"debut": PNG, "fait": PNG}, Cmd/Ctrl+V), the program's name typed with the goal. prudent:
        no Opus for the account (the fake refuses it), Sonnet's dotted ellipse expected. Returns what was seen."""
        vu = {"ecrans": [], "collages": [], "saisie": None, "temoin": None, "etape": None,
              "capture": logiciel.via if logiciel else "collage", "gestes": []}
        self.lancer(env, "guide-")
        self.avancer("bienvenue-retour", DELAI_RELANCE, relance=True, prefixe="guide-")
        self.forcer = True
        how = self.p.cliquer(GUIDE)
        self.clics.append({"a_s": self.secondes(), "ecran": "guide-bienvenue-retour", "bouton": GUIDE, "comment": how})
        self.p.fenetre_titre = GUIDE_TITRE
        self.precedent, self.forcer = None, True
        try:
            self.marcher_guide(images, prudent, vu, logiciel)
        finally:
            self.p.fenetre_titre = None
            self.precedent, self.forcer = None, True
        return vu

    def marcher_guide(self, images, prudent, vu, logiciel=None):
        p, t = self.p, self.t
        debut = self.horloge()
        fin = debut + DELAI_GUIDE if self.echeance is None else min(debut + DELAI_GUIDE, self.echeance)
        cle_prec, depuis, clic, faits = None, 0.0, None, set()
        while True:
            e = self.lire()
            cle = cle_guide(e)
            now = self.horloge()
            if cle != cle_prec:
                cle_prec, depuis, clic = cle, now, None
                if cle == "sans-fenetre" and "enregistre" in vu["ecrans"]:
                    return      # « Fermer le guide »: its window went
                # macOS's picker out, the screen is not captured, as a user takes none then (the first night's
                # walk took one there, and the picker never gave the window: cause not proven, 2026-10-23).
                self.entrer(cle, "guide-", capture=not (cle == "selecteur" and logiciel is not None))
                vu["ecrans"].append(cle)
            action = None
            ratee = next((why for key, _, why in PHOTO_RATEE if key == cle), None)
            if ratee:
                self.echouer(ratee)
            elif cle == "inconnu":
                if now - depuis >= INCONNU_GUIDE:
                    self.echouer("le guide montre un écran inattendu")
            elif cle in ATTENTES_GUIDE:
                if now - depuis >= (DELAI_FENETRE if cle == "sans-fenetre" else 90):
                    self.echouer("le guide reste sur « %s » depuis %d s" % (cle, int(now - depuis)))
                if cle == "selecteur" and logiciel is not None:
                    # macOS's picker is out: the window and the button macOS shows over it.
                    action = ("logiciel", "montrer")
            elif cle == "comment":
                action = ("cliquer", "J'ai compris")
            elif cle == "choisir":
                if logiciel is None:
                    # Bibo could take the window, the robot pastes instead (Linux): pasting from then on.
                    action = ("coller", "debut")
                elif e.bouton("Réessayer"):
                    action = ("cliquer", "Réessayer")      # the click came too soon: Bibo asks again
                elif logiciel.via == "selecteur":
                    action = ("cliquer", CHOISIR_FENETRE)
                else:
                    action = ("logiciel", "montrer")
            elif cle == "fenetre":
                action = ("cliquer", "Oui, c'est elle")
            elif cle == "objectif":
                if "objectif" not in faits:
                    faits.add("objectif")
                    if logiciel is not None and logiciel.via == "selecteur":
                        self.bibo_devant(vu)
                    if e.texte.startswith(norm(PROGRAMME_CONNU)):
                        # The program's name from the window (Windows): only the goal, its field has the focus.
                        p.taper(OBJECTIF_GUIDE)
                        vu["saisie"] = "clavier, le logiciel rempli par Bibo"
                    else:
                        # The field of the program has the focus: the program, Tab, the goal.
                        p.taper(LOGICIEL_GUIDE)
                        p.tabulation()
                        p.taper(OBJECTIF_GUIDE)
                        vu["saisie"] = "clavier"
                    self.forcer = True
                    continue
                action = ("cliquer", "Continuer")
            elif cle in ("coller-debut", "coller-fait"):
                if logiciel is not None:
                    self.echouer("Bibo demande une capture collée au lieu de prendre lui-même la photo de la fenêtre")
                action = ("coller", cle.split("-", 1)[1])
            elif cle == "apercu":
                if "temoin" not in faits:
                    # Before any answer, no ellipse on the screen: the ring's reading proves nothing otherwise.
                    faits.add("temoin")
                    vu["temoin"] = self.lire_anneaux(self.captures[-1])
                    t.check(vu["temoin"]["boutons_verts"] >= 1, "l'aperçu ne montre pas la fenêtre (aucun bouton vert)")
                    t.check(not vu["temoin"]["entoures"], "une ellipse rouge sur l'aperçu, avant toute réponse de Claude")
                t.check(e.bouton("Refaire la capture") is None, "l'aperçu dit « la même capture que la dernière fois »")
                action = ("cliquer", "Envoyer à Claude")
            elif cle == "etape":
                if "etape" not in faits:
                    faits.add("etape")
                    vu["etape"] = self.verifier_etape(e, prudent)
                if logiciel is not None and "pas" not in faits:
                    # The step itself, in the program: « Nouveau », as Claude said.
                    faits.add("pas")
                    how = logiciel.faire_etape()
                    vu["gestes"].append("Nouveau")
                    self.clics.append({"a_s": self.secondes(), "ecran": "logiciel", "bouton": "Nouveau", "comment": how})
                    self.forcer = True
                    continue
                action = ("cliquer", "C'est fait")
            elif cle == "fin":
                # The summary's step is checked in the note it becomes (AT-SPI names a list item, no text).
                action = ("cliquer", "Enregistrer cette procédure")
            elif cle == "enregistrer":
                action = ("cliquer", "Enregistrer dans mon cerveau")
            elif cle == "enregistre":
                vu["enregistre"] = e.titre
                action = ("cliquer", "Fermer le guide")
            if action and (clic is None or now - clic[0] >= CLIC_RELANCE):
                if clic is not None and clic[1] >= CLIC_ESSAIS:
                    self.echouer("« %s » ne change rien sur l'écran « %s » du guide (%d essais)" % (action[1], cle, clic[1]))
                if action[0] == "coller":
                    p.coller_image(images[action[1]])
                    vu["collages"].append(action[1])
                    clic = (now, (clic[1] if clic else 0) + 1)
                    self.forcer = True
                elif action[0] == "logiciel":
                    how = logiciel.montrer()
                    vu["gestes"].append(cle)
                    self.clics.append({"a_s": self.secondes(), "ecran": "guide-" + cle, "bouton": logiciel.titre, "comment": how})
                    clic = (now, (clic[1] if clic else 0) + 1)
                    self.forcer = True
                else:
                    state = e.bouton(action[1])
                    if state:
                        how = p.cliquer(action[1])
                        self.clics.append({"a_s": self.secondes(), "ecran": "guide-" + cle, "bouton": action[1], "comment": how})
                        clic = (now, (clic[1] if clic else 0) + 1)
                    else:
                        self.forcer = True
                        if now - depuis >= CLIC_RELANCE * CLIC_ESSAIS:
                            self.echouer("l'écran « %s » du guide n'offre pas le bouton « %s »%s" % (
                                cle, action[1], " (désactivé)" if state is False else ""))
            if now >= fin:
                self.echouer("toujours sur l'écran « %s » du guide après %d min" % (cle, int((now - debut) // 60)))
            self.dormir(POLL)

    def bibo_devant(self, vu):
        """After macOS's picker, Bibo in front again (lib.rs, guide_choisir): what the user types next goes to
        Bibo, not to the program picked. Asked before the robot types, its keyboard bringing Bibo in front."""
        lu = [(None, None)]

        def devant():
            lu[0] = self.p.app_devant()
            return lu[0][0] is not False

        self.p.attendre(devant, DELAI_DEVANT, self.horloge, self.dormir, pas=0.5)
        ok, nom = lu[0]
        vu["devant"] = nom
        if ok is False:
            self.echouer("après le sélecteur de macOS, « %s » est devant et pas Bibo : ce que l'utilisateur tape irait "
                         "dans son logiciel" % (nom or "aucune app"))

    def lire_anneaux(self, nom):
        """The green buttons of the capture `nom` and those a red ring goes round."""
        w, h, px = ecrans().lire_png((self.dossier / nom).read_bytes())
        trouves = anneaux(w, h, px)
        return {"capture": nom, "boutons_verts": len(trouves), "entoures": [a["boite"] for a in entoures(trouves)],
                "cotes": [a["cotes"] for a in trouves]}

    def verifier_etape(self, e, prudent):
        """The step: Claude's sentence, the ellipse the page says it drew (full for Opus, dotted for the
        cautious mode), and on a capture of the screen a red ring round the green button."""
        t = self.t
        t.check(norm(REPONSES_GUIDE["debut"]["consigne"]) in e.textes, "l'étape ne montre pas la consigne de Claude")
        attendu = ANNEAU_POINTILLE if prudent else ANNEAU_PLEIN
        t.check(norm(attendu) in e.textes, "l'étape ne dit pas « %s »" % attendu)
        dit_prudent = any(x.startswith(PRUDENT) for x in e.textes)
        t.check(dit_prudent == prudent, "l'étape %s le mode prudent" % ("ne dit pas" if prudent else "dit à tort"))
        self.dormir(1.0)
        vu = self.lire_anneaux(self.capturer("guide-etape-anneau"))
        t.check(vu["entoures"], "aucune ellipse rouge autour du bouton vert sur la capture de l'étape (%d bouton(s) vert(s), "
                                "côtés : %s)" % (vu["boutons_verts"], vu["cotes"]))
        vu.update(niveau="pointille" if prudent else "plein", prudent=dit_prudent)
        return vu

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
                "bibo": {"titre": self.dernier.titre, "texte": self.dernier.texte}, "boutons_principaux": self.principaux,
                "dernier_ecran": dict(self.dernier.resume(), lecture=self.dernier.lecture), "lecture_fenetre_ms": {"pleine": mean(self.lectures), "rapide": mean(self.rapides),
                                                           "lectures": [len(self.lectures), len(self.rapides)]}}


# --------------------------------------------------------------------------- the guide (docs/proposals/guide.md §9)

# The guide's window (lib.rs, open_guide) and the installer's.
GUIDE_TITRE = "Bibo, ton guide"
APP_TITRE = TITRE_FENETRE
# What the robot types, ASCII (System Events' keystroke): a made-up program and what to do in it.
LOGICIEL_GUIDE, OBJECTIF_GUIDE = "Atelier Demo", "Faire une facture"
# Bibo's words on the goal's screen when it named the program from the window (guide.js, showGoal).
PROGRAMME_CONNU = "Dis-moi ce que tu veux faire dans ton logiciel"
# The fake Claude's answers (engine/tests/fake_claude.py, scenario "guide"): « Nouveau », then done.
REPONSES_GUIDE = {
    "debut": {"etat": "etape", "consigne": "Clique sur « Nouveau », en haut à gauche, sous le titre.", "libelle": "Nouveau",
              "cible": "vert", "confiance": 0.92, "question": None, "choix": [], "precedente": "sans_objet",
              "action_sensible": False, "suspect": False, "resume_etape": "Clique sur « Nouveau » en haut à gauche."},
    "fait": {"etat": "termine", "consigne": "La facture est enregistrée : c'est fait !", "libelle": None, "cible": None,
             "confiance": 0.95, "question": None, "choix": [], "precedente": "faite", "action_sensible": False, "suspect": False,
             "resume_etape": "Clique sur « Valider »."},
}
ETAPE_ENREGISTREE = "1. Clique sur « Nouveau » en haut à gauche."
# Bibo's titles in the guide's window (app/src/guide.js), after norm(): the screen they stand for. « Montre-moi
# ta fenêtre. » is the paste's and the wait for the window's (« choisir »): its buttons say which (cle_guide).
ECRANS_GUIDE = (
    ("charge", "Je prépare le guide"),
    ("comment", "Comment je regarde ton écran"),
    ("objectif", "Qu'est-ce que tu veux faire ?"),
    ("coller-debut", "Montre-moi ta fenêtre."),
    ("selecteur", "Choisis ta fenêtre."),
    ("fenetre", "C'est bien celle-ci ?"),
    ("photo", "Je prends ta fenêtre en photo"),
    ("coller-fait", "Montre-moi où tu en es."),
    ("lecture", "Je lis ton image"),
    ("apercu", "Voici ce que Claude va voir."),
    ("regarde", "Claude regarde ta fenêtre"),
    ("etape", "Étape "),
    ("fin", "Bravo, c'est fait !"),
    ("brouillon", "Je prépare la procédure"),
    ("enregistrer", "Je l'écris dans ton cerveau ?"),
    ("enregistre", "Enregistré dans "),
)
# The buttons of the wait for the window: a click in it (Windows, X11), the system's picker (macOS).
CHOISIR_LISTE, CHOISIR_FENETRE = "Choisir dans une liste", "Choisir la fenêtre"
# Where Bibo's own photo of the window does not come (Windows, macOS): (screen, Bibo's title, the failure).
PHOTO_RATEE = (
    ("photo-noire", "Ton logiciel ne me laisse pas le prendre en photo.", "la photo de la fenêtre d'Atelier Demo est noire"),
    ("fenetre-noire", "Cette fenêtre ne se laisse pas prendre en photo.", "la fenêtre d'Atelier Demo, choisie à nouveau, reste noire"),
    ("sans-photo", "Je ne peux pas voir tes fenêtres tout seul", "Bibo ne voit pas les fenêtres de ce système : il demande une capture collée"),
    ("apercu-meme", "Ta fenêtre n'a pas changé depuis la dernière photo.",
     "la deuxième photo est la même que la première : le clic sur « Nouveau » dans Atelier Demo n'a rien changé"),
    ("reduite", "Ta fenêtre est réduite.", "Bibo dit la fenêtre d'Atelier Demo réduite"),
    ("introuvable", "Je ne trouve plus ta fenêtre.", "Bibo ne trouve plus la fenêtre d'Atelier Demo"),
    ("terminal", "Je ne guide pas dans un terminal.", "Bibo a pris un terminal pour la fenêtre du logiciel"),
)
ATTENTES_GUIDE = {"sans-fenetre", "chargement", "charge", "lecture", "regarde", "brouillon", "photo", "selecteur"}
# On the step: what the page says for the screen reader with its ellipse, Opus's full one or Sonnet's dotted one.
ANNEAU_PLEIN = "Sur l'image, « Nouveau » est entouré en rouge."
ANNEAU_POINTILLE = "Sur l'image, la zone à vérifier est entourée en pointillés."
PRUDENT = "Mode prudent"
DELAI_GUIDE = 6 * 60
INCONNU_GUIDE = 30
# The ellipse's red (styles.css, .ring ellipse and .zoom-ring) and the button's green (ecrans_guide.VERT).
ROUGE, VERT = (229, 38, 43), (20, 160, 60)


def cle_guide(e):
    """The guide's screen the window shows: a known one, a photo that did not come, or « inconnu »."""
    if not e.fenetre:
        return "sans-fenetre"
    if not e.titre:
        return "chargement"
    for key, start, _ in PHOTO_RATEE:
        if e.titre.startswith(norm(start)):
            return key
    for key, start in ECRANS_GUIDE:
        if e.titre.startswith(norm(start)):
            if key == "coller-debut" and (e.bouton(CHOISIR_LISTE) is not None or e.bouton(CHOISIR_FENETRE) is not None):
                return "choisir"
            return key
    return "inconnu"


def masque(px, i, couleur, ecart):
    r, g, b = px[i], px[i + 1], px[i + 2]
    return abs(r - couleur[0]) <= ecart and abs(g - couleur[1]) <= ecart and abs(b - couleur[2]) <= ecart


def anneaux(w, h, px):
    """[(the green box on the screen, the sides of it where the ring's red is found)] of a capture (RGB):
    each green button and whether an ellipse is around it, as Bibo draws it on the image and its zoom."""
    vert = bytearray(w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            if masque(px, (row + x) * 3, VERT, 45):
                vert[row + x] = 1
    boites = []
    for start in range(w * h):
        if not vert[start]:
            continue
        pile, x0, y0, x1, y1, n = [start], w, h, 0, 0, 0
        vert[start] = 0
        while pile:
            i = pile.pop()
            x, y = i % w, i // w
            n += 1
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
            for j in (i - 1 if x else -1, i + 1 if x + 1 < w else -1, i - w, i + w):
                if 0 <= j < w * h and vert[j]:
                    vert[j] = 0
                    pile.append(j)
        if n >= 40 and x1 - x0 >= 6 and y1 - y0 >= 3:
            boites.append((x0, y0, x1, y1))
    out = []
    for x0, y0, x1, y1 in boites:
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        dx, dy = bw // 2 + 14, bh // 2 + 14

        def rouge(xs, ys):
            return any(0 <= x < w and 0 <= y < h and masque(px, (y * w + x) * 3, ROUGE, 60) for y in ys for x in xs)
        cotes = [c for c, ok in (
            ("gauche", rouge(range(x0 - dx, x0), range(cy - bh // 2, cy + bh // 2 + 1))),
            ("droite", rouge(range(x1 + 1, x1 + 1 + dx), range(cy - bh // 2, cy + bh // 2 + 1))),
            ("haut", rouge(range(cx - bw // 4, cx + bw // 4 + 1), range(y0 - dy, y0))),
            ("bas", rouge(range(cx - bw // 4, cx + bw // 4 + 1), range(y1 + 1, y1 + 1 + dy)))) if ok]
        out.append({"boite": [x0, y0, bw, bh], "cotes": cotes})
    return out


def entoures(trouves):
    """The green buttons a ring goes round: red on three of its sides at least (a dotted ring has gaps)."""
    return [a for a in trouves if len(a["cotes"]) >= 3]


def note_du_guide(cerveau, depuis):
    """(the procedure note the guide wrote, its text) in the brain's Guides/ since `depuis`, or why not."""
    dossier = Path(cerveau) / "Guides"
    notes = [p for p in sorted(dossier.glob("*.md")) if p.stat().st_mtime >= depuis] if dossier.is_dir() else []
    if len(notes) != 1:
        return None, "%d note(s) neuve(s) dans %s (une attendue)" % (len(notes), dossier)
    texte = notes[0].read_text(encoding="utf-8")
    for must in ('source: "bibo-guide"', 'logiciel: "%s"' % LOGICIEL_GUIDE, "allowed-tools: []", ETAPE_ENREGISTREE):
        if must not in texte:
            return None, "la note %s n'a pas « %s »" % (notes[0].name, must)
    return notes[0], None


# How the walk showed the windows to Bibo, as the summary says it.
CAPTURES_DITES = {"clic": "la fenêtre d'Atelier Demo montrée d'un clic, deux photos prises par Bibo",
                  "selecteur": "la fenêtre d'Atelier Demo choisie dans le sélecteur de macOS, deux photos prises par Bibo",
                  "collage": "deux captures collées"}

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TRACES_MAX = 64 * 1024 * 1024


def montrer(p, home):
    """A path as the report shows it, the same on the 3 OS: ~/… under home, with slashes."""
    try:
        return "~/" + Path(p).relative_to(home).as_posix()
    except ValueError:
        return Path(p).as_posix()


def traces_du_guide(racines, depuis, exclus, fragments, dossiers_app):
    """[(file, why)] of the files written since `depuis` under `racines` (`exclus` left out, links not
    followed): one that holds a fragment of an image of the guide (the captures pasted, those sent to
    Claude, their base64), or a PNG anywhere in it, in a folder of the app (`dossiers_app`: words of
    its paths, its identifier). Bibo keeps neither image nor answer on the disk (§5)."""
    exclus = [os.path.normcase(os.path.abspath(str(e))) for e in exclus]
    vus, out = set(), []
    for racine in racines:
        for dirpath, dirnames, filenames in os.walk(str(racine)):
            here = os.path.normcase(os.path.abspath(dirpath))
            if any(here == e or here.startswith(e + os.sep) for e in exclus):
                dirnames[:] = []
                continue
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                key = os.path.normcase(os.path.abspath(p))
                if key in vus or os.path.islink(p):
                    continue
                vus.add(key)
                try:
                    st = os.stat(p)
                    if st.st_mtime < depuis or not stat.S_ISREG(st.st_mode) or st.st_size > TRACES_MAX:
                        continue
                    with open(p, "rb") as f:
                        data = f.read()
                except OSError:
                    continue
                if any(frag and frag in data for frag in fragments):
                    out.append((p, "un morceau d'une image du guide"))
                elif PNG_SIGNATURE in data and any(m in key for m in dossiers_app):
                    out.append((p, "une image PNG dans un dossier de l'app"))
    return out


RELAIS_CS = r'''// claude.exe of the field test "app" (terrain_app.py): runs the fake Claude with the harness' Python,
// its arguments quoted as Windows reads them back, its stdin handed down as is (a stdin redirected by .NET
// starts with the BOM of its writer's encoding), its stdout and stderr passed through.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;

static class Relais
{
    const string PYTHON = @"__PYTHON__";
    const string FAUX = @"__FAUX__";

    static string Quote(string a)
    {
        if (a.Length > 0 && a.IndexOfAny(new char[] { ' ', '\t', '\n', '\v', '"' }) < 0) return a;
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < a.Length; i++)
        {
            int bs = 0;
            while (i < a.Length && a[i] == '\\') { bs++; i++; }
            if (i == a.Length) { b.Append('\\', bs * 2); break; }
            if (a[i] == '"') { b.Append('\\', bs * 2 + 1); b.Append('"'); }
            else { b.Append('\\', bs); b.Append(a[i]); }
        }
        b.Append('"');
        return b.ToString();
    }

    static void Copy(Stream from, Stream to)
    {
        byte[] buf = new byte[65536];
        int n;
        while ((n = from.Read(buf, 0, buf.Length)) > 0) { to.Write(buf, 0, n); to.Flush(); }
    }

    static int Main(string[] args)
    {
        StringBuilder line = new StringBuilder("-I " + Quote(FAUX));
        foreach (string a in args) line.Append(' ').Append(Quote(a));
        ProcessStartInfo psi = new ProcessStartInfo(PYTHON, line.ToString());
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        using (Process p = Process.Start(psi))
        {
            Stream o = Console.OpenStandardOutput(), e = Console.OpenStandardError();
            Thread to = new Thread(delegate () { Copy(p.StandardOutput.BaseStream, o); });
            Thread te = new Thread(delegate () { Copy(p.StandardError.BaseStream, e); });
            to.Start();
            te.Start();
            p.WaitForExit();
            to.Join();
            te.Join();
            return p.ExitCode;
        }
    }
}
'''
FAUX_CLAUDE = Path(__file__).resolve().with_name("fake_claude.py")


def poser_faux_claude(home, exe, travail, prudent):
    """The fake Claude at exe, where the app finds Claude Code, the real one set aside, and its settings
    (~/.claude/fake): the guide's scenario, its two answers, Opus refused when `prudent`. On Windows (an
    .exe) a relay compiled by the .NET Framework's csc, since the app runs no .cmd. Returns where the real
    one waits."""
    home, exe = Path(home), Path(exe)
    Path(travail).mkdir(parents=True, exist_ok=True)
    vrai = exe.with_name(exe.name + ".terrain-vrai")
    os.replace(str(exe), str(vrai))
    fake = home / ".claude" / "fake"
    if fake.exists():
        shutil.rmtree(str(fake))
    (fake / "guide").mkdir(parents=True)
    (fake / "scenario").write_text("guide", encoding="utf-8")
    (fake / "version").write_text("2.1.306 (Claude Code)", encoding="utf-8")
    # A subscription, as only one asks Claude (guide.rs): Pro, the plan Opus is refused to, when `prudent`.
    (fake / "plan").write_text("pro" if prudent else "max", encoding="utf-8")
    for demande, answer in REPONSES_GUIDE.items():
        (fake / "guide" / (demande + ".json")).write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    if prudent:
        (fake / "refuse-opus").write_text("", encoding="utf-8")
    if exe.suffix == ".exe":
        src = Path(travail) / "relais-claude.cs"
        src.write_text(RELAIS_CS.replace("__PYTHON__", sys.executable.replace('"', '""'))
                       .replace("__FAUX__", str(FAUX_CLAUDE).replace('"', '""')), encoding="ascii")
        csc = Path(os.environ.get("WINDIR") or r"C:\Windows") / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
        p = subprocess.run([str(csc), "/nologo", "/target:exe", "/optimize+", "/out:%s" % exe, str(src)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, creationflags=SANS_CONSOLE)
        if p.returncode != 0 or not exe.is_file():
            os.replace(str(vrai), str(exe))
            raise ErreurPilote("le claude.exe relais ne compile pas : %s" % p.stdout.decode("utf-8", "replace")[-300:])
    else:
        exe.write_text("#!/bin/sh\nexec '%s' -I '%s' \"$@\"\n" % (sys.executable, FAUX_CLAUDE), encoding="utf-8")
        os.chmod(str(exe), 0o755)
    return vrai


def rendre_vrai_claude(exe, vrai):
    if Path(vrai).exists():
        if Path(exe).exists():
            os.remove(str(exe))
        os.replace(str(vrai), str(exe))


# The user's program the guide is walked in (Windows, macOS): « Atelier Demo », the list of invoices Linux
# pastes (ecrans_guide.liste, « Nouveau » in green), then the invoice saved once « Nouveau » is clicked in it.
# The drawn screens' 1280 × 800 shown at 800 × 500, the size of a program on a laptop's screen. Two real
# buttons lie over the drawn ones, which the drivers find by name, as a screen reader does: « Importer »
# (nothing happens), where the user clicks to show the window, and « Nouveau », the step.
TITRE_LOGICIEL = "Atelier Demo - Factures clients"
TAILLE_LOGICIEL = (800, 500)
PLACE_LOGICIEL = (40, 60)
# On macOS, lower: macOS 26's picker drew the window placed at 60 points 20 points lower, with its « Share
# This Window » button, which accessibility still gave at the window's real place (the night of 2026-10-23:
# 3 clicks in the middle of that button fell 3 points over the drawn one). The desk test's window, at 80,
# is picked (app/src-tauri/tests/fixtures/guide/pilote_mac.py).
PLACE_LOGICIEL_MAC = (40, 100)
# « Importer », as ecrans_guide.liste draws it next to « Nouveau ».
IMPORTER = {"x": 374, "y": 120, "largeur": 130, "hauteur": 40}

ATELIER_CS = r'''// Atelier Demo, the user's program of the field test "app" (terrain_app.py, LogicielWindows): the list of
// invoices, then the invoice saved once "Nouveau" is clicked. The drawn screens fill the window; two real
// buttons lie over the drawn ones. Compiled on the VM by the csc.exe of the .NET Framework; ASCII only.
//
//   atelier-demo.exe <title> <list.png> <invoice.png> <x> <y> <w> <h> <nx> <ny> <nw> <nh> <ix> <iy> <iw> <ih>
using System;
using System.Drawing;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("Atelier Demo")]

static class Atelier
{
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    static Button Bouton(string text, string[] a, int at, Color back, Color fore)
    {
        Button b = new Button();
        b.Text = text;
        b.Bounds = new Rectangle(int.Parse(a[at]), int.Parse(a[at + 1]), int.Parse(a[at + 2]), int.Parse(a[at + 3]));
        b.FlatStyle = FlatStyle.Flat;
        b.FlatAppearance.BorderSize = 0;
        b.FlatAppearance.MouseOverBackColor = back;
        b.FlatAppearance.MouseDownBackColor = back;
        b.BackColor = back;
        b.ForeColor = fore;
        b.TabStop = false;
        return b;
    }

    [STAThread]
    static int Main(string[] a)
    {
        SetProcessDPIAware();
        Form f = new Form();
        f.Text = a[0];
        f.StartPosition = FormStartPosition.Manual;
        f.Location = new Point(int.Parse(a[3]), int.Parse(a[4]));
        f.ClientSize = new Size(int.Parse(a[5]), int.Parse(a[6]));
        f.FormBorderStyle = FormBorderStyle.FixedSingle;
        f.MaximizeBox = false;
        PictureBox screen = new PictureBox();
        screen.Dock = DockStyle.Fill;
        screen.SizeMode = PictureBoxSizeMode.StretchImage;
        screen.Image = Image.FromFile(a[1]);
        Button nouveau = Bouton("Nouveau", a, 7, Color.FromArgb(20, 160, 60), Color.White);
        Button importer = Bouton("Importer", a, 11, Color.White, Color.FromArgb(30, 30, 36));
        nouveau.Click += delegate
        {
            screen.Image = Image.FromFile(a[2]);
            nouveau.Visible = false;
            importer.Visible = false;
        };
        screen.Controls.Add(nouveau);
        screen.Controls.Add(importer);
        f.Controls.Add(screen);
        Application.Run(f);
        return 0;
    }
}
'''

PAGE_ATELIER = """<!doctype html><meta charset=utf-8><title>%(titre)s</title>
<style>html,body{margin:0;background:#f4f5f7;overflow:hidden}img{display:block;width:%(w)dpx;height:%(h)dpx}
button{position:absolute;border:0;padding:0;margin:0;font:600 12px sans-serif}</style>
<img id=ecran src="liste.png" alt="">
%(boutons)s
<script>document.getElementById('nouveau').onclick=function(){document.getElementById('ecran').src='facture.png';
document.querySelectorAll('button').forEach(function(b){b.remove()})}</script>
"""
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Logiciel:
    """The user's program, open on the screen: its window, which Bibo takes in photo itself. via: how the
    user shows it to Bibo, "clic" (a click in it) or "selecteur" (the system's picker)."""

    via = None
    titre = TITRE_LOGICIEL

    def __init__(self, pilote, dossier):
        self.p, self.dossier = pilote, Path(dossier)
        self.proc = None
        self.dossier.mkdir(parents=True, exist_ok=True)
        liste, boites = ecrans().liste(vert=True)
        for name, png in (("liste", liste.png()), ("facture", ecrans().facture_creee()[0].png())):
            (self.dossier / (name + ".png")).write_bytes(png)
        k = TAILLE_LOGICIEL[0] / liste.w
        self.places = {name: [round(b["x"] * k), round(b["y"] * k), round(b["largeur"] * k), round(b["hauteur"] * k)]
                       for name, b in (("Nouveau", boites["nouveau"]), ("Importer", IMPORTER))}

    def ouvrir(self):
        """The program opened, its window on the screen."""
        raise NotImplementedError

    def montrer(self):
        """The user's gesture that shows Bibo the window, while Bibo waits for it: returns how it was done."""
        raise NotImplementedError

    def faire_etape(self):
        """« Nouveau » clicked in the program, as the step says: returns how."""
        raise NotImplementedError

    def fermer(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass


class LogicielWindows(Logiciel):
    """A Windows program of its own, as an ERP is (AssemblyTitle: Bibo names it from it): the user clicks once
    in its window, on « Importer » (the driver's mouse, as a user's)."""

    via = "clic"

    def ouvrir(self):
        src, exe = self.dossier / "atelier-demo.cs", self.dossier / "atelier-demo.exe"
        src.write_text(ATELIER_CS, encoding="ascii")
        csc = Path(os.environ.get("WINDIR") or r"C:\Windows") / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
        rc, out = self.p._run([csc, "/nologo", "/target:winexe", "/optimize+", "/out:%s" % exe, src], timeout=300,
                              creationflags=SANS_CONSOLE)
        if rc != 0 or not exe.is_file():
            raise ErreurPilote("Atelier Demo ne compile pas : %s" % out.strip()[-300:])
        args = [exe, self.titre, self.dossier / "liste.png", self.dossier / "facture.png", PLACE_LOGICIEL[0], PLACE_LOGICIEL[1],
                TAILLE_LOGICIEL[0], TAILLE_LOGICIEL[1]] + self.places["Nouveau"] + self.places["Importer"]
        try:
            self.proc = subprocess.Popen([str(a) for a in args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ErreurPilote("Atelier Demo ne se lance pas (%s)" % (e.strerror or type(e).__name__))
        self.h = self.p.attendre(self._fenetre, 60, self.p.horloge, self.p.dormir)
        if not self.h:
            raise ErreurPilote("la fenêtre d'Atelier Demo ne s'ouvre pas")
        self.p.ignores = (nom_programme(exe),)

    def _fenetre(self):
        for f in self.p._outil(["fenetres"]).get("fenetres") or []:
            if isinstance(f, dict) and f.get("pid") == self.proc.pid and norm(f.get("titre")) == norm(self.titre):
                return f.get("h")
        return None

    def _clic(self, libelle):
        res = self.p._outil(["cliquer", self.h, libelle, "souris"])
        if not res.get("ok"):
            raise ErreurPilote("pas de clic sur « %s » dans Atelier Demo (%s)" % (libelle, res.get("pourquoi")))
        return res.get("comment")

    def montrer(self):
        return self._clic("Importer")

    def faire_etape(self):
        return self._clic("Nouveau")


def dessus(b, f):
    """The middle of the button b on the window f ({pos, size} both, in points)."""
    cx, cy = b["pos"][0] + b["size"][0] / 2, b["pos"][1] + b["size"][1] / 2
    return f["pos"][0] <= cx <= f["pos"][0] + f["size"][0] and f["pos"][1] <= cy <= f["pos"][1] + f["size"][1]


class LogicielMac(Logiciel):
    """A program in a browser, as many are (Chrome's window of one page, its own profile): the user picks its
    window in macOS's picker. The page's buttons, read through accessibility."""

    via = "selecteur"
    processus = "Google Chrome"

    def ouvrir(self):
        if not Path(CHROME_MAC).is_file():
            raise ErreurPilote("pas de Google Chrome sur ce Mac (%s) : pas de logiciel où guider" % CHROME_MAC)
        boutons = "\n".join('<button id=%s type=button style="left:%dpx;top:%dpx;width:%dpx;height:%dpx;background:%s;color:%s">%s</button>'
                            % (name.lower(), x, y, w, h, "rgb(20,160,60)" if name == "Nouveau" else "#fff",
                               "#fff" if name == "Nouveau" else "#1e1e24", name)
                            for name, (x, y, w, h) in self.places.items())
        page = self.dossier / "atelier.html"
        page.write_text(PAGE_ATELIER % {"titre": self.titre, "w": TAILLE_LOGICIEL[0], "h": TAILLE_LOGICIEL[1], "boutons": boutons},
                        encoding="utf-8")
        x, y = PLACE_LOGICIEL_MAC
        try:
            self.proc = subprocess.Popen(
                [CHROME_MAC, "--user-data-dir=" + str(self.dossier / "profil"), "--no-first-run", "--no-default-browser-check",
                 "--disable-features=Translate", "--force-renderer-accessibility", "--window-position=%d,%d" % (x, y),
                 "--window-size=%d,%d" % (TAILLE_LOGICIEL[0], TAILLE_LOGICIEL[1] + 40), "--app=" + page.as_uri()],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ErreurPilote("Google Chrome ne se lance pas (%s)" % (e.strerror or type(e).__name__))
        if not self.p.attendre(self._cadre, 60):
            raise ErreurPilote("la fenêtre d'Atelier Demo ne s'ouvre pas dans Chrome")
        self.p.ignores = (self.processus,)

    def _cadre(self):
        f = self.p._osa({"op": "cadre", "processus": self.processus, "titre": self.titre})
        return f if isinstance(f.get("pos"), list) and isinstance(f.get("size"), list) else None

    def montrer(self):
        """macOS's picker is out (Bibo asked for it): the pointer over a part of the program's window, then a
        click on the button macOS shows there (« Share This Window », a small button of ControlCenter's, on
        macOS 15 and 26), or on the window itself (macOS 14 lays a button of the window's size over it). As
        the desk test of the capture does it (app/src-tauri/tests/fixtures/guide/pilote_mac.py). Of the small
        buttons, the widest whose middle is on the window. Returns a click of the mouse (CLIC_SOURIS), then where,
        on which window and the buttons read (a failure's record)."""
        f = self._cadre()
        if not f:
            raise ErreurPilote("la fenêtre d'Atelier Demo n'est plus là")
        (x, y), (w, h) = f["pos"], f["size"]
        over, share, boutons = [], [], []
        for i in range(12):
            self.p._osa({"op": "souris", "x": x + 0.5 * w + 7 * (i % 3), "y": y + 0.12 * h + 5 * (i % 2), "clic": False})
            boutons = [b for b in self.p._osa({"op": "boutons"}).get("boutons") or []
                       if isinstance(b, dict) and isinstance(b.get("pos"), list) and isinstance(b.get("size"), list)]
            over = [b for b in boutons if b["pos"] == f["pos"] and b["size"] == f["size"]]
            share = sorted((b for b in boutons if dessus(b, f)), key=lambda b: -b["size"][0]) \
                if boutons and all(b["size"][1] < 60 for b in boutons) else []
            if over or share:
                break
            time.sleep(1)
        if share:
            b = share[0]
            bx, by, how = b["pos"][0] + b["size"][0] * 0.75, b["pos"][1] + b["size"][1] / 2, "« Share This Window »"
        elif over:
            bx, by, how = x + 0.5 * w, y + 0.12 * h, "la fenêtre, sous le sélecteur"
        else:
            bx, by, how = x + w / 2 + 40, y + h / 2, "le milieu de la fenêtre (aucun bouton du sélecteur lu)"
        self.p._osa({"op": "souris", "x": bx, "y": by, "clic": True})
        lus = ", ".join("%s %s" % ("/".join(x for x in (b.get("n"), b.get("d")) if x) or "sans nom", b["pos"] + b["size"]) for b in boutons)
        return "%s (sélecteur de macOS : %s à %d, %d ; fenêtre %s ; boutons lus : %s)" % (
            CLIC_SOURIS, how, bx, by, f["pos"] + f["size"], lus or "aucun")

    def faire_etape(self):
        res = self.p._osa({"op": "cliquer", "processus": self.processus, "fenetre": None, "libelle": "Nouveau"})
        if not res.get("ok"):
            raise ErreurPilote("pas de clic sur « Nouveau » dans Atelier Demo (%s)" % res.get("pourquoi"))
        return res.get("comment")


_ECRANS = []


def ecrans():
    """engine/tests/ecrans_guide.py, loaded from its path (this folder never goes on sys.path)."""
    if not _ECRANS:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ecrans_guide", str(Path(__file__).resolve().with_name("ecrans_guide.py")))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _ECRANS.append(module)
    return _ECRANS[0]


def guide_terrain(t, parcours, pilote, home, env, captures, travail):
    """The guide on a third launch (Claude alone): the fake Claude in Claude Code's place, the user's program
    open where Bibo takes its window in photo (Windows, macOS; Linux pastes), the walk, then what it must
    have left (the procedure) and must not (an image), the calls the fake got, and on Windows the windows'
    commands kept apart. Returns the details."""
    prudent = pilote.guide_prudent
    t("app-guide-faux-claude")
    debut = time.time()
    exe = next(places[0] for name, places in pilote.outils(home) if name == "Claude Code")
    if not Path(exe).exists():
        raise ErreurPilote("pas de Claude Code à %s : l'app ne l'a pas installé" % exe)
    vrai = poser_faux_claude(home, exe, travail, prudent)
    fake = Path(home) / ".claude" / "fake"
    images = {"debut": ecrans().liste(vert=True)[0].png(), "fait": ecrans().facture_creee()[0].png()}
    logiciel = pilote.logiciel(Path(travail) / "logiciel")
    try:
        if logiciel is not None:
            t("app-guide-logiciel")
            logiciel.ouvrir()
        vu = parcours.guide(env, images, prudent, logiciel)
        t("app-guide-fermeture")
        pilote.lire()   # the installer's window again, the guide's gone
        vu["fermeture"] = pilote.fermer()
    finally:
        rendre_vrai_claude(exe, vrai)
        if logiciel is not None:
            logiciel.fermer()
            pilote.ignores = ()
    d = {"prudent": prudent, "capture": vu["capture"], "ecrans": vu["ecrans"], "collages": vu["collages"],
         "gestes": vu["gestes"], "saisie": vu["saisie"], "temoin": vu["temoin"], "etape": vu["etape"],
         "enregistre": vu.get("enregistre"), "devant": vu.get("devant")}
    t("app-guide-procedure")
    note, pourquoi = note_du_guide(pilote.cerveau(home), debut)
    t.check(note, pourquoi)
    d["note"] = note.name
    t("app-guide-modeles")
    try:
        modeles = (fake / "models").read_text(encoding="utf-8").split()
    except OSError:
        modeles = []
    d["modeles"] = modeles
    attendus = ["opus", "sonnet", "sonnet"] if prudent else ["opus", "opus"]
    t.check(modeles == attendus, "le faux Claude a été lancé avec %s (attendu : %s)" % (modeles, attendus))
    t("app-guide-disque")
    fragments = []
    for png in images.values():
        b64 = __import__("base64").b64encode(png)
        fragments += [png[len(png) // 2:len(png) // 2 + 64], b64[len(b64) // 2:len(b64) // 2 + 80]]
    for f in sorted((fake / "fragments").glob("*.json")) if (fake / "fragments").is_dir() else []:
        for x in json.loads(f.read_text(encoding="utf-8")):
            fragments += [bytes.fromhex(x["raw"]), x["b64"].encode("ascii")]
    t.check(len(fragments) >= 4 + 2 * len(modeles), "le faux Claude n'a pas dit ce qu'il a reçu : rien à chercher")
    racines = pilote.racines_traces(home)
    # Left out: the robot's own folders (its scripts, its captures of the screen, the repository it runs from)
    # and the fake Claude's records.
    traces = traces_du_guide(racines, debut, [Path(travail).parent, captures, fake, Path(__file__).resolve().parents[2]],
                             fragments, ("com.braininbox.installer", ".brain-in-a-box", "biab-guide", "ebwebview"))
    d["traces"] = [(montrer(p, home), why) for p, why in traces][:20]
    t.check(not traces, "des traces des images du guide sur le disque : %s" % "; ".join(
        "%s (%s)" % (p, why) for p, why in d["traces"][:5]))
    return d


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
PUBLIE = ("app", "engine/hooks/_agents.py", ".github/workflows/release.yml", ".github/ci/app-release-plan.py")
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


def archive_testee(kit, t, opts, sha, codex=False):
    """The tested commit's sources, as GitHub zips a branch, with a marker the robot checks after.
    codex: _agents.py's gate open on every system in this copy, each entry kept as it is otherwise."""
    work = kit.workdir(opts, "app-sources")
    marker = work / MARQUEUR
    marker.write_text(sha + "\n", encoding="utf-8")
    zip_path = work / "brain-in-a-box-terrain.zip"
    rc, out = kit.run(["git", "-C", kit.REPO, "archive", "--format=zip", "--prefix=brain-in-a-box-terrain/",
                       "--add-file=%s" % marker, "-o", zip_path, sha], timeout=300)
    t.check(rc == 0, "git archive des sources testées : %s" % out.strip()[-200:])
    if codex:
        gate = "brain-in-a-box-terrain/engine/hooks/_agents.py"
        opened = work / "brain-in-a-box-terrain-codex.zip"
        with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(opened, "w", zipfile.ZIP_DEFLATED) as dst:
            t.check(gate in src.namelist(), "les sources testées n'ont pas %s" % gate)
            for info in src.infolist():
                data = src.read(info)
                if info.filename == gate:
                    text = ouvrir_porte(data.decode("utf-8"))
                    t.check(text is not None, "la ligne de la porte a bougé dans _agents.py")
                    data = text.encode("utf-8")
                attr = info.external_attr
                dst.writestr(info, data)
                # zipfile writes 0o600 where git archive leaves no mode: the central directory says
                # what GitHub's zip says, mode for mode.
                dst.filelist[-1].external_attr = attr
        zip_path = opened
    return zip_path


def planter_sources(zip_path, home):
    """The sources of zip_path where the app keeps the ones it installs from (~/.brain-in-a-box/src), as a
    previous install left them, without the marker: the app reads the kit's gate there before installing,
    and the marker found after says it installed from the archive itself."""
    src = Path(home) / ".brain-in-a-box" / "src"
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            rel = info.filename.split("/", 1)[1] if "/" in info.filename else ""
            if not rel or rel == MARQUEUR or info.is_dir():
                continue
            dest = src.joinpath(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode and os.name != "nt":
                os.chmod(dest, mode)
    return src


def codex_installe(home, windows):
    """Where the app's step puts Codex: OpenAI's installer's place (codex.rs, find)."""
    if windows:
        local = Path(os.environ.get("LOCALAPPDATA") or Path(home) / "AppData" / "Local")
        return [local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"]
    return [Path(home) / ".local" / "bin" / "codex"]


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
    if sys.platform.startswith("linux"):
        return PiloteLinux(travail)
    raise ErreurPilote("pas encore de pilote de fenêtre pour %s" % sys.platform)


def outils(t, home, pilote, codex=False):
    """What the app's steps put on the machine, apart from the brain. codex: Codex in Claude Code's
    place, and no Claude Code."""
    t("app-outils")
    tools = pilote.outils(home)
    if codex:
        claude = [x for x in tools if x[0] == "Claude Code"]
        t.check(len(claude) == 1, "le pilote ne dit pas où serait Claude Code")
        shown = str(claude[0][1][0]).replace(str(home), "~")
        t.check(not any(Path(p).exists() for p in claude[0][1]), "Claude Code est installé alors que seul Codex est choisi (%s)" % shown)
        tools = [("Codex", pilote.codex_installe(home)) if x[0] == "Claude Code" else x for x in tools]
    for name, places in tools:
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


def journaux_systeme(pilote, details):
    """The system's own logs on a failure (PiloteLinux.journaux), never a failure of their own."""
    try:
        logs = pilote.journaux()
    except Exception as e:  # what is read on a failure must not hide that failure
        logs = {"erreur": "%s: %s" % (type(e).__name__, str(e)[:200])}
    if logs:
        details["journaux_systeme"] = logs


def clics_sans_souris(clics):
    """A reason when a click was not the mouse's (AXPress, Invoke, Toggle), else None."""
    autres = [c for c in clics if c.get("comment") != CLIC_SOURIS and not str(c.get("comment")).startswith(CLIC_SOURIS + " (")]
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
    codex = getattr(opts, "parcours", "app") == "app-codex"
    delai = getattr(opts, "delai_min", 0) or 0
    details = t.details
    details["source"] = source
    parcours = None
    try:
        t("app-preparation")
        t.check(source in SOURCES, "source inconnue « %s » (release ou build)" % source)
        pilote = pilote or pilote_du_systeme(kit.workdir(opts, "app"), opts)
        parcours = Parcours(t, pilote, captures, horloge, dormir, echeance=horloge() + delai * 60 if delai else None,
                            codex=codex)
        downloads = home / "Downloads"
        downloads.mkdir(exist_ok=True)
        extra = {}
        if source == "release":
            fichier = depuis_release(t, pilote, downloads, token, details, kit.tested_ref())
            details["sources_du_cerveau"] = "zip master de GitHub (comme un utilisateur)"
        else:
            fichier = depuis_build(t, opts.fichier, downloads, details)
        if source == "build" or codex:
            sha = kit.tested_ref()
            t.check(sha, "commit testé inconnu")
            archive = archive_testee(kit, t, opts, sha, codex)
            extra["BIAB_ARCHIVE"] = str(archive)
            details["sources_du_cerveau"] = "commit testé %s (BIAB_ARCHIVE)" % sha[:12]
            if codex:
                planter_sources(archive, home)
                details["sources_du_cerveau"] = ("commit testé %s, Codex ouvert dans cette copie (BIAB_ARCHIVE, et "
                                                 "~/.brain-in-a-box/src avant le premier lancement)" % sha[:12])
        details["fichier"] = fichier.name
        details["quarantaine_posee"] = pilote.marquer_telecharge(fichier)

        t("app-installation")
        t.notes.extend(pilote.installer(fichier, details, parcours.capturer))
        # What the app's steps must bring, taken away now (the harness needs nothing of it any more),
        # then the environment the app gets, read after.
        t("app-poste-neuf")
        poste = pilote.poste_neuf()
        if poste:
            details["poste_neuf"] = poste
            if poste.get("note"):
                t.notes.append(poste["note"])
        env = pilote.environnement(home, extra)

        details["duree_parcours_s"] = parcours.premier_lancement(env)
        t("app-fermeture")
        details["fermeture"] = pilote.fermer()
        t("app-nuit")
        nuit = pilote.ecarter_la_nuit()
        if nuit:
            details["nuit_reelle"] = nuit

        outils(t, home, pilote, codex)
        pilote.constats(t, details)
        t("app-sources")
        marker = home / ".brain-in-a-box" / "src" / MARQUEUR
        if source == "build" or codex:
            t.check(marker.is_file() and marker.read_text(encoding="utf-8").strip() == kit.tested_ref(),
                    "l'app n'a pas installé les sources du commit testé (BIAB_ARCHIVE ignoré ?)")
        else:
            t.check(not marker.exists(), "des sources du terrain traînent dans ~/.brain-in-a-box/src")
        kit.check_install(t, opts)

        parcours.relance(env)
        t("app-fermeture-relance")
        pilote.fermer()
        if not codex:
            # The guide: offered by the welcome now, walked with a fake Claude (no AI on these VMs).
            details["guide"] = guide_terrain(t, parcours, pilote, home, env, captures, kit.workdir(opts, "guide"))
        t("app-clics")
        sans = clics_sans_souris(getattr(pilote, "clics_installation", []) + parcours.clics)
        t.check(not sans, sans)

        # The nightly the app's install registered, started by the scheduler as on the first night.
        t("app-nuit-rendue")
        rendue = pilote.rendre_la_nuit()
        if rendue:
            details["nuit_rendue"] = rendue
        kit.run_nightly(t, opts)
        t.resume = "app %s installée, %s%d écrans cliqués à la souris jusqu'au cerveau en %d min, vérifications conformes, " \
                   "deuxième lancement : déjà installé%s, une nuit lancée par le planificateur%s" % (
                       details.get("release") or "construite", "Codex choisi seul, " if codex else "", len(parcours.ecrans),
                       details["duree_parcours_s"] // 60,
                       "" if codex else " ; guide : %s, l'étape entourée%s, la procédure dans Guides/, aucune image sur le disque" % (
                           CAPTURES_DITES.get(details["guide"]["capture"], CAPTURES_DITES["collage"]),
                           " en pointillés (Sonnet, mode prudent)" if details["guide"]["prudent"] else ""),
                       " ; " + details["git"]["resume"] if (details.get("git") or {}).get("resume") else "")
    except ErreurPilote as e:
        if parcours:
            parcours.sur_echec()
            details["install_log"] = journal_installation(home)
            journaux_systeme(pilote, details)
        t.check(False, parcours.raison(str(e)) if parcours else str(e))
    except kit.Echec as e:
        if parcours:
            parcours.sur_echec()
            details["install_log"] = journal_installation(home)
            journaux_systeme(pilote, details)
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
