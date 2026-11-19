# Memory Benchmark Test Data (`tests/data/`)

Extracted from `graph-rag-mcp-test-wikipedia-ge10@25636130e3a.zip` (selected lightweight datasets only).

> **Safety rule:** Always seed these datasets into an isolated temporary benchmark directory. Never write or seed benchmark data into `~/.thyca`.

## Datasets

### `wikipedia_ge10/` (Vietnamese MLQA)

- `gold_ge10.jsonl` (~2.30 MB): **976** Vietnamese MLQA questions with `answers`, `mlqa_title`, `gold_document_id`, `trace_label`, and **748** unique `gold_context` passages across **66** Wikipedia topics.
- `manifest.json` (~19 KB): metadata for the **66** Wikipedia article records.
- **Note:** The source archive does not include the 66 full raw Wikipedia Markdown pages (`src_md/`) or `graph.db`. Seed this benchmark from the **748** unique `gold_context` passages in `gold_ge10.jsonl` (each passage as its own `##` entry, or grouped by `gold_document_id`).

## Excluded from Source Archive

Heavy binaries and unrelated codebase files were intentionally left out: `data-releases/20261111/input.tar.gz.part-*` (~105 MB), `data-releases/20261111/exports.tar.gz` (~22.6 MB / `graph.db`), and `graph-rag-mcp` application source, UI, and Excel reports. Non-public EasyBooks docs were not included.
