#!/usr/bin/env python3
"""The brain's night check: finds what is wrong in the notes vault, fixes what
is safe by itself (one git commit per fix, each one undoable) and writes the
rest in Profile/controle.md for the user to look at.

    controle.py nuit|maintenant [--brain DIR] [--git GIT] [--sans-claude|--avec-claude]
    controle.py essai [--brain DIR]           what a check would do; writes nothing
    controle.py annuler <ID>|derniere-nuit [--nuit RUN] [--essai]
    controle.py annuler S1-... --remettre-le-secret    puts a masked secret back in its note
    controle.py secret-change S1-...          the user changed that secret: stop saying so
    controle.py statut | pause | reprendre | lecons
    controle.py gbrain [maintenant|reprendre]

Run by the nightly (nuit) and by the /controle skill. Never pushes, never
rewrites history, never follows a link, never writes outside the vault and its
own state folder. Exit code 0 unless it crashes.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _controle_rules as rules  # noqa: E402
import _controle_judge as judge  # noqa: E402

ENV_HELP = """environment (all optional):
  BRAIN_DIR            the vault (default ~/Documents/Brain); --brain wins
  BRAIN_CONTROLE_DIR   the state folder (default ~/.brain-in-a-box/controle); --etat wins
  GBRAIN_HOME          the folder that holds .gbrain (default the home folder)
  CLAUDE_BIN           the claude executable of the review (default ~/.local/bin/claude, else PATH)
