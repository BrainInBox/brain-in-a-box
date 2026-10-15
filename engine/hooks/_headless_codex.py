"""Asks the user's Codex CLI one question, with nothing but the question.

Codex has no "no tool" switch and no start-up line to check, and a tool that runs does not
show in the `codex exec --json` stream (docs/proposals/codex.md, §2.2 and step 0). So "no tool"
holds by prevention alone, checked before every call, every check failing closed:
- every feature of engine/codex-features.json turned off, and any feature Codex still turns on
  pauses Codex (decision 1: the list only grows; taking a name out never loosens the guard);
- a read-only sandbox, approvals never asked, no web search, agents, skills, project doc,
  developer instructions or notify program, the user's config.toml and rules ignored, and the
  MCP servers of the layers the call reads turned off by name;
- the provider and its URLs fixed by -c, which only requirements.toml and a legacy
  managed_config.toml (file or macOS profile) pass over: either one present pauses Codex;
- a ChatGPT login only (auth.json, then `codex login status` compared to fixed lines, never
  copied): an API key would be billed, and no key of the caller's environment reaches Codex;
- the names $CODEX_HOME/.env would add to Codex's own environment, checked;
- a fresh private folder, an allow-list environment, the prompt on stdin, --ephemeral (no
  session file, nothing of the vault left in Codex's databases, step 0).
Then the stream: thread.started first, one turn, answer, reasoning and warning items only (any
other item ends the run), and the answer file must be the streamed answer, a JSON object.

    answer = run(prompt, schema, system_prompt, codex_home="/Users/x/.codex", timeout=300)
    answer.output       # the JSON object Codex returned, already parsed
    check(codex_home)   # the same checks, without asking anything

Not wired yet (phase 3 does it), and Codex stays closed on every system (_agents.py). Its
process handling is _headless_claude.py's. Not a hook: installed next to them, never registered.
"""
import json
import os
import plistlib
import re
import shutil
import stat
import sys
import tempfile
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path

from _headless_claude import HeadlessError, _brief, _label, _login_name, supervise

WINDOWS = os.name == "nt"
# The version step 0 checked every option and setting below with.
MIN_CODEX = (0, 162, 1)
REQUIRED_FLAGS = ("--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
                  "--sandbox", "--cd", "--output-schema", "--output-last-message")
PROBE_TIMEOUT = 60
FILE_CAP = 1 << 20
ANSWER_CAP = 4_000_000
CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/"
FEATURES_FILE = "codex-features.json"
# The features that give the model a tool, run code, or reach a server of their own (step 0's
# classification, and the spike's list): the data file must keep them, and Codex must know them.
CORE = ("shell_tool", "view_image", "image_generation", "goals", "sleep_tool", "code_mode_host", "apps", "plugins",
        "remote_plugin", "multi_agent", "multi_agent_v2", "memories", "skill_search", "tool_suggest", "browser_use",
        "computer_use", "in_app_browser", "code_mode", "hooks")
# Stays on whatever -c says; adds no tool while shell_tool (in CORE) is off (step 0).
FORCED = ("unified_exec",)
SETTINGS = (
    'model_provider="openai"', 'openai_base_url=""', 'chatgpt_base_url="%s"' % CHATGPT_BASE_URL,
    'cli_auth_credentials_store="file"', 'approval_policy="never"', 'web_search="disabled"', "agents.enabled=false",
    "tools.experimental_request_user_input.enabled=false", "tools.update_plan.enabled=false",
    "skills.include_instructions=false", "skills.bundled.enabled=false", "project_doc_max_bytes=0",
    "include_environment_context=false", "include_permissions_instructions=false", "include_apps_instructions=false",
    "include_collaboration_mode_instructions=false",
    # A system config.toml's own, both passed after every turn: its text to the model, the
    # session (the vault) to its program (hooks/src/registry.rs, `legacy_notify_argv`).
    'developer_instructions=""', "notify=[]",
    'history.persistence="none"', "check_for_update_on_startup=false", "analytics.enabled=false",
    "feedback.enabled=false")
