"""Asks the user's Claude Code one question, with nothing but the question.

`claude -p` runs with no tool but its JSON answer, no MCP server, no hook, no
skill, no CLAUDE.md, none of the user's settings files, in a fresh private
folder, with a short allow-list for environment: whatever the text it reads
says, all it can do is answer. Its stream is checked line by line against that
(fail closed: no valid start-up line, no answer; a model other than the one asked
for, or a refusal of the model, which Claude Code would have another model answer
in its place, ends the run), and a claude logged in any
other way than a claude.ai subscription, or whose settings send it to a gateway
or a cloud provider, is never started, since an API key would be billed. A subscription reaches it two ways: the login `claude auth
login` stores, or on a machine without a browser the token `claude setup-token`
prints, in CLAUDE_CODE_OAUTH_TOKEN or, for the scheduled jobs, in
~/.brain-in-a-box/claude-oauth-token (mode 600), read only when Claude is asked:
the one credential the child is given, never in the jobs' own environment, its
value never written anywhere. Same contract as the app's copy (app/src-tauri/src/claude.rs, which
the repair asks through), which knows only the first; the test the_app_and_the_launcher_send_alike
checks both send the same argv and stdin.

    answer = run(prompt, schema, system_prompt, budget_usd=1.0, timeout=600)
    answer.output  # the JSON object Claude returned, already parsed
    run(prompt, ..., images=[png])   # PNG images it sees first (Bibo's guide's nightly check)
    check()        # the same checks of this claude, without asking it anything

Used by daily-reflection.py and by the night check's review (_controle_judge.py).
Not a hook: installed next to them, never registered in settings.json.
"""
import base64
import json
import os
import queue
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque, namedtuple
from contextlib import contextmanager
from pathlib import Path

WINDOWS = os.name == "nt"
# The first version checked with every flag below (the app's MIN_CLAUDE).
MIN_CLAUDE = (2, 1, 296)
REQUIRED_FLAGS = ("--restricted", "--safe-mode", "--tools", "--permission-prompts", "--json-schema",
                  "--max-budget-usd", "--no-session-persistence", "--strict-mcp-config")
# A question with images is read as stream-json.
IMAGE_FLAGS = REQUIRED_FLAGS + ("--input-format",)
PROBE_TIMEOUT = 60
# One stream line, and the whole stream: a result line carries the full answer.
LINE_CAP = 4_000_000
STREAM_CAP = 32_000_000
# Once claude has exited, what it wrote is read within this time; a child it
# left behind holding the pipes is not waited for.
DRAIN = 2.0
LIMIT_RE = re.compile(r"hit your .*limit|session limit|usage limit", re.I)
# The setup-token of `claude setup-token`, passed on when set: nothing else of its kind is.
# `claude auth status` says "claude.ai" for the stored login, and "oauth_token" for any other
# token: this one, but also a gateway's ANTHROPIC_AUTH_TOKEN. So "oauth_token" is the
# subscription only when the child was given this variable.
TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
# Where the scheduled jobs find it: an environment variable there would reach every step of the
# night (gbrain included) and, set with `launchctl setenv`, every program of the session.
TOKEN_FILE = (".brain-in-a-box", "claude-oauth-token")
# What claude's settings files may add to its environment ("env") that sends the question
# elsewhere or bills it, whatever `auth status` says: an API key, a gateway token or URL, a
# cloud provider (CLAUDE_CODE_USE_*), or an apiKeyHelper.
REROUTE_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AWS_BEARER_TOKEN_BEDROCK",
                "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN", "ANTHROPIC_AWS_API_KEY")
# Where an administrator's settings live (None: this OS's folder; tests point it elsewhere).
MANAGED_DIR = None
PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
              "https_proxy", "http_proxy", "all_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS")
WINDOWS_VARS = ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "SystemRoot",
                "SystemDrive", "windir", "PATHEXT", "COMSPEC", "USERNAME",
                # Where Claude Code looks for Git Bash, which it needs to start on Windows.
                "CLAUDE_CODE_GIT_BASH_PATH", "ProgramFiles", "ProgramW6432")

