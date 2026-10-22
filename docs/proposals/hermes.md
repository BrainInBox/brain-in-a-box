# Hermès, l'interface par défaut (proposition)

Statut : brouillon. Décisions de Chafic du 20/10/2026 ci-dessous ; code en cours sur les branches `feat/hermes-*`, rien n'est fusionné. Il manque l'accord de Chad (décisions 0 et 3).
Base : `master` à `fa1ed66`, doc officielle de Hermès lue le 20/10/2026 (`llms-full.txt`), page « Legal and compliance » de Claude Code lue le même jour.

---

## Mise à jour du 20/10/2026 : décisions de Chafic

Cette section remplace la recommandation du §3 et les décisions 1, 2 et 4 du §7.

- **Claude par défaut, grâce au plugin officiel `claude-subscription-directsdk`** (catalogue de Nous Research, marqué « Experimental »).
  - Il fait tourner Hermès sur un abonnement Claude Pro ou Max en pilotant le programme Claude Code officiel, non modifié, avec le compte où l'utilisateur s'est connecté lui-même. Hermès ne touche jamais aux identifiants, ce qui reste dans le cadre des règles d'Anthropic.
  - Installation : `hermes plugins install claude-subscription-directsdk`, puis choisir le fournisseur `claude-subscription-directsdk-experimental`.
  - Prérequis : Hermès 0.21.4 ou plus récent, `claude` installé et connecté.
  - Décompte : chaque tour est pris sur le quota « Agent SDK » de l'abonnement.
- **Une personne qui a ChatGPT** : Hermès se connecte avec `openai-codex`. La décision 3 reste à Chad.
- **WhatsApp** : numéro personnel, en mode « message à soi-même », avec un avertissement clair sur le risque de restriction.
- **Nouveau : les autorisations de l'ordinateur, sur Mac et Windows.** Bibo guide l'utilisateur pour que Hermès puisse utiliser l'ordinateur.
  - Sur Mac : l'accès complet au disque pour `Hermes.app` ; l'Accessibilité et l'Enregistrement de l'écran pour l'identité donnée par `hermes computer-use doctor` ; le micro.
  - Aucune app ne peut s'accorder ces droits elle-même : Bibo ouvre le bon panneau, explique, puis vérifie.
  - Sous Windows : le micro, et l'Accès contrôlé aux dossiers de Defender s'il est activé.
- **Découpage en branches**, depuis `feat/hermes`, qui porte la base commune `app/src-tauri/src/hermes.rs` :
  - `feat/hermes-1-audit` : le diagnostic ;
  - `feat/hermes-2-install` : l'étape `hermes` ;
  - `feat/hermes-3-connexion` : le plugin Claude ou ChatGPT ;
  - `feat/hermes-4-cerveau` : l'import et GBrain en serveur MCP ;
  - `feat/hermes-5-messagerie` : WhatsApp et Telegram, et la passerelle en service ;
  - `feat/hermes-6-autorisations` : les autorisations.

## Mise à jour du 22/10/2026 : le guide demande à ChatGPT ou à Claude ce qu'ils savent

Le but du point 4 s'élargit : dès l'installation, le cerveau sait qui est la personne, à partir de ce que ChatGPT ou Claude sait déjà d'elle. Le lien Hermès et cerveau reste ; l'import vient le remplir.

