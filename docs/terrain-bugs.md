# Journal des bugs de terrain

Chaque bug trouvé sur une VM (workflow `terrain`, `.github/workflows/terrain.yml`), ou en reproduisant
un échec de terrain, a ici une entrée, ajoutée par la PR qui le corrige. La règle : pas de correctif
sans savoir pourquoi les tests ne l'avaient pas vu, et pas de correctif sans le test qui l'aurait vu
(il échoue avant, passe après). Les catégories qui reviennent disent où écrire les prochains tests.

Jamais d'extrait de note, de valeur de secret ni de donnée d'un vrai cerveau ici : le dépôt est public.

Une entrée :

```
## AAAA-MM-JJ · OS · titre court
- Symptôme : ce qu'on a vu (scénario et étape du terrain, message).
- Cause : la vraie, vérifiée.
- Pourquoi les tests ne l'avaient pas vu :
- Correctif : PR #…
- Test de non-régression : fichier et nom du test.
- Catégorie à surveiller : encodage Windows · fins de ligne · horloge et planificateur · secrets ·
  suppression de notes · plusieurs nuits · montée de version · …
```

## 2026-10-14 · local, macOS · M1 n'archive pas le dernier bloc d'un memory.md qui finit par une ligne vide
- Symptôme : scénario `nuits`, étape `nuit-j20` : à J+20, la correction M1 finit en échec
  (« 1 ligne(s) perdue(s) »), le fichier est remis comme avant, alerte rouge « contrat » à chaque nuit,
  et les blocs de plus de 15 jours restent dans memory.md.
- Cause : quand les blocs à archiver vont jusqu'à la fin du fichier et que le fichier finit par une
  ligne vide, cette dernière ligne vide part avec le bloc mais n'arrive pas dans l'archive (`_append`
  retire les lignes vides de fin de bloc). Le contrat compte une ligne perdue et refuse, à raison.
  La première nuit ne le montre pas : la ligne vide posée sous le pointeur d'archive compense.
- Pourquoi les tests ne l'avaient pas vu : les tests de M1 font une seule nuit ; il faut un pointeur
  d'archive déjà en place et un dernier bloc à archiver (environ 15 jours sans nouvelle entrée).
- Correctif : `memory_blocks` laisse dans memory.md les lignes vides de fin d'un bloc qui va jusqu'à
  la fin du fichier (branche `feat/terrain`, PR des tests de terrain).
- Test de non-régression : `test_controle.CalibrationDecisions.test_m1_archives_the_last_block_on_a_later_night`
  (rouge avant le correctif : nuit à J+4 qui met les deux mois dans le pointeur, puis J+20) et `terrain.py nuits`.
  Il a fallu le mois de l'archive déjà dans le pointeur : sinon la réécriture du pointeur compense.
- Catégorie à surveiller : plusieurs nuits.

## 2026-10-14 · Windows · Les tests et la mise à jour de gbrain cassaient sur la console cp1252
- Symptôme : premier passage sur un runner Windows : `test_reflection.py` lisait et écrivait ses
  fichiers dans l'encodage local, et la sortie de `gbrain_update.py`, recopiée telle quelle dans le
  journal de nuit (UTF-8), y arrivait en cp1252 : accents illisibles.
- Cause : `open()`, `read_text()`, `write_text()` sans `encoding="utf-8"` prennent l'encodage local
  (cp1252) ; sous `python -I`, `PYTHONIOENCODING` est ignoré, donc stdout aussi.
- Pourquoi les tests ne l'avaient pas vu : la CI ne tournait que sur macOS, en UTF-8 partout.
- Correctif : #35 (fichiers des tests de la réflexion et sortie de `gbrain_update.py` en UTF-8) ;
  #37 pour les tests du contrôle (stdout et stderr reconfigurés en UTF-8).
- Test de non-régression : le job `windows` de `test.yml` lance `engine/tests` avec le Python que
  l'installeur a mis en place.
- Catégorie à surveiller : encodage Windows.

## 2026-10-14 · Windows · Un job de CI à cheval sur 04:00 voyait la vraie tâche de nuit
- Symptôme : l'étape « mise à jour de gbrain à blanc » échouait : `~/.gbrain/update-state.json` était
  apparu pendant le test, alors qu'une marche à blanc n'écrit rien.
- Cause : `install.ps1` enregistre `BrainNightly` à 04:00 heure du runner (UTC), avec rattrapage d'une
  nuit manquée ; le job passait 04:00, la vraie nuit a tourné en même temps et écrit l'état.
- Pourquoi les tests ne l'avaient pas vu : le test supposait le fichier absent ; rien ne tenait compte
  de l'horloge du runner ni de la tâche que le job venait d'enregistrer.
- Correctif : #37 (comparaison avant/après, empreinte et date, au lieu de « absent »).
- Test de non-régression : l'étape elle-même ; `terrain.yml` tourne à 01:17 UTC et désactive les tâches
  `Brain*` juste après chaque installation (le scénario `installation` lance la nuit lui-même, une fois).
- Catégorie à surveiller : horloge et planificateur.

## 2026-10-14 · macOS et Windows · Annuler une nuit remettait les secrets en clair
- Symptôme : après `controle annuler derniere-nuit`, un mot de passe masqué cette nuit-là revenait en
  clair dans sa note.
- Cause : annuler une nuit défaisait chacun de ses commits (`git revert`), les masquages S1 compris.
- Pourquoi les tests ne l'avaient pas vu : ils vérifiaient qu'annuler rend chaque fichier octet pour
  octet ; le retour du secret était donc l'attendu.
- Correctif : #37. Une nuit annulée laisse les secrets masqués ; un secret ne revient que par son
  identifiant, avec `--remettre-le-secret`.
- Test de non-régression : `test_controle.SecretsLifecycle.test_a_nights_undo_leaves_secrets_masked` ;
  job `windows` de `test.yml` (« undoing the night put a secret back ») ; scénario `nuits`, étape
  `secret-reste-masque` (la nuit 4 masque un mot de passe, puis elle est annulée).
- Catégorie à surveiller : secrets.

## 2026-10-14 · macOS et Windows · D1 et D2 supprimaient des notes qui avaient du contenu
- Symptôme : une note sans texte mais avec des propriétés ou un titre à elle comptait comme vide (D2) ;
  deux notes au même corps mais aux propriétés différentes comptaient comme copies (D1). Supprimées.
- Cause : seul le corps de la note était comparé, sans l'en-tête (frontmatter).
- Pourquoi les tests ne l'avaient pas vu : le coffre piégé n'avait que des notes vraiment vides et des
  copies exactes.
- Correctif : #37. D1 compare le fichier entier ; D2 garde une note qui a une propriété (dates à part)
  ou un titre autre que son nom.
- Test de non-régression : `test_controle.Deletions.test_notes_with_properties_or_a_title_are_not_empty`,
  `test_same_body_other_properties_is_not_a_copy`.
- Catégorie à surveiller : suppression de notes.

## 2026-10-14 · Windows · Des tests écrivaient en mode texte : \r\n au lieu de \n
- Symptôme : deux tests du contrôle échouaient sur Windows : la note qu'ils écrivaient avait des fins de
  ligne CRLF, et la comparaison octet pour octet ne tenait plus.
- Cause : `open(..., "w")` sans `newline=""` traduit `\n` en `\r\n` sous Windows.
- Pourquoi les tests ne l'avaient pas vu : ils ne tournaient que sur macOS.
- Correctif : #37 (`newline=""` partout où l'octet compte).
- Test de non-régression : le job `windows` de `test.yml` lance `engine/tests`.
- Catégorie à surveiller : fins de ligne.
