"""Tests of engine/hooks/_headless.py: which engine a task without tools asks, and when the other
one takes over (docs/proposals/codex.md, §1.4 and step 3).

First the engines' launchers are replaced by recorders that answer or fail as each test says:
every order, try and fallback. Then the whole path with fake_claude.py and fake_codex.py: without
agents.json, Claude gets exactly the call it got before the choice existed, and Codex's code is
not even loaded. Codex is closed on every system (_agents.CODEX_OUVERT): the tests open it here
only, in this process. Every run happens in a throwaway HOME.

    python3 -I -m unittest discover -s engine/tests -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
FIXTURES = HERE.parent.parent / "app" / "src-tauri" / "tests" / "fixtures" / "repair"
FAKE_CLAUDE = HERE / "fake_claude.py"
FAKE_CODEX = HERE / "fake_codex.py"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(HERE))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import _agents  # noqa: E402
import _headless  # noqa: E402
import _headless_claude  # noqa: E402
import _headless_codex  # noqa: E402
import mock_openai  # noqa: E402
from _headless_claude import HeadlessError  # noqa: E402

POSIX = os.name != "nt"
OPEN = frozenset({_agents.systeme()})
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict"],
          "properties": {"verdict": {"type": "string", "enum": ["ok", "ko"]}}}
ANSWER = {"verdict": "ok"}
CLAUDE = {"budget_usd": 0.4, "timeout": 30, "max_turns": 3, "model": "sonnet", "effort": "medium"}
CODEX = {"timeout": 20, "effort": "medium"}
KINDS = ("start", "unsupported", "auth", "init", "timeout", "quota", "api", "invalid")
# §1.4: the other engine only when the first cannot answer now; after a timeout, towards Claude only.
TO_CODEX = {"start", "unsupported", "auth", "quota", "api"}
TO_CLAUDE = {"start", "unsupported", "auth", "quota", "api", "timeout"}


def write_agents(home, agents, prefere=None, codex_home=None, raw=None):
    d = os.path.join(home, ".brain-in-a-box")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "agents.json")
    if raw is None:
        v = {"version": 1, "agents": list(agents), "prefere": prefere or agents[0]}
        if "codex" in agents:
            v["codex_home"] = codex_home
        raw = json.dumps(v)
    with open(p, "w", encoding="utf-8") as f:
        f.write(raw)
    if POSIX:
        os.chmod(p, 0o600)
    return p


class Recorded(unittest.TestCase):
    """The launchers replaced by recorders: plan[moteur] is what each call does, in turn (an
    exception to raise, else the answer)."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="headless-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        # Not ~/.codex: Codex's folder is the one agents.json holds, never the default.
        self.codex_home = os.path.join(self.home, "elsewhere", "codex-home")
        os.makedirs(self.codex_home)
        self.calls, self.plan = [], {"claude": [], "codex": []}
        patches = [mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home}),
                   mock.patch.object(_agents, "CODEX_OUVERT", OPEN),
                   mock.patch.object(_headless_claude, "run", self.fake("claude", "run")),
                   mock.patch.object(_headless_claude, "check", self.fake("claude", "check")),
                   mock.patch.object(_headless_codex, "run", self.fake("codex", "run")),
                   mock.patch.object(_headless_codex, "check", self.fake("codex", "check")),
                   mock.patch.object(_headless.time, "sleep", lambda s: self.calls.append(("sleep", s, {})))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(_headless.MODEL_VAR, None)

    def fake(self, moteur, what):
        def call(*args, **kw):
            self.calls.append((moteur, what, dict(kw, args=args)))
            step = self.plan[moteur].pop(0) if self.plan[moteur] else None
            if isinstance(step, Exception):
                raise step
            if what == "check":
                return "1.0." + moteur
            if moteur == "claude":
                return _headless_claude.Answer(dict(ANSWER, by="claude"), "2.1.296", 0.12, 2)
            return _headless_codex.Answer(dict(ANSWER, by="codex"), "0.162.1", None, 1, {"input_tokens": 9})
        return call

    def asked(self, what="run"):
        return [m for m, w, _ in self.calls if w == what]

    def both(self, prefere):
        write_agents(self.home, ["claude", "codex"], prefere, self.codex_home)

    def run_(self, **kw):
        return _headless.run("prompt é", SCHEMA, "system", claude=dict(CLAUDE), codex=dict(CODEX), **kw)

    def fails(self, **kw):
        with self.assertRaises(HeadlessError) as cm:
            self.run_(**kw)
        return cm.exception


