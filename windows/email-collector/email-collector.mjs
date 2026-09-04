// email-collector.mjs - Collecteur Gmail -> digest, pour brain-in-a-box (Windows).
//
// Couche DETERMINISTE de la recette gbrain email-to-brain : du code pull les
// mails (lecture seule, METADONNEES uniquement -- jamais le corps), filtre le
// bruit, genere les liens Gmail, deduplique. L'enrichissement (ecrire les liens
// [[clients/<slug>]] dans BrainCo) est une 2e etape faite par l'agent.
//
// Runtime : bun (ou node 18+). Zero dependance npm (fetch + node:http natifs).
//
// Commandes :
//   bun email-collector.mjs auth      -> flux OAuth navigateur (1re fois), stocke le refresh_token
//   bun email-collector.mjs collect   -> pull les mails depuis le dernier run
//   bun email-collector.mjs digest    -> ecrit le digest markdown du jour
//   bun email-collector.mjs run       -> collect + digest
//
// Config : ~/.gbrain/email-collector/config.json (cree par set-google-oauth.ps1)
// Donnees : ~/.gbrain/email-collector/data/  (LOCAL, jamais dans BrainCo)

import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";

const SCOPE = "https://www.googleapis.com/auth/gmail.readonly";
const BASE = join(homedir(), ".gbrain", "email-collector");
const CFG_PATH = join(BASE, "config.json");
const DATA = join(BASE, "data");
const MSG_DIR = join(DATA, "messages");
const DIGEST_DIR = join(DATA, "digests");
const STATE_PATH = join(DATA, "state.json");

// --- bruit / signatures (deterministe, identique a la recette) ---
const NOISE_SENDERS = [
  "noreply", "no-reply", "notifications@", "calendar-notification",
  "mailer-daemon", "postmaster", "donotreply",
];
const SIGNATURE_PATTERNS = [
  /docusign/i, /dropbox sign/i, /hellosign/i, /pandadoc/i,
  /please sign/i, /signature needed/i, /ready for your signature/i,
  /everyone has signed/i, /you just signed/i,
];

function isNoise(from) {
  const f = (from || "").toLowerCase();
  return NOISE_SENDERS.some((p) => f.includes(p));
}
function isSignature(from, subject) {
  return SIGNATURE_PATTERNS.some((p) => p.test(subject || "") || p.test(from || ""));
}

// --- helpers fichiers ---
function ensureDirs() {
  for (const d of [BASE, DATA, MSG_DIR, DIGEST_DIR]) mkdirSync(d, { recursive: true });
}
function readJson(path, fallback) {
  if (!existsSync(path)) return fallback;
  return JSON.parse(readFileSync(path, "utf8"));
}
function writeJson(path, obj) {
  writeFileSync(path, JSON.stringify(obj, null, 2), "utf8");
}
function loadConfig() {
  if (!existsSync(CFG_PATH)) {
    throw new Error(`Config absente: ${CFG_PATH}. Lance d'abord set-google-oauth.ps1.`);
  }
  const cfg = readJson(CFG_PATH, null);
  if (!cfg?.client_id || !cfg?.client_secret) {
    throw new Error("client_id/client_secret manquants dans config.json.");
  }
  return cfg;
}
function saveConfig(cfg) {
  writeJson(CFG_PATH, cfg);
}
function today() {
  // Date locale YYYY-MM-DD sans dependance.
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// --- OAuth ---
async function exchangeToken(params) {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(params),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`OAuth token ${res.status}: ${text}`);
  return JSON.parse(text);
}

