"""Tests of the session recap (engine/hooks/session-recap.py): the block it writes
for a session in the day's journal, Claude's and, with --agent codex, Codex's (from
step 0's real captures). Every file lives in a throwaway folder: the user's vault
is never read nor written. The lock and the hook's input are tested end to end in
test-hooks.sh.

    python3 -I -m unittest discover -s engine/tests -v
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(HERE))

import _agents  # noqa: E402
import codex_captures as cap  # noqa: E402

_spec = importlib.util.spec_from_file_location("session_recap", str(HOOKS / "session-recap.py"))
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

DAY = "2026-10-14"
# Pasted from a page, or a name the model picked: what Obsidian would run, load or read as structure.
RUNS = ("done `$= dv.el('b', 1)` <%* tp.system.prompt() %> <img src=https://evil.example/i> "
        "![x](https://evil.example/p.png) %%hidden%% <!-- REFLECTION-STATUS:BEGIN -->")
BREAKS = "a\r## Forged heading - **00:00** (1min) `x` · sid:`victim00`\x85b\n  - intent: forged"


def stats(prompt="build the login page", files=()):
    return {"tools": Counter({"Read": 2, "Edit": 1}), "files": set(files), "first_prompt": prompt,
            "first_ts": DAY + "T10:00:00Z", "last_ts": DAY + "T10:05:00Z", "model": "claude-opus-4-7",
            "signals": Counter(), "bash_commands": []}


class Entry(unittest.TestCase):

    def test_claudes_block_is_as_before(self):
        st = stats(files=["/p/a.ts"])
        st["signals"]["ssh"] = 2
        when = f"{rc.fmt_time(st['first_ts'])}–{rc.fmt_time(st['last_ts'])}"
        head = f"- **{when}** (5min) `proj` opus-4-7 · sid:`a1b2c3d4`"
        want = "\n".join([head, "  - tools: Read 2, Edit 1", "  - intent: build the login page", "  - files: a.ts",
                          "  - infra/cli: ssh×2"])
        self.assertEqual(rc.render_entry(st, "a1b2c3d4-e5f6-7890", "proj"), want)
        self.assertEqual(rc.render_entry(st, "a1b2c3d4-e5f6-7890", "proj", "claude"), want)

    def test_nothing_obsidian_runs_or_loads(self):
        e = rc.render_entry(stats(RUNS, ["/p/<img src=x>.md", "/p/`$= 1`.md"]), "a1b2c3d4-e5f6", "$= dv.el('b', 2)")
        for bad in ("<%", "<img", "![", "%%", "<!--"):
            self.assertNotIn(bad, e)
        self.assertIsNone(re.search(r"`\s*\$?=", e), "a code span Dataview would run")
        self.assertEqual(e.count("`"), 4, "only the folder's and the session id's code spans")
        self.assertIn("  - intent: done '$= dv.el('b', 1)'", e)

    def test_one_line_per_field_whatever_the_line_breaks(self):
        e = rc.render_entry(stats(BREAKS, ["/p/x\n- **00:01** sid:`victim00`.md"]), "a1b2c3d4", "proj\n## Also forged")
        lines = e.splitlines()  # every line break Obsidian or Python knows
        self.assertEqual(lines, e.split("\n"))
        self.assertEqual([l for l in lines if not l.startswith("  - ")], [lines[0]])
        self.assertNotIn("sid:`victim00`", e)

    def test_long_intent_cut_after_masking(self):
        key = "sk-ant-" + "A1b2C3d4" * 6
        e = rc.render_entry(stats("x " * 60 + key + " tail " * 40), "a1b2c3d4", "proj")
        intent = next(l for l in e.splitlines() if l.startswith("  - intent: "))
        self.assertLessEqual(len(intent) - len("  - intent: "), 140)
        self.assertNotIn(key[:12], e)


class Clean(unittest.TestCase):
    """_vault_write.clean_item, which the recap shares with the reflection."""

    def test_runs_of_marks_and_a_mask_leave_nothing_behind(self):
        # A replace made once leaves one behind: "!![" gave "![", "%%%" gave "%%" (hides the rest of the
        # note), "--->" gave "-->"; and a "!" before a masked key made "![REDACTED]".
        for raw in ("!![x](https://evil.example/p.png)", "!!![[Private note]]", "a %%% hides the rest",
                    "b ---> c", "!sk-ant-" + "A1b2C3d4" * 4):
            with self.subTest(raw):
                out = rc.clean_item(raw, 500)
                for bad in ("![", "%%", "-->"):
                    self.assertNotIn(bad, out)

    def test_a_long_paste_is_cleaned_in_linear_time(self):
        # The whole first prompt is cleaned: 200 kB of unclosed "<!--" took about 25 s, a long run of
        # list marks grew the same way, and the hook hung at each Stop.
        for raw in ("<!--" * 50_000, "- " * 500_000 + "x"):
            start = time.monotonic()
            rc.clean_item(raw, 140)
            self.assertLess(time.monotonic() - start, 5)


class Journal(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="biab-recap-"))
        self.path = self.dir / f"{DAY}.md"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def entry(self, sid, prompt="build the login page", files=()):
        return rc.render_entry(stats(prompt, files), sid, "proj")

    def test_new_journal_then_an_existing_one_without_the_section(self):
        self.assertTrue(rc.upsert_session(self.path, "a1b2c3d4", self.entry("a1b2c3d4")))
        self.assertTrue(self.path.read_text(encoding="utf-8").startswith(f"# {DAY} — Daily\n\n## 📊 Claude Sessions\n- **"))
        self.path.write_text("# mine\n\nnotes\n", encoding="utf-8")
        rc.upsert_session(self.path, "a1b2c3d4", self.entry("a1b2c3d4"))
        self.assertTrue(self.path.read_text(encoding="utf-8").startswith("# mine\n\nnotes\n\n## 📊 Claude Sessions\n- **"))
        self.assertFalse(rc.upsert_session(self.path, "a1b2c3d4", self.entry("a1b2c3d4")))
        self.assertEqual([p.name for p in self.dir.iterdir()], [self.path.name])

    def test_a_crlf_journal_with_a_bom_keeps_both(self):
        # As the earlier recap wrote it on Windows (write_text), or a person did.
        old = self.entry("a1b2c3d4", "earlier stop").replace("\n", "\r\n")
        self.path.write_bytes(("\ufeff# mine\r\n\r\n## 📊 Claude Sessions\r\n" + old + "\r\n- my own line\r\n").encode("utf-8"))
        self.assertTrue(rc.upsert_session(self.path, "a1b2c3d4", self.entry("a1b2c3d4", "last stop")))
        text = self.path.read_bytes().decode("utf-8")
        self.assertTrue(text.startswith("\ufeff# mine\r\n"))
        self.assertNotRegex(text, r"(?<!\r)\n", "a bare line feed in a CRLF file")
        self.assertIn("\r\n- my own line\r\n", text)
        self.assertEqual(text.count("sid:`a1b2c3d4`"), 1)
        self.assertIn("  - intent: last stop\r\n", text)
        self.assertFalse(rc.upsert_session(self.path, "a1b2c3d4", self.entry("a1b2c3d4", "last stop")))

    def test_a_session_cannot_forge_or_erase_another_ones_block(self):
        self.path.write_text(f"# {DAY} — Daily\n\n## 📊 Claude Sessions\n", encoding="utf-8")
        rc.upsert_session(self.path, "victim00-1111", self.entry("victim00-1111"))
        hostile = self.entry("hostile1-2222", BREAKS, ["/p/x\n- **00:01** sid:`victim00`\n  - files: y.md"])
        rc.upsert_session(self.path, "hostile1-2222", hostile)
        rc.upsert_session(self.path, "victim00-1111", self.entry("victim00-1111", "second stop"))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len([l for l in lines if l.startswith("- **") and "sid:`victim00`" in l]), 1)
        self.assertIn(hostile, "\n".join(lines), "the other session's block is whole")
        self.assertEqual(len([l for l in lines if l.startswith("#")]), 2)

    def test_a_write_made_meanwhile_is_not_lost(self):
        self.path.write_text(f"# {DAY} — Daily\n\n## 📊 Claude Sessions\n", encoding="utf-8")
        other = "- written by the reflection meanwhile\n"

        class Raced(type(self.path)):
            """The journal, which another writer changes right after our first read of it."""
            done = False

            def _race(self, data):
                if not Raced.done:
                    Raced.done = True
                    with open(str(self), "a", encoding="utf-8") as f:
                        f.write(other)
                return data

            def read_bytes(self):
                return self._race(super().read_bytes())

            def read_text(self, *a, **kw):
                return self._race(super().read_text(*a, **kw))

        rc.upsert_session(Raced(str(self.path)), "a1b2c3d4", self.entry("a1b2c3d4"))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(other, text)
        self.assertIn("sid:`a1b2c3d4`", text)

# Claude's or Codex's Stop hook in a fresh Python, Codex opened there for the second: what it imported.
FRESH = """
import io, json, runpy, sys
hooks, recap, agent, sid, path = sys.argv[1:]
sys.path.insert(0, hooks)
if agent == "codex":
    import _agents
    _agents.CODEX_OUVERT = frozenset({_agents.systeme()})
