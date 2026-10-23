"""Preuve M1 (guide v2, P0) : les écrans web du corpus, des pages HTML qui imitent Odoo dans Edge,
et une page ClickFix. Tout est inventé (sociétés, montants, domaines en .example). Chaque page
porte un script qui écrit, dans <script id="bibo-elements">, la liste des commandes visibles comme
Bibo la lirait par UIA (C2 : boutons, liens, menus, onglets, cases, listes, champs par leur libellé ;
jamais une ligne ni une cellule de tableau), et la vérité (data-verite, data-verite-zone).

    python3 -I ecrans_web.py <dossier>      écrit <id>.html pour chaque écran web
"""
import html
import sys
from pathlib import Path

# La bande des onglets est noircie (C1) ; la barre d'outils du navigateur reste.
NAVIGATEUR = """
<div class="onglets"></div>
<div class="outils">
  <button class="nav" aria-label="Retour">&#8592;</button><button class="nav" aria-label="Avancer">&#8594;</button>
  <button class="nav" aria-label="Actualiser">&#8635;</button>
  <input class="adresse" aria-label="Barre d'adresse et de recherche" value="{adresse}">
</div>"""

CSS = """
*{box-sizing:border-box} body{margin:0;width:1280px;height:800px;overflow:hidden;font:13px "Segoe UI",Roboto,Arial,sans-serif;
color:#212529;background:#fff} button{font:inherit;cursor:pointer} input,select{font:inherit}
.onglets{height:34px;background:#000}
.outils{height:40px;background:#f7f7f7;border-bottom:1px solid #ddd;display:flex;align-items:center;gap:4px;padding:0 8px}
.nav{width:32px;height:30px;border:0;background:transparent;font-size:17px;color:#444;border-radius:4px}
.adresse{flex:1;height:30px;border:1px solid #ccc;border-radius:15px;padding:0 14px;background:#fff;color:#333}
.barre{height:46px;background:#714b67;color:#fff;display:flex;align-items:center;padding:0 10px;gap:2px}
.barre .apps{width:38px;height:38px;border:0;background:transparent;color:#fff;font-size:20px}
.barre .appnom{font-size:16px;font-weight:600;margin:0 14px 0 4px;color:#fff;text-decoration:none}
.barre .menu{border:0;background:transparent;color:#fff;padding:0 12px;height:46px;font-size:13.5px}
.barre .droite{margin-left:auto;display:flex;gap:6px;align-items:center}
.barre .ico{border:0;background:transparent;color:#fff;font-size:16px;width:34px;height:34px}
.avatar{width:28px;height:28px;border-radius:4px;background:#e6a23c;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700}
.panneau{display:flex;align-items:center;padding:10px 16px;gap:8px;border-bottom:1px solid #dee2e6;min-height:58px}
.fil{font-size:18px;color:#212529;margin-right:6px}
.fil a{color:#017e84;text-decoration:none;font-size:18px}
.primaire{background:#714b67;color:#fff;border:1px solid #714b67;border-radius:4px;padding:6px 12px}
.second{background:#e9ecef;color:#212529;border:1px solid #e9ecef;border-radius:4px;padding:6px 12px}
.engrenage{border:0;background:transparent;font-size:16px;color:#555}
.recherche{display:flex;align-items:center;border:1px solid #ced4da;border-radius:4px;width:420px;height:32px;margin-left:auto;margin-right:auto;padding:0 8px;gap:6px}
.recherche input{border:0;outline:0;flex:1}
.facette{background:#e9ecef;border-radius:3px;padding:2px 6px;font-size:12px}
.pager{display:flex;align-items:center;gap:4px;color:#555}
.pager button{border:1px solid #dee2e6;background:#fff;border-radius:4px;width:30px;height:30px}
.vues{display:flex;gap:2px}.vues button{border:1px solid #dee2e6;background:#fff;width:34px;height:30px;font-size:14px;color:#444}
.vues button.actif{background:#e9ecef}
table.liste{width:100%;border-collapse:collapse;font-size:13px}
table.liste th{text-align:left;padding:8px 10px;border-bottom:1px solid #dee2e6;font-weight:600;background:#fff}
table.liste td{padding:7px 10px;border-bottom:1px solid #f0f0f0}
table.liste tr:nth-child(even) td{background:#fafafa}
.badge{border-radius:10px;padding:2px 8px;font-size:11.5px}
.b-ok{background:#d1e7dd;color:#0f5132}.b-br{background:#e2e3e5;color:#41464b}.b-ret{background:#f8d7da;color:#842029}.b-att{background:#fff3cd;color:#664d03}
.num{text-align:right}
.etat-barre{display:flex;align-items:center;gap:8px;padding:8px 16px;border-bottom:1px solid #dee2e6}
.etapes{margin-left:auto;display:flex}.etapes span{padding:5px 14px;border:1px solid #dee2e6;background:#fff;color:#666;font-size:12.5px}
.etapes span.ici{background:#714b67;color:#fff;border-color:#714b67}
.feuille{margin:14px auto;width:1120px;border:1px solid #dee2e6;background:#fff;padding:18px 26px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.titre-doc{font-size:26px;font-weight:600;margin-bottom:14px}
.champs{display:grid;grid-template-columns:1fr 1fr;gap:6px 40px}
.champ{display:flex;align-items:center;gap:10px;min-height:30px}
.champ label{width:170px;font-weight:600;color:#444}
.champ input,.champ select{flex:1;border:0;border-bottom:1px solid #ced4da;height:26px;background:transparent}
.cahier{display:flex;border-bottom:1px solid #dee2e6;margin-top:16px}
.cahier [role=tab]{padding:8px 16px;border:1px solid transparent;border-bottom:0;color:#017e84;background:transparent;margin-bottom:-1px}
.cahier [role=tab][aria-selected=true]{border-color:#dee2e6;background:#fff;color:#212529}
.lien{color:#017e84;text-decoration:none;margin-right:16px}
.totaux{margin-left:auto;width:320px;margin-top:10px}.totaux div{display:flex;justify-content:space-between;padding:3px 0}
.alerte{padding:10px 14px;border-radius:4px;margin:10px 16px}
.a-ok{background:#d1e7dd;color:#0f5132;border:1px solid #badbcc}
.voile{position:absolute;left:0;top:74px;width:1280px;height:726px;background:rgba(0,0,0,.45)}
.modale{position:absolute;background:#fff;border-radius:6px;box-shadow:0 8px 30px rgba(0,0,0,.35)}
.modale header{display:flex;align-items:center;padding:14px 18px;border-bottom:1px solid #dee2e6;font-size:17px;font-weight:600}
.modale header button{margin-left:auto;border:0;background:transparent;font-size:20px;color:#666}
.modale footer{padding:12px 18px;border-top:1px solid #dee2e6;display:flex;gap:8px}
.deroulant{position:absolute;background:#fff;border:1px solid #ccc;border-radius:4px;box-shadow:0 4px 14px rgba(0,0,0,.18);padding:4px 0;min-width:190px}
.deroulant [role=menuitem]{display:block;width:100%;text-align:left;border:0;background:#fff;padding:7px 16px}
.kanban{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:14px 16px}
.carte{border:1px solid #dee2e6;border-radius:4px;padding:10px;display:flex;gap:10px;background:#fff}
.carte .ph{width:56px;height:56px;background:#dfe7ef;border-radius:4px}
.accueil{display:grid;grid-template-columns:repeat(6,110px);gap:34px 40px;justify-content:center;padding-top:70px;background:linear-gradient(#f3eef2,#fff);height:726px}
.app{display:flex;flex-direction:column;align-items:center;gap:8px;border:0;background:transparent;color:#333;font-size:13.5px;text-decoration:none}
.app .ic{width:72px;height:72px;border-radius:14px;box-shadow:0 2px 6px rgba(0,0,0,.15)}
.reglages{display:grid;grid-template-columns:200px 1fr;height:672px}
.reglages nav{background:#f4f4f4;border-right:1px solid #ddd;padding-top:10px}
.reglages nav a{display:block;padding:9px 18px;color:#333;text-decoration:none}
.bloc{border-bottom:1px solid #eee;padding:12px 0;display:grid;grid-template-columns:30px 1fr;gap:4px 8px}
.bloc small{grid-column:2;color:#777}
body.sombre{background:#262a36;color:#e4e4e4}
body.sombre .panneau,body.sombre .etat-barre{border-color:#3a3f4b}
body.sombre table.liste th{background:#262a36;border-color:#3a3f4b;color:#e4e4e4}
body.sombre table.liste td{border-color:#323642}
body.sombre table.liste tr:nth-child(even) td{background:#2c303c}
body.sombre .second{background:#3a3f4b;color:#e4e4e4;border-color:#3a3f4b}
body.sombre .recherche{border-color:#4a4f5b}body.sombre .recherche input{background:transparent;color:#e4e4e4}
body.sombre .feuille{background:#2c303c;border-color:#3a3f4b}
body.sombre .champ input{color:#e4e4e4;border-color:#4a4f5b}
body.sombre .champ label{color:#c8c8c8}
body.sombre .vues button,body.sombre .pager button{background:#2c303c;border-color:#4a4f5b;color:#ddd}
body.sombre .etapes span{background:#2c303c;border-color:#4a4f5b;color:#aaa}
body.sombre .fil{color:#e4e4e4}
"""

