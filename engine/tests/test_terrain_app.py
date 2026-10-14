"""Tests of the field test "app" (engine/tests/terrain_app.py) off a VM: where the app comes from,
what the robot reads in the window, and the walk through it, played by a fake driver that shows
the app's screens with the app's own words.

    python3 -I -m unittest engine/tests/test_terrain_app.py -v
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import terrain  # noqa: E402
import terrain_app as ta  # noqa: E402

NB = " "   # what the app's nbsp() puts before ! ? : ;


class Horloge:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def dormir(self, s):
        self.now += s


def neuf(installe):
    """The app's screens on a fresh Mac (app/src/app.js), as Bibo words them: next screen per
    button, or (seconds, next screen) by itself. `installe()`: the first run got to the end."""
    return {
        "bienvenue": {"titre": "Salut, moi c’est Bibo" + NB + "!",
                      "texte": "Je vais installer ton second cerveau pour Claude. 10 à 20 minutes, tu n’as aucune commande à taper.",
                      "boutons": {"Vérifier mon Mac": "diag-en-cours"}},
        "diag-en-cours": {"titre": "Je regarde ton Mac…", "texte": "Ça prend quelques secondes.",
                          "apres": (3, lambda: "deja" if installe() else "diag")},
        "diag": {"titre": "J’ai fait le tour.", "texte": "Il manque Claude Code, Bun, Obsidian et ton cerveau. Je m’en occupe.",
                 "boutons": {"Revérifier": "diag-en-cours", "Tout installer": "voyage"},
                 "textes": ["macOS 26.6 · Puce Apple · 7 Go de mémoire", "Claude Code", "À installer"]},
        "deja": {"titre": "Tout est déjà installé" + NB + "!",
                 "texte": "Ton cerveau cherche par mots-clés" + NB + ": tu peux lui apprendre à chercher par le sens.",
                 "boutons": {"Activer la recherche par le sens": None, "Continuer": "verif"}},
        "voyage": {"titre": "Une option avant de commencer",
                   "texte": "Ton cerveau retrouve tes notes par mots-clés. Avec une clé Voyage AI, il les retrouve aussi par le sens.",
                   "boutons": {"Ouvrir Voyage AI": None, "Vérifier": None, "Passer (recherche par mots-clés)": "claude",
                               "Continuer avec cette clé": None}},
        "claude": {"titre": "J’installe Claude Code.", "texte": "Étape 1 sur 4. L’outil officiel d’Anthropic.", "apres": (30, "verif-inline")},
        "verif-inline": {"titre": "Je vérifie ton compte Claude…", "apres": (2, "login-inline")},
        "login-inline": {"titre": "Connecte ton compte Claude maintenant",
                         "texte": "Comme ça, si une étape coince, Claude pourra m’aider à la réparer.",
                         "boutons": {"Plus tard": "bun", "Connecter mon compte Claude": None}},
        "bun": {"titre": "J’installe Bun.", "texte": "Étape 2 sur 4. C’est le moteur de ta mémoire.", "apres": (10, "obsidian")},
        "obsidian": {"titre": "J’installe Obsidian.", "apres": (60, "brain")},
        "brain": {"titre": "Je prépare ton cerveau.", "texte": "Étape 4 sur 4. C’est l’étape la plus longue.", "apres": (400, "installe")},
        "installe": {"titre": "C’est installé" + NB + "!", "texte": "Plus qu’une petite étape.", "apres": (2, "verif")},
        "verif": {"titre": "Je vérifie ton compte Claude…", "apres": (2, "login")},
        "login": {"titre": "Dernière étape" + NB + ": ton compte Claude",
                  "texte": "Connecte-toi pour que Claude puisse travailler avec ton cerveau.",
                  "boutons": {"Plus tard": "fini", "Connecter mon compte Claude": None}},
        "fini": {"titre": "Ton second cerveau est prêt" + NB + "!", "texte": "Claude retient maintenant ce que tu lui apprends.",
                 "boutons": {"Activer la recherche par le sens": None, "Ouvrir mon cerveau dans Obsidian": None,
                             "Personnaliser mon cerveau (15 min)": None}},
    }


class FauxPilote(ta.Pilote):
    """The app's window, played from a table of screens on a fake clock."""

    machine = "Mac"
    suffixe = "_macos-universal.dmg"

    def __init__(self, horloge, home=None, change=None):
        self.h = horloge
        self.home = home
        self.fait = False
        self.sources = True     # the app unpacks the sources it was handed
        self.ecrans = neuf(lambda: self.fait)
        for k, v in (change or {}).items():
            self.ecrans[k] = v
        self.etat = None
        self.depuis = 0.0
        self.clics, self.captures, self.envs, self.fermetures = [], [], [], 0
        self.dialogue = None        # {"processus", "textes", "boutons", "part_sur": button that closes it}
        self.vite = False           # offers quick reads of Bibo's words
        self.pleines = 0

    def aller(self, etat):
        self.etat = etat() if callable(etat) else etat
        self.depuis = self.h.now
        if self.etat == "fini" and not self.fait:
            self.fait = True
            self.installer_le_kit()

    def installer_le_kit(self):
        """What the app's steps leave on the Mac, and the sources it was handed."""
        if not self.home:
            return
        home = Path(self.home)
        for rel in (".local/bin/claude", ".bun/bin/bun"):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text("x")
        (home / "Applications" / "Obsidian.app" / "Contents" / "MacOS").mkdir(parents=True)
        archive = self.envs[-1].get("BIAB_ARCHIVE")
        if archive and self.sources:
            src = home / ".brain-in-a-box" / "src"
            src.mkdir(parents=True)
            with zipfile.ZipFile(archive) as z:
                root = z.namelist()[0].split("/")[0]
                (src / ta.MARQUEUR).write_bytes(z.read(root + "/" + ta.MARQUEUR))

    def ouvrir(self, env):
        self.envs.append(env)
        self.aller("bienvenue")

    def vivante(self):
        return self.etat is not None

    def fermer(self):
        self.etat = None
        self.fermetures += 1
        return "Cmd+Q"

    def spec(self):
        spec = self.ecrans[self.etat]
        if "apres" in spec and self.h.now - self.depuis >= spec["apres"][0]:
            self.aller(spec["apres"][1])
            spec = self.ecrans[self.etat]
        return spec

    def rapide(self):
        if not self.vite or self.etat is None:
            return None
        self.h.now += 0.1
        spec = self.spec()
        return ta.norm(spec["titre"]), ta.norm(spec.get("texte", ""))

    def lire(self):
        self.h.now += 0.5   # reading the window takes a moment
        self.pleines += 1
        if self.etat is None:
            return ta.Ecran()
        spec = self.spec()
        dialogues = [dict(self.dialogue)] if self.dialogue else []
        late = self.h.now - self.depuis < spec.get("boutons_apres", 0)   # the tree shows the words first
        return ta.Ecran(fenetre=True, titre=spec["titre"], texte=spec.get("texte", ""),
                        boutons=[] if late else [(b, True) for b in spec.get("boutons", {})], textes=spec.get("textes", []),
                        dialogues=dialogues)

    def cliquer(self, libelle, processus=None):
        self.clics.append((processus, libelle) if processus else libelle)
        if processus:
            if self.dialogue and libelle == self.dialogue.get("part_sur"):
                self.dialogue = None
            return "AXPress"
        nxt = self.ecrans[self.etat].get("boutons", {}).get(libelle)
        if nxt:
            self.aller(nxt)
        return "AXPress"

    def capturer(self, chemin):
        Path(chemin).write_bytes(b"\x89PNG")
        self.captures.append(Path(chemin).name)

    def marquer_telecharge(self, fichier):
        return "0083;00000000;Safari;TEST"

    def installer(self, fichier, details, capturer=None):
        details["signature"] = "adhoc"
        return []

    outils = ta.PiloteMac.outils

    def arbre(self):
        return {"app": [], "faux": True}


