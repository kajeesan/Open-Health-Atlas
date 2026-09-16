# Unimplemented and partial product behavior

This is the current register of intended Hermes behavior
that current executable code does not complete. Historical design language was
used only as a lead; code and tests below determine status. External services
that users must supply are not treated as missing Hermes code when a complete
generic integration boundary already exists.

## Retired reference scope

U-01 through U-07 belonged to the obsolete synthetic interpretation runtime,
which has been removed with its test-only endpoints and exclusive tests. Those
reference-runtime gaps are retired, not completed product features. Genuine
external Hermes owns reasoning; OpenHealthAtlas retains deterministic evidence
preparation and validated optional `synthesis-v1` storage with write-back off
by default. Historical issue numbers remain reserved to avoid reinterpreting
older references.

### U-08 — Production cross-pillar recovery links

- **Intended behavior:** connect recovery/sleep observations with training,
  nutrition, mind, labs, and care evidence in the normal product flow.
- **Status:** **partial; user-facing cross-links remain unverified**.
- **Executable evidence:** `app/templates/recovery.html` retains its Cross-links
  card and `app/static/js/recovery.js` has no cross-domain runtime call. The
  deterministic records and governed Recovery API remain available.
- **Missing component:** a bounded deterministic evidence/API path and an
  exercised display flow for these relationships; any model explanation belongs
  to genuine external Hermes.
- **End-user impact:** no live cross-pillar AI synthesis is established by the
  Recovery screen. Keep the existing card while repairing or verifying its path.

## Deterministic intelligence

### U-09 — Causal inference engine

- **Intended behavior:** if Hermes ever makes causal claims, establish an
  explicit causal design with assumptions, interventions/confounders, and
  falsification—not merely stronger association prose.
- **Status:** **not implemented**.
- **Executable evidence:**
  `toolkit/hermes_insights/associations.py`,
  `toolkit/hermes_insights/ledger.py`, and
  `toolkit/hermes_insights/synthesis.py` keep observational
  findings, hypothesis evidence, alternatives, and model inference distinct.
  `toolkit/tests/test_outcome_associations.py` and
  `toolkit/tests/test_phase5_ledger.py` enforce the association-not-causation
  language and evidence boundary.
- **Missing component:** an approved causal estimand/design, confounder model,
  identification checks, and validation against suitable data.
- **End-user impact:** Hermes can surface hypotheses and associations but must
  not tell users that one logged behavior caused an outcome.
- **Minimum future work:** define a narrowly scoped causal feature with expert
  review and synthetic counterexamples; otherwise retain the current boundary.

### U-10 — Higher-order interactions

- **Intended behavior:** analyze bounded combinations beyond two exposures if a
  product requirement establishes value and adequate sample sizes.
- **Status:** **not implemented**; pairwise is the implemented maximum.
- **Executable evidence:**
  `toolkit/hermes_insights/associations.py::validate_options` and the
  `outcome-associations` parser in `toolkit/health.py` accept only `none` or
  `pairwise`; `toolkit/tests/test_interactions.py` validates the four pairwise
  cells, minimum-cell gates, and component-alone effects.
- **Missing component:** registered higher-order search, sparsity/sample gates,
  multiplicity control, presentation, and performance bounds.
- **End-user impact:** three-way and larger combinations are not evaluated.
- **Minimum future work:** specify strict candidate-generation and minimum-cell
  rules, then add adversarial sparsity and false-discovery tests.

### U-11 — Twelve lateral-knee readiness features

- **Intended behavior:** compute severity, onset, first-positive, and resolution
  features for central, left, and right lateral-knee pain.
- **Status:** **not implemented**.
- **Executable evidence:** `toolkit/hermes_insights/registry.py` marks exactly
  these 12 entries `logic_not_implemented`;
  `toolkit/tests/test_feature_frame_readiness.py::test_exact_six_state_predicate_order_including_populated_unconnected`
  verifies that state independently from implemented-but-never-logged features.
- **Missing component:** deterministic episode definitions and calculations.
- **End-user impact:** the registry exposes the intended features but no value
  can become ready even when relevant records exist.
- **Minimum future work:** approve episode boundaries and laterality rules,
  implement calculations, and add onset/resolution/boundary tests.

### U-12 — Quarterly asymmetry-to-pain episode analysis

- **Intended behavior:** relate quarterly strength/laterality asymmetry to later
  pain episodes using an event-centered, temporally bounded algorithm.
- **Status:** **not implemented**.
- **Executable evidence:**
  `toolkit/tests/fixtures/autonomous_insights/06-quarterly-asymmetry.json` and
  the `quarterly_asymmetry` branch of
  `tests/e2e/test_autonomous_insight_traces.py::test_each_trace_replays_exactly`
  require `logic_not_implemented` and reject a pain hypothesis while retaining
  the underlying strength/control/mobility measurements.
- **Missing component:** episode construction, exposure window, lag policy,
  confounder/coverage checks, and accepted interpretation boundary.
- **End-user impact:** current muscle/laterality displays work, but Hermes does
  not make this longitudinal pain connection.
- **Minimum future work:** specify the deterministic event algorithm and test
  null, reversed, missingness, laterality, and boundary cases.

## UI and domain depth

