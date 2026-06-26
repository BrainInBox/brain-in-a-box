// mail-candidates.mjs — dedup + pre-filtre les mails collectes -> candidats "clients".
// Retire le bruit evident (flag noise du collecteur, invitations calendrier, notifs,
// domaines non-clients). Garde les mails susceptibles de concerner un client.
// Ecrit ~/.gbrain/email-collector/data/candidates.json et imprime un resume.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const MSG_DIR = path.join(os.homedir(), ".gbrain", "email-collector", "data", "messages");
const OUT = path.join(os.homedir(), ".gbrain", "email-collector", "data", "candidates.json");

// domaines/expediteurs qui ne sont jamais un client
const HARD_NOISE = /e\.read\.ai|notifications\.lightfield|accounts\.google\.com|devinci\.fr|vivatech\.com|openclassrooms\.com|gptdao\.ai|ccsend|openai\.com|no-reply@|noreply@|registration@vivatech/i;
// sujets calendrier / notifs (pas de vrais echanges)
const CAL_SUBJECT = /^\s*(Invitation|Accept[eé]|Refus[eé]|Decline|Tentative|Peut-[eê]tre|Maybe)\b|🗓|Read Mee|^Prepare for your|Identity verification|Alerte de s[eé]curit[eé]/i;

const emailsOf = (s) => (String(s || "").match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || []).map((e) => e.toLowerCase());
const isExternal = (e) => !e.endsWith("@citymood.io") && !HARD_NOISE.test(e);

let all = [];
for (const f of fs.readdirSync(MSG_DIR).filter((f) => f.endsWith(".json"))) {
  const j = JSON.parse(fs.readFileSync(path.join(MSG_DIR, f), "utf8"));
  const arr = Array.isArray(j) ? j : (j.messages || Object.values(j).find(Array.isArray) || []);
  all.push(...arr);
}
// dedup par id (garde le record le plus complet = body le plus long)
const byId = new Map();
for (const m of all) {
  const id = m.id || (m.from + "|" + m.subject + "|" + m.date);
  const prev = byId.get(id);
  if (!prev || (m.body || "").length > (prev.body || "").length) byId.set(id, m);
}
const uniq = [...byId.values()];

let dropped = { noise: 0, calendar: 0, hardNoise: 0, internalOnly: 0 };
const candidates = [];
for (const m of uniq) {
  if (m.noise === true) { dropped.noise++; continue; }
  if (CAL_SUBJECT.test(m.subject || "")) { dropped.calendar++; continue; }
  if (HARD_NOISE.test(m.from || "")) { dropped.hardNoise++; continue; }
  const parties = [...emailsOf(m.from), ...emailsOf(m.to)];
  const ext = parties.filter(isExternal);
  const fromCity = emailsOf(m.from).some((e) => e.endsWith("@citymood.io"));
  // garde si un tiers externe est implique, OU si outbound depuis citymood (l'agent jugera via le corps)
  if (ext.length === 0 && !fromCity) { dropped.internalOnly++; continue; }
  candidates.push({
    id: m.id, date: m.date, direction: m.direction, from: m.from, to: m.to,
    subject: m.subject, link: m.link, body: String(m.body || m.snippet || "").slice(0, 1500),
  });
}

fs.writeFileSync(OUT, JSON.stringify(candidates, null, 2), "utf8");
console.log("Mails uniques:", uniq.length, "| candidats retenus:", candidates.length);
console.log("Ecartes:", JSON.stringify(dropped));
console.log("\nCandidats (date | dir | from -> to | subject):");
for (const c of candidates) {
  const from = (c.from || "").replace(/.*<|>.*/g, "").trim().slice(0, 34);
  const to = (c.to || "").replace(/.*<|>.*/g, "").trim().slice(0, 34);
  console.log("  " + (c.date || "").slice(5, 16) + " [" + (c.direction || "?").slice(0, 3) + "] " + from + " -> " + to + " | " + (c.subject || "").slice(0, 42));
}
console.log("\n-> ecrit:", OUT);