- **Le guide propose, la personne n'a rien à chercher.** À la fin de l'installation, une fois l'IA du cerveau connectée, Bibo propose de lui-même : « ChatGPT te connaît déjà. Je lui demande ce qu'il sait de toi ? ». Avant, c'était un petit bouton « Importer ma mémoire » sur le dernier écran, qui reste.
- **Il devine à qui demander.** ChatGPT si l'ordinateur le montre (son app, celle de Codex, Codex ou sa connexion), sinon Claude si son app est là. Rien de visible (un compte utilisé seulement dans le navigateur ne laisse pas de trace) : la personne choisit, ou répond « Ni l'un ni l'autre », et l'entretien d'accueil part de zéro.
- **Trois gestes pour la personne.** Bibo copie sa question et ouvre `chatgpt.com/?q=` ou `claude.ai/new?q=`, la question dans l'adresse. La personne l'envoie, copie la réponse et la colle. L'importeur existant lit, trie et range ; la personne voit le plan et relit avant que rien ne soit gardé.
- **Une seule fois par lancement**, jamais quand un import est déjà dans le cerveau (`Imports/*-import-*.md`), ni quand l'IA du cerveau n'est pas connectée (c'est elle qui trie).
- **Limites connues.**
  - Aucune API ne donne cette mémoire : la connexion du point 3 ne la lit pas. Elle ne sort que par la fenêtre de ChatGPT ou de Claude.
  - `?q=` n'est un engagement ni d'OpenAI ni d'Anthropic, et OpenAI a limité l'envoi automatique en 2025. L'écran promet donc seulement d'ouvrir la page et de préparer la question ; elle est aussi copiée, à coller si elle n'est pas écrite. Si le presse-papiers refuse la copie, l'écran ne dit pas « copiée » : il montre le bouton « Copier la question ».
  - La vérification anti-robot de ChatGPT bloque un navigateur piloté : le pré-remplissage par `?q=` n'a pas pu être prouvé ici, seulement l'adresse et son ouverture.
  - La phrase fait 1 045 caractères et l'adresse 1 676, sous la limite de 2 000 de l'app et de 2 048 d'`open_link` (test `the_question_fits_in_the_address_that_writes_it`).
- **Avec le point 6** (écran, clavier et souris), le guide pourra faire ces trois gestes lui-même : ce sera le premier usage qui prouve l'autorisation.

## Mise à jour du 22/10/2026 : les autorisations de l'ordinateur (point 6)

À la fin de l'installation, Hermès peut ouvrir les fichiers de la personne, se servir de son écran et l'entendre, sans qu'elle cherche où ça se règle. Aucune app ne peut s'accorder ces droits elle-même : Bibo dit à quoi chacun sert, ouvre la fenêtre ou la page du système, puis vérifie tout seul (`app/src-tauri/src/hermes_access.rs`).

- **Le guide propose, une autorisation à la fois.** Avant l'offre de mémoire, Bibo demande à Hermès ce qui lui manque. Chaque autorisation est présentée par ce qu'elle apporte (« Avec ça, il retrouve un document que tu lui cites, range tes factures ou lit un PDF »), puis par ce qu'elle ouvre (« Ça lui ouvre tous tes fichiers, y compris tes mails et tes messages ») et la façon de la retirer. « Plus tard » passe à la suivante. Une seule fois par lancement, et ce qui est déjà accordé n'est jamais redemandé.
- **Sur Mac** :
  - **les fichiers** (accès complet au disque pour `Hermes.app`) : ce droit n'a pas de fenêtre à lui ; Bibo ouvre sa page des Réglages et montre l'app Hermès dans le Finder (`open -R`), à glisser dans la liste si elle n'y est pas ;
  - **l'écran, le clavier et la souris** (Accessibilité et Enregistrement de l'écran pour CuaDriver) : par le parcours d'Hermès lui-même, `hermes computer-use permissions grant`, précédé de `hermes computer-use install` si CuaDriver manque ; l'état vient de `hermes computer-use permissions status --json` ;
  - **le micro** : la fenêtre de macOS d'abord, au nom d'Hermès ; s'il a été refusé avant, sa page des Réglages, seul endroit où un refus se reprend.
- **Sous Windows** : le micro (pour toutes les apps, puis pour les apps de bureau) et l'Accès contrôlé aux dossiers de Defender quand il est activé. Windows ne demande rien lui-même : Bibo ouvre la page.
- **Quand c'est à la personne d'agir** dans une page, Bibo revérifie toutes les 4 secondes et passe à la suite dès que c'est fait.
- **C'est Hermès qui répond, pas Bibo.** macOS accorde ces droits app par app : un contrôle lancé par Bibo répondrait pour Bibo. Le contrôle (`hermes_access.js`) est donc lancé par l'app Hermès elle-même, en mode Node (`ELECTRON_RUN_AS_NODE=1`, par `open`), jamais depuis une copie. Pour les fichiers, il liste les dossiers que macOS protège, sans les lire, comme le docteur d'Hermès. Pour le micro, il lit l'état d'AVFoundation ; la fenêtre de macOS ne s'ouvre qu'après le « oui » de la personne.
- **Jamais à la place de la personne.** Ni la base TCC, ni `tccutil`. Un état que Bibo n'a pas pu lire n'est jamais pris pour un oui : l'écran demande. Bibo n'ouvre que des pages d'une liste fermée (`SETTINGS_PANES`).
- **Chaque autorisation finit sur un essai** proposé tout de suite dans Hermès, par exemple pour les fichiers : « Qu'est-ce qui traîne dans mes Téléchargements ? ».
- **Prouvé sur ce Mac**, sans rien accorder : le contrôle lancé par la vraie app Hermès a répondu en son nom (fichiers refusés, micro jamais demandé). C'est le test `the_real_hermes_app_answers_for_itself`, en lecture seule, ignoré par défaut et lancé à la main. Les écrans sont rejoués par `app/dev/screens.mjs` (10 parcours `acces=`).
- **Limites connues.**
  - Personne n'a encore accordé ces droits au bout du parcours : macOS ne le permet qu'à la main. `hermes computer-use permissions grant` n'a tourné qu'avec un Hermès d'essai.
  - L'essai est proposé, pas fait par le guide : c'est un écart au principe d'un premier usage réel qui prouve chaque autorisation. Avec l'écran accordé, le guide pourra le faire lui-même.
  - Si une version d'Hermès ferme le mode Node d'Electron (son fusible `RunAsNode`), le contrôle ne voit plus rien : l'état reste inconnu, jamais un oui.
  - Sous Windows, aucun vrai PC n'a été essayé. Bibo n'y installe pas encore Hermès (points 2 à 4 sur Mac seulement), et `hermes::bin()` ne cherche pas encore dans `%LOCALAPPDATA%\hermes`, le dossier d'Hermès sous Windows : l'écran n'y paraît que si la commande `hermes` est trouvée.
  - Sous Linux, rien à demander.

