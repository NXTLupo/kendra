#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KEY="${KENDRA_MINISIGN_SECRET_KEY:-}"

if [ -z "$KEY" ] || [ ! -f "$KEY" ]; then
  echo "Set KENDRA_MINISIGN_SECRET_KEY to your private minisign key outside this repository." >&2
  exit 2
fi
if [ $(( $(stat -f '%Lp' "$KEY") & 077 )) -ne 0 ]; then
  echo "The private signing key must have mode 0600 (no group/other access)." >&2
  exit 2
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "Commit and review all intelligence changes before building a signed release." >&2
  exit 2
fi

COMMIT="$(git rev-parse HEAD)"
RELEASE_NAME="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
TARGET="$ROOT/releases/latest"
mkdir -p "$TARGET"

git archive --format=tar.gz --output="$TARGET/kendra-update.tar.gz" HEAD
ARCHIVE_SHA="$(shasum -a 256 "$TARGET/kendra-update.tar.gz" | awk '{print $1}')"
MANIFEST_TMP="$TARGET/manifest.yaml.tmp"
SIGNATURE_TMP="$TARGET/manifest.minisig.tmp"

printf '%s\n' \
  'format: kendra-signed-git-release' \
  'version: 1' \
  "release_name: '$RELEASE_NAME'" \
  "git_commit: '$COMMIT'" \
  'artifacts:' \
  '  - path: kendra-update.tar.gz' \
  "    sha256: '$ARCHIVE_SHA'" >"$MANIFEST_TMP"
mv "$MANIFEST_TMP" "$TARGET/manifest.yaml"
minisign -Sm "$TARGET/manifest.yaml" -s "$KEY" -x "$SIGNATURE_TMP"
mv "$SIGNATURE_TMP" "$TARGET/manifest.minisig"

echo "Signed intelligence release prepared from $COMMIT"
echo "Review and commit releases/latest/, then push main to the pinned GitHub remote."