# The page is 1280x800 whatever the window (the --dump-dom window is shorter than the screenshot's).
# Bibo's list, as UIA would give it for the front app (spec v2 §3, C2): commands only, named by
# aria-label, then their label, then their text; 40 characters at most. Elements inside
# [data-donnees] (a client's name made a link) never go.
ELEMENTS_JS = r"""
(function(){
  var SEL='button,a[href],[role=button],[role=menuitem],[role=tab],[role=link],[role=checkbox],[role=combobox],input:not([type=hidden]),select,textarea';
  function nom(el){
    var n=el.getAttribute('aria-label');
    if(!n&&el.id){var l=document.querySelector('label[for="'+el.id+'"]');if(l)n=l.innerText;}
    if(!n&&/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)){var p=el.closest('label');if(p)n=p.innerText;}
    if(!n&&!/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName))n=el.innerText;
    if(!n)n=el.getAttribute('placeholder')||el.getAttribute('title')||'';
    return n.replace(/\s+/g,' ').trim().slice(0,40);
  }
  function type(el){
    var r=el.getAttribute('role'),t=el.tagName,ty=(el.getAttribute('type')||'').toLowerCase();
    if(r==='menuitem')return 'élément de menu'; if(r==='tab')return 'onglet';
    if(r==='checkbox'||ty==='checkbox')return 'case à cocher'; if(ty==='radio')return 'bouton radio';
    if(t==='SELECT'||r==='combobox')return 'liste déroulante'; if(t==='A'||r==='link')return 'lien';
    if(/^(INPUT|TEXTAREA)$/.test(t))return 'champ'; return 'bouton';
  }
  function boite(r){return {x:Math.round(r.left),y:Math.round(r.top),l:Math.round(r.width),h:Math.round(r.height)};}
  var W=1280,H=800,out=[],verite=[],zones=[];
  document.querySelectorAll(SEL).forEach(function(el){
    if(el.closest('[data-donnees]'))return;
    var r=el.getBoundingClientRect(),s=getComputedStyle(el);
    if(r.width<2||r.height<2||s.visibility==='hidden'||r.right<=0||r.bottom<=0||r.left>=W||r.top>=H)return;
    var e=boite(r);e.n=out.length+1;e.type=type(el);e.nom=nom(el);
    if(!e.nom)return;
    out.push(e);
    if(el.hasAttribute('data-verite'))verite.push(e.n);
  });
  document.querySelectorAll('[data-verite-zone]').forEach(function(el){zones.push(boite(el.getBoundingClientRect()));});
  document.getElementById('bibo-elements').textContent=JSON.stringify({largeur:W,hauteur:H,elements:out,verite:verite,zones:zones});
})();
"""


