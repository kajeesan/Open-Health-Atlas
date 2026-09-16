# Testing

OpenHealthAtlas tests behaviour at the contract boundary: empty schema shape, migrations,
validated writes, authentication, API/UI responses, provenance, deterministic
analysis, failure behavior, and external Hermes evidence contracts. All retained
fixtures are empty or fictional.

## Fresh environment

```bash
oha_test_env="$(mktemp -d)/venv"
python3 -m venv "$oha_test_env"
"$oha_test_env/bin/python" -m pip install --upgrade pip
"$oha_test_env/bin/python" -m pip install -r requirements-dev.txt
export PATH="$oha_test_env/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider
```

The environment and all fixture data stay outside the release checkout. Disable
pytest caches in focused runs too (`-p no:cacheprovider`).

Unix-socket bridge tests require a platform that permits local `AF_UNIX`
sockets. They do not connect to a live broker, account, or health database.

## Auditable partitions

The whole suite can be split without changing test semantics:

```bash
# Panel, API, authentication, integrations, and browser-facing contracts.
# The Unix-socket file is a separate platform-permission shard.
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider tests --ignore=tests/test_bridge.py
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider tests/test_bridge.py

# Deterministic toolkit, evidence preparation and validated synthesis.
"$oha_test_env/bin/python" -m pytest -q -p no:cacheprovider toolkit/tests
```

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
Acceptance checks verify the current migration v6, refusal to overwrite, fictional
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
