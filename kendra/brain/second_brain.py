"""Kendra's second brain: Karpathy's LLM-wiki pattern on plain files.

The SQLite store is her fast associative memory; this is her durable,
readable one. Three parts, mirroring the manifest architecture:

- ``raw/``   — immutable append-only JSONL of everything she experiences
               (turns, observations, research). Never edited, never deleted.
- ``wiki/``  — markdown concept pages compiled FROM raw by her own idle
               agent (see BrainConsolidator.compile_wiki). Pages carry
               ``[[slug]]`` cross-links and are safe to open in any editor.
- ``MANIFEST.md`` — the deterministic schema: what flows where, how pages
               are written, what may never happen (raw edits, safety talk).

Everything here is stdlib + files so the identical directory rides the
Pi's NVMe untouched. No service may write wiki pages except through
``upsert_page`` — that is what keeps compile idempotent and merges safe.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

WORD = re.compile(r"[a-z0-9']+")
# Words that match every page and carry no meaning for lookup scoring.
STOP = frozenset(
    "the a an and or of to in on for with is are was were be been do does did "
    "you your i my me we our it its this that what who when where how why "
    "kendra jonathan about have has had can could would should tell know".split()
)

MANIFEST_TEMPLATE = """# Kendra's Second Brain — Manifest

This file is the deterministic schema for Kendra's file-based knowledge
system. It states how experience becomes knowledge. Code follows this file;
when they disagree, fix the code.

## Layout

- `raw/YYYY-MM-DD.jsonl` — IMMUTABLE experience log. One JSON object per
  line: `{"ts", "kind", "content", "meta"}`. Kinds: `turn`, `observation`,
  `research`, `memory`. Appended by the brain service only. Never edited,
  never deleted, never compacted.
- `wiki/<slug>.md` — compiled concept pages. Front matter (`slug`, `title`,
  `updated`, `sources`), then one standalone fact per bullet with its date,
  then a `Links:` line of `[[slug]]` cross-references.
- `state.json` — the compile cursor: how many lines of each raw file have
  been compiled so far.

## The loop

1. **Ingest** — every conversation turn, ambient sight observation, and
   research result is appended raw and unedited the moment it happens.
2. **Compile** — when Kendra is idle, her consolidator reads uncompiled raw
   entries against this manifest and upserts wiki pages: facts must be
   short, declarative, self-contained sentences naming people explicitly
   (never bare pronouns); questions and speculation are not facts; newer
   facts supersede contradicted older ones on the same page.
3. **Execute** — every live turn's memory retrieval merges the two best
   wiki excerpts into her volatile context (provenance `wiki`), so anything
   she has ever learned is one file read away — offline, on any hardware.

## Boundaries

- Raw is append-only. A compile bug is fixed by recompiling, never by
  rewriting history.
- Wiki pages hold knowledge about the world and about Kendra's own build.
  Behavioral rules live in the charter; safety interlocks live in the
  reflex service and are not expressible here.
- This directory must remain portable: plain UTF-8 files, no absolute
  paths, no machine-specific references. It transplants to the Pi as-is.
