#!/bin/sh
# Install the optional Telegram action worker, extension module, and evening
# timer without modifying or restarting an external gateway checkout.
set -eu

[ "$(id -u)" -eq 0 ] || {
  echo '{"ok":false,"error":"run as root"}'
  exit 1
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKER="$SCRIPT_DIR/hermes-telegram-actions"
EXTENSION="$SCRIPT_DIR/hermes_telegram_gateway_extension.py"
SERVICE="$SCRIPT_DIR/hermes-telegram-evening.service"
TIMER="$SCRIPT_DIR/hermes-telegram-evening.timer"
GUIDE="$SCRIPT_DIR/hermes-telegram-integration.md"

for source in "$WORKER" "$EXTENSION" "$SERVICE" "$TIMER" "$GUIDE"; do
  [ -f "$source" ] && [ ! -L "$source" ] || {
    echo '{"ok":false,"error":"required regular source artifact missing"}'
    exit 1
  }
done

python3 - "$WORKER" "$EXTENSION" <<'PY'
import pathlib
import sys

for item in sys.argv[1:]:
    compile(pathlib.Path(item).read_text(), item, "exec")
PY

install -o root -g root -m 0755 -d /usr/local/lib/hermes-bridge
install -o root -g root -m 0755 -d /usr/local/lib/hermes-telegram
install -o hermes -g hermes -m 0700 -d \
  /var/lib/hermes/.runtime/telegram-actions
install -o root -g root -m 0755 "$WORKER" \
  /usr/local/lib/hermes-bridge/hermes-telegram-actions
install -o root -g root -m 0644 "$EXTENSION" \
  /usr/local/lib/hermes-telegram/health_actions.py
install -o root -g root -m 0644 "$GUIDE" \
  /usr/local/lib/hermes-telegram/INTEGRATION.md
install -o root -g root -m 0644 "$SERVICE" \
  /etc/systemd/system/hermes-telegram-evening.service
install -o root -g root -m 0644 "$TIMER" \
  /etc/systemd/system/hermes-telegram-evening.timer
systemctl daemon-reload

echo '{"ok":true,"status":"installed-disabled","external_gateway_modified":false,"evening_timer":false}'
echo "Wire the extension through its documented adapter contract:"
echo "  /usr/local/lib/hermes-telegram/INTEGRATION.md"
echo "Then validate with fictional input before explicitly enabling the timer."
