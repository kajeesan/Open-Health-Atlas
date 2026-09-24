"""Phase 1 acceptance tests: the app shell serves, is themed, and is locked down.
Updated in Phase 2: app routes require a session, so shell tests use `authed`."""


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_dashboard_renders(authed):
    r = authed.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "OpenHealthAtlas" in html
    # Ten-pillar navigation contract. Order lives in app/__init__.py NAV.
    for item in ("Dashboard", "Consistency", "Body", "Mind", "Nutrition",
                 "Recovery", "External care", "Labs", "Data Explorer",
                 "Insight Explorer"):
        assert item in html, f"nav item {item} missing from shell"
    # Plans/Models/Chat/Export left the nav for the Settings drawer.
    for row in ("Models &amp; spend", "Export", "Vault notes"):
        assert row in html, f"settings drawer row {row} missing from shell"
    # task-50 item 6 (owner C3): the Program editor UI (page + drawer link)
    # was removed — /api/plans/* + health.py subcommands stay agent-only.
    assert "Program</span>" not in html
    assert "Sign out" in html  # logout control shows for a live session


def test_dashboard_has_strength_balance_figure_cards(authed):
    """§3f Phase 2 (owner markup image 9): front + back body-map cards sit in
    the first two columns directly below the radar row — read-only, strength-balance
    FIXED as the default (no lens selector, no activation fallback: the
    dashboard's first figure appearance was owner-decided to be this lens),
    whole card links to /training."""
    html = authed.get("/").get_data(as_text=True)
    for marker in ('id="dash-mf-front"', 'id="dash-mf-back"',
                   'id="dash-mf-legend"', "Strength balance",
                   "vendor/body-muscles/body-muscles.umd.min.js",
                   "js/muscle-figure.js"):
        assert marker in html, marker
    # no lens selector on the dashboard cards — the lens is fixed
    assert 'data-group="mflens"' not in html
    # the cards sit between the radar row and the Day-ratings row
    assert html.index('id="dash-radar-muscle"') < html.index('id="dash-mf-front"') \
        < html.index('id="ch-heat"')


def test_stub_pages_render(authed):
    for path in ("/training", "/nutrition", "/labs", "/models", "/export",
                 "/vault-notes", "/consistency", "/data", "/insights"):
        r = authed.get(path)
        assert r.status_code == 200, f"{path} did not render"


def test_program_page_removed_redirects_to_training(authed):
    """task-50 item 6 (owner C3, UI-only removal): the Program & schedule
    editor page is gone — /program now 302s to /training, the closest
    surviving page. The editor DOM + program.js must not exist anywhere,
    and Body must not have picked them back up. The backend
    (/api/plans/*, bridge, health.py routine subcommands) is untouched —
    that's covered by tests/test_plans_api.py, not here."""
    r = authed.get("/program")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/training")
    body = authed.get("/training").get_data(as_text=True)
    assert "js/program.js" not in body
    assert 'id="program-editor-root"' not in body
    # Body still carries the Goals card + the overall/weight:strength charts
    # (unrelated to the editor removal — just guarding against regression)
    assert 'id="goals-list"' in body and 'id="ch-wsr"' in body


def test_legacy_plans_redirects_to_training(authed):
    """The Plans page dissolved in the nav flip; its program editor moved to
    Body, then to Settings (/program), then task-50 removed the editor UI
    entirely. Bookmarks to /plans must survive (302 straight to /training,
    not a 404)."""
    r = authed.get("/plans")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/training")


def test_legacy_chat_redirects_to_insights(authed):
    """Chat now lives embedded in Insight Explorer; /chat bookmarks survive."""
    r = authed.get("/chat")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/insights")


def test_consistency_page_links_to_habit_ledger(authed):
    """task-35: the Habit-ledger card is a whole-card link to the drill
    sub-page, plus an explicit "all ›" chip (design image 11)."""
    html = authed.get("/consistency").get_data(as_text=True)
    assert "/consistency/habits" in html


def test_consistency_habits_page_renders(authed):
    r = authed.get("/consistency/habits")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Habit ledger" in html
    assert "js/consistency_habits.js" in html


def test_mind_page_renders(authed):
    r = authed.get("/mind")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Mind" in html
    # tabs and neutral medication language; no named treatment is bundled
    for item in ("Medication", "Social", "General"):
        assert item in html, f"{item!r} missing from /mind"
    # task-31: brain-dump + prescriber cards drill to their sub-pages
    for href in ("/mind/brain-dump", "/mind/prescriber-questions"):
        assert href in html, f"drill link {href} missing from /mind"


def test_mind_braindump_page_renders(authed):
    r = authed.get("/mind/brain-dump")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Brain-dump" in html
    assert "js/mind_braindump.js" in html


def test_mind_prescriber_page_is_honest_pending(authed):
    r = authed.get("/mind/prescriber-questions")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # honest-pending: no data source, no fabricated questions
    assert "0 saved" in html
    assert "No questions collected yet." in html


def test_recovery_page_renders(authed):
    r = authed.get("/recovery")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Recovery" in html
    for item in ("Sleep hours", "Sleep stages", "HRV", "Resting HR"):
        assert item in html, f"{item!r} missing from /recovery"
    # task-30: each KPI label drills to its /recovery/<metric> sub-page
    for href in ("/recovery/sleep", "/recovery/readiness",
                 "/recovery/hrv", "/recovery/rhr"):
        assert href in html, f"KPI drill link {href} missing from /recovery"


