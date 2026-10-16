"""The launcher without tools (engine/hooks/_headless_codex.py) with the real Codex CLI, in ChatGPT
mode, against mock_openai.py: fake tokens, no account, no secret, nothing leaves 127.0.0.1
(docs/proposals/codex.md, §5.2); then the reflection's and the review's own questions, through the
choice of engine (_headless.py), with Codex chosen; then a session the real Codex wrote, read as the
reflection and Codex's Stop hook read it (_codex_rollout.py); then the kit's hooks, run by the real
Codex from a hooks.json (--agent codex); then, on macOS and Linux, install.sh with Codex chosen, in a
home of its own: Codex from its pinned installer, and the real Codex running the hooks install.sh wired
and approved. The job codex-mock of test.yml runs it on macOS, Linux and Windows once the official
installer has put the pinned Codex in place. It writes Codex's system
settings (/etc/codex, %ProgramData%\\OpenAI\\Codex): on a CI runner only. Every scenario is
required: a codex missing, or one that cannot run, is a failure.

    python3 -I engine/tests/codex_mock.py
"""
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ENGINE, "hooks"))
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

import _agents  # noqa: E402
import _codex_rollout as rollout  # noqa: E402
import _controle_judge as judge  # noqa: E402
import _headless_codex as codex  # noqa: E402
import mock_openai  # noqa: E402
from _headless_claude import HeadlessError  # noqa: E402

WINDOWS = os.name == "nt"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "n"],
          "properties": {"verdict": {"type": "string", "enum": ["ok", "ko"]}, "n": {"type": "integer"}}}
SYSTEM = "You are a strict JSON judge. MOCK-SYSTEM-MARKER"
PROMPT = "Judge this day of notes: é à ü 漢字 🙂. MOCK-PROMPT-SENTINEL"
# What Codex 0.162.1 announces under the lock (step 0, the same on the three systems).
TOOLS = {"functions.exec", "functions.exec>apply_patch", "functions.exec>clock__curr_time",
         "functions.exec>list_mcp_resource_templates", "functions.exec>list_mcp_resources",
         "functions.exec>read_mcp_resource", "functions.request_user_input_async", "functions.wait"}
ENDPOINTS = {"GET /v1/models", "GET /v1/responses", "POST /v1/responses", "GET /backend-api/wham/accounts/check",
             "GET /backend-api/wham/settings/user"}
CANARIES = {"OPENAI_API_KEY": "sk-canary-parent-openai-0123456789", "CODEX_API_KEY": "sk-canary-parent-codex-0123456789",
            "CODEX_ACCESS_TOKEN": "canary-parent-access-token", "OPENAI_BASE_URL": "http://127.0.0.1:9/canary"}
FAILURES = []


def check(cond, what):
    print(("ok    " if cond else "FAIL  ") + what, flush=True)
    if not cond:
        FAILURES.append(what)


def scenario(fn):
    print("\n## " + fn.__name__, flush=True)
    try:
        fn()
    except Exception:
        traceback.print_exc()
        FAILURES.append(fn.__name__ + ": " + traceback.format_exc().strip().splitlines()[-1])


class Bench:
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="codex-mock-"))
        self.home = os.path.join(self.root, "codex-home")
        self.markers = os.path.join(self.root, "markers")
        os.makedirs(self.home)
        os.makedirs(self.markers)
        with open(os.path.join(self.home, "auth.json"), "w", encoding="utf-8") as f:
            json.dump(mock_openai.fake_auth(), f)
        self.marker_py = os.path.join(self.root, "marker.py")
        with open(self.marker_py, "w", encoding="utf-8") as f:
            f.write("import pathlib, sys\npathlib.Path(%r, sys.argv[1]).write_text('x')\nsys.exit(1)\n" % self.markers)
        self.codex = codex.find_codex()
        self.mock = mock_openai.Mock(answer='{"verdict":"ok","n":7}')
        # Where a system config.toml's own URLs and exporter point: nothing of the launcher's may reach it.
        self.trap = mock_openai.Mock(answer='{"verdict":"ko","n":0}')
        self.command = [sys.executable, "-I", os.path.join(HERE, "mock_openai.py"), "shim", self.mock.root,
                        self.codex[0]]

    def marker_argv(self, name):
        return [sys.executable, self.marker_py, name]

    def found_markers(self):
        time.sleep(2)   # notify runs after the turn
        return sorted(os.listdir(self.markers))

    def run(self, **kw):
        return codex.run(PROMPT, SCHEMA, SYSTEM, codex_home=self.home, timeout=180, command=self.command,
                         work_root=self.root, **kw)

    def raw_exec(self, cwd, prompt=b"Reply.", home=None, ephemeral=True, args=(), env=None, binary=None):
        """codex exec as anyone would run it, without the launcher: the control of a trap, or a
        session of the user's (ephemeral=False: Codex keeps it in home/sessions)."""
        argv = [binary or self.codex[0], "exec", "--json", "--skip-git-repo-check"] + (["--ephemeral"] if ephemeral else []) + [
                "-c", 'cli_auth_credentials_store="file"', "-c", "features.enable_request_compression=false",
                "-c", "openai_base_url=" + json.dumps(self.mock.root + "/v1"),
                "-c", "chatgpt_base_url=" + json.dumps(self.mock.root + "/backend-api/")] + list(args) + ["-"]
        e = {k: v for k, v in os.environ.items() if k not in CANARIES and k != "RUST_LOG"}
        e.update(env or {}, CODEX_HOME=home or self.home)
        return subprocess.run(argv, input=prompt, cwd=cwd, env=e, capture_output=True, timeout=180)


