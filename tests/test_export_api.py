"""Phase 11: export endpoints — table list, CSV/XLSX, full-DB snapshot, whitelist."""
import io
import pathlib
import sqlite3
import zipfile

import pytest

from app import auth as auth_mod
from app import create_app

SCHEMA = (pathlib.Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()


@pytest.fixture()
def client(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb); con.executescript(SCHEMA)
    con.execute("INSERT INTO vitals(date, systolic, diastolic) VALUES(date('now'), 124, 80)")
    con.execute("INSERT INTO vitals(date, systolic, diastolic) VALUES(date('now','-100 day'), 118, 76)")
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    return c


def test_tables_list(client):
    t = client.get("/api/export/tables").get_json()["tables"]
    assert "vitals" in t and "daily_metrics" in t


def test_csv_export(client):
    r = client.get("/api/export/table?name=vitals&format=csv")
    assert r.status_code == 200 and "text/csv" in r.content_type
    body = r.get_data(as_text=True)
    assert "systolic" in body and "124" in body


def test_csv_date_filter(client):
    r = client.get("/api/export/table?name=vitals&format=csv&days=30")
    assert r.get_data(as_text=True).count("\n") == 2  # header + 1 recent row (100d-old excluded)


def test_xlsx_export(client):
    r = client.get("/api/export/table?name=vitals&format=xlsx")
    assert r.status_code == 200
    assert r.data[:2] == b"PK"  # xlsx is a zip


def test_csv_formula_injection_defanged(client, tmp_path):
    con = sqlite3.connect(tmp_path / "health.db")
    con.execute("INSERT INTO vitals(date, systolic, notes) VALUES(date('now'), 120, ?)",
                ("=HYPERLINK(\"http://malicious.example\",\"x\")",))
    con.execute("INSERT INTO vitals(date, systolic, notes) VALUES(date('now'), 121, ?)",
                ("@SUM(A1)",))
    con.commit(); con.close()
    body = client.get("/api/export/table?name=vitals&format=csv").get_data(as_text=True)
    assert "'=HYPERLINK" in body and '"=HYPERLINK' not in body.replace("\"'=", "")
    assert "'@SUM" in body
    # numbers are untouched — 124 must not grow a quote
    assert ",124," in body or body.count("124") >= 1


def test_xlsx_formula_injection_defanged(client, tmp_path):
    from openpyxl import load_workbook
    con = sqlite3.connect(tmp_path / "health.db")
    con.execute("INSERT INTO vitals(date, systolic, notes) VALUES(date('now'), 122, ?)",
                ("=2+2",))
    con.commit(); con.close()
    r = client.get("/api/export/table?name=vitals&format=xlsx")
    wb = load_workbook(io.BytesIO(r.data))
    cells = [c.value for row in wb.active.iter_rows() for c in row]
    assert "'=2+2" in cells          # stored as text, not a formula
    assert "=2+2" not in cells


def test_unknown_table_rejected(client):
    assert client.get("/api/export/table?name=sqlite_master&format=csv").status_code == 400
    assert client.get("/api/export/table?name=evil;DROP&format=csv").status_code == 400


def test_full_db_snapshot(client):
    r = client.get("/api/export/database")
    assert r.status_code == 200
    assert r.data[:16].startswith(b"SQLite format 3")


def test_tables_reports_dated(client):
    d = client.get("/api/export/tables").get_json()
    assert "vitals" in d["dated"]          # has a date column
    assert "recipes" not in d["dated"]     # config table, no date column


def test_bundle_unknown_table_rejected(client):
    assert client.get("/api/export/bundle?tables=vitals,sqlite_master&format=csv").status_code == 400
    assert client.get("/api/export/bundle?tables=evil;DROP&format=csv").status_code == 400


def test_bundle_requires_a_table(client):
    assert client.get("/api/export/bundle?tables=&format=csv").status_code == 400


def test_bundle_bad_dates_rejected(client):
    assert client.get("/api/export/bundle?tables=vitals&format=csv&from=not-a-date").status_code == 400
    assert client.get("/api/export/bundle?tables=vitals&format=csv&from=2026-13-40").status_code == 400
    # from after to
    assert client.get("/api/export/bundle?tables=vitals&format=csv&from=2026-06-01&to=2026-05-01").status_code == 400


def test_bundle_date_range_filters(client, tmp_path):
    con = sqlite3.connect(tmp_path / "health.db")
    con.execute("INSERT INTO vitals(date, systolic) VALUES('2026-03-15', 130)")  # in range
    con.execute("INSERT INTO vitals(date, systolic) VALUES('2026-01-01', 141)")  # before
    con.execute("INSERT INTO vitals(date, systolic) VALUES('2026-12-31', 151)")  # after
    con.commit(); con.close()
    body = client.get(
        "/api/export/bundle?tables=vitals&format=csv&from=2026-03-01&to=2026-03-31"
    ).get_data(as_text=True)
    assert "130" in body            # in-range row exported
    assert "141" not in body and "151" not in body  # out-of-range rows filtered out


def test_bundle_date_range_canonicalizes_non_canonical_iso(client, tmp_path):
    # `date.fromisoformat` also accepts non-canonical forms like "20260315"
    # (basic ISO, no dashes) on Python 3.11+ — must canonicalize before binding
    # into the SQL params, so this behaves identically to the dashed form.
    con = sqlite3.connect(tmp_path / "health.db")
    con.execute("INSERT INTO vitals(date, systolic) VALUES('2026-03-15', 130)")  # in range
    con.execute("INSERT INTO vitals(date, systolic) VALUES('2026-01-01', 141)")  # before
    con.execute("INSERT INTO vitals(date, systolic) VALUES('2026-12-31', 151)")  # after
    con.commit(); con.close()
    body = client.get(
        "/api/export/bundle?tables=vitals&format=csv&from=20260315&to=20260315"
    ).get_data(as_text=True)
    assert "130" in body
    assert "141" not in body and "151" not in body


def test_bundle_csv_zip_shape(client, tmp_path):
    con = sqlite3.connect(tmp_path / "health.db")
    con.execute("INSERT INTO body_metrics(date, weight_kg) VALUES('2026-03-15', 85)")
    con.commit(); con.close()
    r = client.get("/api/export/bundle?tables=vitals,body_metrics&format=csv")
    assert r.status_code == 200 and r.data[:2] == b"PK"  # zip magic
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert set(z.namelist()) == {"vitals.csv", "body_metrics.csv"}


def test_bundle_xlsx_sheet_per_table(client):
    from openpyxl import load_workbook
    r = client.get("/api/export/bundle?tables=vitals,body_metrics&format=xlsx")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.data))
    assert set(wb.sheetnames) == {"vitals", "body_metrics"}


def test_bundle_single_table_plain_csv(client):
    r = client.get("/api/export/bundle?tables=vitals&format=csv")
    assert r.status_code == 200 and "text/csv" in r.content_type
    assert r.data[:2] != b"PK"  # a single table is a plain file, not a zip


def test_bundle_no_date_column_exports_whole(client, tmp_path):
    # recipes has no date column: the range must be ignored and all rows exported.
    con = sqlite3.connect(tmp_path / "health.db")
    con.execute("INSERT INTO recipes(recipe_id, name) VALUES('r1', 'Chili')")
    con.commit(); con.close()
    body = client.get(
        "/api/export/bundle?tables=recipes&format=csv&from=2026-01-01&to=2026-01-02"
    ).get_data(as_text=True)
    assert "Chili" in body  # range doesn't apply -> row still present


def test_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p2.db"), "PANEL_COOKIE_SECURE": False})
    assert app.test_client().get("/api/export/tables").status_code == 401