class Noms(terrain.Etapes):
    """Etapes that remember every step name."""

    def __init__(self):
        super().__init__()
        self.vus = []

    def __call__(self, nom):
        self.vus.append(nom)
        self.nom = nom


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="terrain-app-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.h = Horloge()
        self.t = Noms()
        self.shots = Path(self.tmp) / "captures"
        self.shots.mkdir()

    def parcours(self, pilote):
        return ta.Parcours(self.t, pilote, self.shots, horloge=self.h, dormir=self.h.dormir)


class Walk(Base):
    def test_a_fresh_mac_is_clicked_through_to_the_brain(self):
        p = FauxPilote(self.h)
        pa = self.parcours(p)
        took = pa.premier_lancement({"HOME": "/h"})
        self.assertEqual(p.clics, ["Vérifier mon Mac", "Tout installer", "Passer (recherche par mots-clés)",
                                   "Plus tard", "Plus tard"])
        self.assertGreater(took, 500)
        shots = p.captures
        self.assertEqual(shots[:5], ["01-bienvenue.png", "02-diagnostic-en-cours.png", "03-diagnostic.png",
                                     "04-voyage.png", "05-etape-claude.png"])
        for name in ("etape-bun", "etape-obsidian", "etape-brain", "etape-brain-2min", "etape-brain-4min",
                     "etape-brain-6min", "connexion-installation", "connexion", "fini"):
            self.assertTrue(any(re.match(r"^\d\d-%s\.png$" % name, s) for s in shots), name)
        self.assertEqual(shots, sorted(shots))
        self.assertEqual(sorted(os.listdir(self.shots)), shots)
        self.assertEqual(self.t.vus[0], "app-lancement")
        for step in ("app-bienvenue", "app-diagnostic", "app-voyage", "app-etape-claude", "app-connexion-installation",
                     "app-etape-brain", "app-connexion", "app-fini"):
            self.assertIn(step, self.t.vus)
        for step in self.t.vus:
            self.assertRegex(step, terrain.ETAPE_RE)
        self.assertIn("Il manque Claude Code, Bun, Obsidian et ton cerveau.", self.t.details["diagnostic"]["bibo"])
        b = pa.bilan()
        brain = next(e for e in b["ecrans"] if e["ecran"] == "etape-brain")
        self.assertGreaterEqual(brain["duree_s"], 400)
        self.assertEqual(b["bibo"]["titre"], "Ton second cerveau est prêt !")

    def test_quick_reads_stand_for_full_ones_while_bibo_says_the_same(self):
        slow, quick = FauxPilote(self.h), FauxPilote(Horloge())
        quick.vite = True
        self.parcours(slow).premier_lancement({})
        pa = ta.Parcours(Noms(), quick, self.shots, horloge=quick.h, dormir=quick.h.dormir)
        pa.premier_lancement({})
        self.assertEqual(quick.clics, slow.clics)
        names = lambda shots: [re.sub(r"^\d\d-", "", n) for n in shots]
        self.assertEqual(names(quick.captures), names(slow.captures))
        self.assertLess(quick.pleines * 3, slow.pleines)
        reads = pa.bilan()["lecture_fenetre_ms"]["lectures"]
        self.assertEqual(reads[0], quick.pleines)
        self.assertGreater(reads[1], reads[0])

    def test_a_step_over_in_a_second_still_gets_its_capture(self):
        # Bun installs in about a second on a GitHub VM: a full read of the window (2 s) misses it.
        short = {"bun": {"titre": "J’installe Bun.", "apres": (1.0, "obsidian")}}
        slow, quick = FauxPilote(self.h, change=short), FauxPilote(Horloge(), change=short)
        quick.vite = True
        self.parcours(slow).premier_lancement({})
        self.assertFalse([n for n in slow.captures if "etape-bun" in n])
        pa = ta.Parcours(Noms(), quick, self.shots, horloge=quick.h, dormir=quick.h.dormir)
        pa.premier_lancement({})
        self.assertEqual(len([n for n in quick.captures if n.endswith("-etape-bun.png")]), 1)
        self.assertEqual(quick.clics, slow.clics)

    def test_buttons_late_in_the_tree_are_read_again_at_once(self):
        p = FauxPilote(self.h)
        p.vite = True
        p.ecrans["login"] = dict(p.ecrans["login"], boutons_apres=3)
        pa = self.parcours(p)
        pa.premier_lancement({})
        shown = next(e for e in pa.ecrans if e["ecran"] == "connexion")
        click = next(c for c in pa.clics if c["ecran"] == "connexion")
        self.assertEqual(click["bouton"], "Plus tard")
        self.assertLessEqual(click["a_s"] - shown["debut_s"], 5, "not at the next periodic full read")
        self.assertEqual([c["bouton"] for c in pa.clics], ["Vérifier mon Mac", "Tout installer",
                                                           "Passer (recherche par mots-clés)", "Plus tard", "Plus tard"])

    def test_opened_again_it_says_all_is_installed(self):
        p = FauxPilote(self.h)
        pa = self.parcours(p)
        pa.premier_lancement({})
        p.fermer()
        del p.clics[:]
        vus = pa.relance({})
        self.assertIn("deja-installe", vus)
        self.assertEqual(p.clics, ["Vérifier mon Mac", "Continuer", "Plus tard"])
        self.assertIn("app-relance-deja-installe", self.t.vus)
        self.assertTrue(any(s.endswith("-relance-deja-installe.png") for s in p.captures))

    def test_opened_again_and_installing_again_fails(self):
        p = FauxPilote(self.h)
        pa = self.parcours(p)
        pa.premier_lancement({})
        p.fermer()
        p.fait = False      # the app finds something missing again
        with self.assertRaises(terrain.Echec) as cm:
            pa.relance({})
        self.assertIn("au deuxième lancement, l'app veut encore installer", str(cm.exception))
        self.assertEqual(self.t.nom, "app-relance-diagnostic")

    def test_a_failed_step_names_it_and_quotes_bibo(self):
        p = FauxPilote(self.h, change={
            "brain": {"titre": "Je prépare ton cerveau.", "apres": (90, "oups")},
            "oups": {"titre": "Oups, ça a coincé.",
                     "texte": "L’installation du cerveau s’est arrêtée (code 1) sur" + NB + ": « bun install ».",
                     "boutons": {"Copier le rapport": None, "Réessayer": "brain"}}})
        pa = self.parcours(p)
        with self.assertRaises(terrain.Echec) as cm:
            pa.premier_lancement({})
        self.assertEqual(self.t.nom, "app-etape-brain")
        reason = pa.raison(str(cm.exception))
        self.assertIn("l'installation a échoué dans l'app", reason)
        self.assertIn("Bibo : « Oups, ça a coincé. L'installation du cerveau s'est arrêtée (code 1) sur : « bun install ».",
                      reason)
        self.assertTrue(p.captures[-1].endswith("-echec.png"))
        self.assertNotIn("Réessayer", p.clics)
        pa.sur_echec()
        self.assertTrue(any(n.endswith("-echec-arbre.json") for n in os.listdir(self.shots)))

    def test_a_click_that_changes_nothing_fails(self):
        p = FauxPilote(self.h, change={"bienvenue": {"titre": "Salut, moi c’est Bibo !", "boutons": {"Vérifier mon Mac": None}}})
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("le clic sur « Vérifier mon Mac » ne change rien (3 essais)", str(cm.exception))
        self.assertEqual(p.clics, ["Vérifier mon Mac"] * 3)
        self.assertEqual(self.t.nom, "app-bienvenue")

    def test_a_missing_button_fails(self):
        p = FauxPilote(self.h, change={"voyage": {"titre": "Une option avant de commencer", "boutons": {"Ouvrir Voyage AI": None}}})
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("l'écran « voyage » n'offre pas le bouton « Passer (recherche par mots-clés) »", str(cm.exception))

    def test_a_machine_with_everything_is_not_a_fresh_vm(self):
        p = FauxPilote(self.h)
        p.fait = True
        with self.assertRaises(terrain.Echec) as cm:
            self.parcours(p).premier_lancement({})
        self.assertIn("la VM n'est pas neuve", str(cm.exception))

    def test_a_step_that_never_ends_is_cut_with_a_capture_every_two_minutes(self):
        p = FauxPilote(self.h, change={"brain": {"titre": "Je prépare ton cerveau.", "apres": (10 ** 9, "installe")}})
        pa = self.parcours(p)
        with self.assertRaises(terrain.Echec) as cm:
            pa.premier_lancement({})
        self.assertIn("toujours sur l'écran « etape-brain » après 55 min", str(cm.exception))
        waits = [s for s in p.captures if re.match(r"^\d\d-etape-brain-\d+min\.png$", s)]
        self.assertGreaterEqual(len(waits), 25)
        self.assertLessEqual(self.h.now, ta.DELAI_PREMIER + 600)

    def test_no_window_fails_with_a_capture(self):
        p = FauxPilote(self.h)
        p.ouvrir = lambda env: None
        pa = self.parcours(p)
        with self.assertRaises(terrain.Echec) as cm:
            pa.premier_lancement({})
        self.assertIn("aucune fenêtre", str(cm.exception))
        self.assertEqual(p.captures, ["01-sans-fenetre.png"])
        self.assertIn("(Bibo n'a rien dit à l'écran)", pa.raison("x"))


