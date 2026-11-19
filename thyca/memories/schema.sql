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
  memory_id     TEXT NOT NULL,
  memory_title  TEXT NOT NULL,
  heading_raw    TEXT NOT NULL,
  leaf_ord       INTEGER NOT NULL,
  line_start     INTEGER NOT NULL,
  line_end       INTEGER NOT NULL,
  text_raw       TEXT NOT NULL CHECK(length(text_raw)>0),
  text_norm      TEXT NOT NULL CHECK(length(text_norm)>0),
  content_hash   TEXT NOT NULL,
  expires_at     TEXT,
  forgotten_at   TEXT,
  project        TEXT,
  chat_session   TEXT,
  bots           TEXT,
  task           TEXT,
  cont           TEXT,
  kind           TEXT,
  status         TEXT,
  habit          TEXT,
  habit_explicit INTEGER NOT NULL DEFAULT 0,
  UNIQUE(path, memory_id, leaf_ord)
);

CREATE INDEX IF NOT EXISTS chunks_day ON chunks(timeline_day, memory_id, leaf_ord);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);

CREATE TABLE IF NOT EXISTS leaf_gets(
  chunk_id     TEXT PRIMARY KEY,
  memory_id   TEXT NOT NULL,
  get_count    INTEGER NOT NULL CHECK(get_count >= 1),
  last_get_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leaf_searches(
  chunk_id        TEXT PRIMARY KEY,
  memory_id      TEXT NOT NULL,
  search_count    INTEGER NOT NULL CHECK(search_count >= 1),
  last_search_at  TEXT NOT NULL
);
