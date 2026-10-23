//! The assistant on the phone (docs/proposals/hermes.md, point 5). WhatsApp: Hermes's own
//! server links the user's account (its bridge, the way WhatsApp Web is linked) and gives the
//! code to scan, which Bibo draws (qr.rs): Hermes Desktop has no WhatsApp screen. Telegram:
//! Hermes Desktop's own QR page (Messagerie), opened by its hermes:// link. Either way the
//! gateway, which carries the messages, is installed as a service first, so that the assistant
//! still answers once Bibo is closed. Done is when the gateway itself says the platform is
//! connected, in gateway_state.json, which holds no secret: the .env with the tokens is never
//! opened, and the user's number never leaves this file (no log, no screen).
//!
//! The server is Bibo's own `hermes serve --isolated`: a headless backend on a free loopback
//! port, with a session token Bibo picked, never the one Hermes Desktop runs (its token stays
//! with it). It stops once the pairing is saved or cancelled, and when Bibo quits.

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Child;
use std::sync::{Mutex, MutexGuard};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};

use crate::{hermes, qr, sys};

const NO_HERMES: &str = "Hermes n’est pas encore installé sur cet ordinateur.";
const MESSAGING_PAGE: &str = "hermes://open/messaging";
const WHATSAPP: &str = "/api/messaging/whatsapp/onboarding";

/// What the assistant writes first, in the user's chat with themselves: the proof that it works.
const HELLO: &str = "Bonjour, c’est ton assistant. Me voilà sur WhatsApp : écris-moi ici quand tu veux, \
                     même loin de ton ordinateur.";

struct Server {
    child: Child,
    port: u16,
    token: String,
}

static SERVER: Mutex<Option<Server>> = Mutex::new(None);

fn server_mut() -> MutexGuard<'static, Option<Server>> {
    SERVER.lock().unwrap_or_else(|e| e.into_inner())
}

/// The platforms that already reach the assistant.
#[derive(Serialize, Debug, PartialEq, Eq)]
pub struct Reach {
    pub whatsapp: bool,
    pub telegram: bool,
}

/// None without Hermes: there is nothing to link yet.
pub fn reach() -> Option<Reach> {
    hermes::bin()?;
    Some(reach_in(&hermes_home()))
}

/// Connected, as the running gateway last wrote it.
fn reach_in(home: &Path) -> Reach {
    let state: Value = std::fs::read_to_string(home.join("gateway_state.json"))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default();
    let up = matches!(state["gateway_state"].as_str(), Some("running" | "degraded"));
    let on = |name: &str| up && state["platforms"][name]["state"] == "connected";
    Reach { whatsapp: on("whatsapp"), telegram: on("telegram") }
}

/// Hermes's home, as Hermes itself finds it (hermes_constants.py): $HERMES_HOME, else
/// %LOCALAPPDATA%\hermes on Windows and ~/.hermes elsewhere.
fn hermes_home() -> PathBuf {
    let set = std::env::var_os("HERMES_HOME").filter(|v| !v.to_string_lossy().trim().is_empty());
    if let Some(dir) = set {
        return PathBuf::from(dir);
    }
    if cfg!(windows) {
        let local = std::env::var_os("LOCALAPPDATA").map_or_else(|| sys::home().join("AppData").join("Local"), PathBuf::from);
        return local.join("hermes");
    }
    sys::home().join(".hermes")
}

/// Where a WhatsApp pairing stands, for the screen: never the account's number or name.
#[derive(Serialize, Debug)]
pub struct Pairing {
    pub id: String,
    /// starting, installing, waiting (the code is there), connected, error, expired, cancelled.
    pub status: String,
    /// The code to scan, as SVG, while it waits.
    pub qr: Option<String>,
    pub error: Option<String>,
}