B = None


def answers_with_everything_locked():
    B.mock.reset()
    os.environ.update(CANARIES)
    try:
        a = B.run()
    finally:
        for k in CANARIES:
            os.environ.pop(k, None)
    check(a.output == {"verdict": "ok", "n": 7}, "the mock's answer comes back as the parsed object")
    check(a.version == os.environ.get("CODEX_RELEASE", a.version), "version %s" % a.version)
    reqs = B.mock.responses()
    check(len(reqs) == 1, "one request to the model (%d)" % len(reqs))
    body = reqs[0]["body"] if reqs else {}
    check(B.mock.auth_kinds() == ["fake-chatgpt"], "every request carries the ChatGPT login, no key: %s"
          % B.mock.auth_kinds())
    check(set(B.mock.endpoints()) <= ENDPOINTS, "only the fixed URLs: %s" % B.mock.endpoints())
    extra = set(B.mock.tools()) - TOOLS
    check(not extra, "no tool announced beyond step 0's: %s" % sorted(extra))
    fmt = (body.get("text") or {}).get("format") or {}
    check(fmt.get("type") == "json_schema" and fmt.get("strict") is True and fmt.get("schema") == SCHEMA,
          "the answer schema, strict")
    dump = json.dumps(B.mock.reqs, ensure_ascii=False)
    check("MOCK-SYSTEM-MARKER" in dump, "the system prompt reaches the model")
    check(PROMPT in dump, "the prompt reaches the model whole (stdin, UTF-8)")
    for k, v in CANARIES.items():
        check(v not in dump, "%s of the caller's environment reaches nothing" % k)
    left = []
    for d, _, files in os.walk(B.home):
        for name in files:
            with open(os.path.join(d, name), "rb") as f:
                if b"MOCK-PROMPT-SENTINEL" in f.read():
                    left.append(os.path.relpath(os.path.join(d, name), B.home))
    check(not left, "nothing of the prompt left in CODEX_HOME (--ephemeral): %s" % left)
    check(not os.path.isdir(os.path.join(B.home, "sessions")), "no session file")


def forced_tool_calls_are_refused():
    target = os.path.join(B.root, "pwned.txt")
    cmd = ('Set-Content -Path "%s" -Value x' if WINDOWS else "echo x > '%s'") % target
    cases = [("exec (JavaScript)", mock_openai.js_exec_call(cmd), ("code-mode host is disabled",)),
             ("exec_command", mock_openai.fn_call("exec_command", {"cmd": cmd}, "call_fc_1"),
              ("unsupported call: exec_command",)),
             ("shell", mock_openai.fn_call("shell", {"command": ["bash", "-lc", cmd]}, "call_sh_1"),
              ("unsupported call: shell",)),
             ("apply_patch", mock_openai.patch_call(target), ("patch rejected: ",)),
             ("view_image", mock_openai.fn_call("view_image", {"path": B.marker_py}, "call_vi_1"),
              ("unsupported call: view_image",))]
    for name, call, refusals in cases:
        B.mock.reset([call])
        try:
            a = B.run()
            got = a.output
        except HeadlessError as e:
            got = "HeadlessError %s" % e
        outs = B.mock.tool_outputs()
        check(got == {"verdict": "ok", "n": 7}, "%s: the run still answers (the stream hides tools): %s" % (name, got))
        check(len(outs) == 1 and outs[0].startswith(refusals), "%s refused: %s" % (name, [o[:120] for o in outs]))
        check(not os.path.exists(target), "%s wrote nothing" % name)


def an_unknown_feature_pauses_codex():
    with open(os.path.join(ENGINE, "codex-features.json"), encoding="utf-8") as f:
        d = json.load(f)
    fewer = os.path.join(B.root, "fewer-features.json")
    d["coupees"] = [n for n in d["coupees"] if n != "fast_mode"]
    with open(fewer, "w", encoding="utf-8") as f:
        json.dump(d, f)
    try:
        codex.check(B.home, B.command, work_root=B.root, features_file=fewer)
        check(False, "a feature left on (fast_mode) pauses Codex")
    except HeadlessError as e:
        check(e.kind == "unsupported" and e.reason == "features" and "fast_mode" in str(e),
              "a feature left on pauses Codex: %s" % e)
    check(codex.check(B.home, B.command, work_root=B.root) == os.environ.get("CODEX_RELEASE", "0.162.1"),
          "the kit's own list passes this Codex's features list")


