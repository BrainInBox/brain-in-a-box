// twenty-extract-commercial.mjs — ETAPE 1 de la synchro CRM -> BrainCo.
// Extrait les opportunites de la "vue Commerciale" = TOUTES sauf stage A_CONTACTER et PERDU.
// Dedup par societe -> liste de clients. Lecture seule (n'ecrit AUCUN fichier).
// Sortie : JSON sur stdout.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const cfg = JSON.parse(readFileSync(join(homedir(), ".gbrain", "twenty", "config.json"), "utf8"));
const root = cfg.url.replace(/\/rest\/?$/, "");
const H = { Authorization: "Bearer " + cfg.api_key, "Content-Type": "application/json" };
const EXCLUDE = new Set(["A_CONTACTER", "PERDU"]);

async function fetchAll(obj) {
  let all = [], cursor = null;
  for (let i = 0; i < 12; i++) {
    const r = await (await fetch(root + "/rest/" + obj + "?limit=60" + (cursor ? "&starting_after=" + cursor : ""), { headers: H })).json();
    const recs = r.data?.[obj] || [];
    all = all.concat(recs);
    cursor = r.pageInfo?.endCursor;
    if (!r.pageInfo?.hasNextPage || !recs.length) break;
  }
  return all;
}
const pname = (p) => p ? (((p.name?.firstName || "") + " " + (p.name?.lastName || "")).trim() || "") : "";
const acct = (pl) => pl === "B2G" ? "collectivite" : pl === "B2B" ? "entreprise" : "à confirmer";
const stageLabel = (s) => (s && typeof s === "object") ? s.label : s;
const slugify = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
  .replace(/^(ville de la |ville de l'|ville de |ville du |ville d'|mairie de la |mairie de |mairie du |mairie d'|commune de |commune du )/, "")
  .replace(/['']/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

const [opps, companies, people] = await Promise.all([fetchAll("opportunities"), fetchAll("companies"), fetchAll("people")]);
const coById = Object.fromEntries(companies.map((c) => [c.id, c]));
const pById = Object.fromEntries(people.map((p) => [p.id, p]));

const inScope = opps.filter((o) => !EXCLUDE.has(o.stage));
const clients = {};
const oppsWithoutCompany = [];
for (const o of inScope) {
  const stg = stageLabel(o.stage);
  const co = o.companyId ? coById[o.companyId] : null;
  if (!co) { oppsWithoutCompany.push({ opportunity: o.name || "(sans nom)", stage: stg }); continue; }
  if (!clients[co.id]) clients[co.id] = {
    companyName: co.name, companyId: co.id, naiveSlug: slugify(co.name),
    domain: co.domainName?.primaryLinkUrl || "", accountType: acct(o.pipeline),
    stages: [], opportunities: [], contacts: [],
  };
  const cl = clients[co.id];
  if (stg && !cl.stages.includes(stg)) cl.stages.push(stg);
  if (o.name) cl.opportunities.push(o.name);
  const poc = o.pointOfContactId ? pById[o.pointOfContactId] : null;
  if (poc) { const nm = pname(poc); if (nm && !cl.contacts.find((x) => x.name === nm)) cl.contacts.push({ name: nm, email: poc.emails?.primaryEmail || "", jobTitle: poc.jobTitle || "", decideur: !!poc.decideur }); }
}
// Enrichir : tous les people rattaches a ces societes (pas seulement le pointOfContact)
for (const p of people) {
  if (p.companyId && clients[p.companyId]) {
    const nm = pname(p); const cl = clients[p.companyId];
    if (nm && !cl.contacts.find((x) => x.name === nm)) cl.contacts.push({ name: nm, email: p.emails?.primaryEmail || "", jobTitle: p.jobTitle || "", decideur: !!p.decideur });
  }
}
process.stdout.write(JSON.stringify({
  inScopeCount: inScope.length, clientCount: Object.keys(clients).length,
  clients: Object.values(clients), oppsWithoutCompany,
}, null, 2));
