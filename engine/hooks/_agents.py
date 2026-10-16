"""Which AI agents the kit works with: Claude Code, Codex or both, and which one is
preferred for the tasks without tools (the reflection, the night check's review).

~/.brain-in-a-box/agents.json holds the choice and is its only source:

    {"version": 1, "agents": ["claude", "codex"], "prefere": "claude",
     "codex_home": "/Users/x/.codex", "pose_par": "install.sh", "date": "2026-10-15"}

Absent: Claude alone, exactly as before the file existed. Only the installers write it,
through `_agents.py choisir`, and only when someone chose: an install run without a
choice leaves no file behind. Everything else reads it with lire(). A file it cannot
trust gives no agent at all, and says why: whoever chose Codex alone excluded Claude, so
nothing falls back to it; the tasks without tools wait for the install to be run again.

Codex is closed on a system until its whole path is green there (docs/proposals/codex.md,
§1.1): every merged phase reaches users at once through master.zip, so asking for it is
refused there, and a file that lists it there is not trusted.

The hooks Codex runs (hooks.json, --agent codex) ask codex_hook() whether to act; Claude Code's
never load this module.

Not a hook: installed next to them, never registered in settings.json.
"""
import argparse
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from collections import namedtuple

AGENTS = ("claude", "codex")
# The systems Codex can be chosen on: macOS and Linux open in phase 6, Windows in phase 7.
CODEX_OUVERT = frozenset()
FICHIER_MAX = 64 * 1024
POSE_PAR = ("install.sh", "install.ps1")
# The sign that Codex runs the kit's hooks: per hook, when it last ran (codex_hook()).
VUS = "codex-hooks-vus.json"
# The sessions the user types in: Codex's terminal (cli) and its editor's (vscode). "exec" is a script's
# run or the kit's own, "mcp" another program's; sub-agents and Codex's internal sessions say an
# object, not a word (codex-rs/protocol/src/protocol.rs, SessionSource, 0.162.1).
SOURCES_TAPEES = ("cli", "vscode")
LIGNE_MAX = 1024 * 1024   # a session file's first line: Codex's base instructions, about 23 kB in 0.162.1
ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class Choix(namedtuple("Choix", "agents prefere codex_home")):
    """agents in AGENTS order; codex_home (absolute) only when Codex is one of them."""
    __slots__ = ()


DEFAUT = Choix(("claude",), "claude", None)
AUCUN = Choix((), None, None)   # what a file the kit cannot trust gives: no agent at all

# Never through a link, never waiting on a pipe. Windows has neither flag: see _ouvrir().
LECTURE = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


class Refus(ValueError):
    """A choice or a file the kit does not take; `code` says why in a word."""

    def __init__(self, code):
        ValueError.__init__(self, code)
        self.code = code


def systeme():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def ouvert(agent, os_=None, codex_ouvert=None):
    if agent != "codex":
        return True
    return (os_ or systeme()) in (CODEX_OUVERT if codex_ouvert is None else codex_ouvert)


def fichier(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".brain-in-a-box", "agents.json")


def valider(d, os_=None, codex_ouvert=None):
    """The Choix a parsed agents.json holds, or Refus("invalide") / Refus("ferme")."""
    if not isinstance(d, dict) or type(d.get("version")) is not int or d["version"] != 1:
        raise Refus("invalide")
    agents, prefere, home = d.get("agents"), d.get("prefere"), d.get("codex_home")
    if not (isinstance(agents, list) and agents and all(isinstance(a, str) and a in AGENTS for a in agents)
            and len(set(agents)) == len(agents) and isinstance(prefere, str) and prefere in agents):
        raise Refus("invalide")
    if ("codex" in agents) != (home is not None):
        raise Refus("invalide")
    if home is not None and not (isinstance(home, str) and os.path.isabs(home)):
        raise Refus("invalide")
    if not all(ouvert(a, os_, codex_ouvert) for a in agents):
        raise Refus("ferme")
    return Choix(tuple(a for a in AGENTS if a in agents), prefere, home)


def a_soi(st, euid):
    """Only this account (or root) could have written it: theirs, and not writable by group
    or others. POSIX: Windows has no such mode bits."""
    return st.st_uid in (euid, 0) and not st.st_mode & 0o022


