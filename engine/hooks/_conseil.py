"""The advice Bibo gives at the start: which AI the brain should work with, from what is already on
this computer (Claude Code and its login, Codex and its ChatGPT login, the ChatGPT and Claude desktop
apps, Hermes Agent). It reads no token and opens no login file: Codex's auth.json and Hermes's .env
are only looked for, never opened; of `claude auth status` only authMethod is kept.

While Codex is closed on this system (_agents.py's CODEX_OUVERT, the one source), the advice never
offers Codex: it may say it is coming. Brain in a Box does not install Hermes: when it is there, one
more sentence says so, and that the evening reflection reads its conversations only where it does
(daily-reflection.py reads $HERMES_DB, else ~/.hermes/state.db).

The rules and the sentences are the JSON block CONSEIL_JSON below, which the app reads too
(app/src-tauri, include_str! of this file) and applies to what it probes itself: one source for both.
Rules are tried in order; the first whose every condition ("si") matches the facts gives the advice,
and the first of "hermes" that matches, Hermes's sentence.

    conseil()                       {"conseil": "claude" | "codex" | "les_deux", "phrase": ...,
                                     "hermes": ... or None, "vu": {fact: bool}}
    importer-memoire conseil [--json]

Not a hook: installed next to them, never registered in settings.json.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _agents  # noqa: E402

CONSEIL_JSON = r"""
{
  "version": 1,
  "faits": {
    "porte": "Codex may be chosen on this system (_agents.py's CODEX_OUVERT)",
    "claude": "Claude Code or the Claude desktop app is on this computer",
    "claude_connecte": "Claude Code is logged in (authMethod claude.ai or oauth_token)",
    "chatgpt": "the ChatGPT or Codex desktop app, Codex, or Codex's login is on this computer",
    "chatgpt_connecte": "Codex's auth.json exists (never opened)",
    "hermes": "Hermes Agent is on this computer",
    "hermes_lu": "the evening reflection reads Hermes's conversations: $HERMES_DB, else ~/.hermes/state.db, is there"
  },
  "regles": [
    {"si": {"porte": false, "claude_connecte": true}, "conseil": "claude", "phrase": "claude_connecte"},
    {"si": {"porte": false, "claude": true}, "conseil": "claude", "phrase": "claude_vu"},
    {"si": {"porte": false, "chatgpt": true}, "conseil": "claude", "phrase": "chatgpt_bientot"},
    {"si": {"porte": false}, "conseil": "claude", "phrase": "rien"},
    {"si": {"porte": true, "claude": true, "chatgpt": true}, "conseil": "les_deux", "phrase": "les_deux"},
    {"si": {"porte": true, "claude_connecte": true}, "conseil": "claude", "phrase": "claude_connecte"},
    {"si": {"porte": true, "claude": true}, "conseil": "claude", "phrase": "claude_vu"},
    {"si": {"porte": true, "chatgpt": true}, "conseil": "codex", "phrase": "codex"},
    {"si": {"porte": true}, "conseil": "claude", "phrase": "rien"}
  ],
  "hermes": [
    {"si": {"hermes": true, "hermes_lu": true}, "phrase": "hermes_lu"},
    {"si": {"hermes": true}, "phrase": "hermes"}
  ],
  "phrases": {
    "claude_connecte": "Tu as déjà Claude Code, connecté à ton abonnement : ton cerveau travaillera avec lui.",
    "claude_vu": "Tu utilises déjà Claude : ton cerveau travaillera avec lui (il te faudra un abonnement Pro ou Max pour Claude Code).",
    "chatgpt_bientot": "Tu utilises ChatGPT : ton cerveau marche avec Claude, il te faudra un abonnement Claude Pro ou Max (ton abonnement ChatGPT ne suffit pas). Codex, l'IA de ChatGPT, arrive bientôt ici. À la fin, tu pourras apporter à ton cerveau ce que ChatGPT sait de toi.",
    "rien": "Ton cerveau marche avec Claude : il te faudra un abonnement Claude Pro ou Max.",
    "codex": "Tu utilises ChatGPT : ton cerveau peut travailler avec Codex, l'IA de ChatGPT, sur ton abonnement ChatGPT. À la fin, tu pourras apporter à ton cerveau ce que ChatGPT sait de toi.",
    "les_deux": "Tu as Claude et ChatGPT : ton cerveau peut travailler avec les deux, Claude d'abord.",
    "hermes": "J'ai vu Hermes : je ne l'installe pas et n'y touche pas. Ses meilleures compétences arrivent dans Claude avec ton cerveau.",
    "hermes_lu": "J'ai vu Hermes : je ne l'installe pas et n'y touche pas. Ses meilleures compétences arrivent dans Claude avec ton cerveau, et chaque soir ton cerveau lit aussi ses conversations."
  }
}
"""
REGLES = json.loads(CONSEIL_JSON)
WINDOWS = os.name == "nt"


def decider(faits, regles=None):
    """(advice, sentence key) of the first rule whose conditions all match the facts."""
    regles = regles or REGLES
    for r in regles["regles"]:
        if all(bool(faits.get(k)) == v for k, v in r["si"].items()):
            return r["conseil"], r["phrase"]
    raise ValueError("no rule matches")   # the last rules have one condition: never reached


def _existe(*parts):
    """Whether something is there, without opening it nor following it further than lstat."""
    try:
        return os.path.lexists(os.path.join(*parts))
    except (OSError, ValueError):
        return False


def _apps(home, applications=None):
    """{"claude": bool, "chatgpt": bool} of the desktop apps. Linux has none of them."""
    found = {"claude": False, "chatgpt": False}
    if WINDOWS:
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        found["claude"] = _existe(local, "AnthropicClaude", "claude.exe") or \
            bool(glob.glob(os.path.join(glob.escape(local), "Packages", "Claude_*")))
        found["chatgpt"] = bool(glob.glob(os.path.join(glob.escape(local), "Packages", "OpenAI.ChatGPT-Desktop_*")))
    elif sys.platform == "darwin" or applications is not None:
        for root in applications if applications is not None else ("/Applications", os.path.join(home, "Applications")):
            found["claude"] |= _existe(root, "Claude.app", "Contents", "Info.plist")
            found["chatgpt"] |= _existe(root, "ChatGPT.app", "Contents", "Info.plist") or \
                _existe(root, "Codex.app", "Contents", "Info.plist")
    return found


def _hermes(home, env):
    h = (env.get("HERMES_HOME") or "").strip() or os.path.join(home, ".hermes")
    if any(_existe(h, n) for n in ("config.yaml", "state.db", "hermes-agent")):
        return True
    exe = "hermes.exe" if WINDOWS else "hermes"
    # Absolute folders only: an empty or relative entry is wherever this runs from.
    dirs = [d for d in env.get("PATH", "").split(os.pathsep) if d and os.path.isabs(d)]
    return any(os.path.isfile(os.path.join(d, exe)) for d in [os.path.join(home, ".local", "bin")] + dirs)


def _hermes_lu(home, env):
    """daily-reflection.py's HERMES_DB is there."""
    return _existe((env.get("HERMES_DB") or "").strip() or os.path.join(home, ".hermes", "state.db"))


