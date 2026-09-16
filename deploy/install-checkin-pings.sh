#!/bin/sh
# Install optional energy/focus check-in pings: script + two systemd timers
# (10:30 + 15:00 UTC). Idempotent.
# Usage: sudo /opt/hermes/deploy/install-checkin-pings.sh
#
# Sends via `hermes send` (one-way outbound, gateway credentials, held by the
# hermes user — the panel never touches this path). Verify the CLI once before
# enabling: sudo -u hermes -i hermes send "synthetic ping test"
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }

install -o root -g root -m 0755 -d /usr/local/lib/hermes-bridge
install -o root -g root -m 0755 /opt/hermes/deploy/checkin-ping \
        /usr/local/lib/hermes-bridge/checkin-ping
cp /opt/hermes/deploy/hermes-checkin-ping@.service /etc/systemd/system/
cp /opt/hermes/deploy/hermes-checkin-ping-am.timer /etc/systemd/system/
cp /opt/hermes/deploy/hermes-checkin-ping-pm.timer /etc/systemd/system/
systemctl daemon-reload

echo "Installed disabled. Test one authorized notification with:"
echo "  sudo systemctl start hermes-checkin-ping@pm.service"
echo "  journalctl -u hermes-checkin-ping@pm --no-pager -n 10"
echo "Enable scheduling only after validation:"
echo "  sudo systemctl enable --now hermes-checkin-ping-am.timer hermes-checkin-ping-pm.timer"
