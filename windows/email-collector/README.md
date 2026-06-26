# Email collector (Windows) — recette gbrain email-to-brain

Collecteur Gmail déterministe : pull les mails (lecture seule, **métadonnées only**,
jamais le corps), filtre le bruit, génère les liens Gmail, déduplique. Sort un
digest markdown. L'enrichissement (écrire `[[clients/<slug>]]` dans BrainCo) est
une 2e étape (agent).

## Confidentialité
- Mails bruts → **local** (`~/.gbrain/email-collector/data/`), **jamais** dans BrainCo (partagé).
- Dans BrainCo ne vont que des **lignes timeline** (expéditeur, objet, lien) — pas le corps.
- Scope OAuth = `gmail.readonly`. On ne fait que lire.

## Installation (Option B — Google OAuth direct)
1. Crée les creds OAuth (Desktop app) sur https://console.cloud.google.com
   en étant connecté avec **le compte mail à connecter** (ex. mohamedfakhoury@citymood.io),
   API Gmail activée, scope `gmail.readonly`.
2. Enregistre-les (saisie masquée) :
   ```
   powershell -ExecutionPolicy Bypass -File set-google-oauth.ps1
   ```
3. Première autorisation (ouvre le navigateur) :
   ```
   bun email-collector.mjs auth
   ```
4. Collecte + digest :
   ```
   bun email-collector.mjs run
   ```

## Fichiers
- `set-google-oauth.ps1` — enregistre client_id/secret dans `~/.gbrain/email-collector/config.json`.
- `email-collector.mjs` — collecteur (commandes : `auth` | `collect` | `digest` | `run`).
- Données : `~/.gbrain/email-collector/data/{messages,digests}/` + `state.json`.

## Cron (à brancher)
Task Scheduler + lanceur wscript (comme les autres tâches brain), toutes les 30 min :
`bun email-collector.mjs run`. L'enrichissement BrainCo tourne ensuite (agent).