def the_user_s_mcp_server_never_starts():
    cfg = os.path.join(B.home, "config.toml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("[mcp_servers.usermcp]\ncommand = %s\nargs = %s\n" % (json.dumps(sys.executable),
                                                                     json.dumps([B.marker_py, "usermcp"])))
    try:
        B.mock.reset()
        a = B.run()
        check(a.output == {"verdict": "ok", "n": 7}, "an MCP server in the user's config.toml does not break the call")
        check("usermcp" not in B.found_markers(), "and it does not start")
        work = tempfile.mkdtemp(dir=B.root)
        B.mock.reset()
        B.raw_exec(work)
        check("usermcp" in B.found_markers(), "control: codex exec without the launcher starts it")
    finally:
        os.remove(cfg)
        for m in os.listdir(B.markers):
            os.remove(os.path.join(B.markers, m))


def system_dir():
    if WINDOWS:
        return os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "OpenAI", "Codex")
    return "/etc/codex"


def admin_write(path, text):
    if WINDOWS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return
    subprocess.run(["sudo", "-n", "mkdir", "-p", os.path.dirname(path)], check=True)
    subprocess.run(["sudo", "-n", "tee", path], input=text.encode("utf-8"), stdout=subprocess.DEVNULL, check=True)


def admin_rm(path):
    if WINDOWS:
        if os.path.exists(path):
            os.remove(path)
    else:
        subprocess.run(["sudo", "-n", "rm", "-f", path], check=True)


def a_system_config_is_neutralised():
    path = os.path.join(system_dir(), "config.toml")
    text = ('developer_instructions = "SYSTEM-DEV-MARKER"\nnotify = %s\nopenai_base_url = %s\nchatgpt_base_url = %s\n'
            '\n[mcp_servers.sysmcp]\ncommand = %s\nargs = %s\n'
            % (json.dumps(B.marker_argv("notify")), json.dumps(B.trap.root + "/v1"),
               json.dumps(B.trap.root + "/backend-api/"), json.dumps(sys.executable), json.dumps([B.marker_py, "sysmcp"])))
    admin_write(path, text)
    try:
        B.mock.reset()
        B.trap.reset()
        a = B.run()
        check(a.output == {"verdict": "ok", "n": 7}, "a system config.toml with an MCP server and notify: answered")
        check(len(B.mock.responses()) == 1 and B.trap.reqs == [],
              "the URLs the launcher pins pass over the system's: %s" % B.trap.endpoints())
        check(B.found_markers() == [], "its MCP server and its notify program do not run: %s" % B.found_markers())
        check("SYSTEM-DEV-MARKER" not in json.dumps(B.mock.reqs), "its developer instructions are not sent")
        B.mock.reset()
        B.raw_exec(tempfile.mkdtemp(dir=B.root))
        seen = B.found_markers()
        check("sysmcp" in seen and "notify" in seen, "control: without the launcher both run: %s" % seen)
        check("SYSTEM-DEV-MARKER" in json.dumps(B.mock.reqs), "control: and its instructions are sent")
    finally:
        admin_rm(path)
        for m in os.listdir(B.markers):
            os.remove(os.path.join(B.markers, m))


def a_system_otel_exporter_gets_nothing():
    """A system config.toml's [otel] is read by `codex exec` (only a project's is refused): its log
    exporter would get the whole prompt. The launcher turns every exporter off."""
    path = os.path.join(system_dir(), "config.toml")
    admin_write(path, '[otel]\nlog_user_prompt = true\nlog_agent_responses = true\n'
                      'exporter = { otlp-http = { endpoint = %s, protocol = "json" } }\n'
                % json.dumps(B.trap.root + "/v1/logs"))
    try:
        B.mock.reset()
        B.trap.reset()
        a = B.run()
        check(a.output == {"verdict": "ok", "n": 7}, "a system config.toml with an [otel] exporter: answered")
        check(B.trap.reqs == [], "its exporter gets nothing: %s" % B.trap.endpoints())
        B.mock.reset()
        B.trap.reset()
        B.raw_exec(tempfile.mkdtemp(dir=B.root), prompt=b"Reply. OTEL-PROMPT-MARKER")
        check("OTEL-PROMPT-MARKER" in json.dumps(B.trap.reqs),
              "control: without the launcher, its exporter gets the prompt: %s" % B.trap.endpoints())
        B.mock.reset()
        B.trap.reset()
        with mock.patch.object(codex, "SETTINGS", tuple(x for x in codex.SETTINGS if not x.startswith("otel."))):
            B.run()
        check("MOCK-PROMPT-SENTINEL" in json.dumps(B.trap.reqs),
              "control: the launcher without its otel settings, the exporter gets the prompt: %s"
              % B.trap.endpoints())
    finally:
        admin_rm(path)


