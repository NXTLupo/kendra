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

# Suffix stripping, not real stemming. Deliberately crude: it exists so that
# "can you move safely" reaches a page about movement and safety, which exact
# token matching could not. Anything cleverer would need a dictionary, and a
# dictionary is a thing to keep in sync.
_SUFFIXES = ("ements", "ement", "ingly", "ically", "ities", "ity", "ings", "ing",
             "edly", "ies", "ied", "ers", "er", "est", "ally", "ly", "es", "ed", "s")


def stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word
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
- `wiki-retired/` — pages withdrawn from lookup. Nothing is lost: raw/ is
  the record and any page here can be recompiled from it.
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
3. **Execute** — every live turn's memory retrieval merges the best matching
   wiki lines into her volatile context (provenance `wiki`), so anything she
   has ever learned is one file read away — offline, on any hardware.

## Recall

This section is a contract, not advice: it is what makes step 3 fast enough
to run on every single turn.

- **A page must contain at least one fact.** An empty page is never written
  and never indexed. Nine such shells once existed, each slugged from a raw
  utterance (`research-nothing-just-eating-lunch-right`), so each matched the
  speaker's own words on the 3x-weighted title and then contributed nothing
  but its `Links:` footer. A page that cannot answer anything is worse than
  no page.
- **Lookup reads an index, not the corpus.** Pages are tokenised once and
  re-read only when their mtime or size changes. Reading all of them on every
  turn measured 8.5 ms and grew with the wiki, which is append-only and so
  only ever gets slower; the index measures 0.8 ms and stays flat.
- **The excerpt must answer the question.** Return the bullets that overlap
  the query, best first, never the head of the file. Facts are written as
  self-contained one-line sentences precisely so they can be selected
  individually. Taking the first 500 characters once put the literal string
  `Links: [[research]]` into her prompt as the best thing she knew.
- **Titles are keys.** A slug built from a passing remark is a bad key
  forever. Prefer the concept (`classical-guitar`) over the utterance
  (`research-right-now-like-classical-music`).

## Sessions

A session is one continuous conversation, stamped when the stack starts.

