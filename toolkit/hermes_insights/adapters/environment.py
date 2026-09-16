"""Generated canonical calendar and exact weather/air observations."""

from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Any

from . import (
    DefinitionIndex, REGISTRY_VERSION, context_value, date_where, finite,
    in_range, iter_dates, make_observation, natural_key, range_bounds,
    resolved_identity_parts, table_columns,
)
from ..settings import TIMEZONE_NAME


ADAPTER_ID = "environment"
WEATHER_METRICS = (
    "cloud_cover_mean_pct", "condition", "daylight_hours", "et0_mm",
    "feels_like_max_c", "feels_like_min_c", "humidity_mean_pct",
    "precipitation_hours", "precipitation_mm", "pressure_mean_hpa", "rain_mm",
    "snowfall_cm", "solar_radiation_mj", "sunrise_min", "sunset_min",
    "sunshine_hours", "temp_max_c", "temp_min_c", "uv_index_clear_sky_max",
    "uv_index_max", "weather_code", "wind_dir_cos", "wind_dir_sin",
    "wind_gusts_max_ms", "wind_speed_max_ms",
)
AIR_METRICS = (
    "alder_pollen", "birch_pollen", "co_ugm3", "european_aqi_max",
    "european_aqi_mean", "grass_pollen", "mugwort_pollen", "no2_ugm3",
    "olive_pollen", "ozone_ugm3", "pm10_ugm3", "pm2_5_ugm3",
    "ragweed_pollen", "so2_ugm3",
)


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _emit(
    out, definitions, key, day, value, *, source=None, provenance=None,
    include_provenance=False,
):
    if key is None or key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, day, value, source=source, provenance=provenance,
        include_provenance=include_provenance,
    ))


def _calendar_days(conn, date_range):
    days = iter_dates(date_range)
    if days:
        return days
    # `range=all` has no artificial epoch.  Its generated calendar spans the
    # real dated source inventory and therefore cannot create observation-only
    # dates that never existed in any registered domain.
    lower, upper = None, None
    for table_row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        table = table_row[0]
        try:
            columns = table_columns(conn, table)
        except ValueError:
            continue
        if "date" not in columns:
            continue
        bounds = conn.execute(
            f"SELECT MIN(date),MAX(date) FROM {table} WHERE date IS NOT NULL"
        ).fetchone()
        if not bounds or bounds[0] is None:
            continue
        lower = bounds[0][:10] if lower is None else min(lower, bounds[0][:10])
        upper = bounds[1][:10] if upper is None else max(upper, bounds[1][:10])
    if lower is None or upper is None:
        return []
    start, end = date.fromisoformat(lower), date.fromisoformat(upper)
    return [(start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)]


def _load_calendar(conn, definitions, date_range, out, include_provenance):
    for day in _calendar_days(conn, date_range):
        current = date.fromisoformat(day)
        month = current.month
        values = {
            "calendar.weekday": current.weekday(),
            "calendar.weekend": int(current.weekday() >= 5),
            "calendar.month": month,
            "calendar.quarter": (month - 1) // 3 + 1,
            "calendar.season": (
                "winter" if month in {12, 1, 2} else
                "spring" if month in {3, 4, 5} else
                "summer" if month in {6, 7, 8} else "autumn"
            ),
        }
        for key, value in values.items():
            _emit(out, definitions, key, day, value, source="generated_calendar",
                  provenance={
                      "adapter": ADAPTER_ID, "formula": key.replace("calendar.", "calendar_") + "_v1",
                      "timezone": TIMEZONE_NAME, "generated_from_date": day,
                  }, include_provenance=include_provenance)


def _weather_value(row, metric, hhmm):
    if metric == "sunrise_min":
        return hhmm(row.get("sunrise")) if hhmm else None
    if metric == "sunset_min":
        return hhmm(row.get("sunset")) if hhmm else None
    if metric in {"wind_dir_sin", "wind_dir_cos"}:
        degrees = finite(row.get("wind_dir_deg"))
        if degrees is None:
            return None
        radians = math.radians(degrees % 360)
        return math.sin(radians) if metric.endswith("sin") else math.cos(radians)
    return row.get(metric)


def _load_weather(conn, definitions, date_range, context, out, include_provenance):
    columns = table_columns(conn, "weather")
    if not {"date", "location"} <= columns:
        return
    where, params = date_where("date", date_range)
    hhmm = context_value(context, "hhmm_min") or context_value(context, "_hhmm_min")
    for row in _rows(conn, "SELECT * FROM weather WHERE " + where + " ORDER BY date,location", params):
        label = row.get("location")
        if not isinstance(label, str) or not label.strip():
            continue
        identity_key, token, _normalized, revision = resolved_identity_parts(
            conn, "location", label)
        provenance = {
            "adapter": ADAPTER_ID, "table": "weather",
            "natural_key": natural_key("weather", row, "date", "location"),
            "identity_key": identity_key, "original_label": label,
            "alias_revision_id": revision,
            "wind_direction_raw_degrees_retained_as_provenance": row.get("wind_dir_deg"),
        }
        for metric in WEATHER_METRICS:
            source_column = {
                "sunrise_min": "sunrise", "sunset_min": "sunset",
                "wind_dir_sin": "wind_dir_deg", "wind_dir_cos": "wind_dir_deg",
            }.get(metric, metric)
            if source_column not in columns:
                continue
            key = definitions.identity_key(
                f"weather.{token}.{metric}", prefix="weather.", suffix=f".{metric}",
                identity_key=identity_key,
            )
            _emit(out, definitions, key, row["date"],
                  _weather_value(row, metric, hhmm),
                  source=row.get("source") or "weather", provenance=provenance,
                  include_provenance=include_provenance)


def _load_air(conn, definitions, date_range, out, include_provenance):
    columns = table_columns(conn, "air_quality")
    if not {"date", "location"} <= columns:
        return
    where, params = date_where("date", date_range)
    for row in _rows(conn, "SELECT * FROM air_quality WHERE " + where + " ORDER BY date,location", params):
        label = row.get("location")
        if not isinstance(label, str) or not label.strip():
            continue
        identity_key, token, _normalized, revision = resolved_identity_parts(
            conn, "location", label)
        provenance = {
            "adapter": ADAPTER_ID, "table": "air_quality",
            "natural_key": natural_key("air_quality", row, "date", "location"),
            "identity_key": identity_key, "original_label": label,
            "alias_revision_id": revision,
        }
        for metric in AIR_METRICS:
            if metric not in columns:
                continue
            key = definitions.identity_key(
                f"air.{token}.{metric}", prefix="air.", suffix=f".{metric}",
                identity_key=identity_key,
            )
            _emit(out, definitions, key, row["date"], row.get(metric),
                  source=row.get("source") or "air", provenance=provenance,
                  include_provenance=include_provenance)


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    # Calendar entries are STATIC_CONFIG/LP_NONE registry context.  They are
    # intentionally not observations and therefore never inflate daily N.
    _load_weather(conn, definitions, date_range, context, out, include_provenance)
    _load_air(conn, definitions, date_range, out, include_provenance)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = ["ADAPTER_ID", "AIR_METRICS", "REGISTRY_VERSION", "WEATHER_METRICS", "load"]
