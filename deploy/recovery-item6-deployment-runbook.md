# Recovery Item 6 deployment runbook

This runbook describes a future, separately approved deployment of the
fictional Recovery Item 6 candidate. It does not authorize a VPS change,
service restart, Telegram send, fault injection, schema migration, or real-data
activation.

## Immutable inputs

Before deployment, select the pushed OpenHealthAtlas candidate commit, the
pushed external-Hermes candidate commit, and the later release record that
binds both commits to their artifact hashes. The canonical Hermes upstream is
`NousResearch/hermes-agent`; the approved deployment candidate may be published
from the owner's `kajeesan/hermes-agent` fork. Repository URL equality is not a
release proof. Require the recorded upstream base to be an ancestor of the
clean candidate, then verify the exact commit, tree and artifact hashes. Reject
a dirty candidate checkout, an unrecorded artifact hash, or a package built
from another commit.

The candidate commits must be present in the named pushed branch histories;
they do not have to remain the remote tips. In particular, the later immutable
OpenHealthAtlas release-record commit is expected to descend from the earlier
OpenHealthAtlas candidate. Verify ancestry and the recorded candidate tree and
artifact hashes instead of requiring `origin/<branch>` to equal the candidate.

`recovery-item6-staging-manifest.json` is the exact source-to-live destination,
owner, group and mode map. Build and hash an offline candidate tree from that
map before touching any live path. The map is staging metadata, not permission
to copy into those destinations. Never merge, reset, clean, overwrite, or
deploy into the existing dirty live Hermes checkout. Preserve its tracked patch
and all untracked entries in the private rollback inventory. Resolve that
current checkout from the external-Hermes service configuration and retain it
as the untouched rollback source. Stage the clean candidate in the dedicated
`/opt/hermes-agent-releases/<exact-commit>` release directory. That separately
verified clean checkout must resolve to the exact commit in the immutable
release record before a service pointer may change.

Every OpenHealthAtlas source file and deterministic-bundle file in the staging
map must be tracked by the candidate and match its exact hash and byte count in
that candidate's `RELEASE_MANIFEST.tsv`. The append-only superseding release
record must hash those exact manifest bytes. A missing row, an extra staged
file, or any manifest/file mismatch stops preflight.

The deterministic Python bundle is an explicit tracked-file allow-list. Never
replace it with a recursive checkout copy: generated files and
`toolkit/hermes_insights/interpretation/` are outside this deployment boundary.
Apply mode `0755` to every path named by `executable_files` and mode `0644` to
every other bundle file; currently `health.py` is the sole executable. Reject
an executable-list/mode-contract mismatch before staging.
Generate `toolkit/DEPLOYED.manifest` from the immutable release record with the
single line `git_commit=<exact-openhealthatlas-contract-commit>`. It is a
runtime provenance input, not a tracked source file; inventory and back up any
pre-existing destination before creating it.
The external Hermes home is intentionally unresolved in source control. A
deployment preflight must read it from the actual external-Hermes service
configuration, bind that absolute root in the deployment record, and stop if
it differs from the separately approved target.

The accepted fictional database remains schema v5. Do not run
`schema-migrate`, `make_demo_db.py`, or migration 006 against it. The v5
ancestry sidecar attests current snapshot integrity and reproducibility only;
it does not attest the original historical generation event.

Preserve the accepted OpenHealthAtlas topology exactly:

- source root: `/opt/openhealthatlas-fictional`;
- installed profile: `/usr/local/lib/hermes-openhealthatlas-fictional`;
- fixture root: `/var/lib/openhealthatlas-fictional`;
- external-Hermes MCP server alias: `openhealthatlas_fictional`;
- observed Recovery tool names:
  `mcp_openhealthatlas_fictional_openhealthatlas_recovery_snapshot` and
  `mcp_openhealthatlas_fictional_openhealthatlas_recovery_detail`.

Do not silently reuse the unqualified `/usr/local/lib/hermes-openhealthatlas`
profile or `openhealthatlas` alias; they are different topology.
A live preflight must read the actual service configuration and staged paths
for all five topology facts above. Any missing value or mismatch—including a
different MCP alias or observed registry tool name—stops deployment before a
copy, configuration edit, or restart.

## Staged deployment order

1. Complete `openhealthatlas-recovery-item6-rollback-inventory-v1` before any
   deployment-target write. Every planned mutation target must be exactly
   covered by the inventory, and every mandatory protected control must also be
   inventoried without being classified as a write target. Record every
   target's canonical path, present/absent state, type,
   non-symlink proof, hash/size where regular, owner/group, mode, mtime, ACL,
   xattr and capability state, plus a byte-equivalent backup or explicit
   absence marker. Record the gateway user-unit fragment, drop-ins, referenced
   environment files, enabled/active state, `ExecStart`, `WorkingDirectory` and
   resolved `HERMES_HOME`. Preserve the existing dirty checkout's tracked
   binary patch, index state and all untracked entries in a private opaque
   archive. Use a root-owned mode-0700 backup root and mode-0600 inventory.
   Rehearse restoration into an isolated temporary tree before proceeding.
