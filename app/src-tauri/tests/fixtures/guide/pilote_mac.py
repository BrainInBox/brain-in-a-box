"""The hand of the macOS desk test of the guide's capture (tests/bureau_mac.rs), on a VM of the CI's
only: GitHub's macOS runners let osascript drive System Events and post mouse and key events, as
the app's E2E does. Pages of known colours in Chrome (no data), then the test's app, opened by `open`
(its own process, without the screen recording permission), whose gestures this does as it asks.
No screen is captured here, no image is written.

    python3 pilote_mac.py <Bureau.app> <folder>

The exit code is 0 only if every scenario the test must give is there and passed.
"""
import json
import os
import signal
import subprocess
import sys
import time

APP, DIR = sys.argv[1], os.path.abspath(sys.argv[2])
LOG = os.path.join(DIR, "bureau.log")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Where the user's window is, and the other one over a part of it (top left corner, size, in points). An
# odd width: CoreVideo pads its rows, which an image read with the wrong row length would show.
CIBLE = (300, 80, 723, 540)
CACHE = (200, 250, 400, 300)

SCENARIOS = [
    "sans_permission_au_depart", "le_selecteur_donne_la_fenetre", "une_image_sans_permission", "cest_fait_le_meme_filtre",
    "la_fenetre_telle_quelle_est", "reduite", "rouverte", "arreter_pendant_le_choix", "apres_arreter_la_fenetre_oubliee",
    "selecteur_ferme", "choisie_de_nouveau", "fenetre_fermee", "oubliee_a_la_fin", "sans_permission_a_la_fin",
]

JXA = r'''
ObjC.import('CoreGraphics');
function run(argv) {
  var req = JSON.parse(argv[0]);
  var se = Application('System Events');
  var out = null;
  if (req.op === 'fenetre') out = fenetre(se, req.titre);
  else if (req.op === 'souris') out = souris(req.x, req.y, req.clic);
  else if (req.op === 'touche') out = touche(req.code);
  else if (req.op === 'boutons') out = boutons(se);
  else if (req.op === 'devant') out = devant(se, req.titre);
  else if (req.op === 'reduire') out = reduire(se, req.titre, req.oui);
  else if (req.op === 'placer') out = placer(se, req.titre, req.at);
  return JSON.stringify(out);
}
function p(el, n) { try { var v = el[n](); return v === undefined ? null : v; } catch (e) { return null; } }
// Chrome's windows, a minimized one included, by a part of their title.
function win(se, titre) {
  var ws = se.processes.byName('Google Chrome').windows();
  for (var i = 0; i < ws.length; i++) if (String(p(ws[i], 'name') || '').indexOf(titre) >= 0) return ws[i];
  return null;
}
function fenetre(se, titre) {
  try {
    var w = win(se, titre);
    return w ? {pos: w.position(), size: w.size(), titre: p(w, 'name')} : null;
  } catch (e) { return {erreur: String(e)}; }
}
function placer(se, titre, at) {
  try {
    var w = win(se, titre);
    w.position = [at[0], at[1]];
    w.size = [at[2], at[3]];
    return {pos: w.position(), size: w.size()};
  } catch (e) { return {erreur: String(e)}; }
}
function devant(se, titre) {
  try {
    var pr = se.processes.byName('Google Chrome');
    pr.frontmost = true;
    win(se, titre).actions.byName('AXRaise').perform();
    return {ok: true, devant: p(pr, 'frontmost')};
  } catch (e) { return {erreur: String(e)}; }
}
function reduire(se, titre, oui) {
  try {
    var w = win(se, titre);
    w.attributes.byName('AXMinimized').value = oui;
    return {ok: true, reduite: w.attributes.byName('AXMinimized').value()};
  } catch (e) { return {erreur: String(e)}; }
}
function noeuds(el, depth, out) {
  var ks = [];
  try { ks = el.uiElements(); } catch (e) { return; }
  for (var i = 0; i < ks.length; i++) {
    var k = ks[i];
    if (p(k, 'role') === 'AXButton') out.push({n: p(k, 'name'), d: p(k, 'description'), pos: p(k, 'position'), size: p(k, 'size')});
    if (depth < 8) noeuds(k, depth + 1, out);
  }
}
// The picker's buttons: ControlCenter's, outside its menu bar.
function boutons(se) {
  var out = [];
  try {
    var ws = se.processes.byName('ControlCenter').windows();
    for (var i = 0; i < ws.length; i++) noeuds(ws[i], 0, out);
  } catch (e) {}
  return out.filter(function (b) { return b.pos && b.pos[1] > 70; });
}
function souris(x, y, clic) {
  var pt = $.CGPointMake(x, y);
  var types = clic ? [5, 1, 2] : [5];
  types.forEach(function (t) {
    var ev = $.CGEventCreateMouseEvent($(), t, pt, 0);
    if (t !== 5) $.CGEventSetIntegerValueField(ev, 1, 1);
    $.CGEventPost(0, ev);
    delay(t === 5 ? 0.3 : 0.08);
  });
  return {ok: true};
}
function touche(code) {
  [true, false].forEach(function (down) {
    $.CGEventPost(0, $.CGEventCreateKeyboardEvent($(), code, down));
    delay(0.05);
  });
  return {ok: true};
}
'''


def jxa(req):
    r = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", JXA, json.dumps(req)], capture_output=True, text=True, timeout=120)
    if r.returncode:
        return {"erreur_osascript": r.stderr.strip()[-300:]}
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"illisible": r.stdout[-300:]}


def say(*parts):
    print("pilote:", *parts, flush=True)


