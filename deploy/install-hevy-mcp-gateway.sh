#!/bin/sh
# Install the protected local Hevy MCP routine-publishing gateway.
# Usage: sudo /opt/hermes/deploy/install-hevy-mcp-gateway.sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0" >&2; exit 1; }

KEY=${HERMES_HEVY_KEY_FILE:-/etc/hermes/hevy.env}
MODE=$(stat -c '%U %G %a' "$KEY" 2>/dev/null || echo absent)
[ "$MODE" = "root root 600" ] || {
  echo "FAIL: $KEY must be root:root 0600, is: $MODE" >&2
  exit 1
}
grep -qE '^HEVY_API_KEY=.+' "$KEY" || {
  echo "FAIL: HEVY_API_KEY missing/empty in $KEY" >&2
  exit 1
}

NODE=${HERMES_HEVY_NODE:-$(command -v node || true)}
NPM=${HERMES_HEVY_NPM:-$(command -v npm || true)}
[ -x "$NODE" ] && [ -x "$NPM" ] || {
  echo "FAIL: Node/npm runtime is missing (Node 20+ required)" >&2
  exit 1
}
MAJOR=$("$NODE" -p 'Number(process.versions.node.split(".")[0])')
[ "$MAJOR" -ge 20 ] || {
  echo "FAIL: hevy-mcp requires Node 20+, found $MAJOR" >&2
  exit 1
}

VERSION=3.4.1
EXPECTED_INTEGRITY='sha512-vyzjCPUARPjO0WLXNl3bRHiMyEjcCo+oTv3V3NOsiY1bBv3oiGgXiLfWrx7Qwys6408zGLXct+ja6N7hEFFn3A=='
ACTUAL_INTEGRITY=$("$NPM" view "hevy-mcp@$VERSION" dist.integrity)
[ "$ACTUAL_INTEGRITY" = "$EXPECTED_INTEGRITY" ] || {
  echo "FAIL: npm integrity metadata for hevy-mcp@$VERSION changed" >&2
  exit 1
}

ROOT=/usr/local/lib/hermes-hevy-mcp
PACKAGE="$ROOT/package"
getent passwd hermes-hevy-mcp >/dev/null || \
  useradd --system --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin hermes-hevy-mcp
install -o root -g root -m 0755 -d "$ROOT" "$PACKAGE"
"$NPM" install \
  --prefix "$PACKAGE" \
  --omit=dev --ignore-scripts --no-audit --no-fund --save-exact \
  "hevy-mcp@$VERSION"
INSTALLED=$("$NODE" -p "require('$PACKAGE/node_modules/hevy-mcp/package.json').version")
[ "$INSTALLED" = "$VERSION" ] || {
  echo "FAIL: installed hevy-mcp version is $INSTALLED" >&2
  exit 1
}
chown -R root:root "$PACKAGE"
chmod -R go-w "$PACKAGE"

install -o root -g root -m 0755 \
  /opt/hermes/deploy/hermes-hevy-mcp-gateway \
  "$ROOT/hermes-hevy-mcp-gateway"
install -o root -g root -m 0755 \
  /opt/hermes/deploy/hermes-hevy-mcp-client \
  "$ROOT/hermes-hevy-mcp-client"
install -o root -g root -m 0644 \
  /opt/hermes/deploy/hermes-hevy-mcp-gateway.service \
  /etc/systemd/system/hermes-hevy-mcp-gateway.service
AGENT_HOME=${HERMES_AGENT_HOME:-/var/lib/hermes-agent}
install -o hermes -g hermes -m 0755 -d "$AGENT_HOME/skills/hevy"
install -o hermes -g hermes -m 0644 \
  /opt/hermes/deploy/hermes-hevy-skill.md \
  "$AGENT_HOME/skills/hevy/SKILL.md"
install -o hermes-hevy-mcp -g hermes -m 0700 -d /var/log/hermes-hevy-mcp
touch /var/log/hermes-hevy-mcp/audit.jsonl
chown hermes-hevy-mcp:hermes /var/log/hermes-hevy-mcp/audit.jsonl
chmod 0600 /var/log/hermes-hevy-mcp/audit.jsonl

systemctl daemon-reload

echo "Installed protected Hevy MCP gateway disabled:"
echo "  package : hevy-mcp@$VERSION"
echo "  skill   : $AGENT_HOME/skills/hevy/SKILL.md"
echo "Validate configuration, then explicitly start or enable the service:"
echo "  sudo systemctl start hermes-hevy-mcp-gateway.service"
echo "  sudo systemctl enable hermes-hevy-mcp-gateway.service"
echo "Hermes still needs the root-owned client command added as MCP server 'hevy'."
