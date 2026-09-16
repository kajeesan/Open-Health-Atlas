#!/bin/sh
# Install the optional Hevy sync collector and systemd timer. Idempotent.
# Usage: sudo /opt/hermes/deploy/install-hevy-sync.sh
# Prereq: /etc/hermes/hevy.env exists, root-owned 0600, containing
#   HEVY_API_KEY=<user-supplied value> (never commit this file)
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }

# Owner AND GROUP must be root: root:hermes 0640 would hand the key to the
# hermes user — the exact principal the split exists to protect it from.
E=${HERMES_HEVY_ENV:-/etc/hermes/hevy.env}
M=$(stat -c '%U %G %a' "$E" 2>/dev/null || echo absent)
case "$M" in
  "root root 600") : ;;
  absent) echo "FAIL: $E missing — create it with HEVY_API_KEY=… (root:root, 0600)" >&2; exit 1 ;;
  *) echo "FAIL: $E must be root:root 0600, is: $M" >&2; exit 1 ;;
esac
grep -qE '^HEVY_API_KEY=.+' "$E" || { echo "FAIL: HEVY_API_KEY missing/empty in $E" >&2; exit 1; }

install -o root -g root -m 0755 -d /usr/local/lib/hermes-hevy
install -o root -g root -m 0755 /opt/hermes/deploy/hevy-collector \
        /usr/local/lib/hermes-hevy/hevy-collector
cp /opt/hermes/deploy/hermes-hevy-sync.service /etc/systemd/system/
cp /opt/hermes/deploy/hermes-hevy-sync.timer /etc/systemd/system/
systemctl daemon-reload

echo "Installed disabled. Validate one synthetic or user-authorized sync with:"
echo "  sudo systemctl start hermes-hevy-sync.service"
echo "  journalctl -u hermes-hevy-sync --no-pager -n 20"
echo "Enable scheduling only after validation:"
echo "  sudo systemctl enable --now hermes-hevy-sync.timer"
