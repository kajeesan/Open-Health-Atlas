# Health toolkit modularization plan

This is the authoritative plan for the approved toolkit modularization project.
It preserves the original architecture, milestone order and acceptance criteria.
The owner updated the continuation policy on 22 September 2026: proceed between
verified milestones through fresh main tasks, without routine approval to continue.

Use [Refactoring](REFACTORING.md#current-toolkit-project) for current progress and
dated evidence. Use [Development](DEVELOPMENT.md) for contributor conventions and
[Testing](TESTING.md) for commands and prerequisites. Historical audit plans are
reference material, not an alternative current plan.

## Objective and preserved contracts

Make the toolkit easier to understand, review, change and extend while retaining
its interfaces and data behavior. Keep OpenHealthAtlas responsible for canonical
records, deterministic calculations, evidence and validated writes. External
clients retain interpretation and conversation authority.

Preserve the executable path `toolkit/health.py`, command names, arguments,
defaults, output formats, error codes, database compatibility, transaction
ownership, validated-write restrictions and desktop/integration behavior.
Preserve every retained wired workflow. The retired local synthetic interpretation
subsystem and its exclusive tests must not return.

## Approved architecture

Extract beneath `toolkit/hermes_insights/`, which the desktop builder includes.

| Responsibility | Target owner |
|---|---|
| Stable executable and necessary compatibility exports | Small `health.py` facade |
| Argument parsing, explicit registration, dispatch and error formatting | CLI module |
| Validation and workflow coordination | Domain-specific command modules |
| Provider/file parsing and normalization | Source-specific importer modules |
| Domain calculations and queries | Cohesive domain modules |
| Shared numerical formulas and existing analytical engines | Existing owning modules |

Commands call domain/importer functions and existing engines. Domain functions
receive connections, clocks and configuration explicitly where required.
Shared calculations do not open databases, print, parse CLI arguments or import
the CLI. New domain modules must never import `health.py`.

Introduce a small command context carrying database location, clock/timezone,
vault location and stable CLI launch path. Preserve existing transaction ownership
and data-boundary wrappers. Use explicit command registration; do not introduce
dynamic plugin discovery, a dependency-injection framework or a service container.

Unconverted handlers and limited compatibility wrappers may remain temporarily.
Move internal tests from mutable CLI globals toward the owning module's explicit
dependencies as those consumers are converted. Keep numerical corrections separate
from extraction. Do not refactor ledger or synthesis internals.

## Milestone sequence

### Milestone 1: conventions and corrected baseline

Adapt the reviewed writing extensions to public source, preserve development
setup, place authoritative knowledge in contributor documentation and retain
short agent pointers. Update obsolete module/test references, keep generated
indexes and temporary reports outside tracked source, and avoid blanket Markdown
ignores or a formatting rewrite.

Reproduce and correct future records affecting recovery/nutrition, cancellation
at inclusive association/interaction thresholds, extreme valid Wilson confidence
and finite-endpoint percentile overflow. Add only relevant regression cases.
Record the corrected revision, independent review, verification and representative
timings before structural extraction. Completion evidence is in the dated
[corrected baseline](REFACTORING.md#corrected-toolkit-baseline-22-september-2026).

### Milestone 2: prove the extraction pattern

Extract shared CLI plumbing and the Hevy CSV importer. Preserve all retained
command behavior while unconverted handlers remain behind temporary compatibility
boundaries. Verify the extraction through the source CLI, broker and isolated
packaged desktop application. Compare outputs and database effects against the
corrected baseline, then document ownership and the next bounded extraction.

The incoming main owns shared registration, compatibility wiring, identity
inventories and release packaging changes. One implementation writer owns the
overlapping `health.py`, new CLI/context/importer modules and directly affected
tests. Settle exact module names after tracing current callers. Other Hevy formats
and command families remain for Milestone 3.

### Milestone 3: remaining command families

Treat each family below as a substantial milestone with its own implementation,
verification, independent review, commit and fresh-main handoff. Keep this order:

1. Remaining Hevy imports, Cronometer, Google Health, recipes and catalog imports.
2. Daily logging, hydration, commitments, schedules, notes and collectors.
3. Nutrition, profiles, targets and food workflows.
4. Training, fitness tests, muscle calculations, pain and mobility.
5. Daily frames, scores, recovery/readiness and laboratory workflows.
6. CLI coordination around existing feature, association, ledger, synthesis and
   orchestration engines. Their internal redesign remains excluded.

### Milestone 4: final acceptance

Remove obsolete compatibility code, complete documentation, verify the integrated
application and compare final performance with the corrected baseline. Close the
project with an evidence report; do not spawn an unrelated successor or infer
publication authority from completion.

## Integration hazards

- `hermes_surface._engine_manifest` uses an explicit inventory: register new
  extracted runtime modules that belong to its scope.
- `exact_cache.current_identity` recursively hashes toolkit Python source.
  Preserve its invalidation behavior.
- `provenance.engine_manifest` has a narrower numerical scope. Keep these three
  identities distinct instead of replacing them with one generic manifest.
- Code changes can alter fingerprints, resampling seeds and existing finding-ID
  tie-breaks. Compare numerical outputs, selections and database effects separately.
  Carry forward the concrete [Milestone 1 caveat](REFACTORING.md#corrected-toolkit-baseline-22-september-2026);
  never summarize every changed result as hash metadata alone.
- Desktop packaging selects approved directories and release-manifest entries.
  Track intended new files before regenerating the inventory. Verify imports from
  an isolated packaged layout, not only the checkout.
- Preserve launch paths consumed by desktop workspaces, brokers and analysis jobs.
- `tests/test_bridge.py` parses the literal `LOGGABLE` assignment. Replace this
  source-layout dependency with behavioral verification when moving that contract.
- Preserve vendor-byte and license checks. Historical recovery staging manifests
  are not the general deployment mechanism; do not update or activate them mechanically.

## Verification and acceptance

Use fictional records, temporary databases, controlled clocks and isolated output
directories. Reuse the full fictional persona, green-day scenarios and
existing focused fixtures. Add tests for demonstrated regressions or meaningful
gaps; do not create a duplicate suite or a blanket 50-record-per-type expansion.

Follow the current application, end-to-end and actual MCP partitions in
[Testing](TESTING.md#auditable-partitions). Root discovery already includes toolkit
tests. Complete verification requires the optional SDK, Node and native library.
Match CI timezone settings and installer-safe temporary directories.

Compare baseline and candidate behavior on the same inputs and clocks, including
applicable invalid input, missing data, units, time boundaries, repeated imports,
failed transactions and unauthorized writes. Verify source CLI, broker and packaged
paths when affected. Retain privacy/history scans, release-inventory verification,
required hosted checks and branch protections. Hosted checks remain a publication/
merge gate; lack of push authority does not prevent verified local continuation.

Freeze relevant source during verification. Reports identify the revision, exact
command, exit status, pass/fail/skip counts, duration and log location. Distinguish
cross-revision evidence, focused reruns and expected identity changes. Investigate
unexpected failures and skips; never turn incomplete checks into a success claim.
Measure representative performance and investigate unexplained regressions. Plan
optimizations separately.

Final acceptance requires:

- `health.py` contains launch and limited compatibility plumbing, without importer,
  SQL or calculation implementations.
- Every retained command has a clear owner; calculations have one authoritative
  implementation.
- Existing workflows, data protections and integration contracts remain functional.
- Desktop packages contain and load extracted modules.
- Contributors can locate each calculation, its contract, callers and tests without
  chat history.
- Performance comparisons show no unexplained regression, and the branch has
  accurate documentation and reviewable evidence.

These are evidence gates, not a guarantee of perfection. Delivery remains reversible
and independently reviewed.

## Roles and ownership

Keep the main orchestrator and independent reviewer on `gpt-6-astra` with `xhigh`
reasoning. Successor mains preserve this configuration; report unavailability
instead of silently changing it. Implementation uses the configured main model.
A lower-cost runner may execute fixed checks, not make correctness decisions.

The main owns scope, sequencing, integration, evidence assessment and reporting.
Assign each worker a base revision, bounded objective, exact files/responsibility,
preserved contracts, checks and completion criteria. Workers preserve others'
changes and know they are sharing the checkout.

Use one production writer while files overlap `health.py`. At most two writers
may work later on disjoint modules with settled interfaces. One integration owner
controls registration, fingerprints, release inventory and workflows. Verification
helpers cannot weaken tests or silently repair failures. Reviewers cannot approve
their own implementation. Optional grandchildren perform bounded discovery or
checks without further delegation. Reuse agents rather than filling slots.

Apply Software Writer 2.2.0, its current project extensions, Extension Setup 2.1.0
when configuration needs adaptation, and Sentry code-review for independent review.
Use the prose helper only for narrow documentation wording. Follow Flask, SQLite,
plain JavaScript and desktop conventions, not unrelated framework assumptions.

## Authorization and exclusions

The owner authorizes local implementation, feature-branch commits, reviewable PR
preparation and successive fresh-main tasks through this plan. After a milestone's
verification and independent-review gates pass, continue locally to the next
milestone without a routine owner approval or an intervening merge. This policy
supersedes the earlier stop-for-owner-review requirement before starting the next
milestone, including Milestone 1 to Milestone 2.

Pushes, merges, release publication, credentials, private health data and live
integrations retain their separate authorization boundaries. No new permission
for those actions is granted. Do not commit to main, force-push, bypass protections,
accept legal terms, access the owner's private health directory or import private
Git history. Preserve public licensing, attribution and sanitized commit identity.
The older audit checkout is a read-only reference, never a merge/cherry-pick source.

Defer ledger/synthesis internals, route/frontend cleanup, Renovate, lint/formatting
expansion and ChunkHound. Clipboard Copy remains excluded. Do not introduce health
data RAG, caching, background analysis or AI workflow changes. Downloadable installer
publication is a separate release task.

Resolve ordinary in-scope failures autonomously. Pause for an unresolved correctness
or safety problem, material scope/architecture change, or an action outside these
boundaries. Report the concrete problem and completed evidence rather than asking
for routine permission to continue.

## Fresh-main relay

At each substantial milestone, the outgoing main:

1. Finishes the bounded implementation, relevant tests and independent review;
   resolves blockers and commits coherent changes on the feature branch.
2. Updates the current checkpoint in `docs/REFACTORING.md` and writes a concise
   handoff outside tracked source. Keep private local paths, task identifiers,
   temporary logs and machine-specific receipts in that local handoff.
3. Records the exact checkout path, remote, branch and final commit; this plan's
   path and revision; completed work; decisions and reasons; exact verification
   evidence; unresolved issues and inherited caveats; and the next bounded scope,
   ownership and acceptance criteria. Include these same relay requirements.
4. Creates one fresh main task on the same model/reasoning configuration with the
   complete handoff. Use the intended checkout directly; if it is not a saved
   project, use a projectless task with its explicit path. Never select a similarly
   named audit project. Do not reset to main or discard accumulated local commits.
5. Stops repository editing on dispatch. Confirms that the successor has actually
   started, verified the checkpoint and explicitly accepted ownership. Creation
   alone is not delivery. Record its acceptance in the external handoff receipt.
   If startup fails, inspect that task and resolve the cause before creating any
   replacement; never leave competing production owners.
6. Gives the owner a concise completion/progress summary without a routine
   continuation question. Then leaves implementation to the successor.

The incoming main reads `AGENTS.md`, the project extensions, this plan, current
checkpoint and local handoff. Before editing it verifies the checkout, remote,
branch, commit, worktree status and relevant evidence. It preserves unrelated
changes and explicitly acknowledges the inherited caveats and ownership transfer.
It then executes its assigned milestone, not another orchestrator-setup task.
After passing that milestone's gates it performs the same relay for its successor.

No new orchestration framework, scheduler or duplicate task chain is required.
Use the existing task and subagent tools. Retain local evidence access across
handoffs; a summary or passing count alone is insufficient context.
