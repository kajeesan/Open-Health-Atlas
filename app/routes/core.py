"""Core page routes and user-visible navigation targets."""
from datetime import date

from flask import Blueprint, abort, jsonify, redirect, render_template, url_for

from app import db_read

bp = Blueprint("core", __name__)

# §3c: the five athletic-radar axes, a fixed UI contract (matches the engine's
# axes list). Off-list slugs 404 rather than render a dead drill page.
ATHLETIC_AXES = ("strength", "endurance", "speed", "balance", "flexibility")


@bp.get("/training")
def training():
    return render_template("training.html")


@bp.get("/training/muscle/<group>")
def muscle_detail(group):
    """§3b drill-down page shell. The group string is validated by the engine
    when the page's JS calls the API (single vocabulary home); a bad group
    renders the engine's error, and Jinja autoescaping keeps the echo safe."""
    return render_template("muscle_detail.html", group=group)


@bp.get("/training/athletic/<axis>")
def athletic_detail(axis):
    """§3c drill-down page shell for one athletic-radar axis. The axis is
    validated here against the fixed whitelist (404 otherwise); the page's JS
    then calls /api/training/athletic-detail for the real data."""
    axis = axis.lower()
    if axis not in ATHLETIC_AXES:
        abort(404)
    return render_template("athletic_detail.html",
                           axis=axis, label=axis.capitalize())


# §3f Task C — the two REPORT-lens WORKSPACE pages (Pain, Mobility). Reached by
# clicking a region on the Body-map figure while its Pain/Mobility lens is
# active (training.js routes explore→drill / report→here). Each page carries its
# OWN body map (point at the exact spot, including several), a coach chat, and
# the read-only physio-loop / mobility state — all from the SAME read-only
# `muscle-map --lens <lens>` payload + the existing `/api/chat/send` agent path.
#
# DETERMINISM (Phase 3 decision 6): capture stays coach/agent-only. These shells
# add NO writer — the lens is fixed server-side (a trusted literal, no user
# input), and the pre-selected region rides ?region= read CLIENT-side only
# (never echoed into the HTML, so no injection surface + no server region vocab
# to police). The panel still never writes pain/mobility data.
@bp.get("/training/pain")
def pain_workspace():
    """Pain workspace: report a painful spot to the coach + see the cited
    physio loop. READ-ONLY for writes — the chat is the only capture path."""
    return render_template("report_workspace.html", lens="pain", label="Pain")


@bp.get("/training/mobility")
def mobility_workspace():
    """Mobility workspace: point at a restricted area + discuss with the coach +
    see the cited mobility screen. READ-ONLY for writes (chat is the capture)."""
    return render_template("report_workspace.html", lens="mobility", label="Mobility")


@bp.get("/consistency")
def consistency():
    """Consistency pillar (design v-consistency): kept-word ring, streaks,
    habit ledger, follow-through trend, 12-month day-ratings calendar."""
    return render_template("consistency.html")


@bp.get("/consistency/habits")
def consistency_habits():
    """Habit-ledger drill sub-page (task-35, design image 11). Composes
    /api/dash/habits-ledger-detail (db_read only) into per-habit status
    cards (Established/Building/Rebuilding/Fragile — deterministic, see that
    route's docstring) plus a "ready for a new habit?" gate. No range dd:
    the classification windows (all-time + a fixed 30d) are fixed by the
    rules, not user-selectable."""
    return render_template("consistency_habits.html")


@bp.get("/mind")
def mind():
    """Mind pillar (design v-mind): medication/social/general tabs — dose +
    vitals context, mood/focus + user-defined assessments, brain-dump, honest
    placeholders for what isn't logged yet (social, prescriber questions)."""
    return render_template("mind.html")


@bp.get("/mind/brain-dump")
def mind_braindump():
    """Brain-dump drill sub-page (task-31, design image 27). REAL data: every
    subjective_daily.brain_dump entry newest-first (via /api/dash/subjective
    days=all) + a deterministic current-streak chip (mind_braindump.js). No
    new endpoint — rides the existing subjective read. Honest-empty when none."""
    return render_template("mind_braindump.html")


@bp.get("/mind/prescriber-questions")
def mind_prescriber():
    """Prescriber-questions drill sub-page (task-31, design image 27). There is
    NO data source yet — the appointment-summary workflow that would collect
    these questions doesn't feed the panel. So this is an honest-pending
    scaffold (structure ready, zero fabricated questions), not a live page."""
    return render_template("mind_prescriber.html")


