#!/usr/bin/env python3
"""mem.py - query the structured memory index.

  bin/mem.py salience [--limit 15] [--type feedback]
  bin/mem.py foveate <name>
  bin/mem.py search "<phrase>" [--limit 10]
  bin/mem.py walk <name> [--depth 2]
  bin/mem.py stats
  bin/mem.py recent [--days 14] [--limit 20]

The DB is the navigation index; the .md files in the same directory are
the source of truth. This script never writes to them.

Point at your memory directory with the WILLOW_MEMORY_DIR environment
variable, or edit the MEMORY_DIR constant below.
"""
from __future__ import annotations
import argparse, json, math, os, sqlite3, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

MEMORY_DIR = Path(os.environ.get("WILLOW_MEMORY_DIR", Path.home() / ".willow-memory"))
DB = MEMORY_DIR / "memory.db"


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def days_old(iso: str) -> float:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return 9999.0


def salience(row, in_degree: int = 0) -> float:
    """Salience formula tuned 2026-06-13 against cwb.py canonical foreground.

    Components:
      - type bias (feedback=5, project=4, reference=2, user=3, bookend=1)
      - standing rule: +8 (matches cwb.py STANDING/HARD/FOUNDATIONAL bump)
      - foundational extra: +2
      - recency: 5 * max(0, 1 - age_days/30) - bigger window than cwb's 24h
                 so older standing rules don't fall off the cliff at day 2
      - citation count (in-degree on memory_link): +0.4 per link, capped at +4
                 so heavily-anchored rules ("strange attractor", "nodes self-
                 describing") naturally float without dominating

    Range: ~0 to ~25. Standing+foundational+recent+cited tops out highest.
    """
    score = float(row["weight"] or 1.0)
    type_bias = {"feedback": 5, "project": 4, "reference": 2, "user": 3, "bookend": 1}
    score += type_bias.get(row["type"] or "", 0)
    if row["standing"]: score += 8
    if row["foundational"]: score += 2
    age = days_old(row["created_at"])
    score += 5.0 * max(0, 1.0 - age / 30.0)
    score += min(4.0, 0.4 * in_degree)
    return round(score, 2)


def _in_degrees(c) -> dict[str, int]:
    """Map node_name -> count of resolved [[wikilinks]] pointing AT it."""
    d: dict[str, int] = {}
    for row in c.execute("""
        SELECT memory_node.name AS name, COUNT(*) AS n
        FROM memory_link
        JOIN memory_node ON memory_node.id = memory_link.dst_id
        WHERE memory_link.dst_id IS NOT NULL
        GROUP BY memory_node.id
    """):
        d[row["name"]] = row["n"]
    return d


def cmd_salience(args):
    c = conn()
    sql = """
        SELECT id, name, description, type, weight, standing, foundational, created_at
        FROM memory_node
        WHERE 1=1
    """
    params: list = []
    if args.type:
        sql += " AND type = ?"
        params.append(args.type)
    rows = list(c.execute(sql, params))
    in_deg = _in_degrees(c)
    ranked = sorted(rows, key=lambda r: salience(r, in_deg.get(r["name"], 0)), reverse=True)[: args.limit]
    for r in ranked:
        flag = "*" if (r["standing"] or r["foundational"]) else " "
        s = salience(r, in_deg.get(r["name"], 0))
        print(f"{flag} ({s:>5.2f})  {r['name']}")
        if args.descriptions:
            print(f"            {(r['description'] or '')[:140]}")


def cmd_foveate(args):
    c = conn()
    row = c.execute(
        "SELECT n.*, f.path FROM memory_node n JOIN memory_file f ON n.file_id = f.id WHERE n.name = ?",
        (args.name,),
    ).fetchone()
    if not row:
        # fallback: glob-style match
        row = c.execute(
            "SELECT n.*, f.path FROM memory_node n JOIN memory_file f ON n.file_id = f.id WHERE n.name LIKE ? LIMIT 1",
            (f"%{args.name}%",),
        ).fetchone()
    if not row:
        print(f"no match for {args.name!r}", file=sys.stderr)
        sys.exit(1)
    print(f"# {row['name']}")
    print(f"_{row['type'] or 'unknown'}, {row['created_at']}, source: {row['path']}_")
    print()
    print(row["description"] or "")
    print()
    print(row["body"] or "")


