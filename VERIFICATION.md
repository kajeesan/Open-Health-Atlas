# Release verification

Verification dates: 15–16 September 2026. The current public source release
uses the same runtime, tests and dependencies as the final **1,981-test**
checkpoint below. The subsequent MIT license, creator-credit and publication
documentation changes are reviewed separately; they do not introduce runtime
changes. The new public repository uses a clean parentless history.

Earlier sections record their own local refactor, isolated acceptance and
separately authorized installation checkpoints. Their private-upload and
deployment descriptions are historical. The rolling batch history and recovery
commits are in [docs/REFACTORING.md](docs/REFACTORING.md).

## Retained suite

Environment: CPython 3.14.6 on macOS, installed from `requirements-dev.txt` in
an external temporary environment. Bytecode and pytest caches were disabled;
all databases, sockets and diagnostic outputs were temporary and fictional.
No private health directory or live Telegram installation was used.

At checkpoint `0a35b82`, all **1,830 retained tests passed** in these
non-overlapping partitions. The latest wording and deployment-path follow-up
was then verified with affected selections. The schema-7 compatibility batch
received complete partitioned verification, recorded at the end:

| Selection | Result |
| --- | --- |
| `python -m pytest -q -p no:cacheprovider --ignore=tests/e2e` | 1,815 passed in 497.09s |
| `python -m pytest -q -p no:cacheprovider tests/e2e/test_autonomous_insight_traces.py` | 14 passed in 137.72s |
| `python -m pytest -q -p no:cacheprovider tests/e2e/test_hermes_green_days_acceptance.py` | 1 passed in 89.01s |

The non-E2E run set `HEALTH_DB` to an unused temporary path as a fail-safe for
otherwise unconfigured reads. E2E subprocesses used the same pinned interpreter
through `PATH`. Each partition used an external `--basetemp` directory. Local
Unix sockets required execution outside the restricted socket sandbox. An
initial socket-bind failure was environmental; the socket-enabled run passed.

This is complete partitioned coverage, not one uninterrupted invocation. Earlier
August test counts and historical receipts do not certify this candidate.

After the capped-server measurements, a timeout-only correction passed the
existing broker, bridge and insights API selection: **98 passed in 2.05s**.
The actual ten-scenario generator ran again and all **14 trace tests passed in
133.93s**. These checks overlap the 1,830 above. Scenario inputs, seeds,
classifications and narratives stayed unchanged; generated seals and three
recorded client-wait expectations were updated. The computed analytical engine
identity remained unchanged by the timeout correction.

## What the checks establish

- Empty initialization, schema migrations, validated imports/writes, read-only
  access, authentication and supported API/CLI operations.
- Deterministic calculations, source/time/unit boundaries, missingness,
  provenance, stale evidence rejection, ledger/synthesis and orchestration.
- The real panel/broker/tool/evidence/conversation path with a **fake external
  Hermes**. This does not prove real-model reasoning or Telegram transport.
- Ten regenerated deterministic traces retain their original inputs, seeds,
  expected classifications and narratives. Only engine-dependent seals and
  obsolete page-digest proof wording changed. Behavioral and evidence-integrity
  assertions remain.

## Measured repairs

The comprehensive fictional Green-only command previously exceeded 110 seconds:

```bash
oha_demo_dir="$(mktemp -d)"
python scripts/make_demo_db.py --output "$oha_demo_dir/health.db" \
  --anchor-date 2026-06-30
HEALTH_DB="$oha_demo_dir/health.db" python toolkit/health.py outcome-associations \
  --outcome subjective.day_rating --mode green-vs-non-green \
  --from 2026-03-02 --to 2026-06-30 --min-n 30 \
  --interactions none --top 3
```

The optimized engine completed in **132.46s**, peak RSS **1.95 GiB**. The integrated
panel bridge client and real broker completed a newly corrected fictional
fixture in **134.12s**, returning all three requested findings inside that checkpoint's
180-second child limit. The final broader-read limits are described below. No tested candidate or resampling iteration was removed:
3,755 permutation candidates, seven Fisher candidates and 7.51 million
permutations were retained in the measured original fixture.

Old/new full fixture results and internal evidence matched with a shared engine
identity; 8,004 raw permutation statistics also matched. Source edits intentionally
change engine/evidence identities and resampling seeds. Prior findings require
recomputation rather than relabeling.

Browser checks verified:

- Recovery's insufficient-data state, available sleep component and explicit
  same-source HRV/RHR exclusions;
