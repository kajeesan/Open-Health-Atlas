#!/bin/sh
# Install the optional Google Health collector and timer disabled.
set -eu

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }
CONFIG=${HERMES_GHEALTH_CONFIG:-/etc/hermes/ghealth.json}
MODE=$(stat -c '%U %G %a' "$CONFIG" 2>/dev/null || echo absent)
[ "$MODE" = "root root 600" ] || {
  echo "FAIL: $CONFIG must be root:root 0600, is: $MODE" >&2
  exit 1
}

install -o root -g root -m 0755 -d /usr/local/lib/hermes-ghealth
install -o root -g root -m 0755 /opt/hermes/deploy/ghealth-sync \
  /usr/local/lib/hermes-ghealth/ghealth-sync
install -o root -g root -m 0644 \
  /opt/hermes/deploy/hermes-ghealth-sync.service \
  /etc/systemd/system/hermes-ghealth-sync.service
install -o root -g root -m 0644 \
  /opt/hermes/deploy/hermes-ghealth-sync.timer \
  /etc/systemd/system/hermes-ghealth-sync.timer
systemctl daemon-reload

echo "Installed disabled. Run one authorized manual sync first:"
echo "  sudo systemctl start hermes-ghealth-sync.service"
echo "Enable scheduling only after validation:"
echo "  sudo systemctl enable --now hermes-ghealth-sync.timer"
