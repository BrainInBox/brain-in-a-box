"""Asks the user's Claude Code one question, with nothing but the question.

`claude -p` runs with no tool but its JSON answer, no MCP server, no hook, no
skill, no CLAUDE.md, none of the user's settings files, in a fresh private
folder, with a short allow-list for environment: whatever the text it reads
says, all it can do is answer. Its stream is checked line by line against that
(fail closed: no valid start-up line, no answer), and a claude logged in any
other way than a claude.ai subscription is never started, since an API key
would be billed. Same contract as the Mac app's repair (app/src-tauri/src/repair.rs).

    answer = run(prompt, schema, system_prompt, budget_usd=1.0, timeout=600)
    answer.output  # the JSON object Claude returned, already parsed

Used by daily-reflection.py. Not a hook: installed next to them, never
registered in settings.json.
"""
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque, namedtuple
from pathlib import Path

WINDOWS = os.name == "nt"
# The first version checked with every flag below (the app's MIN_CLAUDE).
MIN_CLAUDE = (2, 1, 296)
REQUIRED_FLAGS = ("--restricted", "--safe-mode", "--tools", "--permission-prompts", "--json-schema",
                  "--max-budget-usd", "--no-session-persistence", "--strict-mcp-config")
PROBE_TIMEOUT = 60
# One stream line, and the whole stream: a result line carries the full answer.
LINE_CAP = 4_000_000
STREAM_CAP = 32_000_000
# Once claude has exited, what it wrote is read within this time; a child it
# left behind holding the pipes is not waited for.
DRAIN = 2.0
LIMIT_RE = re.compile(r"hit your .*limit|session limit|usage limit", re.I)
PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
              "https_proxy", "http_proxy", "all_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS")
WINDOWS_VARS = ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "SystemRoot",
                "SystemDrive", "windir", "PATHEXT", "COMSPEC", "USERNAME")

Answer = namedtuple("Answer", "output version cost_usd num_turns")


class HeadlessError(Exception):
    """kind: start, unsupported, auth, init, timeout, quota, api, invalid."""

    def __init__(self, kind, detail):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail

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


def base_argv(schema, system_prompt, *, budget_usd, max_turns=3, model=None, effort=None):
    """claude's options, without the program itself. The prompt goes through stdin, after every
    option: --disallowedTools takes several values and would swallow a prompt placed last."""
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
    return a + ["--json-schema", json.dumps(schema, separators=(",", ":")), "--append-system-prompt", system_prompt,
                "--output-format", "stream-json", "--verbose"]


def child_path(program):
    """PATH of the child: the folder of claude (and of its link target) and the system's, nothing the user added."""
    dirs = [os.path.dirname(os.path.abspath(program)), os.path.dirname(os.path.realpath(program))]
    if WINDOWS:
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        dirs += [os.path.join(root, "System32"), root, os.path.join(root, "System32", "Wbem")]
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