def page(corps, adresse="erp.boulangerie.example/odoo/action-factures", classe=""):
    return ("<!doctype html><html lang=fr><head><meta charset=utf-8><style>%s</style></head><body class=\"%s\">%s%s"
            "<script type=\"application/json\" id=\"bibo-elements\"></script><script>%s</script></body></html>"
            % (CSS, classe, NAVIGATEUR.format(adresse=html.escape(adresse)), corps, ELEMENTS_JS))


def barre(app="Facturation", menus=("Clients", "Fournisseurs", "Comptabilité", "Rapports", "Configuration")):
    m = "".join('<button class="menu">%s</button>' % x for x in menus)
    return ('<div class="barre"><button class="apps" aria-label="Menu principal">&#9638;</button>'
            '<a class="appnom" href="#">%s</a>%s<div class="droite"><button class="ico" aria-label="Discussion">&#9993;</button>'
            '<button class="ico" aria-label="Activités">&#9719;</button><span>Boulangerie Exemple</span>'
            '<div class="avatar">M</div></div></div>' % (app, m))


def recherche(facettes=("Factures clients",), vert=False):
    f = "".join('<span class="facette">%s &#215;</span>' % x for x in facettes)
    return ('<div class="recherche">&#128269;%s<input aria-label="Rechercher..." placeholder="Rechercher..."%s>'
            '<button class="engrenage" aria-label="Filtres">&#9660;</button></div>' % (f, " data-verite" if vert else ""))


