"""Body and protocol-comparable fitness/mobility measurement episodes."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import statistics
from typing import Any, Mapping

from . import (
    DefinitionIndex, REGISTRY_VERSION, context_value, finite, has_columns,
    in_range, make_observation, natural_key, range_bounds, table_columns,
)
from ..normalize import normalized_label


ADAPTER_ID = "quarterly"
FITNESS_PARENT_MAX_AGE_DAYS = 120
BODY_FIELDS = {
    "weight_kg": ("body.weight_kg", "kg"),
    "waist_cm": ("body.waist_cm", "cm"),
    "chest_cm": ("body.chest_cm", "cm"),
    "arm_cm": ("body.arm_cm", "cm"),
    "thigh_cm": ("body.thigh_cm", "cm"),
    "hip_cm": ("body.hip_cm", "cm"),
    "neck_cm": ("body.neck_cm", "cm"),
    "body_fat_pct": ("body.body_fat_pct", "percent"),
}
KIND_VALUE = {
    "hold": ("seconds", "seconds"), "timed": ("seconds", "seconds"),
    "control": ("rating", "ordinal_1_3"), "distance": ("cm", "cm"),
    "rom": ("degrees", "degrees"), "binary": ("passed", "binary"),
}
MANUAL_BODY_SOURCES = {
    "manual", "ui", "chat", "chat-panel", "chat-telegram", "panel-ui",
}


def episode_comparison(current: Mapping[str, Any], previous: Mapping[str, Any] | None):
    value = current.get("value")
    prior = previous.get("value") if previous else None
    delta = value - prior if finite(value) is not None and finite(prior) is not None else None
    return {
        "prior_date": previous.get("date") if previous else None,
        "prior_value": prior,
        "change": delta,
        "percent_change": 100 * delta / prior if delta is not None and prior > 0 else None,
    }


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _emit(
    out, definitions, key, day, value, *, unit=None, source=None,
    provenance=None, include_provenance=False,
):
    if key not in definitions or value is None:
        return
    if isinstance(value, (int, float)) and finite(value) is None:
        return
    out.append(make_observation(
        definitions, key, day, value, unit=unit, source=source,
        provenance=provenance, include_provenance=include_provenance,
    ))


def _body_rank(row):
    source = (row.get("source") or "").casefold()
    if source in MANUAL_BODY_SOURCES:
        tier = 0
    elif source == "fitbit":
        tier = 1
    elif source == "hevy":
        tier = 2
    else:
        tier = 3
    lexical = source if tier == 3 else ""
    return tier, lexical, -(row.get("id") or 0)


def _selected_body(rows, required):
    candidates = [row for row in rows if all(row.get(column) is not None for column in required)]
    return min(candidates, key=_body_rank) if candidates else None


def _load_body(conn, definitions, date_range, context, include_provenance, out):
    columns = table_columns(conn, "body_metrics")
    if not {"id", "date"} <= columns:
        return
    all_rows = _rows(conn, "SELECT * FROM body_metrics WHERE date IS NOT NULL ORDER BY date,id")
    by_day = defaultdict(list)
    for row in all_rows:
        by_day[row["date"]].append(row)
    series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day, rows in sorted(by_day.items()):
        for column, (key, unit) in BODY_FIELDS.items():
            if column not in columns:
                continue
            chosen = _selected_body(rows, (column,))
            if chosen:
                series[key].append({"date": day, "value": chosen[column],
                                    "row": chosen, "unit": unit})
        if "photo_ref" in columns:
            photos = [row for row in rows if (row.get("photo_ref") or "").strip()]
            if photos:
                chosen = min(photos, key=_body_rank)
                series["body.photo_session"].append({
                    "date": day, "value": 1, "row": chosen, "unit": "binary",
                })
        if {"waist_cm", "chest_cm"} <= columns:
            chosen = _selected_body(rows, ("waist_cm", "chest_cm"))
            if chosen and chosen["chest_cm"] > 0:
                series["body.wcr"].append({
                    "date": day, "value": chosen["waist_cm"] / chosen["chest_cm"],
                    "row": chosen, "unit": "ratio",
                })
    today = getattr(context, "today", date.today())
    for key, items in sorted(series.items()):
        prior = None
        for item in items:
            row = item["row"]
            if in_range(item["date"], date_range):
                _emit(out, definitions, key, item["date"], item["value"],
                      unit=item["unit"], source=row.get("source") or "manual",
                      provenance={
                          "adapter": ADAPTER_ID, "table": "body_metrics",
                          "natural_key": natural_key("body_metrics", row),
                          "source_tier": _body_rank(row)[0],
                          "same_row_wcr": key == "body.wcr",
                          "age_days": (today - date.fromisoformat(item["date"])).days,
                          **episode_comparison(item, prior),
                      }, include_provenance=include_provenance)
            prior = item


def _test_value(row, spec, e1rm_fn):
    kind = spec.get("kind")
    if kind == "strength":
        if e1rm_fn is None or row.get("load_kg") is None or row.get("reps") is None:
            return None, "kg_e1rm"
        return e1rm_fn(row["load_kg"], row["reps"]), "kg_e1rm"
    column, unit = KIND_VALUE.get(kind, (None, None))
    return (row.get(column), unit) if column else (None, None)


def _distance_to_band(value, band):
    if band is None:
        return None
    lo, hi = band
    if lo is not None and value < lo:
        return lo - value
    if hi is not None and value > hi:
        return value - hi
    return 0.0


def _parent_span_days(items, event_date):
    current = date.fromisoformat(event_date)
    oldest = min(date.fromisoformat(item["date"]) for item in items)
    return (current - oldest).days


def _ratio_episodes(valid, seed, side):
    tagged = []
    for item in valid:
        movement = item["row"]["movement"]
        if item["row"]["side"] == side and movement in {seed["num"], seed["den"]}:
            tagged.append((item["date"], item["row"]["id"], movement, item))
    state = {}
    per_day = {}
    for _day, _row_id, movement, item in sorted(tagged):
        state[movement] = item
        numerator, denominator = state.get(seed["num"]), state.get(seed["den"])
        if not numerator or not denominator or denominator["value"] == 0:
            continue
        parents = (numerator, denominator)
        parent_span = _parent_span_days(parents, item["date"])
        if parent_span > FITNESS_PARENT_MAX_AGE_DAYS:
            continue
        per_day[item["date"]] = {
            "date": item["date"],
            "value": numerator["value"] / denominator["value"],
            "numerator": numerator, "denominator": denominator,
            "protocol": (numerator["protocol"], denominator["protocol"]),
            "parent_span_days": parent_span,
        }
    return [per_day[day] for day in sorted(per_day)]


def _ratio_side_gap_episodes(valid, seed):
    tagged = []
    wanted = {(seed["num"], "left"), (seed["num"], "right"),
              (seed["den"], "left"), (seed["den"], "right")}
    for item in valid:
        pair = (item["row"]["movement"], item["row"]["side"])
        if pair in wanted:
            tagged.append((item["date"], item["row"]["id"], pair, item))
    state, per_day = {}, {}
    for _day, _row_id, pair, item in sorted(tagged):
        state[pair] = item
        if not wanted <= set(state):
            continue
        numerator_values = [state[(seed["num"], side)]["value"]
                            for side in ("left", "right")]
        denominator_values = [state[(seed["den"], side)]["value"]
                              for side in ("left", "right")]
        numerator_protocols = {
            state[(seed["num"], side)]["protocol"]
            for side in ("left", "right")
        }
        denominator_protocols = {
            state[(seed["den"], side)]["protocol"]
            for side in ("left", "right")
        }
        # A bilateral side gap is comparable only when each movement used the
        # same normalized equipment protocol on the left and right.
        if len(numerator_protocols) != 1 or len(denominator_protocols) != 1:
            continue
        # Appendix B: a side gap is null when its max is zero. Both component
        # gaps are required by the reviewed ratio-gap formula.
        if max(numerator_values) == 0 or max(denominator_values) == 0:
            continue
        gaps = [(max(values) - min(values)) / max(values)
                for values in (numerator_values, denominator_values)]
        parents = [state[key] for key in sorted(wanted)]
        parent_span = _parent_span_days(parents, item["date"])
        if parent_span > FITNESS_PARENT_MAX_AGE_DAYS:
            continue
        per_day[item["date"]] = {
            "date": item["date"], "value": max(gaps), "parents": parents,
            "protocol": tuple(parent["protocol"] for parent in parents),
            "parent_span_days": parent_span,
        }
    return [per_day[day] for day in sorted(per_day)]


def _min_side_axis_episodes(valid, movement, upper_bound=None):
    """Build protocol-comparable bilateral weakest-side episodes.

    State is isolated by normalized protocol, kind, and unit.  A later
    unmatched unilateral measurement therefore cannot erase the last valid
    pair from another protocol.  Each episode is anchored to the later parent
    observation, never backdated to whichever side happened to be weaker.
    """
    tagged = [
        item for item in valid
        if item["row"]["movement"] == movement
        and item["row"]["side"] in {"left", "right"}
        and (upper_bound is None or item["date"] <= upper_bound)
    ]
    state = defaultdict(dict)
    per_episode = {}
    for item in sorted(
        tagged, key=lambda value: (value["date"], value["row"]["id"]),
    ):
        comparable = (
            item["protocol"], item["spec"].get("kind"), item["unit"],
        )
        state[comparable][item["row"]["side"]] = item
        pair = state[comparable]
        if not {"left", "right"} <= set(pair):
            continue
        parents = (pair["left"], pair["right"])
        event_date = max(parent["date"] for parent in parents)
        parent_span = _parent_span_days(parents, event_date)
        if parent_span > FITNESS_PARENT_MAX_AGE_DAYS:
            continue
        weakest = min(
            parents,
            key=lambda value: (value["value"], value["row"]["side"]),
        )
        # Latest row wins if both sides were corrected on the same day under
        # one protocol; distinct protocols remain distinct measurement
        # episodes rather than being silently merged.
        per_episode[(event_date, comparable)] = {
            "date": event_date,
            "value": weakest["value"],
            "weakest": weakest,
            "parents": parents,
            "protocol": comparable[0],
            "kind": comparable[1],
            "unit": comparable[2],
            "parent_span_days": parent_span,
            "trigger_id": item["row"]["id"],
        }
    return [
        per_episode[key] for key in sorted(
            per_episode, key=lambda value: (value[0], value[1])
        )
    ]


def _load_fitness(conn, definitions, date_range, context, include_provenance, out):
    if not has_columns(
        conn, "fitness_tests", "id", "date", "movement", "side", "voided",
        "equipment_note",
    ):
        return
    catalog = context_value(context, "CATALOG", {}) or {}
    e1rm_fn = context_value(context, "e1rm")
    rows = _rows(conn, "SELECT * FROM fitness_tests WHERE voided=0 ORDER BY date,id")
    valid = []
    previous_by_protocol = {}
    latest = {}
    side_gap_events = {}
    today = getattr(context, "today", date.today())
    for row in rows:
        spec = catalog.get(row.get("movement"))
        if not spec:
            continue
        value, unit = _test_value(row, spec, e1rm_fn)
        if finite(value) is None:
            continue
        protocol = normalized_label(row.get("equipment_note") or "")
        comparable = (row["movement"], row["side"], spec.get("kind"), unit, protocol)
        item = {"date": row["date"], "value": value, "unit": unit,
                "row": row, "spec": spec, "protocol": protocol}
        prior = previous_by_protocol.get(comparable)
        if in_range(row["date"], date_range):
            key = f"fitness.test.{row['movement']}.{row['side']}.value"
            _emit(out, definitions, key, row["date"], value, unit=unit,
                  source=row.get("source"), provenance={
                      "adapter": ADAPTER_ID, "table": "fitness_tests",
                      "natural_key": natural_key("fitness_tests", row),
                      "movement": row["movement"], "side": row["side"],
                      "kind": spec.get("kind"), "protocol": protocol,
                      "age_days": (today - date.fromisoformat(row["date"])).days,
                      **episode_comparison(item, prior),
                  }, include_provenance=include_provenance)
        previous_by_protocol[comparable] = item
        # Axis display is anchored to the requested range.  Full history is
        # retained in ``valid`` for protocol-comparable prior episodes, but a
        # future row must never hide the latest row at or before range end.
        _lo, range_end = range_bounds(date_range)
        if range_end is None or row["date"] <= range_end[:10]:
            latest[(row["movement"], row["side"])] = item
        valid.append(item)

        if spec.get("unilateral"):
            other = "right" if row["side"] == "left" else "left"
            pair = previous_by_protocol.get(
                (row["movement"], other, spec.get("kind"), unit, protocol))
            if (pair and _parent_span_days((item, pair), row["date"])
                    <= FITNESS_PARENT_MAX_AGE_DAYS):
                values = [value, pair["value"]]
                gap = (max(values) - min(values)) / max(values) if max(values) else None
                if gap is not None:
                    side_gap_events[(row["date"], row["movement"], protocol)] = {
                        "value": gap, "rows": [row, pair["row"]], "unit": "ratio",
                        "parent_span_days": _parent_span_days((item, pair), row["date"]),
                    }

    previous_side_gap = {}
    for (day, movement, protocol), item in sorted(side_gap_events.items()):
        prior = previous_side_gap.get((movement, protocol))
        if not in_range(day, date_range):
            previous_side_gap[(movement, protocol)] = {"date": day, **item}
            continue
        _emit(out, definitions, f"fitness.test.{movement}.side_gap", day,
              item["value"], unit="fraction", source="fitness_tests", provenance={
                  "adapter": ADAPTER_ID, "formula": "(max-min)/max",
                  "protocol": protocol,
                  "parent_span_days": item["parent_span_days"],
                  "age_days": (today - date.fromisoformat(day)).days,
                  "parent_natural_keys": [natural_key("fitness_tests", row)
                                          for row in item["rows"]],
                  **episode_comparison({"date": day, **item}, prior),
              }, include_provenance=include_provenance)
        previous_side_gap[(movement, protocol)] = {"date": day, **item}

    ratio_seed = context_value(context, "RATIO_SEED", ()) or ()
    for seed in ratio_seed:
        sides = ("left", "right") if seed.get("per_side") else ("bilateral",)
        for side in sides:
            prior_by_protocol = {}
            for episode in _ratio_episodes(valid, seed, side):
                prior = prior_by_protocol.get(episode["protocol"])
                if in_range(episode["date"], date_range):
                    base = f"fitness.ratio.{seed['key']}.{side}"
                    provenance = {
                        "adapter": ADAPTER_ID, "formula": "numerator/denominator",
                        "seed": seed["key"], "band": seed.get("band"),
                        "component_protocols": list(episode["protocol"]),
                        "parent_span_days": episode["parent_span_days"],
                        "age_days": (today - date.fromisoformat(episode["date"])).days,
                        "parent_natural_keys": [
                            natural_key("fitness_tests", episode["numerator"]["row"]),
                            natural_key("fitness_tests", episode["denominator"]["row"]),
                        ],
                        **episode_comparison(episode, prior),
                    }
                    _emit(out, definitions, f"{base}.value", episode["date"],
                          episode["value"], unit="ratio", source="fitness_tests",
                          provenance=provenance,
                          include_provenance=include_provenance)
                    _emit(out, definitions, f"{base}.distance_to_band", episode["date"],
                          _distance_to_band(episode["value"], seed.get("band")),
                          unit="ratio", source="fitness_tests", provenance=provenance,
                          include_provenance=include_provenance)
                prior_by_protocol[episode["protocol"]] = episode
        if seed.get("per_side"):
            prior_by_protocol = {}
            for episode in _ratio_side_gap_episodes(valid, seed):
                prior = prior_by_protocol.get(episode["protocol"])
                if in_range(episode["date"], date_range):
                    _emit(out, definitions, f"fitness.ratio.{seed['key']}.side_gap",
                          episode["date"], episode["value"], unit="fraction",
                          source="fitness_tests", provenance={
                              "adapter": ADAPTER_ID,
                              "formula": "max_numerator_or_denominator_between_side_gap",
                              "component_protocols": list(episode["protocol"]),
                              "parent_span_days": episode["parent_span_days"],
                              "age_days": (today - date.fromisoformat(episode["date"])).days,
                              "parent_natural_keys": sorted({
                                  natural_key("fitness_tests", parent["row"])
                                  for parent in episode["parents"]
                              }),
                              **episode_comparison(episode, prior),
                          }, include_provenance=include_provenance)
                prior_by_protocol[episode["protocol"]] = episode

    axes = context_value(context, "ATHLETIC_AXES", {}) or {}
    better = context_value(context, "KIND_BETTER", {}) or {}
    axis_score = context_value(context, "axis_score") or context_value(context, "_axis_score")
    targets = {}
    if has_columns(conn, "athletic_targets", "axis", "lift", "target"):
        targets = {(row["axis"], row.get("lift") or ""): row["target"]
                   for row in _rows(conn, "SELECT * FROM athletic_targets")}
    if axis_score:
        for axis, config in axes.items():
            movement = config.get("test")
            if not movement or config.get("agg") == "e1rm_lift":
                continue
            target = targets.get((axis, ""))
            if config.get("agg") == "min_side":
                _lo, upper = range_bounds(date_range)
                for episode in _min_side_axis_episodes(
                    valid, movement, upper[:10] if upper else None,
                ):
                    if not in_range(episode["date"], date_range):
                        continue
                    weakest = episode["weakest"]
                    score = axis_score(
                        episode["value"], target,
                        better.get(episode["kind"], "higher"),
                    )
                    axis_parents = episode["parents"]
                    _emit(
                        out, definitions,
                        f"fitness.athletic_axis.{axis}.score",
                        episode["date"], score, unit="score_0_100",
                        source="fitness_tests", provenance={
                            "adapter": ADAPTER_ID, "formula": "axis_score",
                            "target": target, "weakest_side": True,
                            "weakest_side_name": weakest["row"].get("side"),
                            "component_protocol": episode["protocol"],
                            "parent_span_days": episode["parent_span_days"],
                            "age_days": (
                                today - date.fromisoformat(episode["date"])
                            ).days,
                            "parent_natural_keys": sorted(
                                natural_key("fitness_tests", parent["row"])
                                for parent in axis_parents
                            ),
                            "parents": [
                                {
                                    "side": parent["row"].get("side"),
                                    "date": parent["date"],
                                    "value": parent["value"],
                                    "natural_key": natural_key(
                                        "fitness_tests", parent["row"],
                                    ),
                                }
                                for parent in sorted(
                                    axis_parents,
                                    key=lambda value: (
                                        value["row"].get("side") or "",
                                        value["date"], value["row"]["id"],
                                    ),
                                )
                            ],
                        }, include_provenance=include_provenance,
                    )
                continue
            candidates = [item for (mv, _side), item in latest.items() if mv == movement]
            if not candidates:
                continue
            item = max(candidates, key=lambda value: (value["date"], value["row"]["id"]))
            axis_parents = [item]
            axis_event_date = item["date"]
            score = axis_score(item["value"], target, better.get(item["spec"].get("kind"), "higher"))
            if in_range(axis_event_date, date_range):
                _emit(out, definitions, f"fitness.athletic_axis.{axis}.score",
                      axis_event_date, score, unit="score_0_100", source="fitness_tests",
                      provenance={
                          "adapter": ADAPTER_ID, "formula": "axis_score",
                          "target": target, "weakest_side": False,
                          "weakest_side_name": None,
                          "component_protocol": item["protocol"],
                          "parent_span_days": 0,
                          "parent_natural_keys": sorted(
                              natural_key("fitness_tests", parent["row"])
                              for parent in axis_parents
                          ),
                          "parents": [
                              {
                                  "side": parent["row"].get("side"),
                                  "date": parent["date"],
                                  "value": parent["value"],
                                  "natural_key": natural_key(
                                      "fitness_tests", parent["row"],
                                  ),
                              }
                              for parent in sorted(
                                  axis_parents,
                                  key=lambda value: (
                                      value["row"].get("side") or "",
                                      value["date"], value["row"]["id"],
                                  ),
                              )
                          ],
                      }, include_provenance=include_provenance)

        # Strength is the existing 28-date mean of available owner-targeted lift
        # scores. Targets and exact lift names are owner config; no lift/target is
        # inferred. This is one derived episode at the latest contributing set.
        strength_targets = [(lift, target) for (axis, lift), target in targets.items()
                            if axis == "strength" and lift]
        if strength_targets and has_columns(
            conn, "hevy_sets", "id", "date", "exercise_title", "set_type",
            "weight_kg", "reps",
        ) and e1rm_fn:
            _lo, upper = range_bounds(date_range)
            anchor = date.fromisoformat(upper) if upper else today
            lower = (anchor - timedelta(days=27)).isoformat()
            hevy_rows = _rows(conn, """SELECT * FROM hevy_sets
                WHERE date>=? AND date<=? AND COALESCE(set_type,'normal')!='warmup'
                  AND weight_kg IS NOT NULL AND reps IS NOT NULL
                ORDER BY date,id""", (lower, anchor.isoformat()))
            lift_scores, parents, missing = [], [], []
            for lift, target in strength_targets:
                candidates = []
                for row in hevy_rows:
                    if row["exercise_title"] != lift:
                        continue
                    value = e1rm_fn(row["weight_kg"], row["reps"])
                    if value is not None:
                        candidates.append((value, row))
                if not candidates:
                    missing.append(lift)
                    continue
                value, row = max(candidates, key=lambda pair: (pair[0], pair[1]["date"], pair[1]["id"]))
                score = axis_score(value, target, "higher")
                if score is not None:
                    lift_scores.append(score); parents.append((lift, target, value, row))
            if lift_scores:
                when = max(row["date"] for _lift, _target, _value, row in parents)
                if in_range(when, date_range):
                    _emit(out, definitions, "fitness.athletic_axis.strength.score",
                          when, round(statistics.mean(lift_scores)),
                          unit="score_0_100", source="hevy", provenance={
                              "adapter": ADAPTER_ID,
                              "formula": "mean_targeted_lift_axis_scores_28_dates",
                              "window": {"from": lower, "to": anchor.isoformat()},
                              "lifts": [{"lift": lift, "target": target, "e1rm": value}
                                        for lift, target, value, _row in parents],
                              "missing_targeted_lifts": missing,
                              "parent_natural_keys": [natural_key("hevy_sets", row)
                                                      for _l, _t, _v, row in parents],
                          }, include_provenance=include_provenance)


def load(conn, definitions, date_range, context, include_provenance=False):
    definitions = DefinitionIndex(definitions)
    out: list[Any] = []
    _load_body(conn, definitions, date_range, context, include_provenance, out)
    _load_fitness(conn, definitions, date_range, context, include_provenance, out)
    return sorted(out, key=lambda item: (item.observed_at, item.feature_key,
                                         item.source or ""))


__all__ = [
    "ADAPTER_ID", "FITNESS_PARENT_MAX_AGE_DAYS", "REGISTRY_VERSION",
    "episode_comparison", "load",
]
