"""T53: deploy/ghealth-sync pure-transform unit tests — Google Health API v4
dataPoints JSON -> the import-google-health payload. Fixture shapes follow the
doc pages re-verified 2026-07-18/19 (users.dataTypes.dataPoints reference:
sleep.summary.stagesSummary[{type,minutes,count}], minutes-as-strings,
dailyRestingHeartRate.beatsPerMinute, weight.weightGrams, steps.count, ...).
NO network anywhere in this file — the transforms are pure functions, loaded
from the script file (broker-test idiom: deploy scripts have no .py suffix).
"""
import pathlib
import types

SYNC = pathlib.Path(__file__).resolve().parent.parent.parent / "deploy" / "ghealth-sync"


def _load():
    mod = types.ModuleType("ghealth_sync")
    exec(compile(SYNC.read_text(), str(SYNC), "exec"), mod.__dict__)
    return mod


def sleep_dp(start="2026-07-16T20:30:00Z", end="2026-07-17T04:30:00Z",
             offset="7200s", asleep="450", in_period="480", stages=None):
    return {
        "name": "users/me/dataTypes/sleep/dataPoints/abc",
        "sleep": {
            "interval": {"startTime": start, "startUtcOffset": offset,
                         "endTime": end, "endUtcOffset": offset},
            "summary": {
                "minutesAsleep": asleep, "minutesAwake": "30",
                "minutesToFallAsleep": "8", "minutesAfterWakeUp": "4",
                "minutesInSleepPeriod": in_period,
                "stagesSummary": stages if stages is not None else [
                    {"type": "DEEP", "minutes": "75", "count": 3},
                    {"type": "REM", "minutes": "100", "count": 5},
                    {"type": "LIGHT", "minutes": "275", "count": 18},
                    {"type": "AWAKE", "minutes": "30", "count": 2},
                ],
            },
        },
    }


# ── sleep ───────────────────────────────────────────────────────────────────

def test_sleep_maps_stages_and_times():
    m = _load()
    days, warns = m.transform({"sleep": [sleep_dp()]})
    assert warns == []
    # session lands on its WAKE date (endTime local: 09:47 +02:00 → 07-17)
    assert list(days) == ["2026-07-17"]
    s = days["2026-07-17"]["sleep"]
    assert s["deep_min"] == 75 and s["rem_min"] == 100
    assert s["light_min"] == 275 and s["awake_min"] == 30
    assert s["awakenings"] == 2                       # AWAKE count
    assert s["time_asleep_hours"] == 7.5              # 450/60
    assert s["time_in_bed_hours"] == 8.0              # 480/60
    # local clock times (UTC + 7200s offset)
    assert s["bedtime"] == "22:30" and s["wake_time"] == "06:30"
    # sleep duration also feeds daily_metrics.sleep_hours
    assert days["2026-07-17"]["metrics"]["sleep_hours"] == 7.5


def test_sleep_longest_session_wins_naps_ignored():
    m = _load()
    nap = sleep_dp(start="2026-07-17T12:00:00Z", end="2026-07-17T12:40:00Z",
                   asleep="38", in_period="40", stages=[])
    days, _ = m.transform({"sleep": [nap, sleep_dp()]})
    assert days["2026-07-17"]["sleep"]["time_asleep_hours"] == 7.5


def test_sleep_classic_without_stage_minutes():
    """Classic (non-staged) sleep: ASLEEP/RESTLESS segments carry no
    deep/rem/light split — stage fields stay absent, duration still lands."""
    m = _load()
    dp = sleep_dp(stages=[{"type": "ASLEEP", "minutes": "430", "count": 1},
                          {"type": "RESTLESS", "minutes": "14", "count": 2}])
    days, _ = m.transform({"sleep": [dp]})
    s = days["2026-07-17"]["sleep"]
    assert "deep_min" not in s and "rem_min" not in s
    assert s["time_asleep_hours"] == 7.5
    assert "awakenings" not in s                      # no AWAKE summary


def test_sleep_tolerates_sleepSummary_container_name():
    """The two doc fetches disagreed on the summary key ('summary' vs
    'sleepSummary') — the transform accepts both."""
    m = _load()
    dp = sleep_dp()
    dp["sleep"]["sleepSummary"] = dp["sleep"].pop("summary")
    days, warns = m.transform({"sleep": [dp]})
    assert warns == []
    assert days["2026-07-17"]["sleep"]["deep_min"] == 75