def requirements_pause_codex():
    path = os.path.join(system_dir(), "requirements.toml")
    admin_write(path, 'model_provider = "elsewhere"\n')
    try:
        codex.check(B.home, B.command, work_root=B.root)
        check(False, "requirements.toml pauses Codex")
    except HeadlessError as e:
        check(e.kind == "unsupported" and e.reason == "managed", "requirements.toml pauses Codex: %s" % e)
    finally:
        admin_rm(path)


def an_api_key_login_is_refused():
    p = os.path.join(B.home, "auth.json")
    with open(p, encoding="utf-8") as f:
        good = f.read()
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-canary-apikey-login-0123456789"}, f)
    try:
        B.mock.reset()
        codex.check(B.home, B.command, work_root=B.root)
        check(False, "an API key login is refused")
    except HeadlessError as e:
        check(e.kind == "auth" and e.reason == "billed" and "sk-canary" not in str(e), "an API key login is refused")
    finally:
        with open(p, "w", encoding="utf-8") as f:
            f.write(good)
    check(B.mock.reqs == [], "and nothing was sent")


def the_reflection_and_the_review_ask_codex_through_the_choice():
    """daily-reflection.py's and the review's own questions, through their own functions (dr.ask,
    judge.ask: schema, instructions, wait, tries, effort), Codex the one chosen (a reading of
    agents.json given as is: Codex stays closed on every system). The prompts are the bench's. The
    real Codex must send each schema strict, as it is."""
    spec = importlib.util.spec_from_file_location("daily_reflection", os.path.join(ENGINE, "hooks", "daily-reflection.py"))
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    dr.configure(logs=os.path.join(B.root, "logs"))
    chosen = (_agents.Choix(("codex",), "codex", B.home), "ok")
    day = {"what_i_did": ["Shipped the login page"], "key_decisions": [], "projects": ["app"], "todo": [],
           "memory": ["Login page shipped"], "suspect": False}
    review = {"suspect": False, "constats": [], "lecons": []}
    saved = B.mock.answer
    try:
        B.mock.reset()
        B.mock.answer = json.dumps(day)
        a = dr.ask(dr.SENTINEL + "\n" + PROMPT, chosen, codex_command=B.command)
        check((a.moteur, a.output) == ("codex", day), "the reflection's question: Codex answers, named")
        check(dr.clean_answer(a.output)["what_i_did"] == ["Shipped the login page"], "and its answer is the reflection's")
        sent = B.mock.responses()[0]["body"] if B.mock.responses() else {}
        fmt = (sent.get("text") or {}).get("format") or {}
        check(fmt.get("strict") is True and fmt.get("schema") == dr.SCHEMA, "the reflection's schema, strict, as is")
        check(dr.SYSTEM[:60] in json.dumps(B.mock.reqs, ensure_ascii=False), "the reflection's instructions are sent")
        sch = judge.schema(["E01", "E02"], ["L01"])
        B.mock.reset()
        B.mock.answer = json.dumps(review)
        out = judge.ask('Contrôle.\n<extrait id="E01" note="a.md">La base est sur Neon.</extrait>\n', sch,
                        codex_command=B.command, work_root=B.root, agents=chosen)
        check((out.cause, out.moteur, out.answer) == (None, "codex", review),
              "the review's question: Codex answers, named: %s %s" % (out.cause, out.detail))
        sent = B.mock.responses()[0]["body"] if B.mock.responses() else {}
        fmt = (sent.get("text") or {}).get("format") or {}
        check(fmt.get("strict") is True and fmt.get("schema") == sch, "the review's schema, strict, as is")
        effort = json.dumps(sent.get("reasoning") or {})
        check(judge.CODEX_EFFORT in effort, "the review's effort: %s" % effort)
    finally:
        B.mock.answer = saved


