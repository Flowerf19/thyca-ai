PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_files(
  path          TEXT PRIMARY KEY,
  source_kind   TEXT NOT NULL CHECK(source_kind IN ('daily','canonical')),
  timeline_day  TEXT,
  mtime_ns      INTEGER NOT NULL,
  size_bytes    INTEGER NOT NULL,
  CHECK((source_kind='daily' AND timeline_day IS NOT NULL)
     OR (source_kind='canonical' AND timeline_day IS NULL))
);

CREATE TABLE IF NOT EXISTS chunks(
  row_id         INTEGER PRIMARY KEY,
  chunk_id       TEXT NOT NULL UNIQUE,
  path           TEXT NOT NULL REFERENCES source_files(path) ON DELETE CASCADE,
  source_kind    TEXT NOT NULL CHECK(source_kind IN ('daily','canonical')),
  timeline_day   TEXT,
  session_id     TEXT NOT NULL,
  session_title  TEXT NOT NULL,
  heading_raw    TEXT NOT NULL,
  leaf_ord       INTEGER NOT NULL,
  line_start     INTEGER NOT NULL,
  line_end       INTEGER NOT NULL,
  text_raw       TEXT NOT NULL CHECK(length(text_raw)>0),
  text_norm      TEXT NOT NULL CHECK(length(text_norm)>0),
  content_hash   TEXT NOT NULL,
  expires_at     TEXT,
  forgotten_at   TEXT,
  UNIQUE(path, session_id, leaf_ord)
);

CREATE INDEX IF NOT EXISTS chunks_day ON chunks(timeline_day, session_id, leaf_ord);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text_raw,
  content='chunks', content_rowid='row_id',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text_raw) VALUES (new.row_id, new.text_raw);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text_raw) VALUES ('delete', old.row_id, old.text_raw);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text_raw) VALUES ('delete', old.row_id, old.text_raw);
  INSERT INTO chunks_fts(rowid, text_raw) VALUES (new.row_id, new.text_raw);
END;
