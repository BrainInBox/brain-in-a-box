Branche : `bbox/guide` (worktree `~/brain-in-a-box-worktrees/bbox-guide`, partie de `7ee92e6314`, recalée le 26/10 sur `425ac359c5`, le dernier commit de `~/bbox`)

# Reprise : le guide (session 1)

**TERMINÉ** le 26/10/2026 à 09h25 : les trois étapes sont prouvées à l'écran sur `bbox-blank101`, sur le build 6 (`f6bf842`, tout compris, rien posé à chaud), installation neuve, autorisations remises à zéro. La réunion avec les branchements n'est pas faite : `REPRISE-branchements.md` dit encore « EN COURS ». Branche à réunir : `bbox/guide` (HEAD `f6bf8425db`).

Mission : `~/brain-in-a-box-worktrees/MISSION-guide.md`. Trois étapes, dans l'ordre :

1. La souris Bbox (balise de position, conversion capture vers écran, vol du curseur avec une bulle, par-dessus les autres apps). Fini quand : sur un Mac neuf, « où je change mon fond d'écran ? » fait voler la souris jusqu'à la bonne ligne des Réglages, captures à l'appui.
2. La visite en cinq arrêts portée par la souris. Fini quand : après l'installation et les autorisations, la visite se déroule seule, puis Awa refait seule deux choses vues.
3. La souris aux clics délicats des autorisations : la bonne ligne et l'interrupteur.

## État

- Étape 1 : PROUVÉE (build 6). Awa tape « ou je change mon fond d'ecran » : Bbox ouvre Réglages, Fond d'écran, et la souris vole sur « Ajouter une photo… », bulle « Ajouter une photo ». Preuve `preuves/guide/build6-etape1-fond-ecran-la-souris-montre.png` (et `etape1-fond-ecran-la-souris-montre.png`, build 4 : sur une image, bulle « Choisis une image »).
- Étape 2 : PROUVÉE (build 5, la visite n'a pas changé au build 6). Juste après la dernière autorisation, Bbox dit « Je vais te faire faire un petit tour de Bbox » et la visite se déroule seule : 5 arrêts (« 1 sur 5 » à « 5 sur 5 » : Où on parle, Tes conversations, Ce que je sais faire, Ton téléphone, Ce que je fais tout seul), la souris posée sur chacun, toutes les 11 s, puis sur la zone d'écriture avec « Si tu ne sais plus où faire quelque chose, demande-moi : je te montre avec la souris. ». Ensuite (build 6), deux gestes vus pendant la visite : reprendre une conversation, ouvrir « Ce que je sais faire ». Preuves `etape2-visite-5-arrets-build5.png`, `etape2-visite-fin-demande-moi.png`, `build6-etape2-*`. Ce que la visite apprend vraiment à une personne, seul un essai avec quelqu'un de réel le dira.
- Étape 3 : PROUVÉE (build 6). Accessibilité : la souris sur la ligne « CuaDriver », bulle « La ligne CuaDriver », puis sur l'interrupteur, bulle « Allume cet interrupteur, puis tape le mot de passe du Mac ». Quand Awa dit « c'est fait » sans l'avoir fait, Bbox le voit, rouvre la page et remontre l'interrupteur. Enregistrement de l'écran : macOS demande, la ligne « Bbox f6bf842 » est là, la souris la montre puis l'interrupteur ; « Quitter et rouvrir » ramène Awa sur la même conversation. Accès complet au disque : même chose (build 5). Preuves `build6-etape3-*`, `build5-*`.
- Commits sur `bbox/guide` : `ea55989176` (étape 1), `8345fab525` (étape 2), `35c49e765c` (étape 3), `40359d4bdf` (la souris des Réglages dans la vraie app), `5a524ecc99` (la visite démarrait en ancien tour à 6 arrêts), `f6bf8425db` (page Enregistrement de l'écran vide après l'Accessibilité).
- vitest complet : 14 échecs, tous hors du guide (scripts de signature et de paquet qui veulent `HERMES_PYTHON`, `python3`, une liste de clés de traduction déjà en retard sur `425ac359c5`). À comparer avec `~/bbox` au moment de la réunion.

## Ce qui est fait, en bref

- Étape 1 : l'IA finit sa réponse par `[POINT:x,y:bulle]`, lu sur sa dernière capture de tout l'écran. Le moteur retire la balise (affichage, lecture à voix haute, même pendant l'écriture), la convertit en fractions de l'écran et envoie l'événement `bbox.point`. L'app fait voler un curseur bleu (`#0053fd`) dans une fenêtre transparente posée au-dessus de toutes les apps, qui ne prend aucun clic, avec une bulle d'une phrase ; il disparaît au bout de 12 s. Fichiers : `agent/bbox_pointer.py`, `electron/bbox-pointer-window.ts`.
- Étape 2 : quand les trois autorisations sont données, l'outil des autorisations dit à Bbox de lancer la visite (`gui_tour`, preset `bbox`). Cinq arrêts (où on parle, tes conversations, ce que Bbox sait faire, ton téléphone, ce que Bbox fait tout seul), une phrase et un exemple chacun, 11 s par arrêt (moins d'une minute en tout), la souris vole à chaque arrêt, puis revient sur le champ de saisie avec la phrase de fin de la fiche. Textes dans `apps/desktop/src/i18n/brand.ts` (`appTour.visit`).

- Étape 3 : quand Bbox demande une autorisation, l'outil ouvre lui-même la bonne page des Réglages. Une fois la réponse écrite, la souris vole sur la ligne à activer (« CuaDriver » dans Accessibilité, « Bbox » dans Enregistrement d'écran et Accès complet au disque), puis sur son interrupteur, et reste 30 s. La réponse ne dit jamais où cliquer. Fichiers : `agent/bbox_pointer.py` (`show_in_settings`, `take_pending`), `tools/bbox_permissions_tool.py` (`_show`, `MOUSE`).