FEATURE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
# A row of `codex features list` (0.162.1 lists guardianv2.thread_context, at the removed stage).
ROW_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
# What -c can name (the key is split on dots) and what step 0 saw.
MCP_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
EFFORT = re.compile(r"[a-z]{1,16}")
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.]+)?")
LOGIN_OK = "Logged in using ChatGPT"
LOGIN_BILLED = ("Logged in using an API key", "Logged in using workload identity", "Logged in using access token",
                "Logged in using personal access token", "Logged in using Amazon Bedrock")
# The items of an answer: the message, its reasoning, warnings ("Code Mode is unavailable...").
ITEMS = ("agent_message", "reasoning", "error")
QUOTA_RE = re.compile(r"hit your (usage limit|spend cap)|usage limit|quota exceeded|upgrade to plus", re.I)
AUTH_RE = re.compile(r"\b401\b|unauthori[sz]ed|missing bearer|could not be refreshed|sign in again|log ?in again|"
                     r"not logged in", re.I)
PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy",
              "no_proxy", "SSL_CERT_FILE", "CODEX_CA_CERTIFICATE")
WINDOWS_VARS = ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "SystemRoot", "SystemDrive",
                "windir", "PATHEXT", "COMSPEC", "USERNAME", "ProgramFiles", "ProgramW6432", "ProgramData")
ENV_NAMES = ("HOME", "PATH", "LANG", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "BRAIN_HEADLESS") + WINDOWS_VARS + PROXY_VARS
EXTRA_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
# Where an administrator's layers live (None: this system's; tests point them elsewhere).
MANAGED = None
MDM = None

Answer = namedtuple("Answer", "output version cost_usd num_turns tokens")
Cage = namedtuple("Cage", "work schema system answer env empty_env")


def _read_small(path, cap=FILE_CAP):
    """A small regular file's text: never waits on a pipe, never reads more than cap."""
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise OSError("%s is not a regular file" % path)
        data = f.read(cap + 1)
    if len(data) > cap:
        raise ValueError("%s is too big" % path)
    return data.decode("utf-8")


def _settings_in(text):
    return any(l.strip() and not l.strip().startswith("#") for l in text.splitlines())


# --------------------------------------------------------------------------- the binary

def find_codex():
    """argv of the user's codex (the official installer's place first), or None."""
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        local = os.path.join(base, "Programs", "OpenAI", "Codex", "bin", "codex.exe")
        if os.path.isfile(local):
            return [local]
        found = shutil.which("codex.exe", path=os.environ.get("PATH", "")) if os.environ.get("PATH") else None
        return [found] if found and found.lower().endswith(".exe") else None
    local = Path.home() / ".local" / "bin" / "codex"
    if local.is_file() and os.access(str(local), os.X_OK):
        return [str(local)]
    # launchd and the Task Scheduler start with a bare PATH. An empty entry would mean the current folder.
    dirs = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep) + list(EXTRA_DIRS) if d)
    found = shutil.which("codex", path=dirs) if dirs else None
    return [found] if found else None


def _codex(command):
    """argv of the codex to run: `command` (tests, the CI's wrapper) or the user's."""
    argv0 = list(command) if command else find_codex()
    if not argv0:
        raise HeadlessError("start", "Codex not found (~/.local/bin/codex or PATH)")
    if WINDOWS and not argv0[0].lower().endswith(".exe"):
        # npm's codex.cmd goes through cmd.exe, which reads our arguments as its own syntax.
        raise HeadlessError("start", "%s is not an .exe" % os.path.basename(argv0[0]))
    return argv0


