# Kendra Brain — Native Persistent Cognition

Kendra Brain is Kendra's durable local cognitive subsystem. It is not a note-taking workflow and it does not require an external knowledge application.

## What it stores

The canonical database stores:

- **episodes** — conversations and mission experiences
- **facts** — durable claims with provenance and confidence
- **preferences** — user-stated preferences and stable interaction patterns
- **relationships / people** — named people, relationship metadata, consent notes
- **places** — named locations and optional marker identifiers
- **interests** — weighted topics that strengthen and decay over time
- **goals** — persistent open objectives with priorities
- **open questions** — unresolved questions Kendra can revisit
- **self-model** — identity/runtime statements such as name, role, knowledge policy
- **reflections** — explicit synthesis records tied to the memories that justified them
- **sessions and turns** — conversation continuity
- **cognitive events** — append-only operational events for debugging and audit

The default database is `data/kendra-brain.db` in development. Production should place it in `/var/lib/kendra/kendra-brain.db` so application A/B updates cannot replace it.

## Provenance

Every memory carries one of these provenance labels:

- `observed`
- `user_stated`
- `researched`
- `inferred`
- `system`

The label is part of memory, not decoration. The planner receives provenance in retrieved context and is instructed not to flatten these categories into equal certainty.

## Corrections

A correction does not delete the previous record. Kendra creates a replacement memory and marks the earlier memory inactive with `superseded_by` pointing to the new record. The history remains inspectable.

Use:

```bash
kendra brain search "query"
kendra brain correct MEMORY_ID "corrected content" --reason "why it changed"
```

## Retrieval

The zero-download baseline combines:

1. SQLite FTS5 lexical retrieval
2. deterministic local hashing vectors
3. salience
4. confidence

This means the brain is usable immediately, before any embedding model is installed.

For higher-quality semantic retrieval, place a local MiniLM model at the configured path and set:

```yaml
brain:
  embedding:
    provider: sentence_transformers
```

The provider is loaded with `local_files_only=True`; runtime retrieval does not download models.

## Automatic consolidation

The raw conversation episode is always stored first. If the local LLM is available, `BrainConsolidator` then extracts only conservative durable candidates. It is explicitly instructed not to store greetings, transient instructions, or Kendra's own unsupported speculation as facts.

If consolidation fails, the episode remains intact. Memory persistence therefore does not depend on a successful extraction pass.

## Backups

Canonical backup:

```bash
kendra brain backup
```

This uses SQLite's online backup API and creates a consistent `.sqlite3` snapshot in `exports/brain-backups/`.

Portable inspection export:

```bash
kendra brain export-jsonl
```

This writes records to JSONL in `exports/brain-jsonl/`. Binary embedding blobs are base64 encoded. JSONL is an interchange/export format; SQLite remains the canonical runtime state.

Copy database backups to a second computer or encrypted backup medium periodically.

## Useful commands

```bash
kendra brain stats
kendra brain self
kendra brain remember "A durable fact" --provenance user_stated
kendra brain search "durable fact"
kendra brain backup
kendra brain export-jsonl
```

## Failure behavior

- Brain unavailable during startup: agent service should be treated as degraded/unready when `agent.require_brain` is true.
- Embedding model missing: switch back to `hashing`; do not allow runtime download.
- Database corruption: stop Kendra services, preserve the damaged file, restore the latest SQLite backup, then run integrity checks.
- Conflicting memory: store a correction/superseding record rather than deleting history.

## Privacy

The brain database is local data and can contain personal information. Production permissions should restrict `/var/lib/kendra` to the Kendra service account. Do not commit the live database, face embeddings, recipient configs, or credentials to Git.