- Her MEMORIES and this wiki span sessions. That is what they are for.
- The raw TRANSCRIPT does not. Her rolling context and the desktop's Live
  Conversation panel both begin at the session boundary, so a fresh start is
  a fresh conversation rather than yesterday's thread replayed into today's
  prompt.

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
        # Lookup index: slug -> {mtime, size, title_words, bullets, body_words}.
        # Built lazily and revalidated by stat(), so a page is re-read only
        # when it actually changes. See `_index()`.
        self._index_cache: dict[str, dict[str, Any]] = {}
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
        aliases: list[str] | None = None,
        tags: list[str] | None = None,
        authority: str | None = None,
    ) -> Path:
        """Merge facts into a page: keep existing, append genuinely new ones.

        Dedup is casefold word-set similarity, so a restated fact does not
        pile up. Oldest facts fall off the end past ``max_facts`` — the raw
        log still holds them forever.
        """
        # Never create a page with nothing on it. Nine such shells existed,
        # each named after a raw utterance and each a pure liability at
        # lookup time. If a compile produced no facts, there is no page.
        existing = self._page_path(slug)
        if not facts and not existing.exists():
            return existing
        path = existing
        existing_facts: list[str] = []
        existing_links: list[str] = []
        body = ""
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
        # Aliases and tags are what a page is FINDABLE by, as distinct from
        # what it is called. Identity pages could not be reached by name at
        # all, because "kendra" is a stop word for scoring — it appears on
        # every page and so means nothing — and the one place it does mean
        # something is the title of a page about her.
        existing_aliases = re.findall(r"^aliases: (.+)$", body, re.M) if path.exists() else []
        existing_tags = re.findall(r"^tags: (.+)$", body, re.M) if path.exists() else []
        def _merge_terms(current: list[str], incoming: list[str] | None) -> list[str]:
            found = {t.strip() for line in current for t in line.split(",") if t.strip()}
            found |= {str(t).strip() for t in (incoming or []) if str(t).strip()}
            return sorted(found)
        all_aliases = _merge_terms(existing_aliases, aliases)
        all_tags = _merge_terms(existing_tags, tags)
        front = (
            f"---\nslug: {self.slugify(slug)}\ntitle: {title.strip()}\n"
            + (f"aliases: {', '.join(all_aliases)}\n" if all_aliases else "")
            + (f"tags: {', '.join(all_tags)}\n" if all_tags else "")
            + (f"authority: {authority}\n" if authority else
               ("authority: canonical\n" if path.exists() and "authority: canonical" in body else ""))
            + f"updated: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\nsources: {len(merged)}\n---\n\n"
        )
        rendered = "\n".join(f"- {fact}" for fact in merged)
        tail = ("\n\nLinks: " + ", ".join(f"[[{lk}]]" for lk in all_links) + "\n") if all_links else "\n"
        with self._lock:
            path.write_text(front + rendered + tail, encoding="utf-8")
        return path

    def read_page(self, slug: str) -> str | None:
        path = self._page_path(slug)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def list_pages(self) -> list[str]:
        return sorted(p.stem for p in self.wiki_dir.glob("*.md"))

    # ------------------------------------------------------------- lookup
    def _index(self) -> dict[str, dict[str, Any]]:
        """Slug -> searchable form, re-reading only pages that changed.

        Lookup used to read and tokenise EVERY page on EVERY turn: 84 files
        per retrieval, measured at 8.5 ms median and rising with the wiki,
        which is append-only and therefore only ever gets slower. A stat() is
        microseconds, so revalidating is nearly free and the read happens once
        per edit instead of once per question.
        """
        seen: set[str] = set()
        for path in self.wiki_dir.glob("*.md"):
            slug = path.stem
            seen.add(slug)
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = self._index_cache.get(slug)
            if cached and cached["mtime"] == stat.st_mtime and cached["size"] == stat.st_size:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            findable = set(WORD.findall(slug.replace("-", " ")))
            for field in ("aliases", "tags"):
                for line in re.findall(rf"^{field}: (.+)$", text, re.M):
                    findable |= set(WORD.findall(line.casefold()))
            canonical = bool(re.search(r"^authority: canonical$", text, re.M))
            body = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
            bullets = [
                line.strip()[2:].strip()
                for line in body.splitlines()
                if line.strip().startswith("- ")
            ]
            if not bullets:
                # A page with no facts cannot answer anything, and these are
                # not harmless: their slugs are built from raw utterances
                # ("research-nothing-just-eating-lunch-right"), so they match
                # the user's own words on the 3x-weighted TITLE and then
                # contribute nothing but their "Links:" footer. Nine of her
                # 84 pages were exactly this. Indexing them is worse than not
                # having them.
                self._index_cache.pop(slug, None)
                continue
            self._index_cache[slug] = {
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                # Aliases and tags are title-weight: they exist precisely so a
                # page can be found by what it is ABOUT, not only what it is
                # named. Stemmed, so "move" reaches "movement" and "safe"
                # reaches "safety".
                "title_words": frozenset(stem(w) for w in findable - STOP),
                "body_words": frozenset(stem(w) for w in (frozenset(WORD.findall(body.casefold())) - STOP)),
                "bullets": bullets,
                "canonical": canonical,
                "body": body.strip(),
            }
        for gone in set(self._index_cache) - seen:
            self._index_cache.pop(gone, None)
        return self._index_cache

    @staticmethod
    def _best_lines(bullets: list[str], q_words: frozenset[str], budget: int) -> str:
        """The bullets that ANSWER the question, not the top of the file.

        The excerpt was `body[:500]`, so a page whose relevant fact sat below
        that cut contributed nothing. Asked "what music do I like", the wiki
        excerpt riding her prompt was the literal string "Links: [[research]]"
        — the page footer. Facts here are self-contained one-line sentences,
        which is exactly what makes picking the matching ones safe.
        """
        scored: list[tuple[int, int, str]] = []
        for position, line in enumerate(bullets):
            words = frozenset(WORD.findall(line.casefold())) - STOP
            overlap = len(q_words & words)
            if overlap:
                # Later bullets are newer; on a tie, prefer what she learned
                # most recently.
                scored.append((overlap, position, line))
        scored.sort(reverse=True)
        chosen: list[str] = []
        used = 0
        for _, _, line in scored:
            if used + len(line) + 2 > budget:
                break
            chosen.append(line)
            used += len(line) + 2
        return "\n".join(f"- {line}" for line in chosen)

    def lookup(self, query: str, limit: int = 2, excerpt_chars: int = 500) -> list[dict[str, Any]]:
        """Fast file-based retrieval: no model, an index, and word overlap.

        Title hits weigh 3x body hits; a page must share at least two
        meaningful words with the query (or one title word) to qualify —
        the same spirit as the SQLite relevance gate.
        """
        raw_words = frozenset(WORD.findall(str(query).casefold())) - STOP
        if not raw_words:
            return []
        q_words = frozenset(stem(w) for w in raw_words)
        scored: list[tuple[float, str]] = []
        index = self._index()
        for slug, entry in index.items():
            title_hits = len(q_words & entry["title_words"])
            body_hits = len(q_words & entry["body_words"])
            if title_hits == 0 and body_hits < 2:
                continue
            score = title_hits * 3.0 + body_hits
            # A canonical page outranks a compiled one at equal relevance.
            # Without this, ties were broken by whichever slug sorted higher,
            # so a page named after a passing remark ("research-you-didn-
            # headlines") beat the canonical page on the same subject.
            if entry.get("canonical"):
                score += 1.5
            scored.append((score, slug))
        scored.sort(key=lambda item: (-item[0], item[1]))
        results = []
        for _score, slug in scored[:limit]:
            entry = index[slug]
            excerpt = self._best_lines(entry["bullets"], raw_words, excerpt_chars)
            if not excerpt:
                # No bullet matched, so fall back to her NEWEST facts on the
                # page rather than the top of the file — which was returning
                # the "Links:" footer as the excerpt riding her prompt.
                excerpt = self._best_lines(
                    entry["bullets"], frozenset(WORD.findall(" ".join(entry["bullets"]).casefold())) - STOP,
                    excerpt_chars,
                ) or "\n".join(f"- {line}" for line in entry["bullets"][-3:])
            results.append(
                {
                    "slug": slug,
                    "title": slug.replace("-", " "),
                    "excerpt": excerpt,
                    "score": round(_score, 1),
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
