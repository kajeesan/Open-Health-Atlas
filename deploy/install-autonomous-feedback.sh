#!/bin/sh
# Install evening-feedback artifacts disabled. Activation is a separate,
# explicit administrator action.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODE=dry-run
case "${1:-}" in
  "") ;;
  --dry-run) ;;
  --install) MODE=install ;;
  --enable) MODE=enable ;;
  *) echo '{"ok":false,"error":"usage: install-autonomous-feedback.sh [--dry-run|--install|--enable]"}'; exit 2 ;;
esac

WORKER="$SCRIPT_DIR/hermes-evening-feedback"
SERVICE="$SCRIPT_DIR/hermes-evening-feedback.service"
TIMER="$SCRIPT_DIR/hermes-evening-feedback.timer"
for source in "$WORKER" "$SERVICE" "$TIMER"; do
  [ -f "$source" ] && [ ! -L "$source" ] || {
    echo '{"ok":false,"error":"required regular source artifact missing"}'; exit 1;
  }
done
python3 - "$WORKER" <<'PY'
import pathlib, sys
compile(pathlib.Path(sys.argv[1]).read_text(), sys.argv[1], "exec")
PY
grep -q '^OnCalendar=\*-\*-\* 20:30 UTC$' "$TIMER"
grep -q '^RandomizedDelaySec=5m$' "$TIMER"
grep -q '^Persistent=false$' "$TIMER"

if [ "$MODE" = dry-run ]; then
  python3 - "$WORKER" "$SERVICE" "$TIMER" <<'PY'
import hashlib, json, pathlib, sys
print(json.dumps({"ok": True, "status": "dry-run", "enabled": False,
                  "artifacts": {pathlib.Path(p).name: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
                                for p in sys.argv[1:]}}, sort_keys=True))
PY
  exit 0
fi

TEST_MODE=${HERMES_AUTONOMOUS_TEST_MODE:-0}
if [ "$TEST_MODE" != "1" ]; then
  [ "$(id -u)" -eq 0 ] || {
    echo '{"ok":false,"error":"--install/--enable requires root"}'; exit 1;
  }
fi
LIB_ROOT=${HERMES_FEEDBACK_LIB_ROOT:-/usr/local/lib/hermes-autonomous}
SYSTEMD_ROOT=${HERMES_FEEDBACK_SYSTEMD_ROOT:-/etc/systemd/system}
RUNTIME_ROOT=${HERMES_FEEDBACK_RUNTIME_ROOT:-/var/lib/hermes/.runtime/evening-feedback}
SYSTEMCTL=${HERMES_FEEDBACK_SYSTEMCTL:-}
canonical_test_root() {
  python3 - "$1" <<'PY'
import os
import pathlib
import sys

candidate = pathlib.Path(sys.argv[1])
if not candidate.is_absolute():
    raise SystemExit(1)
resolved = candidate.resolve(strict=False)
bases = {
    pathlib.Path(value).resolve(strict=False)
    for value in ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
}
if not any(
    resolved != base
    and os.path.commonpath((str(resolved), str(base))) == str(base)
    for base in bases
):
    raise SystemExit(1)
print(resolved)
PY
}
canonical_test_executable() {
  python3 - "$1" <<'PY'
import os
import pathlib
import stat
import sys

candidate = pathlib.Path(sys.argv[1])
if not candidate.is_absolute() or candidate.is_symlink():
    raise SystemExit(1)
try:
    mode = candidate.stat().st_mode
    resolved = candidate.resolve(strict=True)
except OSError:
    raise SystemExit(1)
bases = {
    pathlib.Path(value).resolve(strict=False)
    for value in ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
}
if (
    not stat.S_ISREG(mode)
    or not os.access(candidate, os.X_OK)
    or not any(
        resolved != base
        and os.path.commonpath((str(resolved), str(base))) == str(base)
        for base in bases
    )
):
    raise SystemExit(1)
print(resolved)
PY
}
if [ "$TEST_MODE" = "1" ]; then
  LIB_ROOT=$(canonical_test_root "$LIB_ROOT") || {
    echo '{"ok":false,"error":"test-mode install roots must resolve beneath a temporary root"}'
    exit 1
  }
  SYSTEMD_ROOT=$(canonical_test_root "$SYSTEMD_ROOT") || {
    echo '{"ok":false,"error":"test-mode install roots must resolve beneath a temporary root"}'
    exit 1
  }
  RUNTIME_ROOT=$(canonical_test_root "$RUNTIME_ROOT") || {
    echo '{"ok":false,"error":"test-mode install roots must resolve beneath a temporary root"}'
    exit 1
  }
  [ -n "$SYSTEMCTL" ] || {
    echo '{"ok":false,"error":"test mode requires an explicit temporary systemctl executable"}'
    exit 1
  }
  SYSTEMCTL=$(canonical_test_executable "$SYSTEMCTL") || {
    echo '{"ok":false,"error":"test mode systemctl must be a temporary regular executable"}'
    exit 1
  }
