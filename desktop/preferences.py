"""Small workspace-owned browser preferences in the existing panel-state DB."""
import json
import re

THEMES = {"paper", "ember", "alpine", "verdant", "sunbeam", "ridge",
          "grove", "timber", "glacier", "canyon", "cyber", "cosmos"}
KEYS = {"panel-theme", "hermes.dashboard.analysis-job", "hermes.insight.analysis-job",
        # The retained report workspace has exactly the pain/mobility lenses.
        "hermes.insight.conversation", "hermes.pain.conversation",
        "hermes.mobility.conversation"}
PREFIX = "desktop-browser:"


def valid(key, value):
    if key not in KEYS:
        return False
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    if key == "panel-theme":
        return value in THEMES
    if key.endswith("analysis-job"):
        return re.fullmatch(r"[0-9a-f]{32}", value) is not None
    return re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) is not None


def read():
    from app.panel_db import get_db
    values = {}
    for row in get_db().execute("SELECT key,value FROM settings WHERE key LIKE ?", (PREFIX + "%",)):
        key = row["key"][len(PREFIX):]
        if valid(key, row["value"]):
            values[key] = row["value"]
    return values


def write(key, value):
    from app.panel_db import get_db
    if not valid(key, value):
        raise ValueError("Unsupported display preference")
    db = get_db()
    if value is None:
        db.execute("DELETE FROM settings WHERE key=?", (PREFIX + key,))
    else:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (PREFIX + key, value))
    db.commit()


def bootstrap_script():
    payload = json.dumps(read(), ensure_ascii=True)
    keys = json.dumps(sorted(KEYS))
    return """'use strict';
(() => {
  const saved = %s;
  const allowed = new Set(%s);
  const originalSet = Storage.prototype.setItem;
  const originalRemove = Storage.prototype.removeItem;
  for (const key of allowed) {
    if (Object.prototype.hasOwnProperty.call(saved, key)) originalSet.call(localStorage, key, saved[key]);
    else originalRemove.call(localStorage, key);
  }
  // Serialize updates so an older theme/task save cannot win a request race.
  let pending = Promise.resolve();
  const persist = (key, value) => {
    pending = pending.catch(() => {}).then(() => fetch('/desktop/api/preferences', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content},
      body: JSON.stringify({key, value}),
    }));
  };
  Storage.prototype.setItem = function(key, value) {
    originalSet.call(this, key, value);
    if (this === localStorage && allowed.has(key)) persist(key, String(value));
  };
  Storage.prototype.removeItem = function(key) {
    originalRemove.call(this, key);
    if (this === localStorage && allowed.has(key)) persist(key, null);
  };
})();
""" % (payload, keys)
