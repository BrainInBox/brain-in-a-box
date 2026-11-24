"""The libraries the Linux AppImage leaves to the system, though linuxdeploy puts them in.

The AppImage is built on Ubuntu 22.04 and leaves EGL and Mesa to the system. Mesa's EGL loads
libwayland-client.so.0: when the AppImage carries its own (22.04's 1.20), that one is already in the
process and Mesa gets it. Mesa 26 (Ubuntu 26.04) needs three symbols wayland 1.20 does not have
(wl_display_create_queue_with_name, wl_display_dispatch_queue_timeout, wl_fixes_interface): Mesa's
EGL does not load, WebKit says « Could not create default EGL display: EGL_BAD_PARAMETER.
Aborting... » and the window stays empty (terrain, app-linux, app-chargement, from 2026-11-12).
`.github/ci/appimage-tools.sh trim` takes them out once Tauri has bundled the AppImage, `check`
refuses an AppImage that still carries one. Every workflow that builds the AppImage trims it before
it checks it.

    python -I -m unittest discover -s engine/tests -v
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "ci"
WORKFLOWS = [ROOT / ".github" / "workflows" / n for n in ("release.yml", "test.yml", "terrain.yml")]
TO_JSON = 'puts JSON.generate(YAML.safe_load(File.read(ARGV[0], encoding: "UTF-8")), ascii_only: true)'


def load():
    spec = importlib.util.spec_from_file_location("appimage_libs", CI / "appimage_libs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LibsLeftToTheSystem(unittest.TestCase):
    def setUp(self):
        self.al = load()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lib = self.tmp / "squashfs-root" / "usr" / "lib"
        self.lib.mkdir(parents=True)
        for name in ("libwayland-client.so.0", "libwayland-cursor.so.0", "libwayland-egl.so.1",
                     "libwebkit2gtk-4.1.so.0", "libgtk-3.so.0"):
            (self.lib / name).write_bytes(b"\x7fELF")

    def run_main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.al.main(list(argv))
        return code, out.getvalue()

    def test_wayland_client_is_the_systems(self):
        self.assertIn("libwayland-client.so.0", self.al.SYSTEM)

    def test_check_refuses_an_appdir_that_carries_wayland_client(self):
        code, said = self.run_main("check", str(self.tmp / "squashfs-root"))
        self.assertEqual(code, 1)
        self.assertIn("libwayland-client.so.0", said)
        self.assertIn("::error::", said)

    def test_trim_takes_it_out_and_keeps_the_rest(self):
        (self.lib / "x86_64-linux-gnu").mkdir()
        (self.lib / "x86_64-linux-gnu" / "libwayland-client.so.0.20.0").write_bytes(b"\x7fELF")
        os.symlink("x86_64-linux-gnu/libwayland-client.so.0.20.0", self.lib / "libwayland-client.so")
        code, _ = self.run_main("trim", str(self.tmp / "squashfs-root"))
        self.assertEqual(code, 0)
        left = sorted(p.relative_to(self.lib).as_posix() for p in self.lib.rglob("*") if not p.is_dir())
        self.assertEqual(left, ["libgtk-3.so.0", "libwayland-cursor.so.0", "libwayland-egl.so.1",
                                "libwebkit2gtk-4.1.so.0"])
        code, said = self.run_main("check", str(self.tmp / "squashfs-root"))
        self.assertEqual(code, 0, said)

    def test_a_folder_that_is_no_appdir_is_refused(self):
        code, said = self.run_main("check", str(self.tmp / "absent"))
        self.assertEqual(code, 1)
        self.assertIn("no usr/lib", said)


class EveryBuildTrimsBeforeItChecks(unittest.TestCase):
    """Each step that checks an AppImage built here trims it first (release.yml publishes it,
    test.yml and terrain.yml run what release.yml would publish)."""

    def steps(self, workflow):
        if not shutil.which("ruby"):
            self.skipTest("ruby not installed")
        data = json.loads(subprocess.run(["ruby", "-ryaml", "-rjson", "-e", TO_JSON, str(workflow)],
                                         capture_output=True, text=True, check=True).stdout)
        for job, spec in data["jobs"].items():
            for step in spec.get("steps", []):
                yield job, step.get("name", ""), step.get("run", "") or ""

    def test_each_check_follows_a_trim_of_the_same_image(self):
        seen = 0
        for workflow in WORKFLOWS:
            for job, name, run in self.steps(workflow):
                lines = run.splitlines()
                for i, line in enumerate(lines):
                    m = re.search(r'appimage-tools\.sh"? check (\S+)', line)
                    if not m:
                        continue
                    seen += 1
                    before = "\n".join(lines[:i])
                    self.assertRegex(before, r'appimage-tools\.sh"? trim ' + re.escape(m.group(1)),
                                     "%s, job %s, step « %s »: the AppImage is checked untrimmed"
                                     % (workflow.name, job, name))
        self.assertGreaterEqual(seen, 3)

    def test_the_tools_script_checks_and_trims_with_appimage_libs(self):
        text = (CI / "appimage-tools.sh").read_text(encoding="utf-8")
        self.assertIn('appimage_libs.py" check', text)
        self.assertIn('appimage_libs.py" trim', text)
        self.assertIn("LDAI_RUNTIME_FILE=", text)


if __name__ == "__main__":
    unittest.main()