# ── daily summary types ─────────────────────────────────────────────────────

def test_daily_types_map_to_metrics():
    m = _load()
    days, warns = m.transform({
        "daily-resting-heart-rate": [
            {"date": "2026-07-17", "dailyRestingHeartRate": {"beatsPerMinute": 52}}],
        "daily-heart-rate-variability": [
            {"date": {"year": 2026, "month": 7, "day": 17},
             "dailyHeartRateVariability": {"averageHeartRateVariabilityMilliseconds": 38.5}}],
        "daily-oxygen-saturation": [
            {"date": "2026-07-17", "dailyOxygenSaturation": {"averagePercentage": 98.0}}],
        "daily-respiratory-rate": [
            {"date": "2026-07-17", "dailyRespiratoryRate": {"averageBreathsPerMinute": 14.2}}],
    })
    assert warns == []
    mtr = days["2026-07-17"]["metrics"]
    assert mtr["resting_hr"] == 52 and mtr["hrv_ms"] == 38.5
    assert mtr["spo2_pct"] == 98.0 and mtr["respiratory_rate"] == 14.2


def test_daily_date_inside_payload_container_also_works():
    m = _load()
    days, _ = m.transform({"daily-resting-heart-rate": [
        {"dailyRestingHeartRate": {"beatsPerMinute": 52,
                                   "date": "2026-07-17"}}]})
    assert days["2026-07-17"]["metrics"]["resting_hr"] == 52


# ── interval types: summed per local date ───────────────────────────────────

def test_steps_summed_per_local_date():
    m = _load()
    dp = lambda start, n: {"steps": {"count": str(n)},
                           "interval": {"startTime": start, "endTime": start}}
    days, _ = m.transform({"steps": [
        dp("2026-07-17T06:00:00+02:00", 1200),
        dp("2026-07-17T18:30:00+02:00", 6150),
        dp("2026-07-16T23:30:00+02:00", 500),   # previous local day
    ]})
    assert days["2026-07-17"]["metrics"]["steps"] == 7350
    assert days["2026-07-16"]["metrics"]["steps"] == 500


def test_active_energy_summed():
    m = _load()
    days, _ = m.transform({"active-energy-burned": [
        {"activeEnergyBurned": {"caloriesKcal": 260.0},
         "interval": {"startTime": "2026-07-17T08:00:00+02:00"}},
        {"activeEnergyBurned": {"caloriesKcal": 160.0},
         "interval": {"startTime": "2026-07-17T17:00:00+02:00"}},
    ]})
    assert days["2026-07-17"]["metrics"]["active_energy_kcal"] == 420.0


# ── weight ──────────────────────────────────────────────────────────────────

def test_weight_grams_to_kg_latest_per_date():
    m = _load()
    days, _ = m.transform({"weight": [
        {"weight": {"weightGrams": "67400"},
         "interval": {"startTime": "2026-07-17T07:00:00+02:00"}},
        {"weight": {"weightGrams": "67200"},
         "interval": {"startTime": "2026-07-17T21:00:00+02:00"}},
    ]})
    assert days["2026-07-17"]["weight_kg"] == 67.2


# ── resilience ──────────────────────────────────────────────────────────────

def test_unrecognized_shapes_warn_never_raise():
    m = _load()
    days, warns = m.transform({
        "daily-resting-heart-rate": [{"date": "2026-07-17", "unexpected": {}}],
        "steps": [{"steps": {}}],                       # no count, no interval
        "sleep": [{"sleep": {}}],                       # no interval
    })
    assert days == {}
    assert len(warns) == 3


def test_unknown_data_type_warns():
    m = _load()
    days, warns = m.transform({"blood-glucose": [{"bloodGlucose": {}}]})
    assert days == {} and len(warns) == 1


def test_payload_shape_matches_import_contract():
    """End-to-end shape check: transform output must be exactly the
    import-google-health payload day-dict (date-keyed here, listified by
    build_payload)."""
    m = _load()
    days, _ = m.transform({"sleep": [sleep_dp()],
                           "daily-resting-heart-rate": [
                               {"date": "2026-07-17",
                                "dailyRestingHeartRate": {"beatsPerMinute": 52}}]})
    payload = m.build_payload(days)
    assert set(payload) == {"days"}
    (day,) = payload["days"]
    assert day["date"] == "2026-07-17"
    assert set(day) <= {"date", "metrics", "sleep", "weight_kg"}
