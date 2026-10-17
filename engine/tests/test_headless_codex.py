"""Tests of engine/hooks/_headless_codex.py: one question to Codex, with nothing but the question.

The real codex is never started here (the job codex-mock of test.yml runs it, against
mock_openai.py). fake_codex.py stands for it: it answers the probes as Codex 0.162.1 did on
step 0's runners, applies the `-c` it is given as the real one does, records what it is given
(argv, environment, folder, stdin) and plays `codex exec --json` streams. Every run happens in a
throwaway HOME and CODEX_HOME, with this machine's managed files replaced by throwaway paths.

    python3 -I -m unittest discover -s engine/tests -v
"""
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")
ENGINE = os.path.dirname(HERE)
FAKE = os.path.join(HERE, "fake_codex.py")
P0 = Path(HERE) / "fixtures" / "codex" / "phase0"
sys.path.insert(0, HOOKS)
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import _headless_codex as codex  # noqa: E402
import mock_openai  # noqa: E402
from _headless_claude import HeadlessError  # noqa: E402

WINDOWS = os.name == "nt"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "items"],
          "properties": {"verdict": {"type": "string", "enum": ["ok", "ko"]},
                         "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                              "required": ["t"], "properties": {"t": {"type": "string"}}}}}}
ANSWER = {"verdict": "ok", "items": [{"t": "journée à Montréal ✓"}]}
PARENT_SECRETS = {"OPENAI_API_KEY": "sk-canari-parent-openai", "CODEX_API_KEY": "sk-canari-parent-codex",
                  "CODEX_ACCESS_TOKEN": "canari-access-token", "OPENAI_BASE_URL": "http://canari.invalid/v1",
                  "RUST_LOG": "trace", "VOYAGE_API_KEY": "pa-canari-parent", "ANTHROPIC_API_KEY": "sk-ant-canari",
                  "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-canari", "GITHUB_TOKEN": "ghp_canari_parent",
                  "CODEX_SQLITE_HOME": "/canari", "CODEX_ROLLOUT_TRACE_ROOT": "/canari",
                  "OPENAI_WORKLOAD_IDENTITY_CONTEXT": "canari"}
FAKE_KEY = "sk-proj-canari0123456789ABCD"


def chatgpt_auth(**changes):
    d = {"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-exchanged-by-a-real-chatgpt-login",
         "tokens": {"id_token": mock_openai.fake_id_token("plus"), "access_token": "fake-access",
                    "refresh_token": "fake-refresh",
                    "account_id": "acct"}, "last_refresh": "2026-10-15T00:00:00Z"}
    d.update(changes)
    return d


def applied(capture_text, coupees):
    """What `codex features list` prints with our -c, as Codex 0.162.1 does (removed: ignored,
    unified_exec: stays on)."""
    out = []
    for line in capture_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name, stage, on = parts[0], " ".join(parts[1:-1]), parts[-1]
        if name in coupees and stage != "removed" and name != "unified_exec":
            on = "false"
        out.append("%-40s %-18s %s" % (name, stage, on))
    return "\n".join(out) + "\n"


def captures():
    return sorted(P0.glob("*/features-default.txt"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="codex-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.codex_home = os.path.join(self.home, ".codex")
        self.fake = os.path.join(self.home, ".codex-fake")
        os.makedirs(self.codex_home)
        os.makedirs(self.fake)
        self.managed = os.path.join(self.tmp, "etc-codex")
        patches = [mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home}),
                   mock.patch.object(codex, "MANAGED", [os.path.join(self.managed, "requirements.toml"),
                                                        os.path.join(self.managed, "managed_config.toml")]),
                   mock.patch.object(codex, "MDM", [os.path.join(self.tmp, "mdm", "com.openai.codex.plist")]),
                   mock.patch.object(codex, "PROGRAM_DATA", os.path.join(self.tmp, "programdata"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.auth(chatgpt_auth())
        self.set("answer.json", json.dumps(ANSWER, ensure_ascii=False))
        self.set("home", self.codex_home)

    def set(self, name, text):
        with open(os.path.join(self.fake, name), "w", encoding="utf-8") as f:
            f.write(text)

    def auth(self, d):
        with open(os.path.join(self.codex_home, "auth.json"), "w", encoding="utf-8") as f:
            json.dump(d, f)

    def recorded(self, name):
        with open(os.path.join(self.fake, name), encoding="utf-8") as f:
            return json.load(f)

    def calls(self):
        p = os.path.join(self.fake, "calls.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def exec_calls(self):
        return [c for c in self.calls() if c["argv"][:1] == ["exec"] and "--help" not in c["argv"]]

    def command(self):
        return [sys.executable, "-I", FAKE]

    def run_codex(self, prompt="Une journée.\n", timeout=60, **kw):
        return codex.run(prompt, SCHEMA, "Tu es un juge strict.", codex_home=self.codex_home, timeout=timeout,
                         command=self.command(), work_root=self.tmp, **kw)

    def refused(self, kind, reason=None, **kw):
        with self.assertRaises(HeadlessError) as cm:
            self.run_codex(**kw)
        self.assertEqual(cm.exception.kind, kind, cm.exception)
        if reason is not None:
            self.assertEqual(cm.exception.reason, reason, cm.exception)
        return cm.exception


# --------------------------------------------------------------------------- the feature list

class FeatureList(unittest.TestCase):
    """engine/codex-features.json: the features the launcher turns off (Chad's decision 1)."""

    def test_every_listed_name_is_passed_false(self):
        coupees = codex.load_features()
        flags = codex.config_flags(coupees)
        pairs = [flags[i + 1] for i in range(0, len(flags), 2)]
        self.assertTrue(all(flags[i] == "-c" for i in range(0, len(flags), 2)))
        for name in coupees:
            self.assertIn("features.%s=false" % name, pairs)
        self.assertFalse([p for p in pairs if p.startswith("features.") and not p.endswith("=false")])

    def test_the_tool_features_of_step_0_are_in_it(self):
        coupees = set(codex.load_features())
        for name in ("shell_tool", "view_image", "image_generation", "goals", "sleep_tool", "code_mode_host", "apps",
                     "plugins", "remote_plugin", "multi_agent", "multi_agent_v2", "memories", "skill_search",
                     "tool_suggest", "browser_use", "computer_use", "in_app_browser", "code_mode", "hooks"):
            self.assertIn(name, coupees)
            self.assertIn(name, codex.CORE)

    def test_every_capture_of_step_0_passes_the_strict_guard(self):
        coupees = codex.load_features()
        self.assertEqual(len(captures()), 6)
        for p in captures():
            with self.subTest(capture=p.parent.name):
                codex.check_features(applied(p.read_text(encoding="utf-8"), coupees), coupees)

    def test_a_name_taken_out_pauses_codex(self):
        # Adding a name can only make the guard stricter; taking one out never loosens it.
        coupees = codex.load_features()
        text = (P0 / "ubuntu-0.162.1" / "features-default.txt").read_text(encoding="utf-8")
        for name in ("fast_mode", "shell_tool", "enable_request_compression"):
            fewer = tuple(n for n in coupees if n != name)
            with self.subTest(name=name), self.assertRaises(HeadlessError) as cm:
                codex.check_features(applied(text, fewer), fewer)
            self.assertEqual((cm.exception.kind, cm.exception.reason), ("unsupported", "features"))
            self.assertIn(name, str(cm.exception))

    def test_a_feature_codex_brings_on_pauses_it(self):
        coupees = codex.load_features()
        text = applied((P0 / "macos-0.162.1" / "features-default.txt").read_text(encoding="utf-8"), coupees)
        for extra in ("brand_new_tool                           stable             true",
                      "next_thing                               under development  true"):
            with self.subTest(extra=extra), self.assertRaises(HeadlessError) as cm:
                codex.check_features(text + extra + "\n", coupees)
            self.assertEqual(cm.exception.reason, "features")
            self.assertIn(extra.split()[0], str(cm.exception))
        codex.check_features(text + "old_thing                                removed            true\n", coupees)

    def test_a_listed_feature_that_stays_on_pauses_it(self):
        coupees = codex.load_features()
        text = applied((P0 / "ubuntu-0.162.1" / "features-default.txt").read_text(encoding="utf-8"), coupees)
        stuck = text.replace("code_mode_host                           stable             false",
                             "code_mode_host                           stable             true")
        self.assertNotEqual(stuck, text)
        with self.assertRaises(HeadlessError) as cm:
            codex.check_features(stuck, coupees)
        self.assertIn("code_mode_host", str(cm.exception))

    def test_unified_exec_on_only_with_the_shell_off(self):
        coupees = codex.load_features()
        text = applied((P0 / "ubuntu-0.162.1" / "features-default.txt").read_text(encoding="utf-8"), coupees)
        self.assertRegex(text, r"(?m)^unified_exec\s+stable\s+true$")
        codex.check_features(text, coupees)

    def test_a_core_feature_missing_from_codex_or_unreadable_output(self):
        coupees = codex.load_features()
        text = applied((P0 / "ubuntu-0.162.1" / "features-default.txt").read_text(encoding="utf-8"), coupees)
        gone = "\n".join(l for l in text.splitlines() if not l.startswith("shell_tool ")) + "\n"
        for bad in (gone, "", "Usage: codex features list\n", text + "a line that is not a row\n"):
            with self.subTest(bad=bad[-40:]), self.assertRaises(HeadlessError) as cm:
                codex.check_features(bad, coupees)
            self.assertEqual(cm.exception.reason, "features")

    def test_a_broken_data_file_is_refused(self):
        tmp = tempfile.mkdtemp(prefix="codex-features-")
        self.addCleanup(shutil.rmtree, tmp, True)
        good = codex.load_features()
        cases = {"not-json": "{", "list": "[]", "version": json.dumps({"version": 2, "coupees": list(good)}),
                 "name": json.dumps({"version": 1, "coupees": list(good) + ["Shell tool"]}),
                 "twice": json.dumps({"version": 1, "coupees": list(good) + [good[0]]}),
                 "core-gone": json.dumps({"version": 1, "coupees": [n for n in good if n != "hooks"]})}
        for name, text in cases.items():
            p = os.path.join(tmp, name + ".json")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            with self.subTest(case=name), self.assertRaises(HeadlessError) as cm:
                codex.load_features(p)
            self.assertEqual((cm.exception.kind, cm.exception.reason), ("unsupported", "features"))
        with self.assertRaises(HeadlessError):
            codex.load_features(os.path.join(tmp, "absent.json"))

    def test_the_data_file_is_outside_codeowners_the_launcher_inside(self):
        with open(os.path.join(os.path.dirname(ENGINE), ".github", "CODEOWNERS"), encoding="utf-8") as f:
            owned = [l.split()[0] for l in f if l.strip() and not l.startswith("#")]
        self.assertIn("/engine/hooks/_headless_codex.py", owned)
        self.assertFalse([o for o in owned if "/engine/codex-features.json".startswith(o)])


# --------------------------------------------------------------------------- the command

class Command(Base):
    def test_argv_is_exactly_the_reviewed_command(self):
        self.run_codex()
        argv = self.recorded("argv.json")
        work = self.recorded("cwd.json")["cwd"]
        head = argv[:argv.index("-c")]
        cage = os.path.dirname(work)
        self.assertEqual(head, ["exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                                "--ignore-rules", "-s", "read-only", "-C", work,
                                "--output-schema", os.path.join(cage, "schema.json"),
                                "-o", os.path.join(cage, "answer.json")])
        self.assertEqual(argv[-1], "-")   # the prompt: stdin, nothing else
        pairs = argv[len(head):-1]
        self.assertTrue(all(pairs[i] == "-c" for i in range(0, len(pairs), 2)), pairs)
        values = pairs[1::2]
        for must in ('model_provider="openai"', 'openai_base_url=""',
                     'chatgpt_base_url="https://chatgpt.com/backend-api/"', 'cli_auth_credentials_store="file"',
                     'developer_instructions=""', "notify=[]", 'approval_policy="never"', 'web_search="disabled"',
                     "agents.enabled=false", "project_doc_max_bytes=0", "skills.include_instructions=false",
                     "skills.bundled.enabled=false", 'history.persistence="none"',
                     "check_for_update_on_startup=false",
                     # No telemetry export: a system config.toml's [otel] would send the prompt elsewhere.
                     'otel.exporter="none"', 'otel.trace_exporter="none"', 'otel.metrics_exporter="none"',
                     "otel.log_user_prompt=false", "otel.log_agent_responses=false",
                     "model_instructions_file=" + json.dumps(os.path.join(cage, "system.md"))):
            self.assertIn(must, values)
        self.assertEqual(values, codex.config_flags(codex.load_features(),
                                                    instructions=os.path.join(cage, "system.md"))[1::2])
        for word in argv:
            self.assertFalse(word.startswith(("--dangerously", "--enable", "--add-dir", "--profile", "-p")), word)
        self.assertNotIn("Une journée", " ".join(argv))

    def test_every_option_of_the_call_is_checked_in_the_help_first(self):
        self.run_codex()
        argv = self.recorded("argv.json")
        long_names = {"-s": "--sandbox", "-C": "--cd", "-o": "--output-last-message"}
        used = {long_names.get(a, a) for a in argv if a.startswith("-") and a not in ("-", "-c")}
        self.assertEqual(used - set(codex.REQUIRED_FLAGS), set())

    def test_model_and_effort_when_asked_and_checked(self):
        self.run_codex(model="gpt-5.2-codex", effort="medium")
        argv = self.recorded("argv.json")
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5.2-codex")
        self.assertIn('model_reasoning_effort="medium"', argv)
        for bad in ({"model": "x; rm -rf ~"}, {"model": "--dangerously-bypass-approvals-and-sandbox"},
                    {"effort": 'low"\nnotify=["sh"]'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_codex(**bad)

    def test_schema_and_instructions_reach_their_files(self):
        self.run_codex()
        listing = self.recorded("cwd.json")["listing"]
        self.assertEqual(listing, [])   # the work folder is empty: no file of ours to read
        argv = self.recorded("argv.json")
        self.assertEqual(argv[argv.index("--output-schema") + 1].rsplit(os.sep, 1)[-1], "schema.json")

    def test_a_schema_codex_would_reject_is_refused_before_anything_runs(self):
        loose = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
        partial = {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "string"},
                                                                               "b": {"type": "string"}},
                   "required": ["a"]}
        nested = {"type": "object", "additionalProperties": False, "required": ["l"],
                  "properties": {"l": {"type": "array", "items": loose}}}
        for bad in (loose, partial, nested):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                codex.run("x", bad, "s", codex_home=self.codex_home, timeout=10, command=self.command(),
                          work_root=self.tmp)
        self.assertEqual(self.calls(), [])

    def test_instructions_are_never_empty(self):
        # Codex refuses an empty model_instructions_file, and every probe reads it.
        with self.assertRaises(ValueError):
            codex.run("x", SCHEMA, " \n", codex_home=self.codex_home, timeout=10, command=self.command(),
                      work_root=self.tmp)
        self.assertEqual(self.calls(), [])
        self.assertEqual(codex.check(self.codex_home, command=self.command(), work_root=self.tmp), "0.162.1")

    def test_the_prompt_goes_through_stdin_whole(self):
        unit = "Ligne é à ü ç — 漢字 🙂 «guillemets»\r\nsecond line\n"
        big = "DEBUT " + unit * (150000 // len(unit)) + " FIN"
        self.run_codex(prompt=big)
        with open(os.path.join(self.fake, "stdin.txt"), "rb") as f:
            self.assertEqual(f.read(), big.encode("utf-8"))

    def test_the_folder_is_private_fresh_and_gone(self):
        self.run_codex()
        cwd = self.recorded("cwd.json")
        if not WINDOWS:
            self.assertEqual(cwd["mode"], "0o700")
        self.assertTrue(cwd["cwd"].startswith(os.path.realpath(self.tmp)) or cwd["cwd"].startswith(self.tmp))
        self.assertFalse(os.path.exists(cwd["cwd"]))
        self.assertFalse([p for p in os.listdir(self.tmp) if p.startswith("brain-codex-")])


class Environment(Base):
    def test_the_child_gets_the_allow_list_only(self):
        with mock.patch.dict(os.environ, PARENT_SECRETS):
            self.run_codex()
        env = self.recorded("env.json")
        for key, value in PARENT_SECRETS.items():
            self.assertNotIn(key, env)
            self.assertNotIn(value, json.dumps(env))
        self.assertEqual(env["CODEX_HOME"], self.codex_home)
        self.assertEqual(env["BRAIN_HEADLESS"], "1")
        self.assertEqual(env["HOME"], self.home)
        allowed = {"HOME", "PATH", "LANG", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "CODEX_HOME", "BRAIN_HEADLESS",
                   "SSL_CERT_FILE", "CODEX_CA_CERTIFICATE"} | set(codex.WINDOWS_VARS) | set(codex.PROXY_VARS)
        allowed = {k.upper() for k in allowed} | {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}   # set by macOS, by Python
        self.assertEqual({k for k in env if k.upper() not in allowed}, set())
        tmp = env.get("TMPDIR") or env.get("TEMP")
        self.assertEqual(os.path.dirname(tmp), os.path.dirname(self.recorded("cwd.json")["cwd"]))

    def test_every_probe_runs_with_the_same_environment(self):
        with mock.patch.dict(os.environ, PARENT_SECRETS):
            self.run_codex()
        homes = {c["codex_home"] for c in self.calls()}
        self.assertTrue(homes <= {self.codex_home} | {h for h in homes if h and h.endswith("vide")}, homes)
        self.assertIn(self.codex_home, homes)

    def test_the_path_holds_codex_and_the_system_only(self):
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(["/home/x/evil-bin", os.environ.get("PATH", "")])}):
            self.run_codex()
        path = self.recorded("env.json")["PATH"].split(os.pathsep)
        self.assertNotIn("/home/x/evil-bin", path)
        self.assertIn(os.path.dirname(sys.executable), path)

    def test_proxies_and_their_certificate_go_through(self):
        proxy = {"HTTPS_PROXY": "http://proxy.corp:3128", "SSL_CERT_FILE": "/etc/corp-ca.pem",
                 "CODEX_CA_CERTIFICATE": "/etc/corp-ca.pem"}
        with mock.patch.dict(os.environ, proxy):
            self.run_codex()
        env = self.recorded("env.json")
        for k, v in proxy.items():
            self.assertEqual(env.get(k), v)


# --------------------------------------------------------------------------- the checks before

class Login(Base):
    def test_a_chatgpt_login_with_its_exchanged_key_is_used(self):
        self.assertEqual(self.run_codex().output, ANSWER)

    def test_an_api_key_login_is_never_used(self):
        self.auth({"auth_mode": "apikey", "OPENAI_API_KEY": FAKE_KEY})
        e = self.refused("auth", "billed")
        self.assertNotIn(FAKE_KEY, str(e))
        self.assertEqual(self.exec_calls(), [])

    def test_no_login_or_a_half_one(self):
        os.remove(os.path.join(self.codex_home, "auth.json"))
        self.refused("auth", "logged_out")
        for bad in (chatgpt_auth(tokens={"access_token": "a"}), chatgpt_auth(tokens={"refresh_token": ""}),
                    chatgpt_auth(auth_mode="ChatGPT"), chatgpt_auth(tokens=None)):
            self.auth(bad)
            with self.subTest(bad=bad):
                self.refused("auth")
        with open(os.path.join(self.codex_home, "auth.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.refused("auth", "logged_out")
        self.assertEqual(self.exec_calls(), [])

    @unittest.skipIf(WINDOWS, "no FIFO on Windows")
    def test_an_auth_file_that_is_a_pipe_does_not_hang(self):
        p = os.path.join(self.codex_home, "auth.json")
        os.remove(p)
        os.mkfifo(p)
        t = time.monotonic()
        self.refused("auth", "logged_out")
        self.assertLess(time.monotonic() - t, 20)

    def test_a_workspace_account_is_paused(self):
        # Codex downloads a Business, Edu or Enterprise workspace's own settings, which pass over
        # every -c (cloud-config/src/service.rs, config_requirements.rs): personal plans only.
        for plan in ("business", "enterprise", "hc", "ent26", "edu", "education", "edu_plus", "k12", "team",
                     "self_serve_business_usage_based", "enterprise_cbp_automation", "guest", "free_workspace",
                     "a-plan-of-tomorrow", "", None):
            self.auth(chatgpt_auth(tokens={"id_token": mock_openai.fake_id_token(plan), "access_token": "a",
                                           "refresh_token": "r"}))
            with self.subTest(plan=plan):
                e = self.refused("unsupported", "managed")
                self.assertNotIn("mock@example.com", str(e))
        for id_token in ("x.y.z", "", "a.b", 42, None, mock_openai.fake_id_token("plus").replace(".", "", 1)):
            self.auth(chatgpt_auth(tokens={"id_token": id_token, "access_token": "a", "refresh_token": "r"}))
            with self.subTest(id_token=id_token):
                self.refused("unsupported", "managed")
        self.assertEqual(self.exec_calls(), [])

    def test_a_personal_plan_is_asked(self):
        for plan in ("free", "go", "plus", "pro", "prolite", "promax", "Plus"):
            self.auth(chatgpt_auth(tokens={"id_token": mock_openai.fake_id_token(plan), "access_token": "a",
                                           "refresh_token": "r"}))
            with self.subTest(plan=plan):
                self.assertEqual(self.run_codex().output, ANSWER)

    def test_login_status_is_compared_never_copied(self):
        cases = [("Logged in using an API key - " + FAKE_KEY[:8] + "***" + FAKE_KEY[-5:], "billed"),
                 ("Logged in using workload identity", "billed"), ("Logged in using access token", "billed"),
                 ("Not logged in", "logged_out"), ("Error checking login status: " + FAKE_KEY, "logged_out"),
                 ("Logged in using ChatGPTx", "logged_out")]
        for line, reason in cases:
            self.set("login", line)
            with self.subTest(line=line):
                e = self.refused("auth", reason)
                self.assertNotIn(FAKE_KEY[:8], str(e))
                self.assertNotIn(FAKE_KEY[-5:], str(e))
                self.assertNotIn(line, str(e))
        self.assertEqual(self.exec_calls(), [])

    def test_the_login_is_read_from_the_file_store(self):
        self.run_codex()
        status = [c["argv"] for c in self.calls() if "login" in c["argv"]]
        self.assertEqual(len(status), 1)
        self.assertIn('cli_auth_credentials_store="file"', status[0])

    def test_a_codex_home_that_is_not_there(self):
        with self.assertRaises(HeadlessError) as cm:
            codex.run("x", SCHEMA, "s", codex_home=os.path.join(self.tmp, "absent"), timeout=10,
                      command=self.command(), work_root=self.tmp)
        self.assertEqual((cm.exception.kind, cm.exception.reason), ("auth", "logged_out"))
        with self.assertRaises(HeadlessError):
            codex.run("x", SCHEMA, "s", codex_home="relative/.codex", timeout=10, command=self.command(),
                      work_root=self.tmp)


class DotEnv(Base):
    def write_env(self, text):
        with open(os.path.join(self.codex_home, ".env"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_names_codex_would_load_that_reroute_or_bill_are_refused(self):
        for line in ("OPENAI_API_KEY=" + FAKE_KEY, "export OPENAI_API_KEY=" + FAKE_KEY, "RUST_LOG=trace",
                     "HTTPS_PROXY=http://evil:8080", "https_proxy=http://evil:8080", "SSL_CERT_FILE=/tmp/evil.pem",
                     "NODE_EXTRA_CA_CERTS=/tmp/evil.pem", "OPENAI_ORGANIZATION=org-x", "OPENAI_BASE_URL=http://evil",
                     "SOMETHING_ELSE=1", "  OPENAI_API_KEY = " + FAKE_KEY):
            self.write_env("# a comment\nLANG=fr_FR.UTF-8\n" + line + "\n")
            with self.subTest(line=line):
                e = self.refused("unsupported", "dotenv")
                self.assertNotIn(FAKE_KEY, str(e))
                self.assertNotIn("evil", str(e))
        self.assertEqual(self.exec_calls(), [])

    def test_harmless_or_ignored_names_pass(self):
        self.write_env("# mine\n\nLANG=fr_FR.UTF-8\nCODEX_ANYTHING=ignored-by-codex\nexport LANG=C.UTF-8\n")
        self.assertEqual(self.run_codex().output, ANSWER)

    def test_a_line_that_cannot_be_read_is_refused(self):
        for text in ("JUST A LINE\n", "LANG\n", "\ufeffOPENAI_API_KEY=x\n"):
            self.write_env(text)
            with self.subTest(text=text):
                self.refused("unsupported", "dotenv")

    @unittest.skipIf(WINDOWS, "no FIFO on Windows")
    def test_a_dotenv_that_is_a_pipe_does_not_hang(self):
        os.mkfifo(os.path.join(self.codex_home, ".env"))
        self.refused("unsupported", "dotenv")


class ManagedLayers(Base):
    def put(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_requirements_or_a_legacy_managed_config_pause_codex(self):
        for name in ("requirements.toml", "managed_config.toml"):
            for text in ('model_provider = "trap"\n', '[features]\nshell_tool = true\n',
                         'allowed_sandbox_modes = ["danger-full-access"]\n', 'chatgpt_base_url = "http://evil/"\n'):
                p = os.path.join(self.managed, name)
                self.put(p, text)
                with self.subTest(name=name, text=text):
                    e = self.refused("unsupported", "managed")
                    self.assertIn(name, str(e))
                os.remove(p)
        self.assertEqual(self.exec_calls(), [])

    def test_comments_only_are_not_settings(self):
        self.put(os.path.join(self.managed, "requirements.toml"), "# nothing yet\n\n   # still nothing\n")
        self.assertEqual(self.run_codex().output, ANSWER)

    def test_an_mdm_profile_that_forces_codex_settings(self):
        p = codex.MDM[0]
        os.makedirs(os.path.dirname(p), exist_ok=True)
        for key in ("config_toml_base64", "requirements_toml_base64"):
            with open(p, "wb") as f:
                plistlib.dump({key: "bW9kZWxfcHJvdmlkZXIgPSAidHJhcCIK"}, f)
            with self.subTest(key=key):
                self.refused("unsupported", "managed")
        with open(p, "wb") as f:
            plistlib.dump({"other": "x"}, f)
        self.assertEqual(self.run_codex().output, ANSWER)


class Probes(Base):
    def test_an_old_codex_or_one_without_our_flags(self):
        self.set("version", "codex-cli 0.161.9")
        self.refused("unsupported", "version")
        self.set("version", "codex-cli 0.163.0-alpha.11")
        help_text = open(os.path.join(HERE, "fixtures", "codex", "exec-help-0.162.1.txt"), encoding="utf-8").read()
        p = os.path.join(self.tmp, "help.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(help_text.replace("--ignore-user-config", "--ignore-user-cfg"))
        self.set("help", p)
        e = self.refused("unsupported", "flags")
        self.assertIn("--ignore-user-config", str(e))
        self.set("version", "something odd")
        self.refused("start")
        self.assertEqual(self.exec_calls(), [])

    def test_the_version_comes_back(self):
        self.set("version", "codex-cli 0.163.0-alpha.11")
        self.assertEqual(self.run_codex().version, "0.163.0-alpha.11")
        self.assertEqual(codex.check(self.codex_home, command=self.command(), work_root=self.tmp),
                         "0.163.0-alpha.11")

    def test_features_through_the_fake_codex(self):
        self.set("features-extra", "brand_new_tool                           stable             true")
        e = self.refused("unsupported", "features")
        self.assertIn("brand_new_tool", str(e))
        self.set("features-extra", "")
        self.set("features-stuck", "image_generation")
        self.assertIn("image_generation", str(self.refused("unsupported", "features")))
        self.set("features-stuck", "")
        self.set("features-rc", "1")
        self.refused("unsupported", "features")
        self.assertEqual(self.exec_calls(), [])

    def test_features_list_is_read_with_our_settings_the_user_s_codex_home_and_an_empty_one(self):
        self.run_codex()
        lists = [c for c in self.calls() if "features" in c["argv"] and "list" in c["argv"]]
        self.assertEqual(len(lists), 2)
        self.assertEqual(lists[0]["codex_home"], self.codex_home)
        self.assertNotEqual(lists[1]["codex_home"], self.codex_home)
        self.assertTrue(lists[1]["codex_home"].endswith("vide"), lists[1]["codex_home"])
        for c in lists:
            for name in codex.load_features():
                self.assertIn("features.%s=false" % name, c["argv"])

    def test_a_feature_only_the_user_s_config_turns_off_pauses_codex(self):
        # `features list` reads the user's config.toml, which the call ignores (--ignore-user-config):
        # a feature a system layer turns on and that file turns off is on in the call.
        self.set("features-extra", "brand_new_tool                           stable             true")
        self.set("features-user-off", "brand_new_tool")
        e = self.refused("unsupported", "features")
        self.assertIn("brand_new_tool", str(e))
        self.assertEqual(self.exec_calls(), [])

    def test_check_asks_nothing(self):
        codex.check(self.codex_home, command=self.command(), work_root=self.tmp)
        self.assertEqual(self.exec_calls(), [])
        self.assertFalse(os.path.exists(os.path.join(self.fake, "stdin.txt")))


class Mcp(Base):
    def test_a_system_server_is_turned_off_by_name(self):
        self.set("mcp-system", '["corp-search"]')
        self.run_codex()
        self.assertIn("mcp_servers.corp-search.enabled=false", self.recorded("argv.json"))

    def test_a_server_of_the_user_s_config_is_left_to_ignore_user_config(self):
        # Turning it off by name would break the call: with --ignore-user-config, Codex reads
        # `-c mcp_servers.<name>.enabled=false` as a server without a command (invalid transport).
        self.set("mcp-user", '["my-notes"]')
        with open(os.path.join(self.codex_home, "config.toml"), "w", encoding="utf-8") as f:
            f.write('[mcp_servers.my-notes]\ncommand = "notes-mcp"\n')
        self.assertEqual(self.run_codex().output, ANSWER)
        self.assertFalse([a for a in self.recorded("argv.json") if "my-notes" in a])

    def test_a_server_from_a_layer_we_cannot_name_pauses_codex(self):
        self.set("mcp-user", '["cloud-tool"]')   # listed with the user's CODEX_HOME, not in its config.toml
        e = self.refused("unsupported", "mcp")
        self.assertIn("cloud-tool", str(e))

    def test_a_server_named_in_a_comment_is_not_declared(self):
        self.set("mcp-user", '["cloud-tool"]')
        for text in ("# [mcp_servers.cloud-tool]\n# command = \"x\"\n",
                     "[mcp_servers]  # cloud-tool = { command = \"x\" }\n",
                     "[mcp_servers]\n#cloud-tool = { command = \"x\" }\n",
                     "model = \"o3\"  # mcp_servers.cloud-tool.command = \"x\"\n"):
            with open(os.path.join(self.codex_home, "config.toml"), "w", encoding="utf-8") as f:
                f.write(text)
            with self.subTest(text=text):
                self.assertIn("cloud-tool", str(self.refused("unsupported", "mcp")))
        self.assertEqual(self.exec_calls(), [])

    def test_a_server_that_stays_on_or_a_name_codex_cannot_take(self):
        self.set("mcp-system", '["corp-search"]')
        self.set("mcp-stuck", '["corp-search"]')
        self.refused("unsupported", "mcp")
        self.set("mcp-stuck", "[]")
        self.set("mcp-system", '["corp.search"]')
        self.refused("unsupported", "mcp")
        self.assertEqual(self.exec_calls(), [])

    def test_the_lists_are_read_with_an_empty_codex_home_then_the_user_s(self):
        self.set("mcp-system", '["corp-search"]')
        self.run_codex()
        lists = [c for c in self.calls() if "mcp" in c["argv"]]
        self.assertEqual(len(lists), 3)
        empty = lists[0]["codex_home"]
        self.assertNotEqual(empty, self.codex_home)
        self.assertEqual(lists[1]["codex_home"], empty)
        self.assertEqual(lists[2]["codex_home"], self.codex_home)
        self.assertNotIn("mcp_servers.corp-search.enabled=false", lists[0]["argv"])
        for c in lists[1:]:
            self.assertIn("mcp_servers.corp-search.enabled=false", c["argv"])


# --------------------------------------------------------------------------- the stream

class Stream(Base):
    def test_a_clean_answer(self):
        a = self.run_codex()
        self.assertEqual(a.output, ANSWER)
        self.assertEqual(a.version, "0.162.1")
        self.assertIsNone(a.cost_usd)
        self.assertEqual(a.num_turns, 1)
        self.assertEqual(a.tokens, {"input_tokens": 1200, "cached_input_tokens": 0, "output_tokens": 34,
                                    "reasoning_output_tokens": 8})

    def test_warnings_are_tolerated(self):
        self.set("scenario", "reconnecting")
        self.assertEqual(self.run_codex().output, ANSWER)

    def test_anything_that_is_not_an_answer_ends_the_run(self):
        for scenario in ("command", "file-change", "unknown-item", "unknown-event", "second-turn"):
            self.set("scenario", scenario)
            with self.subTest(scenario=scenario):
                e = self.refused("init")
                self.assertNotIn("id_ed25519", str(e))
                self.assertNotIn("/etc/passwd", str(e))

    def test_no_thread_no_answer(self):
        self.set("scenario", "no-thread")
        self.refused("init")

    def test_errors_get_their_kind(self):
        for scenario, kind in (("401", "auth"), ("usage-limit", "quota"), ("api-error", "api"),
                               ("no-result", "api")):
            self.set("scenario", scenario)
            with self.subTest(scenario=scenario):
                e = self.refused(kind)
                self.assertEqual(e.retryable, kind == "api")

    def test_the_answer_file_must_be_the_streamed_answer_and_an_object(self):
        for scenario in ("mismatch", "no-file", "not-json", "not-object"):
            self.set("scenario", scenario)
            with self.subTest(scenario=scenario):
                e = self.refused("invalid")
                self.assertNotIn("Voici ma réponse", str(e))

    def test_a_codex_that_hangs_is_killed_with_its_children(self):
        self.set("scenario", "hang")
        t = time.monotonic()
        self.refused("timeout", timeout=4)
        self.assertLess(time.monotonic() - t, 30)
        with open(os.path.join(self.fake, "grandchild.pid"), encoding="utf-8") as f:
            pid = int(f.read())
        if not WINDOWS:
            time.sleep(0.5)
            with self.assertRaises(OSError):
                os.kill(pid, 0)

    def test_a_child_left_behind_does_not_hold_the_run(self):
        self.set("scenario", "orphan")
        t = time.monotonic()
        self.assertEqual(self.run_codex().output, ANSWER)
        self.assertLess(time.monotonic() - t, 30)
        with open(os.path.join(self.fake, "grandchild.pid"), encoding="utf-8") as f:
            pid = int(f.read())
        if not WINDOWS:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@unittest.skipIf(WINDOWS, "the shim execs a program: a .py needs a POSIX launcher script here")
class Shim(Base):
    """mock_openai.py's wrapper, as codex_mock.py uses it in CI, around fake_codex.py."""

    def command(self):
        wrapper = os.path.join(self.tmp, "codex")
        with open(wrapper, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\nexec %s -I %s "$@"\n' % (sys.executable, FAKE))
        os.chmod(wrapper, 0o755)
        return [sys.executable, "-I", os.path.join(HERE, "mock_openai.py"), "shim", "http://127.0.0.1:9", wrapper]

    def test_the_probes_pass_through_the_call_gets_the_mock_s_urls_where_the_launcher_s_were(self):
        self.assertEqual(self.run_codex().output, ANSWER)
        argv = self.recorded("argv.json")
        values = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-c"]
        self.assertIn('model_provider="openai"', values)
        self.assertEqual([v for v in values if "base_url" in v],
                         ['openai_base_url="http://127.0.0.1:9/v1"',
                          'chatgpt_base_url="http://127.0.0.1:9/backend-api/"'])
        launcher = codex.config_flags(codex.load_features(), instructions="x")[1::2]
        self.assertLess(launcher.index('openai_base_url=""'), launcher.index("notify=[]"))
        self.assertLess(values.index('openai_base_url="http://127.0.0.1:9/v1"'), values.index("notify=[]"))

    def test_a_call_without_the_pinned_settings_is_refused(self):
        for drop in mock_openai.PINNED:
            args = ["exec", "--json"] + [x for p in mock_openai.PINNED if p != drop for x in ("-c", p)] + ["-"]
            with self.subTest(drop=drop):
                p = subprocess.run(self.command() + args, capture_output=True, timeout=60)
                self.assertEqual(p.returncode, 3, p.stderr)
                self.assertIn(drop, p.stderr.decode("utf-8"))
        self.assertEqual(self.exec_calls(), [])


class Binary(unittest.TestCase):
    def test_on_windows_only_an_exe(self):
        with mock.patch.object(codex, "WINDOWS", True):
            for cmd in (["C:\\npm\\codex.cmd"], ["C:\\x\\codex.bat"], ["C:\\x\\codex"]):
                with self.subTest(cmd=cmd), self.assertRaises(HeadlessError) as cm:
                    codex._codex(cmd)
                self.assertEqual(cm.exception.kind, "start")
            self.assertEqual(codex._codex(["C:\\x\\codex.exe"]), ["C:\\x\\codex.exe"])

    @unittest.skipIf(WINDOWS, "the Unix installer's place")
    def test_the_official_installer_s_place_comes_first(self):
        tmp = tempfile.mkdtemp(prefix="codex-find-")
        self.addCleanup(shutil.rmtree, tmp, True)
        local = os.path.join(tmp, ".local", "bin", "codex")
        other = os.path.join(tmp, "elsewhere", "codex")
        for p in (local, other):
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(p, 0o755)
        with mock.patch.dict(os.environ, {"HOME": tmp, "PATH": os.path.dirname(other)}):
            self.assertEqual(codex.find_codex(), [local])
            os.remove(local)
            self.assertEqual(codex.find_codex(), [other])
        with mock.patch.dict(os.environ, {"HOME": tmp, "PATH": ""}), \
                mock.patch.object(codex, "EXTRA_DIRS", ()):
            self.assertIsNone(codex.find_codex())
            with self.assertRaises(HeadlessError) as cm:
                codex._codex(None)
            self.assertEqual(cm.exception.kind, "start")

    def test_neither_the_current_folder_nor_a_relative_one_is_searched(self):
        # shutil.which puts the current folder first on Windows, even with path=: a codex.exe of the
        # folder a session runs `controle` from would have run. A relative entry is that folder's too.
        tmp = tempfile.mkdtemp(prefix="codex-find-")
        self.addCleanup(shutil.rmtree, tmp, True)
        name = "codex.exe" if WINDOWS else "codex"
        here = os.path.join(tmp, "here")
        os.makedirs(os.path.join(here, "bin"))
        for p in (os.path.join(here, name), os.path.join(here, "bin", name)):
            with open(p, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(p, 0o755)
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(here)
        env = {"HOME": tmp, "USERPROFILE": tmp, "LOCALAPPDATA": os.path.join(tmp, "local")}
        with mock.patch.object(codex, "EXTRA_DIRS", ()):
            for path in (empty, os.pathsep.join([".", "bin", empty])):
                with self.subTest(path=path), mock.patch.dict(os.environ, dict(env, PATH=path)):
                    self.assertIsNone(codex.find_codex())
            with mock.patch.dict(os.environ, dict(env, PATH=os.pathsep.join(["bin", os.path.join(here, "bin")]))):
                self.assertEqual(codex.find_codex(), [os.path.join(here, "bin", name)])


# --------------------------------------------------------------------------- Windows: %ProgramData%\OpenAI\Codex

SYSTEM, ADMINS, USERS = "S-1-5-18", "S-1-5-32-544", "S-1-5-32-545"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
CREATOR_OWNER, EVERYONE, AUTHENTICATED, INTERACTIVE = "S-1-3-0", "S-1-1-0", "S-1-5-11", "S-1-5-4"
A_USER = "S-1-5-21-1004336348-1177238915-682003330-1001"
FULL, MODIFY, READ_EXECUTE = 0x1F01FF, 0x1301BF, 0x1200A9
OI, CI, IO, INHERITED = 0x01, 0x02, 0x08, 0x10
# What an administrator who locks the folder leaves: SYSTEM and the Administrators in full, the users reading.
LOCKED = [(0, OI | CI, FULL, SYSTEM), (0, OI | CI, FULL, ADMINS), (0, OI | CI, READ_EXECUTE, USERS),
          (0, OI | CI | IO, FULL, CREATOR_OWNER)]
# What a folder made in C:\ProgramData inherits: its users may add files and folders.
PROGRAMDATA_LIKE = [(0, INHERITED, FULL, SYSTEM), (0, OI | CI | INHERITED, FULL, ADMINS),
                    (0, OI | CI | INHERITED, READ_EXECUTE, USERS), (0, CI | INHERITED, 0x6, USERS),
                    (0, OI | CI | IO | INHERITED, FULL, CREATOR_OWNER)]


class SystemSettingsAcl(unittest.TestCase):
    """unsafe(): who could change a folder of Codex's system settings, from its owner and access list."""

    def test_a_folder_an_administrator_locked(self):
        for owner in (ADMINS, SYSTEM, TRUSTED_INSTALLER):
            with self.subTest(owner=owner):
                self.assertIsNone(codex.unsafe(owner, LOCKED, child_files=True))
        self.assertIsNone(codex.unsafe(ADMINS, [(0, 0, 0x80000000 | 0x20000000, USERS)]), "generic read and execute")

    def test_an_owner_who_is_not_system_nor_the_administrators(self):
        for owner in (A_USER, USERS, EVERYONE, CREATOR_OWNER):
            with self.subTest(owner=owner):
                self.assertIn("owner", codex.unsafe(owner, LOCKED))

    def test_any_other_account_that_may_change_it(self):
        for sid in (USERS, EVERYONE, AUTHENTICATED, INTERACTIVE, A_USER):
            for mask in (0x2, 0x4, 0x10, 0x100, 0x40, 0x10000, 0x40000, 0x80000, MODIFY, FULL, 0x40000000, 0x10000000):
                with self.subTest(sid=sid, mask=hex(mask)):
                    self.assertIn(sid, codex.unsafe(ADMINS, LOCKED + [(0, 0, mask, sid)]) or "")

    def test_what_a_folder_made_in_programdata_inherits(self):
        self.assertIn(USERS, codex.unsafe(ADMINS, PROGRAMDATA_LIKE) or "")

    def test_the_files_it_would_get(self):
        child = LOCKED + [(0, OI | IO, 0x40000000, USERS)]
        self.assertIsNone(codex.unsafe(ADMINS, child), "the folder itself")
        self.assertIn("what it holds", codex.unsafe(ADMINS, child, child_files=True) or "")
        self.assertIsNone(codex.unsafe(ADMINS, LOCKED + [(0, CI | IO, 0x40000000, USERS)], child_files=True),
                          "subfolders only: not the files Codex reads")

    def test_no_access_list_an_entry_of_another_kind_and_a_deny_entry(self):
        self.assertIn("no access list", codex.unsafe(ADMINS, None))
        for kind in (5, 9, 11, 17):
            with self.subTest(kind=kind):
                self.assertIn("kind", codex.unsafe(ADMINS, LOCKED + [(kind, 0, 0x1, None)]) or "")
        self.assertIsNone(codex.unsafe(ADMINS, LOCKED + [(1, 0, FULL, USERS)]))

    def test_a_sid_read_from_its_bytes(self):
        raw = bytes([1, 2, 0, 0, 0, 0, 0, 5]) + (32).to_bytes(4, "little") + (544).to_bytes(4, "little")
        self.assertEqual(codex._sid(raw), ADMINS)
        for bad in (raw[:7], raw[:12], bytes([2]) + raw[1:]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                codex._sid(bad)


class SystemSettings(unittest.TestCase):
    """check_system_settings(): the folders and files, as Windows would describe them (security() stood in)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="programdata-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.openai = os.path.join(self.root, "OpenAI")
        self.dir = os.path.join(self.openai, "Codex")
        self.acl, self.asked = {}, []
        for p in (mock.patch.object(codex, "WINDOWS", True), mock.patch.object(codex, "PROGRAM_DATA", self.root),
                  mock.patch.object(codex, "security", self.security)):
            p.start()
            self.addCleanup(p.stop)

    def security(self, path):
        self.asked.append(os.path.relpath(path, self.root))
        v = self.acl.get(os.path.relpath(path, self.root), (ADMINS, LOCKED))
        if isinstance(v, Exception):
            raise v
        return v

    def put(self, name, text="x = 1\n"):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def paused(self):
        with self.assertRaises(HeadlessError) as cm:
            codex.check_system_settings()
        self.assertEqual((cm.exception.kind, cm.exception.reason), ("unsupported", "managed"), cm.exception)
        return str(cm.exception)

    def test_nothing_there_nothing_read(self):
        codex.check_system_settings()
        self.assertEqual(self.asked, [])

    def test_locked_folders_and_files_pass(self):
        os.makedirs(self.dir)
        for name in ("config.toml", "requirements.toml", "managed_config.toml"):
            self.put(name)
        codex.check_system_settings()
        self.assertEqual(self.asked, ["OpenAI", os.path.join("OpenAI", "Codex")] +
                         [os.path.join("OpenAI", "Codex", n) for n in ("config.toml", "requirements.toml", "managed_config.toml")])

    def test_each_one_another_account_could_change_pauses_codex(self):
        os.makedirs(self.dir)
        self.put("config.toml")
        self.put("requirements.toml")
        for where, acl in (("OpenAI", (A_USER, LOCKED)), ("OpenAI", (ADMINS, PROGRAMDATA_LIKE)),
                           (os.path.join("OpenAI", "Codex"), (ADMINS, LOCKED + [(0, OI | IO, 0x2, USERS)])),
                           (os.path.join("OpenAI", "Codex", "config.toml"), (ADMINS, LOCKED + [(0, 0, MODIFY, USERS)])),
                           (os.path.join("OpenAI", "Codex", "requirements.toml"), (A_USER, LOCKED)),
                           (os.path.join("OpenAI", "Codex"), OSError(5, "denied"))):
            self.acl = {where: acl}
            with self.subTest(where=where, acl=acl):
                self.assertIn(os.path.join(self.root, where), self.paused())

    def test_only_openai_there(self):
        os.makedirs(self.openai)
        codex.check_system_settings()
        self.assertEqual(self.asked, ["OpenAI"])
        self.acl = {"OpenAI": (A_USER, LOCKED)}
        self.paused()

    def test_a_file_where_a_folder_goes_a_folder_where_a_file_goes(self):
        with open(self.openai, "w") as f:
            f.write("x")
        self.assertIn("is not a folder", self.paused())
        os.remove(self.openai)
        os.makedirs(os.path.join(self.dir, "config.toml"))
        self.assertIn("is not a file", self.paused())

    @unittest.skipIf(WINDOWS, "a link on Windows needs a privilege: WindowsAcl makes a junction")
    def test_a_link(self):
        os.makedirs(os.path.join(self.root, "elsewhere"))
        os.symlink(os.path.join(self.root, "elsewhere"), self.openai)
        self.assertIn("is a link", self.paused())
        self.assertEqual(self.asked, [])

    def test_programdata_windows_does_not_say(self):
        with mock.patch.object(codex, "PROGRAM_DATA", None), mock.patch.object(codex, "known_program_data", lambda: None):
            self.assertIn("%ProgramData%", self.paused())


class SystemSettingsInTheCall(Base):
    def test_checked_before_any_probe_and_again_before_the_question(self):
        seen = []
        with mock.patch.object(codex, "check_system_settings", lambda: seen.append(len(self.calls()))):
            self.assertEqual(self.run_codex().output, ANSWER)
        probes = [c for c in self.calls() if c not in self.exec_calls()]
        self.assertEqual(seen, [0, len(probes)])

    def test_a_pause_asks_nothing_and_runs_no_probe(self):
        def pause():
            raise HeadlessError("unsupported", "a folder another account could change", "managed")
        with mock.patch.object(codex, "check_system_settings", pause):
            self.refused("unsupported", "managed")
            with self.assertRaises(HeadlessError):
                codex.check(self.codex_home, command=self.command(), work_root=self.tmp)
        self.assertEqual(self.calls(), [])


@unittest.skipUnless(WINDOWS, "Windows' own permissions")
class WindowsAcl(unittest.TestCase):
    """The real thing: folders whose owner and access list icacls sets, read by security()."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="programdata-"))
        self.addCleanup(self.cleanup)
        self.openai = os.path.join(self.root, "OpenAI")
        self.dir = os.path.join(self.openai, "Codex")
        os.makedirs(self.dir)
        p = mock.patch.object(codex, "PROGRAM_DATA", self.root)
        p.start()
        self.addCleanup(p.stop)

    def cleanup(self):
        subprocess.run(["icacls", self.root, "/reset", "/T", "/C", "/Q"], capture_output=True)
        subprocess.run(["takeown", "/F", self.root, "/R", "/D", "Y"], capture_output=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def icacls(self, path, *args):
        p = subprocess.run(["icacls", path] + list(args), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, "icacls %s %s: %s %s" % (path, args, p.stdout, p.stderr))

    def lock(self, path):
        """As an administrator locks it: SYSTEM and the Administrators in full, the users reading, the
        Administrators its owner; nothing inherited, no entry left from before."""
        self.icacls(path, "/reset")
        self.icacls(path, "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
                    "*S-1-5-32-545:(OI)(CI)RX")
        self.icacls(path, "/setowner", "*S-1-5-32-544")

    def put(self, name):
        p = os.path.join(self.dir, name)
        with open(p, "w") as f:
            f.write("x = 1\n")
        self.icacls(p, "/setowner", "*S-1-5-32-544")
        return p

    def paused(self):
        with self.assertRaises(HeadlessError) as cm:
            codex.check_system_settings()
        self.assertEqual(cm.exception.reason, "managed", cm.exception)
        return str(cm.exception)

    def test_the_known_folder_not_the_variable(self):
        p = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('CommonApplicationData')"],
                           capture_output=True, text=True)
        real = p.stdout.strip()
        self.assertTrue(real and os.path.isdir(real), p)
        with mock.patch.dict(os.environ, {"ProgramData": self.root, "ALLUSERSPROFILE": self.root}):
            self.assertEqual(os.path.normcase(codex.known_program_data()), os.path.normcase(real))
            with mock.patch.object(codex, "PROGRAM_DATA", None), mock.patch.object(codex, "MANAGED", None):
                self.assertEqual(codex._managed_files()[0], Path(real) / "OpenAI" / "Codex" / "requirements.toml")

    def test_locked_by_an_administrator(self):
        self.lock(self.openai)
        self.lock(self.dir)
        self.put("config.toml")
        owner, aces = codex.security(self.dir)
        self.assertEqual(owner, ADMINS)
        self.assertEqual({a[3] for a in aces}, {SYSTEM, ADMINS, USERS}, aces)
        codex.check_system_settings()

    def test_a_folder_its_users_may_add_to_as_one_made_in_programdata(self):
        # What a folder made in C:\\ProgramData inherits: Users may add files and folders to it.
        self.lock(self.openai)
        self.lock(self.dir)
        codex.check_system_settings()
        self.icacls(self.openai, "/grant", "*S-1-5-32-545:(CI)(WD,AD,WEA,WA)")
        self.assertIn("S-1-5-32-545 may change it", self.paused())

    def test_users_who_may_add_files_or_an_owner_of_theirs(self):
        self.lock(self.openai)
        self.lock(self.dir)
        self.icacls(self.dir, "/grant", "*S-1-5-32-545:(OI)(IO)(W)")
        self.assertIn("what it holds", self.paused())
        self.lock(self.dir)
        self.icacls(self.openai, "/grant", "*S-1-5-11:(M)")
        self.assertIn("S-1-5-11", self.paused())
        self.lock(self.openai)
        self.icacls(self.openai, "/setowner", os.environ["USERNAME"])
        self.assertIn("its owner is", self.paused())
        self.lock(self.openai)
        codex.check_system_settings()
        self.icacls(self.put("requirements.toml"), "/grant", "*S-1-1-0:(M)")
        self.assertIn("requirements.toml", self.paused())

    def test_a_junction(self):
        shutil.rmtree(self.openai)
        target = os.path.join(self.root, "elsewhere")
        os.makedirs(os.path.join(target, "Codex"))
        p = subprocess.run(["cmd", "/c", "mklink", "/J", self.openai, target], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p)
        self.assertIn("is a link", self.paused())


if __name__ == "__main__":
    unittest.main()