- corrected recipe nutrients, real consumption and persisted `panel-ui` rows;
- immediate headline and target-breakdown refresh after consumption;
- note preview, validated save and reload from the same configured vault;
- navigation/rendering for Dashboard, Body, Mind, Consistency, External care,
  Labs, Data Explorer and Insight Explorer, with no observed browser errors.

Navigation/render smoke is not exhaustive interaction or visual certification.

## Full Explorer and pairwise evidence replay

The final optimization reuses outcome-independent exposure alignment across
three modes, while giving each mode its own outcome rows. Full internal replay
defers normalization of references until the selected finding is known. No
candidate, statistical draw, source boundary or evidence check was removed.

On the local comprehensive fictional fixture, all-mode Explorer improved from
**186.33s to 149.01s**, with sampled peak memory falling from **4.23 to 2.42 GiB**.
Full replay completed in **141.94s / 2.48 GiB**; its prior run was stopped
uncompleted after 314.22s / 4.76 GiB. With a common engine identity, full old/new
Explorer output matched exactly. A positive pair fixture also matched analysis
and replay exactly, retaining 16 pair findings and 2,000 permutation plus
2,000 bootstrap draws. The existing alignment regression now checks the
remapped candidates against independently computed per-mode candidates.

The same source on the capped one-CPU server took **448.55s for Explorer** and
**442.58s for exact replay**, with peak child RSS **2.39 GiB**. Both diagnostic
reads completed, preserved their database and agreed on the finding, input and
evidence identities. This was a failure of the old 180-second production budget;
it is retained as such, rather than relabelled as a passing budget check.

The final finite read limits are **570s child, 580s panel client, 585s broker
socket and 600s example web worker**. The promotion write remains 110s child /
190s client; generic Hermes and protected-tool limits remain separate.
The actual capped-server application client → fresh Unix broker → current CLI
check **passed: Explorer 499.89s, exact replay 478.98s**, with peak child RSS
**2.37 GiB**. Both requests stayed within the final limits. The exact finding,
statistics, evidence fingerprint and expanded replay references agreed. Peer-UID
authorization, four intent/result audit entries, socket permissions and cleanup
were verified; source, original fixture, working database and verified snapshot
were unchanged. The unit exited successfully and its socket was removed.

This final server run used the existing panel's Python 3.12.3 and read-only
packages (Flask 3.1.1; other core package versions matched the source pins); no
packages or production services were altered for it. The earlier direct
diagnostic used Python 3.11.15. Private homes were inaccessible, networking was
disabled and writes were confined to the isolated test-output directory. An
initial launch stopped before computation because its privacy guard rejected
readable empty home-directory mounts; the successful run made them inaccessible.
The source example web-worker timeout was recorded, not installed. This check
exercises the real bridge client and broker, not a new HTTP login/browser run.

## Additional isolated genuine-Hermes checks

### Initial local-model instance

A separate test installation uses genuine external Hermes 0.18.0, pinned public
source `173514e5234e606a2a5ff39654b4cb733e3a697f`, and a local
Qwen3-4B-Instruct-2507 model served by MLX LM 0.31.3. It is outside this release
tree and adds no product dependency. Newly seeded fictional data and separate
configuration were used; existing installations, accounts and credentials were
not reused. Runtime sandbox checks blocked private-home/keychain access and
non-local networking. Model services were stopped after testing.

| Check | Evidence and limit |
| --- | --- |
| Guided catalog → query → evidence → governed Telegram adapter capture | Passed in 46.59s with four real model requests; correct 8.3h / 7.09h records; health database unchanged. Telegram sending was locally captured. |
| Guided insufficient-data analysis → evidence → capture | Technical path passed in 54.02s. One model sentence wrongly implied fictional vendor labels were live data, so answer quality is qualified. |
| Positive analysis | Real analysis and evidence returned successfully in one attempt; final explanation exceeded the local context limit. A later attempt chose a different date/mode/result count. No complete positive-answer acceptance is claimed. |
| Ordinary question after input-schema repair | Valid catalog/query/evidence calls without custom JSON coaching. Final context was 18,581 tokens, above the tested 16K limit; model selection of latest records also did not fully answer the requested comparison. |
| Larger-context trial | Stopped by the monitor when machine-wide memory pressure became warning. The cause cannot be attributed solely to the model. 20K capacity was not accepted. |
| Actual MCP 1.26.0 / Pydantic 2.13.4 schemas | All 12 tools exported; explicit nested date/reference fields added. Seventeen valid/malformed comparisons preserved validation, handler inputs and errors. |
| Actual Hermes/Telegram SDK callback and reaction extension | Sixteen checks passed. Authorized callback ran the real worker and CLI: 1,850 → 2,100ml on a verified disposable copy; duplicate caused no extra write. Unauthorized events were refused; heart reaction/idempotency passed. |
| Existing action-worker checks in isolated runtime | Nine passed in 1.38s; a separate nine required-delivery tests passed in 0.27s. |

