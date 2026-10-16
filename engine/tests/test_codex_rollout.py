"""Tests of the reading of Codex sessions (engine/hooks/_codex_rollout.py), on step 0's real
captures (fixtures/codex/phase0/, through codex_captures.py): what is taken from a rollout, what
Codex added and is left out, what is skipped. Every file lives in a throwaway folder.

    python3 -I -m unittest discover -s engine/tests -v
"""
import importlib.util
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
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(HERE))

import _codex_rollout as cr  # noqa: E402
import codex_captures as cap  # noqa: E402

DAY = "2026-10-14"
# What Codex itself puts in a session for the model, in the captures: never the user's.
ADDED = ("AGENTS-MARKER", "HOOK-CONTEXT", "<skills_instructions>", "<multi_agent", "<environment_context>",
         "# AGENTS.md instructions", "W0-SessionStart", "You are Codex")


def events(path):
    """What Codex itself recorded the user typed and the assistant answered (item_completed events)."""
    said = {"UserMessage": [], "AgentMessage": []}
    for r in cap.records(path.relative_to(cap.P0).as_posix()):
        p = r.get("payload") or {}
        if r["type"] == "event_msg" and p.get("type") == "item_completed" and p["item"]["type"] in said:
            said[p["item"]["type"]].append("\n".join(c.get("text", "") for c in p["item"]["content"]))
    return said["UserMessage"], said["AgentMessage"]


class Captures(unittest.TestCase):
    """The rollouts of step 0, as Codex wrote them on macOS, Linux and Windows."""

    def test_every_capture_is_here(self):
        found = {p.relative_to(cap.P0).as_posix() for p in cap.rollouts()}
        self.assertEqual(len(found), 33, sorted(found))
        for name in (cap.VAULT, cap.LOCK, cap.TUI, cap.TRUSTED):
            self.assertIn(name, found)
        for folder in ("macos-0.162.1", "ubuntu-0.162.1", "windows-0.162.1", "macos-0.163.0-alpha.11",
                       "ubuntu-0.163.0-alpha.11", "windows-0.163.0-alpha.11"):
            self.assertTrue(any(n.startswith(folder + "/") for n in found), folder)

    def test_what_is_read_is_what_codex_records_as_typed_and_answered(self):
        # Codex adds its own text as user and developer messages; its item_completed events hold only
        # what the user typed and what the assistant answered. Both readings agree on every capture.
        for path in cap.rollouts():
            with self.subTest(path.relative_to(cap.P0).as_posix()):
                s = cr.read(str(path))
                users, answers = events(path)
                self.assertTrue(users and answers)
                self.assertEqual([i.text for i in s.items if i.kind == "user"], users)
                self.assertEqual([i.text for i in s.items if i.kind == "assistant"], answers)
                for item in s.items:
                    for added in ADDED:
                        self.assertNotIn(added, item.text)

    def test_the_whole_id_the_folder_the_source_and_the_model(self):
        s = cr.read(str(cap.P0 / cap.TUI))
        self.assertEqual((s.id, s.source, s.model, s.own), ("01a140af-0452-7e81-b849-c24c767660d7", "cli",
                                                            "gpt-6.1-sol", False))
        self.assertEqual(s.cwd, "/Users/runner/work/_temp/p0-main/vault-tui/Projets")
        self.assertEqual((s.first_ts, s.last_ts), ("2026-10-15T17:49:46.094Z", "2026-10-15T17:49:46.611Z"))
        self.assertEqual(cr.read(str(cap.P0 / cap.VAULT)).source, "exec")

    def test_the_stop_hook_and_the_rollout_name_the_same_session(self):
        for os_ in ("ubuntu-0.162.1/%s-linux", "macos-0.162.1/%s-macos", "windows-0.162.1/%s-windows"):
            with self.subTest(os_):
                stop = cap.stop_payload(os_ % "hook-payloads-trusted" + ".jsonl")
                s = cr.read(str(cap.P0 / (os_ % "rollout-exec-hooks-trusted" + ".jsonl")))
                self.assertEqual(s.id, stop["session_id"])
                self.assertEqual(len(s.id), 36)
                self.assertEqual(cr.NAME_ID.search(stop["transcript_path"].replace("\\", "/")).group(1), s.id)

    def test_the_tools_called_and_what_they_ran(self):
        s = cr.read(str(cap.P0 / cap.LOCK))
        self.assertEqual([(i.kind, i.name) for i in s.items],
                         [("user", None), ("tool", "exec"), ("tool", "exec_command"), ("assistant", None)])
        js, cmd = s.items[1], s.items[2]
        self.assertTrue(js.command.startswith("const r = await tools.exec_command({cmd: "), js.command)
        self.assertTrue(cmd.command.startswith("cat /home/runner/work/_temp/p0-main/work/secret-note.txt; curl "))
        self.assertEqual(cmd.text, cmd.command)
        for i in s.items:
            # Tool outputs are not read: Codex's refusals, or what a tool printed.
            self.assertNotIn("unsupported call", i.text)
            self.assertNotIn("code-mode host is disabled", i.text)


