PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS identities (
    person_uid TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    relationship TEXT,
    consent_status TEXT NOT NULL CHECK(consent_status IN ('granted','revoked')),
    consent_recorded_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_identities_display_name_nocase
ON identities(lower(display_name));

CREATE TABLE IF NOT EXISTS face_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_uid TEXT NOT NULL REFERENCES identities(person_uid) ON DELETE CASCADE,
    vector BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    quality REAL NOT NULL DEFAULT 1.0 CHECK(quality >= 0 AND quality <= 1),
    capture_context TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_person ON face_embeddings(person_uid);

CREATE TABLE IF NOT EXISTS encounters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_uid TEXT REFERENCES identities(person_uid) ON DELETE SET NULL,
    recognized INTEGER NOT NULL,
    confidence REAL,
    method TEXT NOT NULL,
    photo_id TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_encounters_created ON encounters(created_at DESC);
