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
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TERRAIN = os.path.join(HERE, "terrain.py")
REF_GUARD = os.path.join(HERE, "..", "..", ".github", "ci", "terrain-ref.sh")
MERGE_GUARD = os.path.join(HERE, "..", "..", ".github", "ci", "terrain-garde.sh")
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "terrain.yml")
TERMINAL = os.path.join(HERE, "..", "..", ".github", "ci", "terminal.py")
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
kind = "constats" if '"constats"' in schema else "import" if '"faits"' in schema else "reflexion"
if kind in plan.get("quota_on", []):
    with open(plan["quota"], encoding="utf-8") as f:   # what Claude Code prints at the usage limit
        sys.stdout.write(f.read())
    sys.exit(1)
if kind == "import":   # three facts, each citing a block of the request, unless the plan says none
    refs = re.findall(r"^\[([a-z]\d+) \u00b7", stdin, re.M)
    answer = {"faits": [{"categorie": "identite", "texte": "Fait num\u00e9ro %%s de l'import." %% w,
                         "sources": [] if plan.get("sans_source") else [refs[k %% len(refs)]], "date": "",
                         "confiance": "sur"} for k, w in enumerate(("un", "deux", "trois"))], "suspect": False}
elif kind == "constats":
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
    def test_a_scenario_killed_mid_step_leaves_a_failed_row_naming_the_step(self):
        # A job step's timeout kills the harness before it records: the row it left says where.
        seen = []
        opts = types.SimpleNamespace(json=self.json, home=Path(self.home), parcours="app", vm=True)

        def scenario(t, o):
            t("app-etape-brain")
            seen.append(self.results()["app"])
            t.check(False, "x")
        self.assertEqual(terrain.play("app", scenario, opts), "echec")
        self.assertEqual((seen[0]["statut"], seen[0]["etape"]), ("echec", "app-etape-brain"))
        self.assertIn("interrompu pendant l'étape « app-etape-brain » : délai de l'étape du job dépassé", seen[0]["raison"])
        self.assertEqual((self.results()["app"]["etape"], self.results()["app"]["raison"]), ("app-etape-brain", "x"))
        rows = terrain.load_reports([self.json], [])[0][2]["scenarios"]
        self.assertEqual([r["etape"] for r in rows], ["app-etape-brain"])

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
    def stub(self, constats, quota_on=(), refuse=False, sans_source=False):
        here = os.path.join(self.home, ".terrain-stub")
        os.makedirs(here)
        plan = {"help": HELP, "init": OK_STREAM[0], "assistant": OK_STREAM[1], "user": OK_STREAM[2],
                "result": OK_STREAM[-1], "constats": constats, "reflexion": REFLEXION,
                "quota": os.path.join(FIXTURES, "quota.jsonl"), "quota_on": list(quota_on), "refuse": refuse,
                "sans_source": sans_source}
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
            self.assertEqual(f.read().split(), ["token"] * 4,
                             "one review, one reflection, one request per import, the token passed on")
        self.assertNotIn("factice-pas-un-secret", json.dumps(r) + out)
        self.assertEqual((r["details"]["import"]["zip"]["faits"], r["details"]["import"]["collage"]["faits"]), (3, 3))
        self.assertIn("import : 3 + 3 fait(s)", r["raison"])

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
            self.assertEqual(f.read().split(), ["token"] * 4)

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

    def test_the_import_counts_its_facts_and_skips_at_the_usage_limit(self):
        self.stub([constat(p, "contradiction") for p in terrain.PAIRES], sans_source=True)
        rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 1, out)
        r = self.results()["vrai-claude"]
        self.assertEqual((r["statut"], r["etape"]), ("echec", "import-zip"))
        self.assertIn("0 fait(s) tiré(s) de l'export", r["raison"])
        shutil.rmtree(self.home)
        os.makedirs(self.home)
        self.stub([constat(p, "contradiction") for p in terrain.PAIRES], quota_on=["import"])
        rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 0, out)
        r = self.results()["vrai-claude"]
        self.assertEqual(r["statut"], "saute", out)
        self.assertIn("(import)", r["raison"])

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


    def test_the_app_jobs_get_the_app_as_a_user_and_never_a_claude_token(self):
        for name, os_name, ref in (("app-macos", "macos", '"$TERRAIN_REF"'), ("app-windows", "windows", '"$env:TERRAIN_REF"'),
                                   ("app-linux", "linux", '"$TERRAIN_REF"')):
            job = self.jobs[name]
            steps = job["steps"]
            text = json.dumps(steps)
            # A fresh VM: nothing of the kit, nor Claude Code, before the app installs them itself.
            for kit in ("./install.sh", "install.ps1", "claude.ai/install", "terrain-install", "gbrain", "CLAUDE_CODE_OAUTH_TOKEN"):
                self.assertNotIn(kit, text, name)
            source = [st for st in steps if st.get("id") == "source"]
            self.assertEqual(len(source), 1, name)
            self.assertIn("terrain_app.py source --ref " + ref, source[0]["run"])
            # Linux builds in a job of its own, on 22.04 as release.yml does (app-linux-build).
            builder = self.jobs.get(name + "-build", job)
            builds = [st for st in builder["steps"] if "cargo" in json.dumps(st)]
            self.assertGreaterEqual(len(builds), 2, name)
            for st in builds + [st for st in steps if "download-artifact" in st.get("uses", "")]:
                self.assertTrue(st["if"].startswith("steps.source.outputs.source == 'build'"), st.get("name"))
            # The read-only token reaches the scenario step only, and only to download a release.
            holders = [st for st in steps if "github.token" in json.dumps(st)]
            self.assertEqual(len(holders), 1, name)
            self.assertEqual(holders[0]["env"]["GH_TOKEN"], "${{ steps.source.outputs.source == 'release' && github.token || '' }}")
            self.assertIn("terrain.py app", holders[0]["run"])
            self.assertEqual(job["permissions"], {"contents": "read", "attestations": "read"})
            # The JSON joins the report's artifacts (terrain-*); the captures stay out of its download.
            names = [st["with"]["name"] for st in steps if str(st.get("uses", "")).startswith("actions/upload-artifact@")]
            self.assertEqual(sorted(names), ["captures-%s-${{ matrix.parcours }}" % os_name, "terrain-%s-${{ matrix.parcours }}" % os_name])
            # Two paths: Claude alone as users get it, and Codex chosen alone in the robot's copy of the kit.
            self.assertEqual(job["strategy"], {"fail-fast": False, "matrix": {"parcours": ["app", "app-codex"]}})
            self.assertEqual(job["env"]["PARCOURS"], "${{ matrix.parcours }}")
            self.assertEqual(terrain.JOBS[name], (os_name, ("%s-app" % os_name, "%s-app-codex" % os_name)))
        self.assertIn("pattern: terrain-*", "pattern: " + self.jobs["rapport"]["steps"][1]["with"]["pattern"])
        self.assertIn("app", terrain.ATTENDUS["app"])
        self.assertEqual(terrain.ATTENDUS["app-codex"], ("app",))
        # Windows: a PC without Python before the app (the description the other jobs use), and the
        # stub handed to the scenario, which puts it first on the app's PATH.
        steps = self.jobs["app-windows"]["steps"]
        fresh = [i for i, st in enumerate(steps) if st.get("run", "").startswith(". .github/ci/poste-neuf.ps1\n")]
        scenario = [i for i, st in enumerate(steps) if "terrain.py app" in st.get("run", "")]
        self.assertEqual(len(fresh), 1)
        self.assertLess(fresh[0], scenario[0])
        self.assertIn('"--stub=$env:TERRAIN_STUB"', steps[scenario[0]]["run"])

    def test_nothing_the_app_touched_gets_into_a_cache(self):
        # The app, then Claude Code, Bun and gbrain's dependencies run as the job's user: a cache
        # saved after them could carry a binary they rewrote to the next build, release.yml's
        # included. So: the terrain's own keys, saved right after the build, never on master.
        with open(WORKFLOW.replace("terrain.yml", "release.yml"), encoding="utf-8") as f:
            release = f.read()
        release_keys = re.findall(r"(?:key|restore-keys): ([a-z-]+)", release)
        self.assertEqual(sorted(set(release_keys)), ["app-release-", "tauri-cli-"])
        pinned = re.compile(r"^actions/cache/(restore|save)@[0-9a-f]{40}$")
        for name, job in self.jobs.items():
            steps = job["steps"]
            caches = [(i, st) for i, st in enumerate(steps) if str(st.get("uses", "")).startswith("actions/cache")]
            if name not in ("app-macos", "app-windows", "app-linux-build"):
                self.assertEqual(caches, [], name)
                continue
            app = min([i for i, st in enumerate(steps) if "terrain.py app" in st.get("run", "")
                       or "poste-neuf.ps1" in st.get("run", "")] or [len(steps)])
            kinds = []
            for i, st in caches:
                m = pinned.match(st["uses"])
                self.assertTrue(m, "%s: %s saves in a post step, or is not pinned" % (name, st["uses"]))
                kinds.append(m.group(1))
                key = st["with"]["key"]
                if m.group(1) == "restore":
                    self.assertTrue(key.startswith("terrain-"), key)
                    for prefix in release_keys:
                        self.assertFalse(key.startswith(prefix), key)
                else:
                    self.assertLess(i, app, "%s: saved after the app ran" % st["name"])
                    self.assertIn("github.ref != 'refs/heads/master'", st["if"])
                    self.assertRegex(key, r"^\$\{\{ steps\.[a-z-]+\.outputs\.cache-primary-key \}\}$")
            self.assertEqual(sorted(kinds), ["restore", "restore", "save", "save"], name)

    def test_the_linux_app_job_has_a_desktop_and_one_stand_in_only(self):
        # The window needs a display, a window manager and the session bus before the scenario; the
        # system's password window needs a person: one polkit rule, for pkexec, for this user only.
        steps = self.jobs["app-linux"]["steps"]
        desktop = [i for i, st in enumerate(steps) if st.get("name") == "A desktop for the app"]
        scenario = [i for i, st in enumerate(steps) if "terrain.py app" in st.get("run", "")]
        self.assertEqual(len(desktop), 1)
        self.assertLess(desktop[0], scenario[0])
        run = steps[desktop[0]]["run"]
        for need in ("Xvfb :99", "openbox", "xdotool", "gir1.2-atspi-2.0", "imagemagick", "pkexec", 'echo "DISPLAY=:99"'):
            self.assertIn(need, run)
        rules = re.findall(r"action\.id == \\?\"([a-z.]+)\\?\"", run)
        self.assertEqual(rules, ["org.freedesktop.policykit.exec"])
        self.assertIn('subject.user == \\"$USER\\"', run)
        self.assertNotIn("sudo -n true", run)
        # The polkit rule says what the window would show: the results compare it with root.rs.
        self.assertIn('polkit.spawn(["/usr/bin/logger", "-t", "fenetre-mot-de-passe", "--", "" + action.lookup("cmdline_short")])', run)
        # The AppImage is release.yml's: built on Ubuntu 22.04 with its command, its tools and runtime
        # pinned and checked, then handed to the 24.04 desktop.
        build_job = self.jobs["app-linux-build"]
        self.assertEqual(build_job["runs-on"], "ubuntu-22.04")
        self.assertEqual(self.jobs["app-linux"]["needs"], ["ref", "app-linux-build"])
        build = next(st for st in build_job["steps"] if st.get("name") == "Build the app from the tested commit (branch)")
        self.assertIn("--bundles appimage -- --locked", build["run"])
        self.assertIn('appimage-tools.sh" check "${images[0]}"', build["run"])
        tools = [st for st in build_job["steps"] if "appimage-tools.sh" in st.get("run", "") and st is not build]
        self.assertEqual(len(tools), 1, "the tools, and LDAI_RUNTIME_FILE, in a step before the build's")
        with open(os.path.join(os.path.dirname(os.path.dirname(WORKFLOW)), "ci", "app-release-plan.py"), encoding="utf-8") as f:
            self.assertIn('"os": "ubuntu-22.04"', f.read(), "release.yml builds on 22.04 too")

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

    def test_windows_starts_without_git_where_the_installer_brings_it(self):
        # The fresh install and the app: TERRAIN_SANS_GIT set before anything runs (the harness then
        # wants the proof, and fails without it); the upgrade keeps the runner's Git (v0.2.0's own).
        proof = '"TERRAIN_SANS_GIT=$env:RUNNER_TEMP\\sans-git.json"'
        first = self.jobs["windows"]["steps"][0]["run"]
        self.assertIn("if ($env:PARCOURS -eq 'neuf') {\n  " + proof, first)
        self.assertIn(proof, self.jobs["app-windows"]["steps"][0]["run"])
        for name, job in self.jobs.items():
            if name not in ("windows", "app-windows"):
                self.assertNotIn("TERRAIN_SANS_GIT", json.dumps(job), name)
        # windows neuf: terrain-install.ps1 zips the tested sources with git, then takes Git away, then installs.
        with open(os.path.join(os.path.dirname(os.path.dirname(WORKFLOW)), "ci", "terrain-install.ps1"), encoding="utf-8") as f:
            install = f.read()
        order = [install.index(s) for s in ("git archive", "sans-git.ps1') -Preuve $env:TERRAIN_SANS_GIT", "irm https://", "Invoke-Expression")]
        self.assertEqual(order, sorted(order))
        steps = self.jobs["windows"]["steps"]
        names = [st.get("name", "") for st in steps]
        harness = names.index("Git for the harness (the one the installer brought)")
        self.assertLess(names.index("Fresh install (irm | iex), no Python but the Store stub, no Git"), harness)
        self.assertLess(harness, next(i for i, st in enumerate(steps) if "terrain.py nuits" in st.get("run", "")))
        self.assertIn("matrix.parcours == 'neuf'", steps[harness]["if"])
        self.assertIn("GITHUB_PATH", steps[harness]["run"])


