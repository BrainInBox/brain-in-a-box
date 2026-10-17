"""What .github/workflows/release.yml must keep true, since its publishing part only ever
runs on master, after the merge:

- the build that publishes restores no cache: a cache is shared with every job of the
  default branch, so a poisoned one would end up in a preview signed with the repo's name;
- every action is pinned to a commit, a tag can be moved;
- only the publishing job can write, and it attests what it publishes before publishing it;
- a fork's pull request leaves no installer behind in the repository's Actions tab.

The workflow is read with Ruby's YAML (macOS ships it, as for test-hooks.sh).

    python -I -m unittest discover -s engine/tests -v
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
TO_JSON = 'puts JSON.generate(YAML.safe_load(File.read(ARGV[0], encoding: "UTF-8")), ascii_only: true)'
PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")


def load():
    ruby = shutil.which("ruby")
    if not ruby:
        if sys.platform == "darwin":
            raise AssertionError("no ruby on this Mac: the release workflow cannot be read")
        raise unittest.SkipTest("no ruby here to read YAML (the macOS run checks it)")
    p = subprocess.run([ruby, "-ryaml", "-rjson", "-e", TO_JSON, str(WORKFLOW)],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode:
        raise AssertionError(f"release.yml does not parse: {p.stderr}")
    return json.loads(p.stdout)


class ReleaseWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = load()
        cls.jobs = cls.wf["jobs"]

    def steps(self, job):
        return self.jobs[job]["steps"]

    def using(self, job, action):
        return [s for s in self.steps(job) if s.get("uses", "").startswith(action + "@")]

    def test_no_trigger_runs_with_the_base_repository_rights(self):
        # YAML 1.1 reads the key "on" as true.
        triggers = self.wf.get("on", self.wf.get("true"))
        self.assertNotIn("pull_request_target", triggers)
        self.assertNotIn("workflow_run", triggers)

    def test_a_change_to_a_file_the_app_carries_publishes_it(self):
        # The app is built with Codex's gate (engine/hooks/_agents.py, agents.rs): opening Codex on a
        # system publishes the app that offers it, and the night checks the preview holds that change.
        triggers = self.wf.get("on", self.wf.get("true"))
        for event in ("pull_request", "push"):
            self.assertIn("engine/hooks/_agents.py", triggers[event]["paths"], event)
        app = WORKFLOW.parents[2] / "app" / "src-tauri" / "src" / "agents.rs"
        self.assertIn('include_str!("../../../engine/hooks/_agents.py")', app.read_text(encoding="utf-8"))
        terrain_app = (WORKFLOW.parents[2] / "engine" / "tests" / "terrain_app.py").read_text(encoding="utf-8")
        published = re.search(r"^PUBLIE = \((.*)\)$", terrain_app, re.M).group(1)
        for path in triggers["push"]["paths"]:
            self.assertIn('"%s"' % path.replace("/**", ""), published, path)

    def test_every_action_is_pinned_to_a_commit(self):
        for name, job in self.jobs.items():
            for step in job["steps"]:
                if "uses" in step:
                    with self.subTest(job=name, uses=step["uses"]):
                        self.assertRegex(step["uses"], PINNED)

    def test_the_build_that_publishes_restores_no_cache(self):
        caches = self.using("build", "actions/cache")
        self.assertTrue(caches, "no cache step found: update this test")
        for step in caches:
            with self.subTest(step=step.get("name")):
                self.assertEqual(step.get("if"), "needs.plan.outputs.publish != 'true'")
        # Without its cache, the pinned Tauri CLI is built from crates.io.
        cli = next(s for s in caches if s.get("id") == "tauri-cli")
        install = next(s for s in self.steps("build") if "cargo install tauri-cli" in s.get("run", ""))
        self.assertEqual(install.get("if"), "steps.tauri-cli.outputs.cache-hit != 'true'")
        self.assertIn("--locked", install["run"])
        self.assertIn("cargo-tauri", cli["with"]["path"])

    def test_only_the_publishing_job_can_write(self):
        self.assertEqual(self.wf["permissions"], {"contents": "read"})
        for name, job in self.jobs.items():
            if name == "publish":
                continue
            with self.subTest(job=name):
                for scope, level in (job.get("permissions") or {}).items():
                    self.assertIn(level, ("read", "none"), scope)
        publish = self.jobs["publish"]
        self.assertEqual(publish["permissions"],
                         {"contents": "write", "id-token": "write", "attestations": "write"})
        self.assertIn("needs.plan.outputs.publish == 'true'", publish["if"])
        # It runs no code of the repository: no checkout.
        self.assertFalse(self.using("publish", "actions/checkout"))

    def test_what_is_published_is_attested_first(self):
        steps = self.steps("publish")
        attest = [i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/attest@")]
        create = [i for i, s in enumerate(steps) if "gh release create" in s.get("run", "")]
        sums = [i for i, s in enumerate(steps) if "> SHA256SUMS" in s.get("run", "")]
        self.assertEqual((len(attest), len(create), len(sums)), (1, 1, 1))
        self.assertLess(sums[0], attest[0])
        self.assertLess(attest[0], create[0])
        self.assertEqual(steps[attest[0]]["with"], {"subject-checksums": "dist/SHA256SUMS"})

    def test_a_fork_pull_request_uploads_nothing(self):
        uploads = self.using("build", "actions/upload-artifact")
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].get("if"), "needs.plan.outputs.upload == 'true'")
        plan_env = next(s for s in self.steps("plan") if s.get("id") == "plan")["env"]
        self.assertEqual(plan_env["HEAD_REPO"], "${{ github.event.pull_request.head.repo.full_name }}")
        self.assertEqual(plan_env["REPOSITORY"], "${{ github.repository }}")
        self.assertEqual(plan_env["EVENT_NAME"], "${{ github.event_name }}")
        self.assertIn("upload", self.jobs["plan"]["outputs"])


if __name__ == "__main__":
    unittest.main()
