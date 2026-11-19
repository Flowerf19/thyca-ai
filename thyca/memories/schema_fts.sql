-- FTS5 index + sync triggers for chunks (shared by greenfield init and rebuild_fts_v10).
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text_raw,
  memory_title,
  text_norm,
  content='chunks', content_rowid='row_id',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text_raw, memory_title, text_norm)
  VALUES (new.row_id, new.text_raw, new.memory_title, new.text_norm);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text_raw, memory_title, text_norm)
  VALUES ('delete', old.row_id, old.text_raw, old.memory_title, old.text_norm);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text_raw, memory_title, text_norm)
  VALUES ('delete', old.row_id, old.text_raw, old.memory_title, old.text_norm);
  INSERT INTO chunks_fts(rowid, text_raw, memory_title, text_norm)
  VALUES (new.row_id, new.text_raw, new.memory_title, new.text_norm);
END;