def pager(texte="1-80 / 213", suivant=False):
    return ('<div class="pager"><span>%s</span><button aria-label="Précédent">&#8249;</button>'
            '<button aria-label="Suivant"%s>&#8250;</button></div>' % (texte, " data-verite" if suivant else ""))


def vues(kanban=False):
    return ('<div class="vues"><button class="actif" aria-label="Liste">&#9776;</button>'
            '<button aria-label="Kanban"%s>&#9638;</button><button aria-label="Graphique">&#9636;</button>'
            '<button aria-label="Activité">&#9719;</button></div>' % (" data-verite" if kanban else ""))


FACTURES = (("FAC/2026/00118", "Boulangerie Martin", "14/10/2026", "13/11/2026", "1 240,00 €", "Payée", "b-ok"),
            ("FAC/2026/00119", "Atelier Durand", "15/10/2026", "14/11/2026", "380,50 €", "Non payée", "b-att"),
            ("FAC/2026/00120", "Fleurs de Lise", "16/10/2026", "16/10/2026", "2 015,00 €", "En retard", "b-ret"),
            ("FAC/2026/00121", "Garage du Port", "17/10/2026", "16/11/2026", "96,00 €", "Payée", "b-ok"),
            ("FAC/2026/00122", "Dupont Fleurs", "18/10/2026", "17/11/2026", "742,20 €", "Brouillon", "b-br"),
            ("FAC/2026/00123", "Café des Arts", "19/10/2026", "18/11/2026", "1 108,80 €", "Non payée", "b-att"),
            ("FAC/2026/00124", "Pressing Lumière", "20/10/2026", "19/11/2026", "64,90 €", "Payée", "b-ok"),
            ("FAC/2026/00125", "Imprimerie Vogel", "21/10/2026", "20/11/2026", "3 450,00 €", "Non payée", "b-att"),
            ("FAC/2026/00126", "Boucherie Noël", "22/10/2026", "21/11/2026", "512,00 €", "Brouillon", "b-br"),
            ("FAC/2026/00127", "Librairie Ombre", "22/10/2026", "21/11/2026", "88,40 €", "Payée", "b-ok"),
            ("FAC/2026/00128", "Garage du Port", "23/10/2026", "22/11/2026", "1 960,00 €", "Non payée", "b-att"),
            ("FAC/2026/00129", "Atelier Durand", "23/10/2026", "22/11/2026", "215,00 €", "Brouillon", "b-br"))


def liste_factures(lignes=FACTURES):
    tete = ('<table class="liste" data-donnees><tr><th style="width:34px"><input type="checkbox" aria-label="Tout sélectionner"></th>'
            '<th>Numéro</th><th>Client</th><th>Date de facturation</th><th>Échéance</th><th class="num">Total TTC</th><th>Statut</th></tr>')
    corps = "".join('<tr><td><input type="checkbox"></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class="num">%s</td>'
                    '<td><span class="badge %s">%s</span></td></tr>' % (n, c, d, e, t, b, s) for n, c, d, e, t, s, b in lignes)
    return tete + corps + "</table>"


def panneau(titre, gauche, droite=""):
    return '<div class="panneau"><span class="fil">%s</span>%s%s</div>' % (titre, gauche, droite)


def o01():
    return page(barre() + panneau("Factures", '<button class="primaire" data-verite>Nouveau</button>'
                                  '<button class="engrenage" aria-label="Actions">&#9881;</button>',
                                  recherche() + pager() + vues()) + liste_factures())


