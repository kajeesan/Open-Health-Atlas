# Hermes OpenHealthAtlas evidence workflow

Use the `openhealthatlas_health_*` MCP tools for open-ended questions about the
fictional health record. OpenHealthAtlas discovers registered features,
performs bounded reads and deterministic calculations, and returns structured
evidence. Hermes chooses the calls, asks follow-ups, combines results, evaluates
plausible explanations, and writes the conversational answer. Never make
OpenHealthAtlas perform model reasoning and never replace Hermes reasoning with
deterministic prose.

The installation is bound by a root-owned configuration to one named fictional
database and date range. It refuses owner data, unbounded ranges, arbitrary
SQL, table names, column names, database or vault paths, writes, and environment
path overrides. Interpretation write-back remains disabled. All observations
in this installation are fictional, including those labelled Apple or Fitbit.

## Open-ended fictional health questions

The four tools are composable capabilities, not a whitelist of supported user
questions:

1. Call `openhealthatlas_health_catalog` when the necessary feature keys are
   unknown. Search or filter the registry by domain, role, and availability;
   follow its bounded cursor instead of requesting the complete registry.
2. Call `openhealthatlas_health_query` with one to 32 returned feature keys and
   a date range inside the approved fictional fixture. Choose `latest`,
   `series`, `summary`, or `period_compare` according to the question. Keep the
   returned missingness, coverage, units, source identity, and limitations.
3. Call `openhealthatlas_health_analyze` only when the question requires a
   deterministic relationship or interaction calculation. Supply one outcome
   plus one to 32 explicit exposure keys returned by the catalog.
   An observed association alone does not establish a cause.
4. Finish a broad-question turn with `openhealthatlas_health_evidence`. Pass at
   most 20 complete objects from the earlier results' `evidence_refs`. Preserve
   `contract`, `operation`, `arguments` and `result_id`, plus `finding_id` when
   present; copy nested arguments unchanged. It recomputes and verifies the bounded evidence without relying
   on hidden subprocess state. A bundle may contain at most one reference with
   `operation: health_analyze`: choose its overall reference or one finding-specific
   reference. The bundle must stay within the returned work budget; split an oversized question
   into a follow-up turn. Do not invent, duplicate, or edit a reference.

Calling any generic health tool creates a same-turn delivery obligation. The
gateway will fail closed unless the final `openhealthatlas_health_evidence`
completion satisfies that obligation. The server-owned disclosure renders a
bounded deterministic summary before any labelled Hermes interpretation.
Hermes may naturally reference, round, compare, and combine numbers and dates
from the supplied evidence. The gateway renders the authoritative deterministic
section first; the following prose is explicitly labelled model-generated and
must not be presented as the deterministic record. Do not introduce a new
numeric, date, wording, or exact-line restriction without explicit owner
approval.

Every result uses `ok`, `insufficient_data`, `unsupported`, or `refused`.
`insufficient_data` and `unsupported` are useful deterministic answers: state
the limitation and ask the smallest follow-up that could change it. Never infer
that code presence, registry membership, or a passing test proves a user-level
health conclusion.

The final reply must visibly separate:

## Deterministic result

Report only returned values, coverage, missingness, calculations, limitations,
and evidence identity. Preserve units, range, feature keys, and uncertainty.

## Hermes interpretation

Explain what the evidence may mean, supporting and weakening observations,
alternatives, and the next useful question. Label hypotheses as hypotheses.
Never diagnose, claim causation, or present model prose as a deterministic
OpenHealthAtlas finding.

Keep the answer focused on the requested records and relevant limitations.
Do not append routine medical-advice or diagnosis disclaimers, or repeat the
tool's `non_diagnostic_text`. That field remains machine-contract metadata.
Explain specific missing data, uncertainty or limits when they affect the
answer, rather than adding a blanket warning to every reply.

## Fixed acceptance controls

The following fixed tools remain available for regression and accepted journey
compatibility. The original Green-day control was `green-days-actionable-v1`;
the comprehensive recovery fixture is `comprehensive-persona-v1`.

For a bounded date range, select these tools in order:

1. `feature-frame --from DATE --to DATE --family subjective --include-provenance`
   to identify the exact dates where `subjective.day_rating` is observed with
   value `3` (Green) and retain each row's source/provenance.
2. `data-readiness --from DATE --to DATE --outcome subjective.day_rating` to
   inspect coverage, limitations, and prerequisites before interpreting.
3. `analysis-refresh --kind manual --outcome subjective.day_rating --mode
   green-vs-non-green --from DATE --to DATE --anchor DATE` to persist the
   targeted deterministic analysis and its raw statistical ranking. Preserve
   that order; do not hide a stronger marker in favor of a weaker action.
