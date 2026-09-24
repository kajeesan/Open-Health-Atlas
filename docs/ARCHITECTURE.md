# Architecture

OpenHealthAtlas separates product code from private deployment state. The checkout is
read-only application/toolkit material; health records, panel authentication
state, credentials, vault notes, service configuration, and audit logs belong
in user-controlled paths outside it.

## Runtime shape

The macOS desktop adapter in `desktop/` bundles the retained application and a
Python runtime inside a native AppKit/WebKit window. It binds an OS-assigned
loopback port and a private, per-launch broker socket. A one-use capability
travels over an anonymous parent pipe to the native window, establishing the
existing server-side session. Exact host/origin checks, CSRF, restrictive
cookies and a native local-origin filter remain active. The existing web
deployment retains its passkey flow; the desktop trust boundary is the signed
local application and the user's operating-system account.

`desktop/workspaces.py` initializes through supported product scripts, imports
verified copies and migrates new database generations after snapshot checks.
Metadata changes select a generation atomically. Workspace changes restart
the runtime so import-time timezone and database settings cannot cross over.
Parent pipes own setup helpers and the existing broker's process group;
quitting or losing the native parent stops owned work. Existing Hermes
services and global sockets are never selected by the desktop adapter.
See [desktop build and installation](DESKTOP.md) for distribution limits.

An optional standalone stdio MCP server connects an external AI client to a
fixed local database through `LocalSurface`. It reuses the deterministic
catalog, query, analysis and evidence functions with explicit per-instance
database, timezone and dataset identity. It does not import or launch the
Hermes gateway adapter. Canonical reads are enforced by SQLite read-only
connections; analysis caches remain separate derived state.

```text
Browser
  |
  | HTTPS + passkey session + CSRF
  v
Flask panel (app/)
  |                     \
  | read-only SQLite     \ validated JSON over AF_UNIX
  v                       v
health.db              hermes-bridge
                           |
                           | allowlisted health.py command
                           v
                        health.db

External collectors ---> health.py / broker ---> append-oriented records
Deterministic engine ---> registry, frames, readiness, associations, ledgers
```

The panel opens the health database read-only. Mutations cross `app/bridge.py`
and `deploy/hermes-bridge`, which validate an operation envelope and invoke only
allowlisted `toolkit/health.py` commands. Both bridge processes derive command
names and flags from `deploy/bridge_commands.py`; the installed broker carries
a root-owned adjacent copy and enforces every privileged request itself. The panel-state database is separate;
it stores WebAuthn credentials, sessions, audit events, conversation metadata,
and UI state.

## Major packages

### OpenHealthAtlas and the Hermes names

