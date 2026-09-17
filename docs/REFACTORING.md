# Refactoring checkpoint

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
