#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo ./scripts/install_initial_slot.sh" >&2
  exit 2
fi
if ! id kendra >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/kendra --shell /usr/sbin/nologin kendra
fi
install -d -o kendra -g kendra /opt/kendra /var/lib/kendra/models /var/lib/kendra/photos /var/lib/kendra/outbox /var/lib/kendra/exports
TARGET=/opt/kendra/slot-a
rm -rf "$TARGET"
mkdir -p "$TARGET"
# Copy application source but never local runtime data, secrets, or Git metadata.
tar -C "$ROOT" \
  --exclude=.git --exclude=.venv --exclude=config/local.yaml --exclude=config/hardware.local.yaml \
  --exclude=config/recipients.local.json --exclude=data --exclude=runtime --exclude=logs \
  --exclude=photos --exclude=outbox --exclude=exports --exclude=models --exclude=hardware/vendor \
  -cf - . | tar -C "$TARGET" -xf -
chown -R kendra:kendra "$TARGET"
runuser -u kendra -- python3 -m venv "$TARGET/.venv"
runuser -u kendra -- "$TARGET/.venv/bin/python" -m pip install --upgrade pip
runuser -u kendra -- "$TARGET/.venv/bin/pip" install -e "$TARGET[brain,hardware]"
ln -sfn "$TARGET" /opt/kendra/current
chown -h kendra:kendra /opt/kendra/current
cat <<'MSG'
Initial application slot installed at /opt/kendra/slot-a and current -> slot-a.
Next:
  sudo ./scripts/install_systemd.sh
Then review /etc/kendra/production.yaml before enabling any service.
MSG