---

## 0. En bref

- **La demande** : un lien, un bouton « Installer », et la personne arrive sur Hermès, déjà relié à son cerveau et à son abonnement, puis branché sur WhatsApp et Telegram. Ni Python ni terminal à gérer. Claude par défaut.
- **Déjà dans Bibo** :
  - le diagnostic voit Claude Code et sa connexion (`claude`, `claude_auth`), ainsi que Codex et sa connexion ChatGPT (`codex`, `codex_auth`), cachés tant que Codex est fermé ;
  - l'écran « Avec quelle IA veux-tu travailler ? » (`agents.json`) ;
  - l'installation de Claude Code et de Codex ;
  - les boutons de connexion (`open_claude_login`, `open_codex_login`).
- **À ajouter** : une étape `hermes`, la connexion de Hermès à une IA, le lien entre Hermès et le cerveau, la messagerie guidée.
- **Bloquant** : Hermès ne peut pas utiliser l'abonnement Claude de l'utilisateur (§3), pour deux raisons :
  - techniquement, Claude Pro est refusé et Claude Max ne marche que sur des crédits payés en plus ;
  - les règles d'Anthropic l'interdisent.

  « Claude par défaut » et « Hermès par défaut » ne vont donc pas ensemble aujourd'hui.
- **Changement de cap** : depuis #24 (13/10), le kit est centré sur Claude Code. Le CHANGELOG dit « Hermes is no longer a target » pour les compétences, et `test-hooks.sh` vérifie qu'il ne reste aucune trace de Hermès dans les compétences fournies. Remettre Hermès au centre se décide avec Chad (décision 0).

---

## 1. Le parcours visé

1. **Diagnostic** (existant), plus trois lignes : « Hermès (ton assistant) », « Connexion de Hermès », « Hermès relié à ton cerveau ».
2. **Choix d'IA** (écran existant, `codex.md` §4.1) : on ajoute une phrase qui dit avec quoi Hermès répondra (§3).
3. **Installer** : un seul bouton. Étapes : agent(s), outils (`clt`, `bun`, `obsidian`), `brain`, puis `hermes`.
4. **Connexions** : une fenêtre du fournisseur par compte. Ce geste reste humain :
   - Anthropic exige que la connexion passe par son propre parcours ;
   - OpenAI passe par un code d'appareil.
5. **Hermès s'ouvre**, déjà relié au cerveau (§4).
6. **Messagerie** (facultatif, « Plus tard » possible) : Telegram en un scan, WhatsApp en un scan (§5).

---

## 2. Installer Hermès sans rien gérer

Faits vérifiés le 20/10/2026 :

- **Paquets officiels**, liés depuis le site de Hermès :
  - `https://hermes-assets.nousresearch.com/Hermes-Setup.dmg` (6 752 854 octets, modifié le 06/06/2026) ;
  - `https://hermes-assets.nousresearch.com/Hermes-Setup.exe`.
- **Signature** : une app installée depuis ce paquet (`Hermes.app`, identifiant `com.nousresearch.hermes.setup`, arm64) est signée Developer ID (équipe `T2F6S8MF7C`) et notarisée. Gatekeeper l'accepte, contrairement à Bibo aujourd'hui.
- **Rien à installer à côté** (doc, pages installation et bureau) :
  - les paquets embarquent le moteur, Python et les dépendances ;
  - au premier lancement, l'app installe le moteur dans `~/.hermes` (`%LOCALAPPDATA%\hermes` sous Windows), avec la même structure qu'une installation en ligne de commande.
