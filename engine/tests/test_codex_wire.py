"""Tests of engine/codex/wire.py for install.ps1 (--ps1): the hooks in the form Codex runs on Windows,
& '<python.exe>' '<hook>' --agent codex, approved under the command Codex runs there, its lines in
French. install.sh's side (no --ps1) is test-hooks.sh's, section 15bis; install.ps1's functions are
.github/ci/install-ps1-codex.ps1's. On Windows, the command is run the way Codex 0.162.1 runs a hook:
the session's shell, -NoProfile -Command (step 0), with a path PowerShell would otherwise read.

    python3 -I -m unittest discover -s engine/tests -v
"""
import hashlib
import json
import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WIRE = os.path.join(os.path.dirname(HERE), "codex", "wire.py")
WINDOWS = os.name == "nt"
HOOKS6 = {"UserPromptSubmit": ["correction-detector.py"], "Stop": ["session-logger.py", "session-indexer.py", "session-recap.py"],
          "SessionStart": ["gbrain-update-check.py", "controle-sessionstart.py"]}
LABEL = {"UserPromptSubmit": "user_prompt_submit", "Stop": "stop", "SessionStart": "session_start"}


def ps_quote(s):
    return "'" + "".join(c * 2 if c in "'\u2018\u2019\u201a\u201b" else c for c in s) + "'"


