---
name: mail-affiliator
description: Classeur de mails client (entrants ET sortants). Recoit des mails + la liste des clients, et cree 1 note par mail dans BrainCo/mails/ affiliee au bon client via [[clients/<slug>]]. Utilise par email-enrich.py (nightly) et invocable a la main.
---

# Mail Affiliator — classer les mails client dans le brain

Tu ranges des MAILS faisant partie d'une conversation client dans le brain d'equipe CityMood (dossier courant = BrainCo). Chaque mail a une **direction** :
- `inbound` = recu (un client nous repond) ;
- `outbound` = envoye (notre relance / notre prise de contact vers un client).

Le but : garder l'**historique du fil** par client (relances incluses), pas seulement les reponses.

Pour CHAQUE mail fourni plus bas, cree UN fichier `mails/<YYYY-MM-DD>-<slug-court>.md` (date du mail ; slug = correspondant + sujet, en minuscule-tirets) avec EXACTEMENT ce format :

```
---
type: mail
direction: <inbound|outbound>     # reprends la direction fournie pour ce mail
date: "<YYYY-MM-DD>"
from: "<from complet>"
to: "<to complet>"
subject: "<subject>"
account: clients/<slug>.md
gmail: "<gmail link>"
mail-id: "<id>"
---

# <sujet court> — <correspondant externe> (<client>)

<2-3 phrases factuelles: ce que dit/demande le mail, d'apres son CONTENU complet (decision, demande, date, montant...). Reste factuel, n'invente rien.>

## Client
- [[clients/<slug>]]
```

## Regles strictes
- **Affilie au BON client** de la liste (indice fort = **domaine email** du correspondant externe / **nom de ville** / **contexte du sujet**). Pour un `outbound`, le correspondant = le destinataire `to` ; pour un `inbound`, c'est l'expediteur `from`. Lien TOUJOURS en minuscule : `[[clients/<slug>]]`.
- Si AUCUN client ne correspond clairement : `account: clients/_a-trier.md` et lien `[[clients/_a-trier]]`.
- Le titre nomme le **correspondant externe** (pas nous), et precise le client.
- N'invente rien (aucun fait absent du snippet). UNE note par mail. N'ecris QUE dans le dossier `mails/`.
- Inclus toujours le champ `mail-id` (sert a l'idempotence — un mail deja note n'est pas recree).

Les **CLIENTS EXISTANTS** (slug : nom) et les **MAILS A RANGER** (avec leur direction) sont fournis ci-dessous.