4. Call `feature-registry` with no arguments. Use its existing deterministic
   `pillar`, `actionability`, `roles`, `confounder_role`, `temporal_type`, and
   `lag_eligibility` metadata to classify the returned exposure findings.
5. When the top result is a marker, continue the investigation. Look for
   eligible direct or indirect exposures whose `temporal_direction` says
   `exposure_precedes_outcome`; compare them with the marker, checked and
   unchecked confounders, contradictory findings, and same-day alternatives.
6. Select at most three findings for the answer: the strongest supported direct
   upstream behavior, the strongest physiological marker, and at most one
   meaningful competitor or alternative. For each selected finding, call
   `finding-evidence` with the exact outcome, finding ID, input fingerprint, and
   same date range. Do not cite a finding that was not replayed successfully.
7. Call `synthesis-prepare --batch-id ID` to obtain the ledger-owned references
   needed for the conversational answer. It records `model_invoked=false`.
8. Do not call `synthesis-record`. Interpretation write-back is disabled for
   the normal Hermes journey and this skill does not authorize enabling it.

The visible reply must keep two exact sections:

## Deterministic result

List Green dates, coverage, returned findings, limitations, and their exact
evidence IDs/provenance. Show the raw deterministic statistical ranking,
including effect sizes, q-values, stability, sample sizes, timing, and stronger
markers that will not lead the actionable ranking. These are OpenHealthAtlas
results, not Hermes claims.

## Hermes interpretation

### What the user did

Rank only supported upstream behavior or exposure separately from statistical
strength. `actionability=direct` can be an actionable contributor when it
precedes the outcome. `actionability=indirect` is a possible mechanism, not the
action itself. Show supporting and weakening evidence, temporal order,
alternatives, and categorical confidence. Never invent a weaker actionable
answer when the evidence does not support one.

### What the body showed

Treat wearable, recovery, and vital measurements such as resting heart rate or
HRV as physiological markers, even when they rank first statistically. State
whether each supports an upstream hypothesis, is merely associated, or could
be a consequence. A marker is not what the user did.

### What remains unknown

List untested alternatives, missing data, checked and unchecked confounders,
counterexamples, and the next observation or question that could change the
ranking. Same-day order-unknown results may be consequences. For the known
`green-days-v1` acceptance control, the exact three-day Green-Yellow-Red cycle
is the dominant synthetic limitation; do not infer a behavioral explanation.

The actionable ranking does not establish a cause. If only a marker is
supported, say clearly: lower resting heart rate was associated
with Green days, but it is a physiological marker and the available evidence
does not show what behavior produced it, so you cannot determine what the user
did.

If readiness or analysis returns insufficient data or no eligible findings,
do not manufacture a leader or ranked list. Keep all four visible sections,
state the limitation, and ask concrete follow-up questions that would obtain
the missing comparison evidence.

Never run a second model, the local synthetic interpretation gateway, an
unbounded database query, or a health write outside the listed tools. Never
label model prose as a deterministic finding.

## Bounded fictional recovery workflow

For a question about current recovery, use the two recovery tools instead of
the Green-day association sequence:

1. Call `openhealthatlas_recovery_snapshot` once. OpenHealthAtlas returns the
   fixed-date composite components (sleep, HRV, and resting heart rate), plus
   separately reported training recency, effective sets, performance trend,
   soreness, and visible missingness.
2. Show the deterministic snapshot, then ask the user which one of the returned
   focus options they want to inspect. Do not choose for them.
3. After the user answers, call `openhealthatlas_recovery_detail` with exactly
   that focus. Explain the result as Hermes, keeping the deterministic values
   and the model interpretation visibly separate.

The gateway renders the trusted `Data & evidence disclosure` from the
successful same-turn OpenHealthAtlas MCP completion before any optional Hermes
prose. Hermes must not reconstruct, replace, or omit that block. The tool's
`delivery_contract` is the machine-enforced contract; the legacy
`reply_requirement` remains explanatory metadata only.
Include the fictional fixture ID, approved range and anchor, shared
field groups, privacy-safe result/input/public-evidence identities, visible
policy ID/version/hash, component missingness, current-snapshot integrity
scope, disabled interpretation write-back, and excluded field groups.
Keep `non_diagnostic_text` in the machine contract; the displayed disclosure
does not repeat it. Do not turn an empty component-missingness list into a
claim of complete historical ancestry. Do not add or reconstruct the raw
soreness note or a note-derived fingerprint: the model-facing tool response
includes only its date/presence, derived sore flags, and approved source label
and locator.

The composite is not “overall health” and training or soreness is not silently
converted into the sleep/HRV/resting-HR score. A low or high value does not
establish readiness or causation. If a component is missing,
say so rather than substituting another signal. The tools accept no private
health payload and store no Hermes interpretation.