# rate_limit: what Claude Code said of the subscription's limits (status, five_hour, seven_day), or None.
Answer = namedtuple("Answer", "output version cost_usd num_turns rate_limit", defaults=(None,))


def _brief(value, cap=200):
    """A value for an error message: one line, printable, capped."""
    return re.sub(r"\s+", " ", "".join(c if c.isprintable() else " " for c in str(value))).strip()[:cap]


def _label(value):
    """A name for an error message when the model may have chosen it: a tool or a subtype, nothing else."""
    return re.sub(r"[^A-Za-z0-9_.:-]", "", str(value))[:64] or "?"


class HeadlessError(Exception):
    """kind: start, unsupported, auth, init, timeout, quota, api, invalid, refused (the model's
    safeguards; its detail, their category).
    reason, for the checks of the CLI only: version or flags (unsupported),
    logged_out or billed (auth)."""

    def __init__(self, kind, detail, reason=None):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.reason = kind, detail, reason

    @property
    def retryable(self):
        """Only a network or service hiccup: a second try at anything else gets the same answer."""
        return self.kind in ("timeout", "api")


def find_claude():
    """argv of the user's claude (~/.local/bin first, where the official installer puts it), or None."""
    exe = "claude.exe" if WINDOWS else "claude"
    local = Path.home() / ".local" / "bin" / exe
    if local.is_file() and os.access(str(local), os.X_OK):
        return [str(local)]
    # launchd and the Task Scheduler start with a bare PATH.
    dirs = os.environ.get("PATH", "").split(os.pathsep) + [str(Path.home() / ".claude" / "local"), "/opt/homebrew/bin", "/usr/local/bin"]
    # An empty entry would mean the current folder.
    found = shutil.which(exe, path=os.pathsep.join(d for d in dirs if d))
    # On Windows only an .exe: a claude.cmd would put our arguments through cmd.exe.
    return [found] if found and (not WINDOWS or found.lower().endswith(".exe")) else None


def base_argv(schema, system_prompt, *, budget_usd, max_turns=3, model=None, effort=None, with_images=False):
    """claude's options, without the program itself. The prompt goes through stdin, after every
    option: --disallowedTools takes several values and would swallow a prompt placed last.
    with_images: the question is a line of stream-json (stdin_text), said last. The same argv as the
    app's (claude.rs, argv), byte for byte: the schema compact, as json.dumps writes it."""
    a = ["-p", "--restricted", "--safe-mode", "--tools", "",
         "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Agent", "Task", "Skill",
         "--strict-mcp-config", "--disable-slash-commands", "--no-chrome",
         "--settings", '{"disableAllHooks":true}',
         "--permission-mode", "dontAsk", "--permission-prompts", "none", "--no-session-persistence",
         "--max-turns", str(max_turns), "--max-budget-usd", f"{budget_usd:.2f}"]
    if model:
        a += ["--model", model]
    if effort:
        a += ["--effort", effort]
    a += ["--json-schema", json.dumps(schema, separators=(",", ":")), "--append-system-prompt", system_prompt,
          "--output-format", "stream-json", "--verbose"]
    return a + (["--input-format", "stream-json"] if with_images else [])


def stdin_text(prompt, images=None):
    """What claude reads on stdin: the prompt as is or, with images (PNG, bytes), one line of
    stream-json, one user message: each image, then the prompt. Compact, the text unescaped but for
    what JSON requires: the same bytes as the app's (claude.rs, stdin)."""
    if not images:
        return prompt
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(png).decode("ascii")}} for png in images]
    content.append({"type": "text", "text": prompt})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}},
                      ensure_ascii=False, separators=(",", ":")) + "\n"


