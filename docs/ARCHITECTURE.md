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

| Area | Location | Responsibility |
|---|---|---|
| Web application | `app/` | Authentication, security middleware, HTML, JSON APIs, read models, bridge client |
| Health toolkit entry point | `toolkit/health.py` | Stable executable, explicit legacy-handler wiring and temporary compatibility adapters |
| CLI plumbing | `toolkit/hermes_insights/cli.py`, `command_context.py` | Command registration, parsing, dispatch, error output and explicit command configuration |
| Hevy CSV import | `toolkit/hermes_insights/commands/hevy.py`, `importers/hevy_csv.py` | Transaction coordination and source-specific metric normalization |
| Shared analytical runtime | `toolkit/hermes_insights/runtime.py`, `catalogs.py`, `calculations.py` | Database/clock context, configured catalogs and shared formulas used directly by CLI and Hermes tools |
| Database contracts | `toolkit/SCHEMA.sql`, `app/panel_db.py` | Empty health and panel schemas, append protections, indexes, views |
| Schema evolution | `toolkit/hermes_insights/migrations.py` | Exact-shape preflight, checksums, transactional v1-v7 upgrades |
| Long analysis reads | `toolkit/hermes_insights/analysis_jobs.py`, `exact_cache.py` | Private job/result sidecar, one execution slot, exact snapshot reuse and fresh collection status |
| Deterministic intelligence | `toolkit/hermes_insights/` | Registry, frames, identities, provenance, readiness, statistics, associations, interactions, goals, ledger, synthesis, orchestration |
| Generic integrations | `deploy/`, `config/` | Broker, collectors, OAuth helper, scheduler, messaging actions, service/timer examples |
| Standalone local MCP | `scripts/openhealthatlas_mcp.py`, `toolkit/hermes_insights/local_surface.py` | Provider-neutral read-only tools and bounded background work for a client-selected model |
| Acceptance assets | `tests/`, `toolkit/tests/`, `scripts/` | Synthetic fixtures, failure/adversarial tests, empty initialization, fictional demo |

## Toolkit command boundaries

`health.py:main` supplies an explicit mapping of unconverted handlers to
`cli.run`. The CLI registers their arguments and the extracted `import-hevy`
handler, then owns dispatch and error formatting. Other imports and domain
handlers remain in `health.py` until their planned extraction.

`CommandContext` carries the database path, civil clock, timezone, vault path
and stable executable path. The facade resolves configuration at invocation.
Analytical adapters continue using their separate `AdapterContext` contract.
Legacy handlers retain their compatibility bindings. The Hevy importer receives
the existing permissive number parser explicitly, without importing the facade.

`commands.hevy.import_csv` opens the configured database, requires the migrated
source column, commits a successful replacement and rolls back a failed import.
It closes the connection before returning a result or propagating an error.
`importers.hevy_csv.replace_history` owns CSV normalization and row replacement.
It neither commits nor emits CLI output. Its date parser also serves the legacy
Hevy API date fallback.

CSV imports replace only rows whose source is `hevy`. Empty exports clear that
history. Manually logged rows survive. Invalid optional numbers and unknown
dates retain the legacy missing-value behavior, while invalid integer fields
raise. Bulk CSV imports remain excluded from both broker allowlists.

The explicit Hermes engine inventory includes the extracted runtime modules.
Recursive exact-cache identity still covers toolkit Python source.
Numerical provenance retains its narrower engine inventory. These identities serve
different contracts and are not interchangeable.

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
