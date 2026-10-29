# REPRISE : où en est Bbox (26/10/2026, soir)

À lire en premier pour reprendre le travail. Ce document est mis à jour à chaque étape. La boussole est `VISION.md`, dans ce même dossier. Les décisions que Chafic a tranchées sont dans le skill `brain-in-a-box-product`, section « Décisions validées par Chafic, ne plus redemander » ; la même section est recopiée dans la page `projects/brain-in-a-box` du C-brain. On ne les repose pas.

## La prochaine action (29/10, 11 h 30 UTC)

Depuis le 26/10 à 0 h 50, le travail est partagé entre des sessions qui ont chacune leur mission et leur fichier de reprise ; ce fichier-là fait foi pour sa part :
- **Réunion** (`../MISSION-reunion-suite.md`, qui prolonge `../MISSION-branchements.md`) : `REPRISE-branchements.md`, section « Réunion ». Les branchements (téléphone par Telegram, présentation à voix haute, import de la mémoire, ami déjà équipé) sont terminés et fusionnés avec le guide dans la branche `bbox/reunion`. Les défauts de l'état des lieux sont corrigés (`028a154`, `7dea272`, `cdb943b`, `f3b61be`, `afd6c78`), build unique `Bbox afd6c78`. 28/10 : dernier passage sur le Mac neuf `bbox-reunion2`, captures `r3-*` ; prouvés : Bbox écrit le premier, un seul bonjour, zéro « Échec du tour », cerveau, mémoire ChatGPT, les cinq autorisations avec leur premier usage, puis Bbox propose le téléphone. Le retour après « Quitter et rouvrir » est corrigé dans `ec018a3` et **prouvé à l'écran le 28/10** (build `Bbox ec018a3`, validé par Chafic, quatrième passage, captures `r4-*`). **29/10 : première heure finie sur `Bbox ec018a3`** : téléphone relié à 10 h 59, Bbox écrit le premier sur Telegram à 11 h 13 et répond à Chafic en 10 secondes, visite avec la souris sur chaque arrêt, souris qui montre où retrouver Telegram (captures `r4-23` à `r4-34`). Nouveaux défauts 18 à 23 (téléphone et visite), notés, pas corrigés. Reste : le récapitulatif à Chafic sur Telegram.
- **Guide** (`../MISSION-guide.md`) : `REPRISE-guide.md` (souris et visite).

Ce qui attend Chafic : après le récapitulatif, choisir quels défauts corriger et quels Mac de test supprimer ; dire s'il régénère la clé Fastlane affichée par erreur dans une session précédente ; choisir, après le récapitulatif, quels Mac de test supprimer. Validé le 28/10 à 12 h 47 : `bbox-reunion1` supprimé (55 Go libres) et un build de plus (`ec018a3`) pour prouver le retour après « Quitter et rouvrir ».

Le détail de la priorité du 25/10 (cerveau, import, autorisations, tous prouvés) est dans `HISTORIQUE.md`.

