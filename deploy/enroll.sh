#!/bin/sh
# Print a one-time passkey enrollment URL (valid 15 minutes, single use).
# Usage: sudo /opt/hermes/deploy/enroll.sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }
set -a; . /etc/hermes/panel.env; set +a
cd /opt/hermes
exec sudo -u panel env \
  PANEL_SECRET_KEY="$PANEL_SECRET_KEY" \
  PANEL_RP_ID="$PANEL_RP_ID" \
  PANEL_ORIGIN="$PANEL_ORIGIN" \
  PANEL_DB="$PANEL_DB" \
  .venv/bin/flask --app wsgi enroll-token