class Order(Recorded):
    def test_without_agents_json_claude_alone_with_the_callers_settings(self):
        a = self.run_()
        self.assertEqual(self.asked(), ["claude"])
        _, _, kw = self.calls[0]
        self.assertEqual(kw.pop("args"), ("prompt é", SCHEMA, "system"))
        self.assertEqual(kw, CLAUDE, "the caller's settings, and nothing more, reach the Claude launcher")
        self.assertEqual((a.output, a.moteur, a.version, a.cost_usd, a.num_turns, a.tokens, a.secours),
                         (dict(ANSWER, by="claude"), None, "2.1.296", 0.12, 2, None, None))

    def test_the_preferred_engine_first_then_only_on_failure_the_other(self):
        self.both("codex")
        a = self.run_()
        self.assertEqual((self.asked(), a.moteur, a.output["by"]), (["codex"], "codex", "codex"))
        self.calls.clear()
        self.both("claude")
        a = self.run_()
        self.assertEqual((self.asked(), a.moteur), (["claude"], "claude"))

    def test_codex_gets_its_folder_from_agents_json_and_its_model_from_brain_codex_model(self):
        write_agents(self.home, ["codex"], "codex", self.codex_home)
        self.run_()
        _, _, kw = self.calls[0]
        self.assertEqual((kw["codex_home"], kw["model"], kw["timeout"], kw["effort"]),
                         (self.codex_home, None, 20, "medium"))
        with mock.patch.dict(os.environ, {_headless.MODEL_VAR: "gpt-5.2-codex"}):
            self.run_()
        self.assertEqual(self.calls[-1][2]["model"], "gpt-5.2-codex")

    def test_a_model_name_that_is_not_one_is_a_codex_failure_claude_answers(self):
        self.both("codex")
        with mock.patch.dict(os.environ, {_headless.MODEL_VAR: "gpt-5 --dangerously"}):
            a = self.run_()
        self.assertEqual((self.asked(), a.moteur), (["claude"], "claude"), "Codex is not started with it")
        self.assertIn("unsupported", a.secours)

    def test_only_an_agent_listed_ever_answers(self):
        write_agents(self.home, ["claude"])
        self.plan["claude"] = [HeadlessError("auth", "not logged in", "logged_out")]
        e = self.fails()
        self.assertEqual((e.kind, self.asked()), ("auth", ["claude"]), "Codex is not listed: never asked")
        self.calls.clear()
        write_agents(self.home, ["codex"], "codex", self.codex_home)
        self.plan["codex"] = [HeadlessError("quota", "You've hit your usage limit")]
        e = self.fails()
        self.assertEqual((e.kind, self.asked()), ("quota", ["codex"]), "Claude is not listed: never asked")