fn pairing(v: &Value) -> Result<Pairing, String> {
    let id = v["pairing_id"].as_str().ok_or("Hermes n’a pas préparé de code.")?;
    let status = v["status"].as_str().unwrap_or("error").to_string();
    let qr = v["qr_payload"]
        .as_str()
        .filter(|p| status == "waiting" && !p.is_empty())
        .and_then(|p| qr::encode(p.as_bytes()))
        .map(|code| code.to_svg());
    // Hermes's own words, in English, follow for whoever helps the user.
    let error = v["error"]
        .as_str()
        .map(str::trim)
        .filter(|e| !e.is_empty())
        .map(|e| format!("Hermes n’a pas pu relier WhatsApp. ({e})"));
    Ok(Pairing { id: id.to_string(), status, qr, error })
}

/// Starts linking the user's own WhatsApp, the choice made for everyone: they talk to the
/// assistant in their chat with themselves, and nobody else can.
pub fn whatsapp_start() -> Result<Pairing, String> {
    pairing(&call("POST", WHATSAPP.to_string() + "/start", Some(json!({ "mode": "self-chat" })))?)
}

pub fn whatsapp_poll(id: &str) -> Result<Pairing, String> {
    pairing(&call("GET", format!("{WHATSAPP}/{}", path_part(id)?), None)?)
}

/// What the end of the linking gave.
#[derive(Serialize, Debug)]
pub struct Linked {
    /// The gateway runs as a service: the assistant answers while the computer is on.
    pub always_on: bool,
    /// The gateway says WhatsApp is connected.
    pub connected: bool,
    /// The assistant's first message went to the user's chat with themselves.
    pub hello: bool,
}

/// Once the phone has scanned: the gateway as a service, then Hermes saves the link and
/// restarts the gateway with it, then the assistant writes first.
pub fn whatsapp_finish(id: &str) -> Result<Linked, String> {
    let path = format!("{WHATSAPP}/{}", path_part(id)?);
    let now = call("GET", path.clone(), None)?;
    if now["status"] != "connected" {
        return Err("WhatsApp n’est pas encore relié : vise le code avec ton téléphone.".into());
    }
    let phone = now["account_phone"].as_str().filter(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit())).map(str::to_string);
    let bin = hermes::bin().ok_or(NO_HERMES)?;
    let always_on = install_gateway(&bin);
    call("POST", path + "/apply", Some(json!({})))?;
    let home = hermes_home();
    let connected = wait_until(Duration::from_secs(120), || reach_in(&home).whatsapp);
    let hello = connected && phone.is_some_and(|p| say_hello(&bin, &p));
    // Only now: the gateway restart Hermes started has finished (it runs in its own session).
    stop_server();
    Ok(Linked { always_on, connected, hello })
}

pub fn whatsapp_cancel(id: &str) {
    if let Ok(id) = path_part(id) {
        let _ = call("DELETE", format!("{WHATSAPP}/{id}"), None);
    }
    stop_server();
}

/// Telegram: the gateway as a service, then Hermes Desktop on its Messagerie page, where
/// « Créer avec un code QR » makes the user's own bot. True when the service is there; done is
/// seen by `reach`.
pub fn telegram_open() -> Result<bool, String> {
    let bin = hermes::bin().ok_or(NO_HERMES)?;
    let always_on = install_gateway(&bin);
    if !sys::open_with_system(MESSAGING_PAGE) {
        return Err("Je n’ai pas pu ouvrir Hermes. Ouvre-le toi-même, puis va dans Messagerie.".into());
    }
    Ok(always_on)
}

/// Stops Bibo's server, and with it a pairing still waiting.
pub fn stop_server() {
    if let Some(mut server) = server_mut().take() {
        sys::stop(&mut server.child);
    }
}

/// The gateway as a service (launchd, a Windows scheduled task or login item, systemd): it
/// starts with the computer and comes back if it stops. Already there: Hermes says so and leaves
/// it as it is (no --force).
fn install_gateway(bin: &Path) -> bool {
    let env = [("HERMES_NONINTERACTIVE", "1")];
    sys::run_with(bin, &["gateway", "install"], &env, None, Duration::from_secs(180)).ok()
}

