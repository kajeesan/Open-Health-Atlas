#!/bin/sh
# Stage the OHA-owned Hermes plugin. This script does not enable it or restart.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_DIR="$SCRIPT_DIR/hermes-openhealthatlas-delivery-plugin"
SELECTED_HERMES_HOME=${HERMES_HOME:-}

if [ "${1:-}" = "--hermes-home" ]; then
  [ "$#" -eq 2 ] || {
    echo "usage: $0 --hermes-home ABSOLUTE_PATH" >&2
    exit 2
  }
  SELECTED_HERMES_HOME=$2
elif [ "$#" -ne 0 ] || [ -z "$SELECTED_HERMES_HOME" ]; then
  echo "usage: $0 --hermes-home ABSOLUTE_PATH" >&2
  echo "Hermes home must be explicit; set HERMES_HOME or pass --hermes-home." >&2
  exit 2
fi

case "$SELECTED_HERMES_HOME" in
  /*) ;;
  *) echo "Hermes home must be an absolute path" >&2; exit 2 ;;
esac
[ "$SELECTED_HERMES_HOME" != "/" ] || {
  echo "Refusing filesystem root as Hermes home" >&2
  exit 2
}
[ -d "$SELECTED_HERMES_HOME" ] && [ ! -L "$SELECTED_HERMES_HOME" ] || {
  echo "Hermes home must be an existing non-symlink directory" >&2
  exit 2
}

PLUGINS_DIR="$SELECTED_HERMES_HOME/plugins"
[ -d "$PLUGINS_DIR" ] && [ ! -L "$PLUGINS_DIR" ] || {
  echo "Hermes plugins directory must be an existing non-symlink directory" >&2
  exit 2
}

TARGET="$PLUGINS_DIR/openhealthatlas-recovery-delivery"
[ ! -e "$TARGET" ] && [ ! -L "$TARGET" ] || {
  echo "Refusing to replace existing plugin target: $TARGET" >&2
  exit 2
}

STAGE=$(mktemp -d "$PLUGINS_DIR/.openhealthatlas-recovery-delivery.XXXXXX")
cleanup() {
  if [ -n "${STAGE:-}" ] && [ -d "$STAGE" ]; then
    rm -rf -- "$STAGE"
  fi
}
trap cleanup EXIT HUP INT TERM

install -m 0644 "$SOURCE_DIR/__init__.py" "$STAGE/__init__.py"
install -m 0644 "$SOURCE_DIR/plugin.yaml" "$STAGE/plugin.yaml"
install -m 0644 "$SOURCE_DIR/README.md" "$STAGE/README.md"
install -m 0644 "$SOURCE_DIR/enablement.example.yaml" \
  "$STAGE/enablement.example.yaml"
mv -- "$STAGE" "$TARGET"
STAGE=
trap - EXIT HUP INT TERM

echo "Installed disabled plugin at $TARGET"
echo "No config was changed and no service was restarted."
echo "For a separately approved deployment, merge:"
echo "  $TARGET/enablement.example.yaml"
echo "into:"
echo "  $SELECTED_HERMES_HOME/config.yaml"
