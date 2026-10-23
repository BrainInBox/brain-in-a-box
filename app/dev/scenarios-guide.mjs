// The guide's scenarios (guide-mock.html), for app/dev/screens.mjs: each runs the guide's window to its
// &go= screen, and screens-guide.txt holds what it shows. The mock's own checks end a run elsewhere (the
// gestures past &gestes=, a word of the system's on screen), and screens.mjs fails it.

// The guide in paste mode: a whole session to its end and the procedure saved, on each system, then each
// step's variant, each error of §2 and each state that keeps Claude from being asked.
export const GUIDE_SCENARIOS = [
  'g=ok&vu=0&go=enregistre',
  'g=ok&go=enregistre&os=windows',
  'g=ok&go=enregistre&os=linux',
  'g=ok&go=apercu&sent=1',
  'g=ok&go=apercu&nth=2&click=cacher&cache=1',
  'g=ok&go=apercu&nth=3&click=cacher,autre&cache=1&taille=1',
  'g=ok&go=apercu&depose=1',
  'g=ok&go=apercu&nth=2&pause=1&click=arreter',
  'g=ok&go=objectif&nth=2&click=comment',
  'g=ok&go=abandon&click=bloque,next,bloque,next,bloque',
  'g=pointille&go=etape',
  'g=bas&go=etape',
  'g=ambigu&go=etape&nth=2',
  'g=pas-ici&go=etape',
  'g=bloque&go=etape',
  'g=pas-fait&go=etape&nth=2',
  'g=pas-fait&go=etape&nth=2&click=reessaie',
  'g=sensible&go=etape&nth=4',
  'g=suspect&go=bloque',
  'g=clickfix&go=bloque',
  'g=refus&go=apercu&nth=2&cache=1',
  'g=quota&go=erreur',
  'g=presque-limite&go=etape',
  'g=hors-ligne&go=etape',
  'g=delai&go=etape',
  'g=init&go=erreur',
  'g=image-non-vue&go=etape',
  'g=noire&go=apercu',
  'g=petit&go=apercu&os=windows',
  'g=budget&go=erreur',
  'g=budget-etape&go=abandon',
  'g=colle&go=apercu',
  'g=deja&go=coller',
  'g=remote&go=enregistre',
  'g=refusee&go=enregistre',
  'g=auth&go=objectif',
  'g=billed&go=facture',
  'g=vieux&go=vieux',
  'g=arrete&go=arrete',
  'g=installer&go=installer',
  // The review's (a beginner's stops): the capture of before pasted again, the filter alone, the limit
  // today or tomorrow, a client's name in a step to save, back from the first capture, the reduced
  // mode kept from a step to the next and its note, no image in what was pasted, a JPEG dropped, a file
  // dropped on a step (« C'est fait » with it, as a paste: point 20) or on a question (nothing waits for it).
  'g=ok&go=apercu&nth=2&meme=1',
  'g=filtre&go=bloque',
  'g=quota&go=erreur&reprise=7200',
  'g=quota&go=erreur&reprise=86400',
  'g=ambigu&go=enregistrer',
  'g=ok&go=objectif&nth=2&click=back',
  'g=pointille&go=etape&click=reduire',
  'g=ok&go=etape&nth=2&click=reduire',
  'g=ok&go=apercu&vide=1',
  'g=ok&go=apercu&vide=1&os=windows',
  'g=ok&go=erreur&depose=jpeg',
  'g=ok&go=etape&nth=2&lache=1',
  'g=ambigu&go=etape&nth=2&lache=question',
  // Phase 5, decision 8 (Claude Pro): no Opus for the account, Sonnet in the cautious mode; neither model.
  'g=prudent&go=etape',
  'g=prudent&go=etape&nth=2',
  'g=sans-modele&go=erreur',
  // Linux: WebKitGTK hands the page no image of the clipboard; Rust reads it (phase 5's terrain found it).
  'g=ok&go=apercu&os=linux&gtk=1',
  'g=ok&go=coller&nth=2&os=linux&gtk=vide',
  // Linux: a Ctrl+V of text on the preview or « Cacher une zone » leaves the user there, as on the other systems.
  'g=ok&go=apercu&os=linux&gtk=1&recolle=apercu',
  'g=ok&go=cacher&click=cacher&os=linux&gtk=1&recolle=cacher',
  // The automatic capture (capture/, phases 2a and 2b), one gesture budget: from « C'est fait » to the next
  // step, 2 gestures (« C'est fait », « Envoyer à Claude »), where pasting costs 6 (guide-mock.html counts them).
  // Windows: a click in the window, « C'est bien celle-ci ? », the program filled in, each step photographed.
  'g=ok&cap=clic&os=windows&vu=0&go=enregistre&gestes=2',
  // A program of its own (an ERP), not a site: its name from the program; the tabs' warning is a site's.
  'g=ok&cap=clic&os=windows&clic=sage&go=etape&gestes=2',
  // macOS 14 and later: the system's picker; macOS 14 gives no name (the field stays empty).
  'g=ok&cap=selecteur&go=enregistre&gestes=2',
  'g=ok&cap=selecteur&selecteur=vide&go=objectif',
  // Without the mouse (critique 11): « Choisir dans une liste », a minimized window and a terminal not chosen.
  'g=ok&cap=clic&os=windows&go=objectif&click=liste,fenetre-1',
  // No click in 45 s, counted down: then « Réessayer » or the list.
  'g=ok&cap=clic&os=windows&clic=aucun&go=choisir&nth=2',
  'g=ok&cap=clic&os=windows&clic=terminal&go=terminal',
  // The window clicked closes before « Oui »: chosen again.
  'g=ok&cap=clic&os=windows&clic=ferme&go=objectif',
  // A protected program, or one run as administrator: black, so a paste, for the rest of the session.
  'g=ok&cap=clic&os=windows&rate=noire&go=apercu',
  'g=ok&cap=clic&os=windows&rate=noire:2&go=enregistre',
  // Minimized: opened again, looked at again. Closed after a step: chosen again, the session goes on.
  'g=ok&cap=clic&os=windows&rate=reduite&go=etape',
  'g=ok&cap=clic&os=windows&rate=fermee:2&go=etape&nth=2',
  // macOS's picker closed once, then refused (paste mode).
  'g=ok&cap=selecteur&selecteur=ferme&go=etape',
  'g=ok&cap=selecteur&selecteur=refuse&go=apercu',
  // Paste mode where Bibo cannot see windows: macOS 13, Wayland, X11 without a compositing manager. The
  // reason is never said (the mock ends on « mot-interdit » at a word of the system's).
  'g=ok&cap=macos_13&go=coller',
  'g=ok&cap=wayland&os=linux&go=coller',
  'g=ok&cap=sans_compositeur&os=linux&go=coller',
  // A capture pasted where the window is to be chosen (the night's walk, terrain_app.py): pasting from then on.
  'g=ok&cap=clic&os=windows&colle-avant=1&go=enregistre',
  'g=ok&cap=selecteur&colle-avant=1&go=enregistre',
  // The review's point 20: a capture pasted on a step stands for « C'est fait »; « Arrêter » asks once a step
  // was shown; the looks left said only near the end. Point 19: a note for the company, copied.
  'g=ok&cap=clic&os=windows&go=apercu&nth=2&colle-etape=1',
  'g=ok&go=etape&arret=1',
  'g=ok&go=etape&compte=32',
  'g=ok&vu=0&go=comment&click=note-entreprise',
  // The reviews of #70: a PDF reader whose title names Sage is no Sage; « J'ai déjà une capture » leads to the
  // paste; the brain's summaries said at the limit only once the install went through; on a Mac, the window
  // picked gone: the picker's words, not a click's.
  'g=ok&cap=clic&os=windows&clic=pdf&go=objectif',
  'g=ok&cap=selecteur&selecteur=introuvable&go=objectif',
  'g=ok&cap=clic&os=windows&go=coller&click=deja-capture',
  'g=quota&go=erreur&complet=0',
  // Their third pass. The window that drew black, chosen again: the paste at once, not a third black photo;
  // another window: its photo. A site Bibo does not know, in a browser named « Arc »: the program asked, the
  // tabs said. Not open yet: no wait until « Il est ouvert » (its splash or Start would be taken). Nothing
  // changed, or not here: a new window may have opened, chosen again. The note not copied: shown, to copy by
  // hand. « Arrêter le guide ? » is a screen of its own: a paste is not taken there, and the wait for a click
  // stops (its end would replace the question).
  'g=ok&cap=clic&os=windows&rate=noire&go=coller&nth=2&click=auto',
  'g=ok&cap=clic&os=windows&rate=noire&go=etape&click=auto,liste,fenetre-4',
  'g=ok&cap=clic&os=windows&clic=arc&go=apercu',
  'g=ok&cap=clic&os=windows&go=objectif&click=pas-ouvert',
  'g=pas-fait&cap=clic&os=windows&go=etape&nth=2&click=nouvelle-fenetre',
  'g=pas-ici&cap=clic&os=windows&go=pas-ici',
  'g=ok&vu=0&go=comment&click=note-entreprise&copie=0',
  'g=ok&go=arreter&arret=1&colle-arret=1',
  'g=ok&cap=clic&os=windows&rate=fermee:2&go=arreter&arret=choisir:2',
  // The follow-ups (chantier B). A claude.ai account whose plan Claude Code does not say: no guide, the price
  // and the way on; logging in again reads the plan. The waits numbered (sécu 3 of C4): the page's stop comes
  // before its wait reaches Rust, for the list or a capture pasted meanwhile, and the wait ends at once; a
  // page opened again numbers from the status on.
  'g=sans-forfait&go=forfait',
  'g=sans-forfait&go=forfait&click=next',
  // Its follow-up (the review of #76): the browser that would not open, the login again and the same account
  // still without its plan, or now with it; a login billed per use, logged in again, or still billed.
  'g=sans-forfait&go=forfait&lien=0&click=next',
  'g=sans-forfait&go=forfait&click=reconnecter,next',
  'g=sans-forfait&relu=ok&go=objectif&click=reconnecter,next',
  'g=billed&os=windows&go=facture&click=next,next',
  'g=billed&relu=ok&go=objectif&click=next,next',
  'g=ok&cap=clic&os=windows&course=1&go=liste&click=liste',
  'g=ok&cap=selecteur&course=1&go=coller&click=next,deja-capture',
  'g=ok&cap=clic&os=windows&base=7&go=objectif',
];

// The guide's window sizes, as the page asks Rust for them, in turn: the image made wide to hide a zone
// and kept so while zones are drawn, the reduced mode kept from a step to the next.
export const GUIDE_SIZES = {
  'g=ok&go=apercu&nth=2&click=cacher&cache=1': 'large',
  'g=ok&go=etape&nth=2&click=reduire': 'reduit normal reduit',
};
