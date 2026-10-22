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
  once its secrets exist; without them nothing changes.

And what goes with it: the Windows installer (Tauri's NSIS template, our words, pictures and
uninstall hook, app/src-tauri/installer/), the DMG's window, and the download site (site/), whose
buttons are the files published here.

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
        self.assertEqual(len(runs), 2, "the build, and the bundle of the signed app")
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
        pattern = re.search(r'test\("([^"]+)"\)', steps[prune]["run"]).group(1).replace("\\\\", "\\")
        self.assertRegex("app-v0.1.112-preview.112", pattern)
        self.assertNotRegex("app-latest", pattern)

    def test_signing_runs_only_for_the_published_preview_with_its_secrets(self):
        build = self.jobs["build"]
        self.assertEqual(build["env"]["SIGNPATH"],
                         "${{ needs.plan.outputs.publish == 'true' && secrets.SIGNPATH_API_TOKEN != '' "
                         "&& vars.SIGNPATH_ORGANIZATION_ID != '' }}")
        signpath = [s for s in build["steps"] if "SIGNPATH" in json.dumps(s) or "signpath" in json.dumps(s)]
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
        # Apple: only the build step reads the secrets, only for the published preview, on macOS.
        # Elsewhere, only whether one is set.
        for name, s in [(s.get("name", ""), s) for s in build["steps"]]:
            for key, value in (s.get("env") or {}).items():
                if "secrets.APPLE" in str(value):
                    with self.subTest(step=name, var=key):
                        if name.startswith("Build ("):
                            self.assertRegex(value, r"^\$\{\{ runner\.os == 'macOS' && needs\.plan\.outputs\.publish == 'true' "
                                                    r"&& secrets\.APPLE_[A-Z_]+ \|\| '' \}\}$")
                        else:
                            self.assertRegex(value, r"^\$\{\{ needs\.plan\.outputs\.publish == 'true' && secrets\.APPLE_[A-Z_]+ != '' \}\}$")
        # Without the secrets, the steps that build and check run as before.
        for key in ("Build (", "The DMG verifies", "The Windows installer is there"):
            s = next(s for s in build["steps"] if s.get("name", "").startswith(key))
            self.assertNotIn("SIGNPATH", s.get("if", ""), key)
        self.assertEqual(build["env"]["APPLE_SIGNING_IDENTITY"], "-")
        for job in ("plan", "publish"):
            self.assertNotIn("secrets.", json.dumps(self.jobs[job]), job)

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
        self.assertIn('MessageBox MB_YESNO|MB_ICONQUESTION "$(biboRemoveTasks)" /SD IDNO', self.hooks)
        self.assertIn("Tes notes restent", self.french)


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
