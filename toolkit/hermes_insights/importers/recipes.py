"""Cronometer recipe CSV decoding and recipe-total normalization."""

import csv
import re


def read_csv(path, *, per_gram, per_serving, multiplier, parse_number, slug):
    """Yield recipe rows after checking that the export amount matches the explicit mode."""
    META = {"Food ID", "Food Name", "Comments", "Amount"}
    with open(path, newline="") as f:
        rd = csv.DictReader(f)
        if not rd.fieldnames or "Food Name" not in rd.fieldnames:
            raise SystemExit("recipe CSV must contain a Food Name column")
        ncols = [h for h in rd.fieldnames if h not in META]
        for row in rd:
            amount = (row.get("Amount") or "").strip().casefold()
            amount_is_gram = bool(re.fullmatch(r"(?:1(?:\.0+)?)?\s*g(?:ram)?s?", amount))
            amount_is_serving = "serving" in amount
            if amount_is_gram and not per_gram:
                raise SystemExit("CSV contains per-gram nutrients (Amount is 'g'); "
                         "re-run with --per-gram --batch-grams G")
            if amount_is_serving and not per_serving:
                raise SystemExit("CSV contains per-serving nutrients; "
                         "re-run with --per-serving --servings N")
            if per_gram and not amount_is_gram:
                raise SystemExit("--per-gram requires each CSV Amount to identify grams")
            if per_serving and not amount_is_serving:
                raise SystemExit("--per-serving requires each CSV Amount to identify a serving")
            name = row["Food Name"]
            rid = slug(name)
            yield rid, name, row.get("Comments") or None, _nutrients(
                row, ncols, multiplier, parse_number=parse_number)


def _nutrients(row, ncols, multiplier, *, parse_number):
    for col in ncols:
        v = parse_number(row[col])
        if v is None: continue
        # Keep deterministic decimal CSV scaling free of binary-float
        # tails (e.g. 0.10 × 3157 must store as 315.7, not
        # 315.70000000000005).
        v = round(v * multiplier, 9)
        m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", col)
        nutrient, unit = (m.group(1), m.group(2)) if m else (col, "")
        yield nutrient.strip(), unit, v
