# REPRISE : où en est Bbox (25/10/2026, nuit)

À lire en premier pour reprendre le travail. Ce document est mis à jour à chaque étape. La boussole est `VISION.md`, dans ce même dossier. Les décisions que Chafic a tranchées sont dans le skill `brain-in-a-box-product`, section « Décisions validées par Chafic, ne plus redemander » ; la même section est recopiée dans la page `projects/brain-in-a-box` du C-brain. On ne les repose pas.

## Où est le code

- **Bbox** est la copie privée de l'app d'Hermès (moteur et fenêtre, licence MIT), dans `~/bbox`, sur la branche locale `bbox/identity`. Elle part de la version `v0.22.5` d'Hermès. Le dépôt privé s'appelle `BrainInBox/bbox`, mais la branche `bbox/identity` n'y est pas poussée.
- **Ce dossier** est le kit, branche `feat/bbox`. Il contient `VISION.md` et ce fichier.
- **Les documents du PO** sont dans le dossier parent : `PLAN-bbox.md` (les phases), `FICHE-bbox.md` (la première heure), `PROJET-brain-in-a-box.md` (le dossier, §3 ter et §3 quater pour l'IA). Ce dossier parent n'est pas un dépôt git : ces fichiers ne se commitent pas.
- **Les scripts, sorties et captures d'essai** sont dans `../.scratch/phase1/`. Les outils (Tart, `cdp.py`, accès SSH aux Macs virtuels) sont dans `../.tools/`.

## Ce qui est fait

- Phase 0 : `VISION.md` (`cc5828c`), fiche d'une page, plan validé par Chafic le 24/10.
- Phase 1, l'app s'appelle Bbox, sur `bbox/identity` :
  - `b867224` : le nom Bbox partout à l'écran ;
  - `808fe77` : Bbox parle en son nom jusque dans la conversation, et l'IA gratuite de Nous est retirée ;
  - `bdaf790` : la carte de connexion met en avant le compte de la personne (plus de Nous Portal, plus de modèles en local) ;
  - `22e1324` : connexion à l'abonnement Claude (Pro ou Max) depuis la fenêtre, par la même connexion que celle d'Hermès au terminal ;
  - `e4249b7` : Claude en premier, marqué « Recommended », puis ChatGPT ;
  - `1f7daae` : chaque ligne porte la marque de son IA ;
  - `18b4f11` : un code refusé se dit en mots simples, en nommant Claude ou ChatGPT, sans accuser Internet ;
  - cas « Hermès déjà là » : `d88596e` (Bbox ne redémarre jamais une passerelle, il s'y raccroche telle quelle), `d1bc9ed` (Bbox a son propre lien `bbox://` et ne prend jamais `hermes://`), `0f77a5e` (jamais remplacer une passerelle qui fait tourner le code d'une autre installation), `7c38474` (jamais réécrire, redémarrer, arrêter ni retirer le service de démarrage d'une autre installation), `45b752d` (mise en forme d'un test), `5dc1465` (« Désinstaller » dans les réglages ne retire que Bbox).
- Preuve ChatGPT sur un Mac vierge : le 24/10 à 20 h 14 UTC, Bbox `b867224` a répondu par l'abonnement ChatGPT de Chafic, sur le Mac virtuel `bbox-essai`.
- **Build 6 (`5dc1465`), Mac vierge** : la carte « Let's get you setup with Bbox » montre Claude (Recommended) puis ChatGPT, chacun avec son logo (`run6-1.png`, `blank-2.png`). La page Claude s'ouvre et attend le code (`run6-2.png`) ; un faux code donne « The sign-in with Claude did not go through » (`run6-3.png`) ; ChatGPT affiche son code d'appareil (`run6-4.png`, code masqué).
- **Build 6, Mac où Hermès est déjà installé** (fabriqué par `vm-coexist-make.sh` : code d'une autre version, réglages, connexion Claude d'Awa, mémoire, skills, profil `travail`, tâche planifiée, session « Ranger les factures », passerelle en marche lancée par son propre service de démarrage, `Hermes.app`). Empreinte avant et après l'ouverture puis la fermeture de Bbox (`vm-coexist-snap.sh`, sortie `coexist6e-after.out`) :
  - inchangés : réglages, connexion, mémoire, skills, profils, tâches planifiées, code et commande `hermes` de la personne, service de démarrage, `Hermes.app`, nombre de sessions ;
  - la passerelle de la personne tourne toujours, même processus avant et après : Bbox écrit dans le journal « a bundled app never restarts a gateway, attaching as-is » ;
  - Bbox n'ajoute que des caches, ses propres fichiers d'état et cinq liens de commande dans `~/.local/bin`, seulement là où le nom est libre (`cli-provision.ts` ne remplace jamais un fichier ni un lien valide) ; la commande `hermes` de la personne n'est pas touchée ;
  - à l'écran : la session « Ranger les factures » et la tâche planifiée de la personne sont là (`coexist-2.png`, `coexist-3.png`).
- Tests sur `5dc1465` : tests Python ciblés au vert (32 sur l'identité, Claude et la cohabitation), typage propre ; vitest : 7 échecs restants, tous d'environnement (chemins `/private/var` du Mac, sondes de lancement), déjà présents avant nos changements.

## Les deux chantiers en cours

1. **La vraie connexion de Chafic** sur le Mac de test remis à neuf (build 6, carte de connexion à l'écran). Le relais `../.scratch/phase1/relais.py` (lancé par `relais-start.sh`) ouvre une page « Connecter tes comptes à Bbox » sur le Mac mini, joignable par Tailscale depuis les seuls appareils de Chafic (son MacBook et ses iPhone) : il ne journalise que les chemins, jamais le code. Le tunnel vers la fenêtre de Bbox (port 9333) et le relais tournent en arrière-plan.
2. **Le reste de la marque et de l'IA gratuite** : `store/free-tier.ts`, `terms-butterbar.tsx`, `guest_allowed`, `free_tier_offer`, `default_soul.py`, `prompt_builder.py`, la mention MIT ; puis la preuve avec un ChatGPT gratuit et le retrait de `~/bbox-baseline`.

## La prochaine action

Quand Chafic a connecté ses comptes : vérifier dans le Mac de test que Bbox répond par Claude puis par ChatGPT, capture à l'appui, puis déconnecter son ChatGPT du Mac de test et arrêter le relais.

Attention : comme `hermes auth add anthropic`, la connexion Claude enregistre le compte sans le rendre actif. Bbox ouvre ensuite l'étape de choix du modèle, que le relais ne valide pas : la valider par `cdp.py` avant de tester une réponse. À trancher plus tard : faire de Claude le fournisseur actif d'office après la connexion (petit changement, à prouver).

## Notes d'essai

- Remettre le Mac de test à neuf : `vm-blank-1.sh` (retire le service de démarrage fabriqué, vide le disque avec `sync`, arrête le Mac virtuel proprement), relancer `tart run bbox-essai`, puis `vm-blank-2.sh`, puis le tunnel `-L 9333:127.0.0.1:9333`. Sans `sync` et sans arrêt propre, le déplacement du fichier de démarrage est perdu.
- Une commande `launchctl print` passée par SSH a été bloquée par une règle de sécurité du Mac mini : pour remettre le Mac de test à neuf, on déplace le fichier de démarrage et on redémarre le Mac virtuel au lieu d'appeler `launchctl`.
- Une session n'apparaît dans la fenêtre que si son nombre de messages est au moins 1.

## Ce qui se fait à la fin, et que seul Chafic peut faire

Chafic connecte ses propres comptes sur le Mac de test : il colle le code Claude dans la page du relais (qui le tape dans Bbox), et il tape le code ChatGPT sur la page d'OpenAI. Le PO ne voit jamais ni mot de passe ni code Claude. Une fois la preuve faite, son compte ChatGPT est déconnecté du Mac de test.

## Règles à garder

- Rien n'est publié sur GitHub sans un récapitulatif validé par Chafic, et on ne touche pas à son Hermès quotidien (`~/.hermes`, en lecture seule).
- Un commit à chaque étape qui marche, sous-agents compris.
- Dans les Macs virtuels, on ne lance jamais `python3`, `git` ni `clang` système par SSH, car ils ouvrent la fenêtre d'installation des outils Apple. Les traces sont déplacées, jamais effacées ; nos propres builds d'essai, eux, sont supprimés.
- Pendant un chantier lancé, Chafic ne reçoit qu'un seul message, à la fin : les captures et le geste que lui seul peut faire.