class WindowsWithoutGit(unittest.TestCase):
    """Windows starts without Git (.github/ci/sans-git.ps1): the proof the harness reads again probe by
    probe, and install.ps1's journal line for each winget attempt."""

    CI = os.path.join(HERE, "..", "..", ".github", "ci")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="terrain-sans-git-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def proof(self, **change):
        p = {"absent": True, "fin_epoch": 1760000000, "sondes": {k: [] for k in terrain.SONDES_GIT},
             "nouveau_processus": {"get_command": [], "where": [], "cmd_git_code": 9009},
             "desinstalle": [{"dossier": "C:\\Program Files\\Git", "version": "2.55.0.5", "code": 0, "secondes": 14}],
             "winget": {"chemin": "C:\\Users\\r\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe", "version": "v1.11.430"}}
        for k, v in change.items():
            p[k] = v
        path = os.path.join(self.tmp, "sans-git.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(p, f)
        return path

    def read(self, path):
        return terrain.preuve_sans_git(terrain.Etapes(), path)

    def refused(self, path, why):
        with self.assertRaises(terrain.Echec) as cm:
            self.read(path)
        self.assertIn(why, str(cm.exception))

    def test_the_proof_is_read_again_probe_by_probe(self):
        self.assertEqual(self.read(self.proof())["winget"]["version"], "v1.11.430")
        for k in terrain.SONDES_GIT:
            with self.subTest(sonde=k):
                # Its summary says absent: never trusted, each probe is read.
                sondes = dict({s: [] for s in terrain.SONDES_GIT}, **{k: ["C:\\Program Files\\Git\\cmd\\git.exe"]})
                self.refused(self.proof(sondes=sondes), "Git était encore trouvable avant l'installation : %s : C:\\Program" % k)
        sondes = {s: [] for s in terrain.SONDES_GIT if s != "app_paths"}
        self.refused(self.proof(sondes=sondes), "la preuve ne dit pas tout : sonde(s) app_paths absente(s)")
        self.refused(self.proof(nouveau_processus={"get_command": [], "where": ["C:\\Git\\cmd\\git.exe"], "cmd_git_code": 9009}),
                     "un nouveau processus : C:\\Git\\cmd\\git.exe")
        self.refused(self.proof(nouveau_processus={"get_command": [], "where": [], "cmd_git_code": 0}),
                     "un nouveau processus : git --version")
        self.refused(self.proof(nouveau_processus={"get_command": [], "where": []}), "ce qu'un nouveau processus trouvait")
        # Its output unread, its probes are empty: never taken for « nothing found ».
        self.refused(self.proof(nouveau_processus={"get_command": [], "where": [], "cmd_git_code": 9009,
                                                   "illisible": "powershell.exe : Access denied"}),
                     "ce qu'un nouveau processus trouvait : sa sortie est illisible (powershell.exe : Access denied)")
        self.refused(self.proof(nouveau_processus={"get_command": [], "where": [], "cmd_git_code": 9009, "illisible": ""}),
                     "sa sortie est illisible ()")
        self.refused(self.proof(desinstalle=[{"dossier": "C:\\Program Files\\Git", "code": 1}]),
                     "le désinstalleur de Git a échoué : C:\\Program Files\\Git (code 1)")
        self.refused(self.proof(winget=None), "pas de winget sur la VM")
        self.refused(self.proof(fin_epoch=None), "quand Git a été retiré")
        self.refused(os.path.join(self.tmp, "absente.json"), "pas de preuve que Git était absent")
        self.refused(None, "TERRAIN_SANS_GIT absent")

    def test_sans_git_ps1_writes_every_probe_the_harness_reads(self):
        with open(os.path.join(self.CI, "sans-git.ps1"), "rb") as f:
            src = f.read()
        self.assertEqual([i for i, c in enumerate(src) if c > 0x7f][:3], [], "Windows PowerShell 5.1 reads it as ANSI")
        text = src.decode("ascii")
        block = text[text.index("$sondes = [ordered]@{"):]
        block = block[:block.index("\n}\n")]
        self.assertEqual(re.findall(r"^    ([a-z_]+) +=", block, re.M), list(terrain.SONDES_GIT))
        for key in ("nouveau_processus", "cmd_git_code", "fin_epoch", "desinstalle", "winget"):
            self.assertIn(key, text)
        self.assertIn("'/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'", text)
        self.assertIn('throw "Git est encore trouvable', text)

    def test_claude_s_bash_is_read_from_its_debug_log(self):
        bash = "C:\\Program Files\\Git\\bin\\bash.exe"
        debug = '2026-10-15T22:47:01.120Z [DEBUG] Starting\n2026-10-15T22:47:01.124Z [DEBUG] Using bash path: "%s"\n' % bash
        self.assertEqual(terrain.bash_de_claude(terrain.Etapes(), debug, "Not logged in · Please run /login"), bash)

        def refused(debug, dit, why):
            with self.assertRaises(terrain.Echec) as cm:
                terrain.bash_de_claude(terrain.Etapes(), debug, dit)
            self.assertIn(why, str(cm.exception))
        # No Git Bash, in its words: the debug log's (2.1), or what it printed before stopping.
        refused("x\n2026-10-15T22:47:01.124Z [DEBUG] Git Bash not found; BashTool will be unavailable\ny\n", "Not logged in",
                "Claude Code ne trouve pas Git Bash ; il dit : « 2026-10-15T22:47:01.124Z [DEBUG] Git Bash not found; "
                "BashTool will be unavailable »")
        refused("", "Claude Code on Windows requires git-bash (https://git-scm.com/downloads/win).",
                "Claude Code ne trouve pas Git Bash ; il dit : « Claude Code on Windows requires git-bash")
        refused("", "Claude Code on Windows requires either Git for Windows (for bash) or PowerShell. Install one of:\n"
                "  - Git for Windows: https://git-scm.com/downloads/win\n  - PowerShell 7: https://aka.ms/powershell\n"
                "Or set CLAUDE_CODE_GIT_BASH_PATH to your bash.exe location.",
                "il dit : « Claude Code on Windows requires either Git for Windows (for bash) or PowerShell.")
        # Neither: its debug log changed, not Git Bash gone.
        refused("[DEBUG] a\n\n[DEBUG] Shell: C:\\Program Files\\Git\\bin\\bash.exe\n", "Not logged in",
                "format du journal de débogage de Claude Code changé : ni « Using bash path » ni « Git Bash not found » ; "
                "il dit : « Not logged in » ; fin du journal : [DEBUG] a | [DEBUG] Shell: C:\\Program Files")
        refused("", "(pas de réponse en 180 s)", "fin du journal : (vide)")
        # Only its last line goes in the reason.
        refused("", "Welcome\n\nNot logged in · Please run /login\n", "il dit : « Not logged in · Please run /login » ;")

    def test_codex_alone_asks_no_claude_code_for_git_bash(self):
        # Codex chosen alone: no Claude Code to ask which bash it takes (Codex runs its commands through
        # PowerShell, gbq.cmd is check_rest's); the rest of Git's checks are the same.
        home = Path(self.tmp) / "home"
        (home / ".brain-in-a-box").mkdir(parents=True)
        args = "install -e --id Git.Git --source winget --silent --accept-package-agreements --accept-source-agreements"
        (home / ".brain-in-a-box" / "install.log").write_text("> winget %s --scope user\nwinget : code 0\n" % args, encoding="utf-8")
        git_dir = Path(tempfile.mkdtemp(dir=self.tmp)) / "cmd"
        git_dir.mkdir()
        exe = str(git_dir / "git.exe")
        (git_dir.parent / "bin").mkdir()
        (git_dir.parent / "bin" / "bash.exe").write_bytes(b"MZ")
        asked = []

        def claude(t, opts, env):
            asked.append(opts.parcours)
            raise terrain.Echec("Claude Code n'est pas installé (~/.local/bin/claude.exe)")

        def run(argv, **kw):
            return (0, "abc123\n") if "rev-list" in argv else (0, "brain init\n")
        for parcours in ("app-codex", "app"):
            t = terrain.Etapes()
            opts = types.SimpleNamespace(home=home, parcours=parcours)
            with mock.patch.dict(os.environ, {terrain.terrain_app.SANS_GIT: self.proof()}), \
                    mock.patch.object(terrain, "git_de_session", lambda t: (exe, "git version 2.55.0.windows.5", {})), \
                    mock.patch.object(terrain, "run", run), mock.patch.object(terrain, "claude_et_git_bash", claude):
                if parcours == "app":
                    with self.assertRaises(terrain.Echec):
                        terrain.check_git(t, opts)
                else:
                    terrain.check_git(t, opts)
                    self.assertEqual(t.details["git"]["premier_commit"], "brain init")
                    self.assertTrue(t.details["git"]["resume"].endswith("coffre versionné (« brain init »)"), t.details["git"]["resume"])
        self.assertEqual(asked, ["app"])

    def test_install_ps1_notes_each_winget_attempt_in_its_journal(self):
        with open(os.path.join(HERE, "..", "..", "install.ps1"), encoding="utf-8") as f:
            ps1 = f.read()
        self.assertIn('Note-Winget "> winget $($a -join \' \')`r`nwinget : code $code"', ps1)
        self.assertIn('if ($script:LogOpen) { Add-Content -LiteralPath $LOG -Value "`r`n$t" -Encoding utf8 }', ps1)
        # The journal opens once the choice of agents is accepted (a refused one leaves nothing in it), with
        # Python's attempts kept until then, and before Git's.
        order = [ps1.index(s) for s in ('Winget-Install "Python.Python.3.12"', "# <<< agents", 'install =====" -Encoding utf8',
                                        "foreach ($n in $WingetNotes) { Note-Winget $n }", 'Winget-Install "Git.Git"')]
        self.assertEqual(order, sorted(order))
        args = "install -e --id Git.Git --source winget --silent --accept-package-agreements --accept-source-agreements"
        log = "\r\n===== 2026-10-15 01:20:00 install =====\r\n\r\n> winget %s --scope user\r\nwinget : code -1978335216\r\n" \
              "\r\n> winget %s\r\nwinget : code 0\r\n\r\n> winget install -e --id Python.Python.3.12 x\r\nwinget : code 0\r\n" % (args, args)
        found = [(m.group("args").endswith("--scope user"), m.group("code")) for m in terrain.WINGET_GIT.finditer(log)]
        self.assertEqual(found, [(True, "-1978335216"), (False, "0")])

    def test_a_fresh_windows_install_and_the_app_check_git_first(self):
        called = []

        def check_git(t, opts):
            called.append(opts.parcours)
            raise terrain.Echec("vu")
        home = Path(self.tmp)
        with mock.patch.object(terrain, "WINDOWS", True), mock.patch.object(terrain, "check_git", check_git):
            for parcours, fresh in (("neuf", True), ("app", True), ("app-codex", True), ("montee", False), ("montee", True)):
                with self.assertRaises(terrain.Echec):
                    terrain.check_install(terrain.Etapes(), types.SimpleNamespace(home=home, parcours=parcours), fresh=fresh)
        self.assertEqual(called, ["neuf", "app", "app-codex"])


@unittest.skipIf(sys.platform == "win32", "the Unix form of Codex's hooks; Windows' runs on its VM (app-windows, app-codex)")
class CodexAlone(unittest.TestCase):
    """terrain.check_codex: what the app's install with Codex chosen alone must have left, on a home made
    as install.sh leaves it, the kit's real correction-detector run as Codex runs it."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="terrain-codex-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        h = self.home
        hooks = h / ".claude" / "hooks" / "brain"
        hooks.mkdir(parents=True)
        for src in sorted((terrain.REPO / "engine" / "hooks").glob("*.py")):
            text = src.read_text(encoding="utf-8")
            if src.name == "_agents.py":
                text = terrain.terrain_app.ouvrir_porte(text)
            (hooks / src.name).write_text(text, encoding="utf-8")
            os.chmod(hooks / src.name, 0o755)
        state = h / ".brain-in-a-box"
        state.mkdir()
        self.agents = {"version": 1, "agents": ["codex"], "prefere": "codex", "codex_home": str(h / ".codex"),
                       "pose_par": "install.sh", "date": "2026-10-17"}
        self.write(state / "agents.json", json.dumps(self.agents))
        os.chmod(state / "agents.json", 0o600)
        self.write(h / ".local" / "bin" / "codex", "#!/bin/sh\necho 'codex-cli 0.162.1'\n")
        os.chmod(h / ".local" / "bin" / "codex", 0o755)
        groups = {}
        for event, names in terrain.WIRED.items():
            groups[event] = [{"hooks": [{"type": "command", "command": "%s --agent codex" % (hooks / n), "timeout": 30} for n in names]}]
        self.write(h / ".codex" / "hooks.json", json.dumps({"hooks": groups}))
        tables = "".join('[hooks.state."%s:%s:0:%d"]\ntrusted_hash = "sha256:x"\n\n' % (h / ".codex" / "hooks.json", e, i)
                         for e, names in terrain.WIRED.items() for i in range(len(names)))
        self.write(h / ".codex" / "config.toml", "# brain-in-a-box\n" + tables)
        self.write(h / ".codex" / "AGENTS.md", "# mine\n\n%s\n# AGENTS.md — Global directives\n" % terrain.KIT_MARK)
        self.write(h / "Documents" / "Brain" / "AGENTS.md", "Read CLAUDE.md first.\n")
        for d in (terrain.REPO / "skills").iterdir():
            if (d / "SKILL.md").is_file():
                self.write(h / ".agents" / "skills" / d.name / "SKILL.md", "x")

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def check(self):
        t = terrain.Etapes()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            terrain.check_codex(t, types.SimpleNamespace(home=self.home, parcours="app-codex"))
        return t

    def test_codex_alone_as_install_sh_leaves_it(self):
        t = self.check()
        self.assertEqual(t.details["codex"], "codex-cli 0.162.1")
        vus = json.loads((self.home / ".brain-in-a-box" / "codex-hooks-vus.json").read_text(encoding="utf-8"))
        self.assertIn("correction-detector", vus["hooks"])

    def test_what_is_not_codex_alone_fails(self):
        cases = [
            ("agents", lambda h: self.write(h / ".brain-in-a-box" / "agents.json", json.dumps(dict(self.agents, agents=["claude", "codex"]))),
             "agents.json ne dit pas Codex seul"),
            ("agents", lambda h: self.write(h / ".brain-in-a-box" / "agents.json", json.dumps(dict(self.agents, pose_par="install.ps1"))),
             "agents.json posé par install.ps1"),
            ("codex", lambda h: self.write(h / ".local" / "bin" / "codex", "#!/bin/sh\necho 'codex-cli 0.161.0'\n"), "codex --version"),
            ("hooks-codex", lambda h: self.write(h / ".codex" / "hooks.json", json.dumps({"hooks": {}})), "n'est pas branché"),
            ("hooks-codex", lambda h: self.write(h / ".codex" / "config.toml", ""), "le kit n'a approuvé que 0 hooks"),
            ("hooks-lances", lambda h: shutil.copy(terrain.REPO / "engine" / "hooks" / "_agents.py", h / ".claude" / "hooks" / "brain"),
             "ne laisse pas son signal de vie"),
            ("agents-md", lambda h: self.write(h / ".codex" / "AGENTS.md", "# mine\n"), "repère du kit est 0 fois"),
            ("skills", lambda h: shutil.rmtree(h / ".agents"), "non installé pour Codex"),
            ("sans-claude", lambda h: self.write(h / ".claude" / "settings.json", '{"hooks": "correction-detector.py"}'), "settings.json"),
            ("sans-claude", lambda h: self.write(h / ".local" / "bin" / "claude", "x"), "Claude Code est installé"),
        ]
        for step, damage, said in cases:
            with self.subTest(said=said):
                self.setUp()
                damage(self.home)
                with self.assertRaises(terrain.Echec) as cm:
                    self.check()
                self.assertIn(said, str(cm.exception))


    def test_the_kit_installed_is_the_robots_copy_with_codex_open(self):
        # check_install's files: the tested commit's, but _agents.py as the robot's copy has it (gate
        # open), and codex-features.json, which only a Codex install places.
        def installed():
            hooks = self.home / ".claude" / "hooks" / "brain"
            for name, src in terrain.kit_files():
                if not (hooks / name).exists():
                    shutil.copy(src, hooks / name)
            shutil.copy(terrain.REPO / "engine" / "codex-features.json", hooks)
            return hooks, types.SimpleNamespace(home=self.home, parcours="app-codex")
        with mock.patch.object(terrain, "check_codex", lambda t, o: None), \
                mock.patch.object(terrain, "check_rest", lambda *a: None):
            terrain.check_install(terrain.Etapes(), installed()[1])
            for damage, said in ((lambda h: shutil.copy(terrain.REPO / "engine" / "hooks" / "_agents.py", h), "_agents.py installé"),
                                 (lambda h: (h / "codex-features.json").unlink(), "codex-features.json n'est pas installé")):
                with self.subTest(said=said):
                    self.setUp()
                    hooks, opts = installed()
                    damage(hooks)
                    with self.assertRaises(terrain.Echec) as cm:
                        terrain.check_install(terrain.Etapes(), opts)
                    self.assertIn(said, str(cm.exception))


class MergeGuard(unittest.TestCase):
    """.github/ci/terrain-garde.sh: the check that keeps the agent's merges out of the maintainer's paths."""

    ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

    def garde(self, files, head="claude/terrain-m1", repo="BrainInBox/brain-in-a-box"):
        tmp = tempfile.mkdtemp(prefix="terrain-garde-")
        self.addCleanup(shutil.rmtree, tmp, True)
        # (name, previous name, deletions[, status]): GitHub's status, "modified" when not given.
        rows = "".join("%s\t%s\t%d\t%s\n" % (f[0], f[1] or "-", f[2], f[3] if len(f) > 3 else "modified") for f in files)
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
                               ("engine/tests/test_nouveau.py", "", 0, "added"), ("docs/terrain-bugs.md", "", 0),
                               ("engine/tests/fake_claude.py", "", 0)])
        self.assertEqual(rc, 0, said)

    @unittest.skipIf(sys.platform == "win32", "the check runs on ubuntu-latest")
    def test_a_new_module_beside_the_tests_goes_red(self):
        # engine/tests/uuid.py would be the uuid of whatever puts engine/tests on its path.
        for files in ([("engine/tests/uuid.py", "", 0, "added")], [("engine/tests/uuid/__init__.py", "", 0, "added")],
                      [("engine/tests/textwrap.py", "engine/tests/test_x.py", 0, "renamed")]):
            with self.subTest(files=files):
                rc, said = self.garde(files)
                self.assertEqual(rc, 1, said)
                self.assertIn("is a new module beside the tests", said)

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


class Imports(unittest.TestCase):
    def test_no_file_beside_the_harness_stands_for_the_standard_library(self):
        # The job `claude` runs terrain.py with the token in its environment: a uuid.py or a
        # textwrap.py dropped in engine/tests must never run in place of the standard library's.
        tmp = tempfile.mkdtemp(prefix="terrain-imports-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tests = os.path.join(tmp, "engine", "tests")
        os.makedirs(tests)
        for name in ("terrain.py", "terrain_app.py", "vault_piege.py", "import_fixtures.py"):
            shutil.copy(os.path.join(HERE, name), tests)
        marks = os.path.join(tmp, "ran")
        for name in ("uuid", "textwrap", "urllib", "unicodedata", "hashlib", "argparse", "json", "pwd", "winreg"):
            with open(os.path.join(tests, name + ".py"), "w", encoding="utf-8") as f:
                f.write("open(%r, 'a').write(%r)\n" % (marks, name + " "))
        p = subprocess.run([sys.executable, "-I", os.path.join(tests, "terrain.py"), "rapport"], capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertFalse(os.path.exists(marks), "ran in place of the standard library: " + (
            open(marks).read() if os.path.exists(marks) else ""))


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

    def test_linux_reinstalls_over_itself_and_nothing_missing_passes(self):
        # No earlier kit installs on Linux: its "reinstallation" path writes the rows of an upgrade.
        only = self.write("l.json", {"os": "linux", "parcours": "reinstallation", "scenarios": [
            {"scenario": "installeur", "statut": "ok"}]})
        rc, out = self.terrain("issue", only, "--attendus", "linux-reinstallation,linux-neuf", "--date", "2026-10-15")
        self.assertIn("Terrain du 2026-10-15 : 4 échec(s).", out)
        for name in ("montee-avant", "installeur-montee", "montee"):
            self.assertIn("| linux | reinstallation | %s | absent |" % name, out)
        self.assertIn("| linux | neuf | job | aucun-resultat |", out)
        # Every row green, yet the linux job did not succeed: said too.
        green = [self.write("ln.json", {"os": "linux", "parcours": "neuf", "scenarios": [
                     {"scenario": s, "statut": "ok"} for s in terrain.ATTENDUS["neuf"]]}),
                 self.write("lr.json", {"os": "linux", "parcours": "reinstallation", "scenarios": [
                     {"scenario": s, "statut": "ok"} for s in terrain.ATTENDUS["reinstallation"]]})]
        rc, out = self.terrain("issue", *green, "--attendus", "linux-neuf,linux-reinstallation",
                               "--jobs", "ref=success,linux=failure")
        self.assertIn("1 échec(s)", out)
        self.assertIn("| linux | ? | job | linux-failure |", out)

    def test_the_summary_table(self):
        files, _ = self.files()
        rc, out = self.terrain("rapport", *files, "--attendus", "macos-neuf,macos-montee")
        self.assertEqual(rc, 0, out)
        self.assertIn("### Terrain windows · neuf · `abababababab`", out)
        self.assertIn("| nuits | ÉCHEC | nuit-2 |", out)
        self.assertIn("| vrai-claude | SAUTÉ |", out)
        self.assertIn("aucun résultat : job interrompu, annulé ou trop long", out)
        self.assertEqual(out.count("### Terrain"), 3)


SYSTEMCTL = """#!/bin/sh
case "$*" in
  *is-enabled*) cat "%(state)s/enabled" 2>/dev/null || echo disabled ;;
  *is-active*) cat "%(state)s/active" 2>/dev/null || echo inactive ;;
  *list-timers*) echo "NEXT LEFT LAST PASSED UNIT ACTIVATES" ;;
