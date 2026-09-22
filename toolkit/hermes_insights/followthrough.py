"""Commitment prerequisites and read-only evening feedback completeness."""

from datetime import date, timedelta

from . import migrations
from .runtime import table_exists


COMMIT_STATUS = {"kept", "partly", "broke"}


CHECKIN_KINDS = {"energy", "focus", "mood"}


def require_followthrough(c):
    """Require migration-owned follow-through tables without DDL."""
    migrations.require_table(c, "commitments")
    migrations.require_table(c, "commitments_log")
    migrations.require_table(c, "checkins")


def feedback_report(c, d):
    """Return daily prompt completeness without converting silence into an answer."""
    requested = date.fromisoformat(d)
    cutoff = (requested - timedelta(days=13)).isoformat()  # 14 inclusive dates

    rating = None
    if table_exists(c, "subjective_daily"):
        rating = c.execute(
            "SELECT day_rating FROM subjective_daily WHERE date=?", (d,)).fetchone()
    rating_value = rating["day_rating"] if rating else None
    day_state = {"present": rating_value in (1, 2, 3), "value": rating_value}

    word = None
    if table_exists(c, "commitments_log"):
        word = c.execute(
            "SELECT status FROM commitments_log WHERE date=? AND commitment_id=0",
            (d,)).fetchone()
    word_value = word["status"] if word else None
    word_state = {"present": word_value in COMMIT_STATUS, "value": word_value}

    missing_commitments = []
    if table_exists(c, "commitments"):
        if table_exists(c, "commitments_log"):
            missing_commitments = [dict(r) for r in c.execute(
                """SELECT cm.id, cm.name FROM commitments cm
                   LEFT JOIN commitments_log cl
                     ON cl.commitment_id=cm.id AND cl.date=?
                   WHERE cm.active=1 AND cl.id IS NULL ORDER BY cm.id""", (d,))]
        else:
            missing_commitments = [dict(r) for r in c.execute(
                "SELECT id, name FROM commitments WHERE active=1 ORDER BY id")]

    checkin_states = {}
    for kind in ("energy", "focus", "mood"):
        rows = []
        if table_exists(c, "checkins"):
            rows = c.execute(
                """SELECT time, value FROM checkins
                   WHERE date=? AND kind=? ORDER BY time, id""", (d, kind)).fetchall()
        latest = rows[-1] if rows else None
        checkin_states[kind] = {
            "count": len(rows),
            "latest_time": latest["time"] if latest else None,
            "latest_value": latest["value"] if latest else None,
            "missing_today": latest is None,
        }

    latest_pain = {}
    if table_exists(c, "pain_log"):
        for row in c.execute(
                """SELECT id, date, region, side, intensity FROM pain_log
                   WHERE voided=0 AND date>=? AND date<=?
                   ORDER BY date, id""", (cutoff, d)):
            latest_pain[(row["region"], row["side"])] = row
    due = [{"region": key[0], "side": key[1],
            "latest_intensity": row["intensity"], "latest_date": row["date"]}
           for key, row in latest_pain.items()
           if row["date"] != d and row["intensity"] is not None and row["intensity"] > 0]
    due.sort(key=lambda item: (-item["latest_intensity"], -date.fromisoformat(
        item["latest_date"]).toordinal(), item["region"], item["side"]))
    due = due[:2]

    prompt_fields = []
    if not day_state["present"]:
        prompt_fields.append("day_rating")
    if not word_state["present"]:
        prompt_fields.append("whole_day_word")
    if checkin_states["mood"]["missing_today"]:
        prompt_fields.append("mood")
    if due:
        prompt_fields.append("pain_change")
    return {"ok": True, "date": d, "day_rating": day_state,
         "whole_day_word": word_state,
         "active_commitments_missing_log": missing_commitments,
         "checkins": checkin_states, "pain_followup_due": due,
         "prompt_fields": prompt_fields,
         "complete_for_prompt": not prompt_fields}