// Flux navigateur (1re fois) : recupere et stocke le refresh_token.
async function runAuthFlow(cfg) {
  return new Promise((resolve, reject) => {
    const server = createServer(async (req, res) => {
      try {
        const url = new URL(req.url, "http://127.0.0.1");
        const code = url.searchParams.get("code");
        const err = url.searchParams.get("error");
        if (err) { res.end(`Erreur OAuth: ${err}`); server.close(); return reject(new Error(err)); }
        if (!code) { res.end("En attente du code..."); return; }
        const port = server.address().port;
        const tok = await exchangeToken({
          code,
          client_id: cfg.client_id,
          client_secret: cfg.client_secret,
          redirect_uri: `http://127.0.0.1:${port}`,
          grant_type: "authorization_code",
        });
        if (!tok.refresh_token) {
          res.end("Pas de refresh_token recu (deja autorise ? revoque l'acces et reessaie).");
          server.close();
          return reject(new Error("refresh_token absent -- ajoute prompt=consent / revoque l'ancien acces."));
        }
        cfg.refresh_token = tok.refresh_token;
        saveConfig(cfg);
        res.end("OK ! Refresh token enregistre. Tu peux fermer cet onglet et revenir au terminal.");
        server.close();
        resolve();
      } catch (e) { reject(e); }
    });

    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      const authUrl = "https://accounts.google.com/o/oauth2/v2/auth?" + new URLSearchParams({
        client_id: cfg.client_id,
        redirect_uri: `http://127.0.0.1:${port}`,
        response_type: "code",
        scope: SCOPE,
        access_type: "offline",
        prompt: "consent",
        login_hint: cfg.account || "",
      });
      console.log(`\nOuverture du navigateur pour autoriser ${cfg.account}...`);
      console.log(`Si rien ne s'ouvre, colle cette URL :\n${authUrl}\n`);
      // Windows : ouvrir l'URL via rundll32 (passe l'URL TELLE QUELLE au
      // navigateur par defaut). PAS `cmd /c start` : cmd traite les `&` de
      // l'URL comme des separateurs et tronque tout apres client_id.
      spawn("rundll32", ["url.dll,FileProtocolHandler", authUrl], { detached: true, stdio: "ignore" });
    });
  });
}

async function getAccessToken(cfg) {
  if (!cfg.refresh_token) throw new Error("Pas de refresh_token. Lance: bun email-collector.mjs auth");
  const tok = await exchangeToken({
    client_id: cfg.client_id,
    client_secret: cfg.client_secret,
    refresh_token: cfg.refresh_token,
    grant_type: "refresh_token",
  });
  return tok.access_token;
}

// --- Gmail (REST, metadonnees uniquement) ---
async function gmail(accessToken, path, params) {
  const url = new URL(`https://gmail.googleapis.com/gmail/v1/users/me/${path}`);
  for (const [k, v] of Object.entries(params || {})) {
    if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, x));
    else url.searchParams.set(k, v);
  }
  const res = await fetch(url, { headers: { Authorization: `Bearer ${accessToken}` } });
  const text = await res.text();
  if (!res.ok) throw new Error(`Gmail ${path} ${res.status}: ${text}`);
  return JSON.parse(text);
}

function header(msg, name) {
  const h = msg.payload?.headers?.find((x) => x.name.toLowerCase() === name.toLowerCase());
  return h?.value || "";
}
function gmailLink(messageId, account) {
  // authuser pointe sur LE bon compte (sinon ouvre le compte par defaut).
  return `https://mail.google.com/mail/u/?authuser=${encodeURIComponent(account)}#inbox/${messageId}`;
}

// Decode un corps base64url Gmail en texte.
function decodeB64(data) {
  return data ? Buffer.from(data, "base64url").toString("utf8") : "";
}

// Extrait le corps d'un message pour un mime donne (text/plain ou text/html).
// Recursif : gere les multipart imbriques.
function extractBody(payload, mime) {
  if (!payload) return "";
  if (payload.mimeType === mime && payload.body?.data) return decodeB64(payload.body.data);
  for (const part of payload.parts || []) {
    const found = extractBody(part, mime);
    if (found) return found;
  }
  return "";
}

function stripHtml(html) {
  return html.replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/[ \t]{2,}/g, " ").trim();
}