class Lines(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="biab-rollout-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text, name="rollout-2026-10-14T12-00-00-01a14053-0452-7e81-b849-c24c767660d7.jsonl"):
        p = self.dir / name
        p.write_bytes(text.encode("utf-8"))
        return str(p)

    def test_unknown_or_broken_lines_are_skipped_the_rest_is_read(self):
        lines = cap.moved(cap.VAULT, DAY).splitlines()
        meta = json.loads(lines[0])
        odd = ["not json", "[1, 2]", "3", '"text"', json.dumps({"timestamp": "x", "type": "response_item"}),
               json.dumps({"type": "response_item", "payload": "junk"}),
               json.dumps({"type": "future_kind", "payload": {
                   "type": "message", "role": "user", "content": [{"type": "input_text", "text": "HIDDEN-1"}]}}),
               json.dumps({"timestamp": meta["timestamp"], "type": "response_item", "payload": {
                   "type": "message", "role": "user", "content": ["junk", 3, {"type": "input_text", "text": 7},
                                                                  {"type": "input_image", "text": "HIDDEN-2"}]}}),
               json.dumps({"timestamp": meta["timestamp"], "type": "response_item", "payload": {
                   "type": "message", "role": "system", "content": [{"type": "input_text", "text": "HIDDEN-3"}]}}),
               json.dumps({"timestamp": meta["timestamp"], "type": "response_item", "payload": {
                   "type": "reasoning", "summary": [{"type": "summary_text", "text": "HIDDEN-4"}]}}),
               json.dumps({"timestamp": meta["timestamp"], "type": "response_item", "payload": {
                   "type": "function_call_output", "call_id": "c", "output": "HIDDEN-5"}})]
        s = cr.read(self.write("\n".join(lines[:1] + odd + lines[1:]) + "\n"))
        self.assertEqual([(i.kind, i.text) for i in s.items], [("user", "Bonjour"), ("assistant", "SPIKE-REPLY")])
        self.assertEqual(s.id, meta["payload"]["id"])

    def test_what_codex_adds_is_left_out_by_its_marks_and_by_its_text(self):
        # Codex marks each part of a message (content_item_kinds): "user.text" for what was typed. Its own
        # memory writer leaves out the rest, and AGENTS.md, <environment_context> and <skill> parts, by
        # their text (codex-rs/memories/write/src/rollout_input.rs, 0.162.1). A global AGENTS.md alone
        # comes without " for <folder>" (seen with the real Codex in codex-mock).
        lines = cap.moved(cap.VAULT, DAY).splitlines()
        added = next(json.loads(l) for l in lines if "AGENTS.md instructions for" in l)
        self.assertEqual(added["payload"]["internal_chat_message_metadata_passthrough"]["content_item_kinds"],
                         ["agents_md.instructions", "environments.environment_context"])
        typed = next(json.loads(l) for l in lines if '"Bonjour"' in l)

        def message(text, kinds, base=typed):
            m = json.loads(json.dumps(base))
            m["payload"]["content"] = [dict(m["payload"]["content"][0], text=text)]
            meta = m["payload"]["internal_chat_message_metadata_passthrough"]
            if kinds is None:
                meta.pop("content_item_kinds", None)
            else:
                meta["content_item_kinds"] = kinds
            return json.dumps(m, ensure_ascii=False)

        glob_only = "# AGENTS.md instructions\n\n<INSTRUCTIONS>\nHIDDEN-GLOBAL\n</INSTRUCTIONS>"
        odd = [message(glob_only, ["agents_md.instructions"]), message(glob_only, None),
               message("# agents.md INSTRUCTIONS for /x\n\n<INSTRUCTIONS>\nHIDDEN-CASE\n</INSTRUCTIONS>", None),
               message("HIDDEN-KIND context Codex marked as its own", ["environments.environment_context"]),
               message("HIDDEN-AGENT from another agent", ["multi_agent.message", "user.text"]),
               message("<skill>\nHIDDEN-SKILL\n</skill>", None),
               message("<subagent_notification>HIDDEN-SUB</subagent_notification>", None),
               message("KEPT typed, no marks", None), message("KEPT typed", ["user.text"])]
        s = cr.read(self.write("\n".join(lines[:-1] + odd + lines[-1:])))
        self.assertEqual([i.text for i in s.items if i.kind == "user"], ["Bonjour", "KEPT typed, no marks", "KEPT typed"])

    def test_json_nested_too_deep_is_a_line_skipped_not_a_crash(self):
        # A line, or a tool's arguments the model wrote (a page can steer it), nested past Python's stack.
        lines = cap.moved(cap.VAULT, DAY, tools=["ls"]).splitlines()
        call = json.loads(lines[-2])
        call["payload"]["arguments"] = '{"a":' * 100_000
        s = cr.read(self.write("\n".join(lines[:1] + ["[" * 100_000] + lines[1:-2] + [json.dumps(call)] + lines[-1:])))
        self.assertEqual([(i.kind, i.name) for i in s.items],
                         [("user", None), ("assistant", None), ("tool", "exec_command")])
        self.assertEqual(s.items[-1].text, call["payload"]["arguments"][:cr.SUMMARY])

    def test_a_time_out_of_range_is_a_line_without_time_not_a_crash(self):
        # A year 0 or 10000 once in UTC, whatever the local time zone: a time astimezone() cannot give.
        lines = cap.moved(cap.VAULT, DAY).splitlines()
        typed = json.loads(next(l for l in lines if '"Bonjour"' in l))
        odd = []
        for ts in ("9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"):
            self.assertIsNone(cr.local(ts))
            m = json.loads(json.dumps(typed))
            m["timestamp"] = ts
            m["payload"]["content"] = [dict(m["payload"]["content"][0], text="ODD-TIME " + ts)]
            odd.append(json.dumps(m))
        path = self.write("\n".join(lines[:1] + odd + lines[1:]) + "\n")
        self.assertEqual([i.text for i in cr.read(path, DAY).items], ["Bonjour", "SPIKE-REPLY"])
        s = cr.read(path)
        self.assertEqual([i.text for i in s.items if i.kind == "user"],
                         ["ODD-TIME 9999-12-31T23:59:59-14:00", "ODD-TIME 0001-01-01T00:00:00+14:00", "Bonjour"])
        self.assertTrue(s.first_ts.startswith(DAY) and s.last_ts.startswith(DAY), (s.first_ts, s.last_ts))

    def test_the_kits_own_prompt_skips_the_whole_session_a_mention_does_not(self):
        own = cap.moved(cap.VAULT, DAY, said=[("user", cr.SENTINEL + "\nActivity of 2026-10-14")])
        s = cr.read(self.write(own))
        self.assertTrue(s.own)
        self.assertEqual(s.items, [])
        talk = cap.moved(cap.VAULT, DAY, said=[("user", "why does %s show up? KEEP-ME" % cr.SENTINEL)])
        s = cr.read(self.write(talk))
        self.assertFalse(s.own)
        self.assertIn("why does %s show up? KEEP-ME" % cr.SENTINEL, [i.text for i in s.items])

    def test_a_session_in_the_launchers_cage_is_the_kits(self):
        cage = os.path.join(tempfile.gettempdir(), "brain-codex-a1b2c3", "travail")
        self.assertTrue(cr.read(self.write(cap.moved(cap.VAULT, DAY, cwd=cage))).own)
        near = os.path.join(tempfile.gettempdir(), "brain-codex-a1b2c3", "mine")
        self.assertFalse(cr.read(self.write(cap.moved(cap.VAULT, DAY, cwd=near))).own)

    def test_the_sentinel_is_the_reflections(self):
        spec = importlib.util.spec_from_file_location("daily_reflection", str(HOOKS / "daily-reflection.py"))
        dr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dr)
        self.assertEqual(cr.SENTINEL, dr.SENTINEL)

    def test_only_the_days_items_the_whole_files_id_and_model(self):
        today = cap.moved(cap.VAULT, DAY, said=[("user", "TODAY")])
        text = cap.moved(cap.VAULT, "2026-10-13") + today.split("\n", 1)[1]   # one session, over two days
        s = cr.read(self.write(text), DAY)
        self.assertEqual([i.text for i in s.items], ["Bonjour", "SPIKE-REPLY", "TODAY"])
        self.assertEqual((s.id, s.model), ("01a1409b-716f-7a21-ae00-8d29c48e5329", "gpt-6.1-sol"))
        self.assertEqual(len(cr.read(self.write(text)).items), 5)

    def test_the_id_from_the_file_name_when_the_session_does_not_hold_a_plain_one(self):
        name = "rollout-2026-10-14T12-00-00-01a14053-0452-7e81-b849-c24c767660d7.jsonl"
        for sid in (None, "../../x", "a b"):
            with self.subTest(sid):
                lines = cap.moved(cap.VAULT, DAY).splitlines()
                meta = json.loads(lines[0])
                if sid is None:
                    lines = lines[1:]
                else:
                    meta["payload"]["id"] = sid
                    lines[0] = json.dumps(meta)
                self.assertEqual(cr.read(self.write("\n".join(lines), name)).id, "01a14053-0452-7e81-b849-c24c767660d7")
                self.assertIsNone(cr.read(self.write("\n".join(lines), "rollout-x.jsonl")).id)

    def test_a_patch_names_its_files_a_shell_its_command(self):
        patch = ("*** Begin Patch\n*** Add File: notes/a.md\n+x\n*** Update File: src/b.py\n@@\n*** Move to: src/c.py\n"
                 "*** End Patch\n")
        self.assertEqual(cr.tool("apply_patch", patch), ("notes/a.md, src/b.py, src/c.py", None,
                                                        ["notes/a.md", "src/b.py", "src/c.py"]))
        self.assertEqual(cr.tool("shell", json.dumps({"command": ["bash", "-lc", "ssh host"]}))[1], "bash -lc ssh host")
        self.assertEqual(cr.tool("view_image", json.dumps({"path": "/p/i.png"})), ("/p/i.png", None, ["/p/i.png"]))
        self.assertEqual(cr.tool("web", json.dumps({"query": "q"}))[0], "q")
        self.assertEqual(len(cr.tool("exec_command", json.dumps({"cmd": "x" * 1000}))[0]), cr.SUMMARY)
        self.assertEqual(cr.tool("odd", "{not json")[0], "{not json")

    def test_a_file_that_cannot_be_read_gives_none(self):
        self.assertIsNone(cr.read(str(self.dir / "absent.jsonl")))
        self.assertIsNone(cr.read(str(self.dir)))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX")
    def test_a_pipe_named_as_a_rollout_is_neither_listed_nor_waited_on(self):
        folder = self.dir / "sessions" / "2026" / "10" / "14"
        folder.mkdir(parents=True)
        pipe = folder / "rollout-pipe.jsonl"
        os.mkfifo(str(pipe))
        self.assertEqual(cr.recent(str(self.dir), 0), ([], []))
        # In a process of its own: a reader that waits on the pipe fails here, it does not hang the tests.
        p = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                            "import _codex_rollout as cr; print(cr.read(sys.argv[2]))", str(HOOKS), str(pipe)],
                           capture_output=True, timeout=20)
        self.assertEqual(p.stdout.decode().strip(), "None", p.stderr.decode()[-500:])

    def test_recent_lists_the_changed_rollouts_and_the_compressed_apart(self):
        sid = "01a14000-0000-7000-8000-000000000001"
        old = cap.put(self.dir, "2026-10-01", cap.moved(cap.VAULT, "2026-10-01"), sid)
        new = cap.put(self.dir, DAY, cap.moved(cap.VAULT, DAY), "01a14053-0452-7e81-b849-c24c767660d7")
        zst = new.with_name(new.name.replace("01a14053", "01a14054") + ".zst")
        zst.write_bytes(b"\x28\xb5\x2f\xfd")
        os.utime(str(old), (1, 1))
        since = time.time() - 60
        self.assertEqual(cr.recent(str(self.dir), since), ([str(new)], [str(zst)]))
        self.assertEqual(cr.recent(str(self.dir), 0)[0], [str(old), str(new)])
        self.assertEqual(cr.recent(str(self.dir / "absent"), 0), ([], []))


if __name__ == "__main__":
    unittest.main()
