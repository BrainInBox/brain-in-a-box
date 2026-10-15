"""Which AI agents the kit works with: Claude Code, Codex or both, and which one is
preferred for the tasks without tools (the reflection, the night check's review).

~/.brain-in-a-box/agents.json holds the choice and is its only source:

    {"version": 1, "agents": ["claude", "codex"], "prefere": "claude",
     "codex_home": "/Users/x/.codex", "pose_par": "install.sh", "date": "2026-10-15"}

Absent: Claude alone, exactly as before the file existed. Only the installers write it,
through `_agents.py choisir`, and only when someone chose: an install run without a
choice leaves no file behind. Everything else reads it with lire(), which falls back to
Claude alone when the file cannot be trusted, and says why.

Codex is closed on a system until its whole path is green there (docs/proposals/codex.md,
§1.1): every merged phase reaches users at once through master.zip, so asking for it is
refused there, and a file that lists it there is not trusted.

Not a hook: installed next to them, never registered in settings.json.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from collections import namedtuple

AGENTS = ("claude", "codex")
# The systems Codex can be chosen on: macOS and Linux open in phase 6, Windows in phase 7.
CODEX_OUVERT = frozenset()
FICHIER_MAX = 64 * 1024
POSE_PAR = ("install.sh", "install.ps1")


class Choix(namedtuple("Choix", "agents prefere codex_home")):
    """agents in AGENTS order; codex_home (absolute) only when Codex is one of them."""
    __slots__ = ()


DEFAUT = Choix(("claude",), "claude", None)


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


def lire(home=None, os_=None, codex_ouvert=None):
    """(Choix, etat). etat: "absent", "ok", or why the file is not trusted: "illisible",
    "invalide", "ferme" (it lists an agent not open on this system). Not trusted: Claude alone."""
    p = fichier(home)
    if not os.path.lexists(p):
        return DEFAUT, "absent"
    try:
        with open(p, "rb") as f:
            raw = f.read(FICHIER_MAX + 1)
        if len(raw) > FICHIER_MAX:
            return DEFAUT, "illisible"
        d = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError):
        return DEFAUT, "illisible"
    try:
        return valider(d, os_, codex_ouvert), "ok"
    except Refus as e:
        return DEFAUT, e.code


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


def ecrire(choix, pose_par, existant, home=None):
    """Writes agents.json in one step, 0600; a link in its place is replaced, never followed.
    A file it replaces that was not a choice the kit trusted is first kept next to it
    (agents.json.bak-<date>, 0600). False when the file already holds this choice: nothing
    is written then."""
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
    if etat not in ("ok", "absent") and os.path.isfile(p):
        with open(p, "rb") as f:
            old = f.read()
        stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
        bak = "%s.bak-%s" % (p, stamp)
        while os.path.lexists(bak):
            bak, n = "%s.bak-%s-%d" % (p, stamp, n), n + 1
        with os.fdopen(os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(old)
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