### 2. Ensuite, dans cet ordre (validé le 25/10)
(État au 26/10 : 1 et 2 prouvés à l'écran ; 4 en cours dans les sessions guide et réunion ; 3 attend que Chafic dise qui.)
1. La mémoire de ChatGPT ou Claude, récupérée dans le cerveau (`importer-memoire`, `import.rs`), proposée par Bbox de lui-même. **Piste du PO (25/10, à vérifier au début du chantier)** : `importer-memoire` fait trier les souvenirs par la commande `claude`, qu'`install.sh` installe déjà ; elle accepte une connexion passée par `CLAUDE_CODE_OAUTH_TOKEN` (`_headless_claude.setup_token`). Bbox lancerait donc l'importeur du kit tel quel en lui prêtant sa propre connexion Claude, sans seconde connexion pour la personne. Le chemin le plus simple pour elle : la phrase à coller dans ChatGPT ou Claude (`importer-memoire phrase`), puis sa réponse collée dans Bbox (`preparer -`), l'accord, `lancer`.
2. Les autorisations du Mac une à une, chacune avec un premier usage utile (logique de `hermes_access.rs`).
3. Le premier essai avec une vraie personne : Chafic dira qui. On ne lui repose pas la question.
4. La souris Bbox et la visite en cinq arrêts.
5. Les Réglages au « tu » (environ 340 phrases), Windows, la signature Apple.

### 4. Plus tard (liste unique, rien ici n'est prioritaire)
- Calendrier jusqu'au vrai rendez-vous, refus, « Mon point du jour », annonce avant l'autorisation, « 3,8 Go » au lieu de « 3.8 GB » ;
- « Visite : Pas de réponse », chronomètre pendant la visite, « session », « Med », « Ran sw_vers », « J'ai fait N étapes », l'arrêt « Messageries » de la visite ;
- **place sur le disque** : un Mac plein (courant) fait refuser les outils d'Apple, et Awa resterait devant un message d'Apple. Apple demande 9,5 Go libres sur macOS 27 et 19,2 Go sur macOS 26.6 (mesuré). Bbox devrait regarder la place libre avant d'ouvrir la fenêtre d'Apple, le dire en une phrase et proposer de faire de la place avec elle ; à faire juste après la preuve du cerveau ;
- Bbox lui-même pèse 4,6 Go une fois installé : à alléger plus tard ;
- textes en anglais : sur un Mac réglé en français, la fenêtre est en français (« Nouvelle conversation », « Ce que je sais faire », « Prêt », « Intelligent », vu sur `bbox-vierge3`) ; restent « NEW SESSION » et « Med ». Les textes anglais du build 17 venaient du Mac de test réglé en anglais ;
- la réflexion du soir et la nuit du kit passent par Claude Code : `install.sh` l'installe, mais sans connexion à lui (Awa n'a que celle de Bbox) elles ne tournent pas ; à brancher sur la connexion de Bbox, hors de la première heure ;
- « Desktop boot failed » sur un Mac lent, le PATH du terminal, la licence MIT à l'écran, F6 et F7 avant une version publique, le Developer ID (et `fr.lproj` alors vérifiable), Windows.

## Décisions prises seul par le PO

(Chafic, 25/10 au soir : « tu avances seul, tu prends les initiatives nécessaires et tu en es responsable ». Une ligne par décision, datée, avec sa raison. Le point horaire lit cette section.)

- 26/10 0 h 50 : le point horaire suit les deux nouvelles sessions (leurs fichiers de reprise, leurs commits, leur activité) au lieu de cette seule session, et ne classe plus la visite ni les Réglages en « secondaire ». Raison : sinon il aurait signalé à tort la session guide, qui fait exactement sa mission, et l'inactivité normale de cette session. Il reste actif jusqu'à la fin de la phase 2, comme demandé.
- 26/10 0 h 30 : pour l'enregistrement de l'écran, Bbox dit d'activer « Bbox » (et non « CuaDriver ») et ouvre lui-même chaque page des Réglages (`425ac359c5`). Raison : macOS 27 range cette autorisation sous le nom de l'app qui contient CuaDriver ; dit « cherche CuaDriver », Awa ne trouve rien.
- 26/10 0 h 05 : une simple vérification ne démarre plus CuaDriver tant que l'écran n'a jamais été accordé (`7ee92e6314`). Raison : ce démarrage silencieux, pendant l'étape du micro, a dépensé l'unique question « contrôler ce Mac » de macOS, affichée derrière Bbox ; au moment où Awa a dit oui, les Réglages se sont ouverts sur une page vide.