// Construit un enregistrement complet a partir d'un id de message.
// format=full => on recupere le CORPS COMPLET (texte), pas seulement le snippet.
async function buildRecord(accessToken, id, account, direction) {
  const msg = await gmail(accessToken, `messages/${id}`, { format: "full" });
  const from = header(msg, "From");
  const subject = header(msg, "Subject");
  const plain = extractBody(msg.payload, "text/plain");
  const body = (plain || stripHtml(extractBody(msg.payload, "text/html")) || msg.snippet || "").slice(0, 50000);
  return {
    id,
    account,
    direction,                       // "inbound" (recu) | "outbound" (envoye)
    from,
    to: header(msg, "To"),
    subject,
    date: header(msg, "Date"),
    snippet: msg.snippet || "",
    body,                            // corps complet (texte), plafonne a 50k
    link: gmailLink(id, account),
    noise: isNoise(from),
    signature: isSignature(from, subject),
    collectedAt: today(),
  };
}

async function collect() {
  ensureDirs();
  const cfg = loadConfig();
  const account = cfg.account || "me";
  const accessToken = await getAccessToken(cfg);
  const state = readJson(STATE_PATH, { lastCollect: null, knownIds: {} });

  // Fenetre : depuis le dernier run (heures), sinon 7 derniers jours au 1er run.
  let q = "newer_than:7d";
  if (state.lastCollect) {
    const hours = Math.max(1, Math.ceil((Date.now() - state.lastCollect) / 3.6e6));
    q = `newer_than:${hours}h`;
  }

  // Entrant (inbox) ET sortant (sent) : memes enregistrements complets, direction
  // differente. Les sortants donnent l'historique des relances/envois.
  // in:inbox / in:sent : sinon une requete `newer_than` nue ramasse AUSSI les
  // mails envoyes et les etiquette a tort "inbound" (-> relances perdues).
  const sources = [
    { q: `in:inbox ${q}`, max: 50, direction: "inbound" },
    { q: `in:sent ${q}`, max: 30, direction: "outbound" },
  ];

  const collected = [];
  for (const src of sources) {
    const list = await gmail(accessToken, "messages", { q: src.q, maxResults: src.max });
    for (const ref of list.messages || []) {
      if (state.knownIds[ref.id]) continue;
      const rec = await buildRecord(accessToken, ref.id, account, src.direction);
      collected.push(rec);
      state.knownIds[ref.id] = { direction: rec.direction, from: rec.from, subject: rec.subject, collectedAt: rec.collectedAt };
    }
  }

  state.lastCollect = Date.now();
  writeJson(STATE_PATH, state);

  const out = join(MSG_DIR, `${today()}.json`);
  const existing = readJson(out, []);
  writeJson(out, existing.concat(collected));
  console.log(`collect: ${collected.length} nouveau(x) mail(s) -> ${out}`);
  return collected;
}

function digest() {
  ensureDirs();
  const day = today();
  const msgs = readJson(join(MSG_DIR, `${day}.json`), []);
  const signatures = msgs.filter((m) => m.signature);
  const triage = msgs.filter((m) => !m.signature && !m.noise);
  const noise = msgs.filter((m) => m.noise);

  const line = (m) => `- **${m.from}** — ${m.subject || "(sans objet)"}  \n  ${m.snippet}  \n  [Ouvrir dans Gmail](${m.link})`;
  const section = (title, arr) => `## ${title} (${arr.length})\n\n${arr.map(line).join("\n\n") || "_(rien)_"}\n`;

  const md = [
    `# Digest mail — ${day}`,
    "",
    section("Signatures en attente", signatures),
    section("A traiter (vrais mails)", triage),
    section("Bruit (filtre)", noise),
  ].join("\n");

  const out = join(DIGEST_DIR, `${day}.md`);
  writeFileSync(out, md, "utf8");
  console.log(`digest: ${triage.length} a traiter, ${signatures.length} signature(s), ${noise.length} bruit -> ${out}`);
}

// --- entree ---
const cmd = process.argv[2] || "run";
try {
  if (cmd === "auth") {
    await runAuthFlow(loadConfig());
    console.log("auth: refresh_token enregistre.");
  } else if (cmd === "collect") {
    await collect();
  } else if (cmd === "digest") {
    digest();
  } else if (cmd === "run") {
    await collect();
    digest();
  } else {
    console.error(`Commande inconnue: ${cmd} (auth|collect|digest|run)`);
    process.exit(2);
  }
} catch (e) {
  console.error(`ERREUR: ${e.message}`);
  process.exit(1);
}
