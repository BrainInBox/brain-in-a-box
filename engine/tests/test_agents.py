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
import unittest
from pathlib import Path

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

    def put(self, d):
        self.p.parent.mkdir(parents=True, exist_ok=True)
        self.p.write_bytes(d if isinstance(d, bytes) else (d if isinstance(d, str) else json.dumps(d)).encode("utf-8"))

    def lire(self, ouvert=None):
        return ag.lire(self.home, codex_ouvert=ouvert)

    def choisir(self, agents="", prefere="", ouvert=None, env=None):
        return ag.choisir(agents, prefere, self.lire(ouvert), codex_ouvert=ouvert, env=env or {}, home=self.home)

    def run_cli(self, *args, env=None):
        e = {k: v for k, v in os.environ.items() if k not in ("CODEX_HOME", "BIAB_AGENTS", "BIAB_PREFERE")}
        e.update(HOME=self.home, USERPROFILE=self.home, **(env or {}))
        p = subprocess.run([sys.executable, "-I", str(HOOKS / "_agents.py"), "choisir", *args],
                           env=e, capture_output=True)
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

    def test_untrusted_files_fall_back_to_claude_and_say_why(self):
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
                    self.assertEqual(self.lire(TOUT), (ag.DEFAUT, etat))

    def test_a_folder_in_its_place_is_unreadable(self):
        self.p.mkdir(parents=True)
        self.assertEqual(self.lire(), (ag.DEFAUT, "illisible"))

    def test_codex_listed_on_a_closed_system_is_not_trusted(self):
        self.put({"version": 1, "agents": ["claude", "codex"], "prefere": "claude", "codex_home": self.home})
        self.assertEqual(self.lire(), (ag.DEFAUT, "ferme"))
        self.assertEqual(self.lire(TOUT)[1], "ok")


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

    def test_codex_home_is_not_read_without_codex(self):
        code, out, _ = self.run_cli("--agents=claude", "--pose-par=install.sh", env={"CODEX_HOME": "rel"})
        self.assertEqual((code, out), (0, "agents=claude prefere=claude source=choix ecrit=oui"))
        self.assertNotIn("codex_home", json.loads(self.p.read_text(encoding="utf-8")))

    def test_only_the_installers_put_it(self):
        code, out, err = self.run_cli("--agents=claude", "--pose-par=bibo")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--pose-par", err)
        self.assertFalse(os.path.lexists(self.p.parent))


if __name__ == "__main__":
    unittest.main()
