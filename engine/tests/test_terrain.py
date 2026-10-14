"""Tests of the field-test harness itself (engine/tests/terrain.py): what runs off a VM, the
scoring of the real-Claude scenario, and what the nightly report and issue may say.

The real-Claude scenario runs here against a stand-in for claude (POSIX: CLAUDE_BIN must be an
executable). Every run happens in a throwaway home.

    python3 -I -m unittest discover -s engine/tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TERRAIN = os.path.join(HERE, "terrain.py")
REF_GUARD = os.path.join(HERE, "..", "..", ".github", "ci", "terrain-ref.sh")
WORKFLOW = os.path.join(HERE, "..", "..", ".github", "workflows", "terrain.yml")
TOKEN = "secrets.CLAUDE_CODE_OAUTH_TOKEN"
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import terrain  # noqa: E402
from test_judge import HELP, OK_STREAM  # noqa: E402

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
schema = args[args.index("--json-schema") + 1]
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
    def stub(self, constats):
        here = os.path.join(self.home, ".terrain-stub")
        os.makedirs(here)
        plan = {"help": HELP, "init": OK_STREAM[0], "assistant": OK_STREAM[1], "user": OK_STREAM[2],
                "result": OK_STREAM[-1], "constats": constats, "reflexion": REFLEXION}
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


def workflow():
    """terrain.yml as data (read by ruby's YAML: `on` comes back as the key "true")."""
    p = subprocess.run(["ruby", "-ryaml", "-rjson", "-e", "puts JSON.dump(YAML.safe_load(File.read(ARGV[0])))",
                        WORKFLOW], capture_output=True, check=True)
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
        self.assertIn("Terrain du 2026-10-15 : 3 échec(s).", out)
        self.assertIn("| windows | neuf | nuits | nuit-2 |", out)
        self.assertIn("| windows | neuf | ? | ? |", out)
        self.assertIn("| windows | montee | job | aucun-resultat |", out)
        self.assertIn("https://github.com/BrainInBox/brain-in-a-box/actions/runs/42", out)
        self.assertIn(terrain.REPRO["nuits"], out)
        for bad in (leak, "canari", "la note dit", "rm -rf", "touch", "inventé", "pas de jeton"):
            self.assertNotIn(bad, out)

    def test_a_green_night_gives_no_issue(self):
        b = self.write("b.json", {"os": "macos", "parcours": "neuf", "scenarios": [
            {"scenario": "nuits", "statut": "ok"}, {"scenario": "vrai-claude", "statut": "saute"}]})
        rc, out = self.terrain("issue", b, "--attendus", "macos-neuf", "--run-url", "javascript:alert(1)")
        self.assertEqual((rc, out.strip()), (0, ""))

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
