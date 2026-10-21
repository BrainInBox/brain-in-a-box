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
# Where the user's window is, and the other one over a part of it (top left corner, size, in points).
CIBLE = (300, 80, 720, 540)
CACHE = (200, 250, 400, 300)

SCENARIOS = [
    "sans_permission_au_depart", "le_selecteur_donne_la_fenetre", "une_image_sans_permission", "cest_fait_le_meme_filtre",
    "la_fenetre_telle_quelle_est", "reduite_mesuree", "rouverte", "arreter_pendant_le_choix", "apres_arreter_mesure",
    "selecteur_ferme", "choisie_de_nouveau", "fenetre_fermee", "oubliee_a_la_fin", "sans_permission_a_la_fin",
]

JXA = r'''
ObjC.import('CoreGraphics');
function run(argv) {
  var req = JSON.parse(argv[0]);
  var se = Application('System Events');
  var out = null;
  if (req.op === 'fenetre') out = fenetre(se, req.pid);
  else if (req.op === 'souris') out = souris(req.x, req.y, req.clic);
  else if (req.op === 'touche') out = touche(req.code);
  else if (req.op === 'boutons') out = boutons(se);
  else if (req.op === 'devant') out = devant(se, req.pid);
  else if (req.op === 'reduire') out = reduire(se, req.pid, req.oui);
  return JSON.stringify(out);
}
function p(el, n) { try { var v = el[n](); return v === undefined ? null : v; } catch (e) { return null; } }
function proc(se, pid) { return se.processes.whose({unixId: pid})[0]; }
function fenetre(se, pid) {
  try {
    var ws = proc(se, pid).windows();
    for (var i = 0; i < ws.length; i++) return {pos: ws[i].position(), size: ws[i].size(), titre: p(ws[i], 'name')};
  } catch (e) { return {erreur: String(e)}; }
  return null;
}
function devant(se, pid) {
  try {
    var pr = proc(se, pid);
    pr.frontmost = true;
    var w = pr.windows()[0];
    w.actions.byName('AXRaise').perform();
    return {ok: true, devant: p(pr, 'frontmost')};
  } catch (e) { return {erreur: String(e)}; }
}
function reduire(se, pid, oui) {
  try {
    var w = proc(se, pid).windows()[0];
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


def chrome(name, path, at):
    x, y, w, h = at
    return subprocess.Popen([CHROME, "--user-data-dir=" + os.path.join(DIR, "profil-" + name), "--no-first-run", "--no-default-browser-check",
                             "--disable-features=Translate", "--window-position=%d,%d" % (x, y), "--window-size=%d,%d" % (w, h),
                             "--app=file://" + path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def frame(pid):
    deadline = time.time() + 30
    while time.time() < deadline:
        f = jxa({"op": "fenetre", "pid": pid})
        if isinstance(f, dict) and f.get("pos"):
            return f
        time.sleep(1)
    raise SystemExit("no window for process %d" % pid)


def choose(target):
    """Hover a part of the user's window that nothing covers, then click the picker's « Share This
    Window » button: an unnamed AXButton of ControlCenter (macOS 15, 26), or, not in the tree (14), the
    middle of the window hovered."""
    time.sleep(3)
    f = frame(target)
    x, y, w, h = f["pos"][0], f["pos"][1], f["size"][0], f["size"][1]
    jxa({"op": "souris", "x": x + 0.5 * w, "y": y + 0.12 * h, "clic": False})
    time.sleep(2)
    buttons = jxa({"op": "boutons"})
    say("picker buttons", json.dumps(buttons))
    if isinstance(buttons, list) and buttons:
        b = buttons[0]
        bx, by = b["pos"][0] + b["size"][0] * 0.75, b["pos"][1] + b["size"][1] / 2
    else:
        bx, by = x + w / 2 + 40, y + h / 2
    jxa({"op": "souris", "x": bx, "y": by, "clic": True})


def main():
    os.makedirs(DIR, exist_ok=True)
    nonce = str(os.getpid())
    target = chrome("cible", page("cible", "Cible Bibo " + nonce, "#00aac8", then="#2e7d32"), CIBLE)
    say("target", frame(target.pid))
    cover = chrome("cache", page("cache", "Cache Bibo " + nonce, "#ff00ff"), CACHE)
    say("cover", frame(cover.pid))
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
                choose(target.pid)
            elif gesture == "couvrir":
                say("cover in front", jxa({"op": "devant", "pid": cover.pid}))
            elif gesture == "clic_page":
                f = frame(target.pid)
                say("click in the page", jxa({"op": "souris", "x": f["pos"][0] + 0.5 * f["size"][0], "y": f["pos"][1] + 0.2 * f["size"][1], "clic": True}))
            elif gesture == "reduire":
                say("minimize", jxa({"op": "reduire", "pid": target.pid, "oui": True}))
            elif gesture == "rouvrir":
                say("open again", jxa({"op": "reduire", "pid": target.pid, "oui": False}), jxa({"op": "devant", "pid": target.pid}))
            elif gesture == "fermer_le_selecteur":
                time.sleep(3)
                say("escape", jxa({"op": "touche", "code": 53}))
            elif gesture == "fermer_la_fenetre":
                target.send_signal(signal.SIGTERM)
                target.wait(timeout=30)
                say("target closed")
            open(os.path.join(DIR, "fait-%d" % msg["n"]), "w").close()
        time.sleep(0.5)

    for p in (target, cover):
        if p.poll() is None:
            p.terminate()
    results = {m["scenario"]: m for m in lines if "scenario" in m}
    missing = [s for s in SCENARIOS if s not in results]
    failed = [s for s, m in results.items() if not m.get("ok")]
    print(json.dumps({"scenarios": len(results), "manquants": missing, "rates": failed, "fin": done}, ensure_ascii=False), flush=True)
    sys.exit(0 if done and done.get("fin") and not missing and not failed else 1)


if __name__ == "__main__":
    main()