The completed guided model journeys used source `09800f6`. Subsequent tool
guidance/schema changes were checked with all 98 affected tool/delivery tests
and the real SDK; the final ordinary-question and extension checks used source
`4583091`. All 381 copied source files were verified at each recorded version.
Scenario values were not changed or relabelled to force acceptance.

The model server's saved restart settings are 16K input, 1,024 output, one
request at a time and no retained prompt cache. Recorded 16K-era MLX allocations
peaked at 6.96 GB; those counters are not total physical memory. An 8 GiB
allocation guideline is not a hard memory cap. No further local-model inference
ran after that resource stop.

This instance uses the existing adapter source-test mode and relocates receipt
destinations in its staged copy. It does not prove root-owned production
installation, polling/webhooks or Telegram Bot API connectivity. The current
v6 fixture was not relabelled as the separate accepted-v5 Recovery lane.
External Hermes also counts repeated validation errors toward a server circuit
breaker, which can misleadingly report a healthy server as unreachable; that
external behavior was recorded, not patched here.

### Hosted-model follow-up, 16 September

An isolated fictional profile used an existing genuine Hermes 0.18.0 runtime
with a hosted model. It exposed exactly the four generic health tools and the
real delivery plugin. Private home files were hidden; the existing interpreter
and packages were mounted read-only. The application process and its children
shared a verified CPU/memory limit. No model runtime was added to OpenHealthAtlas.

The ordinary questions received the installed generic product skill, with no
hand-authored tool arguments, verified keys, expected answers or extra reference
coaching. Completed cases were:

| Case | Model/tool/delivery time | Verified result |
| --- | --- | --- |
| Sleep comparison | 26.72s | Correct 8.3h versus 7.09h comparison and evidence replay. |
| Insufficient-data analysis | 41.79s | Correctly distinguished insufficient evidence from proof of no association. |
| Positive caffeine/Green-day analysis | 49.35s | Preserved the 28-day mean, two-day lag, effect, confidence interval, sample, missingness, adjusted q-value and exploratory limitations. |
| Follow-up using the prior conversation | 35.98s | Requeried the original dates, identified the fictional source and did not infer a cause. |
| Missing day rating | 20.19s | Reported the absent observation without inventing a rating. |

These times exclude setup and teardown; complete launches took 44.08–73.11s.
The positive case's final model request contained **45,950 prompt tokens**.
Each case completed fresh evidence replay and real Telegram-adapter formatting
into a local capture. Fictional databases remained unchanged. This verifies
selected model/tool/adapter journeys, not Telegram Bot API communication or
the complete inbound gateway/session-storage path.

The first insufficient-data reply exposed a genuine generic-renderer bug:
“would not prove causation” matched the positive causation pattern. The narrow
correction recognizes direct negation while continuing to reject a later
affirmative claim. Its new regression failed before the fix; all **99 affected
tool/MCP/delivery checks passed in 2.77s** afterward. The exact previously rejected
reply also passed a saved-output replay, and a fresh ordinary model run completed.
The specialized Recovery renderer and live integration code were not changed.

An initial positive-case attempt incorrectly selected a legacy shorter test
fixture. That setup failure is preserved separately. The successful rerun used
the original comprehensive fixture after checking its declared bounds; no
records or fixture labels were changed to force a pass. The external runtime's
misleading circuit-breaker response to validation errors was observed again.

The first successful comparison used exact source `4583091`. Later runs used
that verified 381-file export plus only the generic-renderer correction in this
batch. Full source identities, prompts, tool evidence, final answers, failures
and resource/isolation receipts are retained outside the release tree. Raw
model reasoning and operational connection details are excluded from the public
verification record. During those earlier capture-only checks, no live chat
messages were sent.

## Installed corrections and genuine Telegram acceptance

The owner separately authorized a narrow installation update and fictional-only
testing. The installed delivery plugin, advertised MCP schemas and public skill
now match the reviewed source. Exact before/after hashes, ownership, permissions
and rollback copies were checked. The installed tool path passed real SDK
query/evidence and renderer checks without a source-test bypass; the fictional
database was unchanged. The specialized accepted-v5 renderer was retained.

