# REPRISE : où en est Bbox (24/10/2026, nuit)

À lire en premier pour reprendre le travail. Ce document est mis à jour à chaque étape. La boussole est `VISION.md`, dans ce même dossier. Les décisions que Chafic a tranchées sont dans le skill `brain-in-a-box-product`, section « Décisions validées par Chafic, ne plus redemander » ; la même section est recopiée dans la page `projects/brain-in-a-box` du C-brain. On ne les repose pas.

## Où est le code

- **Bbox** est la copie privée de l'app d'Hermès (moteur et fenêtre, licence MIT), dans `~/bbox`, sur la branche locale `bbox/identity`. Elle part de la version `v0.22.5` d'Hermès. Le dépôt privé s'appelle `BrainInBox/bbox`, mais la branche `bbox/identity` n'y est pas poussée.
- **Ce dossier** est le kit, branche `feat/bbox`. Il contient `VISION.md` et ce fichier.
- **Les documents du PO** sont dans le dossier parent : `PLAN-bbox.md` (les phases), `FICHE-bbox.md` (la première heure), `PROJET-brain-in-a-box.md` (le dossier, §3 ter et §3 quater pour l'IA).
- **Les scripts, sorties et captures d'essai** sont dans `../.scratch/phase1/`. Les outils (Tart, `cdp.py`, accès SSH aux Macs virtuels) sont dans `../.tools/`.

## Ce qui est fait

- Phase 0 : `VISION.md` (`cc5828c`), fiche d'une page, plan validé par Chafic le 24/10.
- Phase 1, l'app s'appelle Bbox, sur `bbox/identity` :
  - `b867224` : le nom Bbox partout à l'écran ;
  - `808fe77` : Bbox parle en son nom jusque dans la conversation, et l'IA gratuite de Nous est retirée ;
  - `bdaf790` : la carte de connexion met en avant le compte de la personne (plus de Nous Portal, plus de modèles en local) ;
  - `22e1324` : connexion à l'abonnement Claude (Pro ou Max) depuis la fenêtre, par la même connexion que celle d'Hermès au terminal (la page Claude s'ouvre, on autorise, on colle le code dans Bbox) ;
  - `e4249b7` : la carte montre Claude en premier, marqué « Recommandé », puis ChatGPT ;
  - `1f7daae` : chaque ligne porte la marque de son IA (avant, la ligne ChatGPT affichait l'icône de Bbox).
- Preuve sur un Mac vierge : le 24/10 à 20 h 14 UTC, Bbox `b867224` a répondu par l'abonnement ChatGPT de Chafic, sur le Mac virtuel `bbox-essai`.
- Tests (sur `bdaf790`) : 657 tests d'écran réussis, typage et eslint propres, tests Python ciblés au vert.

## Les deux chantiers en cours

1. **Claude et ChatGPT dans la fenêtre, sur le Mac de test.** Le code est commité (`22e1324`, `e4249b7`, `1f7daae`). Il reste à :
   - fabriquer l'app : `scripts/build-bundle.sh`, ce qui donne `release/mac-arm64/Bbox <sha>.app` ;
   - l'installer sur le Mac virtuel `bbox-essai` avec `../.scratch/phase1/essai-install3.sh <run>` ;
   - prendre les captures avec `cdp.py` sur le port 9333 : les deux lignes à l'écran, puis la page Claude et le code ChatGPT.
2. **Le cas « Mac où Hermès est déjà installé ».** Le moteur que Bbox fait tourner, ce qu'il écrit dans `~/.hermes`, la passerelle déjà en marche, la mise à jour automatique : l'analyse va dans `../.scratch/phase1/coexist-analysis.md`. Ensuite viennent les correctifs, testés et commités un par un, puis l'essai sur un Mac virtuel où l'on recrée un Hermès existant, avec une empreinte des fichiers avant et après.

## La prochaine action

Fabriquer l'app à partir du dernier commit de `bbox/identity`, l'installer sur `bbox-essai`, puis faire la capture de la carte où l'on voit Claude et ChatGPT.

## Ce qui se fait à la fin, et que seul Chafic peut faire

Chafic connecte ses propres comptes sur le Mac de test : il tape le code ChatGPT sur la page d'OpenAI, et il colle le code Claude dans Bbox. Le PO ne voit jamais ni mot de passe ni code Claude. Une fois la preuve faite, son compte ChatGPT est déconnecté du Mac de test.

## Règles à garder

- Rien n'est publié sur GitHub sans un récapitulatif validé par Chafic, et on ne touche pas à son Hermès quotidien (`~/.hermes`, en lecture seule).
- Un commit à chaque étape qui marche, sous-agents compris.
- Dans les Macs virtuels, on ne lance jamais `python3`, `git` ni `clang` par SSH, car ils ouvrent la fenêtre d'installation des outils Apple. Les traces sont déplacées, jamais effacées ; nos propres builds d'essai, eux, sont supprimés.
- Pendant un chantier lancé, Chafic ne reçoit qu'un seul message, à la fin : les captures et le geste que lui seul peut faire.
