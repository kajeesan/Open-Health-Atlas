"""Exact editable-note allowlist and validated vault file paths."""

import os
import re


# Exact paths keep raw observations and executable/configuration files unreachable.
EDITABLE_NOTES = {"personal/plan.md", "personal/habits.md",
                  "personal/profile.md", "personal/goals.md"}


def vault_write_path(vault, rel_dir, filename):
    """Resolve <vault>/<rel_dir>/<filename> with the write-note escape guard.
    filename is a single validated component — never a path. Must START with
    an alphanumeric: an empty audio name would otherwise yield the hidden
    file '.txt', and a leading '-' reads as a flag everywhere else."""
    if (not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$", filename or "")
            or ".." in filename):
        raise SystemExit(f"bad filename: {filename!r} (letters, digits, . _ - only; "
                 "must start with a letter or digit)")
    target = os.path.abspath(os.path.join(vault, rel_dir, filename))
    if os.path.commonpath([target, vault]) != vault:
        raise SystemExit("path escapes the vault")
    return target
