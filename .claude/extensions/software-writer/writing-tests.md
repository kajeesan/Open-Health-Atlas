## Named-value assignments

- `project.stacks` = Python 3 with Flask/Jinja and SQLite; plain browser JavaScript, HTML and CSS; Swift/AppKit desktop wrapper; shell scripts selected by their shebang. For stacks without a matching reference, apply universal rules without importing Django or React conventions.
- `tests.frameworks` = pytest from `requirements-dev.txt`, Flask's test client, fictional end-to-end journeys and actual optional MCP SDK client/server tests. Existing JavaScript checks invoke Node; native statistical tests use the separately built library.
- `tests.fixture_sources` = `tests/conftest.py` fixtures `app`, `client`, `authed`; `tests/support.py:csrf_from`; fictional files under `toolkit/tests/fixtures/`; pytest `tmp_path` and `monkeypatch`. Reuse domain-specific helpers only in their intended suite.

## Pre-Step-3

Read `CONTRIBUTING.md` §Principles and `docs/TESTING.md` §Coverage classes. The default Flask fixture disables CSRF and rate limiting, and `authed` bypasses WebAuthn. Explicitly enable the feature under test for security cases. Control clocks and configuration for numerical regressions.

## Post-Step-6

Read `docs/TESTING.md` §Auditable partitions and §Calculation testing strategy before choosing checks. Follow `docs/DEVELOPMENT.md` §Review evidence when reporting results. CI partitions run serially; no parallel runner or production-scale test tier is prescribed.
