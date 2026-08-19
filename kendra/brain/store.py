from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from ..config import Settings
from ..paths import resolve_path
from .embeddings import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    OnnxMiniLMEmbeddingProvider,
    Qwen3OnnxEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
    cosine_similarity,
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _vector(blob: bytes | None, dimensions: int | None) -> np.ndarray | None:
    if blob is None or not dimensions:
        return None
    value = np.frombuffer(blob, dtype=np.float32)
    return value if value.size == dimensions else None


@dataclass(slots=True)
class SearchHit:
    id: int
    kind: str
    content: str
    provenance: str
    confidence: float
    salience: float
    subject: str | None
    predicate: str | None
    object: str | None
    source_uri: str | None
    source_title: str | None
    created_at: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "salience": self.salience,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "source_uri": self.source_uri,
            "source_title": self.source_title,
            "created_at": self.created_at,
            "score": self.score,
        }


class BrainStore:
    """Kendra Brain's durable local cognitive store."""

    def __init__(self, db_path: Path, embedding_provider: EmbeddingProvider):
        self.db_path = db_path
        self.embedding = embedding_provider
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.conn.executescript(schema)
        self.conn.commit()
        self._repair_fts_if_needed()

    @classmethod
    def from_settings(cls, settings: Settings) -> BrainStore:
        provider_name = str(settings.get("brain.embedding.provider", "hashing"))
        dimensions = int(settings.get("brain.embedding.dimensions", 384))
        if provider_name == "qwen3_onnx":
            model_path = resolve_path(settings.require("brain.embedding.model_path"), settings.root)
            provider: EmbeddingProvider = Qwen3OnnxEmbeddingProvider(model_path)
        elif provider_name == "onnx_minilm":
            model_path = resolve_path(settings.require("brain.embedding.model_path"), settings.root)
            provider: EmbeddingProvider = OnnxMiniLMEmbeddingProvider(model_path)
        elif provider_name == "sentence_transformers":
            model_path = resolve_path(settings.require("brain.embedding.model_path"), settings.root)
            provider = SentenceTransformerEmbeddingProvider(model_path)
        elif provider_name == "hashing":
            provider = HashingEmbeddingProvider(dimensions)
        else:
            raise ValueError(f"Unknown brain embedding provider: {provider_name}")
        return cls(settings.path("paths.brain_db"), provider)

    def _repair_fts_if_needed(self) -> None:
        memory_count = int(self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        fts_count = int(self.conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0])
        if memory_count == fts_count:
            return
        with self.conn:
            self.conn.execute("DELETE FROM memory_fts")
            self.conn.execute(
                """
                INSERT INTO memory_fts(memory_id, content, subject, predicate, object)
                SELECT id, content, COALESCE(subject,''), COALESCE(predicate,''), COALESCE(object,'')
                FROM memories
                """
            )

    def close(self) -> None:
        self.conn.close()

    def event(self, event_type: str, payload: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO cognitive_events(event_type, payload_json, created_at) VALUES (?, ?, ?)",
                (event_type, json.dumps(payload, sort_keys=True), now_iso()),
            )

    def remember(
        self,
        *,
        kind: str,
        content: str,
        provenance: str,
        confidence: float = 0.7,
        salience: float = 0.5,
        subject: str | None = None,
        predicate: str | None = None,
        object_value: str | None = None,
        source_uri: str | None = None,
        source_title: str | None = None,
        source_timestamp: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        supersede_conflict: bool = False,
    ) -> int:
        content = content.strip()
        if not content:
            raise ValueError("Memory content cannot be empty")
        allowed_provenance = {"observed", "user_stated", "researched", "inferred", "system"}
        if provenance not in allowed_provenance:
            raise ValueError(f"Unsupported provenance: {provenance}")
        if not 0 <= confidence <= 1 or not 0 <= salience <= 1:
            raise ValueError("confidence and salience must be between 0 and 1")

        vector = self.embedding.encode(content)
        timestamp = now_iso()
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO memories(
                    kind, content, provenance, confidence, salience,
                    subject, predicate, object, source_uri, source_title,
                    source_timestamp, embedding, embedding_dimensions,
                    created_at, updated_at, session_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind, content, provenance, confidence, salience,
                    subject, predicate, object_value, source_uri, source_title,
                    source_timestamp, _blob(vector), int(vector.size),
                    timestamp, timestamp, session_id, json.dumps(metadata or {}, sort_keys=True),
                ),
            )
            memory_id = int(cursor.lastrowid)
            self.conn.execute(
                "INSERT INTO memory_fts(memory_id, content, subject, predicate, object) VALUES (?, ?, ?, ?, ?)",
                (memory_id, content, subject or "", predicate or "", object_value or ""),
            )
            if supersede_conflict and subject and predicate:
                rows = self.conn.execute(
                    "SELECT id FROM memories WHERE active=1 AND id<>? AND subject=? AND predicate=?",
                    (memory_id, subject, predicate),
                ).fetchall()
                for row in rows:
                    self.conn.execute(
                        "UPDATE memories SET active=0, superseded_by=?, updated_at=? WHERE id=?",
                        (memory_id, timestamp, int(row["id"])),
                    )
        self.event("memory_created", {"memory_id": memory_id, "kind": kind, "provenance": provenance})
        return memory_id

    def correct(
        self,
        memory_id: int,
        *,
        corrected_content: str,
        provenance: str = "user_stated",
        confidence: float = 0.95,
        reason: str | None = None,
        object_value: str | None = None,
    ) -> int:
        old = self.conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if old is None:
            raise KeyError(f"Unknown memory id {memory_id}")
        metadata = json.loads(old["metadata_json"] or "{}")
        if reason:
            metadata["correction_reason"] = reason
        new_id = self.remember(
            kind=str(old["kind"]),
            content=corrected_content,
            provenance=provenance,
            confidence=confidence,
            salience=max(float(old["salience"]), 0.8),
            subject=old["subject"],
            predicate=old["predicate"],
            object_value=old["object"] if object_value is None else object_value,
            source_uri=old["source_uri"],
            source_title=old["source_title"],
            session_id=old["session_id"],
            metadata=metadata,
        )
        with self.conn:
            self.conn.execute(
                "UPDATE memories SET active=0, superseded_by=?, updated_at=? WHERE id=?",
                (new_id, now_iso(), memory_id),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO memory_links(source_memory_id, target_memory_id, relation, weight, created_at) VALUES (?, ?, 'corrected_by', 1.0, ?)",
                (memory_id, new_id, now_iso()),
            )
        self.event("memory_corrected", {"old_memory_id": memory_id, "new_memory_id": new_id})
        return new_id

    def _fts_query(self, query: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9_'-]+", query)
        return " OR ".join(f'"{token}"' for token in tokens[:16])

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        kinds: Iterable[str] | None = None,
        include_system: bool = False,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        kinds_set = set(kinds or [])
        candidate_rows: dict[int, sqlite3.Row] = {}
        lexical_rank: dict[int, float] = {}
        fts_query = self._fts_query(query)
        if fts_query:
            try:
                rows = self.conn.execute(
                    """
                    SELECT m.*, bm25(memory_fts) AS rank
                    FROM memory_fts
                    JOIN memories m ON m.id = CAST(memory_fts.memory_id AS INTEGER)
                    WHERE memory_fts MATCH ? AND m.active=1
                    ORDER BY rank
                    LIMIT 80
                    """,
                    (fts_query,),
                ).fetchall()
                for row in rows:
                    if kinds_set and row["kind"] not in kinds_set:
                        continue
                    if not include_system and row["provenance"] == "system":
                        # Her build/architecture docs answer build questions
                        # via the deterministic route only; in general recall
                        # they made idle chat ruminate about chassis phases.
                        continue
                    mid = int(row["id"])
                    candidate_rows[mid] = row
                    lexical_rank[mid] = float(row["rank"])
            except sqlite3.OperationalError:
                pass

        recent = self.conn.execute(
            "SELECT * FROM memories WHERE active=1 ORDER BY salience DESC, created_at DESC LIMIT 100"
        ).fetchall()
        for row in recent:
            if kinds_set and row["kind"] not in kinds_set:
                continue
            if not include_system and row["provenance"] == "system":
                continue
            candidate_rows.setdefault(int(row["id"]), row)

        qvec = getattr(self.embedding, 'encode_query', self.embedding.encode)(query)
        scored: list[SearchHit] = []
        for mid, row in candidate_rows.items():
            mvec = _vector(row["embedding"], row["embedding_dimensions"])
            semantic = cosine_similarity(qvec, mvec) if mvec is not None else 0.0
            rank = lexical_rank.get(mid)
            lexical = 0.0 if rank is None else 1.0 / (1.0 + max(0.0, rank + 10.0))
            # Relevance gate: without it, salience+recency outvote weak
            # semantic scores and the freshest memories flood every prompt —
            # a moon-phase question was answered through Raspberry Pi facts.
            if semantic < 0.18 and rank is None:
                continue
            salience = float(row["salience"])
            confidence = float(row["confidence"])
            score = (semantic * 0.58) + (lexical * 0.22) + (salience * 0.12) + (confidence * 0.08)
            scored.append(
                SearchHit(
                    id=mid,
                    kind=str(row["kind"]),
                    content=str(row["content"]),
                    provenance=str(row["provenance"]),
                    confidence=confidence,
                    salience=salience,
                    subject=row["subject"],
                    predicate=row["predicate"],
                    object=row["object"],
                    source_uri=row["source_uri"],
                    source_title=row["source_title"],
                    created_at=str(row["created_at"]),
                    score=score,
                )
            )
        scored.sort(key=lambda hit: hit.score, reverse=True)
        chosen = scored[: max(1, min(limit, 50))]
        if chosen:
            timestamp = now_iso()
            with self.conn:
                self.conn.executemany(
                    "UPDATE memories SET last_accessed_at=?, access_count=access_count+1 WHERE id=?",
                    [(timestamp, hit.id) for hit in chosen],
                )
        return chosen

    def get_memory(self, memory_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown memory id {memory_id}")
        result = dict(row)
        result.pop("embedding", None)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result

    def add_episode(
        self,
        user_text: str,
        kendra_text: str,
        *,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        content = f"User: {user_text.strip()}\nKendra: {kendra_text.strip()}"
        return self.remember(
            kind="episode",
            content=content,
            provenance="observed",
            confidence=1.0,
            salience=0.45,
            session_id=session_id,
            metadata=metadata,
        )

    def begin_session(self, session_id: str, context: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO sessions(id, started_at, context) VALUES (?, ?, ?)",
                (session_id, now_iso(), context),
            )

    def add_turn(
        self,
        session_id: str,
        user_text: str,
        kendra_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO turns(session_id, user_text, kendra_text, created_at, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (session_id, user_text, kendra_text, now_iso(), json.dumps(metadata or {}, sort_keys=True)),
            )

    def end_session(self, session_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (now_iso(), session_id))

    def upsert_person(
        self,
        name: str,
        *,
        relationship: str | None = None,
        consent_notes: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        timestamp = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO people(name, relationship, consent_notes, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    relationship=COALESCE(excluded.relationship, people.relationship),
                    consent_notes=COALESCE(excluded.consent_notes, people.consent_notes),
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (name.strip(), relationship, consent_notes, timestamp, timestamp, json.dumps(metadata or {}, sort_keys=True)),
            )
            row = self.conn.execute("SELECT id FROM people WHERE name=?", (name.strip(),)).fetchone()
        return int(row["id"])

    def upsert_place(
        self,
        name: str,
        *,
        description: str | None = None,
        marker_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        timestamp = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO places(name, description, marker_id, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description=COALESCE(excluded.description, places.description),
                    marker_id=COALESCE(excluded.marker_id, places.marker_id),
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (name.strip(), description, marker_id, timestamp, timestamp, json.dumps(metadata or {}, sort_keys=True)),
            )
            row = self.conn.execute("SELECT id FROM places WHERE name=?", (name.strip(),)).fetchone()
        return int(row["id"])

    def reinforce_interest(self, topic: str, delta: float = 0.1, source: str = "experience") -> None:
        topic = topic.strip()
        if not topic:
            raise ValueError("topic cannot be empty")
        row = self.conn.execute("SELECT weight FROM interests WHERE topic=?", (topic,)).fetchone()
        weight = min(1.0, max(0.0, (float(row["weight"]) if row else 0.4) + delta))
        timestamp = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO interests(topic, weight, last_reinforced_at, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(topic) DO UPDATE SET
                  weight=excluded.weight,
                  last_reinforced_at=excluded.last_reinforced_at,
                  source=excluded.source
                """,
                (topic, weight, timestamp, source),
            )

    def decay_interests(self, factor: float = 0.985) -> None:
        with self.conn:
            self.conn.execute("UPDATE interests SET weight=MAX(0.05, weight * ?)", (factor,))

    def interests(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT topic, weight, last_reinforced_at, source FROM interests ORDER BY weight DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_question(self, question: str, interest_weight: float = 0.5) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO open_questions(question, interest_weight, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (question.strip(), interest_weight, now_iso(), now_iso()),
            )
        return int(cursor.lastrowid)

    def resolve_question(self, question_id: int, memory_id: int | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE open_questions SET status='resolved', resolved_memory_id=?, updated_at=? WHERE id=?",
                (memory_id, now_iso(), question_id),
            )

    def questions(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, question, interest_weight, created_at FROM open_questions WHERE status='open' ORDER BY interest_weight DESC, created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_goal(
        self,
        title: str,
        description: str = "",
        priority: float = 0.5,
        provenance: str = "system",
    ) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO goals(title, description, priority, provenance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (title.strip(), description.strip(), priority, provenance, now_iso(), now_iso()),
            )
        return int(cursor.lastrowid)

    def complete_goal(self, goal_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE goals SET status='done', updated_at=? WHERE id=?", (now_iso(), goal_id)
            )

    def goals(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, title, description, priority, provenance, created_at FROM goals WHERE status='open' ORDER BY priority DESC, created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_self(self, key: str, value: Any, provenance: str = "system") -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO self_model(key, value_json, provenance, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    provenance=excluded.provenance,
                    updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, sort_keys=True), provenance, now_iso()),
            )

    def self_model(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key, value_json, provenance, updated_at FROM self_model").fetchall()
        return {
            str(row["key"]): {
                "value": json.loads(row["value_json"]),
                "provenance": row["provenance"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def add_reflection(
        self,
        text: str,
        basis_memory_ids: list[int],
        *,
        model_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO reflections(text, basis_memory_ids_json, created_at, model_id, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (text.strip(), json.dumps(basis_memory_ids), now_iso(), model_id, json.dumps(metadata or {}, sort_keys=True)),
            )
        return int(cursor.lastrowid)

    def context_for(
        self,
        query: str,
        *,
        limit: int = 12,
        character_budget: int = 7000,
        include_self_model: bool = True,
        exclude_kinds: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        excluded = {str(kind) for kind in (exclude_kinds or [])}
        hits = self.search(query, limit=limit)
        memories: list[dict[str, Any]] = []
        used = 0
        for hit in hits:
            # Raw dialogue episodes fed back into a live prompt cause verbatim
            # parroting: a small model copies its own remembered reply instead
            # of answering. Live turns exclude them; the recall tool does not.
            if hit.kind in excluded:
                continue
            item = hit.as_dict()
            size = len(item["content"])
            if memories and used + size > character_budget:
                break
            memories.append(item)
            used += size
        context = {
            "memories": memories,
            "interests": self.interests(),
            "goals": self.goals(),
            "open_questions": self.questions(),
        }
        # The self-model is identity boilerplate the charter already states. A
        # small local model treats it as a script and recites it ("I'm Kendra, a
        # small hexapod robot and an intellectual companion...") instead of
        # holding a conversation, so ordinary chat turns leave it out.
        if include_self_model:
            context["self_model"] = self.self_model()
        return context

    def recent_turns(self, limit: int = 6, max_age_seconds: float = 900.0) -> list[dict[str, Any]]:
        """Most recent conversation turns, oldest first, for live prompt history.

        Speech synthesis and dialogue coherence both depend on conditioning on
        the ongoing discourse (Qwen3-Omni report, section 2.4). Without this,
        every utterance is answered as if it were the first thing ever said.
        """
        limit = max(1, min(int(limit), 24))
        cutoff = (datetime.now(UTC) - timedelta(seconds=float(max_age_seconds))).isoformat()
        rows = self.conn.execute(
            """
            SELECT user_text, kendra_text FROM turns
            WHERE created_at >= ?
              AND COALESCE(json_extract(metadata_json, '$.source'), '') NOT LIKE 'probe%'
            ORDER BY id DESC LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [
            {"user_text": row[0], "kendra_text": row[1]}
            for row in reversed(rows)
        ]

    def dashboard_snapshot(self, limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        turns = self.conn.execute(
            """
            SELECT id, session_id, user_text, kendra_text, created_at, metadata_json
            FROM turns ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        events = self.conn.execute(
            """
            SELECT id, event_type, payload_json, created_at
            FROM cognitive_events ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        memories = self.conn.execute(
            """
            SELECT id, kind, content, provenance, confidence, salience, created_at
            FROM memories WHERE active=1 ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {
            "stats": self.stats(),
            "turns": [
                {
                    **{key: row[key] for key in row.keys() if key != "metadata_json"},
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                }
                for row in turns
            ],
            "events": [
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload_json"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in events
            ],
            "memories": [dict(row) for row in memories],
            "goals": self.goals(limit=8),
            "questions": self.questions(limit=8),
        }

    def import_memory_jsonl(self, source: Path, source_label: str) -> dict[str, int]:
        if source.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("Brain transfer exceeds the 25 MiB safety limit")
        records: list[dict[str, Any]] = []
        header_seen = False
        scanned = 0
        with source.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                if line_number > 50_001:
                    raise ValueError("Brain transfer exceeds 50,000 records")
                value = json.loads(line)
                if value.get("type") == "header":
                    data = value.get("data") or {}
                    if data.get("format") != "kendra-brain-jsonl" or int(data.get("version", 0)) != 1:
                        raise ValueError("Unsupported Kendra Brain transfer format")
                    header_seen = True
                    continue
                scanned += 1
                if value.get("type") != "row" or value.get("table") != "memories":
                    continue
                data = value.get("data")
                if not isinstance(data, dict) or int(data.get("active", 1)) != 1:
                    continue
                records.append(data)
        if not header_seen:
            raise ValueError("Kendra Brain transfer header is missing")

        imported = 0
        duplicates = 0
        for data in records:
            kind = str(data.get("kind") or "fact")[:64]
            content = str(data.get("content") or "").strip()[:20_000]
            provenance = str(data.get("provenance") or "observed")
            if provenance not in {"observed", "user_stated", "researched", "inferred", "system"}:
                continue
            subject = str(data["subject"])[:500] if data.get("subject") is not None else None
            predicate = str(data["predicate"])[:500] if data.get("predicate") is not None else None
            object_value = str(data["object"])[:2_000] if data.get("object") is not None else None
            if not content:
                continue
            exists = self.conn.execute(
                """
                SELECT id FROM memories
                WHERE active=1 AND kind=? AND content=? AND provenance=?
                  AND subject IS ? AND predicate IS ? AND object IS ?
                LIMIT 1
                """,
                (kind, content, provenance, subject, predicate, object_value),
            ).fetchone()
            if exists is not None:
                duplicates += 1
                continue
            raw_metadata = data.get("metadata_json") or {}
            if isinstance(raw_metadata, str):
                try:
                    raw_metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    raw_metadata = {}
            metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
            metadata["brain_transfer_source"] = source_label[:200]
            metadata["brain_transfer_original_created_at"] = data.get("created_at")
            self.remember(
                kind=kind,
                content=content,
                provenance=provenance,
                confidence=min(1.0, max(0.0, float(data.get("confidence", 0.7)))),
                salience=min(1.0, max(0.0, float(data.get("salience", 0.5)))),
                subject=subject,
                predicate=predicate,
                object_value=object_value,
                source_uri=str(data["source_uri"])[:2_000] if data.get("source_uri") else None,
                source_title=str(data["source_title"])[:500] if data.get("source_title") else None,
                source_timestamp=str(data["source_timestamp"])[:100]
                if data.get("source_timestamp")
                else None,
                metadata=metadata,
            )
            imported += 1
        self.event(
            "brain_transfer_imported",
            {"source": source_label[:200], "imported": imported, "duplicates": duplicates},
        )
        return {"scanned": scanned, "memory_records": len(records), "imported": imported, "duplicates": duplicates}

    def stats(self) -> dict[str, Any]:
        tables = [
            "memories", "people", "places", "interests", "goals", "open_questions",
            "sessions", "turns", "reflections", "cognitive_events",
        ]
        counts = {
            table: int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
        active = int(self.conn.execute("SELECT COUNT(*) FROM memories WHERE active=1").fetchone()[0])
        return {
            "db": str(self.db_path),
            "bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "active_memories": active,
            "counts": counts,
            "embedding_provider": type(self.embedding).__name__,
        }
