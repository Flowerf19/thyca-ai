---
status: accepted
created: 2026-08-18
last_updated: 2026-08-18
---

# Decision — L2 vectors live on `chunks.embedding`

## Decision

Semantic vectors for v1 are stored as `chunks.embedding` BLOB on the leaf row. Do not create a `chunks_vec` / sqlite-vec virtual table in this slice. NumPy (and later sqlite-vec reading the same BLOB) search that column.

`ArchiveStore.replace_source` upserts by `chunk_id`. A row whose `embedding_hash` is unchanged keeps its BLOB. New or hash-changed rows get `embedding=NULL`. `update_embedding` writes a BLOB only when `chunk_id`, `profile_id`, and `embedding_hash` still match.

## Consequences

- `schema.sql` is the source of truth; the example store must not grow a hand-made `chunks_vec`.
- GOAL-003 cosine reads `chunks.embedding`. Profile change invalidates by rewriting `embedding_hash` and nulling the BLOB.