def _codex_home(codex_home):
    """The CODEX_HOME agents.json recorded: Codex refuses one that is not a folder, and its login is there."""
    if not codex_home or not os.path.isabs(codex_home) or not os.path.isdir(codex_home):
        raise HeadlessError("auth", "Codex's folder (CODEX_HOME) is not there: run `codex login` with your ChatGPT "
                                    "account, then the install again", "logged_out")
    return os.path.normpath(codex_home)


def child_path(program):
    """PATH of the child: the folder of codex (and of its link target) and the system's, nothing the user added."""
    dirs = [os.path.dirname(os.path.abspath(program)), os.path.dirname(os.path.realpath(program))]
    if WINDOWS:
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        dirs += [os.path.join(root, "System32"), root, os.path.join(root, "System32", "Wbem")]
    else:
        dirs += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return os.pathsep.join(dict.fromkeys(dirs))


def child_env(tmp, path, codex_home):
    """The child's whole environment, an allow-list: no OPENAI_API_KEY, CODEX_API_KEY,
    CODEX_ACCESS_TOKEN or OPENAI_BASE_URL (Codex would use the key before the login, and bill
    it), no RUST_LOG, no other variable of the caller's."""
    env = {"HOME": str(Path.home()), "PATH": path, "LANG": os.environ.get("LANG") or "en_US.UTF-8",
           "CODEX_HOME": codex_home, "BRAIN_HEADLESS": "1"}
    user = _login_name()
    if user:
        env["USER"] = env["LOGNAME"] = user
    if WINDOWS:
        env.update({k: os.environ[k] for k in WINDOWS_VARS if os.environ.get(k)})
        env["TEMP"] = env["TMP"] = tmp
    else:
        env["TMPDIR"] = tmp
    env.update({k: os.environ[k] for k in PROXY_VARS if os.environ.get(k)})
    return env


# --------------------------------------------------------------------------- the settings

def _features_path(path):
    if path:
        return path
    here = os.path.dirname(os.path.abspath(__file__))
    # Installed next to the hooks; engine/ in the repository.
    for p in (os.path.join(here, FEATURES_FILE), os.path.join(os.path.dirname(here), FEATURES_FILE)):
        if os.path.isfile(p):
            return p
    return os.path.join(here, FEATURES_FILE)


def load_features(path=None):
    """The names engine/codex-features.json turns off, or HeadlessError (unsupported, features)."""
    def bad(why):
        return HeadlessError("unsupported", "%s %s: Codex is paused until the install is run again"
                                            % (FEATURES_FILE, why), "features")
    try:
        d = json.loads(_read_small(_features_path(path), 256 * 1024))
    except (OSError, ValueError, RecursionError):
        raise bad("is missing or unreadable")
    if not isinstance(d, dict) or type(d.get("version")) is not int or d["version"] != 1:
        raise bad("is not a version 1 list")
    names = d.get("coupees")
    if not (isinstance(names, list) and names and all(isinstance(n, str) and FEATURE_NAME.fullmatch(n) for n in names)
            and len(set(names)) == len(names)):
        raise bad("holds something that is not a feature name")
    missing = [n for n in CORE if n not in names]
    if missing:
        raise bad("lacks " + ", ".join(missing))
    return tuple(names)


def config_flags(coupees, mcp_off=(), instructions=None, effort=None):
    """Every -c of the call: our features off, SETTINGS, the instructions file, the effort and
    the MCP servers turned off. Values are TOML: a JSON string is a TOML basic string."""
    items = ["features.%s=false" % n for n in coupees] + list(SETTINGS)
    if instructions is not None:
        items.append("model_instructions_file=" + json.dumps(str(instructions)))
    if effort is not None:
        items.append("model_reasoning_effort=" + json.dumps(effort))
    items += ["mcp_servers.%s.enabled=false" % n for n in mcp_off]
    out = []
    for item in items:
        out += ["-c", item]
    return out


