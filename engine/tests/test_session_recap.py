"""Tests of the session recap (engine/hooks/session-recap.py): the block it writes
for a session in the day's journal. Every file lives in a throwaway folder: the
user's vault is never read nor written. The lock and the hook's input are tested
end to end in test-hooks.sh.

    python3 -I -m unittest discover -s engine/tests -v
"""
import importlib.util
import re
import shutil
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))

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


if __name__ == "__main__":
    unittest.main()