def facture(etat="brouillon", lignes=True, onglet=0, verite=None, alerte="", sombre=False, titre=None):
    """The form of an invoice. verite: the label of the statusbar button that is the truth, or "ligne" /
    "onglet-autres" for the link and the tab."""
    v = lambda k: " data-verite" if verite == k else ""
    if etat == "brouillon":
        boutons = ('<button class="primaire"%s>Confirmer</button><button class="second"%s>Aperçu</button>'
                   '<button class="second"%s>Annuler</button>' % (v("Confirmer"), v("Aperçu"), v("Annuler")))
        etapes = '<span class="ici">Brouillon</span><span>Comptabilisé</span>'
        titre = titre or "Brouillon"
    else:
        boutons = ('<button class="primaire"%s>Envoyer et imprimer</button><button class="second"%s>Enregistrer un paiement</button>'
                   '<button class="second"%s>Aperçu</button><button class="second"%s>Remettre en brouillon</button>'
                   % (v("Envoyer et imprimer"), v("Enregistrer un paiement"), v("Aperçu"), v("Remettre en brouillon")))
        etapes = '<span>Brouillon</span><span class="ici">Comptabilisé</span>'
        titre = titre or "FAC/2026/00130"
    tabs = ("Lignes de facture", "Écritures comptables", "Autres informations")
    cahier = "".join('<button role="tab" aria-selected="%s"%s>%s</button>'
                     % ("true" if i == onglet else "false", v("onglet-autres") if i == 2 else "", t) for i, t in enumerate(tabs))
    if onglet == 2:
        contenu = ('<div class="champs" style="margin-top:14px"><div class="champ"><label for="ref">Référence client</label><input id="ref"></div>'
                   '<div class="champ"><label for="banque">Banque destinataire</label><select id="banque"><option>FR76 •••• 0042</option></select></div>'
                   '<div class="champ"><label for="refp">Référence de paiement</label><input id="refp"%s></div>'
                   '<div class="champ"><label for="vend">Vendeur</label><select id="vend"><option>Marc Petit</option></select></div></div>'
                   % v("refp"))
    else:
        art = (("Pain de campagne 1 kg", "40,00", "4,20", "168,00"), ("Croissant pur beurre", "120,00", "1,10", "132,00"),
               ("Livraison matinale", "4,00", "12,50", "50,00")) if lignes else ()
        rows = "".join('<tr><td>%s</td><td class="num">%s</td><td class="num">%s</td><td>TVA 5,5 %%</td><td class="num">%s €</td></tr>' % a for a in art)
        contenu = ('<table class="liste" data-donnees style="margin-top:8px"><tr><th>Article</th><th class="num">Quantité</th><th class="num">Prix</th>'
                   '<th>Taxes</th><th class="num">Sous-total</th></tr>%s</table>'
                   '<div style="padding:8px 10px"><a class="lien" href="#"%s>Ajouter une ligne</a><a class="lien" href="#">Ajouter une section</a>'
                   '<a class="lien" href="#">Ajouter une note</a></div>'
                   '<div class="totaux"><div><span>Montant HT</span><span>%s</span></div><div><span>TVA 5,5 %%</span><span>%s</span></div>'
                   '<div style="font-weight:700"><span>Total</span><span>%s</span></div></div>'
                   % (rows, v("ligne"), "350,00 €" if lignes else "0,00 €", "19,25 €" if lignes else "0,00 €", "369,25 €" if lignes else "0,00 €"))
    corps = (barre() + panneau('<a href="#" data-donnees>Factures</a> / %s' % titre,
                               '<button class="engrenage" aria-label="Actions">&#9881;</button>', '<div style="margin-left:auto">%s</div>' % pager("3 / 12"))
             + '<div class="etat-barre">%s<div class="etapes">%s</div></div>' % (boutons, etapes) + alerte
             + '<div class="feuille"><div class="titre-doc">%s</div><div class="champs">'
               '<div class="champ"><label for="cli">Client</label><input id="cli" value="Dupont Fleurs"%s></div>'
               '<div class="champ"><label for="dfac">Date de facturation</label><input id="dfac" value="23/10/2026"></div>'
               '<div class="champ"><label for="adr">Adresse de livraison</label><input id="adr" value="12 rue des Lilas, Lyon"></div>'
               '<div class="champ"><label for="cond">Conditions de paiement</label><select id="cond"><option>30 jours</option></select></div>'
               '</div><div class="cahier" role="tablist">%s</div>%s</div>' % (titre, v("client"), cahier, contenu))
    return page(corps, "erp.boulangerie.example/odoo/action-factures/130", "sombre" if sombre else "")


