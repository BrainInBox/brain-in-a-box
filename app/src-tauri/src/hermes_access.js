// What macOS lets Hermes do, for Brain in a Box (app/src-tauri/src/hermes_access.rs). The Hermes
// app runs it itself, as Node (ELECTRON_RUN_AS_NODE=1, started by `open`), so that macOS answers
// for Hermes. It only reads; with « ask », macOS's own microphone window, in Hermes's name.
// One line: {"disque": "ok|non|inconnu", "micro": "ok|jamais|non|inconnu"}.
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const ask = process.argv.includes('ask');

// Full Disk Access: folders only it lets list, listed and never read (Hermes's doctor lists the
// second one, which a new Mac may not have yet).
function disque() {
  let seen = 'inconnu';
  const dirs = ['/Library/Application Support/com.apple.TCC', path.join(os.homedir(), 'Library/Application Support/com.apple.TCC')];
  for (const dir of dirs) {
    try {
      fs.readdirSync(dir);
      return 'ok';
    } catch (e) {
      if (e.code === 'EPERM' || e.code === 'EACCES') seen = 'non';
    }
  }
  return seen;
}

// The microphone, from AVFoundation in a script Hermes starts: macOS answers for the app that
// started it. 0 never asked, 1 restricted, 2 refused, 3 given. With « ask » and never asked: the
// window, then its answer (two minutes at most).
const MIC = `ObjC.import('Foundation');
$.NSBundle.bundleWithPath('/System/Library/Frameworks/AVFoundation.framework').load;
const D = $.NSClassFromString('AVCaptureDevice');
const st = () => Number(D.authorizationStatusForMediaType($('soun')));
if (${ask} && st() === 0) {
  D.requestAccessForMediaTypeCompletionHandler($('soun'), function () {});
  for (let i = 0; i < 1200 && st() === 0; i++) $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
}
st();`;

function micro() {
  try {
    const out = execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', MIC], { timeout: ask ? 130000 : 15000, stdio: ['ignore', 'pipe', 'ignore'] });
    return { 0: 'jamais', 1: 'non', 2: 'non', 3: 'ok' }[out.toString().trim()] || 'inconnu';
  } catch {
    return 'inconnu';
  }
}

process.stdout.write(JSON.stringify({ disque: disque(), micro: micro() }));