class Fallback(Recorded):
    def test_which_failures_hand_over_to_the_other_engine(self):
        for prefere, other, allowed in (("claude", "codex", TO_CODEX), ("codex", "claude", TO_CLAUDE)):
            for kind in KINDS:
                with self.subTest(prefere=prefere, kind=kind):
                    self.both(prefere)
                    self.calls.clear()
                    self.plan = {"claude": [], "codex": []}
                    self.plan[prefere] = [HeadlessError(kind, "said by " + prefere)]
                    if kind in allowed:
                        a = self.run_()
                        self.assertEqual((self.asked(), a.moteur), ([prefere, other], other))
                        self.assertEqual(a.secours, "%s: %s: said by %s" % (prefere, kind, prefere))
                    else:
                        e = self.fails()
                        self.assertEqual((self.asked(), e.kind, e.moteur), ([prefere], kind, prefere))

    def test_claude_out_of_time_never_hands_the_vault_to_codex(self):
        self.both("claude")
        self.plan["claude"] = [HeadlessError("timeout", "no answer in time")] * 2
        e = self.fails(essais=2, pause=7)
        self.assertEqual((e.kind, self.asked()), ("timeout", ["claude", "claude"]))

    def test_tries_per_engine(self):
        cases = [  # (preferred, its failures, essais) -> (calls, answered by)
            ("claude", ["api", "api"], 2, ["claude", "claude", "codex"], "codex"),
            ("claude", ["api"], 2, ["claude", "claude"], "claude"),
            ("claude", ["timeout"], 2, ["claude", "claude"], "claude"),
            ("claude", ["quota"], 2, ["claude", "codex"], "codex"),
            ("claude", ["api"], 1, ["claude", "codex"], "codex"),
            # Codex has no spending cap: a call out of time is not made again, Claude takes over.
            ("codex", ["timeout"], 2, ["codex", "claude"], "claude"),
            ("codex", ["api"], 2, ["codex", "codex"], "codex"),
            ("codex", ["api", "api"], 2, ["codex", "codex", "claude"], "claude"),
            ("codex", ["auth"], 2, ["codex", "claude"], "claude"),
        ]
        for prefere, kinds, essais, calls, by in cases:
            with self.subTest(prefere=prefere, kinds=kinds, essais=essais):
                self.both(prefere)
                self.calls.clear()
                self.plan = {"claude": [], "codex": []}
                self.plan[prefere] = [HeadlessError(k, k) for k in kinds]
                a = self.run_(essais=essais, pause=3)
                self.assertEqual((self.asked(), a.moteur), (calls, by))
                sleeps = [c for c in self.calls if c[0] == "sleep"]
                self.assertEqual(len(sleeps), calls.count(prefere) - 1)
                self.assertTrue(all(s[1] == 3 for s in sleeps))

    def test_both_failing_say_both_the_preferred_first(self):
        self.both("claude")
        self.plan = {"claude": [HeadlessError("quota", "You've hit your limit")],
                     "codex": [HeadlessError("auth", "Codex is not logged in", "logged_out")]}
        e = self.fails()
        self.assertEqual((e.kind, e.moteur), ("quota", "claude"))
        self.assertEqual(_headless.texte(e),
                         "claude: quota: You've hit your limit; then codex: auth: Codex is not logged in")

    def test_every_failed_try_is_heard(self):
        heard = []
        self.both("codex")
        self.plan["codex"] = [HeadlessError("api", "503"), HeadlessError("api", "502")]
        self.run_(essais=2, journal=lambda *a: heard.append((a[0], a[1], a[2], str(a[3]))))
        self.assertEqual(heard, [("codex", 1, 2, "api: 503"), ("codex", 2, 2, "api: 502")])


class Names(Recorded):
    def test_claude_alone_is_named_nowhere_as_before(self):
        for listed in (None, ["claude"]):
            with self.subTest(listed=listed):
                if listed:
                    write_agents(self.home, listed)
                self.plan["claude"] = [HeadlessError("quota", "You've hit your limit")]
                heard = []
                e = self.fails(journal=lambda *a: heard.append(a[0]))
                self.assertIsNone(e.moteur)
                self.assertEqual(_headless.texte(e), "quota: You've hit your limit")
                self.assertEqual(heard, [None])
                self.assertIsNone(self.run_().moteur)

    def test_with_codex_listed_every_answer_and_error_names_its_engine(self):
        self.both("claude")
        self.assertEqual(self.run_().moteur, "claude")
        self.plan["claude"] = [HeadlessError("init", "tool used: Bash")]
        e = self.fails()
        self.assertEqual(_headless.texte(e), "claude: init: tool used: Bash")


