//! Bbrain, the guide that stays once Bibo has installed everything (PARCOURS-guide.md). Bibo talks
//! to Hermes's engine the way Hermes's own app does: a `hermes serve --isolated` of its own on a
//! free loopback port (hermes_messaging::spawn_serve), then JSON-RPC 2.0 on its WebSocket
//! (`/api/ws`; the contract is Hermes's apps/shared/src/gateway-contract.openrpc.json). An answer
//! goes back to whoever asked; every other frame (an event, a question of the engine's: approval,
//! clarify) goes to Bbrain's two windows, the conversation and the pane of glass over the screen.
//! The engine is the user's own Hermes: its model, its memory, its tools. Bibo adds no model.

use std::collections::HashMap;
use std::io::ErrorKind;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use serde_json::{json, Value};
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{Message, WebSocket};

use crate::{hermes_messaging, sys};

/// What Bbrain's windows may ask the engine: to talk, to see the screen, to listen and to speak.
/// Never a shell, a setting, nor another conversation.
pub const METHODS: &[&str] = &["prompt.submit", "session.interrupt", "image.attach", "voice.toggle", "voice.record", "voice.tts"];

const GONE: &str = "Bbrain a perdu le fil avec Hermes. Réessaie dans un instant.";

type Pending = Arc<Mutex<HashMap<u64, Sender<Value>>>>;

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// The socket to the engine, shared by every caller: a frame out is queued, its answer comes back
/// by its id. One thread owns the socket (tungstenite is blocking): it writes what is queued, then
/// reads for 40 ms at most, in turn.
#[derive(Clone)]
pub struct Link {
    out: Sender<String>,
    pending: Pending,
    next: Arc<AtomicU64>,
    alive: Arc<AtomicBool>,
}

impl Link {
    pub fn open(port: u16, token: &str, on_frame: impl Fn(Value) + Send + 'static) -> Result<Link, String> {
        let url = format!("ws://127.0.0.1:{port}/api/ws?token={token}");
        let (mut socket, _) = tungstenite::connect(url.as_str()).map_err(|e| format!("Bbrain n’a pas pu joindre Hermes ({e})."))?;
        if let MaybeTlsStream::Plain(stream) = socket.get_mut() {
            stream.set_read_timeout(Some(Duration::from_millis(40))).map_err(|e| e.to_string())?;
        }
        let (out, outbox) = mpsc::channel();
        let link = Link { out, pending: Pending::default(), next: Arc::new(AtomicU64::new(1)), alive: Arc::new(AtomicBool::new(true)) };
        let (pending, alive) = (link.pending.clone(), link.alive.clone());
        std::thread::spawn(move || {
            pump(&mut socket, &outbox, &pending, &on_frame);
            alive.store(false, Ordering::SeqCst);
            // The callers still waiting wake up: their channel is gone.
            lock(&pending).clear();
            on_frame(json!({"jsonrpc": "2.0", "method": "event", "params": {"type": "bbrain.closed"}}));
        });
        Ok(link)
    }

    pub fn alive(&self) -> bool {
        self.alive.load(Ordering::SeqCst)
    }

    /// One request and its answer: the result, or the engine's own words for its refusal.
    pub fn call(&self, method: &str, params: Value, limit: Duration) -> Result<Value, String> {
        let id = self.next.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = mpsc::channel();
        lock(&self.pending).insert(id, tx);
        let frame = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params});
        let answer = match self.out.send(frame.to_string()) {
            Ok(()) => rx.recv_timeout(limit).ok(),
            Err(_) => None,
        };
        lock(&self.pending).remove(&id);
        let answer = answer.ok_or(GONE)?;
        match answer.get("error") {
            Some(error) => Err(error["message"].as_str().unwrap_or("Hermes a refusé.").to_string()),
            None => Ok(answer["result"].clone()),
        }
    }

    /// The user's answer to a question of the engine's (approval, clarify), by the question's id.
    pub fn reply(&self, id: Value, result: Value) -> Result<(), String> {
        let frame = json!({"jsonrpc": "2.0", "id": id, "result": result});
        self.out.send(frame.to_string()).map_err(|_| GONE.to_string())
    }
}