"""


class SecondBrain:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.wiki_dir = self.root / "wiki"
        self.state_path = self.root / "state.json"
        self.manifest_path = self.root / "MANIFEST.md"
        self._lock = threading.Lock()
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            self.manifest_path.write_text(MANIFEST_TEMPLATE, encoding="utf-8")

    # ------------------------------------------------------------- ingest
    def ingest(self, kind: str, content: str, meta: dict[str, Any] | None = None) -> None:
        content = str(content or "").strip()
        if not content:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kind": str(kind),
            "content": content[:4000],
            "meta": meta or {},
        }
        day_file = self.raw_dir / (time.strftime("%Y-%m-%d") + ".jsonl")
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock, day_file.open("a", encoding="utf-8") as fh:
            fh.write(line)

    # ------------------------------------------------------------ compile
    def _load_state(self) -> dict[str, int]:
        try:
            return {str(k): int(v) for k, v in json.loads(self.state_path.read_text()).items()}
        except Exception:
            return {}

    def pending(self, limit: int = 60) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Uncompiled raw entries plus the cursor that would consume them."""
        state = self._load_state()
        cursor = dict(state)
        entries: list[dict[str, Any]] = []
        for day_file in sorted(self.raw_dir.glob("*.jsonl")):
            done = state.get(day_file.name, 0)
            lines = day_file.read_text(encoding="utf-8").splitlines()
            taken = done
            for raw_line in lines[done:]:
                if len(entries) >= limit:
                    break
                try:
                    entries.append(json.loads(raw_line))
                except Exception:
                    pass
                taken += 1
            cursor[day_file.name] = taken
            if len(entries) >= limit:
                break
        return entries, cursor

    def advance(self, cursor: dict[str, int]) -> None:
        with self._lock:
            self.state_path.write_text(json.dumps(cursor, indent=0), encoding="utf-8")

    def pending_count(self) -> int:
        state = self._load_state()
        total = 0
        for day_file in self.raw_dir.glob("*.jsonl"):
            done = state.get(day_file.name, 0)
            with day_file.open("r", encoding="utf-8") as fh:
                total += max(0, sum(1 for _ in fh) - done)
        return total

    # --------------------------------------------------------------- wiki
    @staticmethod
    def slugify(text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-")
        return slug[:60] or "misc"

    def _page_path(self, slug: str) -> Path:
        return self.wiki_dir / f"{self.slugify(slug)}.md"

    def upsert_page(
        self,
        slug: str,
        title: str,
        facts: list[str],
        links: list[str] | None = None,
        max_facts: int = 40,
    ) -> Path:
        """Merge facts into a page: keep existing, append genuinely new ones.

        Dedup is casefold word-set similarity, so a restated fact does not
        pile up. Oldest facts fall off the end past ``max_facts`` — the raw
        log still holds them forever.
        """
        path = self._page_path(slug)
        existing_facts: list[str] = []
        existing_links: list[str] = []
        if path.exists():
            body = path.read_text(encoding="utf-8")
            existing_facts = re.findall(r"^- (.+)$", body, re.M)
            existing_links = re.findall(r"\[\[([a-z0-9-]+)\]\]", body)

        def words(s: str) -> frozenset[str]:
            # The trailing date stamp is bookkeeping, not meaning — leaving
            # it in made every restated fact look novel.
            s = re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$", "", s)
            return frozenset(WORD.findall(s.casefold())) - STOP

        merged = list(existing_facts)
        for fact in facts:
            fact = str(fact).strip().rstrip("-• ").strip()
            if not fact or fact.endswith("?"):
                continue
            fw = words(fact)
            if not fw:
                continue
            duplicate = any(
                len(fw & words(old)) / max(1, len(fw | words(old))) > 0.7 for old in merged
            )
            if not duplicate:
                stamp = time.strftime("%Y-%m-%d")
                merged.append(f"{fact} ({stamp})" if not re.search(r"\(\d{4}-\d{2}-\d{2}\)$", fact) else fact)
        merged = merged[-max_facts:]

        all_links = sorted({self.slugify(lk) for lk in (existing_links + list(links or []))} - {self.slugify(slug)})
        front = (
            f"---\nslug: {self.slugify(slug)}\ntitle: {title.strip()}\n"
            f"updated: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\nsources: {len(merged)}\n---\n\n"
        )
        body = "\n".join(f"- {fact}" for fact in merged)
        tail = ("\n\nLinks: " + ", ".join(f"[[{lk}]]" for lk in all_links) + "\n") if all_links else "\n"
        with self._lock:
            path.write_text(front + body + tail, encoding="utf-8")
        return path

    def read_page(self, slug: str) -> str | None:
        path = self._page_path(slug)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def list_pages(self) -> list[str]:
        return sorted(p.stem for p in self.wiki_dir.glob("*.md"))

    # ------------------------------------------------------------- lookup
    def lookup(self, query: str, limit: int = 2, excerpt_chars: int = 500) -> list[dict[str, Any]]:
        """Fast file-based retrieval: no model, no index, just word overlap.

        Title hits weigh 3x body hits; a page must share at least two
        meaningful words with the query (or one title word) to qualify —
        the same spirit as the SQLite relevance gate.
        """
        q_words = frozenset(WORD.findall(str(query).casefold())) - STOP
        if not q_words:
            return []
        scored: list[tuple[float, str, str]] = []
        for path in self.wiki_dir.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            title_words = frozenset(WORD.findall(path.stem.replace("-", " "))) - STOP
            body_words = frozenset(WORD.findall(text.casefold())) - STOP
            title_hits = len(q_words & title_words)
            body_hits = len(q_words & body_words)
            if title_hits == 0 and body_hits < 2:
                continue
            scored.append((title_hits * 3.0 + body_hits, path.stem, text))
        scored.sort(reverse=True)
        results = []
        for score, slug, text in scored[:limit]:
            body = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
            results.append(
                {
                    "slug": slug,
                    "title": slug.replace("-", " "),
                    "excerpt": body.strip()[:excerpt_chars],
                    "score": round(score, 1),
                }
            )
        return results

    def stats(self) -> dict[str, Any]:
        raw_lines = 0
        for day_file in self.raw_dir.glob("*.jsonl"):
            with day_file.open("r", encoding="utf-8") as fh:
                raw_lines += sum(1 for _ in fh)
        return {
            "raw_entries": raw_lines,
            "pending_compile": self.pending_count(),
            "wiki_pages": len(self.list_pages()),
            "root": str(self.root),
        }
