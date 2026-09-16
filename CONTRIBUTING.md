# Contributing

Open Health Atlas welcomes carefully scoped contributions. By intentionally
submitting a contribution for inclusion, you agree that it is provided under
the project's MIT License. Preserve the licenses and notices of third-party
material. Security reports must use the private process in
[SECURITY.md](SECURITY.md), not a public issue.

## Principles

- Use only fictional or generated test data.
- Never commit populated databases, exports, logs, credentials, `.env` files,
  authentication state, private paths/hosts, or provider account IDs.
- Preserve provenance, timing, units, laterality, and identity contracts.
- Fail closed at trust boundaries; do not convert an unknown into a plausible
  default.
- Keep deterministic evidence distinct from inference and general knowledge.
- Do not claim diagnosis or causation from association code.
- Prefer one canonical implementation; remove a duplicate only with dependency
  and behavior evidence.
- Add unfinished product behavior to `UNIMPLEMENTED.md` instead of shipping a
  placeholder as a completed feature.

## Get started

- [Try the fictional demo](docs/TRY_DEMO.md) to explore the product.
- [Connect a local MCP client](docs/LOCAL_MCP.md) to use the deterministic tools.
- [Development setup](docs/DEVELOPMENT.md) to install dependencies and change code.
- [Testing](docs/TESTING.md) for focused checks and the optional MCP tests.

Keep development databases and environments outside the checkout.
`scripts/devserver.py` creates a fictional local environment with working
brokers and a development-only sign-in; never deploy that script.

## Changes

1. State the user-visible behavior and trust boundary.
2. Reuse relevant tests. Add tests for a distinct retained behavior or an
   uncovered regression; do not create assertions for incidental wording or CSS.
3. For a schema change, add a checksum-validated forward migration, exact-shape
   preflight, foreign-key/quick-check evidence, and a non-destructive recovery
   procedure.
4. For a feature, register identity, unit, timing, provenance, completeness,
   and readiness behavior.
5. For model-like output, prove evidence ancestry, numeric/unit grounding,
   medication safety, terminal behavior, and persistence atomicity.
6. Update the feature-retention/limitation documentation when public behavior
   changes.
7. Run the relevant checks in [docs/TESTING.md](docs/TESTING.md). Broaden to the
   full suite when the runtime change requires it; documentation-only changes
   need command, link and privacy verification.
8. Scan the entire diff for privacy, secrets, paths, binaries, and third-party
   material before review.

## Style and dependencies

Follow the existing Python and browser-code conventions. Keep functions small
at validation boundaries and use stable machine-readable error codes.
Dependencies must be justified, pinned, and reviewed for license and security
impact. Do not add a CDN dependency; the panel CSP expects self-hosted assets.

## Pull request evidence

A review should be able to see:

- behavior retained or changed;
- files and contracts affected;
- exact commands and results;
- invalid and adversarial cases;
- privacy/security impact;
- deliberate exclusions or limitations; and
- migration/recovery impact, if any.

Do not include real health screenshots or copied production output. Use the
fixed-anchor fictional demonstration.