def child_env(tmp, path):
    """The child's whole environment: an allow-list, so no API key, token or VOYAGE_API_KEY travels.
    USER is needed: without it Claude Code misses its login in the macOS keychain."""
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
            subprocess.run([taskkill, "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
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
    timed_out = stopped = False
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
            err_tail.append(chunk.decode("utf-8", "replace").rstrip())
        else:
            total += len(chunk)
            if total > STREAM_CAP or on_line(chunk.decode("utf-8", "replace").rstrip("\r\n")) is False:
                stopped = True
                break
    if not (timed_out or stopped):
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
    if timed_out or stopped or exited_at is not None and open_pipes:
        _kill_tree(proc)
    try:
        code = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        code = None
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()
        except (OSError, ValueError):
            pass
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


def missing_flags(help_text):
    """The REQUIRED_FLAGS that `claude --help` does not list."""
    words = set(re.split(r"[^A-Za-z0-9-]+", help_text or ""))
    return [f for f in REQUIRED_FLAGS if f not in words]


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


def check_cli(argv0, env, cwd):
    """Version, flags and login of this claude, checked before any question is asked.
    Returns the version; raises HeadlessError (unsupported, auth, start)."""
    _, out, err = _capture(argv0 + ["--version"], env, cwd)
    version = parse_version(out)
    if version is None:
        raise HeadlessError("start", f"claude --version said {(out or ' '.join(err))[:120]!r}")
    if version < MIN_CLAUDE:
        raise HeadlessError("unsupported", f"Claude Code {'.'.join(map(str, version))} is older than "
                                           f"{'.'.join(map(str, MIN_CLAUDE))}: run `claude update`")
    _, out, _ = _capture(argv0 + ["--help"], env, cwd)
    missing = missing_flags(out)
    if missing:
        raise HeadlessError("unsupported", "this claude lacks " + ", ".join(missing))
    _, out, _ = _capture(argv0 + ["auth", "status"], env, cwd)
    method = auth_method(out)
    if method != "claude.ai":
        raise HeadlessError("auth", f"Claude Code login is {method or 'missing'}, and only a claude.ai subscription "
                                    "is used (an API key would be billed). Run `claude auth login`.")
    return version


def check_init(v, model=None):
    """The start-up line must show Claude exactly as the options ask: no tool but the answer, no MCP
    server, no skill or command, no prompt for permissions, no API key, built-in plugins only, a
    known version and, when `model` is given, that model. Returns the version or raises HeadlessError."""
    def bad(what):
        return HeadlessError("init", what)
    if v.get("tools") != ["StructuredOutput"]:
        raise bad(f"tools {v.get('tools')}")
    if v.get("mcp_servers") != []:
        raise bad("MCP servers")
    for field in ("skills", "slash_commands"):  # what --safe-mode and --disable-slash-commands turn off
        if v.get(field) != []:
            raise bad(f"{field} {v.get(field)}")
    for field, want in (("output_style", "default"), ("permissionMode", "dontAsk"), ("apiKeySource", "none")):
        if v.get(field) != want:
            raise bad(f"{field} {v.get(field)!r}")
    plugins = v.get("plugins")
    if not isinstance(plugins, list) or not all(
            isinstance(p, dict) and p.get("path") == "builtin" and str(p.get("source", "")).endswith("@builtin")
            for p in plugins):
        raise bad("plugins")
    version = str(v.get("claude_code_version") or "")
    parsed = parse_version(version)
    if parsed is None or parsed < MIN_CLAUDE:
        raise bad(f"version {version!r}")
    if model and model not in str(v.get("model") or ""):
        raise bad(f"model {v.get('model')!r}")
    return version


def foreign_tool(v):
    """Name of the first tool, other than the answer itself, an assistant message uses (any kind:
    tool_use, server_tool_use, mcp_tool_use...), or None."""
    content = (v.get("message") or {}).get("content")
    for block in content if isinstance(content, list) else []:
        kind = block.get("type") if isinstance(block, dict) else None
        if isinstance(kind, str) and kind.endswith("tool_use"):
            name = block.get("name") or "?"
            if not (kind == "tool_use" and name == "StructuredOutput"):
                return str(name)
    return None


def parse_result(result, api_errors):
    """The structured answer of the result line, or why there is none. The errors the service
    types on the assistant messages come first; their text is only a fallback."""
    subtype = result.get("subtype")
    said = str(result.get("result") or "")
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
        raise HeadlessError("api", "; ".join(map(str, result.get("errors") or [])) or subtype)
    # error_max_turns, error_max_budget_usd, error_max_structured_output_retries...
    raise HeadlessError("invalid", f"{subtype}: {'; '.join(map(str, result.get('errors') or []))}"[:300])


class Watch:
    """Claude's stream, line by line: the start-up check first, then errors and the result."""

    def __init__(self, model=None):
        self.model, self.init, self.version, self.result, self.api_errors = model, None, None, None, []

    def line(self, line):
        """False once the run must stop: Claude did not start, or did not behave, as required."""
        try:
            v = json.loads(line)
        except ValueError:
            return self.ok()
        if not isinstance(v, dict):
            return self.ok()
        kind = v.get("type")
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
        if kind == "assistant":
            for key in ("error", "api_error"):
                if isinstance(v.get(key), str):
                    self.api_errors.append(v[key])
            name = foreign_tool(v)
            if name:
                self.init = HeadlessError("init", f"tool used: {name}")
        elif kind == "result":
            self.result = v
        return self.ok()

    def ok(self):
        return not isinstance(self.init, HeadlessError)

    def conclude(self, timed_out, stderr_tail=()):
        """The answer, or HeadlessError. Fail closed: no valid start-up line, no answer."""
        if isinstance(self.init, HeadlessError):
            raise self.init
        if timed_out:
            raise HeadlessError("timeout", "no answer in time")
        if self.init is None:
            raise HeadlessError("start", "no start-up line" + (f" ({stderr_tail[-1][:200]})" if stderr_tail else ""))
        if self.result is None:  # started, then died: the connection, most of the time
            raise HeadlessError("api", "no result" + (f" ({stderr_tail[-1][:200]})" if stderr_tail else ""))
        out = parse_result(self.result, self.api_errors)
        return Answer(out, self.version, self.result.get("total_cost_usd"), self.result.get("num_turns"))


def run(prompt, schema, system_prompt, *, budget_usd, timeout, max_turns=3, model=None, effort=None, command=None):
    """One isolated question to Claude Code. Returns an Answer, or raises HeadlessError.
    command: argv that stands for claude (tests); default, the user's claude."""
    argv0 = list(command) if command else find_claude()
    if not argv0:
        raise HeadlessError("start", "Claude Code not found (~/.local/bin/claude or PATH)")
    # Claude's folder: fresh, private, outside any vault, gone afterwards.
    work = tempfile.mkdtemp(prefix="brain-claude-")
    try:
        os.chmod(work, 0o700)
        tmp = os.path.join(work, "tmp")
        os.mkdir(tmp, 0o700)
        env = child_env(tmp, child_path(argv0[0]))
        check_cli(argv0, env, work)
        watch = Watch(model)
        args = argv0 + base_argv(schema, system_prompt, budget_usd=budget_usd, max_turns=max_turns,
                                 model=model, effort=effort)
        _, timed_out, err = supervise(args, env, work, prompt, timeout, watch.line)
        return watch.conclude(timed_out, err)
    finally:
        shutil.rmtree(work, ignore_errors=True)