def exec_argv(cage, coupees, mcp_off, model=None, effort=None):
    """codex's arguments, without the program itself. The prompt is `-`: stdin only (an argument
    and stdin would both be sent), whatever its size."""
    a = ["exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
         "-s", "read-only", "-C", cage.work, "--output-schema", cage.schema, "-o", cage.answer]
    if model:
        a += ["-m", model]
    return a + config_flags(coupees, mcp_off, cage.system, effort) + ["-"]


def strict_schema(schema, where="$"):
    """Codex sends the schema in strict mode (step 0's request): every object closed, every
    property required. ValueError otherwise, before anything runs: the service would refuse it."""
    if isinstance(schema, list):
        for i, sub in enumerate(schema):
            strict_schema(sub, "%s[%d]" % (where, i))
        return
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        if schema.get("additionalProperties") is not False:
            raise ValueError("%s: Codex answers in strict mode: additionalProperties must be false" % where)
        if sorted(schema.get("required") or []) != sorted(props):
            raise ValueError("%s: Codex answers in strict mode: every property must be required" % where)
    for key, sub in schema.items():
        if key in ("properties", "$defs", "definitions") and isinstance(sub, dict):
            for name, s in sub.items():
                strict_schema(s, "%s.%s" % (where, name))
        elif key in ("items", "anyOf", "oneOf", "allOf"):
            strict_schema(sub, "%s.%s" % (where, key))


# --------------------------------------------------------------------------- the checks before

def _probe(argv, env, cwd, what):
    """(exit code, stdout, stderr lines) of a short probe. Its stderr is the caller's to compare, never to copy."""
    lines = []
    code, timed_out, err = supervise(argv, env, cwd, "", PROBE_TIMEOUT, lines.append)
    if timed_out:
        raise HeadlessError("start", "codex %s did not answer in %ds" % (what, PROBE_TIMEOUT))
    return code, "\n".join(lines), err


def check_version(argv0, env, cwd):
    _, out, _ = _probe(argv0 + ["--version"], env, cwd, "--version")
    m = VERSION.search(out)
    if not m:
        raise HeadlessError("start", "codex --version said %r" % _brief(out, 120))
    if tuple(int(x) for x in m.groups()) < MIN_CODEX:
        raise HeadlessError("unsupported", "Codex %s is older than %s: update it (the install does)"
                                           % (m.group(0), ".".join(map(str, MIN_CODEX))), "version")
    _, out, _ = _probe(argv0 + ["exec", "--help"], env, cwd, "exec --help")
    words = set(re.split(r"[^A-Za-z0-9-]+", out))
    missing = [f for f in REQUIRED_FLAGS if f not in words]
    if missing:
        raise HeadlessError("unsupported", "this codex exec lacks " + ", ".join(missing), "flags")
    return m.group(0)


def dotenv_names(codex_home):
    """Names $CODEX_HOME/.env gives Codex's own environment (it skips CODEX_*); values never read
    into anything. ValueError when a line cannot be read the way the kit reads it."""
    p = os.path.join(codex_home, ".env")
    if not os.path.lexists(p):
        return []
    names = []
    for raw in _read_small(p).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, eq, _ = line.partition("=")
        name = name.strip()
        if not eq or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
            raise ValueError("a line of .env")
        if not name.upper().startswith("CODEX_"):
            names.append(name)
    return names


def _dotenv_refused(name):
    n = name.upper()
    return (n == "RUST_LOG" or n.startswith(("OPENAI_", "SSL_")) or n.endswith("_PROXY") or "CA" in n
            or n not in {x.upper() for x in ENV_NAMES})


def check_dotenv(codex_home):
    try:
        refused = sorted({_label(n) for n in dotenv_names(codex_home) if _dotenv_refused(n)})
    except (OSError, ValueError):
        raise HeadlessError("unsupported", "Codex's .env (in CODEX_HOME) cannot be read the way Codex reads it: "
                                           "Codex is paused while it is there", "dotenv")
    if refused:
        raise HeadlessError("unsupported", "Codex's .env (in CODEX_HOME) sets %s, which could send the question "
                                           "elsewhere or bill it: Codex is paused while it does"
                            % ", ".join(refused[:5]), "dotenv")


