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
