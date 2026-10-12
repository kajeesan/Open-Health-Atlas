# System design

## Design principles

OpenHealthAtlas is built around explicit contracts rather than implicit interpretation:

- Preserve raw/source identity and provenance before deriving features.
- Separate event time, effective time, ingestion time, and analysis time.
- Fail closed when evidence, units, identities, coverage, or ancestry cannot be
  resolved.
- Keep deterministic observation, model inference, and general knowledge
  visibly distinct.
- Treat associations as bounded evidence, never as causal conclusions.
- Make retries, idempotency, terminal outcomes, and persistence identities
  observable and testable.
- Keep private state and configuration outside product source.

## Deterministic pipeline

1. **Capture/import.** Validated CLI commands and generic collectors append
   source records with source identity and ingestion metadata.
2. **Normalize.** Adapters map records into registered feature identities while
   preserving units, timing, laterality, and original provenance.
3. **Frame.** The feature-frame builder resolves bounded date ranges,
   completeness, aliases, transformations, and data-quality states.
4. **Assess readiness.** Each feature is classified as sufficient,
   implemented-but-never-logged, structurally unavailable, or explicitly
   `logic_not_implemented`.
5. **Analyze.** Statistical routines compute descriptive values and bounded
   association contrasts with explicit sample gates, lags, rolling windows,
   false-discovery metadata, stability checks, and pairwise interaction cells.
6. **Ledger.** Findings and hypotheses retain immutable fingerprints,
   supporting/contradicting evidence, range ancestry, and state transitions.
7. **Synthesize/orchestrate.** Deterministic novelty rules create synthesis,
   trigger, outbox, and notification records without silently sending through
   a live external channel.

The deterministic registry defines the retained feature families and their
unit, timing, source and readiness contracts. The separate synthetic
interpretation taxonomy has been retired.

## Time and identity

OpenHealthAtlas does not use one timestamp for every purpose. Records expose the timing
available from the source, and downstream contracts specify which clock is
authoritative. Date-range boundaries are inclusive and timezone-aware. The
deployment timezone is configured through `HERMES_TIMEZONE` and defaults to
`UTC`.

Persisted feature and evidence identities are content-derived or explicitly
registered. Aliases resolve through a controlled mapping. External IDs remain
external configuration; synthetic fixtures use fictional identities.

## Outcomes and comparisons

The deterministic association layer supports all-data, ordinal,
Green-vs-non-Green, and Red-vs-non-Red analysis, bounded lags, rolling windows,
point/mean/sum/count/delta/frequency/days-since transforms, and no-interaction
or pairwise-interaction modes. Yellow observations remain Yellow; binary views
group them without relabelling them.

HRV dashboard comparisons use the latest observed source and its measurement
method. Apple SDNN and Fitbit RMSSD retain separate series; Apple wins a
same-date source tie. Baselines require seven observations from that source
within the trailing 60 days. Unknown methods remain visible as observations
without a baseline or directional status.

Fitness observations with missing setup descriptions remain available for
inspection but cannot qualify as analytical evidence. Explicit bodyweight or
no-equipment descriptions are valid protocols. Multiple recorded protocols
within a feature's requested range also prevent pooled comparison. Readiness
counts and `latest_at` use eligible observations; descriptive record counts
remain available with exclusion reasons. Existing eligibility rules also
exclude incomplete nutrition totals from analytical freshness. Qualification checks recorded setup
consistency, not whether a free-text description is accurate.

## Descriptive observations

`descriptive.py` returns observed values and arithmetic comparisons for
registered numeric features, including ranges below the association minimum
of 30 aligned units. It does not run statistical tests, assign confidence or
produce ledger-eligible hypotheses. Formal association gates remain unchanged.

Each feature retains its own observed-day and observed-unit counts, missing
days, units and source identities. Each exposure comparison uses its own
paired denominator; an unrelated empty feature does not remove other pairs.
Missing days remain unknown. A zero is usable only when the canonical frame
supplies it through an observation or its completeness contract.

Period comparisons split the requested calendar range into chronological
halves. Exposure contrasts use a requested lag, defaulting to one day, and
reuse the association engine's alignment and lineage restrictions. The
supported lags are 0, 1, 2, 3 and 7 days. Source-incompatible records retain
separate summaries and `source_comparisons`, with no pooled mean difference.
The response shows up to eight homogeneous outcome/exposure source pairs,
ordered by paired count, and reports the total and shown counts. Each retains
its own paired and group denominators, source parents and comparison limits.
A source pair with only one observation cannot estimate a contrast; separate
source comparisons are not independent corroboration.

Slow fitness measurements and derived features require matching, nonempty
canonical equipment-protocol identities before summary or contrast arithmetic.
Missing or incompatible protocols block that arithmetic even within one source;
recorded observations remain inspectable. Records the analytical layer marks
ineligible only for `missing_protocol` or `mixed_protocols` stay in descriptive
units, where this check reports them; other exclusions still apply.

Known logged nutrition values without confirmed full-day coverage remain
visible in `partial_recorded_facts`, with `value_scope: recorded_subtotal` and
`completeness: full_day_coverage_unconfirmed`. They retain source identities
but are excluded from comparison inputs and eligible daily-unit counts. A
recorded subtotal is not a full-day total.

Contrasts show group counts, arithmetic differences and counterexample counts.
`no_clear_pattern` means equal observed means for that contrast, not evidence
that no relationship exists. Shared lineage and source rows are explicitly
dependent evidence; these summaries do not adjust for temporal dependence.
Displayed samples contain at most the first six and last six units per
summary or comparison; the input fingerprint covers every loaded observation.