def child_path(program, git=None):
    """PATH of the child: the folder of claude (and of its link target) and the system's, nothing the user added.
    git: the git the caller found, when the PATH it was started with may not hold one (Windows)."""
    dirs = [os.path.dirname(os.path.abspath(program)), os.path.dirname(os.path.realpath(program))]
    if WINDOWS:
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        dirs += [os.path.join(root, "System32"), root, os.path.join(root, "System32", "Wbem")]
        # Claude Code finds Git Bash from git.exe, and does not start without it.
        git = git or shutil.which("git")
        if git:
            dirs.append(os.path.dirname(git))
    else:
        dirs += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return os.pathsep.join(dict.fromkeys(dirs))


def _login_name():
    name = os.environ.get("USER") or os.environ.get("LOGNAME") or os.environ.get("USERNAME")
    if name or WINDOWS:
        return name
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return None


def setup_token():
    """The setup-token: CLAUDE_CODE_OAUTH_TOKEN when set, else the token file when its owner alone
    can read it; None when neither. A token file that is not right is an auth error, said.
    `claude setup-token` prints the token across lines, and copied as is it holds line breaks
    (refused with a 401): a token has no whitespace, so all of it is dropped."""
    token = "".join(os.environ.get(TOKEN_VAR, "").split())
    if token:
        return token
    path, shown = Path.home().joinpath(*TOKEN_FILE), "~/" + "/".join(TOKEN_FILE)
    try:
        st = os.lstat(str(path))
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise HeadlessError("auth", f"{shown} is not a plain file", "logged_out")
    if not WINDOWS and (st.st_mode & 0o077 or st.st_uid != os.getuid()):
        raise HeadlessError("auth", f"{shown} can be read by others: chmod 600 {shown}", "logged_out")
    try:
        with open(str(path), encoding="utf-8") as f:
            token = "".join(f.read(8192).split())
    except (OSError, ValueError):
        token = ""
    if not re.fullmatch(r"[\x21-\x7e]{20,4096}", token):
        raise HeadlessError("auth", f"{shown} does not hold one token (the line `claude setup-token` prints)",
                            "logged_out")
    return token