def test_recovery_drill_pages_render(authed):
    # the three real-data metric drills share one template
    for metric, title in (("sleep", "Sleep"), ("hrv", "HRV"), ("rhr", "Resting HR")):
        r = authed.get(f"/recovery/{metric}")
        assert r.status_code == 200, f"/recovery/{metric} did not render"
        html = r.get_data(as_text=True)
        assert "recovery breakdown" in html
        assert "js/recovery_detail.js" in html


def test_recovery_readiness_drill_has_real_engine(authed):
    r = authed.get("/recovery/readiness")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # T48: the engine is real now — no more "no engine yet"/"score pending"
    # placeholders; the engine detail cards remain available.
    assert "Component breakdown" in html
    assert "What's dragging it down" in html
    assert "Muscle recovery" in html
    assert "Soreness summary" in html
    assert "js/readiness_detail.js" in html


def test_recovery_drill_unknown_metric_404(authed):
    assert authed.get("/recovery/bogus").status_code == 404


def test_care_page_renders(authed):
    r = authed.get("/care")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for item in ("External care", "Care activity streak", "Routine adherence",
                 "Morning", "Evening", "Weekly", "Adherence",
                 "product log"):
        assert item in html, f"{item!r} missing from /care"


def test_nutrition_page_renders(authed):
    r = authed.get("/nutrition")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for item in ("Food quality score", "Calories", "Water", "Logged today",
                 "Targets coverage", "hydration vs target"):
        assert item in html, f"{item!r} missing from /nutrition"
    # the header range dd exists with all five whitelisted options
    assert 'data-dd="nutrition"' in html
    for opt in ("Day", "Week", "Month", "Year", "All time"):
        assert f">{opt}<" in html, f"range option {opt!r} missing"
    # task-41: the KPI tile row is present (all four honest tiles)
    for tile in ("Protein", "Log coverage"):
        assert f">{tile}<" in html, f"KPI tile {tile!r} missing"
    # T47: the Recipes filter pills exist (reusing the .pills/.pill classes)
    # and the owner-removed Log-by-grams card is gone (the /api/nutrition/
    # log-food route + subcommand stay agent-reachable; only the UI went).
    assert 'id="nPills"' in html
    for pill in ("All", "Closes today's gaps", "High protein"):
        assert f">{pill}<" in html, f"filter pill {pill!r} missing"
    assert "Log by grams" not in html


def test_nutrition_target_card_is_honest_scaffold(authed):
    r = authed.get("/nutrition")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # every nutrient row from the design is scaffolded, in the design's order
    for label in ("Calories", "Protein", "Carbs", "Fat", "Fibre", "Water",
                  "Vit D", "Magnesium", "Iron", "Zinc", "Omega-3"):
        assert f">{label}<" in html, f"target-card row {label!r} missing"
    # legend chips from the current design contract
    for chip in ("too little", "good", "too much", "100% target", "maintenance band"):
        assert chip in html, f"legend chip {chip!r} missing"
    # PHASE is a read-only INDICATOR, not a control — every button stays
    # natively disabled + aria-disabled (assistive tech + the "not a dead
    # control" grep) permanently.
    assert html.count('aria-disabled="true"') >= 3
    for phase in ("Cut", "Maintain", "Bulk"):
        assert f">{phase}<" in html
    assert 'id="nTgtReason"' in html
    assert 'id="nutrition-target-setup"' in html


def test_insights_page_links_conversations_card(authed):
    """T51: the Insight Explorer's conversations card carries
    the "all ›" drill link + is honest that this is the panel's own chat log,
    not Telegram."""
    html = authed.get("/insights").get_data(as_text=True)
    assert "Conversations with Hermes" in html
    assert "/insights/conversations" in html


def test_insights_page_has_explicit_analysis_and_history_controls(authed):
    """Analysis and history remain available through explicit controls."""
    html = authed.get("/insights").get_data(as_text=True)
    for control_id in (
        "ins-analyze",
        "load-hypotheses",
        "load-syntheses",
        "load-run-audit",
    ):
        assert f'id="{control_id}"' in html


def test_insights_conversations_page_renders(authed):
    r = authed.get("/insights/conversations")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Conversations with Hermes" in html
    assert "General panel chat" in html
    assert "js/insights_conversations.js" in html
    assert html.count("<main") == 1


def test_security_headers_on_public_page(client):
    r = client.get("/login")
    csp = r.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    # same-origin, NOT no-referrer — Flask-WTF's HTTPS CSRF layer needs the
    # same-origin Referer; no-referrer breaks every secure POST (found live).
    assert r.headers.get("Referrer-Policy") == "same-origin"


def test_csp_pins_no_frontend_outbound(client):
    # §3f vendoring review: the FULL policy is pinned so a regression that
    # quietly opens an outbound channel (connect-src, script-src, …) fails
    # loudly. Every fetch/script/img/style source must stay same-origin
    # (data: images excepted). Change this test only with the policy itself.
    assert client.get("/login").headers.get("Content-Security-Policy") == (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    )


def test_vendored_echarts_served(client):
    r = client.get("/static/vendor/echarts.min.js")
    assert r.status_code == 200
    assert b"Apache" in r.data[:400]


def test_vendored_body_muscles_served_with_license(client):
    import hashlib
    import pathlib
    r = client.get("/static/vendor/body-muscles/body-muscles.umd.min.js")
    assert r.status_code == 200
    # byte-unmodified vendoring: pin the sha256 recorded in VENDOR.md (the
    # supply-chain vetting verified this exact hash against a reproducible
    # build of the upstream source)
    assert hashlib.sha256(r.data).hexdigest() == \
        "c7fe91ea3ea05a11f3b684c165af5b7be085d88bd8da9dcc1230e1f181613604"
    vend = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "vendor" / "body-muscles"
    assert (vend / "LICENSE").exists() and (vend / "NOTICE").exists()
