"""Deployment contract for the automatic Hevy refresh cadence."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TIMER = ROOT / "deploy" / "hermes-hevy-sync.timer"


def test_hevy_sync_runs_four_times_in_canonical_timezone():
    text = TIMER.read_text()
    assert text.count("OnCalendar=") == 4
    for time in ("03:30", "12:30", "18:30", "23:15"):
        assert f"OnCalendar=*-*-* {time} UTC" in text
    assert "RandomizedDelaySec=5min" in text
    assert "Persistent=true" in text
