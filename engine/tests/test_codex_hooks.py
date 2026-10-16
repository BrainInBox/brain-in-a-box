"""Codex's hooks (--agent codex): correction-detector, controle-sessionstart, gbrain-update-check,
session-logger, session-indexer and session-recap, each run as Codex runs it (a process of its own,
the payload on stdin), on what Codex 0.162.1 and 0.163.0-alpha.11 gave its hooks in step 0
(fixtures/codex/phase0/: macOS, Linux and Windows, Windows paths, PowerShell 7 and 5.1), in a copy
of the hooks where Codex is open (it is closed on every system: _agents.CODEX_OUVERT). What they
answer is checked against Codex's own schemas (codex_captures.output_errors). Without the argument
the hooks are Claude Code's, as before: tested here for what Codex could change in them.

    python3 -I -m unittest discover -s engine/tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
sys.path.insert(0, str(HERE))
import codex_captures as cap  # noqa: E402

GATE = "CODEX_OUVERT = frozenset()\n"
OPEN = 'CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})\n'
BY_EVENT = {"SessionStart": ("controle-sessionstart", "gbrain-update-check"),
            "UserPromptSubmit": ("correction-detector",),
            "Stop": ("session-logger", "session-indexer", "session-recap")}
ALL = sorted(h for hooks in BY_EVENT.values() for h in hooks)
CORRECTION = "no, actually do it the other way"
CODEX = ("--agent", "codex")
POSIX = os.name != "nt"


def first_of_each_capture():
    """(capture, event, payload): what Codex gave each hook event the first time, in every capture."""
    seen = set()
    for name, rec in cap.hook_payloads():
        if (name, rec["event"]) not in seen:
            seen.add((name, rec["event"]))
            yield name, rec["event"], rec["stdin"]


class Bench(unittest.TestCase):
    """A home, a working folder and a temp folder of their own; two copies of the hooks: `open`, where
    Codex is open on every system, and `closed`, as shipped."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="biab-codex-hooks-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        self.home, self.work, self.tmp = self.root / "home", self.root / "work", self.root / "tmp"
        for d in (self.home, self.work, self.tmp):
            d.mkdir()
        self.closed, self.open = self.root / "closed", self.root / "open"
        for d in (self.closed, self.open):
            d.mkdir()
            for f in HOOKS.glob("*.py"):
                shutil.copy(str(f), str(d))
        gate = (self.open / "_agents.py").read_text(encoding="utf-8")
        self.assertEqual(gate.count(GATE), 1, "the gate's line moved: this bench would not open Codex")
        (self.open / "_agents.py").write_text(gate.replace(GATE, OPEN), encoding="utf-8")
        self.brain = self.home / ".brain-in-a-box"
        self.choose(["claude", "codex"])
        self.etat()

    def choose(self, agents):
        self.brain.mkdir(exist_ok=True)
        p = self.brain / "agents.json"
        if agents is None:
            if p.exists():
                p.unlink()
            return
        p.write_text(json.dumps({"version": 1, "agents": agents, "prefere": agents[0],
                                 **({"codex_home": str(self.home / ".codex")} if "codex" in agents else {})}),
                     encoding="utf-8")
        os.chmod(str(p), 0o600)

    def etat(self):
        """A night with fixes, and a secret to change: a green line, and a red one (systemMessage)."""
        state = self.brain / "controle"
        state.mkdir(parents=True, exist_ok=True)
        (state / "etat.json").write_text(json.dumps(
            {"run": "20261014-0407", "mode": "nuit", "dernier_succes": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "corrections": 2, "a_voir": 0, "secrets_a_changer": 1, "rapport": "Profile/controle.md",
             "rapport_reecrit": True, "alertes": []}), encoding="utf-8")
        shown = state / "shown.json"
        if shown.exists():
            shown.unlink()

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items()
             if k not in ("CLAUDE_CODE_ENTRYPOINT", "BRAIN_HEADLESS", "CLAUDE_PROJECT_DIR", "GBRAIN_HOME",
                          "BRAIN_CONTROLE_DIR", "CODEX_HOME")}
        e.update(HOME=str(self.home), USERPROFILE=str(self.home), TMPDIR=str(self.tmp), TEMP=str(self.tmp),
                 TMP=str(self.tmp))
        e.update(extra)
        return e

    def run_hook(self, hook, payload, argv=CODEX, hooks=None, **env):
        """(code, stdout, stderr) of the hook run as Codex runs it: its own process, the payload on stdin."""
        p = subprocess.run([sys.executable, "-I", str((hooks or self.open) / (hook + ".py")), *argv],
                           input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), cwd=str(self.work),
                           env=self.env(**env), capture_output=True, timeout=60)
        return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8", "replace")

    def logs(self):
        d = self.home / ".claude" / "logs"
        out = {}
        for name in ("sessions.log", "sessions-%s.jsonl" % time.strftime("%Y-%m-%d")):
            p = d / name
            out[name] = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return out

    def vus(self):
        p = self.brain / "codex-hooks-vus.json"
        return sorted(json.loads(p.read_text(encoding="utf-8"))["hooks"]) if p.exists() else []

    def written(self):
        """What a hook may leave behind: logs, journal, locks, the sign that Codex runs the hooks."""
        found = []
        for base in (self.home / ".claude", self.home / "Documents", self.tmp, self.brain / "codex-hooks-vus.json"):
            if base.is_file():
                found.append(str(base.relative_to(self.root)))
            for d, _, files in os.walk(str(base)):
                found += [os.path.relpath(os.path.join(d, f), str(self.root)) for f in files]
        return sorted(found)

    def answered(self, event, out):
        """The hook's answer, parsed, after Codex's own schema took it."""
        answer = json.loads(out)
        self.assertEqual(cap.output_errors(event, answer), [], out)
        return answer


