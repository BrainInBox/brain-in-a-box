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

    def test_an_annotation_on_a_decoy_fails(self):
        self.stub([constat(terrain.PAIRES[2], "contradiction"), constat(terrain.LEURRES[0], "contradiction")])
        rc, out = self.terrain("vrai-claude", "--home", self.home, "--json", self.json)
        self.assertEqual(rc, 1, out)
        r = self.results()["vrai-claude"]
        self.assertEqual((r["statut"], r["etape"]), ("echec", "mesure"))
        self.assertIn("annotation fausse : C1-", r["raison"])


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