## Décisions prises seul (datées, avec leur raison)

- 26/10 : branche recalée sur `425ac359c5` (le nouveau texte des autorisations de l'écran). Raison : l'étape 3 se pose sur ce texte, et la réunion sera plus simple.
- 26/10 : la souris des Réglages se place par la position de la fenêtre des Réglages (lue sans aucune autorisation) plus des écarts mesurés sur macOS 27, pas par une capture. Raison : au moment où on demande l'écran, Bbox ne voit pas encore l'écran ; seule la position des fenêtres se lit sans autorisation.
- 26/10 : la souris montre après la réponse, pas pendant. Raison : la personne lit la phrase, puis la souris lui montre où ; les deux en même temps se disputent son attention.
- 26/10 : pour l'écran, une seule ligne à la fois : d'abord « CuaDriver » dans Accessibilité, puis « Bbox » dans Enregistrement d'écran. Raison : macOS 27 range l'Enregistrement d'écran sous Bbox, pas sous CuaDriver ; dire à Awa de chercher CuaDriver là, c'est la perdre.

- 26/10 : balise `[POINT:x,y:bulle]` en fin de réponse, comme Clicky. Raison : la mission demande ce savoir-faire, et une balise en texte marche avec tous les modèles, sans outil en plus.
- 26/10 : la position voyage en fractions de l'écran (`fx`, `fy`), pas en pixels. Raison : l'app projette sur l'écran réel sans connaître l'échelle de la capture, ni la densité de l'écran.
- 26/10 : la capture de tout l'écran est plafonnée à 1200 px de large quand c'est Bbox (au lieu de 1456). Raison : au-delà d'environ 1,15 mégapixel, Anthropic réduit l'image avant que le modèle la voie, et ses coordonnées deviennent fausses ; 1200 px garde même un écran 4:3 en dessous.
- 26/10 : la balise est lue à la fin de la réponse, la dernière gagne, un point hors capture est ramené sur l'écran. Raison : une réponse peut citer plusieurs endroits, la souris n'en montre qu'un, le dernier est celui de l'étape suivante.
- 26/10 : la souris ne suit que la conversation ouverte. Raison : une réponse d'une autre conversation ne doit pas faire bouger la souris sous les yeux de la personne.
- 26/10 : la visite avance seule (11 s par arrêt), « Suivant » et « Retour » restent. Raison : la mission dit « se déroule seule » ; une phrase et un exemple se lisent en 11 s, cinq arrêts tiennent en une minute, loin des 5 minutes.
- 26/10 : la visite démarre quand l'outil des autorisations voit les trois accordées (et la consigne de l'outil la fait lancer aussi si la personne en a refusé une). Raison : la fiche place le tour juste après l'installation et les autorisations ; c'est le moteur qui sait quand c'est fini.
- 26/10 : le 5e arrêt montre « Tâches planifiées » dans la barre de gauche. Raison : c'est là que la personne retrouve et arrête ce que Bbox fait tout seul.
- 26/10 : le build se fait dans le worktree, avec le cache de `~/bbox` (`--cache`) et ses `node_modules` en liens. Raison : `~/bbox` reste à l'autre session et à la réunion ; 42 Go libres seulement.
- 26/10 : le dossier `~/brain-in-a-box-worktrees/.scratch` a disparu pendant la session (pas de mon fait). Mes outils et sorties vont dans `~/brain-in-a-box-worktrees/.guide`.
- 26/10 : la souris après une réponse passe par `bbox_pointer.after_reply`, commun aux deux moteurs de conversation. Raison : l'app de bureau tourne sur `gateway/`, pas sur `tui_gateway/` où la souris était branchée ; dans la vraie app, elle ne partait pas.
- 26/10 : la bulle parle la langue du Mac quand Hermès n'a que l'anglais. Raison : l'app de bureau suit la langue du Mac, la bulle doit parler comme la fenêtre.
- 26/10 : avant de montrer la ligne, Bbox rouvre la page demandée, 7 s après la demande. Raison, mesurée : la demande propre de CuaDriver bascule les Réglages sur Enregistrement de l'écran 3 à 5 s après son départ ; sans ça, la souris montrait la mauvaise page.
- 26/10 : la souris reste 3,5 s sur la ligne avant l'interrupteur. Raison : le vol dure jusqu'à 1,3 s ; à 1,8 s, la bulle de la ligne ne se lisait pas.
- 26/10 : `install.sh` relance aussi `universalAccessAuthWarn`. Raison : macOS n'inscrit une app dans Enregistrement de l'écran qu'une fois par vie de ce programme ; après nos remises à zéro, la liste restait vide. C'est un problème du Mac de test, pas d'Awa : un Mac neuf n'a jamais rien remis à zéro.
- 26/10 : outils de preuve `hotpatch.sh` (poser du Python à chaud), `film.sh`, `ask.sh` (`KEEP=1` pour rester dans la conversation), `vnc-restart.sh`, `caffeinate -d` sur la VM contre la veille. Une preuve finit toujours sur un vrai build.
- 26/10 : la visite démarrait en ancien tour à 6 arrêts. Raison : à l'arrivée de la demande dans l'app, une ligne ne connaissait que « quick » et « full ». Les tests unitaires ne passaient pas par là ; un test le couvre désormais (1 532 tests de l'app au vert, tsc et eslint OK).
- 26/10 : Bbox relance lui-même `universalAccessAuthWarn` avant de demander l'écran. Raison, lue dans le journal de macOS sur le build 5 : ce programme, qui inscrit les apps sur ces pages, venait d'inscrire CuaDriver pour l'Accessibilité et a ignoré la demande de l'écran ; la page restait vide. Arrive à tout Mac, neuf compris, dès que l'Accessibilité passe avant l'écran.
- 26/10 : tests Python de la passerelle : 6 échecs (`test_local_automation`, `test_readiness`, `test_session_local_route`) existent aussi sans mes changements, 2 autres ne ratent qu'en lot. Pas de mon fait, à revoir à la réunion avec les 14 échecs vitest.

## Constats en route (hors mission, pour la suite)

- Quand Awa dit « c'est fait » alors que l'interrupteur est éteint, Bbox a d'abord essayé de vérifier par un script, ce qui a fait poser à macOS une question en plus (« Bbox demande l'autorisation de contrôler System Events »), puis il a bien rouvert la page et remontré l'interrupteur. Le script est de trop : vérifier avec `mac_permissions` suffit.
- Taper « / » dans la zone d'écriture ouvre une liste de compétences en anglais (« /apple-reminders », « /computer-use »…) : du jargon sous les yeux d'Awa.
- La souris a montré le micro à 20 pixels près, sur le bouton voisin : la position des boutons du bas mérite d'être prise sur la page plutôt que devinée sur la capture.
- Le micro : au premier essai (build 4), Bbox décrivait le bouton avec des mots ; au build 5, il l'a montré avec la souris. À surveiller, pas encore une règle.
- Sous une réponse, l'app affiche « Self-improvement review · Skill 'mac-file-tidying' created » : du jargon en anglais sous les yeux d'Awa.
- La toute première capture de l'écran fait poser à macOS une question de plus (CuaDriver veut « contourner le sélecteur de fenêtre privée »). La souris de Bbox passe sous cette fenêtre. Bbox devrait l'annoncer et montrer « Autoriser ».

## Prochaine action

Rien pour le guide. À la réunion (quand `REPRISE-branchements.md` dira « TERMINÉ ») : fusionner `bbox/guide` et la branche des branchements, tests au vert (revoir les 14 échecs vitest et les 6 échecs Python de la passerelle, tous antérieurs), un seul build, puis toute la première heure sur un Mac neuf. Outils : `~/brain-in-a-box-worktrees/.guide` (`build.sh`, `install.sh`, `ask.sh`, `film.sh`, `vnc.sh` ; au clavier VNC, ni accents ni Cmd ; après « Quitter et rouvrir », Bbox n'a plus le port de pilotage, on continue au clavier).