"""
RUN_ID = re.compile(r"^\d{8}-\d{4}(-\d{1,2})?$")
# S2-261014-02: type, day (with its year: an id never comes back), rank in the day.
ACTION_ID = re.compile(r"^[A-Z][0-9]-\d{6}-\d{2,3}$")
TRAILER = re.compile(r"^Brain-Controle-(Run|Action|Masque|Annule): (\S+)$", re.M)
MASK_ID = re.compile(r"\[secret masqué par le contrôle · (S1-\d{6}-\d{2,3})\]")
IDENT = ["-c", "user.email=brain@local", "-c", "user.name=brain"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EDIT_GUARD = 600
RUN_BUDGET = 20 * 60
KEEP_RUNS = 30
INDEX_DAYS = 400
QUARANTINE_DAYS = 30
FILES_PER_NIGHT = 40
CAPS = {"S1": 100, "S2": 30, "X1": 20, "M2": 200, "D1": 20, "D2": 20, "F1": 20, "C1": 10, "C2": 3, "C3": 50}
S1_FILES = 40
BREAKER_SHARE, BREAKER_MIN = 0.30, 10
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]
VERDICT = {"vert": ("🟢", "tout va bien"), "orange": ("🟠", "à regarder"), "rouge": ("🔴", "à traiter")}
SECRET_KINDS = {
    "aws": "clé AWS", "github": "jeton GitHub", "ia": "clé d'IA", "stripe": "clé Stripe", "slack": "jeton Slack",
    "google": "clé Google", "telegram": "jeton Telegram", "jwt": "jeton de connexion", "discord": "lien secret Discord",
    "url": "mot de passe dans une adresse", "bearer": "jeton d'accès", "mot_de_passe": "mot de passe",
    "cle_privee": "clé privée", "service": "clé d'un service en ligne", "connu": "secret déjà repéré ailleurs",
}
REPORT_TEXT = {
    "S1": "J'ai masqué {n} secret{s} écrit{s} en clair ({kinds}).",
    "S1_hist": " Déjà enregistré dans l'historique : **change-le** là où il a été créé.",
    "S2": "J'ai mis un avertissement au-dessus d'une commande qui efface ou écrase. La commande n'a pas changé.",
    "S2_n": "J'ai mis un avertissement au-dessus de {n} commandes qui effacent ou écrasent. Elles n'ont pas changé.",
    "X1_chars": "J'ai retiré {n} caractère{s} invisible{s} qui pouvai{ent} cacher des consignes.",
    "X1_callout": "J'ai mis un avertissement en tête de ce texte venu de l'extérieur : il contient des phrases "
                  "adressées à une IA.",
    "M1": "J'ai rangé {n} vieux bloc{s} de ta mémoire dans l'archive. Rien n'est perdu ni résumé. "
          "memory.md : {avant} → {apres}.",
    "M2": "J'ai mis de côté {n} leçon{s} en double ou recopiée{s}{sections}. lessons.md : {avant} → {apres}.",
    "D1": "J'ai supprimé une copie exacte de [[{canon}]].",
    "D2": "J'ai supprimé une note vide, sans lien vers elle depuis plus de 30 jours.",
    "F1": "J'ai réparé une ligne de l'en-tête qui empêchait de lire la note.",
    "C1": "J'ai signalé une contradiction : cette note dit « {quote} », [[{new}]] (plus récente) dit le contraire.",
    "C2": "J'ai marqué une décision comme remplacée par une plus récente.",
    "C3": "J'ai mis de côté {n} leçon{s} que Claude a jugée{s} inutile{s} ou en double.",
    "echec": "Cette correction n'a pas donné le résultat prévu : j'ai remis le fichier comme avant.",
}
POINT_TEXT = {
    "secret_incertain": "[[{n}]] contient peut-être un mot de passe ou une clé ({x} endroit{s}). Je n'en suis pas "
                        "sûr, alors je n'y ai pas touché : regarde toi-même.",
    "commande_consignes": "[[{n}]] contient une commande qui efface ou écrase. Je ne modifie pas les fichiers de "
                          "consignes : regarde-la toi-même.",
    "piege_phrases": "J'ai laissé de côté [[{n}]] : elle contient des phrases qui donnent des ordres à une IA.",
    "piege_invisibles": "[[{n}]] contient des caractères invisibles. Je n'y ai pas touché.",
    "entete_ouvert": "L'en-tête de [[{n}]] n'est pas fermé : ajoute une ligne --- à sa fin.",
    "entete_illisible": "Une ligne de l'en-tête de [[{n}]] est illisible et je n'ai pas su la réparer sans risque.",
    "consignes_grosses": "[[{n}]] fait {x} : un fichier de consignes aussi gros n'est lu qu'en partie. Allège-le.",
    "doublon_profil": "[[{n}]] et [[{m}]] ont exactement le même contenu. Je ne supprime rien dans Profile/ ni "
                      "Decisions/ : choisis laquelle garder.",
    "doublon_lie": "[[{n}]] est une copie exacte de [[{m}]], mais d'autres notes pointent vers elle : je l'ai gardée.",
    "secret_cache": "Le fichier caché {rel} contient {x} secret{s} écrit{s} en clair. Je ne modifie pas les fichiers "
                    "cachés : retire-le{s} toi-même (le cerveau est sauvegardé en ligne).",
    "fichier_secret": "Le fichier {rel} ressemble à un fichier de secrets. S'il contient une vraie clé, sors-le du "
                      "cerveau : il est sauvegardé en ligne.",
    "dossiers_casse": "Les dossiers {x} existent tous les deux : tes notes sont indexées deux fois. À regrouper à la "
                      "main (/controle t'explique comment).",
    "chemin_absent": "[[{n}]] parle de {x}, qui n'existe pas sur cet ordinateur.",
    "version_differente": "[[{n}]] dit {x}.",
    "plafond": "Il reste des corrections « {x} » : la suite la nuit prochaine.",
    "temps": "Le contrôle a atteint sa durée maximale : la suite la nuit prochaine.",
    "juge_secret": "J'ai laissé [[{n}]] hors de la relecture par Claude : elle contient peut-être encore un secret.",
}
ALERT_TEXT = {
    "contrat": "La correction {x} n'a pas donné le résultat prévu : j'ai remis le fichier comme avant.",
    "secret_historique": "{x} secret{s} masqué{s} étai{ent} déjà enregistré{s} dans l'historique du cerveau ({ids}) : "
                         "change-le{s} là où il{s} {a} été créé{s}. Quand c'est fait, dis-le à Claude (/controle) et "
                         "je n'en parlerai plus.",
    "memoire_grosse": "memory.md fait encore {x} : il faut l'alléger.",
    "lecons_grosses": "lessons.md reste gros ({x}) : tape « /controle leçons » pour regrouper tes leçons en "
                      "quelques règles.",
    "disjoncteur": "Trop de corrections « {x} » d'un coup : je n'en ai fait aucune, par prudence.",
    "retrograde": "Tu as souvent annulé les corrections « {x} » : je ne les fais plus, je les signale seulement.",
    "rapport_seul": "Je n'ai rien corrigé : {x}.",
    "commit_refuse": "git a refusé un commit (hook ou signature) : j'ai arrêté les corrections.",
    "attente_installeur": "Une version modifiée d'un script de nuit est en attente de ton accord : le contrôle et "
                          "la mise à jour de gbrain ne sont peut-être pas branchés.",
    "partiel": "Plus de 10 000 notes : je n'ai regardé que les 10 000 premières.",
    "reflexion": "La réflexion du jour échoue depuis le {x}.",
    "gbrain": "{x}",
    "claude": "{x}",
}
# Why the review was skipped, when the report should say it (orange).
CLAUDE_SKIP = {
    "absent": "Relecture par Claude impossible : Claude Code est introuvable sur cet ordinateur.",
    "version": "Relecture par Claude impossible : Claude Code est trop ancien, mets-le à jour.",
    "options": "Relecture par Claude impossible : cette version de Claude Code n'a pas les protections nécessaires.",
    "connexion": "Relecture par Claude impossible : Claude Code n'est pas connecté à ton compte.",
    "abonnement": "Relecture par Claude sautée : Claude Code est branché sur une clé payante, je ne l'utilise "
                  "qu'avec ton abonnement.",
    "limite": "Claude indisponible cette nuit (limite d'usage).",
    "limite-reflexion": "Relecture par Claude sautée : la réflexion du jour a déjà touché la limite d'usage.",
    "quota-jour": "Relecture par Claude sautée : déjà 3 relectures aujourd'hui.",
    "demarrage": "Relecture par Claude ignorée : Claude n'a pas démarré avec les protections demandées.",
    "reponse": "Relecture par Claude ignorée : sa réponse n'était pas conforme.",
    "delai": "Relecture par Claude abandonnée : pas de réponse à temps.",
    "erreur": "Relecture par Claude abandonnée : le service de Claude a renvoyé une erreur.",
    "lancement": "Relecture par Claude impossible : Claude Code n'a pas pu être lancé.",
    "erreur-interne": "Relecture par Claude abandonnée après une erreur du contrôle.",
    "suspect": "Claude a repéré un texte qui lui donne des ordres : aucune correction venant de lui cette nuit.",
}
CAUSES = {
    "pas_de_git": "le cerveau n'est pas un dépôt git", "git_introuvable": "git est introuvable",
    "fusion": "une fusion git est en cours", "rebase": "un rebase git est en cours",
    "detachee": "git n'est sur aucune branche (HEAD détachée)", "occupe": "git est occupé (verrou récent)",
    "verrou_git": "un verrou git est resté (le nightly le retire)", "sous_dossier": "le cerveau est dans un autre dépôt git",
    "commit_refuse": "le commit de tes notes a été refusé (hook ou signature)",
    "pause": "le contrôle est en pause (dis « /controle reprendre » pour relancer)",
    "sans_commit": "le dépôt git n'a encore aucun commit",
}


# --------------------------------------------------------------------------- small helpers

def home():
    return Path(os.path.expanduser("~"))


def state_dir(opts):
    return Path(opts.etat or os.environ.get("BRAIN_CONTROLE_DIR") or home() / ".brain-in-a-box" / "controle")


def gbrain_dir():
    return Path(os.environ.get("GBRAIN_HOME", "").strip() or home()) / ".gbrain"


def vault_dir(opts):
    return Path(os.path.realpath(opts.brain or os.environ.get("BRAIN_DIR") or home() / "Documents" / "Brain"))


def plural(n, word="s"):
    return word if n > 1 else ""


def safe_name(name):
    return re.sub(r"[\[\]|\n\r]", " ", rules.HIDDEN.sub("", name)).strip() or "?"


def link(rel):
    return "[[%s]]" % safe_name(rules.note_name(rel))


def date_fr(d):
    return "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)


def parse_now(value):
    if not value:
        return datetime.now().replace(microsecond=0)
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (d.astimezone().replace(tzinfo=None) if d.tzinfo else d).replace(microsecond=0)


def run_key(run):
    """Runs in the order they ran: 20261014-0400-2 after 20261014-0400."""
    return run[:13], int(run[14:] or 1)


def older_than(entry, limit):
    """True when a quarantine entry was made before limit (or says nothing of when)."""
    try:
        return datetime.fromisoformat(str(entry["le"])) < limit
    except (KeyError, TypeError, ValueError):
        return True


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def write_private(path, data):
    """Writes bytes atomically in a 0600 file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path, data):
    write_private(path, (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


class Lock:
    """run.lock held with the system's file lock (flock, or msvcrt on Windows): it
    goes with its holder, killed or crashed, and two runs can never both take it
    over. The file stays; it says who holds it, for the user."""

    def __init__(self, path):
        self.path, self.held, self.f = str(path), False, None

    def acquire(self):
        try:
            f = open(self.path, "a+b")
            os.chmod(self.path, 0o600)
        except OSError:
            return False
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        f.seek(0)
        f.truncate()
        f.write(b"%d %d\n" % (os.getpid(), int(time.time())))
        f.flush()
        self.f, self.held = f, True
        return True

    def release(self):
        if self.held:
            self.f.close()
            self.f, self.held = None, False


class Git:
    def __init__(self, binary, cwd):
        self.binary, self.cwd = binary, str(cwd)

    def run(self, *args, data=None, timeout=60):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1", GIT_EDITOR="true")
        try:
            p = subprocess.run([self.binary, "-C", self.cwd] + list(args), input=data, capture_output=True,
                               timeout=timeout, env=env, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return 124, b"", b"timeout"
        except OSError as e:
            return 127, b"", str(e).encode()
        return p.returncode, p.stdout, p.stderr

    def ok(self, *args, **kw):
        return self.run(*args, **kw)[0] == 0

    def text(self, *args, **kw):
        rc, out, _ = self.run(*args, **kw)
        return out.decode("utf-8", "replace") if rc == 0 else None

    def commit(self, message, paths=None):
        args = IDENT + ["commit", "-q", "-F", "-"]
        if paths:
            args += ["--"] + list(paths)
        return self.ok(*args, data=message.encode("utf-8"))


def find_git(opts):
    return opts.git or shutil.which("git")


def preflight(vault, git):
    """None when fixes may be made, else the cause (key of CAUSES)."""
    if git is None:
        return "git_introuvable"
    top = git.text("rev-parse", "--show-toplevel")
    if top is None:
        return "pas_de_git"
    if os.path.realpath(top.strip()) != os.path.realpath(str(vault)):
        return "sous_dossier"
    gitdir = (git.text("rev-parse", "--absolute-git-dir") or "").strip()
    if not gitdir:
        return "pas_de_git"
    if any(os.path.exists(os.path.join(gitdir, f)) for f in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")):
        return "fusion"
    if any(os.path.exists(os.path.join(gitdir, f)) for f in ("rebase-merge", "rebase-apply")):
        return "rebase"
    lock = os.path.join(gitdir, "index.lock")
    if os.path.exists(lock):
        return "occupe" if time.time() - os.path.getmtime(lock) < EDIT_GUARD else "verrou_git"
    if not git.ok("symbolic-ref", "-q", "HEAD"):
        return "detachee"
    return None


def status_paths(git):
    """Paths with changes not committed yet (modified, added or untracked), deletions excluded."""
    rc, out, _ = git.run("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if rc != 0:
        return None
    paths, items, i = [], out.decode("utf-8", "surrogateescape").split("\0"), 0
    while i < len(items):
        item = items[i]
        i += 1
        if len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        if "R" in xy or "C" in xy:
            i += 1
        if "D" not in xy:
            paths.append(path)
    return paths


def atomic_write(vault, rel, data, mode=None):
    path = safe_path(vault, rel)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    if not rules.inside(os.path.realpath(parent), str(vault)):
        raise OSError("outside the vault")
    fd, tmp = tempfile.mkstemp(prefix=".controle-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mode is None:
            mask = os.umask(0)
            os.umask(mask)
            mode = 0o666 & ~mask
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_path(vault, rel):
    """Absolute path of rel, refused when it leaves the vault, goes through a dot
    folder or through a symbolic link."""
    parts = rel.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        raise OSError("refused path")
    cur = str(vault)
    for p in parts:
        cur = os.path.join(cur, p)
        if os.path.islink(cur):
            raise OSError("link in path")
    return cur


# --------------------------------------------------------------------------- state

class State:
    def __init__(self, path):
        self.dir = Path(path)

    def ensure(self):
        for d in (self.dir, self.dir / "runs", self.dir / "quarantaine"):
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(str(d), 0o700)
            except OSError:
                pass

    def load(self, name, default):
        return read_json(self.dir / name, default)

    def save(self, name, data):
        write_json(self.dir / name, data)

    def key(self, create=True):
        path = self.dir / "cle"
        try:
            with open(str(path), "rb") as f:
                key = f.read()
            if len(key) == 32:
                return key
        except OSError:
            pass
        if not create:
            return secrets.token_bytes(32)
        key = secrets.token_bytes(32)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        except FileExistsError:
            return self.key(create=False)
        return key

    def runs(self):
        return sorted((p for p in (self.dir / "runs").glob("*.json") if RUN_ID.match(p.stem)),
                      key=lambda p: run_key(p.stem))

    def journals(self):
        """Run journals, oldest first."""
        out = []
        for p in self.runs():
            j = read_json(p, {})
            if j.get("run") == p.stem:
                out.append(j)
        return out

    def quarantined(self):
        """(file, {id: entry}) of each quarantine file."""
        for p in sorted((self.dir / "quarantaine").glob("*.json")):
            actions = read_json(p, {}).get("actions")
            yield p, {k: v for k, v in actions.items() if isinstance(v, dict)} if isinstance(actions, dict) else {}

    def purge(self, now, live=()):
        """Keeps 30 run journals; the fingerprints of their fixes outlive them in
        empreintes.json, so that an undo stays an undo. A masked value stays in
        quarantine 30 days, and as long as its mask is in a note: never committed,
        it may be the only copy left (`live`: the ids of the masks in the vault)."""
        old = self.runs()[:-KEEP_RUNS]
        if old:
            index = self.load("empreintes.json", {})
            for p in old:
                for a in read_json(p, {}).get("actions") or []:
                    if isinstance(a, dict) and a.get("statut") == "fait" and ACTION_ID.match(str(a.get("id"))):
                        index[a["id"]] = {"type": a.get("type"), "run": p.stem, "empreintes": a.get("empreintes") or []}
            cutoff = (now - timedelta(days=INDEX_DAYS)).strftime("%y%m%d")
            self.save("empreintes.json", {k: v for k, v in index.items() if k.split("-")[1] >= cutoff})
        for p in old:
            try:
                p.unlink()
            except OSError:
                pass
            shutil.rmtree(str(p.with_suffix("")), ignore_errors=True)
        limit = now - timedelta(days=QUARANTINE_DAYS)
        for p, actions in self.quarantined():
            keep = {aid: q for aid, q in actions.items() if aid in live or not older_than(q, limit)}
            try:
                if keep and keep != actions:
                    write_json(p, {"run": p.stem, "actions": keep})
                elif not keep and (actions or p.stat().st_mtime < limit.timestamp()):
                    p.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- the run

class Run:
    judge_command = None              # tests: a stub instead of claude, as a list
    judge_timeout = judge.ASK_TIMEOUT

    def __init__(self, opts, mode):
        self.opts, self.mode = opts, mode
        self.now = parse_now(opts.maintenant)
        self.today = self.now.date()
        self.vault = vault_dir(opts)
        self.state = State(state_dir(opts))
        self.writes = mode != "essai"
        self.t0 = time.time()
        self.actions, self.would, self.skipped = [], [], []
        self.points, self.alerts = [], []
        self.touched, self.quarantine = set(), {}
        self.cause, self.claude = None, "saute:sans-claude"
        self.judge_info, self.judge_updates = {}, {}
        self.judge_verdict = self.judge_dossier = None
        self.lessons_kept_open = set()
        self.to_change = []
        self.explanations = []
        self.leftover = set()
        self.notes_count = 0
        self.exclusions = []
        self.gbrain_version = None

    # ---- identifiers and fingerprints

    def fp(self, *parts):
        return hmac.new(self.key, "\0".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()

    def new_run_id(self):
        base = self.now.strftime("%Y%m%d-%H%M")
        taken = {j.get("run") for j in self.state.journals()}
        run, n = base, 1
        while run in taken or (self.state.dir / "runs" / (run + ".json")).exists():
            n += 1
            run = "%s-%d" % (base, n)
        return run

    def first_seq(self):
        day = self.today.strftime("%y%m%d")
        used = [0]
        for j in self.state.journals():
            if str(j.get("run", "")).startswith(self.now.strftime("%Y%m%d")):
                used += [int(a["id"].rsplit("-", 1)[1]) for a in j.get("actions", [])
                         if ACTION_ID.match(a.get("id", "")) and a["id"].split("-")[1] == day]
        if self.git:
            log = self.git.text("log", "--since=3 days ago", "--format=%B", "-E",
                                "--grep=^Brain-Controle-(Action|Masque|Annule): ") or ""
            for kind, value in TRAILER.findall(log):
                if ACTION_ID.match(value) and value.split("-")[1] == day:
                    used.append(int(value.rsplit("-", 1)[1]))
        return max(used) + 1

    def next_id(self, t):
        aid = "%s-%s-%02d" % (t, self.today.strftime("%y%m%d"), self.seq)
        self.seq += 1
        return aid

    # ---- main flow

    def execute(self):
        if not self.vault.is_dir():
            self.alert("rouge", "vault_absent")
            return self.finish(scan=None)
        profile = next((p for p in ("Profile", "Profil") if (self.vault / p).is_dir()), "Profile")
        if self.writes:
            self.state.ensure()
        self.key = self.state.key(create=self.writes)
        self.run = self.new_run_id()
        self.refused = self.state.load("refusees.json", {})
        self.white = self.state.load("blanche.json", {})
        self.paused = (self.state.dir / "pause").exists()
        binary = find_git(self.opts)
        self.git = Git(binary, self.vault) if binary else None
        self.cause = preflight(self.vault, self.git)
        git_usable = self.cause is None
        if git_usable and not self.git.ok("rev-parse", "--verify", "-q", "HEAD") and not status_paths(self.git):
            self.cause, git_usable = "sans_commit", False
        self.git_ok = git_usable
        if self.cause is None and self.paused:
            self.cause = "pause"
        self.can_fix = self.cause is None and self.writes
        self.seq = self.first_seq()
        self.ctx = rules.Context(self.today, profile, now_ts=self.now.timestamp(),
                                 allowed_secret=lambda v: self.fp("valeur", v) in self.white)
        self.ctx.read = lambda rel: rules.read_note(str(self.vault), rel)
        # gbrain's keys and what earlier nights quarantined are masked wherever they show up again.
        self.ctx.secret_values = {v for v in self.known_values() if rules.propagable(v)}
        self.masked_ids = []
        if self.can_fix:
            self.secrets_phase()
            if not self.commit_notes():
                self.cause, self.can_fix = "commit_refuse", False
        if self.cause:
            self.alert("orange", "rapport_seul", CAUSES[self.cause])
        scan = rules.scan_vault(str(self.vault))
        self.notes_count = len(scan.notes)
        self.exclusions = [{"chemin": rel, "cause": cause} for rel, cause in scan.skipped] + \
            [{"chemin": rel, "cause": "lien"} for rel in scan.symlinks]
        self.snapshot = dict(scan.by_rel)
        self.tracked = set()
        if self.git_ok:
            self.git_facts()
        an = rules.analyze(scan, self.ctx)
        self.points += an.points
        for level, code, x in an.alerts:
            self.alert(level, code, x)
        self.extra_checks(scan, profile)
        try:
            self.judge_verdict, self.claude = self.ask_judge(an, scan)
            an.plans.update(self.plan_judge_actions(self.judge_verdict))
        except Exception as e:  # the deterministic fixes still run
            self.judge_info["erreur"] = type(e).__name__
            self.judge_verdict, self.claude = None, "saute:erreur-interne"
            an.plans.update({"C1": [], "C2": [], "C3": []})
        why = self.claude.split(":", 1)[1] if self.claude.startswith("saute:") else None
        if why in CLAUDE_SKIP:
            self.alert("orange", "claude", CLAUDE_SKIP[why])
        elif self.judge_verdict is not None and self.judge_verdict.suspect:
            self.alert("orange", "claude", CLAUDE_SKIP["suspect"])
        self.demoted = self.demoted_types() if self.git_ok else set()
        self.apply_all(an)
        self.after_judge()
        return self.finish(scan)

    def alert(self, level, code, x=None):
        self.alerts.append((level, code, x))

    def over_time(self):
        return time.time() - self.t0 > RUN_BUDGET

    # ---- phase 1: secrets in notes not committed yet

    def secrets_phase(self):
        paths = status_paths(self.git) or []
        files = 0
        for rel in paths:
            if not rel.lower().endswith(".md") or any(p.startswith(".") for p in rel.split("/")):
                continue
            note = rules.read_note(str(self.vault), rel)
            if note is None:
                continue
            targets = [t for t in rules.detect_s1(note, self.ctx) if self.fp("S1", rel, t.key) not in self.refused]
            if not targets:
                continue
            left = CAPS["S1"] - sum(a["unites"] for a in self.actions if a["type"] == "S1")
            if files >= S1_FILES or left <= 0:
                self.leftover.add("S1")
                break
            aid = self.next_id("S1")
            change = rules.make_s1(note, targets, self.ctx, aid, limit=left)
            if self.write_change("S1", aid, note, change, targets, commit=False):
                files += 1
                self.masked_ids.append(aid)

    def commit_notes(self):
        if not status_paths(self.git):
            return True
        if not self.git.ok("add", "-A"):
            return False
        day = self.today.isoformat()
        msg = "nightly %s" % day if self.mode == "nuit" else "notes du %s (avant contrôle)" % day
        if self.masked_ids:
            msg += "\n\n" + "\n".join("Brain-Controle-Masque: " + i for i in self.masked_ids)
        return self.git.commit(msg + "\n")

    # ---- git facts

    def git_facts(self):
        rc, out, _ = self.git.run("ls-files", "-z")
        if rc == 0:
            self.tracked = {rules.nfc(p) for p in out.decode("utf-8", "surrogateescape").split("\0") if p}
        log = self.git.text("-c", "core.quotepath=off", "log", "--no-renames", "--name-only", "--format=@@%ct")
        last, first, when = {}, {}, None
        for line in (log or "").split("\n"):
            if line.startswith("@@"):
                try:
                    when = int(line[2:])
                except ValueError:
                    when = None
            elif line and when is not None:
                last.setdefault(rules.nfc(line), when)
                first[rules.nfc(line)] = when
        self.ctx.last_commit, self.ctx.first_commit = last, first

    def demoted_types(self):
        """Types undone at least 5 times and 30 % of the time over 60 days: report only."""
        since = (self.now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
        log = self.git.text("log", "--since=" + since, "--format=%B", "-E",
                            "--grep=^Brain-Controle-(Action|Masque|Annule): ") or ""
        done, undone = {}, {}
        for kind, value in TRAILER.findall(log):
            if not ACTION_ID.match(value):
                continue
            t = value[:2]
            bucket = undone if kind == "Annule" else done if kind in ("Action", "Masque") else None
            if bucket is not None:
                bucket[t] = bucket.get(t, 0) + 1
        return {t for t, n in undone.items() if n >= 5 and n >= 0.3 * max(done.get(t, 0), n)}

    # ---- checks the catalogue only reports

    def extra_checks(self, scan, profile):
        if scan.partial:
            self.alert("orange", "partiel")
        if self.tracked:
            for pair in rules.case_collisions(self.tracked):
                self.points.append(("dossiers_casse", pair[0], " et ".join(p + "/" for p in pair)))
        h = str(home())
        for rel in (profile + "/stack.md", "CLAUDE.md"):
            note = scan.by_rel.get(rel)
            if note is None:
                continue
            for p in rules.home_paths_missing(note, h)[:5]:
                self.points.append(("chemin_absent", rel, p))
        stack = scan.by_rel.get(profile + "/stack.md")
        if stack is not None:
            actual = self.tool_versions()
            for tool, written in rules.written_versions(stack).items():
                if actual.get(tool) and actual[tool] != written:
                    self.points.append(("version_differente", stack.rel,
                                        "%s %s, mais c'est %s qui est installé" % (tool, written, actual[tool])))
        pending = home() / ".brain-in-a-box" / "pending"
        try:
            if pending.is_dir() and any(pending.iterdir()):
                self.alert("rouge", "attente_installeur")
        except OSError:
            pass

    def tool_versions(self):
        exe = ".exe" if os.name == "nt" else ""
        out = {}
        for tool, path in (("bun", home() / ".bun" / "bin" / ("bun" + exe)),
                           ("claude", home() / ".local" / "bin" / ("claude" + exe))):
            if not path.is_file():
                continue
            env = {k: v for k, v in os.environ.items()
                   if not any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
            try:
                p = subprocess.run([str(path), "--version"], capture_output=True, timeout=20, env=env,
                                   stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
                m = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", p.stdout.decode("utf-8", "replace"))
                if m:
                    out[tool] = m.group(1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        pkg = read_json(home() / "DEV" / "gbrain" / "package.json", {})
        if isinstance(pkg.get("version"), str):
            out["gbrain"] = pkg["version"]
        return out

    # ---- the Claude review (_controle_judge.py)

    def ask_judge(self, an, scan):
        """Builds the dossier, asks Claude without any tool, validates every
        quote. Returns (verdict or None, status for etat.json)."""
        if self.opts.sans_claude:
            return None, "saute:sans-claude"
        if self.mode == "essai" and not self.opts.avec_claude:
            return None, "saute:essai"
        if self.paused or not self.git_ok:
            return None, "saute:pause" if self.paused else "saute:sans-git"
        state = self.state.load("juge.json", {})
        calls = state.get("appels") if isinstance(state.get("appels"), dict) else {}
        n_calls = calls.get("n") if calls.get("jour") == self.today.isoformat() and isinstance(calls.get("n"), int) \
            else 0
        reflection = home() / ".claude" / "logs" / "daily-reflection.state.json"
        try:
            age = time.time() - os.path.getmtime(str(reflection))
        except OSError:
            age = None
        eligible = [n for n in scan.notes if judge.eligible(n)]
        anchors = self.changed_since(state.get("dernier_commit_juge"))
        lessons, left_out = self.lesson_pool(scan, an)
        ok, why = judge.should_ask(
            calls_today=n_calls, reflection_error=str(read_json(reflection, {}).get("last_error") or ""),
            reflection_age=age, lessons_left=bool(lessons), sunday=self.today.weekday() == 6,
            changed=any(anchors is None or rules.nfc(n.rel) in anchors for n in eligible))
        if not ok:
            return None, "saute:" + why
        dossier = judge.build_dossier(eligible, self.ctx.profile, self.today, anchors, self.note_day,
                                      self.known_values(), lessons, left_out)
        self.judge_dossier = dossier
        for rel, cause, sha in dossier.excluded:
            if cause == "secret":
                self.points.append(("juge_secret", rel, sha))
        self.judge_info = {"extraits": len(dossier.excerpts), "lecons_envoyees": len(dossier.lessons),
                           "notes_exclues": len(dossier.excluded), "dossier_sha256": dossier.sha256}
        if not dossier.excerpts and not dossier.lessons:
            return None, "saute:rien-de-neuf"
        git_path = self.git.binary
        command = self.judge_command or judge.default_command()
        ok, why = judge.available(command, git_path)
        if not ok:
            return None, "saute:" + why
        if self.writes:
            state["appels"] = {"jour": self.today.isoformat(), "n": n_calls + 1}
            self.state.save("juge.json", state)
            run_dir = self.state.dir / "runs" / self.run
            write_private(run_dir / "dossier.txt", dossier.text.encode("utf-8"))
            os.chmod(str(run_dir), 0o700)
            self.judge_info["dossier_garde"] = True
        out = judge.ask(dossier.text, judge.schema(dossier.excerpts, dossier.lessons), command,
                        timeout=self.judge_timeout, git_path=git_path)
        self.judge_info.update(version=out.version, cout=None if out.cost is None else round(out.cost, 4),
                               tours=out.turns, duree_s=out.secs,
                               cause=out.cause, detail=judge.mask_text(out.detail) if out.detail else None)
        if out.cause:
            return None, "saute:" + out.cause
        try:
            verdict = judge.validate(out.answer, dossier, self.refused_finding)
        except judge.Rejected as e:
            self.judge_info["rejet"] = str(e)
            return None, "saute:reponse"
        self.judge_info.update(constats=len(verdict.findings), lecons=len(verdict.lessons),
                               abandons=dict(verdict.dropped), suspect=verdict.suspect)
        return verdict, "fait"

    def changed_since(self, sha):
        """Paths changed since the last review; never reviewed: those committed in
        the last 7 days (all of them would only show the newest few notes)."""
        if not (isinstance(sha, str) and re.match(r"^[0-9a-f]{40}$", sha)
                and self.git.ok("cat-file", "-e", sha + "^{commit}")):
            week = (self.now - timedelta(days=7)).timestamp()
            return {p for p, t in self.ctx.last_commit.items() if t >= week} | \
                {rules.nfc(p) for p in status_paths(self.git) or []}
        rc, out, _ = self.git.run("-c", "core.quotepath=off", "diff", "--name-only", "-z", sha, "HEAD")
        if rc != 0:
            return None
        names = {rules.nfc(p) for p in out.decode("utf-8", "surrogateescape").split("\0") if p}
        return names | {rules.nfc(p) for p in status_paths(self.git) or []}

    def lesson_key(self, text):
        return self.fp("lecon", rules._norm_lesson(text))

    def lesson_pool(self, scan, an):
        note = scan.by_rel.get(rules.lessons_path(self.ctx))
        if note is None:
            return [], []
        spans = [(t.data[1], t.data[2]) for rel, ts in an.plans.get("M2") or [] if rel == note.rel for t in ts]
        return judge.lesson_candidates(note, spans, self.state.load("lecons-vues.json", {}), self.lesson_key)

    def note_day(self, note):
        d = rules.note_date(note)
        ts = self.ctx.last_commit.get(rules.nfc(note.rel))
        return d or (date.fromtimestamp(ts) if ts else None)

    def known_values(self):
        """Exact values masked in the dossier: gbrain's keys and what S1 quarantined."""
        values = set()

        def walk(v, key=""):
            if isinstance(v, dict):
                for k, x in v.items():
                    walk(x, str(k))
            elif isinstance(v, list):
                for x in v:
                    walk(x, key)
            elif isinstance(v, str) and any(w in key.lower() for w in ("key", "token", "secret", "password")):
                values.add(v)

        walk(read_json(gbrain_dir() / "config.json", {}))
        for p in (self.state.dir / "quarantaine").glob("*.json"):
            actions = read_json(p, {}).get("actions")
            for a in (actions.values() if isinstance(actions, dict) else ()):
                for item in (a.get("lignes") or []) if isinstance(a, dict) else ():
                    if isinstance(item, list) and len(item) == 3:
                        values.update(judge.masked_values(str(item[1]), str(item[2])))
        return sorted(values)

    def refused_finding(self, f):
        rels = [f.a.rel] + ([f.b.rel] if f.b else [])
        return any(self.fp(t, rel, f.key) in self.refused for t in ("C1", "C2") for rel in rels)

    def side_date(self, note, quote):
        """The note's own date, else when the quoted paragraph was last committed."""
        d = rules.note_date(note)
        if d:
            return d
        span = rules.locate_quote(note, quote)
        if span is None:
            return None
        out = self.git.text("blame", "--porcelain", "-L", "%d,%d" % (span[0] + 1, span[1] + 1), "--", note.rel)
        times = [int(x) for x in re.findall(r"^committer-time (\d+)$", out or "", re.M)]
        return date.fromtimestamp(max(times)) if times else None

    def plan_judge_actions(self, verdict):
        """C1 (contradiction), C2 (decision replaced) and C3 (lesson set aside)
        from the review's validated findings; everything else goes to the report."""
        self.ctx.judge = {"C1": {}, "C2": {}, "C3": {}}
        if verdict is not None:
            for f in verdict.findings:
                self.plan_finding(f, verdict.suspect)
            if not verdict.suspect:
                self.plan_lessons(verdict.lessons)
        return {t: sorted(self.ctx.judge[t].items()) for t in ("C1", "C2", "C3")}

    def plan_finding(self, f, suspect):
        if suspect or f.b is None or f.kind in ("proche_doublon", "perime") or f.current == "inconnu":
            return self.judge_point(f)
        new, old = (f.a, f.b) if f.current == "a" else (f.b, f.a)
        q_new, q_old = (f.quote_a, f.quote_b) if f.current == "a" else (f.quote_b, f.quote_a)
        n_old, n_new = self.snapshot.get(old.rel), self.snapshot.get(new.rel)
        if n_old is None or n_new is None or old.rel == new.rel:
            return self.judge_point(f)
        if f.kind == "decision_remplacee":
            t = "C2"
            d_old, d_new = rules.note_date(n_old), rules.note_date(n_new)
            ok = f.confidence == "haute" and rules.in_decisions(new.rel)
        else:
            t = "C1"
            d_old, d_new = self.side_date(n_old, q_old), self.side_date(n_new, q_new)
            ok = f.confidence in ("haute", "moyenne") and rules.locate_quote(n_new, q_new) is not None
        if rules.annotated(n_old, q_old, t):
            return  # flagged on an earlier night: the callout is already in the note
        if ok and d_old and d_new and d_new > d_old:
            target = rules.Target(f.key, {"new_rel": new.rel, "new_quote": q_new, "old_quote": q_old})
            self.ctx.judge[t].setdefault(old.rel, []).append(target)
            # The fix's own detector decides: a note it would not touch goes to the report.
            if target in rules.DETECTORS[t](n_old, self.ctx):
                return
            self.ctx.judge[t][old.rel].remove(target)
            if not self.ctx.judge[t][old.rel]:
                del self.ctx.judge[t][old.rel]
        self.judge_point(f)

    def plan_lessons(self, verdicts):
        rel = rules.lessons_path(self.ctx)
        for v in verdicts:
            if v.confidence != "haute":
                continue
            text = rules._norm_lesson(v.lesson.raw)
            if v.verdict == "bruit":
                if (self.today - v.lesson.day).days < 7:
                    self.lessons_kept_open.add(v.lesson.key)
                    continue
                key, data = "bruit\0" + text, {"verdict": "bruit", "text": text, "other": None}
            else:
                other = rules._norm_lesson(v.other.raw)
                key = "doublon\0" + "\0".join(sorted([text, other]))
                data = {"verdict": "doublon", "text": text, "other": other}
            self.ctx.judge["C3"].setdefault(rel, []).append(rules.Target(key, data))

    def report_quote(self, text, limit=80):
        return rules.inline_quote(judge.mask_text(text), limit)

    def judge_point(self, f):
        na, qa = link(f.a.rel), self.report_quote(f.quote_a)
        nb, qb = (link(f.b.rel), self.report_quote(f.quote_b)) if f.b else ("", "")
        if f.kind == "contradiction":
            text = "%s dit « %s », %s dit « %s ». Laquelle est vraie ? Dis-le à Claude, il corrigera la note " \
                   "avec toi." % (na, qa, nb, qb)
        elif f.kind == "decision_remplacee":
            text = "%s et %s : l'une des deux décisions semble remplacer l'autre (« %s », « %s »). À vérifier, " \
                   "puis dis-le à Claude." % (na, nb, qa, qb)
        elif f.kind == "proche_doublon":
            text = "%s et %s disent presque la même chose (« %s »). Tu peux les regrouper." % (na, nb, qa)
        else:
            text = "%s dit « %s » : c'est peut-être dépassé.%s" % (
                na, qa, " %s dit « %s »." % (nb, qb) if f.b else "")
        # Claude's own words never reach the vault (a note can dictate them): the
        # run's journal keeps them, outside it.
        if f.said:
            self.explanations.append({"chemin": f.a.rel, "texte": f.said})
        self.points.append(("juge_" + f.kind, f.a.rel, {"key": f.key, "text": text}))

    def after_judge(self):
        """Lessons the review classified are not sent again; the next review
        starts from what changed after this run's own commits."""
        if self.judge_verdict is None or not self.writes:
            return
        self.judge_updates["dernier_commit_juge"] = (self.git.text("rev-parse", "HEAD") or "").strip() or None
        if self.judge_verdict.suspect:
            return
        note = rules.read_note(str(self.vault), rules.lessons_path(self.ctx))
        pending = set()
        if note is not None:
            for t in rules.detect_c3(note, self.ctx):
                # One the user put back stays classified: it must not bring Claude back every night.
                if self.fp("C3", note.rel, t.key) not in self.refused:
                    pending.update(x for x in (t.data["text"], t.data["other"]) if x)
        seen = self.state.load("lecons-vues.json", {})
        day = self.today.isoformat()
        for les in self.judge_dossier.lessons.values():
            if les.key not in self.lessons_kept_open and rules._norm_lesson(les.raw) not in pending:
                seen[les.key] = day
        for key in self.judge_dossier.lessons_left_out:
            seen[key] = day
        if note is not None:
            _, entries = rules.lessons_structure(note)
            present = {self.lesson_key("\n".join(note.lines[s:e])) for s, e, _ in entries}
            seen = {k: v for k, v in seen.items() if k in present}
        self.state.save("lecons-vues.json", seen)

    # ---- phase 2: the catalogue

    def apply_all(self, an):
        for t in rules.PRIORITY:
            plans = an.plans.get(t) or []
            if not plans or t not in rules.MAKERS:
                continue
            if t in self.demoted:
                self.alert("orange", "retrograde", rules.TYPE_NAMES[t])
                self.would += [(t, rel, len(ts)) for rel, ts in plans]
                continue
            notes = {rel for rel, _ in plans}
            if len(notes) > BREAKER_MIN and len(notes) > BREAKER_SHARE * max(an.eligible, 1):
                self.alert("orange", "disjoncteur", rules.TYPE_NAMES[t])
                continue
            for rel, targets in plans:
                groups = [[x] for x in targets] if t in ("F1", "C1", "C2") else [targets]
                for group in groups:
                    if not self.can_fix:
                        self.would.append((t, rel, len(group)))
                        continue
                    if self.over_time():
                        self.leftover.add("temps")
                        return
                    self.apply_action(t, rel, group)

    def remaining(self, t):
        used = sum(a["unites"] for a in self.actions if a["type"] == t and a["statut"] == "fait")
        if t in ("D1", "D2"):
            used = sum(1 for a in self.actions if a["type"] in ("D1", "D2") and a["statut"] == "fait")
        if t == "S1":
            files = sum(1 for a in self.actions if a["type"] == "S1" and a["statut"] == "fait")
            if files >= S1_FILES:
                return 0
        return CAPS.get(t, 10 ** 6) - used

    def apply_action(self, t, rel, planned):
        allowed = {x.key for x in planned if self.fp(t, rel, x.key) not in self.refused}
        if not allowed:
            return self.skip(t, rel, "refusee")
        left = self.remaining(t)
        if left <= 0:
            self.leftover.add(t)
            return
        if rules.nfc(rel) not in self.tracked:
            return self.skip(t, rel, "hors_git")
        note = rules.read_note(str(self.vault), rel)
        snap = self.snapshot.get(rel)
        if note is None or snap is None or note.sha != snap.sha:
            return self.skip(t, rel, "changee_depuis_lecture")
        if rel not in self.touched and time.time() - note.mtime < EDIT_GUARD:
            return self.skip(t, rel, "modifiee_recemment")
        current = [x for x in rules.DETECTORS[t](note, self.ctx) if x.key in allowed]
        if not current:
            return self.skip(t, rel, "plus_rien_a_faire")
        aid = self.next_id(t)
        change = rules.MAKERS[t](note, current, self.ctx, aid,
                                 limit=left if t in ("S1", "S2", "M2", "C3") else None)
        if change is None:
            return self.skip(t, rel, "plus_rien_a_faire")
        if t in ("S1", "S2", "C3") and len(current) > left:
            self.leftover.add(t)
        new_files = len((set(change.writes) | set(change.deletes)) - self.touched)
        if len(self.touched) + new_files > FILES_PER_NIGHT:
            self.leftover.add(t)
            return
        for p in change.writes:
            if p == rel or rules.nfc(p) in self.tracked:
                continue
            if os.path.lexists(os.path.join(str(self.vault), *p.split("/"))) or self.git.ok("check-ignore", "-q", "--", p):
                return self.skip(t, rel, "hors_git")
        if self.write_change(t, aid, note, change, current, commit=True):
            for p in set(change.writes) | set(change.deletes):
                self.snapshot[p] = rules.read_note(str(self.vault), p)
                if self.snapshot[p] is None:
                    self.snapshot.pop(p, None)

    def skip(self, t, rel, cause):
        self.skipped.append({"type": t, "chemin": rel, "cause": cause})

    def write_change(self, t, aid, note, change, targets, commit):
        """Writes one fix, checks its contract on what is really on disk, then commits it.
        Returns True when the fix is done."""
        vault = str(self.vault)
        paths = list(dict.fromkeys(list(change.writes) + change.deletes))
        before = {}
        for p in paths:
            cur = note if p == note.rel else rules.read_note(vault, p)
            if cur is None and os.path.lexists(os.path.join(vault, *p.split("/"))):
                self.skip(t, note.rel, "illisible")
                return False
            before[p] = cur
        for p in paths:
            again = rules.read_note(vault, p)
            if (again.sha if again else None) != (before[p].sha if before[p] else None):
                self.skip(t, note.rel, "changee_depuis_lecture")
                return False
        new_dirs = []
        for p in change.writes:
            d = os.path.dirname(os.path.join(vault, *p.split("/")))
            while not os.path.isdir(d) and d not in new_dirs and rules.inside(d, vault) and d != vault:
                new_dirs.append(d)
                d = os.path.dirname(d)
        record = {"id": aid, "type": t, "chemins": paths, "commit": None, "unites": change.units,
                  "empreintes": [self.fp(t, note.rel, x.key) for x in targets], "resume": self.summary(t, change),
                  "statut": "fait", "phase": "nuit" if commit else "secrets"}
        try:
            for p, text in change.writes.items():
                b = before[p]
                data = b.encode(text) if b else rules.encode_text(text)
                mode = (os.stat(b.path).st_mode & 0o777) if b else None
                atomic_write(self.vault, p, data, mode)
            for p in change.deletes:
                os.unlink(safe_path(self.vault, p))
        except OSError:
            self.restore(before, new_dirs)
            self.skip(t, note.rel, "ecriture_impossible")
            return False
        after = {p: rules.read_note(vault, p) for p in paths}
        ok, why = rules.check_contract(
            change, {p: (n.text if n else None) for p, n in before.items()},
            {p: (n.text if n else None) for p, n in after.items()},
            read_text=lambda r: (lambda n: n.text if n else None)(rules.read_note(vault, r)))
        if ok:
            for p in paths:
                b, a = before[p], after[p]
                if b and a and (a.bom != b.bom or a.newline != b.newline):
                    ok, why = False, "encodage ou fins de ligne changés"
        if not ok:
            self.restore(before, new_dirs)
            record.update(statut="echec", cause=why)
            self.actions.append(record)
            self.alert("rouge", "contrat", aid)
            return False
        if t == "S1":
            self.quarantine_s1(aid, note.rel, change, targets)
        if commit:
            sha = self.commit_change(t, aid, change, record["resume"])
            if sha is None:
                self.unstage(before)
                self.remove_dirs(new_dirs)
                self.can_fix = False
                self.alert("orange", "commit_refuse")
                self.quarantine.pop(aid, None)
                self.save_quarantine()
                return False
            record["commit"] = sha
            self.tracked.update(rules.nfc(p) for p in change.writes)
            self.tracked.difference_update(rules.nfc(p) for p in change.deletes)
        self.touched.update(paths)
        self.actions.append(record)
        return True

    def remove_dirs(self, dirs):
        for d in sorted(dirs, key=len, reverse=True):
            try:
                os.rmdir(d)
            except OSError:
                pass

    def restore(self, before, new_dirs=()):
        self._restore(before)
        self.remove_dirs(new_dirs)

    def _restore(self, before):
        for p, b in before.items():
            try:
                if b is None:
                    path = safe_path(self.vault, p)
                    if os.path.lexists(path):
                        os.unlink(path)
                else:
                    atomic_write(self.vault, p, b.raw, os.stat(b.path).st_mode & 0o777
                                 if os.path.exists(b.path) else None)
            except OSError:
                pass

    def unstage(self, before):
        for p, b in before.items():
            if b is not None and rules.nfc(p) in self.tracked:
                self.git.run("checkout", "HEAD", "--", p)
            else:
                self.git.run("rm", "-q", "--cached", "-f", "--ignore-unmatch", "--", p)
                self.restore({p: b})

    def commit_change(self, t, aid, change, resume):
        adds = list(change.writes)
        if adds and not self.git.ok("add", "--", *adds):
            return None
        if change.deletes and not self.git.ok("rm", "-q", "--cached", "--ignore-unmatch", "--", *change.deletes):
            return None
        where = rules.note_name(change.contract[0][1]) if change.contract else ""
        msg = "contrôle %s : %s (%s)\n\nBrain-Controle-Run: %s\nBrain-Controle-Action: %s\n" % (
            self.today.isoformat(), resume["court"], safe_name(where), self.run, aid)
        if not self.git.commit(msg, adds + change.deletes):
            return None
        return (self.git.text("rev-parse", "HEAD") or "").strip() or None

    def quarantine_s1(self, aid, rel, change, targets):
        """The original lines go to quarantine (0600, outside the vault, never pushed)."""
        lines = change.summary["lines"]
        head = None
        if self.git:
            head = self.git.run("show", "HEAD:" + rel)[1].decode("utf-8", "replace") if \
                self.git.ok("cat-file", "-e", "HEAD:" + rel) else None
        in_history = sum(1 for x in targets if head and x.key in head)
        self.quarantine[aid] = {"chemin": rel, "le": self.now.isoformat(),
                                "lignes": [[i, a, b] for i, (a, b) in sorted(lines.items())],
                                "valeurs": [self.fp("valeur", x.key) for x in targets],
                                "empreintes": [self.fp("S1", rel, x.key) for x in targets],
                                "historique": in_history}
        self.save_quarantine()

    def save_quarantine(self):
        path = self.state.dir / "quarantaine" / (self.run + ".json")
        if self.quarantine:
            write_json(path, {"run": self.run, "actions": self.quarantine})
        elif path.exists():
            path.unlink()

    def summary(self, t, change):
        s = change.summary
        n = s.get("n", change.units)
        if t == "S1":
            kinds = ", ".join(SECRET_KINDS.get(k, k) for k in s["kinds"])
            return {"court": "secret masqué", "n": n, "kinds": kinds}
        if t == "S2":
            return {"court": "avertissement sur une commande qui efface", "n": n}
        if t == "X1":
            return {"court": "texte piégé désarmé", "chars": s.get("chars", 0), "callout": s.get("callout", False)}
        if t == "M1":
            return {"court": "vieux blocs de memory.md archivés", "n": n, "months": s.get("months", []),
                    "avant": s.get("avant"), "apres": s.get("apres")}
        if t == "M2":
            return {"court": "leçons en double ou recopiées mises de côté", "n": n, "sections": s.get("sections", 0),
                    "avant": s.get("avant"), "apres": s.get("apres")}
        if t == "D1":
            return {"court": "doublon exact supprimé", "canon": rules.note_name(s["canon"])}
        if t == "D2":
            return {"court": "note vide supprimée"}
        if t == "F1":
            return {"court": "en-tête réparé"}
        if t == "C1":
            return {"court": "contradiction signalée", "new": rules.note_name(s["new_rel"]),
                    "quote": self.report_quote(s["old_quote"])}
        if t == "C2":
            return {"court": "décision marquée remplacée", "new": rules.note_name(s["new_rel"])}
        if t == "C3":
            return {"court": "leçons écartées", "n": n, "doublons": s.get("doublons", 0)}
        return {"court": rules.TYPE_NAMES.get(t, t)}

    # ---- report, state, output

    def health(self):
        lines = []
        info = read_json(gbrain_dir() / "last-update.json", {})
        version = info.get("after") or info.get("before")
        if isinstance(version, str) and re.match(r"^v?\d+(\.\d+){2,3}$", version):
            self.gbrain_version = version if version.startswith("v") else "v" + version
        status = info.get("status")
        if status in ("rolled_back", "broken"):
            text = ("gbrain ne démarre plus après une mise à jour ratée : relance l'installation." if status == "broken"
                    else "gbrain : la dernière mise à jour a échoué, retour arrière fait.")
            self.alert("rouge", "gbrain", text)
        elif isinstance(info.get("retard_jours"), int) and info["retard_jours"] > 14:
            self.alert("rouge", "gbrain", "gbrain a %d jours de retard sur ses mises à jour." % info["retard_jours"])
        elif self.gbrain_version:
            lines.append("🟢 gbrain %s" % self.gbrain_version)
        st = read_json(home() / ".claude" / "logs" / "daily-reflection.state.json", {})
        since = st.get("failing_since")
        if isinstance(since, str) and re.match(r"^\d{4}-\d{2}-\d{2}", since):
            d = since[:10]
            why = " (limite d'usage de Claude)" if re.search(r"hit your .*limit|session limit|usage limit",
                                                             str(st.get("last_error") or ""), re.I) else ""
            self.alert("orange", "reflexion", "%s/%s%s" % (d[8:10], d[5:7], why))
        if self.claude == "fait":
            i = self.judge_info
            cost = i.get("cout")
            money = (" (≈ %s $ décomptés sur ton abonnement)" % ("%.2f" % cost).replace(".", ",")
                     if isinstance(cost, float) else "")
            lines.append("🟢 Claude a relu %d extrait%s et %d leçon%s%s" % (
                i.get("extraits", 0), plural(i.get("extraits", 0)), i.get("lecons_envoyees", 0),
                plural(i.get("lecons_envoyees", 0)), money))
        elif self.claude == "saute:sans-claude":
            lines.append("⚪ Relecture par Claude : désactivée pour ce contrôle.")
        elif self.claude == "saute:rien-de-neuf":
            lines.append("⚪ Relecture par Claude : rien de neuf depuis la dernière.")
        return lines

    def secrets_to_change(self):
        """S1 ids whose value git already had (and the remote): "change-le" in every
        report and at every session start until the user says it is done
        (secret-change), undoes it, or 30 days have gone by."""
        done = self.state.load("secrets-changes.json", {})
        limit = self.now - timedelta(days=QUARANTINE_DAYS)
        ids = {aid for _, actions in self.state.quarantined() for aid, q in actions.items()
               if q.get("historique") and aid not in done and not older_than(q, limit)}
        if ids and self.git_ok:
            log = self.git.text("log", "--format=%B", "--grep=^Brain-Controle-Annule: S1-") or ""
            ids -= {v for k, v in TRAILER.findall(log) if k == "Annule"}
        return sorted(ids)

    def verdict(self):
        if any(level == "rouge" for level, _, _ in self.alerts) or self.to_change:
            return "rouge"
        if self.points or self.alerts:
            return "orange"
        return "vert"

    def point_lines(self):
        """(fingerprint, text, once) of each point to look at."""
        out = []
        for code, rel, x in self.points:
            if code.startswith("juge_") and isinstance(x, dict):
                out.append((self.fp("point", code, x["key"]), x["text"], True))
                continue
            m = x if isinstance(x, str) and code in ("doublon_profil", "doublon_lie") else None
            text = POINT_TEXT[code].format(
                n=safe_name(rules.note_name(rel)), m=safe_name(rules.note_name(m)) if m else "",
                x=(rules.size_label(x) if code == "consignes_grosses" else safe_name(str(x)) if x is not None else ""),
                s=plural(x) if isinstance(x, int) else "", rel=safe_name(rel))
            once = code.startswith(("piege_", "juge_"))
            out.append((self.fp("point", code, rel, str(x if once else "")), text, once))
        for t in sorted(self.leftover):
            if t == "temps":
                out.append((self.fp("point", "temps"), POINT_TEXT["temps"], False))
            else:
                out.append((self.fp("point", "plafond", t), POINT_TEXT["plafond"].format(x=rules.TYPE_NAMES[t]), False))
        return out

    def finish(self, scan):
        state = self.state.load("juge.json", {}) if self.writes else {}
        state.update(self.judge_updates)
        signaled = state.get("signales", {}) if isinstance(state.get("signales"), dict) else {}
        health = self.health() if scan is not None else []
        self.to_change = self.secrets_to_change() if scan is not None else []
        verdict = self.verdict() if scan is not None else "rouge"
        points = self.point_lines() if scan is not None else []
        new = [p for p in points if p[0] not in signaled]
        shown = [p for p in points if not p[2] or p[0] not in signaled]
        done = [a for a in self.actions if a["statut"] == "fait"]
        report_rel = None
        useful = bool(self.actions or new or self.alerts or self.to_change)
        end = datetime.now()
        if self.writes and scan is not None and useful and self.git_ok and self.cause != "commit_refuse":
            report_rel = self.write_report(verdict, shown, health)
        if self.writes and scan is not None:
            for fp, _, _ in points:
                signaled.setdefault(fp, self.today.isoformat())
            limit = (self.today - timedelta(days=60)).isoformat()
            state["signales"] = {k: v for k, v in signaled.items() if v >= limit}
            self.state.save("juge.json", state)
            journal = self.journal(verdict, len(shown), end)
            write_json(self.state.dir / "runs" / (self.run + ".json"), journal)
            prev = self.state.load("etat.json", {})
            etat = {"run": self.run, "mode": self.mode, "fin": end.replace(microsecond=0).isoformat(),
                    "dernier_succes": end.replace(microsecond=0).isoformat(), "verdict": verdict,
                    "corrections": len(done), "a_voir": len(shown), "secrets_a_changer": len(self.to_change),
                    "secrets_ids": self.to_change,
                    "rapport": report_rel or prev.get("rapport"), "rapport_reecrit": bool(report_rel),
                    "claude": self.claude, "cause": self.cause,
                    "alertes": [{"niveau": lv, "code": code} for lv, code, _ in self.alerts]}
            self.state.save("etat.json", etat)
            self.state.purge(self.now, {i for n in scan.notes for i in MASK_ID.findall(n.text)})
        elif self.writes:
            self.state.ensure()
            prev = self.state.load("etat.json", {})
            prev.update(verdict="rouge", alertes=[{"niveau": "rouge", "code": "vault_absent"}],
                        fin=end.replace(microsecond=0).isoformat())
            self.state.save("etat.json", prev)
        self.print_summary(verdict, shown, scan)
        return 0

    def journal(self, verdict, a_voir, end):
        return {"run": self.run, "mode": self.mode, "debut": self.now.isoformat(),
                "fin": end.replace(microsecond=0).isoformat(), "duree_s": int(time.time() - self.t0),
                "verdict": verdict, "corrections": sum(1 for a in self.actions if a["statut"] == "fait"),
                "a_voir": a_voir, "actions": self.actions, "abandons": self.skipped,
                "a_faire": [{"type": t, "chemin": rel, "unites": n} for t, rel, n in self.would],
                "points": [{"code": c, "chemin": rel} for c, rel, _ in self.points],
                "alertes": [{"niveau": lv, "code": c} for lv, c, _ in self.alerts],
                "claude": self.claude, "cause": self.cause, "gbrain": self.gbrain_version,
                "notes_lues": self.notes_count, "exclusions": self.exclusions[:200],
                "suite": sorted(self.leftover), "juge": self.judge_info, "explications": self.explanations,
                "annule_le": None}

    def action_row(self, a):
        r, t = a["resume"], a["type"]
        where = link(a["chemins"][0])
        if a["statut"] != "fait":
            return "| %s | %s | %s |" % (a["id"], REPORT_TEXT["echec"], where)
        if t == "S1":
            q = self.quarantine.get(a["id"], {})
            text = REPORT_TEXT["S1"].format(n=r["n"], s=plural(r["n"]), kinds=r["kinds"])
            if q.get("historique"):
                text += REPORT_TEXT["S1_hist"]
        elif t == "S2":
            text = REPORT_TEXT["S2_n"].format(n=r["n"]) if r["n"] > 1 else REPORT_TEXT["S2"]
        elif t == "X1":
            parts = []
            if r.get("chars"):
                parts.append(REPORT_TEXT["X1_chars"].format(n=r["chars"], s=plural(r["chars"]),
                                                            ent="ent" if r["chars"] > 1 else "t"))
            if r.get("callout"):
                parts.append(REPORT_TEXT["X1_callout"])
            text = " ".join(parts)
        elif t == "M1":
            text = REPORT_TEXT["M1"].format(n=r["n"], s=plural(r["n"]), avant=rules.size_label(r["avant"]),
                                            apres=rules.size_label(r["apres"]))
            where = "memory.md → " + ", ".join("[[memory-%s]]" % m for m in r["months"])
        elif t == "M2":
            sections = (" et rangé %d vieille%s journée%s dans l'archive" % (r["sections"], plural(r["sections"]),
                                                                           plural(r["sections"]))
                        if r["sections"] else "")
            text = REPORT_TEXT["M2"].format(n=r["n"], s=plural(r["n"]), sections=sections,
                                            avant=rules.size_label(r["avant"]), apres=rules.size_label(r["apres"]))
            where = "lessons.md → [[lessons-ecartees]]" if r["n"] else "lessons.md → archive"
        elif t == "D1":
            text = REPORT_TEXT["D1"].format(canon=safe_name(r["canon"]))
            where = safe_name(rules.note_name(a["chemins"][0]))
        elif t == "D2":
            text, where = REPORT_TEXT["D2"], safe_name(rules.note_name(a["chemins"][0]))
        elif t == "C1":
            text = REPORT_TEXT["C1"].format(quote=r["quote"], new=safe_name(r["new"]))
        elif t == "C2":
            text, where = REPORT_TEXT["C2"], "%s → [[%s]]" % (where, safe_name(r["new"]))
        elif t == "C3":
            text = REPORT_TEXT["C3"].format(n=r["n"], s=plural(r["n"]))
            where = "lessons.md → [[lessons-ecartees]]"
        else:
            text = REPORT_TEXT.get(t, rules.TYPE_NAMES.get(t, t))
        return "| %s | %s | %s |" % (a["id"], text.replace("|", "\\|"), where.replace("|", "\\|"))

    def write_report(self, verdict, shown, health):
        rel = self.ctx.profile + "/controle.md"
        emoji, word = VERDICT[verdict]
        done = [a for a in self.actions if a["statut"] == "fait"]
        night = self.mode == "nuit"
        when = "cette nuit" if night else "aujourd'hui"
        title = ("Contrôle de la nuit du %s" % date_fr(self.today) if night
                 else "Contrôle du %s à %s" % (date_fr(self.today), self.now.strftime("%H:%M")))
        brief = []
        if done:
            brief.append("j'ai fait %d correction%s tout seul %s, toutes annulables." % (len(done), plural(len(done)), when))
        elif self.cause:
            brief.append("je n'ai rien corrigé (%s)." % CAUSES[self.cause])
            if self.would:
                brief.append("J'aurais fait %d correction%s." % (len(self.would), plural(len(self.would))))
        else:
            brief.append("je n'ai rien eu à corriger %s." % when)
        if shown:
            brief.append("%d point%s demande%s ton avis." % (len(shown), plural(len(shown)), "nt" if len(shown) > 1 else ""))
        if self.gbrain_version:
            brief.append("gbrain est en %s." % self.gbrain_version)
        out = ["---", "type: rapport", "title: Contrôle du cerveau", "date: %s" % self.today.isoformat(),
               "verdict: %s" % verdict, "controle_run: \"%s\"" % self.run, "---", "",
               "# %s — %s %s" % (title, emoji, word), "", "**En bref :** " + " ".join(brief), ""]
        if self.actions:
            out += ["## Ce que j'ai corrigé", "", "| N° | Ce que j'ai fait | Où |", "|---|---|---|"]
            out += [self.action_row(a) for a in self.actions]
            out += ["", "Pour défaire une correction, dis à Claude : « /controle annuler %s ». Pour tout défaire : "
                        "« /controle annuler la dernière nuit »." % self.actions[0]["id"], ""]
        if shown:
            out += ["## À regarder toi-même (je n'y ai pas touché)", ""] + ["- " + text for _, text, _ in shown] + [""]
        out += ["## Santé", ""]
        minutes = int((time.time() - self.t0) // 60)
        out.append("- 🟢 Contrôle terminé à %s (%s)" % (datetime.now().strftime("%H:%M"),
                                                       "%d min" % minutes if minutes else "moins d'une minute"))
        for level, code, x in self.alerts:
            text = ALERT_TEXT.get(code, code)
            if code == "secret_historique":
                continue
            text = text.format(x=x if not isinstance(x, int) else rules.size_label(x), s="", ent="t")
            out.append("- %s %s" % ("🔴" if level == "rouge" else "🟠", text))
        if self.to_change:
            n = len(self.to_change)
            out.append("- 🔴 " + ALERT_TEXT["secret_historique"].format(
                x=n, s=plural(n), ent="ent" if n > 1 else "t", a="ont" if n > 1 else "a", ids=", ".join(self.to_change)))
        out += ["- " + h for h in health]
        out += ["", "## Historique", "", "| Nuit | Verdict | Corrigé | À regarder | gbrain |", "|---|---|---|---|---|"]
        rows = [j for j in self.state.journals() if j.get("mode") in ("nuit", "maintenant")][-13:]
        current = {"run": self.run, "verdict": verdict, "corrections": len(done), "a_voir": len(shown),
                   "gbrain": self.gbrain_version}
        for j in [current] + list(reversed(rows)):
            run = str(j.get("run", ""))
            day = "%s/%s" % (run[6:8], run[4:6]) if len(run) >= 8 else "?"
            out.append("| %s | %s | %s | %s | %s |" % (day, VERDICT.get(j.get("verdict"), ("?",))[0],
                                                       j.get("corrections", 0), j.get("a_voir", 0),
                                                       j.get("gbrain") or "—"))
        skipped = len(self.skipped)
        out += ["", "<details><summary>Détails techniques</summary>", "",
                "run %s · %d notes lues · %d correction%s sautée%s · plafonds atteints : %s · journal : "
                "~/.brain-in-a-box/controle/runs/%s.json" % (
                    self.run, self.notes_count, skipped, plural(skipped), plural(skipped),
                    ", ".join(sorted(self.leftover)) or "aucun", self.run) + self.judge_details(), "",
                "</details>", ""]
        text = "\n".join(out)
        # What the user wrote in the report since its last commit (answers, notes) goes
        # into git first: rewriting it must not lose a line (in pause too).
        if rel in (status_paths(self.git) or []) and not (
                self.git.ok("add", "--", rel) and self.git.commit("notes avant rapport\n", [rel])):
            return None
        try:
            prev = rules.read_note(str(self.vault), rel)
            atomic_write(self.vault, rel, (prev.encode(text) if prev else rules.encode_text(text)))
        except OSError:
            return None
        msg = "contrôle %s : rapport\n\nBrain-Controle-Run: %s\nBrain-Controle-Action: rapport\n" % (
            self.today.isoformat(), self.run)
        if not (self.git.ok("add", "--", rel) and self.git.commit(msg, [rel])):
            self.unstage({rel: prev})
            return None
        return rel

    def judge_details(self):
        i = self.judge_info
        if not i.get("dossier_garde"):
            return ""
        out = ""
        if i.get("version"):
            out += " · Claude Code %s" % i["version"]
        dropped = sum((i.get("abandons") or {}).values())
        if dropped:
            out += " · %d constat%s de Claude écarté%s (citation introuvable ou incomplète)" % (
                dropped, plural(dropped), plural(dropped))
        return out + " · dossier envoyé : ~/.brain-in-a-box/controle/runs/%s/dossier.txt" % self.run

    def print_summary(self, verdict, shown, scan):
        done = [a for a in self.actions if a["statut"] == "fait"]
        if scan is None:
            print("[controle] dossier du cerveau introuvable : rien vérifié")
            return
        print("[controle] run %s (%s) : %d correction%s, %d point%s à regarder, verdict %s%s" % (
            self.run, self.mode, len(done), plural(len(done)), len(shown), plural(len(shown)), verdict,
            " — rien corrigé : " + CAUSES[self.cause] if self.cause else ""))
        if self.mode == "essai":
            for t, rel, n in self.would:
                print("  ferait %s (%s) dans %s : %d élément%s" % (t, rules.TYPE_NAMES[t], safe_name(rel), n, plural(n)))
            for _, text, _ in shown:
                print("  à regarder : " + text)


# --------------------------------------------------------------------------- undo

class Undo:
    def __init__(self, opts):
        self.opts = opts
        self.vault = vault_dir(opts)
        self.state = State(state_dir(opts))
        self.now = parse_now(opts.maintenant)
        self.essai = opts.essai
        self.put_back = opts.remettre_le_secret
        binary = find_git(opts)
        self.git = Git(binary, self.vault) if binary else None

    def say(self, text):
        print(text)

    def find_action(self, aid):
        """The fix's record: in its run's journal, else in empreintes.json once the
        journal is gone (30 runs later), else None."""
        for j in reversed(self.state.journals()):
            for a in j.get("actions", []):
                if a.get("id") == aid:
                    return a
        kept = self.state.load("empreintes.json", {}).get(aid)
        return dict(kept, id=aid) if isinstance(kept, dict) else None

    def undone(self, aid):
        return bool(self.git.text("log", "-n", "1", "--format=%H", "--grep=^Brain-Controle-Annule: %s$" % aid))

    def prepare(self):
        """Commits pending notes first; refuses when they hold a secret not masked yet."""
        if self.essai:
            return True
        pending = status_paths(self.git)
        if pending is None:
            return False
        if not pending:
            return True
        from _redact import find_secrets
        for rel in pending:
            n = rules.read_note(str(self.vault), rel) if rel.lower().endswith(".md") else None
            if n is not None and find_secrets(n.text):
                self.say("Des notes pas encore enregistrées contiennent un mot de passe ou une clé : lance d'abord "
                         "« controle maintenant », puis recommence l'annulation.")
                return False
        return self.git.ok("add", "-A") and self.git.commit("notes avant annulation\n")

    def run_one(self, aid):
        action = self.find_action(aid)
        if aid.startswith("S1-"):
            return self.undo_secret(aid, action)
        sha = (self.git.text("log", "-n", "1", "--format=%H", "--grep=^Brain-Controle-Action: %s$" % aid) or "").strip()
        if not sha:
            self.say("Je ne trouve pas la correction %s." % aid)
            return 1
        if self.undone(aid):
            self.say("La correction %s est déjà annulée." % aid)
            return 0
        files = (self.git.text("show", "--name-only", "--format=", sha) or "").split()
        if self.essai:
            self.say("J'annulerais la correction %s (%s), qui a touché : %s." % (
                aid, rules.TYPE_NAMES.get(aid[:2], aid[:2]), ", ".join(safe_name(f) for f in files)))
            return 0
        if not self.prepare():
            return 1
        result = self.revert(aid, sha)
        if result == "ok" and action:
            self.refuse(action)
        return 0 if result == "ok" else 1

    def revert(self, aid, sha):
        rc = self.git.run(*(IDENT + ["revert", "--no-commit", sha]))[0]
        if rc != 0:
            self.git.run(*(IDENT + ["revert", "--abort"]))
            self.say("Je n'ai pas pu annuler %s tout seul : la note a été modifiée au même endroit depuis. "
                     "À faire à la main : compare la version d'avant et la version actuelle (git show %s)." % (aid, sha[:10]))
            return "main"
        msg = "annulation contrôle %s\n\nBrain-Controle-Annule: %s\n" % (aid, aid)
        if not self.git.commit(msg):
            self.git.run(*(IDENT + ["revert", "--abort"]))
            self.say("git a refusé le commit d'annulation de %s : rien n'a changé." % aid)
            return "refus"
        self.say("Correction %s annulée." % aid)
        return "ok"

    def refuse(self, action):
        refused = self.state.load("refusees.json", {})
        for fp in action.get("empreintes", []):
            refused[fp] = {"type": action.get("type"), "id": action.get("id"), "le": self.now.date().isoformat()}
        self.state.save("refusees.json", refused)

    def undo_secret(self, aid, action):
        """A masked secret comes back from quarantine, never through git, and only
        when asked for by its id with --remettre-le-secret."""
        q = next((a[aid] for _, a in self.state.quarantined() if aid in a), None)
        if not q:
            self.say("La valeur d'origine de %s n'est plus en quarantaine : rien à remettre." % aid)
            return 1
        if self.undone(aid):
            self.say("La correction %s est déjà annulée." % aid)
            return 0
        warning = ("Attention : le secret remis dans la note sera enregistré dans l'historique du cerveau et "
                   "sauvegardé en ligne à la prochaine nuit.")
        rel = q["chemin"]
        if self.essai:
            self.say("Je remettrais %d ligne%s de %s telles qu'elles étaient. %s" % (
                len(q["lignes"]), plural(len(q["lignes"])), safe_name(rel), warning))
            return 0
        if not self.put_back:
            self.say("%s a masqué un mot de passe ou une clé. %s Si c'est vraiment voulu, relance avec "
                     "--remettre-le-secret." % (aid, warning))
            return 1
        if not self.prepare():
            return 1
        note = rules.read_note(str(self.vault), rel)
        if note is None:
            self.say("La note %s n'existe plus : rien à remettre." % safe_name(rel))
            return 1
        lines = note.lines
        for i, original, masked in q["lignes"]:
            if i < len(lines) and lines[i] == masked:
                lines[i] = original
            elif lines.count(masked) == 1:
                lines[lines.index(masked)] = original
            else:
                self.say("La ligne masquée de %s a changé depuis : à remettre à la main." % safe_name(rel))
                return 1
        atomic_write(self.vault, rel, note.encode("\n".join(lines)), os.stat(note.path).st_mode & 0o777)
        msg = "annulation contrôle %s\n\nBrain-Controle-Annule: %s\n" % (aid, aid)
        if not (self.git.ok("add", "--", rel) and self.git.commit(msg, [rel])):
            atomic_write(self.vault, rel, note.raw, os.stat(note.path).st_mode & 0o777)
            self.git.run("checkout", "HEAD", "--", rel)
            self.say("git a refusé le commit d'annulation : rien n'a changé.")
            return 1
        white = self.state.load("blanche.json", {})
        for fp in q.get("valeurs", []):
            white[fp] = self.now.date().isoformat()
        self.state.save("blanche.json", white)
        self.refuse(action or {"id": aid, "type": "S1", "empreintes": q.get("empreintes") or []})
        self.say("Correction %s annulée. %s" % (aid, warning))
        return 0

    def run_night(self, run):
        """Reverts the night's commits, newest first. Its masked secrets stay masked:
        each one comes back on its own (annuler S1-... --remettre-le-secret). The night
        counts as undone only when nothing is left to do by hand."""
        journals = self.state.journals()
        if run is None:
            done = [j for j in journals if any(a.get("statut") == "fait" for a in j.get("actions", []))
                    and not j.get("annule_le")]
            if not done:
                self.say("Aucune nuit de contrôle à annuler.")
                return 0
            run = done[-1]["run"]
        if not RUN_ID.match(run):
            self.say("Identifiant de nuit invalide.")
            return 1
        journal = next((j for j in journals if j.get("run") == run), {})
        log = self.git.text("log", "--format=%H%x00%B%x01", "--grep=^Brain-Controle-Run: %s$" % run) or ""
        commits = []
        for chunk in log.split("\x01"):
            if "\x00" not in chunk:
                continue
            sha, body = chunk.strip("\n").split("\x00", 1)
            ids = [v for k, v in TRAILER.findall(body) if k == "Action"]
            if ids and ids[0] != "rapport" and ACTION_ID.match(ids[0]):
                commits.append((sha.strip(), ids[0]))
        if not commits and any(a.get("commit") for a in journal.get("actions", [])):
            self.say("Je ne trouve pas les corrections de la nuit %s dans ce cerveau (%s) : rien n'a changé." % (
                run, safe_name(self.vault.name)))
            return 1
        masked = sorted({a["id"] for a in journal.get("actions", []) if a.get("type") == "S1"
                         and a.get("statut") == "fait" and not self.undone(a.get("id", ""))})
        kept = (" Les secrets masqués restent masqués (%s) : pour en remettre un dans sa note, « controle annuler "
                "<ID> --remettre-le-secret », un par un." % ", ".join(masked)) if masked else ""
        todo = [(s, i) for s, i in commits if not i.startswith("S1") and not self.undone(i)]
        if self.essai:
            self.say("J'annulerais %d correction%s de la nuit %s.%s" % (len(todo), plural(len(todo)), run, kept))
            return 0
        if not self.prepare():
            return 1
        ok, by_hand = [], []
        actions = {a.get("id"): a for a in journal.get("actions", [])}
        for sha, aid in todo:
            r = self.revert(aid, sha)
            if r == "ok":
                ok.append(aid)
                action = actions.get(aid) or self.find_action(aid)
                if action:
                    self.refuse(action)
            else:
                by_hand.append(aid)
        if journal and ok and not by_hand:
            journal["annule_le"] = self.now.isoformat()
            write_json(self.state.dir / "runs" / (run + ".json"), journal)
        if ok or by_hand:
            self.note_in_report(run, ok, by_hand)
        recent = [j for j in self.state.journals() if any(a.get("statut") == "fait" for a in j.get("actions", []))][-2:]
        if ok and len(recent) == 2 and all(j.get("annule_le") for j in recent):
            (self.state.dir / "pause").touch()
            self.say("Deux nuits annulées de suite : je mets les corrections en pause (« controle reprendre » pour relancer).")
        self.say("Nuit %s : %d correction%s annulée%s%s.%s" % (
            run, len(ok), plural(len(ok)), plural(len(ok)),
            ", %d à faire à la main (%s)" % (len(by_hand), ", ".join(by_hand)) if by_hand else "", kept))
        return 0

    def note_in_report(self, run, ok, by_hand):
        profile = next((p for p in ("Profile", "Profil") if (self.vault / p).is_dir()), "Profile")
        rel = profile + "/controle.md"
        note = rules.read_note(str(self.vault), rel)
        if note is None:
            return
        text = note.text.rstrip("\n") + "\n\n## Annulé le %s\n\nJ'ai défait %d correction%s de la nuit %s%s.\n" % (
            self.now.strftime("%d/%m/%Y"), len(ok), plural(len(ok)), run,
            " ; à faire à la main : %s" % ", ".join(by_hand) if by_hand else "")
        atomic_write(self.vault, rel, note.encode(text), os.stat(note.path).st_mode & 0o777)
        msg = ("contrôle %s : annulation de la nuit %s\n\nBrain-Controle-Action: rapport\n" % (
            self.now.date().isoformat(), run))
        if not (self.git.ok("add", "--", rel) and self.git.commit(msg, [rel])):
            atomic_write(self.vault, rel, note.raw, os.stat(note.path).st_mode & 0o777)
            self.git.run("checkout", "HEAD", "--", rel)


# --------------------------------------------------------------------------- commands

def with_lock(opts, fn):
    state = State(state_dir(opts))
    state.ensure()
    lock = Lock(state.dir / "run.lock")
    if not lock.acquire():
        print("[controle] déjà en cours")
        return 0
    try:
        return fn()
    finally:
        lock.release()


def cmd_check(opts):
    if opts.cmd == "essai":
        return Run(opts, "essai").execute()
    return with_lock(opts, lambda: Run(opts, opts.cmd).execute())


def cmd_undo(opts):
    undo = Undo(opts)
    if undo.git is None or preflight(undo.vault, undo.git) is not None:
        print("Annulation impossible : %s." % CAUSES.get(preflight(undo.vault, undo.git) if undo.git else
                                                         "git_introuvable", "git n'est pas prêt"))
        return 1
    target = opts.cible
    if opts.nuit or target in ("derniere-nuit", "dernière-nuit"):
        return with_lock(opts, lambda: undo.run_night(opts.nuit))
    if not target or not ACTION_ID.match(target):
        print("Donne l'identifiant d'une correction (ex. S2-261014-02) ou « derniere-nuit ».")
        return 1
    return with_lock(opts, lambda: undo.run_one(target))


def cmd_status(opts):
    state = State(state_dir(opts))
    etat = state.load("etat.json", {})
    if not etat.get("run"):
        print("Aucun contrôle n'a encore tourné.")
    else:
        emoji, word = VERDICT.get(etat.get("verdict"), ("?", "?"))
        print("Dernier contrôle : %s (%s) — %s %s." % (etat.get("fin", "?"), etat.get("mode", "?"), emoji, word))
        print("%s correction(s), %s point(s) à regarder%s." % (
            etat.get("corrections", 0), etat.get("a_voir", 0),
            ", %s secret(s) à changer" % etat["secrets_a_changer"] if etat.get("secrets_a_changer") else ""))
        if etat.get("rapport"):
            print("Détail : %s" % etat["rapport"])
        if etat.get("cause"):
            print("Rien corrigé : %s." % CAUSES.get(etat["cause"], etat["cause"]))
    if (state.dir / "pause").exists():
        print("Le contrôle est en pause : il signale sans corriger (« controle reprendre » pour relancer).")
    lock = Lock(state.dir / "run.lock")
    if (state.dir / "run.lock").exists() and not lock.acquire():
        print("Un contrôle est en cours.")
    lock.release()
    return 0


def cmd_pause(opts, on):
    state = State(state_dir(opts))
    state.ensure()
    path = state.dir / "pause"
    if on:
        path.touch()
        print("Contrôle en pause : il signalera sans rien corriger.")
    else:
        try:
            path.unlink()
        except OSError:
            pass
        print("Contrôle relancé : il corrigera de nouveau à la prochaine nuit.")
    return 0


def cmd_secret_changed(opts):
    """The user changed a secret git already had: no more "change-le" about it."""
    aid = opts.cible or ""
    if not re.match(r"^S1-\d{6}-\d{2,3}$", aid):
        print("Donne l'identifiant du secret masqué que tu as changé (ex. S1-261014-01).")
        return 1
    state = State(state_dir(opts))
    state.ensure()
    done = state.load("secrets-changes.json", {})
    done[aid] = parse_now(opts.maintenant).date().isoformat()
    state.save("secrets-changes.json", done)
    etat = state.load("etat.json", {})
    if aid in (etat.get("secrets_ids") or []):
        etat["secrets_ids"] = [i for i in etat["secrets_ids"] if i != aid]
        etat["secrets_a_changer"] = len(etat["secrets_ids"])
        state.save("etat.json", etat)
    print("C'est noté : je ne te rappelle plus de changer le secret masqué par %s." % aid)
    return 0


def cmd_lessons(opts):
    vault = vault_dir(opts)
    profile = next((p for p in ("Profile", "Profil") if (vault / p).is_dir()), "Profile")
    note = rules.read_note(str(vault), profile + "/lessons.md")
    if note is None:
        print("Pas de lessons.md lisible dans %s." % profile)
        return 1
    _, entries = rules.lessons_structure(note)
    print(note.path)
    print("%s, %d entrée%s." % (rules.size_label(len(note.raw)), len(entries), plural(len(entries))))
    return 0


def cmd_gbrain(opts):
    info = read_json(gbrain_dir() / "last-update.json", {})
    state = read_json(gbrain_dir() / "update-state.json", {})
    updater = Path(os.path.dirname(os.path.abspath(__file__))) / "gbrain_update.py"
    if opts.cible in ("maintenant", "reprendre"):
        if not updater.is_file():
            print("La mise à jour à 7 jours de gbrain n'est pas encore installée.")
            return 1
        args = [sys.executable, "-I", str(updater)] + (["--reprendre"] if opts.cible == "reprendre" else [])
        return subprocess.call(args, creationflags=NO_WINDOW)
    if not info:
        print("Pas encore d'information sur les mises à jour de gbrain.")
        return 0
    version = info.get("after") or info.get("before") or "?"
    print("gbrain %s — état : %s." % (version, info.get("status", "?")))
    if isinstance(info.get("retard_jours"), int):
        print("Retard : %d jour%s." % (info["retard_jours"], plural(info["retard_jours"])))
    nxt = info.get("prochaine") or {}
    if isinstance(nxt, dict) and nxt.get("tag"):
        print("Prochaine version : %s, installable à partir du %s." % (nxt["tag"], nxt.get("eligible_le", "?")))
    blocked = state.get("bloques") or info.get("bloques") or {}
    if isinstance(blocked, dict):
        for tag, why in sorted(blocked.items()):
            print("Bloquée : %s (%s)." % (tag, why.get("cause", "?") if isinstance(why, dict) else why))
    if info.get("derniere_verif_ok"):
        print("Dernière vérification réussie : %s." % info["derniere_verif_ok"])
    return 0


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--brain", help="the vault (default: BRAIN_DIR, else ~/Documents/Brain)")
    common.add_argument("--git", help="git executable (default: the one on PATH)")
    common.add_argument("--etat", help="state folder (default: BRAIN_CONTROLE_DIR, else ~/.brain-in-a-box/controle)")
    common.add_argument("--maintenant", help="ISO date-time used as 'now' (tests)")
    ap = argparse.ArgumentParser(prog="controle", description=__doc__.split("\n\n")[0], epilog=ENV_HELP,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    for name, text in (("nuit", "the nightly check"), ("maintenant", "a check now"),
                       ("essai", "what a check would do, writes nothing")):
        p = sub.add_parser(name, parents=[common], help=text, epilog=ENV_HELP,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        g = p.add_mutually_exclusive_group()
        g.add_argument("--sans-claude", action="store_true", help="no Claude review this time")
        g.add_argument("--avec-claude", action="store_true", help="ask Claude in essai too (nothing is written)")
    p = sub.add_parser("annuler", parents=[common], help="undo one fix (ID) or a whole night (derniere-nuit)",
                       epilog=ENV_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cible", nargs="?")
    p.add_argument("--nuit", help="run id of the night to undo")
    p.add_argument("--essai", action="store_true", help="show what would be undone")
    p.add_argument("--remettre-le-secret", action="store_true",
                   help="puts a masked secret (S1-...) back in its note: it then goes to git and the online backup")
    for name, text in (("statut", "last check"), ("pause", "report only from now on"),
                       ("reprendre", "fix again"), ("lecons", "where lessons.md is, its size")):
        sub.add_parser(name, parents=[common], help=text)
    p = sub.add_parser("secret-change", parents=[common], help="the user changed a secret git already had (S1-...)")
    p.add_argument("cible", nargs="?")
    p = sub.add_parser("gbrain", parents=[common], help="gbrain's version and updates")
    p.add_argument("cible", nargs="?", choices=["maintenant", "reprendre"])
    return ap


def main(argv=None):
    # Under -I, PYTHONIOENCODING is ignored: a Windows log or pipe would be cp1252
    # and an emoji or a note's name would crash the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    opts = parser().parse_args(argv)
    if opts.cmd in ("nuit", "maintenant", "essai"):
        return cmd_check(opts)
    if opts.cmd == "annuler":
        return cmd_undo(opts)
    if opts.cmd == "statut":
        return cmd_status(opts)
    if opts.cmd in ("pause", "reprendre"):
        return cmd_pause(opts, opts.cmd == "pause")
    if opts.cmd == "lecons":
        return cmd_lessons(opts)
    if opts.cmd == "secret-change":
        return cmd_secret_changed(opts)
    if opts.cmd == "gbrain":
        return cmd_gbrain(opts)
    parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