def child_env(tmp, path):
    """The child's whole environment: an allow-list, so no API key, other token or VOYAGE_API_KEY
    travels; only a subscription's setup-token (TOKEN_VAR, or the token file), which is how claude
    logs in where no browser ever ran. USER is needed: without it Claude Code misses its login in
    the macOS keychain."""
    env = {"HOME": str(Path.home()), "PATH": path, "LANG": os.environ.get("LANG") or "en_US.UTF-8",
           "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}
    user = _login_name()
    if user:
        env["USER"] = env["LOGNAME"] = user
    if WINDOWS:
        env.update({k: os.environ[k] for k in WINDOWS_VARS if os.environ.get(k)})
        env["TEMP"] = env["TMP"] = tmp
    else:
        env["TMPDIR"] = tmp
    env.update({k: os.environ[k] for k in PROXY_VARS if os.environ.get(k)})
    token = setup_token()
    if token:
        env[TOKEN_VAR] = token
    return env


def _feed(pipe, data):
    try:
        pipe.write(data)
    except OSError:
        pass  # a child that exits without reading its input
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _pump(pipe, tag, q):
    """Whole lines, as bytes. A line past LINE_CAP is dropped rather than held in memory."""
    skipping = False
    try:
        while True:
            chunk = pipe.readline(LINE_CAP + 1)
            if not chunk:
                break
            whole = chunk.endswith(b"\n")
            if skipping or (not whole and len(chunk) > LINE_CAP):
                skipping = not whole
                continue
            q.put((tag, chunk))
    except (OSError, ValueError):
        pass
    finally:
        q.put((tag, None))


def _kill_tree(proc):
    if WINDOWS:
        taskkill = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")
        try:
            subprocess.run([taskkill, "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def supervise(argv, env, cwd, stdin_text, timeout, on_line):
    """Runs argv in a process group of its own, with only `env`. stdin is written on a thread, each
    stdout line goes to on_line(str), which returns False to stop the run. Past `timeout` the whole
    group is killed. Returns (exit code or None when killed, timed_out, last stderr lines)."""
    kw = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd)
    if WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kw)
    except OSError as e:
        raise HeadlessError("start", f"cannot run {argv[0]}: {e}")
    threading.Thread(target=_feed, args=(proc.stdin, stdin_text.encode("utf-8")), daemon=True).start()
    q = queue.Queue()
    for pipe, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
        threading.Thread(target=_pump, args=(pipe, tag, q), daemon=True).start()
    deadline = time.monotonic() + timeout
    err_tail, open_pipes, total, exited_at = deque(maxlen=20), 2, 0, None
    timed_out = stopped = overflow = False
    try:
        while open_pipes:
            now = time.monotonic()
            if now >= deadline:
                timed_out = True
                break
            if exited_at is None and proc.poll() is not None:
                exited_at = now
            if exited_at is not None and now - exited_at > DRAIN:
                break
            try:
                tag, chunk = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                open_pipes -= 1
            elif tag == "err":
                err_tail.append(chunk[:1000].decode("utf-8", "replace").rstrip())
            else:
                total += len(chunk)
                overflow = total > STREAM_CAP
                if overflow or on_line(chunk.decode("utf-8", "replace").rstrip("\r\n")) is False:
                    stopped = True
                    break
        if not (timed_out or stopped):
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    except BaseException:
        # Stopped from outside (the app's « Arrêter », Ctrl-C): Claude and what it started go too,
        # never left to finish a request.
        _kill_tree(proc)
        raise
    if timed_out or stopped or exited_at is not None and open_pipes:
        _kill_tree(proc)
    try:
        code = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        code = None
    if not WINDOWS:
        # Claude gone, its group may not be: a child that let go of the pipes would outlive it.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()
        except (OSError, ValueError):
            pass
    if overflow:
        raise HeadlessError("invalid", f"more than {STREAM_CAP} bytes of output")
    return (None if timed_out or stopped else code), timed_out, list(err_tail)


def _capture(argv, env, cwd):
    """(exit code, stdout, stderr tail) of a short probe such as `claude --version`."""
    lines = []
    code, timed_out, err = supervise(argv, env, cwd, "", PROBE_TIMEOUT, lambda l: lines.append(l))
    if timed_out:
        raise HeadlessError("start", f"{' '.join(argv[-2:])} did not answer in {PROBE_TIMEOUT}s")
    return code, "\n".join(lines), err


def parse_version(text):
    """(major, minor, patch) of the first version in text, or None."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def missing_flags(help_text, flags=REQUIRED_FLAGS):
    """The flags (REQUIRED_FLAGS) that `claude --help` does not list."""
    words = set(re.split(r"[^A-Za-z0-9-]+", help_text or ""))
    return [f for f in flags if f not in words]


def auth_method(status):
    """authMethod of `claude auth status` (JSON, sometimes after a line of notes). Nothing else is read."""
    i = (status or "").find("{")
    if i < 0:
        return None
    try:
        v, _ = json.JSONDecoder().raw_decode(status[i:])
    except ValueError:
        return None
    return v.get("authMethod") if isinstance(v, dict) else None


def _managed_dir():
    if MANAGED_DIR is not None:
        return Path(MANAGED_DIR)
    if WINDOWS:
        return Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "ClaudeCode"
    return Path("/Library/Application Support/ClaudeCode" if sys.platform == "darwin" else "/etc/claude-code")


def settings_files():
    """The settings files whose "env" claude applies to itself here: the user's (the child's HOME,
    no CLAUDE_CONFIG_DIR) and an administrator's. The child's folder is fresh: no project ones."""
    user, managed = Path.home() / ".claude", _managed_dir()
    return ([user / "settings.json", user / "settings.local.json", managed / "managed-settings.json"]
            + sorted(managed.glob("managed-settings.d/*.json")))


def _reroutes(name):
    n = name.upper()
    return (n in REROUTE_VARS or n.startswith("CLAUDE_CODE_USE_")
            or (n.startswith("ANTHROPIC_") and n.endswith("BASE_URL")))


def rerouted():
    """Names (never values) of what claude's settings set that sends the question elsewhere or bills it."""
    found = set()
    for path in settings_files():
        try:
            with open(path, encoding="utf-8-sig") as f:
                v = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        env = v.get("env") if isinstance(v.get("env"), dict) else {}
        found.update(_label(k) for k, val in env.items() if isinstance(k, str) and val and _reroutes(k))
        if v.get("apiKeyHelper"):
            found.add("apiKeyHelper")
    return sorted(found)