2. From the exact OpenHealthAtlas candidate, run
   `scripts/build_readiness_ancestry.py` against the accepted fictional v5
   database and fixed range `2026-03-02` through anchor `2026-06-30`. Write to
   the new, non-existing private path in the staging manifest as the `hermes`
   service account. The generator must create a hermes-owned mode-0600 sidecar
   and must not print its path, database identity, canonical-row digest, or
   sidecar identity.
3. Recheck the database hash and file stats. Any database change, SQLite WAL,
   SHM or journal file, sidecar replacement, or schema version other than v5
   stops the deployment.
4. Stage exactly the OpenHealthAtlas sources listed in
   `recovery-item6-staging-manifest.json`; reject missing, extra, wrong-owner or
   wrong-mode artifacts. Configure `readiness_fixture_lane` as `accepted-v5`
   and bind the private sidecar path only in the root/service-owned tool
   configuration. Do **not** run `install-openhealthatlas-tool.sh`: that legacy
   Green-day installer can synthesize a fixture, installs the development
   configuration, omits Item 6 artifacts, and may restart the bridge.
   Generate the recorded `toolkit/DEPLOYED.manifest` only after its exact
   OpenHealthAtlas commit is immutable; validate its one-line contract and mode
   before the first deterministic tool call.
5. Materialize the full tracked external-Hermes candidate tree in
   `/opt/hermes-agent-releases/<exact-commit>` from the exact fork commit and
   verify every path against its recorded Git tree. Do not copy `.git`, a venv,
   caches or generated files. Preserve the existing gateway `ExecStart` Python
   and make the new clean directory its `WorkingDirectory` through the new
   user-unit drop-in
   `hermes-gateway.service.d/openhealthatlas-item6.conf`; never write into the
   current dirty checkout. Install the OHA plugin
   disabled by invoking `install-openhealthatlas-delivery-plugin.sh` with the
   Hermes home resolved from the actual service configuration as an explicit
   `--hermes-home` argument. The installer must never default a root invocation
   to `/root/.hermes`. Install at
   `$HERMES_HOME/plugins/openhealthatlas-recovery-delivery/` and preserve every
   other plugin setting when adding `openhealthatlas-recovery-delivery` to
   `plugins.enabled`. Merge the exact `mcp_servers.openhealthatlas_fictional`
   stanza from the v2 manifest into `$HERMES_HOME/config.yaml`, preserving all
   unrelated keys and using the unchanged gateway Python as `command`. Record
   the complete rendered config and its expected post-hash before replacement.
   Preserve the exact MCP server alias
   `openhealthatlas_fictional`; Recovery renderer v1.0.0 retains its accepted
   two-tool identity, while generic-evidence renderer v1.0.2 binds the four
   composable health tools. Both reject bare names, other server prefixes, and
   suffix collisions. Verify the exact twelve-name MCP include set. Stage and
   hash exactly the eight external-Hermes runtime
   files enumerated by the v2 staging manifest, including the tracked
   `plugins/platforms/telegram/health_actions.py`; missing, extra, or changed
   runtime files stop preflight. Do not copy any runtime file into the preserved
   dirty checkout.
6. Verify every staged OpenHealthAtlas file against the exact candidate
   `RELEASE_MANIFEST.tsv`, verify that manifest's hash against the superseding
   release record, and verify all external-Hermes runtime hashes against that
   record. Do not restart a service until a separate VPS-deployment approval
   is recorded.

## Gate ownership and ordering

| Gate | State required before staging | Action class |
|---|---|---|
| Clean combined Hermes candidate | Upstream-base ancestry, exact commit/tree/hashes and tests accepted | Local candidate construction |
| Live checkout and rollback inventory | Actual service pointers resolved; dirty checkout preserved; complete inventory and restore rehearsal passed | Separately approved pre-stage backup and restore-rehearsal mutation |
| MCP alias, plugin and ancestry sidecar | `openhealthatlas_fictional`, delivery plugin and new sidecar installed under a separately approved staging window | Live staging mutation |
| Restart and running registry | Exact gateway unit restarted and expected registered tool names observed | Separately approved runtime mutation |
| Telegram and fault injection | Genuine two-turn fictional acceptance and bounded failures pass | Separately approved live acceptance |

Passing an earlier row never authorizes a later row.

## Required acceptance gates

All acceptance inputs are fictional. A source test, Graphify edge, or passing
unit test cannot substitute for the relevant runtime gate.

- **Deterministic:** one fixed-range Recovery run returns
  `insufficient_data`, no overall score or band, sleep `92`, and HRV/resting-HR
  exclusions with `10` same-source Fitbit baseline observations against `14`
  required. Recompute the public result, input, policy, and public-evidence
  identities from their documented projections.