fn pump(socket: &mut WebSocket<MaybeTlsStream<TcpStream>>, outbox: &Receiver<String>, pending: &Pending, on_frame: &impl Fn(Value)) {
    loop {
        loop {
            match outbox.try_recv() {
                Ok(text) => {
                    if socket.send(Message::text(text)).is_err() {
                        return;
                    }
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    let _ = socket.close(None);
                    return;
                }
            }
        }
        match socket.read() {
            Ok(Message::Text(text)) => {
                if let Some(reply) = route(text.as_str(), pending, on_frame) {
                    if socket.send(Message::text(reply)).is_err() {
                        return;
                    }
                }
            }
            Ok(Message::Close(_)) => return,
            Ok(_) => {}
            Err(tungstenite::Error::Io(e)) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {}
            Err(_) => return,
        }
    }
}

/// What the engine asks the user before acting, which Bbrain's conversation answers. Any other
/// question of the engine's (its own app's windows, its theme) gets "no such method" at once, as the
/// contract wants of a client without it: the engine goes on instead of waiting for nobody.
const ANSWERED: &[&str] = &["approval", "clarify"];

/// A question of the engine's that the user answers (approval, clarify).
pub fn asks_user(frame: &Value) -> bool {
    frame.get("id").is_some_and(|id| !id.is_null()) && frame["method"].as_str().is_some_and(|m| ANSWERED.contains(&m))
}

/// An answer goes to its caller; an event, or a question Bbrain answers, to the windows. What to
/// write back to the engine, if anything.
fn route(text: &str, pending: &Pending, on_frame: &impl Fn(Value)) -> Option<String> {
    let frame = serde_json::from_str::<Value>(text).ok()?;
    if let Some(method) = frame.get("method").and_then(Value::as_str) {
        let id = frame.get("id").filter(|id| !id.is_null());
        if let Some(id) = id.filter(|_| !ANSWERED.contains(&method)) {
            let refusal = json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": format!("Bbrain does not handle {method}")}});
            return Some(refusal.to_string());
        }
        on_frame(frame);
        return None;
    }
    let caller = frame["id"].as_u64().and_then(|id| lock(pending).remove(&id));
    if let Some(caller) = caller {
        let _ = caller.send(frame);
    }
    None
}

struct Engine {
    child: Child,
    link: Link,
    session: String,
}

static ENGINE: Mutex<Option<Engine>> = Mutex::new(None);

/// The conversation Bbrain talks in, the engine started and joined first if need be. Both windows
/// ask at once: the second one waits, then gets the same.
pub fn start(on_frame: impl Fn(Value) + Send + 'static) -> Result<String, String> {
    let mut slot = lock(&ENGINE);
    if let Some(engine) = slot.as_mut() {
        if engine.link.alive() && matches!(engine.child.try_wait(), Ok(None)) {
            return Ok(engine.session.clone());
        }
        sys::stop(&mut engine.child);
        *slot = None;
    }
    let (mut child, port, token) = hermes_messaging::spawn_serve()?;
    let opened = Link::open(port, &token, on_frame).and_then(|link| {
        // Without it, the engine never asks this client before acting: it would give up asking.
        link.call("client.capabilities", json!({"server_requests": true}), Duration::from_secs(30))?;
        let created = link.call("session.create", json!({"cwd": sys::home()}), Duration::from_secs(90))?;
        let session = created["session_id"].as_str().ok_or("Hermes n’a pas ouvert de conversation.")?.to_string();
        Ok((link, session))
    });
    match opened {
        Ok((link, session)) => {
            *slot = Some(Engine { child, link, session: session.clone() });
            Ok(session)
        }
        Err(e) => {
            sys::stop(&mut child);
            Err(e)
        }
    }
}

