// twenty-setup-pipeline.mjs - Ajoute (de façon ADDITIVE, idempotente) :
//   - pipeline : option "Prospection"
//   - stage    : options "À contacter" + "Contacté"
// Préserve toutes les options existantes. Lit la clé depuis ~/.gbrain/twenty/config.json.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const cfg = JSON.parse(readFileSync(join(homedir(), ".gbrain", "twenty", "config.json"), "utf8"));
const root = cfg.url.replace(/\/rest\/?$/, "");
const H = { Authorization: `Bearer ${cfg.api_key}`, "Content-Type": "application/json" };

async function getField(name) {
  const j = await (await fetch(root + "/rest/metadata/objects?limit=80", { headers: H })).json();
  const opp = (j.data?.objects || j.objects || []).find((o) => (o.nameSingular || o.name) === "opportunity");
  return (opp?.fields || []).find((f) => f.name === name);
}

async function ensureOptions(name, additions) {
  const f = await getField(name);
  if (!f) { console.log(`${name}: champ introuvable`); return; }
  const have = new Set((f.options || []).map((o) => o.value));
  const toAdd = additions.filter((a) => !have.has(a.value));
  if (!toAdd.length) { console.log(`${name}: deja a jour (${[...have].join(", ")})`); return; }
  const options = [...(f.options || []), ...toAdd];   // existant preserve + nouveaux
  const r = await fetch(root + "/rest/metadata/fields/" + f.id, {
    method: "PATCH", headers: H, body: JSON.stringify({ options }),
  });
  const t = await r.text();
  console.log(`${name}: PATCH HTTP ${r.status}` + (r.status >= 300 ? "  ERR " + t.slice(0, 240) : ""));
}

// 0. token valide ?
try {
  const p = JSON.parse(Buffer.from(String(cfg.api_key).split(".")[1], "base64url").toString("utf8"));
  console.log("Token expire le:", p.exp ? new Date(p.exp * 1000).toISOString() : "?");
} catch { /* ignore */ }
const test = await fetch(root + "/rest/companies?limit=1", { headers: H });
console.log("Test lecture API -> HTTP", test.status, "\n");
if (test.status !== 200) { console.log("Token invalide -> stop."); process.exit(1); }

// 1. pipeline + Prospection
await ensureOptions("pipeline", [{ value: "PROSPECTION", label: "Prospection", color: "blue", position: 2 }]);
// 2. stage + À contacter / Contacté
await ensureOptions("stage", [
  { value: "A_CONTACTER", label: "À contacter", color: "gray", position: 10 },
  { value: "CONTACTE", label: "Contacté", color: "orange", position: 11 },
]);

// 3. verif finale
console.log("\n=== etat final ===");
for (const n of ["pipeline", "stage"]) {
  const f = await getField(n);
  console.log(`${n}: ` + (f.options || []).map((o) => `${o.value}("${o.label}")`).join(", "));
}
