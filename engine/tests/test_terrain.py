"""Tests of the field-test harness itself (engine/tests/terrain.py): what runs off a VM, the
scoring of the real-Claude scenario, and what the nightly report and issue may say.

The real-Claude scenario runs here against a stand-in for claude (POSIX: CLAUDE_BIN must be an
executable). Every run happens in a throwaway home.

    python3 -I -m unittest discover -s engine/tests -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TERRAIN = os.path.join(HERE, "terrain.py")
REF_GUARD = os.path.join(HERE, "..", "..", ".github", "ci", "terrain-ref.sh")
MERGE_GUARD = os.path.join(HERE, "..", "..", ".github", "ci", "terrain-garde.sh")
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "terrain.yml")
TOKEN = "secrets.CLAUDE_CODE_OAUTH_TOKEN"
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import terrain  # noqa: E402
from test_judge import FIXTURES, HELP, OK_STREAM  # noqa: E402

STUB = r'''#!%(python)s -I
import json, os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
here = os.path.join(os.environ["HOME"], ".terrain-stub")
with open(os.path.join(here, "plan.json"), encoding="utf-8") as f:
    plan = json.load(f)
args = sys.argv[1:]


def emit(v):
    sys.stdout.write(json.dumps(v, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if args == ["--version"]:
    print("2.1.296 (Claude Code)")
    sys.exit(0)
if args == ["--help"]:
    with open(plan["help"], encoding="utf-8") as f:
        sys.stdout.write(f.read())
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    emit({"loggedIn": True, "authMethod": "oauth_token"})
    sys.exit(0)
stdin = sys.stdin.buffer.read().decode("utf-8")
with open(os.path.join(here, "calls"), "a", encoding="utf-8") as f:
    f.write("%%s\n" %% ("token" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else "sans-jeton"))
if plan.get("refuse"):   # what Claude Code prints when the service refuses the token
    emit(plan["init"])
    emit({"type": "result", "subtype": "success", "is_error": True,
          "result": "Failed to authenticate. API Error: 401 OAuth access token is invalid \u00b7 Please run /login"})
    sys.exit(1)
schema = args[args.index("--json-schema") + 1]
if ("constats" if '"constats"' in schema else "reflexion") in plan.get("quota_on", []):
    with open(plan["quota"], encoding="utf-8") as f:   # what Claude Code prints at the usage limit
        sys.stdout.write(f.read())
    sys.exit(1)
if '"constats"' in schema:
    excerpts = re.findall(r'<extrait id="(E\d+)" note="([^"]*)"[^>]*>(.*?)</extrait>', stdin, re.S)
    found = lambda rel, q: next((i for i, n, t in excerpts if n == rel and q in t), None)
    constats = []
    for c in plan["constats"]:
        a, b = found(c["a"], c["qa"]), found(c["b"], c["qb"])
        if a and b:
            constats.append({"type": c["type"], "a": a, "b": b, "citation_a": c["qa"], "citation_b": c["qb"],
                             "actuel": "b", "confiance": "haute", "explication": "La plus récente dit autre chose."})
    answer = {"suspect": False, "constats": constats, "lecons": []}
else:
    answer = plan["reflexion"]
assistant = plan["assistant"]
assistant["message"]["content"][0]["input"] = answer
emit(plan["init"])
emit(assistant)
emit(plan["user"])
emit(dict(plan["result"], structured_output=answer, result=json.dumps(answer, ensure_ascii=False)))
'''
REFLEXION = {"what_i_did": ["Préparé la tournée de jeudi, commande `rm -rf ~/tournee-tmp` notée <% tp.date.now() %>"],
             "key_decisions": ["Le meunier actuel reste jusqu'en janvier"], "projects": ["Boulangerie : tournée"],
             "todo": ["Appeler le meunier lundi"], "memory": ["Tournée de jeudi prête"], "suspect": True}


def constat(pair, kind):
    old_rel, _, _, old_q, new_rel, _, _, new_q = pair
    return {"type": kind, "a": old_rel, "qa": old_q, "b": new_rel, "qb": new_q}


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="terrain-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.json = os.path.join(self.tmp, "resultats.json")
        self.env = dict(os.environ)
        for k in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_BIN", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT"):
            self.env.pop(k, None)

    def terrain(self, *args):
        p = subprocess.run([sys.executable, "-I", TERRAIN] + list(args), env=self.env, capture_output=True,
                           timeout=900)
        return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")

    def results(self):
        with open(self.json, encoding="utf-8") as f:
            return {r["scenario"]: r for r in json.load(f)["scenarios"]}


class OffAVm(Sandbox):
    def test_vm_scenarios_are_skipped_and_say_so(self):
        for name in terrain.VM_ONLY:
            rc, out = self.terrain(name, "--home", self.home, "--json", self.json)
            self.assertEqual(rc, 0, out)
            self.assertIn("TERRAIN %s SAUTÉ : seulement sur une VM GitHub jetable" % name, out)
        self.assertEqual({r["statut"] for r in self.results().values()}, {"saute"})

    def test_the_home_must_be_a_throwaway_folder(self):
        self.assertEqual(self.terrain("nuits")[0], 2)
        self.assertEqual(self.terrain("nuits", "--home", os.path.expanduser("~"))[0], 2)
        rc, out = self.terrain("installation", "--vm", "--home", self.home)
        self.assertEqual(rc, 2, "--vm off a GitHub-hosted runner")
        self.assertFalse(os.path.exists(os.path.join(self.home, "terrain-resultats.json")))

    def test_no_token_no_real_claude(self):
        rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 0, out)
        self.assertIn("TERRAIN vrai-claude SAUTÉ : pas de jeton", out)
        # A run of another ref or workflow than master's own never gets the token, and says so.
        self.env["TERRAIN_JETON"] = "reserve"
        rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 0, out)
        self.assertIn("TERRAIN vrai-claude SAUTÉ : jeton réservé au workflow de master", out)

    def test_a_workflow_step_is_recorded(self):
        self.terrain("etape", "installeur", "--resultat", "failure", "--home", self.home, "--json", self.json)
        r = self.results()["installeur"]
        self.assertEqual((r["statut"], r["etape"], r["reproduire"]), ("echec", "installeur", terrain.REPRO_VM))


@unittest.skipIf(os.name == "nt", "CLAUDE_BIN must be an executable: a script stands in for claude on POSIX only")
class RealClaudeScenario(Sandbox):
    def stub(self, constats, quota_on=(), refuse=False):
        here = os.path.join(self.home, ".terrain-stub")
        os.makedirs(here)
        plan = {"help": HELP, "init": OK_STREAM[0], "assistant": OK_STREAM[1], "user": OK_STREAM[2],
                "result": OK_STREAM[-1], "constats": constats, "reflexion": REFLEXION,
                "quota": os.path.join(FIXTURES, "quota.jsonl"), "quota_on": list(quota_on), "refuse": refuse}
        with open(os.path.join(here, "plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        path = os.path.join(self.tmp, "claude")
        with open(path, "w", encoding="utf-8") as f:
            f.write(STUB % {"python": sys.executable})
        os.chmod(path, 0o755)
        self.env.update(CLAUDE_BIN=path, CLAUDE_CODE_OAUTH_TOKEN="factice-pas-un-secret")
        return os.path.join(here, "calls")

    def test_every_contradiction_found_scores_full(self):
        calls = self.stub([constat(p, "decision_remplacee" if p[0].startswith("Decisions/") else "contradiction")
                           for p in terrain.PAIRES])
        rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 0, out)
        r = self.results()["vrai-claude"]
        self.assertEqual(r["statut"], "ok", out)
        m = r["details"]["mesure"]
        self.assertEqual((m["trouvees"], m["constats"], m["justes"], m["annotations_fausses"]), (6, 6, 6, []))
        with open(calls, encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["token", "token"], "one review, one reflection, the token passed on")
        self.assertNotIn("factice-pas-un-secret", json.dumps(r) + out)

    def test_with_the_token_only_the_repositorys_scripts_run(self):
        # On a VM, a launcher left by an installer (or rewritten by code that ran before) is never
        # what runs with the token: the repository's controle.py and daily-reflection.py are.
        calls = self.stub([constat(p, "contradiction") for p in terrain.PAIRES[:3]])
        planted = os.path.join(self.home, ".local", "bin", "controle")
        hook = os.path.join(self.home, ".claude", "hooks", "brain", "daily-reflection.py")
        trace = os.path.join(self.tmp, "planted-ran")
        for path in (planted, hook):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\necho \"$0\" >> '%s'\nexit 0\n" % trace)
            os.chmod(path, 0o755)
        self.env.update(GITHUB_ACTIONS="true", RUNNER_ENVIRONMENT="github-hosted")
        rc, out = self.terrain("vrai-claude", "--vm", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(trace), "a launcher found on the machine ran with the token")
        self.assertEqual(self.results()["vrai-claude"]["details"]["controle"], "controle.py du dépôt")
        with open(calls, encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["token", "token"])

    def test_the_usage_limit_is_skipped_not_red(self):
        # Chad's subscription at its limit: nothing to repair, and nothing to retry.
        for quota_on, where in ((["constats", "reflexion"], "(relecture)"), (["reflexion"], "(réflexion ; relecture")):
            with self.subTest(quota_on=quota_on):
                shutil.rmtree(self.home)
                os.makedirs(self.home)
                self.stub([constat(p, "contradiction") for p in terrain.PAIRES], quota_on=quota_on)
                rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
                self.assertEqual(rc, 0, out)
                r = self.results()["vrai-claude"]
                self.assertEqual(r["statut"], "saute", out)
                self.assertIn("quota de l'abonnement atteint", r["raison"])
                self.assertIn(where, r["raison"])

    def test_a_refused_token_says_what_claude_said(self):
        # First run with the real token (2026-10-14): "saute:connexion" alone, nothing to tell a
        # refused token from a missing login or a launcher bug.
        self.stub([constat(p, "contradiction") for p in terrain.PAIRES], refuse=True)
        rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 1, out)
        r = self.results()["vrai-claude"]
        self.assertEqual((r["statut"], r["etape"]), ("echec", "relecture"))
        self.assertIn("saute:connexion (Failed to authenticate. API Error: 401 OAuth access token is invalid", r["raison"])
        self.assertIn("401", r["details"]["juge"]["detail"])
        self.assertNotIn("factice-pas-un-secret", json.dumps(r) + out)

    def test_the_tokens_shape_is_said_never_its_value(self):
        self.stub([constat(p, "contradiction") for p in terrain.PAIRES], refuse=True)
        self.env["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat01-factice-\n pas-un-secret\n"
        self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        r = self.results()["vrai-claude"]
        self.assertEqual(r["details"]["jeton"],
                         {"longueur": len("sk-ant-oat01-factice-pas-un-secret"), "prefixe_setup_token": True,
                          "blancs_retires": 3})
        self.assertNotIn("pas-un-secret", json.dumps(r))

    def test_an_annotation_on_a_decoy_fails(self):
        self.stub([constat(terrain.PAIRES[2], "contradiction"), constat(terrain.LEURRES[0], "contradiction")])
        rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 1, out)
        r = self.results()["vrai-claude"]
        self.assertEqual((r["statut"], r["etape"]), ("echec", "mesure"))
        self.assertIn("annotation fausse : C1-", r["raison"])


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "the guard runs on the Linux runner, in bash")
class RefGuard(unittest.TestCase):
    """.github/ci/terrain-ref.sh: what a run started by hand may test."""

    def git(self, cwd, *args):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.path.join(self.tmp, "gitconfig"),
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        return subprocess.run(["git", "-C", cwd] + list(args), env=env, check=True,
                              capture_output=True).stdout.decode().strip()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="terrain-ref-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        origin, self.clone = os.path.join(self.tmp, "origin"), os.path.join(self.tmp, "clone")
        os.makedirs(origin)
        self.git(origin, "init", "-q", "-b", "master")
        self.git(origin, "commit", "-q", "--allow-empty", "-m", "one")
        self.git(origin, "tag", "v0.2.0")
        self.master = self.git(origin, "rev-parse", "HEAD")
        self.git(origin, "checkout", "-q", "-b", "feat/x")
        self.git(origin, "commit", "-q", "--allow-empty", "-m", "two")
        self.feat = self.git(origin, "rev-parse", "HEAD")
        # A fork's pull request: its commit is reachable through refs/pull/1/head only.
        self.git(origin, "checkout", "-q", "--detach", "master")
        self.git(origin, "commit", "-q", "--allow-empty", "-m", "fork")
        self.fork = self.git(origin, "rev-parse", "HEAD")
        self.git(origin, "update-ref", "refs/pull/1/head", self.fork)
        self.git(origin, "checkout", "-q", "master")
        self.git(self.tmp, "clone", "-q", origin, self.clone)
        self.git(self.clone, "fetch", "-q", "origin", "refs/pull/1/head")   # the object is here now

    def guard(self, ref):
        out = os.path.join(self.tmp, "github-output")
        if os.path.exists(out):
            os.remove(out)
        p = subprocess.run(["bash", REF_GUARD, ref], cwd=self.clone, capture_output=True,
                           env=dict(os.environ, GITHUB_OUTPUT=out))
        said = p.stdout.decode() + p.stderr.decode()
        if not os.path.exists(out):
            return p.returncode, None, said
        with open(out, encoding="utf-8") as f:
            return p.returncode, f.read().strip(), said

    def test_a_branch_a_tag_or_a_commit_on_a_branch(self):
        for ref, sha in (("master", self.master), ("feat/x", self.feat), ("v0.2.0", self.master),
                         (self.feat, self.feat)):
            with self.subTest(ref=ref):
                self.assertEqual(self.guard(ref)[:2], (0, "sha=" + sha))

    def test_a_forks_commit_or_anything_else_is_refused(self):
        for ref in ("refs/pull/1/head", "refs/pull/1/merge", self.fork, "pull/1/head", "-x", "--upload-pack=x",
                    "master..feat/x", "feat x", "", "inconnue", self.master[:12], "refs/heads/master", "/master"):
            with self.subTest(ref=ref):
                rc, out, said = self.guard(ref)
                self.assertEqual((rc, out), (1, None), said)
                self.assertIn("::error::terrain: ref refused", said)


def workflow(path=WORKFLOW):
    """A workflow as data (read by ruby's YAML: `on` comes back as the key "true")."""
    p = subprocess.run(["ruby", "-ryaml", "-rjson", "-e", "puts JSON.dump(YAML.safe_load(File.read(ARGV[0])))",
                        path], capture_output=True, check=True)
    return json.loads(p.stdout.decode("utf-8"))


@unittest.skipUnless(shutil.which("ruby"), "terrain.yml is read with ruby's YAML (the macOS runner has it)")
class Workflow(unittest.TestCase):
    """What terrain.yml promises about the token and the tested commit, checked on the file itself."""

    def setUp(self):
        self.wf = workflow()
        self.jobs = self.wf["jobs"]

    def test_the_token_reaches_one_step_of_the_claude_job_on_master_only(self):
        for name, job in self.jobs.items():
            if name != "claude":
                self.assertNotIn(TOKEN, json.dumps(job), name)
        claude = self.jobs["claude"]
        holders = [st for st in claude["steps"] if TOKEN in json.dumps(st)]
        self.assertEqual(len(holders), 1)
        self.assertEqual(list(holders[0]["env"]), ["CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertIn("env.TERRAIN_JETON == 'master' &&", holders[0]["env"]["CLAUDE_CODE_OAUTH_TOKEN"])
        master = "github.ref == 'refs/heads/master' && (inputs.ref || 'master') == 'master'"
        self.assertIn(master, claude["env"]["TERRAIN_JETON"])
        self.assertIn(master, claude["environment"])
        self.assertIn("'terrain-claude'", claude["environment"])
        # Nothing of the kit is installed before it on that VM: Anthropic's installer only.
        text = json.dumps(claude["steps"])
        for kit in ("./install.sh", "install.ps1\"", "terrain-install", "gbrain", "terrain.py installation"):
            self.assertNotIn(kit, text)

    def test_every_tested_commit_comes_through_the_guard(self):
        ref = self.jobs["ref"]
        self.assertIn(".github/ci/terrain-ref.sh", json.dumps(ref["steps"]))
        own = [st for st in ref["steps"] if str(st.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(len(own), 1)
        self.assertNotIn("ref", own[0].get("with", {}), "the guard is the workflow's own code")
        self.assertEqual(own[0]["with"]["fetch-depth"], 0)
        for name, job in self.jobs.items():
            if name in ("ref", "rapport"):
                continue
            self.assertIn("ref", job["needs"] if isinstance(job["needs"], list) else [job["needs"]], name)
            for st in job["steps"]:
                if str(st.get("uses", "")).startswith("actions/checkout@"):
                    self.assertEqual(st["with"]["ref"], "${{ needs.ref.outputs.sha }}", name)
            self.assertEqual(job["env"]["TERRAIN_SHA"], "${{ needs.ref.outputs.sha }}", name)

    def test_a_run_started_by_hand_tests_its_own_branch(self):
        on = self.wf["true"]
        self.assertEqual(sorted(on), ["schedule", "workflow_dispatch"], "nothing a stranger can trigger")
        self.assertEqual(on["workflow_dispatch"]["inputs"]["ref"]["default"], "")
        self.assertEqual(self.wf["env"]["TERRAIN_REF"], "${{ inputs.ref || github.ref_name }}")
        self.assertTrue(terrain.REPRO_VM.startswith("gh workflow run terrain.yml --ref <branche>"))
        group = self.wf["concurrency"]["group"]
        self.assertIn("github.event_name", group)
        self.assertIn("inputs.ref || github.ref_name", group)


    def test_a_job_has_time_for_every_step_it_runs(self):
        # A job cut by its own timeout kills a scenario before it records: the steps' budgets fit in it.
        for name, job in self.jobs.items():
            paths = (job.get("strategy") or {}).get("matrix", {}).get("parcours") or [None]
            for path in paths:
                steps = [st for st in job["steps"] if path is None or "matrix.parcours" not in st.get("if", "")
                         or "matrix.parcours == '%s'" % path in st["if"]]
                budget = sum(st.get("timeout-minutes", 0) for st in steps)
                if budget:
                    self.assertGreaterEqual(job["timeout-minutes"], budget + 10, "%s (%s)" % (name, path))

    def test_the_report_knows_every_job_and_every_path(self):
        rapport = self.jobs["rapport"]
        self.assertEqual(sorted(rapport["needs"]), sorted(n for n in self.jobs if n != "rapport"))
        self.assertEqual(sorted(rapport["env"]["JOBS"].split(",")),
                         sorted("%s=${{ needs.%s.result }}" % (n, n) for n in terrain.JOBS))
        self.assertEqual(sorted(terrain.JOBS), sorted(n for n in self.jobs if n != "rapport"))
        want = sorted("%s-%s" % (o, pa) for pa in terrain.ATTENDUS for o in terrain.ATTENDUS_OS.get(pa, ("macos", "windows")))
        self.assertEqual(sorted(rapport["env"]["ATTENDUS"].split(",")), want)
        runs = json.dumps(rapport["steps"])
        self.assertEqual(runs.count('--jobs \\"$JOBS\\"'), 2, "rapport and issue both get the jobs' results")
        # Green again on a scheduled night: the issue is closed, not left open with a comment.
        self.assertIn('gh issue close \\"$open\\"', runs)


    def test_the_app_job_gets_the_app_as_a_user_and_never_a_claude_token(self):
        job = self.jobs["app-macos"]
        steps = job["steps"]
        text = json.dumps(steps)
        # A fresh VM: nothing of the kit, nor Claude Code, before the app installs them itself.
        for kit in ("./install.sh", "install.ps1", "claude.ai/install", "terrain-install", "gbrain", "CLAUDE_CODE_OAUTH_TOKEN"):
            self.assertNotIn(kit, text)
        source = [st for st in steps if st.get("id") == "source"]
        self.assertEqual(len(source), 1)
        self.assertIn('terrain_app.py source --ref "$TERRAIN_REF"', source[0]["run"])
        builds = [st for st in steps if "cargo" in json.dumps(st)]
        self.assertGreaterEqual(len(builds), 2)
        for st in builds:
            self.assertEqual(st["if"], "steps.source.outputs.source == 'build'", st.get("name"))
        # The read-only token reaches the scenario step only, and only to download a release.
        holders = [st for st in steps if "github.token" in json.dumps(st)]
        self.assertEqual(len(holders), 1)
        self.assertEqual(holders[0]["env"]["GH_TOKEN"], "${{ steps.source.outputs.source == 'release' && github.token || '' }}")
        self.assertIn("terrain.py app", holders[0]["run"])
        self.assertEqual(job["permissions"], {"contents": "read", "attestations": "read"})
        # The JSON joins the report's artifacts (terrain-*); the captures stay out of its download.
        names = [st["with"]["name"] for st in steps if str(st.get("uses", "")).startswith("actions/upload-artifact@")]
        self.assertEqual(sorted(names), ["captures-macos-app", "terrain-macos-app"])
        self.assertIn("pattern: terrain-*", "pattern: " + self.jobs["rapport"]["steps"][1]["with"]["pattern"])
        self.assertEqual(job["env"]["PARCOURS"], "app")
        self.assertIn("app", terrain.ATTENDUS["app"])

    def test_a_fresh_windows_has_no_python_in_both_workflows(self):
        # One description of a fresh PC (.github/ci/poste-neuf.ps1), used by both installs: the
        # terrain's nightly install exercises winget's Python too, and the two cannot drift apart.
        tests = workflow(WORKFLOW.replace("terrain.yml", "test.yml"))["jobs"]["windows"]["steps"]
        fresh = [st for st in tests if "Fresh install" in st.get("name", "")]
        terrain_fresh = [st for st in self.jobs["windows"]["steps"] if st.get("id") == "installeur"]
        for st in fresh + terrain_fresh:
            self.assertTrue(st["run"].startswith(". .github/ci/poste-neuf.ps1\n"), st["name"])
        self.assertEqual((len(fresh), len(terrain_fresh)), (1, 1))
        self.assertNotIn("hostedtoolcache", json.dumps(tests), "a second copy of the fresh PC in test.yml")


class MergeGuard(unittest.TestCase):
    """.github/ci/terrain-garde.sh: the check that keeps the agent's merges out of the maintainer's paths."""

    ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

    def garde(self, files, head="claude/terrain-m1", repo="BrainInBox/brain-in-a-box"):
        tmp = tempfile.mkdtemp(prefix="terrain-garde-")
        self.addCleanup(shutil.rmtree, tmp, True)
        rows = "".join("%s\t%s\t%d\n" % (n, prev or "-", d) for n, prev, d in files)
        with open(os.path.join(tmp, "rows"), "w", encoding="utf-8", newline="") as f:
            f.write(rows)
        # A stand-in for gh: prints the rows the real `gh api --jq ... @tsv` would.
        with open(os.path.join(tmp, "gh"), "w", encoding="utf-8", newline="") as f:
            f.write('#!/bin/sh\ncat "%s"\n' % os.path.join(tmp, "rows"))
        os.chmod(os.path.join(tmp, "gh"), 0o755)
        env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], PR="7", HEAD_REF=head,
                   HEAD_REPO=repo, GITHUB_REPOSITORY="BrainInBox/brain-in-a-box")
        p = subprocess.run(["bash", MERGE_GUARD], cwd=self.ROOT, env=env, capture_output=True)
        return p.returncode, p.stdout.decode() + p.stderr.decode()

    @unittest.skipIf(sys.platform == "win32", "the check runs on ubuntu-latest")
    def test_a_fix_of_the_night_check_with_a_new_test_passes(self):
        rc, said = self.garde([("engine/hooks/_controle_rules.py", "", 3), ("engine/tests/test_controle.py", "", 0),
                               ("docs/terrain-bugs.md", "", 0)])
        self.assertEqual(rc, 0, said)

    @unittest.skipIf(sys.platform == "win32", "the check runs on ubuntu-latest")
    def test_the_maintainers_paths_or_a_weakened_test_go_red(self):
        for files in ([("install.ps1", "", 1)], [(".github/workflows/terrain.yml", "", 0)],
                      [("engine/nightly/gbrain-nightly.sh", "", 0)], [("engine/hooks/_redact.py", "", 2)],
                      [("docs/terrain-agent.md", "", 1)], [("engine/hooks/x.py", "engine/hooks/_headless_claude.py", 0)],
                      [("engine/tests/test_controle.py", "", 1)], [("test-hooks.sh", "", 4)]):
            with self.subTest(files=files):
                rc, said = self.garde(files)
                self.assertEqual(rc, 1, said)
                self.assertIn("::error::terrain-garde", said)

    @unittest.skipIf(sys.platform == "win32", "the check runs on ubuntu-latest")
    def test_other_branches_pass_and_a_forks_agent_branch_fails(self):
        self.assertEqual(self.garde([("install.ps1", "", 9)], head="feat/x")[0], 0)
        rc, said = self.garde([("README.md", "", 0)], repo="someone/brain-in-a-box")
        self.assertEqual(rc, 1, said)

    def test_the_check_judges_from_master_and_never_runs_the_pr(self):
        with open(os.path.join(self.ROOT, ".github", "workflows", "terrain-garde.yml"), encoding="utf-8") as f:
            wf = f.read()
        self.assertIn("pull_request_target:", wf)
        self.assertNotIn("head.sha", wf)
        self.assertNotIn("ref:", wf.split("actions/checkout@v4", 1)[1].split("- name:", 1)[0])
        self.assertIn("contents: read", wf)
        self.assertNotIn(": write", wf)


class RepairAgentLimits(unittest.TestCase):
    """docs/terrain-agent.md's "Never merge these yourself" and .github/CODEOWNERS name the same paths."""

    ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

    def owned(self):
        with open(os.path.join(self.ROOT, ".github", "CODEOWNERS"), encoding="utf-8") as f:
            lines = [l.split() for l in f if l.strip() and not l.startswith("#")]
        self.assertTrue(all(len(l) == 2 and l[0].startswith("/") and l[1].startswith("@") for l in lines), lines)
        return [l[0][1:] for l in lines]

    def listed(self):
        with open(os.path.join(self.ROOT, "docs", "terrain-agent.md"), encoding="utf-8") as f:
            text = f.read()
        section = text.split("## Never merge these yourself", 1)[1].split("\n## ", 1)[0]
        paths = []
        for token in re.findall(r"`([^`\s]+)`", section):
            token = token.rstrip("*")
            if token.strip("./") and os.path.exists(os.path.join(self.ROOT, token)):
                paths.append(token)
        return paths

    def test_every_path_the_agent_must_not_merge_has_an_owner(self):
        owned, listed = self.owned(), self.listed()
        self.assertGreaterEqual(len(listed), 10)
        for path in listed:
            self.assertTrue(any(path == o or (o.endswith("/") and (path + "/").startswith(o)) for o in owned),
                            "%s is listed in terrain-agent.md, not in CODEOWNERS" % path)
        for o in owned:
            self.assertTrue(os.path.exists(os.path.join(self.ROOT, o)), "CODEOWNERS names %s, which is gone" % o)
            self.assertTrue(any(p == o or p.startswith(o) for p in listed),
                            "CODEOWNERS names %s, terrain-agent.md does not" % o)
        for must in ("install.ps1", "install.sh", "engine/nightly/", "engine/tests/terrain.py",
                     "engine/tests/vault_piege.py", "docs/terrain-agent.md", ".github/"):
            self.assertIn(must, owned)


class Score(unittest.TestCase):
    def test_points_and_annotations(self):
        old, new = terrain.PAIRES[3][0], terrain.PAIRES[3][4]
        j = {"actions": [{"id": "C1-1", "type": "C1", "statut": "fait", "chemins": [old],
                          "resume": {"new": os.path.basename(new)[:-3]}},
                         {"id": "C1-2", "type": "C1", "statut": "fait", "chemins": [new],
                          "resume": {"new": os.path.basename(old)[:-3]}}],
             "points": [{"code": "juge_contradiction", "chemin": terrain.PAIRES[4][4]},
                        {"code": "juge_contradiction", "chemin": terrain.LEURRES[1][0]},
                        {"code": "juge_proche_doublon", "chemin": terrain.LEURRES[2][0]}]}
        s = terrain.score(j)
        self.assertEqual((s["trouvees"], s["constats"], s["justes"], s["annotations_fausses"]), (2, 4, 2, ["C1-2"]))
        self.assertEqual(terrain.score({})["precision"], None)


class Reports(Sandbox):
    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return path

    def files(self):
        leak = "password: " + "hunter2-ci-" + "canari"
        a = self.write("a.json", {"os": "windows", "parcours": "neuf", "ref": "ab" * 20, "scenarios": [
            {"scenario": "nuits", "statut": "echec", "etape": "nuit-2", "raison": "la note dit « %s »" % leak,
             "reproduire": "rm -rf /"},
            {"scenario": "vrai-claude", "statut": "saute", "etape": None, "raison": "pas de jeton"},
            {"scenario": "inventé", "statut": "echec", "etape": "`$(touch x)`", "raison": "x"}]})
        b = self.write("b.json", {"os": "macos", "parcours": "neuf", "scenarios": [
            {"scenario": "installation", "statut": "ok", "etape": "planificateur", "raison": "conforme"}]})
        return [a, b], leak

    def test_the_issue_holds_fixed_identifiers_only(self):
        files, leak = self.files()
        rc, out = self.terrain("issue", *files, "--attendus", "macos-neuf,windows-neuf,windows-montee",
                               "--run-url", "https://github.com/BrainInBox/brain-in-a-box/actions/runs/42",
                               "--date", "2026-10-15")
        self.assertEqual(rc, 0, out)
        self.assertIn("Terrain du 2026-10-15 : 7 échec(s).", out)
        self.assertIn("| windows | neuf | nuits | nuit-2 |", out)
        self.assertIn("| windows | neuf | ? | ? |", out)
        self.assertIn("| windows | neuf | installeur | absent |", out)
        self.assertIn("| macos | neuf | nuits | absent |", out)
        self.assertIn("| windows | montee | job | aucun-resultat |", out)
        self.assertIn("https://github.com/BrainInBox/brain-in-a-box/actions/runs/42", out)
        self.assertIn(terrain.REPRO["nuits"], out)
        for bad in (leak, "canari", "la note dit", "rm -rf", "touch", "inventé", "pas de jeton"):
            self.assertNotIn(bad, out)

    def green(self):
        return [self.write("n.json", {"os": "macos", "parcours": "neuf", "scenarios": [
                    {"scenario": s, "statut": "ok"} for s in terrain.ATTENDUS["neuf"]]}),
                self.write("c.json", {"os": "macos", "parcours": "claude", "scenarios": [
                    {"scenario": "vrai-claude", "statut": "saute"}]})]

    def test_a_green_night_gives_no_issue(self):
        rc, out = self.terrain("issue", *self.green(), "--attendus", "macos-neuf,macos-claude",
                               "--jobs", "ref=success,macos=success,claude=success", "--run-url", "javascript:alert(1)")
        self.assertEqual((rc, out.strip()), (0, ""))

    def test_nothing_missing_passes_for_green(self):
        # A step killed by its timeout, or a guard that refused to run, writes no row.
        only = self.write("o.json", {"os": "windows", "parcours": "montee", "scenarios": [
            {"scenario": "installeur-v0.2.0", "statut": "ok"}]})
        rc, out = self.terrain("issue", only, "--attendus", "windows-montee", "--date", "2026-10-15")
        self.assertIn("Terrain du 2026-10-15 : 3 échec(s).", out)
        for name in ("montee-avant", "installeur-montee", "montee"):
            self.assertIn("| windows | montee | %s | absent |" % name, out)
        # Every row there, yet the job did not succeed (an upload, a checkout...): said too.
        rc, out = self.terrain("issue", *self.green(), "--attendus", "macos-neuf,macos-claude",
                               "--jobs", "ref=success,macos=cancelled,claude=success,inconnu=failure,windows=bizarre")
        self.assertIn("1 échec(s)", out)
        self.assertIn("| macos | ? | job | macos-cancelled |", out)
        self.assertNotIn("inconnu", out)
        # A job whose own rows already say what failed gets no second row.
        rc, out = self.terrain("issue", only, "--attendus", "windows-montee", "--jobs", "windows=failure")
        self.assertNotIn("windows-failure", out)
        # The commit to test refused: nothing ran, every expected path says so.
        rc, out = self.terrain("issue", "--attendus", "macos-neuf,windows-claude", "--jobs",
                               "ref=failure,macos=skipped,claude=skipped")
        self.assertIn("| tous | ? | job | ref-failure |", out)
        self.assertIn("| macos | neuf | job | aucun-resultat |", out)
        self.assertIn("| windows | claude | job | aucun-resultat |", out)

    def test_the_summary_table(self):
        files, _ = self.files()
        rc, out = self.terrain("rapport", *files, "--attendus", "macos-neuf,macos-montee")
        self.assertEqual(rc, 0, out)
        self.assertIn("### Terrain windows · neuf · `abababababab`", out)
        self.assertIn("| nuits | ÉCHEC | nuit-2 |", out)
        self.assertIn("| vrai-claude | SAUTÉ |", out)
        self.assertIn("aucun résultat : job interrompu, annulé ou trop long", out)
        self.assertEqual(out.count("### Terrain"), 3)


if __name__ == "__main__":
    unittest.main()