esac
"""
CRONTAB = """#!/bin/sh
[ -f "%(state)s/crontab" ] && exec cat "%(state)s/crontab"
echo "no crontab for tester" >&2; exit 1
"""


@unittest.skipUnless(os.name == "posix", "systemd and cron: Linux (stand-ins run on a Mac too)")
class LinuxScheduler(unittest.TestCase):
    """check_scheduler's Linux half, against stand-ins for systemctl and crontab."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="terrain-linux-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state, bin_ = os.path.join(self.tmp, "state"), os.path.join(self.tmp, "bin")
        os.makedirs(self.state)
        os.makedirs(bin_)
        for name, text in (("systemctl", SYSTEMCTL), ("crontab", CRONTAB)):
            with open(os.path.join(bin_, name), "w", encoding="utf-8") as f:
                f.write(text % {"state": self.state})
            os.chmod(os.path.join(bin_, name), 0o755)
        saved_path, saved_linger = os.environ["PATH"], terrain.LINGER_DIR
        os.environ["PATH"] = bin_ + os.pathsep + saved_path
        terrain.LINGER_DIR = Path(self.tmp) / "linger"
        self.addCleanup(setattr, terrain, "LINGER_DIR", saved_linger)
        self.addCleanup(os.environ.__setitem__, "PATH", saved_path)
        self.home = Path(self.tmp) / "home"
        self.units = self.home / ".config" / "systemd" / "user"

    def timers(self, enabled="enabled", active="active", linger=True):
        self.units.mkdir(parents=True)
        for f in sorted((Path(terrain.REPO) / "engine" / "nightly" / "systemd").iterdir()):
            shutil.copy(str(f), str(self.units / f.name))
        for name, value in (("enabled", enabled), ("active", active)):
            with open(os.path.join(self.state, name), "w") as f:
                f.write(value + "\n")
        if linger:
            terrain.LINGER_DIR.mkdir()
            (terrain.LINGER_DIR / __import__("getpass").getuser()).touch()

    def cron(self, lines=terrain.CRON_LINES):
        with open(os.path.join(self.state, "crontab"), "w", encoding="utf-8") as f:
            f.write("MAILTO=me\n# >>> brain-in-a-box: the kit\n%s\n# <<< brain-in-a-box\n"
                    % "\n".join(l + " >> out 2>&1" for l in lines))

    def check(self, parcours):
        t = terrain.Etapes()
        terrain.check_linux_scheduler(t, types.SimpleNamespace(home=self.home, parcours=parcours))
        return t

    def fails(self, parcours, words):
        with self.assertRaises(terrain.Echec) as e:
            self.check(parcours)
        self.assertIn(words, str(e.exception))

    def test_the_fresh_install_runs_on_systemd_timers_that_catch_up(self):
        self.timers()
        t = self.check("neuf")
        self.assertEqual((t.details["planificateur"], t.details["linger"]), ("systemd", True))

    def test_a_timer_that_does_not_run_or_differs_fails(self):
        self.timers(active="inactive")
        self.fails("neuf", "gbrain-nightly.timer ne tourne pas (inactive)")
        with open(self.units / "gbrain-lint.timer", "a", encoding="utf-8") as f:
            f.write("# changed\n")
        with open(os.path.join(self.state, "active"), "w") as f:
            f.write("active\n")
        self.fails("neuf", "gbrain-lint.timer absent ou pas la version testée")

    def test_lingering_refused_is_a_failure_of_the_fresh_path_only(self):
        self.timers(linger=False)
        self.fails("neuf", "lingering absent")
        self.assertEqual(self.check("app").details["linger"], False)

    def test_systemd_and_cron_both_would_run_twice(self):
        self.timers()
        self.cron()
        self.fails("neuf", "deux fois")

    def test_the_reinstallation_without_a_terminal_runs_on_cron(self):
        self.cron()
        self.assertEqual(self.check("reinstallation").details["planificateur"], "cron")
        self.fails("neuf", "planificateur cron, attendu systemd")
        self.cron(terrain.CRON_LINES[:2])
        self.fails("reinstallation", "ligne cron absente : $HOME/.claude/hooks/brain/gbrain-lint.sh")

    def test_nothing_scheduled_fails(self):
        self.fails("app", "rien ne lance la nuit")