/// The socket, once Bbrain has started.
pub fn link() -> Result<Link, String> {
    lock(&ENGINE).as_ref().map(|e| e.link.clone()).filter(Link::alive).ok_or_else(|| GONE.to_string())
}

/// Bbrain's engine stops with Bibo.
pub fn stop() {
    if let Some(mut engine) = lock(&ENGINE).take() {
        sys::stop(&mut engine.child);
    }
}

/// Push to talk: Control and Option held together, and only them. Their state is read every 40 ms
/// from the system: the state of the modifier keys, never a key typed, so no permission is asked.
#[cfg(target_os = "macos")]
pub fn watch_keys(on_change: impl Fn(bool) + Send + 'static) {
    #[link(name = "CoreGraphics", kind = "framework")]
    unsafe extern "C" {
        fn CGEventSourceFlagsState(state: i32) -> u64;
    }
    // kCGEventFlagMask*, and kCGEventSourceStateCombinedSessionState (0).
    const SHIFT: u64 = 1 << 17;
    const CONTROL: u64 = 1 << 18;
    const OPTION: u64 = 1 << 19;
    const COMMAND: u64 = 1 << 20;
    static WATCHING: AtomicBool = AtomicBool::new(false);
    if WATCHING.swap(true, Ordering::SeqCst) {
        return;
    }
    std::thread::spawn(move || {
        let mut held = false;
        loop {
            let flags = unsafe { CGEventSourceFlagsState(0) };
            let now = keys_held(flags, CONTROL | OPTION, SHIFT | COMMAND);
            if now != held {
                held = now;
                on_change(now);
            }
            std::thread::sleep(Duration::from_millis(40));
        }
    });
}