class Dialogs(Base):
    def test_the_system_asking_for_the_app_is_accepted_once_and_captured(self):
        p = FauxPilote(self.h)
        p.dialogue = {"processus": "UserNotificationCenter", "part_sur": "Allow", "boutons": ["Don't Allow", "Allow"],
                      "textes": ["“Brain in a Box” would like to access files in your Documents folder."]}
        pa = self.parcours(p)
        pa.premier_lancement({})
        self.assertEqual([c for c in p.clics if isinstance(c, tuple)], [("UserNotificationCenter", "Allow")])
        self.assertEqual(p.captures[:2], ["01-dialogue-usernotificationcenter.png", "02-bienvenue.png"])
        d = pa.bilan()["dialogues_systeme"]
        self.assertEqual((len(d), d[0]["reponse"], d[0]["essais"]), (1, "Allow", 1))

    def test_a_dialog_for_someone_else_is_never_clicked(self):
        p = FauxPilote(self.h, change={"brain": {"titre": "Je prépare ton cerveau.", "apres": (100, "installe")}})
        p.dialogue = {"processus": "CoreServicesUIAgent", "boutons": ["OK", "Move to Trash"],
                      "textes": ["“Autre chose” cannot be opened."]}
        pa = self.parcours(p)
        pa.premier_lancement({})
        self.assertFalse([c for c in p.clics if isinstance(c, tuple)])
        self.assertEqual(pa.bilan()["dialogues_systeme"][0]["reponse"], None)
        self.assertIn("dialogue système ouvert (CoreServicesUIAgent) : « “Autre chose” cannot be opened. »", pa.raison("x"))

    def test_a_dialog_that_stays_is_tried_three_times_only(self):
        p = FauxPilote(self.h)
        p.dialogue = {"processus": "UserNotificationCenter", "part_sur": None, "boutons": ["Allow"],
                      "textes": ["“Brain in a Box” would like to access files in your Documents folder."]}
        self.parcours(p).premier_lancement({})
        self.assertEqual(len([c for c in p.clics if isinstance(c, tuple)]), ta.CLIC_ESSAIS)

    def test_only_a_request_naming_the_app(self):
        self.assertEqual(ta.bouton_autoriser({"textes": ["“Brain in a Box” would like to access files in your Documents folder."],
                                              "boutons": ["Don’t Allow", "Allow"]}), "Allow")
        self.assertEqual(ta.bouton_autoriser({"textes": ["“brain-in-a-box” souhaite accéder"], "boutons": ["Autoriser"]}),
                         "Autoriser")
        self.assertIsNone(ta.bouton_autoriser({"textes": ["“Terminal” would like to control"], "boutons": ["Allow"]}))
        self.assertIsNone(ta.bouton_autoriser({"textes": ["“Brain in a Box” is damaged"], "boutons": ["Move to Trash"]}))


