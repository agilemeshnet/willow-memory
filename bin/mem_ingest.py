#!/usr/bin/env python3
"""Walk the memory directory and ingest every .md into memory.db.

The .md files stay the source of truth. This script is idempotent;
re-running it picks up changes by file mtime. The DB is a derived index.

Salience scoring (Hormma-style; weights influence the salience query):
- standing OR foundational: +10
- type = feedback:  +3
- type = project:   +2
- type = reference: +1
- type = bookend:   +1
- recency bump: +1/log(days_old + 2)
- weight column (manual override, default 1.0)

Re-run: bin/mem_ingest.py
"""
from __future__ import annotations
import hashlib, json, os, re, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

# Point at your memory directory with WILLOW_MEMORY_DIR, or edit the default.
MEMORY_DIR = Path(os.environ.get("WILLOW_MEMORY_DIR", Path.home() / ".willow-memory"))
DB = MEMORY_DIR / "memory.db"
SCHEMA = MEMORY_DIR / "memory_schema.sql"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:[\|#][^\]]*)?\]\]")
DATE_IN_NAME = re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?:[T_](\d{2})[-:](\d{2}))?")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Empty dict if no frontmatter."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_yaml, body = m.group(1), m.group(2)
    fm: dict = {}
    current_key = None
    for line in raw_yaml.splitlines():
        if not line.strip(): continue
        if line.startswith("  ") and current_key:
            # nested under metadata: etc.
            sub = line.strip().split(":", 1)
            if len(sub) == 2 and isinstance(fm.get(current_key), dict):
                fm[current_key][sub[0].strip()] = sub[1].strip()
            continue
        if ":" not in line: continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v == "":
            fm[k] = {}
            current_key = k
        else:
            fm[k] = v
            current_key = k
    return fm, body


def standing_foundational(body: str, description: str) -> tuple[bool, bool]:
    blob = (body + " " + (description or "")).lower()
    standing = "standing" in blob[:600] or "⚡ standing" in blob[:600] or "standing rule" in blob[:600]
    foundational = "foundational" in blob[:600]
    return standing, foundational


def parse_created_at(path: Path, fm: dict) -> str:
    """Best-effort timestamp extraction from filename or frontmatter."""
    m = DATE_IN_NAME.search(path.stem)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        hh = m.group(4) or "00"
        mm = m.group(5) or "00"
        return f"{y}-{mo}-{d}T{hh}:{mm}:00+00:00"
    if "ended_at" in fm:
        return fm["ended_at"]
    # fallback: file mtime
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA.read_text())
    return conn


def upsert_file(conn, path: Path) -> int:
    digest = sha256_of(path)
    size = path.stat().st_size
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("SELECT id, sha256 FROM memory_file WHERE path = ?", (str(path),)).fetchone()
    if row:
        if row[1] == digest:
            return row[0]
        conn.execute(
            "UPDATE memory_file SET sha256=?, size_bytes=?, mtime=?, indexed_at=? WHERE id=?",
            (digest, size, mtime, now, row[0]),
        )
        return row[0]
    cur = conn.execute(
        "INSERT INTO memory_file (path, filename, sha256, size_bytes, mtime, indexed_at) VALUES (?,?,?,?,?,?)",
        (str(path), path.name, digest, size, mtime, now),
    )
    return cur.lastrowid


def upsert_node(conn, file_id: int, path: Path, fm: dict, body: str) -> int | None:
    name = fm.get("name") or path.stem
    if not name:
        return None
    description = fm.get("description", "")
    type_ = (fm.get("metadata", {}) or {}).get("type") if isinstance(fm.get("metadata"), dict) else None
    if not type_:
        # try guess from filename prefix
        for t in ("feedback", "project", "reference", "user", "dream", "bookend"):
            if path.name.startswith(t):
                type_ = "bookend" if t == "dream" else t
                break
    standing, foundational = standing_foundational(body, description)
    created_at = parse_created_at(path, fm)
    row = conn.execute("SELECT id FROM memory_node WHERE name = ?", (name,)).fetchone()
    if row:
        conn.execute(
            """UPDATE memory_node SET file_id=?, description=?, type=?, body=?,
                                       standing=?, foundational=?, created_at=?
               WHERE id=?""",
            (file_id, description, type_, body, int(standing), int(foundational),
             created_at, row[0]),
        )
        return row[0]
    cur = conn.execute(
        """INSERT INTO memory_node
            (file_id, name, description, type, body, standing, foundational, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (file_id, name, description, type_, body, int(standing), int(foundational), created_at),
    )
    return cur.lastrowid


def upsert_links(conn, src_id: int, body: str):
    conn.execute("DELETE FROM memory_link WHERE src_id = ?", (src_id,))
    seen = set()
    for m in WIKILINK_RE.finditer(body):
        tgt = m.group(1).strip()
        if tgt in seen: continue
        seen.add(tgt)
        row = conn.execute("SELECT id FROM memory_node WHERE name = ?", (tgt,)).fetchone()
        dst_id = row[0] if row else None
        conn.execute(
            "INSERT OR IGNORE INTO memory_link (src_id, dst_name, dst_id) VALUES (?,?,?)",
            (src_id, tgt, dst_id),
        )


def resolve_pending_links(conn):
    """Pass 2: any link with dst_id NULL where the target now exists, resolve it."""
    conn.execute(
        """UPDATE memory_link
           SET dst_id = (SELECT id FROM memory_node WHERE name = memory_link.dst_name)
           WHERE dst_id IS NULL"""
    )


def main():
    if not SCHEMA.exists():
        print(f"missing schema: {SCHEMA}", file=sys.stderr); sys.exit(2)
    conn = init_db()
    files = sorted(MEMORY_DIR.glob("*.md"))
    inserted = 0
    skipped = 0
    for p in files:
        if p.name in ("MEMORY.md", "README.md", "PINNED.md"):
            skipped += 1
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:
            print(f"skip {p.name}: {e}", file=sys.stderr)
            continue
        fm, body = parse_frontmatter(text)
        file_id = upsert_file(conn, p)
        node_id = upsert_node(conn, file_id, p, fm, body)
        if node_id is None:
            continue
        upsert_links(conn, node_id, body)
        inserted += 1
    resolve_pending_links(conn)
    conn.commit()
    print(f"INGEST DONE: {inserted} nodes from {len(files)} files ({skipped} skipped: MEMORY/README/PINNED)")
    # tally
    for row in conn.execute("""
        SELECT type, count(*) FROM memory_node GROUP BY type ORDER BY count(*) DESC
    """):
        print(f"  {row[1]:>4}  type={row[0]}")
    row = conn.execute("SELECT count(*) FROM memory_link").fetchone()
    print(f"  {row[0]:>4} edges")
    row = conn.execute("SELECT count(*) FROM memory_node WHERE standing OR foundational").fetchone()
    print(f"  {row[0]:>4} standing-or-foundational")


if __name__ == "__main__":
    main()