External Hermes 0.18.0 was separately corrected so a normal tool validation
error does not count as a broken connection. **250 canonical Hermes tests
passed**; all five new regressions failed against the original module. On the
installed runtime, four validation errors followed by a corrected request
passed with no connectivity failures counted. Three actual closed-session
errors still triggered the connection breaker and blocked the fourth attempt.
That patch belongs to the external Hermes runtime, not the OpenHealthAtlas tree.

A coordinated gateway restart loaded the corrections and a temporary reserved
message route. Two genuine user-sent private Telegram messages passed the sole
live receiver's authorization and dispatch boundary, then entered a separate
fictional profile using the standard native gateway and persistent session store.
No second receiver was started, and private persona and chat history were
excluded from the isolated test profile.

| Real Telegram turn | Verified result |
| --- | --- |
| Sleep comparison | Fresh query/evidence, correct 8.3h versus 7.09h, sensible limits, governed Bot API reply; native processing 99.66s. |
| Follow-up about source and cause | A separate process reopened the same stored session, queried fresh evidence, identified `synthetic-demo` and did not infer a cause; governed Bot API reply in 38.07s. |

Stored history grew from 0 to 11 to 17 entries across those launches. Both replies
used the configured hosted model and four installed generic tools. Independent
review compared the factual claims to actual tool outputs. The first turn made
one rejected catalog request and repaired it; first-attempt success for every
tool call is not claimed. Sender/receiver bot identity continuity was checked.

This proves the live receiver → isolated native gateway/session → real model
and installed tools → governed Telegram send path. The isolation boundary is
intentional; this does not certify private-history mixing or every Telegram
feature. After both turns, the temporary route was disabled, its late-message
guard retained, temporary credentials removed and test processes stopped.
Fictional database and live credential-store checksums remained unchanged.

The VM was not resized. The current application stack and test children stayed
under the existing shared **one CPU / 8,000,000,000-byte memory maximum**, with
no swap expansion. The operating system uses additional resources outside that
application limit. No model, backup policy or general Telegram setup was changed.

## Source, dependencies and publication history

Current-source sanitation removed private operational locators from ten
historical receipts. Receipt outcomes, chronology, fixture IDs, source commits
and artifact hashes remain; redaction labels are explicit. Historical original
receipt hashes do not attest to redacted bytes. The release manifest does.
Intentional author attribution and third-party notices remain intact.

The final source and exact clean publication history must pass:

```bash
python scripts/release_manifest.py verify
python scripts/release_scan.py --history
git diff --check
```

The existing scanner plus a focused independent receipt/deployment review is a
bounded privacy check, not an exhaustive certification. Private development
history is preserved separately and still contains old private locators. It must
not be pushed as the public release. The uploaded clean history starts with a
parentless source export; subsequent release fixes extend that clean history
without rewriting it. Exact candidate identities and post-commit scans are
recorded separately in the operational handoff.

Fresh dependency audit: **32 resolved packages, no known vulnerabilities**.
Only the temporary environment's pip bootstrap tool required updating, to
26.2.1; project pins are unchanged. Both vendored browser bundles match the
SHA-256 values recorded in `PUBLICATION_DECISION.md`.

## Remaining limits

- Selected genuine hosted-model and real Telegram journeys passed. This does
  not establish broad model faithfulness or every private-session workflow.
  The small local-model results remain qualified as recorded above.
- Cold all-mode analysis and separate legacy evidence replay still take several
  minutes on the capped server. The latest isolated measurements are recorded
  below. Background jobs acknowledge promptly, and unchanged requests reuse
  exact results. Larger windows and simultaneous work can increase wait times;
  generic Hermes tool budgets remain separate from these panel reads.
- The recorded current-source performance measurements used an isolated server
  copy. Deployment of the complete toolkit/panel is tracked separately from
  those measurements; service activation does not imply faster analysis.
- Browser “Stop waiting” aborts waiting; the server read can continue to its
  bounded limit. In-flight completion during service restart is not guaranteed.
- A clean source export was uploaded to a separate private release branch.
  Public visibility and shared history were not changed.

## Disclaimer cleanup follow-up

The owner authorized the wording cleanup, private GitHub upload and deployment.
The 18 repeated browser prose locations now lead to one findable notice in the
existing Settings drawer. Routine Telegram rendering omits its two fixed
medical-disclaimer templates while preserving the machine contracts and all
substantive evidence and consent validation. Public skill guidance also avoids
routine repeated disclaimers; future model phrasing is not guaranteed by that
instruction alone.