def tree(subrole=True, texte=True):
    """What System Events gives for the welcome screen (shape of a real dump on a GitHub VM)."""
    st = lambda s: {"r": "AXStaticText", "s": "", "n": s, "v": s, "k": []}
    group = lambda *k, s="": {"r": "AXGroup", "s": s, "n": "", "v": "", "k": list(k)}
    message = group(group(st("Salut, moi c’est Bibo" + NB + "!")),
                    *([group(st("Je vais installer ton second cerveau pour Claude."))] if texte else []),
                    s="AXApplicationStatus" if subrole else "")
    hero = group(message, s="AXLandmarkBanner")
    main = group({"r": "AXList", "s": "", "n": "", "v": "", "k": [group(st("1"), st("Je regarde ce qui manque sur ton Mac."))]},
                 group(st("Il te faut un abonnement Claude Pro ou Max.")))
    footer = group({"r": "AXButton", "s": "", "n": "Vérifier mon Mac", "v": "", "d": "", "e": True, "k": []},
                   {"r": "AXButton", "s": "", "n": "Plus tard", "v": "", "d": "", "e": False, "k": []})
    web = {"r": "AXWebArea", "s": "", "n": "", "v": "", "k": [hero, main, footer]}
    window = {"r": "AXWindow", "s": "AXStandardWindow", "n": "Brain in a Box", "v": "", "k": [
        group(group({"r": "AXScrollArea", "s": "", "n": "", "v": "", "k": [web]})),
        {"r": "AXButton", "s": "AXCloseButton", "n": "", "v": "", "d": "close button", "e": True, "k": []}]}
    return [{"titre": "Brain in a Box", "arbre": window}]


class ReadingTheWindow(unittest.TestCase):
    def test_bibo_speaks_in_the_status_region(self):
        e = ta.ecran_depuis_arbre(tree())
        self.assertEqual((e.fenetre, e.lecture), (True, "statut"))
        self.assertEqual(e.titre, "Salut, moi c'est Bibo !")
        self.assertEqual(e.texte, "Je vais installer ton second cerveau pour Claude.")
        self.assertEqual(e.boutons, [("Vérifier mon Mac", True), ("Plus tard", False)])
        self.assertIs(e.bouton("Vérifier mon Mac"), True)
        self.assertIs(e.bouton("Plus tard"), False)
        self.assertIsNone(e.bouton("close button"))
        self.assertIn("Il te faut un abonnement Claude Pro ou Max.", e.textes)
        self.assertEqual(ta.cle_ecran(e), ("bienvenue", None))
        # Where a quick read finds Bibo again: window 0, group, group, scroll area, page, header, region.
        self.assertEqual(e.statut, (0, (0, 0, 0, 0, 0, 0)))
        self.assertIsNone(ta.ecran_depuis_arbre(tree(subrole=False)).statut)
        self.assertEqual(ta.dits_rapides(["Salut, moi c’est Bibo" + NB + "!", " ", "Je vais installer."]),
                         ("Salut, moi c'est Bibo !", "Je vais installer."))
        self.assertIsNone(ta.dits_rapides([]))

    def test_without_the_status_role_the_first_texts_stand_in(self):
        e = ta.ecran_depuis_arbre(tree(subrole=False))
        self.assertEqual((e.lecture, e.titre, e.texte), ("repli", "Salut, moi c'est Bibo !",
                                                          "Je vais installer ton second cerveau pour Claude."))
        e = ta.ecran_depuis_arbre(tree(subrole=False, texte=False))
        self.assertEqual((e.titre, e.texte), ("Salut, moi c'est Bibo !", ""))

    def test_no_window_no_page(self):
        self.assertEqual(ta.cle_ecran(ta.ecran_depuis_arbre([])), ("sans-fenetre", None))
        bare = [{"titre": "Brain in a Box", "arbre": {"r": "AXWindow", "k": []}}]
        self.assertEqual(ta.cle_ecran(ta.ecran_depuis_arbre(bare)), ("chargement", None))

    def test_a_dialog_as_data(self):
        d = ta.dialogue_depuis_arbre("UserNotificationCenter", {"r": "AXWindow", "k": [
            {"r": "AXStaticText", "n": "", "v": "“Brain in a Box” would like to access files in your Documents folder.", "k": []},
            {"r": "AXButton", "n": "Don’t Allow", "k": []}, {"r": "AXButton", "n": "Allow", "k": []}]})
        self.assertEqual(d["boutons"], ["Don't Allow", "Allow"])
        self.assertEqual(ta.bouton_autoriser(d), "Allow")

    def test_the_screens_the_robot_knows(self):
        for titre, cle in (("Je regarde ton PC…", "diagnostic-en-cours"), ("J’installe Bun.", "etape-bun"),
                           ("Une fenêtre Apple va s’ouvrir" + NB + ": clique sur Installer.", "etape-clt"),
                           ("Dernière étape" + NB + ": ton compte Claude", "connexion"),
                           ("Je n’ai rien fait.", "inconnu"), ("Ouvre l’app Brain in a Box.", "hors-app")):
            self.assertEqual(ta.cle_ecran(ta.Ecran(True, titre))[0], cle, titre)
        repair = ta.Ecran(True, "Je n’ai rien fait.", boutons=[("Copier le rapport", True), ("Réessayer", True)])
        self.assertEqual(ta.cle_ecran(repair)[0], "echec")

    def test_every_word_the_robot_looks_for_is_in_the_app(self):
        # The robot reads the app's own words: a reworded screen must change terrain_app.py too.
        with open(os.path.join(REPO, "app", "src", "app.js"), encoding="utf-8") as f:
            app = ta.norm(f.read().replace("${OS.machine}", "Mac"))
        words = [start for _, start in ta.ECRANS] + list(ta.ETAPES) + list(ta.BOUTONS_ECHEC) + [
            "Vérifier mon Mac", "Tout installer", "Passer (recherche par mots-clés)", "Plus tard", "Continuer",
            "Personnaliser mon cerveau (15 min)", "Ouvrir mon cerveau dans Obsidian"]
        for w in words:
            self.assertIn(ta.norm(w), app, w)


