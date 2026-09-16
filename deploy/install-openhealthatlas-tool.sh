#!/bin/sh
# Install the bounded external-Hermes -> OpenHealthAtlas tool adapter.
# Usage: sudo /opt/openhealthatlas-fictional/deploy/install-openhealthatlas-tool.sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }

ROOT=/usr/local/lib/hermes-openhealthatlas-fictional
AGENT_HOME=${HERMES_AGENT_HOME:-/var/lib/hermes-agent}
FIXTURE_ROOT=/var/lib/openhealthatlas-fictional
FIXTURE_DB=$FIXTURE_ROOT/health.db
FIXTURE_VAULT=$FIXTURE_ROOT/vault
FIXTURE_MANIFEST=$FIXTURE_ROOT/fixture-manifest.json
TRUSTED_MANIFEST=$ROOT/fixture-manifest.json
FIXTURE_BUILDER=/opt/openhealthatlas-fictional/scripts/demo_flow.py
FIXTURE_ID=green-days-actionable-v1
HERMES_PYTHON=/opt/hermes/.venv/bin/python
TOOL_AUDIT=/var/lib/hermes/audit/openhealthatlas-tools.jsonl

[ -x "$HERMES_PYTHON" ] || {
  echo "Missing project Python environment: $HERMES_PYTHON" >&2
  exit 1
}

BUILT_NOW=0
if [ ! -e "$FIXTURE_ROOT" ]; then
  [ -f "$FIXTURE_BUILDER" ] || {
    echo "Missing fixture builder: $FIXTURE_BUILDER" >&2
    exit 1
  }
  install -o hermes -g hermes -m 0700 -d "$FIXTURE_ROOT"
  runuser -u hermes -- env -i \
    PATH=/usr/local/bin:/usr/bin:/bin \
    LANG=C.UTF-8 \
    HERMES_TIMEZONE=UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    "$HERMES_PYTHON" "$FIXTURE_BUILDER" \
    --data-dir "$FIXTURE_ROOT" --fixture-id "$FIXTURE_ID" --seed-only
  BUILT_NOW=1
fi

[ -d "$FIXTURE_ROOT" ] && [ ! -L "$FIXTURE_ROOT" ] && \
  [ -d "$FIXTURE_VAULT" ] && [ ! -L "$FIXTURE_VAULT" ] && \
  [ -f "$FIXTURE_DB" ] && [ ! -L "$FIXTURE_DB" ] && \
  [ -f "$FIXTURE_MANIFEST" ] && [ ! -L "$FIXTURE_MANIFEST" ] || {
  echo "Missing or partial fictional fixture: $FIXTURE_ROOT" >&2
  exit 1
}

install -o root -g root -m 0755 -d "$ROOT"
if [ -e "$TRUSTED_MANIFEST" ]; then
  [ -f "$TRUSTED_MANIFEST" ] && [ ! -L "$TRUSTED_MANIFEST" ] && \
    [ "$(stat -c %u "$TRUSTED_MANIFEST")" -eq 0 ] && \
    [ $((0$(stat -c %a "$TRUSTED_MANIFEST") & 0022)) -eq 0 ] || {
    echo "Installed fixture manifest is not trusted: $TRUSTED_MANIFEST" >&2
    exit 1
  }
  VERIFY_MANIFEST=$TRUSTED_MANIFEST
else
  [ "$BUILT_NOW" -eq 1 ] || {
    echo "Refusing a pre-existing fixture without a root-owned seed receipt" >&2
    exit 1
  }
  VERIFY_MANIFEST=$FIXTURE_MANIFEST
fi

"$HERMES_PYTHON" "$FIXTURE_BUILDER" \
  --data-dir "$FIXTURE_ROOT" \
  --fixture-id "$FIXTURE_ID" \
  --verify-fixture "$VERIFY_MANIFEST" \
  --audit "$TOOL_AUDIT"

# Bind to the authenticated fixture's actual schema, including retained v6
# installations. Installing adapter files must never migrate or relabel data.
TOOL_CONFIG_TMP="$(mktemp)"
trap 'rm -f "$TOOL_CONFIG_TMP"' EXIT
"$HERMES_PYTHON" - "$FIXTURE_DB" > "$TOOL_CONFIG_TMP" <<'PYCONFIG'
import json
from pathlib import Path
import sys
sys.path.insert(0, "/opt/openhealthatlas-fictional/toolkit")
from hermes_insights.migrations import schema_status
version = schema_status(sys.argv[1])["current_version"]
if version not in {6, 7}:
    raise SystemExit("fictional tool requires an initialized schema-v6 or schema-v7 fixture")
config = json.loads(Path("/opt/openhealthatlas-fictional/deploy/hermes-openhealthatlas-tool-config.json").read_text())
config["readiness_fixture_lane"] = {6: "development-v6", 7: "development-v7"}[version]
print(json.dumps(config, indent=2, sort_keys=True))
PYCONFIG

for target in \
  "$ROOT/hermes-openhealthatlas-tool" \
  "$ROOT/tool-config.json" \
  "$TRUSTED_MANIFEST" \
  "$FIXTURE_ROOT" \
  "$FIXTURE_VAULT" \
  "$FIXTURE_DB" \
  "$FIXTURE_MANIFEST" \
  "$TOOL_AUDIT"
do
  [ ! -L "$target" ] || { echo "Refusing symlink target: $target" >&2; exit 1; }
done

chown hermes:hermes "$FIXTURE_ROOT" "$FIXTURE_DB" "$FIXTURE_VAULT" "$FIXTURE_MANIFEST"
chmod 0700 "$FIXTURE_ROOT" "$FIXTURE_VAULT"
chmod 0600 "$FIXTURE_DB" "$FIXTURE_MANIFEST"

install -o root -g root -m 0755 \
  /opt/openhealthatlas-fictional/deploy/hermes-openhealthatlas-tool \
  "$ROOT/hermes-openhealthatlas-tool"
install -o root -g root -m 0644 \
  "$TOOL_CONFIG_TMP" \
  "$ROOT/tool-config.json"
install -o root -g root -m 0644 \
  /opt/openhealthatlas-fictional/deploy/hermes-bridge.service \
  /etc/systemd/system/hermes-bridge.service
if [ ! -e "$TRUSTED_MANIFEST" ]; then
  install -o root -g root -m 0644 "$FIXTURE_MANIFEST" "$TRUSTED_MANIFEST"
fi

install -o hermes -g hermes -m 0755 -d \
  "$AGENT_HOME/skills/openhealthatlas"
install -o hermes -g hermes -m 0644 \
  /opt/openhealthatlas-fictional/deploy/hermes-openhealthatlas-skill.md \
  "$AGENT_HOME/skills/openhealthatlas/SKILL.md"

install -o hermes -g hermes -m 0700 -d /var/lib/hermes/audit
touch "$TOOL_AUDIT"
chown hermes:hermes "$TOOL_AUDIT"
chmod 0600 "$TOOL_AUDIT"

systemctl daemon-reload
if systemctl is-active --quiet hermes-bridge.service; then
  systemctl restart hermes-bridge.service
fi

echo "Installed bounded OpenHealthAtlas tool integration:"
echo "  tool  : $ROOT/hermes-openhealthatlas-tool"
echo "  skill : $AGENT_HOME/skills/openhealthatlas/SKILL.md"
echo "  seed  : $TRUSTED_MANIFEST"
echo "  broker: systemd sandbox allows only the fictional fixture addition"
echo "The adapter is bound to the configured fictional database and contains no model runtime."