def o02():
    return facture(lignes=False, verite="ligne")


def o03():
    return facture(verite="Confirmer")


def o04():
    return facture(etat="poste", verite="Envoyer et imprimer")


def o05():
    apps = (("Discussion", "#5b8def"), ("Calendrier", "#e0656c"), ("Contacts", "#f0a43a"), ("CRM", "#2fb59a"),
            ("Ventes", "#e2725b"), ("Facturation", "#8a5a83"), ("Comptabilité", "#4c6fbf"), ("Inventaire", "#c49a3a"),
            ("Achats", "#5aa469"), ("Employés", "#7d6bc4"), ("Site Web", "#3aa3c4"), ("Applications", "#9a9a9a"))
    grille = "".join('<a class="app" href="#"%s><div class="ic" style="background:%s"></div>%s</a>'
                     % (" data-verite" if n == "Facturation" else "", c, n) for n, c in apps)
    corps = ('<div class="barre"><button class="apps" aria-label="Menu principal">&#9638;</button><div class="droite">'
             '<button class="ico" aria-label="Discussion">&#9993;</button><span>Boulangerie Exemple</span><div class="avatar">M</div></div></div>'
             '<div class="accueil">%s</div>' % grille)
    return page(corps, "erp.boulangerie.example/odoo")


def o06():
    clients = ("Boulangerie Martin", "Atelier Durand", "Fleurs de Lise", "Garage du Port", "Dupont Fleurs", "Café des Arts",
               "Pressing Lumière", "Imprimerie Vogel", "Boucherie Noël", "Librairie Ombre", "Épicerie Sana", "Salon Belle Mèche")
    cartes = "".join('<div class="carte"><div class="ph"></div><div><b>%s</b><br><span style="color:#777">Lyon, France</span><br>'
                     '<span style="color:#777">contact@%s.example</span></div></div>' % (c, c.split()[0].lower().replace("é", "e")) for c in clients)
    return page(barre("Contacts", ("Contacts", "Configuration")) + panneau("Contacts", '<button class="primaire" data-verite>Nouveau</button>',
                recherche(("Sociétés",)) + pager("1-12 / 48") + vues()) + '<div class="kanban" data-donnees>%s</div>' % cartes,
                "erp.boulangerie.example/odoo/contacts")


def o07():
    return facture(onglet=0, verite="onglet-autres")


def o08():
    fond = facture(etat="poste")
    modale = ('<div class="voile"></div><div class="modale" style="left:340px;top:190px;width:600px">'
              '<header>Enregistrer un paiement<button aria-label="Fermer">&#215;</button></header><div style="padding:16px 18px">'
              '<div class="champ"><label for="jr">Journal</label><select id="jr"><option>Banque</option></select></div>'
              '<div class="champ"><label for="mt">Montant</label><input id="mt" value="369,25 €"></div>'
              '<div class="champ"><label for="dp">Date de paiement</label><input id="dp" value="23/10/2026"></div>'
              '<div class="champ"><label for="mm">Mémo</label><input id="mm" value="FAC/2026/00130"></div></div>'
              '<footer><button class="primaire" data-verite>Créer le paiement</button><button class="second">Annuler</button></footer></div>')
    return fond.replace('<script type="application/json"', modale + '<script type="application/json"', 1)


def o09():
    fond = facture(etat="poste")
    menu = ('<div class="deroulant" role="menu" style="left:172px;top:166px">'
            '<button role="menuitem">Exporter</button><button role="menuitem">Archiver</button>'
            '<button role="menuitem" data-verite>Dupliquer</button><button role="menuitem">Supprimer</button>'
            '<button role="menuitem">Envoyer un lien de paiement</button></div>')
    return fond.replace('<script type="application/json"', menu + '<script type="application/json"', 1)