## Training evidence and reported context

`training_evidence.py` assembles bounded training records, logged exposure,
fitness comparisons, discomfort, recovery and explicitly reported context.
It preserves parent identities, dates, classifications, coverage and
conflicting evidence. Supporting fitness parents can precede the requested
range and are labelled as lookback evidence. Same-day order remains unknown
when records provide only civil dates; ingestion time is not event time.

Training windows and pre-symptom contrasts describe logged sets, not total
physical activity. Missing training dates remain unknown unless a valid
explicit-none capture or source-sync interval proves an empty day. Exercise
categories and e1RM are estimates; they do not measure performed technique,
joint angles or isolated muscle force. Imported notes remain untrusted data.

`training_context.py` stores explicit answers, skips and corrections as
immutable revisions. Answers attach to an exercise template, fitness test,
pain record or personal history, with inclusive effective dates. A repeated
capture ID must contain the same payload. A correction must retain its
subject and context field; it supersedes prior answers only within its
effective interval. The resolved context on each dated record is authoritative,
while superseded rows remain audit history. A skipped answer remains unknown.

The packet proposes missing-context questions after reading saved context.
Its conversation policy defaults to one question per turn, without a fixed
overall question cap. External clients own follow-up selection, hypotheses and
explanation. Packet retrieval does not invoke a model or establish causation.

## Tested strength comparisons

Tested opposing-muscle ratios and left/right gaps retain raw values, parent
dates, source identities and comparison limitations. They do not establish
adequate absolute strength or explain pain. Gym-load e1RM has no validated
population reference in this implementation; tested target bands and
diagnostic asymmetry cutoffs are not applied.

Where a cited study reports one, a ratio carries a `reference` with the
published typical value, optional range, an evidence grade (`strong`,
`moderate` or `rough_estimate`) and how the study measured it. Each side
states `reference_position` relative to that range. The reference is research
context for display and conversation (`reference_use: research_context_only`):
it does not set `ideal` or `band`, change flags or colors, or create
distance-to-band observations. Most references come from dynamometer torque
or derived ratios, not gym-machine loads, so they are not personal targets.

Comparison eligibility requires recorded setup, matching measurement dates and
explicitly non-pain-limited effort; strength tests also retain their repetition
protocol gate. Missing setup, different setups, mixed sessions, missing sides
and zero denominators remain visible where applicable. Available tested
ratios and recorded limbs color the body balance lens as `trend_only`; an
unrecorded side remains `untested` even when the other side has a value.
Shared body regions are display projections,
not independent muscle measurements.

Same-exercise progress uses a prior eligible session within 120 days, with
matching reported setup, side and notes. A recorded load change without that
context remains visible but is not an eligible personal-baseline comparison.
Reported setup does not verify technique. The separate everyday movement-pattern
view retains its labelled heuristic behavior and does not color the body lens.

## Lateral-knee event semantics

The lateral-knee registry entries implement NRS severity, reported onset,
first observed positive and observed resolution for central, left and right
records. Each region/side is a distinct identity. The last non-voided record
by ID wins within a date and identity.

Reported onset retains its exact or approximate precision and cannot follow
its report date. First observed positive refers to the earliest retained
positive observation, not a newly inferred onset. Resolution requires an
explicit NRS zero after an unresolved positive report. Missing reports neither
resolve an episode nor establish a pain-free day.

Absent event markers become structural zeros only under effective capture
completeness for the exact pain region and side. General pain completeness
does not fill a lateral-knee identity. Even exact completeness does not invent
a zero NRS value. These event definitions do not implement a longitudinal
asymmetry-to-pain model; see [U-12](../UNIMPLEMENTED.md#u-12--quarterly-asymmetry-to-pain-episode-analysis).

## External interpretation boundary

Genuine external Hermes owns conversation and model reasoning. OpenHealthAtlas
supplies deterministic records, findings, provenance and validated evidence
through its governed tools. `synthesis-prepare` assembles ledger evidence without
invoking a model. Optional `synthesis-v1` storage retains validated external
provider/model identities and evidence ancestry; write-back remains disabled by
default in the accepted integration.

The unused local synthetic interpretation runtime, its taxonomy, injected
endpoints and exclusive tests are retired. Their removal preserves shared
engine contracts, deterministic calculations and every existing wired UI
control. See [PRODUCT_AUTHORITY.md](PRODUCT_AUTHORITY.md) and
[../UNIMPLEMENTED.md](../UNIMPLEMENTED.md).

## Failure model

Validation failures use stable error codes. Database migrations preflight exact
shape and checksums inside transactions, then run foreign-key and quick checks.
Durable analysis and synthesis paths validate evidence ancestry and record
explicit completion or failure states. Conversation delivery keeps uncertain
turns distinct from completed replies and does not automatically resend them.

The broker similarly rejects unknown operations, malformed payloads, invalid
numeric ranges, cross-user identity attempts, disallowed paths, and duplicate
or conflicting idempotency requests before invoking a write.

## Product boundary

OpenHealthAtlas owns product code, schemas, validation, analysis, UI, and generic
integration contracts. Users own records, accounts, service endpoints,
credentials, provider terms, backups, deployment hardening, and clinical
decisions. A configured external service is not bundled OpenHealthAtlas functionality;
an absent internal runtime component is recorded as unimplemented.