Affected checks passed: 116 web, 91 tool/delivery, 54 physio/fitness, and 26
initialization/generic-surface/actual-trace tests. These overlap the earlier
retained-suite coverage. Both saved real Telegram replies replayed with only
the fixed sentence removed; the first reply's existing model-added limitation
was preserved. This offline replay is separate from a new live model run.

The changed screens and notice passed fictional desktop/mobile browser checks;
no controls were removed, no horizontal overflow appeared at 390px, and no
browser errors/warnings were observed. The preview was stopped afterward.
Migration verification separately passed 148 existing fictional-data tests.
Server activation uses verified snapshots and explicit data-preservation checks;
private operational results remain outside the publication tree.

The three controller deployment paths are now configurable through the
server-owned environment. All 33 existing controller tests passed in 1.25s,
including configured-path execution and rejection of an old CLI identity.
Defaults and validation bodies remain unchanged.

## Historical lab-catalog compatibility

The first deployment rehearsal rejected an older migration-001 checksum before
modifying live records. Server-only comparison confirmed the original records
and control files were unchanged, and the original services were restored.

The checksum identifies the exact earlier lab-catalog declaration using
`owner-confirmed`. Migration 007 now accepts that historical declaration only
when the complete schema matches, preserves its ledger entry and every existing
column value, and admits both `owner-confirmed` and `user-confirmed` afterward.
Unknown histories, altered declarations and unsupported dependencies are refused.
Published migrations 001–006 remain unchanged.

Focused verification passed 204 migration/lab tests in 52.26s, followed by 30
focused migration tests in 4.29s after independent review. The existing schema
lanes retain v5/v6 and add v7; 57 readiness/API/tool checks passed in 1.94s.
The operator helper separately passed a fictional legacy health-v4/panel-v1
rehearsal to v7/v2, preserving records, BLOBs, historical ledger entries and
confidence labels. Originals remained unchanged. These are overlapping
selections, not additional tests to add to the retained-suite total.

Ten real scenario traces were regenerated. Independent comparison verified that
inputs, seeds, ranges, expected classifications and narratives stayed unchanged;
only schema-dependent trace seals changed. The analytical engine identity is
unchanged by this compatibility repair.

All **1,861 current retained tests are verified** across these non-overlapping
partitions:

| Selection | Result |
| --- | --- |
| Full non-E2E suite, `--ignore=tests/e2e` | 1,845 passed; one stale version expectation failed; 705.02s |
| Corrected future-version case | 1 passed in 0.20s |
| `tests/e2e/test_autonomous_insight_traces.py` | 14 passed in 160.24s |
| `tests/e2e/test_hermes_green_days_acceptance.py` | 1 passed in 114.17s |

The failed test still treated newly supported version 7 as unsupported. It now
uses the current version plus one (8), preserving rejection of unsupported
versions and acceptance of versions 3–7. Independent review confirmed that no
assertion was weakened. Production code stayed unchanged during verification.
This was not one uninterrupted all-green run. Commands and environment match
the retained-suite setup above; the final conversation journey uses fake
external Hermes. Earlier genuine hosted-model/Telegram checks remain separately
qualified in their own sections.

Final server migration and activation results are recorded outside source after
execution. Only `health.py` performs forward health-database migrations. The
operator verifies consistent server-local snapshots and exact record
preservation before activating the new services.

## Responsive analysis follow-up

The owner requested faster comprehensive analysis and repairs to existing
incomplete workflows. Work started from `4d773d0` (clean deployed export
`a33917ee`). Existing calculation and data-ownership contracts remain intact.

The same complete local fictional analysis measured **180.55s before and
123.94s after**, a 31.4% reduction. Peak memory remained about 2.4 GiB. Every
candidate and 2,000-draw statistical operation was retained. Complete unrounded
results, source evidence and replay matched under a common engine identity;
ordinary code changes legitimately change engine-derived seeds and IDs.
The focused statistics/association/provenance selection passed 112 tests.

New persistent jobs acknowledge browser requests promptly, retain status across
reloads, deduplicate work and recover interrupted attempts under a per-database
OS execution lock. The existing broker sleeps when idle and wakes on submission.
Forty-eight focused job/broker checks passed, including simultaneous submissions,
parent death with a surviving calculation child, restart, queue draining,
timeout, corrupted results and changing inputs. A real small-fixture CLI
submission acknowledged in 0.199s; this is not a comprehensive server timing.

