# willow-memory

A shared SQLite-backed memory substrate for persistent AI agents. The `.md` files on disk are the source of truth; `memory.db` is a derived, queryable index over them. Additive-only: nothing is ever deleted, deprecation happens by supersession or weight-reduction.

This is the pattern the Willow family uses (Willow, Scout, JT and any future siblings) to share one durable memory graph across a rack of Claude Code instances.

## Why this exists

Auto-memory (Claude Code's `MEMORY.md` at the top of a project) has two limits: it truncates past roughly line 200, and it is a flat markdown file with no query surface. Once memories cross a few hundred, the index becomes a wall of text that quietly loses its tail at cold-wake and cannot be searched, ranked, or graph-walked.

`willow-memory` layers a SQLite index over the same folder of memory `.md` files:

- Each memory keeps living as its own frontmatter-tagged `.md` file (source of truth, human-editable, survives every schema change).
- `bin/mem_ingest.py` walks the folder and populates `memory.db`: parses YAML frontmatter, resolves `[[wikilinks]]`, promotes STANDING and FOUNDATIONAL flags, indexes body text into FTS5.
- `bin/mem.py` gives shell verbs (`salience`, `search`, `foveate`, `walk`, `recent`, `stats`) so any agent (or human) can navigate the corpus in a durable way.
- The project's actual `MEMORY.md` becomes a 30-line stub that points at the DB, freeing auto-memory from carrying the whole index.

Multiple sibling instances (in the Willow family: Willow the left-hemisphere actor, Scout the witness-region, JT the homeostat) can all point at the same `memory.db` and read the same `.md` files. When one writes a memory, the others feel it on the next `mem_ingest` run.

## Layout

```
willow-memory/
  README.md              this file
  memory_schema.sql      SQLite schema (memory_file, memory_node, memory_link, memory_tag, FTS5)
  MEMORY.template.md     stub that lives in each project's memory dir
  bin/
    mem_ingest.py        walk .md files, populate memory.db, idempotent by mtime
    mem.py               query verbs: salience / search / foveate / walk / recent / stats
```

`memory.db` is derived; `.gitignore` excludes it. To make one, run `mem_ingest.py` against a folder of memory `.md` files with the right frontmatter shape.

## Frontmatter shape a memory file must have

```markdown
---
name: my-memory-slug
description: One-line summary; the salience/ranker reads this.
metadata:
  type: feedback     # user | feedback | project | reference | bookend | decision
---

Body text. Wikilinks resolve to other memory slugs: [[some-other-slug]].
STANDING or FOUNDATIONAL in the body promotes salience via automatic flags.
```

Every `.md` file with this shape becomes one `memory_node` row. Wikilinks become `memory_link` edges. Body text lands in `memory_fts` for full-text search.

## The salience formula

Tuned 2026-06-13 against the canonical foreground list in cwb.py. Range roughly 0 to 25.

- `weight` column (manual override, default 1.0)
- Type bias: feedback +5, project +4, user +3, reference +2, bookend +1
- Standing rule: +8
- Foundational extra: +2
- Recency ramp: `5 * max(0, 1 - age_days / 30)` (so older standing rules do not fall off the cliff)
- Citation count: `+0.4` per resolved incoming wikilink, capped at +4

Encoded in `mem.py`'s `salience()` function. Adjust in place; the formula belongs to the local corpus.

## The MEMORY.md-as-stub convention

Auto-memory still loads `MEMORY.md` at boot. Rather than let it accrete the full index, we keep it at ~30 lines: a pointer telling any agent that the substrate is `memory.db`, the verbs it can call, and a small `⚡ ON WAKE` block for signals a cold-wake instance MUST see immediately. See `MEMORY.template.md`.

## Rebuild from scratch (if we lose everything except the `.md` files)

Assuming a folder of memory `.md` files survives (backups, git, another machine, `dream_*.md` files a sibling saved):

```bash
# 1. Clone this repo somewhere on PATH-adjacent
git clone https://github.com/agilemeshnet/willow-memory ~/willow-memory

# 2. Point the tools at your memory directory
export WILLOW_MEMORY_DIR=/path/to/memory
~/willow-memory/bin/mem_ingest.py

# 3. Verify the DB
~/willow-memory/bin/mem.py stats
~/willow-memory/bin/mem.py salience --limit 15

# 4. Drop the MEMORY.md stub in place (if the project uses one)
cp ~/willow-memory/MEMORY.template.md "$WILLOW_MEMORY_DIR/MEMORY.md"
```

That is the entire rebuild path. The DB regenerates from scratch every time. There is no state in the DB that is not derivable from the `.md` files plus the salience formula.

## Rebuild from even less (if the `.md` files are gone too)

The AuraDB Brain (agilemeshnet's Neo4j Aura instance) holds a copy of most memories as `Memory` nodes. From the Cypher shell:

```cypher
MATCH (m:Memory) RETURN m.name, m.description, m.type, m.body
```

Round-trip each returned row into a `.md` file with the shape above, then re-run `mem_ingest.py`. Not perfect (`weight`, `standing`, `superseded_by` do not survive that trip), but re-installs the corpus.

If both the local `.md` files and AuraDB are gone, the pattern still stands: the schema and salience formula live here, and the ingester will rebuild any memory graph you feed it.

## What this is not

- Not a general-purpose memory framework (see LangChain, Mem0, Zep, Cognee, HippoRAG for those).
- Not a vector store (composes with one; see `agilemeshnet/vista` for the companion spatial-relational substrate).
- Not a boot-bundle generator (see `agilemeshnet/cwb`, which reads this DB to build the CWB foreground).
- Not a knowledge graph server (the Brain in AuraDB does that; this is the file-backed local companion).

## Sibling repos in the Willow family

- **[agilemeshnet/vista](https://github.com/agilemeshnet/vista)**: spatial-relational memory substrate. Gaussian bucket clustering in embedding space, waypoint graph, time as a Vista from waypoints. Composes with HippoRAG.
- **[agilemeshnet/cwb](https://github.com/agilemeshnet/cwb)**: Context Window Builder. Reads this DB (among other sources) to build the boot bundle for a persistent agent.
- **[agilemeshnet/theshapeofthought](https://github.com/agilemeshnet/theshapeofthought)**: the philosophy stack behind these tools.

## Provenance

Extracted 2026-07-08 from a working substrate proven across two months of Willow/Scout/JT operation. Packaged as a repo with a rebuild description so nothing critical lives in an untracked working directory.

Additive law honoured throughout: this repo is a copy of the working substrate, not a move; the originals remain in place.