def _methode(argv0):
    """authMethod of `claude auth status`, run as the questions are (a private folder, an allow-list
    environment); None when it cannot tell."""
    import _headless_claude as hc
    try:
        with hc._cage(argv0, None, None) as (work, env):
            _, out, _ = hc._capture(argv0 + ["auth", "status"], env, work)
    except (hc.HeadlessError, OSError):
        return None
    return hc.auth_method(out)


def sondes(home=None, env=None, claude=None, codex=None, applications=None, codex_ouvert=None):
    """The facts CONSEIL_JSON's rules read, from this computer. claude/codex: the argv standing for
    them (tests); applications: the folders holding macOS apps (tests)."""
    import _headless_claude as hc
    import _headless_codex as hx
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    claude = claude if claude is not None else hc.find_claude()
    codex = codex if codex is not None else hx.find_codex()
    try:
        auth = _existe(_agents.codex_home(env, home), "auth.json")
    except _agents.Refus:
        auth = False
    apps = _apps(home, applications)
    connecte = bool(claude) and _methode(list(claude)) in ("claude.ai", "oauth_token")
    return {"porte": _agents.ouvert("codex", codex_ouvert=codex_ouvert),
            "claude": bool(claude) or apps["claude"], "claude_connecte": connecte,
            "chatgpt": bool(codex) or auth or apps["chatgpt"], "chatgpt_connecte": auth,
            "hermes": _hermes(home, env), "hermes_lu": _hermes_lu(home, env)}


def conseil(faits=None, **kw):
    faits = faits if faits is not None else sondes(**kw)
    choix, cle = decider(faits)
    p = REGLES["phrases"]
    hermes = next((r["phrase"] for r in REGLES["hermes"] if all(bool(faits.get(k)) == v for k, v in r["si"].items())),
                  None)
    return {"conseil": choix, "phrase": p[cle], "hermes": p[hermes] if hermes else None, "vu": faits}