Thirty-eight focused exact-cache/Hermes checks passed. Real separate CLI
processes and actual generic analysis → evidence calls reused the calculation;
forged evidence identities were still rejected. Tests cover same-count and
same-timestamp data changes, WAL, hidden rowids, schema/code/config changes,
malformed cache contents, private modes, lock contention, bounded retention and
read-only compatibility. Operational readiness is recomputed on cache/job
delivery so collector ages can change without repeating statistics.

Six existing workflow defects were repaired and checked: stale region context
after a failed save; incomplete All-time records; misleading activity outcome
labels; unknown/zero/taken supplement semantics; malformed vitals handling; and
supplement product-create validation. The relevant 154 tests passed, with seven
unrelated restock cases deselected, plus 45 conversation tests. These selections
overlap other verification and are not additional retained-suite totals.

Browser/API integration passed 117 selected API/conversation checks. Actual
browser testing with a fictional queued/running/completed fixture verified
Insight Explorer acknowledgement, stopping the wait, reload recovery and final
findings; Dashboard stop/resume/completion also passed. Desktop and 390px phone
layouts showed no horizontal overflow or browser warnings/errors. A native
Safari automation attempt hung before verification; the successful check used
the in-app browser. Temporary viewport/tab/server were cleaned up. This fixture
check demonstrates client behaviour; genuine broker calculation timing is a
separate acceptance check.

The actual ten-scenario generator completed in 131.83s. Independent comparison
verified every scenario input, seed, range, classification and narrative; only
generated engine-dependent seals and their manifest hashes changed.

All **1,941 retained tests passed**, each on its first invocation in these
non-overlapping final partitions:

| Selection | Result |
| --- | --- |
| `python -m pytest -q -p no:cacheprovider --ignore=tests/e2e` | 1,926 passed in 563.92s |
| `tests/e2e/test_autonomous_insight_traces.py` | 14 passed in 126.70s |
| `tests/e2e/test_hermes_green_days_acceptance.py` | 1 passed in 89.97s |

The pinned interpreter, external fail-safe database paths, external temporary
directories and socket-enabled execution follow the earlier retained-suite
setup. Runtime and test source hashes were unchanged across the suite; only
the root-owned verification document and release manifest changed concurrently.
This is complete partitioned coverage. Capped-server measurements and release
activation are recorded separately after execution.

### Post-checkpoint polling and native acceleration

The first capped Explorer job completed in about 465.14s. The harness then
failed a readiness comparison because it used raw Python floats against the
CLI's existing canonical JSON representation. The exact same result has zero
differences under the actual transport contract. Source/fictional data stayed
unchanged; owned processes and sockets were cleaned up, with no OOM events.
The failed attempt is retained and does not count as a complete server pass.

Pending job polls now avoid loading a health snapshot or scanning its identity;
they disclose only validated queue state and request scope. Completed results
retain every freshness, integrity and readiness check. Polling waits five
seconds between status requests. The 158 relevant job/broker/API/cache checks
passed after this bounded follow-up.

An explicitly configured optional native kernel subsequently reduced a paired
complete local calculation from **131.80s to 80.96s**. Complete unrounded and
canonical output equality passed with a common engine identity. All 3,755
permutation candidates and 7,510,000 draws were retained; actual native selection
was instrumented. Peak RSS was 2.26/2.30 GiB. These timings cover the calculation
after imports/registry setup, not complete browser requests or server timings.

The native/stat selection passed 76 tests, and the native-enabled engine/evidence
selection passed 98. These overlap. Independent review found no remaining
actionable numerical/bounds issue. Normal Python operation needs no compiler or
library, and rejected/unavailable libraries fall back. The library is trusted
operator-installed code; validation does not sandbox malicious native binaries.
The matching package loader and C source are now part of numerical, generic and
cache identities. Complete regression results and linked capped-server
acceptance for this final numerical source are recorded below.

The final native-aware source passed **all 1,973 retained tests**, with no
failures or skips on the first invocation of each disjoint partition:

| Selection | Result |
| --- | --- |
| Full non-E2E suite | 1,958 passed in 622.17s |
| Actual trace replay | 14 passed in 135.46s |
| Real panel/broker/fake-Hermes journey | 1 passed in 99.48s |

Global native selection was unset so normal Python remains covered. The
dedicated native tests used the explicitly validated external library; its
hash remained unchanged. The actual ten-scenario generator passed in 135.83s,
and independent comparison preserved every non-seal scenario field. Runtime
and test hashes stayed fixed; concurrent changes were documentation/manifest
only. Server measurements and activation remain separate evidence.

### Linked capped-server acceptance