def check_cli(argv0, env, cwd, flags=REQUIRED_FLAGS):
    """Version, flags (REQUIRED_FLAGS, IMAGE_FLAGS) and login of this claude, checked before any
    question is asked. Returns the version; raises HeadlessError (unsupported, auth, start)."""
    _, out, err = _capture(argv0 + ["--version"], env, cwd)
    version = parse_version(out)
    if version is None:
        raise HeadlessError("start", f"claude --version said {_brief(out or ' '.join(err), 120)!r}")
    if version < MIN_CLAUDE:
        raise HeadlessError("unsupported", f"Claude Code {'.'.join(map(str, version))} is older than "
                                           f"{'.'.join(map(str, MIN_CLAUDE))}: run `claude update`", "version")
    _, out, _ = _capture(argv0 + ["--help"], env, cwd)
    missing = missing_flags(out, flags)
    if missing:
        raise HeadlessError("unsupported", "this claude lacks " + ", ".join(missing), "flags")
    elsewhere = rerouted()
    if elsewhere:
        raise HeadlessError("auth", f"Claude Code's settings set {', '.join(elsewhere[:5])}: its answers would "
                                    "go elsewhere or be billed, and only a claude.ai subscription is used.", "billed")
    _, out, _ = _capture(argv0 + ["auth", "status"], env, cwd)
    method = auth_method(out)
    if method == "oauth_token" and not env.get(TOKEN_VAR):
        raise HeadlessError("auth", "Claude Code is logged in with a token that is not the setup-token (a gateway's "
                                    "ANTHROPIC_AUTH_TOKEN?), and only a claude.ai subscription is used.", "billed")
    if method not in ("claude.ai", "oauth_token"):
        raise HeadlessError("auth", f"Claude Code login is {_label(method) if method else 'missing'}, and only "
                                    "a claude.ai subscription is used (an API key would be billed). "
                                    "Run `claude auth login` (no browser: `claude setup-token`, then "
                                    "CLAUDE_CODE_OAUTH_TOKEN).",
                            "billed" if method and method != "none" else "logged_out")
    return version


def check_init(v, model=None):
    """The start-up line must show Claude exactly as the options ask: no tool but the answer, no MCP
    server, no skill or command, no prompt for permissions, no API key, built-in plugins only, a
    known version and, when `model` is given, that model. Returns the version or raises HeadlessError."""
    def bad(what):
        return HeadlessError("init", what)
    if v.get("tools") != ["StructuredOutput"]:
        raise bad(f"tools {_brief(v.get('tools'))}")
    if v.get("mcp_servers") != []:
        raise bad("MCP servers")
    for field in ("skills", "slash_commands"):  # what --safe-mode and --disable-slash-commands turn off
        if v.get(field) != []:
            raise bad(f"{field} {_brief(v.get(field))}")
    for field, want in (("output_style", "default"), ("permissionMode", "dontAsk"), ("apiKeySource", "none")):
        if v.get(field) != want:
            raise bad(f"{field} {_brief(v.get(field))!r}")
    plugins = v.get("plugins")
    if not isinstance(plugins, list) or not all(
            isinstance(p, dict) and p.get("path") == "builtin" and str(p.get("source", "")).endswith("@builtin")
            for p in plugins):
        raise bad("plugins")
    version = str(v.get("claude_code_version") or "")
    parsed = parse_version(version)
    if parsed is None or parsed < MIN_CLAUDE:
        raise bad(f"version {_brief(version, 40)!r}")
    if model and model not in str(v.get("model") or ""):
        raise bad(f"model {_brief(v.get('model'), 80)!r}")
    return version


