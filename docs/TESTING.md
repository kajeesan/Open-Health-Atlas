# Testing

OpenHealthAtlas tests behaviour at the contract boundary: empty schema shape, migrations,
validated writes, authentication, API/UI responses, provenance, deterministic
analysis, failure behavior, and external Hermes evidence contracts. All retained
fixtures are empty or fictional.

## Fresh environment

From the repository root, use Python 3.11 or newer on macOS or Linux.
For clone instructions, see [Development](DEVELOPMENT.md).

```bash
oha_test_env="$(mktemp -d)/venv"
python3 -m venv "$oha_test_env"
"$oha_test_env/bin/python" -m pip install -r requirements-dev.txt
export PATH="$oha_test_env/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
export TZ=Europe/Paris HERMES_TIMEZONE=Europe/Paris
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider
```

This runs the base suite. The actual local MCP protocol module is skipped
unless the optional SDK is installed. To include it:

```bash
"$oha_test_env/bin/python" -m pip install -r requirements-mcp.txt
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider tests/test_local_mcp.py
```

For a complete suite, also follow the prerequisites below. A base-only run
must report optional protocol, JavaScript or native-library skips accurately.

The environment and all fixture data stay outside the release checkout. Disable
pytest caches in focused runs too (`-p no:cacheprovider`).
The retained fixtures use the example `Europe/Paris` civil timezone; the host
test clock and application clock should agree. If supplying `--basetemp`, use a
directory beneath the operating system's temporary root, because installer
tests deliberately reject deployment paths outside those roots.

Unix-socket bridge tests require a platform that permits local `AF_UNIX`
sockets. They do not connect to a live broker, account, or health database.

## Auditable partitions

Use the same three disjoint partitions as `.github/workflows/checks.yml`.
Root discovery already includes `toolkit/tests`; do not run it again when
summing complete-suite counts.

```bash
oha_test_root="$(mktemp -d /tmp/oha-tests.XXXXXX)"

# Application, toolkit and integration contracts, including local sockets.
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider --durations=10 \
  --basetemp "$oha_test_root/application" \
  --ignore=tests/e2e --ignore=tests/test_local_mcp.py

# Fictional end-to-end journeys.
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider --durations=10 \
  --basetemp "$oha_test_root/journeys" tests/e2e

# Actual optional MCP SDK client and stdio server.
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider --durations=10 \
  --basetemp "$oha_test_root/protocol" tests/test_local_mcp.py
```

## Complete verification prerequisites

Install both requirements files and provide Node and a C compiler. Node runs
retained browser-code tests even though the panel needs no asset build.
Build the native statistical test library outside source:

```bash
"$oha_test_env/bin/python" -m pip install -r requirements-dev.txt -r requirements-mcp.txt
"$oha_test_env/bin/python" -m pip check
node --version
cc --version
oha_native_dir="$(mktemp -d /tmp/oha-native.XXXXXX)"
case "$(uname -s)" in
  Darwin) oha_native_library="$oha_native_dir/liboha_stats.dylib" ;;
  *) oha_native_library="$oha_native_dir/liboha_stats.so" ;;
esac
"$oha_test_env/bin/python" scripts/build_native_stats.py \
  --output "$oha_native_library"
export OHA_TEST_NATIVE_LIBRARY="$oha_native_library"
export HEALTH_DB="$oha_native_dir/unused-health.db"
export TZ=Europe/Paris HERMES_TIMEZONE=Europe/Paris
export PYTHONDONTWRITEBYTECODE=1
```

Use these settings for either the full command or all partitions. A missing
dependency is not a passing check. Desktop packaging has its own build and
artifact checks in [Desktop release](DESKTOP_RELEASE.md).

On macOS, if the compiler cannot locate SDK headers, pass
`--sysroot /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk` to the native
build command when that installed SDK exists.

## Calculation testing strategy

Reproduce a defect against unchanged production code before applying its fix.
Use independently worked expectations, controlled civil dates, fictional
records and explicit configuration. Cover relevant missing-data, unit, date
boundary and numerical-extreme behavior through the owning calculation or
retained command. Avoid tests tied to incidental source layout.

Separate numerical corrections from module moves. For extraction, compare
outputs and database effects on the same input and clock; assess expected
fingerprint changes separately. Retain a repeatable timing workload at the
corrected revision for later refactor comparisons. Do not turn measurements
into speculative optimization work.

## Clean initialization and demo

```bash
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider tests/test_initialization.py
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider tests/test_demo_flow.py

data_dir="$(mktemp -d)/empty"
"$oha_test_env/bin/python" scripts/init_hermes.py --data-dir "$data_dir"

demo_db="$(mktemp -d)/health-demo.db"
"$oha_test_env/bin/python" scripts/make_demo_db.py \
  --output "$demo_db" --anchor-date 2026-06-30

demo_dir="$(mktemp -d)/complete-flow"
"$oha_test_env/bin/python" scripts/demo_flow.py --data-dir "$demo_dir"
```

The fixed-anchor demo is a 120-day comprehensive fictional persona. It includes
a three-day-per-week full-body program, logged sets and cardio, wearable and
sleep records, subjective recovery and soreness, recipes and meals, hydration,
micronutrients, supplements, skincare, habits, body metrics, vitals, labs,
pain/rehabilitation, assessments, weather, air quality, and deliberate gaps.
Acceptance checks verify the current schema version, refusal to overwrite, fictional
identities, reproducible normalized records, successful governed feature frames
for sleep/wearable/training/recovery/subjective, and fixed-date recovery output.
Analysis, finding, hypothesis, synthesis, and notification tables must remain
empty: generated records are observations, not invented interpretations.

The focused subregion checks are:

```bash
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider \
  toolkit/tests/test_submuscle_map.py \
  toolkit/tests/test_muscle_detail.py \
  tests/test_initialization.py \
  tests/test_training_api.py
```
The complete-flow report additionally proves a retained importer, validated CLI
writes, readiness/associations, durable analysis ledger, authenticated UI/API
reads. The retired local synthetic interpretation lifecycle is no longer part
of this demonstration; the report invokes no model or external service.

## Coverage classes

- **Deterministic/schema:** exact schema, migrations, registry, adapters,
  transformations, readiness, timing, identity, statistics, associations,
  interactions, ledger, synthesis, orchestration.
- **Validation/security:** invalid types/ranges, booleans-as-numbers, non-finite
  values, CSRF, passkeys, sessions, headers, broker allowlists, path and peer
  boundaries, append protections.
- **Failure/adversarial:** malformed imports, stale/conflicting identities,
  forged evidence, ancestry and hashes, mismatched units, partial persistence,
  retries, idempotency, and tamper checks.
- **UI/API:** Flask route and template contracts, JSON response shapes,
  redirects, authentication gates, static asset wiring, deterministic trace API
  replay, plus the separately recorded manual in-app browser smoke. The trace
  fixtures deliberately make no screenshot-review claim.
- **End to end:** empty initialization; synthetic import/write; UI/API reads;
  deterministic analysis/ledger; exports.

## Release verification record

The final release review records exact commands, pass counts, duration,
platform, privacy scan, and any platform-specific exception in
`VERIFICATION.md`. Do not replace fresh evidence with an old green count after
changing code, tests, schemas, manifests, or configuration.
