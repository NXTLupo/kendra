"""Local biometric identity service for Kendra.

Biometric embeddings remain in a separate SQLite database from Kendra Brain.
The Second Brain stores the social meaning of a person; this package stores the
minimum biometric representation needed to resolve an observed face to an
opaque person UID.
"""