def _managed_files():
    if MANAGED is not None:
        return [Path(p) for p in MANAGED]
    if WINDOWS:
        root = Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "OpenAI" / "Codex"
    else:
        root = Path("/etc/codex")
    return [root / "requirements.toml", root / "managed_config.toml"]


def _mdm_files():
    if MDM is not None:
        return [Path(p) for p in MDM]
    if sys.platform != "darwin":
        return []
    base = Path("/Library/Managed Preferences")
    user = _login_name()
    return [base / "com.openai.codex.plist"] + ([base / user / "com.openai.codex.plist"] if user else [])


def check_managed():
    """requirements.toml pins or bounds what -c sets (provider, URLs, features, sandbox...), a
    legacy managed_config.toml passes over every -c: either one with a setting pauses Codex."""
    for p in _managed_files():
        if not os.path.lexists(str(p)):
            continue
        try:
            text = _read_small(p)
        except (OSError, ValueError):
            text = "unreadable"
        if _settings_in(text):
            raise HeadlessError("unsupported", "%s (%s) sets Codex's settings above the kit's: Codex is paused "
                                               "while it does" % (p.name, p.parent), "managed")
    for p in _mdm_files():
        if not os.path.lexists(str(p)):
            continue
        try:
            with open(str(p), "rb") as f:
                d = plistlib.loads(f.read(FILE_CAP))
        except Exception:
            d = {"config_toml_base64": "unreadable"}
        if isinstance(d, dict) and (d.get("config_toml_base64") or d.get("requirements_toml_base64")):
            raise HeadlessError("unsupported", "a configuration profile (%s) sets Codex's settings above the kit's: "
                                               "Codex is paused while it does" % p.name, "managed")


def check_auth_file(codex_home):
    """auth.json says a ChatGPT login, with what refreshes it. Nothing else of it is read: a real
    ChatGPT login also stores an exchanged OPENAI_API_KEY, which Codex does not use for the model."""
    try:
        d = json.loads(_read_small(os.path.join(codex_home, "auth.json")))
    except (OSError, ValueError, RecursionError):
        raise HeadlessError("auth", "Codex is not logged in (no readable auth.json): run `codex login` with your "
                                    "ChatGPT account", "logged_out")
    mode = d.get("auth_mode") if isinstance(d, dict) else None
    if mode == "chatgpt":
        tokens = d.get("tokens")
        if isinstance(tokens, dict) and isinstance(tokens.get("refresh_token"), str) and tokens["refresh_token"].strip():
            return
        raise HeadlessError("auth", "Codex's ChatGPT login is incomplete: run `codex login` again", "logged_out")
    if not mode and not (isinstance(d, dict) and d.get("OPENAI_API_KEY")):
        raise HeadlessError("auth", "Codex is not logged in: run `codex login` with your ChatGPT account",
                            "logged_out")
    raise HeadlessError("auth", "Codex is logged in another way than with a ChatGPT account (an API key would be "
                                "billed): run `codex logout`, then `codex login` with your ChatGPT account.", "billed")


def check_login(argv0, env, cwd, flags):
    """`codex login status`, read as Codex reads auth.json for the call. Its line, on stderr, is
    compared to fixed ones and never kept: an API key's shows part of the key."""
    code, _, err = _probe(argv0 + flags + ["login", "status"], env, cwd, "login status")
    first = next((l.strip() for l in err if l.strip()), "")
    if code == 0 and first == LOGIN_OK:
        return
    if first.startswith(LOGIN_BILLED):
        raise HeadlessError("auth", "Codex is logged in another way than with a ChatGPT account (an API key would "
                                    "be billed): run `codex logout`, then `codex login` with your ChatGPT account.",
                            "billed")
    raise HeadlessError("auth", "Codex's ChatGPT login could not be confirmed (codex login status): run "
                                "`codex login` with your ChatGPT account", "logged_out")


