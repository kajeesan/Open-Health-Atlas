"""Phase 4 end-to-end outcome association and pure-read contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import pathlib
import random
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_insights import associations, migrations, runtime
from hermes_insights.command_context import CommandContext
from hermes_insights.commands import associations as association_commands
from hermes_insights.associations import (
    AssociationError,
    _base_gate,
    _candidate_lineage_status,
    _component_value,
    _component_lineage_reason,
    _components_for,
    _lineage_reason,
    _mode_value,
    _orientation,
    _quality,
    _risk_effect,
    _source_completeness_interval,
    _relation_allowed,
    _window_transform,
    analyze_outcome,
    recompute_finding_evidence,
    validate_options,
)
from hermes_insights.contracts import DateRange, canonical_json
from hermes_insights.registry import build_registry


def _fixture():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        (ROOT / "SCHEMA.sql").read_text()
    )
    context = runtime.adapter_context(clock=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc), timezone="Europe/Paris")
    definitions = build_registry(conn, context)
    selected = [
        item for item in definitions
        if item.key in {"subjective.day_rating", "sleep.duration_hours"}
    ]
    return conn, context, selected


def _seed_lagged_signal(conn: sqlite3.Connection, *, count: int = 90) -> DateRange:
    start = date(2026, 1, 1)
    generator = random.Random(9182)
    prior = None
    for offset in range(count):
        day = start + timedelta(days=offset)
        sleep = 5.0 + generator.random() * 4.0
        conn.execute(
            """INSERT INTO sleep_log(
                 date,time_asleep_hours,time_in_bed_hours,source)
               VALUES(?,?,?,?)""",
            (day.isoformat(), sleep, 9.0, "manual"),
        )
        if prior is not None:
            rating = 1 if prior < 6.2 else 2 if prior < 7.5 else 3
            conn.execute(
                "INSERT INTO subjective_daily(date,day_rating,source) VALUES(?,?,?)",
                (day.isoformat(), rating, "manual"),
            )
        prior = sleep
    conn.commit()
    return DateRange(start + timedelta(days=1), start + timedelta(days=count - 1))


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"min_n": 29}, "min-n"),
        ({"min_n": 501}, "min-n"),
        ({"top": 0}, "top"),
        ({"top": 101}, "top"),
        ({"mode": "green-only"}, "mode"),
        ({"interactions": "triples"}, "interactions"),
    ],
)
def test_scalar_validation_happens_without_database(kwargs, message):
    values = {
        "outcome_key": "subjective.day_rating",
        "mode": "all",
        "min_n": 30,
        "interactions": "none",
        "top": 30,
    }
    values.update(kwargs)
    with pytest.raises(AssociationError, match=message):
        validate_options(**values)


def test_cli_rejects_bounds_and_abbreviated_flags_before_database_open(tmp_path):
    absent = tmp_path / "absent.db"
    base = [
        sys.executable,
        str(ROOT / "health.py"),
        "outcome-associations",
        "--outcome",
        "subjective.day_rating",
        "--all",
    ]
    environment = {**__import__("os").environ, "HEALTH_DB": str(absent)}
    invalid = subprocess.run(
        [*base, "--min-n", "29"],
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
    )
    abbreviated = subprocess.run(
        [*base, "--min", "30"],
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
    )
    non_day_mode = subprocess.run(
        [
            sys.executable,
            str(ROOT / "health.py"),
            "outcome-associations",
            "--outcome",
            "sleep.duration_hours",
            "--mode",
            "red-vs-non-red",
            "--all",
        ],
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
    )
    assert invalid.returncode == abbreviated.returncode == non_day_mode.returncode == 2
    assert __import__("json").loads(invalid.stdout)["error"]["code"] == "validation_error"
    assert "unrecognized arguments" in __import__("json").loads(
        abbreviated.stdout
    )["error"]["message"]
    assert "supports only ordinal" in __import__("json").loads(
        non_day_mode.stdout
    )["error"]["message"]
    assert not absent.exists()


@pytest.mark.parametrize(
    "version", (0, 1, 2, migrations.AUTONOMOUS_SCHEMA_VERSION + 1),
)
def test_phase4_accepts_v3_through_current_additive_schema(monkeypatch, version):
    monkeypatch.setattr(
        migrations,
        "schema_status",
        lambda _path: {"current_version": version},
    )
    with pytest.raises(migrations.SchemaError):
        runtime.require_analytical_schema("unused.db")
    for supported in range(3, migrations.AUTONOMOUS_SCHEMA_VERSION + 1):
        monkeypatch.setattr(
            migrations,
            "schema_status",
            lambda _path, supported=supported: {"current_version": supported},
        )
        assert runtime.require_analytical_schema("unused.db")["current_version"] == supported


def test_planted_prior_day_positive_all_day_modes_and_reproducibility():
    conn, context, definitions = _fixture()
    requested = _seed_lagged_signal(conn)
    first = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="all",
        top=12,
    )
    second = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="all",
        top=12,
    )
    assert canonical_json(first) == canonical_json(second)
    assert first["meta"]["modes"] == [
        "ordinal", "green-vs-non-green", "red-vs-non-red"
    ]
    assert first["meta"]["baseline_range"]["from"] == (
        requested.start - timedelta(days=34)
    ).isoformat()
    assert first["meta"]["baseline_range"]["to"] == requested.end.isoformat()
    assert all(size > 1 for size in first["meta"]["candidate_family_sizes"].values())
    ordinal = [
        finding for finding in first["findings"]
        if finding["outcome"]["mode"] == "ordinal"
        and finding["exposure"]["components"][0]["lag_days"] == 1
        and finding["exposure"]["components"][0]["window_days"] == 1
    ]
    assert ordinal
    intended = ordinal[0]
    assert intended["effect"]["estimate"] > 0.8
    assert intended["testing"]["p"] == pytest.approx(1 / 2001)
    assert intended["testing"]["family_size"] == first["meta"][
        "candidate_family_sizes"
    ]["ordinal"]
    assert intended["testing"]["permutation_iterations"] == 2000
    assert intended["testing"]["bootstrap_iterations"] == 2000
    assert intended["effect"]["ci95"][0] > 0
    assert intended["quality"]["tier"] == "exploratory_unreplicated"
    assert "association_not_causation" in intended["warnings"]
    assert "causes" not in canonical_json(first).lower()
    conn.close()


def test_analysis_is_select_only_under_strict_authorizer():
    conn, context, definitions = _fixture()
    requested = _seed_lagged_signal(conn, count=60)
    baseline_changes = conn.total_changes
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    allowed = {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_FUNCTION,
        getattr(sqlite3, "SQLITE_RECURSIVE", 33),
    }

    def authorizer(action, arg1, _arg2, _db, _trigger):
        if action in allowed:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and arg1 == "table_info":
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorizer)
    result = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="ordinal",
        top=1,
    )
    assert result["ok"] is True
    assert conn.total_changes == baseline_changes
    assert statements
    assert not any(
        statement.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE")
        )
        for statement in statements
    )
    conn.close()


def test_phase4_command_handlers_are_end_to_end_pure_reads(
    tmp_path, monkeypatch
):
    database = tmp_path / "schema-v3.db"
    setup = sqlite3.connect(database)
    setup.row_factory = sqlite3.Row
    setup.executescript((ROOT / "SCHEMA.sql").read_text())
    setup.commit()
    setup.close()
    migrations.migrate(
        str(database), 3, 0, "f" * 40
    )
    setup = sqlite3.connect(database)
    setup.row_factory = sqlite3.Row
    requested = _seed_lagged_signal(setup, count=45)
    setup.close()

    before_bytes = database.read_bytes()
    verify = sqlite3.connect(database)
    try:
        before_schema = verify.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
    finally:
        verify.close()
    statements: list[str] = []
    authorizer_actions: list[int] = []
    original_cx_ro = runtime.connect_read_only

    def traced_read_only_connection(database):
        conn = original_cx_ro(database)
        conn.set_trace_callback(statements.append)
        conn.set_authorizer(
            lambda action, arg1, arg2, database_name, trigger: (
                authorizer_actions.append(action)
                or runtime.read_only_authorizer(
                    action, arg1, arg2, database_name, trigger
                )
            )
        )
        return conn

    command_context = CommandContext(str(database), lambda: datetime(2026, 7, 23, tzinfo=timezone.utc), "Europe/Paris", str(tmp_path / "vault"), str(ROOT / "health.py"))
    monkeypatch.setattr(runtime, "connect_read_only", traced_read_only_connection)
    common = {
        "from_date": requested.start.isoformat(),
        "to_date": requested.end.isoformat(),
        "days": None,
        "all_dates": False,
    }
    analysis = association_commands.outcome_associations_cmd(command_context, SimpleNamespace(
        outcome="subjective.day_rating",
        mode="ordinal",
        min_n=30,
        interactions="none",
        top=1,
        **common,
    ))
    assert analysis["ok"] is True
    assert analysis["meta"]["analysis_version"] == "outcome-v1"
    assert len(analysis["findings"]) == 1

    evidence = association_commands.finding_evidence_cmd(command_context, SimpleNamespace(
        outcome="subjective.day_rating",
        finding_id=analysis["findings"][0]["finding_id"],
        input_fingerprint=analysis["meta"]["input_fingerprint"],
        **common,
    ))
    assert evidence["ok"] is True
    assert evidence["finding"]["finding_id"] == analysis["findings"][0]["finding_id"]
    assert "findings" not in evidence
    assert database.read_bytes() == before_bytes
    verify = sqlite3.connect(database)
    try:
        after_schema = verify.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
    finally:
        verify.close()
    assert after_schema == before_schema
    assert statements
    assert not set(authorizer_actions) & {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_DROP_TABLE,
    }
    assert not any(
        statement.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE")
        )
        for statement in statements
    )


def test_finding_evidence_verifies_fingerprint_and_returns_only_one_finding():
    conn, context, definitions = _fixture()
    requested = _seed_lagged_signal(conn, count=70)
    conn.execute(
        """INSERT INTO entity_aliases(
             entity_type,alias_key,canonical_key,canonical_label,source,active,
             created_at)
           VALUES('food','raw-food','canonical-food','Canonical food','owner',1,
                  '2026-01-01T00:00:00')"""
    )
    analysis = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="all",
        top=100,
    )
    finding = analysis["findings"][0]
    baseline_changes = conn.total_changes
    statements = []
    conn.set_trace_callback(statements.append)
    allowed = {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_FUNCTION,
        getattr(sqlite3, "SQLITE_RECURSIVE", 33),
    }
    conn.set_authorizer(
        lambda action, arg1, _arg2, _db, _trigger: (
            sqlite3.SQLITE_OK
            if action in allowed
            or (action == sqlite3.SQLITE_PRAGMA and arg1 == "table_info")
            else sqlite3.SQLITE_DENY
        )
    )
    evidence = recompute_finding_evidence(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        finding_id_value=finding["finding_id"],
        input_fingerprint_value=analysis["meta"]["input_fingerprint"],
    )
    assert evidence["finding"]["finding_id"] == finding["finding_id"]
    assert "source_references" in evidence["finding"]
    assert isinstance(evidence["finding"]["source_manifests"], list)
    alias_ids = analysis["coverage"]["dependencies"]["alias_revision_ids"]
    assert len(alias_ids) == 1
    assert (
        f"entity_aliases:{alias_ids[0]}"
        in {
            item["natural_key"]
            for item in evidence["finding"]["source_references"]
        }
    )
    assert "entity_aliases" in {
        item["table"]
        for item in evidence["finding"]["source_manifests"]
    }
    assert "findings" not in evidence
    assert conn.total_changes == baseline_changes
    assert not any(
        statement.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE")
        )
        for statement in statements
    )
    with pytest.raises(AssociationError) as stale:
        recompute_finding_evidence(
            conn,
            definitions,
            requested,
            context,
            outcome_key="subjective.day_rating",
            finding_id_value=finding["finding_id"],
            input_fingerprint_value="sha256:" + "0" * 64,
        )
    assert stale.value.code == "stale_finding"
    conn.close()


def test_single_source_completeness_uses_outcome_and_exposure_source_bounds():
    rows = [{
        "date": "2026-07-10",
        "outcome_observations": [{
            "feature_key": "subjective.day_rating",
            "observed_at": "2026-07-10",
            "value": 3,
            "state": "observed",
            "source": "manual",
            "provenance": {
                "adapter": "daily",
                "table": "subjective_daily",
                "natural_key": "subjective_daily:2026-07-10",
                "completeness_revision_id": 7,
            },
        }],
        "exposure_observations": [{
            "feature_key": "training.session",
            "observed_at": "2026-07-03",
            "value": 0,
            "state": "structural_zero",
            "source": "hevy",
            "provenance": {
                "adapter": "training",
                "table": "source_sync_runs",
                "source_sync_run_ids": [8],
            },
        }],
    }]
    assert _source_completeness_interval(rows) == {
        "from": "2026-07-03",
        "to": "2026-07-10",
        "completeness_revision_ids": [7],
        "source_sync_interval_ids": [8],
    }


def test_non_day_outcome_rejects_day_only_modes_and_unconfigured_target():
    conn, context, definitions = _fixture()
    requested = _seed_lagged_signal(conn, count=35)
    with pytest.raises(AssociationError, match="supports only"):
        analyze_outcome(
            conn,
            definitions,
            requested,
            context,
            outcome_key="sleep.duration_hours",
            mode="green-vs-non-green",
        )
    with pytest.raises(AssociationError) as error:
        analyze_outcome(
            conn,
            definitions,
            requested,
            context,
            outcome_key="sleep.duration_hours",
            mode="ordinal",
        )
    assert error.value.code == "outcome_direction_unconfigured"
    conn.close()


def test_point_and_rolling_date_math_never_reads_the_future():
    conn, context, definitions = _fixture()
    sleep = next(item for item in definitions if item.key == "sleep.duration_hours")
    days = {}
    start = date(2026, 1, 1)
    for offset in range(10):
        day = (start + timedelta(days=offset)).isoformat()
        days[day] = [{
            "feature_key": sleep.key,
            "observed_at": day,
            "value": float(offset + 1),
            "state": "observed",
            "source": "fixture",
            "provenance": {"natural_key": f"fixture:{offset}"},
        }]
    point, refs, _ = _component_value(
        sleep,
        {"lag_days": 2, "window_days": 1, "transform": "point"},
        "2026-01-10",
        days,
    )
    assert point == 8.0
    assert {row["observed_at"] for row in refs} == {"2026-01-08"}
    rolling, refs, _ = _component_value(
        sleep,
        {"lag_days": 2, "window_days": 3, "transform": "mean"},
        "2026-01-10",
        days,
    )
    assert rolling == 7.0
    assert {row["observed_at"] for row in refs} == {
        "2026-01-06", "2026-01-07", "2026-01-08"
    }
    assert all(row["observed_at"] < "2026-01-10" for row in refs)
    conn.close()


def test_alignment_reuse_isolates_features_source_states_and_subsequent_reads(monkeypatch):
    conn, context, _ = _fixture()
    requested = _seed_lagged_signal(conn, count=12)
    keys = {"subjective.day_rating", "sleep.duration_hours", "sleep.time_in_bed_hours"}
    definitions = [
        item for item in build_registry(conn, context) if item.key in keys
    ]
    original_frame = associations.build_feature_frame
    original_align = associations._aligned_candidate
    original_fingerprint = associations.input_fingerprint
    original_redundancy = associations._redundancy_filter
    modes = ("ordinal", "green-vs-non-green", "red-vs-non-red")
    expected_candidates = {mode: [] for mode in modes}
    mode_index = 0
    checked = set()
    consulted = []

    def frame_with_source_boundaries(*args, **kwargs):
        frame = original_frame(*args, **kwargs)
        rows = []
        for row in frame["observations"]:
            if row["feature_key"] == "sleep.duration_hours":
                if row["observed_at"] == "2026-01-04":
                    row = {**row, "provenance": {
                        **(row.get("provenance") or {}), "candidate_eligible": False,
                    }}
                elif row["observed_at"] == "2026-01-05":
                    continue
                elif row["observed_at"] == "2026-01-06":
                    rows.append({**row, "source": "conflicting_fixture",
                                 "value": row["value"] + 1})
            rows.append(row)
        return {**frame, "observations": rows}

    def compare_with_uncached(*args, **kwargs):
        cached = original_align(*args, **kwargs)
        assert cached == original_align(*args)
        for mode in modes:
            uncached = original_align(*args[:4], mode)
            expected_candidates[mode].append(uncached)
            consulted.extend(uncached["_input_observations"])
        return cached

    def compare_remapped_candidates(candidates, suppression):
        nonlocal mode_index
        mode = modes[mode_index % len(modes)]
        assert candidates == expected_candidates[mode]
        for candidate in candidates:
            checked.add((candidate["definition"].key,
                         candidate["component"]["transform"], mode))
        expected_candidates[mode].clear()
        mode_index += 1
        return original_redundancy(candidates, suppression)

    def compare_full_provenance(observations, **kwargs):
        fingerprint = original_fingerprint(observations, **kwargs)
        assert fingerprint == original_fingerprint(consulted, **kwargs)
        assert associations.source_manifests(observations) == associations.source_manifests(consulted)
        consulted.clear()
        return fingerprint

    monkeypatch.setattr(associations, "build_feature_frame", frame_with_source_boundaries)
    monkeypatch.setattr(associations, "_aligned_candidate", compare_with_uncached)
    monkeypatch.setattr(associations, "input_fingerprint", compare_full_provenance)
    monkeypatch.setattr(associations, "_redundancy_filter", compare_remapped_candidates)
    first = analyze_outcome(
        conn, definitions, requested, context,
        outcome_key="subjective.day_rating", mode="all", top=3,
    )
    conn.execute("UPDATE sleep_log SET time_asleep_hours = 8.5, source = 'revised_fixture'")
    conn.commit()
    second = analyze_outcome(
        conn, definitions, requested, context,
        outcome_key="subjective.day_rating", mode="all", top=3,
    )
    assert first["meta"]["input_fingerprint"] != second["meta"]["input_fingerprint"]
    assert checked == {
        (key, transform, mode)
        for key in keys - {"subjective.day_rating"}
        for transform in ("point", "mean")
        for mode in ("ordinal", "green-vs-non-green", "red-vs-non-red")
    }
    conn.close()


def test_load_delta_frequency_and_days_since_use_only_declared_prior_dates():
    definition = SimpleNamespace(
        lag_eligibility={
            "lags": (0, 1, 2, 3, 7),
            "windows": (1, 3, 7, 28, 90, 365),
        }
    )
    start = date(2026, 1, 1)
    values = (0, 0, 1, 2, 3, 4, 5, 6, 7)
    days = {
        (start + timedelta(days=index)).isoformat(): [{
            "feature_key": "training.session",
            "observed_at": (start + timedelta(days=index)).isoformat(),
            "value": float(value),
            "state": "observed",
            "source": "fixture",
            "provenance": {"natural_key": f"fixture:{index}"},
        }]
        for index, value in enumerate(values)
    }
    delta, refs, _ = _component_value(
        definition,
        {"lag_days": 1, "window_days": 3, "transform": "delta_per_day"},
        "2026-01-10",
        days,
    )
    assert delta == pytest.approx(3.0)
    assert {row["observed_at"] for row in refs} == {
        "2026-01-04", "2026-01-05", "2026-01-06",
        "2026-01-07", "2026-01-08", "2026-01-09",
    }
    frequency, _, _ = _component_value(
        definition,
        {"lag_days": 1, "window_days": 7, "transform": "frequency_per_week"},
        "2026-01-10",
        days,
    )
    assert frequency == 7.0
    days_since, refs, _ = _component_value(
        definition,
        {"lag_days": 1, "window_days": 28, "transform": "days_since"},
        "2026-01-10",
        days,
    )
    assert days_since == 0.0
    assert all(row["observed_at"] <= "2026-01-09" for row in refs)


def test_yellow_is_retained_in_both_binary_day_contrasts_and_signs_are_explicit():
    assert _mode_value(2.0, "green-vs-non-green") == 0.0
    assert _mode_value(2.0, "red-vs-non-red") == 0.0
    assert _mode_value(3.0, "green-vs-non-green") == 1.0
    assert _mode_value(1.0, "red-vs-non-red") == 1.0
    rows = []
    # Exposed rows contain more Red outcomes; the raw Red risk difference is
    # positive and remains visible while orientation is negative.
    for index in range(40):
        exposed = float(index < 20)
        positive = float(index < 15 if exposed else index < 25)
        rows.append({"exposure": exposed, "outcome": positive})
    effect = _risk_effect(rows)
    assert effect["estimate"] == pytest.approx(0.5)
    conn, _, definitions = _fixture()
    outcome = next(item for item in definitions if item.key == "subjective.day_rating")
    assert _orientation(conn, outcome, "green-vs-non-green") == 1
    assert _orientation(conn, outcome, "red-vs-non-red") == -1
    assert effect["estimate"] * _orientation(
        conn, outcome, "red-vs-non-red"
    ) == pytest.approx(-0.5)
    conn.close()


def test_selected_outcome_and_shared_lineage_use_actual_row_date_overlap():
    conn, context, definitions = _fixture()
    outcome = next(item for item in definitions if item.key == "subjective.day_rating")
    assert _lineage_reason(outcome, outcome) == "same_feature"
    sleep = next(item for item in definitions if item.key == "sleep.duration_hours")
    synthetic = SimpleNamespace(
        key="derived.sleep",
        lineage_roots=sleep.lineage_roots,
    )
    assert _component_lineage_reason(
        synthetic,
        sleep,
        {"lag_days": 0},
    ) is None
    outcome_observation = {
        "observed_at": "2026-01-02",
        "provenance": {"natural_keys": ["sleep_log:2"]},
    }
    overlapping = [{
        "outcome_observations": [outcome_observation],
        "exposure_observations": [{
            "observed_at": "2026-01-01",
            "provenance": {"natural_key": "sleep_log:2"},
        }],
    }]
    assert _candidate_lineage_status(
        synthetic, sleep, overlapping
    ) == "mechanical_tautology"
    nonoverlapping = [{
        "outcome_observations": [outcome_observation],
        "exposure_observations": [{
            "observed_at": "2026-01-01",
            "provenance": {"natural_key": "sleep_log:1"},
        }],
    }]
    assert _candidate_lineage_status(
        synthetic, sleep, nonoverlapping
    ) == "autoregressive_lineage"
    assert _quality(
        effect_pass=True,
        q_pass=True,
        stability_pass=True,
        context_only_lineage=True,
    ) == {
        "tier": "exploratory_unreplicated",
        "eligible_for_hypothesis": False,
    }
    conn.close()


def test_nonoverlapping_shared_lineage_is_context_only_and_not_hypothesis():
    conn, context, definitions = _fixture()
    requested = _seed_lagged_signal(conn, count=70)
    outcome = next(
        item for item in definitions if item.key == "subjective.day_rating"
    )
    sleep = next(
        item for item in definitions if item.key == "sleep.duration_hours"
    )
    shared_sleep = replace(sleep, lineage_roots=outcome.lineage_roots)
    result = analyze_outcome(
        conn,
        [outcome, shared_sleep],
        requested,
        context,
        outcome_key=outcome.key,
        mode="ordinal",
        top=100,
    )
    assert result["suppression_counts"]["mechanical_tautology"] > 0
    context_findings = [
        item for item in result["findings"]
        if item["exposure"]["components"][0]["exposure_key"] == sleep.key
        and item["exposure"]["components"][0]["lag_days"] > 0
    ]
    assert context_findings
    assert all(
        "autoregressive_lineage" in item["warnings"]
        and not item["quality"]["eligible_for_hypothesis"]
        and any(
            evidence["code"] == "autoregressive_lineage"
            for evidence in item["evidence_against"]
        )
        for item in context_findings
    )
    conn.close()


def test_all_range_fingerprint_ignores_future_unaligned_exposure_rows():
    conn, context, definitions = _fixture()
    _seed_lagged_signal(conn, count=70)
    requested = DateRange(None, None, kind="all")
    first = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="ordinal",
        top=3,
    )
    conn.execute(
        """INSERT INTO sleep_log(
             date,time_asleep_hours,time_in_bed_hours,source)
           VALUES('2027-01-01',7.25,8.0,'future-fixture')"""
    )
    conn.commit()
    second = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="ordinal",
        top=3,
    )
    assert second["meta"]["analysis_range"] == first["meta"]["analysis_range"]
    assert second["meta"]["candidate_family_sizes"] == first["meta"][
        "candidate_family_sizes"
    ]
    assert second["meta"]["input_fingerprint"] == first["meta"][
        "input_fingerprint"
    ]
    assert second["coverage"]["source_manifests"] == first["coverage"][
        "source_manifests"
    ]
    conn.close()


def test_non_day_ordinal_values_one_two_three_do_not_get_day_color_gate():
    rows = [
        {
            "unit_key": str(index),
            "date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
            "outcome": float(index % 3 + 1),
            "outcome_raw": float(index % 3 + 1),
            "exposure": float(index % 6),
            "exposure_states": ["observed"],
        }
        for index in range(30)
    ]
    candidate = {
        "rows": rows,
        "eligible_n": 30,
        "complete_n": 30,
        "missing_n": 0,
        "missing_rate": 0.0,
        "definition": SimpleNamespace(
            value_kind="continuous", zero_semantics="valid"
        ),
    }
    assert _base_gate(
        candidate, "ordinal", 30, "subjective.energy"
    ) is None


def test_substance_relation_and_lagged_e1rm_temporal_boundary():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "SCHEMA.sql").read_text())
    context = runtime.adapter_context(clock=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc), timezone="Europe/Paris")
    definitions = build_registry(conn, context)
    caffeine = next(
        item for item in definitions if item.key == "substance.caffeine_mg"
    )
    day_rating = next(
        item for item in definitions if item.key == "subjective.day_rating"
    )
    assert _relation_allowed(caffeine, day_rating)
    e1rm = SimpleNamespace(
        key="training.exercise.squat.best_e1rm_kg",
        lineage_roots=("hevy.exercise.squat.load_reps",),
    )
    training = SimpleNamespace(
        key="training.session",
        pillar="training",
        lineage_roots=("hevy.training.session",),
    )
    assert _component_lineage_reason(
        training, e1rm, {"lag_days": 0}
    ) == "same_session_e1rm"
    assert _component_lineage_reason(
        training, e1rm, {"lag_days": 1}
    ) is None
    conn.close()


def test_short_and_environment_transforms_follow_registered_value_kind():
    food_amount = SimpleNamespace(
        value_kind="continuous",
        temporal_type="event_occurrence",
        aggregation_id="LP_SHORT",
        key="food.named.item.grams",
    )
    food_event = SimpleNamespace(
        value_kind="binary",
        temporal_type="event_occurrence",
        aggregation_id="LP_SHORT",
        key="food.named.item.occurred",
    )
    rain = SimpleNamespace(
        value_kind="continuous",
        temporal_type="daily_measurement",
        aggregation_id="LP_ENV",
        key="weather.place.rain_mm",
    )
    assert _window_transform(food_amount, "short") == "sum"
    assert _window_transform(food_event, "short") == "count"
    assert _window_transform(rain, "environment") == "sum"


def test_declared_state_components_are_exact_cross_product():
    conn, _context, definitions = _fixture()
    sleep = next(
        item for item in definitions if item.key == "sleep.duration_hours"
    )
    components = _components_for(sleep)
    assert len(components) == 20
    assert {
        (item["lag_days"], item["window_days"], item["transform"])
        for item in components
    } == {
        (
            lag,
            1 if window == "point" else window,
            "point" if window == "point" else "mean",
        )
        for lag in (0, 1, 2, 3, 7)
        for window in ("point", 3, 7, 28)
    }
    conn.close()


def test_load_count_features_include_every_declared_delta_candidate():
    conn, context, _definitions = _fixture()
    full_definitions = build_registry(conn, context)
    for key in ("training.session", "training.working_sets"):
        definition = next(item for item in full_definitions if item.key == key)
        components = _components_for(definition)
        deltas = {
            (item["lag_days"], item["window_days"])
            for item in components
            if item["transform"] == "delta_per_day"
        }
        assert deltas == {
            (lag, window)
            for lag in (0, 1, 2, 3, 7)
            for window in (1, 3, 7, 28, 90, 365)
        }
    conn.close()


def test_nondefault_min_n_is_visibility_only_and_evidence_replays_canonically():
    conn, context, definitions = _fixture()
    requested = _seed_lagged_signal(conn, count=75)
    default = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="ordinal",
        min_n=30,
        top=100,
    )
    stricter = analyze_outcome(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="ordinal",
        min_n=60,
        top=100,
    )
    assert stricter["meta"]["input_fingerprint"] == default["meta"][
        "input_fingerprint"
    ]
    default_by_id = {
        item["finding_id"]: item for item in default["findings"]
    }
    common = [
        item for item in stricter["findings"]
        if item["finding_id"] in default_by_id
    ]
    assert common
    finding = common[0]
    assert finding["testing"]["q"] == default_by_id[
        finding["finding_id"]
    ]["testing"]["q"]
    evidence = recompute_finding_evidence(
        conn,
        definitions,
        requested,
        context,
        outcome_key="subjective.day_rating",
        finding_id_value=finding["finding_id"],
        input_fingerprint_value=stricter["meta"]["input_fingerprint"],
    )
    assert evidence["finding"]["finding_id"] == finding["finding_id"]
    conn.close()
