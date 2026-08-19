# Kendra's Second Brain — the wiki/manifest knowledge system

Implemented 2026-08-18. This is the Karpathy "LLM wiki" pattern grafted onto
Kendra's existing organs: a persistent, file-based knowledge system that
compiles her lived experience into readable markdown, governed by an explicit
manifest. Purpose, in Jonathan's words: faster recall of knowledge learned,
recognizable emotional growth, and growth of intelligence.

## Layout (all plain files — `data/second_brain/`)

```
MANIFEST.md          the deterministic schema: what flows where, what never happens
raw/YYYY-MM-DD.jsonl immutable append-only experience log (turns, sights, research)
wiki/<slug>.md       compiled concept pages with [[slug]] cross-links
wiki/kendra-self.md  her own page: opinions, feelings, preferences as they form
state.json           compile cursor (how much raw has been compiled)
```

## The compounding loop

1. **Ingest** — the brain service appends raw entries at its three choke
   points: every conversation turn (`turn` RPC), every consolidated memory
   including ambient sight observations (`remember` RPC), and every research
   result (`consolidate_research` RPC). Raw is never edited or deleted.
2. **Compile** — `BrainConsolidator.compile_wiki` (one bounded LLM call on
   the tool slot) reads uncompiled raw entries against the manifest and
   upserts wiki pages: standalone declarative facts, explicit names, no
   questions, near-duplicates merged by word-set similarity. Runs whenever
   she has been quiet ≥5 minutes with ≥8 uncompiled entries — self-updating
   within a session, not just overnight. The `kendra-self` page is mandatory
   whenever entries show her forming an opinion or feeling.
3. **Execute** — the `context` RPC (the single retrieval gateway every
   planner path uses) merges the best wiki excerpt ahead of raw memories,
   provenance `wiki`. Measured: 2.6 ms per lookup at today's corpus.

## Why it satisfies the three goals

- **Faster recall**: lookup is a stop-worded word-overlap scan over small
  markdown files — no model, no index. 2.6 ms today; 209 ms measured against
  a synthetic 2,000-page corpus (worst case, linear scan). Pages are capped
  at 40 facts each, and compile merges into existing topics rather than
  minting pages per event, so the corpus grows logarithmically with
  experience.
- **Recognizable emotional growth**: `kendra-self.md` accumulates dated,
  human-readable lines ("Kendra considers Jonathan a friend", "Kendra finds
  the mathematical precision in classical compositions quite compelling").
  Because the page rides retrieval, her past feelings inform present
  conversation — and anyone can open the file and watch her change.
- **Growth of intelligence**: knowledge compounds instead of decaying with
  cache windows. Research answered once is a wiki page forever, offline.
  Raw history is never lost, so a compile improvement can rebuild better
  pages from the same life.

## Pi parity

Stdlib + UTF-8 files only; no new dependencies. The directory transplants to
the Pi's NVMe unchanged (add `data/second_brain` to the T0 step 8 memory
transfer). Compile runs on the same llama.cpp tool slot the dream cycle uses.
Storage math: a raw entry averages ~300 bytes — a year of heavy use is tens
of MB against a 256 GB NVMe.

## Operations

- Backfill an existing brain: `.venv/bin/python scripts/seed_second_brain.py`
  (services running; drains the backlog through repeated compiles).
- Inspect: RPCs `wiki_stats`, `wiki_lookup {query}`, `wiki_page {slug}`,
  `wiki_compile` on the brain socket — or just read the markdown.