def check_features(text, coupees):
    """`codex features list` with our -c: every CORE feature known to this Codex, and none on but
    FORCED and the removed stage (which ignores -c and does nothing, step 0). Fail closed: a
    line the kit cannot read, a feature it does not know turned on."""
    def bad(why):
        return HeadlessError("unsupported", why + ": Codex is paused until the kit knows this version "
                                                  "(re-run the install)", "features")
    rows = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 3 or parts[-1] not in ("true", "false") or not ROW_NAME.fullmatch(parts[0]):
            raise bad("codex features list printed a line the kit cannot read")
        rows[parts[0]] = (" ".join(parts[1:-1]), parts[-1] == "true")
    missing = [n for n in CORE if n not in rows]
    if missing:
        raise bad("this Codex does not list %s, which the kit turns off" % ", ".join(missing))
    on = sorted(n for n, (stage, value) in rows.items() if value and stage != "removed" and n not in FORCED)
    stuck, unknown = [n for n in on if n in coupees], [n for n in on if n not in coupees]
    if stuck:
        raise bad("Codex keeps %s on although the kit turns it off" % ", ".join(stuck[:8]))
    if unknown:
        raise bad("Codex turns on %s, which the kit does not know" % ", ".join(unknown[:8]))


def _mcp(argv0, env, cwd, flags):
    """[(name, enabled)] of `codex mcp list --json`."""
    code, out, _ = _probe(argv0 + flags + ["mcp", "list", "--json"], env, cwd, "mcp list")
    try:
        v = json.loads(out) if code == 0 else None
    except ValueError:
        v = None
    if not isinstance(v, list) or not all(isinstance(s, dict) and isinstance(s.get("name"), str)
                                          and isinstance(s.get("enabled"), bool) for s in v):
        raise HeadlessError("unsupported", "codex mcp list did not answer as the kit reads it: Codex is paused",
                            "mcp")
    return [(s["name"], s["enabled"]) for s in v]


def declared_by_user(codex_home, name):
    """Whether the user's config.toml defines this MCP server: the call ignores that file."""
    try:
        text = _read_small(os.path.join(codex_home, "config.toml"))
    except (OSError, ValueError):
        return False
    n = re.escape(name)
    if re.search(r"mcp_servers\s*\.\s*([\"']?)%s\1\s*[\].=]" % n, text):
        return True
    if re.search(r"(?m)^\s*\[\s*mcp_servers\s*\]|mcp_servers\s*=", text):
        return re.search(r"(?<![A-Za-z0-9_-])([\"']?)%s\1\s*=" % n, text) is not None
    return False


def check_mcp(argv0, cage, codex_home, flags_for):
    """The MCP servers the call would start: those of the layers it reads (system, project),
    listed with an empty CODEX_HOME, are turned off by name. Not the user's config.toml's: the
    call ignores that file, and naming one of its servers there would break the call (Codex
    reads a lone `enabled` as a server without a command). Any server still on with the user's
    CODEX_HOME must be one of that file's. Returns the names turned off."""
    names = [n for n, _ in _mcp(argv0, cage.empty_env, cage.work, flags_for(()))]
    odd = [_label(n) for n in names if not MCP_NAME.fullmatch(n)]
    if odd:
        raise HeadlessError("unsupported", "an MCP server of Codex's system settings has a name the kit cannot turn "
                                           "off (%s): Codex is paused" % ", ".join(odd[:5]), "mcp")
    flags = flags_for(names)
    still = [_label(n) for n, on in _mcp(argv0, cage.empty_env, cage.work, flags) if on]
    if still:
        raise HeadlessError("unsupported", "MCP server %s stays on: Codex is paused" % ", ".join(still[:5]), "mcp")
    unknown = [_label(n) for n, on in _mcp(argv0, cage.env, cage.work, flags)
               if on and n not in names and not declared_by_user(codex_home, n)]
    if unknown:
        raise HeadlessError("unsupported", "MCP server %s comes from settings the kit cannot turn off: Codex is "
                                           "paused" % ", ".join(unknown[:5]), "mcp")
    return names