class Names(unittest.TestCase):
    def test_capture_names_sort_and_stay_plain(self):
        self.assertEqual(ta.nom_capture(3, "etape-brain-2min"), "03-etape-brain-2min.png")
        self.assertEqual(ta.nom_capture(12, "relance-déjà installé !"), "12-relance-deja-installe.png")
        self.assertEqual(ta.nom_capture(7, "dialogue-UserNotificationCenter"), "07-dialogue-usernotificationcenter.png")
        self.assertEqual(ta.nom_capture(1, "«»"), "01-ecran.png")

    def test_master_downloads_a_branch_builds(self):
        self.assertEqual(ta.choisir_source("master"), "release")
        for ref in ("feat/terrain-app", "v0.2.0", "a" * 40, "", "master2"):
            self.assertEqual(ta.choisir_source(ref), "build", ref)
        p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "terrain_app.py"), "source", "--ref", "master"],
                           capture_output=True)
        self.assertEqual(p.stdout.decode().strip(), "source=release")

    def test_the_app_gets_a_users_environment_never_a_token(self):
        secrets = {"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-z", "CI": "true",
                   "ACTIONS_RUNTIME_TOKEN": "t", "PATH": "/opt/homebrew/bin:/usr/bin"}
        with mock.patch.dict(os.environ, secrets):
            env = ta.env_utilisateur("/Users/runner", {"BIAB_ARCHIVE": "/tmp/k.zip"})
        for k in secrets:
            self.assertTrue(k == "PATH" or k not in env, k)
        self.assertEqual(env["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
        self.assertEqual((env["HOME"], env["BIAB_ARCHIVE"]), ("/Users/runner", "/tmp/k.zip"))


def release(tag, created, prerelease=True, draft=False, names=("Brain-in-a-Box_%s_macos-universal.dmg", "SHA256SUMS")):
    label = tag[len("app-v"):] if tag.startswith("app-v") else tag
    return {"tag_name": tag, "created_at": created, "prerelease": prerelease, "draft": draft, "target_commitish": "c" * 40,
            "assets": [{"name": n % label if "%s" in n else n, "size": 0, "digest": "",
                        "browser_download_url": "https://github.com/%s/releases/download/%s/%s" % (
                            ta.REPO_SLUG, tag, n % label if "%s" in n else n)} for n in names]}


class Release(Base):
    def test_the_newest_preview(self):
        rels = [release("app-v0.1.0-preview.4", "2026-10-14T17:31:20Z"),
                release("app-v0.1.0-preview.5", "2026-10-15T09:00:00Z", draft=True),
                release("v0.3.0", "2026-10-16T00:00:00Z", prerelease=False),
                release("app-v0.1.0-preview.6-rc", "2026-10-16T00:00:00Z"),
                release("app-v0.1.0-preview.3", "2026-10-13T00:00:00Z"), "garbage"]
        self.assertEqual(ta.derniere_preview(rels)["tag_name"], "app-v0.1.0-preview.4")
        self.assertIsNone(ta.derniere_preview([]))

    def test_only_this_repositorys_files(self):
        rel = release("app-v0.1.0-preview.4", "x")
        name, url, _, _ = ta.asset(rel, "_macos-universal.dmg", "app-v0.1.0-preview.4")
        self.assertEqual(name, "Brain-in-a-Box_0.1.0-preview.4_macos-universal.dmg")
        rel["assets"][0]["browser_download_url"] = "https://evil.example/x.dmg"
        self.assertIsNone(ta.asset(rel, "_macos-universal.dmg", "app-v0.1.0-preview.4"))
        self.assertIsNone(ta.asset(rel, "_windows-x64-setup.exe", "app-v0.1.0-preview.4"))
        with self.assertRaises(ValueError):
            ta._get("https://github.com/x", token="ghs_x")

    def test_sha256sums(self):
        sums = "%s  a.dmg\n%s *b.exe\nnot a line\n" % ("1" * 64, "2" * 64)
        self.assertEqual(ta.somme_attendue(sums, "a.dmg"), "1" * 64)
        self.assertEqual(ta.somme_attendue(sums, "b.exe"), "2" * 64)
        self.assertIsNone(ta.somme_attendue(sums, "c.dmg"))
        self.assertIsNone(ta.somme_attendue("XYZ  a.dmg", "a.dmg"))

    def download(self, content, sums_for=None, attestation=0):
        name = "Brain-in-a-Box_0.1.0-preview.4_macos-universal.dmg"
        good = hashlib.sha256(sums_for if sums_for is not None else content).hexdigest()
        files = {"SHA256SUMS": ("%s  %s\n" % (good, name)).encode(), name: content}
        rel = release("app-v0.1.0-preview.4", "2026-10-14T17:31:20Z")
        calls = []

        def get(url, token=None, timeout=60):
            calls.append((url, token))
            return io.BytesIO(json.dumps([rel]).encode())

        def fetch(url, dest, timeout=600):
            calls.append((url, None))
            Path(dest).write_bytes(files[url.rsplit("/", 1)[1]])

        def gh(argv, **kw):
            calls.append((argv, kw["env"].get("GH_TOKEN")))
            return subprocess.CompletedProcess(argv, attestation, b"verification failed" if attestation else b"ok")

        details = {}
        with mock.patch.object(ta, "_get", get), mock.patch.object(ta, "telecharger", fetch), \
                mock.patch.object(ta.shutil, "which", lambda n: "/usr/bin/gh"), mock.patch.object(ta.subprocess, "run", gh):
            dest = ta.depuis_release(self.t, FauxPilote(self.h), self.tmp, "ghs_token", details)
        return dest, details, calls

    def test_a_download_is_checked_like_a_careful_user_does(self):
        dest, details, calls = self.download(b"dmg bytes")
        self.assertEqual(dest.read_bytes(), b"dmg bytes")
        self.assertEqual((details["release"], details["attestation"]),
                         ("app-v0.1.0-preview.4", "vérifiée (release.yml, refs/heads/master)"))
        gh = [c for c in calls if isinstance(c[0], list)][0]
        self.assertEqual(gh[0][1:3], ["attestation", "verify"])
        self.assertIn("--signer-workflow", gh[0])
        self.assertEqual(gh[0][gh[0].index("--source-ref") + 1], "refs/heads/master")
        # The token goes to the API and to gh, never to a file download.
        self.assertEqual({tok for url, tok in calls if isinstance(url, str) and not url.startswith("https://api.github.com/")},
                         {None})
        self.assertEqual(self.t.vus, ["app-telechargement", "app-verification"])

    def test_a_file_that_does_not_match_its_sum_or_its_attestation_fails(self):
        with self.assertRaises(terrain.Echec) as cm:
            self.download(b"tampered", sums_for=b"dmg bytes")
        self.assertIn("ne correspond pas à SHA256SUMS", str(cm.exception))
        self.assertEqual(self.t.nom, "app-verification")
        with self.assertRaises(terrain.Echec) as cm:
            self.download(b"dmg bytes", attestation=1)
        self.assertIn("l'attestation", str(cm.exception))


# --------------------------------------------------------------------------- Windows

def uia(t, n="", k=(), **kw):
    """A node as pilote_windows.cs dumps it (t control type, lt localized type, n name, e enabled...)."""
    node = {"t": t, "lt": kw.pop("lt", t.lower()), "n": n, "e": kw.pop("e", True), "k": list(k)}
    node.update(kw)
    return node


def page_uia(statut="StatusBar", p_texte=False, boutons=(("Vérifier mon PC", True), ("Plus tard", False))):
    """The welcome screen as UI Automation gives WebView2's page (Chromium's roles), in the app's window."""
    texte = lambda s: uia("Text", s) if not p_texte else uia("Text", "", [uia("Text", s)])
    paragraphe = lambda s: uia("Group", "", [texte(s)], lt="paragraph") if not p_texte else texte(s)
    region = {"StatusBar": lambda k: uia("StatusBar", "", k, lt="status bar"),
              "lt": lambda k: uia("Group", "", k, lt="status"),
              "live": lambda k: uia("Group", "", k, lt="group", l=1),
              None: lambda k: uia("Group", "", k, lt="group")}[statut]
    message = region([paragraphe("Salut, moi c’est Bibo" + NB + "!"), paragraphe("Je vais installer ton second cerveau.")])
    hero = uia("Group", "", [uia("Image", "Bibo"), message], lt="banner")
    main = uia("Group", "", [uia("List", "", [uia("ListItem", "", [uia("Text", "Je regarde ce qui manque sur ton PC.")])]),
                             uia("Text", "Il te faut un abonnement Claude Pro ou Max.", l=1)], lt="main")
    footer = uia("Group", "", [uia("Button", b, [uia("Text", b)], e=on) for b, on in boutons], lt="content info")
    doc = uia("Document", "Brain in a Box", [uia("Group", "", [hero, main, footer])])
    return uia("Window", "Brain in a Box", [
        uia("TitleBar", "", [uia("Button", "Minimize"), uia("Button", "Close")]),
        uia("Pane", "", [uia("Pane", "Chrome Legacy Window", [doc], c="Chrome_RenderWidgetHostHWND")])], c="Tauri Window")


class WindowsReading(unittest.TestCase):
    def ecran(self, arbre):
        return ta.ecran_depuis_arbre([{"titre": "Brain in a Box", "arbre": ta.arbre_uia(arbre)}])

    def test_bibo_in_the_status_region_whatever_ui_automation_calls_it(self):
        for statut in ("StatusBar", "lt", "live"):
            e = self.ecran(page_uia(statut))
            self.assertEqual((e.lecture, e.titre, e.texte), ("statut", "Salut, moi c'est Bibo !",
                                                             "Je vais installer ton second cerveau."), statut)
            self.assertEqual(e.boutons, [("Vérifier mon PC", True), ("Plus tard", False)], statut)
            self.assertEqual(ta.cle_ecran(e), ("bienvenue", None))
            self.assertIn("Il te faut un abonnement Claude Pro ou Max.", e.textes)
            # A button's own text is its label, never one more text of the page.
            self.assertEqual(e.textes.count("Vérifier mon PC"), 0)
        e = self.ecran(page_uia(None))
        self.assertEqual((e.lecture, e.titre, e.texte), ("repli", "Salut, moi c'est Bibo !", "Je vais installer ton second cerveau."))

    def test_a_paragraph_without_a_name_is_read_through(self):
        e = self.ecran(page_uia("StatusBar", p_texte=True))
        self.assertEqual((e.titre, e.texte), ("Salut, moi c'est Bibo !", "Je vais installer ton second cerveau."))

    def test_the_window_frame_is_not_the_page(self):
        e = self.ecran(page_uia())
        self.assertIsNone(e.bouton("Close"))
        self.assertIsNone(e.bouton("Minimize"))
        loading = uia("Window", "Brain in a Box", [uia("TitleBar", "", [uia("Button", "Close")]), uia("Pane", "")])
        self.assertEqual(ta.cle_ecran(self.ecran(loading)), ("chargement", None))


def nsis(page, lancer=1):
    """The installer's pages (NSIS, French, tauri-bundler's template) as UI Automation shows them."""
    b = lambda n, on=True: uia("Button", n, e=on)
    pages = {
        "bienvenue": [uia("Text", "Bienvenue dans le programme d'installation de Brain in a Box"), b("Suivant >"), b("Annuler")],
        "dossier": [uia("Text", "Choisissez le dossier d'installation"), uia("Edit", "Dossier de destination"),
                    b("Parcourir..."), b("< Précédent"), b("Installer"), b("Annuler")],
        "installation": [uia("Text", "Installation en cours"), b("Afficher les détails"), b("< Précédent", False),
                         b("Suivant >", False), b("Annuler", False)],
        "installe": [uia("Text", "Installation terminée"), b("Afficher les détails"), b("< Précédent", False),
                     b("Suivant >"), b("Annuler", False)],
        "fin": [uia("Text", "Fin de l'installation de Brain in a Box"),
                uia("CheckBox", "Créer un raccourci sur le bureau.", x=1), uia("CheckBox", "Lancer Brain in a Box", x=lancer),
                b("< Précédent", False), b("Fermer"), b("Annuler", False)],
        "message": [uia("Text", "Erreur : le téléchargement de WebView2 a échoué"), b("OK")],
    }[page]
    return [{"h": 7, "pid": 41, "titre": "Installation de Brain in a Box", "classe": "#32770",
             "arbre": uia("Window", "Installation de Brain in a Box", [uia("TitleBar", "", [uia("Button", "Close")]),
                                                                       uia("Pane", "", pages)])}]


class FauxSetup:
    pid = 41

    def __init__(self, h):
        self.h, self.fini, self.code = h, None, 0

    def poll(self):
        return self.code if self.fini is not None and self.h.now >= self.fini else None

    def kill(self):
        self.fini = self.h.now


class FauxWindows(ta.PiloteWindows):
    """PiloteWindows with its helper played: the installer's pages, on a fake clock."""

    def __init__(self, h, travail, pages=None):
        super().__init__(travail, outil="pilote-windows.exe", horloge=h, dormir=h.dormir)
        self.h = h
        self.setup = FauxSetup(h)
        self.page, self.lancer, self.depuis, self.duree = "bienvenue", 1, 0.0, 40
        self.suite = pages if pages is not None else {
            ("bienvenue", "Suivant >"): "dossier", ("dossier", "Installer"): "installation", ("installe", "Suivant >"): "fin"}
        self.appels = []

    def aller(self, page):
        self.page, self.depuis = page, self.h.now

    def _outil(self, args, timeout=120):
        args = [str(a) for a in args]
        self.appels.append(args)
        self.h.now += 0.3
        if self.page == "installation" and self.h.now - self.depuis >= self.duree:
            self.aller("installe")
        if args[0] == "lire":
            gone = self.setup.fini is not None
            return {"ok": True, "fenetres": [] if gone else nsis(self.page, self.lancer), "devant": None}
        if args[0] == "devant":
            return {"ok": True, "devant": True}
        if args[0] == "cocher":
            self.lancer = int(args[3])
            return {"ok": True, "coche": bool(self.lancer)}
        if args[0] == "cliquer":
            if self.page == "fin" and args[2] == "Fermer":
                self.setup.fini = self.h.now + 1
            nxt = self.suite.get((self.page, args[2]))
            if nxt:
                self.aller(nxt)
            return {"ok": True, "comment": "Invoke"}
        raise AssertionError(args)


class WindowsInstaller(Base):
    def test_the_wizard_is_clicked_through_with_a_capture_of_every_page(self):
        p = FauxWindows(self.h, self.tmp)
        shots = []
        pages = p.assistant(p.setup, [], capturer=lambda name: shots.append(name) or name)
        self.assertEqual([x["page"] for x in pages], ["bienvenue", "dossier", "installation", "installe", "fin", "fin"])
        self.assertEqual([c for x in pages for c in x["clics"]],
                         ["Suivant >", "Installer", "Suivant >", "Lancer Brain in a Box : décoché", "Fermer"])
        self.assertEqual(shots, ["installeur-" + x["page"] for x in pages])
        # The desktop shortcut is left as the installer offers it.
        self.assertFalse([a for a in p.appels if a[0] == "cocher" and "bureau" in a[2]])
        self.assertEqual(p.lancer, 0)
        self.assertEqual(p.setup.poll(), 0)

    def test_pages_as_data(self):
        keys = {k: ta.page_installeur(nsis(k))["cle"] for k in ("bienvenue", "dossier", "installation", "installe", "fin", "message")}
        self.assertEqual(keys, {k: k for k in keys})
        self.assertEqual(ta.action_installeur(ta.page_installeur(nsis("installation"))), None)
        self.assertEqual(ta.action_installeur(ta.page_installeur(nsis("fin", lancer=0))), ("cliquer", "Fermer"))
        self.assertEqual(ta.norm_win("&Suivant >"), "Suivant >")
        # The title bar's Close is the window's, never the installer's last button.
        self.assertNotIn("Close", ta.page_installeur(nsis("bienvenue"))["boutons"])

    def test_an_error_box_of_the_installer_fails_with_its_words(self):
        p = FauxWindows(self.h, self.tmp)
        p.page = "message"
        with self.assertRaises(ta.ErreurPilote) as cm:
            p.assistant(p.setup, [])
        self.assertIn("l'installeur affiche : « Erreur : le téléchargement de WebView2 a échoué »", str(cm.exception))

    def test_a_click_that_changes_nothing_and_an_installer_that_never_ends(self):
        p = FauxWindows(self.h, self.tmp, pages={})
        with self.assertRaises(ta.ErreurPilote) as cm:
            p.assistant(p.setup, [])
        self.assertIn("le clic sur « Suivant > » ne change rien dans l'installeur (3 essais)", str(cm.exception))
        p = FauxWindows(Horloge(), self.tmp, pages={("bienvenue", "Suivant >"): "installation"})
        p.duree = 10 ** 9
        with self.assertRaises(ta.ErreurPilote) as cm:
            p.assistant(p.setup, [])
        self.assertIn("l'installeur n'a pas fini en 10 min (page « installation »)", str(cm.exception))

    def installed(self, ruche="HKCU", ailleurs=False, cible=None):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        local, roaming, profile = (root / n for n in ("Local", "Roaming", "Profile"))
        folder = (root / "Programs" if ailleurs else local) / "Brain in a Box"
        folder.mkdir(parents=True)
        exe = folder / "brain-in-a-box.exe"
        exe.write_bytes(b"MZ")
        menu = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        menu.mkdir(parents=True)
        (menu / "Brain in a Box.lnk").write_bytes(b"L")
        (profile / "Desktop").mkdir(parents=True)
        p = FauxWindows(self.h, self.tmp)
        p._outil = lambda args, timeout=120: {"ok": True, "cible": str(cible or exe)}
        entries = [{"ruche": ruche, "cle": "Brain in a Box", "desinstalleur": str(folder / "uninstall.exe")}]
        details = {}
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(roaming), "USERPROFILE": str(profile)}), \
                mock.patch.object(ta.PiloteWindows, "desinstallations", staticmethod(lambda: entries)):
            notes = p.verifier_installation(details)
        return p, details, notes, exe

    def test_installed_for_the_user_alone_and_opened_from_the_start_menu(self):
        p, details, notes, exe = self.installed()
        self.assertEqual(p.exe, str(exe))
        self.assertEqual((details["app"], details["raccourci_bureau"]), ("brain-in-a-box.exe", False))
        self.assertIn("SmartScreen ne s'est pas affiché", notes[0])
        self.assertIn("constat, pas un échec", notes[0])
        with self.assertRaises(ta.ErreurPilote) as cm:
            self.installed(ruche="HKLM")
        self.assertIn("pour tous les utilisateurs", str(cm.exception))

    def test_installed_elsewhere_or_a_shortcut_elsewhere_fails(self):
        with self.assertRaises(ta.ErreurPilote) as cm:
            self.installed(ailleurs=True)
        self.assertIn("hors des programmes de l'utilisateur", str(cm.exception))
        with self.assertRaises(ta.ErreurPilote) as cm:
            self.installed(cible=Path(self.tmp) / "autre" / "brain-in-a-box.exe")
        self.assertIn("le raccourci du menu Démarrer mène à", str(cm.exception))