- **Mac Intel** : non pris en charge par Hermès.
- **Linux** : pas de paquet bureau. L'installeur en ligne de commande fournit lui-même uv, un Python épinglé, Node, ripgrep et FFmpeg.
- **Installeur sans question** :
  - Mac et Linux : `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive` ;
  - Windows : `install.ps1` avec `-NonInteractive`.

Proposition :

- Étape `hermes` à la fin de `ORDER` (`steps.rs` l.45-52), sur les trois systèmes.
- **macOS** : même mécanique qu'Obsidian (téléchargement, `hdiutil`, copie dans `/Applications`). L'URL n'est pas versionnée (`?build=`) : on vérifie la **signature** (équipe `T2F6S8MF7C`, `spctl`) plutôt qu'une empreinte figée, qui casserait à chaque mise à jour de Hermès.
- **Moteur** : Bibo lance l'installeur non interactif plutôt que d'attendre le premier lancement de l'app, pour garder le journal et les erreurs dans Bibo. À vérifier en phase 0 : l'app reprend bien un moteur déjà installé.
- **Mac Intel** : étape sautée, avec une phrase claire. L'utilisateur garde Claude Code.
- **Linux** : moteur seul. WhatsApp et Telegram marchent, mais il n'y a pas d'app bureau.
- **Disque** : à mesurer en phase 0. L'installeur ajoute un navigateur Chromium par défaut ; `--skip-browser` existe.

---

## 3. Relier Hermès à une IA : le point dur

### Claude

- **Doc Hermès** (fournisseurs) : la connexion par abonnement « only works on a Claude Max plan with purchased extra usage credits », et « the base Max allowance is never consumed by Hermes ». **Claude Pro ne peut pas l'utiliser.** La seule autre voie documentée est une clé API Anthropic, payée à l'usage.
- **Règles d'Anthropic** (Claude Code, section « Authentication and credential use ») : « Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users. Moreover, developers may not collect, store, or intermediate Claude.ai credentials or session tokens ». Anthropic peut appliquer ces règles sans préavis.
- **Ce qui reste permis** : l'utilisateur qui se connecte lui-même au binaire Claude Code non modifié. C'est ce que fait le kit aujourd'hui.
- **Constaté en pratique** : un branchement direct de Hermès sur un abonnement Claude Max a déjà été refusé (erreurs 400 et 429, septembre 2026).
- **Conséquence** : Bibo ne branche pas Hermès sur l'abonnement Claude et ne copie aucun jeton Claude. Pour un utilisateur Claude, Hermès a besoin d'une autre source : une clé API Anthropic saisie par l'utilisateur, un abonnement ChatGPT ou un abonnement Nous Portal.

### ChatGPT

- **Prise en charge officielle** par Hermès (`openai-codex`) : connexion par code d'appareil, jetons dans `~/.hermes/auth.json`, sans Codex CLI. Ça fonctionne en pratique avec un abonnement ChatGPT.
- **Non documenté** par Hermès : quels forfaits ChatGPT sont éligibles, et comment l'usage est décompté.
- **Un seul détenteur par compte** (doc Hermès, sécurité et fournisseurs) :
  - deux connexions du même compte OpenAI partagent une famille de jetons, et OpenAI révoque la plus ancienne ;
  - un jeton emprunté à Codex CLI est invalidé chez l'autre au premier rafraîchissement (`auth.adopt_external_logins`).

  Si le kit fait tourner Codex et Hermès sur le même compte ChatGPT, il faut choisir qui tient la connexion (décision 3).

### Recommandation

Quand l'utilisateur n'a rien, proposer **ChatGPT par défaut** : un seul abonnement fait tourner à la fois le cerveau (Codex) et Hermès. Claude reste proposé, et Bibo dit alors clairement que Hermès demandera une autre source.

---

## 4. Relier Hermès au cerveau

- **Import** : `hermes import-agent claude-code --yes` (ou `codex`) importe :
  - les consignes (`CLAUDE.md` ou `AGENTS.md`), dans la mémoire de Hermès ;
  - les permissions Bash ;
  - les serveurs MCP ;
  - les compétences.

  Il ne lit jamais les identifiants. Les changements suivants se reprennent grâce à `~/.hermes/import-sync.json`.