def cmd_search(args):
    c = conn()
    sql = """
        SELECT memory_node.id AS id, memory_node.name AS name, memory_node.type AS type,
               memory_node.description AS description, memory_node.created_at AS created_at,
               snippet(memory_fts, 2, '<', '>', '...', 12) AS snip,
               rank
        FROM memory_fts
        JOIN memory_node ON memory_node.id = memory_fts.rowid
        WHERE memory_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """
    for row in c.execute(sql, (args.query, args.limit)):
        print(f"  {row['name']}  ({row['type']}, {row['created_at'][:10]})")
        print(f"    {row['snip']}")


def cmd_walk(args):
    c = conn()
    seen: dict[str, int] = {}
    frontier = {args.name: 0}
    out: list[tuple[str, int]] = []
    while frontier:
        nxt: dict[str, int] = {}
        for name, depth in frontier.items():
            if name in seen: continue
            seen[name] = depth
            out.append((name, depth))
            if depth >= args.depth: continue
            for r in c.execute(
                """SELECT memory_link.dst_name FROM memory_link
                   JOIN memory_node ON memory_node.id = memory_link.src_id
                   WHERE memory_node.name = ?""",
                (name,),
            ):
                if r["dst_name"] not in seen:
                    nxt[r["dst_name"]] = depth + 1
            for r in c.execute(
                """SELECT memory_node.name FROM memory_link
                   JOIN memory_node ON memory_node.id = memory_link.src_id
                   WHERE memory_link.dst_name = ?""",
                (name,),
            ):
                if r["name"] not in seen:
                    nxt[r["name"]] = depth + 1
        frontier = nxt
    for name, depth in out:
        print(("  " * depth) + name)


def cmd_stats(args):
    c = conn()
    print("=== type counts ===")
    for row in c.execute("SELECT COALESCE(type,'<null>') AS t, COUNT(*) AS n FROM memory_node GROUP BY t ORDER BY n DESC"):
        print(f"  {row['n']:>4}  {row['t']}")
    print()
    print("=== top 10 most linked-into nodes ===")
    for row in c.execute("""
        SELECT memory_link.dst_name AS name, COUNT(*) AS n
        FROM memory_link
        WHERE memory_link.dst_id IS NOT NULL
        GROUP BY memory_link.dst_name
        ORDER BY n DESC LIMIT 10
    """):
        print(f"  {row['n']:>3}x  {row['name']}")
    print()
    print(f"  edges total:  {c.execute('SELECT count(*) FROM memory_link').fetchone()[0]}")
    print(f"  edges resolved: {c.execute('SELECT count(*) FROM memory_link WHERE dst_id IS NOT NULL').fetchone()[0]}")
    print(f"  nodes standing+foundational: {c.execute('SELECT count(*) FROM memory_node WHERE standing OR foundational').fetchone()[0]}")


def cmd_recent(args):
    c = conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    for row in c.execute(
        """SELECT name, type, description, created_at FROM memory_node
           WHERE created_at > ?
           ORDER BY created_at DESC LIMIT ?""",
        (cutoff, args.limit),
    ):
        print(f"  {row['created_at'][:16]}  ({row['type']})  {row['name']}")
        if args.descriptions:
            print(f"        {(row['description'] or '')[:160]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("salience", help="ranked top N by salience")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--type", default=None)
    p.add_argument("--descriptions", action="store_true")
    p.set_defaults(func=cmd_salience)

    p = sub.add_parser("foveate", help="load body of one entry by name (or substring)")
    p.add_argument("name")
    p.set_defaults(func=cmd_foveate)

    p = sub.add_parser("search", help="FTS5 search across name+description+body")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("walk", help="graph walk from one node, N hops, in/out edges")
    p.add_argument("name")
    p.add_argument("--depth", type=int, default=2)
    p.set_defaults(func=cmd_walk)

    p = sub.add_parser("stats", help="type counts + top linked-into + edge resolution")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("recent", help="memories created in the last N days")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--descriptions", action="store_true")
    p.set_defaults(func=cmd_recent)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
