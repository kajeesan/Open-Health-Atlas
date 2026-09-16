"""Deterministic capture, analysis, and evidence support for health data.

The normal command boundary remains ``toolkit/health.py``.  The additional
``hermes_surface`` module is a closed, read-only façade for an external Hermes;
it accepts registered feature keys rather than SQL, paths, or writes.
"""

from .migrations import AUTONOMOUS_SCHEMA_VERSION

__all__ = ["AUTONOMOUS_SCHEMA_VERSION"]