def foreign_tool(v):
    """Name of the first tool, other than the answer itself, an assistant message uses (any kind:
    tool_use, server_tool_use, mcp_tool_use..., or a server tool's result), or None. The model
    picks that name: it is reduced to a bare label before it reaches any message."""
    content = (v.get("message") or {}).get("content")
    for block in content if isinstance(content, list) else []:
        kind = block.get("type") if isinstance(block, dict) else None
        if isinstance(kind, str) and kind.endswith(("tool_use", "tool_result")):
            name = block.get("name")
            if not (kind == "tool_use" and name == "StructuredOutput"):
                return _label(name or kind)
    return None


def parse_result(result, api_errors):
    """The structured answer of the result line, or why there is none. The errors the service
    types on the assistant messages come first; their text is only a fallback."""
    subtype = result.get("subtype")
    said = _brief(result.get("result") or "")
    if subtype == "success" and result.get("is_error") is False:
        out = result.get("structured_output")
        if isinstance(out, dict):
            return out
        raise HeadlessError("invalid", "no structured answer")
    if subtype == "success":
        if any(e in ("rate_limit", "usage_limit_reached") for e in api_errors) or LIMIT_RE.search(said):
            raise HeadlessError("quota", said[:200] or "usage limit")
        if "authentication_failed" in api_errors or "/login" in said or "Not logged in" in said:
            raise HeadlessError("auth", said[:200] or "not logged in")
        raise HeadlessError("api", said[:200] or f"API error {result.get('api_error_status')}")
    if subtype == "error_during_execution":
        raise HeadlessError("api", _brief("; ".join(map(str, result.get("errors") or []))) or subtype)
    # error_max_turns, error_max_budget_usd, error_max_structured_output_retries... Their errors can
    # quote the answer that failed the schema, text the model chose: only the subtype is kept.
    raise HeadlessError("invalid", _label(subtype))


def rate_limit(info):
    """The status and the 5-hour and 7-day windows of a rate_limit_event, when they have the shape
    Claude Code gives them; None otherwise."""
    if not isinstance(info, dict) or not isinstance(info.get("status"), str):
        return None
    windows = info.get("unifiedWindows") if isinstance(info.get("unifiedWindows"), dict) else {}

    def window(w):
        if not isinstance(w, dict):
            return None
        used, resets = w.get("utilization"), w.get("resetsAt")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or isinstance(resets, bool) \
                or not isinstance(resets, int) or resets < 0:
            return None
        return {"utilization": float(used), "resetsAt": resets}
    return {"status": _label(info["status"]), "five_hour": window(windows.get("five_hour")),
            "seven_day": window(windows.get("seven_day"))}


