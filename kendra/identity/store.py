from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).reshape(-1).tobytes()


def _vector(blob: bytes, dimensions: int) -> np.ndarray:
    value = np.frombuffer(blob, dtype=np.float32)
    if value.size != dimensions:
        raise ValueError("Stored identity embedding has invalid dimensions")
    return value


@dataclass(slots=True)
class IdentityMatch:
    person_uid: str | None
    display_name: str | None
    confidence: float
    status: str
    method: str = "face"

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_uid": self.person_uid,
            "display_name": self.display_name,
            "confidence": self.confidence,
            "status": self.status,
            "method": self.method,
        }


class IdentityStore:
    """Separate local biometric store.

    The database deliberately does not contain conversational memories. It maps
    face embeddings to opaque person UIDs. Social knowledge belongs in Kendra
    Brain and is linked by person_uid.
    """

    def __init__(self, path: Path, match_threshold: float = 0.50):
        self.path = path
        self.match_threshold = float(match_threshold)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.conn.executescript(schema)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create_identity(
        self,
        display_name: str,
        *,
        consent: bool,
        relationship: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        name = display_name.strip()
        if not name:
            raise ValueError("display_name cannot be empty")
        if not consent:
            raise PermissionError("Explicit consent is required before biometric enrollment")
        existing = self.conn.execute(
            "SELECT person_uid, consent_status FROM identities WHERE lower(display_name)=lower(?)",
            (name,),
        ).fetchone()
        timestamp = now_iso()
        if existing:
            if existing["consent_status"] != "granted":
                raise PermissionError("This identity has revoked biometric consent")
            return str(existing["person_uid"])
        person_uid = f"person_{uuid.uuid4().hex}"
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO identities(
                    person_uid, display_name, relationship, consent_status,
                    consent_recorded_at, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, 'granted', ?, ?, ?, ?)
                """,
                (
                    person_uid,
                    name,
                    relationship,
                    timestamp,
                    timestamp,
                    timestamp,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
        return person_uid

    def revoke(self, person_uid: str, *, delete_embeddings: bool = True) -> None:
        timestamp = now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE identities SET consent_status='revoked', updated_at=? WHERE person_uid=?",
                (timestamp, person_uid),
            )
            if delete_embeddings:
                self.conn.execute("DELETE FROM face_embeddings WHERE person_uid=?", (person_uid,))

    def add_embedding(
        self,
        person_uid: str,
        vector: np.ndarray,
        *,
        quality: float = 1.0,
        capture_context: str | None = None,
    ) -> int:
        row = self.conn.execute(
            "SELECT consent_status FROM identities WHERE person_uid=?", (person_uid,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown person_uid {person_uid}")
        if row["consent_status"] != "granted":
            raise PermissionError("Biometric consent is not granted")
        value = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(value))
        if not norm:
            raise ValueError("Face embedding has zero norm")
        value = value / norm
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO face_embeddings(person_uid, vector, dimensions, quality, capture_context, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    person_uid,
                    _blob(value),
                    int(value.size),
                    max(0.0, min(1.0, float(quality))),
                    capture_context,
                    now_iso(),
                ),
            )
        return int(cursor.lastrowid)

    def match(self, vector: np.ndarray) -> IdentityMatch:
        query = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if not norm:
            return IdentityMatch(None, None, 0.0, "unknown")
        query /= norm
        rows = self.conn.execute(
            """
            SELECT e.vector, e.dimensions, e.quality, i.person_uid, i.display_name
            FROM face_embeddings e
            JOIN identities i ON i.person_uid=e.person_uid
            WHERE i.consent_status='granted'
            """
        ).fetchall()
        best_uid: str | None = None
        best_name: str | None = None
        best_score = -1.0
        for row in rows:
            reference = _vector(row["vector"], int(row["dimensions"]))
            if reference.size != query.size:
                continue
            denom = float(np.linalg.norm(reference) * np.linalg.norm(query))
            score = float(np.dot(reference, query) / denom) if denom else -1.0
            score *= 0.9 + (0.1 * float(row["quality"]))
            if score > best_score:
                best_uid = str(row["person_uid"])
                best_name = str(row["display_name"])
                best_score = score
        if best_uid is None or best_score < self.match_threshold:
            return IdentityMatch(None, None, max(0.0, best_score), "unknown")
        return IdentityMatch(best_uid, best_name, min(1.0, best_score), "recognized")

    def record_encounter(
        self,
        match: IdentityMatch,
        *,
        photo_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO encounters(person_uid, recognized, confidence, method, photo_id, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match.person_uid,
                    1 if match.status == "recognized" else 0,
                    match.confidence,
                    match.method,
                    photo_id,
                    now_iso(),
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
        return int(cursor.lastrowid)

    def list_identities(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT i.person_uid, i.display_name, i.relationship, i.consent_status,
                   i.created_at, i.updated_at, COUNT(e.id) AS embeddings
            FROM identities i
            LEFT JOIN face_embeddings e ON e.person_uid=i.person_uid
            GROUP BY i.person_uid
            ORDER BY lower(i.display_name)
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        identities = int(self.conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0])
        embeddings = int(self.conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0])
        encounters = int(self.conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0])
        return {"identities": identities, "embeddings": embeddings, "encounters": encounters}
