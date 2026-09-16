#!/bin/sh
# Install the panel->health.py write broker (socket, not sudo — see
# docs/ARCHITECTURE.md "The write bridge"). Idempotent.
# Usage: sudo /opt/hermes/deploy/install-bridge.sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }

# 1. Shared group that gates the socket; panel is the only human-facing member.
getent group hermesbridge >/dev/null || groupadd --system hermesbridge
id -nG panel | tr ' ' '\n' | grep -qx hermesbridge || usermod -aG hermesbridge panel

# 2. Broker code: root-owned, world-readable, not writable by panel/hermes.
install -o root -g root -m 0755 -d /usr/local/lib/hermes-bridge
install -o root -g root -m 0755 /opt/hermes/deploy/hermes-bridge \
        /usr/local/lib/hermes-bridge/hermes-bridge
install -o root -g root -m 0644 /opt/hermes/deploy/bridge_commands.py \
        /usr/local/lib/hermes-bridge/bridge_commands.py
install -o root -g root -m 0755 /opt/hermes/deploy/hermesctl \
        /usr/local/lib/hermes-bridge/hermesctl

# 3. Hermes-owned append-only audit log (the panel never writes it).
install -o hermes -g hermes -m 0700 -d /var/lib/hermes/audit
sudo -u hermes touch /var/lib/hermes/audit/bridge.jsonl
sudo -u hermes chmod 600 /var/lib/hermes/audit/bridge.jsonl

# 4. Install the service disabled. Activation remains an explicit operator step.
install -o root -g root -m 0644 /opt/hermes/deploy/hermes-bridge.service \
        /etc/systemd/system/hermes-bridge.service
systemctl daemon-reload

echo "Broker installed disabled. After configuration and synthetic validation:"
echo "  sudo systemctl enable --now hermes-bridge.service"
echo "Restart the panel afterward so its hermesbridge group membership is active."
