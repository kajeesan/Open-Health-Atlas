"""Recipe import validation and atomic configuration writes."""

from .. import migrations, runtime
from ..command_context import CommandContext
from ..importers.recipes import read_csv


def import_csv(context: CommandContext, a, *, parse_number, slug, meal_types):
    """Import recipe totals and optional batch metadata in one transaction."""
    if a.per_serving and a.per_gram:
        raise SystemExit("--per-serving and --per-gram are mutually exclusive")
    if a.servings is not None and not a.per_serving:
        raise SystemExit("--servings requires --per-serving")
    if a.per_serving:
        if a.servings is None: raise SystemExit("--per-serving requires --servings N")
        if a.servings < 1: raise SystemExit("--servings must be >= 1")
    if a.per_gram and a.batch_grams is None:
        raise SystemExit("--per-gram requires --batch-grams G")
    if a.batch_grams is not None and a.batch_grams <= 0:
        raise SystemExit("--batch-grams must be > 0")
    if a.portions is not None:
        if a.portions < 1: raise SystemExit("--portions must be >= 1")
        if a.batch_grams is None: raise SystemExit("--portions requires --batch-grams G")
    if a.meal_type is not None and a.meal_type not in meal_types:
        raise SystemExit(f"--meal-type must be one of: {', '.join(meal_types)}")

    mult = a.batch_grams if a.per_gram else (a.servings if a.per_serving else 1)
    c = runtime.connect(context.database)
    try:
        with c:
            loaded = []
            if a.meal_type is not None:
                migrations.require_table(c, "recipes", ("meal_type",))
            for rid, name, notes, nutrients in read_csv(
                    a.csv, per_gram=a.per_gram, per_serving=a.per_serving,
                    multiplier=mult, parse_number=parse_number, slug=slug):
                c.execute("""INSERT INTO recipes(recipe_id,name,notes,source)
                             VALUES(?,?,?, 'cronometer')
                             ON CONFLICT(recipe_id) DO UPDATE SET
                               name=excluded.name, notes=excluded.notes, source=excluded.source""",
                          (rid, name, notes))
                c.execute("DELETE FROM recipe_nutrients WHERE recipe_id=?", (rid,))
                for nutrient, unit, value in nutrients:
                    c.execute("INSERT OR REPLACE INTO recipe_nutrients(recipe_id,nutrient,unit,per_gram) VALUES(?,?,?,?)",
                              (rid, nutrient, unit, value))
                if a.batch_grams is not None:
                    if a.portions is None:
                        c.execute("UPDATE recipes SET batch_grams=? WHERE recipe_id=?",
                                  (a.batch_grams, rid))
                    else:
                        gpp = round(a.batch_grams / a.portions, 1)
                        c.execute("""UPDATE recipes
                                     SET batch_grams=?, portions=?, grams_per_portion=?
                                     WHERE recipe_id=?""",
                                  (a.batch_grams, a.portions, gpp, rid))
                if a.meal_type is not None:
                    c.execute("UPDATE recipes SET meal_type=? WHERE recipe_id=?",
                              (a.meal_type, rid))
                loaded.append(name)
            c.commit()
            res = {"ok": True, "recipes_loaded": loaded}
            if a.batch_grams is None:
                res["next"] = ("run `set-batch <recipe> --grams <cooked weight>` "
                               "so per-gram nutrition can be computed")
            if a.per_serving: res.update({"per_serving": True, "servings": a.servings})
            if a.per_gram: res.update({"per_gram": True, "batch_grams": a.batch_grams})
            if a.portions is not None:
                res.update({"portions": a.portions,
                            "grams_per_portion": round(a.batch_grams / a.portions, 1)})
            if a.meal_type is not None: res["meal_type"] = a.meal_type
            return (res)
    finally:
        c.close()