class Untrusted(Recorded):
    def cases(self):
        yield "illisible", dict(raw="{not json")
        yield "invalide", dict(raw=json.dumps({"version": 1, "agents": ["claude"], "prefere": "codex"}))
        yield "ferme", dict(agents=["claude", "codex"], codex_home=self.codex_home, closed=True)
        if POSIX:
            yield "droits", dict(agents=["claude"], mode=0o666)

    def test_a_file_the_kit_cannot_trust_asks_no_engine(self):
        for etat, how in self.cases():
            with self.subTest(etat):
                self.calls.clear()
                p = write_agents(self.home, how.get("agents", ["claude"]), codex_home=how.get("codex_home"),
                                 raw=how.get("raw"))
                if "mode" in how:
                    os.chmod(p, how["mode"])
                with mock.patch.object(_agents, "CODEX_OUVERT", frozenset() if how.get("closed") else OPEN):
                    self.assertEqual(_headless.moteurs(), ((), etat))
                    for call in (self.run_, lambda: _headless.check(claude={}, codex={})):
                        with self.assertRaises(HeadlessError) as cm:
                            call()
                        self.assertEqual((cm.exception.kind, cm.exception.reason), ("agents", etat))
                        self.assertIn("install", cm.exception.detail)
                self.assertEqual(self.calls, [], "neither engine is asked nor checked")

    def test_the_reading_given_is_the_one_used(self):
        """A task reads agents.json once: a file changed meanwhile does not change who it asks."""
        write_agents(self.home, ["claude"])
        lu = _headless.lire()
        write_agents(self.home, ["codex"], "codex", self.codex_home)
        self.assertEqual(self.run_(agents=lu).output["by"], "claude")


class Check(Recorded):
    def test_the_first_engine_that_may_be_asked(self):
        self.assertEqual(_headless.check(claude={"command": ["x"]}, codex={}), (None, "1.0.claude"))
        self.assertEqual(self.calls[0][2], {"command": ["x"], "args": ()})
        self.both("codex")
        self.calls.clear()
        self.plan["codex"] = [HeadlessError("unsupported", "a feature the kit does not know", "features")]
        self.assertEqual(_headless.check(claude={}, codex={"command": ["y"]}), ("claude", "1.0.claude"))
        self.assertEqual(self.calls[0][2], {"command": ["y"], "args": (self.codex_home,)})
        self.plan = {"claude": [HeadlessError("auth", "logged out", "logged_out")],
                     "codex": [HeadlessError("auth", "billed", "billed")]}
        with self.assertRaises(HeadlessError) as cm:
            _headless.check(claude={}, codex={})
        self.assertEqual((cm.exception.reason, cm.exception.moteur), ("billed", "codex"))


# --------------------------------------------------------------------------- the whole path