The final numerical source passed isolated fictional-data acceptance under the
existing shared one-CPU, 8,000,000,000-byte memory limit with unchanged no-swap
settings. The optional native kernel was active. Timings distinguish the initial
job acknowledgement from the complete cold calculation and subsequent exact
reuse:

| Operation | Acknowledgement | Result/retrieval time |
| --- | --- | --- |
| Cold all-mode Explorer job | 0.95s | 282.22s |
| Completed Explorer job retrieval | — | 4.92s |
| Exact Explorer CLI repeat | — | 4.55s |
| Separate cold legacy evidence job | 0.51s | 266.25s |
| Exact legacy evidence repeat | — | 0.86s |

The legacy evidence calculation uses its distinct request/cache key. Its result
and subsequent reuse were verified. Source and fictional data remained
unchanged, peak measured memory was 2,717,057,024 bytes (2.72 GB), no OOM event
occurred, and all owned test descendants were gone after cleanup. CPU, memory
and no-swap limits were unchanged.

These are linked acceptance records: a validated cold Explorer result was
retained and a continuation completed the remaining checks. This was not one
uninterrupted run; the original failed receipt is preserved separately. That
native attempt stopped because the test harness blocked SQLite temporary
storage. The continuation configured private temporary directories before
process startup; product source was unchanged. This is separate from the earlier
465.14s run's readiness-comparison failure. Detailed receipts remain outside
source. These measurements used an isolated server copy; activation of the
canonical installation is a separate operational record.

## Standalone local MCP extension

The standalone connection reuses the deterministic engine with explicit
per-instance database, timezone and dataset identity. It exposes read-only
catalog/query tools and bounded background analysis/evidence tools over stdio.
The existing fictional Hermes MCP, delivery plugin and Telegram entry points
are unchanged. No database migration or model runtime was added.

Side-by-side verification against the prior source passed 14 complete legacy
envelope comparisons under a common generic engine identity, 14 local-surface
semantic comparisons and 12 uncached, full unrounded analytical comparisons.
Catalog, all four query views, positive/insufficient analysis and lineage
replay were exercised. Fictional database bytes were unchanged. Three focused
local-instance regressions cover concurrent database/cache isolation,
cross-instance evidence refusal, legacy compatibility, URI-special filenames
and explicit timezone binding without changing process settings.

The optional MCP 1.30.0 SDK environment passed five actual client/server
subprocess tests in 4.36s. These cover tool discovery, queries, successful and
insufficient analyses, evidence verification, malformed requests and task
refusals, database errors, missing-file startup, pending-request deduplication,
busy responses, disconnect cleanup and bounded oversized worker output.
Numerical text and structured results use the existing canonical serializer.
Worker execution is bounded independently of the parent process; completed
task retention is bounded and expires.

The resolved optional dependency audit covered 50 packages with zero known
vulnerabilities at verification time. License metadata was inspected; the
official MCP SDK is MIT-licensed and remains an optional installed dependency.
Base dependency pins are unchanged.

The actual ten-scenario generator passed in 127.54s. Every fixture, seal and
fixture-manifest byte remained identical. The numerical engine identity is
unchanged; the generic/local evidence identities include their changed
interface source. These protocol checks establish a working standard MCP
connection, not the answer quality of every model or client application.

All **1,981 retained tests passed**, with no failures or skips, in disjoint
partitions:

| Partition | Passed | Time |
| --- | ---: | ---: |
| Base non-E2E suite, including the three local-instance regressions | 1,961 | 584.69s |
| Actual trace replay | 14 | 130.02s |
| Real panel/broker/fake-Hermes journey | 1 | 94.10s |
| Actual optional MCP SDK subprocess tests | 5 | 4.36s |

The base and E2E partitions used the unchanged pinned development environment.
The optional SDK partition used its separate environment. Its initial 4.41s
run passed; the five tests then passed again in 4.36s after replacing a
location-specific test/example timezone with a neutral example. Runtime code
and dependency pins were unchanged. Full collection with the SDK installed confirmed that
all 1,981 nodes are present and the four partitions cover each node once.
Runtime and test bytes stayed frozen during their respective checks. Later
changes were documentation and release-manifest updates only.

## Initial public MIT source release

The owner authorized the new `kajeesan/Open-Health-Atlas` repository, MIT
licensing for original project code and creator attribution to Kajeesan
Jeevendra. Separate reviewers compared the complete 397-file source inventory
with the final passing MCP checkpoint. Changes are confined to documentation,
the original-code license and the regenerated release manifest. Runtime,
tests, fixtures, dependency declarations and all vendor bytes are unchanged.

