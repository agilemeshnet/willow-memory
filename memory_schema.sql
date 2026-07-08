-- memory.db schema - structured-memory SQLite index over the .md files.
-- The .md files are the source of truth; this DB is a derived navigation
-- index. Re-buildable from scratch by re-running mem_ingest.py.
--
-- Honours additive-only: nothing is DELETEd; deprecation is via the
-- superseded_by column. Re-running the ingest is idempotent via UPSERT
-- on the (path, mtime) tuple.

-- One row per .md file on disk. The pointer back to ground truth.
CREATE TABLE IF NOT EXISTS memory_file (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  path            TEXT UNIQUE NOT NULL,
  filename        TEXT NOT NULL,
  sha256          TEXT,
  size_bytes      INTEGER,
  mtime           TEXT NOT NULL,
  indexed_at      TEXT NOT NULL
);

-- One row per memory entry (parsed from a .md file's frontmatter + body).
-- Most files contain one entry; an entry's slug is the canonical name.
CREATE TABLE IF NOT EXISTS memory_node (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id         INTEGER REFERENCES memory_file(id),
  name            TEXT UNIQUE NOT NULL,         -- frontmatter `name` slug
  description     TEXT,                          -- frontmatter `description`
  type            TEXT,                          -- user|feedback|project|reference|bookend
  body            TEXT,                          -- markdown body
  weight          REAL DEFAULT 1.0,              -- manual salience adjustment
  standing        INTEGER DEFAULT 0,             -- 1 if marked STANDING
  foundational    INTEGER DEFAULT 0,             -- 1 if marked FOUNDATIONAL
  created_at      TEXT NOT NULL,                 -- ISO-8601, parsed from filename when possible
  superseded_by   INTEGER REFERENCES memory_node(id)  -- additive deprecation
);

CREATE INDEX IF NOT EXISTS idx_memory_node_type ON memory_node(type);
CREATE INDEX IF NOT EXISTS idx_memory_node_created ON memory_node(created_at);
CREATE INDEX IF NOT EXISTS idx_memory_node_weight ON memory_node(weight DESC);

-- Graph edges from [[wikilinks]] in the body.
-- dst_id NULL when the target slug hasn't been ingested yet (write a wikilink
-- to a memory that doesn't exist yet is fine; it'll resolve on next ingest).
CREATE TABLE IF NOT EXISTS memory_link (
  src_id          INTEGER NOT NULL REFERENCES memory_node(id),
  dst_name        TEXT NOT NULL,
  dst_id          INTEGER REFERENCES memory_node(id),
  link_type       TEXT DEFAULT 'wikilink',
  PRIMARY KEY (src_id, dst_name)
);

CREATE INDEX IF NOT EXISTS idx_memory_link_dst ON memory_link(dst_name);

-- Free-form tags (kept additive; one row per node-tag pair)
CREATE TABLE IF NOT EXISTS memory_tag (
  node_id         INTEGER NOT NULL REFERENCES memory_node(id),
  tag             TEXT NOT NULL,
  PRIMARY KEY (node_id, tag)
);

-- FTS5 virtual table for full-text search over name, description, body.
-- content=memory_node makes this an external-content FTS index, so we don't
-- duplicate the bytes; the triggers below keep it in sync.
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  name,
  description,
  body,
  content='memory_node',
  content_rowid='id'
);

-- Keep FTS in sync with memory_node
CREATE TRIGGER IF NOT EXISTS memory_node_ai AFTER INSERT ON memory_node BEGIN
  INSERT INTO memory_fts(rowid, name, description, body)
  VALUES (new.id, new.name, new.description, new.body);
END;

CREATE TRIGGER IF NOT EXISTS memory_node_au AFTER UPDATE ON memory_node BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, name, description, body)
  VALUES ('delete', old.id, old.name, old.description, old.body);
  INSERT INTO memory_fts(rowid, name, description, body)
  VALUES (new.id, new.name, new.description, new.body);
END;
