# Deployment

OpenHealthAtlas is safe to evaluate locally without enabling any background process.
The files under `deploy/` are generic examples for a dedicated Linux service
account; review and adapt every path, hostname, user, and schedule before use.
Start from `.env.example`, `config/insights.env.example`, and
`config/hevy.env.example`, and `config/telegram.env.example`; copy populated versions only to external,
access-controlled configuration directories.

Back up both databases before applying a schema migration, then verify the
copies can be opened and pass SQLite `PRAGMA quick_check`. Keep configuration,
credentials, uploaded material, and database files outside the source tree.

## Local MCP and continuous operation

For a user-operated local MCP connection, follow [Local MCP setup](LOCAL_MCP.md).
The optional stdio server is launched by the AI client and exposes read-only
health tools. It does not require a hosted server, Hermes, Telegram or changes
to existing system services. Install `requirements-mcp.txt` only in the
environment used to run that connection.

The deterministic OpenHealthAtlas core and standalone MCP connection do not
require Hermes. The existing integrated chat, Telegram and continuous scheduled
automation require a separately installed compatible Hermes runtime. The operator must
also provide an always-on host, configure the selected model or provider,
connect any external services, and keep those dependencies healthy.

The repository supplies bounded adapters, installer contracts, and disabled
service/timer examples; it does not distribute Hermes itself or silently
activate background jobs. “Continuous” means the approved services can run
while the host and external dependencies remain available—not that the system
is maintenance-free or independent of its infrastructure.

Do not enable a timer or service until its command succeeds interactively with
synthetic data and the intended least-privilege account. Collectors should have
only the network and filesystem access their external provider requires.

Telegram polling remains an external dependency. The retained worker and
gateway-extension contract are documented in
`deploy/hermes-telegram-integration.md`; the installer never patches or
restarts an external checkout.

No automatic down-migration is provided. Rollback is non-destructive: stop the
new process, retain the failed database for diagnosis, restore a verified
pre-migration copy to a new path, and point the private deployment configuration
at that copy. Never overwrite the only database copy.

Migration records require an exact code identity. A Git checkout resolves this
automatically. For an archive/package deployment without `.git`, set
`HERMES_CODE_VERSION` to the 40-character commit identifier of the packaged
source before running `toolkit/health.py schema-migrate`; do not reuse a
historical deployment manifest from another checkout.

Autonomous insight schedules use `deploy/hermes-autonomous-context` and
`deploy/install-autonomous-insights.sh`. The installer defaults to `--dry-run`;
`--install` publishes every job disabled, and each activation stage needs a
separate explicit approval. Review the fictional UTC job contract and supply a
root/service-owned, non-writable external context file before any activation.

## Vault location

The panel and CLI share vault-path resolution: `HEALTH_VAULT`, then `VAULT_DIR`,
then an explicit `HERMES_DATA_DIR` plus `/vault`. Legacy database-only callers
retain a vault beside their explicitly selected database. With no overrides,
the standard data directory contains `health.db` and a separate `vault/`.
Configure the same environment for the panel and broker. The resolver does not
move existing notes or sidecars; inspect and copy any older data separately.

## Panel agent paths

The server-owned environment may set `HERMES_VAULT` for Hermes working-directory
and autonomous-context files, `OPENHEALTHATLAS_TOOL_AUDIT_LOG` for the protected
adapter audit, and `OPENHEALTHATLAS_TOOL_HEALTH_CLI` for the CLI identity expected
in that audit. Defaults remain the generic paths in `deploy/hermesctl`. These
settings are operator configuration, never fields accepted from a panel request.
The protected fictional adapter's CLI identity is distinct from a collector's
`HERMES_HEALTH_CLI`; preserve the correct path for each consumer.

Preserve existing passkey identity, origin and secret values when upgrading an
older installation. Move any old source-embedded timezone, medication grouping
and display values into private environment configuration before switching to
the generic source defaults. Keep those values out of the release archive.

## Background analysis and exact result reuse

The Dashboard and Insight Explorer submit a bounded analysis job and receive
an acknowledgement instead of holding a web request open for the calculation.
The existing broker processes the queue; there is no additional service or
model dependency. Authenticated status requests return queued, running,
completed, failed or stale. Browser storage contains only an opaque job ID.
Stop waiting stops polling; Resume or reloading reconnects to the stored job.
Interrupted work is recovered after the OS execution lock proves the old
calculation has ended, with bounded retries and attempt-specific publication.

The toolkit stores derived jobs/results outside the canonical health database
in a private `.openhealthatlas-analysis` directory beside it, separated by
database identity. The trusted `OPENHEALTHATLAS_ANALYSIS_DIR` environment setting
can select a different private parent directory. The broker account needs
write access there; directories are 0700 and files 0600. Keep this health-data
sidecar out of source, public backups and other users' access. It is disposable
derived state, not a substitute for health-record backups. It must be shared
by the matching CLI/broker processes and included in any required service
write-path allowlist.

