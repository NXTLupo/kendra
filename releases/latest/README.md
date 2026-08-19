# Signed intelligence release channel

This directory is populated by `scripts/build_signed_intelligence_release.sh` only after a reviewed code commit and a private minisign key are available.

The private key must remain outside the repository. Commit only the generated `manifest.yaml`, `manifest.minisig`, and `kendra-update.tar.gz`. Kendra verifies the signed manifest and archive hash, builds the inactive A/B slot, and refuses unsigned or malformed updates.