def page(name, title, colour, then=None):
    path = os.path.join(DIR, name + ".html")
    script = ""
    if then:
        script = "<script>document.addEventListener('click',function(){document.body.style.background='%s'})</script>" % then
    with open(path, "w", encoding="utf-8") as f:
        # A white square at the top left: an image read with the wrong row length would skew it.
        f.write("<!doctype html><meta charset=utf-8><title>%s</title><style>html,body{margin:0;height:100%%;background:%s}"
                "i{position:absolute;left:20px;top:20px;width:100px;height:100px;background:#fff}</style><body><i></i>%s"
                % (title, colour, script))
    return path


def chrome(path, at):
    """A window of one Chrome (one profile, so one process System Events sees), placed there."""
    x, y, w, h = at
    return subprocess.Popen([CHROME, "--user-data-dir=" + os.path.join(DIR, "profil"), "--no-first-run", "--no-default-browser-check",
                             "--disable-features=Translate", "--window-position=%d,%d" % (x, y), "--window-size=%d,%d" % (w, h),
                             "--app=file://" + path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def frame(titre):
    deadline = time.time() + 30
    while time.time() < deadline:
        f = jxa({"op": "fenetre", "titre": titre})
        if isinstance(f, dict) and f.get("pos"):
            return f
        time.sleep(1)
    raise SystemExit("no window titled %s" % titre)


def in_front(titre):
    """That window in front, tried again while System Events does not find it yet."""
    r = None
    for _ in range(10):
        r = jxa({"op": "devant", "titre": titre})
        if isinstance(r, dict) and r.get("ok"):
            break
        time.sleep(1)
    return r


def choose(target):
    """Hover a part of the user's window that nothing covers, then click the picker there. macOS 15
    and 26 put a « Share This Window » button over the window hovered (an unnamed AXButton of
    ControlCenter, small); macOS 14 lays an unnamed AXButton of its frame over the window hovered, once
    the pointer moves on it: the target's own is waited for, never another window's clicked."""
    time.sleep(3)
    f = frame(target)
    x, y, w, h = f["pos"][0], f["pos"][1], f["size"][0], f["size"][1]
    over, share = [], []
    for i in range(12):
        jxa({"op": "souris", "x": x + 0.5 * w + 7 * (i % 3), "y": y + 0.12 * h + 5 * (i % 2), "clic": False})
        time.sleep(1)
        buttons = jxa({"op": "boutons"})
        buttons = buttons if isinstance(buttons, list) else []
        over = [b for b in buttons if b["pos"] == f["pos"] and b["size"] == f["size"]]
        # macOS 15, 26: one small button, no window's overlay; 14: overlays, the target's awaited.
        share = [b for b in buttons if b["size"][1] < 60] if all(b["size"][1] < 60 for b in buttons) else []
        if over or share:
            break
    say("picker buttons", json.dumps(buttons))
    if over:
        bx, by = x + 0.5 * w, y + 0.12 * h
    elif share:
        b = share[0]
        bx, by = b["pos"][0] + b["size"][0] * 0.75, b["pos"][1] + b["size"][1] / 2
    else:
        bx, by = x + w / 2 + 40, y + h / 2
    say("click", bx, by)
    jxa({"op": "souris", "x": bx, "y": by, "clic": True})


def main():
    os.makedirs(DIR, exist_ok=True)
    nonce = str(os.getpid())
    target, cover = "Cible Bibo " + nonce, "Cache Bibo " + nonce
    browser = chrome(page("cible", target, "#00aac8", then="#2e7d32"), CIBLE)
    say("target", frame(target))
    chrome(page("cache", cover, "#ff00ff"), CACHE).wait(timeout=60)
    frame(cover)
    say("cover", jxa({"op": "placer", "titre": cover, "at": list(CACHE)}))
    open(LOG, "w").close()
    subprocess.run(["/usr/bin/open", "-n", APP, "--stdout", LOG, "--stderr", LOG, "--args", "--bureau", DIR], check=True)

    lines, seen, done = [], 0, None
    deadline = time.time() + 900
    while time.time() < deadline and done is None:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            new = f.read().splitlines()[seen:]
        seen += len(new)
        for line in new:
            print("test:", line, flush=True)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            lines.append(msg)
            if "fin" in msg:
                done = msg
            gesture = msg.get("pilote")
            if not gesture:
                continue
            if gesture == "choisir":
                choose(target)
            elif gesture == "couvrir":
                say("cover in front", in_front(cover))
            elif gesture == "clic_page":
                # In front first: a click on a window behind only brings it there.
                say("target in front", in_front(target))
                time.sleep(1)
                f = frame(target)
                say("click in the page", jxa({"op": "souris", "x": f["pos"][0] + 0.5 * f["size"][0], "y": f["pos"][1] + 0.2 * f["size"][1], "clic": True}))
            elif gesture == "reduire":
                say("minimize", jxa({"op": "reduire", "titre": target, "oui": True}))
            elif gesture == "rouvrir":
                say("open again", jxa({"op": "reduire", "titre": target, "oui": False}), in_front(target))
            elif gesture == "fermer_le_selecteur":
                time.sleep(3)
                say("escape", jxa({"op": "touche", "code": 53}))
            elif gesture == "fermer_la_fenetre":
                browser.send_signal(signal.SIGTERM)
                browser.wait(timeout=30)
                say("target closed")
            open(os.path.join(DIR, "fait-%d" % msg["n"]), "w").close()
        time.sleep(0.5)

    if browser.poll() is None:
        browser.terminate()
    results = {m["scenario"]: m for m in lines if "scenario" in m}
    missing = [s for s in SCENARIOS if s not in results]
    failed = [s for s, m in results.items() if not m.get("ok")]
    print(json.dumps({"scenarios": len(results), "manquants": missing, "rates": failed, "fin": done}, ensure_ascii=False), flush=True)
    sys.exit(0 if done and done.get("fin") and not missing and not failed else 1)


if __name__ == "__main__":
    main()