def preflight(argv0, cage, codex_home, coupees, effort=None):
    """Every check before a question, as the question runs. (version, MCP servers to turn off)."""
    version = check_version(argv0, cage.env, cage.work)
    check_dotenv(codex_home)
    check_managed()
    check_auth_file(codex_home)

    def flags_for(mcp_off):
        return config_flags(coupees, mcp_off, cage.system, effort)
    check_login(argv0, cage.env, cage.work, flags_for(()))
    code, out, _ = _probe(argv0 + flags_for(()) + ["features", "list"], cage.env, cage.work, "features list")
    if code != 0:
        raise HeadlessError("unsupported", "codex features list failed (exit code %s; is Codex's config.toml "
                                           "valid?): Codex is paused" % code, "features")
    check_features(out, coupees)
    return version, check_mcp(argv0, cage, codex_home, flags_for)


# --------------------------------------------------------------------------- the stream

def _failure(message):
    said = _brief(message)
    if QUOTA_RE.search(said):
        return HeadlessError("quota", said[:200])
    if AUTH_RE.search(said):
        return HeadlessError("auth", said[:200])
    return HeadlessError("api", said[:200] or "turn failed")


class Watch:
    """codex exec --json, line by line (exec/src/exec_events.rs)."""

    def __init__(self):
        self.thread, self.turns, self.answer, self.usage, self.failed, self.init = None, 0, None, None, None, None
        self.errors = []

    def line(self, line):
        """False once the run must stop: Codex did not start, or did not behave, as required."""
        try:
            v = json.loads(line)
        except ValueError:
            return self.ok()
        if not isinstance(v, dict):
            return self.ok()
        kind = v.get("type")
        if self.thread is None:
            if kind == "thread.started" and isinstance(v.get("thread_id"), str):
                self.thread = v["thread_id"]
            else:
                self.init = HeadlessError("init", "%s before thread.started" % _label(kind))
            return self.ok()
        if kind == "turn.started":
            self.turns += 1
            if self.turns > 1:
                self.init = HeadlessError("init", "a second turn")
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = v.get("item") if isinstance(v.get("item"), dict) else {}
            if item.get("type") not in ITEMS:
                # A tool's item, or one this version brings that the kit does not know.
                self.init = HeadlessError("init", "tool used: %s" % _label(item.get("type")))
            elif item["type"] == "agent_message" and kind == "item.completed" and isinstance(item.get("text"), str):
                self.answer = item["text"]
        elif kind == "error":
            self.errors.append(str(v.get("message") or ""))   # "Reconnecting... 1/5": a warning, unless the turn fails
        elif kind == "turn.failed":
            err = v.get("error") if isinstance(v.get("error"), dict) else {}
            self.failed = str(err.get("message") or "turn failed")
        elif kind == "turn.completed":
            usage = v.get("usage") if isinstance(v.get("usage"), dict) else {}
            self.usage = {k: n for k, n in usage.items() if isinstance(k, str) and type(n) is int}
        else:
            self.init = HeadlessError("init", "unknown event %s" % _label(kind))
        return self.ok()

    def ok(self):
        return not isinstance(self.init, HeadlessError)

    def conclude(self, timed_out, code, answer_path, version):
        """The answer, or HeadlessError. Fail closed: no thread, no single clean turn, no answer."""
        if isinstance(self.init, HeadlessError):
            raise self.init
        if timed_out:
            raise HeadlessError("timeout", "no answer in time")
        if self.thread is None:
            raise HeadlessError("start", "Codex did not start (exit code %s)" % code)
        if self.failed is not None:
            raise _failure(self.failed)
        if self.usage is None:
            last = self.errors[-1] if self.errors else ""
            raise _failure(last) if last and (QUOTA_RE.search(last) or AUTH_RE.search(last)) else \
                HeadlessError("api", "no result" + (" (%s)" % _brief(last, 160) if last else ""))
        if self.answer is None:
            raise HeadlessError("invalid", "no answer in the stream")
        try:
            text = _read_small(answer_path, ANSWER_CAP)
        except (OSError, ValueError):
            raise HeadlessError("invalid", "no answer file")
        if text.strip() != self.answer.strip():
            raise HeadlessError("invalid", "the answer file is not the streamed answer")
        try:
            out = json.loads(text)
        except ValueError:
            raise HeadlessError("invalid", "the answer is not JSON")
        if not isinstance(out, dict):
            raise HeadlessError("invalid", "the answer is not a JSON object")
        return Answer(out, version, None, self.turns, self.usage)