`toolkit/hermes_insights/` is OpenHealthAtlas product code, including its
deterministic calculations. Its historical package name does not mean that
Hermes must be installed. `hermes_surface.py` and the deployment adapters connect
to the optional external Hermes agent. Existing `HERMES_*` settings and broker
names remain compatibility interfaces. The [agent boundary](#deterministic-interpretive-and-agent-boundaries)
defines the separate responsibilities.

| Area | Location | Responsibility |
|---|---|---|
| Web application | `app/` | Authentication, security middleware, HTML, JSON APIs, read models, bridge client |
| Health toolkit entry point | `toolkit/health.py` | Stable executable, explicit command configuration and retained Python compatibility delegates |
| CLI plumbing | `toolkit/hermes_insights/cli.py`, `command_context.py` | Command registration, parsing, dispatch, error output and explicit command configuration |
| File and provider imports | `toolkit/hermes_insights/commands/`, `importers/` | Import transactions, source normalization and catalog validation |
| Daily workflows | `toolkit/hermes_insights/commands/`, `capture_contracts.py`, `followthrough.py`, `schedules.py`, `vault_notes.py` | Governed capture, commitments, schedules, notes and collector coordination |
| Nutrition and food | `toolkit/hermes_insights/nutrition.py`, `food.py`, `score_contracts.py`, `commands/` | Profile targets, nutrition coverage, recipe scaling, food capture and freezer stock |
| Training and movement | `toolkit/hermes_insights/fitness.py`, `muscles.py`, `muscle_figure.py`, `figure_contracts.py`, `physio.py`, `body_measurements.py`, `commands/` | Training edits, fitness reports, muscle volume and figure lenses |
| Daily frames, scores, Recovery and labs | `toolkit/hermes_insights/daily_frames.py`, `scores.py`, `recovery.py`, `labs.py`, `commands/` | Legacy daily reports, dashboard scores, verified Recovery and laboratory workflows |
| Shared analytical runtime | `toolkit/hermes_insights/runtime.py`, `catalogs.py`, `calculations.py` | Database/clock context, configured catalogs and shared formulas used directly by CLI and Hermes tools |
| Database contracts | `toolkit/SCHEMA.sql`, `app/panel_db.py` | Empty health and panel schemas, append protections, indexes, views |
| Schema evolution | `toolkit/hermes_insights/migrations.py` | Exact-shape preflight, checksums, transactional v1-v7 upgrades |
| Long analysis reads | `toolkit/hermes_insights/analysis_jobs.py`, `exact_cache.py` | Private job/result sidecar, one execution slot, exact snapshot reuse and fresh collection status |
| Deterministic intelligence | `toolkit/hermes_insights/` | Registry, frames, identities, provenance, readiness, statistics, associations, interactions, goals, ledger, synthesis, orchestration |
| Generic integrations | `deploy/`, `config/` | Broker, collectors, OAuth helper, scheduler, messaging actions, service/timer examples |
| Standalone local MCP | `scripts/openhealthatlas_mcp.py`, `toolkit/hermes_insights/local_surface.py` | Provider-neutral read-only tools and bounded background work for a client-selected model |
| Acceptance assets | `tests/`, `toolkit/tests/`, `scripts/` | Synthetic fixtures, failure/adversarial tests, empty initialization, fictional demo |

## Toolkit command boundaries

`health.py:main` passes command configuration to `cli.run`. The CLI registers every
command and owns argument parsing, dispatch and error formatting. The facade
retains compatibility delegates for older Python callers; registration calls the
owning modules directly.

Retained Python compatibility covers database and clock rebinding, read-only
connection guards, configured catalogs, shared calculation delegates and older
command entry points. These delegates call the same owners as direct commands.
`_now` remains a call-time clock seam, including when callers replace
`datetime`. `_phase3_context` uses only the binding names declared by
`runtime.adapter_context`. New Python callers should use the owning module and
its explicit context or configuration parameters.

The CLI has no legacy-handler overlay. Its schema, event, feature, association,
ledger, synthesis, orchestration and job handlers register directly. The
facade's unused parser-only delegates are removed. The executable commands and
parser contracts remain unchanged.

`CommandContext` carries the database path, civil clock, timezone, vault path
and stable executable path. The facade resolves configuration at invocation.
Analytical adapters continue using their separate `AdapterContext` contract.
Commands receive number parsing, configuration, input streams and provider
transport explicitly where needed. None of the command source modules imports
the facade.

| Import family | Command owner | Source owner |
|---|---|---|
| Hevy CSV | `commands/hevy.py` | `importers/hevy_csv.py` |
| Hevy workouts, quarterly observations, templates, routines and body measurements | `commands/hevy.py` | `importers/hevy_json.py` |
| Cronometer daily nutrition | `commands/cronometer.py` | `importers/cronometer.py` |
| Google Health normalized records | `commands/google_health.py` | `importers/google_health.py` |
| Recipe nutrients and batch metadata | `commands/recipes.py` | `importers/recipes.py` |
| Authored exercise subregions | `commands/submuscle_map.py` | `importers/submuscle_map.py` |
| Laboratory catalog | `commands/lab_catalog.py` | `importers/lab_catalog.py` |

Paths in this table are relative to `toolkit/hermes_insights/`. Command owners
validate workflow prerequisites, open the configured database and own commit,
rollback and connection closure. Source owners parse and normalize provider files.
They neither commit nor emit CLI output. Existing schema guards remain mandatory.
Catalog dry runs retain their reports without writing records.

Shared fitness and body bounds live in `fitness_contracts.py` and
`body_contracts.py`. Routine snapshots live in `routine_history.py`, shared by schedule, routine
and Hevy import commands. The facade retains compatibility exports for those
consumers. Quarterly Hevy configuration still resolves once at startup and supplies
both imports and activation accounting. Shared calculations retain their existing
owners.

| Daily workflow | Command owner | Shared contract or source owner |
|---|---|---|
| Generic observations, day rating, hydration and supplement capture | `commands/daily_capture.py` | `capture_contracts.py`; existing event and trigger authorities |
| Commitment configuration, logs, checkins and feedback status | `commands/followthrough.py` | `followthrough.py` |
| Weekday schedules, planned times and timing adherence | `commands/schedules.py` | `schedules.py`, `routine_history.py`; existing calculations |
| Editable notes, journal append and immutable transcripts | `commands/notes.py` | `vault_notes.py`, `importers/common.py` |
| Weather, air quality and collector provenance | `commands/collectors.py` | `importers/open_meteo.py`; existing collector-record validation |

Daily commands own database closure and preserve their original commit points.
Generic-write restrictions and optional capture sources have one owner in
`capture_contracts.py`. Labs remain outside the generic writer. The exact note
allowlist and vault path guard live in `vault_notes.py`. Food commands directly reuse
capture-source validation; laboratory commands call the same vault path guard.

Schedule history and versioned plan snapshots share the schedule transaction.
Day-rating milestone triggers and supplement capture links likewise remain atomic
with their observations. Timing reports use the configured medication aliases and
existing time calculations. `commands/daily_frames.py` owns `adherence` and reuses the legacy frame.

Weather and air commands commit and emit the primary result before separately
attempting best-effort provenance. A provenance failure cannot undo that result.
On primary-write failure, the command retains the transaction through the provenance
attempt, preserving the existing failure path when its separate write lock is
unavailable. Rollback and closure follow. Explicit `collector-run-record` remains
strict. Provider normalization receives its transport explicitly and neither writes
the database nor emits output.

Hevy CSV replaces only rows whose source is `hevy`. An empty export clears that
history. JSON workout imports retain their separate shrink guard and explicit
force option. Manual records, provider-specific update rules and ingestion-time
behavior are preserved. Routine import snapshots and quarterly observation triggers
remain in their import transaction. Bulk imports remain excluded from both broker
allowlists.

The explicit Hermes engine inventory includes the extracted runtime modules.
Recursive exact-cache identity still covers toolkit Python source.
Numerical provenance retains its narrower engine inventory. These identities serve
different contracts and are not interchangeable.

### Nutrition and food ownership

`commands/nutrition.py` owns profile and phase writes, manual macro targets,
current target reports and daily nutrition coverage. `nutrition.py` owns target
calculations and the per-day nutrition values and score. The `scores.py` owner
calls those same functions directly. Shared score bands
and rounding live in `score_contracts.py`. Recovery reuses the score owner
for sleep selection and baseline queries.

The facade resolves water, phase, protein and micronutrient configuration at
startup, preserving missing and invalid configuration behavior. It supplies
`NutritionConfig` and the civil clock explicitly. Hydration capture and dynamic
target computation receive the same resolved fallback. Coverage applies current
targets to historical days and omits days without nutrition. Hydration stays separate.

`commands/food.py` owns batch configuration, recipe tags, ingredient sidecars,
prep, eat, food logging, menu and restock commands. `food.py` owns recipe lookup,
nutrient scaling, capture validation and restock guards. Recipe import normalization
remains in its existing importer: `recipe_nutrients.per_gram` stores batch totals.
Food scaling uses current recipe metadata, including when reading older records.

Food capture, stock changes and completeness invalidations retain one transaction.
Prep also updates optional recipe metadata and resets restock state atomically.
Eat selects one oldest positive batch. It can refuse a request even when later
batches contain enough stock. Restock snoozes use current civil time for backdated
consumption. These are preserved behaviors, not new inventory rules.

Ingredient sidecars use the supplied vault and validated full payload. They replace
one file through a temporary file and atomic rename, with cleanup on failure.
They remain distinct from immutable transcript captures. Broker permissions remain
unchanged. Configuration writers and ingredient sidecars stay outside its allowlist.

### Training and movement ownership

`commands/training.py` owns set capture, scheduled and latest-session reads,
and routine set/remove/undo. `commands/fitness.py` owns fitness capture, soft
voids, target writes and fitness/body reports. `commands/muscles.py` dispatches
volume, detail and the four figure lenses. `commands/physio.py` owns pain,
self-test and trial capture and their soft voids. Each command owns its connection
and preserves the original commit boundary.

`fitness.py` queries fitness results, quarterly coverage, current strength,
owner-target radar and everyday movement patterns. It calls the existing
`calculations.py` owners for e1RM, latest tests, ratio judgment and axis scoring.
A complete older quarter takes precedence over a newer partial quarter.
`body_measurements.py` keeps V-taper measurements paired within one source row.

`muscles.py` owns authored-first volume, activation set counts and the separate
subregion theory. Its shared rollup serves both commands and retained score and
readiness consumers. Unanchored legacy windows have only a lower date bound.
Anchored readiness windows also exclude rows after the anchor. These differ
from the analytical adapter's window and completeness contracts. `scores.py` and `recovery.py`
consume the shared rollup while preserving those distinct windows.

`muscle_figure.py` projects activation, strength balance, pain and mobility onto
the exact SVG vocabulary in `figure_contracts.py`. The submuscle importer receives
the same vocabulary explicitly. Quarterly configuration still resolves once at
facade startup and supplies both importers and activation accounting. Mobility
uses existing fitness capture and cited norms, without separate storage.
`physio.py` owns pain vocabulary and schema guards. Catalogs retain cited content.

Routine edits, undo history and versioned plan snapshots share one transaction.
Fitness capture and quarterly triggers remain atomic. Pain capture, links,
completeness invalidation and first-positive triggers also remain atomic. Existing
events, migrations and orchestration modules retain those authorities. Undo can
still reverse schedule edits.

Validation and report asymmetries remain intentional compatibility constraints.
Fitness accepts extra valid value fields and rejects repeated voids. Physio voids
can repeat and retain whitespace reasons. Pain has stricter source/capture rules
than fitness, self-tests and trials. Legacy future-row inclusion, inclusive lower
bounds, laterality deduplication and intermediate rounding remain unchanged.
The body figure keeps uncertain subregion theory separate from measured tests
and weighted exposure. Broker permissions and medical content are unchanged.

### Daily frames, scores, Recovery and labs ownership

`commands/daily_frames.py` owns the legacy daily-frame, feature, correlation,
day-signature, adherence, summary, blood-pressure, and coverage reports.
`daily_frames.py` owns their queries and calculations. These reports remain
separate from the governed feature-frame and association engines. Original
all-history source selection, source preference, zero-fill, lags, and tie
ordering remain unchanged. Some legacy queries use only a lower date bound,
including their diagnostics.

`commands/scores.py` coordinates dashboard scores; `commands/recovery.py`
coordinates Recovery.
`scores.py` shares sleep selection and recovery baseline queries between the
two reports, and reuses nutrition, muscle rollup, and shared numerical owners.
`recovery.py` owns the composite policy, calculation context, and result.
The existing `readiness.py` continues to own governed data sufficiency.

Recovery calculates on the connection yielded by `verified_readiness_snapshot`.
This boundary retains schema/lane checks, private ancestry sidecars, and final
file validation. The ancestry generator consumes the Recovery owner directly.
Same-source baselines, bounded training history, input fingerprints, and private
soreness-note handling remain unchanged. The public evidence projection does
not expose private ancestry material.

`commands/labs.py` owns binary raw capture, text ingestion, and lab reports.
`labs.py` owns report parsing, validation, catalog lookup, and read
calculations. The existing lab catalog importer remains the catalog-document
authority. Raw capture uses exclusive file creation. Ingestion validates the
complete batch against stored priors before writing rows and confirmed catalog
entries in one transaction. Priors remain the latest stored result across all
dates; rows in the incoming batch do not become priors for one another.

Commands own connection closure and preserve commit points. Domain functions
receive connections, clocks, and configuration explicitly. Lab latest/trend
selection, exact confirmation names, report-range precedence, and catalog
reflagging remain unchanged. Parser contracts and broker permissions remain
unchanged.

### Feature, ledger and orchestration command ownership

The remaining command coordination belongs under `commands/`. Existing analytical
and persistence engines retain their implementations.

| Command area | Command owner | Existing engine authority |
|---|---|---|
| SQL reads, schema inspection and migrations | `schema.py` | `runtime.py`, `migrations.py` |
| Raw captures, canonical events, completeness and aliases | `events.py` | `events.py`, `orchestrator.py` |
| Registry, feature frames, data readiness and goals | `features.py` | `registry.py`, `frame.py`, `readiness.py`, `goals.py` |
| Associations and finding replay | `associations.py` | `associations.py`, `exact_cache.py` |
| Manual analysis and hypotheses | `ledger.py` | `ledger.py` |
| Synthesis preparation, recording and history | `synthesis.py` | `synthesis.py`, `orchestrator.py` |
| Scheduled analysis and trigger-driven analysis | `scheduled_analysis.py` | `ledger.py`, `orchestrator.py`, `synthesis.py` |
| Trigger and notification transitions | `orchestration.py` | `orchestrator.py` |
| Analysis-job launch and dispatch | `analysis_jobs.py` | `analysis_jobs.py` |

Command-owner paths are relative to `hermes_insights/commands/`; engine paths are
relative to `hermes_insights/`. `commands/analytical.py` provides shared range,
registry and schema coordination. Commands construct analytical contexts from the
supplied civil clock and timezone after existing validation gates. The event engine
retains its separate range clock.

Phase 2 and Phase 3 commands preserve their respective minimum schema checks.
Associations retain the analytical compatibility gate. Ledger and orchestration
preflight the exact current autonomous schema, then check minimum versions
inside their write transactions. These checks serve different purposes and scopes.

Medication events and their triggers share one transaction; corrections attach
triggers to replacement events. Association reads share one read-only snapshot
with the exact cache. Manual refresh computes before acquiring its write transaction;
hypothesis promotion recomputes and persists within one write snapshot.

Scheduled analysis commits preparation, persists each completed or failed run,
and finalizes separately. Finalization rechecks any trigger lease before writing.
Synthesis recording commits SQLite before its idempotent Markdown append. If the
file append fails, an exact retry can complete it from the durable record.

Within `synthesis.py`, material resolution checks the terminal batch, runs,
finding membership and hypothesis evidence in order. Separate validators cover
linked findings, dormant evidence and hypothesis origins. Existing ancestor
loaders remain pinned to the referenced evaluation. Recording and history reads
retain fingerprint verification before cadence/cutoff validation. These readers
validate stored versions rather than requiring a current-engine seal.

The ledger's private `_ledger_contracts.py` owns shared value validation,
canonical identifiers, errors and transition result types. `_ledger_validation.py`
owns analytical result validation, including sample counts, orientation,
statistical methods, stability, rates and coverage. `_ledger_transitions.py`
owns ordered hypothesis decisions and lineage/window comparisons over loaded
records. `_ledger_store.py` owns authenticated analysis reads and batch/run
persistence on injected connections. `_ledger_hypotheses.py` coordinates
evaluations, evidence-item appends, annotations and hypothesis read views.
`ledger.py` retains verified seals and public verified-write gates, and supplies
call-time clocks and timezone to its explicit wrappers. It re-exports the
existing public contract objects and the fingerprint helper consumed by
synthesis. These internal owners do not begin or complete transactions.

The job command keeps the configured stable CLI path and fixed association/finding
handler map. Its existing engine retains child fencing, inherited lock descriptors,
bounded output and error serialization. Canonical analytical JSON and legacy query
output remain distinct; broker permissions are unchanged.

### Calculation and test navigation

Start with the domain owner listed below, then trace its command or adapter caller.
Function docstrings and the linked ownership sections describe local contracts;
[System design](SYSTEM_DESIGN.md#time-and-identity) owns shared time and identity
semantics. Domain paths below are relative to `toolkit/hermes_insights/`, and
test paths are relative to `toolkit/tests/`.

| Calculation or boundary | Authoritative owner | Main callers | Relevant tests |
|---|---|---|---|
| Shared strength, muscle weighting, timing and score formulas | `calculations.py` | `fitness.py`, `muscles.py`, `scores.py`, `recovery.py`, analytical adapters through `runtime.py` | `test_fitness.py`, `test_muscle_groups.py`, `test_timing.py`, `test_scores.py` |
| Nutrition targets and daily nutrition scores | `nutrition.py`, `score_contracts.py` | `commands/nutrition.py`, `scores.py` | `test_nutrition_targets.py`, `test_nutrition_coverage.py`, `test_calculation_date_boundaries.py` |
| Recipe quantities, food capture and inventory guards | `food.py` | `commands/food.py` | `test_phase2_nutrition.py`, `test_nutrition_workflow_boundaries.py` |
| Fitness reports, muscle volume and figure lenses | `fitness.py`, `muscles.py`, `muscle_figure.py`, `physio.py` | Corresponding `commands/` modules | `test_fitness.py`, `test_muscle_detail.py`, `test_muscle_map.py`, `test_mobility.py`, `test_physio.py` |
| Legacy daily frames, correlations and coverage | `daily_frames.py` | `commands/daily_frames.py`, `scores.py` | `test_engine.py`, `test_daily_frame_lab_boundaries.py` |
| Dashboard scores and verified Recovery | `scores.py`, `recovery.py` | `commands/scores.py`, `commands/recovery.py` | `test_scores.py`, `test_readiness.py`, `test_calculation_date_boundaries.py` |
| Laboratory parsing, validation and latest/trend selection | `labs.py` | `commands/labs.py` | `test_labs.py`, `test_daily_frame_lab_boundaries.py` |
| Feature definitions, frames and readiness | `registry.py`, `frame.py`, `readiness.py`, `adapters/` | `commands/features.py`, analytical engines and local/Hermes surfaces | `test_feature_registry.py`, `test_phase3_adapters.py`, `test_feature_frame_readiness.py` |
| Statistical inference, associations and interactions | `stats.py`, `associations.py`, `interactions.py` | Association commands, ledger coordination and local/Hermes surfaces | `test_statistical_boundaries.py`, `test_outcome_associations.py`, `test_interactions.py`, `test_native_stats.py` |
| Evidence identity and durable analytical state | `provenance.py`, `ledger.py`, `synthesis.py`, `orchestrator.py` | [Analytical command owners](#feature-ledger-and-orchestration-command-ownership) | `test_provenance.py`, `test_phase5_ledger.py`, `test_phase5_synthesis.py`, `test_phase6_orchestrator.py`, `test_cli_coordination_boundaries.py` |

Use these rows as navigation starting points. [Testing](TESTING.md#auditable-partitions)
defines the complete application, fictional journey and actual MCP suites.

## Data ownership and writes

`health.db` is the canonical health record. The baseline schema has 68 tables,
two views, 16 indexes, and ten append-only triggers. Migration v4 adds the
latest provenance, event, feature, analysis, hypothesis, synthesis, trigger,
outbox, and notification structures; migration v5 adds persistent recipe
restock state, v6 makes synthesis provider-neutral, and v7 preserves historical lab-confirmation
labels alongside the current label. Initialization inserts no user or health observations; it seeds
only the bundled cited exercise-to-subregion configuration.

`panel.db` is an application-state database with seven tables and five indexes.
It is not a second health-data authority. A clean initialization contains no
credentials, sessions, chat conversations, or messages.

Importers preserve source identity and ingestion metadata. Deterministic
features carry source, timing, completeness, transformation, and provenance
contracts. Identity resolution is explicit; title substring matching is not
treated as a stable identity.

## Deterministic, interpretive, and agent boundaries

OpenHealthAtlas owns the deterministic engine and integrates with a separate
external agent:

1. The deterministic engine executes against SQLite-backed records. It owns
   feature definitions, transformations, statistical evidence, bounded lags and
   windows, pairwise interactions, readiness, findings, hypothesis history,
   synthesis records, triggers, and notification decisions. `synthesis-prepare`
   supplies validated ledger evidence without invoking a model; optional
   `synthesis-v1` writes retain their existing validation and disabled defaults.
2. An external AI client provides conversation. A compatible MCP client can use
   the standalone local tools; Hermes provides the existing integrated
   conversations, Telegram and approved automation. Clients can gather
   symptoms, ask follow-up questions, request governed evidence, and propose
   explanations with supporting and conflicting evidence. They do not own
   the canonical records, deterministic calculations, provenance,
   or validated writes.

The obsolete local synthetic interpretation package, its test-only API and
exclusive tests have been retired. It had no wired product UI consumer. Shared
engine contracts, deterministic evidence preparation, external Hermes adapters,
and the existing browser conversation controls remain. External Hermes is not
bundled with this repository. The standalone MCP connection supports a
client-selected external model without requiring Hermes. See
[PRODUCT_AUTHORITY.md](PRODUCT_AUTHORITY.md).

The web dashboard is a read and evidence-inspection surface, not the agent. For
example, a knee-pain conversation belongs in Hermes: the user describes the
symptoms, Hermes asks for missing context, and OpenHealthAtlas supplies bounded
records and deterministic findings such as pain history, workload, exercise
exposure, left/right measures, and recovery. Hermes may return competing
hypotheses and next questions, but it must not present association as diagnosis
or proven causation.

## Web security boundary

- Every non-public route requires a server-side session.
- WebAuthn registration/login challenges and credentials live in `panel.db`.
- State-changing browser requests require CSRF protection.
- Cookies are HTTP-only, `SameSite=Strict`, and secure by default.
- CSP restricts scripts, styles, images, and connections to the application.
- The app trusts only one forwarded-protocol hop and does not trust forwarded
  client IP or host headers.
- The bridge validates peer identity, request shape, command allowlists, and
  record constraints before invoking the toolkit.

OpenHealthAtlas is designed for a single user or a tightly controlled private service,
not untrusted multi-tenant hosting.

## External boundaries

Hevy, Google Health/Fitbit providers, OCR/transcription tools, messaging
gateways, model/agent services, reverse proxies, Tailscale, and systemd are
external dependencies. OpenHealthAtlas retains generic adapters and templates; it does
not bundle their accounts, services, data, keys, or proprietary code.

## Initialization and recovery

`scripts/init_hermes.py` creates external directories with restrictive modes,
initializes both empty databases, applies migrations through v7, and refuses to
overwrite existing files. Migrations are forward-only. Recovery uses a verified
copy made before migration; never overwrite the only database. See
[DEPLOYMENT.md](DEPLOYMENT.md).

## Subregion inference boundary

The muscle-detail engine exposes two separate products: weighted set exposure,
and an uncertain relative-strength theory. The latter uses activation weights,
within-exercise personal performance, repeated direct tests, and explicit
confidence. It never treats exposure as measured strength or compares raw load
between unlike exercises. See [SUBREGION_INFERENCE.md](SUBREGION_INFERENCE.md).
