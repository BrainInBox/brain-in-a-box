// Loads PGLite with gbrain's own pgvector build, persisted to a real directory,
// and runs a distance query. Copied into the gbrain checkout and run from there,
// so it resolves the exact @electric-sql/pglite that gbrain ships.
//   bun pgvector_smoke.ts <dataDir>
import { PGlite } from '@electric-sql/pglite';
import { vector } from '@electric-sql/pglite/vector';

const dataDir = process.argv[2];
const t0 = performance.now();
const db = await PGlite.create({ dataDir, extensions: { vector } });
await db.exec('CREATE EXTENSION IF NOT EXISTS vector');
const ext = await db.query<{ extversion: string }>(`SELECT extversion FROM pg_extension WHERE extname = 'vector'`);
await db.exec('CREATE TABLE IF NOT EXISTS t (id int, v vector(3)); TRUNCATE t; INSERT INTO t VALUES (1, \'[1,0,0]\'), (2, \'[0,1,0]\'), (3, \'[0.9,0.1,0]\')');
await db.exec('CREATE INDEX IF NOT EXISTS t_hnsw ON t USING hnsw (v vector_cosine_ops)');
const near = await db.query<{ id: number; d: number }>(`SELECT id, (v <=> '[1,0,0]')::float AS d FROM t ORDER BY v <=> '[1,0,0]' LIMIT 2`);
await db.close();
console.log(JSON.stringify({ pgvector: ext.rows[0]?.extversion ?? null, nearest: near.rows, dataDir, ms: Math.round(performance.now() - t0) }));
if (!ext.rows.length || near.rows[0]?.id !== 1 || near.rows[1]?.id !== 3) process.exit(1);