def _ouvrir(p):
    """(fd, stat) of p, a regular file reached without a link; OSError otherwise."""
    if os.path.islink(p):   # O_NOFOLLOW closes the race where there is one
        raise OSError("%s is a link" % p)
    fd = os.open(p, LECTURE)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError("%s is not a regular file" % p)
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def lire(home=None, os_=None, codex_ouvert=None):
    """(Choix, etat). etat: "absent" (Claude alone), "ok", or why the file is not trusted, and
    then no agent (AUCUN): "illisible" (a link, a pipe, too big, not JSON), "droits" (another
    account could have changed it), "invalide", "ferme" (it lists an agent not open here)."""
    p = fichier(home)
    if not os.path.lexists(p):
        return DEFAUT, "absent"
    try:
        fd, st = _ouvrir(p)
        with os.fdopen(fd, "rb") as f:
            raw = f.read(FICHIER_MAX + 1)
        if os.name == "posix" and not a_soi(st, os.geteuid()):
            return AUCUN, "droits"
        if len(raw) > FICHIER_MAX:
            return AUCUN, "illisible"
        d = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return AUCUN, "illisible"
    try:
        return valider(d, os_, codex_ouvert), "ok"
    except Refus as e:
        return AUCUN, e.code


def noms(texte):
    """"claude", "Codex, claude" -> the names in the order given, once each; Refus("inconnu") otherwise."""
    out = []
    for n in texte.split(","):
        n = n.strip().lower()
        if n not in AGENTS:
            raise Refus("inconnu")
        if n not in out:
            out.append(n)
    return out


def codex_home(env=None, home=None):
    """$CODEX_HOME as Codex reads it, else ~/.codex: the scheduler does not see an exported one."""
    h = ((os.environ if env is None else env).get("CODEX_HOME") or "").strip()
    if not h:
        return os.path.join(home or os.path.expanduser("~"), ".codex")
    if not os.path.isabs(h):
        raise Refus("codex_home")
    return os.path.normpath(h)


def choisir(agents, prefere, existant, os_=None, codex_ouvert=None, env=None, home=None):
    """Resolves the installer's choice. `agents` and `prefere` as typed ("" when not given),
    `existant` what lire() returned. (Choix, source), source "defaut" (nothing chosen, no
    file), "fichier" (the choice made before) or "choix"; Refus otherwise."""
    agents, prefere = agents.strip(), prefere.strip().lower()
    deja, etat = existant
    if not agents and not prefere:
        if etat == "absent":
            return DEFAUT, "defaut"
        if etat != "ok":
            raise Refus("fichier:" + etat)
        return deja, "fichier"
    if agents:
        choisis = noms(agents)
    elif etat in ("ok", "absent"):
        choisis = list(deja.agents)
    else:
        raise Refus("fichier:" + etat)
    if prefere and prefere not in AGENTS:
        raise Refus("inconnu")
    if not all(ouvert(n, os_, codex_ouvert) for n in choisis):
        raise Refus("ferme")
    if not prefere:
        prefere = deja.prefere if etat == "ok" and deja.prefere in choisis else choisis[0]
    if prefere not in choisis:
        raise Refus("prefere")
    ch = codex_home(env, home) if "codex" in choisis else None
    return Choix(tuple(a for a in AGENTS if a in choisis), prefere, ch), "choix"


def _de_cote(p):
    """Keeps what is at p next to it, agents.json.bak-<date> (0600), without reading through a
    link: a regular file's bytes, a link's target as text. A pipe or the like holds nothing."""
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode):
        src = None
    elif stat.S_ISREG(st.st_mode):
        src = os.fdopen(_ouvrir(p)[0], "rb")   # unreadable: OSError, nothing replaced
    else:
        return
    try:
        stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
        bak = "%s.bak-%s" % (p, stamp)
        while os.path.lexists(bak):
            bak, n = "%s.bak-%s-%d" % (p, stamp, n), n + 1
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(bak, flags, 0o600), "wb") as f:
            if src is None:
                f.write(b"agents.json was a link to " + os.fsencode(os.readlink(p)) + b"\n")
            else:
                shutil.copyfileobj(src, f)
    finally:
        if src is not None:
            src.close()


