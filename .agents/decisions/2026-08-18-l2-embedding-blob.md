---
status: superseded
created: 2026-08-18
last_updated: 2026-08-20
superseded_by: 580ae03 (drop embedding runtime) — xem `.agents/plans/l2-memory-retrieval.md` GOAL-007
---

# Decision — L2 vectors live on `chunks.embedding`

> **Superseded 2026-08-20:** commit `580ae03` ("refactor(memory): drop embedding, keep FTS and trigram") removed the embedding runtime, `chunks.embedding` BLOB, and all semantic columns from `schema.sql` v3. Runtime retrieval is lexical-only (FTS5 + trigram). The hybrid architecture (lexical + exact vector + RRF) remains frozen in `.agents/plans/l2-memory-retrieval.md` and is **not** implemented; do not reintroduce embedding as implemented. This decision is kept as history; its contract no longer matches the code.

## Decision (original, 2026-08-18)

Semantic vectors for v1 are stored as `chunks.embedding` BLOB on the leaf row. Do not create a `chunks_vec` / sqlite-vec virtual table in this slice. NumPy (and later sqlite-vec reading the same BLOB) search that column.

`ArchiveStore.replace_source` upserts by `chunk_id`. A row whose `embedding_hash` is unchanged keeps its BLOB. New or hash-changed rows get `embedding=NULL`. `update_embedding` writes a BLOB only when `chunk_id`, `profile_id`, and `embedding_hash` still match.

## Consequences (original)

- `schema.sql` is the source of truth; the example store must not grow a hand-made `chunks_vec`.
- GOAL-003 cosine reads `chunks.embedding`. Profile change invalidates by rewriting `embedding_hash` and nulling the BLOB.