def load_hook(name, module):
    spec = importlib.util.spec_from_file_location(module, os.path.join(ENGINE, "hooks", name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def a_real_codex_session_is_read():
    """A session the real Codex keeps (a user's codex exec, not --ephemeral, a global AGENTS.md), read by
    _codex_rollout.py as the reflection (codex_sessions) and Codex's Stop hook (parse_rollout) read it:
    its whole id, what was typed, the answers, the tool called, and nothing Codex added for the model.
    A session holding the reflection's own prompt is left out."""
    dr = load_hook("daily-reflection.py", "daily_reflection")
    rc = load_hook("session-recap.py", "session_recap")
    home = os.path.join(B.root, "session-home")
    os.makedirs(home)
    shutil.copy(os.path.join(B.home, "auth.json"), home)
    with open(os.path.join(home, "AGENTS.md"), "w", encoding="utf-8") as f:
        f.write("SESSION-AGENTS-MARKER: global instructions.\n")
    work = os.path.realpath(tempfile.mkdtemp(dir=B.root))
    prompt = "SESSION-PROMPT-MARKER é à 漢字 🙂"
    reply = [mock_openai.message("SESSION-COMMENTARY-MARKER", "msg_s1"),
             mock_openai.fn_call("brain_no_such_tool", {"path": "SESSION-TOOL-MARKER"}, "call_s1")]
    B.mock.reset([reply])
    p = B.raw_exec(work, prompt.encode("utf-8"), home=home, ephemeral=False)
    started = [json.loads(l) for l in p.stdout.decode("utf-8", "replace").splitlines() if '"thread.started"' in l]
    thread = started[0].get("thread_id") if started else None
    check(p.returncode == 0 and thread, "a session of the user's: codex exec ran (%s, %s)"
          % (p.returncode, p.stderr.decode("utf-8", "replace")[-300:]))
    check("SESSION-AGENTS-MARKER" in json.dumps(B.mock.reqs), "its AGENTS.md reached the model")
    paths, packed = rollout.recent(home, 0)
    check(len(paths) == 1 and packed == [], "Codex kept one rollout: %s %s" % (paths, packed))
    s = rollout.read(paths[0]) if paths else None
    if s is None:
        check(False, "the rollout is read")
        return
    check(s.id == thread and len(s.id) == 36, "its whole id, the thread's: %s %s" % (s.id, thread))
    same = os.path.normcase(os.path.basename(s.cwd or "")) == os.path.normcase(os.path.basename(work))
    check(s.source == "exec" and same, "its source and folder: %s %s" % (s.source, s.cwd))
    said = [(i.kind, i.text) for i in s.items if i.kind != "tool"]
    check(said == [("user", prompt), ("assistant", "SESSION-COMMENTARY-MARKER"), ("assistant", B.mock.answer)],
          "what was typed and answered, nothing Codex added: %s" % said)
    tools = [(i.name, i.text) for i in s.items if i.kind == "tool"]
    check(tools == [("brain_no_such_tool", "SESSION-TOOL-MARKER")], "the tool called: %s" % tools)
    dump = json.dumps([i.text for i in s.items], ensure_ascii=False)
    for added in ("SESSION-AGENTS-MARKER", "AGENTS.md", "<environment_context>", "unsupported call"):
        check(added not in dump, "%s is not read" % added)
    with open(paths[0], encoding="utf-8") as f:
        msgs = [r["payload"] for r in map(json.loads, f) if r.get("type") == "response_item"
                and r["payload"].get("type") == "message" and r["payload"].get("role") == "user"]
    marks = [((m.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds"),
              [c.get("text", "")[:40] for c in m.get("content", [])]) for m in msgs]
    agents_md = [m for m in marks if any("SESSION-AGENTS-MARKER" in t or t.startswith("# AGENTS.md") for t in m[1])]
    check(len(agents_md) == 1 and agents_md[0][0] and not any(k.startswith("user.") for k in agents_md[0][0])
          and agents_md[0][1][0].lower().startswith("# agents.md instructions"),
          "the global AGENTS.md: marked as Codex's, its text as Codex's: %s" % marks)
    B.mock.reset([reply])
    B.raw_exec(work, (dr.SENTINEL + "\nSELF-RUN-MARKER").encode("utf-8"), home=home, ephemeral=False)
    found, packed = dr.codex_sessions(time.strftime("%Y-%m-%d"), home)
    labels = [label for label, _, _ in found]
    check(labels == ["codex %s · exec · cwd=%s" % (thread, s.cwd)],
          "the reflection reads it, says it was a codex exec, the kit's own left out: %s" % labels)
    text = found[0][1] if found else ""
    check(" user: " + prompt in text and "   → brain_no_such_tool SESSION-TOOL-MARKER" in text
          and "SELF-RUN" not in text and "SESSION-AGENTS-MARKER" not in text, "the reflection's text of it")
    stats = rc.parse_rollout(paths[0])
    check((stats["first_prompt"], dict(stats["tools"]), stats["files"]) ==
          (prompt, {"brain_no_such_tool": 1}, {"SESSION-TOOL-MARKER"}), "Codex's Stop hook reads it: %s" % stats)


HOOK_WRAP = r'''import json, os, subprocess, sys
hook, facts, typed = sys.argv[1:4]
payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
path, source = payload.get("transcript_path"), None
if isinstance(path, str) and os.path.isfile(path):
    with open(path, "rb") as f:
        try:
            source = json.loads(f.readline())["payload"]["source"]
        except Exception:
            source = "unreadable"
with open(facts, "a", encoding="utf-8") as f:
    f.write(json.dumps({"hook": os.path.basename(hook), "event": payload.get("hook_event_name"),
                        "file": isinstance(path, str) and os.path.isfile(path), "source": source}) + "\n")
if typed != "-":
    payload["transcript_path"] = typed
r = subprocess.run([sys.executable, hook, "--agent", "codex"], input=json.dumps(payload).encode("utf-8"),
                   capture_output=True)
sys.stdout.buffer.write(r.stdout)
sys.stderr.buffer.write(r.stderr)
sys.exit(r.returncode)
'''


def codex_runs_the_kits_hooks():
    """The kit's hooks (a copy where Codex is open), run by the real Codex from a hooks.json, in a user's
    codex exec session (trust bypassed: approving them is phase 6); on Windows in the PowerShell form of
    the plan (& "python.exe" "hook"). A test wrapper notes what each hook was given, then hands it on.
    As it is, an exec run: from its first hook, Codex has written the session file and it says "exec",
    so the hooks say and write nothing (but the sign that they ran). Handed on as if typed in Codex's
    terminal: their answers reach the model, so Codex took them. The launcher's own call runs none."""
    import codex_captures as cap
    hooks, home, chome = (os.path.join(B.root, n) for n in ("hooks-open", "hooks-home", "hooks-codex-home"))
    state = os.path.join(home, ".brain-in-a-box", "controle")
    for d in (hooks, state, chome):
        os.makedirs(d)
    for name in os.listdir(os.path.join(ENGINE, "hooks")):
        if name.endswith(".py"):
            shutil.copy(os.path.join(ENGINE, "hooks", name), hooks)
    gate = os.path.join(hooks, "_agents.py")
    with open(gate, encoding="utf-8") as f:
        text = f.read()
    check(text.count("CODEX_OUVERT = frozenset()\n") == 1, "the gate's line, opened in the copy")
    with open(gate, "w", encoding="utf-8") as f:
        f.write(text.replace("CODEX_OUVERT = frozenset()\n", 'CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})\n'))
    agents = os.path.join(home, ".brain-in-a-box", "agents.json")
    with open(agents, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "agents": ["claude", "codex"], "prefere": "claude", "codex_home": chome}, f)
    os.chmod(agents, 0o600)
    with open(os.path.join(state, "etat.json"), "w", encoding="utf-8") as f:
        json.dump({"run": "20261014-0407", "mode": "nuit", "dernier_succes": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "corrections": 2, "a_voir": 0, "secrets_a_changer": 1, "rapport": "Profile/controle.md",
                   "rapport_reecrit": True, "alertes": []}, f)
    shutil.copy(os.path.join(B.home, "auth.json"), chome)
    wrap, facts, typed = (os.path.join(B.root, n) for n in ("hookwrap.py", "hook-facts.jsonl", "typed.jsonl"))
    with open(wrap, "w", encoding="utf-8") as f:
        f.write(HOOK_WRAP)
    shutil.copy(str(cap.P0 / cap.TUI), typed)   # a session of Codex's terminal (source "cli"), step 0
    names = ["controle-sessionstart.py", "correction-detector.py", "session-logger.py"]

    def handler(hook, given):
        argv = [sys.executable, wrap, os.path.join(hooks, hook + ".py"), facts, given]
        h = {"type": "command", "command": " ".join(shlex.quote(a) for a in argv), "timeout": 30}
        if WINDOWS:
            h["commandWindows"] = "& " + " ".join('"%s"' % a for a in argv)
        return {"hooks": [h]}

    def session(given):
        """A user's codex exec, a correction typed: (what each hook was given, what reached the model)."""
        with open(os.path.join(chome, "hooks.json"), "w", encoding="utf-8") as f:
            json.dump({"hooks": {"SessionStart": [handler("controle-sessionstart", given)],
                                 "UserPromptSubmit": [handler("correction-detector", given)],
                                 "Stop": [handler("session-logger", "-")]}}, f, indent=1)
        for p in (facts, os.path.join(state, "shown.json")):
            if os.path.exists(p):
                os.remove(p)
        B.mock.reset()
        p = B.raw_exec(os.path.realpath(tempfile.mkdtemp(dir=B.root)), b"no, actually do it the other way",
                       home=chome, ephemeral=False, args=["--dangerously-bypass-hook-trust"],
                       env={"HOME": home, "USERPROFILE": home})
        check(p.returncode == 0, "codex exec with the kit's hooks ran (%s, %s)"
              % (p.returncode, p.stderr.decode("utf-8", "replace")[-300:]))
        seen = []
        if os.path.exists(facts):
            with open(facts, encoding="utf-8") as f:
                seen = [json.loads(l) for l in f]
        check(sorted(r["hook"] for r in seen) == names, "Codex ran the three hooks once each: %s" % seen)
        return seen, json.dumps(B.mock.reqs, ensure_ascii=False)

    seen, dump = session("-")
    print("facts: what each hook was given by Codex 0.162.1 in a codex exec run:", seen)
    check(len(seen) == 3 and all(r["file"] is True and r["source"] == "exec" for r in seen),
          "from the first hook (SessionStart), Codex has written the session file and it says exec")
    check("CORRECTION DETECTED" not in dump and "Contrôle de cette nuit" not in dump,
          "an exec run: no reminder, no night check's line reach the model")
    check(not os.path.exists(os.path.join(home, ".claude", "logs", "sessions.log")), "an exec run: nothing logged")
    check(sorted(_agents.vus(home)) == ["controle-sessionstart", "correction-detector", "session-logger"],
          "the sign that Codex runs each hook: %s" % _agents.vus(home))
    seen, dump = session(typed)
    check("CORRECTION DETECTED" in dump, "as if typed in the terminal: correction-detector's reminder reached "
                                         "the model, Codex took its answer")
    check("Contrôle de cette nuit : 2 corrections faites" in dump,
          "as if typed in the terminal: the night check's line reached the model, Codex took its answer, "
          "systemMessage included")
    B.mock.reset()
    os.remove(facts)
    a = codex.run(PROMPT, SCHEMA, SYSTEM, codex_home=chome, timeout=180, command=B.command, work_root=B.root)
    check(a.output == {"verdict": "ok", "n": 7} and not os.path.exists(facts),
          "the launcher's own call, with that hooks.json in its CODEX_HOME, runs none of them")


HOOKS6 = ["controle-sessionstart", "correction-detector", "gbrain-update-check", "session-indexer", "session-logger",
          "session-recap"]


def install_sh_wires_codex():
    """install.sh --agents codex, from a copy of the kit where Codex is open, in a home of its own with
    stand-ins for what would schedule jobs or reach gbrain: Codex comes from its pinned installer, for
    real. Then the real Codex, in the vault, runs the six hooks from the hooks.json install.sh wrote,
    approved by the tables it wrote in config.toml, no trust bypassed (control: without them, none
    runs), and reads the kit's block in the global AGENTS.md, the vault's AGENTS.md and the skills in
    ~/.agents/skills. gbq's rules allow the kit's gbq only (control: without host_executable, a gbq the
    session writes in its folder would run outside the sandbox)."""
    if WINDOWS:
        print("install.sh is macOS's and Linux's: install.ps1 wires Codex in phase 7")
        return
    repo = os.path.dirname(ENGINE)
    root = os.path.join(B.root, "install-sh")
    kit, home, stubs = (os.path.join(root, n) for n in ("kit", "home", "bin"))
    os.makedirs(stubs)
    os.makedirs(kit)
    for name in ("install.sh", "engine", "skills", "vault-skeleton", "onboarding"):
        (shutil.copytree if os.path.isdir(os.path.join(repo, name)) else shutil.copy)(
            os.path.join(repo, name), os.path.join(kit, name))
    with open(os.path.join(kit, "test-hooks.sh"), "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\nexit 0\n")
    gate = os.path.join(kit, "engine", "hooks", "_agents.py")
    with open(gate, encoding="utf-8") as f:
        text = f.read()
    check(text.count("CODEX_OUVERT = frozenset()\n") == 1, "the gate's line, opened in the kit's copy")
    with open(gate, "w", encoding="utf-8") as f:
        f.write(text.replace("CODEX_OUVERT = frozenset()\n", 'CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})\n'))
    # What would schedule jobs, install Obsidian or reach gbrain: stand-ins that do nothing.
    for t in ("launchctl", "systemctl", "loginctl", "crontab", "brew", "sudo", "apt-get"):
        with open(os.path.join(stubs, t), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n" + ('[ "$1" = -l ] && { echo "no crontab for $USER" >&2; exit 1; }\n' if t == "crontab" else "")
                    + ("exit 1\n" if t == "systemctl" else "exit 0\n"))
    os.makedirs(os.path.join(home, ".bun", "bin"))
    os.makedirs(os.path.join(home, "DEV", "gbrain", ".git"))
    for t in ("bun", "gbrain"):
        with open(os.path.join(home, ".bun", "bin", t), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")
    for d in (stubs, os.path.join(home, ".bun", "bin")):
        for t in os.listdir(d):
            os.chmod(os.path.join(d, t), 0o755)
    # The runner's Codex (the workflow's) out of reach: install.sh has to install its own.
    path = os.pathsep.join([stubs] + [d for d in os.environ["PATH"].split(os.pathsep)
                                      if d and not os.path.exists(os.path.join(d, "codex"))])
    user = os.environ.get("USER") or "runner"
    env = {"HOME": home, "PATH": path, "USER": user, "LOGNAME": user, "TMPDIR": os.path.join(root, "tmp"),
           "LANG": "en_US.UTF-8", "SHELL": "/bin/bash", "GIT_TERMINAL_PROMPT": "0"}
    os.makedirs(env["TMPDIR"])
    p = subprocess.run(["bash", os.path.join(kit, "install.sh"), "--agents", "codex"], stdin=subprocess.DEVNULL,
                       env=env, capture_output=True, timeout=900)
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    print("install.sh's Codex lines:", "\n".join(l for l in out.splitlines() if "odex" in l))
    check(p.returncode == 0 and "brain-in-a-box installed" in out, "install.sh --agents codex ran to the end (%s): %s"
          % (p.returncode, out[-600:]))
    codex_bin = os.path.join(home, ".local", "bin", "codex")
    v = subprocess.run([codex_bin, "--version"], capture_output=True, text=True) if os.path.isfile(codex_bin) else None
    check(v is not None and v.stdout.strip() == "codex-cli 0.162.1" and "Codex 0.162.1 installed" in out,
          "Codex from its pinned installer, checked, in the home's ~/.local/bin: %s" % (v and v.stdout.strip()))
    if v is None:
        return
    chome = os.path.join(home, ".codex")
    check("the kit approved its 6 hooks for you" in out and "with /hooks in Codex" in out, "the approval is said")
    config = os.path.join(chome, "config.toml")
    with open(config, encoding="utf-8") as f:
        toml = f.read()
    check(toml.count('[hooks.state."%s:' % os.path.join(chome, "hooks.json")) == 6, "six tables in config.toml")
    shutil.copy(os.path.join(B.home, "auth.json"), chome)
    vault = os.path.join(home, "Documents", "Brain")
    vus = os.path.join(home, ".brain-in-a-box", _agents.VUS)

    def session():
        if os.path.exists(vus):
            os.remove(vus)
        B.mock.reset()
        r = B.raw_exec(vault, b"Reply.", home=chome, ephemeral=False, env={"HOME": home, "USERPROFILE": home},
                       binary=codex_bin)
        check(r.returncode == 0, "codex exec in the vault ran (%s, %s)" % (r.returncode, r.stderr.decode("utf-8", "replace")[-300:]))
        return sorted(_agents.vus(home)), json.dumps(B.mock.reqs, ensure_ascii=False)

    # Codex runs one event's hooks together, and a hook may miss another's line in the sign of life
    # once (_agents.vu): what each session showed, over three sessions at most.
    seen, dump = session()
    for _ in range(2):
        if seen == HOOKS6:
            break
        seen = sorted(set(seen) | set(session()[0]))
    check(seen == HOOKS6, "the real Codex ran the six hooks install.sh wired and approved, no trust bypassed: %s" % seen)
    check("Codex does not load `CLAUDE.md` files." in dump, "Codex read the kit's block in the global AGENTS.md")
    check("This brain's rules are in `CLAUDE.md`" in dump, "Codex read the vault's AGENTS.md (the vault is a git repository)")
    with open(os.path.join(repo, "skills", "brain-boost", "SKILL.md"), encoding="utf-8") as f:
        desc = f.read().split("description:", 1)[1].split("\n", 1)[0].strip()[:60]
    check("brain-boost" in dump and desc in dump, "Codex announced the kit's skills from ~/.agents/skills")
    shutil.move(config, config + ".kept")
    seen, _ = session()
    check(seen == [], "control: without the tables install.sh wrote, Codex runs none of the hooks: %s" % seen)
    shutil.move(config + ".kept", config)
    rules = os.path.join(chome, "rules", "brain-in-a-box.rules")
    gbq = os.path.join(home, ".local", "bin", "gbq")

    def decision(rules_file, *cmd):
        r = subprocess.run([codex_bin, "execpolicy", "check", "--resolve-host-executables", "--rules", rules_file, "--"]
                           + list(cmd), capture_output=True, text=True, env=dict(os.environ, HOME=home))
        try:
            return json.loads(r.stdout).get("decision")
        except ValueError:
            return "error: %s %s" % (r.stdout[-200:], r.stderr[-200:])

    allowed = [c for c in (["gbq", "query", "x"], ["gbq", "search", "x"], [gbq, "query", "x"], ["~/.local/bin/gbq", "query", "x"])
               if decision(rules, *c) == "allow"]
    check(len(allowed) == 4, "gbq query and gbq search allowed, at the kit's gbq: %s" % allowed)
    asked = {" ".join(c): decision(rules, *c) for c in (["./gbq", "query", "x"], ["/tmp/gbq", "query", "x"],
                                                      ["gbq", "sync"], ["gbq", "skillify", "https://x"])}
    check(all(v is None for v in asked.values()), "another gbq, or another gbq command, matches no rule: %s" % asked)
    bare = os.path.join(root, "no-host.rules")
    with open(rules, encoding="utf-8") as f, open(bare, "w", encoding="utf-8") as g:
        g.write("".join(l for l in f if not l.startswith("host_executable(")))
    check(decision(bare, "./gbq", "query", "x") == "allow",
          "control: without host_executable, a ./gbq the session wrote would run without asking")


def main():
    global B
    if os.environ.get("GITHUB_ACTIONS") != "true":
        sys.exit("codex_mock.py writes Codex's system settings: it runs on a CI runner only")
    B = Bench()
    if not B.codex:
        sys.exit("FAIL  no codex to run (the official installer's place, or PATH)")
    print("codex:", B.codex[0], subprocess.run(B.codex + ["--version"], capture_output=True, text=True).stdout.strip())
    try:
        for fn in (answers_with_everything_locked, forced_tool_calls_are_refused, an_unknown_feature_pauses_codex,
                   the_user_s_mcp_server_never_starts, a_system_config_is_neutralised,
                   a_system_otel_exporter_gets_nothing, requirements_pause_codex,
                   an_api_key_login_is_refused, the_reflection_and_the_review_ask_codex_through_the_choice,
                   a_real_codex_session_is_read, codex_runs_the_kits_hooks, install_sh_wires_codex):
            scenario(fn)
    finally:
        B.mock.close()
        B.trap.close()
        shutil.rmtree(B.root, ignore_errors=True)
    print("\n%d failure(s)" % len(FAILURES) + "".join("\n  - " + f for f in FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
