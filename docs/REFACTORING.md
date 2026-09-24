# Refactoring checkpoint

## Ledger validation owners, 24 September 2026

Following independent acceptance of `1eadde3`, shared contracts and analytical
validation move into private ledger modules. The
[architecture guide](ARCHITECTURE.md#feature-ledger-and-orchestration-command-ownership)
records their responsibilities. The facade retains seal creation, its single
token, call-time clocks and all persistence. Public functions, error and result
types retain explicit facade exports; their implementation module changes are
recorded separately from behavioral compatibility.

Four new first-failure cases passed unchanged production code before extraction.
The focused ledger, Phase 5 CLI, synthesis, provenance, exact-cache, Phase 6 CLI
and orchestration suites passed 296 tests in 156.87 seconds on Python 3.12.13,
with no failures, errors or skips. The candidate total includes those four cases.
Five valid payload/database comparisons and six exception comparisons preserve
all values, codes, validation flags and messages. Error class ownership moves
to `_ledger_contracts`, while callers still catch the same facade-exported type.

Structural comparison accounts for every original declaration. The desktop
builder's source-copy routine produced an isolated layout that passed three
fresh import orders, seal-isolation checks and public compatibility checks.
This is source-layout verification, not a native application build. Both new
modules are registered in the broad surface inventory; the narrow numerical
provenance identity remains unchanged. Independent review precedes transition
extraction; integration owns the later full-suite and packaged application gates.

## Ledger validation extraction, 24 September 2026

This batch starts from public `main` at
`84e931000af3a13a631d3a860437ccd271baf9a2`. Sample-count and effect-orientation
checks now have private helpers inside `ledger.py`. Validation order, error
codes, seal types, clock seams and caller-owned transactions remain unchanged.
The remaining numeric checks, transitions and persistence stay in place.

The focused ledger, Phase 5 CLI, synthesis, provenance and exact-cache suites
passed 254 tests on Python 3.12.13 in 63.99 seconds, with no failures, errors or
skips. Eight new validation-precedence and seal-isolation cases also passed
against unchanged production code before extraction. They are included in the
254-test candidate count.

Five fictional valid cases retain identical canonical payloads and complete
database dumps; six invalid cases retain identical exception types, codes,
validation flags and messages. Public exports and signatures match. The broad
surface and exact-cache identities changed, while the narrow numerical
provenance manifest and identity remain unchanged. No stored evidence or
fixture fingerprint was refreshed.

Independent review precedes the next validation-module extraction. Full-suite
and packaged application verification remain integration checkpoints. Interface
work proceeds separately; profile editing is excluded from this implementation.

## Quality audit, 24 September 2026

This local batch starts from public `main` at
`03a3aacca8572fa534d8f594530de3afb63f098e`. It fixes finding ranking when
statistics are missing, dropdown keyboard behavior, batch-entry error feedback
and accessible control names. Reviewed dead code and unused imports are removed.
The [development guide](DEVELOPMENT.md#static-checks-and-dependency-updates)
describes the new static checks and dependency-update policy.

Verification used fictional data on Python 3.12.13: 2,100 application tests,
15 end-to-end journeys and five actual MCP tests passed, with no failures,
errors or skips. The unchanged baseline passed 2,118 tests. Two new regression
cases failed before the missing import was fixed. Eighty focused compatibility
checks also passed on Python 3.11.15. Browser checks covered desktop and narrow
navigation, persisted meal/batch entries, invalid-input recovery and analysis
waiting states. Independent source review found no remaining actionable issue
in the reviewed changes. These checks do not qualify a replacement installer.

Larger ledger and synthesis refactors remain separate work. Readability work
should also address technical result summaries and missing-profile setup routes.
The older sections below are historical records. Use the current README and
release documentation for installation and publication status.

## Current toolkit project

The current plan is [Health toolkit modularization](TOOLKIT_MODULARIZATION.md).
It owns scope, architecture, milestone order, acceptance and continuation policy.
Older entries below are dated history, including superseded approval checkpoints.

The approved toolkit plan is complete locally. Milestone 4's production commit
is `17851cfca77b33369a22a04d97f7f0a72c0ac908`, with tested tree
`a817fecead2567ea7956c1183f6dba51338b2954`. See
[final acceptance](#final-toolkit-acceptance-22-september-2026) for verification,
package sealing and remaining qualification limits.

The branch remains `codex/health-toolkit-modularization`; accumulated commits
are preserved. The plan ends with this milestone. Push, merge, hosted checks
and release publication retain their separate authorization and review gates.

The [corrected baseline record](#corrected-toolkit-baseline-22-september-2026)
contains the inherited resampling and finding-ID tie-break caveat. Final
acceptance preserves that caveat and the collector failure-path limitation.
Machine-specific execution records and raw evidence remain outside public
source. Older entries below are historical checkpoints.

## Agreed requirements

The owner delegated selection of the most complete development branch. Every field and control already wired to collect or display data is required functionality. Report broken or unverified fields instead of deleting them. Telegram is the primary interaction path. Preserve useful health features and external Hermes integration. Company-specific work, the public website and portfolio redesign are deferred.

Public author credit stays. Personal health records, machine/home paths, device identifiers, credentials and real deployment details must not enter the eventual public release, including its published history.

## Verified starting point — 15 September 2026

- GitHub repository: `kajeesan/OpenHealthAtlas`, currently private.
- Selected source: `codex/general-health-tool-surface` at `b8cf11c8ae03c8e171cad5ac437dc6bb5da3bd71`.
- It is 44 commits ahead of `main` (`f17025dfcf47ce67ba560a4d79d980bba7430b15`), 12 ahead of the recovery branch, and 30 ahead of the interpretation-safety branch.
- GitHub comparisons confirm the other two development branches are ancestors, with no unique commits missing from the selected branch.
- GitHub heads match the locally available objects used for inspection. The original audit checkout remains on its original main commit.
- Work happens on an isolated local `codex/health-refactor` worktree. No remote branch or repository setting has been changed.
- Existing development work includes generic Hermes tools, Telegram delivery, natural interpretation wording support and date-sensitive fitness fixture fixes. Do not rebuild them from the older main snapshot.

## Privacy findings

The repository scanner found four files with private absolute home paths in the selected commit, among 415 tracked files:

- `tools/project_map/evidence/fictional-primary-telegram-proof-levels-2026-08-17.json`
- `tools/project_map/evidence/general-health-surface-vps-deployment-2026-08-19.json`
- `tools/project_map/evidence/generic-renderer-v101-vps-deployment-2026-08-19.json`
- `tools/project_map/evidence/generic-renderer-v102-vps-deployment-2026-08-19.json`

Scanning all locally available refs found six affected historical file paths across 745 blobs. The two additional historical paths are `tools/project_map/README.md` and `tools/project_map/app.py`.

Matched private values are deliberately omitted here. This was a scan of committed tree blobs and locally available history with the existing scanner, not an exhaustive privacy sign-off. Working-tree hygiene and the final chosen publication history require separate verification. Editing the four current files does not remove their earlier versions from Git history.

## Functional gaps to verify, not remove

The development documentation reports that the comprehensive fictional-persona Green-day analysis exceeds the broker timeout. It also records that the corrected insufficient-data Recovery result needs renewed browser verification. These are documented prior findings, not newly reproduced failures.

The unimplemented register lists additional partial/scaffold features. Map each to actual UI controls and runtime consumers before deciding whether it is an existing wired feature, a misleading placeholder or unimplemented future scope. Primary Telegram status does not authorize removing other wired UI behavior.

## Refactoring sequence

1. Inventory each existing wired UI read/write path: screen and field, handler, endpoint, tool/service, stored data and displayed result. Record `verified`, `broken`, `unverified` or `not wired`; code presence alone is not a pass. Reuse the existing route inventory and focused tests instead of creating another exhaustive framework.
2. Remove incidental test locks: exact CSS, documentation phrasing, broad source-substring bans and whole-page HTML golden fingerprints. Keep actual behavior and meaningful existing checks. Do not create a replacement test for each removed incidental assertion.
3. Trace and retire synthetic interpretation/reference components that have no retained product consumer. Preserve necessary shared contracts and deterministic ledger/evidence functions. Remove exclusive obsolete tests with the code.
4. Simplify shared calculation services, clock/database context and duplicated command definitions while retaining compatible caller routes and every required wired field.
5. Repair actual gaps and measure representative fictional workflows. Run relevant checks for each changed batch; broaden verification for the final retained product. Repeat only when changes or failures justify it.
6. Complete source sanitation, choose a clean publication-history strategy, update the manifest and documentation, and present the exact release state for publication approval.

## Checkpoints and proportional verification

Local Git commits are the primary code checkpoints. Save each coherent completed batch with a clear message and its relevant verification results. Keep the initial `b8cf11c` baseline identifiable, but do not describe it as a passing or release-ready version. Known baseline defects remain explicitly recorded above. Save unfinished work only when useful, and label its verification status honestly.

| Change | Appropriate verification |
| --- | --- |
| Documentation or working instructions | Review content and scope, check whitespace, check newly committed content for privacy hazards and verify the file manifest. No application test run. |
| Removal of incidental tests only | Confirm actual product code is unchanged, collect affected test files and run the remaining relevant checks. |
| Shared code or module removal | Check retained imports and entry points, then run tests for affected callers and a representative workflow. |
| Calculation or data parsing | Use existing known-input/expected-result cases, relevant units/time/source boundaries, and a focused regression case when missing. |
| Wired UI data field or Telegram flow | Exercise the affected input-to-storage-to-display or tool-to-reply path with fictional data, plus relevant existing tests. |
| Database schema or data change | Work on a copy, create a consistent SQLite backup outside Git, verify it opens and passes integrity checks, test the migration and rehearse restoration on a disposable copy. |
| Final release candidate | Run the curated retained suite, retained-workflow checks, manifest verification and source/history privacy review. |

A new test needs a distinct supported behavior or realistic regression to protect. Do not add replacement tests for deleted incidental text/style locks. Repeat checks when changes or failures justify it.

For a failed code batch, identify the responsible diff and use a scoped restoration or revert, preserving unrelated work. For a data/schema failure, restore the matching database snapshot alongside compatible code in the test environment. Never assume a Git revert undoes a migration or external service action. Do not upload database snapshots or raw diagnostic logs to GitHub.

### Checkpoint log

| Checkpoint | Source | Verification status |
| --- | --- | --- |
| Pre-refactor baseline | `b8cf11c8ae03c8e171cad5ac437dc6bb5da3bd71` | GitHub branch inclusion and source identity verified. Known privacy and functional gaps remain. |
| Setup and recovery policy | Commit titled `docs: establish refactor checkpoints and verification policy` | Documentation-only checkpoint. Its handoff records the resulting commit ID and verification results. |

The setup checkpoint uses the existing baseline's sanitized author/committer identity so Git does not add the workstation's configured private email. It does not change the user's Git configuration.

## Batch 1 — incidental test constraints

- Recovery point: `693e252255bfa90e422f9ef5a67524c684949995`. The app-created worktree was detached at this revision; its local branch is `codex/health-refactor-implementation`. The original worktrees remain untouched.
- Removed exact CSS, broad source-substring bans, deployment-document sentence checks, whole-page HTML golden hashes, and incidental shell wording/class/mock-value locks. Static fixture rendering, wired controls/navigation, read-only database enforcement, delivery projections, service hardening and browser security response checks remain.
- Changed tests: `test_db_read.py`, `test_phase7_conversations.py`, `test_shell.py`, `test_openhealthatlas_tool.py`. Product code is unchanged. Existing fixture rendering was reduced to one render per page; no replacement test matrix was added.
- Verification: `python -m pytest -q -p no:cacheprovider tests/test_db_read.py tests/test_phase7_conversations.py tests/test_shell.py tests/test_openhealthatlas_tool.py` — **107 passed in 4.17s** in an isolated environment installed from `requirements-dev.txt`. Independent diff review caught a still-used test constant during cleanup; it was restored before this final run. Bytecode/cache outputs are disabled or external.
- Fresh independent investigation: 16 Recovery/Green API tests pass; the comprehensive fictional Recovery workflow returns `insufficient_data` with its available sleep component and no fabricated overall score. Browser proof and comprehensive Green timing remain outstanding.
- Reference consumer trace found only test-gated Flask endpoints and a synthetic demo stage outside exclusive interpretation tests. There is no frontend consumer. Deterministic contracts, ledger, synthesis evidence, provenance and genuine Hermes integration remain required.
- Publication remains blocked: the existing scanner still reports four current and six historical paths with private home paths. Historical live-service receipts are not fresh runtime proof.

## Batch 2 — retire the synthetic reference runtime

- Recovery point: `e968fb2667a3b9fbdf69a80b49c33714858a79e7`.
- Removed 19 synthetic interpretation modules, their taxonomy module, 17 exclusive toolkit test files and the reference API test file. Removed only the two test-gated reference routes, their configuration and the independent synthetic demo stage. The shared deterministic contracts, ledger, synthesis evidence, provenance, generic Hermes adapters and frontend files remain unchanged.
- Independent source review verified retained imports, dynamic consumers, script entry points and route registrations. No retained product consumer of the deleted package remains. Historical receipts stay historical; active architecture, testing, privacy, retention and product-map documentation now describe retirement. Publication documents no longer claim that old passes certify this branch.
- Verification in the isolated pinned environment: `python -m pytest -q -p no:cacheprovider tests/test_insights_api.py tests/test_phase7_conversations.py tests/test_demo_flow.py toolkit/tests/test_phase5_ledger.py toolkit/tests/test_phase5_synthesis.py toolkit/tests/test_phase5_cli.py tests/test_project_map_proof_levels.py tests/test_project_map_item6.py` — **324 passed in 176.08s**. Full retained collection — **1,842 tests in 1.27s**. Collection is not a full-suite pass.
- Fresh browser verification through the real local broker and comprehensive fictional database: Recovery shows a dash and the insufficient-data reason; its drill shows sleep 92/100, HRV/RHR excluded with 10/14 same-source observations, muscle recovery and the projected soreness summary. No browser errors/warnings observed. This verifies the previously outstanding Recovery display; it does not establish genuine external-Hermes delivery.
- Fictional SQLite backup and restoration rehearsal passed integrity and foreign-key checks and compared schema plus every row across 69 tables. Data and diagnostics remain outside source.
- Comprehensive Green analysis reproducibly exceeds the broker budget (**110.12s**). A 40-second partial profile measured alignment at 28.04s cumulative, including 16.39 million repeated day-value calls; frame construction used 10.32s. This is measured early-run evidence, not a complete runtime profile. Optimize only after structural extraction, preserving arithmetic, range boundaries and evidence.

### Retained path checkpoint

| Surface | Owning path | Fresh evidence / remaining limit |
| --- | --- | --- |
| Recovery KPI, trends and readiness drill | Recovery JS → recovery/dash APIs → read-only DB or broker `readiness` | Engine, API and insufficient-data browser result verified. |
| Dashboard Green-day card | Dashboard JS → insights API → broker `outcome-associations` | Endpoint tests pass; comprehensive workload times out. |
| Nutrition recipes, eating/prep, nutrients and supplement adherence | Nutrition JS → nutrition APIs → read models / validated CLI | Existing API checks pass; fresh browser write journey still pending. |
| Vault note picker, diff and save | Vault-note JS → plans note APIs → `write-note` | Existing API checks pass; secondary path retained. |
| Mind, care and consistency readings | Domain JS → dash/read-model APIs | Existing source/missingness/habit/care checks pass. |
| Scoped browser conversations and chosen evidence | Conversation JS → chat APIs → external Hermes bridge / panel records | Local contract checks pass; genuine external completion unverified. |
| Generic logging and program APIs | Log/plans APIs → validated CLI | Existing API tests pass. No current quick-log or program-editor browser caller found; retained agent operations remain. |
| Social/prescriber placeholders, XP and recipe photos | Existing templates / documented scaffolds | Dedicated storage/input engines are not wired; no existing field or control removed. |

## Batch 3a — shared analytical runtime

- Recovery point: `c7de096d7264cbb223f1b45bf25b3182ee171cee`.
- Moved configured catalogs, shared formulas, database/clock bindings and analytical context construction into `toolkit/hermes_insights/catalogs.py`, `calculations.py` and `runtime.py`. `hermes_surface.py` now imports the shared runtime directly and does not load the CLI. `health.py` retains compatible entry points and call-time bindings; it is now 9,409 lines versus 10,089 at handoff.
- Preserved initializer side effects, nutrient configuration, fixed-time calculations, source preference, read-only enforcement and coherent transactions. Independent review compared moved catalog statements and calculation bodies with the recovery point and found no semantic changes. Generic engine fingerprints now include the moved files.
- Checks: `python -m pytest -q -p no:cacheprovider toolkit/tests/test_engine.py toolkit/tests/test_phase3_adapters.py toolkit/tests/test_mobility.py toolkit/tests/test_outcome_associations.py toolkit/tests/test_phase5_cli.py toolkit/tests/test_phase6_cli.py toolkit/tests/test_hermes_surface.py` — **166 passed in 213.93s**. The updated existing engine-manifest assertion also passed separately.
- Additional temporary checks confirmed standalone Hermes import without `health` loaded; fictional environment initializers; fixed-clock 120-day cutoff, latest-ID ties, void/protocol handling and Epley rounding against the prior source; and live rebinding of CLI date/catalog compatibility hooks. No schema or calculation policy changed.
- Active architecture/testing documentation now also reflects current migration v6; this does not change the separate accepted-v5 Recovery delivery boundary.

## Batch 3b — shared bridge declarations

- Recovery point: `0eee8c5a14f734c8d078200b5460c41d9f40e9ac`.
- Added `deploy/bridge_commands.py` as the command/flag declaration used by `app/bridge.py` and `deploy/hermes-bridge`. All 56 commands, flags, 17 client-side flag-check commands and no-value flags match the prior source. The 51 health commands agree with the CLI parser; the remaining five route to Hermes control operations.
- Broker validation, profiles, timeouts, audits and execution bodies are unchanged. `install-bridge.sh` now includes the adjacent declaration module as root-owned mode `0644`. No installation or live service was changed.
- Removed duplicate map/source-spelling assertions and adapted the existing script loader to real adjacent imports. Retained broker rejection coverage for unavailable imports and batch-portion changes.
- Checks: `python -m pytest -q -p no:cacheprovider tests/test_bridge.py tests/test_broker_validate.py` — **30 passed in 0.96s** in the integrated tree, using disposable local sockets. The isolated worker additionally verified a temporary installed layout with `python -S`, no source import path, real startup/request refusals/clean shutdown, and installer shell syntax. Independent integration review found no scope or enforcement changes.

## Source sanitation — historical operational receipts

- Recovery point: `103f7f525334ec6347979162a6fd031dfe5501f8`.
- Independently reviewed 88 tracked deployment/evidence files, including 30 receipts. Redacted private home paths in four receipts plus concrete runtime/session, backup and working-directory locators across ten receipts total. Stable labels preserve repeated identities. Original outcomes, chronology, source commits and artifact hash claims remain unchanged.
- `tools/project_map/README.md` now explains that old receipt-byte hashes describe unredacted historical records, while the release manifest covers the sanitized copies. Public author attribution remains intact.
- Removed `tests/test_project_map_item6.py` and `tests/test_project_map_proof_levels.py`: 18 historical receipt/status/prose assertions, including dependencies on private development commits, with no retained runtime execution. Their obsolete byte-equality locks were the only redaction-related failures. Calculation/evidence integrity, governance, disclosure, delivery and privacy tests remain.
- Removed generated Python caches from source; diagnostics and retained cache copies are external. Current source scanner: **pass, zero findings across 381 tracked files**. This bounded scanner plus review is not an exhaustive privacy certification. Private development history still contains earlier locators and must not be published.
- Checks: `python -m pytest -q -p no:cacheprovider tests/test_release_scan.py tests/test_openhealthatlas_tool.py tests/test_openhealthatlas_generic_mcp.py tests/test_openhealthatlas_delivery_plugin.py tests/test_openhealthatlas_generic_delivery.py` — **103 passed in 3.14s**. Sanitized JSON structure comparison and whitespace checks passed.
- Fresh dependency audit: **32 resolved packages, no known vulnerabilities** after updating only the temporary environment's pip bootstrap tool to 26.2.1. Project dependency pins are unchanged. Both vendored browser bundles match their documented SHA-256 values.

## Fictional nutrition workflow repair

- Recovery point: `f28a719157dbc151a7e31991b5367822b979deb4`.
- Browser verification found recipe cards showing 0 kcal: the comprehensive fixture stored per-gram nutrients in a legacy column that the canonical importer/calculator treats as batch totals. Corrected `scripts/make_demo_db.py` to seed the declared five-portion totals. No production calculation formula or schema changed; existing databases are not rewritten.
- Extended the existing initialization/demo test to execute menu → eat → persisted nutrition rows on its disposable fictional database. It checks 510 kcal / 24 g protein for oats and stock 3 → 2. `python -m pytest -q -p no:cacheprovider tests/test_initialization.py` — **2 passed in 3.52s**. Independent review confirmed storage semantics against the importer and calculator.
- The same browser journey exposed stale target-breakdown/gauge data after consumption. `nutrition.js` now refreshes the existing target, calorie and coverage displays after resetting the today cache. No UI control changed or disappeared. JavaScript syntax and whitespace checks pass.
- Fresh isolated browser/broker verification: corrected cards show declared nutrients; two successive one-portion actions create two `panel-ui` rows of 360 g / 510 kcal / 24 g protein, stock becomes 1, and headline plus target breakdown update to 1,020 kcal / 48 g protein without reloading. No browser errors observed. A verified fictional database backup preceded writes.
- Navigation/render smoke passed for Dashboard, Body, Mind, Consistency, External care, Labs, Data Explorer and Insight Explorer. This is rendering/navigation evidence, not exhaustive control coverage or genuine model verification.

## Vault read/write alignment

- Recovery point: `414e5d963f1d8b5ea2c77ca7dcd7f6abd0a568ea`.
- Reproduced the wired note editor reporting a save while reload returned empty: panel reads used the configured vault, but CLI writes used the database parent. The same mismatch affected recipe ingredient sidecars.
- Added a pure shared path resolver in existing `settings.py`, used by panel configuration, note writing, raw qualitative captures, recipe sidecars and synthesis. Explicit `HEALTH_VAULT`/`VAULT_DIR` and the standard data-directory layout agree; legacy database-only and programmatically rebound callers retain their prior behavior. Existing files are not moved. Whitelists, validation and write ordering remain unchanged.
- A new save/reload regression first failed against the prior code, then passed. Independent review also caught and resolved a recipe response-path issue; returned file metadata remains vault-relative and is covered by an existing test.
- Final verification: **30 passed in 7.83s**, using `tests/test_plans_api.py`, `toolkit/tests/test_qualitative.py`, the `test_health.py` nodes `test_write_note_whitelist_and_atomic`, `test_write_note_rejects_non_whitelisted`, `test_write_note_uses_rebound_database_without_environment`, `test_recipe_ingredients_set_writes_validated_sidecar`, `test_recipe_ingredients_invalid_replacement_preserves_prior_file`, `test_recipe_ingredients_rejects_unknown_recipe_without_writing`; the `test_labs.py` nodes `test_capture_preserves_bytes_verbatim`, `test_capture_refuses_overwrite`, `test_capture_rejects_path_escape`, `test_capture_rejects_empty`; and `test_phase5_cli.py::test_synthesis_record_commits_ledger_before_append_only_file`. All used `python -m pytest -q -p no:cacheprovider` in the pinned environment.
- Five fresh-process checks confirmed panel/CLI path agreement for standard defaults, database-only legacy layout, data-directory layout and both explicit vault aliases. Browser preview → save → reload now retains the exact fictional note in the configured vault.

## Measured analysis performance and final retained suite

- Recovery point: `5da863c7a2318c47f7cea5db85e38c1de761ea53`.
- Profile-led changes in `associations.py` reuse exact per-feature/day/window calculations within one analysis, preserve distinct historical ranges and ordered candidate evidence, deduplicate repeated object references only for set-valued provenance operations, and defer source-reference normalization until returned findings. `stats.py` reuses validated fixed ranks/scaling while preserving random draws, iteration counts and statistical arithmetic. Two focused regressions were added to existing test files.
- Independent review found no calculation, source/conflict/missingness or replay defect. Full old/new fixture results and all internal findings match with a shared engine identity; 8,004 raw permutation statistics match the prior implementation. Real code changes intentionally change engine fingerprints and sampling seeds.
- The comprehensive Green-only query completes in 132.46s at about 1.95 GiB peak RSS. The corrected fictional fixture completed through the real panel bridge client and broker in **134.12s**, returning three findings. The measured original family retains 3,755 permutation candidates, seven Fisher candidates and all 7.51 million permutations.
- Outcome reads now have a shared 180s child budget, 190s panel wait, 195s broker socket margin and a 210s example web-worker limit. Other broker command caps and external Hermes limits remain unchanged. Dashboard theme changes no longer start duplicate analyses. The browser now says “Stop waiting” and explains that the bounded server read may continue.
- Removed obsolete Phase 7 page-hash metadata from E2E traces. Regenerated actual engine-dependent trace seals; independent JSON comparison confirms scenario inputs, seeds, ranges, expected classifications and narratives are unchanged.
- **All 1,829 retained tests pass:** 1,814 non-E2E tests in 631.15s; 14 trace tests in 150.86s; one real-panel/broker/fake-Hermes journey in 134.87s. These are non-overlapping final partitions. Exact commands and limits are recorded in `VERIFICATION.md`. The existing ten-scenario generator also completed successfully in 152.15s.
- Remaining measured limits: the broad query still needs about two minutes and roughly 2 GiB. Comprehensive all-mode Explorer/pairwise replay performance has not been verified. Genuine external-Hermes reasoning and Telegram transport remain unverified; local E2E uses a fake external runtime. No broad feature or UI control was removed to hide these limits.

## Genuine-Hermes test setup and evidence guidance

- Recovery point: `09800f611b5bfb54a75da7c13d42f8a2fd0b77e5`. Its separate parentless export passed exact manifest/source/history checks and fresh empty initialization plus the complete fictional demo.
- A separate genuine Hermes 0.18.0 test installation and local Qwen3-4B-Instruct model were created outside this release tree. No model runtime or new dependency was added to OpenHealthAtlas. Sandboxes block private-home/keychain access and non-loopback networking during execution; all health data is newly seeded and fictional. Telegram transport is a local substitute.
- Actual model/tool execution exposed unclear evidence-reference guidance: hash-only references were rejected and delivery failed closed. Complete-object guidance allowed the genuine catalog → query → evidence → disclosure/formatting chain to finish without changing health data.
- Clarified the existing MCP description and skill with required reference fields and the one-analysis-reference limit; also clarified that vendor-like labels do not make fictional observations live data. Runtime validation and prose freedom remain unchanged. Independent review found no new restriction or contract change.
- `python -m pytest -q -p no:cacheprovider tests/test_openhealthatlas_generic_mcp.py tests/test_openhealthatlas_tool.py tests/test_openhealthatlas_generic_delivery.py tests/test_openhealthatlas_delivery_plugin.py` — **98 passed in 2.88s**. The ordinary-prompt retry and final isolated-runtime acceptance are recorded separately when complete; earlier guided success does not certify unqualified answer quality or actual Telegram network delivery.

## Advertised MCP argument schemas

- Recovery point: `f01eb39b00950726412307025767d3996e2faac5`.
- The ordinary-question recheck showed that generic dictionary schemas did not advertise `range.from`/`range.to`; the local model repeatedly supplied `start`/`end`. Evidence objects had the same missing-field metadata problem.
- Added schema-only `Annotated` metadata to the existing range and evidence-reference types in `deploy/hermes-openhealthatlas-mcp`. The documented Pydantic JSON-schema hook changes advertised fields, not value validation. Existing tool bodies, closed validators, error behavior and privacy controls remain unchanged; no dependency or test matrix was added.
- The existing four tool/MCP/delivery test files pass: **98 passed in 2.80s**. An actual MCP 1.26.0 / Pydantic 2.13.4 probe exports all 12 tools and changes only the three intended nested argument schemas. Seventeen valid/malformed request comparisons have identical SDK results/errors and handler inputs before and after.
- This fixes a model-facing contract description; the isolated ordinary-question retry remains the practical acceptance check. The unrelated external Hermes behavior that counts repeated validation errors toward a server circuit breaker is recorded as an external-runtime limitation, not hidden as an OpenHealthAtlas calculation failure.

## Final isolated-runtime result

- Source tested through `4583091fa3dd559abb39659913c9a4e4e72f746f`; exact 381-file export verified. Genuine Hermes and local model installation remain outside OpenHealthAtlas source, using fresh fictional data and restrictive local execution profiles.
- Two guided real-model/evidence/capture journeys completed (query 46.59s; insufficient-data analysis 54.02s). The latter has a misleading source-label sentence, so technical success is not unqualified model-answer acceptance.
- Advertised nested schemas fixed the ordinary question's malformed date/reference inputs. Its final context exceeded 16K; a 20K retry was stopped on machine-wide memory pressure. Positive-answer completion also remains unverified. No tool payloads, scenario values or validators were weakened to force a pass. Model services are off; restart defaults are the previously tested 16K configuration.
- Closed the original Telegram extension verification gap against genuine Hermes and the real Telegram SDK: 16 compatibility/authorization/dispatch checks passed; a real worker/CLI water action changed only a disposable copy's intake by 250ml, with duplicate idempotency. Original database and verified backup remain intact. Nine existing worker tests passed in 1.38s. Corrected the documentation's inaccurate claim that those unit tests alone exercise the adapter signature.
- Final scope, exact suite partitions and remaining limits are consolidated in `VERIFICATION.md`. The local test-instance operator record preserves successful, failed and partial runs separately. Actual Bot API transport, production installation and accepted-v5 specialized delivery are not claimed from this v6 test setup.

## Refactor checkpoint, 15 September 2026

The authorized refactor, local checkpoints, retained-suite verification and isolated test-instance construction are complete. Recovery, nutrition, vault and the measured Green-day request are repaired; APIs advertise usable nested model arguments. All 1,829 retained tests passed, followed by 98 affected checks after each metadata clarification and a real SDK comparison. Attribution remains intact; source privacy, dependency and browser-bundle checks pass. The clean parentless publication candidate is prepared and scanned separately from private development history; its final identity is reported in the handoff. No push, visibility change, shared-history rewrite, existing installation change or live Telegram operation occurred. Model-quality, resource and external-service limits remain explicit rather than hidden behind passing local tests.

## Follow-up: hosted-model acceptance and negated causation, 16 September

- Recovery point: `0cfca4743c0a5d662f9e39662eb68ffb7364c6fd`.
- An isolated profile used genuine Hermes 0.18.0 with a hosted model and the existing fictional fixtures. Five ordinary comparison, analysis, follow-up and missing-record cases completed evidence replay and real adapter capture. The positive case reached 45,950 prompt tokens and preserved the returned timing, statistics and limitations. Exact results and qualifications are in `VERIFICATION.md`; operational details stay outside source.
- A genuine insufficient-data answer was withheld because the generic delivery regex treated “would not prove causation” as an affirmative assertion. Corrected only that generic check in `deploy/hermes-openhealthatlas-delivery-plugin/__init__.py`. Direct negation is allowed; each occurrence is still inspected, so a subsequent affirmative claim remains blocked. No numeric, date, identity, evidence or write restriction changed; the specialized Recovery renderer is unchanged.
- Added one retained regression in `tests/test_openhealthatlas_generic_delivery.py`. It failed before the fix. `python -m pytest -q -p no:cacheprovider tests/test_openhealthatlas_generic_mcp.py tests/test_openhealthatlas_tool.py tests/test_openhealthatlas_generic_delivery.py tests/test_openhealthatlas_delivery_plugin.py` — **99 passed in 2.77s**. Replayed the exact rejected reply successfully and completed a fresh ordinary model run without extra coaching.
- Preserved an initial wrong-fixture test-setup failure and the external runtime's misleading circuit-breaker response to argument errors. Corrected profile selection without changing fixture data, identities or questions. All fictional database checksums remained unchanged; temporary test credentials were removed and test processes stopped.
- Rebuilt `RELEASE_MANIFEST.tsv`: all 380 listed files verify. The current-source privacy scan reports zero findings across 381 tracked files; generated verification artifacts and bytecode remain outside the release tree.
- The renderer correction is applied to local source and isolated test profiles only. Actual Telegram network delivery, the complete inbound gateway path and live deployment of this correction remain separate work requiring the appropriate test connection and authorization. No push, publication or shared-history rewrite occurred.

## Completion follow-up: full reads, installed repairs and Telegram, 16 September

- Recovery point: `1131080917e73b27cb3fbb308562004a2b4b731e`. The owner authorized the four remaining completion items and orchestrator/sub-agent/grandchild review. Production tests used only verified fictional data and required agent files; private health records and private history were excluded.
- `associations.py` now shares mode-independent exposure alignment while retaining distinct outcome rows and defers discarded internal-replay reference normalization. The existing alignment regression compares all remapped modes to independent alignment. Complete old/new output equivalence and the positive pair fixture passed with a common engine identity; statistical draws and evidence semantics remain intact.
- Local all-mode Explorer measured 149.01s / 2.42 GiB, versus 186.33s / 4.23 GiB; full replay completed in 141.94s / 2.48 GiB. Capped-server diagnostic reads took 448.55s / 442.58s with peak child RSS 2.39 GiB. They explicitly failed the former 180s budget despite correct, unchanged evidence and databases.
- Read limits now follow 570s child → 580s panel → 585s broker → 600s example worker. Promotion remains 110s child / 190s client; external Hermes and protected-tool limits are unchanged. Actual current-source application-client/broker acceptance on the capped server passed: Explorer 499.89s, exact replay 478.98s, peak child RSS 2.37 GiB. Source, fictional database/snapshot and exact finding/evidence were unchanged; real peer authorization, audit and socket cleanup passed. The source worker example was not installed.
- All **1,830 retained tests passed**: 1,815 non-E2E in 497.09s, 14 trace tests in 137.72s and one panel/broker/fake-Hermes journey in 89.01s. After the timeout-only follow-up, 98 existing broker/bridge/API tests passed in 2.05s; actual trace regeneration and all 14 replay tests passed in 133.93s. These selections overlap. Original trace inputs, seeds, classifications and narratives are unchanged.
- Installed the reviewed generic delivery correction, advertised MCP schema guidance and public skill with hash/ownership/permission checks and verified rollback copies. A real installed SDK/tool/evidence/rendering check passed without source-test mode. The specialized accepted-v5 delivery boundary and fictional database were preserved.
- Corrected external Hermes validation-error connectivity accounting separately. Its 250 canonical tests passed; five new regressions failed against the original module. The installed runtime passed four validation errors followed by a valid request, while three real closed-session errors still opened the connection breaker. No OpenHealthAtlas model dependency was added.
- Two genuine user-sent Telegram messages passed live authorization/dispatch into an isolated native gateway and persistent session, genuine hosted-model/tool processing, evidence replay and governed Bot API replies. Independent review verified the comparison and follow-up; history persisted 0 → 11 → 17 entries across separate processes. One rejected catalog call was repaired. The temporary test route is disabled, its reserved-prefix guard remains, credentials were deleted and test processes stopped.
- The current application stack remains capped at one CPU and 8,000,000,000 bytes with no swap expansion; VM allocation was not changed. The narrow installation update is separate from the isolated current-source performance copy. Full operational evidence stays outside public source. No push, publication or history rewrite occurred.
- Final live service checks passed after test cleanup. Aggregate application memory was 1.50 GB and memory-limit/OOM counters stayed zero. The final server test used the existing panel Python 3.12.3 and read-only packages; an initial empty-home mount was rejected before computation, then replaced by inaccessible home mounts. Full source/manifest and the new parentless candidate are verified separately in the final handoff.
- Final manifest verification covered all 380 listed files. Source sanitation found only six generated cache directories; these were preserved outside source, then the current-source scanner passed with zero findings across 381 tracked files. No generated verification data enters the candidate.

## Disclaimer cleanup and authorized release preparation, 16 September

- Recovery point: `0a35b8220e4f7bbe7f88e5a8d38df95a17061e61`. The owner approved consolidating repeated medical disclaimers, uploading the changes to the existing GitHub repository and deploying the complete version. Repository visibility and shared history remain unchanged.
- Removed repeated medical/diagnosis slogans from 18 browser prose locations. One short notice is available under Settings → About OpenHealthAtlas. Source, measurement, missingness, association context and every existing control remain. Two toolkit-generated descriptions retain their useful explanation without repeating diagnostic disclaimers.
- Removed the two fixed generic/Recovery disclaimer sentences from visible Telegram rendering. Existing machine fields, exact contract checks, evidence/consent requirements and diagnostic/causal restrictions remain. The public Hermes skill directs normal replies toward relevant limits instead of routine boilerplate. Both saved real replies replayed with only the fixed sentence removed; prior model-authored text was preserved.
- Verification: 116 existing web tests passed in 3.76s; 91 existing tool/delivery tests in 1.66s; 54 physio/fitness tests in 24.52s; 26 initialization/generic-surface/actual-trace tests in 148.02s. These are selected checks after the earlier full retained-suite pass, not a newly inflated total. The obsolete literal disclaimer assertions were removed; semantic restrictions remain.
- Fictional browser verification passed for Dashboard, Body/pain, the pain conversation and the Settings notice on desktop and a 390px phone viewport. Main pain content had zero generic medical-disclaimer matches, all conversation controls remained and no browser errors/warnings or horizontal overflow were observed. The temporary preview was stopped. Independent sub-agent/grandchild review found no blockers.
- Deployment preparation identified older database layouts. Existing migration suites passed 148 tests in 28.40s on fictional databases. Any server migration uses `health.py`, consistent server-only backups and record-preservation checks; no private records or account/configuration values enter source. Operational activation and GitHub identities are recorded separately after execution.
- Deployment mapping exposed three fixed `hermesctl` paths. Added server-environment configuration with unchanged defaults and validator bodies; the protected fictional CLI remains separate from the collector CLI. All 33 existing controller tests passed in 1.25s, including configured-path execution and rejection of receipts from the wrong CLI. Independent review confirmed the request cannot override these settings.

## Deployment compatibility repair — legacy lab confirmation

- Recovery point: `82a2d63cfac228ec6793a33238d71ae3ff6b561f`. Its exact clean export was uploaded as commit `23a14e10fbb60f1d501a7b21e8a525678d93e2aa` on the private release branch. A server-only rehearsal refused the older migration-001 checksum before changing live records; original services were restored after verifying unchanged records and controls.
- The legacy checksum is exactly reproducible from the public migration SQL by substituting `owner-confirmed` for `user-confirmed` in the lab-catalog CHECK constraint. Migrations 002–004 and all other inspected declarations match. A checksum alias alone would break new confirmed-lab ingestion, so it is not used as a bypass.
- Added strict recognition of the known historical migration-001/schema pair and explicit migration 007. The new closed CHECK accepts cited and both confirmation labels. Every existing named-column value and the historical ledger row remain intact. Migrations 001–006 stay frozen; unknown schema/history and unowned table dependencies are refused.
- The fresh schema supports the latest catalog shape while preserving deliberate initialization to earlier targets. Distinct readiness lanes retain v5/v6 and add v7; reused fixtures keep their validated version. No model, UI control or calculation formula changed in this compatibility batch.
- Independent review added case-insensitive dependency protection against foreign-key cascades and literal-preserving checks for confidence values. Focused migration/lab verification passed 204 tests in 52.26s, then 30 final migration checks in 4.29s; 57 readiness/API/tool checks passed in 1.94s. The operator helper separately passed a fictional legacy health-v4/panel-v1 → v7/v2 rehearsal with exact record preservation and unchanged originals.
- All **1,861 current retained tests are verified**: the non-E2E run passed 1,845 tests in 705.02s with one stale test expecting schema 7 to be unsupported; the corrected future-version-8 case passed in 0.20s. All 14 actual trace tests passed in 160.24s, and the panel/broker/fake-Hermes journey passed in 114.17s. This is complete partitioned coverage plus the corrected case, not one uninterrupted green run. Production files stayed frozen during verification.
- Ten regenerated scenarios retain their inputs, seeds, ranges, classifications and narratives; only actual trace seals changed. The analytical engine identity remains unchanged. Final manifest/export checks and operational deployment results are recorded separately. No private records or private source/configuration values are included in this release history.

## Responsive analysis and existing workflow repairs

- Recovery point: `4d773d09d6aeca2b312d62e9894ac8905bcd14ec`, deployed through clean private release `a33917eec70ce37589783fdaf5104cd1156bf6c1`. The owner requested correction of slow comprehensive analysis and incomplete existing workflows, using orchestrator/sub-agent/grandchild implementation and independent review. Existing service/resource, data and publication boundaries remain in force.
- Exact statistical optimizations precompute shuffle bounds and scaling and reuse sorted bootstrap values. Random draws, every candidate/iteration and arithmetic are retained. The same comprehensive local fixture improved from 180.55s to 123.94s; memory remained about 2.4 GiB. Complete unrounded output and internal/replay comparisons match under a common engine identity. The 112 focused statistical/association/provenance tests passed. This is a local measurement, not a new server timing claim.
- Added private persistent analysis jobs in the existing broker, immediate browser acknowledgement, authenticated status, stop/resume and reload continuity. Jobs are bounded, deduplicated and fenced by attempt identity. An inherited cross-process lock prevents overlap after supervisor loss. Canonical health records remain read-only; no health or panel schema migration was introduced.
- Added exact snapshot/configuration/code result reuse for CLI outcomes, legacy finding replay and generic Hermes analysis/evidence replay. Clock-sensitive readiness is refreshed separately. Review caught and fixed malformed cached payloads, fallback lock bypass, first-use races and unstable Python callable hashing. Actual CLI subprocesses and Hermes analysis → evidence tests prove reuse without skipping result identity checks.
- Repaired six existing workflow categories: region-save failure cannot send stale body context; Data Explorer All includes older subjective/safety data; activity distinguishes failed/unconfirmed writes and excludes reads before its limit; supplement unknown/zero/taken states and zero-adherence days are preserved; malformed/nonfinite vitals cannot be silently dropped or produce partial writes; supplement product creation validates its existing name/active/dose/unit fields. No control was removed and no speculative research/gamification feature was added.
- Focused verification: 154 log/nutrition/dashboard/health checks passed with seven unrelated restock cases deselected; 45 conversation checks passed, including a regression that fails against the prior source. Actual JavaScript module tests cover old records, nullable intake and failed actions. The final cache/Hermes selection passed 38 tests; jobs/broker passed 48; browser/API integration passed 117. These selections overlap the final retained suite.
- Actual browser checks passed the queued → stopped → reload/resume → completed flow on Insight Explorer and Dashboard, with desktop/mobile layouts, no horizontal overflow and no browser errors/warnings. This used a fictional queued-result fixture, separate from actual backend timing. A blocked native Safari automation attempt was replaced with the in-app browser; the temporary viewport, tab and server were cleaned up.
- All **1,941 retained tests passed** on their first invocation in exhaustive, disjoint partitions: 1,926 non-E2E in 563.92s, 14 trace replays in 126.70s and one real-panel/broker/fake-Hermes journey in 89.97s. The actual ten-scenario generator passed in 131.83s; independent review confirms every non-seal scenario field stayed unchanged. Runtime/test source hashes stayed frozen. Capped-server measurement and authorized release activation remain separate until completed.

### Capped measurement and optional native follow-up

- The first capped fictional Explorer job completed in about 465.14s with unchanged records and source. Its acceptance harness then compared raw readiness floats with the CLI's canonical 12-significant-digit representation. A read-only diagnostic showed zero differences when using the existing transport contract. That test-comparison failure and its receipts are retained; no product calculation was changed to force the comparison to pass.
- The run also exposed avoidable polling work. Pending status now reads only validated private queue metadata, returning no result or freshness claim. Completed responses and execution still check the coherent data identity, integrity and current readiness. Browser polling is five seconds; a 158-test focused impact selection and independent review passed. This followed the 1,941-test checkpoint above.
- A small optional original C kernel preserves MT19937 state, rejection draws, swaps and ordered products; Python retains `math.fsum` and the final statistic. Bounded native chunks and a trusted, explicit build/load path retain compiler-free Python fallback. Complete current-source local analysis improved from 131.80s to 80.96s with exact full unrounded/canonical output equality under a common engine identity, all 3,755 permutation candidates and 7,510,000 draws intact. Peak memory remained about 2.3 GiB.
- The actual native/stat integration passed 76 focused checks, followed by 98 native-enabled association/adversarial/provenance/evidence checks. Independent review covered maximum buffers, state rollover, malformed receipts, FIFO rejection and restart from the original Python seed after a native failure. The loader and C source are included in numerical/generic/cache identities. The completed full-source verification, regenerated trace seals and linked capped-server native acceptance are recorded below; earlier counts describe their own checkpoints.
- Final native-aware source verification passed all **1,973 tests**, with no failures/skips: 1,958 non-E2E in 622.17s, 14 trace replays in 135.46s and one real-panel/broker/fake-Hermes journey in 99.48s. Default Python and the separately selected actual native tests were both covered. The real ten-scenario generator passed in 135.83s; independent review preserved every non-seal field. Runtime/test bytes stayed frozen, and the external library hash remained unchanged. This is source verification; installed-server acceptance is recorded separately.

### Final linked capped-server acceptance

- Recovery point: `bf37d6ff0ef2174bde19fbe5477f2c53387560e7`. No runtime or test source changed after the 1,973-test verification. This batch updates only `VERIFICATION.md`, this checkpoint and `RELEASE_MANIFEST.tsv`.
- The isolated fictional native-enabled Explorer job acknowledged in 0.95s and completed cold in 282.22s. Completed-job retrieval took 4.92s and exact Explorer CLI reuse 4.55s. Separate legacy evidence acknowledged in 0.51s, completed cold in 266.25s and repeated through its exact cache in 0.86s; the evidence result was verified.
- Source and fictional data stayed unchanged. Peak measured memory was 2,717,057,024 bytes (2.72 GB), with no OOM event and all owned test descendants gone after cleanup. The existing one-CPU, 8,000,000,000-byte memory and no-swap limits were unchanged.
- Acceptance combines the validated cold Explorer result with a continuation for the remaining checks. It is not one uninterrupted run; the original failed receipt is retained outside source. The original native attempt stopped when the test harness blocked SQLite temporary storage; the continuation configured private temporary directories before startup without changing product source. This is separate from the earlier readiness-comparison failure. These are isolated-copy measurements. Canonical service activation and final release identities remain separate operational records.
- Documentation-only verification uses manifest integrity, whitespace checks, independent claim review and current-source/exact-candidate privacy scans. The retained runtime suite is not repeated for these documentation changes.

## Standalone local MCP connection

- Recovery point: `9ad10c5fcd032fd867bdc0e393e8aa7d76006d11`. The owner approved an optional model-agnostic local MCP connection for a user-selected database. Built-in API-key chat is outside this batch. Existing Hermes, Telegram and panel workflows remain supported.
- Added `local_surface.py` with immutable per-instance database/timezone settings and an opaque dataset identity. Shared catalog, query, analysis, evidence and exact-cache functions now accept explicit bindings. The default fictional Hermes entry point retains its contract and gates. Read-only URI construction handles special filename characters; an explicit local timezone binds calendar context and training time conversion without changing default callers.
- Added the standalone stdio server, optional `requirements-mcp.txt` and actual SDK tests. Long analysis/evidence operations return task handles promptly, with one heavy worker, duplicate suppression, bounded status waits, independent execution deadlines, result expiry and disconnect cleanup. Canonical health records stay read-only; derived cache files remain separate. No provider credentials, model service, network listener or health write tool was added.
- Core verification passed 51 focused checks and a final three-test rerun after source-identity integration. Independent old/new comparison verified 14 complete legacy envelopes, 14 local semantic projections and 12 full unrounded analytical results, with unchanged fictional records. Five actual MCP SDK subprocess tests passed in 4.41s. Independent sub-agent/grandchild reviews found no remaining blockers; these selections overlap the final retained suite.
- Optional MCP SDK 1.30.0 dependency review covered 50 resolved packages with zero known vulnerabilities and recorded license metadata. Ordinary runtime/development dependency pins are unchanged. Actual ten-scenario regeneration passed in 127.54s with all fixture, seal and manifest bytes unchanged.
- README, architecture, deployment and product-authority guidance now distinguish standalone MCP clients from existing Hermes integrations. The local guide documents setup, platform support, task behaviour and read-only scope. Independent documentation review found no remaining mismatch with the implemented interface.
- All **1,981 retained tests passed**, with no failures/skips: 1,961 base non-E2E in 584.69s, 14 trace replays in 130.02s, one real-panel/broker/fake-Hermes journey in 94.10s and five actual SDK protocol tests in 4.36s. The five protocol tests were rerun after a release scan identified a location-specific timezone in a new example and inherited-environment test value; both now use a neutral example. The scanner and runtime code were unchanged. Full SDK-enabled collection proves disjoint coverage of every retained test. Runtime/test source remained unchanged during its corresponding verification window.
- The standalone connection is distributed as an optional local entry point. This batch requires no change to the running VPS, its resource allocation or its existing Hermes/Telegram configuration. Exact clean-export and publication identities are recorded separately from local development history.

## Initial public MIT release

- Recovery point: `9b8bcf631be1043aa73ea1b004533f209fd469e6`. The owner authorized a separate public repository named Open Health Atlas, MIT licensing for original project code and creator credit for Kajeesan Jeevendra. The new URL is `https://github.com/kajeesan/Open-Health-Atlas`; the prior repository and its development history remain private.
- Replaced the original-code license with standard MIT terms and made creator credit prominent in the README, with matching copyright, NOTICE and citation metadata. Third-party licenses and all eight vendor files remain unchanged. The contribution policy now uses MIT without extra attribution conditions.
- Updated active clone instructions for the new `main` branch and replaced stale publication, security and independent-review claims. The public source starts from a fresh root commit; no private development ancestry is imported. Historical source receipts retain their original provenance references.
- Independent license and release-review agents verified the metadata scope, source privacy and exact continuity with the 1,981-test checkpoint. Runtime, test, fixture and dependency bytes are unchanged, so this documentation/license batch does not repeat the application suite. Final manifest, clean-export, new-history and GitHub publication checks are recorded separately at release completion.
- Source publication does not change the existing VPS, its resource limits, its live records, model configuration or Telegram integration. The desktop launcher remains an unimplemented proposal.

## Repository onboarding and community

- Recovery point: public root `d07f3942098ecc4e421ce9cd16d127cf8f92c66b`. The owner authorized clearer repository presentation, fictional visuals, three setup routes, community files, proportionate CI and a bounded starter backlog. Private development history, live installations, desktop packaging and built-in API-key chat remain outside this batch.
- The README now leads with practical uses, creator credit, a current fictional-data UI screenshot and separate demo/MCP/development routes. The hosted demo was verified as a read-only static snapshot and is labelled accordingly; the screenshot comes from the current local application after its manifest-verified fictional Green-day analysis completed.
- Added a Contributor Covenant code of conduct with accurate GitHub abuse-reporting guidance, concise issue forms and a PR template. Two starter accessibility tasks identify existing fields/state gaps, affected files and concrete verification. They require no external outreach or assignees.
- Added GitHub Actions for release inventory/privacy and relevant retained tests. Repository-only changes use the scanner regression checks; application, workflow and consumed seed changes run the existing application, native, JavaScript, E2E and actual MCP checks. No new application test framework or platform matrix was introduced.
- The release scanner permits only the exact reviewed screenshot path/hash and now checks every historical blob/path pair, so an identical image at an unapproved path is still rejected. Two distinct scanner regressions were added. Seven scanner tests and five existing MCP tests passed locally; workflow parsing and routing cases were independently reviewed.
- Fresh isolated Python 3.14.6 setup passed dependency consistency and 13 existing initialization, demo-flow and actual MCP tests in 96.88s. The documented initialization, fixed-anchor database generation and scripted demo also passed; the latter imported 45 fictional days, wrote 45 validated ratings, performed analysis and checked authenticated API reads. No model or external service was invoked.
- Application/toolkit code, retained features, MIT licensing, creator credit and vendor files are unchanged. Final GitHub run results, public community-file recognition and publication checks are recorded after execution; no passing CI badge is claimed in advance.
- The first Linux workflow passed inventory/privacy checks and 1,941 application tests, with 22 failures in 1,405.84s: installer test paths outside approved temporary roots, and date expectations affected by differing timezones or crossing midnight after collection. Corrected the workflow's temporary directory and example timezone; updated three existing test modules to use current configured civil dates and season-independent workout timestamps. Product code, safety guards and assertions are unchanged. The 23 installer checks passed in 13.70s; 33 date checks passed in 13.89s under differing host/application timezones. Independent review found no weakened behavior; a fresh full GitHub run remains pending.

### Verified public onboarding checkpoint

- [Hosted run 35156999775](https://github.com/kajeesan/Open-Health-Atlas/actions/runs/35156999775) passed at `6be7d75656bf0a7a34ba8c1b7d2ea1936189dfa2`: 1,963 application tests in 1,392.76s, 15 fictional E2E tests in 608.92s and five actual MCP tests in 9.50s. All 1,983 retained tests passed with no test skips/failures. Dependency consistency, the 406-entry manifest, current/history privacy and cleanup also passed.
- [PR #3](https://github.com/kajeesan/Open-Health-Atlas/pull/3) is merged on public `main` at the exact tested commit. GitHub recognizes the community files and shows both issue forms; its community-file checklist is 100%, which is not a product-quality score. The two published starter accessibility issues remain unassigned, and private vulnerability reporting is enabled.
- The final documentation follow-up records the actual results and adds GitHub's live workflow badge. It changes only README, verification/checkpoint documentation and the manifest; application and test bytes remain frozen at the passing checkpoint. No external-user feedback, outreach, live-installation change, desktop packaging or built-in API-key chat is claimed.
- Remaining product limitations stay in `UNIMPLEMENTED.md`; no new owner decision blocks the repository onboarding work. Future repository-only changes use the lightweight checks, while application/workflow changes retain the full suite.

## README visual overview, 17 September 2026

- Recovery point: `76363caac18e5c372c915d1cc4eb8a980f4510a4`. The owner requested a more visual lead image matching the public demo's radar charts and body diagrams. Replaced the README's lead image with a fresh 2880 × 1568 browser capture of that fictional, read-only snapshot; browser and operating-system controls are outside the frame.
- The short caption identifies the demo snapshot. `docs/SCREENSHOTS.md` records source, capture dimensions, unchanged pixels, metadata inspection and exact hash. The earlier current-application calculation screenshot remains available there. Both reviewed path/hash pairs remain in the privacy scanner for current and historical verification.
- Visual inspection confirmed complete cards, charts, front/back diagrams and navigation. All seven existing scanner regressions passed in 0.15s. The batch changes only documentation, the reviewed image allowlist and a new asset; runtime, calculations, tests, dependencies and the live installation are unchanged. Publication uses the existing manifest/privacy gates and lightweight repository workflow.

## Body-page layout, 17 September 2026

- Recovery point: `1dc2fcb889e5ecdaacdd05afbc109c041929d948`. The owner approved publishing the proposed layout to GitHub. The Body map now leads in the DOM and occupies the larger desktop column; Muscle Balance and Athletic Profile use compact cards stacked beside it. Tablet layouts place the charts below the map, and narrow screens stack the figures with a two-column lens selector.
- Changes are confined to `app/templates/training.html` and Body-page-scoped rules in `app/static/css/panel.css`, plus this checkpoint and the release manifest. Every existing ID and control/link attribute set is retained. Pain, Mobility and all later sections remain unchanged; no calculations, JavaScript handlers, data schemas, dependencies or live installations changed.
- All 77 existing training API, application-shell and authentication tests passed in 2.30s. Independent review found no blocking issues, and the layout detector returned no findings. Actual fictional-app browser checks confirmed aligned front/back figures, visible lens controls and no horizontal overflow at 1180px and 390px; Pain and Mobility panels loaded through their existing APIs. Temporary browser and demo processes were stopped after verification. Full hosted workflow results are reported separately by GitHub.


## Desktop packaging checkpoint — 17 September 2026

- Owner authorized a self-contained macOS package from the clean public repository.
  Public main was `1dc2fcb889e5ecdaacdd05afbc109c041929d948`; the approved Body layout
  at `8ab53324f5b5858314b4266326092f0ce5f0147d` is preserved in branch ancestry.
- Added a small Swift AppKit/WebKit host, pinned relocatable Python runtime build,
  loopback session boundary, process-owned validated broker, guided workspaces,
  checked snapshot migrations, stable bundled MCP entry point and release CI.
  Existing numerical engine and web passkey deployment are retained.
- Packaged fictional acceptance passed local auth/CSRF/origin checks, validated
  write, exact restart preservation, isolated demo/import, simulated upgrade
  backups, abrupt launcher restart and damaged-settings recovery. The same
  journey passed with external networking denied. Real bundled MCP completed
  positive analysis/evidence/status and disconnect/upgrade-reconnect checks.
- Native macOS window completed fictional setup, rendered the approved Body map
  and radar, navigated to Help, copied MCP settings and saved via NSSavePanel.
  Thirty focused desktop regressions pass. Retained test partitions and precise
  qualifications are recorded in `docs/DESKTOP_ACCEPTANCE.md`.
- Local ad hoc signing verifies integrity only. No Developer ID identity is
  available; notarization, Gatekeeper download acceptance and a clean customer
  Mac remain open. No public desktop release is claimed. Next: clean-commit
  build, final archive acceptance and reviewed PR; then authorized signing.

### Desktop preference persistence follow-up

The nonpersistent native browser session intentionally discards cookies, but
its random loopback origin also discarded the existing theme and remembered
analysis handles. The desktop adapter now restores and saves a closed set of
validated display/opaque-handle preferences in each workspace's existing
panel-state settings table. Authentication remains ephemeral; the normal web
application is unchanged. The preferences are included in verified panel
backup generations. Focused server/preferences/shell checks: 31 passed. The
packaged verifier now asserts theme persistence across restart and upgrade.

### Final local archive acceptance

The clean preference-fixed source at `acfcca18739fe54486f19bd9746c1242dfc0abb0`
produced verified macOS arm64 DMG/ZIP artifacts. A copy installed from the DMG
passed twelve offline lifecycle/data checks and the real MCP SDK journey.
Deleting/reinstalling a disposable app copy preserved all data files and the
saved theme. Post-acceptance signature and all bundle hashes remained valid;
3,058 files scanned with zero privacy findings and no generated bytecode.
Exact hashes, hosted build link and remaining release gates are in
`docs/DESKTOP_ACCEPTANCE.md`. No Apple signing facility exists locally or in
repository secrets. The Mac locked before the final native theme recheck;
owner action is needed for that check and for distribution signing.

### Local follow-up pending account-privacy resolution

The retained report workspace routes use pain and mobility lens identifiers.
The desktop preference allowlist now follows those actual identifiers, plus
the Insight workspace, instead of unused pillar guesses. A regression checks
all three remembered selections. Five focused preference/server tests pass.
This follow-up stays local because GitHub's automatic PR test-merge author
metadata does not currently meet the publication email policy. Pushed source
commits retain the sanitized identity; no account-wide privacy setting has
been changed without owner approval. Existing local archive acceptance above
identifies its exact source snapshot and is not a claim that this newer
follow-up was published.

### Native interrupted-upgrade cancellation

With the Mac available again, the final local app retained its Ember theme
after a genuine native quit/reopen. A disposable locked-panel upgrade fixture
also showed the intended slow-start message. Killing the native parent while
the backup was blocked exposed a remaining launcher lifetime gap: the runtime
waited for SQLite instead of promptly observing parent EOF. Original health
and panel database bytes were unchanged.

Backups now receive the launcher's cancellation signal, with a short SQLite
busy wait so the backup progress callback observes cancellation promptly. A
cancelled startup exits before creating a replacement web session. One real
exclusive-lock regression reproduced the fault and verifies cancellation
while the source stays locked, unchanged source bytes and preserved selection.
All 32 focused desktop tests pass. Packaged native revalidation follows.

The owner requested GitHub email privacy. The account setting was off and is
now enabled, with a server confirmation. No email-address values were copied
into reports. New generated metadata must be verified after the next reviewed
branch update; earlier generated objects are not rewritten by this setting.
The owner confirmed they do not have a Developer ID Application identity;
normal signed/notarized distribution remains pending.

### Native cancellation revalidation

The cancellation-fixed `5d18014` bundle passed the actual native crash test while
a copied panel DB remained locked: runtime shutdown in 0.07 seconds; both original
databases byte-identical; old generation retained. Reopening completed recovery
into a new generation with exact health-row preservation and the Ember theme
still visible. The bundled offline verifier now independently exercises parent
pipe loss during an actual blocked upgrade and confirms no partial generation
is selected. All 13 packaged offline checks and 32 focused desktop tests pass.

### Reviewed build inputs and masked GitHub metadata

The account's email-privacy setting is enabled under owner instruction, and
PR7's newly generated test merge now uses GitHub-provided no-reply identities.
The scanner accepts only the two reviewed exact identities in whole Git
author/committer email fields. The same values remain rejected in source,
reference names, name fields and message bodies; private and near-match
addresses remain rejected. Local commits keep the original example.invalid
identity. Earlier platform-generated objects are not rewritten by the setting.

The builder now copies only manifest-listed entries within its existing
explicit source scopes, verifies regular non-symlink source bytes/size/hash
before creating output, and writes the checked bytes rather than rereading
paths. Ignored JSONL and unreviewed modules are excluded. The selected 154
product files match the verified cancellation-fixed app byte-for-byte.
Combined scanner/build-input regression tests: 22 passed. Independent review
found no blocking issue. No app runtime or numerical engine changed in this
batch; the evaluated app remains source `5d18014`.

## AGPL license transition, 17 September 2026

- Recovery point: `1dc2fcb889e5ecdaacdd05afbc109c041929d948` from public `main`. The owner requested AGPL to permit commercial use while keeping covered distributed and network-served improvements available under its source-sharing conditions. Selected standard AGPL version 3 only (`AGPL-3.0-only`), with no custom upstream-contribution requirement.
- Replaced the original-code license with the unmodified GNU license text and aligned README, NOTICE, citation, contribution and working instructions. Added `LICENSING.md` and preserved the preceding MIT notice in `docs/LICENSE-MIT.md`; earlier permissions and historical release records remain intact.
- Third-party license terms, vendor assets, application/toolkit code, application tests, dependencies and live services are unchanged. The release scanner now recognizes the official FSF and GNU license hosts, covered by its existing safe-provenance test. Public history records only the owner's authorship; that is provenance evidence, not a legal ownership audit. No copyright assignment is introduced for future contributions.
- Verification: exact comparison with the official GNU license text and the preceding MIT notice passed; runtime/vendor/dependency byte comparison, local documentation links and diff whitespace passed. All seven release-scanner tests passed. The current-tree privacy scan passed, and the public-main ancestry passed in a single-branch copy (439 historical blobs). The all-remote-refs scan separately reported an existing unreviewed Apple-documentation host in the unrelated desktop branch; no unrelated history or scanner allowance was changed for it. The release manifest was regenerated. No new full runtime-suite or independent-review claim is made. The new license is prepared locally and has not yet been published.

### Publication check follow-up

- The owner explicitly authorized publication. PR #8 encountered the same all-remote-refs finding in the existing desktop branch. Reviewed its two public Apple developer documentation URLs for packaging and notarization; registered `developer.apple.com` as an allowed official documentation host and covered it in the existing safe-provenance test. Desktop branch contents, history and branch protections remain unchanged.
- Verification passed: seven scanner tests, the 409-entry release inventory, diff whitespace, and complete available public history (531 blobs, no findings). This is a publication-check correction, not a desktop feature change or a waiver of privacy verification.

## Desktop release preparation, 19 September 2026

- Recovery point: `a5c14f4dee13b93e06b14c81512e6f977149c3b4`. Merged current public main `ab3ce1c4b4194ce3ebe9e50189402f785cf49f0e` without rewriting history; the approved Body layout remains in ancestry. Preserved the current AGPL-3.0-only policy, historical MIT permissions and creator/third-party attribution. Preparing version 0.2.0; the existing v0.1.0 source release is unchanged.
- Native inspection reproduced missing desktop help/workspace links in the narrow-window More menu and a generic startup failure after sign-out. Restored those links, bounded the menu with scrolling, and added a signed-out recovery page. Reopening uses a new native runtime capability from an exact local main-frame route; revoked sessions and old launch tokens remain invalid. Desktop CSRF lifetime matches the existing fixed session lifetime without refreshing authentication; the source web deployment is unchanged.
- Updated AI connection instructions to require opening the updated workspace before reconnecting; changing the dashboard selection does not retarget an existing client's configured workspace. Updated native About/licensing surfaces, corresponding-source access and release documentation. No health calculations, schema, provider runtime or live integration changed.
- Supporting checks: nine source scanner regressions and 52 focused desktop/session/preferences/web-auth/shell checks passed; native Swift typecheck passed. The changed template detector reported no findings. Packaged verification and signing outcomes are tracked in the desktop acceptance record; earlier candidate evidence does not certify this revision.

- Native candidate acceptance exposed silent Finder reveal failures for the new source/notices actions. Replaced those with a standard source-save dialog that copies the exact embedded archive unchanged and an in-app read-only license viewer. The first candidate otherwise passed fourteen offline packaged checks and nine real MCP checks; final receipts must bind the corrected native build.

## First-run action and theme repair, 20 September 2026

- Recovery point: `61c8a199ef6f367d7925bf77d92f12bad48667c0`, the signed/notarized 0.2.0 candidate. Owner feedback identified a below-fold start action and an unthemed first-run page. The desktop gate was redirecting public static assets to setup before a workspace existed. It now lets the existing public static endpoint load; session and API gates are unchanged. The retained server test checks the actual stylesheet response is CSS rather than setup HTML.
- The welcome page uses the existing palette and display/UI type, a distinct selected workspace option, and an always-visible Continue action with an explicit form association. Timezone remains editable in a compact disclosure; required validation reveals it before focusing the field. Existing workspaces, compatible-copy import, progress/error feedback and double-submit prevention remain. Returning to setup uses the workspace's validated saved theme.
- Browser checks used an isolated fictional source harness: action visibility at 800x570, wide and narrow layouts, dark-theme inheritance, selection feedback, missing-import-file feedback, empty-timezone focus recovery and a genuine fictional-workspace creation request. An automation empty-fill no-op was corrected with actual keystrokes; it was not a product failure. 33 retained desktop/preferences/shell checks passed. Native packaging and signing receipts for version 0.2.1 remain separately required; the installed 0.2.0 app is not edited in place.

## Official Personal Contour Mark, 20 September 2026

- Recovery point: `14efc727b162022aa0684c257b3bf22167fa737e`. The owner selected the Personal Contour Mark as the official Open Health Atlas logo. The canonical SVG is an exact copy of that selected vector, with original attribution and AGPL-3.0-only metadata retained. Product naming remains Open Health Atlas.
- Replaced the former native icon renderer with the selected six cubic paths, exact stroke widths/round caps, rounded teal field and coral waypoint. Explicit 1024-pixel sRGB bitmap rendering maps the SVG's 512-unit coordinates without a device-dependent colour space or display-scale assumption. No in-place change to a signed application is made.
- Independent geometry/colour review found exact parity between the SVG and renderer; release 0.2.2 requires a fresh icon family, clean-source build, Developer ID signatures, app/DMG notarization and final artifact acceptance. Earlier candidate receipts remain historical.

## Drag-to-Applications installer, 20 September 2026

- Recovery point: `e2103be577d08817e83bfb234f5e49ee0824e313` (published 0.2.2 preview). Owner requested a compact installer with one app icon, a directional arrow and the Applications folder, without a visible instruction file. Version 0.2.3 changes installer presentation only; app operations and data handling remain unchanged.
- Added a native paper-theme background renderer with standard and Retina images, fixed Finder window/icon settings, and hash-pinned build-only disk-image tooling. These inputs are required in corresponding source; the tooling is isolated outside the shipped runtime. License/source/notices remain in the app menus.
- Retained packaging boundary tests passed (26). The mounted layout was inspected in Finder, exposing and fixing double scaling in the Retina background. Final Finder visual confirmation and signed/notarized artifact acceptance are recorded with the release, separately from source checks. Preview qualification limits remain unchanged.

- Final payload review caught hidden-extension Finder metadata added to the already signed app by the packaging tool. Removed that setting and added a mounted-image strict signature/visible-items/shortcut check before DMG notarization. No signature verification bypass is used.


## Nontechnical setup guide, 20 September 2026

- Baseline: published desktop preview source `5db136edc0b19dbc0e2a319927fd9bf4cd612ff1`. Added `docs/GETTING_STARTED.md` for installation, fictional first use, the primary local MCP connection and transition to personal workspaces. Added `docs/OPTIONAL.md` with separate Hermes, Telegram, Google Health, Hevy and VPS sections. Each setup has a self-contained copy-paste agent prompt, completion criteria and a stop/recovery path.
- Matched the app's actual Help, Download settings, workspace and tool-discovery behavior. Kept read-only MCP, model-provider transfer, separate Mac/server databases, Linux-only collector templates and protected fictional Hermes adapters explicit. Account sign-in, private credential entry and personal-data transfer decisions remain with the user.
- Linked the guide from the README, desktop installation and desktop MCP pages. Corrected stale pending-download statements in the two installation entry points using the published 0.2.3 prerelease assets and notes. Historical candidate evidence remains unchanged; no stable qualification or new installed-client acceptance claim is made.
- Verification: checked the exported configuration builder, help/setup templates, tool guide, collector scripts/services and upstream provider documentation. Verified 56 local Markdown links/anchors and all nine complete agent prompts; diff whitespace, the 452-file release inventory and current-source privacy scan passed. Registered four reviewed public provider-documentation/account-setup hosts in the existing release scanner and extended its existing public-provenance regression case; all other privacy checks remain active. All nine retained release-scanner tests passed. The required narrow wording review completed; its final punctuation correction was applied. No runtime implementation, desktop bundle, account, live integration, website or published release was changed.


## Hostinger setup guide follow-up, 20 September 2026

- The owner selected Hostinger for the optional VPS guide. Updated the section, navigation and standalone agent prompt for Hostinger KVM VPS, hPanel, a new Ubuntu Plain OS installation, SSH access, both firewall layers, backup restoration and renewal/cancellation. Existing servers must be inspected without reinstalling or overwriting them. Personal-data transfer and actual purchases remain separate user decisions.
- Checked Hostinger's official dashboard, SSH and OS documentation. Added its public documentation host to the existing source scanner and its existing public-provenance regression case. Documentation links, source privacy, release inventory and retained scanner tests are checked for this edit; no account, server, running service or published artifact is changed.

## Desktop help simplification, 20 September 2026

- Reorganized the existing desktop help page into steps: choose records, add the AI connection, and ask a first question. Secondary help, raw connection settings, diagnostics and optional integrations remain available in collapsed sections. Added copy controls for the first question, setup-agent instructions and each optional integration. The optional prompts match the reviewed guide, including Hostinger.
- Kept the existing configuration download, workspace selection, read-only connection boundary and diagnostic endpoint. The help route now uses the workspace's validated saved theme. Styles are scoped to a separate help stylesheet; first-run setup and the dashboard layout are unchanged. Clipboard fallback preserves focus and removes its temporary text field even on failure.
- Verification: 42 existing desktop session/preferences/MCP/shell tests passed; six isolated JavaScript scenarios covered successful copying, no workspace, workspace-fetch failure, configuration failure, clipboard fallback and copy failure. The design detector and JavaScript syntax check passed. Browser inspection at 1100×900 and 390×844 covered light/dark themes, no horizontal overflow, collapsed defaults, settings/question/Hostinger copying and screen-reader step labels. The browser harness used fictional records and a fixture executable for exported configuration; it does not claim installed-client MCP or native app acceptance. The full-page screenshot tool produced a duplicated stitch; DOM inspection confirmed one help section and one footer, and ordinary viewport captures were used for visual review.
- Changes remain in source for a future desktop build. No signed bundle, installed application, account, service or published release was replaced. Refresh the release inventory before packaging these sources.


## Help guide release preparation, 20 September 2026

- The owner authorized publishing the completed help page and setup guides. Prepare 0.2.4 on the existing Mac preview channel, preserving the current source and data boundaries. The earlier audit-checkout photo-theme commit is separate and is not imported into this desktop release.
- Existing functional evidence is 42 desktop/session/preferences/MCP/shell tests, six isolated clipboard/error-state checks and fictional browser inspection. Recheck source/history privacy, release inventory and packaging boundaries before creating an exact signed and notarized candidate. Candidate-specific installed-app, offline, MCP and Apple trust results belong in external release receipts; earlier results do not qualify new bytes.
- Publish only the verified installer and matching source/checksums, update the existing download link, and preserve the previous release. The preview label and open clean-Mac/minimum-OS qualification limits remain. No production health database, provider credential, live integration or user's installed app is changed by this release preparation.


## Unified setup and restored themes, 20 September 2026

- The owner authorized a combined public desktop update: a single copied AI setup instruction, prominent Connect AI and Full experience setup tabs, Hevy/Google Health/Cronometer priority, advanced Hostinger/Hermes/Telegram options, clear Back navigation, and the earlier reviewed photo themes and theme-aware logo. The first tab copies the exact displayed instruction plus the selected workspace's configuration. It copies no health records or credentials and asks for explicit personal-data approval before an agent reads records. The retained configuration API remains compatible.
- Added server-rendered setup tabs and allowlisted return paths so Back can return to the caller's app page without permitting external destinations. First-run help returns to setup; a workspace-switching page has Back only when a workspace is already open. Cronometer is accurately presented as a daily-summary CSV import with unique daily rows and validated CLI writes, not a live API.
- Ported the targeted UI patch from the older audit checkout without importing its private Git history or unrelated files. Preserved current desktop, body-layout and security changes. The eight approved picker themes are Paper, Ember, Forest Verdant, Black Beach, Blue Glacier, Canyon Creek, El Capitan and Cyberpunk. The saved-preference boundary now supports the new keys; retired stored themes fall back to Paper. Included only the twelve referenced desktop/mobile images, with exact-byte release-scanner registration, metadata inspection and original El Capitan source attribution. The inline logo geometry matches the approved native icon.
- Supporting verification: 80 targeted desktop/auth/shell/MCP/colour/Cronometer tests passed before the navigation/legacy-preference additions; all ten desktop-server/preference tests passed afterward. Nine isolated JavaScript scenarios cover exact copied/viewed configuration, personal-data approval, empty/malformed/failure states, clipboard fallback and full-setup copying. Browser checks covered both tabs, previous-page Back, wide/narrow layouts, all eight theme selections, saved-theme reload and theme-aware branding. The image contact sheet and metadata were reviewed. A temporary preview initially lacked its broker; verification continued with an isolated real desktop broker and fictional records. No real health data or live integrations were used.
- The combined final source checks passed 111 tests before the Back-link cases were parameterized; the final twelve desktop-server/preference cases passed. Nine JavaScript scenarios and 57 guide links passed. All six in-app integration prompts match the guide. Prepare version 0.2.5 using the signed/notarized preview release process. Exact artifact, CI and native results will be recorded with that release. Previous release remains available; no user data migration or personal installed-app replacement is authorized by source publication.

## Desktop merge compatibility repair, 21 September 2026

- Recovery point: `16fe07165282489a3057c7fb5d43dcb45724fe43`, the published 0.2.5 source and PR #7 head. The owner authorized merging the desktop changes into public main. The older private audit repository remains separate.
- Independent review reproduced an authenticated malformed setup request stopping the helper without scheduling recovery. `desktop/server.py` now validates workspace fields before stopping the helper and schedules recovery after unexpected operation failures. Existing session, CSRF and data boundaries remain intact.
- Added nine regression cases in `tests/test_desktop_server.py`. Before the fix, eight failed and one existing recovery behavior passed. After the fix, 34 desktop server/workspace/preference tests and 91 adjacent authentication, shell, MCP, packaging and privacy checks passed. Two scanner checks initially hit the host's Xcode license prompt. Both passed using the installed Command Line Tools Git. All tests used fictional records and temporary databases.
- Independent review of the final patch found no actionable issue. Required GitHub checks must pass on the updated PR head before merging. This source repair does not rebuild or replace the published 0.2.5 installer.

## Toolkit modularization conventions, 21 September 2026

- Recovery point: public main `ef053a6de7ca61b1fc33d88b69da32dca04a2a24`.
  Work uses `codex/health-toolkit-modularization`; the first milestone requires
  owner review before merge or module extraction.
- Adapted the reviewed Software Writer extensions to the current Flask,
  SQLite, plain JavaScript and desktop source. Short agent instructions point
  to contributor guidance, with explicit override delivery. Preserved the
  development guide's setup and moved working safeguards into its conventions.
- Updated testing guidance to the actual application, fictional end-to-end
  and real MCP partitions, including Node/native prerequisites and macOS SDK
  handling. Retired interpretation behavior and old audit test counts were
  not imported. Temporary reports and indexes remain outside tracked source.
- Read-only configuration review checked extension mechanisms, shared settings,
  pointers, current helpers and links. Its native-library suffix, PR-link and
  safeguard-preservation findings were corrected. The narrow prose review
  requested no wording changes. Numerical corrections and their verification
  are recorded separately below when complete.

## Corrected toolkit baseline, 22 September 2026

- Corrected baseline: `c427296a286237ff72ffb733bc9bbdb4e25fcc54`.
  Runtime corrections are in `562bd52`; `c427296` adds only reviewed replay
  identity metadata and its release inventory. Working conventions are in
  `9c45501`, based on public main `ef053a6`.
- Excluded future records from current recovery baselines and nutrition weight
  and activity inputs. Preserved inclusive association/interaction thresholds
  with integer count arithmetic. Corrected extreme valid Wilson confidence
  handling and percentile interpolation between finite opposite extremes.
  No module extraction, schema, dependency or CLI interface change was made.
- Added 17 focused regressions: unchanged production failed 13 while four
  controls passed; corrected production passed all 17. Another 197 adjacent
  tests passed. These focused runs are not added to complete-suite counts.

Complete local verification used Python 3.11.14, the declared dependencies,
Node, the native test library, fictional data and the CI civil timezone.
Commands and prerequisites are in [Testing](TESTING.md#auditable-partitions).

| Partition | Revision | Passed | Failed/skipped | Pytest duration |
|---|---|---:|---|---:|
| Root application, including toolkit | `562bd52` | 2,047 | 0/0 | 504.28 s |
| Fictional end-to-end journeys | `c427296` | 15 | 0/0 | 199.81 s |
| Actual optional MCP client/server | `562bd52` | 5 | 0/0 | 3.32 s |

The disjoint total is 2,067 tests. Runtime source is identical between these
verification revisions. Earlier interrupted socket checks, runner environment
failures and stale replay-pin failures remain in local evidence; they are not
represented as passing runs. Fixing the runner required no product changes.

Independent numerical and configuration reviews found no blocking issue.
The ten original traces reproduced exactly on the public baseline. Updated
traces retain their inputs, expected statuses, assertions, notification
decisions and database row counts. Their pins were refreshed only after
comparison and independent review; no guard was removed or generalized.

Engine identity changes also reseed statistical intervals and affect existing
finding-ID tie-breaks. In the fictional reversal scenario, the prepared
strongest-candidate slot changes from lag one/+0.6 to same-day/-0.6 among
equally ranked findings. Both candidates and their numerical evidence remain
present. Holding the old engine identity in an external diagnostic process
reproduced all ten complete baseline traces byte for byte. This inherited
selection behavior is a disclosed review caveat; ledger and synthesis rules
were not changed.

Paired timings used a fixed-date fictional database, one first launch and five
repeated fresh processes per workload. The table records repeated medians;
filesystem caches were not forcibly cleared and native acceleration was off.

| Workload | Public baseline | Corrected baseline |
|---|---:|---:|
| CLI schema status | 142.68 ms | 146.27 ms |
| Fixed-date recovery/readiness | 132.75 ms | 132.93 ms |
| Nutrition targets | 124.81 ms | 125.75 ms |
| Statistics, 120 observations with 2,000 permutation/bootstrap draws each | 286.38 ms | 282.92 ms |

Outputs and database bytes were unchanged in these timing workloads. The
initial first-launch gap did not recur in the paired follow-up. These small
workloads establish a repeatable refactor baseline, not a general latency claim.

An isolated ad hoc macOS app built from `562bd52` passed 14 lifecycle checks,
nine bundled MCP checks, all 17 boundary regressions against packaged source,
signature verification and a 3,080-file bundle audit. A synced-folder metadata
failure was resolved by building into the system temporary directory. No
installer was published or installed, and no GUI, notarization or clean-machine
qualification is claimed. Its runtime bytes match the corrected baseline.

Exact commands, logs, trace comparisons and timing samples are retained in the
local milestone review package outside source. Release inventory and privacy
checks remain required at the final review head. The branch remains local;
hosted GitHub checks and owner review are required before merge. Milestone 2
has not begun.

## CLI and Hevy CSV extraction, 22 September 2026

Milestone 2 is complete locally. Production commit `2d460ca1b006d23be37ef295689037cda8e4da5a`
contains the extraction. Independent source review and the complete test partitions
used immutable Git tree `7e1c743556ea13fba5c64ebb52b94fa5bcac8c53` before that commit.
The commit resolves to that exact tree. No source changed during verification.

The CLI now has explicit registration, parsing, dispatch and error formatting.
Hevy CSV parsing and its transaction coordinator have separate owners. The stable
executable and unconverted handlers remain in the compatibility facade. See
[Toolkit command boundaries](ARCHITECTURE.md#toolkit-command-boundaries) for owners
and dependency contracts. Other importer formats remain for Milestone 3.

Six added import cases characterize metric distance conversion, missing values,
empty history and failed imports. All six also passed against unchanged baseline
production. An existing isolated allowlist test failed because its import path
depended on other collected tests. A scoped path setup fixed it without changing
assertions. Registry tests now refer to the CLI module that owns their contract.

| Partition | Passed | Failed/errors/skipped | Pytest duration |
|---|---:|---|---:|
| Application, including toolkit | 2,053 | 0/0/0 | 531.46 s |
| Fictional end-to-end journeys | 15 | 0/0/0 | 198.15 s |
| Actual MCP client/server | 5 | 0/0/0 | 3.48 s |

The disjoint total is 2,073 tests. Focused checks and external comparison probes
are not added to that count. Tests used the declared environment, native library,
Node, fictional records and the configured civil timezone.

Baseline and candidate retained all 269 CLI exit codes and standard outputs.
Five legacy error traces changed file/function locations while retaining their
terminal exception types and messages. The other standard-error outputs matched.
All 52 database snapshot pairs preserved schema, records, identifiers and sequences
apart from generated ingestion timestamps. Each changed timestamp was validated
against its originating command's execution interval. Four real broker requests
preserved allowed writes, bulk-import rejection and read-only query protection.

Paired timings used one first launch and five repeated fresh processes per workload.
Filesystem caches were not forcibly cleared, and native acceleration was disabled.
Hevy used a fresh identical database for each timed import.

| Workload | Baseline median | Extracted median |
|---|---:|---:|
| Hevy CSV, five sets | 132.87 ms | 135.54 ms |
| Schema status | 148.84 ms | 149.66 ms |
| Readiness | 139.81 ms | 137.26 ms |
| Nutrition targets | 130.18 ms | 130.24 ms |
| Statistics, 120 observations | 288.31 ms | 289.76 ms |

All timed outputs matched, and the shared read-only database bytes were unchanged.
No material unexplained slowdown appeared in these representative workloads.

The isolated ad hoc macOS app built from `2d460ca` passed 14 lifecycle checks,
nine bundled MCP checks, 269 packaged CLI cases and four actual broker requests.
All extracted modules loaded exclusively from the bundle and matched committed
source. The 3,086-file audit and strict signature verification passed after
execution, with bundle file hashes unchanged.

Packaged outputs and database effects matched the source baseline, with documented
traceback movement and one Python-version-specific argument-error rendering change.
That argument-error output matched the previous actual package exactly. The build
used cached dependencies offline. Sandbox restrictions and two external probe
configuration mistakes were resolved by unchanged-source retries. All attempts
remain in the evidence. This is local package verification, without native GUI,
minimum-OS, notarization or clean-customer-install qualification.

The explicit Hermes inventory includes the extracted files. Recursive cache
identity remains active, and the numerical provenance hash and replay pins are
unchanged. All existing end-to-end traces replayed exactly. The earlier
[resampling and tied-candidate caveat](#corrected-toolkit-baseline-22-september-2026)
remains relevant to future numerical engine changes.

Independent source and evidence review found no remaining blocker. Local release
inventory, vendor/license and source/history privacy checks passed. Exact commands,
revisions, comparisons and package limits remain in the local evidence package.
Nothing was pushed, merged, published or connected to a live service.

The next bounded family is remaining Hevy imports, Cronometer, Google Health,
recipes and catalogs, following the [approved milestone order](TOOLKIT_MODULARIZATION.md#milestone-3-remaining-command-families).

## Provider and catalog import extraction, 22 September 2026

The first Milestone 3 family is complete locally. Production commit
`ffb8d7aaa53107d400113e4ac96dc133c1081d17` contains the extraction. Source verification
ran before the commit against frozen tree `a265a92e9b13a585ef067fbfb937c89eef14c2ec`.
The commit contains that exact tree. All 496 source file hashes remained unchanged.

The remaining Hevy formats, Cronometer, Google Health, recipes and both catalogs
now have command and parser owners. Shared body/fitness bounds and routine snapshots
have one owner each. The facade retains temporary compatibility and unconverted
handlers. See [Toolkit command boundaries](ARCHITECTURE.md#toolkit-command-boundaries)
for the module map and transaction boundaries. No schema, numerical engine or broker
allowlist changed.

Five new characterization cases protect failed-import rollback and Hevy body updates.
All five also passed against unchanged baseline production. One existing internal
parser test now calls its owning module. The final focused run passed 182 tests.
Those reruns are not added to the complete count below.

| Partition | Passed | Failed/errors/skipped | Pytest duration |
|---|---:|---|---:|
| Application, including toolkit | 2,058 | 0/0/0 | 492.83 s |
| Fictional end-to-end journeys | 15 | 0/0/0 | 191.05 s |
| Actual MCP client/server | 5 | 0/0/0 | 3.35 s |

The disjoint total is 2,078 tests. No prior Milestone 2 case was lost. Tests used
the declared environment, native library, Node, fictional data and aligned civil
clocks. Source and Git state stayed frozen through verification.

All 345 source CLI invocations retained raw standard output and exit status.
A separate parser diagnostic confirmed all 122 public command contracts. Only
nine internal handler names changed. Sixteen error traces moved to extracted owners
while preserving terminal exception types and messages. Thirteen real broker
requests retained allowed dispatch, all bulk-import refusals and write restrictions.

All 222 complete database comparisons preserved schema, records, references and
sequence state after narrow validation of generated times and quarterly event IDs.
Seeded timestamps remained exact. Each excluded timestamp was traced to its original
write window. Every time-dependent quarterly event ID was independently rehashed
from its complete material. Explicit second boundaries verified timestamp refresh
versus preservation. These comparisons do not permit blanket timestamp or ID removal.

Paired timing used seven workloads and 84 fresh processes, alternating revision
order for each pair. Each workload had one first launch and five repetitions.
Filesystem caches were not forcibly cleared and database copying was outside timing.

| Workload | Baseline median | Extracted median |
|---|---:|---:|
| Hevy JSON | 121.18 ms | 120.65 ms |
| Cronometer | 120.96 ms | 121.36 ms |
| Recipes | 122.26 ms | 122.59 ms |
| Schema status | 139.61 ms | 139.98 ms |
| Readiness | 127.82 ms | 129.32 ms |
| Nutrition targets | 120.13 ms | 121.27 ms |
| Statistics | 280.50 ms | 280.59 ms |

All timed outputs matched and read-only fixture bytes stayed unchanged. Median
changes ranged from -0.44% to +1.17%, with no unexplained regression in this sample.

The ad hoc macOS app built from `ffb8d7a` passed 14 lifecycle checks and nine
actual MCP checks. All 23 selected modules loaded exclusively from the bundle
and matched committed source. The final 3,101-file audit found no changes or
findings, and the deep/strict signature passed after all package execution.

The bundled interpreter retained all 345 CLI outputs and the candidate parser
inventory, all 222 validated database comparisons and all 13 broker responses.
Error traces include isolated-launcher and standard-library frame differences.
One argument-error rendering difference comes from Python 3.11 versus 3.12.
The prior actual app produced identical output under the same bundled interpreter.
These differences remain recorded rather than discarded.

Build and verifier retries addressed a copied Swift cache's absolute paths,
restricted icon conversion, network-sandbox application and process inspection.
The unchanged tools passed after correcting their execution environment. No check
or runtime guard was weakened. This is local package verification, without native
GUI, minimum-OS, notarization or clean-customer-install qualification.

The explicit Hermes inventory includes all extracted runtime files. Recursive
cache identity remains active. Numerical provenance and all replay pins remain
unchanged. The inherited [resampling and tied-candidate caveat](#corrected-toolkit-baseline-22-september-2026)
still applies to future numerical identity changes. Ledger and synthesis rules
were not redesigned.

Independent source, verification and package reviews found no remaining blocker.
Release inventory, vendor/license and source/history privacy checks passed.
Exact commands, immutable source linkage, comparisons and diagnostic attempts remain
in the local evidence package outside source. Nothing was pushed, merged, published
or connected to a live service.

The next bounded family is daily logging, hydration, commitments, schedules, notes
and collectors, following the [approved order](TOOLKIT_MODULARIZATION.md#milestone-3-remaining-command-families).

## Daily workflow extraction, 22 September 2026

The second Milestone 3 family is complete locally. Production commit
`97c838bfb81abe97bae695bc8fe30a293f495778` contains the extraction and exactly matches
source-tested tree `6a7f0bb7d68e88af3d64b5fb1960797d7591c405`. All 510 source-file
hashes and staged Git state remained unchanged during complete verification.

Eighteen daily capture, commitment, schedule, note and collector commands now have
explicit owners and direct CLI dispatch. All 122 public parser definitions,
including handler-name metadata, remain exact. The facade retains 94 unconverted
handlers and temporary compatibility exports. See
[Toolkit command boundaries](ARCHITECTURE.md#toolkit-command-boundaries) for owners
and transaction contracts. `adherence` remains with the later daily-frame family.
Governed supplement capture is already extracted.

Five added provider characterization cases also passed unchanged baseline. Existing
collector cases now exercise explicit transport and real SQLite. Generic labs-write
refusal is behavioral, and note-whitelist parity uses its owning contract. A retained
recovery test exposed a removed helper before final verification. Its shared guard
was restored. Source-layout dependencies and mutable collector facade stubs were
removed without widening write permissions.

| Partition | Passed | Failed/errors/skipped | Pytest duration |
|---|---:|---|---:|
| Application, including toolkit | 2,063 | 0/0/0 | 501.91 s |
| Fictional end-to-end journeys | 15 | 0/0/0 | 190.53 s |
| Actual MCP client/server | 5 | 0/0/0 | 3.30 s |

The disjoint total is 2,083 tests. Focused reruns and external comparison probes
are not added to that count. Seven prior test identifiers were renamed or
reparameterized with their behaviors preserved. Five characterization cases were
added. Tests used the declared environment, native library, Node, fictional data
and aligned civil clocks.

All 394 source CLI invocations retained raw standard output and exit status.
A separate parser diagnostic matched exactly. Eleven error traces moved to extracted
owners while preserving terminal exception types and messages. All 21 actual broker
responses matched, including allowed dispatch and table, column, path, flag and
command refusals.

All 336 complete database snapshot pairs matched after narrow generated-time
validation. 208 were raw-equal. Every other schema, value, source, reference,
sequence and identity remained exact. The independent audit traced 728 timestamp
occurrences to their originating writes and reproduced six time-dependent event
hashes, plus raw-capture/content and trigger/deduplication hashes. Explicit second
boundaries verified timestamp preservation versus refresh. All 336 vault snapshots
matched exact paths and bytes. No blanket timestamp or identity removal was used.

Paired timing used nine workloads and 108 fresh launches, alternating revision
order with one first observation and five repetitions. Fixture copying was outside
timing and filesystem caches were not forcibly cleared. An intermediate eager HTTP
import was restored to its original on-demand behavior before acceptance.

| Workload | Baseline median | Extracted median |
|---|---:|---:|
| Schema status | 138.80 ms | 139.34 ms |
| Readiness | 129.75 ms | 130.25 ms |
| Nutrition targets | 121.90 ms | 122.31 ms |
| Statistics | 279.05 ms | 279.17 ms |
| Water addition | 122.43 ms | 122.60 ms |
| Day rating | 123.90 ms | 124.53 ms |
| Feedback status | 122.74 ms | 123.05 ms |
| Timing adherence | 124.85 ms | 125.89 ms |
| Note write | 122.62 ms | 123.00 ms |

All timed outputs matched. Median changes were at most 1.04 ms, with no unexplained
regression in this bounded sample.

The offline ad hoc app built from `97c838b` passed 14 lifecycle and nine actual MCP
checks. All 19 selected modules matched committed source, and all 148 loaded module
files in the isolation probe stayed inside the bundle. The complete command
collection verified 54 loaded product files against tested source.
After all execution, deep/strict signature verification passed and all 3,111 audited
file hashes remained identical, with zero findings.

The package retained raw standard output and exit status for all 394 CLI invocations,
exact parser metadata, 336 validated database comparisons, 336 vault comparisons and
21 broker responses.
Source-root traceback locations move into the bundle. Two JSON errors
change standard-library frames under Python 3.12. The argument-error quoting
difference was reproduced exactly with the actual preceding app. This qualifies
local package behavior, without native GUI, minimum-OS, notarization, Gatekeeper
or clean-customer-install acceptance.

The explicit Hermes inventory includes all ten new runtime modules. Recursive cache
identity remains active. Numerical provenance and replay pins are unchanged. The
inherited [resampling and tied-candidate caveat](#corrected-toolkit-baseline-22-september-2026)
remains. Schema, broker privileges and ledger/synthesis rules were not changed.

Independent source, verification and package reviews found no remaining blocker.
Inventory, vendor/license and source/history privacy checks passed. Earlier fixture,
helper, socket, whitespace, eager-import and comparison-harness diagnostics remain
in local evidence, with interrupted attempts separate from successful checks.
Nothing was pushed, merged, published or connected to a live service.

The next bounded family is nutrition, profiles, targets and food workflows,
following the [approved order](TOOLKIT_MODULARIZATION.md#milestone-3-remaining-command-families).

## Nutrition and food extraction, 22 September 2026

The third Milestone 3 family is complete locally. Production commit
`f09f9fe34904837649e3e6ed7b478e2939b68820` exactly matches source-tested tree
`d692d80f2b88a0687e9c5c58609454ef848a7dec`. All 516 file hashes and staged Git state
remained unchanged during complete verification.

Fourteen profile, phase, target, coverage, recipe, food, inventory and restock
commands now have direct CLI owners. The facade retains 80 unconverted handlers.
All 122 parser definitions and handler-name metadata remain exact. Nutrition
coverage and retained scores share one calculation owner. Score bands and rounding
also have one owner. See [Nutrition and food ownership](ARCHITECTURE.md#nutrition-and-food-ownership)
for configuration, transaction and sidecar contracts.

Nine new cases characterize rollback, oldest-batch behavior, current-day snoozes,
sidecar replacement and independent configuration/clocks. Four existing nutrition
date cases now use explicit dependencies. The recovery case retains its existing
consumer. All fourteen relevant cases also passed unchanged baseline through an
external adapter. No test-only production seam or weakened guard was introduced.

| Partition | Passed | Failed/errors/skipped | Pytest duration |
|---|---:|---|---:|
| Application, including toolkit | 2,072 | 0/0/0 | 508.75 s |
| Fictional end-to-end journeys | 15 | 0/0/0 | 193.58 s |
| Actual MCP client/server | 5 | 0/0/0 | 3.32 s |

The disjoint total is 2,092 tests. Focused repeats and external comparison
assertions are not additional tests. Checks used fictional records, declared
dependencies, Node, the native statistical library and aligned civil clocks.

All 419 source CLI calls and the separate parser diagnostic retained raw stdout
and exit status. All 15 actual broker responses matched. Of 420 stderr records,
402 match raw bytes, two differ only by source roots, and sixteen relocate frames
to new owners. Their full exception chains remain exact.

All 374 complete database pairs agree. Of these, 289 match raw bytes. The remaining pairs
differ only in six named generated timestamp fields. Each of 496 occurrences is
traced to one of 74 revision-specific originating cells and its invocation interval.
No IDs or arbitrary dates were normalized. All 374 vault comparisons match paths
and bytes. Broker children use their actual civil date. The separate CLI fixture
launcher supplies a fixed clock.

Paired timing used ten workloads and 120 fresh launches, alternating revision
order with one first observation and five repetitions. Fixture copying was outside
timing. Filesystem caches were not forcibly cleared. All paired stdout and stderr
bytes matched.

| Workload | Baseline median | Extracted median |
|---|---:|---:|
| schema-status | 144.86 ms | 144.06 ms |
| readiness | 136.43 ms | 135.97 ms |
| nutrition-targets | 126.84 ms | 128.16 ms |
| stats | 283.03 ms | 285.09 ms |
| nutrition-coverage | 130.22 ms | 131.04 ms |
| scores | 124.07 ms | 125.73 ms |
| menu | 122.28 ms | 123.60 ms |
| eat | 124.63 ms | 124.69 ms |
| log-food | 123.92 ms | 125.46 ms |
| recipe-ingredients | 124.11 ms | 124.11 ms |

Observed median changes ranged from -0.55% to +1.34%, with a maximum increase of
2.06 ms. No material regression appeared in this bounded sample.


The actual offline ad hoc app built from `f09f9fe` passed 14 lifecycle and nine
bundled MCP checks. An isolated probe found all 153 loaded module files inside
the bundle. Thirteen selected files matched Git, including all five new owners.
The complete CLI collection checked 59 loaded product files against committed
source. After explicit verifier release, deep/strict signature verification passed
and all 3,116 audited file hashes remained unchanged, with zero findings.

The package retained raw stdout and exit status for all 419 CLI calls, the exact
parser diagnostic, 374 validated database comparisons, 374 exact vault comparisons
and 15 actual broker responses against both source versions. Against the candidate,
source-root normalization leaves two JSON standard-library traces and one argparse
quoting difference. Actual preceding/current app diagnostics reproduce identical
Python/argparse bytes and unknown-command output. Raw stderr equality is not claimed.

The initial default-sandbox build failed at native icon generation. The unchanged
builder passed with approved native tool access. No product repair was required.
This qualifies local package behavior. Native GUI/charts, minimum OS, Developer ID,
notarization, Gatekeeper and clean-customer installation remain unqualified. No
installer archive was created or published.

The explicit Hermes inventory includes all five new runtime modules. Recursive
cache identity remains intact. Numerical provenance and golden replay pins are
unchanged. The inherited [seed and tied-candidate caveat](#corrected-toolkit-baseline-22-september-2026)
remains. Schema, broker privileges, existing import normalization and ledger/synthesis
rules were not changed.

Independent source, contract, suite, timing and package reviews found no remaining
blocker. Inventory, license/vendor and source/history privacy checks passed. Earlier
fixture, comparison, cache and execution-permission diagnostics remain in local
evidence, separate from successful verification. Nothing was pushed, merged,
published or connected to a live service.

The owner changed main and successor tasks to Astra High. Independent review stays
at Astra extra high. The [authoritative plan](TOOLKIT_MODULARIZATION.md#roles-and-ownership)
records that override. The next bounded family covers training, fitness tests,
muscle calculations, pain and mobility in the approved order.

## Training and movement extraction, 22 September 2026

The fourth Milestone 3 family is complete locally. Production commit
`8e2b0894fa6d5b0f3c2c9bea1014a5a804d457af` matches source-tested tree
`8a29ddec0c4a8039233439ea571b48f88fa0f3d0`. All 527 file hashes and staged Git
state remained unchanged during final verification.

Twenty training, fitness, muscle, pain and mobility commands now have direct
CLI owners. Sixty unconverted handlers remain in the facade. All 122 parser
definitions and handler names remain exact. The shared rollup has one owner;
its anchored and legacy windows retain their distinct behavior. See
[Training and movement ownership](ARCHITECTURE.md#training-and-movement-ownership)
for dependencies, transaction boundaries and preserved asymmetries.

Nine new cases characterize extra fitness fields, void behavior and rollback
through actual SQLite/CLI paths. Two existing mobility and pain cases now use
explicit owners and dependencies. The new cases passed unchanged baseline;
the original two existing cases also passed baseline. No test-only production
seam or weakened assertion was introduced.

| Partition | Passed | Failed/errors/skipped | Pytest duration |
|---|---:|---|---:|
| Application, including toolkit | 2,081 | 0/0/0 | 513.46 s |
| Fictional end-to-end journeys | 15 | 0/0/0 | 193.78 s |
| Actual MCP client/server | 5 | 0/0/0 | 3.32 s |

The disjoint total is 2,101 tests. Focused repeats and contract audit assertions
are not additional tests. Final checks used fictional records, declared
dependencies, Node, the native statistical library and aligned civil clocks.

All 437 source CLI calls and the separate parser diagnostic retain raw stdout
and exit status. All twenty actual broker responses match: thirteen exposed
commands and seven refused writers. Of 438 stderr records, 433 match raw bytes;
five relocate traceback frames while retaining complete exception chains.

All 420 complete database comparisons agree, with 292 raw-equal. All 420 vault
comparisons match exact paths and bytes. Generated-time exceptions cover eleven
named columns: 2,692 occurrences trace to 164 originating revision/cell writes within captured UTC-second intervals.
Enqueue audit-event IDs contain generated time. Each of 466 occurrences reproduces
its complete canonical hash material, covering 26 revision-specific IDs; stable
trigger IDs remain exact. No arbitrary identifier or date is stripped. CLI civil
time is fixed; actual broker children use real civil time.

Four injected trigger-failure corpus cases reach protected schema-inventory
refusal, not late trigger/event insertion. The evidence states that limit. A
separate durable fitness idempotency-conflict case and the retained pain boundary
case prove enqueue rollback. Earlier variable seed timestamps were corrected in
the input fixture before final collection, without adding a comparison exception.

Paired timing used ten workloads and 120 fresh launches after complete suites,
alternating revision order with one first observation and five repetitions.
Every paired stdout/stderr stream matches. Fixture copying was outside timing,
and filesystem caches were not forcibly cleared.

| Workload | Baseline median | Extracted median |
|---|---:|---:|
| scores | 126.79 ms | 125.88 ms |
| readiness | 131.81 ms | 133.40 ms |
| muscle-volume | 125.24 ms | 124.49 ms |
| muscle-detail | 126.45 ms | 127.31 ms |
| muscle-map | 126.23 ms | 125.04 ms |
| fitness-tests | 124.27 ms | 124.97 ms |
| athletic-radar | 124.75 ms | 125.23 ms |
| strength-ratios | 125.89 ms | 125.70 ms |
| pain-log | 147.80 ms | 150.60 ms |
| routine-set | 137.92 ms | 138.57 ms |

Observed median changes ranged from -0.94% to +1.89%, with a maximum increase
of 2.80 ms. No material unexplained regression appeared in this bounded sample.
The readiness workload returned its supported insufficient-data result, without
a schema refusal. These timings do not establish general workload latency.

The actual offline ad hoc app built from `8e2b089` passed fourteen
lifecycle and nine bundled MCP checks. An isolated -I -B probe verified 22 Git-exact selected files, including all ten
new owners, and 163 contained loaded module paths. The full CLI collection
verified 69 contained product files across 437 traces.
The corresponding-source ZIP has 529 entries: 527 exact project files and two
pinned dependency sources. Its SHA-256 is
`8c7bcf1c202839b3bb1e56a5e4054d74be9cc9fd7c474640f7dde18cb1c754d9`.
The package preserves all 437 CLI outputs/exits, the parser diagnostic, 420
complete database/vault pairs and twenty broker responses against both source
versions. Of 438 stderr files, 432 are raw-equal. Against the candidate, source-root
substitution leaves only Python standard-library strptime frames and argparse
choice quoting. Actual preceding/current apps reproduce identical unknown-command
output with identical Python/argparse bytes. Raw stderr equality is not claimed.
Harness CLI/parser and broker server launches use -I -B; the unchanged broker
child command does not forward those flags and retains bytecode suppression.
After explicit execution release, final deep/strict signature verification passed
and all 3,126 audited file hashes remained unchanged, with zero findings. The first
default-sandbox build failed at icon generation; the unchanged native-access retry
passed. No source or sealed-app repair was required.

This qualifies local package behavior. Native GUI/charts, minimum OS, Developer
ID, notarization, Gatekeeper and clean-customer installation remain unqualified.
No installer archive was created or published. Pattern scans are not exhaustive
privacy certification.

The explicit Hermes inventory includes all ten new runtime modules. Recursive
cache identity, narrow numerical provenance and golden replay pins remain
unchanged. The inherited [seed and tied-candidate caveat](#corrected-toolkit-baseline-22-september-2026)
and [daily collector failure-path behavior](#daily-workflow-extraction-22-september-2026)
remain. Schema, broker privileges, existing import normalization and
ledger/synthesis internals were not changed.

Independent source, contract, suite, timing and package reviews found no
remaining blocker. Inventory, vendor/license and source/history privacy checks
passed. Initial name-shadowing and fixture failures remain separate diagnostics.
Review caught three facade bindings removed too broadly; they were restored
before final verification. The superseded suite was interrupted and is excluded
from passing counts.

The next family covers daily frames, scores, Recovery/readiness and laboratory
workflows. Continue through the approved fresh-main relay on Astra High, with
independent review on Astra extra high. Push, merge, release and live/private
boundaries remain unchanged.

## Daily frames, scores, Recovery and lab extraction, 22 September 2026

The fifth Milestone 3 family is complete locally. Production commit
`459d53f54e0b19b0b06910f43f0daf4ef5096399` matches source-tested tree
`42ac98e32836ea11bab32ee7dc8e4aa85329b7bb`. Seventeen paths changed, including
eight runtime owners and one new boundary-test file. All 536 source files
remained exact during acceptance verification.

Thirteen commands now register directly through the shared CLI, leaving 47
unconverted registrations. All 122 parser contracts and handler names remain
exact. See [Daily frames, scores, Recovery and labs ownership](ARCHITECTURE.md#daily-frames-scores-recovery-and-labs-ownership)
for the calculation, transaction and ancestry owners. Existing numerical
engines, schema, broker permissions and vendor bytes are unchanged.

Eight new cases passed unchanged baseline. They cover lab batch/catalog
rollback, stored-prior selection, intra-batch isolation, catalog reflagging,
trend deduplication, frame history/zero-fill and Recovery range asymmetry.
Two existing tests now call explicit owners with fixed clocks. Their original
assertions remain, including dashboard recovery score 75 and fourteen baseline rows.

| Partition | Passed | Failed/errors/skipped | Pytest duration |
|---|---:|---|---:|
| Application, including toolkit | 2,089 | 0/0/0 | 550.51 s |
| Fictional end-to-end journeys | 15 | 0/0/0 | 204.32 s |
| Actual MCP client/server | 5 | 0/0/0 | 3.42 s |

The first complete run recorded zero process exits and 2,109 passes. A relative
JUnit output path placed XML in an untracked source directory; cleanup deleted
those original XML files. Complete raw logs, commands, process results and all
before/after source hashes remain. Independent review verified these records
and collected the same frozen partitions separately: 2,109 unique, disjoint
test identities. This collection is not a replacement execution or recreated
JUnit evidence. Interrupted redundant attempts and focused reruns are excluded
from the passing total. Git's status difference was confined to that generated
directory; the staged tree and all tracked bytes stayed exact.

Source contract comparison covers 385 actual CLI calls, one parser diagnostic,
306 complete database/vault snapshot pairs, and fifteen actual broker responses.
All exits and stdout payloads match raw bytes. Of 386 stderr streams,
385 are raw-equal; one lab
failure traceback moves through the new owners and retains the same complete
exception. Generated timestamps are limited to `labs.ingested_at` and
`lab_catalog.created_at`: 736 occurrences trace to 68 revision-specific origin
cells. Seed values, all other fields and identifiers stay literal. All vault
paths and bytes match. The source CLI loaded 77 contained Git-exact product
files, including every new owner.

The broker exercised nine allowed reads, four refused commands and two
allowed commands with invalid integer arguments. Positive Recovery has three
known components and score 83. Its same-input source results and complete
state agree. CLI clocks are controlled; ordinary broker children retain their
real civil clock. Early collector defects and fixture errors remain diagnostic
records; final collection and comparison use separately preserved corrected
helpers and independent raw-evidence review.

Paired timing used ten workloads and 120 fresh launches after source verification,
with alternating revision order, one first observation and five repetitions.
Every paired stdout/stderr stream matched; database/vault copies and audits
were outside the timed process launch. Median changes ranged from -5.74% to
+1.39%, with a maximum increase of 2.34 ms. No material unexplained regression
appeared in this bounded sample; caches were not forcibly evicted. The timing
readiness case retained its supported insufficient-data response, while the
separate source/broker corpus verified a positive composite result.

The actual offline ad hoc app passed fourteen lifecycle checks and nine bundled
MCP checks. Its isolated probe verifies seventeen selected Git-exact files,
including all eight new owners, and 171 contained runtime/product module files.
The external verification harness is separately identified. The CLI collection
loads 77 contained Git-exact product files. All 385 CLI calls, the parser
diagnostic, 306 database/vault snapshot pairs and fifteen broker responses
match both source versions under the same explicit timestamp-origin rules.
All stdout records are raw-equal; 384 of 386 stderr streams are raw-equal.
The two reviewed differences are the lab traceback and inherited Python
argparse choice quoting. The current and preceding bundled Python/argparse
bytes are identical; the preceding sealed app was not executed.

The corresponding-source archive contains 538 entries: all 536 exact project
files and two pinned dependency sources. Its SHA-256 is
`5368b4c7c27b6f8012c8465d5dd72c52d1fdca08f74035f98e1fc92124d955a7`.
Package CLI/parser and server launches use bundled Python with `-I -B`; the
unchanged broker child invocation does not forward these flags. Its inherited
bytecode suppression remains. An initial corpus path incorrectly assumed
test fixtures ship inside the app; final source/package collection uses one
external hash-pinned fictional catalog instead. No product or bundle repair
was needed. A preserved module receipt uses a 39-character commit prefix;
an appended receipt resolves it to the exact production commit.

After explicit execution release, the final deep/strict signature check passed
and all 3,134 audited bundle file hashes remained unchanged, with zero findings.
No app/runtime execution is permitted after that seal without renewed
coordination and a repeated final audit.

Initial review found omitted timed-checkin fields, lost timezone arguments,
changed validation order and incorrect dependency forwarding in the draft.
These were corrected before the source freeze. Independent review found no
remaining source or evidence blocker. Inventory, license/vendor and current
source/history scans passed after generated bytecode was moved outside source.
Pattern scans remain bounded checks, not exhaustive privacy certification.

The inherited [seed and tied-candidate caveat](#corrected-toolkit-baseline-22-september-2026)
and [collector failure-path behavior](#daily-workflow-extraction-22-september-2026)
remain unchanged. Native GUI/charts, minimum OS, Developer ID, notarization,
Gatekeeper and clean-customer installation remain unqualified. No installer
was published.

The next family extracts the remaining CLI coordination around existing
feature, association, ledger, synthesis and orchestration engines, without
redesigning those engines. Continue through the approved fresh-main relay;
final compatibility cleanup and integrated acceptance follow. Publication,
merge and live/private-data boundaries remain separate.

## CLI coordination extraction, 22 September 2026

The sixth Milestone 3 family is complete in the local tree. Production commit
`fee48cc404cbaed566adb453e92c4127bf18651a` matches the tested source tree
`02698d02ba0cac4c49dd1f905cd59bf17c9adb95`. Twenty-six paths changed, and the
frozen tree contains 547 source files.

The remaining 47 commands now register directly through the shared CLI. All
122 parser contracts and handler names remain exact, and the legacy dispatch
map is empty. Ten command modules own coordination for schema/query, events,
features, associations, analysis jobs, ledger, synthesis, scheduled analysis
and orchestration. The facade contains no SQL implementation. See
[Feature, ledger and orchestration command ownership](ARCHITECTURE.md#feature-ledger-and-orchestration-command-ownership).
Its remaining compatibility exports require the final Milestone 4 consumer
audit.

Existing numerical, ledger, synthesis, orchestration, runtime, cache and job
engines remain unchanged. Schema-version asymmetries, transaction boundaries,
JSON formatting and stable child launch paths are preserved. Six new durable
tests cover medication event atomicity, synthesis durability/validation and
the real analysis-job start/work/status path; they also pass the unchanged
baseline.

| Partition | Passed | Failures/errors/skips | JUnit duration |
|---|---:|---|---:|
| Application, including toolkit | 2,095 | 0/0/0 | 527.562 s |
| Fictional end-to-end journeys | 15 | 0/0/0 | 222.279 s |
| Actual MCP client/server | 5 | 0/0/0 | 3.285 s |

These are 2,115 distinct passing identities, with the original JUnit and raw
execution evidence retained. Application/MCP ran on the initial freeze; all
relevant runtime, test and substantive input bytes match the final freeze.
Final E2E ran on the final tree. This is explicit cross-revision evidence, not
three final-tree executions. The prior family's missing original XML remains a
prior-family limit.

The first E2E run failed two trace pins. Independent comparison proved that
only relocated owner labels and one derived projection digest per trace
differed. Reversing those labels and recomputing the digest reproduced both
previous canonical traces exactly. Only those two pins, their fixture manifest
and the release inventory changed before the passing final E2E run. Numerical
outputs, selection, model inputs, lineage and notification status did not
change.

Source comparison covers 336 actual CLI invocations per version and a separate
122-command parser diagnostic. Every exit and stderr stream matches; 334
stdout streams match raw bytes. Two synthesis outputs differ only in their
verified temporary vault root; relative paths and file bytes match. All 178
complete canonical database snapshots and 672 vault snapshots match exactly.
The 178 auxiliary snapshots retain complete SQLite logical state and physical
digests; historical raw sidecar files are not claimed equal or retained.

Cache/job identity differences are recomputed from exact code manifests, paths,
Python/context and captured canonical hash material. Only verified identity
and derived key fields differ; all other logical sidecar fields remain literal.
The corpus positively exercises 46 family commands; actual broker children and
the durable job test prove successful fenced `analysis-job-execute` behavior.

Twenty non-job broker requests and full responses match raw bytes. Real jobs
complete through unmodified child processes with equal requests and result
bytes. Different poll counts reflect asynchronous progress; each generated
job/attempt ID, timestamp and key is checked against its saved state and
interval. Canonical databases remain physically unchanged, with exact empty
vaults.

Authenticated fictional evidence uses one audited fixture adaptation: an
existing `change_conditions` sentence is encoded as a JSON string. Decoding
recovers the same sentence. All other cells and restored trigger SQL remain
exact; the SQLite schema-change counter increases by two. This is disclosed
adapted test data, not an unmodified fixture or a ledger generated entirely
through public writes.

Clean paired timing covers ten workloads and 120 fresh launches, alternating
revision order. Every paired stdout/stderr matches. Median increases range
from 0.57% to 4.18%; the longest association workload increases 108.57 ms
(0.81%). Short-command increases range from 0.97 to 6.06 ms. No material
unexplained regression appears in this bounded comparison against the
preceding checkpoint; Milestone 4 still compares final performance with the
corrected baseline.

An earlier timing attempt is diagnostic only: the baseline contained fifteen
bytecode files while the candidate had none. Independent examination matched
all 877 recursive code objects to fresh source compilation, preserving earlier
source provenance. Timing was repeated from complete Git-exact exports that
reject every bytecode file and cache directory before and after execution. No
performance code change was needed. Filesystem caches were not forcibly
evicted.

The fresh offline ad hoc app passed fourteen lifecycle and nine bundled MCP
checks. Its isolated probe verifies nineteen selected Git-exact product files
and 182 contained module entries across 179 distinct bundle files. The external
verification harness is identified separately. The packaged CLI loads 101
contained Git-exact product files and preserves all 336 cases plus the separate
parser inventory under the same state and identity checks. Only unknown-command
stderr adds the inherited Python 3.11/3.12 difference in its quoting of all 122
choices; 335 stderr streams remain raw-equal. The two verified vault-root stdout
exceptions remain; 334 stdout streams match raw bytes.

Twenty packaged non-job broker envelopes and the actual job request/result bytes
match source. A read-only bundled Python probe independently reproduces both the
unpatched broker and CLI cache identities. It performs no analytical calculation
and preserves the fictional database and every bundle file. Package CLI/parser,
module and identity probes use `-I -B`. The successful broker corpus launches
bundled Python with `-B`; its unchanged child command forwards neither flag
and inherits bytecode suppression.

The corresponding-source ZIP contains 549 entries: all 547 exact project files
and two pinned dependency sources. Its SHA-256 is
`73aa28eb1353535fa6c517dba8b09b68e83c70c3b809e393ecc69521156baea1`.
After explicit execution release, final deep/strict signature verification passed
and all 3,144 audited regular-file hashes remained unchanged, with zero findings.
The probe's larger file count includes two internal Python symlinks; its source
count additionally includes the verified generated build-info file. No bundle
execution is permitted after sealing without renewed coordination and audit.

Initial native-build and socket/sandbox failures remain separate diagnostics.
The unchanged builder and actual verifiers passed with the recorded bounded local
permissions. No product or bundle repair was needed. Independent source, suite,
contract, timing and package reviews found no remaining blocker. License/vendor
and source/history checks passed; final checkpoint inventory verification is
recorded separately from the production package.

The inherited [seed and tied-candidate caveat](#corrected-toolkit-baseline-22-september-2026)
and [collector failure-path behavior](#daily-workflow-extraction-22-september-2026)
remain. Native GUI/charts, minimum OS, Developer ID, notarization, Gatekeeper
and clean-customer installation remain unqualified. Pattern scans are bounded
checks, not exhaustive privacy certification. No installer was published.

The next milestone audits the remaining compatibility consumers, removes only
proven obsolete plumbing, completes documentation and integrated verification,
and compares final performance with the corrected baseline. Continue through
the approved fresh-main relay on the same branch. Push, merge, publication,
credentials and live/private-data boundaries remain separate.

## Final toolkit acceptance, 22 September 2026

Production commit `17851cfca77b33369a22a04d97f7f0a72c0ac908` matches the
verified tree `a817fecead2567ea7956c1183f6dba51338b2954`. The closing
checkpoint changes only this documentation and its release inventory.

Milestone 4 removes 43 obsolete internal dispatch delegates and the empty legacy-handler overlay. The shared CLI registers every executable command centrally and dispatches it to its owning module. The facade retains database and clock seams, read-only guards, configured catalogs and older Python delegates. It contains no SQL, importer or domain-calculation implementation. The consumer inventory records evidence of the removal and the limits of auditing undocumented external imports.

[Architecture](ARCHITECTURE.md#toolkit-command-boundaries) describes retained compatibility and direct registration. Its [calculation and test index](ARCHITECTURE.md#calculation-and-test-navigation) connects domain owners, callers and relevant tests. Numerical engines, schemas, transactions, broker permissions, public licensing and vendor bytes are unchanged in this milestone.

Complete source verification covers 2,115 distinct passing identities: 2,095 application tests, 15 fictional end-to-end tests and five actual MCP tests. The initial restricted runs passed 2,086 application tests, 14 journeys and all five MCP tests. Nine application checks and one journey then passed with local socket/process permission. Only those unsuccessful identities were retried. Source hashes remained unchanged; focused checks and retry overlap are excluded from the total. The evidence comes from split environments; it does not establish three uninterrupted green runs.

Earlier family corpora remain accepted through independently checked revision links and comparisons of owner bytes, handler bodies, binding values and configuration expressions. The fresh final-family comparison exercises 336 CLI cases per version and a separate 122-command parser inventory against the corrected baseline. Every exit status and stderr stream matches. Two synthesis outputs differ only in verified temporary vault paths. The other 334 output streams match byte for byte. Ten internal importer callback names retain their previously approved owner changes. Reversing only those names recovers the complete baseline parser inventory.

Both CLI versions receive the same explicit migration-label input,
`HERMES_CODE_VERSION=c427296a286237ff72ffb733bc9bbdb4e25fcc54`.
Actual source identities are checked separately, and seeded fixture rows remain
unchanged. The comparison does not claim default deployment-version equality.

Comparisons between the source and packaged broker verify twenty non-job
request/response envelopes byte for byte. Real child jobs complete with exact request and result
bytes. Generated IDs, timestamps, cache keys and asynchronous poll sequences
are checked against their own stored state and captured identity material.
Canonical databases remain physically unchanged and vaults remain empty.
Historical sidecar SQLite files are represented by complete logical snapshots
and physical digests, without a claim of physical-byte equality.

The fresh ad hoc app passed fourteen lifecycle checks and nine actual bundled
MCP checks. Its isolated module probe and CLI traces verify that the runtime
owners are contained and Git-exact. The packaged corpus preserves all 336 cases
and the separate parser inventory. The single unknown-command stderr difference
is the verified Python 3.11/3.12 change in quoting all 122 choices. Every other
stderr stream matches. The two verified temporary-vault stdout differences remain.

Package CLI/parser and module probes use bundled Python with `-I -B`. The
broker runs with `-B` without `-I`. Its unchanged child inherits bytecode
suppression. Read-only probes reproduce both the default broker and actual
script CLI identities under bundled Python 3.12.13 before execution release.

The corresponding-source ZIP contains all 547 Git-exact project files and two
pinned dependency source archives. Its SHA-256 is
`53ad3e8b0a8f1a36c179b010c1dcb76437c28c5fb54d6a81817689cf8c22f5e9`.
After every execution owner released the app, final deep/strict signature
verification passed. All 3,144 audited regular-file hashes remained unchanged,
with zero findings. No bundle process ran after the seal.

The corrected-baseline timing comparison uses 60 alternating fresh-process launches across five workloads, with cache-free Git exports, fixed fictional inputs and no native acceleration. All paired output streams and database/vault effects match. Median command times for schema, readiness and nutrition increase by approximately 5–6 milliseconds. Statistics and association medians decrease slightly in this sample. Independent review found no material unexplained regression. These measurements do not establish a general speed guarantee.

The inherited readiness benchmark returns the same insufficient-data result, so its timing does not qualify populated readiness calculations. The statistical workload retains its 120 observations and fixed seeds. The association workload returns one finding. Filesystem caches were not forcibly cleared.

The inherited engine-identity/resampling and tied-candidate caveat remains. Collector failure-provenance lock contention also remains. Current verification retains its original XML. The missing original XML in the earlier daily-frame family remains a limitation of that historical family. Source/history scans have bounded scope and do not provide exhaustive privacy certification.

Push, merge, hosted checks and release publication remain separate gates. Local verification does not qualify GUI/charts, minimum OS, Developer ID, notarization, Gatekeeper or clean-customer installation.

This closes Milestone 4 and the approved toolkit modularization plan. The
feature branch and sealed local package remain unpublished.

## Guided ChatGPT desktop setup, 23 September 2026

The Connect AI page now guides supported ChatGPT desktop users through one step
at a time, with Back and Next controls. It preserves the selected workspace and
exact command arguments, separates personal-data approval from fictional setup,
and keeps the generic setup-agent instructions available. Loading failures,
missing workspaces, unavailable MCP settings and copying failures have recovery
paths. Completing the guide records only the user's confirmation. That
confirmation does not prove that ChatGPT is connected. See
[Guided ChatGPT desktop setup](DESKTOP_MCP.md#guided-chatgpt-desktop-setup).

Forty focused desktop-guide, server-boundary, MCP-configuration and
release-scanner tests passed. A fictional source preview verified the six-step
flow, navigation, missing-settings help, copy feedback, the generic instructions,
and desktop/narrow layouts. The first visual pass found that the initial screen
was unnecessarily tall. One bounded correction kept navigation visible and
simplified the configuration rows. The JavaScript harness covers clipboard
payload handling. Browser feedback alone does not prove a client connection.
The protected ChatGPT settings surface was not inspected. No client configuration
was changed, and no signed installer was modified. The published 0.2.6 release
remains unchanged. The only scanner policy addition is the reviewed official
documentation host `learn.chatgpt.com`.

The subsequent local revision makes agent-assisted setup the first route. Users
copy instructions for a local desktop agent, then choose **It's connected** to
test the connection or **I need help** to open the manual steps directly.
The MCP settings step retains help for unsupported clients.
Personal-workspace approval remains required before the test, and
Back follows the selected route. The supplied configuration and read-only
connection are unchanged. This remains an unreleased interface change.
