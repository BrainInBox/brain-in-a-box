// twenty-import.mjs — Importe le CRM Twenty dans BrainCo.
//   Companies -> clients/<slug>.md     People -> people/<slug>.md
//
// Principes :
//   - CREATE-ONLY : n'ecrase JAMAIS un fichier existant (preserve les fiches ecrites a la main).
//   - DRY-RUN par defaut (rapport seul). Ajoute --write pour ecrire reellement.
//   - Ne commit/push rien : c'est une etape separee et explicite.
//   - SRP : acces reseau / slug / mapping markdown / IO fichier sont des fonctions distinctes.
import { readFileSync, existsSync, writeFileSync, mkdirSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const WRITE = process.argv.includes("--write");
const BRAINCO = join(homedir(), "Documents", "BrainCo");
const TODAY = new Date().toISOString().slice(0, 10);

// --- config / acces API (SRP : reseau isole) ---
function loadConfig() {
  return JSON.parse(readFileSync(join(homedir(), ".gbrain", "twenty", "config.json"), "utf8"));
}
async function fetchAll(root, H, obj) {
  let all = [], cursor = null;
  for (let i = 0; i < 12; i++) {
    const url = root + "/rest/" + obj + "?limit=60" + (cursor ? "&starting_after=" + cursor : "");
    const r = await (await fetch(url, { headers: H })).json();
    const recs = r.data?.[obj] || [];
    all = all.concat(recs);
    cursor = r.pageInfo?.endCursor;
    if (!r.pageInfo?.hasNextPage || !recs.length) break;
  }
  return all;
}

// --- slugs (SRP : nom -> slug gbrain, minuscules ascii) ---
function stripPrefix(s) {
  return s.replace(/^(ville de la |ville de l'|ville de |ville du |ville d'|mairie de la |mairie de |mairie du |mairie d'|commune de |commune du )/, "");
}
function slugify(name) {
  const base = stripPrefix((name || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""));
  return base.replace(/['']/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}
function personName(p) {
  const n = p.name || {};
  return ((n.firstName || "") + " " + (n.lastName || "")).trim() || (typeof p.name === "string" ? p.name : "") || "";
}
function personSlug(p) { return slugify(personName(p)); }

// --- mapping CRM -> markdown (SRP : pur, aucune IO) ---
function pipelineToAccountType(pl) {
  return pl === "B2G" ? "collectivite" : pl === "B2B" ? "entreprise" : "à confirmer";
}
function clientSheet(company, accountType) {
  const fm = [
    "---",
    "type: client",
    `account-type: ${accountType}`,
    "crm: twenty",
    `crm-id: ${company.id}`,
    company.domainName?.primaryLinkUrl ? `domain: ${company.domainName.primaryLinkUrl}` : null,
    `updated: ${TODAY}`,
    "---",
  ].filter(Boolean).join("\n");
  const body = [
    `\n# ${company.name}`,
    "\n## Status\n_Importé du CRM Twenty — à enrichir._",
    "\n## Contacts\n",
    "\n## Meetings\n",
  ].join("\n");
  return fm + "\n" + body + "\n";
}
function personSheet(p, clientSlug) {
  const email = p.emails?.primaryEmail || "";
  const fm = [
    "---",
    "type: people",
    "crm: twenty",
    `crm-id: ${p.id}`,
    clientSlug ? `client: ${clientSlug}` : null,
    p.jobTitle ? `role: "${String(p.jobTitle).replace(/"/g, "'")}"` : null,
    email ? `email: ${email}` : null,
    `updated: ${TODAY}`,
    "---",
  ].filter(Boolean).join("\n");
  const body = [
    `\n# ${personName(p)}`,
    clientSlug ? `- Société : [[clients/${clientSlug}]]` : null,
    p.jobTitle ? `- Rôle : ${p.jobTitle}` : null,
    email ? `- Email : ${email}` : null,
    p.decideur != null ? `- Décideur : ${p.decideur ? "oui" : "non"}` : null,
  ].filter(Boolean).join("\n");
  return fm + "\n" + body + "\n";
}

// --- IO fichier (SRP : create-only) ---
function existingSlugs(dir) {
  const d = join(BRAINCO, dir);
  if (!existsSync(d)) return new Set();
  return new Set(readdirSync(d).filter((f) => f.endsWith(".md")).map((f) => f.slice(0, -3)));
}
function writeSheet(dir, slug, content) {
  const d = join(BRAINCO, dir);
  if (!existsSync(d)) mkdirSync(d, { recursive: true });
  writeFileSync(join(d, slug + ".md"), content, "utf8");
}

// --- orchestration ---
(async () => {
  if (!existsSync(BRAINCO)) { console.log("BrainCo introuvable:", BRAINCO); process.exit(1); }
  const cfg = loadConfig();
  const root = cfg.url.replace(/\/rest\/?$/, "");
  const H = { Authorization: "Bearer " + cfg.api_key, "Content-Type": "application/json" };

  const companies = await fetchAll(root, H, "companies");
  const people = await fetchAll(root, H, "people");
  const opps = await fetchAll(root, H, "opportunities");

  // account-type d'un client = pipeline de sa 1re opportunite (B2G/B2B)
  const acctByCompany = {};
  for (const o of opps) {
    if (o.companyId && !acctByCompany[o.companyId] && o.pipeline) acctByCompany[o.companyId] = pipelineToAccountType(o.pipeline);
  }
  // slug client par companyId (pour lier les people)
  const slugByCompany = {};
  for (const c of companies) slugByCompany[c.id] = slugify(c.name);

  const existClients = existingSlugs("clients");
  const existPeople = existingSlugs("people");

  // --- plan clients ---
  let cNew = 0, cSkip = 0; const cNewList = []; const bySlug = {};
  for (const c of companies) {
    const slug = slugify(c.name);
    if (!slug) continue;
    (bySlug[slug] ||= []).push(c.name);
    if (existClients.has(slug)) { cSkip++; continue; }
    cNew++; cNewList.push(slug);
    if (WRITE) writeSheet("clients", slug, clientSheet(c, acctByCompany[c.id] || "à confirmer"));
  }
  // --- plan people ---
  let pNew = 0, pSkip = 0, pNoClient = 0;
  for (const p of people) {
    const slug = personSlug(p);
    if (!slug) continue;
    if (existPeople.has(slug)) { pSkip++; continue; }
    pNew++;
    const cslug = p.companyId ? slugByCompany[p.companyId] : null;
    if (!cslug) pNoClient++;
    if (WRITE) writeSheet("people", slug, personSheet(p, cslug));
  }

  // --- rapport ---
  console.log((WRITE ? "=== ECRITURE dans BrainCo ===" : "=== DRY-RUN (rien ecrit) ===") + "\n");
  console.log(`Companies ${companies.length}  ->  clients a creer: ${cNew} | deja existants (skip): ${cSkip}`);
  console.log(`People    ${people.length}  ->  people a creer: ${pNew} | deja existants (skip): ${pSkip} | sans client lie: ${pNoClient}`);
  const dups = Object.entries(bySlug).filter(([, arr]) => arr.length > 1);
  if (dups.length) {
    console.log(`\n⚠️ Collisions de slug (${dups.length}) — plusieurs societes -> meme fichier (1 seul gagne) :`);
    dups.slice(0, 20).forEach(([s, arr]) => console.log(`   ${s}.md  <-  ${arr.join("  /  ")}`));
  }
  console.log(`\nExemples clients a creer: ${cNewList.slice(0, 15).join(", ")}`);
  if (!WRITE) console.log("\n-> relance avec  --write  pour ecrire (rien ne sera commit/push automatiquement).");
})();
