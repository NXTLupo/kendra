from __future__ import annotations

import numpy as np
import pytest

from kendra.identity.store import IdentityStore


def test_identity_requires_consent_and_persists_matching(tmp_path):
    path = tmp_path / "identities.db"
    store = IdentityStore(path, match_threshold=0.7)
    try:
        with pytest.raises(PermissionError):
            store.create_identity("Jonathan", consent=False)

        uid = store.create_identity("Jonathan", consent=True, relationship="owner")
        store.add_embedding(uid, np.array([1.0, 0.0, 0.0], dtype=np.float32))
        match = store.match(np.array([0.99, 0.01, 0.0], dtype=np.float32))
        assert match.status == "recognized"
        assert match.person_uid == uid
        assert match.display_name == "Jonathan"
    finally:
        store.close()

    reopened = IdentityStore(path, match_threshold=0.7)
    try:
        match = reopened.match(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        assert match.status == "recognized"
        assert match.person_uid == uid
    finally:
        reopened.close()


def test_revocation_removes_biometric_match(tmp_path):
    store = IdentityStore(tmp_path / "identities.db", match_threshold=0.7)
    try:
        uid = store.create_identity("Guest", consent=True)
        vector = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        store.add_embedding(uid, vector)
        assert store.match(vector).status == "recognized"
        store.revoke(uid, delete_embeddings=True)
        assert store.match(vector).status == "unknown"
        row = next(item for item in store.list_identities() if item["person_uid"] == uid)
        assert row["consent_status"] == "revoked"
        assert row["embeddings"] == 0
    finally:
        store.close()