class WindowsApp(Base):
    def test_the_window_a_dialog_in_front_and_a_click_on_it(self):
        p = FauxWindows(self.h, self.tmp)
        p.proc = FauxSetup(self.h)
        p.exe = r"C:\Users\r\AppData\Local\Brain in a Box\brain-in-a-box.exe"
        devant = {"h": 9, "pid": 77, "classe": "#32770", "exe": r"C:\Windows\System32\SmartScreen.exe",
                  "arbre": uia("Window", "Windows", [uia("Text", "Brain in a Box souhaite accéder à tes fichiers"),
                                                     uia("Button", "Autoriser"), uia("Button", "Refuser")])}
        console = dict(devant, classe="ConsoleWindowClass")
        calls = []

        def outil(args, timeout=120):
            calls.append([str(a) for a in args])
            if args[0] == "lire":
                return {"ok": True, "fenetres": [{"h": 5, "classe": "Tauri Window", "titre": "Brain in a Box",
                                                  "arbre": page_uia()}], "devant": outil.devant}
            return {"ok": True, "comment": "Invoke"}
        p._outil = outil
        outil.devant = devant
        e = p.lire()
        self.assertEqual((e.titre, ta.cle_ecran(e)[0]), ("Salut, moi c'est Bibo !", "bienvenue"))
        self.assertEqual(e.dialogues[0]["processus"], "SmartScreen")
        self.assertEqual(ta.bouton_autoriser(e.dialogues[0]), "Autoriser")
        p.cliquer("Autoriser", processus="SmartScreen")
        p.cliquer("Vérifier mon PC")
        self.assertEqual([c for c in calls if c[0] == "cliquer"], [["cliquer", "9", "Autoriser"], ["cliquer", "5", "Vérifier mon PC"]])
        # Brought to the front once, the first time the window was seen.
        p.lire()
        self.assertEqual([c for c in calls if c[0] == "devant"], [["devant", "5"]])
        outil.devant = console
        self.assertEqual(p.lire().dialogues, [])

    def test_a_pc_is_clicked_through_with_its_own_words(self):
        f = FauxPilote(self.h)
        f.machine = "PC"
        f.ecrans["bienvenue"] = dict(f.ecrans["bienvenue"], boutons={"Vérifier mon PC": "diag-en-cours"})
        f.ecrans["login-inline"]["boutons"]["Plus tard"] = "brain"     # Windows: Claude Code, then install.ps1
        self.parcours(f).premier_lancement({})
        self.assertEqual(f.clics, ["Vérifier mon PC", "Tout installer", "Passer (recherche par mots-clés)",
                                   "Plus tard", "Plus tard"])
        self.assertFalse([c for c in f.captures if "etape-bun" in c or "etape-obsidian" in c])