- **Ancestry:** the v5 sidecar verifies in one read-only SQLite transaction;
  database and sidecar hashes/stats are identical before and after. The public
  result says provider sync was not performed, training ancestry is
  manifested, and the historical generation event is not attested. Private
  paths and database/canonical-row/sidecar identities stay out of MCP model
  input, gateway renderer input, Telegram, screenshots, and committed public
  evidence.
- **MCP:** snapshot and each detail completion are independently
  self-contained. Trusted facts originate only from the successful direct
  same-turn OpenHealthAtlas completion and are bound to Hermes session, turn,
  and tool-call identities by external Hermes. Receipt lookup, caller facts,
  and model-authored facts are rejected.
- **Telegram:** external Hermes validates the complete governed text payload
  before Telegram formatting and before the first Bot API send. Formatting and
  multi-chunk delivery are not atomic wire-byte validation. Every trusted
  disclosure chunk precedes the first optional-prose chunk; a partial failure
  invalidates the delivery plan. Optional prose is visibly labelled `Hermes
  interpretation` and is constrained only by the documented explicit bounded
  checks—not by general semantic verification. Its visible facts include
  fictional scope/range, deterministic state,
  sleep and exclusions, policy ID/version/hash, public identities, current
  snapshot scope, absent provider sync, manifested training ancestry,
  historical-event limitation, versioned sharing and qualitative-source
  authorization, disabled write-back, exclusions, and non-diagnostic meaning.
  Renderer v1.0.0 accepts only the accepted schema-v5 lane; a development-v6
  completion must yield the fixed no-prose failure payload.
- **Safety failure:** a missing/malformed delivery marker, absent renderer,
  renderer exception, payload mutation, destination redirect, token replay,
  invented overall score, explicit conflicting state/range/fixture/policy, or
  enabled-write-back claim produces the fixed no-prose failure payload. The
  bounded checks are not claimed to understand every natural-language
  contradiction.
- **Privacy:** a synthetic raw-note canary and any note-derived secret are
  absent at the OHA projection boundary and never reach the gateway renderer.
  The restricted server-side deterministic receipt may retain the fictional
  raw note at mode 0600; no Hermes interpretation is stored.
- **Database continuity:** database hash, size and file stats remain unchanged;
  schema stays v5; no synthesis run or interpretation write-back row is added.
- **UI:** after the final evidence contract is stable, a fresh localhost
  desktop and mobile browser run shows the same `insufficient_data` state,
  sleep `92`, no `58/100` result, and no invented score. Browser chat remains
  optional and is not an acceptance dependency.
- **Real data:** non-fictional envelopes and missing, unknown, or unapproved
  governance decisions remain denied. No real/private data is accessed.

Live Telegram proof and VPS fault injection require their own approval. Record
their results only after they run; never promote the local candidate record to
deployed or Working runtime evidence in advance.

## Rollback

Rollback is unavailable until the v1 rollback inventory is complete and its
isolated restore rehearsal reproduces every recorded hash, mode, owner and
service pointer. The inventory must cover:

- the 31 deterministic source files, generated `DEPLOYED.manifest`, five fixed
  OpenHealthAtlas profile/source destinations, and every created or
  metadata-modified parent directory;
- the eight clean external-Hermes runtime files, Hermes skill, four plugin
  files, `$HERMES_HOME/config.yaml`, enabled-plugin state, and exact MCP alias;
- the gateway user-unit fragment, all drop-ins and referenced environment
  files, enabled/active state, `ExecStart`, `WorkingDirectory` and
  `HERMES_HOME`;
- the accepted v5 database and fixture manifests, ancestry-sidecar
  present/absent state, tool audit and both MCP receipts; and
- a private opaque archive of the untouched live checkout's HEAD, branch,
  origin role, index, tracked binary patch, modified-file bytes/metadata and
  all untracked entries.

On any deployment or acceptance failure, stop only the new clean gateway path,
restore the prior service pointers, code, config, plugin and evidence-file
state from verified backups, and restart only the previously approved gateway
service. If the sidecar did not exist before staging, move the failed sidecar
to restricted quarantine and restore absence at its live path without deleting
evidence. Move a failed clean release to restricted quarantine outside
`/opt/hermes-agent-releases/`, then restore prechange absence at its release
path. Never reset, clean, overwrite, archive or delete the original dirty
checkout during this rollback, and never overwrite, migrate or delete the
accepted v5 database.
Because this candidate adds no schema migration and keeps write-back disabled,
rollback must not require a database down-migration.

## Explicit non-goals

This slice does not add a model/provider or interpretation runtime to
OpenHealthAtlas, enable interpretation write-back, activate real/private data,
implement legal compliance, execute deletion, prove the original fixture
generation event, make browser chat mandatory, or claim full semantic review
of arbitrary Hermes prose. Item 6 remains Partially built system-wide until
separately accepted runtime and real-data governance work is complete.