# --------------------------------------------------------------------------- the call

@contextmanager
def _cage(argv0, codex_home, work_root):
    """Codex's folders: fresh, private, outside any vault (under work_root when given), gone
    afterwards. The work folder is empty; `vide` is the empty CODEX_HOME the MCP check reads."""
    root = os.path.realpath(tempfile.mkdtemp(prefix="brain-codex-", dir=work_root))
    try:
        os.chmod(root, 0o700)
        work, tmp, empty = (os.path.join(root, n) for n in ("travail", "tmp", "vide"))
        for d in (work, tmp, empty):
            os.mkdir(d, 0o700)
        env = child_env(tmp, child_path(argv0[0]), codex_home)
        yield Cage(work, os.path.join(root, "schema.json"), os.path.join(root, "system.md"),
                   os.path.join(root, "answer.json"), env, dict(env, CODEX_HOME=empty))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _choices(system_prompt, model, effort):
    if not system_prompt.strip():
        raise ValueError("Codex refuses empty instructions")
    if model is not None and not MODEL_NAME.fullmatch(model):
        raise ValueError("not a model name: %r" % _brief(model, 80))
    if effort is not None and not EFFORT.fullmatch(effort):
        raise ValueError("not a reasoning effort: %r" % _brief(effort, 40))


def check(codex_home, command=None, *, work_root=None, features_file=None):
    """Whether this codex may be asked, without asking it anything: its version, or HeadlessError
    (start, unsupported, auth). The probes run as the question would."""
    argv0 = _codex(command)
    codex_home = _codex_home(codex_home)
    coupees = load_features(features_file)
    with _cage(argv0, codex_home, work_root) as cage:
        # Codex refuses an empty instructions file, and the probes read it.
        _write(cage.system, "Answer only with the JSON object of the schema.")
        return preflight(argv0, cage, codex_home, coupees)[0]


def run(prompt, schema, system_prompt, *, codex_home, timeout, model=None, effort=None, command=None,
        work_root=None, features_file=None):
    """One isolated question to Codex. Returns an Answer, or raises HeadlessError (ValueError for
    a schema, system prompt, model or effort the call cannot take). codex_home: agents.json's. command: argv
    that stands for codex (tests, the CI's wrapper); default, the user's codex."""
    strict_schema(schema)
    _choices(system_prompt, model, effort)
    argv0 = _codex(command)
    codex_home = _codex_home(codex_home)
    coupees = load_features(features_file)
    with _cage(argv0, codex_home, work_root) as cage:
        _write(cage.schema, json.dumps(schema, ensure_ascii=False))
        _write(cage.system, system_prompt)
        version, mcp_off = preflight(argv0, cage, codex_home, coupees, effort)
        watch = Watch()
        code, timed_out, _ = supervise(argv0 + exec_argv(cage, coupees, mcp_off, model, effort), cage.env, cage.work,
                                       prompt, timeout, watch.line)
        return watch.conclude(timed_out, code, cage.answer, version)