class WindowsEnvironment(unittest.TestCase):
    def test_a_session_s_environment_the_store_python_first_never_a_token(self):
        block = ["=C:=C:\\", "Path=C:\\Windows\\system32;C:\\Users\\r\\AppData\\Local\\Microsoft\\WindowsApps",
                 "USERPROFILE=C:\\Users\\r", "TEMP=C:\\Users\\r\\AppData\\Local\\Temp", "broken"]
        session = ta.env_depuis_bloc(block)
        self.assertEqual(sorted(session), ["Path", "TEMP", "USERPROFILE"])
        with mock.patch.dict(os.environ, {"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "CI": "true"}):
            env = ta.env_app_windows(session, "C:\\stub", {"BIAB_ARCHIVE": "C:\\t\\k.zip"})
        self.assertEqual(env["Path"], "C:\\stub;C:\\Windows\\system32;C:\\Users\\r\\AppData\\Local\\Microsoft\\WindowsApps")
        self.assertEqual(sorted(env), ["BIAB_ARCHIVE", "Path", "TEMP", "USERPROFILE"])
        self.assertEqual(ta.env_app_windows(session)["Path"], session["Path"])

    def test_the_helper_source_is_ascii_and_answers_every_request(self):
        with open(ta.PiloteWindows.SOURCE, "rb") as f:
            src = f.read()
        self.assertEqual([i for i, c in enumerate(src) if c > 0x7f][:3], [], "csc reads it in the ANSI code page")
        with open(ta.__file__, encoding="utf-8") as f:
            py = f.read()
        asked = set(re.findall(r'_outil\(\[\s*"([a-z]+)"', py)) | {"cocher", "cliquer"}
        self.assertEqual(asked, {"lire", "cliquer", "cocher", "fermer", "devant", "capturer", "raccourci"})
        for op in asked:
            self.assertIn('op == "%s"' % op, src.decode("ascii"))


class Scenario(Base):
    """The whole scenario on the build path, a fake window and a fake check of the kit: what reaches
    the app, what the results say."""

    def run_it(self, pilote=None, check=None):
        home = Path(self.tmp) / "home"
        home.mkdir()
        dmg = Path(self.tmp) / "Brain-in-a-Box_0.1.0-terrain.abc_macos-universal.dmg"
        dmg.write_bytes(b"dmg")
        sha = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True).stdout.decode().strip()
        opts = types.SimpleNamespace(home=home, json=str(Path(self.tmp) / "r.json"), parcours="app", vm=True,
                                     source="build", fichier=str(dmg), captures=str(self.shots))
        checked = []
        kit = types.SimpleNamespace(Echec=terrain.Echec, workdir=terrain.workdir, run=terrain.run, REPO=terrain.REPO,
                                    tested_ref=lambda: sha,
                                    check_install=check or (lambda t, o: (t("hooks"), checked.append(o.home))))
        p = pilote or FauxPilote(self.h, home=home)
        secrets = {"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-z"}
        with mock.patch.dict(os.environ, secrets):
            terrain.play("app", lambda t, o: ta.scenario(t, o, kit, pilote=p, horloge=self.h, dormir=self.h.dormir), opts)
            left = {k: os.environ.get(k) for k in secrets}
        with open(opts.json, encoding="utf-8") as f:
            row = json.load(f)["scenarios"][0]
        return row, p, checked, sha, left, home

    def test_built_installed_clicked_checked_and_opened_again(self):
        row, p, checked, sha, left, home = self.run_it()
        self.assertEqual(row["statut"], "ok", row["raison"])
        self.assertIn("deuxième lancement : déjà installé", row["raison"])
        self.assertEqual(len(checked), 1)
        self.assertEqual(p.fermetures, 2)
        for env in p.envs:
            self.assertFalse({"GH_TOKEN", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"} & set(env), env)
            self.assertTrue(env["BIAB_ARCHIVE"].endswith(".zip"))
        self.assertEqual((left["GH_TOKEN"], left["GITHUB_TOKEN"]), (None, None), "popped for the rest of the job")
        d = row["details"]
        self.assertEqual((d["source"], d["sources_du_cerveau"]), ("build", "commit testé %s (BIAB_ARCHIVE)" % sha[:12]))
        self.assertTrue((home / "Downloads" / "Brain-in-a-Box_0.1.0-terrain.abc_macos-universal.dmg").is_file())
        self.assertEqual(d["captures"], sorted(os.listdir(self.shots)))
        self.assertTrue(any(c.endswith("-relance-deja-installe.png") for c in d["captures"]))
        self.assertGreater(d["duree_parcours_s"], 500)

    def test_a_failure_after_the_window_still_quotes_bibo(self):
        def check(t, o):
            t("lanceurs")
            t.check(False, "gbq query ne trouve pas Profile/lessons.md (code 127)")
        row, p, _, _, _, _ = self.run_it(check=check)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "lanceurs"))
        self.assertIn("gbq query ne trouve pas Profile/lessons.md (code 127) ; Bibo : « Ton second cerveau est prêt !",
                      row["raison"])
        self.assertTrue(row["details"]["captures"][-1].endswith("-echec.png"))

    def test_sources_the_app_did_not_take_fail(self):
        p = FauxPilote(self.h, home=Path(self.tmp) / "home")
        p.sources = False
        row, _, _, _, _, _ = self.run_it(pilote=p)
        self.assertEqual((row["statut"], row["etape"]), ("echec", "app-sources"))
        self.assertIn("BIAB_ARCHIVE ignoré", row["raison"])


if __name__ == "__main__":
    unittest.main()