@bp.get("/recovery")
def recovery():
    """Recovery pillar (design v-recovery): sleep + HRV/RHR KPIs and trends
    (real, from daily_metrics). HR/RHR live here by product contract (dropped
    from Body). Readiness KPI has no engine yet — the readiness drill (task-30)
    renders an honest skeleton (real HRV/RHR/sleep component rows, pending
    score/gauge)."""
    return render_template("recovery.html")


# task-30 drill sub-pages: the four Recovery KPI cards each link to
# /recovery/<metric>. Off-list slugs 404 rather than render a dead page.
RECOVERY_METRICS = ("sleep", "hrv", "rhr", "readiness")
RECOVERY_LABELS = {"sleep": "Sleep", "hrv": "HRV", "rhr": "Resting HR"}


@bp.get("/recovery/<metric>")
def recovery_detail(metric):
    """Recovery KPI drill sub-page (task-30). `readiness` is an honest skeleton
    (no engine); sleep/hrv/rhr share one real-data template + JS, parameterized
    by the metric. Off-list slugs 404. Data comes from /api/dash/recovery-detail
    (SELECT-only db_read composition — no bridge, no write path)."""
    metric = metric.lower()
    if metric not in RECOVERY_METRICS:
        abort(404)
    if metric == "readiness":
        return render_template("readiness_detail.html")
    return render_template("recovery_detail.html",
                           metric=metric, label=RECOVERY_LABELS[metric])


@bp.get("/care")
def care():
    """Configurable care routines
    over skincare_log + skincare_products (/api/dash/care). Unseeded on the
    demo DB — every card renders its own honest empty state. No appointments
    or diagnosis content by product design."""
    return render_template("care.html")


@bp.get("/plans")
def plans():
    """Legacy Plans URL; the current consolidated surface is Body."""
    return redirect(url_for("core.training"))


@bp.get("/program")
def program():
    """Legacy Program URL; validated plan APIs remain, while the current UI is Body."""
    return redirect(url_for("core.training"))


@bp.get("/integrations")
def integrations():
    """Integrations & privacy settings page (design image 31, task-36): a
    read-only "Connected sources" status board (Telegram bot, Hevy, Wearable,
    Bloodwork lab) plus a short factual privacy note. Real data comes from
    /api/integrations/sources (db_read + the existing hermes-status/
    fitbit-status bridge reads — no new bridge subcommands); this page has
    no controls of its own, so the Fitbit re-auth button stays on Models &
    spend rather than being duplicated here."""
    return render_template("integrations.html")


@bp.get("/vault-notes")
def vault_notes():
    """Vault notes settings page (design: Settings drawer → General). Edits the
    four whitelisted Markdown notes with a diff preview; the write still goes
    through /api/plans/note → the bridge → health.py write-note (unchanged)."""
    return render_template("vault_notes.html")


@bp.get("/nutrition")
def nutrition():
    return render_template("nutrition.html")


@bp.get("/nutrition/recipe/<rid>")
def recipe_detail(rid):
    """Recipe detail sub-page (T33 drill-down). Validates the id against the
    real recipes table so an unknown slug 404s instead of rendering a dead
    shell; the page's JS then loads /api/nutrition/recipe/<rid> for the full
    macro/micro breakdown. If the health DB is down we still render the shell
    (the page shows its own unavailable state) rather than mask the outage as
    a 404."""
    if db_read.available():
        rows = db_read.query("SELECT name FROM recipes WHERE recipe_id = ?", (rid,))
        if not rows:
            abort(404)
        name = rows[0]["name"]
    else:
        name = None
    return render_template("recipe_detail.html", rid=rid, name=name)


@bp.get("/labs")
def labs():
    """§labs bloodwork view: panel-grouped results + per-test trend. Read-only;
    the page's JS calls /api/labs (bridge → the `labs` read command)."""
    return render_template("labs.html")


@bp.get("/models")
def models():
    return render_template("models.html")


@bp.get("/export")
def export():
    return render_template("export.html")


@bp.get("/chat")
def chat():
    """Legacy Chat URL. Chat now lives embedded in Insight Explorer (same agent,
    same /api/chat/* endpoints) — this is its one canonical home. 302 so
    bookmarks survive the redesign."""
    return redirect(url_for("core.insights"))


@bp.get("/insights")
def insights():
    return render_template("insights.html")


@bp.get("/insights/conversations")
def insights_conversations():
    """Cursor-paginated panel investigations, never Telegram conversations."""
    return render_template("insights_conversations.html")


@bp.get("/data")
def data():
    return render_template("data.html")



@bp.get("/healthz")
def healthz():
    return jsonify(ok=True)


@bp.get("/")
def dashboard():
    return render_template("dashboard.html", today=date.today().isoformat())