Exact reuse covers the full request, coherent SQLite contents including WAL
changes, executable files, configured catalogs and civil day/timezone. Cached
evidence is never accepted on age or file timestamps alone. Stored payloads
are integrity checked. Clock-sensitive collector readiness is refreshed before
returning an outcome; unchanged findings need no repeated statistical work.
Generic Hermes analysis/evidence replay shares the raw calculation while
retaining existing result/finding identity checks. Legacy finding replay uses
its own exact key because its modes and pairwise search differ from Explorer.

One OS lock per database limits expensive work across jobs and synchronous
calls using its private workspace. The lock is inherited by the calculation child, so losing
its supervisor cannot permit a concurrent replacement. Cache storage is bounded
to 16 results/64 MiB; individual results are at most 8 MiB. The job queue and
completed history have separate bounds. Cache corruption is not evidence and
does not bypass the execution lock. Existing immutable installations without
a usable private workspace retain their stateless synchronous read behavior;
background jobs require the configured writable workspace.

### Optional exact permutation accelerator

Python remains the default and requires no compiler or native library. An
operator can explicitly build the optional kernel with an already installed
64-bit Linux/macOS C compiler, placing outputs outside the source checkout:

```bash
python scripts/build_native_stats.py \
  --output /absolute/private-runtime/rank_products.so --cc /usr/bin/cc
```

macOS uses a `.dylib` output and may need `--sysroot` for its installed SDK.
Set `HEALTH_STATS_NATIVE_LIBRARY` in the canonical broker/CLI environment to
the resulting absolute, canonical library path. Keep the adjacent `.json`
receipt. The matching library and receipt are operator-owned deployment
artifacts; never put them in Git. Files and their directories must not be
group/world writable. Public-code artifacts may be root-owned 0755/0644 so the
actual service identity can read them; derived health-data caches remain private.

The loader verifies source/build identity, binary hash, platform, ABI and a
numerical self-check. Missing, incompatible or rejected libraries use Python;
no request compiles or downloads anything. The accelerator preserves random
draws and their order. Python still performs the accurate summation, scaling,
clamping and probability calculation. Product buffers are bounded to 2 MiB.
The loader and C source participate in evidence/cache source identities.

Build and measure under the same resource limits as the application. Verify
`native_status()` under the actual service account/group rather than assuming
that a configured path means acceleration is active. Deploy the exact validated
binary with matching source. The separately installed fictional acceptance
toolkit and its configuration need not be changed to accelerate canonical
browser/broker reads.

## Analysis request budget

Broad outcome analysis and finding replay have a finite 570-second broker
child limit. The panel waits 580 seconds, the broker socket allows 585 seconds,
and the example gunicorn worker allows 600 seconds. Keep any separately
configured proxy timeout above the panel wait.

Before the background-job and exact-statistics optimization, the comprehensive
120-day fictional all-mode request completed through the real
client and broker in 499.89 seconds on a shared one-CPU application budget;
exact evidence replay took 478.98 seconds and peak child RSS was 2.37 GiB.
Larger windows and simultaneous requests can take longer. Hypothesis promotion
keeps its separate 110-second child / 190-second client limits. External Hermes
and protected generic health tools also retain separate limits.

Deploy the complete matching toolkit and broker declarations. An older health
database needs an explicit schema plan and migrations through `health.py`;
version 6 rebuilds two synthesis tables and version 7 updates the lab catalog
constraint while preserving records and historical confirmation labels. Make verified consistent
snapshots first, rehearse locally on the server, pause writers during the final
migration and compare named-column record contents before resuming. A failed
later migration can leave an earlier step committed; restore the verified
snapshot together with compatible code if rollback is needed. Never silently
rewrite incompatible records to make a migration pass.

The synchronous endpoints and their limits remain available for compatibility.
Background jobs have the same bounded calculation deadline; the web request
does not wait for that deadline. Service shutdown may interrupt computation,
while the durable queue and lock make recovery explicit on restart.

## Historical lab-catalog compatibility

One supported migration-001 variant used `owner-confirmed` where the later
public declaration used `user-confirmed`. The exact historical checksum and
table definition are recognized together; arbitrary checksum or schema drift
is rejected. Never edit the old migration ledger to match the new source.

Migration 007 keeps every catalog value, including the original confirmation
label and creation time, and adds support for both labels. Existing migrations
001–006 retain their original definitions and checksums. Current lab ingestion
continues to write `user-confirmed`. Rehearse upgrades against verified private
copies before enabling the updated code against existing records.

Readiness fixture lanes remain explicit: accepted-v5, development-v6 and
development-v7 identify their exact schema versions. Existing fixtures are
validated and keep their matching lane; configuration changes do not silently
migrate or relabel them.