sys.stdin = io.StringIO(json.dumps({"session_id": sid, "cwd": "/w/proj", "transcript_path": path}))
sys.argv = [recap] + (["--agent", "codex"] if agent == "codex" else [])
try:
    runpy.run_path(recap, run_name="__main__")
except SystemExit:
    pass
print("LOADED", "_codex_rollout" in sys.modules, "_agents" in sys.modules)
"""


class Codex(unittest.TestCase):
    """A Codex session's block (--agent codex): read from its rollout, under its whole id. Codex is
    closed on every system (_agents.CODEX_OUVERT): opened here only, in this process."""
    A, B = "01a14053-0452-7e81-b849-c24c767660d7", "01a14053-9c1e-7d20-a4b1-5f0e2d3c4b5a"   # the same minute

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="biab-recap-codex-"))
        self.home, self.codex_home = self.root / "home", self.root / "home" / ".codex"
        self.brain, self.locks = self.root / "Brain", self.root / "locks"
        self.journals = self.brain / "Journal"
        self.home.mkdir()
        (self.brain / "Profile").mkdir(parents=True)
        day = time.strftime("%Y-%m-%d")
        self.journal = self.journals / f"{day}.md"
        self.day = day
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ.update(HOME=str(self.home), USERPROFILE=str(self.home), BRAIN_DIR=str(self.brain), LANG="en_US.UTF-8")
        for k in ("LC_ALL", "LC_MESSAGES"):
            os.environ.pop(k, None)
        p = mock.patch.object(rc, "LOCK_DIR", self.locks)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def choose(self, agents, opened=True):
        folder = self.home / ".brain-in-a-box"
        folder.mkdir(exist_ok=True)
        (folder / "agents.json").write_text(json.dumps(
            {"version": 1, "agents": agents, "prefere": agents[0],
             **({"codex_home": str(self.codex_home)} if "codex" in agents else {})}), encoding="utf-8")
        os.chmod(str(folder / "agents.json"), 0o600)
        if opened:
            p = mock.patch.object(_agents, "CODEX_OUVERT", frozenset({_agents.systeme()}))
            p.start()
            self.addCleanup(p.stop)

    def rollout(self, sid, tools=("ssh deploy@example.org",), base=cap.LOCK, said=(), prompt=None, source="cli"):
        """A session typed in Codex's terminal (source "cli"): the capture's lines, its source the TUI's."""
        return cap.put(self.codex_home, self.day,
                       cap.moved(base, self.day, sid=sid, tools=tools, said=said, prompt=prompt, source=source), sid)

    def stop(self, sid, path, argv=("--agent", "codex")):
        """session-recap.py run as Codex's Stop hook, with the payload Codex gave it in step 0."""
        payload = dict(cap.stop_payload(), session_id=sid, transcript_path=None if path is None else str(path))
        with mock.patch.object(sys, "argv", ["session-recap.py", *argv]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                contextlib.suppress(SystemExit):
            rc.main()
        for lock in self.locks.glob("recap-*.lock"):
            os.utime(str(lock), (1, 1))   # past the 30 s debounce

    def blocks(self):
        text = self.journal.read_text(encoding="utf-8") if self.journal.exists() else ""
        return [l for l in text.splitlines() if l.startswith("- **")], text

    def test_a_codex_session_from_its_rollout_under_its_whole_id(self):
        self.choose(["claude", "codex"])
        self.stop(self.A, self.rollout(self.A))
        heads, text = self.blocks()
        self.assertEqual(len(heads), 1, text)
        self.assertRegex(heads[0], r"^- \*\*\d\d:\d\d–\d\d:\d\d\*\* \(\d+s\) `/home/runner/work/_temp/p0-main/work` "
                                   r"codex gpt-6\.1-sol · sid:`%s`$" % self.A)
        self.assertIn("  - tools: exec_command 2, exec 1\n", text)
        self.assertIn("  - intent: Judge. P0-SENTINEL-ROLLOUT-7f3a\n", text)
        self.assertIn("  - infra/cli: curl×2, ssh×1", text)
        self.assertTrue(text.startswith(f"# {self.day} — Daily\n\n## 📊 Claude Sessions\n- **"), text)

    def test_a_time_out_of_range_in_the_rollout_still_gives_the_block(self):
        # A year 0 or 10000 once in UTC, whatever the local time zone (_codex_rollout.local).
        self.choose(["claude", "codex"])
        p = self.rollout(self.A)
        with open(str(p), "ab") as f:
            for ts in ("9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"):
                f.write((json.dumps({"timestamp": ts, "type": "event_msg", "payload": {"type": "x"}}) + "\n").encode())
        self.stop(self.A, p)
        heads, text = self.blocks()
        self.assertEqual(len(heads), 1, text)
        self.assertRegex(heads[0], r"^- \*\*\d\d:\d\d–\d\d:\d\d\*\* \(\d+s\) .* · sid:`%s`$" % self.A)

    def test_two_sessions_of_the_same_minute_keep_a_block_each(self):
        self.choose(["codex"])
        a = self.rollout(self.A)
        self.stop(self.A, a)
        self.stop(self.B, self.rollout(self.B, tools=("terraform plan",)))
        a.write_bytes(cap.moved(cap.LOCK, self.day, sid=self.A, tools=("ssh deploy@example.org", "kubectl get pods"),
                                source="cli").encode("utf-8"))
        self.stop(self.A, a)   # its second Stop: its own block only is replaced
        heads, text = self.blocks()
        self.assertEqual([h.rsplit("sid:", 1)[1] for h in heads], [f"`{self.B}`", f"`{self.A}`"], text)
        self.assertIn("kubectl", text)
        self.assertIn("terraform", text)

    def test_a_claude_block_with_the_same_first_characters_is_never_taken(self):
        self.choose(["claude", "codex"])
        claude = rc.render_entry(stats(), self.A, "proj")
        rc.upsert_session(self.journal, self.A, claude)
        self.stop(self.A, self.rollout(self.A))
        self.stop(self.A, self.rollout(self.A, tools=("kubectl get pods",)))
        rc.upsert_session(self.journal, self.A, rc.render_entry(stats("second stop"), self.A, "proj"))
        heads, text = self.blocks()
        self.assertEqual([h.rsplit("sid:", 1)[1] for h in heads], [f"`{self.A}`", f"`{self.A[:8]}`"], text)
        self.assertIn("  - intent: second stop", text)
        self.assertIn("kubectl", text)

    def test_silent_unless_agents_json_lists_codex_and_codex_is_open(self):
        for case in ("absent", "closed", "claude"):
            with self.subTest(case):
                if case == "closed":
                    self.choose(["claude", "codex"], opened=False)
                elif case == "claude":
                    self.choose(["claude"])   # open: what silences it is the list
                with mock.patch.object(rc, "parse_rollout", wraps=rc.parse_rollout) as reader:
                    self.stop(self.A, self.rollout(self.A))
                self.assertFalse(reader.called)
                self.assertFalse(self.journal.exists())
                self.assertFalse(self.locks.exists() and any(self.locks.iterdir()), "not even a lock")

    def test_without_the_argument_the_hook_is_claudes_as_before(self):
        self.choose(["claude", "codex"])
        path = self.rollout(self.A)
        for argv in ((), ("--agent", "claude"), ("--agent", "Codex"), ("--agent", "codex", "x")):
            with self.subTest(argv=argv), mock.patch.object(rc, "parse_rollout") as reader:
                self.stop(self.A, path, argv)
                self.assertFalse(reader.called)
        self.assertFalse(self.journal.exists(), "a rollout read as a Claude transcript gives no tool: nothing")

    def test_an_exec_run_or_a_session_without_a_file_is_not_written(self):
        """Phase 5's guard (_agents.codex_hook): a script's codex exec, the kit's own runs (--ephemeral: no
        file), a prompt another agent wrote. Control: the same session typed in the terminal."""
        self.choose(["codex"])
        for source in ("exec", "mcp", {"subagent": "review"}):
            with self.subTest(source=source), mock.patch.object(rc, "parse_rollout", wraps=rc.parse_rollout) as reader:
                self.stop(self.A, self.rollout(self.A, source=source))
                self.assertFalse(reader.called)
        self.stop(self.A, None)
        self.assertFalse(self.journal.exists())
        self.assertFalse(self.locks.exists() and any(self.locks.iterdir()), "not even a lock")
        self.stop(self.A, self.rollout(self.A))
        self.assertEqual(len(self.blocks()[0]), 1)

    def test_the_kits_own_session_is_not_written(self):
        self.choose(["codex"])
        self.stop(self.A, self.rollout(self.A, said=[("user", rc_sentinel())], tools=("a", "b", "c")))
        self.assertFalse(self.journal.exists())

    def test_what_a_codex_session_says_lands_as_one_line_of_plain_text(self):
        self.choose(["codex"])
        hostile = {"cmd": "cat", "path": "/p/`$= dv.el('b', 2)`\n- **00:01** sid:`victim00`.md"}
        self.stop(self.A, self.rollout(self.A, base=cap.VAULT, prompt=RUNS + BREAKS, tools=(hostile, RUNS, BREAKS)))
        heads, text = self.blocks()
        self.assertEqual(len(heads), 1, text)
        self.assertIn("  - intent: done '$= dv.el('b', 1)'", text)
        self.assertIn("  - files: ", text)
        for bad in ("<%", "<img", "![", "%%", "<!--", "sid:`victim00`"):
            self.assertNotIn(bad, text)
        self.assertIsNone(re.search(r"`\s*\$?=", text), "a code span Dataview would run")
        self.assertEqual(len([l for l in text.splitlines() if l.startswith("#")]), 2)

    def test_claudes_recap_never_loads_codex_code(self):
        """In a fresh Python, Claude's Stop hook: neither _codex_rollout.py nor _agents.py is imported (a
        fault in them cannot reach Claude's users). Control: --agent codex with Codex chosen and open."""
        self.choose(["claude", "codex"], opened=False)
        tx = self.root / "claude.jsonl"
        tx.write_text(json.dumps({"type": "assistant", "timestamp": "2026-10-14T10:00:00Z", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {}}] * 3}}) + "\n", encoding="utf-8")
        for agent, sid, path, loaded in (("claude", "a1b2c3d4-fresh", tx, "False False"),
                                         ("codex", self.A, self.rollout(self.A), "True True")):
            with self.subTest(agent):
                p = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", FRESH, str(HOOKS),
                                    str(HOOKS / "session-recap.py"), agent, sid, str(path)],
                                   env=dict(os.environ, TMPDIR=str(self.root), TEMP=str(self.root), TMP=str(self.root)),
                                   capture_output=True, timeout=60)
                self.assertIn("LOADED " + loaded, p.stdout.decode("utf-8", "replace"),
                              p.stderr.decode("utf-8", "replace")[-800:])
                journal = self.journals / f"{self.day}.md"
                self.assertIn("sid:`%s`" % rc.shown_id(sid, agent), journal.read_text(encoding="utf-8"))