@unittest.skipUnless(os.name == "posix", "a pty: Linux and macOS")
class Terminal(unittest.TestCase):
    """.github/ci/terminal.py, which answers install.sh's questions in the field tests."""

    SCRIPT = ('read -rs -p "  Voyage key: " k; echo; echo "key=[$k]"; '
              'read -r -p "  Install Obsidian? [y/N] " a; echo "obsidian=$a"; [ -t 0 ] && echo tty; exit 5')

    def term(self, *answers):
        argv = [sys.executable, "-I", TERMINAL] + ["--answer=" + a for a in answers] + ["--", "bash", "-c", self.SCRIPT]
        p = subprocess.run(argv, capture_output=True, timeout=60)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    def test_each_question_gets_its_answer_in_a_real_terminal(self):
        rc, out = self.term("Voyage key:=", "Obsidian=y")
        self.assertEqual(rc, 5, out)
        for line in ("key=[]", "obsidian=y", "tty"):
            self.assertIn(line, out.replace("\r", "").splitlines())

    def test_a_question_nobody_expected_stops_the_run(self):
        rc, out = self.term("Voyage key:=")
        self.assertEqual(rc, 3, out)
        self.assertIn("no answer for the question 'Install Obsidian? [y/N]'", out)
        self.assertNotIn("obsidian=", out)

    def test_an_expected_question_never_asked_fails(self):
        p = subprocess.run([sys.executable, "-I", TERMINAL, "--answer=linger=y", "--", "true"], capture_output=True,
                           timeout=60)
        self.assertEqual(p.returncode, 4)
        self.assertIn("never asked about 'linger'", p.stdout.decode())


if __name__ == "__main__":
    unittest.main()
