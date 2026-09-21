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
skipped. For complete verification, also prepare Node and the native library
as described in [Testing](TESTING.md#complete-verification-prerequisites). Then run:

```bash
export TZ=Europe/Paris HERMES_TIMEZONE=Europe/Paris
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
CI uses a unique directory beneath `/tmp` for installer-test safety and aligns
the test clocks with the retained example timezone. Completed runs report the
slowest tests so long statistical checks can be distinguished from a stall.

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

## Working conventions

Start from public `main` or an explicit release tag, inspect the remote and
working tree, and preserve unrelated changes. Use a dedicated feature branch
and small, coherent commits. Stage named files and use the public baseline's
sanitized author identity. Never accept software license terms automatically
to unblock a tool.

Record the starting commit before risky changes. Never access the owner's
private health directory or live Telegram installation for tests. Never import
or publish private development history into this clean public repository.

Keep work within the agreed product scope. Preserve working UI fields and
controls, including secondary interaction paths. Treat an unverified wired
feature as a gap to investigate. Remove obsolete code or tests only after
accounting for retained consumers. Company-specific work, portfolio changes
and the separate website belong in separate tasks.

Preserve CLI paths, commands, arguments, defaults, output formats, error codes,
database compatibility and transaction ownership. Use existing wrappers at
data boundaries. Shared calculations receive required connections, clocks and
configuration explicitly; they do not open databases, parse CLI arguments or
print. Fix numerical behavior separately from structural extraction.

The current CLI is `toolkit/health.py`; shared runtime and calculation modules
already live in `toolkit/hermes_insights/`. New domain modules must not import
the CLI. Follow the product boundary in
[Architecture](ARCHITECTURE.md#deterministic-interpretive-and-agent-boundaries).

When changing analytical code, inspect each identity owner:
`hermes_surface._engine_manifest`, `exact_cache.current_identity` and
`provenance.engine_manifest`. Their scopes differ. Code changes may alter
fingerprints and resampling seeds; compare numerical results and database
effects separately from expected identity changes. Retain vendor-byte and
license checks.

Desktop packaging includes approved paths listed in the release inventory.
Follow [Release manifest](../RELEASE_MANIFEST.md#regenerate) after staging new
files, and [Desktop release](DESKTOP_RELEASE.md) for package verification.
Historical recovery staging inventories are not general deployment manifests.
A passing source check does not qualify a replacement installer for publication.

Keep logs, temporary reports, environments and generated indexes outside the
checkout. Existing ignore rules cover caches and `graphify-out/`; they do not
exclude contributor Markdown. Do not introduce repository-wide formatting or
new tooling as a side effect of a bounded change.

Before database-changing work, make and verify a consistent fictional database
snapshot outside Git. Code rollback does not restore database contents.
If verification fails, isolate the regression and change only the responsible
batch. Update [Refactoring](REFACTORING.md) after meaningful batches with
decisions, changed files, checks, gaps and the next step.

Commits remain local until task-specific publication authorization and privacy
review permit a push. A merge requires the owner's milestone review when that
checkpoint is part of the task. Source publication never authorizes live
service activation or changes to the owner's records.

## Review evidence

Review correctness, caller compatibility, security, performance and maintenance
cost against the actual diff and retained contracts. Consequential changes need
an independent reader; the implementer cannot supply their own independent
approval. Findings identify a concrete trigger, consequence and source evidence.
Do not block a change on stylistic preference alone.

Freeze the relevant source revision during verification. Each report states
the revision, exact command, exit status, pass/fail/skip counts, elapsed time and
log location. Investigate unexpected failures and skips. Root pytest already
discovers toolkit tests: focused reruns support diagnosis, not higher coverage
totals. Verification helpers report failures without weakening tests or silently
repairing code.

For calculation work, reproduce the defect before the fix and preserve
representative timing measurements for later comparisons. Use fictional records,
controlled clocks and isolated outputs. Record what was tested, what remains
unverified and which results belong to an older revision. Keep private local
paths and raw records out of public reports.

## Documentation ownership

This map assigns authoritative homes for new or edited facts. It does not claim
all historical duplication has been reconciled. Link to an owning heading
instead of copying its explanation into agent instructions or code comments.

| Surface | Owns |
|---|---|
| `README.md` | Product introduction, quick start and navigation |
| `CONTRIBUTING.md` | Contribution principles, change process and licensing obligations |
| `docs/DEVELOPMENT.md` | Developer setup, working conventions, review evidence, this map and writing-tool setup |
| `docs/TESTING.md` | Test prerequisites, commands, partitions and coverage classes |
| `docs/ARCHITECTURE.md` | Runtime responsibilities, module navigation and trust boundaries |
| `docs/SYSTEM_DESIGN.md` | Analytical semantics, time, identity and evidence contracts; project terminology |
| `docs/PRODUCT_AUTHORITY.md` | Accepted product and external-client authority decisions |
| `docs/DEPLOYMENT.md`, `docs/DESKTOP*.md`, `docs/LOCAL_MCP.md` | Service operations, desktop lifecycle/release, and local protocol setup respectively |
| `docs/GETTING_STARTED.md`, `docs/TRY_DEMO.md`, `docs/OPTIONAL.md` | User setup, fictional demonstration and optional integrations respectively |
| `docs/PRIVACY.md`, `SECURITY.md` | Data handling and publication boundaries; vulnerability reporting respectively |
| `FEATURE_RETENTION_MATRIX.md`, `UNIMPLEMENTED.md` | Retained capabilities and missing functionality respectively |
| `docs/SUBREGION_INFERENCE.md`, `docs/authored-submuscle-map.md` | Inference methodology and consumed authored seed map respectively |
| `RELEASE_MANIFEST.md`, `RELEASE_MANIFEST.tsv` | Inventory procedure and generated identities; generated inventory is exempt from duplication checks |
| `docs/REFACTORING.md`, `VERIFICATION.md`, `INDEPENDENT_REVIEW.md`, `PUBLICATION_DECISION.md` | Dated checkpoints and decisions; exempt where historical evidence repeats verified facts |
| `REFERENCES.md`, `THIRD_PARTY_NOTICES.md`, `LICENSING.md`, `LICENSE`, `NOTICE`, `CITATION.cff`, vendor notices | Research, licensing, attribution and citation; exempt where legal or citation repetition is required |
| `AGENTS.md` | Short navigation to contributor guidance |
| `AGENTS.override.md`, `.claude/extensions/software-writer/*.md` | Formal skill delivery and settings; required envelopes/shared assignments are exempt |
| `.github/pull_request_template.md` | Evidence prompts for one change; exempt for per-change evidence |

Preserve existing section shapes when editing these guides. New module
documentation uses purpose, public API, contracts, quirks and tests only where
those sections carry useful facts. No additional changelog is prescribed.

## Writing tools

The project extensions target Software Writer 2.2.0 and Extension Setup 2.1.0,
reviewed from `it-bens/ai-tools` revision
`5ef774268a65f4433d52607ee576e6eb4893f64b`. Sentry's `code-review` guidance was
reviewed at `c2f99a5b04b4cd992ec3022d7c2c3e23e938d241`. These are optional
contributor tools, not application dependencies or correctness guarantees.

`.claude/extensions/software-writer/` holds the project settings. Codex loads
them through the root `AGENTS.override.md`, which first instructs it to read
`AGENTS.md`. From a fresh session at the repository root, invoke a writing skill
and confirm the matching extension is read. Read the files explicitly in a
session that predates their creation.

Apply conventions to Flask, SQLite, plain JavaScript and the Swift desktop
wrapper. The documentation helper reviews only wording; it does not establish
technical truth or approve changes. Keep tool configuration references pointed
at this guide and current code, not another checkout's historical test counts.