class Brain(unittest.TestCase):
    """Where a session goes: the brain the session start names (BRAIN_DIR, else Documents/Brain, else ~/Brain,
    each with its Profile folder), never one made up. And its titles: in the brain's language (_langue.py, as
    the reflection: soul.md's, else the computer's), a section already there kept in its own."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="biab-recap-brain-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.day = time.strftime("%Y-%m-%d")
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ.update(HOME=str(self.home), USERPROFILE=str(self.home), LANG="en_US.UTF-8")
        for k in ("BRAIN_DIR", "LC_ALL", "LC_MESSAGES"):
            os.environ.pop(k, None)
        p = mock.patch.object(rc, "LOCK_DIR", self.root / "locks")
        p.start()
        self.addCleanup(p.stop)
        self.tx = self.root / "claude.jsonl"
        self.tx.write_text(json.dumps({"type": "assistant", "timestamp": DAY + "T10:00:00Z", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {}}] * 3}}) + "\n", encoding="utf-8")
        self.n = 0

    def brain(self, where, soul=None):
        (where / "Profile").mkdir(parents=True)
        if soul is not None:
            (where / "Profile" / "soul.md").write_text(soul, encoding="utf-8")
        return where

    def stop(self, cwd="/w/proj"):
        """A Claude Code session's Stop hook; the id its block shows."""
        self.n += 1
        sid = "brain%03d-%d" % (self.n, os.getpid())
        payload = {"session_id": sid, "cwd": cwd, "transcript_path": str(self.tx)}
        with mock.patch.object(sys, "argv", ["session-recap.py"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), contextlib.suppress(SystemExit):
            rc.main()
        return "sid:`%s`" % sid[:8]

    def journal(self, brain):
        path = brain / "Journal" / f"{self.day}.md"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def test_a_french_brain_titles_its_journal_in_french(self):
        brain = self.brain(self.home / "Documents" / "Brain", "# Soul\n\n## Langue\nfrançais\n")
        sid = self.stop()
        text = self.journal(brain)
        self.assertTrue(text.startswith(f"# {self.day} — Journal\n\n## 📊 Sessions avec Claude\n- **"), text)
        self.assertIn(sid, text)

    def test_the_computers_language_when_soul_md_names_none(self):
        os.environ["LANG"] = "fr_FR.UTF-8"
        unnamed = self.brain(self.root / "a", "## Language\n{{LANGUAGE}}\n")
        english = self.brain(self.root / "b", "## Language\nEnglish, please\n")
        for brain, title, section in ((unnamed, "Journal", "📊 Sessions avec Claude"),
                                      (english, "Daily", "📊 Claude Sessions")):
            with self.subTest(brain.name):
                os.environ["BRAIN_DIR"] = str(brain)
                self.stop()
                self.assertTrue(self.journal(brain).startswith(f"# {self.day} — {title}\n\n## {section}\n- **"))

    def test_a_section_already_there_is_kept_in_its_own_language(self):
        french = self.brain(self.root / "fr", "## Langue\nfrançais\n")
        english = self.brain(self.root / "en", "## Language\nEnglish\n")
        for brain, there in ((french, "## 📊 Claude Sessions"), (english, "## 📊 Sessions avec Claude")):
            with self.subTest(brain.name):
                os.environ["BRAIN_DIR"] = str(brain)
                (brain / "Journal").mkdir()
                (brain / "Journal" / f"{self.day}.md").write_text(f"# mine\n\n{there}\n- s1\n", encoding="utf-8")
                sid = self.stop()
                text = self.journal(brain)
                self.assertEqual(text.count("📊"), 1, text)
                self.assertTrue(text.startswith(f"# mine\n\n{there}\n- s1\n- **"), text)
                self.assertIn(sid, text)

    def test_the_brain_is_brain_dir_else_documents_else_home_brain(self):
        home_brain = self.brain(self.home / "Brain")
        self.assertIn(self.stop(), self.journal(home_brain), "install.ps1's ~/Brain when Windows guards Documents")
        documents = self.brain(self.home / "Documents" / "Brain")
        sid = self.stop()
        self.assertIn(sid, self.journal(documents), "Documents/Brain first")
        self.assertNotIn(sid, self.journal(home_brain))
        chosen = self.brain(self.root / "chosen")
        os.environ["BRAIN_DIR"] = str(chosen)
        sid = self.stop()
        self.assertIn(sid, self.journal(chosen), "BRAIN_DIR names the brain")
        self.assertNotIn(sid, self.journal(documents))
        os.environ["BRAIN_DIR"] = str(self.root / "gone")
        sid = self.stop()
        self.assertFalse((self.root / "gone").exists(), "a brain is never made")
        for brain in (home_brain, documents, chosen):
            self.assertNotIn(sid, self.journal(brain), brain)

    def test_no_brain_no_folder(self):
        self.stop()
        self.assertEqual(list(self.home.iterdir()), [], "nothing made in the home folder")

    def test_a_session_in_the_brain_says_brain(self):
        brain = self.root / "cerveau"
        for cwd, said in ((str(brain), "Brain"), (str(brain / "Projects"), "Brain"),
                          (str(self.root / "cerveau-2"), None), ("/w/proj", "/w/proj"), ("/w/a\x00b", None)):
            with self.subTest(cwd):
                shown = rc.short_cwd(cwd, str(brain))
                self.assertEqual(shown, said) if said else self.assertNotEqual(shown, "Brain")


def rc_sentinel():
    import _codex_rollout
    return _codex_rollout.SENTINEL + "\nActivity of the day"


if __name__ == "__main__":
    unittest.main()
