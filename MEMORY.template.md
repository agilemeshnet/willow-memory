# MEMORY.md is a stub. The substrate is `memory.db`.

Auto-memory (Claude Code, or whatever wraps the agent) loads this file at boot. It used to hold the full index. It does not any more, because the DB and cwb.py already do that job.

## Where memory actually lives

- **`memory.db`** (this directory) is the structured index: 1000+ nodes typical, [[wikilink]] graph, FTS5 search, standing/foundational flags, weights, superseded_by chains. Populated by `bin/mem_ingest.py` (idempotent, mtime-driven).
- **The `.md` files** in this directory are the source of truth for every memory. Additive law: nothing ever deleted, only weight-reduced or superseded.
- **`icu/cwb.py`** (if you have it) builds the boot bundle. It queries the DB for salience-ranked foreground and reads only the `- ⚡ ON WAKE` lines below from this file. Everything else identity-shaping arrives through cwb.

## Verbs

- `bin/mem.py salience [--limit N] [--type feedback]` - top-ranked memories now
- `bin/mem.py search "phrase"` - FTS5 across the corpus
- `bin/mem.py foveate <name>` - full body of one memory
- `bin/mem.py walk <name> [--depth 2]` - wikilink neighbourhood
- `bin/mem.py recent [--days 14]` - what changed lately
- `bin/mem_ingest.py` - re-index after adding new .md files
- `ls` this dir - full corpus if any script fails

## On-wake action items

(cwb.py picks up any `- ⚡` line below and puts it in the boot bundle. Keep this sparse; only put things a fresh instance must see on cold-wake.)

## Provenance

- Rebuild manual and full pattern at https://github.com/agilemeshnet/willow-memory
