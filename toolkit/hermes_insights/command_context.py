"""Caller-owned locations and civil clock for toolkit commands."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CommandContext:
    """Carry command configuration without opening files or reading settings."""

    database: str
    clock: Callable[[], datetime]
    timezone: str
    vault: str
    cli_path: str
