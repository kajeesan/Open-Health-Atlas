"""Food workflow failures preserve the complete prior transaction state."""

from datetime import datetime
from io import StringIO
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from hermes_insights import migrations
from hermes_insights.command_context import CommandContext
from hermes_insights.commands import food, nutrition


SCHEMA = Path(__file__).resolve().parents[1] / "SCHEMA.sql"


@pytest.fixture()
def food_context(tmp_path):
    database = tmp_path / "food.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA.read_text())
        connection.execute(
            "INSERT INTO recipes(recipe_id,name,batch_grams,grams_per_portion,portions) "
            "VALUES('lentil-stew','Lentil Stew',1000,250,4)"
        )
        connection.executemany(
            "INSERT INTO recipe_nutrients(recipe_id,nutrient,unit,per_gram) "
            "VALUES('lentil-stew',?,?,?)",
            [("Energy", "kcal", 2000), ("Protein", "g", 100)],
        )
        connection.execute(
            "INSERT INTO meal_inventory(recipe_id,portions_remaining,grams_per_portion,prepped_on) "
            "VALUES('lentil-stew',4,250,'2026-01-01')"
        )
    migrations.migrate(
        str(database), target=2, expected_from=0,
        # Public extraction baseline; fixture adoption must not depend on Git availability.
        code_version="f17311b7b3cdad601b5b56b90e92aa7b18bedcfb",
    )
    return CommandContext(
        database=str(database),
        clock=lambda: datetime(2026, 1, 15, 12, tzinfo=ZoneInfo("Europe/Paris")),
        timezone="Europe/Paris", vault=str(tmp_path / "vault"), cli_path="unused",
    )


def stored_rows(context, table):
    with sqlite3.connect(context.database) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def test_eat_uses_oldest_batch_portion_weight_with_current_recipe_totals(food_context):
    with sqlite3.connect(food_context.database) as connection:
        connection.execute(
            "INSERT INTO meal_inventory(recipe_id,portions_remaining,grams_per_portion,prepped_on) "
            "VALUES('lentil-stew',3,400,'2026-01-10')"
        )
        connection.execute(
            "UPDATE recipes SET batch_grams=2000,grams_per_portion=400,portions=5"
        )

    result = food.eat(food_context, SimpleNamespace(recipe="lentil-stew", portions=2, date=None))

    assert result["grams"] == 500
    assert result["kcal"] == 500
    assert result["protein_g"] == 25
    with sqlite3.connect(food_context.database) as connection:
        assert connection.execute(
            "SELECT portions_remaining FROM meal_inventory ORDER BY prepped_on"
        ).fetchall() == [(2,), (3,)]


def test_backdated_eat_checks_snooze_against_current_day(food_context):
    with sqlite3.connect(food_context.database) as connection:
        connection.execute(
            "INSERT INTO recipe_restock_state(recipe_id,action,snooze_until) "
            "VALUES('lentil-stew','later','2026-01-14')"
        )

    result = food.eat(
        food_context, SimpleNamespace(recipe="lentil-stew", portions=2, date="2026-01-12"))

    assert result["restock_alert"] is True


def test_eat_refuses_short_oldest_batch_despite_sufficient_total_stock(food_context):
    with sqlite3.connect(food_context.database) as connection:
        connection.execute("UPDATE meal_inventory SET portions_remaining=1")
        connection.execute(
            "INSERT INTO meal_inventory(recipe_id,portions_remaining,grams_per_portion,prepped_on) "
            "VALUES('lentil-stew',3,400,'2026-01-10')"
        )
    prior_inventory = stored_rows(food_context, "meal_inventory")

    with pytest.raises(SystemExit, match="only 1.0 portions left"):
        food.eat(food_context, SimpleNamespace(recipe="lentil-stew", portions=2, date=None))

    assert stored_rows(food_context, "meal_inventory") == prior_inventory
    assert stored_rows(food_context, "nutrition_log") == []


