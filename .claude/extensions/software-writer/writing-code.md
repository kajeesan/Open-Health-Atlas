## Named-value assignments

- `project.stacks` = Python 3 with Flask/Jinja and SQLite; plain browser JavaScript, HTML and CSS; Swift/AppKit desktop wrapper; shell scripts selected by their shebang. For stacks without a matching reference, apply universal rules without importing Django or React conventions.
- `code.primitives` =
  | Call shape | Raw primitive | Helper | Invariant carried |
  |---|---|---|---|
  | Panel canonical reads | Direct SQLite connection/query | `app.db_read.get_health_db`, `app.db_read.query` | Request-scoped read-only connection and teardown; parameterized values |
  | Panel application state | Direct SQLite connection | `app.panel_db.get_db` | Request-scoped panel-state connection and teardown; callers own transactions |
  | Panel canonical mutations | Direct SQL or privileged subprocess | `app.bridge.run` | Validated broker request; broker and toolkit retain write authority |
  | Analytical reads | Writable SQLite connection | `toolkit.hermes_insights.runtime.connect_read_only` | Read-only URI, query-only mode and closed authorizer; `toolkit/health.py:cx_ro` is the CLI compatibility wrapper |
- `code.di_pattern` = Flask composition through `app.create_app(overrides)` and request configuration; analytical functions receive connections, clocks and `toolkit.hermes_insights.contracts.AdapterContext`. Extracted commands receive `toolkit.hermes_insights.command_context.CommandContext`; importer dependencies enter through explicit parameters. Read `docs/ARCHITECTURE.md` §Toolkit command boundaries for composition and transaction owners. This does not claim unconverted handlers already use explicit dependencies.
- `domain.terms` = provenance; readiness; source identity; laterality; event time; effective time; ingestion time; analysis time; deterministic evidence; health.db; panel.db.
- `comments.preserve_patterns` = Copyright, SPDX and third-party license notices.
- `docs.surfaces` = Read `docs/DEVELOPMENT.md` §Documentation ownership for the authoritative surface map and its explicit historical, legal and configuration exemptions.

## Pre-Step-2

Read `docs/ARCHITECTURE.md` §Data ownership and writes and §Web security boundary before editing persistence or broker access. Read `docs/SYSTEM_DESIGN.md` §Time and identity before editing date, identity or provenance logic.

## Post-Step-4

Read `CONTRIBUTING.md` §Style and dependencies before introducing a dependency. Read `docs/DEVELOPMENT.md` §Working conventions for compatibility and packaging checks.

## Post-Step-5

For analytical or agent-facing changes, read `docs/ARCHITECTURE.md` §Deterministic, interpretive, and agent boundaries and compare the changed path with that contract.