The standard MIT text and copyright notice were checked against the canonical
license. Third-party Apache/BSD/MIT notices remain intact. Current source
privacy checks report zero findings. The public copy is initialized from one
clean source snapshot, with no inherited Git parents; its full manifest and
history are checked before publication. These release checks supplement the
existing 1,981 passing tests without rerunning them for metadata changes.

## Repository onboarding checks

The repository/community batch adds documentation, a reviewed fictional UI
screenshot, community templates and GitHub Actions. Application/toolkit code,
dependencies and vendor files remain unchanged. The privacy scanner changes
are isolated to reviewed asset handling and complete historical path checking;
two distinct regressions increase the retained inventory to 1,983 tests.

Local verification used a fresh isolated Python 3.14.6 environment. Dependency
installation and `pip check` passed. Thirteen existing initialization,
scripted-demo and actual MCP subprocess tests passed in 96.88s, including a
successful full-range analysis and verified evidence replay. Separate execution
of the documented initialization, schema check, fixed-anchor demo generation
and scripted demonstration passed. The scripted flow used no external service
or model and returned successful authenticated dashboard/metrics checks.

The current local fictional dashboard was opened in a browser, its analysis
completed, and the All-time filter was selected through the UI. The screenshot
and its exact hash are documented in `docs/SCREENSHOTS.md`. No image pixels or
numerical results were fabricated. The public hosted preview was checked
separately and remains labelled as an older read-only snapshot.

Seven scanner regressions passed locally, including exact screenshot identity,
mutated/relocated rejection and a deleted historical copy at an unapproved
path. Community YAML and Markdown structure, workflow syntax, routing cases,
guide links and copyable configuration were independently checked. These are
internal engineering checks, not external-user feedback. Actual GitHub/Linux
workflow results follow below.

The [first GitHub/Linux run](https://github.com/kajeesan/Open-Health-Atlas/actions/runs/35154303321)
passed inventory/privacy checks and 1,941 application tests, with 22 failures
in 1,405.84s. Eleven installer tests rejected the runner's non-system temporary
directory; eleven date tests exposed collection-time midnight staleness or a
runner/application timezone mismatch. Later E2E/MCP steps were skipped after
that failure, so this run is not a passing full-suite result.

The correction uses a unique system temporary directory and matching example
test timezones. Existing profile tests now read the date at test execution;
journal and timing expectations use the configured civil timezone, and the
workout fixture converts its local time correctly across seasons. Assertions,
installer guards and production date handling are retained. Twenty-three
installer tests passed in 13.70s; 33 date-related tests passed in 13.89s with
the host clock set to UTC and the application set to the example timezone.
These checks address the observed failures; the complete GitHub rerun below
verifies the corrected candidate. Individual live-clock tests can still race if their own
execution crosses midnight.

### Passing hosted verification and publication

[GitHub run 35156999775](https://github.com/kajeesan/Open-Health-Atlas/actions/runs/35156999775)
passed at `6be7d75656bf0a7a34ba8c1b7d2ea1936189dfa2` on Ubuntu 24.04 and
Python 3.11. The disjoint partitions passed all **1,983 retained tests**, with
no test failures or skips:

| Partition | Passed | Time |
| --- | ---: | ---: |
| Application, including scanner, native and JavaScript checks | 1,963 | 1,392.76s |
| Fictional end-to-end journeys | 15 | 608.92s |
| Actual optional MCP client/server | 5 | 9.50s |

Dependency consistency, MCP SDK 1.30.0 import, the 406-entry manifest and full
history privacy scan passed. The source scan covered 407 files and reported
zero findings. Test-data and job cleanup completed successfully. Workflow
steps for the alternative documentation-only route were intentionally not
selected; they are not skipped application tests.

[Pull request #3](https://github.com/kajeesan/Open-Health-Atlas/pull/3) was
merged to public `main` at that exact reviewed commit. GitHub recognized the
conduct policy, contribution guide, PR template and MIT license and reported
100% on its community-file checklist. Its signed-in issue chooser displayed
both Bug report and Feature request forms and the private reporting route.
This measures repository-file presence, not product quality or adoption.

The two bounded starter issues are [persistent form labels](https://github.com/kajeesan/Open-Health-Atlas/issues/1)
and [selected export-format state](https://github.com/kajeesan/Open-Health-Atlas/issues/2).
Both are unassigned and labelled `good first issue` and `accessibility`.
Private vulnerability reporting remains enabled. No external feedback was
collected and no people were contacted or assigned. The final follow-up only
records these results and adds the live workflow badge; runtime and tests
remain the verified bytes above.
