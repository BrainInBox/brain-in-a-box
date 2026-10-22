"""What .github/workflows/release.yml must keep true, since its publishing part only ever
runs on master, after the merge:

- the build that publishes restores no cache: a cache is shared with every job of the
  default branch, so a poisoned one would end up in a preview signed with the repo's name;
- every action is pinned to a commit, a tag can be moved;
- only the publishing job can write, and it attests what it publishes before publishing it;
- a fork's pull request leaves no installer behind in the repository's Actions tab;
- each build gets its own version, the run number, given to Tauri;
- app-latest holds the preview's files under names without their version, the site's buttons
  point there, and it is the only tag ever moved;
- signing (SignPath for Windows, Developer ID for Apple) only runs for the published preview,
  once its secrets exist; without them nothing changes. The app compiles without any secret (each
  dependency's build.rs runs then); the release notes say "unsigned" only where it is;
- app-latest swaps its files only once every new one is up, and the job checks what the site's
  buttons then download.

And what goes with it: the Windows installer (Tauri's NSIS template, our words, pictures and
uninstall hook, app/src-tauri/installer/), the DMG's window, and the download site (site/), whose
buttons are the files published here.

The workflow is read with Ruby's YAML (macOS ships it, as for test-hooks.sh).

    python -I -m unittest discover -s engine/tests -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TAURI = ROOT / "app" / "src-tauri"
INSTALLER = TAURI / "installer"
SITE = ROOT / "site"
TO_JSON = 'puts JSON.generate(YAML.safe_load(File.read(ARGV[0], encoding: "UTF-8")), ascii_only: true)'
PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")


def load(path=WORKFLOW):
    ruby = shutil.which("ruby")
    if not ruby:
        if sys.platform == "darwin":
            raise AssertionError("no ruby on this Mac: the release workflow cannot be read")
        raise unittest.SkipTest("no ruby here to read YAML (the macOS run checks it)")
    p = subprocess.run([ruby, "-ryaml", "-rjson", "-e", TO_JSON, str(path)],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode:
        raise AssertionError(f"{path.name} does not parse: {p.stderr}")
    return json.loads(p.stdout)


def step(job, text):
    """The step of job whose run (or uses) holds text."""
    found = [s for s in job["steps"] if text in s.get("run", "") or text in s.get("uses", "")]
    if len(found) != 1:
        raise AssertionError("%d steps hold %r" % (len(found), text))
    return found[0]


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
        self.assertEqual((len(attest), len(create), len(sums)), (1, 2, 2))
        self.assertLess(sums[0], attest[0])
        # The preview, then app-latest (its files, renamed: their digests are the ones attested).
        self.assertLess(attest[0], create[0])
        self.assertIn('gh release create "$TAG" dist/*', steps[create[0]]["run"])
        self.assertEqual(create[1], sums[1])
        self.assertEqual(steps[create[1]]["name"], "The stable links (app-latest)")
        self.assertEqual(steps[attest[0]]["with"], {"subject-checksums": "dist/SHA256SUMS"})

    def test_each_build_is_given_its_version(self):
        # The plan's version (MAJOR.MINOR, then the run number) goes to Tauri before the build: the
        # Windows installer then updates in place, and the app says which preview it is.
        build = self.jobs["build"]
        self.assertEqual(build["env"]["VERSION"], "${{ needs.plan.outputs.version }}")
        self.assertEqual(self.jobs["plan"]["outputs"]["version"], "${{ steps.plan.outputs.version }}")
        runs = [s["run"] for s in build["steps"] if "cargo tauri b" in s.get("run", "")]
        self.assertEqual(len(runs), 3, "the build, its bundle, and the bundle of the signed app")
        for run in runs:
            for line in run.splitlines():
                if "cargo tauri b" in line:
                    with self.subTest(line=line.strip()):
                        self.assertIn('--config "{\\"version\\":\\"$VERSION\\"}"', line)

    def test_app_latest_holds_the_previews_files_under_fixed_names(self):
        steps = self.steps("publish")
        names = [s.get("name", "") for s in steps]
        latest = names.index("The stable links (app-latest)")
        create = next(i for i, s in enumerate(steps) if 'gh release create "$TAG"' in s.get("run", ""))
        prune = names.index("Keep the 5 newest app previews")
        self.assertLess(create, latest)
        self.assertLess(latest, prune)
        run = steps[latest]["run"]
        # The files of the preview, renamed: Brain-in-a-Box_<LABEL>_windows-x64-setup.exe becomes
        # Brain-in-a-Box_windows-x64-setup.exe; never GitHub's "Latest" release (the kit's).
        self.assertIn('for f in dist/Brain-in-a-Box_"$LABEL"_*; do', run)
        self.assertIn('cp "$f" "latest/Brain-in-a-Box_${f#dist/Brain-in-a-Box_"$LABEL"_}"', run)
        for line in run.splitlines():
            if "gh release create" in line or "gh release edit" in line:
                with self.subTest(line=line.strip()):
                    self.assertIn("--prerelease", line)
                    self.assertIn("app-latest", line)
        # app-latest is the only tag moved; the pruning never takes it.
        moves = [s for s in steps if "git/refs/tags/" in s.get("run", "")]
        self.assertEqual(moves, [steps[latest]])
        self.assertIn('"repos/$GH_REPO/git/refs/tags/app-latest"', run)
        # Every new file goes up before any old one goes: the links mix two previews only for the
        # seconds of the renames, and each rename is tried again before the step gives up.
        self.assertEqual(run.count("gh release upload"), 1)
        self.assertIn("gh release upload app-latest next/* --clobber", run)
        self.assertLess(run.index("gh release upload"), run.index("-X DELETE"))
        self.assertRegex(run, r'for try in 1 2 3; do\s+gh api -X PATCH "repos/\$GH_REPO/releases/assets/\$new" -f name="\$name"')
        # Then what the site downloads, whichever way app-latest was made: each link answers, and
        # the SHA256SUMS it serves is this preview's.
        self.assertIn('base="$GITHUB_SERVER_URL/$GH_REPO/releases/download/app-latest"', run)
        check = run[run.rindex("\nfi\n"):]
        self.assertIn('-o /dev/null -I "$base/${f#latest/}"', check)
        self.assertIn('"$base/SHA256SUMS" | cmp - latest/SHA256SUMS', check)
        pattern = re.search(r'test\("([^"]+)"\)', steps[prune]["run"]).group(1).replace("\\\\", "\\")
        self.assertRegex("app-v0.1.112-preview.112", pattern)
        self.assertNotRegex("app-latest", pattern)

    def test_signing_runs_only_for_the_published_preview_with_its_secrets(self):
        build = self.jobs["build"]
        self.assertEqual(build["env"]["SIGNPATH"],
                         "${{ needs.plan.outputs.publish == 'true' && secrets.SIGNPATH_API_TOKEN != '' "
                         "&& vars.SIGNPATH_ORGANIZATION_ID != '' }}")
        # (The step that only says whether the files are signed, for the release notes, aside.)
        signpath = [s for s in build["steps"] if s.get("id") != "signed"
                    and ("SIGNPATH" in json.dumps(s) or "signpath" in json.dumps(s))]
        self.assertEqual(len(signpath), 7)
        for s in signpath:
            with self.subTest(step=s.get("name") or s.get("uses")):
                self.assertEqual(s.get("if"), "runner.os == 'Windows' && env.SIGNPATH == 'true'")
        submits = self.using("build", "signpath/github-action-submit-signing-request")
        self.assertEqual(len(submits), 2, "the app, then its installer")
        # The secret only goes to SignPath's action, never to a script.
        for s in build["steps"]:
            text = json.dumps(s)
            if "SIGNPATH_API_TOKEN" in text and s not in submits:
                self.fail("SIGNPATH_API_TOKEN outside SignPath's action: %s" % (s.get("name"),))
        for s in submits:
            self.assertEqual(s["with"]["api-token"], "${{ secrets.SIGNPATH_API_TOKEN }}")
            self.assertTrue(s["with"]["github-artifact-id"].startswith("${{ steps.unsigned-"))
        # Apple: only the bundling reads the secrets, only for the published preview, on macOS.
        # Elsewhere, only whether one is set.
        for name, s in [(s.get("name", ""), s) for s in build["steps"]]:
            for key, value in (s.get("env") or {}).items():
                if "secrets.APPLE" in str(value):
                    with self.subTest(step=name, var=key):
                        if name.startswith("Bundle ("):
                            self.assertRegex(value, r"^\$\{\{ runner\.os == 'macOS' && needs\.plan\.outputs\.publish == 'true' "
                                                    r"&& secrets\.APPLE_[A-Z_]+ \|\| '' \}\}$")
                        else:
                            self.assertRegex(value, r"^\$\{\{ needs\.plan\.outputs\.publish == 'true' && secrets\.APPLE_[A-Z_]+ != '' \}\}$")
        # Without the secrets, the steps that build and check run as before.
        for key in ("Build (", "Bundle (", "The DMG verifies", "The Windows installer is there"):
            s = next(s for s in build["steps"] if s.get("name", "").startswith(key))
            self.assertNotIn("SIGNPATH", s.get("if", ""), key)
        self.assertEqual(build["env"]["APPLE_SIGNING_IDENTITY"], "-")
        for job in ("plan", "publish"):
            self.assertNotIn("secrets.", json.dumps(self.jobs[job]), job)

    def test_the_compilation_sees_no_signing_secret(self):
        # Every build.rs and proc-macro of the app's dependencies runs while it compiles: Apple's
        # certificate and passwords only reach the bundling, which compiles nothing. The job's own
        # variables only say whether a secret is set.
        build = self.jobs["build"]
        for key, value in build["env"].items():
            for ref in re.findall(r"secrets\.[A-Z_]+(?: != '')?", value):
                with self.subTest(var=key):
                    self.assertTrue(ref.endswith(" != ''"), ref)
        compile_ = step(build, "cargo tauri build")
        self.assertIn("--no-bundle", compile_["run"])
        self.assertNotIn("secrets.", json.dumps(compile_))
        bundle = step(build, 'cargo tauri bundle --ci --target "$TARGET" --bundles "$BUNDLES"')
        self.assertTrue(bundle["name"].startswith("Bundle ("))
        self.assertIn("APPLE_CERTIFICATE", bundle["env"])
        self.assertNotIn("cargo tauri build", bundle["run"])
        steps = build["steps"]
        self.assertLess(steps.index(compile_), steps.index(bundle))

    def test_signpath_can_read_the_artifact_and_wait_for_its_approval(self):
        build = self.jobs["build"]
        # SignPath's action reads the job and downloads the unsigned artifact with the job's token.
        self.assertEqual(build["permissions"], {"contents": "read", "actions": "read"})
        # Two requests, each waited for (a release-signing policy waits for a person's approval),
        # after a build without any cache: the job gets the time when it signs, and only then.
        waits = sum(int(s["with"]["wait-for-completion-timeout-in-seconds"])
                    for s in self.using("build", "signpath/github-action-submit-signing-request"))
        m = re.fullmatch(r"\$\{\{ matrix\.platform == 'windows' && needs\.plan\.outputs\.publish == 'true' "
                         r"&& vars\.SIGNPATH_ORGANIZATION_ID != '' && (\d+) \|\| (\d+) \}\}", str(build["timeout-minutes"]))
        self.assertTrue(m, build["timeout-minutes"])
        signing, plain = int(m.group(1)), int(m.group(2))
        self.assertEqual(plain, 60)
        self.assertGreaterEqual(signing, plain + waits // 60 + 15)

    def run_step(self, run, env, cwd):
        base = {"PATH": os.environ["PATH"], "LC_ALL": "C.UTF-8"}
        p = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", run], cwd=cwd,
                           env=dict(base, **env), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_the_release_says_unsigned_only_where_it_is(self):
        # Each build says whether its system's files are signed (true or false, from whether the
        # secrets are set); the notes and the title follow, in the app's words (tu).
        build, publish = self.jobs["build"], self.jobs["publish"]
        self.assertEqual(build["outputs"], {"macos_signed": "${{ steps.signed.outputs.macos }}",
                                            "windows_signed": "${{ steps.signed.outputs.windows }}"})
        self.assertEqual(build["env"]["DEVELOPER_ID"], "${{ needs.plan.outputs.publish == 'true' && secrets.APPLE_CERTIFICATE != '' }}")
        self.assertEqual(publish["env"]["MACOS_SIGNED"], "${{ needs.build.outputs.macos_signed }}")
        self.assertEqual(publish["env"]["WINDOWS_SIGNED"], "${{ needs.build.outputs.windows_signed }}")
        signed = next(s for s in build["steps"] if s.get("id") == "signed")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "out")
            for platform, env, line in (("macos", {"DEVELOPER_ID": "true"}, "macos=true"),
                                        ("windows", {"SIGNPATH": "false"}, "windows=false"),
                                        ("linux", {}, "")):
                out.write_text("")
                self.run_step(signed["run"], dict(env, PLATFORM=platform, GITHUB_OUTPUT=str(out)), tmp)
                self.assertEqual(out.read_text().strip(), line, platform)
        notes_step = next(s for s in self.steps("publish") if s.get("name") == "Release notes")
        create = next(s for s in self.steps("publish") if 'gh release create "$TAG"' in s.get("run", ""))
        self.assertIn('--title "$(cat "$RUNNER_TEMP/title")"', create["run"])
        files = ("macos-universal.dmg", "windows-x64-setup.exe", "linux-x86_64.AppImage", "linux-amd64.deb")
        for mac, win in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(mac=mac, win=win), tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "dist").mkdir()
                for f in files:
                    Path(tmp, "dist", "Brain-in-a-Box_0.1.7-preview.7_" + f).write_text("x")
                self.run_step(notes_step["run"], {
                    "RUNNER_TEMP": tmp, "GITHUB_SHA": "0" * 40, "GITHUB_SERVER_URL": "https://github.com",
                    "GH_REPO": "o/r", "VERSION": "0.1.7", "LABEL": "0.1.7-preview.7",
                    "MACOS_SIGNED": str(mac).lower(), "WINDOWS_SIGNED": str(win).lower()}, tmp)
                notes = Path(tmp, "notes.md").read_text(encoding="utf-8")
                title = Path(tmp, "title").read_text(encoding="utf-8")
                self.assertTrue(title.startswith("App Brain in a Box 0.1.7 — préversion 7"), title)
                self.assertEqual("non signée" in title, not (mac and win), title)
                self.assertEqual("Mac" in title, not mac, title)
                self.assertEqual("Windows" in title, not win, title)
                self.assertEqual("signée" in notes, not (mac and win))
                # Gatekeeper's way round, only for an app Apple has not signed.
                self.assertEqual("Ouvrir quand même" in notes, not mac)
                said = re.sub(r"«[^»]*»", "", notes)
                self.assertNotRegex(said, r"(?i)\b(vous|votre|vos|téléchargez|ouvrez|cliquez|allez|rendez|glissez|lancez)\b")

    def test_the_dmg_window_is_laid_out_by_finder(self):
        # Tauri skips Finder's layout on a CI: the background would not show.
        self.assertEqual(self.jobs["build"]["env"]["TAURI_BUNDLER_DMG_IGNORE_CI"], "true")
        conf = json.loads((TAURI / "tauri.macos.conf.json").read_text(encoding="utf-8"))
        dmg = conf["bundle"]["macOS"]["dmg"]
        check = step(self.jobs["build"], "hdiutil verify")["run"]
        self.assertIn('"$mnt/.background/%s"' % Path(dmg["background"]).name, check)

    def test_the_field_test_opens_the_dmg_users_get(self):
        # terrain.yml builds its DMG as this workflow does, its window laid out: the night's capture
        # of it, which the site shows (site/captures.sh), is the window a user opens.
        terrain = load(WORKFLOW.with_name("terrain.yml"))
        builds = [s for job in terrain["jobs"].values() for s in job.get("steps", [])
                  if "--bundles dmg" in s.get("run", "")]
        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0]["env"]["TAURI_BUNDLER_DMG_IGNORE_CI"], "true")

    def test_a_fork_pull_request_uploads_nothing(self):
        uploads = [s for s in self.using("build", "actions/upload-artifact") if s["with"]["name"].startswith("app-")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].get("if"), "needs.plan.outputs.upload == 'true'")
        # The others only go to SignPath, from the preview that is published.
        for s in self.using("build", "actions/upload-artifact"):
            if s not in uploads:
                self.assertIn("env.SIGNPATH == 'true'", s["if"])
        plan_env = next(s for s in self.steps("plan") if s.get("id") == "plan")["env"]
        self.assertEqual(plan_env["HEAD_REPO"], "${{ github.event.pull_request.head.repo.full_name }}")
        self.assertEqual(plan_env["REPOSITORY"], "${{ github.repository }}")
        self.assertEqual(plan_env["EVENT_NAME"], "${{ github.event_name }}")
        self.assertIn("upload", self.jobs["plan"]["outputs"])


# The strings of Tauri's NSIS template (tauri-bundler 2.9.4, languages/French.nsh): a custom
# language file replaces Tauri's own, so each must be in ours.
TAURI_STRINGS = """addOrReinstall alreadyInstalled alreadyInstalledLong appRunning appRunningOkKill
chooseMaintenanceOption choowHowToInstall createDesktop deleteAppData dontUninstall dontUninstallDowngrade
failedToKillApp installingWebview2 newerVersionInstalled older olderOrUnknownVersionInstalled
silentDowngrades unableToUninstall uninstallApp uninstallBeforeInstalling unknown webview2AbortError
webview2DownloadError webview2DownloadSuccess webview2Downloading webview2InstallError
webview2InstallSuccess""".split()


def bmp_size(path):
    """(width, height, bits per pixel) of a BMP."""
    head = path.read_bytes()[:30]
    assert head[:2] == b"BM", path
    return int.from_bytes(head[18:22], "little"), int.from_bytes(head[22:26], "little", signed=True), \
        int.from_bytes(head[28:30], "little")


def jpeg_size(path):
    data = path.read_bytes()
    i = 2
    while i < len(data):
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xC3:
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    raise AssertionError("no frame in %s" % path)


def webp_size(path):
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise AssertionError("%s is not a WebP" % path)
    kind = data[12:16]
    if kind == b"VP8X":
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    if kind == b"VP8 ":
        return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if kind == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    raise AssertionError("%s: unknown WebP chunk %r" % (path, kind))


class WindowsInstaller(unittest.TestCase):
    """The NSIS installer a beginner clicks through: Bibo's pictures and words, no folder to
    choose, an update in place, and an uninstaller that offers to remove the scheduled tasks."""

    @classmethod
    def setUpClass(cls):
        cls.conf = json.loads((TAURI / "tauri.windows.conf.json").read_text(encoding="utf-8"))["bundle"]["windows"]["nsis"]
        cls.nsi = (INSTALLER / "installer.nsi").read_text(encoding="utf-8")
        cls.french = (INSTALLER / "French.nsh").read_text(encoding="utf-8")
        cls.hooks = (INSTALLER / "hooks.nsh").read_text(encoding="utf-8")

    def test_the_config_names_our_files_and_they_are_there(self):
        self.assertEqual(self.conf["installMode"], "currentUser")
        self.assertEqual(self.conf["template"], "installer/installer.nsi")
        self.assertEqual(self.conf["customLanguageFiles"], {"French": "installer/French.nsh"})
        self.assertEqual(self.conf["installerHooks"], "installer/hooks.nsh")
        for key in ("template", "installerHooks", "installerIcon", "uninstallerIcon", "headerImage", "sidebarImage"):
            with self.subTest(key=key):
                self.assertTrue((TAURI / self.conf[key]).is_file(), self.conf[key])
        # NSIS loads 24-bit BMPs at these sizes (MUI2: 164x314 on the side, 150x57 in the header).
        self.assertEqual(bmp_size(TAURI / self.conf["sidebarImage"]), (164, 314, 24))
        self.assertEqual(bmp_size(TAURI / self.conf["headerImage"]), (150, 57, 24))

    def test_the_template_is_tauris_for_the_pinned_cli(self):
        # Tauri fills the template it is given: with another tauri-cli, its own template may have
        # changed, and ours must be taken again from it (the "Bibo:" changes carried over).
        cli = re.search(r'TAURI_CLI_VERSION: "([0-9.]+)"', WORKFLOW.read_text(encoding="utf-8")).group(1)
        self.assertIn("the bundler of tauri-cli %s (TAURI_CLI_VERSION in release.yml)" % cli, self.nsi)
        terrain = (ROOT / ".github" / "workflows" / "terrain.yml").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r'TAURI_CLI_VERSION: "([0-9.]+)"', terrain)), {cli})

    def test_no_folder_to_choose_no_nullsoft_and_an_update_in_place(self):
        self.assertNotIn("MUI_PAGE_DIRECTORY", self.nsi)
        self.assertNotIn('BrandingText "${COPYRIGHT}"', self.nsi)
        self.assertIn('BrandingText "Brain in a Box ${VERSION}"', self.nsi)
        # The same version or an older one already there: no page asking whether to uninstall.
        self.assertRegex(self.nsi, r"\$\{If\} \$WixMode <> 1\s+\$\{AndIf\} \$R0 >= 0\s+Abort")
        # The welcome page says « Mettre à jour Bibo » when Bibo is already there.
        self.assertIn('SendMessage $mui.WelcomePage.Title ${WM_SETTEXT} 0 "STR:$(biboUpdateTitle)"', self.nsi)

    def test_every_string_used_is_written_in_french_and_says_tu(self):
        defined = dict(re.findall(r'^LangString (\w+) \$\{LANG_FRENCH\} "(.*)"$', self.french, re.M))
        for name in TAURI_STRINGS:
            self.assertIn(name, defined)
        used = set(re.findall(r"\$\((\w+)\)", self.nsi + self.hooks)) - {"^RTL", "^NameDA"}
        self.assertFalse(used - set(defined), "strings used but not written: %s" % sorted(used - set(defined)))
        for name, text in defined.items():
            with self.subTest(name=name):
                self.assertNotRegex(text, r"(?i)\b(vous|votre|vos|veuillez|cliquez)\b")
        self.assertIn("Mettre à jour Bibo", defined["addOrReinstall"])
        self.assertIn("tes notes ne changent pas", defined["alreadyInstalledLong"])

    def test_the_hooks_hold_no_words(self):
        # What the uninstaller says is in French.nsh, with the rest: no sentence hidden in the hooks.
        self.hooks.encode("ascii")
        self.assertNotRegex(self.hooks, r'MessageBox [A-Z_|]+ "[^$]')

    def test_uninstalling_offers_to_remove_the_tasks_install_ps1_schedules(self):
        install = (ROOT / "install.ps1").read_text(encoding="utf-8")
        scheduled = set(re.findall(r'Register-BrainTask "(\w+)"', install))
        self.assertEqual(scheduled, {"BrainNightly", "BrainReflection"})
        deleted = set(re.findall(r'BIBO_TASK_DELETE "(\w+)"', self.hooks))
        # BrainSearchReindex: the night's task before gbrain, that an old install may have left.
        self.assertEqual(deleted, scheduled | {"BrainSearchReindex"})
        self.assertIn("!macro NSIS_HOOK_POSTUNINSTALL", self.hooks)
        # Never in an update, nor without a window; silent, the answer is no.
        self.assertRegex(self.hooks, r"\$\{If\} \$UpdateMode <> 1\s+\$\{AndIf\} \$PassiveMode <> 1")
        # No by default, said to someone who reinstalls: the tasks would not come back (the app's
        # "already installed" does not look at them). Windows writes the buttons in its own language.
        self.assertIn('MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "$(biboRemoveTasks)" /SD IDNO', self.hooks)
        self.assertIn("Tes notes restent", self.french)
        ask = re.search(r'^LangString biboRemoveTasks \$\{LANG_FRENCH\} "(.*)"$', self.french, re.M).group(1)
        self.assertIn("Tu réinstalles Bibo\u00a0? Réponds Non (No)", ask)
        self.assertIn("Oui (Yes)", ask)

    def test_going_back_to_an_older_bibo_uninstalls_as_an_update(self):
        # The uninstall the reinstall page starts is the first half of an install: /UPDATE, so that
        # it asks nothing about the brain's tasks, which the Bibo installed next needs.
        page = self.nsi[self.nsi.index("  reinst_uninstall:"):self.nsi.index("  reinst_done:")]
        self.assertNotIn("${IfThen} $UpdateMode = 1 ${|} StrCpy $R1 \"$R1 /UPDATE\"", page)
        self.assertRegex(page, r'ReadRegStr \$R1 SHCTX "\$\{UNINSTKEY\}" "UninstallString"\n(\s*;[^\n]*\n)*\s*StrCpy \$R1 "\$R1 /UPDATE"\n')

    def test_the_installing_page_shows_no_english_count(self):
        # FileFunc's GetSize writes "Size:111 Kb  Files:1  Folders:" above the progress bar: our
        # words go there after it.
        sizes = list(re.finditer(r"^\s*\$\{GetSize\}[^\n]*\n", self.nsi, re.M))
        self.assertTrue(sizes)
        for m in sizes:
            with self.subTest(at=m.start()):
                self.assertRegex(self.nsi[m.end():], r'^(\s*;[^\n]*\n)*\s*SetDetailsPrint textonly\n\s*DetailPrint "\$\(\w+\)"\n\s*SetDetailsPrint both\n')


class MacDisk(unittest.TestCase):
    def test_the_dmg_background_fits_its_window(self):
        conf = json.loads((TAURI / "tauri.macos.conf.json").read_text(encoding="utf-8"))["bundle"]["macOS"]["dmg"]
        tiff = TAURI / conf["background"]
        self.assertTrue(tiff.is_file())
        self.assertEqual(conf["windowSize"], {"width": 660, "height": 400})
        # Bibo on the left, Applications on the right: the arrow of the picture goes from one to the other.
        self.assertLess(conf["appPosition"]["x"], conf["applicationFolderPosition"]["x"])
        self.assertEqual(conf["appPosition"]["y"], conf["applicationFolderPosition"]["y"])


class Site(unittest.TestCase):
    """The download page (site/): its buttons are the files release.yml leaves in app-latest, and
    keep working when GitHub's API says no; what it promises a beginner, and how it says it."""

    LATEST = "https://github.com/BrainInBox/brain-in-a-box/releases/download/app-latest/"

    @classmethod
    def setUpClass(cls):
        cls.html = (SITE / "index.html").read_text(encoding="utf-8")
        cls.js = (SITE / "app.js").read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        # The files each build leaves in dist/, named Brain-in-a-Box_${LABEL}_<what>.
        cls.files = set(re.findall(r'"dist/Brain-in-a-Box_\$\{LABEL\}_([A-Za-z0-9._-]+)"', workflow))

    def links(self):
        return set(re.findall(r'href="(https://github\.com/BrainInBox/brain-in-a-box/releases/download/[^"]+)"', self.html))

    def test_the_buttons_are_the_files_app_latest_holds(self):
        self.assertEqual(self.files, {"macos-universal.dmg", "windows-x64-setup.exe", "linux-x86_64.AppImage", "linux-amd64.deb"})
        expected = {self.LATEST + "Brain-in-a-Box_" + f for f in self.files} | {self.LATEST + "SHA256SUMS"}
        self.assertEqual(self.links(), expected)
        # app.js points the big buttons at the same names.
        self.assertIn("const LATEST = `https://github.com/${REPO}/releases/download/app-latest/`;", self.js)
        names = set(re.findall(r"^  \w+: '([A-Za-z0-9._-]+)',$", self.js[self.js.index("const FILES"):self.js.index("const MAIN")], re.M))
        self.assertEqual(names, self.files)

    def test_no_download_waits_for_githubs_api(self):
        # Every download link is in the page as it is served; without JavaScript the big buttons
        # lead to the systems' own buttons.
        for tag in re.findall(r"<a [^>]*data-dl(?:-main)?[ =][^>]*>", self.html):
            with self.subTest(tag=tag):
                href = re.search(r'href="([^"]+)"', tag).group(1)
                self.assertTrue(href.startswith(self.LATEST) or href == "#installer", href)
        self.assertNotIn(".href = file.url", self.js)
        self.assertNotIn("browser_download_url", self.js)

    def test_a_shared_link_shows_a_picture(self):
        meta = dict(re.findall(r'<meta property="(og:[a-z:_]+)" content="([^"]*)">', self.html))
        self.assertEqual(meta["og:image"], "https://brain-in-a-box.vercel.app/img/og.jpg")
        self.assertEqual((meta["og:image:width"], meta["og:image:height"]), ("1200", "630"))
        self.assertEqual(jpeg_size(SITE / "img" / "og.jpg"), (1200, 630))
        self.assertEqual(meta["og:url"], "https://brain-in-a-box.vercel.app/")
        self.assertEqual(meta["og:locale"], "fr_FR")

    def test_the_title_says_the_guide_and_that_claude_is_ones_own(self):
        h1 = re.sub(r"<[^>]+>", "", re.search(r"<h1[^>]*>(.*?)</h1>", self.html, re.S).group(1))
        self.assertIn("guide", h1)
        self.assertIn('id="guide"', self.html)
        self.assertIn("ton propre abonnement Claude", self.html)

    def test_no_terminal_for_a_beginner(self):
        # Typing claude, Win+R and irm | iex belong to the section for developers only.
        dev = re.search(r'<section[^>]*id="ligne-de-commande".*?</section>', self.html, re.S).group(0)
        rest = self.html.replace(dev, "")
        self.assertNotIn("Win + R", rest)
        self.assertNotIn("irm ", rest)
        self.assertNotRegex(rest, r"(?i)tape\s+<code>claude</code>")
        self.assertIn("Pour les développeurs", dev)

    def test_the_price_is_said_and_never_more_than_known(self):
        self.assertIn("Claude Pro, environ 20&nbsp;€ par mois", self.html)
        self.assertEqual(self.html.count("€"), self.html.lower().count("environ 20&nbsp;€"))

    def test_windows_words_also_in_english(self):
        win = re.search(r'<div class="panel" id="installer-windows">.*?</aside>', self.html, re.S).group(0)
        for fr, en in (("Conserver", "Keep"), ("Informations complémentaires", "More info"),
                       ("Exécuter quand même", "Run anyway"), ("Oui", "Yes")):
            with self.subTest(fr=fr):
                self.assertIn("<strong>%s</strong> (%s)" % (fr, en), win)
        self.assertNotIn("Aucun droit administrateur", self.html)

    def test_edges_two_questions_said_in_full(self):
        # For a file it rarely sees, Edge asks twice: « Conserver », then a window where « Supprimer »
        # stands out and « Conserver quand même » hides under « Afficher plus ». Every time.
        blocks = {
            "next": r'<ol class="next-steps" data-next-for="windows">.*?</ol>',
            "steps": r'<div class="panel" id="installer-windows">.*?</aside>',
            "faq": r"<summary>Windows&nbsp;: mon navigateur dit.*?</details>",
        }
        for where, pattern in blocks.items():
            block = re.search(pattern, self.html, re.S).group(0)
            with self.subTest(where=where):
                self.assertIn("<strong>Conserver</strong> (Keep)", block)
                self.assertIn("<strong>Afficher plus</strong> (Show more)", block)
                self.assertIn("<strong>Conserver quand même</strong> (Keep anyway)", block)
        self.assertNotIn("insiste", self.html)

    def test_the_hero_leads_to_no_release_page(self):
        # GitHub's release page is in English and offers « Source code (zip) » next to the files:
        # only the footer links to the releases.
        hero = re.search(r'<p class="dl-version">.*?</p>', self.html, re.S).group(0)
        self.assertNotIn("<a ", hero)
        self.assertNotIn("release.page", self.js)
        self.assertNotIn("RELEASE_PAGE", self.js)

    def test_uninstalling_on_windows_step_by_step(self):
        faq = re.search(r"<summary>Désinstaller</summary>.*?</details>", self.html, re.S).group(0)
        self.assertIn("Applications installées (Settings › Apps › Installed apps)", faq)
        self.assertIn("«&nbsp;…&nbsp;» à côté de Brain in a Box", faq)
        self.assertIn("<strong>Désinstaller</strong> (Uninstall)", faq)
        # The installer's question: no if Bibo comes back (hooks.nsh).
        self.assertIn("<strong>Non</strong> (No) si tu réinstalles Bibo", faq)

    def test_smart_app_control_is_never_turned_off(self):
        faq = re.search(r'<details id="smart-app-control">.*?</details>', self.html, re.S).group(0)
        self.assertIn("Ne le désactive pas", faq)

    def test_a_phone_sends_the_link_to_the_computer(self):
        for attr in ("data-phone", "data-share", "data-copy-link"):
            self.assertIn(attr, self.html)
        self.assertIn("navigator.share", self.js)
        # Nothing to download on a phone: neither the big buttons nor the install guide's.
        mobile = self.js[self.js.index("if (system === 'mobile')"):]
        hidden = re.search(r"querySelectorAll\('([^']+)'\)\) el\.hidden = true", mobile).group(1).split(", ")
        for sel in ("[data-dl-main]", "[data-top-dl]", ".dl-others", ".dl-version", 'a[data-dl]:not([data-dl="sums"])'):
            self.assertIn(sel, hidden)
        # Each link to an installer carries data-dl (SHA256SUMS, a text file, stays).
        for tag in re.findall(r"<a [^>]*href=\"%s[^\"]+\"[^>]*>" % re.escape(self.LATEST), self.html):
            with self.subTest(tag=tag):
                if "SHA256SUMS" in tag:
                    self.assertIn('data-dl="sums"', tag)
                else:
                    self.assertRegex(tag, r'data-dl="(windows|mac|appimage|deb)"')

    def test_each_capture_is_declared_at_its_size(self):
        # width and height say the picture's own size (no page that jumps as it loads, nothing
        # stretched): a capture made again from another run (captures.sh) must update them.
        tags = re.findall(r'<img [^>]*src="img/[^"]+\.webp"[^>]*>', self.html)
        self.assertGreater(len(tags), 15)
        for tag in tags:
            with self.subTest(tag=tag[:80]):
                name = re.search(r'src="img/([^"]+)"', tag).group(1)
                size = tuple(int(re.search(r'\b%s="(\d+)"' % a, tag).group(1)) for a in ("width", "height"))
                self.assertEqual(size, webp_size(SITE / "img" / name))

    def test_the_strict_csp_holds(self):
        # vercel.json allows no inline script or style, and no third party but GitHub's API.
        self.assertNotRegex(self.html, r"<script(?![^>]*\bsrc=)[^>]*>")
        self.assertNotRegex(self.html, r"\sstyle=")
        self.assertNotRegex(self.html, r"\son[a-z]+=")
        csp = json.loads((SITE / "vercel.json").read_text(encoding="utf-8"))["headers"][0]["headers"][0]["value"]
        self.assertIn("script-src 'self';", csp)
        self.assertNotIn("unsafe", csp)
        for src in re.findall(r'(?:src|href)="((?:img|fonts)/[^"#]+)"', self.html):
            with self.subTest(file=src):
                self.assertTrue((SITE / src).is_file())


if __name__ == "__main__":
    unittest.main()
