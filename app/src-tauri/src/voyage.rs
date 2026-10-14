//! Checks a Voyage AI key with a one-token request before the install relies on it.
//!
//! The key reaches curl through a config file read on stdin (`-K -`), never argv,
//! so it does not show up in `ps`. It is never logged nor sent back to the window.

use std::path::Path;
use std::time::Duration;

use crate::model::{KeyCheck, KeyStatus};
use crate::sys;

const VOYAGE: &str = "https://api.voyageai.com/v1/embeddings";
const BODY: &str = r#"{"input":["ok"],"model":"voyage-4"}"#;
const CURL_ARGS: [&str; 15] = [
    // First, or curl reads ~/.curlrc, which could trace the key to a file.
    "-q",
    "-s",
    "-m",
    "15",
    "-o",
    sys::NULL_DEVICE,
    "-w",
    "%{http_code}",
    "-K",
    "-",
    "-H",
    "Content-Type: application/json",
    "-d",
    BODY,
    VOYAGE,
];

pub fn check_key(key: &str) -> KeyCheck {
    check_with(&sys::curl(), key)
}

fn check_with(curl: &Path, key: &str) -> KeyCheck {
    let key = key.trim();
    if let Err(msg) = accepted(key) {
        return reply(KeyStatus::Invalid, msg);
    }
    let out = sys::run_with(curl, &CURL_ARGS, &[], Some(&curl_config(key)), Duration::from_secs(20));
    // Without -f, curl exits 0 whatever the HTTP status: a non-zero exit means no answer.
    if out.ok() {
        from_http(out.stdout.trim())
    } else {
        from_http("000")
    }
}

/// Why the key cannot be used, checked before any request. Voyage's own keys only:
/// install.sh checks keys against api.voyageai.com, and gbrain is set up for that address.
fn accepted(key: &str) -> Result<(), &'static str> {
    if key.is_empty() {
        return Err("Colle ta clé Voyage ici. Elle commence par pa-.");
    }
    if key.starts_with("al-") {
        return Err("Les clés MongoDB Atlas ne sont pas encore prises en charge : crée une clé sur dashboard.voyageai.com.");
    }
    if !key.starts_with("pa-") {
        return Err("Ce n'est pas une clé Voyage : elle commence par pa-.");
    }
    if !well_formed(key) {
        return Err("Cette clé contient des caractères inattendus. Copie-la à nouveau depuis le site de Voyage.");
    }
    Ok(())
}

/// The key ends up inside a quoted curl config line, here and in install.sh: a quote,
/// a backslash or a line break would let it add directives of its own (an output
/// file, another URL).
pub(crate) fn well_formed(key: &str) -> bool {
    !key.is_empty() && key.len() <= 512 && key.bytes().all(|b| b.is_ascii_graphic() && b != b'"' && b != b'\\')
}

fn curl_config(key: &str) -> String {
    format!("header = \"Authorization: Bearer {key}\"\n")
}

fn from_http(code: &str) -> KeyCheck {
    match code {
        "200" => reply(KeyStatus::Ok, "Clé acceptée : ton cerveau pourra chercher par le sens."),
        "401" | "403" => reply(KeyStatus::Invalid, "Voyage refuse cette clé. Vérifie que tu l'as copiée en entier."),
        "429" => reply(
            KeyStatus::Limited,
            "La clé marche, mais Voyage la freine. Ajoute une carte bancaire sur ton compte Voyage pour lever la limite.",
        ),
        "" | "000" => {
            reply(KeyStatus::Offline, "Je n'arrive pas à joindre Voyage. Vérifie ta connexion internet, puis réessaie.")
        }
        other => KeyCheck {
            status: KeyStatus::Invalid,
            message: format!(
                "Voyage a répondu par une erreur inattendue (code {other}). Vérifie la clé, ou réessaie dans un moment."
            ),
        },
    }
}

