#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo ./scripts/install_systemd.sh" >&2
  exit 2
fi
if ! id kendra >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/kendra --shell /usr/sbin/nologin kendra
fi
install -d -o kendra -g kendra /var/lib/kendra /var/lib/kendra/photos /var/lib/kendra/outbox /var/lib/kendra/exports /var/log/kendra /run/kendra
install -d /etc/kendra
if [ ! -f /etc/kendra/production.yaml ]; then
  install -m 0640 -o root -g kendra "$ROOT/config/production.example.yaml" /etc/kendra/production.yaml
fi
if [ ! -f /etc/kendra/kendra.env ]; then
  install -m 0640 -o root -g kendra "$ROOT/config/kendra.env.example" /etc/kendra/kendra.env
fi
for unit in "$ROOT"/systemd/kendra-*.service; do
  install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload
cat <<'MSG'
Systemd units installed but NOT enabled automatically.
Review /etc/kendra/production.yaml and complete every hardware gate first.
Then enable only the services you have qualified, beginning with:
  sudo systemctl enable --now kendra-reflex kendra-body kendra-brain
MSG
