"""The programs the kit runs (claude, git, gbrain, the clipboard's, Windows' own) are never taken from
the current folder, nor from an empty or relative entry of the PATH: that is the folder a session, the
night's task or a terminal runs from (a cloned repository, a download), where anyone could have put a
claude.exe or a git.exe. shutil.which and a bare name given to subprocess look there first on Windows,
and on every system through such an entry. A folder named in full is searched as before.

    python3 -I -m unittest discover -s engine/tests -v
"""
import importlib.util
import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
HOOKS = ENGINE / "hooks"
sys.path.insert(0, str(HOOKS))
import _headless_claude as hc  # noqa: E402
import controle  # noqa: E402

WINDOWS = os.name == "nt"
EXE = ".exe" if WINDOWS else ""


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gu = load("gbrain_update_lookup", HOOKS / "gbrain_update.py")
gbq = load("gbq_lookup", ENGINE / "search" / "gbq.py")
nightly = load("windows_nightly_lookup", ENGINE / "nightly" / "windows-nightly.py")
dr = load("daily_reflection_lookup", HOOKS / "daily-reflection.py")
im = load("importer_memoire_lookup", HOOKS / "importer-memoire.py")


class Planted(unittest.TestCase):
    """The current folder holds a claude, a git, a gbrain... of someone else's, in it and in its bin/;
    the PATH names them only as ".", "bin" and an empty entry, then a folder of nothing."""

    NAMES = ("claude", "git", "gbrain", "xclip", "osascript", "taskkill")

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="biab-lookup-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.here = os.path.join(self.root, "here")
        self.ran = os.path.join(self.root, "ran")
        for d in (self.here, os.path.join(self.here, "bin")):
            os.makedirs(d)
            for name in self.NAMES:
                p = os.path.join(d, name + EXE)
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write("#!/bin/sh\necho %s >> '%s'\necho planted\n" % (name, self.ran))
                os.chmod(p, 0o755)
        nothing = os.path.join(self.root, "nothing")
        home = os.path.join(self.root, "home")
        for d in (nothing, home):
            os.makedirs(d)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.here)
        self.path = os.pathsep.join([".", "bin", "", nothing])
        env = {"PATH": self.path, "HOME": home, "USERPROFILE": home, "LOCALAPPDATA": os.path.join(home, "local"),
               "BUN_INSTALL": os.path.join(home, ".bun"), "SystemRoot": os.path.join(self.root, "Windows")}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)

    def planted(self, found):
        """Whether `found` (a path, or argv) is one of the planted programs."""
        if isinstance(found, list):
            found = found[0] if found else None
        return found is not None and os.path.abspath(found).startswith(self.here + os.sep)

    def runs(self):
        try:
            with open(self.ran, encoding="utf-8") as f:
                return f.read().split()
        except FileNotFoundError:
            return []

    def test_claude(self):
        with mock.patch.object(hc, "EXTRA_DIRS", (), create=True):
            found = hc.find_claude()
            self.assertIsNone(found, found)
            with self.assertRaises(hc.HeadlessError) as cm:
                hc._claude(None)
            self.assertEqual(cm.exception.kind, "start")
            # A folder named in full is searched.
            os.environ["PATH"] = os.pathsep.join(["bin", os.path.join(self.here, "bin")])
            self.assertEqual(hc.find_claude(), [os.path.join(self.here, "bin", "claude" + EXE)])

    def test_the_git_claude_code_is_given_on_windows(self):
        with mock.patch.object(hc, "WINDOWS", True):
            dirs = hc.child_path(os.path.join(self.root, "x", "claude.exe")).split(os.pathsep)
        self.assertFalse([d for d in dirs if d in ("", ".", "bin") or os.path.abspath(d).startswith(self.here + os.sep)
                          or os.path.abspath(d) == self.here], dirs)

    def test_git_of_the_night_check_and_the_gbrain_update(self):
        self.assertIsNone(controle.find_git(SimpleNamespace(git=None)))
        self.assertIsNone(gu.Updater(gu.parse_args([])).git)
        self.assertIsNone(nightly.find_git())
        os.environ["PATH"] = os.path.join(self.here, "bin")
        git = os.path.join(self.here, "bin", "git" + EXE)
        self.assertEqual((controle.find_git(SimpleNamespace(git=None)), gu.Updater(gu.parse_args([])).git,
                          nightly.find_git()), (git, git, git))

    def test_gbrain_for_gbq(self):
        self.assertIsNone(gbq.gbrain_exe())

    def test_windows_own_programs_by_their_full_path(self):
        system32 = os.path.join(self.root, "Windows", "System32")
        self.assertEqual(gu.system_tool("taskkill.exe"), os.path.join(system32, "taskkill.exe"))
        for name, module in (("gbq", gbq), ("windows-nightly", nightly)):
            with self.subTest(name), mock.patch.object(module, "WINDOWS", True, create=True), \
                    mock.patch.object(module.subprocess, "run") as run:
                if module is gbq:
                    module.kill_tree(mock.Mock(pid=4242))
                else:
                    module.kill_tree(4242)
                self.assertEqual(run.call_args[0][0][0], os.path.join(system32, "taskkill.exe"))

    @unittest.skipIf(WINDOWS, "a folder named \\tools there is the root of the current drive")
    def test_on_windows_a_folder_is_named_in_full_with_its_drive(self):
        # Windows' os.path.isabs up to Python 3.12 (install.ps1's): \tools and /opt/homebrew/bin are absolute,
        # though they are folders of whatever drive is current, where a standard account may create C:\opt. Here,
        # Windows as the lookups see it: that isabs, its PATH separator, \tools a folder of the current one, a
        # Unix folder for Homebrew's; a folder with its drive (C:\tools) is searched.
        def isabs_312(s):
            s = os.fspath(s)[:3].replace("/", "\\")
            return s.startswith("\\") or s[1:3] == ":\\"

        unix = os.path.join(self.root, "opt", "bin")
        for d in ("\\tools", unix, "C:\\tools"):
            os.makedirs(d)
            for name in ("claude.exe", "git", "gbrain"):
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(os.path.join(d, name), 0o755)
        modules = (hc, gu, gbq, nightly)

        def lookups(path):
            os.environ["PATH"] = path
            with mock.patch("os.path.isabs", isabs_312), mock.patch("os.pathsep", ";"), \
                    mock.patch.object(hc, "EXTRA_DIRS", (unix,)):
                patches = [mock.patch.object(m, "WINDOWS", True, create=True) for m in modules]
                for p in patches:
                    p.start()
                try:
                    return (hc.find_claude(), controle.find_git(SimpleNamespace(git=None)), gu.which("git"),
                            nightly.find_git(), gbq.gbrain_exe())
                finally:
                    for p in patches:
                        p.stop()

        self.assertEqual(lookups("\\tools;" + unix), (None,) * 5)
        tools = os.path.join("C:\\tools", "")
        self.assertEqual(lookups("C:\\tools"), ([tools + "claude.exe"], tools + "git", tools + "git", tools + "git",
                                                tools + "gbrain"))
        for d in ("\\tools", "/opt/homebrew/bin", "C:tools", "", "bin"):
            with mock.patch.object(hc, "WINDOWS", True):
                self.assertFalse(hc.absolute_folder(d), d)
        for d in ("C:\\tools", "c:/tools", "\\\\server\\share\\bin"):
            with mock.patch.object(hc, "WINDOWS", True):
                self.assertTrue(hc.absolute_folder(d), d)

    @unittest.skipIf(WINDOWS, "the planted programs are shell scripts")
    def test_nothing_planted_runs(self):
        self.assertEqual(dr._git("--version", cwd=self.here), "")
        self.assertEqual(dr._git("--version"), "")
        with mock.patch.object(im.sys, "platform", "linux"), self.assertRaises(im.Erreur):
            im.presse_papiers()
        self.assertEqual(self.runs(), [])

    @unittest.skipUnless(sys.platform == "darwin", "macOS's notification")
    def test_the_reflection_s_notification(self):
        os.environ.pop("BRAIN_NO_NOTIFY", None)
        with mock.patch.object(dr.subprocess, "run") as run:
            dr.notify("x")
        self.assertEqual(run.call_args[0][0][0], "/usr/bin/osascript")


if __name__ == "__main__":
    unittest.main()