fn say_hello(bin: &Path, phone: &str) -> bool {
    let to = format!("whatsapp:{phone}");
    sys::run(bin, &["send", "--to", &to, HELLO], Duration::from_secs(60)).ok()
}

fn wait_until(limit: Duration, mut done: impl FnMut() -> bool) -> bool {
    let end = Instant::now() + limit;
    loop {
        if done() {
            return true;
        }
        if Instant::now() >= end {
            return false;
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

/// A pairing id as Hermes makes them, safe in a URL path.
fn path_part(id: &str) -> Result<&str, String> {
    let safe = !id.is_empty() && id.len() <= 128 && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
    if safe {
        Ok(id)
    } else {
        Err("Ce n’est pas un code de Hermes.".into())
    }
}

/// One request to Bibo's server, started on first use.
fn call(method: &str, path: String, body: Option<Value>) -> Result<Value, String> {
    let (port, token) = server()?;
    let (status, answer) = request(port, &token, method, &path, body.as_ref())?;
    if (200..300).contains(&status) {
        Ok(answer)
    } else {
        Err(refusal(status, &answer))
    }
}

/// HTTP/1.0, so that the answer ends when the server closes the connection.
fn request(port: u16, token: &str, method: &str, path: &str, body: Option<&Value>) -> Result<(u16, Value), String> {
    let body = body.map(Value::to_string).unwrap_or_default();
    let head = format!(
        "{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nX-Hermes-Session-Token: {token}\r\n\
         Content-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        body.len()
    );
    let exchange = || -> std::io::Result<Vec<u8>> {
        let mut stream = TcpStream::connect_timeout(&SocketAddr::from((Ipv4Addr::LOCALHOST, port)), Duration::from_secs(5))?;
        stream.set_read_timeout(Some(Duration::from_secs(60)))?;
        stream.write_all(head.as_bytes())?;
        stream.write_all(body.as_bytes())?;
        let mut raw = Vec::new();
        stream.read_to_end(&mut raw)?;
        Ok(raw)
    };
    let raw = exchange().map_err(|e| format!("Hermes ne répond pas ({e})."))?;
    parse_response(&raw).ok_or_else(|| "Hermes a répondu quelque chose d’illisible.".to_string())
}

/// The status code and the JSON body of a raw HTTP answer (Null when there is no body).
fn parse_response(raw: &[u8]) -> Option<(u16, Value)> {
    let text = std::str::from_utf8(raw).ok()?;
    let (head, body) = text.split_once("\r\n\r\n")?;
    let status = head.lines().next()?.split_whitespace().nth(1)?.parse().ok()?;
    let json = if body.trim().is_empty() { Value::Null } else { serde_json::from_str(body).ok()? };
    Some((status, json))
}

/// What a refusal means for the user; Hermes's own words follow, for whoever helps them.
fn refusal(status: u16, answer: &Value) -> String {
    let said = match status {
        409 => "Hermes n’est pas encore prêt. Réessaie dans un instant.",
        410 => "Le code a expiré : réessaie, je t’en prépare un nouveau.",
        _ => "Hermes n’a pas pu le faire.",
    };
    match answer["detail"].as_str().map(str::trim).filter(|d| !d.is_empty()) {
        Some(detail) => format!("{said} ({detail})"),
        None => said.to_string(),
    }
}

/// Bibo's server: started once, then reused while it runs. Ready when it prints its port.
fn server() -> Result<(u16, String), String> {
    let mut slot = server_mut();
    if let Some(server) = slot.as_mut() {
        if matches!(server.child.try_wait(), Ok(None)) {
            return Ok((server.port, server.token.clone()));
        }
        *slot = None;
    }
    let bin = hermes::bin().ok_or(NO_HERMES)?;
    let token = fresh_token();
    let env = [("HERMES_DASHBOARD_SESSION_TOKEN", token.as_str())];
    let (mut child, lines) = sys::spawn_piped(&bin, &["serve", "--isolated", "--port", "0"], &env)
        .map_err(|e| format!("Je n’ai pas pu lancer Hermes ({e})."))?;
    let end = Instant::now() + Duration::from_secs(120);
    let port = loop {
        match lines.recv_timeout(end.saturating_duration_since(Instant::now())) {
            Ok((_, line)) => {
                if let Some(port) = ready_port(&String::from_utf8_lossy(&line)) {
                    break port;
                }
            }
            Err(_) => {
                sys::stop(&mut child);
                return Err("Hermes n’a pas démarré. Réessaie dans un instant.".into());
            }
        }
    };
    // Read on, or the server would block on a full pipe.
    std::thread::spawn(move || lines.into_iter().for_each(drop));
    *slot = Some(Server { child, port, token: token.clone() });
    Ok((port, token))
}

/// `HERMES_BACKEND_READY port=N`, printed once the server listens (web_server.py).
fn ready_port(line: &str) -> Option<u16> {
    line.trim().strip_prefix("HERMES_BACKEND_READY port=")?.parse().ok()
}

/// 128 bits from the system's randomness (std seeds every RandomState from it): enough for a
/// token that lives a few minutes on the loopback.
fn fresh_token() -> String {
    use std::hash::{BuildHasher, Hasher};
    let half = || std::collections::hash_map::RandomState::new().build_hasher().finish();
    format!("{:016x}{:016x}", half(), half())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn reads_the_port_the_server_prints() {
        assert_eq!(ready_port("HERMES_BACKEND_READY port=51234\n"), Some(51234));
        assert_eq!(ready_port("HERMES_DASHBOARD_READY port=51234"), None);
        assert_eq!(ready_port("INFO: Uvicorn running on http://127.0.0.1:51234"), None);
        assert_eq!(ready_port("HERMES_BACKEND_READY port=0x10"), None);
    }

    #[test]
    fn reads_an_answer() {
        let ok = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 11\r\n\r\n{\"ok\":true}";
        assert_eq!(parse_response(ok), Some((200, json!({ "ok": true }))));
        let gone = b"HTTP/1.1 410 Gone\r\ncontent-length: 39\r\n\r\n{\"detail\":\"WhatsApp setup expired.\"}";
        let (status, body) = parse_response(gone).expect("readable");
        assert_eq!(status, 410);
        assert_eq!(refusal(status, &body), "Le code a expiré : réessaie, je t’en prépare un nouveau. (WhatsApp setup expired.)");
        assert_eq!(parse_response(b"HTTP/1.1 204 No Content\r\n\r\n"), Some((204, Value::Null)));
        assert_eq!(parse_response(b"garbage"), None);
        assert_eq!(parse_response(b"HTTP/1.1 200 OK\r\n\r\n{not json"), None);
    }

    /// The request a real server gets: the token in its header, the JSON body whole.
    #[test]
    fn talks_to_the_server_with_its_token() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind");
        let port = listener.local_addr().expect("addr").port();
        let server = std::thread::spawn(move || {
            let (mut conn, _) = listener.accept().expect("accept");
            let mut seen = Vec::new();
            let mut buf = [0u8; 4096];
            while !String::from_utf8_lossy(&seen).contains("\"self-chat\"}") {
                let n = conn.read(&mut buf).expect("read");
                assert!(n > 0, "closed early: {}", String::from_utf8_lossy(&seen));
                seen.extend_from_slice(&buf[..n]);
            }
            let reply = "{\"pairing_id\":\"p_1\",\"status\":\"starting\",\"qr_payload\":null}";
            write!(conn, "HTTP/1.1 200 OK\r\ncontent-length: {}\r\n\r\n{reply}", reply.len()).expect("write");
            String::from_utf8(seen).expect("utf8")
        });
        let (status, body) = request(port, "tok123", "POST", "/api/messaging/whatsapp/onboarding/start", Some(&json!({ "mode": "self-chat" }))).expect("answer");
        let seen = server.join().expect("server");
        assert!(seen.starts_with("POST /api/messaging/whatsapp/onboarding/start HTTP/1.0\r\n"), "{seen}");
        assert!(seen.contains("\r\nX-Hermes-Session-Token: tok123\r\n"), "{seen}");
        assert!(seen.contains("\r\nContent-Length: 20\r\n"), "{seen}");
        assert!(seen.ends_with("\r\n\r\n{\"mode\":\"self-chat\"}"), "{seen}");
        assert_eq!(status, 200);
        assert_eq!(body["status"], "starting");
    }

    /// The code to scan while it waits, and nothing about the account, ever.
    #[test]
    fn a_pairing_shows_the_code_and_never_the_number() {
        let waiting = json!({ "pairing_id": "abc_DEF-1", "status": "waiting", "qr_payload": "2@AbCd,EfGh=,IjKl=,MnOp=",
            "account_phone": null, "error": null });
        let p = pairing(&waiting).expect("pairing");
        assert_eq!(p.status, "waiting");
        assert!(p.qr.as_deref().is_some_and(|svg| svg.starts_with("<svg ")), "{p:?}");
        assert!(p.error.is_none());

        let linked = json!({ "pairing_id": "abc_DEF-1", "status": "connected", "qr_payload": "2@old",
            "account_phone": "221770000000", "account_name": "Awa Ndiaye", "account_id": "221770000000:3@s.whatsapp.net" });
        let p = pairing(&linked).expect("pairing");
        assert!(p.qr.is_none(), "no code once linked");
        let shown = serde_json::to_string(&p).expect("json");
        assert!(!shown.contains("221770000000") && !shown.contains("Awa"), "{shown}");

        let failed = json!({ "pairing_id": "x", "status": "error", "error": " Node.js is not installed. " });
        let said = pairing(&failed).expect("pairing").error;
        assert_eq!(said.as_deref(), Some("Hermes n’a pas pu relier WhatsApp. (Node.js is not installed.)"));
        assert!(pairing(&json!({ "status": "waiting" })).is_err());
    }

    #[test]
    fn only_hermes_ids_go_in_a_path() {
        assert_eq!(path_part("Zx9_-a"), Ok("Zx9_-a"));
        for bad in ["", "../config", "a/b", "a b", "a?b=1", "é"] {
            assert!(path_part(bad).is_err(), "{bad}");
        }
        assert!(path_part(&"a".repeat(129)).is_err());
    }

    /// The gateway's own words decide, and only while it runs.
    #[test]
    fn connected_is_what_the_running_gateway_says() {
        let home = std::env::temp_dir().join(format!("bibo-reach-{}", std::process::id()));
        std::fs::create_dir_all(&home).expect("dir");
        let write = |v: Value| std::fs::write(home.join("gateway_state.json"), v.to_string()).expect("write");
        assert_eq!(reach_in(&home), Reach { whatsapp: false, telegram: false }, "no file");

        write(json!({ "gateway_state": "running", "platforms": { "whatsapp": { "state": "connected" }, "telegram": { "state": "connecting" } } }));
        assert_eq!(reach_in(&home), Reach { whatsapp: true, telegram: false });
        write(json!({ "gateway_state": "degraded", "platforms": { "telegram": { "state": "connected" } } }));
        assert_eq!(reach_in(&home), Reach { whatsapp: false, telegram: true });
        write(json!({ "gateway_state": "stopped", "platforms": { "whatsapp": { "state": "connected" } } }));
        assert_eq!(reach_in(&home), Reach { whatsapp: false, telegram: false }, "a stopped gateway reaches nobody");
        std::fs::write(home.join("gateway_state.json"), "{half").expect("write");
        assert_eq!(reach_in(&home), Reach { whatsapp: false, telegram: false });
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn tokens_differ() {
        let (a, b) = (fresh_token(), fresh_token());
        assert_eq!(a.len(), 32);
        assert!(a.bytes().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }
}
