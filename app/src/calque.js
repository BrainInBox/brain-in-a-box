// Bibo's overlay (calque.html): the ring and the bubble where Rust says (calque.rs), in the screen's
// physical pixels from the overlay's corner, made this page's by dividing by its pixel ratio. The text
// goes in as text. No command is called from here: this window may call none.
'use strict';

(() => {
  const anneau = document.querySelector('.anneau');
  const bulle = document.querySelector('.bulle');
  const mot = bulle.querySelector('.mot');
  const texte = bulle.querySelector('.texte');
  let dessin = null;

  const css = (v) => `${v / window.devicePixelRatio}px`;

  function poser() {
    const d = dessin;
    if (!d) return;
    const racine = document.documentElement.style;
    for (const [nom, couleur] of Object.entries(d.couleurs)) racine.setProperty(`--${nom}`, couleur);
    if (d.anneau) {
      const { boite: b, clair, fonce } = d.anneau;
      Object.assign(anneau.style, { left: css(b.x), top: css(b.y), width: css(b.largeur), height: css(b.hauteur) });
      // Light, dark, light, from the edge in: readable on any background (C11).
      anneau.style.boxShadow = [
        `inset 0 0 0 ${css(clair)} ${d.couleurs.clair}`,
        `inset 0 0 0 ${css(clair + fonce)} ${d.couleurs.fonce}`,
        `inset 0 0 0 ${css(2 * clair + fonce)} ${d.couleurs.clair}`,
      ].join(', ');
      anneau.hidden = false;
    } else {
      anneau.hidden = true;
    }
    const b = d.bulle.boite;
    Object.assign(bulle.style, { left: css(b.x), width: css(b.largeur), maxHeight: css(b.hauteur), borderWidth: css(d.bulle.bord) });
    if (d.bulle.en_haut) {
      Object.assign(bulle.style, { top: '', bottom: `calc(100% - ${css(b.y + b.hauteur)})` });
    } else {
      Object.assign(bulle.style, { bottom: '', top: css(b.y) });
    }
    bulle.classList.toggle('en-haut', d.bulle.en_haut);
    mot.textContent = d.mot || '';
    mot.hidden = !d.mot;
    texte.textContent = d.texte;
    bulle.hidden = false;
  }

  window.calqueDessiner = (d) => {
    dessin = d;
    poser();
  };

  window.calqueEffacer = () => {
    dessin = null;
    anneau.hidden = true;
    bulle.hidden = true;
    texte.textContent = '';
  };

  // Moved to a screen of another scale: the same pixels, this page's anew.
  window.addEventListener('resize', poser);
})();
