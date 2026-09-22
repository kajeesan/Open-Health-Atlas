"""Laboratory catalog preview and configuration import transaction."""

import json

from .. import migrations, runtime
from ..command_context import CommandContext
from ..importers.lab_catalog import parse_catalog, LAB_CATALOG_NOTE


def import_catalog(context: CommandContext, path, *, seed):
    """Preview or seed catalog entries while preserving entries absent from the file."""
    with open(path, encoding="utf-8") as f:
        proposed = parse_catalog(f.read())
    c = runtime.connect(context.database)
    try:
        with c:
            migrations.require_table(c, "lab_catalog")
            existing = {r["canonical"] for r in c.execute("SELECT canonical FROM lab_catalog")}
            for p in proposed:
                p["replaces"] = p["canonical"] in existing
            panels = sorted({p["panel"] for p in proposed})
            base = {"dry_run": not seed, "catalog_file": path, "proposed": proposed,
                    "totals": {"tests": len(proposed), "panels": len(panels)},
                    "note": LAB_CATALOG_NOTE}
            if not seed:
                return base
            for p in proposed:
                c.execute(
                    "INSERT OR REPLACE INTO lab_catalog(canonical, display, panel, unit,"
                    " ref_low, ref_high, plaus_low, plaus_high, max_delta, delta_kind,"
                    " aliases, source, confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'cited')",
                    (p["canonical"], p["display"], p["panel"], p["unit"],
                     p["ref_low"], p["ref_high"], p["plaus_low"], p["plaus_high"],
                     p["max_delta"], p["delta_kind"], json.dumps(p["aliases"]), p["source"]))
            c.commit()
            return ({**base, "seeded_tests": len(proposed)})
    finally:
        c.close()