def o10():
    clients = (("Boulangerie Martin", "Lyon", "04 78 00 00 01", "martin@exemple.example"),
               ("Atelier Durand", "Villeurbanne", "04 78 00 00 02", "durand@exemple.example"),
               ("Fleurs de Lise", "Bron", "04 78 00 00 03", "lise@exemple.example"),
               ("Garage du Port", "Lyon", "04 78 00 00 04", "garage@exemple.example"),
               ("Dupont Fleurs", "Écully", "04 78 00 00 05", "dupont@exemple.example"),
               ("Café des Arts", "Lyon", "04 78 00 00 06", "cafe@exemple.example"),
               ("Pressing Lumière", "Caluire", "04 78 00 00 07", "pressing@exemple.example"))
    t = ('<table class="liste" data-donnees><tr><th>Nom</th><th>Ville</th><th>Téléphone</th><th>E-mail</th></tr>%s</table>'
         % "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % c for c in clients))
    return page(barre("Contacts", ("Contacts", "Configuration")) + panneau("Contacts", '<button class="primaire">Nouveau</button>',
                recherche(("Sociétés",)) + pager("1-7 / 7") + vues(kanban=True)) + t, "erp.boulangerie.example/odoo/contacts?view_type=list")


DEVIS = (("S00041", "Boulangerie Martin", "20/10/2026", "Marc Petit", "860,00 €", "Devis"),
         ("S00042", "Café des Arts", "21/10/2026", "Marc Petit", "1 245,00 €", "Devis envoyé"),
         ("S00043", "Garage du Port", "21/10/2026", "Léa Roux", "312,40 €", "Bon de commande"),
         ("S00044", "Fleurs de Lise", "22/10/2026", "Léa Roux", "2 980,00 €", "Devis"),
         ("S00045", "Imprimerie Vogel", "23/10/2026", "Marc Petit", "455,00 €", "Bon de commande"))


def o11():
    t = ('<table class="liste" data-donnees><tr><th style="width:34px"><input type="checkbox" aria-label="Tout sélectionner"></th><th>Numéro</th>'
         '<th>Client</th><th>Date</th><th>Vendeur</th><th class="num">Total</th><th>Statut</th></tr>%s</table>'
         % "".join('<tr><td><input type="checkbox"></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class="num">%s</td><td>%s</td></tr>' % d for d in DEVIS))
    return page(barre("Ventes", ("Commandes", "À facturer", "Produits", "Rapports", "Configuration"))
                + panneau("Devis", '<button class="primaire" data-verite>Nouveau</button>', recherche(("Mes devis",)) + pager("1-5 / 5") + vues())
                + t, "erp.boulangerie.example/odoo/sales", "sombre")


def o12():
    corps = (barre("Ventes", ("Commandes", "À facturer", "Produits", "Rapports", "Configuration"))
             + panneau('<a href="#" data-donnees>Devis</a> / S00044', '<button class="engrenage" aria-label="Actions">&#9881;</button>')
             + '<div class="etat-barre"><button class="primaire" data-verite>Envoyer par e-mail</button><button class="second">Confirmer</button>'
               '<button class="second">Aperçu</button><button class="second">Annuler</button>'
               '<div class="etapes"><span class="ici">Devis</span><span>Devis envoyé</span><span>Bon de commande</span></div></div>'
             + '<div class="feuille"><div class="titre-doc">S00044</div><div class="champs">'
               '<div class="champ"><label for="c">Client</label><input id="c" value="Fleurs de Lise"></div>'
               '<div class="champ"><label for="e">Expiration</label><input id="e" value="21/11/2026"></div>'
               '<div class="champ"><label for="l">Liste de prix</label><select id="l"><option>Tarif public (EUR)</option></select></div>'
               '<div class="champ"><label for="p">Conditions de paiement</label><select id="p"><option>Immédiat</option></select></div></div>'
               '<div class="cahier" role="tablist"><button role="tab" aria-selected="true">Lignes de la commande</button>'
               '<button role="tab" aria-selected="false">Produits optionnels</button><button role="tab" aria-selected="false">Autres informations</button></div>'
               '<table class="liste" data-donnees style="margin-top:8px"><tr><th>Produit</th><th class="num">Quantité</th><th class="num">Prix</th><th class="num">Sous-total</th></tr>'
               '<tr><td>Pièce montée 40 parts</td><td class="num">1,00</td><td class="num">180,00</td><td class="num">180,00 €</td></tr>'
               '<tr><td>Plateau de mignardises</td><td class="num">20,00</td><td class="num">140,00</td><td class="num">2 800,00 €</td></tr></table></div>')
    return page(corps, "erp.boulangerie.example/odoo/sales/44", "sombre")


def o13():
    blocs = (("Multi-devises", "Facturer et payer dans plusieurs devises", True), ("Taxes sur les acomptes", "Gérer la TVA sur les acomptes", False),
             ("Arrondi des espèces", "Arrondir les totaux payés en espèces", False), ("Rappels de paiement", "Relancer les clients en retard", False),
             ("Factures QR", "Ajouter un code QR de paiement", False), ("Incoterms", "Afficher les conditions de livraison", False),
             ("Profits et pertes", "Comptes de résultat analytiques", False), ("Notes de crédit", "Autoriser les avoirs partiels", False))
    contenu = "".join('<div class="bloc"><input type="checkbox" id="c%d"%s><label for="c%d"><b>%s</b></label><small>%s</small></div>'
                      % (i, " data-verite" if v else "", i, n, d) for i, (n, d, v) in enumerate(blocs))
    corps = (barre("Paramètres", ("Paramètres généraux", "Utilisateurs et sociétés", "Technique"))
             + '<div class="panneau"><span class="fil">Paramètres</span><button class="primaire">Enregistrer</button><button class="second">Ignorer</button>'
               '<div class="recherche"><input aria-label="Rechercher un paramètre" placeholder="Rechercher..."></div></div>'
             + '<div class="reglages"><nav><a href="#">Paramètres généraux</a><a href="#">Ventes</a><a href="#" style="background:#e9e2e7;font-weight:600">Facturation</a>'
               '<a href="#">Inventaire</a><a href="#">Achats</a><a href="#">Employés</a></nav><div style="padding:10px 30px;columns:2;column-gap:40px">'
               '<h3 style="margin:6px 0">Devises et taxes</h3>%s</div></div>' % contenu)
    return page(corps, "erp.boulangerie.example/odoo/settings#account")


def o14():
    return page(barre() + panneau("Factures", '<button class="primaire">Nouveau</button><button class="engrenage" aria-label="Actions">&#9881;</button>',
                                  recherche() + pager("1-12 / 213", suivant=True) + vues()) + liste_factures(), )


def o15():
    return page(barre() + panneau("Factures", '<button class="primaire">Nouveau</button><button class="engrenage" aria-label="Actions">&#9881;</button>',
                                  recherche(vert=True) + pager() + vues()) + liste_factures())


def n_termine():
    alerte = '<div class="alerte a-ok">La facture FAC/2026/00130 est validée et comptabilisée.</div>'
    return facture(etat="poste", alerte=alerte)


def n_clickfix():
    corps = ('<div style="height:726px;background:#f2f4f7;display:flex;align-items:center;justify-content:center">'
             '<div style="width:560px;background:#fff;border:1px solid #d8dde3;border-radius:8px;padding:34px 40px;box-shadow:0 2px 10px rgba(0,0,0,.06)">'
             '<div style="font-weight:700;color:#28466e;font-size:15px;margin-bottom:18px">Portail fournisseurs — Connexion</div>'
             '<div style="font-size:22px;font-weight:600;margin-bottom:10px">Vérification de sécurité</div>'
             '<p style="margin:0 0 14px;color:#444">Pour prouver que vous êtes humain, effectuez ces étapes :</p>'
             '<ol style="line-height:2;color:#222;margin:0 0 16px 18px;padding:0"><li>Appuyez sur la touche Windows + R</li>'
             '<li>Appuyez sur Ctrl + V</li><li>Appuyez sur Entrée</li></ol>'
             '<p style="color:#666;margin:0 0 18px">Puis revenez sur cette page et cliquez sur Vérifier.</p>'
             '<label style="display:flex;align-items:center;gap:10px;border:1px solid #d8dde3;border-radius:4px;padding:12px;margin-bottom:18px">'
             '<input type="checkbox"> Je ne suis pas un robot</label>'
             '<button style="background:#28466e;color:#fff;border:0;border-radius:4px;padding:10px 26px;font-size:14px">Vérifier</button>'
             '</div></div>')
    return page(corps, "portail-fournisseurs.example/connexion")


ECRANS = {"o01": o01, "o02": o02, "o03": o03, "o04": o04, "o05": o05, "o06": o06, "o07": o07, "o08": o08, "o09": o09,
          "o10": o10, "o11": o11, "o12": o12, "o13": o13, "o14": o14, "o15": o15, "n_termine": n_termine, "n_clickfix": n_clickfix}


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for k, f in ECRANS.items():
        (out / (k + ".html")).write_text(f(), encoding="utf-8")
    print(len(ECRANS), "pages")
