"""Append-only snapshots shared by routine sync and program editing."""

from . import migrations


def require_history(connection):
    """Require the migration-owned undo journal without creating schema."""
    migrations.require_table(connection, "routines_history")


def snapshot(c, op, routine, exercise, prior):
    """Append the prior routine configuration to the undo journal."""
    c.execute("""INSERT INTO routines_history(op, routine_name, exercise_title, prior_existed,
                 ex_order, target_sets, target_reps, target_weight_kg)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (op, routine, exercise, 1 if prior else 0,
               prior["ex_order"] if prior else None,
               prior["target_sets"] if prior else None,
               prior["target_reps"] if prior else None,
               prior["target_weight_kg"] if prior else None))