### U-13 — Mind social logging

- **Intended behavior:** log and review structured social/context observations
  from the Mind area.
- **Status:** **not implemented**.
- **Executable evidence:** `app/templates/mind.html` states that no
  `social_log` table or logging UI exists, and
  `tests/test_shell.py::test_mind_social_tab_is_honest_scaffold` requires the
  explicit empty state and rejects fabricated values. `toolkit/SCHEMA.sql` and
  the `toolkit/health.py` command registry contain no dedicated social log.
- **Missing component:** schema, validated write/API, read model, and UI wiring.
- **End-user impact:** social context can appear only through other generic
  evidence routes, not a dedicated logging workflow.
- **Minimum future work:** define the record/identity/timing contract, migrate,
  and add API/UI/provenance tests.

### U-14 — Prescriber-question workflow

- **Intended behavior:** capture medication questions for later discussion with
  a clinician and track their status without giving prescribing instructions.
- **Status:** **not implemented**.
- **Executable evidence:** `app/templates/mind_prescriber.html` is a placeholder
  page with no corresponding JavaScript module, API route, or schema table;
  `tests/test_shell.py::test_mind_prescriber_page_is_honest_pending` requires
  the zero-saved, no-questions state.
- **Missing component:** question/status record, safe validated write/read API,
  and non-clinical UI behavior.
- **End-user impact:** the page cannot store or manage questions.
- **Minimum future work:** define a clinician-discussion-only contract and add
  medication-safety, auth, CSRF, and persistence tests.

### U-15 — Consistency XP/level engine

- **Intended behavior:** calculate and persist experience/levels if gamification
  is part of the product.
- **Status:** **not implemented**.
- **Executable evidence:** `app/templates/consistency.html` labels XP/levels
  pending. Although `toolkit/SCHEMA.sql` can store a per-habit `xp` value,
  neither `toolkit/health.py` nor `app/routes/dash.py` calculates levels, and
  `tests/test_shell.py::test_consistency_page_links_to_habit_ledger` covers the
  working habit ledger without claiming a game engine.
- **Missing component:** product rules, deterministic engine, and history.
- **End-user impact:** habits and consistency summaries work; XP/level claims do
  not.
- **Minimum future work:** approve transparent rules and test recalculation,
  missing days, corrections, and migration before exposing the UI.

### U-16 — Recipe photo storage

- **Intended behavior:** attach and display user-controlled recipe images.
- **Status:** **not implemented**.
- **Executable evidence:** the `recipes` table in `toolkit/SCHEMA.sql` has no
  image reference; `app/routes/nutrition.py` and `app/templates/recipe_detail.html`
  expose no upload/serve surface. `tests/test_nutrition_api.py` covers recipe
  list/detail behavior without an image field.
- **Missing component:** file/object storage, safe type/size validation,
  authorization, cleanup, and privacy policy.
- **End-user impact:** recipes are text/nutrient records only.
- **Minimum future work:** choose external private storage, implement strict
  upload serving controls, and add malicious-file/privacy tests.

### U-17 — Supplement product management and inventory depth

- **Intended behavior:** manage supplement products with domain-specific
  validation, correction/retirement workflows, and detailed stock history while
  retaining intake/adherence/notes.
- **Status:** **partial**.
- **Executable evidence:** `toolkit/health.py::LOGGABLE` and
  `toolkit/health.py::log` provide an allowlisted product-create path with
  bounded name, active-state and finite dose/unit validation;
  `toolkit/tests/test_health.py::test_generic_log_can_create_a_bounded_supplement_product`
  proves it. `tests/test_nutrition_api.py` covers reads/adherence. The
  `supplement_products` table in `toolkit/SCHEMA.sql` has no stock quantity or
  stock-event history, and there is no product-management API/UI.
- **Missing component:** edit/retire and
  correction semantics, stock fields/history, and product-management UI/API.
- **End-user impact:** technical users can create allowlisted product rows and
  log adherence, but normal UI users cannot manage a complete inventory.
- **Minimum future work:** define product identity/dose/unit/stock contracts,
  retain the validated create operation, and add correction,
  API, UI, and stock-history tests.

## External dependency clarification

The optional conversational agent/model executable is not registered as a
Hermes implementation gap: the retained command adapter is complete and the
service is an explicitly user-supplied external dependency, like Hevy.

## Audited candidates found implemented

The following were specifically checked and are **not** gaps:

- Red/Yellow/Green categories and Yellow-preserving comparisons are validated;
  deterministic binary views do not relabel Yellow.
- Bounded ranges, lags, rolling transforms, current/range/longitudinal evidence,
  pairwise combinations, event families, conversation families, provenance,
  identities, and the hypothesis ledger are implemented and tested.
- Persistent freezer-restock state, check/mark commands, snoozing, notification
  suppression, and new-batch reset are implemented in `toolkit/health.py`, owned
  by migration v5, and exercised with fictional data in
  `toolkit/tests/test_health.py` and `toolkit/tests/test_restock_migration.py`.
- Training, running, body/laterality, pain/rehabilitation, nutrition, recovery,
  labs, mind/subjective/medication evidence, goals/plans, external care, chat,
  exports, and model-status surfaces have retained product code and tests.
