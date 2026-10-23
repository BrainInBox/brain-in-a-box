"""Les 60 regards au plus, en trois étapes décidées avant les appels.

A (30) : le panneau (5 négatifs, 5 positifs) en mode liste, Haiku, Sonnet et Opus, un écran après l'autre.
B (20) : le meilleur de A sur les 20 autres positifs, en mode liste : il a alors les 30 écrans.
         Meilleur = 0 négatif raté et 0 anneau faux, puis le plus de positifs justes, puis le plus rapide
         (p50) ; aucun : Opus, le modèle de l'app. Q1 : son forfait relevé à chaque regard.
C (10) : sans liste, le meilleur de A sur le panneau (comparer les deux modes).

    python3 -I etapes.py A|B|C --rendu D --dossier R
"""
import json
import subprocess
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))
import regards  # noqa: E402

MODELES = ("haiku", "sonnet", "opus")


def lancer(rendu, sortie, modele, mode, ecrans):
    subprocess.run([sys.executable, "-I", "-B", str(ICI / "regards.py"), "regarder", "--rendu", str(rendu), "--modele", modele,
                    "--mode", mode, "--ecrans", ",".join(ecrans), "--sortie", str(sortie)], check=True)


def meilleur(dossier, rendu):
    looks = [json.loads(l) for l in (dossier / "A.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    b = regards.bilan(looks, rendu)
    sains = [m for m in MODELES if not b["%s/liste" % m]["negatifs_rates"] and not b["%s/liste" % m]["anneaux_faux"]]
    if not sains:
        return "opus", b
    return min(sains, key=lambda m: (-b["%s/liste" % m]["positifs_justes"], b["%s/liste" % m]["p50_s"])), b


def main():
    etape, rendu, dossier = sys.argv[1], Path(sys.argv[3]), Path(sys.argv[5])
    dossier.mkdir(parents=True, exist_ok=True)
    if etape == "A":
        for k in regards.PANNEAU:
            for m in MODELES:
                lancer(rendu, dossier / "A.jsonl", m, "liste", [k])
        return
    m, b = meilleur(dossier, rendu)
    print("meilleur de A :", m, json.dumps({k: v["verdict"] for k, v in b.items()}, ensure_ascii=False))
    if etape == "B":
        lancer(rendu, dossier / "B.jsonl", m, "liste", [k for k in regards.POSITIFS if k not in regards.PANNEAU])
    else:
        lancer(rendu, dossier / "C.jsonl", m, "sans", regards.PANNEAU)


if __name__ == "__main__":
    main()