else
  SYSTEMCTL=${SYSTEMCTL:-systemctl}
fi
WORKER_TARGET="$LIB_ROOT/hermes-evening-feedback"
SERVICE_TARGET="$SYSTEMD_ROOT/hermes-evening-feedback.service"
TIMER_TARGET="$SYSTEMD_ROOT/hermes-evening-feedback.timer"

if [ "$MODE" = enable ]; then
  if [ "${HERMES_AUTONOMOUS_ENABLE_APPROVED:-}" != "YES" ]; then
    echo '{"ok":false,"error":"--enable requires HERMES_AUTONOMOUS_ENABLE_APPROVED=YES explicit gate"}'
    exit 1
  fi
  [ -f "$WORKER_TARGET" ] && [ -f "$SERVICE_TARGET" ] && \
  [ -f "$TIMER_TARGET" ] && [ ! -L "$WORKER_TARGET" ] && \
  [ ! -L "$SERVICE_TARGET" ] && [ ! -L "$TIMER_TARGET" ] && \
  cmp -s "$WORKER" "$WORKER_TARGET" && \
  cmp -s "$SERVICE" "$SERVICE_TARGET" && \
  cmp -s "$TIMER" "$TIMER_TARGET" || {
    echo '{"ok":false,"error":"activation requires exact installed-disabled evening feedback artifacts"}'
    exit 1
  }
  if "$SYSTEMCTL" is-enabled --quiet hermes-evening-feedback.timer 2>/dev/null && \
     "$SYSTEMCTL" is-active --quiet hermes-evening-feedback.timer 2>/dev/null; then
    echo '{"ok":true,"status":"explicitly-enabled","idempotent":true}'
    exit 0
  fi
  if "$SYSTEMCTL" enable --now hermes-evening-feedback.timer && \
     "$SYSTEMCTL" is-enabled --quiet hermes-evening-feedback.timer 2>/dev/null && \
     "$SYSTEMCTL" is-active --quiet hermes-evening-feedback.timer 2>/dev/null; then
    echo '{"ok":true,"status":"explicitly-enabled"}'
    exit 0
  fi
  "$SYSTEMCTL" disable --now hermes-evening-feedback.timer >/dev/null 2>&1 || true
  echo '{"ok":false,"error":"evening activation failed and was compensated to disabled where possible"}'
  exit 1
fi

# An earlier activation is outside this install-only contract. Refuse to touch it.
if "$SYSTEMCTL" is-active --quiet hermes-evening-feedback.timer || \
   "$SYSTEMCTL" is-active --quiet hermes-evening-feedback.service || \
   "$SYSTEMCTL" is-enabled --quiet hermes-evening-feedback.timer; then
  echo '{"ok":false,"error":"timer is already active or enabled; refusing install"}'; exit 1
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$
if [ "$TEST_MODE" = "1" ]; then
  install -m 0755 -d "$LIB_ROOT"
  install -m 0755 -d "$SYSTEMD_ROOT"
  install -m 0700 -d "$RUNTIME_ROOT"
else
  install -o root -g root -m 0755 -d "$LIB_ROOT"
  install -o hermes -g hermes -m 0700 -d "$RUNTIME_ROOT"
fi
for target in "$WORKER_TARGET" "$SERVICE_TARGET" "$TIMER_TARGET"; do
  if [ -e "$target" ]; then cp -p "$target" "$target.bak.$STAMP"; fi
done
if [ "$TEST_MODE" = "1" ]; then
  install -m 0755 "$WORKER" "$WORKER_TARGET"
  install -m 0644 "$SERVICE" "$SERVICE_TARGET"
  install -m 0644 "$TIMER" "$TIMER_TARGET"
else
  install -o root -g root -m 0755 "$WORKER" "$WORKER_TARGET"
  install -o root -g root -m 0644 "$SERVICE" "$SERVICE_TARGET"
  install -o root -g root -m 0644 "$TIMER" "$TIMER_TARGET"
fi
"$SYSTEMCTL" daemon-reload

if "$SYSTEMCTL" is-active --quiet hermes-evening-feedback.timer || \
   "$SYSTEMCTL" is-active --quiet hermes-evening-feedback.service || \
   "$SYSTEMCTL" is-enabled --quiet hermes-evening-feedback.timer; then
  echo '{"ok":false,"error":"post-install disabled-state verification failed"}'; exit 1
fi
echo "{\"ok\":true,\"status\":\"installed-disabled\",\"backup_stamp\":\"$STAMP\"}"