class Whole(unittest.TestCase):
    """fake_claude.py and fake_codex.py stand for the CLIs: nothing is replaced in the launchers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="headless-whole-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        # Not ~/.codex: Codex's folder is the one agents.json holds, never the default.
        self.codex_home = os.path.join(self.home, "elsewhere", "codex-home")
        self.claude_fake = os.path.join(self.home, ".claude", "fake")
        self.codex_fake = os.path.join(self.home, ".codex-fake")
        for d in (self.codex_home, self.claude_fake, self.codex_fake):
            os.makedirs(d)
        self.put(self.claude_fake, "fixtures", str(FIXTURES))
        self.put(self.claude_fake, "answer.json", json.dumps(ANSWER))
        self.put(self.codex_fake, "answer.json", json.dumps(ANSWER))
        self.put(self.codex_fake, "home", self.codex_home)
        self.put(self.codex_home, "auth.json", json.dumps(mock_openai.fake_auth()))
        patches = [mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home}),
                   mock.patch.object(_agents, "CODEX_OUVERT", OPEN),
                   mock.patch.object(_headless_claude, "MANAGED_DIR", os.path.join(self.tmp, "managed")),
                   mock.patch.object(_headless_codex, "MANAGED", [os.path.join(self.tmp, "etc", "requirements.toml")]),
                   mock.patch.object(_headless_codex, "MDM", [])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def put(self, folder, name, text):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(text)

    def get(self, folder, name):
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            return f.read()

    def settings(self):
        claude = dict(CLAUDE, command=[sys.executable, str(FAKE_CLAUDE)], work_root=self.tmp)
        codex = dict(CODEX, command=[sys.executable, "-I", str(FAKE_CODEX)], work_root=self.tmp)
        return claude, codex

    def claude_calls(self):
        p = os.path.join(self.claude_fake, "calls")
        return open(p).read().count("-p") if os.path.exists(p) else 0

    def codex_calls(self):
        p = os.path.join(self.codex_fake, "calls.jsonl")
        if not os.path.exists(p):
            return 0
        with open(p, encoding="utf-8") as f:
            return sum(1 for l in f if json.loads(l)["argv"][:1] == ["exec"] and "--help" not in l)

    def recorded_call(self):
        """What fake_claude was given, the cage's own path (fresh every run) put aside."""
        argv = json.loads(self.get(self.claude_fake, "argv.json"))
        env = json.loads(self.get(self.claude_fake, "env.json"))
        where = json.loads(self.get(self.claude_fake, "cwd.json"))
        env = {k: re.sub(r"brain-claude-[^/\\]+", "brain-claude-<cage>", v) for k, v in env.items()}
        return argv, env, where["mode"], self.get(self.claude_fake, "prompt.txt")

    def test_without_agents_json_claude_gets_exactly_the_call_it_got_before(self):
        claude, codex = self.settings()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-canary", "CODEX_HOME": self.codex_home}):
            direct = _headless_claude.run("prompt é", SCHEMA, "system", **claude)
            before = self.recorded_call()
            through = _headless.run("prompt é", SCHEMA, "system", claude=claude, codex=codex)
            after = self.recorded_call()
        self.assertEqual(after, before, "argv, environment, folder and prompt are the launcher's own")
        self.assertEqual((through.output, through.version, through.cost_usd, through.num_turns),
                         (direct.output, direct.version, direct.cost_usd, direct.num_turns))
        self.assertEqual((self.claude_calls(), self.codex_calls()), (2, 0))

    def test_claude_alone_never_loads_codex_code(self):
        """In a fresh Python: without agents.json, or with Claude alone, _headless_codex.py is not
        even imported (a fault in it cannot reach Claude's users)."""
        claude, _ = self.settings()
        script = ("import json, sys; sys.path.insert(0, %r); import _headless, _headless_claude; "
                  "_headless_claude.MANAGED_DIR = %r; "
                  "a = _headless.run('p', %r, 's', claude=json.loads(sys.argv[1]), codex={}); "
                  "print(a.output, '_headless_codex' in sys.modules)"
                  % (str(HOOKS), os.path.join(self.tmp, "managed"), SCHEMA))
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home)
        for listed in (None, ["claude"]):
            with self.subTest(listed=listed):
                if listed:
                    write_agents(self.home, listed)
                p = subprocess.run([sys.executable, "-I", "-c", script, json.dumps(claude)], env=env,
                                   capture_output=True, timeout=120)
                self.assertEqual(p.stdout.decode().strip(), "{'verdict': 'ok'} False", p.stderr.decode()[-800:])

    def test_codex_preferred_answers_and_claude_is_not_asked(self):
        write_agents(self.home, ["claude", "codex"], "codex", self.codex_home)
        claude, codex = self.settings()
        a = _headless.run("prompt é 🙂", SCHEMA, "system", claude=claude, codex=codex)
        self.assertEqual((a.output, a.moteur, a.version, a.secours), (ANSWER, "codex", "0.162.1", None))
        self.assertEqual((self.claude_calls(), self.codex_calls()), (0, 1))
        self.assertEqual(self.get(self.codex_fake, "stdin.txt"), "prompt é 🙂")
        self.assertEqual(json.loads(self.get(self.codex_fake, "schema.json")), SCHEMA)
        env = json.loads(self.get(self.codex_fake, "env.json"))
        self.assertEqual(env["CODEX_HOME"], self.codex_home)

    def test_claude_logged_out_codex_takes_over_and_it_is_said(self):
        write_agents(self.home, ["claude", "codex"], "claude", self.codex_home)
        self.put(self.claude_fake, "auth", "apiKey")
        claude, codex = self.settings()
        a = _headless.run("prompt", SCHEMA, "system", claude=claude, codex=codex)
        self.assertEqual((a.moteur, self.claude_calls(), self.codex_calls()), ("codex", 0, 1))
        self.assertTrue(a.secours.startswith("claude: auth: Claude Code login is apiKey"), a.secours)

    def test_codex_running_a_tool_is_not_covered_by_claude(self):
        write_agents(self.home, ["claude", "codex"], "codex", self.codex_home)
        self.put(self.codex_fake, "scenario", "command")
        claude, codex = self.settings()
        with self.assertRaises(HeadlessError) as cm:
            _headless.run("prompt", SCHEMA, "system", claude=claude, codex=codex)
        self.assertEqual(_headless.texte(cm.exception), "codex: init: tool used: command_execution")
        self.assertEqual(self.claude_calls(), 0)


if __name__ == "__main__":
    unittest.main()