@pytest.mark.parametrize("command", [food.eat, food.log_food], ids=["eat", "log-food"])
def test_food_failure_preserves_nutrition_stock_and_completeness(food_context, command):
    with sqlite3.connect(food_context.database) as connection:
        connection.executemany(
            "INSERT INTO capture_completeness_revisions(date,scope,state,explicit_none,source) "
            "VALUES('2026-01-15',?,'complete',1,'manual')",
            [("food_identity",), ("nutrition_total",)],
        )
        connection.executescript(
            "CREATE TRIGGER reject_total_revision BEFORE INSERT ON capture_completeness_revisions "
            "WHEN NEW.scope='nutrition_total' AND NEW.state='partial' "
            "BEGIN SELECT RAISE(ABORT,'nutrition completeness unavailable'); END;"
        )
    prior_inventory = stored_rows(food_context, "meal_inventory")
    prior_completeness = stored_rows(food_context, "capture_completeness_revisions")
    args = SimpleNamespace(recipe="lentil-stew", portions=1, grams=250, date=None)

    with pytest.raises(sqlite3.IntegrityError, match="nutrition completeness unavailable"):
        command(food_context, args)

    assert stored_rows(food_context, "nutrition_log") == []
    assert stored_rows(food_context, "meal_inventory") == prior_inventory
    assert stored_rows(food_context, "capture_completeness_revisions") == prior_completeness


def test_prep_failure_preserves_recipe_inventory_and_restock_state(food_context):
    with sqlite3.connect(food_context.database) as connection:
        connection.execute(
            "INSERT INTO recipe_restock_state(recipe_id,action) VALUES('lentil-stew','skip')"
        )
        connection.executescript(
            "CREATE TRIGGER reject_restock_reset BEFORE DELETE ON recipe_restock_state "
            "BEGIN SELECT RAISE(ABORT,'restock reset unavailable'); END;"
        )
    prior_recipe = stored_rows(food_context, "recipes")
    prior_inventory = stored_rows(food_context, "meal_inventory")
    prior_restock = stored_rows(food_context, "recipe_restock_state")

    with pytest.raises(sqlite3.IntegrityError, match="restock reset unavailable"):
        food.prep(food_context, SimpleNamespace(recipe="lentil-stew", portions=6, batch_grams=1800))

    assert stored_rows(food_context, "recipes") == prior_recipe
    assert stored_rows(food_context, "meal_inventory") == prior_inventory
    assert stored_rows(food_context, "recipe_restock_state") == prior_restock


def test_phase_failure_preserves_both_profile_rows(food_context):
    with sqlite3.connect(food_context.database) as connection:
        connection.executemany(
            "INSERT INTO owner_profile(key,value,updated_at) VALUES(?,?,'2026-01-01')",
            [("phase", "maintain"), ("phase_started", "2026-01-01")],
        )
        connection.executescript(
            "CREATE TRIGGER reject_phase_start BEFORE INSERT ON owner_profile "
            "WHEN NEW.key='phase_started' "
            "BEGIN SELECT RAISE(ABORT,'phase start unavailable'); END;"
        )
    prior_profile = stored_rows(food_context, "owner_profile")

    with pytest.raises(sqlite3.IntegrityError, match="phase start unavailable"):
        nutrition.phase_set(food_context, SimpleNamespace(phase="bulk"))

    assert stored_rows(food_context, "owner_profile") == prior_profile


def test_failed_sidecar_replace_preserves_prior_file_and_removes_temporary_file(
    food_context, monkeypatch,
):
    directory = Path(food_context.vault) / "personal" / "recipes"
    directory.mkdir(parents=True)
    target = directory / "lentil-stew.ingredients.json"
    target.write_bytes(b'{"ingredients":[{"name":"previous lentils"}]}\n')
    previous = target.read_bytes()

    def refuse_replace(source, destination):
        raise OSError("sidecar replacement unavailable")

    monkeypatch.setattr(food.os, "replace", refuse_replace)

    with pytest.raises(OSError, match="sidecar replacement unavailable"):
        food.recipe_ingredients_set(
            food_context, SimpleNamespace(recipe="lentil-stew"),
            stdin=StringIO('[{"name":"new lentils","amount":500,"unit":"g"}]'),
        )

    assert target.read_bytes() == previous
    assert list(directory.iterdir()) == [target]