#[cfg(not(target_os = "macos"))]
pub fn watch_keys(_: impl Fn(bool) + Send + 'static) {}

#[cfg(any(target_os = "macos", test))]
fn keys_held(flags: u64, wanted: u64, others: u64) -> bool {
    flags & wanted == wanted && flags & others == 0
}

/// The screen the user looks at, for their model to see where to point: the main display, scaled
/// down to its size in points, so that a point of the image is a point of the screen. macOS asks
/// the user once for the screen's recording; until then, or elsewhere, None: Bbrain only listens.
pub fn shot(dir: &Path, width: f64) -> Option<PathBuf> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    std::fs::create_dir_all(dir).ok()?;
    let path = dir.join("bbrain-ecran.jpg");
    let _ = std::fs::remove_file(&path);
    let file = path.to_str()?;
    let width = format!("{}", width.round() as u32);
    let quiet = |program: &str, args: &[&str]| {
        Command::new(program).args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status().is_ok_and(|s| s.success())
    };
    let taken = quiet("/usr/sbin/screencapture", &["-x", "-m", "-t", "jpg", file]) && path.exists();
    (taken && quiet("/usr/bin/sips", &["--resampleWidth", &width, file])).then_some(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn collect() -> (Arc<Mutex<Vec<Value>>>, impl Fn(Value)) {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let into = seen.clone();
        (seen, move |v| lock(&into).push(v))
    }

    #[test]
    fn an_answer_goes_to_its_caller_and_the_rest_to_the_windows() {
        let pending = Pending::default();
        let (tx, rx) = mpsc::channel();
        lock(&pending).insert(7, tx);
        let (seen, on_frame) = collect();
        assert_eq!(route(r#"{"jsonrpc":"2.0","id":7,"result":{"session_id":"s1"}}"#, &pending, &on_frame), None);
        route(r#"{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta","payload":{"text":"Bon"}}}"#, &pending, &on_frame);
        // A question of the engine's has an id AND a method: the user's to answer, in the conversation.
        assert_eq!(route(r#"{"jsonrpc":"2.0","id":"r1","method":"approval","params":{"command":"rm x"}}"#, &pending, &on_frame), None);
        route(r#"{"jsonrpc":"2.0","id":"r2","method":"clarify","params":{"questions":[]}}"#, &pending, &on_frame);
        route(r#"{"jsonrpc":"2.0","id":99,"result":{}}"#, &pending, &on_frame);
        route("pas du json", &pending, &on_frame);
        assert_eq!(rx.try_recv().unwrap()["result"]["session_id"], "s1");
        assert!(lock(&pending).is_empty());
        let seen = lock(&seen);
        assert_eq!(seen.len(), 3);
        assert_eq!(seen[0]["params"]["type"], "message.delta");
        assert_eq!(seen[1]["method"], "approval");
        assert_eq!(seen[2]["method"], "clarify");
    }

    #[test]
    fn a_question_bbrain_does_not_answer_is_declined_at_once() {
        let (seen, on_frame) = collect();
        let reply = route(r#"{"jsonrpc":"2.0","id":"r3","method":"theme.pick","params":{}}"#, &Pending::default(), &on_frame).unwrap();
        let reply: Value = serde_json::from_str(&reply).unwrap();
        assert_eq!((reply["id"].as_str(), reply["error"]["code"].as_i64()), (Some("r3"), Some(-32601)));
        assert!(lock(&seen).is_empty());
    }

    #[test]
    fn push_to_talk_is_control_and_option_alone() {
        let (wanted, others) = ((1 << 18) | (1 << 19), (1 << 17) | (1 << 20));
        assert!(keys_held(wanted | 0x100, wanted, others));
        assert!(!keys_held(1 << 18, wanted, others));
        assert!(!keys_held(wanted | (1 << 20), wanted, others));
    }

    #[test]
    fn the_windows_ask_only_to_talk_see_listen_and_speak() {
        for never in ["shell.exec", "config.set", "session.create", "session.resume", "approval.respond"] {
            assert!(!METHODS.contains(&never), "{never}");
        }
    }

    /// The first frame from `from` on that `wanted` takes, within `secs`: its place and itself.
    fn wait_for(seen: &Arc<Mutex<Vec<Value>>>, from: usize, secs: u64, wanted: impl Fn(&Value) -> bool) -> (usize, Value) {
        let end = std::time::Instant::now() + Duration::from_secs(secs);
        loop {
            let found = lock(seen).iter().enumerate().skip(from).find(|(_, f)| wanted(f)).map(|(i, f)| (i, f.clone()));
            if let Some(found) = found {
                return found;
            }
            if std::time::Instant::now() > end {
                let after: Vec<Value> = lock(seen).iter().skip(from).cloned().collect();
                panic!("nothing wanted within {secs}s after frame {from}: {after:?}");
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    /// What the assistant said at the end of a turn, if this frame is that.
    fn said(f: &Value) -> Option<&str> {
        (f["params"]["type"] == "message.complete").then(|| f["params"]["payload"]["text"].as_str().unwrap_or_default())
    }

    /// Bbrain against a live engine (Hermes `serve`, its own HOME and a fake local model, as in
    /// .scratch/canal/start_engine.sh), as its windows use it: a name, a place shown on the screen,
    /// a risky command refused then allowed, a question answered. BBRAIN_PORT is the engine's port,
    /// BBRAIN_TOKEN_FILE the file of its token, BBRAIN_CWD its HOME, where the command may delete
    /// Brouillons-vieux; BBRAIN_IMAGE, a screenshot to attach. cargo test bbrain -- --ignored
    #[test]
    #[ignore]
    fn bbrain_talks_with_a_live_engine() {
        let port: u16 = std::env::var("BBRAIN_PORT").unwrap().parse().unwrap();
        let token = std::fs::read_to_string(std::env::var("BBRAIN_TOKEN_FILE").unwrap()).unwrap();
        let cwd = std::env::var("BBRAIN_CWD").unwrap();
        let folder = Path::new(&cwd).join("Brouillons-vieux");
        let (seen, on_frame) = collect();
        let link = Link::open(port, token.trim(), on_frame).unwrap();
        link.call("client.capabilities", json!({"server_requests": true}), Duration::from_secs(30)).unwrap();
        let created = link.call("session.create", json!({"cwd": cwd}), Duration::from_secs(90)).unwrap();
        let session = created["session_id"].as_str().unwrap().to_string();
        let submit = |text: &str| -> usize {
            let from = lock(&seen).len();
            link.call("prompt.submit", json!({"session_id": session, "text": text}), Duration::from_secs(60)).unwrap();
            from
        };

        // A name: the assistant says it back.
        let from = submit("Je m'appelle Awa");
        let (_, f) = wait_for(&seen, from, 60, |f| said(f).is_some());
        eprintln!("bbrain live, name: {}", said(&f).unwrap());
        assert!(said(&f).unwrap().contains("Awa"));

        // The screen attached, then a place to show: the [POINT] tag the pane of glass draws comes through.
        if let Ok(image) = std::env::var("BBRAIN_IMAGE") {
            let attached = link.call("image.attach", json!({"session_id": session, "path": image}), Duration::from_secs(30));
            eprintln!("bbrain live, image.attach: {attached:?}");
            assert!(attached.is_ok());
        }
        let from = submit("Montre-moi où cliquer");
        let (_, f) = wait_for(&seen, from, 60, |f| said(f).is_some());
        eprintln!("bbrain live, show: {}", said(&f).unwrap());
        assert!(said(&f).unwrap().contains("[POINT:640,360:Ici]"));

        // A risky command: the engine asks first, the conversation says no, nothing is deleted.
        let from = submit("Range mon bureau : supprime le dossier Brouillons-vieux");
        let (at, ask) = wait_for(&seen, from, 60, |f| f["method"] == "approval");
        eprintln!("bbrain live, approval asked: {}", ask["params"]);
        assert!(asks_user(&ask));
        link.reply(ask["id"].clone(), json!({"choice": "deny"})).unwrap();
        let (_, f) = wait_for(&seen, at, 60, |f| said(f).is_some());
        eprintln!("bbrain live, after no: {}", said(&f).unwrap());
        assert!(said(&f).unwrap().contains("rien supprimé"));
        assert!(folder.exists());

        // The same command, and yes this time: it runs, once.
        let from = submit("Finalement oui, supprime-le");
        let (at, ask) = wait_for(&seen, from, 60, |f| f["method"] == "approval");
        link.reply(ask["id"].clone(), json!({"choice": "once"})).unwrap();
        let (_, f) = wait_for(&seen, at, 60, |f| said(f).is_some());
        eprintln!("bbrain live, after yes: {}", said(&f).unwrap());
        assert!(said(&f).unwrap().contains("C'est fait"));
        assert!(!folder.exists());

        // A question with choices: the answer comes back to the assistant.
        let from = submit("Aide-moi à choisir par quoi commencer");
        let (at, ask) = wait_for(&seen, from, 60, |f| f["method"] == "clarify");
        eprintln!("bbrain live, question: {}", ask["params"]);
        assert!(asks_user(&ask));
        let qid = ask["params"]["questions"][0]["qid"].clone();
        let qid = qid.as_str().map(str::to_string).unwrap_or_else(|| qid.to_string());
        link.reply(ask["id"].clone(), json!({"answers": {qid: "Trier mes mails"}, "outcome": "submitted"})).unwrap();
        let (_, f) = wait_for(&seen, at, 60, |f| said(f).is_some());
        eprintln!("bbrain live, after the answer: {}", said(&f).unwrap());
        assert!(said(&f).unwrap().contains("Trier mes mails"));
        eprintln!("bbrain live: session {session}, {} frames", lock(&seen).len());
        // Every frame, for the windows' mock (app/dev/bbrain-moteur.js) to play them again.
        if let Ok(path) = std::env::var("BBRAIN_FRAMES") {
            std::fs::write(path, serde_json::to_string(&*lock(&seen)).unwrap()).unwrap();
        }
    }
}
