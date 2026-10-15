# Plan : brancher Codex dans Brain in a Box

Statut : proposition en cours, livrée phase par phase (§6) ; Codex reste fermé sur tous les systèmes tant que son parcours n'y est pas vert. Base : master 407444e. Version 2 du plan : relecture sécurité + faisabilité intégrée (§8).
**Étapes 0 et 1 faites le 2026-10-15, étape 2 le 2026-10-16** : résultats, corrections et écarts dans les sections datées de la fin (« Étape 0 — résultats », « Étape 1 — fait et écarts », « Étape 1 — relecture et corrections », « Étape 2 — fait et écarts », « Détecteur : option dossier jetable »). En cas de conflit, elles priment sur le corps du plan, la plus récente d'abord.
Sources : **VM** = constaté dans les runs du spike (Codex 0.162.1, faux serveur, aucun secret) ; **doc** = doc officielle ; **code** = github.com/openai/codex, commit 3ee47c1 ; **dépôt** = notre code. Ce qui n'a pas de source est marqué **non vérifié**.

## 0. En bref

- `~/.brain-in-a-box/agents.json` dit qui est choisi (`claude`, `codex` ou les deux) et qui est préféré. Absent : Claude seul, comme aujourd'hui. **Ce fichier est la seule source** : la réflexion ne lit que les sessions des agents listés, le secours ne va qu'à un agent listé, les hooks Codex se taisent si `codex` n'y est pas.
- `_headless_claude.py` reste tel quel. On ajoute `_headless_codex.py` (même interface) et `_headless.py` (préféré, puis secours). Côté Rust : `codex.rs` à côté de `repair.rs`.
- Codex n'a ni « aucun outil » ni ligne d'init, et **un outil qui tourne ne se voit pas dans le flux `--json`** (VM + code). « Aucun outil » tient donc par la **prévention seule** : fonctions coupées et vérifiées en liste positive, `code_mode_host=false`, sandbox en lecture seule, valeurs sensibles **fixées par `-c`** (fournisseur, URL), contrôles d'avant (connexion ChatGPT, couches gérées, `.env`, MCP). Un détecteur a posteriori (lecture du rollout) vient en plus, si la phase 0 le valide.
- Mêmes hooks Python, appelés avec `--agent codex` via `hooks.json`. Approbation : décision 2. Windows : jamais vus tourner, la phase 7 attend la phase 0.
- `codex` reste **refusé** par `_agents.py` sur un OS tant que son parcours de bout en bout n'est pas vert : chaque phase fusionnée part chez les utilisateurs par `master.zip`.
- Pas de compte ChatGPT en CI sur le dépôt public (consigne d'OpenAI). La nuit, le vrai binaire tourne contre un faux serveur en mode ChatGPT. Le vrai compte sert à la main (décision 3).

---

## 1. Le choix de l'utilisateur

### 1.1 Stockage
`~/.brain-in-a-box/agents.json` (0600, écriture atomique) :
```
{"version": 1, "agents": ["claude", "codex"], "prefere": "claude",
 "codex_home": "/Users/x/.codex", "pose_par": "install.sh", "date": "2026-10-15"}
```
- Validité : `agents` sous-ensemble non vide de `{claude, codex}`, `prefere` ∈ `agents`, `codex_home` présent seulement si Codex est choisi.
- **Absent** : `["claude"]`, comportement d'aujourd'hui (test de non-régression, phase 1).
- **Illisible ou invalide** : on n'efface rien ; on se rabat sur `["claude"]` (le comportement d'aujourd'hui, pas une détection sur le disque), on le note dans le journal d'erreurs, le contrôle de nuit l'affiche en orange.
- `codex_home` relevé à l'installation (`$CODEX_HOME`, sinon `~/.codex`) : launchd, systemd et Task Scheduler ne voient pas un `CODEX_HOME` exporté dans le `.zshrc`.
- Un seul module, `engine/hooks/_agents.py`, copié avec les hooks (dépôt : boucle de `install.sh` §5, `install.ps1` l.412).
- **Porte par OS** : `_agents.py` refuse `codex` avec un message clair (« Codex arrive bientôt sur ce système ») tant que l'OS n'est pas ouvert. Ouverture : Mac/Linux en phase 6, Windows en phase 7, Bibo en phase 10b.

### 1.2 Qui pose le choix
- **install.sh** : options `--agents claude|codex|claude,codex` et `--prefere claude|codex`, et les variables `BIAB_AGENTS` / `BIAB_PREFERE` (lues aussi).
- **install.ps1** : `param()` déclare seulement `$BrainDir` (dépôt, l.34-35) et `irm … | iex` ne passe aucun paramètre. Donc paramètres `-Agents`/`-Prefere` **et** les mêmes variables `BIAB_AGENTS`/`BIAB_PREFERE` (le modèle de `BIAB_ARCHIVE`, l.289).
- Sans option ni variable : fichier existant gardé ; sinon question si terminal ; sinon `claude` (comportement actuel).
- Seul l'installeur écrit le fichier.
- **Bibo** passe le choix par les variables `BIAB_*`, sur les deux OS (il passe déjà `BRAIN_DIR` ainsi, `steps.rs` l.686). Il relit `agents.json` à l'ouverture.
- **Retirer un agent** (relance avec moins d'agents) : `agents.json` est mis à jour, les fichiers posés pour l'autre agent restent en place (le kit n'a aucune désinstallation, dépôt) et l'installeur le dit, avec la façon de les retirer à la main (`/hooks`, repère `<!-- brain-in-a-box -->`, dossier des skills). Les hooks lancés avec `--agent codex` relisent `agents.json` et se taisent si `codex` n'y est plus. Test d'aller-retour « les deux → Claude → les deux ».

### 1.3 Qui le lit, et pour quoi

| Partie | Ce qu'elle en tire |
|---|---|
| Installeurs | Le branchement à faire, et **si Claude Code est concerné** : installation de Claude, avertissement « install Claude Code first », vérification de `settings.json`, messages de fin (dépôt : `install.sh` l.422, 426, 760 ; `install.ps1` l.776-784) |
| `_headless.py` (réflexion, relecture) | Ordre des moteurs : le préféré, puis l'autre **seulement s'il est listé**. Jamais un binaire trouvé sur le disque |
| `controle.py` | Moteur de la relecture, libellé « relecture : Codex » |
| `daily-reflection.py` | **Seulement** les sessions des agents listés (absent : Claude, Hermes, git, comme aujourd'hui) |
| Hooks | L'agent vient de `--agent codex` ; avec cet argument, silence si `codex` n'est pas listé |
| Bibo (Rust) | Étapes, état « cerveau » par agent, moteur de la réparation, CLI de l'onboarding |

### 1.4 Les trois cas
- **Claude seul** : inchangé octet pour octet, **même si un Codex est installé à part avec des sessions**. Garde : test-hooks.sh, test_reflection, test_judge, terrain, plus un test où `~/.codex/sessions` est rempli et où le dossier envoyé reste identique.
- **Codex seul** : ni `settings.json` ni `CLAUDE.md`, **ni installation de Claude Code** (test VM : `--agents codex` ne crée pas `~/.local/bin/claude`). Hooks dans `~/.claude/hooks/brain` et journaux dans `~/.claude/logs` (§3.0). D'anciens transcripts `~/.claude/projects` ne sont pas lus.
- **Les deux** :
  - un coffre, un `lessons.md`, hooks des deux côtés, réflexion sur les sessions des deux ;
  - tâches sans outils au **préféré**, l'autre **en secours seulement**, si l'erreur montre que le préféré est inutilisable : `start`, `unsupported`, `auth`, `quota`, ou `api` après le nouvel essai prévu. **Pas de secours vers Codex sur `timeout`** (crédits, §2.3) ;
  - jamais de secours sur `init` ni `invalid` : anomalies signalées, pas masquées ;
  - l'état et le journal disent quel moteur a répondu ; `CALLS_PER_DAY` reste global ;
  - le flux entre fournisseurs va **dans les deux sens** (le préféré lit les sessions de l'autre ; le secours envoie tout au second) : dit à l'écran (§4.1).

---

## 2. Le lanceur « IA sans outils »

### 2.1 Interface (Python)
- `_headless.py` : `run(prompt, schema, system_prompt, *, profil, timeout, …)` → `Answer(output, moteur, version, cost_usd, num_turns)` ou `HeadlessError` (mêmes catégories). Aussi `check()` et `moteurs()`.
- `profil` (« reflexion », « juge ») → réglages par moteur. Codex : modèle (défaut Codex ou `BRAIN_CODEX_MODEL`), `model_reasoning_effort`, et un **délai plus court** que Claude (pas de plafond de coût, §2.3).
- `_headless_claude.py` : aucun changement de comportement. `_headless_codex.py` importe `supervise`, `_kill_tree`, `_pump`.
- Appelants : `daily-reflection.py` (`ask_claude` → `_headless.run`) ; `_controle_judge.py` ; `controle.py` (champ `claude` gardé, nouveau champ `moteur`, `--sans-claude`/`--avec-claude` en alias de `--sans-ia`/`--avec-ia`).

### 2.2 Le contrat Codex, point par point

| Garantie (contrat Claude actuel) | Codex : comment | Source |
|---|---|---|
| Aucun outil | **Prévention seule.** `-c features.*=false` (liste du spike) + `code_mode_host=false` + `-s read-only` + `approval_policy="never"` + `web_search="disabled"` + `agents.enabled=false`. Les outils encore annoncés échouent fermés (`exec` : « code-mode host is disabled » ; `apply_patch` : « patch rejected » ; non annoncé : « unsupported call »). Vérifié **avant chaque appel** par `codex features list` lancé avec nos `-c` et le même environnement : chaque fonction de notre liste, plus `hooks`, doit **exister et valoir false** ; une clé absente ou renommée = refus `unsupported`. Fonctions inconnues activées : décision 1 | VM (mode clé API, 3 OS) ; **mode ChatGPT non vérifié** (phase 0, bloque la phase 2) ; prise en compte des `-c` par `features list` **non vérifiée** (phase 0 ; sinon pas de Codex sans outils) |
| Aucun MCP, skill ou hook | `--ignore-user-config`, `skills.include_instructions=false`, `skills.bundled.enabled=false`, `features.hooks=false`. Pré-contrôle : `codex mcp list` lancé comme l'appel doit être vide (phase 0 : vérifier qu'il lit les mêmes couches). En mode ChatGPT, Codex appelle un MCP hébergé (`POST /backend-api/ps/mcp`) et annonce `image_gen__imagegen` hors verrouillage : à rejouer sous verrouillage en phase 0 | VM (hooks coupés : Linux et macOS) ; VM pour le MCP hébergé |
| Aucun réglage utilisateur | `--ignore-user-config --ignore-rules`, `project_doc_max_bytes=0`, `include_*_instructions=false`, `model_instructions_file=<cage>/system.md` | VM |
| Dossier neuf et privé | `_cage` : `-C <cage> --skip-git-repo-check` | dépôt + VM |
| Environnement en liste blanche | HOME, PATH, LANG, USER/LOGNAME, TMPDIR (ou TEMP/TMP), proxys + `SSL_CERT_FILE`/`CODEX_CA_CERTIFICATE`, `CODEX_HOME` (de `agents.json`), variables Windows, `BRAIN_HEADLESS=1`. Jamais `CODEX_API_KEY`, `OPENAI_API_KEY`, `CODEX_ACCESS_TOKEN`, `OPENAI_BASE_URL`. **`$CODEX_HOME/.env`** : Codex le recharge au démarrage pour tout nom qui ne commence pas par `CODEX_`. On en lit **les noms seulement** ; refus si un nom n'est pas dans notre liste blanche, ou s'il vaut `RUST_LOG`, `OPENAI_*`, `*_PROXY`, `SSL_*` ou contient `CA` (orange au contrôle de nuit) | dépôt ; VM (priorité de `CODEX_API_KEY`) ; code (`arg0/src/lib.rs` l.268-296) |
| Abonnement seulement | Avant chaque appel : (1) `auth.json` lu **seulement** pour `auth_mode == "chatgpt"` (écrit tel quel) et la présence de `tokens.refresh_token`. `OPENAI_API_KEY` **ignoré** en mode chatgpt : une vraie connexion ChatGPT y enregistre une clé obtenue par échange, non utilisée pour les appels au modèle ; (2) `codex login status` lancé avec le même environnement, le même `CODEX_HOME` et `-c cli_auth_credentials_store="file"` (aussi passé à `exec`) : sortie comparée à des **préfixes fixes** seulement (`Logged in using ChatGPT` = ok ; `Logged in using an API key`, `Logged in using workload identity`, `Not logged in` = refus), **stderr jamais recopié** (il contient 13 caractères d'une clé API) ; (3) aucune variable de clé chez l'enfant. On ne passe jamais `forced_login_method` (il efface `auth.json`) | VM ; code (`login/src/server.rs` l.442-455, `auth/manager.rs` l.594-600, `cli/src/login.rs` l.447-471 et 615-621) |
| Pas de détournement | **Valeurs fixées par `-c`**, qui passent avant User, Project, la config cloud entreprise et System : `model_provider="openai"`, `openai_base_url=""` (vide = défaut), `chatgpt_base_url=<URL officielle, à confirmer en phase 0>`. **Scan** seulement de ce qui passe avant les `-c` ou les contraint : `managed_config.toml` historique (fichier et MDM `com.openai.codex`) et `requirements.toml`. Refus `unsupported` s'ils définissent `model_provider`, `model_providers`, `openai_base_url`, `chatgpt_base_url`, une méthode de connexion autre que ChatGPT, `features`, `hooks`, `mcp_servers`, `plugins` ou `rules`. Phase 0 : un `/etc/codex/config.toml` piégé (`openai_base_url`, `mcp_servers`) doit rester sans effet | code (`config/src/loader/README.md` l.26-35 ; `state.rs` l.542 : le `model_provider` des exigences passe avant tout ; `core/src/config/mod.rs` l.3927-3934 ; `config_requirements.rs` l.1065-1072) ; **emplacements par OS non vérifiés** |
| Sortie JSON validée | `--output-schema <cage>/schema.json` (`json_schema` strict) + `-o <cage>/answer.json`. Dernier `agent_message` identique au fichier `-o`, puis `clean_answer` / `judge.validate` sans changement | VM |
| Ligne d'init vérifiée (fail-closed) | **N'existe pas.** Remplacée par : les contrôles d'avant ci-dessus ; premier événement `thread.started`, sinon refus | VM |
| Détection d'un outil | **Le flux ne voit pas les outils** : un `exec` forcé a lu un fichier et un `apply_patch` forcé a été refusé sans aucun item d'outil dans `--json` ; `map_item_with_id` jette code mode, DynamicToolCall, ImageView, ImageGeneration, Sleep… Le filtre du flux reste un **filet**, pas une garantie. Détecteur a posteriori, si la phase 0 le valide : pas de `--ephemeral`, lecture du rollout du `thread_id` (les `custom_tool_call` / `function_call` y figurent), tout appel d'outil → `HeadlessError("init")`, réponse jetée, rollout supprimé (phase 0 : aucune trace restante dans `codex resume`). Sinon `--ephemeral` et pas de détecteur, écrit dans SECURITY.md | VM (r3 §14, rollout r3-linux l.3432) ; code (`exec/src/event_processor_with_jsonl_output.rs` l.143-323) |
| Erreurs typées | `turn.failed` ou 401 « Missing bearer » → `auth` ; limite d'usage → `quota` (**forme non vérifiée**, captée dans `vrai-codex`), **aucun nouvel essai** ; le reste → `api`. `error` « Reconnecting… » = avertissement toléré | VM (401, Reconnecting) |
| Plafond de tours et de coût | **N'existe pas.** Délai court côté appelant. « Un seul tour » ne limite pas les appels (VM : 2 requêtes au modèle dans un tour) ; un second `turn.started` reste refusé comme filet. Tokens de `turn.completed.usage` notés | code (aide de `exec`) ; VM |
| Version minimale | `MIN_CODEX = 0.162.1` + options requises lues dans `codex exec --help` | VM |
| Windows : `.exe` seulement | `codex.exe` dans `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin` ; `.cmd` refusé | VM |

À vérifier (phase 0) : prompt **seulement par stdin** (ligne de commande Windows limitée à 32k, `DOSSIER_MAX` = 120k).

### 2.3 Écarts qui restent (explicites, pour SECURITY.md)
1. **`AGENTS.md` global injecté** (VM), inévitable sans copier `auth.json`, qui casse la connexion (`refresh_token_reused`, code). Compensation : fichier de l'utilisateur, aucun outil, sortie au schéma, bloc du kit limité aux sessions interactives.
2. **Aucun plafond de coût ni de tours, et l'argent n'est pas hors de cause** : le code gère des crédits et des plafonds de dépense pour les comptes ChatGPT (`CreditsSnapshot`, `SpendControlLimitSnapshot`, `protocol.rs` l.2529-2541). Passage automatique sur des crédits achetés à la limite d'usage : **non vérifié** (doc + `vrai-codex`). D'ici là : délai court, pas de nouvel essai sur `quota`, pas de secours vers Codex sur `timeout`. Si les crédits sont consommés seuls : avertir au choix (§4.1) et refuser l'appel quand des crédits sont actifs, si on peut le lire sans appeler le modèle.
3. **La sandbox laisse lire tout le disque, et un outil qui tourne ne se voit pas dans le flux** (VM). Seule la prévention protège ; le détecteur du rollout, s'il est retenu, voit l'outil après coup.
4. **Codex écrit dans `~/.codex` pendant un appel sans outils** (VM) : SQLite, ~7 800 fichiers de plugins, skills système.
5. **Formats instables et nouvelles fonctions** (doc ; 57 fonctions activées par défaut en 0.162.1, VM). Politique : décision 1. Le kit ne se met pas à jour seul (dépôt : `install.ps1` l.821 « relance la même commande ») : toute pause dit à l'écran « relance l'installation ».
6. **Mise à jour automatique de Codex** (`auto-update-version`, VM ; automatique **non vérifié**) : les contrôles d'avant tournent à chaque appel.
7. **Hooks, MCP et fonctions imposés par un administrateur** (`requirements.toml`) : refusés (§2.2) ; que `-c` ne les surcharge pas est **non vérifié**.

### 2.4 Rust (Bibo)
- `app/src-tauri/src/codex.rs` : `codex_bin()`, `codex_login_state()` (préfixes fixes, jamais la sortie brute), `argv(profil)`, `child_env()`, `check()` (mêmes contrôles d'avant : `.env`, couches gérées, `features list`, `mcp list`), lecture du rollout si retenue.
- `repair.rs` choisit le moteur avec `agents.json` (préféré, sinon l'autre s'il est listé). `REQUIRED_FLAGS`/`MIN_*` par moteur.
- Parité Python/Rust (`test_judge.py` lit déjà `repair.rs`, dépôt l.28) : argv, liste blanche, contrôles d'avant, refus. Pas de parité sur une « coupure du flux » qui ne garantit rien.

---

## 3. Branchement Codex (installeurs)

### 3.0 Emplacement des hooks : on ne bouge rien
Les hooks restent dans `~/.claude/hooks/brain` (plists, systemd, crontab, Task Scheduler, `controle` et tests y pointent). Un utilisateur Codex seul a un `~/.claude/` avec seulement hooks et journaux. Hors périmètre.

### 3.1 Installation de Codex (si choisi et absent)
- macOS/Linux : `curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh` via `logged env -u VOYAGE_API_KEY …`. Par utilisateur, sans sudo, 3 à 26 s (VM). Bloc PATH possible dans `~/.zprofile`/`~/.bashrc`… (code) : dit à l'écran.
- Windows : `irm https://chatgpt.com/codex/install.ps1 | iex`, sans console. PATH utilisateur seulement (VM) ; sans admin **non vérifié**.
- Aucune connexion par l'installeur : il affiche `codex login`. `--device-auth` (bêta, à activer dans ChatGPT, doc) hors défaut.
- Claude Code n'est installé, vérifié et cité que si `claude` est choisi (§1.3).

### 3.2 Consignes : `AGENTS.md` global **et** `AGENTS.md` du coffre
- **Global** (`$CODEX_HOME/AGENTS.md`) : ajout en fin de fichier avec le repère `<!-- brain-in-a-box -->` (modèle de `CLAUDE.md`, `install.sh` §9). Si `AGENTS.override.md` existe et n'est pas vide, Codex le lit **à la place** (code : `codex-home/src/instructions/mod.rs`) : le bloc va dans le fichier lu, et l'installeur le dit. Contenu : le même modèle que `CLAUDE.global.template.md`, lignes propres à l'agent remplies par `fill()`.
- **Coffre** : `vault-skeleton/CLAUDE.md` et `company-skeleton/CLAUDE.md` portent la lecture de soul/memory/lessons et la règle des corrections. Codex ne lit pas `CLAUDE.md` (code : `project_doc_fallback_filenames` vide par défaut, `config_toml.rs` l.85-87, `agents_md.rs` l.273). On pose un `AGENTS.md` court à la racine du coffre (perso et équipe, neuf ou existant, **seulement s'il est absent**) qui renvoie à `CLAUDE.md` : une seule source. On n'utilise pas `project_doc_fallback_filenames` (clé de premier niveau, §3.3). Phase 0 : Codex le charge depuis la racine git du coffre et suit le renvoi (**non vérifié**).

### 3.3 `hooks.json` et approbation
- Fusion dans `$CODEX_HOME/hooks.json`, modèle `merge_settings` : fichier illisible → étape stoppée sans y toucher ; sinon sauvegarde `.bak-<date>` 0600 et écriture atomique ; notre groupe sans `matcher`, à la fin ; entrées reconnues à leur chemin `/.claude/hooks/brain/<nom>`.
- Événements : `UserPromptSubmit` → correction-detector ; `Stop` → session-logger, session-indexer, session-recap ; `SessionStart` → gbrain-update-check, controle-sessionstart. Pas de `SessionEnd` (1 à 3 s, doc).
- Entrée : `"command": "<hook> --agent codex"`, `"commandWindows": "<python.exe> <hook> --agent codex"` (forme exacte selon la phase 0), `"timeout": 30`.
- Unix : `$SHELL -lc` (code). **Non vérifié** : un profil qui écrit sur stdout salirait la sortie JSON (phase 0).
- Sortie des hooks : Codex rejette **toute** la sortie si un champ est inconnu (`deny_unknown_fields`, code `hooks/src/schema.rs`) ; `systemMessage` est supporté. test-hooks.sh valide nos sorties contre le schéma de Codex (SessionStart, UserPromptSubmit).
- **Approbation** (décision 2). Si précalculée :
  - clé `<chemin absolu de hooks.json>:<event_snake>:<groupe>:<handler>` sur les positions réelles après fusion ; empreinte selon la formule du spike (Linux, macOS, 0.162.1 ; code `hooks/src/engine/discovery.rs`) ;
  - `config.toml` ne reçoit **que des tables neuves `[hooks.state."…"]`**, jamais une clé de premier niveau ni une table existante (TOML : une clé ajoutée après une table lui appartient ; une table redéfinie rend le fichier invalide) ;
  - pas de lecteur TOML (`/usr/bin/python3` = 3.9.6 sans `tomllib`, vérifié sur un Mac) : on ne réécrit **que nos clés exactes**, et on refuse si `hooks` apparaît sous une autre forme (clés pointées, table `[hooks]`/`[hooks.state]`, table en ligne) ;
  - après écriture, Codex valide lui-même le fichier (`codex features list` **sans** `--ignore-user-config`, code 0 attendu ; à prouver en phase 0), sinon restauration de la sauvegarde ;
  - l'empreinte porte sur la ligne de commande, pas sur le script (code) : **le kit approuve ses 6 hooks à la place de l'utilisateur**. Dit à l'écran et dans SECURITY.md, avec « on les retire avec /hooks ».
- Signal de vie : avec `--agent codex`, chaque hook note `~/.brain-in-a-box/codex-hooks-vus.json`. Sessions Codex plus récentes que l'installation sans signal → ligne orange « Les hooks Codex ne tournent pas : ouvre Codex, tape /hooks et approuve-les » (rapport du coffre et Bibo).

### 3.4 Les hooks lisent les payloads Codex (phase 5)
- Garde « mode sans outils » (`_agents.py`) : silence si `BRAIN_HEADLESS=1`, si `CLAUDE_CODE_ENTRYPOINT=sdk-cli`, ou avec `--agent codex` si `transcript_path` est nul ou si la première ligne dit `session_meta.source=="exec"` (VM). **Fichier pas encore écrit** : session considérée interactive (nos appels restent couverts deux fois : `features.hooks=false` + variable). Silence aussi si `codex` n'est pas dans `agents.json`.
- `correction-detector` : `prompt` identique, `additionalContext` injecté (VM).
- `controle-sessionstart` : `source:"startup"`, `additionalContext` injecté (VM) ; `systemMessage` **supporté** (code).
- `session-logger` / `session-indexer` : `session_id`, `cwd`, `transcript_path` (VM) ; champ `agent` ajouté.
- `session-recap` : anti-rebond 30 s et mise à jour par identifiant. **Identifiant Codex** : les 8 premiers caractères d'un UUIDv7 ne distinguent pas deux sessions de la même minute (VM : `01a14053-…` trois fois). Pour Codex, identifiant complet dans le libellé et le motif de remplacement (`session-recap.py` l.207, 234) ; format des blocs Claude inchangé. Même chose pour `Path(tp).stem[:8]` (`daily-reflection.py` l.254), qui vaut « rollout- » pour tout fichier Codex.
- Tous ces faits viennent de `codex exec` (`permission_mode: bypassPermissions`). Interface interactive **non vérifiée** : phase 0.

### 3.5 Skills
- Copie dans `~/.agents/skills/<nom>` (doc ; le code lit aussi `$CODEX_HOME/skills`, VM), via `install_skills.py --dest ~/.agents/skills --manifest ~/.brain-in-a-box/skills-codex.json` (options existantes).
- `controle` : `allowed-tools: Bash(...)` (syntaxe Claude), réaction de Codex **non vérifiée** ; `brain-boost` : section Codex (`codex mcp add`, `~/.agents/skills`) ; `quick-research`, `html-artifact` : vocabulaire neutre.

### 3.6 Sessions Codex → journal et memory
- `engine/hooks/_codex_rollout.py`, partagé par `daily-reflection` et `session-recap`, **utilisé seulement si `codex` est dans `agents.json`**.
- `$CODEX_HOME/sessions/AAAA/MM/JJ/rollout-*.jsonl` (VM, 3 OS), choisis par date de modification.
- `session_meta` → `cwd`, `source` ; `turn_context` → modèle ; `response_item` `message` user/assistant → texte ; `custom_tool_call` / `function_call` → outil et résumé.
- À filtrer (VM) : rôle user commençant par `# AGENTS.md instructions for` ou `<environment_context>`, et tout le rôle developer. `user_message` en interactif préféré s'il existe (**non vérifié**).
- Prompts du kit écartés (`SENTINEL`), y compris un rollout du détecteur non encore supprimé. Lignes inconnues sautées ; `.jsonl.zst` ignoré et signalé.

### 3.7 gbq, le coffre et `controle` dans la sandbox interactive (non vérifié)
- `gbq` écrit un verrou dans `~/.gbrain` et appelle Voyage ; `lessons.md` est hors du dossier de travail ; le skill `controle` écrit dans `~/.brain-in-a-box` et fait des commits dans le coffre.
- Une règle Codex `allow` exempte d'approbation **et sort de la sandbox** (code : `exec_policy.rs` l.442-455), et `gbq` transmet tout à gbrain (`"$GBRAIN" "$@"`, dépôt) : une règle sur `gbq` nu ouvrirait sync, embed, `skillify <url>`… hors sandbox. Donc règles **seulement** `["gbq","query"]` et `["gbq","search"]` ; phase 0 : `gbq query x; autre` ne doit pas être couvert.
- **Pas de `writable_roots`** : l'écriture de `lessons.md` passe par l'approbation, comme pour Claude (l'installeur n'accorde aucune permission à Claude, `merge_settings`). Pas de `network_access=true`.
- Le runner GitHub ne fait pas démarrer bwrap (`bwrap: loopback: Failed RTM_NEWADDR`, r3-linux l.3383) : sur Linux, « bloqué » y voulait dire « ne démarre pas ». Ces points se testent sur macOS et sur une VM Ubuntu 24.04 hors runner GitHub (**moyen à trouver**, non vérifié).

### 3.8 Rien d'écrasé (récapitulatif)
- `hooks.json` : fusion, sauvegarde, atomique, refus si illisible.
- `config.toml` : tables `[hooks.state."…"]` neuves seulement, sauvegarde, atomique, validation par Codex, restauration sinon.
- `AGENTS.md` global : ajout avec repère ; `AGENTS.md` du coffre : posé seulement s'il est absent.
- Skills : manifeste séparé, version modifiée par l'utilisateur gardée.

---

## 4. Bibo

### 4.1 Écran de choix
- Après le diagnostic, avant l'installation : « Avec quelle IA veux-tu travailler ? » — Claude (« avec ton abonnement Claude »), Codex (« avec ton abonnement ChatGPT »), les deux (« Lequel fait le résumé de chaque soir ? »).
- Pré-sélection d'après ce qui est installé et connecté.
- Phrase pour « les deux », dans les deux sens : « Le résumé du soir est écrit par X : il lit aussi tes sessions avec Y. Si X ne répond pas, Y prend le relais et lit tout. »
- Si la phase `vrai-codex` montre que des crédits ChatGPT sont consommés seuls : avertissement au choix de Codex.

### 4.2 Étapes et état « cerveau » par agent
- `ORDER` calculé : étapes `claude` et/ou `codex` en tête (Linux : après `paquets`). L'étape `codex` reprend l'étape `claude` (`steps.rs` l.353) avec `run_installer` (`CODEX_NON_INTERACTIVE=1`).
- **Aujourd'hui `detect::brain().complete()` exige `correction-detector.py` dans `~/.claude/settings.json` et `~/.claude/skills/brain-boost`** (`detect.rs` l.449-461) : Codex seul échouerait à chaque fois, et un utilisateur Claude qui ajoute Codex obtiendrait « Ton cerveau est déjà installé » sans relance (`steps.rs` l.590-592). Donc un état par agent (Codex : `hooks.json`, `AGENTS.md`, `~/.agents/skills`) ; « complet » = complet pour les agents de `agents.json` ; l'étape se relance si un agent choisi n'est pas branché.
- `diagnose.rs` : contrôles Claude fixes (`CLAUDE`, `CLAUDE_AUTH`, `BRAIN_HOOKS`, réseau vers claude.ai, l.58-64, 172-197) → contrôles et vérification réseau par agent.

### 4.3 Connexion ChatGPT, vérifiée sans clé facturée
- Bouton qui ouvre un terminal avec `codex login` (OAuth, port 1455, VM), modèle `open_claude_login`.
- Sondage : `codex login status` comparé à des préfixes fixes (sortie jamais affichée ni enregistrée) ; `auth.json` lu seulement pour `auth_mode` et la présence du jeton de rafraîchissement, `OPENAI_API_KEY` ignoré en mode chatgpt.
- Connexion par clé API : « Codex est connecté avec une clé API, qui serait facturée. Bibo ne s'en sert pas : connecte-toi avec ton compte ChatGPT (`codex logout` puis `codex login`). » Bibo ne déconnecte jamais seul. « Plus tard » possible.

### 4.4 Réparation et onboarding
- Réparation par `codex.rs` quand le moteur est Codex. Textes : une fonction de nom au lieu des 79 « Claude » en dur d'`app.js`.
- Terminal de réparation : Claude seulement en v1.
- Onboarding : CLI préféré ouvert dans le coffre (`codex "<prompt>"`), `ONBOARDING.md` indépendant de l'agent.

### 4.5 Garde de configuration
- `config_guard.rs` ne compare que `~/.claude`, les fichiers du shell et les tâches planifiées (l.23-37). Ajout : `$CODEX_HOME/hooks.json` (règle de type Settings), `config.toml` (seules nos tables `[hooks.state."…"]` peuvent apparaître), `AGENTS.md`/`AGENTS.override.md` (ajout seulement), `~/.agents/skills`.
- Liste `NEVER` du test du catalogue (`remedies.rs` l.579) : + `.codex`, `.agents` (`auth.json` et sessions y sont).

---

## 5. Tests

### 5.1 Unitaires (sans réseau, 3 OS dans test.yml)
- `engine/tests/fake_codex.py` (modèle `fake_claude.py`), piloté par `$HOME/.codex-fake/` : version, aide, `login status`, `auth.json`, `features list`, `mcp list`, scénarios d'événements (ok, `command_execution`, type inconnu, pas de `thread.started`, 401, limite d'usage, deux tours, blocage, orphelin), rollout. Enregistre argv, environnement, dossier, stdin.
- Cas :
  - `CODEX_API_KEY`/`OPENAI_API_KEY` du parent jamais chez l'enfant ;
  - connexion par clé API → refus `auth/billed` ; **`auth.json` ChatGPT réel avec `OPENAI_API_KEY` non nul → accepté** ;
  - `login status` qui affiche une fausse clé : elle n'apparaît dans **aucun** fichier produit (état, journal d'erreurs, coffre) ;
  - `.env` piégé (proxy, `RUST_LOG`, `OPENAI_ORGANIZATION`) → refus ;
  - `managed_config.toml` / `requirements.toml` qui détournent ou imposent hooks/MCP → refus ;
  - fonction de notre liste absente ou à true → refus ; `mcp list` non vide → refus ;
  - rollout avec `function_call` → `init`, réponse jetée, rollout supprimé ;
  - délai → arbre tué ; `.cmd` refusé ; secours seulement pour les bonnes catégories et vers un agent listé ; pas de secours vers Codex sur `timeout` ;
  - parité Python/Rust.
- Sessions : fixtures captées en phase 0. Claude seul + `~/.codex/sessions` rempli → prompt identique à aujourd'hui. Deux UUIDv7 de la même minute → deux blocs distincts.
- `test-hooks.sh` : payloads Codex, garde (`source=="exec"`, `transcript_path` nul, fichier absent, `BRAIN_HEADLESS`, `codex` non listé), sorties validées contre le schéma de Codex, signal de vie, fusion de `hooks.json`, `config.toml` déjà pourvu de `[sandbox_workspace_write]`, `[hooks]`, clés pointées (refus ou tables neuves seulement), idempotence, `AGENTS.override.md`, `AGENTS.md` du coffre déjà présent (gardé), aller-retour d'agents.
- Non-régression : sans `agents.json`, installation identique à aujourd'hui.

### 5.2 CI avec le vrai binaire, sans secret (test.yml, PR et push)
- Job `codex-mock` 3 OS : installeur officiel épinglé (`CODEX_RELEASE=0.162.1`), faux serveur extrait de `e6302ff:.github/workflows/spike-codex.yml` vers `engine/tests/mock_openai.py`, `auth.json` ChatGPT factice, URL du faux serveur via un enrobage `CODEX_BIN` de test (aucune porte de test dans le code de production).
- Vérifie :
  - un appel sans outils de bout en bout, **avec le verrouillage complet en mode ChatGPT** ;
  - le faux serveur force `exec`, `exec_command` et `apply_patch` et vérifie **la réponse d'outil qu'il reçoit** (« code-mode host is disabled », « unsupported call », « patch rejected ») ; toute autre réponse = rouge ;
  - le faux serveur relève les **outils annoncés** dans la requête : tout outil hors de la liste attendue = rouge ;
  - hooks approuvés par empreinte : rappel de correction reçu par le faux serveur, récap écrit, ligne du contrôle injectée ;
  - lecture d'une vraie session Codex.
- Linux : la sandbox ne démarre pas sur ce runner (§3.7), la réponse « patch rejected » y est attendue sous une autre forme (à relever en phase 0).

### 5.3 Tests de nuit (terrain.yml)
Chaque nuit, avec **le dernier** Codex :
- `codex-seul` (3 OS) : `--agents codex` sur VM neuve, pas de `claude` installé, branchement, idempotence, réflexion et relecture via Codex contre le faux serveur, mêmes contrôles d'outils forcés et annoncés qu'en §5.2.
- `les-deux` : Claude installé non connecté, `--prefere claude` : le secours Codex répond, l'état dit « codex ».
- `terrain_app.py` : le robot choisit Codex, étape Codex, nouveaux écrans, « Plus tard » à la connexion.
- `vrai-codex` (manuel, décision 3) : en plus, forme réelle d'un `auth.json` (noms de champs et présence seulement), message de limite d'usage, comportement des crédits.

Risque commun : jeton de rafraîchissement à usage unique (`refresh_token_reused`, code) ; deux runs en parallèle ou une copie ancienne cassent la connexion.

---

## 6. Phases

Chaque phase se fusionne seule, testée (types, lint, tests), 5 fichiers de code au plus (tests à part). `codex` reste fermé par `_agents.py` jusqu'à l'ouverture de son OS. Durées estimées (non vérifiées).

| # | Contenu | Fichiers principaux | Test qui la ferme | Durée |
|---|---|---|---|---|
| **0** | **Vérifs VM** (branche jetable, faux serveur, aucun secret, nombre de runs à autoriser par le mainteneur). Bloquant pour la phase 2 : verrouillage complet **en mode ChatGPT** sur 3 OS (outils annoncés, requêtes réseau, fichiers écrits) ; `features list` tient compte des `-c` ; `-c` fixe fournisseur/URL malgré un `/etc/codex/config.toml` piégé ; `mcp list` lit les mêmes couches ; détecteur du rollout (et suppression sans trace) ; prompt par stdin ; classement des 57 fonctions par défaut (outil ou non). Bloquant pour la phase 7 : hooks Windows, 3 variantes de `commandWindows` (sans guillemets, forme actuelle, `python.exe` par le PATH), `RUST_LOG` sur les hooks, avec 0.162.1 et la dernière version (le code récent met déjà la commande entre guillemets pour `cmd /C`, `command_runner.rs` l.399-401). Autres : hooks dans l'interface interactive (pty, extension de `.github/ci/terminal.py`), `AGENTS.md` du coffre chargé et suivi, `config.toml` validé par `features list`, `gbq query x; autre` non couvert, profil shell bavard, emplacements des couches gérées par OS, captures pour les fixtures. Linux sandbox : VM hors runner GitHub | workflow jetable | rapport de faits (format du spike) | 2 à 3 j |
| 1 | Le choix : `_agents.py` (porte par OS fermée), `--agents/--prefere`, `-Agents/-Prefere`, `BIAB_AGENTS/BIAB_PREFERE`, `agents.json` ; installation et messages de Claude conditionnés à `claude` | `_agents.py`, install.sh, install.ps1 | tests de `_agents`, non-régression sans fichier, `codex` refusé | ½ j |
| 2 | `_headless_codex.py` (contrôles d'avant, `.env`, couches gérées, `features list`, `mcp list`, `login status` à préfixes, rollout si retenu) + `fake_codex.py`, non branché | `_headless_codex.py` | §5.1 | 1 j |
| 3 | `_headless.py` (ordre + secours vers un agent listé) branché dans réflexion et relecture ; libellés de `controle.py` | `_headless.py`, daily-reflection.py, _controle_judge.py, controle.py | test_reflection, test_judge, test_controle avec faux codex | ½ j |
| 4 | Lecture des sessions Codex, seulement si listé ; identifiants complets | `_codex_rollout.py`, daily-reflection.py, session-recap.py | fixtures phase 0, non-régression Claude seul, UUIDv7 | ½ j |
| 5 | Hooks compatibles Codex (garde, `codex` listé, champ `agent`, signal de vie, sorties au schéma de Codex) | correction-detector, controle-sessionstart, session-logger, session-indexer, `_agents.py` | test-hooks.sh | ½ j |
| 6 | install.sh : Codex, `AGENTS.md` global et du coffre, `hooks.json`, approbation, skills, règles gbq ; **ouvre `codex` sur Mac et Linux** | install.sh, CLAUDE.global.template.md, vault-skeleton/AGENTS.md, company-skeleton/AGENTS.md, install_skills.py (si besoin) | test-hooks.sh + `codex-mock` macOS/Linux | 1 j |
| 7 | install.ps1, la même chose ; **ouvre `codex` sur Windows** (bloquée par la phase 0) | install.ps1 | `codex-mock` Windows | 1 j |
| 8 | Rust `codex.rs` : détection, connexion, contrat sans outils | codex.rs, lib.rs | cargo test + parité | 1 j |
| 9 | Réparation avec Codex | repair.rs, app.js (textes de réparation) | cargo test, maquette `app/dev` | ½ j |
| 10a | Bibo, fond : état « cerveau » par agent, étape Codex, diagnostic par agent, garde de configuration | detect.rs, steps.rs, diagnose.rs, config_guard.rs, remedies.rs | cargo test | 1 j |
| 10b | Bibo, écrans : choix, connexion ChatGPT, onboarding, nom de l'assistant ; **ouvre Codex dans Bibo** | app.js, lib.rs, ONBOARDING.md | cargo test + `terrain_app` choix Codex | 1 j |
| 11 | Nuit : `codex-seul`, `les-deux`, `vrai-codex` manuel | terrain.yml, terrain.py, terrain_app.py | un run de nuit vert | 1 j |
| 12 | Docs : README, SECURITY.md (§2.3, approbation des hooks, `.env`, règles gbq, outil invisible dans le flux), brain-boost, skills neutres | docs, skills | relecture | ½ j |

Total estimé : 12 à 13 jours. Codex seul en terminal sur Mac/Linux après la phase 6 ; dans Bibo après la 10b.

**Si les hooks Windows restent introuvables**, la phase 7 sort en mode dégradé annoncé : `AGENTS.md`, skills, réflexion (sans hook), relecture, réparation ; sans récap par session, sans rappel de correction (consigne seule), sans ligne du contrôle au démarrage (visible dans Bibo et le rapport).

**Hors périmètre** : gstack pour Codex ; extension IDE, app de bureau et cloud de Codex ; serveurs MCP ; `--device-auth` ; utilisateurs Codex à clé API (refusés) ; déplacement des hooks hors de `~/.claude` ; terminal de réparation avec Codex ; hooks gérés par une config système ; mise à jour automatique du kit.

---

## 7. Risques et décisions

### Risques principaux
- **Outil invisible** : rien dans le flux ne montre un outil qui tourne. Si une fonction nouvelle ou mal coupée en ajoute un, la sandbox laisse lire le disque. Seule la prévention protège (décision 1).
- **Verrouillage jamais vu en mode ChatGPT** : la phase 2 attend la phase 0.
- **Hooks Windows jamais vus tourner** : bloquent la phase 7 complète.
- **Empreinte d'approbation non documentée**, dépendante de la position : une mise à jour de Codex ou un hook ajouté avant les nôtres les arrête sans bruit ; vu par le signal de vie.
- **Crédits ChatGPT** : un appel sans plafond pourrait coûter de l'argent si les crédits sont consommés seuls (non vérifié).
- **Connexion fragile** : jeton de rafraîchissement à usage unique ; session interactive et appel de nuit qui rafraîchissent ensemble : **non vérifié**.
- **Le kit ne se met pas à jour seul** : un correctif livré n'arrive qu'à la relance de l'installation.
- **Conditions d'OpenAI** sur l'usage automatisé d'un abonnement ChatGPT (deux réflexions par jour) : **non vérifiées**.

### Décision 1 : Codex pour les tâches sans outils, et sa garde des fonctions
Accepter Codex pour réflexion, relecture et réparation malgré les écarts du §2.3 (`AGENTS.md` injecté, sandbox qui lit le disque, outil invisible dans le flux, pas de plafond) — sinon « Codex seul » perd ces trois fonctions. Reste la politique face à une fonction **inconnue** activée par défaut (Codex en a 57 en 0.162.1) :
- **Stricte (recommandé)** : toute fonction activée hors de notre liste classée (phase 0) met le moteur Codex en pause (« relance l'installation »), secours vers Claude s'il est listé. Fail-closed comme le contrat Claude ; coût : des pauses à chaque version de Codex qui ajoute une fonction, jusqu'à la relance du kit.
- Liste positive : seules nos fonctions connues doivent valoir false, une inconnue passe en orange. Moins de pauses, mais un nouvel outil pourrait tourner jusqu'au correctif, sans être vu.
- Pour réduire les pauses plus tard : une mise à jour de nuit du kit sur le modèle de `gbrain_update` (hors périmètre ici).

### Décision 2 : approuver les hooks Codex
- **Recommandé** : empreinte précalculée par l'installeur, **annoncée** (« le kit approuve ses 6 hooks ; tu peux les retirer avec /hooks », écran + SECURITY.md), signal de vie, consigne de repli. Marche seule en 0.162.1 (Linux, macOS).
- Autre : `/hooks` à la première session (documenté, mais rien ne tourne tant que ce n'est pas fait).

### Décision 3 : le vrai Codex en CI
OpenAI demande de ne pas utiliser la connexion ChatGPT en CI sur un dépôt public (doc).
- **A (recommandé)** : pas de compte en CI ; la nuit, vrai binaire + faux serveur en mode ChatGPT ; `vrai-codex` lancé à la main par le mainteneur avant chaque version qui touche Codex (sa connexion, sur une VM jetable ou un poste où il accepte Codex). Non vu la nuit : une régression côté serveur OpenAI.
- B : dépôt privé séparé, compte ChatGPT de test dédié, réécriture d'`auth.json` à chaque run (un abonnement de plus, reconnexion à la main à chaque jeton grillé).
- C : clé API en CI. Rejetée : elle oblige à couper la garde « abonnement seulement ».

---

## 8. Relecture : constats intégrés

Tous les constats sont retenus ; 3 doublons fusionnés, 3 correctifs adaptés.

| Constat | Où |
|---|---|
| Coupure par le flux aveugle (sécurité + faisabilité, doublons) | §0, §2.2 « Aucun outil » / « Détection », §2.3.3, §5.2, §7 |
| `$CODEX_HOME/.env` rechargé | §2.2 liste blanche, §5.1 — **adapté** : refus de tout nom hors liste blanche, en plus des motifs proposés |
| Couches de config non écartées | §2.2 « Pas de détournement » (vérifié : `-c` passe avant cloud et System ; `managed_config` et `requirements.toml` passent avant `-c`) |
| `OPENAI_API_KEY` dans un vrai `auth.json` | §2.2, §4.3, §5.1 (vérifié `server.rs` l.442-455) |
| Crédits ChatGPT | §1.4, §2.2, §2.3.2, §4.1, §5.3, §7 |
| Sessions chez l'autre fournisseur (doublons) | §0, §1.3, §1.4, §3.6, §4.1, §5.1 |
| Règles gbq et `writable_roots` | §3.7 (vérifié `exec_policy.rs`, `engine/bin/gbq`) |
| Bloc `config.toml` fragile / tables neuves seulement (fusionnés) | §3.3, §3.8, §5.1 (vérifié Python 3.9.6) |
| `login status` affiche 13 caractères | §2.2, §4.3, §5.1 (vérifié `safe_format_key`) |
| Verrouillage jamais vu en mode ChatGPT | §2.2, phase 0 bloquante |
| `CLAUDE.md` du coffre ignoré par Codex | §3.2, phase 6 (vérifié : fallback vide par défaut) |
| Bibo : état cerveau, `diagnose.rs` | §4.2, phase 10a (vérifié `detect.rs`, `steps.rs`) |
| `sid[:8]` en collision | §3.4, phase 4 |
| Garde stricte vs nouvelles fonctions | décision 1 — **adapté** : tranché par le mainteneur, pas dans le plan |
| Phases livrées aussitôt | §1.1 porte par OS, §6 |
| Claude installé en Codex seul | §1.3, §1.4, §3.1, phase 1 (vérifié `install.sh`, `install.ps1`) |
| `config_guard` aveugle à Codex | §4.5, phase 10a |
| Sandbox Linux du runner | §3.7, phase 0 (vérifié r3-linux l.3383) |
| `cmd /C` déjà traité dans le code récent | phase 0 (vérifié `command_runner.rs` l.399-401) |
| Hooks vus seulement en `exec` | §3.4, phase 0 — **adapté** : fichier absent = session interactive |
| Exigences administrateur (`requirements.toml`) | §2.2, §2.3.7 (fusionné avec les couches de config) |
| `-Agents` sous Windows | §1.2 (vérifié `param([string]$BrainDir)`) |
| `systemMessage` + `deny_unknown_fields` | §3.3, §3.4, §5.1 (vérifié `schema.rs`) |
| Retrait d'un agent | §1.2 |

## Décisions du mainteneur (2026-10-15)

1. **Nouvelle fonction de Codex** : « On update aussi ». La garde reste stricte : une fonction inconnue activée met Codex en pause pour la réflexion, la relecture et la réparation (secours Claude s'il est choisi). En plus, le kit se met à jour tout seul : la liste des fonctions connues et coupées vit dans un fichier de données à part (ex. `engine/codex-features.json`, hors CODEOWNERS), la nuit (terrain) installe le Codex le plus récent et signale toute fonction absente de la liste, l'agent réparateur du matin l'y ajoute (désactivée) avec un test, et fusionne si tout est vert. Ajouter un nom ne peut que rendre plus strict ; un test vérifie que tout nom listé est passé à false par le lanceur. Retirer un nom ou toucher au lanceur reste réservé au mainteneur (CODEOWNERS).
2. **Approbation des hooks** : l'installeur approuve les hooks du kit (empreintes précalculées), le dit à l'écran et dans SECURITY.md (« retire-les avec /hooks »), signal de vie en filet.
3. **Vrai Codex** : pas de compte ChatGPT en CI. La nuit, le vrai binaire Codex tourne contre le faux serveur ; le scénario `vrai-codex` se lance à la main avant chaque version qui touche Codex.

---

## Étape 0 — résultats (2026-10-15)

**Comment.** Workflow jetable `phase0-codex.yml` sur `feat/codex` (push limité à ce fichier, `contents: read`, aucun secret), retiré par le commit `59bb206`. Faux serveur local (réponses, backend ChatGPT, MCP hébergé), `auth.json` ChatGPT factice, proxy local qui refuse et note toute connexion vers un autre hôte, `strace` sous Linux. Codex **0.162.1** (stable, épinglé) et **0.163.0-alpha.11** (dernière pré-version publiée avec binaires ; alpha.12/13 : tags sans binaires), sur ubuntu-24.04, macOS 15 (arm64), Windows Server 2025. 3 runs sur 4 :
- run 1 : https://github.com/BrainInBox/brain-in-a-box/actions/runs/38777454388
- run 2 : https://github.com/BrainInBox/brain-in-a-box/actions/runs/38779052837
- run 3 : https://github.com/BrainInBox/brain-in-a-box/actions/runs/38780332011

Sonde réutilisable (faux serveur, MCP, proxy) : dans `.github/workflows/phase0-codex.yml`, commit `0c90cc1` de `feat/codex`. Captures pour les fixtures (rollouts exec et TUI, payloads de hooks, `features list`, classement, `classify-<os>.json`) et faits de chaque run (`results.json`) : `engine/tests/fixtures/codex/phase0/`, sans secret (jetons factices).

### Faits

| Point | Résultat | Source |
|---|---|---|
| Verrouillage complet, mode ChatGPT, 3 OS, 2 versions | Outils annoncés : `exec` (code mode, décrit `apply_patch`, `clock__curr_time`, `list_mcp_resource_templates`, `list_mcp_resources`, `read_mcp_resource`), `wait`, et `request_user_input_async` (0.162.1 seulement). Aucun shell, image, goals, sleep, agents, web. 11 appels forcés (exec JS, `exec_command`, `apply_patch`, `shell`, `spawn_agent`, `view_image`, `image_gen__imagegen`, `wait`, `request_user_input_async`…) : tous refusés (« code-mode host is disabled », « unsupported call », « patch rejected… read-only sandbox »), sauf `request_user_input_async` valide → `{"accepted":true}` (message à l'utilisateur, ne bloque pas, aucun accès). Aucun fichier écrit, aucun contenu lu rendu | VM runs 1-3 |
| Réseau sous verrouillage | Seulement les URL fixées : `/v1/models`, `/v1/responses`, `/backend-api/wham/accounts/check`, `/backend-api/wham/settings/user`. Proxy : 0 autre hôte ; `strace` : une seule adresse (le faux serveur). Pas de MCP hébergé, pas de plugins, pas d'analytics | VM |
| Fichiers écrits | Dans `CODEX_HOME` seulement : `goals_1`, `logs_2`, `memories_1`, `queue_1`, `state_5` `.sqlite` (+wal/shm), `installation_id`. Rien ailleurs, aucun processus restant | VM |
| Texte injecté sous verrouillage | `AGENTS.md` global (attendu, §2.3.1) + `model_instructions_file`. Ni skills, ni config, hooks.json ou MCP de l'utilisateur | VM |
| URL officielles | `openai_base_url=""` → `https://chatgpt.com/backend-api/codex` (le vide vaut « non défini ») ; `chatgpt_base_url` officiel = `https://chatgpt.com/backend-api/` (défaut). Avec proxy refusant tout : CONNECT `chatgpt.com:443` (+ `auth.openai.com:443` sur macOS, rafraîchissement) | VM ; code `model-provider-info` l.427-445, `core/src/config/mod.rs` l.3829, l.4455 (0.162.1) |
| Flux `--json` | Premier item : `error` « Code Mode is unavailable because code-mode host is disabled… » à chaque appel verrouillé : à tolérer comme « Reconnecting » | VM |
| `features list` et `-c` | `-c` pris en compte (avant ou après la sous-commande) et prioritaire sur `config.toml`. Clé inconnue (`features.p0_does_not_exist=false`) acceptée en silence. **`unified_exec` reste `true` malgré `-c false`** (seul `requirements.toml` peut le couper) ; sans `shell_tool` il n'ajoute aucun outil. Les 9 fonctions au stade `removed` ignorent `-c`. `features list` n'a pas `--ignore-user-config` : il lit `config.toml` de l'utilisateur (plus strict que l'appel ; un `config.toml` invalide le fait échouer, rc 1) | VM ; code `managed_features.rs` l.178-184 |
| Config système piégée (`/etc/codex/config.toml`, `%ProgramData%\OpenAI\Codex\config.toml`) | `-c` l'emporte pour fournisseur, URL, fonctions : 0 requête au piège. **Mais** ses `mcp_servers` démarrent (malgré `--ignore-user-config`) et son `developer_instructions` est injecté. Neutralisés par `-c developer_instructions=""` et `-c mcp_servers.<nom>.enabled=false` (vérifié 3 OS) | VM runs 1-2 |
| `requirements.toml` (`/etc/codex/`, `%ProgramData%\OpenAI\Codex\`) | Passe avant `-c` : `model_provider` → tous les appels au piège ; `chatgpt_base_url` → appels backend (avec le jeton) au piège ; `[features]` épingle (visible dans `features list`) ; `additional_developer_instructions` injecté ; hooks gérés : tournent sans verrouillage, bloqués par `features.hooks=false` | VM |
| `managed_config.toml` historique | Unix `/etc/codex/` : passe avant `-c` (URL + fournisseur) et démarre ses MCP. Windows : `CODEX_HOME\managed_config.toml` ignoré | VM |
| MDM macOS | Plist écrit dans `/Library/Managed Preferences/com.openai.codex.plist` sans profil MDM : sans effet (Codex ne lit que les valeurs « forcées », clés `config_toml_base64`, `requirements_toml_base64`) | VM ; code `loader/macos.rs` l.27-29, 179-207 |
| `mcp list` | Lit la config utilisateur **et** système ; l'appel verrouillé ignore l'utilisateur mais charge le système : pas les mêmes couches. `mcp list --json` (avec nos `-c`) montre `enabled:false` après neutralisation | VM |
| Détecteur du rollout | Rollout non éphémère : `custom_tool_call`, `function_call` et leurs sorties y sont ; le flux n'en montre aucun (3 OS). `codex delete <id> --force` retire le fichier **et** les lignes SQLite (`threads`, `thread_items`, `thread_turns`…), mais les octets du prompt restent dans `state_5.sqlite(-wal)` / `thread_history_1.sqlite(-wal)`. Supprimer le fichier seul laisse les lignes. `--ephemeral` : aucune trace (fichiers et SQLite) | VM runs 1-2 |
| Prompt par stdin | `-` + stdin : 150 005 caractères UTF-8 (accents, CJK, emoji, CRLF) reçus à l'identique, 3 OS. Argument **et** stdin : stdin ajouté en bloc `<stdin>` | VM |
| Classement (57 fonctions Unix, 58 Windows ; cadre = nos réglages hors fonctions) | **Outils** : `shell_tool` (`exec_command`, `write_stdin`), `view_image`, `image_generation`, `goals`, `sleep_tool`. **Capacité** : `code_mode_host` (seul, il rend le JS exécutable). **Réseau / MCP hébergé** : `apps` (initialize + tools/list sur `/backend-api/ps/mcp`), `plugins`, `remote_plugin`. **Forcée** : `unified_exec`. **removed** (9) : sans effet. Les autres : ni outil, ni réseau, ni texte dans ce cadre. Détail : `classify-<os>.json` | VM run 2 |
| Nouvelle version | 0.163.0-alpha.11 : 176 fonctions (+9), 3 nouvelles actives par défaut (`api_key_cyber_access_programs`, `cli_daybreak`, `code_mode_interrupt`). Mêmes 5 fonctions à outils, verrouillage identique | VM run 2 |
| Hooks Windows | Cause de l'échec du spike : la commande passe par le shell de session, `pwsh.exe -NoProfile -Command "<cmd>"` (`powershell.exe` 5.1 sans pwsh), pas `cmd /C`. La forme `"python.exe" "hook.py"` est une erreur PowerShell : ne tourne jamais. **Tournent** (0.162.1 et alpha.11, pwsh 7 et PowerShell 5.1, SessionStart/UserPromptSubmit/Stop) : sans guillemets, `python.exe` par le PATH, `& "python.exe" "hook.py" …` (aussi avec espaces dans les deux chemins). `commandWindows` et `command_windows` acceptés. `additionalContext` UTF-8 intact. Approbation par empreinte précalculée : marche (clé `D:\…\hooks.json:<event>:<g>:<h>`). `RUST_LOG` : hérité par les hooks, aucune trace utile sur stderr | VM runs 1-3 ; code `core/src/session/mod.rs` l.5141-5148, `core/src/shell.rs` l.24-48 |
| Hooks Unix, shell | Shell de connexion de `passwd`, **`-c` non login** (pas `$SHELL -lc`, qui n'est que le repli sans shell de session) ; le shell s'efface, le parent du hook est `codex`. Bruit sur stdout (`.bash_profile`, `.profile`, `.zprofile`, `.bashrc`, `.zshrc`, `BASH_ENV` sourcé, `.zshenv` avec zsh en shell utilisateur macOS) et même une ligne écrite par le hook avant son JSON : contexte reçu intact | VM runs 1-2 ; code idem |
| Hooks en interactif | TUI (pty) Linux et macOS, auth par clé factice : SessionStart (`source: startup`), UserPromptSubmit (`prompt`, `turn_id`), Stop (`last_assistant_message`, `stop_hook_active`) tournent avec l'empreinte ; contexte injecté ; `permission_mode` `default` (exec : `bypassPermissions`) ; rollout `session_meta.source = "cli"`, `originator = "codex-tui"` (exec : `"exec"`) | VM runs 2-3 |
| Format des sessions | Pas d'événement `user_message`, ni en exec ni en TUI : messages = `response_item` `message` (developer/user/assistant) + `event_msg/item_completed` (`UserMessage`, `AgentMessage`) ; `world_state` porte le texte d'`AGENTS.md` | VM, fixtures |
| `AGENTS.md` du coffre | Chargé depuis la racine git quand on lance Codex dans un sous-dossier (bloc `# AGENTS.md instructions for <cwd>` … `--- project-doc ---`) ; `CLAUDE.md` jamais lu. Sans git, seul le dossier courant compte. Le coffre est un dépôt git (dépôt : `install.sh` §10, `install.ps1` §7). `AGENTS.override.md` non vide remplace le global ; vide : global | VM |
| `config.toml` validé par `features list` | rc 1 : table dupliquée, type faux, syntaxe. rc 0 : clés inconnues, et **une clé posée après nos tables (elle tombe dans la table)** | VM |
| Règles `gbq` | `gbq query x` hors bac à sable ; `gbq query x; touch …`, `gbq sync`, `$(…)`, `\| touch` restent dans le bac à sable ; `gbq query a && gbq search b` hors bac à sable (les deux couverts). `codex execpolicy check` ne découpe pas `bash -lc` | VM macOS, Linux |
| Bac à sable Linux | Runner ubuntu 24.04.5 : `kernel.apparmor_restrict_unprivileged_userns=1` (défaut Ubuntu), pas de bwrap système. Le bac à sable ne démarre pas (« bwrap: loopback: Failed RTM_NEWADDR »), même avec le paquet `bubblewrap` (aucun profil AppArmor) : toute commande sandboxée échoue (fermé). Avec `=0` : lecture de tout le disque OK, écriture refusée, réseau coupé | VM run 1-2 |
| Connexion | `codex login status` : « Logged in using ChatGPT » sur **stderr** (stdout vide), 3 OS. Windows : `secret_auth_storage` actif par défaut ; avec `-c cli_auth_credentials_store="file"` et un `auth.json`, le statut est lu | VM |

### Non vérifié (et ce que ça change)
- **TUI en mode ChatGPT** : le TUI exige au démarrage une origine backend HTTPS (« workspace backend must use an HTTPS origin ») ; impossible contre un faux serveur local. Hooks en interactif vus avec une clé factice (même moteur de hooks). → `vrai-codex` ; les tests de nuit du TUI passent par une clé factice (test seulement).
- **TUI Windows** : pas de pilote ConPTY dans la sonde. Hooks Windows vus en `exec` seulement. → `vrai-codex` ou phase 7 (même moteur, risque faible).
- **MDM macOS réel** : demande un profil de configuration. → le scan lit `com.openai.codex` (valeurs forcées) d'après le code, test unitaire seulement.
- **`AGENTS.md` du coffre suivi par le modèle** : demande un vrai modèle. → `vrai-codex`.
- **Linux hors runner GitHub** (bureau Ubuntu, Debian, Fedora) : non fait. Le runner a les réglages par défaut d'Ubuntu 24.04.
- **`auth.json` réel sous Windows** : `secret_auth_storage` est actif par défaut ; une vraie connexion peut ne pas écrire `auth.json`. → `vrai-codex` avant la phase 7 ; d'ici là le contrôle « abonnement » de Windows ne se fie pas à la présence d'`auth.json`.
- **Outils réels du MCP hébergé (`apps`)** : appelé (initialize, tools/list) mais l'outil du faux serveur n'est pas annoncé (métadonnées de connecteur attendues). `apps` reste coupé : aucun effet sur le plan.
- `requirements.toml` `mcp_servers` (liste blanche) : non testé ; refusé par le scan comme prévu.

### Corrections au plan (prime sur le corps)
1. **§2.2 « Aucun outil »** : la liste « doit exister et valoir false » exclut `unified_exec` (forcé, sans outil sans `shell_tool`) et les fonctions au stade `removed`. Liste classée de départ pour `engine/codex-features.json` : `shell_tool`, `view_image`, `image_generation`, `goals`, `sleep_tool`, `code_mode_host`, `apps`, `plugins`, `remote_plugin`, plus celles du spike (`multi_agent`, `multi_agent_v2`, `memories`, `skill_search`, `tool_suggest`, `browser_use`, `computer_use`, `in_app_browser`, `code_mode`, `hooks`). Le premier `error` « Code Mode is unavailable » est toléré.
2. **§2.2 « Aucun MCP » + précontrôle** : ne plus exiger « `mcp list` vide » (refuserait tout utilisateur qui a un MCP perso). À la place : `codex mcp list --json` lancé avec nos `-c` ; pour chaque nom, `-c mcp_servers.<nom>.enabled=false` ; relecture : tous `enabled:false`, sinon refus. Ajouter `-c developer_instructions=""`.
3. **§2.2 « Pas de détournement »** : `chatgpt_base_url="https://chatgpt.com/backend-api/"` (confirmé). La config système (`config.toml`) n'est pas refusée : elle est neutralisée (point 2). Scan refusant : `requirements.toml` (Unix `/etc/codex/`, Windows `%ProgramData%\OpenAI\Codex\`), `managed_config.toml` (Unix `/etc/codex/` ; ignoré sous Windows), MDM macOS `com.openai.codex` (`config_toml_base64`, `requirements_toml_base64`, forcées). Liste refusée : celle du plan + `additional_developer_instructions`. « Emplacements par OS non vérifiés » → vérifiés (sauf MDM). « `-c` ne surcharge pas les exigences » → **vérifié** (§2.3.7).
4. **§2.2 « Détection d'un outil »** : le détecteur est faisable (rollout complet, `codex delete --force` efface les lignes), mais le texte du prompt (le coffre) resterait en octets dans les fichiers SQLite de Codex. **À trancher par le mainteneur** ; proposé : `--ephemeral` sans détecteur (prévention vue sur 3 OS × 2 versions, aucune trace), écrit dans SECURITY.md.
5. **§2.2 prompt** : stdin seulement, toujours avec `-` ; jamais un argument en plus (stdin serait ajouté en `<stdin>`).
6. **§3.3 Windows** : `"commandWindows": "& \"<python.exe>\" \"<hook>\" --agent codex"` (appel PowerShell, guillemets sur les deux chemins). La forme « `"<python.exe>" "<hook>"` » ne tourne jamais. **§3.3 Unix** : « `$SHELL -lc` » → shell de `passwd` en `-c` ; le « profil bavard » ne casse pas la sortie (vérifié). Test de `test-hooks.sh` : la commande Windows posée commence par `& `.
7. **§3.3 validation de `config.toml`** : `features list` attrape doublons, types, syntaxe, pas une clé mal placée → la règle « tables neuves en fin de fichier seulement » reste la protection.
8. **§3.4** : la garde « sans outils » peut aussi lire `session_meta.source` (`"exec"` = nos appels et l'exec de l'utilisateur, `"cli"` = TUI). Interactif vérifié (Linux, macOS).
9. **§3.6** : supprimer « `user_message` préféré » : lire les `response_item` `message` de rôle user/assistant (filtrer `# AGENTS.md instructions for` et `<environment_context>`), ignorer `world_state`.
10. **§3.7 / doc phase 12** : sur Ubuntu ≥ 23.10 par défaut, le bac à sable de Codex ne démarre pas : en interactif, toute commande sandboxée échoue (les règles `allow` de `gbq` passent, elles sortent du bac à sable). Les tâches sans outils ne sont pas touchées. À dire à l'utilisateur Linux (sans lui faire changer `apparmor_restrict_unprivileged_userns`, réglage système hors du kit).
11. **§5.2 / §5.3 faux serveur** : répondre 426 au GET websocket (sinon 5 reconnexions, ~7 s), lire les corps sans compression (`features.enable_request_compression=false` en test) ou décoder zstd, servir `wham/accounts/check` pour le TUI. Point de départ : la sonde du commit `0c90cc1`.
12. **§6** : phase 2 débloquée (verrouillage vu en mode ChatGPT, 3 OS). Phase 7 débloquée (hooks Windows vus tourner) ; le « mode dégradé annoncé » n'est plus nécessaire.
13. **Décision 1 (« on update aussi »)** : confirmée nécessaire. À la version suivante (0.163.0-alpha.11), 3 nouvelles fonctions actives par défaut : la garde stricte mettrait Codex en pause jusqu'à la mise à jour de la liste. Une clé `-c features.<x>` inconnue est ignorée sans erreur : le contrôle d'existence dans `features list` est indispensable.
14. **§2.2 « Abonnement seulement » / §4.3** : `codex login status` écrit son statut sur **stderr** (stdout vide). Comparer les préfixes fixes à la première ligne de stderr, sans jamais la recopier ni l'enregistrer.

---

## Étape 1 — fait et écarts (2026-10-15)

**Fait** (branche `feat/codex`, PR brouillon) : `engine/hooks/_agents.py` (lecture, validation, choix, écriture, porte par OS : `CODEX_OUVERT` vide), `--agents`/`--prefere` (install.sh), `-Agents`/`-Prefere` (install.ps1), `BIAB_AGENTS`/`BIAB_PREFERE` (les deux), étapes Claude conditionnées (vérification et fusion de `settings.json`, `CLAUDE.md`, skills, avertissement « install Claude Code first », installation de Claude Code, gstack, messages de fin). Tests : `engine/tests/test_agents.py`, `test-hooks.sh` §16 (install.sh entier contre des doublures, avec une copie du kit où Codex est ouvert), `test.yml` (Linux et Windows : pas d'`agents.json` sans choix, Codex refusé avant tout changement, `BIAB_AGENTS` par `irm | iex`). Comparaison master / branche sans choix (macOS, Ubuntu 24.04) : mêmes fichiers octet pour octet, mêmes appels, même sortie, sauf le module lui-même (`_agents.py` copié avec les hooks, sa ligne « ✓ _agents.py » et son entrée dans `installed.json`).

**Écarts au corps du plan (priment sur lui) :**
1. **§1.2 « Sans option ni variable »** : `agents.json` n'est écrit que pour un choix (option, variable, plus tard la réponse à la question). Sans choix, rien n'est écrit : « absent » veut dire « jamais choisi », et la question pourra être posée aux installations existantes quand Codex ouvrira.
2. **§1.2 question au terminal** : reportée aux phases 6 (Mac, Linux) et 7 (Windows). Tant que Codex est fermé, elle n'a qu'une réponse.
3. **§1.1 « Illisible ou invalide »** : vrai pour les lecteurs (`lire()` : Claude seul, et la raison : `illisible`, `invalide`, `ferme`). L'installeur, lui, s'arrête sans rien toucher quand aucun choix n'est donné (comme pour `settings.json`) ; avec un choix, il remplace le fichier et garde l'ancien en `agents.json.bak-<date>` (0600).
4. **§1.1 porte par OS** : appliquée aussi à la lecture. Un `agents.json` qui liste Codex sur un système fermé n'est pas fiable (`ferme`) : Claude seul pour les lecteurs, refus pour l'installeur.
5. **Moment de l'écriture** : le choix est validé et noté au préflight, avant toute installation. Une relance sans option après un échec garde le choix (les messages disent « re-run ./install.sh » sans option).
6. **Ajout** : `engine/hooks/_agents.py` dans CODEOWNERS et dans « Never merge these yourself » (`docs/terrain-agent.md`) : ouvrir un système à Codex revient au mainteneur.
7. **Chemin Codex seul** : en phase 1, les étapes Claude sont seulement sautées. Les messages propres à Codex (onboarding `codex`, titres) viennent avec les phases 6 et 7. Le titre « Claude Code + Obsidian » d'install.ps1 reste tel quel : `steps.rs` en tient la liste, vérifiée par un test Rust. `--with-gstack` sans Claude : sauté, avec un avertissement.
8. **Sortie de `_agents.py choisir`** : une ligne ASCII (`agents=… prefere=… source=… ecrit=…` ou `erreur=<code>`, code 2). Chaque installeur écrit le message dans sa langue (install.ps1 reste ASCII). install.ps1 refuse avant Python tout `-Agents`/`-Prefere` qui contient autre chose que des lettres, des virgules et des espaces (Windows PowerShell 5.1 coupe un argument qui contient un guillemet).
9. **README** : pas touché (phase 12). `--agents` n'accepte que `claude` tant que Codex est fermé.
10. **Coût** : `test-hooks.sh` §16 prend environ 15 s, y compris quand install.sh lance `test-hooks.sh` en fin d'installation.

---

## Étape 1 — relecture et corrections (2026-10-15)

Neuf constats de relecture (sécurité, non-régression), tous vérifiés. Ces corrections priment sur le corps du plan et sur « Étape 1 — fait et écarts ».

1. **§1.1 « Illisible ou invalide » (et écarts 3, 4)** : un `agents.json` non fiable ne donne **aucun agent**, plus Claude. Qui a choisi Codex seul a exclu Claude : un fichier abîmé, d'une autre version ou fermé sur ce système ne doit pas renvoyer ses sessions et son coffre chez Claude. `lire()` rend `AUCUN` (liste vide) avec la raison : `illisible`, `invalide`, `ferme`, `droits`. Les lecteurs (phases 2 à 6) mettent alors les tâches sans outils en pause, les hooks `--agent codex` se taisent, le contrôle de nuit passe à l'orange (« relance l'installation »). Seul un fichier **absent** veut dire Claude seul, comme aujourd'hui.
2. **Lecture et écriture d'`agents.json`** : `lire()` n'ouvre qu'un fichier ordinaire, sans suivre de lien ni attendre sur un tube (`O_NOFOLLOW`, `O_NONBLOCK`, `fstat`) ; un JSON trop imbriqué est `illisible` (plus d'exception). POSIX : un fichier qui n'est ni à l'utilisateur ni à root, ou modifiable par le groupe ou les autres, est `droits` (sinon un autre compte local pourrait rediriger `codex_home`). Windows : pas de contrôle de propriétaire (risque restant). `ecrire()` met de côté ce qu'il remplace sans jamais lire à travers un lien : les octets d'un fichier ordinaire, la cible d'un lien en texte, rien pour un tube.
3. **Captures de l'étape 0** : versées dans le dépôt, `engine/tests/fixtures/codex/phase0/` (par système et version : rollouts, payloads de hooks, `features list`, classement ; par run et système : `facts/run<n>-<id>/<os>/results.json`, source des lignes FACT, et `lock-request.json`). Octets gardés tels quels (`-text`), jetons factices seulement, scan de secrets. `test_codex_phase0.py` échoue si une capture manque ou si un fait du plan change (aucun appel forcé n'a tourné ; `request_user_input_async` accepté en 0.162.1 seulement). Les artefacts des runs expirent le 2026-10-20, leurs journaux 90 jours après.
4. **Écart 5, Windows** : le choix passe après Python (et, en mode `irm | iex`, après les sources), que `_agents.py` exige ; il passe maintenant **avant** Git et le journal d'installation. « Refusé avant tout changement » veut donc dire, sous Windows : Python (et les sources) peuvent avoir été installés, rien d'autre. Le contrôle CI compare aussi `install.log`.
5. **Écart 1 et §4.1, installations existantes** : sur une installation existante (`installed.json` présent) sans `agents.json`, **aucune question**, Claude seul, sauf option ou variable explicite ; Bibo y présélectionne Claude seul (pas de présélection d'après un Codex installé). §1.4 tient ainsi pour un utilisateur Claude qui a aussi Codex. Phase 6 : test pty (`.github/ci/terminal.py`) d'une relance Claude seul dans un terminal, identique à aujourd'hui.
6. **Écart 10, coût** : `install.sh` lance `test-hooks.sh` avec `BIAB_FROM_INSTALL=1` et le §16 se saute alors (13 s de moins pour chaque utilisateur) ; CI et `./test-hooks.sh` le gardent. Banc non isolé : le §16 s'arrête au lieu de lancer des installations.
7. **CI Windows** : l'étape `irm | iex` tourne de nouveau sans choix (la commande du README), et les étapes suivantes sans `agents.json` ; `BIAB_AGENTS=claude` par `irm | iex` devient la dernière étape du job.
8. **Fait de l'étape 0, précisé** : 11 appels forcés, tous refusés **sauf** `request_user_input_async` (accepté en 0.162.1, sans accès, run 3 ; refusé en 0.163.0-alpha.11). Le tableau des faits le disait déjà ; le premier compte rendu disait « tous refusés ».

---

## Étape 2 — fait et écarts (2026-10-16)

**Fait** (branche `feat/codex-headless`, PR brouillon, non branché : la phase 3 l'appelle) : `engine/hooks/_headless_codex.py` (même interface que `_headless_claude.py`, dont il reprend `supervise`, `_kill_tree`, `HeadlessError`), `engine/codex-features.json`, `engine/tests/fake_codex.py` + `test_headless_codex.py` (unitaires, 3 OS), `engine/tests/mock_openai.py` (faux serveur tiré de la sonde de l'étape 0) + `codex_mock.py` (le lanceur avec le **vrai** Codex 0.162.1, mode ChatGPT, job `codex-mock` de test.yml sur les 3 OS, sans secret). `_headless_codex.py` dans CODEOWNERS et « Never merge » (`docs/terrain-agent.md`) ; `codex-features.json` hors des deux. Claude seul : aucun fichier de son chemin touché (`_headless_claude.py`, réflexion, relecture inchangés) ; les installeurs copient `_headless_codex.py` avec les hooks (boucle `engine/hooks/*.py`), inerte tant que rien ne l'appelle.

**Contrat tenu** : `codex exec --json --ephemeral --skip-git-repo-check --ignore-user-config --ignore-rules -s read-only -C <cage>/travail --output-schema … -o … -c … -` ; prompt par stdin seulement ; environnement en liste blanche (`CODEX_HOME` d'`agents.json`, `BRAIN_HEADLESS=1`, jamais `OPENAI_API_KEY`/`CODEX_API_KEY`/`CODEX_ACCESS_TOKEN`/`OPENAI_BASE_URL`/`RUST_LOG`) ; contrôles d'avant dans cet ordre : version ≥ 0.162.1 et options de `exec --help`, `.env`, couches gérées, `auth.json`, `login status`, `features list`, MCP ; flux : `thread.started` d'abord, un seul tour, items `agent_message`/`reasoning`/`error` seulement, fichier `-o` = dernier message, objet JSON.

**Écarts au plan (priment sur lui) :**
1. **Liste des fonctions (décision 1)** : `codex-features.json` = les 55 fonctions actives par défaut dans les 6 captures de l'étape 0 (0.162.1 et 0.163.0-alpha.11, 3 OS ; stade `removed` exclu), plus les 19 de la correction 1. La garde stricte l'exige : toute fonction encore active après nos `-c`, hors `unified_exec` (forcée) et stade `removed`, met Codex en pause — donc les fonctions inoffensives actives par défaut sont coupées aussi (« tout couper » répond sur 3 OS × 2 versions, étape 0 `LALL`). Les 3 actives de l'alpha et `secret_auth_storage` (Windows) y sont déjà : 0.163 ne mettra pas Codex en pause. **Existence** exigée pour les 19 seulement (`CORE`, dans le lanceur, donc sous CODEOWNERS) ; un autre nom absent de `features list` est toléré : il ne peut rien allumer, et une fonction renommée réapparaît active, donc inconnue, donc en pause. Conséquence : retirer un nom du fichier ne desserre jamais la garde (il redevient actif → pause) ; seul le lanceur peut la desserrer.
2. **MCP (corrige la correction 2 de l'étape 0)** : couper par nom un serveur du `config.toml` de l'utilisateur **casse l'appel** : avec `--ignore-user-config`, `-c mcp_servers.<nom>.enabled=false` devient un serveur sans commande, refusé (« invalid transport », code `config/src/mcp_types.rs` l.543, erreur propagée par son `Deserialize` l.597-605 : toute la config de l'appel est refusée). Donc : `codex mcp list --json` lancé avec un `CODEX_HOME` **vide** (dans la cage : les couches que l'appel lit, système et projet) → ces noms seuls sont coupés par `-c`, puis relus `enabled:false` ; relu ensuite avec le vrai `CODEX_HOME`, tout serveur encore actif doit être déclaré dans le `config.toml` de l'utilisateur (que l'appel ignore), sinon pause (couche inconnue, p. ex. config cloud d'entreprise). Nom hors `[A-Za-z0-9_-]` (non adressable par `-c`) : pause.
3. **Ajout `-c notify=[]`** : un `notify` du `config.toml` système tourne après chaque tour, avec la session (le coffre) en argument, **sans** passer par `features.hooks` (code `hooks/src/registry.rs` l.124-126, `core/src/session/mod.rs` l.5158-5159). Vérifié en CI avec témoin (sans le lanceur, il tourne).
4. **Couches gérées** : plus strict que le plan : **tout réglage** dans `requirements.toml` ou `managed_config.toml` (`/etc/codex/`, `%ProgramData%\OpenAI\Codex\`) ou un profil MDM (`config_toml_base64`, `requirements_toml_base64`) met Codex en pause, pas seulement la liste de clés : `requirements.toml` borne aussi bac à sable et approbation, `managed_config.toml` passe avant **tous** nos `-c`. Commentaires seuls : accepté.
5. **`.env`** : noms seulement ; refus de tout nom hors de la liste blanche d'environnement, ou `RUST_LOG`, `OPENAI_*`, `SSL_*`, `*_PROXY`, contenant `CA` ; `CODEX_*` ignorés (Codex les ignore, `arg0/src/lib.rs` l.317) ; une ligne illisible (sans `=`, BOM, valeur sur plusieurs lignes) = refus.
6. **Connexion** : `cli_auth_credentials_store="file"` partout, `auth.json` exigé partout (y compris Windows : fermé, sûr ; une vraie connexion Windows peut vivre dans le trousseau → à revoir en phase 7 avec `vrai-codex`). `login status` : première ligne de stderr égale à `Logged in using ChatGPT` (préfixes « facturés » connus → `billed`, le reste → `logged_out`), jamais recopiée.
7. **Schéma** : Codex l'envoie en `strict: true` (requête capturée à l'étape 0) : le lanceur refuse (`ValueError`, avant tout appel) un objet sans `additionalProperties: false` ou dont une propriété n'est pas requise. Phase 3 : vérifier avec `vrai-codex` que `maxLength`/`maxItems` des schémas de la réflexion et du juge passent en mode strict (**non vérifié**).
8. **Pas de détecteur** (proposition de l'étape 0, en attendant le mainteneur) : `--ephemeral`, donc pas de test « rollout avec `function_call` » (§5.1). Voir la section suivante pour une option sans trace.
9. **Réponse** : `Answer(output, version, cost_usd=None, num_turns, tokens)` (`tokens` = `turn.completed.usage`).
10. **CI** : `codex-mock` tourne sur push vers master et, en PR, quand le lanceur, sa liste, le faux serveur ou test.yml changent (modèle du job `app`). Il vérifie : réponse de bout en bout, outils annoncés ⊆ ceux de l'étape 0, URL fixées, jeton ChatGPT seul (clés du parent jamais vues), schéma strict, rien du prompt dans `CODEX_HOME`, 5 appels forcés refusés, fonction retirée → pause, serveur MCP de l'utilisateur jamais démarré (témoin : sans le lanceur, il démarre), `config.toml` système (MCP, `notify`, `developer_instructions`) neutralisé (témoin), `requirements.toml` → pause, connexion par clé API → refus.

## Détecteur : option dossier jetable (2026-10-16)

Question : peut-on mettre les sessions **et** les bases SQLite de Codex dans un dossier jetable sans déplacer ni copier `auth.json` (qui rendrait un détecteur a posteriori possible sans trace) ? Lu dans le code de Codex 0.162.1 (tag `rust-v0.162.1`, commit 092d3acd), **non vérifié en VM**.

- **Bases SQLite : oui.** Les 7 bases (`state_5`, `logs_2`, `goals_1`, `memories_1`, `memories_v2_1`, `queue_1`, `thread_history_1`) vivent toutes sous `sqlite_home` (`state/src/sqlite.rs` l.34-39, l.61, l.109, l.134). Ordre : clé `sqlite_home` de la config (donc `-c sqlite_home="<cage>"`), sinon `CODEX_SQLITE_HOME`, sinon `CODEX_HOME` (`core/src/config/mod.rs` l.273, l.4119-4124, l.4417 ; `config/src/config_toml.rs` l.369-371 ; `state/src/lib.rs` l.133). Un `requirements.toml` peut l'imposer (`core/src/config/requirements.rs` l.35) : déjà refusé par le lanceur.
- **Sessions (rollouts) : non.** Le chemin est câblé `CODEX_HOME/sessions/AAAA/MM/JJ/` (`rollout/src/recorder.rs` l.1740-1749, `rollout/src/lib.rs` l.86), sans réglage ni variable. Les déplacer = changer `CODEX_HOME`, où Codex lit `auth.json` (`login/src/auth/storage.rs` l.162-163). Un lien vers le vrai `auth.json` n'est **pas sûr** : Codex le réécrit en place aujourd'hui (`storage.rs` l.214-222, troncature, le lien serait suivi), mais une écriture atomique future remplacerait le lien dans le dossier jetable et le jeton rafraîchi serait perdu (`refresh_token_reused`, connexion cassée sans bruit) ; sous Windows un lien demande des droits ; le stockage trousseau est indexé par le chemin de `CODEX_HOME` (`storage.rs` l.246).
- **Autre voie trouvée : `CODEX_ROLLOUT_TRACE_ROOT`.** Variable qui enregistre une trace de la session dans le dossier qu'on lui donne (`rollout-trace/src/thread.rs` l.44, l.106-114 ; fichiers `manifest.json`, `trace.jsonl`, `payloads/`, `bundle.rs` l.8-12), démarrée pour toute session racine, **éphémère comprise** (`core/src/session/session.rs` l.1258), avec les appels d'outils au moment de leur répartition (`thread.rs` l.339) et les requêtes/réponses du modèle (l.357). Combinée à `--ephemeral` (aucun rollout, aucune ligne de session) et `-c sqlite_home="<cage>"`, elle rendrait un détecteur a posteriori possible sans rien laisser dans `CODEX_HOME` (le prompt n'irait que dans la cage, supprimée). Préfixe `CODEX_` : le `.env` ne peut pas la poser (`arg0/src/lib.rs` l.317).
- **À vérifier avant de s'en servir (VM, faux serveur)** : (1) un appel refusé (« unsupported call », « code-mode host is disabled ») apparaît dans la trace (répartition) ou seulement dans les réponses du modèle (`payloads/`) ; (2) rien d'autre n'est écrit dans `CODEX_HOME` (hors `installation_id`) avec `-c sqlite_home` ; (3) l'échec de démarrage de la trace est **silencieux** (`thread.rs` l.114 : avertissement, trace coupée) : le détecteur devrait exiger le dossier de trace, sinon refus ; (4) format interne, sans garantie de stabilité : le lire comme un filet, jamais comme la garantie. Non implémenté (consigne) : à trancher avec la décision sur le détecteur.