class Watch:
    """Claude's stream, line by line: the start-up check first, then errors and the result."""

    def __init__(self, model=None):
        self.model, self.init, self.version, self.result, self.api_errors = model, None, None, None, []
        self.refused, self.rate_limit = None, None

    def line(self, line):
        """False once the run must stop: Claude did not start, or did not behave, as required."""
        try:
            v = json.loads(line)
        except ValueError:
            return self.ok()
        if not isinstance(v, dict):
            return self.ok()
        kind = v.get("type")
        if kind == "system" and v.get("subtype") == "model_refusal_fallback":
            # The model refused; Claude Code would have another one answer in its place, unasked.
            self.refused = HeadlessError("refused", _label(v.get("api_refusal_category") or "?"))
            return False
        if self.init is None:
            if kind != "system":
                self.init = HeadlessError("init", f"{kind!r} before the start-up line")
            elif v.get("subtype") == "init":
                try:
                    self.version = check_init(v, self.model)
                    self.init = True
                except HeadlessError as e:
                    self.init = e
            # Other system events may come first (ui_invalidate when logged out).
            return self.ok()
        if kind == "system" and v.get("subtype") == "init":
            # A second start-up line must be as clean as the first.
            try:
                check_init(v, self.model)
            except HeadlessError as e:
                self.init = e
        elif kind == "assistant":
            for key in ("error", "api_error"):
                if isinstance(v.get(key), str):
                    self.api_errors.append(v[key])
            name = foreign_tool(v)
            if name:
                self.init = HeadlessError("init", f"tool used: {name}")
        elif kind == "result":
            name = self.foreign_model(v)
            if name:
                self.init = HeadlessError("init", f"model used: {name}")
            self.result = v
        elif kind == "rate_limit_event":
            self.rate_limit = rate_limit(v.get("rate_limit_info"))
        return self.ok()

    def foreign_model(self, result):
        """A model the usage counts that is not the one asked for (another one answered, or part
        of it), as a label; None when the question asked for none."""
        usage = result.get("modelUsage")
        if not self.model or not isinstance(usage, dict):
            return None
        return next((_label(k) for k in usage if self.model not in str(k)), None)

    def ok(self):
        return self.refused is None and not isinstance(self.init, HeadlessError)

    def conclude(self, timed_out, stderr_tail=()):
        """The answer, or HeadlessError. Fail closed: no valid start-up line, no answer."""
        if self.refused is not None:
            raise self.refused
        if isinstance(self.init, HeadlessError):
            raise self.init
        if timed_out:
            raise HeadlessError("timeout", "no answer in time")
        if self.init is None:
            raise HeadlessError("start", "no start-up line" + (f" ({_brief(stderr_tail[-1])})" if stderr_tail else ""))
        if self.result is None:  # started, then died: the connection, most of the time
            raise HeadlessError("api", "no result" + (f" ({_brief(stderr_tail[-1])})" if stderr_tail else ""))
        out = parse_result(self.result, self.api_errors)
        return Answer(out, self.version, self.result.get("total_cost_usd"), self.result.get("num_turns"),
                      self.rate_limit)


def _claude(command):
    """argv of the claude to run: `command` (tests, CLAUDE_BIN) or the user's."""
    argv0 = list(command) if command else find_claude()
    if not argv0:
        raise HeadlessError("start", "Claude Code not found (~/.local/bin/claude or PATH)")
    if WINDOWS and not argv0[0].lower().endswith(".exe"):
        # A .cmd or .bat goes through cmd.exe, which reads our arguments as its own syntax.
        raise HeadlessError("start", f"{os.path.basename(argv0[0])} is not an .exe")
    return argv0


@contextmanager
def _cage(argv0, work_root, git):
    """Claude's folder: fresh, private, outside any vault (under work_root when given), gone
    afterwards. Yields (folder, environment)."""
    work = tempfile.mkdtemp(prefix="brain-claude-", dir=work_root)
    try:
        os.chmod(work, 0o700)
        tmp = os.path.join(work, "tmp")
        os.mkdir(tmp, 0o700)
        yield work, child_env(tmp, child_path(argv0[0], git))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check(command=None, *, work_root=None, git=None):
    """Whether this claude may be asked, without asking it anything: its version, or HeadlessError
    (start, unsupported, auth). The probes run as the question would."""
    argv0 = _claude(command)
    with _cage(argv0, work_root, git) as (work, env):
        return check_cli(argv0, env, work)


def run(prompt, schema, system_prompt, *, budget_usd, timeout, max_turns=3, model=None, effort=None, command=None,
        work_root=None, git=None, images=None):
    """One isolated question to Claude Code. Returns an Answer, or raises HeadlessError.
    command: argv that stands for claude (tests); default, the user's claude.
    images: PNG images (bytes) Claude sees before the prompt, in memory only (stdin_text); then
    this claude must read stream-json. None or empty: the question as it always was."""
    argv0 = _claude(command)
    with _cage(argv0, work_root, git) as (work, env):
        check_cli(argv0, env, work, IMAGE_FLAGS if images else REQUIRED_FLAGS)
        watch = Watch(model)
        args = argv0 + base_argv(schema, system_prompt, budget_usd=budget_usd, max_turns=max_turns,
                                 model=model, effort=effort, with_images=bool(images))
        _, timed_out, err = supervise(args, env, work, stdin_text(prompt, images), timeout, watch.line)
        return watch.conclude(timed_out, err)
