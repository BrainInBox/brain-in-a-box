"""Tests of engine/hooks/_agents.py: the choice of agents (Claude Code, Codex, both), its file
~/.brain-in-a-box/agents.json, and the gate that keeps Codex closed on a system until its
path is green there. Every file lives in a throwaway home. install.sh runs it end to end
in test-hooks.sh, install.ps1 in the windows job of test.yml.

    python3 -I -m unittest discover -s engine/tests -v
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import _agents as ag  # noqa: E402

POSIX = os.name != "nt"
TOUT = frozenset(["darwin", "linux", "windows"])   # Codex open everywhere: what phases 6 and 7 will do
CH = os.path.abspath(os.path.join(os.sep, "srv", "codex-home"))   # absolute on every system


class Home(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="biab-agents-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.p = Path(ag.fichier(self.home))

    def put(self, d, mode=0o600):
        self.p.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(self.p):
            self.p.unlink()
        self.p.write_bytes(d if isinstance(d, bytes) else (d if isinstance(d, str) else json.dumps(d)).encode("utf-8"))
        os.chmod(self.p, mode)   # whatever the umask

    def lire(self, ouvert=None):
        return ag.lire(self.home, codex_ouvert=ouvert)

    def choisir(self, agents="", prefere="", ouvert=None, env=None):
        return ag.choisir(agents, prefere, self.lire(ouvert), codex_ouvert=ouvert, env=env or {}, home=self.home)

    def run_cli(self, *args, env=None):
        e = {k: v for k, v in os.environ.items() if k not in ("CODEX_HOME", "BIAB_AGENTS", "BIAB_PREFERE")}
        e.update(HOME=self.home, USERPROFILE=self.home, **(env or {}))
        p = subprocess.run([sys.executable, "-I", str(HOOKS / "_agents.py"), "choisir", *args],
                           env=e, capture_output=True, timeout=20)
        return p.returncode, p.stdout.decode("ascii").strip(), p.stderr.decode("utf-8", "replace")


class Gate(unittest.TestCase):

    def test_codex_closed_on_every_system_until_its_phase(self):
        # Opening a system is phase 6 (macOS, Linux) or 7 (Windows), reviewed by the maintainer.
        self.assertEqual(ag.CODEX_OUVERT, frozenset())
        for os_ in ("darwin", "linux", "windows", ag.systeme()):
            self.assertFalse(ag.ouvert("codex", os_), os_)
            self.assertTrue(ag.ouvert("claude", os_), os_)
            with self.assertRaises(ag.Refus) as r:
                ag.choisir("codex", "", (ag.DEFAUT, "absent"), os_=os_, env={})
            self.assertEqual(r.exception.code, "ferme")

    def test_this_system_has_a_name_the_gate_knows(self):
        self.assertIn(ag.systeme(), ("darwin", "linux", "windows"))


class Read(Home):

    def test_absent_is_claude_alone(self):
        self.assertEqual(self.lire(), (ag.DEFAUT, "absent"))
        self.assertEqual(ag.DEFAUT, (("claude",), "claude", None))

    def test_a_valid_file(self):
        self.put({"version": 1, "agents": ["codex", "claude"], "prefere": "codex", "codex_home": self.home,
                  "pose_par": "install.sh", "date": "2026-10-15", "plus_tard": True})
        self.assertEqual(self.lire(TOUT), (ag.Choix(("claude", "codex"), "codex", self.home), "ok"))

    def test_a_bom_is_read(self):
        self.put(b"\xef\xbb\xbf" + json.dumps({"version": 1, "agents": ["claude"], "prefere": "claude"}).encode())
        self.assertEqual(self.lire(), (ag.DEFAUT, "ok"))

    def test_untrusted_files_give_no_agent_and_say_why(self):
        ok = {"version": 1, "agents": ["claude"], "prefere": "claude"}
        bad = {
            "illisible": [b"{", b"", b"\xff\xfe{", b"x" * (ag.FICHIER_MAX + 1)],
            "invalide": [[], "null", {**ok, "version": 2}, {**ok, "version": True}, {**ok, "version": "1"},
                         {k: v for k, v in ok.items() if k != "version"}, {**ok, "agents": []},
                         {**ok, "agents": "claude"}, {**ok, "agents": ["claude", "claude"]},
                         {**ok, "agents": ["claude", "gemini"]}, {**ok, "agents": [["claude"]]},
                         {**ok, "prefere": "codex"}, {**ok, "prefere": None}, {**ok, "codex_home": self.home},
                         {"version": 1, "agents": ["codex"], "prefere": "codex"},
                         {"version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": "rel/.codex"},
                         {"version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": 3}],
        }
        for etat, cases in bad.items():
            for d in cases:
                with self.subTest(d=str(d)[:60]):
                    self.put(d)
                    self.assertEqual(self.lire(TOUT), (ag.AUCUN, etat))

    def test_an_untrusted_file_never_sends_anything_to_claude(self):
        # Someone who chose Codex alone excluded Claude: a file gone bad must not bring it back.
        self.assertEqual(ag.AUCUN.agents, ())
        seul = {"version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": CH}
        for d, ouvert in (({**seul, "version": 2}, TOUT), ({**seul, "codex_home": "rel"}, TOUT),
                          (json.dumps(seul)[:-9], TOUT), (b"[" * 60000, TOUT), (seul, None)):
            with self.subTest(d=str(d)[:60]):
                self.put(d)
                choix, etat = self.lire(ouvert)
                self.assertNotEqual(etat, "ok")
                self.assertNotIn("claude", choix.agents)
                self.assertIsNone(choix.prefere)

    def test_a_folder_in_its_place_is_unreadable(self):
        self.p.mkdir(parents=True)
        self.assertEqual(self.lire(), (ag.AUCUN, "illisible"))

    def test_codex_listed_on_a_closed_system_is_not_trusted(self):
        self.put({"version": 1, "agents": ["claude", "codex"], "prefere": "claude", "codex_home": self.home})
        self.assertEqual(self.lire(), (ag.AUCUN, "ferme"))
        self.assertEqual(self.lire(TOUT)[1], "ok")

    def test_a_deeply_nested_file_is_unreadable_not_a_crash(self):
        self.put(b"[" * 60000)
        self.assertEqual(self.lire(), (ag.AUCUN, "illisible"))
        self.assertEqual(self.run_cli("--pose-par=install.sh")[:2], (2, "erreur=fichier:illisible"))

    @unittest.skipUnless(POSIX, "named pipes")
    def test_a_pipe_in_its_place_is_never_waited_on(self):
        self.p.parent.mkdir(parents=True)
        os.mkfifo(self.p)
        self.assertEqual(self.run_cli("--pose-par=install.sh")[:2], (2, "erreur=fichier:illisible"))
        self.assertEqual(self.lire(), (ag.AUCUN, "illisible"))

    @unittest.skipUnless(POSIX, "named pipes")
    def test_a_pipe_fed_a_valid_choice_is_still_not_trusted(self):
        # Whatever writes into it is not a file the installer wrote.
        self.p.parent.mkdir(parents=True)
        os.mkfifo(self.p, 0o600)
        w = os.open(self.p, os.O_RDWR | os.O_NONBLOCK)   # a writer is there: a read would get the bytes
        self.addCleanup(os.close, w)
        os.write(w, json.dumps({"version": 1, "agents": ["claude"], "prefere": "claude"}).encode())
        self.assertEqual(self.lire(), (ag.AUCUN, "illisible"))

    @unittest.skipUnless(POSIX, "symlinks")
    def test_a_link_in_its_place_is_not_followed(self):
        target = Path(self.home) / "elsewhere.json"
        target.write_text(json.dumps({"version": 1, "agents": ["claude"], "prefere": "claude"}), encoding="utf-8")
        os.chmod(target, 0o600)
        self.p.parent.mkdir(parents=True)
        self.p.symlink_to(target)
        self.assertEqual(self.lire(), (ag.AUCUN, "illisible"))

    @unittest.skipUnless(POSIX, "modes")
    def test_a_file_another_account_can_change_is_not_trusted(self):
        ok = {"version": 1, "agents": ["claude"], "prefere": "claude"}
        for mode, etat in ((0o666, "droits"), (0o620, "droits"), (0o602, "droits"), (0o600, "ok"),
                           (0o644, "ok"), (0o400, "ok")):
            with self.subTest(mode=oct(mode)):
                self.put(ok, mode)
                self.assertEqual(self.lire(), (ag.DEFAUT if etat == "ok" else ag.AUCUN, etat))
        self.put(ok)
        with mock.patch.object(ag.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertEqual(self.lire(), (ag.AUCUN, "droits"))

    def test_owner_and_mode_rule(self):
        def st(mode, uid):
            return os.stat_result((stat.S_IFREG | mode, 0, 0, 1, uid, 0, 10, 0, 0, 0))
        self.assertTrue(ag.a_soi(st(0o600, 501), 501))
        self.assertTrue(ag.a_soi(st(0o644, 0), 501), "root's own file: only root could have put it")
        self.assertFalse(ag.a_soi(st(0o600, 502), 501))
        self.assertFalse(ag.a_soi(st(0o660, 501), 501))
        self.assertFalse(ag.a_soi(st(0o646, 0), 501))


class Choose(Home):

    def test_nothing_chosen_no_file_is_today(self):
        self.assertEqual(self.choisir(), (ag.DEFAUT, "defaut"))

    def test_nothing_chosen_keeps_the_choice_made_before(self):
        self.put({"version": 1, "agents": ["claude", "codex"], "prefere": "codex", "codex_home": CH})
        self.assertEqual(self.choisir(ouvert=TOUT), (ag.Choix(("claude", "codex"), "codex", CH), "fichier"))

    def test_nothing_chosen_over_an_untrusted_file_is_refused(self):
        self.put("{")
        for agents, prefere in (("", ""), ("", "claude")):
            with self.assertRaises(ag.Refus) as r:
                self.choisir(agents, prefere)
            self.assertEqual(r.exception.code, "fichier:illisible")
        self.put({"version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": CH})
        with self.assertRaises(ag.Refus) as r:
            self.choisir()
        self.assertEqual(r.exception.code, "fichier:ferme")
        if POSIX:
            self.put({"version": 1, "agents": ["claude"], "prefere": "claude"}, 0o666)
            with self.assertRaises(ag.Refus) as r:
                self.choisir()
            self.assertEqual(r.exception.code, "fichier:droits")

    def test_claude_chosen(self):
        for agents in ("claude", " Claude ", "claude,claude"):
            self.assertEqual(self.choisir(agents), (ag.DEFAUT, "choix"), agents)

    def test_names_the_kit_does_not_know(self):
        for agents, prefere in (("gemini", ""), ("claude,", ""), (",", ""), ("claude;codex", ""),
                                ("claude", "gemini"), ("claude", "claude,codex")):
            with self.assertRaises(ag.Refus) as r:
                self.choisir(agents, prefere, ouvert=TOUT)
            self.assertEqual(r.exception.code, "inconnu", (agents, prefere))

    def test_codex_refused_on_a_closed_system_whatever_the_form(self):
        for agents, prefere in (("codex", ""), ("claude,codex", ""), ("codex,claude", "claude"), ("CODEX", "")):
            with self.assertRaises(ag.Refus) as r:
                self.choisir(agents, prefere)
            self.assertEqual(r.exception.code, "ferme", agents)

    def test_preferred_must_be_chosen(self):
        for agents, prefere in (("claude", "codex"), ("", "codex")):
            with self.assertRaises(ag.Refus) as r:
                self.choisir(agents, prefere, ouvert=TOUT)
            self.assertEqual(r.exception.code, "prefere", (agents, prefere))

    def test_preferred_by_default_first_named_or_kept(self):
        env = {"CODEX_HOME": CH}
        self.assertEqual(self.choisir("codex,claude", ouvert=TOUT, env=env)[0].prefere, "codex")
        self.assertEqual(self.choisir("claude,codex", ouvert=TOUT, env=env)[0].prefere, "claude")
        self.put({"version": 1, "agents": ["claude", "codex"], "prefere": "codex", "codex_home": CH})
        self.assertEqual(self.choisir("claude,codex", ouvert=TOUT, env=env)[0].prefere, "codex")
        self.assertEqual(self.choisir("claude", ouvert=TOUT, env=env)[0].prefere, "claude")
        self.assertEqual(self.choisir("", "claude", ouvert=TOUT, env=env),
                         (ag.Choix(("claude", "codex"), "claude", CH), "choix"))

    def test_codex_home_from_codex_home_else_dot_codex(self):
        self.assertEqual(self.choisir("codex", ouvert=TOUT)[0].codex_home, os.path.join(self.home, ".codex"))
        self.assertEqual(self.choisir("codex", ouvert=TOUT, env={"CODEX_HOME": " %s%s " % (CH, os.sep)})[0].codex_home, CH)
        with self.assertRaises(ag.Refus) as r:
            self.choisir("codex", ouvert=TOUT, env={"CODEX_HOME": "rel"})
        self.assertEqual(r.exception.code, "codex_home")
        self.assertIsNone(self.choisir("claude", ouvert=TOUT, env={"CODEX_HOME": "rel"})[0].codex_home)


class Write(Home):

    def write(self, agents, prefere="", ouvert=TOUT, env=None):
        existant = self.lire(ouvert)
        choix, _ = ag.choisir(agents, prefere, existant, codex_ouvert=ouvert, env=env or {"CODEX_HOME": CH},
                              home=self.home)
        return ag.ecrire(choix, "install.sh", existant, self.home)

    def test_written_whole_private_and_read_back(self):
        self.assertTrue(self.write("codex,claude"))
        d = json.loads(self.p.read_text(encoding="utf-8"))
        self.assertEqual(set(d), {"version", "agents", "prefere", "codex_home", "pose_par", "date"})
        self.assertEqual((d["version"], d["agents"], d["prefere"], d["codex_home"], d["pose_par"]),
                         (1, ["claude", "codex"], "codex", CH, "install.sh"))
        self.assertRegex(d["date"], r"^\d{4}-\d\d-\d\d$")
        self.assertEqual(self.lire(TOUT), (ag.Choix(("claude", "codex"), "codex", CH), "ok"))
        if POSIX:
            self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(self.p.parent)), ["agents.json"], "no temporary file left")
        self.assertTrue(self.p.read_bytes().endswith(b"}\n") and b"\r" not in self.p.read_bytes())

    def test_the_same_choice_again_writes_nothing(self):
        self.write("claude")
        before = (self.p.read_bytes(), self.p.stat().st_mtime_ns)
        self.assertFalse(self.write("claude"))
        self.assertEqual((self.p.read_bytes(), self.p.stat().st_mtime_ns), before)

    def test_round_trip_both_claude_both(self):
        for agents, want in (("claude,codex", ("claude", "codex")), ("claude", ("claude",)),
                             ("claude,codex", ("claude", "codex"))):
            self.assertTrue(self.write(agents))
            self.assertEqual(self.lire(TOUT)[0].agents, want)
        self.assertEqual(self.lire(TOUT)[0].codex_home, CH)

    def test_an_untrusted_file_replaced_by_a_choice_is_kept_aside(self):
        self.put("{ my notes")
        self.assertTrue(self.write("claude"))
        baks = [p for p in self.p.parent.iterdir() if p.name.startswith("agents.json.bak-")]
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_text(encoding="utf-8"), "{ my notes")
        if POSIX:
            self.assertEqual(stat.S_IMODE(baks[0].stat().st_mode), 0o600)
        self.assertEqual(self.lire(), (ag.DEFAUT, "ok"))

    def test_a_trusted_file_replaced_needs_no_copy(self):
        self.write("claude,codex")
        self.write("claude")
        self.assertEqual(os.listdir(self.p.parent), ["agents.json"])

    @unittest.skipUnless(POSIX, "symlinks")
    def test_a_link_in_its_place_is_replaced_not_written_through(self):
        target = Path(self.home) / "elsewhere.json"
        target.write_text("precious\n", encoding="utf-8")
        self.p.parent.mkdir(parents=True)
        self.p.symlink_to(target)
        self.assertTrue(self.write("claude"))
        self.assertEqual(target.read_text(encoding="utf-8"), "precious\n")
        self.assertFalse(self.p.is_symlink())
        self.assertEqual(self.lire(), (ag.DEFAUT, "ok"))

    @unittest.skipUnless(POSIX, "symlinks")
    def test_a_link_to_a_private_file_is_never_copied(self):
        target = Path(self.home) / "id_secret"
        target.write_text("PRIVATE-KEY-MATERIAL\n", encoding="utf-8")
        os.chmod(target, 0o600)
        self.p.parent.mkdir(parents=True)
        self.p.symlink_to(target)
        self.assertTrue(self.write("claude"))
        self.assertEqual(target.read_text(encoding="utf-8"), "PRIVATE-KEY-MATERIAL\n")
        for f in self.p.parent.iterdir():
            self.assertNotIn(b"PRIVATE-KEY-MATERIAL", f.read_bytes(), f.name)
        baks = [f for f in self.p.parent.iterdir() if f.name.startswith("agents.json.bak-")]
        self.assertEqual(len(baks), 1, "where the link pointed is kept aside")
        self.assertFalse(baks[0].is_symlink())
        self.assertIn(str(target), baks[0].read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(baks[0].stat().st_mode), 0o600)

    @unittest.skipUnless(POSIX, "named pipes")
    def test_a_pipe_in_its_place_is_replaced_by_a_choice(self):
        self.p.parent.mkdir(parents=True)
        os.mkfifo(self.p)
        self.assertEqual(self.run_cli("--agents=claude", "--pose-par=install.sh")[:2],
                         (0, "agents=claude prefere=claude source=choix ecrit=oui"))
        self.assertTrue(stat.S_ISREG(os.lstat(self.p).st_mode))
        self.assertEqual(self.lire(), (ag.DEFAUT, "ok"))

    @unittest.skipUnless(POSIX, "modes")
    def test_a_file_others_could_change_replaced_by_a_choice_is_kept_aside(self):
        self.put({"version": 1, "agents": ["claude"], "prefere": "claude", "mine": 1}, 0o666)
        self.assertTrue(self.write("claude"))
        baks = [f for f in self.p.parent.iterdir() if f.name.startswith("agents.json.bak-")]
        self.assertEqual(len(baks), 1)
        self.assertIn('"mine": 1', baks[0].read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o600)
        self.assertEqual(self.lire(), (ag.DEFAUT, "ok"))

    def test_an_accented_home(self):
        self.home = os.path.join(self.home, "Hélène Ünal")
        self.p = Path(ag.fichier(self.home))
        self.assertTrue(self.write("claude"))
        self.assertEqual(self.lire(), (ag.DEFAUT, "ok"))


class Cli(Home):
    """What the installers call: one ASCII line, the file written only for a choice."""

    def test_no_choice_no_file(self):
        self.assertEqual(self.run_cli("--agents=", "--prefere=", "--pose-par=install.sh")[:2],
                         (0, "agents=claude prefere=claude source=defaut ecrit=non"))
        self.assertFalse(os.path.lexists(self.p.parent), "nothing created in the home")

    def test_claude_chosen_is_recorded_once(self):
        self.assertEqual(self.run_cli("--agents=claude", "--pose-par=install.ps1")[:2],
                         (0, "agents=claude prefere=claude source=choix ecrit=oui"))
        self.assertEqual(json.loads(self.p.read_text(encoding="utf-8"))["pose_par"], "install.ps1")
        self.assertEqual(self.run_cli("--agents=claude", "--pose-par=install.sh")[:2],
                         (0, "agents=claude prefere=claude source=choix ecrit=non"))
        self.assertEqual(self.run_cli("--pose-par=install.sh")[:2],
                         (0, "agents=claude prefere=claude source=fichier ecrit=non"))

    def test_refusals_leave_the_home_untouched(self):
        for args, code in ((["--agents=codex"], "erreur=ferme"), (["--agents=claude,codex"], "erreur=ferme"),
                           (["--agents=gemini"], "erreur=inconnu"), (["--prefere=codex"], "erreur=prefere"),
                           (["--agents=claude", "--prefere=x"], "erreur=inconnu")):
            self.assertEqual(self.run_cli(*args, "--pose-par=install.sh")[:2], (2, code), args)
        self.assertFalse(os.path.lexists(self.p.parent), "nothing created in the home")
        self.put("[]")
        self.assertEqual(self.run_cli("--pose-par=install.sh")[:2], (2, "erreur=fichier:invalide"))
        self.assertEqual(self.p.read_text(encoding="utf-8"), "[]")
        if POSIX:
            self.put({"version": 1, "agents": ["claude"], "prefere": "claude"}, 0o666)
            self.assertEqual(self.run_cli("--pose-par=install.sh")[:2], (2, "erreur=fichier:droits"))
            self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o666, "left as it was")

    def test_codex_home_is_not_read_without_codex(self):
        code, out, _ = self.run_cli("--agents=claude", "--pose-par=install.sh", env={"CODEX_HOME": "rel"})
        self.assertEqual((code, out), (0, "agents=claude prefere=claude source=choix ecrit=oui"))
        self.assertNotIn("codex_home", json.loads(self.p.read_text(encoding="utf-8")))

    def test_only_the_installers_put_it(self):
        code, out, err = self.run_cli("--agents=claude", "--pose-par=bibo")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--pose-par", err)
        self.assertFalse(os.path.lexists(self.p.parent))


class CodexHook(Home):
    """codex_hook(): whether a hook Codex runs (--agent codex) acts on what Codex gave it
    (docs/proposals/codex.md, §3.4), and the sign that Codex runs the kit's hooks (§3.3)."""

    CLI = {"type": "session_meta", "payload": {"id": "01a140af-0452-7e81-b849-c24c767660d7", "source": "cli",
                                               "originator": "codex-tui", "cwd": "/w"}}

    def setUp(self):
        Home.setUp(self)
        self.put({"version": 1, "agents": ["claude", "codex"], "prefere": "claude", "codex_home": CH})
        self.vus = Path(self.home) / ".brain-in-a-box" / ag.VUS

    def rollout(self, first, name="rollout.jsonl"):
        p = Path(self.home) / name
        p.write_bytes(first if isinstance(first, bytes) else (json.dumps(first) + "\n").encode("utf-8"))
        return str(p)

    def payload(self, transcript, **kw):
        return dict({"session_id": "01a140af-0452-7e81-b849-c24c767660d7", "cwd": "/w", "hook_event_name": "Stop",
                     "model": "gpt-6.1-sol", "permission_mode": "default", "transcript_path": transcript}, **kw)

    def hook(self, payload, env=None, ouvert=TOUT, nom="session-logger"):
        return ag.codex_hook(nom, payload, env=env or {}, home=self.home, codex_ouvert=ouvert)

    def test_an_interactive_session_and_the_sign_codex_runs_the_hook(self):
        self.assertTrue(self.hook(self.payload(self.rollout(self.CLI))))
        self.assertEqual(list(ag.vus(self.home)), ["session-logger"])
        self.assertRegex(ag.vus(self.home)["session-logger"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        if POSIX:
            self.assertEqual(stat.S_IMODE(self.vus.stat().st_mode), 0o600)
        vscode = dict(self.CLI, payload=dict(self.CLI["payload"], source="vscode"))
        self.assertTrue(self.hook(self.payload(self.rollout(vscode)), nom="correction-detector"))
        self.assertEqual(sorted(ag.vus(self.home)), ["correction-detector", "session-logger"], "each hook's own")

    def test_a_session_file_not_written_yet_is_interactive(self):
        for missing in (os.path.join(self.home, "sessions", "rollout-not-yet.jsonl"),
                        "D:\\a\\_temp\\homes\\sessions\\2026\\10\\15\\rollout-x.jsonl"):
            with self.subTest(missing):
                self.assertTrue(self.hook(self.payload(missing)))

    def test_the_kits_own_runs_and_claude_p_are_silent_and_leave_no_sign(self):
        path = self.rollout(self.CLI)
        for env in ({"BRAIN_HEADLESS": "1"}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}):
            with self.subTest(env):
                self.assertFalse(self.hook(self.payload(path), env))
                self.assertFalse(os.path.lexists(self.vus))
        self.assertTrue(self.hook(self.payload(path), {"BRAIN_HEADLESS": "", "CLAUDE_CODE_ENTRYPOINT": "cli"}))

    def test_silent_and_no_sign_unless_agents_json_lists_codex_and_is_trusted(self):
        path = self.rollout(self.CLI)
        cases = [("absent", None, TOUT), ("claude", {"version": 1, "agents": ["claude"], "prefere": "claude"}, TOUT),
                 ("illisible", b"{", TOUT), ("ferme", None, None)]
        for name, d, ouvert in cases:
            with self.subTest(name):
                if d is None and name == "absent":
                    self.p.unlink()
                elif d is not None:
                    self.put(d)
                else:
                    self.put({"version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": CH})
                self.assertFalse(self.hook(self.payload(path), ouvert=ouvert))
                self.assertFalse(os.path.lexists(self.vus))

    def test_what_the_user_did_not_type_is_silent_though_the_hook_ran(self):
        def first(source):
            return dict(self.CLI, payload=dict(self.CLI["payload"], source=source))
        cases = {
            "no file kept (--ephemeral)": [self.payload(None), self.payload(""), self.payload(3),
                                           {k: v for k, v in self.payload("x").items() if k != "transcript_path"}],
            "a prompt another agent wrote": [self.payload(self.rollout(self.CLI), agent_id="019a-sub",
                                                          agent_type="worker")],
            "exec, mcp, unknown, sub-agents and Codex's own": [
                self.payload(self.rollout(first(s), "r%d.jsonl" % i)) for i, s in enumerate(
                    ("exec", "mcp", "unknown", "CLI", None, 1, {"subagent": "review"},
                     {"subagent": {"thread_spawn": {"parent_thread_id": "x", "depth": 1}}},
                     {"internal": "memory_consolidation"}, {"custom": "cli"}))],
            "a first line that does not say": [self.payload(self.rollout(b, "b%d.jsonl" % i)) for i, b in enumerate(
                (b"", b"\n", b"{", b"not json\n", b"[]\n", json.dumps({"type": "turn_context", "payload": {}}).encode(),
                 json.dumps({"type": "session_meta", "payload": "cli"}).encode(),
                 (json.dumps(self.CLI)[:-1] + ', "pad": "' + "x" * ag.LIGNE_MAX + '"}\n').encode(),
                 b"[" * 60000 + b"\n"))],
            "not a file": [self.payload(self.home), self.payload([self.rollout(self.CLI)])],
            "no plain session id": [dict(self.payload(self.rollout(self.CLI)), session_id=v)
                                    for v in (None, "", "a/../b", "a\\b", "x" * 129, 7)],
            "not an object": ["x", None, [self.payload(self.rollout(self.CLI))]],
        }
        for why, payloads in cases.items():
            for i, payload in enumerate(payloads):
                with self.subTest(why=why, i=i):
                    if os.path.lexists(self.vus):
                        self.vus.unlink()
                    self.assertFalse(self.hook(payload))
                    self.assertEqual(list(ag.vus(self.home)), ["session-logger"], "the hook ran: Codex runs it")

    def test_a_first_line_read_whole_up_to_the_limit(self):
        big = dict(self.CLI, payload=dict(self.CLI["payload"], base_instructions="x" * (ag.LIGNE_MAX - 4096)))
        self.assertTrue(self.hook(self.payload(self.rollout(big))))
        crlf = (json.dumps(self.CLI) + "\r\n{}\n").encode("utf-8")
        self.assertTrue(self.hook(self.payload(self.rollout(crlf))))

    @unittest.skipUnless(POSIX, "symlinks")
    def test_a_link_in_place_of_the_session_file_is_not_followed(self):
        link = Path(self.home) / "link.jsonl"
        link.symlink_to(self.rollout(self.CLI))
        self.assertFalse(self.hook(self.payload(str(link))))

    @unittest.skipUnless(POSIX, "named pipes")
    def test_a_pipe_in_place_of_the_session_file_is_never_waited_on(self):
        fifo = os.path.join(self.home, "rollout-fifo.jsonl")
        os.mkfifo(fifo)
        code = ("import json, sys; sys.path.insert(0, %r); import _agents as ag\n"
                "print(ag.codex_hook('x', {'transcript_path': %r}, env={}, home=%r, codex_ouvert=frozenset([ag.systeme()])))"
                % (str(HOOKS), fifo, self.home))
        p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=20)
        self.assertEqual(p.stdout.strip(), b"False", p.stderr)

    def test_the_sign_keeps_the_other_hooks_and_mends_a_bad_file(self):
        path = self.payload(self.rollout(self.CLI))
        self.vus.parent.mkdir(parents=True, exist_ok=True)
        self.vus.write_text(json.dumps({"version": 1, "hooks": {"session-recap": "2026-10-15T08:00:00Z", "x": 3}}),
                            encoding="utf-8")
        self.assertTrue(self.hook(path))
        self.assertEqual(sorted(ag.vus(self.home).items())[0], ("session-logger", ag.vus(self.home)["session-logger"]))
        self.assertEqual(ag.vus(self.home)["session-recap"], "2026-10-15T08:00:00Z")
        self.assertNotIn("x", ag.vus(self.home))
        for bad in (b"{", b"[]", b"\xff", b"[" * 60000):
            with self.subTest(bad=bad[:5]):
                self.vus.write_bytes(bad)
                self.assertEqual(ag.vus(self.home), {})
                self.assertTrue(self.hook(path))
                self.assertEqual(list(ag.vus(self.home)), ["session-logger"])

    @unittest.skipUnless(POSIX, "symlinks")
    def test_the_sign_never_writes_through_a_link(self):
        target = Path(self.home) / "precious"
        target.write_text("precious", encoding="utf-8")
        self.vus.parent.mkdir(parents=True, exist_ok=True)
        self.vus.symlink_to(target)
        self.assertEqual(ag.vus(self.home), {})
        self.assertTrue(self.hook(self.payload(self.rollout(self.CLI))))
        self.assertEqual(target.read_text(encoding="utf-8"), "precious")
        self.assertFalse(self.vus.is_symlink())

    def test_a_sign_that_cannot_be_written_never_silences_the_hook(self):
        self.vus.mkdir(parents=True)
        self.assertTrue(self.hook(self.payload(self.rollout(self.CLI))))
        self.assertEqual(ag.vus(self.home), {})


