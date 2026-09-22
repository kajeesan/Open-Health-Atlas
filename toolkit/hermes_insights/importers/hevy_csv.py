"""Normalize the retained Hevy workout CSV format into metric records."""

import csv
from datetime import datetime


LB_TO_KG = 0.45359237
MI_TO_KM = 1.609344


def parse_date(value):
    """Return an ISO day for supported export dates, or None for unknown dates."""
    for format_string in (
        "%b %d, %Y at %I:%M %p", "%d %b %Y, %H:%M", "%d %b %Y at %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    ):
        try:
            return datetime.strptime((value or "").strip(), format_string).strftime("%Y-%m-%d")
        except (AttributeError, TypeError, ValueError):
            # An unrecognized CSV date remains missing, as in the legacy import.
            continue
    return None


def replace_history(connection, path, *, parse_number):
    """Replace only Hevy rows without committing the caller's transaction.

    Empty exports clear Hevy history. Unrecognized optional values remain missing;
    invalid integer fields raise. The caller must roll back any failed import.
    """
    # Each export is the full HEVY history -> reload hevy rows only. Sets logged
    # through the panel (source='ui'/'chat-panel') must survive reimports.
    connection.execute("DELETE FROM hevy_sets WHERE source='hevy'")
    count = 0
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        weight_key = "weight_kg" if "weight_kg" in columns else (
            "weight_lbs" if "weight_lbs" in columns else None
        )
        weight_multiplier = 1.0 if weight_key == "weight_kg" else LB_TO_KG
        distance_key = "distance_km" if "distance_km" in columns else (
            "distance_miles" if "distance_miles" in columns else None
        )
        distance_multiplier = 1.0 if distance_key == "distance_km" else MI_TO_KM
        for row in reader:
            day = parse_date(row.get("start_time"))
            weight = parse_number(row.get(weight_key)) if weight_key else None
            if weight is not None:
                weight = round(weight * weight_multiplier, 2)
            distance = parse_number(row.get(distance_key)) if distance_key else None
            if distance is not None:
                distance = round(distance * distance_multiplier, 3)
            connection.execute(
                """INSERT INTO hevy_sets(date,workout_title,start_time,end_time,description,exercise_title,
                  superset_id,exercise_notes,set_index,set_type,weight_kg,reps,distance_km,duration_seconds,rpe)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (day, row.get("title"), row.get("start_time"), row.get("end_time"),
                 row.get("description"), row.get("exercise_title"),
                 row.get("superset_id") or None, row.get("exercise_notes"),
                 int(float(row["set_index"])) if row.get("set_index") else None,
                 row.get("set_type"), weight,
                 int(float(row["reps"])) if row.get("reps") else None,
                 distance, parse_number(row.get("duration_seconds")), parse_number(row.get("rpe"))),
            )
            count += 1
    return {"ok": True, "imported_sets": count, "weight_source": weight_key, "converted_to": "kg/km"}
