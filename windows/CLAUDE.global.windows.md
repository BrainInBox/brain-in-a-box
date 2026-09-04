<!-- brain-in-a-box -->
## Directives brain-in-a-box

> Installé par brain-in-a-box (Windows). Bloc ajouté à la fin de ton `~/.claude/CLAUDE.md` (jamais écrasé).

Boucle directrice : **rassembler le contexte → agir → vérifier → recommencer.**

### Règle n°1 ABSOLUE — Capturer les corrections (BLOQUANT)

S'applique dans CHAQUE repo, CHAQUE session. Le hook `~/.claude/hooks/brain/correction-detector.py` (UserPromptSubmit) détecte les signaux de correction (négation, « en fait », « plutôt », « évite », reproche, préférence) et injecte un rappel `⚠️ CORRECTION DETECTED`.

Quand tu le vois, AVANT toute autre chose :
1. **Ajouter** une ligne à `~/Documents/Brain/Profile/lessons.md` sous l'en-tête du jour (`## AAAA-MM-JJ`).
   Format : `- **[contexte court]** → règle : [quoi faire] (quand : [déclencheur])`
2. **Confirmer** visiblement : `✓ noté dans lessons.md`
3. **Ensuite** appliquer la correction et continuer.

Non négociable. Si tu ne peux pas écrire le fichier, dis-le explicitement.

### Avant de supposer qu'un outil manque
Lis `~/Documents/Brain/Profile/stack.md` — outils installés, mémoire GBrain (`gbq`), accès. Si c'est listé, utilise-le.

### Avant de diagnostiquer
Lis `~/Documents/Brain/Profile/memory.md` (contexte récent, décisions actives) et `Projects/<projet>.md`. Le contexte est déjà capturé — ne le redemande pas.

### Directives d'ingénierie
- **Supprimer avant de construire** : avant de refactorer un fichier >300 lignes, retirer le code mort. Commit séparé.
- **Exécution par phases** : jamais un refacto multi-fichiers en une seule réponse. Max ~5 fichiers/phase, vérifier entre chaque.
- **Planifier et construire sont séparés** : « fais un plan » = plan seulement, pas de code avant « go ».
- **Vérification forcée** : avant « fini », lancer type-checker + linter + tests. Si rien n'est configuré, le dire. Jamais « Fini ! » avec des erreurs ouvertes.
- **Sécurité d'abord** : injection, authz, secrets exposés, crypto faible, race conditions. Signaler toute vulnérabilité, même hors scope. Ne jamais committer/logger un secret (.env, *.pem, *.key, tokens).
- **Sécurité des actions destructrices** : ne jamais supprimer un fichier sans vérifier les références. Ne jamais pousser sur un repo partagé sans demande explicite.

### Mémoire (GBrain)
Pour rappeler des décisions/contexte/personnes : `gbq query "<question>"` (sémantique) au lieu de deviner. Cite le slug. Détails : `Profile/stack.md`.