class HooksMuets(Home):
    """hooks_muets(): Codex kept a session since install.sh wired the kit's hooks (pose_hooks()), and ran
    none of them since: the night check's orange line (docs/proposals/codex.md, §3.3)."""

    def setUp(self):
        Home.setUp(self)
        self.codex = os.path.join(self.home, "codex-home")
        self.state = Path(self.home) / ".brain-in-a-box"

    @staticmethod
    def utc(ago):
        return time.strftime(ag.UTC, time.gmtime(time.time() - ago))

    def wired(self, ago):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / ag.POSES).write_text(json.dumps({"version": 1, "date": self.utc(ago)}), encoding="utf-8")

    def seen(self, **ago):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / ag.VUS).write_text(json.dumps({"version": 1, "hooks": {k: self.utc(v) if isinstance(v, int) else v
                                                                             for k, v in ago.items()}}), encoding="utf-8")

    def session(self, ago, name="rollout-2026-10-16T10-00-00-0199f0a0-0000-7000-8000-000000000001.jsonl"):
        day = Path(self.codex) / "sessions" / "2026" / "10" / "16"
        day.mkdir(parents=True, exist_ok=True)
        p = day / name
        p.write_text("{}\n", encoding="utf-8")
        os.utime(p, (time.time() - ago,) * 2)
        return p

    def muets(self):
        return ag.hooks_muets(self.codex, self.home)

    def test_a_session_since_the_install_and_no_hook_since(self):
        self.wired(3600)
        self.session(60)
        self.assertTrue(self.muets())
        self.seen(**{"session-logger": 7200})   # before the install: says nothing of the hooks now
        self.assertTrue(self.muets())
        self.seen(**{"session-logger": 7200, "correction-detector": 30})
        self.assertFalse(self.muets())

    def test_a_compressed_session_counts_too(self):
        self.wired(3600)
        self.session(60, "rollout-2026-10-16T10-00-00-0199f0a0-0000-7000-8000-000000000002.jsonl.zst")
        self.assertTrue(self.muets())

    def test_nothing_to_say_without_a_session_since_or_a_record(self):
        self.session(60)
        self.assertFalse(self.muets(), "no record of the install")
        self.wired(30)
        self.assertFalse(self.muets(), "the session was before the install")
        for bad in ("{", "[]", json.dumps({"version": 1, "date": "hier"}), json.dumps({"version": 1})):
            with self.subTest(record=bad):
                (self.state / ag.POSES).write_text(bad, encoding="utf-8")
                self.assertFalse(self.muets())

    def test_a_malformed_sign_is_no_sign(self):
        self.wired(3600)
        self.session(60)
        self.seen(**{"session-logger": "9999-later"})
        self.assertTrue(self.muets())

    @unittest.skipUnless(POSIX, "named pipes")
    def test_only_regular_session_files_count(self):
        self.wired(3600)
        day = Path(self.codex) / "sessions" / "2026" / "10" / "16"
        day.mkdir(parents=True)
        os.mkfifo(str(day / "rollout-x.jsonl"))
        (day / "rollout-y.jsonl").mkdir()
        self.assertFalse(self.muets())

    def test_the_installers_record_is_private_and_read_back(self):
        ag.pose_hooks(self.home)
        p = self.state / ag.POSES
        d = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(sorted(d), ["date", "version"])
        self.assertTrue(ag.UTC_RE.fullmatch(d["date"]))
        if POSIX:
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.session(-5)   # a session written after it
        self.assertTrue(self.muets())


