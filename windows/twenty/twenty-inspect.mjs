// twenty-inspect.mjs - Teste l'API REST Twenty + liste les objets/champs.
// Lecture seule. La cle est lue depuis ~/.gbrain/twenty/config.json, jamais affichee.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const cfg = JSON.parse(readFileSync(join(homedir(), ".gbrain", "twenty", "config.json"), "utf8"));
const base = cfg.url.replace(/\/rest\/?$/, "") + "/rest";   // URL de base + /rest (evite le doublon)
const headers = { Authorization: `Bearer ${cfg.api_key}` };

async function get(path) {
  const res = await fetch(base + path, { headers });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* non-json */ }
  return { status: res.status, json, text };
}

// Twenty REST renvoie typiquement { data: { <objet>: [...] } }.
function records(json, key) {
  return json?.data?.[key] || json?.data || [];
}

console.log("URL:", cfg.url, "\n");
for (const obj of ["companies", "people", "opportunities", "notes", "tasks"]) {
  const r = await get(`/${obj}?limit=2`);
  const recs = records(r.json, obj);
  const n = Array.isArray(recs) ? recs.length : "?";
  console.log(`/${obj.padEnd(13)} -> HTTP ${r.status} | ${n} record(s)`);
  if (Array.isArray(recs) && recs[0]) {
    console.log("    champs:", Object.keys(recs[0]).join(", "));
  } else if (r.status !== 200) {
    console.log("    ", (r.text || "").slice(0, 120));
  }
}
