---
name: guide-moi
description: Guide pas à pas dans n'importe quel logiciel (un ERP comme Odoo, Sage ou SAP, un site, un réglage) à partir de captures d'écran que la personne colle. Use when the user says "guide-moi", "aide-moi à faire X dans <logiciel>", "je ne trouve pas comment…", "montre-moi où cliquer", or pastes a screenshot of an app asking what to do next.
---

# Guide-moi

Lis `consignes.md`, à côté de ce fichier : ce sont les règles du guide de Bibo. Suis-les, avec ces différences :

1. S'il manque l'objectif ou le logiciel, pose UNE question.
2. Demande une capture de la fenêtre seule, que la personne colle ou glisse ici :
   Mac : Cmd+Ctrl+Maj+4, puis Espace, puis clic sur la fenêtre (l'image va dans le presse-papiers, sans
   fichier) ; Windows : Win+Maj+S ; Linux : l'outil Captures.
   Rappelle qu'elle peut cacher les données sensibles avant.
3. Réponds par UNE étape en texte : la consigne seule, sans coordonnées ni JSON. Dis où se trouve l'élément.
4. Attends « c'est fait » et une nouvelle capture. Vérifie que l'étape précédente est faite.
5. À la fin, propose d'enregistrer la procédure dans `Guides/<logiciel>-<objectif>.md` du cerveau : un
   en-tête `---` avec `name`, `description`, `logiciel` et `source: "bibo-guide"`, puis les étapes
   numérotées (« 1. Clique sur … »), sans aucune donnée (ni client, ni montant, ni numéro). Bibo la
   reprendra la fois suivante. Montre le texte et n'écris qu'après un oui.

Confidentialité : ici, les captures restent dans l'historique de la session, et le résumé du soir lit tes
réponses. Ne recopie aucune donnée de l'écran au-delà de ce qu'il faut pour trouver un bouton. Pour un
logiciel qui montre des clients ou des montants, conseille plutôt le guide de l'app Bibo, qui ne garde rien.
Ne lance aucune commande. Lis seulement une capture que la personne t'a désignée.
