# brain-in-a-box — installation (Windows)

Cerveau d'équipe : un assistant Claude Code qui a déjà toute la mémoire chargée
(projets, décisions, style) + accès au **BrainCo** partagé + des automatisations
qui tournent chaque nuit. Un seul script fait tout : `install.ps1`.

L'installeur est **idempotent** (relançable sans risque) et **non-destructif**
sur tes données (vault, clés, init). Tu peux le rejouer quand tu veux.

---

## 1. Prérequis (à installer AVANT)

| Outil | Pourquoi | Où |
|-------|----------|-----|
| **Git for Windows** | clone gbrain + BrainCo | https://git-scm.com/download/win |
| **Python 3** | hooks (capture des corrections, reflection) | https://www.python.org/downloads/ — **PAS** le stub Microsoft Store |
| **Claude Code** (CLI `claude`) | l'assistant + la reflection de 23h | https://claude.com/claude-code |
| **bun** | runtime gbrain | auto-installé par le script si absent |
| **Clé ZeroEntropy** (gratuite) | embeddings (recherche sémantique) | https://dashboard.zeroentropy.dev — peut être différée |

> ⚠️ Python : si tu tapes `python` et que ça ouvre le Microsoft Store, c'est le
> stub. Installe la vraie version depuis python.org, coche **"Add to PATH"**.

> 🔑 Accès BrainCo : le repo `brain-company` est **privé**. Avant de lancer,
> assure-toi que ton compte GitHub a accès au repo et que `git` est authentifié
> (token ou SSH). Sinon le clone échoue et ton BrainCo sera vide.

---

## 2. Installation

```powershell
# 1. Cloner ce repo
git clone https://github.com/BrainInBox/brain-in-a-box.git
cd brain-in-a-box\windows

# 2. Lancer l'installeur EN MODE ÉQUIPE (récupère le BrainCo partagé)
powershell -ExecutionPolicy Bypass -File install.ps1 -Company https://github.com/BrainInBox/brain-company.git
```

> Remplace l'URL `-Company` par celle du BrainCo réel (demande-la à Mohamed si tu
> ne l'as pas). Sans `-Company`, tu n'auras que ton cerveau perso, **pas** le
> partagé.

Pendant l'install, on te demandera ta **clé ZeroEntropy** (`ze_...`). Colle-la,
ou appuie sur Entrée pour la mettre plus tard (voir §5).

---

## 3. Ce que l'installeur met en place

- **gbrain** cloné + dépendances (`~\DEV\gbrain`), commandes `gbq` / `gbrain` sur le PATH.
- **Vault perso** (`~\Documents\Brain`) — jamais écrasé si déjà présent.
- **BrainCo** (`~\Documents\BrainCo`) cloné, ajouté comme source gbrain et **fédéré** → interrogeable via `gbq query`.
- **Hooks Claude Code** (capture des corrections, logs/index/recap de session) enregistrés dans `~\.claude\settings.json`.
- **2 tâches planifiées** (Planificateur Windows), lancées via `wscript` (pas de fenêtre, survit à l'écran verrouillé) :

| Tâche | Quand | Quoi |
|-------|-------|------|
| `brain-gbrain-nightly` | **04:00** | self-update gbrain, migrations, ré-index + embed du vault |
| `brain-reflection` | **12:00 et 23:00** | synthèse / reflection quotidienne |

---

## 4. Vérifier que ça marche

```powershell
gbq query "test"            # la recherche répond
Get-ScheduledTask brain-*   # les 2 tâches sont là (State = Ready)
```

Puis l'**onboarding** (15 min, personnalise l'assistant) :

```powershell
cd $HOME\Documents\Brain
claude
# colle : "Lis onboarding/ONBOARDING.md et lance-le pour me personnaliser."
```

---

## 5. Si tu as différé la clé ZeroEntropy

```powershell
cd brain-in-a-box\windows
powershell -ExecutionPolicy Bypass -File set-ze-key.ps1
gbq embed --stale
```

---

## 6. Dépannage

- **`gbq` non reconnu** → ouvre un **nouveau** terminal (le PATH n'est mis à jour que pour les nouveaux shells).
- **BrainCo vide après l'install** → ton `git` n'avait pas accès au repo privé. Authentifie-toi, puis relance `install.ps1 -Company <url>` (idempotent).
- **`claude` introuvable** (warning à l'install) → installe Claude Code, puis relance l'installeur.
- **Les tâches ne se lancent pas** → `Get-ScheduledTask brain-* | Get-ScheduledTaskInfo` pour voir le dernier résultat.
- Pour tout relancer proprement : ré-exécute `install.ps1` — il ne touche pas à tes données.