class CliCodex(Home):
    """The installer's other questions: can Codex be chosen here, where does it live, wired now."""

    def cli(self, *args, env=None):
        e = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
        e.update(HOME=self.home, USERPROFILE=self.home, **(env or {}))
        p = subprocess.run([sys.executable, "-I", str(HOOKS / "_agents.py"), *args], env=e, capture_output=True, timeout=20)
        return p.returncode, p.stdout

    def test_codex_closed_here(self):
        self.assertEqual(self.cli("ouvert"), (1, b""))

    def test_codex_home_only_from_a_trusted_file_that_lists_codex(self):
        self.assertEqual(self.cli("codex-home"), (1, b""), "no file")
        self.put({"version": 1, "agents": ["claude"], "prefere": "claude"})
        self.assertEqual(self.cli("codex-home"), (1, b""), "Claude alone")
        self.put({"version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": CH})
        self.assertEqual(self.cli("codex-home"), (1, b""), "Codex is closed here: the file is not trusted")

    def test_codex_home_printed_whole_whatever_the_locale(self):
        home = os.path.join(self.home, "Données é")
        self.put({"version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": home})
        code = ("import sys; sys.path.insert(0, %r); import _agents as ag; ag.CODEX_OUVERT = frozenset([ag.systeme()]); "
                "sys.exit(ag.main(['codex-home']))" % str(HOOKS))
        e = dict(os.environ, HOME=self.home, USERPROFILE=self.home, LC_ALL="C", LANG="C", PYTHONIOENCODING="ascii")
        p = subprocess.run([sys.executable, "-I", "-c", code], env=e, capture_output=True, timeout=20)
        self.assertEqual((p.returncode, p.stdout), (0, os.fsencode(home) + b"\n"), p.stderr)

    def test_pose_hooks_writes_the_record(self):
        self.assertEqual(self.cli("pose-hooks"), (0, b""))
        self.assertTrue(ag._pose(self.home))

    def lire_cli(self, ouvert=False):
        """`_agents.py lire` as the app runs it, Codex open here or not: (exit code, stdout)."""
        gate = "ag.CODEX_OUVERT = frozenset([ag.systeme()]); " if ouvert else ""
        code = "import sys; sys.path.insert(0, %r); import _agents as ag; %ssys.exit(ag.main(['lire']))" % (str(HOOKS), gate)
        e = dict(os.environ, HOME=self.home, USERPROFILE=self.home, LC_ALL="C", LANG="C", PYTHONIOENCODING="ascii")
        p = subprocess.run([sys.executable, "-I", "-c", code], env=e, capture_output=True, timeout=20)
        return p.returncode, p.stdout

    def test_lire_is_what_lire_says_for_the_app(self):
        def said(ouvert=False):
            rc, out = self.lire_cli(ouvert)
            self.assertEqual(rc, 0)
            out.decode("ascii")   # one ASCII line, whatever the locale
            self.assertEqual(out.count(b"\n"), 1, out)
            return json.loads(out)
        self.assertEqual(said(), {"etat": "absent", "agents": ["claude"], "prefere": "claude", "codex_home": None})
        self.put({"version": 1, "agents": ["claude"], "prefere": "claude"})
        self.assertEqual(said(), {"etat": "ok", "agents": ["claude"], "prefere": "claude", "codex_home": None})
        home = os.path.join(self.home, "Données é")
        both = {"version": 1, "agents": ["codex", "claude"], "prefere": "codex", "codex_home": home}
        self.put(both)
        self.assertEqual(said(), {"etat": "ferme", "agents": [], "prefere": None, "codex_home": None},
                         "Codex is closed here: the file is not trusted")
        self.assertEqual(said(ouvert=True),
                         {"etat": "ok", "agents": ["claude", "codex"], "prefere": "codex", "codex_home": home})
        self.put({"version": 1, "agents": ["claude"], "prefere": "codex"})
        self.assertEqual(said(ouvert=True), {"etat": "invalide", "agents": [], "prefere": None, "codex_home": None})
        self.put("{not json")
        self.assertEqual(said()["etat"], "illisible")
        # As the installers call it, with the gate as shipped.
        self.assertEqual(self.cli("lire")[0], 0)

    def muets_cli(self, ouvert=False):
        gate = "ag.CODEX_OUVERT = frozenset([ag.systeme()]); " if ouvert else ""
        code = "import sys; sys.path.insert(0, %r); import _agents as ag; %ssys.exit(ag.main(['hooks-muets']))" % (str(HOOKS), gate)
        e = dict(os.environ, HOME=self.home, USERPROFILE=self.home)
        p = subprocess.run([sys.executable, "-I", "-c", code], env=e, capture_output=True, timeout=20)
        return p.returncode, p.stdout

    def test_hooks_muets_for_the_app_only_of_the_codex_agents_json_records(self):
        codex = os.path.join(self.home, "Données é")
        state = Path(self.home) / ".brain-in-a-box"
        state.mkdir()
        (state / ag.POSES).write_text(json.dumps({"version": 1, "date": time.strftime(ag.UTC, time.gmtime(time.time() - 3600))}))
        day = Path(codex) / "sessions" / "2026" / "10" / "17"
        day.mkdir(parents=True)
        (day / "rollout-2026-10-17T10-00-00-0199f0a0-0000-7000-8000-000000000001.jsonl").write_text("{}\n")
        self.assertEqual(self.muets_cli(ouvert=True), (0, b"non\n"), "no agents.json: Claude alone")
        self.put({"version": 1, "agents": ["claude"], "prefere": "claude"})
        self.assertEqual(self.muets_cli(ouvert=True), (0, b"non\n"), "Claude alone")
        self.put({"version": 1, "agents": ["claude", "codex"], "prefere": "claude", "codex_home": codex})
        self.assertEqual(self.muets_cli(), (0, b"non\n"), "Codex closed here: the file is not trusted")
        self.assertEqual(self.muets_cli(ouvert=True), (0, b"oui\n"))
        self.put({"version": 1, "agents": ["claude", "codex"], "prefere": "claude", "codex_home": os.path.join(self.home, "other")})
        self.assertEqual(self.muets_cli(ouvert=True), (0, b"non\n"), "another Codex home: its sessions only")
        self.assertEqual(self.cli("hooks-muets"), (0, b"non\n"), "as shipped")


if __name__ == "__main__":
    unittest.main()
