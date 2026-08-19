from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from ..config import Settings
from ..ipc import UnixJsonClient, UnixJsonServer
from .store import IdentityStore


class IdentityService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = IdentityStore(
            settings.path("paths.identity_db"),
            float(settings.get("identity.match_threshold", 0.50)),
        )
        self.server = UnixJsonServer(settings.socket_path("identity"), self.handle)

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {"ok": True, **self.store.stats()}
        if method == "create":
            uid = self.store.create_identity(
                str(params["display_name"]),
                consent=bool(params.get("consent", False)),
                relationship=params.get("relationship"),
                metadata=dict(params.get("metadata", {})),
            )
            return {"person_uid": uid}
        if method == "add_embedding":
            vector = np.asarray(params["vector"], dtype=np.float32)
            embedding_id = self.store.add_embedding(
                str(params["person_uid"]),
                vector,
                quality=float(params.get("quality", 1.0)),
                capture_context=params.get("capture_context"),
            )
            return {"embedding_id": embedding_id}
        if method == "recent_recognized":
            return self.store.recent_recognized(float(params.get("within_seconds", 600)))
        if method == "match":
            match = self.store.match(np.asarray(params["vector"], dtype=np.float32))
            return match.as_dict()
        if method == "encounter":
            from .store import IdentityMatch

            raw = dict(params["match"])
            match = IdentityMatch(
                raw.get("person_uid"),
                raw.get("display_name"),
                float(raw.get("confidence", 0.0)),
                str(raw.get("status", "unknown")),
                str(raw.get("method", "face")),
            )
            encounter_id = self.store.record_encounter(
                match,
                photo_id=params.get("photo_id"),
                metadata=dict(params.get("metadata", {})),
            )
            return {"encounter_id": encounter_id}
        if method == "list":
            return self.store.list_identities()
        if method == "revoke":
            self.store.revoke(
                str(params["person_uid"]),
                delete_embeddings=bool(params.get("delete_embeddings", True)),
            )
            return {"ok": True}
        raise KeyError(f"Unknown identity method: {method}")

    async def run(self) -> None:
        try:
            await self.server.serve_forever()
        finally:
            self.store.close()


class IdentityClient:
    def __init__(self, settings: Settings):
        self.rpc = UnixJsonClient(settings.socket_path("identity"), timeout=15)

    async def create(self, display_name: str, *, consent: bool, relationship: str | None = None) -> str:
        result = await self.rpc.call(
            "create",
            {"display_name": display_name, "consent": consent, "relationship": relationship},
        )
        return str(result["person_uid"])

    async def add_embedding(self, person_uid: str, vector: np.ndarray, *, capture_context: str | None = None) -> int:
        result = await self.rpc.call(
            "add_embedding",
            {
                "person_uid": person_uid,
                "vector": np.asarray(vector, dtype=np.float32).tolist(),
                "capture_context": capture_context,
            },
        )
        return int(result["embedding_id"])

    async def recent_recognized(self, within_seconds: float = 600.0) -> list[dict[str, Any]]:
        return await self.rpc.call("recent_recognized", {"within_seconds": within_seconds})

    async def match(self, vector: np.ndarray) -> dict[str, Any]:
        return await self.rpc.call("match", {"vector": np.asarray(vector, dtype=np.float32).tolist()})

    async def encounter(self, match: dict[str, Any], photo_id: str | None = None) -> int:
        result = await self.rpc.call("encounter", {"match": match, "photo_id": photo_id})
        return int(result["encounter_id"])


def run(settings: Settings) -> None:
    asyncio.run(IdentityService(settings).run())
