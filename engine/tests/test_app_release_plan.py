"""Tests for .github/ci/app-release-plan.py: what the app release workflow builds and
when Windows and Linux join the matrix.

    python -I -m unittest discover -s engine/tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "ci" / "app-release-plan.py"
REPO_CONF = Path(__file__).resolve().parents[2] / "app" / "src-tauri" / "tauri.conf.json"


class Plan(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "app" / "src-tauri").mkdir(parents=True)

    def conf(self, name, data):
        (self.root / "app" / "src-tauri" / name).write_text(json.dumps(data), encoding="utf-8")

    def plan(self, publish="false", run="42", **github):
        names = ("PUBLISH", "GITHUB_RUN_NUMBER", "EVENT_NAME", "HEAD_REPO", "REPOSITORY")
        env = {k: v for k, v in os.environ.items() if k not in names}
        env.update(PUBLISH=publish, GITHUB_RUN_NUMBER=run, EVENT_NAME="push", HEAD_REPO="",
                   REPOSITORY="BrainInBox/brain-in-a-box")
        env.update(github)
        p = subprocess.run([sys.executable, "-I", str(SCRIPT)], cwd=self.root, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        if p.returncode:
            return None, p.stderr
        out = dict(line.split("=", 1) for line in p.stdout.splitlines())
        out["matrix"] = json.loads(out["matrix"])
        return out, p.stderr

    def platforms(self, out):
        return [e["platform"] for e in out["matrix"]["include"]]

    def test_the_repository_config_builds_macos(self):
        self.conf("tauri.conf.json", json.loads(REPO_CONF.read_text(encoding="utf-8")))
        out, err = self.plan()
        self.assertIsNotNone(out, err)
        self.assertIn("macos", self.platforms(out))

    def test_macos_only_while_the_config_has_no_windows_installer(self):
        self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": ["app", "dmg"]}})
        out, err = self.plan()
        self.assertEqual(self.platforms(out), ["macos"], err)
        mac = out["matrix"]["include"][0]
        self.assertEqual((mac["target"], mac["bundles"]), ("universal-apple-darwin", "dmg"))

    def test_windows_joins_when_the_windows_config_declares_nsis(self):
        self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": ["app", "dmg"]}})
        self.conf("tauri.windows.conf.json", {"bundle": {"targets": ["nsis"]}})
        out, err = self.plan()
        self.assertEqual(self.platforms(out), ["macos", "windows"], err)
        win = out["matrix"]["include"][1]
        self.assertEqual((win["os"], win["bundles"]), ("windows-latest", "nsis"))

    def test_windows_joins_when_the_main_config_declares_nsis_or_all(self):
        for targets in (["app", "dmg", "nsis"], "all", "nsis"):
            with self.subTest(targets=targets):
                self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": targets}})
                out, err = self.plan()
                self.assertEqual(self.platforms(out)[:2], ["macos", "windows"], err)

    def test_a_windows_config_without_nsis_keeps_windows_out(self):
        self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": "all"}})
        self.conf("tauri.windows.conf.json", {"bundle": {"targets": ["msi"]}})
        self.conf("tauri.linux.conf.json", {"bundle": {"targets": ["rpm"]}})
        out, err = self.plan()
        self.assertEqual(self.platforms(out), ["macos"], err)

    def test_the_repository_config_builds_all_three(self):
        for name in ("tauri.conf.json", "tauri.windows.conf.json", "tauri.linux.conf.json"):
            self.conf(name, json.loads((REPO_CONF.parent / name).read_text(encoding="utf-8")))
        out, err = self.plan()
        self.assertEqual(self.platforms(out), ["macos", "windows", "linux"], err)

    def test_linux_joins_with_an_appimage_and_a_deb_built_on_ubuntu_22_04(self):
        self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": ["app", "dmg"]}})
        self.conf("tauri.linux.conf.json", {"bundle": {"targets": ["deb", "appimage"]}})
        out, err = self.plan()
        self.assertEqual(self.platforms(out), ["macos", "linux"], err)
        linux = out["matrix"]["include"][1]
        # Built on the oldest Ubuntu it runs on: an AppImage runs where its glibc is as recent.
        self.assertEqual((linux["os"], linux["target"], linux["bundles"]),
                         ("ubuntu-22.04", "x86_64-unknown-linux-gnu", "deb,appimage"))

    def test_linux_needs_both_its_packages(self):
        for targets in (["deb"], ["appimage"], ["rpm", "deb"]):
            with self.subTest(targets=targets):
                self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": ["app", "dmg"]}})
                self.conf("tauri.linux.conf.json", {"bundle": {"targets": targets}})
                out, err = self.plan()
                self.assertNotIn("linux", self.platforms(out), err)
        self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": "all"}})
        (self.root / "app" / "src-tauri" / "tauri.linux.conf.json").unlink()
        out, err = self.plan()
        self.assertIn("linux", self.platforms(out), err)

    def test_a_windows_config_without_targets_inherits_the_main_ones(self):
        self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": ["app", "dmg"]}})
        self.conf("tauri.windows.conf.json", {"bundle": {"windows": {"nsis": {}}}})
        out, err = self.plan()
        self.assertEqual(self.platforms(out), ["macos"], err)

    def test_label_says_preview_only_when_the_run_publishes(self):
        self.conf("tauri.conf.json", {"version": "1.2.3", "bundle": {"targets": ["dmg"]}})
        out, _ = self.plan(publish="true", run="17")
        self.assertEqual((out["version"], out["label"], out["publish"]), ("1.2.17", "1.2.17-preview.17", "true"))
        out, _ = self.plan(publish="false", run="18")
        self.assertEqual((out["label"], out["publish"]), ("1.2.18-ci.18", "false"))

    def test_each_build_is_newer_than_the_last(self):
        # The run number is the patch: Windows' installer then updates the app in place, and the
        # version installed says which preview it is.
        self.conf("tauri.conf.json", {"version": "0.1.0", "bundle": {"targets": ["dmg"]}})
        versions = [self.plan(publish="true", run=run)[0]["version"] for run in ("9", "10", "101")]
        self.assertEqual(versions, ["0.1.9", "0.1.10", "0.1.101"])

    def test_a_version_that_would_make_a_strange_tag_is_refused(self):
        for version in ("1.2", "1.2.3-beta", "1.2.3; rm -rf /", ""):
            with self.subTest(version=version):
                self.conf("tauri.conf.json", {"version": version})
                out, err = self.plan()
                self.assertIsNone(out)
                self.assertIn("MAJOR.MINOR.PATCH", err)

    def test_a_pull_request_from_a_fork_leaves_no_installer_to_download(self):
        # Its build would sit in this repository's Actions tab under the app's own name.
        self.conf("tauri.conf.json", {"version": "0.1.0"})
        cases = [
            ({"EVENT_NAME": "push"}, "true"),
            ({"EVENT_NAME": "workflow_dispatch"}, "true"),
            ({"EVENT_NAME": "pull_request", "HEAD_REPO": "BrainInBox/brain-in-a-box"}, "true"),
            ({"EVENT_NAME": "pull_request", "HEAD_REPO": "someone/brain-in-a-box"}, "false"),
            ({"EVENT_NAME": "pull_request", "HEAD_REPO": ""}, "false"),  # fork deleted since
        ]
        for github, upload in cases:
            with self.subTest(**github):
                out, err = self.plan(**github)
                self.assertEqual(out["upload"], upload, err)

    def test_a_run_number_that_is_not_a_number_is_refused(self):
        self.conf("tauri.conf.json", {"version": "0.1.0"})
        out, err = self.plan(run="1 2")
        self.assertIsNone(out)
        self.assertIn("GITHUB_RUN_NUMBER", err)


if __name__ == "__main__":
    unittest.main()