class Schema(unittest.TestCase):
    """The checker the other tests rely on refuses what Codex refuses."""

    def test_codexs_schemas_refuse_what_codex_refuses(self):
        ok = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "x"}}
        self.assertEqual(cap.output_errors("UserPromptSubmit", ok), [])
        self.assertEqual(cap.output_errors("SessionStart", {"systemMessage": "x", "hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": "y"}}), [])
        self.assertEqual(cap.output_errors("Stop", {}), [])
        bad = [("UserPromptSubmit", dict(ok, extra=1)),
               ("UserPromptSubmit", {"hookSpecificOutput": dict(ok["hookSpecificOutput"], more="x")}),
               ("SessionStart", ok),
               ("UserPromptSubmit", {"hookSpecificOutput": {"additionalContext": "x"}}),
               ("UserPromptSubmit", {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": 3}}),
               ("UserPromptSubmit", {"decision": "allow"}),
               ("Stop", {"hookSpecificOutput": {"hookEventName": "Stop"}}),
               ("Stop", {"continue": "yes"}),
               ("Stop", [])]
        for event, answer in bad:
            with self.subTest(event=event, answer=answer):
                self.assertNotEqual(cap.output_errors(event, answer), [])

    def test_a_keyword_it_does_not_know_is_an_error_not_a_pass(self):
        self.assertNotEqual(cap._errors({"type": "string", "pattern": "^a"}, "b", {}, "$"), [])
        self.assertNotEqual(cap._errors({"$ref": "#/definitions/Missing"}, "b", {}, "$"), [])


class Payloads(Bench):
    """Every payload of step 0, through the hooks of its event."""

    def test_every_payload_of_step_0_is_answered_as_codex_takes_it(self):
        n = {"SessionStart": 0, "UserPromptSubmit": 0, "Stop": 0}
        windows = 0
        for name, event, payload in first_of_each_capture():
            n[event] += 1
            windows += "\\" in payload["transcript_path"]
            for hook in BY_EVENT[event]:
                with self.subTest(capture=name, hook=hook):
                    self.etat()
                    code, out, err = self.run_hook(hook, payload)
                    self.assertEqual((code, err), (0, ""))
                    if hook == "controle-sessionstart":
                        answer = self.answered(event, out)
                        self.assertIn("Contrôle de cette nuit : 2 corrections faites",
                                      answer["hookSpecificOutput"]["additionalContext"])
                        self.assertIn("mot de passe ou une clé", answer["systemMessage"])
                    elif hook == "correction-detector":
                        self.assertEqual(out, "", "%r is not a correction" % payload["prompt"])
                        code, out, err = self.run_hook(hook, dict(payload, prompt=CORRECTION))
                        self.assertEqual((code, err), (0, ""))
                        ctx = self.answered(event, out)["hookSpecificOutput"]["additionalContext"]
                        self.assertIn("CORRECTION DETECTED", ctx)
                        self.assertIn("lessons.md", ctx)
                    else:
                        self.assertEqual(out, "")
        self.assertTrue(all(v >= 6 for v in n.values()) and windows >= 8, (n, windows))
        logs = self.logs()
        for log in logs.values():
            self.assertEqual(len(log), n["Stop"], "once per session")
            for entry in log:
                self.assertEqual(sorted(entry), ["agent", "cwd", "session_id", "transcript_path", "ts"])
                self.assertEqual(entry["agent"], "codex")
        stops = [p for _, e, p in first_of_each_capture() if e == "Stop"]
        self.assertEqual([(e["session_id"], e["cwd"], e["transcript_path"]) for e in logs["sessions.log"]],
                         [(p["session_id"], p["cwd"], p["transcript_path"]) for p in stops], "Windows paths as given")
        self.assertEqual(self.vus(), ALL)

    def test_the_lessons_of_the_folder_codex_works_in(self):
        """Codex says where it works (cwd); CLAUDE_PROJECT_DIR is Claude Code's, inherited at most."""
        vault = self.root / "vault"
        (vault / "Profile").mkdir(parents=True)
        (vault / "Profile" / "lessons.md").write_text("# Lessons\n", encoding="utf-8")
        p = next(p for _, e, p in first_of_each_capture() if e == "UserPromptSubmit")
        code, out, err = self.run_hook("correction-detector", dict(p, prompt=CORRECTION, cwd=str(vault)),
                                       CLAUDE_PROJECT_DIR=str(self.work))
        self.assertEqual((code, err), (0, ""))
        ctx = self.answered("UserPromptSubmit", out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Append a line to %s/Profile/lessons.md" % vault, ctx)

    def test_a_session_codex_keeps_of_an_exec_run_is_left_alone(self):
        """The exec captures with the session file Codex wrote for them (source "exec"): the kit's own runs
        and scripts. Nothing said, nothing written; the hooks did run, so their sign is there."""
        n = 0
        for name, event, payload in first_of_each_capture():
            rollout = cap.P0 / name.replace("hook-payloads-", "rollout-exec-hooks-")
            if not rollout.exists():
                continue
            n += 1
            moved = self.root / rollout.name
            shutil.copy(str(rollout), str(moved))
            for hook in BY_EVENT[event]:
                with self.subTest(capture=name, hook=hook):
                    self.etat()
                    code, out, err = self.run_hook(hook, dict(payload, transcript_path=str(moved), prompt=CORRECTION))
                    self.assertEqual((code, out, err), (0, "", ""))
        self.assertGreaterEqual(n, 40)
        self.assertEqual([w for w in self.written() if "codex-hooks-vus" not in w], [])
        self.assertEqual(self.vus(), ALL)

    def test_a_session_in_codexs_terminal_is_served(self):
        """The TUI captures (macOS, Linux) with their session file (source "cli")."""
        n = 0
        for name, event, payload in first_of_each_capture():
            if "tui-apikey" not in name:
                continue
            rollout = cap.P0 / name.replace("hook-payloads-", "rollout-")
            moved = self.root / rollout.name
            shutil.copy(str(rollout), str(moved))
            payload = dict(payload, transcript_path=str(moved), prompt=CORRECTION)
            for hook in BY_EVENT[event]:
                with self.subTest(capture=name, hook=hook):
                    self.etat()
                    code, out, err = self.run_hook(hook, payload)
                    self.assertEqual((code, err), (0, ""))
                    if hook in ("correction-detector", "controle-sessionstart"):
                        self.answered(event, out)
                    n += 1
        self.assertEqual(n, 12)
        self.assertEqual([len(v) for v in self.logs().values()], [2, 2])


class Guard(Bench):
    """When the hooks say nothing and write nothing, whatever they are given."""

    def payloads(self):
        """One payload of each event (step 0, Linux exec), its session file not written yet, and a
        correction typed: each hook would speak or write."""
        out = {}
        for name, event, payload in first_of_each_capture():
            if name.startswith("ubuntu-0.162.1/hook-payloads-trusted"):
                out[event] = dict(payload, transcript_path=str(self.root / "not-yet.jsonl"), prompt=CORRECTION)
        self.assertEqual(sorted(out), sorted(BY_EVENT))
        return out

    def silent(self, hooks=None, argv=CODEX, payload=None, sign=False, **env):
        for event, p in self.payloads().items():
            for hook in BY_EVENT[event]:
                with self.subTest(hook=hook):
                    self.etat()
                    code, out, err = self.run_hook(hook, dict(p, **(payload or {})), argv, hooks, **env)
                    self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual([w for w in self.written() if "codex-hooks-vus" not in w], [])
        self.assertEqual(self.vus(), ALL if sign else [])

    def test_control_the_same_calls_speak_and_write(self):
        for event, p in self.payloads().items():
            for hook in BY_EVENT[event]:
                self.etat()
                code, out, err = self.run_hook(hook, p)
                self.assertEqual((code, err), (0, ""))
                if hook in ("correction-detector", "controle-sessionstart"):
                    self.answered(event, out)
        self.assertEqual([len(v) for v in self.logs().values()], [1, 1])
        self.assertEqual(self.vus(), ALL)

    def test_while_codex_is_closed_on_this_system(self):
        self.silent(self.closed)

    def test_unless_agents_json_lists_codex_and_can_be_trusted(self):
        for agents in (None, ["claude"]):
            with self.subTest(agents=agents):
                self.choose(agents)
                self.silent()
        (self.brain / "agents.json").write_text("{", encoding="utf-8")
        self.silent()

    def test_in_the_kits_own_runs_and_under_claude_p(self):
        self.silent(BRAIN_HEADLESS="1")
        self.silent(CLAUDE_CODE_ENTRYPOINT="sdk-cli")

    def test_without_a_session_file_or_with_a_prompt_another_agent_wrote(self):
        self.silent(payload={"transcript_path": None}, sign=True)
        self.silent(payload={"agent_id": "019a1-sub", "agent_type": "worker"}, sign=True)

    def test_an_id_that_is_not_plain_goes_nowhere(self):
        for bad in ("x/../../../escaped", "a\\..\\b", "", "x" * 200, None, 7):
            with self.subTest(bad=bad):
                p = self.payloads()["Stop"]
                for hook in BY_EVENT["Stop"]:
                    code, out, _ = self.run_hook(hook, dict(p, session_id=bad))
                    self.assertEqual((code, out), (0, ""))
        self.assertEqual([w for w in self.written() if "codex-hooks-vus" not in w and "recap-errors" not in w], [])


class Claude(Bench):
    """Without --agent codex, every hook is Claude Code's, as before: agents.json is not read, _agents.py
    not loaded, no field added, no sign."""

    PAYLOAD = {"session_id": "4f6c9a12-claude", "transcript_path": "/x/claude.jsonl", "cwd": "/Users/builder/proj",
               "hook_event_name": "UserPromptSubmit", "prompt": CORRECTION, "source": "startup"}

    def everything(self, hooks, argv=()):
        """Each hook's (code, stdout, stderr), then what was written, logs read whole."""
        out = []
        for hook in ALL:
            self.etat()
            out.append((hook,) + self.run_hook(hook, self.PAYLOAD, argv, hooks))
        logs = self.logs()
        for log in logs.values():
            for entry in log:
                entry.pop("ts")
        shutil.rmtree(str(self.home / ".claude"), True)
        shutil.rmtree(str(self.tmp))
        self.tmp.mkdir()
        return out, logs

    def test_the_same_whatever_agents_json_says_and_without_codex_code(self):
        before = self.everything(self.closed)
        self.assertEqual(before[1]["sessions.log"], [{"session_id": "4f6c9a12-claude", "cwd": "/Users/builder/proj",
                                                      "transcript_path": "/x/claude.jsonl"}])
        said = {hook: out for hook, _, out, _ in before[0]}
        self.assertIn("CORRECTION DETECTED", said["correction-detector"])
        self.assertIn("Contrôle de cette nuit", json.loads(said["controle-sessionstart"])["systemMessage"])
        for agents in (None, ["claude"], ["codex"]):
            with self.subTest(agents=agents):
                self.choose(agents)
                self.assertEqual(self.everything(self.open), before)
        broken = self.root / "broken"
        shutil.copytree(str(self.closed), str(broken))
        (broken / "_agents.py").write_text("raise RuntimeError('Claude must not load this')\n", encoding="utf-8")
        self.assertEqual(self.everything(broken), before, "Claude's hooks never load _agents.py")
        for argv in (("--agent", "claude"), ("--agent", "Codex"), ("--agent", "codex", "x"), ("--agent",)):
            with self.subTest(argv=argv):
                self.assertEqual(self.everything(self.open, argv), before, "anything but --agent codex is Claude")
        self.assertEqual(self.vus(), [])

    def test_claudes_reminder_character_for_character(self):
        code, out, err = self.run_hook("correction-detector", self.PAYLOAD, ())
        brain = self.home / "Documents" / "Brain"
        reminder = (
            "⚠️ CORRECTION DETECTED — BEFORE doing anything else:\n"
            f"1. Append a line to {brain}/Profile/lessons.md under today's header (## YYYY-MM-DD). The file is "
            "chronological (most recent day at the BOTTOM): today's header lives at the END of the file. Create it "
            "at the end if missing and add the entry right below it, NEVER at the top.\n"
            "   Format: - **[short context]** -> rule: [what to do] (when: [trigger condition])\n"
            "2. Confirm visibly: ✓ noted in lessons.md\n"
            "3. Only then apply the correction and continue.")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                                 "additionalContext": reminder}}) + os.linesep)


@unittest.skipUnless(POSIX, "named pipes")
class Pipe(Bench):

    def test_a_pipe_as_the_session_file_never_holds_a_hook(self):
        fifo = self.root / "rollout-fifo.jsonl"
        os.mkfifo(str(fifo))
        p = next(p for name, e, p in first_of_each_capture() if e == "Stop")
        for hook in BY_EVENT["Stop"]:
            with self.subTest(hook=hook):
                self.assertEqual(self.run_hook(hook, dict(p, transcript_path=str(fifo))), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