fn reply(status: KeyStatus, message: &str) -> KeyCheck {
    KeyCheck { status, message: message.to_string() }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    use std::fs;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    #[cfg(unix)]
    use std::path::PathBuf;

    const KEY: &str = "pa-Sup3rSecret_key.42";

    /// A fake curl that records its argv and stdin, then prints `http` and exits with `code`.
    #[cfg(unix)]
    fn fake_curl(dir: &Path, http: &str, code: i32) -> PathBuf {
        let p = dir.join("curl");
        let script = format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{d}/argv'\ncat > '{d}/stdin'\nprintf '%s' '{http}'\nexit {code}\n",
            d = dir.display()
        );
        fs::write(&p, script).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).unwrap();
        p
    }

    #[test]
    fn prefix_and_charset_are_checked_before_any_request() {
        assert_eq!(accepted("pa-abc"), Ok(()));
        for atlas in ["al-abc", "al-eu-abc"] {
            assert!(accepted(atlas).is_err_and(|m| m.contains("MongoDB Atlas")), "{atlas}");
        }
        for bad in [
            "",
            "sk-ant-abc",
            "PA-abc",
            "abc",
            "pa-a\"b",
            "pa-a\\\"b",
            "pa-a\nurl = \"file:///etc/passwd\"",
            "pa-a\rb",
            "pa-a\tb",
            "pa-a b",
            "pa-é",
        ] {
            assert!(accepted(bad).is_err(), "{bad:?} should be refused");
        }
        assert!(accepted(&format!("pa-{}", "a".repeat(600))).is_err());
        // Base64 shapes stay accepted: refusing a real key would cost search by meaning.
        assert_eq!(accepted("pa-Ab+c/d=_x.y-z"), Ok(()));
    }

    #[cfg(unix)]
    #[test]
    fn refused_keys_never_reach_curl() {
        let dir = tempfile::tempdir().unwrap();
        let curl = fake_curl(dir.path(), "200", 0);
        for bad in ["   ", "sk-123", "pa-\"x", "al-xyz", "al-eu-xyz"] {
            assert_eq!(check_with(&curl, bad).status, KeyStatus::Invalid);
        }
        assert!(!dir.path().join("argv").exists());
    }

    #[test]
    fn http_codes_map_to_statuses() {
        let cases = [
            ("200", KeyStatus::Ok),
            ("401", KeyStatus::Invalid),
            ("403", KeyStatus::Invalid),
            ("429", KeyStatus::Limited),
            ("000", KeyStatus::Offline),
            ("", KeyStatus::Offline),
            ("500", KeyStatus::Invalid),
        ];
        for (code, want) in cases {
            assert_eq!(from_http(code).status, want, "code {code:?}");
        }
        assert!(from_http("502").message.contains("502"));
    }

    #[cfg(unix)]
    #[test]
    fn key_goes_through_stdin_never_argv() {
        let dir = tempfile::tempdir().unwrap();
        let curl = fake_curl(dir.path(), "401", 0);
        let r = check_with(&curl, &format!("  {KEY}\n"));
        assert_eq!(r.status, KeyStatus::Invalid);
        assert!(!r.message.contains(KEY));

        let argv = fs::read_to_string(dir.path().join("argv")).unwrap();
        assert!(!argv.contains(KEY) && !argv.contains("Sup3r"));
        assert!(argv.lines().any(|a| a == VOYAGE));
        assert_eq!(argv.lines().next(), Some("-q"), "~/.curlrc must not be read");
        let stdin = fs::read_to_string(dir.path().join("stdin")).unwrap();
        assert_eq!(stdin, format!("header = \"Authorization: Bearer {KEY}\"\n"));
    }

    #[test]
    fn atlas_keys_are_not_supported_yet() {
        let r = check_with(Path::new("/nonexistent/curl"), " al-eu-xyz ");
        assert_eq!(r.status, KeyStatus::Invalid);
        assert_eq!(
            r.message,
            "Les clés MongoDB Atlas ne sont pas encore prises en charge : crée une clé sur dashboard.voyageai.com."
        );
    }

    #[cfg(unix)]
    #[test]
    fn no_answer_means_offline() {
        let dir = tempfile::tempdir().unwrap();
        // curl's "could not resolve host": exit 6, http_code 000.
        let curl = fake_curl(dir.path(), "000", 6);
        assert_eq!(check_with(&curl, KEY).status, KeyStatus::Offline);
        assert_eq!(check_with(&dir.path().join("missing-curl"), KEY).status, KeyStatus::Offline);
    }

    #[test]
    #[ignore = "network: hits api.voyageai.com"]
    fn real_voyage_rejects_a_bogus_key() {
        assert_eq!(check_key("pa-invalid").status, KeyStatus::Invalid);
    }

    #[test]
    fn the_key_stays_off_the_command_line() {
        assert!(!CURL_ARGS.iter().any(|a| a.contains("pa-") || a.contains("Bearer")));
        assert_eq!(CURL_ARGS[0], "-q", "~/.curlrc must not be read");
        // A curl that is not there: no answer, never a verdict on the key.
        let missing = std::env::temp_dir().join("biab-no-curl").join(sys::exe("curl"));
        assert_eq!(check_with(&missing, KEY).status, KeyStatus::Offline);
    }
}