class Ps1(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="codex-wire-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, ".codex")
        self.hooks = os.path.join(self.tmp, "O'Brien $HOME `x \u2019q\u2019 \u00e9", "hooks")
        self.py = os.path.join(self.tmp, "Python 3.12", "python.exe")
        os.makedirs(self.home)

    def wire(self, *args):
        env = dict(os.environ, HOME=self.tmp, USERPROFILE=self.tmp)
        p = subprocess.run([sys.executable, "-I", WIRE, "--ps1", self.py] + list(args), capture_output=True, env=env)
        return p.returncode, p.stdout.decode("utf-8")

    def command(self, name):
        return "& %s %s --agent codex" % (ps_quote(self.py), ps_quote(ntpath.join(self.hooks, name)))

    def test_the_hooks_in_the_form_codex_runs_on_windows(self):
        code, out = self.wire("merge", self.home, self.hooks)
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"^~[\\/]\.codex[\\/]hooks\.json : hooks du kit ajoutes\n$")
        with open(os.path.join(self.home, "hooks.json"), encoding="utf-8") as f:
            d = json.load(f)
        for evt, names in HOOKS6.items():
            self.assertEqual(d["hooks"][evt], [{"hooks": [{"type": "command", "command": self.command(n), "commandWindows": self.command(n),
                                                           "timeout": 30} for n in names]}])
        written = d["hooks"]["UserPromptSubmit"][0]["hooks"][0]["commandWindows"]
        self.assertIn("O''Brien $HOME `x \u2019\u2019q\u2019\u2019 \u00e9", written, "every quote PowerShell reads as one, doubled")

    def test_approved_under_the_command_codex_runs_there(self):
        self.wire("merge", self.home, self.hooks)
        code, out = self.wire("approve", self.home, self.hooks)
        self.assertEqual((code, out.split("\n")[:2]), (0, ["written", "6"]), out)
        with open(os.path.join(self.home, "config.toml"), encoding="utf-8") as f:
            toml = f.read()
        self.assertTrue(toml.startswith("# brain-in-a-box: the kit's Codex hooks, approved for you by install.ps1 "), toml[:120])
        for evt, names in HOOKS6.items():
            for i, n in enumerate(names):
                ident = {"event_name": LABEL[evt], "hooks": [{"async": False, "command": self.command(n), "timeout": 30, "type": "command"}]}
                digest = "sha256:" + hashlib.sha256(json.dumps(ident, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                                                    .encode("utf-8")).hexdigest()
                key = json.dumps(ntpath.join(self.home, "hooks.json") + ":%s:0:%d" % (LABEL[evt], i), ensure_ascii=False)
                self.assertIn('[hooks.state.%s]\ntrusted_hash = "%s"\n' % (key, digest), toml)

    def test_an_entry_of_the_kit_s_the_user_changed_is_recognised_windows_style(self):
        mine = {"type": "command", "command": "py C:\\X\\.CLAUDE\\Hooks\\Brain\\session-logger.py --agent codex"}
        with open(os.path.join(self.home, "hooks.json"), "w", encoding="utf-8") as f:
            json.dump({"hooks": {"Stop": [{"hooks": [mine]}]}}, f)
        self.wire("merge", self.home, self.hooks)
        with open(os.path.join(self.home, "hooks.json"), encoding="utf-8") as f:
            stop = json.load(f)["hooks"]["Stop"]
        self.assertEqual(stop, [{"hooks": [mine] + [{"type": "command", "command": self.command(n), "commandWindows": self.command(n),
                                                     "timeout": 30} for n in ("session-indexer.py", "session-recap.py")]}])

    def test_an_entry_that_names_the_hook_in_its_windows_command_only(self):
        # Codex runs commandWindows (or command_windows) on Windows: the hook is there, never added twice.
        for key in ("commandWindows", "command_windows"):
            with self.subTest(key=key):
                mine = {"type": "command", "command": "echo mine", key: "& py 'C:\\X\\.claude\\hooks\\brain\\session-logger.py' --agent codex"}
                with open(os.path.join(self.home, "hooks.json"), "w", encoding="utf-8") as f:
                    json.dump({"hooks": {"Stop": [{"hooks": [mine]}]}}, f)
                self.wire("merge", self.home, self.hooks)
                with open(os.path.join(self.home, "hooks.json"), encoding="utf-8") as f:
                    stop = json.load(f)["hooks"]["Stop"]
                self.assertEqual(stop, [{"hooks": [mine] + [{"type": "command", "command": self.command(n), "commandWindows": self.command(n),
                                                             "timeout": 30} for n in ("session-indexer.py", "session-recap.py")]}])

    def test_its_lines_in_french(self):
        with open(os.path.join(self.home, "hooks.json"), "w", encoding="utf-8") as f:
            f.write("{,}")
        code, out = self.wire("check", self.home, self.hooks)
        self.assertEqual(code, 1)
        self.assertIn("n'est pas du JSON valide (ligne 1, colonne 2 : ", out)
        self.assertIn("Laisse tel quel : corrige-le, puis relance la meme commande d'installation.", out)
        self.assertNotIn("re-run ./install.sh", out)


@unittest.skipUnless(WINDOWS, "PowerShell, as Codex runs a hook on Windows")
class Ps1RunsInPowerShell(unittest.TestCase):
    def test_the_hook_gets_its_path_whole_and_codex_s_input(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="codex-wire-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        home, hooks = os.path.join(tmp, ".codex"), os.path.join(tmp, "O'Brien $HOME `x \u2019q\u2019 \u00e9 & ;", "hooks")
        os.makedirs(home)
        os.makedirs(hooks)
        seen = os.path.join(tmp, "seen.json")
        with open(os.path.join(hooks, "correction-detector.py"), "w", encoding="utf-8") as f:
            f.write("import json, sys\njson.dump({'argv': sys.argv, 'stdin': sys.stdin.buffer.read().decode('utf-8')}, "
                    "open(%r, 'w', encoding='utf-8'))\nprint('{}')\n" % seen)
        p = subprocess.run([sys.executable, "-I", WIRE, "--ps1", sys.executable, "merge", home, hooks], capture_output=True)
        self.assertEqual(p.returncode, 0, p)
        with open(os.path.join(home, "hooks.json"), encoding="utf-8") as f:
            cmd = json.load(f)["hooks"]["UserPromptSubmit"][0]["hooks"][0]["commandWindows"]
        shells = [s for s in (shutil.which("pwsh.exe"), shutil.which("powershell.exe")) if s]
        self.assertTrue(shells)
        for shell in shells:
            with self.subTest(shell=shell):
                if os.path.exists(seen):
                    os.remove(seen)
                r = subprocess.run([shell, "-NoProfile", "-Command", cmd], input='{"prompt": "\u00e9"}'.encode("utf-8"),
                                   capture_output=True, timeout=120)
                self.assertEqual(r.returncode, 0, r)
                with open(seen, encoding="utf-8") as f:
                    got = json.load(f)
                self.assertEqual(got["argv"], [os.path.join(hooks, "correction-detector.py"), "--agent", "codex"])
                self.assertEqual(got["stdin"], '{"prompt": "\u00e9"}')


if __name__ == "__main__":
    unittest.main()
