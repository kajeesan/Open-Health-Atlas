# Develop OpenHealthAtlas

Use this route to change code or run tests. For a first look, start with
[Try the demo](TRY_DEMO.md); for a model client, use [Local MCP](LOCAL_MCP.md).

## Install

Use Git and Python 3.11 or newer on macOS or Linux. The application uses Unix
sockets and POSIX file locks; native Windows is not supported. Commands below
use a POSIX shell and keep the environment and generated data outside Git.

```bash
git clone https://github.com/kajeesan/Open-Health-Atlas.git
cd Open-Health-Atlas
oha_dev_env="$(mktemp -d)/venv"
python3 -m venv "$oha_dev_env"
"$oha_dev_env/bin/python" -m pip install -r requirements-dev.txt
export PATH="$oha_dev_env/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
export HERMES_TIMEZONE=UTC
unset HERMES_HEVY_QUARTERLY_CONFIG
```

Use the public `main` branch or an explicit release tag as your baseline.
The environment variable is available in this terminal; in a new terminal,
set it again to the same environment path or create another environment.
There is no Node build step for the bundled panel assets.

## Verify the setup

Run the existing initialization and complete fictional-flow checks:

```bash
"$oha_dev_env/bin/python" -m pytest -q -p no:cacheprovider \
  tests/test_initialization.py tests/test_demo_flow.py
```

To run the panel with working local broker writes, follow the launch section
of [Try the demo](TRY_DEMO.md), using `"$oha_dev_env/bin/python"` instead of
`"$oha_demo_env/bin/python"`. That script generates fictional data and supplies
a development sign-in. Running Flask alone does not start the write broker.

For an empty database to use with CLI imports or local MCP:

```bash
oha_empty_dir="$(mktemp -d)/openhealthatlas-empty"
"$oha_dev_env/bin/python" scripts/init_hermes.py --data-dir "$oha_empty_dir"
HEALTH_DB="$oha_empty_dir/health.db" HERMES_TIMEZONE=UTC \
  "$oha_dev_env/bin/python" toolkit/health.py schema-status
```

The initializer creates `health.db`, `panel.db` and a vault directory. It seeds
public configuration but no personal observations and refuses to overwrite
either database. It does not start a panel, broker, collector or AI client.
Use `toolkit/health.py --help` to discover supported import commands; an empty
database needs imported or manually recorded observations before analysis can
return findings. The [scripted demo](TRY_DEMO.md#run-the-scripted-demonstration)
exercises a retained importer without any external account.

## Run the right checks

[Testing](TESTING.md) describes focused checks and complete-suite partitions.
Reuse tests for the behavior you change; add a test for a distinct retained
behavior or uncovered regression. Documentation-only changes need link,
command and privacy checks, not a runtime suite.

For MCP changes, install the optional SDK and run its real protocol tests:

```bash
"$oha_dev_env/bin/python" -m pip install -r requirements-mcp.txt
"$oha_dev_env/bin/python" -m pytest -q -p no:cacheprovider tests/test_local_mcp.py
```

These start an actual stdio server and SDK client with fictional databases;
they do not call a model. Without the optional dependency, that test module is
skipped. For a full run including MCP:

```bash
"$oha_dev_env/bin/python" -m pytest -q -p no:cacheprovider
```

## Automated repository checks

GitHub Actions verifies the release manifest and scans current source and Git
history for privacy findings on every pull request and push to `main`.
Documentation, reviewed documentation images, community templates and release
scanner/manifest maintenance use these repository checks plus the scanner's
existing regression tests. They do not trigger the application suite.

Application, toolkit, deployment, dependency or workflow changes run the
retained application and fictional end-to-end tests, including JavaScript,
compiled native-kernel and actual optional MCP SDK checks. The consumed seed
file `docs/authored-submuscle-map.md` also triggers that suite. Manual workflow
runs request the full suite; generated test data and the native library remain
outside the checkout.

## Find the code and prepare a contribution

- [Architecture](ARCHITECTURE.md): data ownership and package map.
- [Current checkpoint](REFACTORING.md): decisions, verification and remaining gaps.
- [Unimplemented work](../UNIMPLEMENTED.md): known product limitations.
- [Contributing](../CONTRIBUTING.md): scope, review evidence and privacy rules.
- [Deployment](DEPLOYMENT.md): service, passkey, broker and migration setup.

Keep real health records, credentials, environment files, generated caches and
service configuration out of source and public reports. Use the existing
fictional fixtures for reproductions. Public source begins with a clean
history; historical private checkpoint commits are not available here.