- 25/10 23 h 20 : quand Bbox attend une validation (la carte « Autoriser » d'un clic, d'une commande), sa fenêtre revient devant d'elle-même, au lieu de compter sur une notification de macOS. Raison : pendant qu'il agit à l'écran, Bbox est caché derrière l'app où il travaille ; Awa n'a jamais vu la carte, le premier usage de l'écran a échoué en silence au bout de 5 minutes. La validation reste obligatoire : rien ne passe sans son clic.
- 25/10 23 h 20 : avant de dire l'écran « pas accordé », Bbox relance sans aucune fenêtre le service de CuaDriver et relit. Raison : après la relance demandée par macOS pour les fichiers, l'écran accordé était lu « désactivé », et Bbox disait à Awa de tout refaire dans les Réglages.
- 25/10 22 h 30 : les autorisations viennent après l'import, une à la fois, dans l'ordre micro, écran avec clavier et souris, tous les dossiers. Raison : le micro fait peur le moins et sert tout de suite (elle parle au lieu d'écrire) ; l'écran demande deux interrupteurs dans les Réglages, mieux acceptés une fois la confiance gagnée ; les dossiers en dernier, Documents étant déjà ouvert pour le cerveau.
- 25/10 22 h 30 : pour le micro, c'est la personne qui appuie sur le bouton micro de Bbox, et non Bbox qui déclenche la fenêtre de macOS. Raison : le même geste donne l'autorisation et lui apprend où parler, et le premier usage suit immédiatement.
- 25/10 22 h 15 : la dictée suit la langue du Mac quand la personne n'en a choisi aucune. Raison : Hermès écrit l'anglais par défaut, et Awa a vu sa phrase française écrite en anglais.
- 25/10 21 h 45 : la protection de `soul.md` reste en place ; Bbox range le ton et le style de la personne dans `Profile/memory.md` et ne touche à `soul.md` qu'avec son accord écrit (`3576a91b48`). Raison : la carte « Commande… Rejeter / Exécuter » juste après l'import ferait peur à Awa, et retirer la protection laisserait n'importe quel texte importé changer la façon dont Bbox se comporte.
- 25/10 21 h : Bbox installe Claude Code (installateur officiel d'Anthropic, la ligne du kit pour Linux) avant le kit sur un Mac. Raison : sans lui l'import ChatGPT ou Claude, deuxième priorité, ne peut pas marcher ; le kit le demande sur Mac sans le faire, et le modifier est exclu. La connexion reste celle de Bbox, prêtée pour le seul tri.
- 25/10 20 h 10 : le correctif de la deuxième question Documents est essayé sur blank101 remis sans cerveau, pas sur un troisième Mac vierge. Raison : 64 Go libres sur l'hôte, un Mac de plus en prend 41 ; la phase 1 est déjà prouvée sur un Mac vierge, et l'essai porte sur la question de macOS, que la remise à zéro rend neuve (nouvelle app, autorisation effacée).
- 25/10 20 h : les builds de test sont scellés ad hoc sous leur identifiant. Raison : sans ça macOS pose deux fois la question Documents et, sans le second clic, le cerveau reste vide en silence ; une app signée Developer ID fera pareil d'elle-même.
- 25/10 20 h : Bbox demande l'accès au dossier Documents en premier, avant la fenêtre d'Apple, dans le même message. Raison : c'est là que vit le cerveau ; sans cet accord, l'installation finit sans erreur mais le cerveau reste vide, et Awa ne le saurait jamais.
- 25/10 19 h : la mémoire passe par l'importateur du kit sans le modifier ; Bbox lui prête sa connexion Claude pour la durée de la commande. Raison : « réutilise, ne refais pas », et Awa n'a pas à se connecter une deuxième fois.
- 25/10 19 h : Bbox met lui-même la phrase dans le presse-papiers et ouvre ChatGPT (l'app si elle est là, sinon le site). Raison : Awa n'a plus qu'à coller, envoyer et copier la réponse.
- 25/10 18 h 30 : l'essai du cerveau se fait sur un Mac de test fabriqué depuis l'image officielle d'Apple, disque de 100 Go. Raison : deux Mac de test tout faits (disque de 50 Go) ont fait refuser les outils d'Apple faute de place, et les agrandir demande une commande administrateur, interdite.
- 25/10 16 h 45 : le build 17 part tel quel (`5c324d2`), sans attendre le chantier 11, pour que l'essai rapide d'Opus se fasse pendant que le PO lit l'installateur du kit. Raison : Chafic veut ce build, et le fabriquer ne bloque rien.
- 25/10 16 h 45 : un compte Claude dont l'abonnement ne propose pas Opus 5.5 garde Sonnet 5 (et jamais un Opus plus ancien). Raison : un modèle absent de l'abonnement ferait échouer la première réponse.
- 25/10 17 h : le chantier 11 se code dans un dossier à part (`~/brain-in-a-box-worktrees/bbox-c11`, branche `bbox/brain`), fusionné dans `~/bbox` une fois testé. Raison : `~/bbox` servait au build 17 en même temps.
- 25/10 17 h : le kit part dans Bbox comme copie figée à un commit noté (`bbox_kit/KIT_COMMIT`), et non téléchargé depuis GitHub. Raison : master n'a pas les travaux locaux, on ne publie rien, et l'installation doit marcher à l'identique à chaque fois.
- 25/10 17 h : l'installation du cerveau démarre seule après le prénom, et aussi au premier message quand aucun accueil ne tourne (Bbox déjà installé, accueil sauté). Raison : le cerveau doit être là d'office, sans que la personne le demande.
- 25/10 17 h : un cerveau déjà présent (`~/Documents/Brain`, la base de gbrain et `gbq`) n'est ni réinstallé ni touché ; Bbox apprend seulement où il est. Raison : ne jamais toucher aux notes de quelqu'un qui a déjà le kit.
- 25/10 17 h : sans outils Apple, Bbox ouvre la fenêtre d'Apple en disant quoi cliquer ; si la personne la ferme, un deuxième essai, puis « plus tard » sans insister. Raison : `install.sh` a besoin de git et de python3, et une fenêtre refusée ne doit pas bloquer la conversation.
- 25/10 17 h 30 : `install.sh` garde son choix d'office (Claude Code) ; on ne lui passe aucune option. Raison : c'est ce que fait l'app du kit sur Mac. Il installe lui-même la commande `claude` (depuis claude.ai) : c'est le moteur dont l'import de la mémoire et la réflexion du soir du kit ont besoin, et Bbox lui prêtera sa connexion Claude (voir le chantier 2).

## Où est le code

- **Bbox** est la copie privée de l'app d'Hermès (moteur et fenêtre, licence MIT), dans `~/bbox`, sur la branche locale `bbox/identity`. Elle part de la version `v0.22.5` d'Hermès. Le dépôt privé s'appelle `BrainInBox/bbox`, mais la branche `bbox/identity` n'y est pas poussée.
- **Ce dossier** est le kit, branche `feat/bbox`. Il contient `VISION.md` et ce fichier.
- **Les documents du PO** sont dans le dossier parent : `PLAN-bbox.md` (les phases), `FICHE-bbox.md` (la première heure), `PROJET-brain-in-a-box.md` (le dossier, §3 ter et §3 quater pour l'IA). Ce dossier parent n'est pas un dépôt git : ces fichiers ne se commitent pas.
- **Les scripts, sorties et captures d'essai** sont dans `../.scratch/phase1/`. Les outils (Tart, `cdp.py`, accès SSH aux Macs virtuels) sont dans `../.tools/`.

## Ce qui est fait

En bref (détail, commits et preuves dans `HISTORIQUE.md`, à côté de ce fichier) :
- Phase 0 : `VISION.md`, la fiche et le plan, validés par Chafic le 24/10.
- Phase 1 : l'app s'appelle Bbox partout, connexion Claude ou ChatGPT depuis la fenêtre, prouvée sur un Mac vierge le 24/10 ; le cas « Hermès déjà là » est testé (rien de la personne n'est touché).
- Builds 11 à 17 : accueil en français et au tutoiement, étape Mail et Calendrier, page « Ce que je sais faire ».
- 25/10 au soir : le cerveau installé par Bbox en 90 s avec l'installateur du kit, et l'import de la mémoire ChatGPT prouvé à l'écran (détail dans `HISTORIQUE.md`).
- 26/10 : les trois autorisations du Mac prouvées sur un même build (build 30 `7ee92e6`) ; les branchements (téléphone par Telegram, présentation à voix haute, import de la mémoire, ami déjà équipé) et le guide fusionnés dans `bbox/reunion` (`bbb6bb2`, un seul build).

### Restes de l'analyse « Hermès déjà là » (`../.scratch/phase1/coexist-analysis.md`)

- **F3, décidé par le PO le 25/10 : pas maintenant.** Quand Hermès tourne déjà, Bbox se raccroche à sa passerelle telle quelle : rien n'est perdu, mais les conversations tournent sur le code de la personne, donc sans les retouches Bbox (nom, pas d'IA gratuite). La cible (Awa) n'a pas Hermès ; ce cas concerne surtout les initiés. La « voie séparée » (verrou propre à Bbox, passerelle sans messageries) attendra un besoin réel.
- **Avant toute version publique** (les builds de commit ne sont pas concernés) : F6, le flux de mise à jour retombe sur `NousResearch/hermes-agent` si la variable d'environnement de la CI est absente (`electron-builder.config.cjs:61`), et `resolveUpdateRoot` doit rendre null en mode embarqué ; F7, le nom de commande d'une version publique est `hermes` (`product-identity.cjs:61`), à passer à `bbox`.
- F8 (facultatif) : l'étape de confirmation du modèle ne doit pas changer le modèle sans choix explicite quand d'autres fournisseurs existent.
- Restes signalés par les sous-agents F1, F2, F4 et F5 (non bloquants, l'essai du 24/10 est passé) :
  - côté moteur, `gateway stop`, `stop --all` et le ménage des passerelles orphelines cherchent les passerelles parmi les processus en marche ; en mode Bbox, ils pourraient encore arrêter celle de la personne. L'app ne les appelle pas dans l'essai (même processus avant et après), mais il faudra les protéger aussi ;
  - côté fenêtre, Bbox ne redémarre plus aucune passerelle, même la sienne : après une mise à jour de Bbox, sa propre passerelle garde l'ancien code jusqu'à ce qu'on quitte l'app. Il faudra que la passerelle déclare son dossier de code pour distinguer la sienne ;
  - la désinstallation « Bbox seul » n'existe que sur macOS ; Windows garde le désinstalleur du système (à vérifier au test Windows) ;
  - le message de refus en cas de passerelle étrangère dit encore « hermes gateway stop » : à réécrire en mots simples, au nom de Bbox.

## Journal des builds

Déplacé dans `HISTORIQUE.md`. Les nouveaux builds terminés s'y ajoutent.

## Notes d'essai

- Remettre le Mac de test à neuf : `vm-blank-1.sh` (retire le service de démarrage fabriqué, vide le disque avec `sync`, arrête le Mac virtuel proprement), relancer `tart run bbox-essai`, puis `vm-blank-2.sh`, puis le tunnel `-L 9333:127.0.0.1:9333`. Sans `sync` et sans arrêt propre, le déplacement du fichier de démarrage est perdu.
- Une commande `launchctl print` passée par SSH a été bloquée par une règle de sécurité du Mac mini : pour remettre le Mac de test à neuf, on déplace le fichier de démarrage et on redémarre le Mac virtuel au lieu d'appeler `launchctl`.
- Une session n'apparaît dans la fenêtre que si son nombre de messages est au moins 1.
- Mettre à jour Bbox sur le Mac de test sans perdre les données ni la connexion Claude de Chafic : `essai-update.sh <nom>` (quitte Bbox, remplace l'app par le build du commit courant, la relance avec le port 9333, compare réglages, `SOUL.md`, comptes et sessions avant et après).
- **Disque du Mac de test presque plein** (environ 200 Mo libres au build 7) : « Session unavailable / storage_unavailable » est apparu, et a disparu après avoir libéré de la place et relancé Bbox. Attention, ce message n'a pas une seule cause : au build 8, sur un disque à moitié vide, il venait d'une session sans modèle (voir plus haut). Lire le journal (`receipt refused: storage_unavailable … caused by …`) avant de conclure ; on a libéré des caches du système. Les trois copies de l'Hermès simulé (`~/run6d-traces`, `~/run6e-traces`, `~/coexist-traces`, environ 12 Go) contiennent un dossier `.hermes` : la règle de Chafic qui protège ces dossiers interdit de les supprimer, et c'est voulu. Agrandir le disque ne marche pas : la partition de secours du Mac virtuel bloque l'agrandissement, et macOS refuse de la retirer (protection du système). Le chemin retenu pour le build 8 : un nouveau Mac de test, `bbox-essai2`, cloné de l'image vierge macOS 27 (`macos-golden-gate-vanilla:27.0`, 11 Go libres, de quoi tenir l'app de 4,6 Go), joignable par `.tools/vm-ssh/vssh-bbox-essai2`, où l'on recopie la connexion Claude de Chafic depuis l'ancien par un tuyau SSH, sans jamais l'afficher ; l'ancien `bbox-essai` est ensuite arrêté et n'est plus utilisé, pour que les deux ne se disputent pas le même jeton. Les essais « Hermès déjà là » se font dans un clone jetable.
- **macOS n'accepte que deux Mac virtuels allumés à la fois** (« The number of VMs exceeds the system limit ») : pour démarrer `bbox-essai2`, `bibo-vm` a été arrêté le 25/10 (rien n'y tournait pour nos essais), puis `bbox-essai` une fois la connexion Claude recopiée (pour qu'un seul Mac rafraîchisse le jeton). Au 25/10, seul `bbox-essai2` tourne, avec Bbox ouvert et le tunnel `9333` ; `bibo-vm` et `bbox-essai` sont arrêtés, et se rallument par `tart run <nom> --no-graphics --no-audio`.
- Le sélecteur de modèle (en bas, à droite du champ) ne réagit qu'à de vrais clics de souris : `cdp.py clickxy <x> <y>` (ajouté le 25/10), pas `click`. Le choix vaut pour la conversation en cours ; l'étiquette se met à jour au message suivant.

- **État des Macs virtuels à la fin de la session du 25/10** : `bbox-essai2` allumé, Bbox `3f4b998` (build 16) ouvert avec le port 9333, accueil terminé, Calendrier et Mail autorisés ; `vnc-hold.py` et le tunnel `9333` tournent sur le Mac mini. `bbox-essai`, `bibo-vm` et `bbox-mac-vierge` sont arrêtés. Copies de réglages dans le Mac de test : `~/auth-parked/config.yaml.b11-before`, `.b14-before`, `.b16-before` (et `.with-model`).
- **Relancer l'accueil sur le Mac de test** : quitter Bbox puis l'arrêter par son numéro de processus ; `sed -i "" "s/^  run: false/  run: true/" ~/.hermes/config.yaml` ; `open -a "/Applications/Bbox <sha>.app" --args --remote-debugging-port=9333 "--remote-allow-origins=*"`.
- **Repères d'écran (Mac de test, 1024 x 768)** : par VNC, « Allow Full Access » du Calendrier en `512 354`, « Allow » de Mail en `571 430`, la fenêtre parasite `sshd-keygen-wrapper` se ferme en `453 382` ; par CDP, le prénom est `input[placeholder="Ton prénom"]`, la couleur en `434 245`, la case Calendrier en `810 317`.
- **Tart coupe le Mac virtuel à la deuxième connexion VNC d'un même lancement** : garder une seule connexion ouverte avec `.tools/vnc-hold.py` (copie dans `../.scratch/phase1/`), et passer par elle.
- **Diagnostiquer une autorisation de macOS** : jamais par SSH, car l'autorisation irait à `sshd` et une commande `osascript` vers System Events ou une app finit en attente (sortie 124) avec une fenêtre `sshd-keygen-wrapper`. On fait lancer le script par Bbox lui-même dans une conversation : c'est ainsi que `../.scratch/phase1/cal-diag.js` a prouvé le défaut du Calendrier (« before 3 string 9ms »).
- **Fabriquer un Mac vierge pour l'essai du cerveau** (les outils d'Apple demandent 9,5 Go libres sur macOS 27, 19,2 Go sur macOS 26.6 ; les Mac de test tout faits n'ont que 50 Go) : `tart create <nom> --from-ipsw latest --disk-size 100` (environ 45 min, image d'Apple de 18 Go), puis `../.scratch/phase1/blank100-first.sh <nom>` : Tart crée le compte et ouvre la session tout seul au premier démarrage (`--provisioning-opts`), sans écran d'Apple à cliquer. Ensuite `setup-vm.sh <nom>`, le français (`defaults write -g AppleLanguages -array fr-FR en` et `AppleLocale fr_FR`), `vierge-install.sh`, `vierge-claude.sh <nom> <source>`, `vierge-launch.sh`. Repères VNC de la fenêtre d'Apple sur macOS 27 : « Installer » `686 329`, « Accepter » `769 553`, « Terminé » `704 261`.
- **Commandes bloquées par les règles de sécurité du Mac mini, à ne pas contourner** : tout `rm -r` qui vise un dossier `.hermes` (dont `~/run6d-traces`), une commande qui contient le nom de la passerelle d'Hermès suivi de sa sous-commande, le `hermes` du paquet lancé sur le Mac de Chafic, `hdiutil` enchaîné avec `rm` et `sed`, `launchctl print` et `pkill` par SSH, `rm soul-probe-*`, prettier sur `electron/local-gateway.ts`. On déplace au lieu de supprimer, et on arrête un processus par son numéro.
- **Échecs de tests anciens, hors de notre travail** (notés pendant la session) : 7 échecs vitest et 3 tests Windows (`schtasks`) qui échouent déjà sans nos changements ; la vérification parente (`bbox-parent-check.sh`) reste la référence.

## Ce qui se fait à la fin, et que seul Chafic peut faire

Chafic connecte ses propres comptes sur le Mac de test : il colle le code Claude dans la page du relais (qui le tape dans Bbox), et il tape le code ChatGPT sur la page d'OpenAI. Le PO ne voit jamais ni mot de passe ni code Claude. Une fois la preuve faite, son compte ChatGPT est déconnecté du Mac de test.

## Règles à garder

- Rien n'est publié sur GitHub sans un récapitulatif validé par Chafic, et on ne touche pas à son Hermès quotidien (`~/.hermes`, en lecture seule).
- Un commit à chaque étape qui marche, sous-agents compris.
- Dans les Macs virtuels, on ne lance jamais `python3`, `git` ni `clang` système par SSH, car ils ouvrent la fenêtre d'installation des outils Apple. Les traces sont déplacées, jamais effacées ; nos propres builds d'essai, eux, sont supprimés.
- Pendant un chantier lancé, Chafic ne reçoit qu'un seul message, à la fin : les captures et le geste que lui seul peut faire.