- **Recherche dans les notes** : déclarer GBrain comme serveur MCP de Hermès (`gbrain serve` en stdio). Hermès interroge alors la mémoire directement. À vérifier : le chemin du binaire, et la cohabitation avec la réindexation de 4 h.
- **Résumé du soir** : la réflexion lit déjà les sessions Hermès quand Hermès est installé (CHANGELOG, `HERMES_DB`). À vérifier sur une installation neuve.
- **Taille** : la mémoire de Hermès a un plafond de caractères. Vérifier que le bloc du kit y tient après l'import.
  - **Écart constaté au point 4** : `hermes import-agent claude-code` apporte environ 3 000 caractères dans une mémoire plafonnée à 2 200 par défaut ; la vérification échoue. Bibo n'importe donc pas : il écrit une seule entrée courte dans `MEMORY.md` (où est le cerveau, les quatre notes Profil à lire d'abord, `gbq query` pour le reste), dans le plafond qu'Hermès annonce (`memory_char_limit`). Une mémoire pleine ne reçoit rien et l'étape dit quoi faire.

---

## 5. WhatsApp et Telegram

- **Telegram** : l'app Hermès a un bouton « Create with QR » (Messagerie, Telegram). Un scan dans Telegram suffit : Hermès crée le bot, détecte l'identifiant de l'utilisateur et enregistre les réglages.
- **WhatsApp** passe par le pont Baileys, non officiel, comme WhatsApp Web. Un scan du QR depuis WhatsApp (Appareils connectés) suffit. Deux modes :
  - numéro personnel, en se parlant à soi-même : rapide ;
  - numéro dédié : conseillé par la doc, car WhatsApp peut restreindre un compte relié à un outil non officiel.

  La voie officielle (WhatsApp Business Cloud API) demande un compte Meta Business et une adresse web publique : elle sort du parcours simple.
- **Toujours joignable** : la passerelle est installée en service (`hermes gateway install`, tâche planifiée sous Windows), et l'ordinateur doit rester allumé. À dire à l'écran : « Ton assistant répond tant que ton ordinateur est allumé. »
- **Proposition v1** : Bibo ne refait pas ces écrans. Il ouvre Hermès sur Messagerie et guide pas à pas ; le scan reste le seul geste.

---

## 6. Phases

0. **VM propres** (Mac arm64, Windows 11, Ubuntu 22.04). À tester : installation non interactive, taille et durée, reprise par l'app, connexion ChatGPT, import, GBrain en MCP, Telegram et WhatsApp. Les résultats sont écrits ici.
1. **Diagnostic** : `hermes`, `hermes_auth`, `hermes_brain`, en lecture seule.
2. **Étape `hermes`**, fermée par une porte comme Codex tant que la phase 0 n'est pas verte.
3. **Connexion de Hermès à une IA**, selon les décisions 1 à 3.
4. **Lien au cerveau** (import, MCP).
5. **Messagerie guidée.**
6. **Ouverture**, système par système.

Chaque phase est fusionnée seule, avec les gardes habituelles (`test-hooks.sh`, terrain).

---

## 7. Décisions

0. **Chad** : remettre Hermès dans le kit, comme interface par défaut.
1. **Chafic** : l'IA par défaut pour qui n'a rien. Recommandé : ChatGPT.
2. **Chafic** : l'utilisateur qui n'a que Claude. Trois possibilités :
   - proposer en plus ChatGPT pour Hermès ;
   - une clé API Anthropic ;
   - rester sur Claude Code, sans Hermès.
3. **Chad** : avec Codex et Hermès sur le même compte ChatGPT, qui tient la connexion.
4. **Chafic** : WhatsApp sur le numéro personnel (avec avertissement) ou sur un numéro dédié.

---

## Sources

- **Hermès** :
  - https://hermes-agent.nousresearch.com/docs/getting-started/installation
  - https://hermes-agent.nousresearch.com/docs/user-guide/desktop
  - https://hermes-agent.nousresearch.com/docs/integrations/providers
  - https://hermes-agent.nousresearch.com/docs/user-guide/import-from-other-agents
  - https://hermes-agent.nousresearch.com/docs/user-guide/messaging (pages WhatsApp et Telegram)
  - `https://hermes-agent.nousresearch.com/docs/llms-full.txt`, lu le 20/10/2026
- **Anthropic** : https://code.claude.com/docs/en/legal-and-compliance, section « Authentication and credential use », lue le 20/10/2026.
- **Téléchargements** : liens « Desktop » du site https://hermes-agent.nousresearch.com/