def ecrire(choix, pose_par, existant, home=None):
    """Writes agents.json in one step, 0600; a link in its place is replaced, never followed.
    What it replaces that was not a choice the kit trusted is first kept next to it
    (_de_cote). False when the file already holds this choice: nothing is written then."""
    deja, etat = existant
    if etat == "ok" and deja == choix:
        return False
    p = fichier(home)
    d = {"version": 1, "agents": list(choix.agents), "prefere": choix.prefere}
    if choix.codex_home is not None:
        d["codex_home"] = choix.codex_home
    d.update(pose_par=pose_par, date=time.strftime("%Y-%m-%d"))
    dossier = os.path.dirname(p)
    os.makedirs(dossier, exist_ok=True)
    if etat not in ("ok", "absent") and os.path.lexists(p):
        _de_cote(p)
    fd, tmp = tempfile.mkstemp(dir=dossier, prefix=".agents.json.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


PAS_ECRIT = object()   # _source(): the session file is not there yet


def _source(chemin):
    """What the first line of a session file says of the session: its source ("cli", "exec"...),
    PAS_ECRIT when the file is not there, None when it cannot tell (a link, not a regular file, not a
    session_meta line, a source that is not a word). Never waits on a pipe."""
    try:
        fd, _ = _ouvrir(chemin)
    except FileNotFoundError:
        return PAS_ECRIT
    except (OSError, ValueError):   # ValueError: a NUL in the path
        return None
    with os.fdopen(fd, "rb") as f:
        ligne = f.readline(LIGNE_MAX + 1)
    if len(ligne) > LIGNE_MAX:
        return None
    try:
        d = json.loads(ligne.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    p = d.get("payload") if isinstance(d, dict) and d.get("type") == "session_meta" else None
    source = p.get("source") if isinstance(p, dict) else None
    return source if isinstance(source, str) else None


def codex_hook(nom, payload, env=None, home=None, codex_ouvert=None):
    """Whether the hook `nom`, run by Codex (hooks.json, --agent codex), acts on what Codex gave it
    (docs/proposals/codex.md, §3.4). Never in a run without tools (BRAIN_HEADLESS=1, the kit's own;
    claude -p), nor when agents.json does not list Codex or cannot be trusted: nothing read, nothing
    written then. Otherwise Codex does run the kit's hooks, and vu() notes it (§3.3). Then only for a
    session the user types in: one Codex keeps a file of (none: --ephemeral), under a plain id, a
    prompt the user typed (agent_id: another agent's), a file whose first line says "cli" or "vscode",
    or a file not written yet (Codex may run a session's first hooks before it writes it)."""
    env = os.environ if env is None else env
    if env.get("BRAIN_HEADLESS") == "1" or env.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":
        return False
    if "codex" not in lire(home, codex_ouvert=codex_ouvert)[0].agents:
        return False
    try:
        vu(nom, home)
    except (OSError, ValueError):
        pass   # a sign for later checks: the hook still does its work
    if not isinstance(payload, dict) or payload.get("agent_id"):
        return False
    sid, chemin = payload.get("session_id"), payload.get("transcript_path")
    if not (isinstance(sid, str) and ID.fullmatch(sid) and isinstance(chemin, str) and chemin):
        return False
    source = _source(chemin)
    return source is PAS_ECRIT or source in SOURCES_TAPEES


def vus(home=None):
    """{hook: when Codex last ran it, UTC} from codex-hooks-vus.json; {} when there is none to trust."""
    try:
        fd, _ = _ouvrir(os.path.join(os.path.dirname(fichier(home)), VUS))
        with os.fdopen(fd, "rb") as f:
            d = json.loads(f.read(FICHIER_MAX).decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    h = d.get("hooks") if isinstance(d, dict) else None
    return {k: v for k, v in h.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(h, dict) else {}


def vu(nom, home=None):
    """Notes that Codex ran the hook `nom` now: codex-hooks-vus.json, beside agents.json, written in one
    step (0600); a link in its place is replaced, never followed. Hooks running together may each miss
    the other's line once: each one's own time is what counts."""
    p = os.path.join(os.path.dirname(fichier(home)), VUS)
    d = vus(home)
    d[nom] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dossier = os.path.dirname(p)
    os.makedirs(dossier, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dossier, prefix=".codex-hooks-vus.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": 1, "hooks": d}, f, sort_keys=True)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv):
    """_agents.py choisir --agents=<...> --prefere=<...> --pose-par=install.sh|install.ps1

    Prints one ASCII line for the installer, "agents=claude,codex prefere=claude
    source=choix ecrit=oui", or "erreur=<code>" with exit code 2: the installer says it
    in its own language."""
    ap = argparse.ArgumentParser(prog="_agents.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("choisir")
    c.add_argument("--agents", default="")
    c.add_argument("--prefere", default="")
    c.add_argument("--pose-par", required=True, choices=POSE_PAR)
    a = ap.parse_args(argv)
    try:
        existant = lire()
        choix, source = choisir(a.agents, a.prefere, existant)
        ecrit = source == "choix" and ecrire(choix, a.pose_par, existant)
    except Refus as e:
        print("erreur=" + e.code)
        return 2
    except OSError as e:
        print("erreur=ecriture:" + type(e).__name__)
        return 2
    print("agents=%s prefere=%s source=%s ecrit=%s"
          % (",".join(choix.agents), choix.prefere, source, "oui" if ecrit else "non"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
