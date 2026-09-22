"""Cronometer daily-nutrient import transaction."""

from .. import migrations, runtime
from ..catalogs import MICRO_SEED
from ..command_context import CommandContext
from ..importers.cronometer import read_csv


def import_csv(context: CommandContext, path, *, parse_number):
    """Upsert daily nutrients by source without deleting prior observations."""
    c = runtime.connect(context.database)
    try:
        with c:
            migrations.require_table(c, "nutrient_daily")
            migrations.require_table(c, "nutrition_targets")
            rows, summary = read_csv(path, parse_number=parse_number)
            written = 0
            for d, vals in rows:
                for key, v in vals.items():
                    unit = next((m["unit"] for m in MICRO_SEED if m["key"] == key),
                                "kcal" if key == "energy_kcal" else "g")
                    c.execute("""INSERT INTO nutrient_daily(date, nutrient, amount, unit, source)
                        VALUES(?,?,?,?, 'cronometer')
                        ON CONFLICT(date, nutrient, source) DO UPDATE SET
                          amount=excluded.amount, unit=excluded.unit,
                          ingested_at=datetime('now')""", (d, key, v, unit))
                    written += 1
            c.commit()
            return ({"ok": True, "days": summary["days"], "values_written": written,
                 **{key: value for key, value in summary.items() if key != "days"}})
    finally:
        c.close()
