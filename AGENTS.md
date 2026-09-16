# OpenHealthAtlas working instructions

## Product and scope
- Keep OpenHealthAtlas a health-data product. Company-specific work, portfolio work and website changes are deferred.
- OpenHealthAtlas owns records, deterministic calculations, evidence and validated writes. External AI clients own interpretation and conversation: Hermes for existing integrated workflows, or a compatible client through the optional local MCP connection.
- Telegram is the primary interaction path. Preserve every UI field and control already wired to collect or display data, including working secondary interaction paths.
- A broken or unverified wired feature is a reported gap to repair, not permission to delete or hide it.
- Remove unnecessary tests, duplicated rules and obsolete reference code only after accounting for retained consumers. Do not replace incidental CSS or wording assertions with new tests.
- Preserve calculation accuracy, source/time/unit semantics, data integrity and supported operations. Add tests only for a distinct retained behavior or real regression without existing coverage.

## Working baseline
- Use the current public `main` branch or an explicit release tag as the implementation baseline.
- Historical refactor checkpoints in `docs/REFACTORING.md` describe prior private development work. Their Git history is intentionally absent from this clean public repository.
- Read `docs/REFACTORING.md` for the current checkpoint and `docs/ARCHITECTURE.md` for navigation. Verify consequential decisions in current source.

## Privacy and publication
- Keep intentional public author attribution in the README, NOTICE and citation material.
- Exclude personal health data, credentials, private home paths, device identifiers and real deployment details from the public source and its history.
- Use existing fictional fixtures and temporary databases for verification. Never use the owner's private health directory or live Telegram installation for tests.
- Project code uses the MIT License; preserve Kajeesan Jeevendra's copyright and creator credit and all third-party licenses/notices.
- The initial clean public release is owner-authorized. Future publication actions still require task-specific authorization; never import or publish the private development history or infer live-service activation from source publication.

## Context and progress
- Read targeted files and sections. Save long diagnostic outputs outside source; return compact findings.
- Maintain `docs/REFACTORING.md` after meaningful batches: decisions, changed files, verification, open gaps and next step.
- Keep generated files, databases and caches outside the release tree. Do not add another framework, review layer or test matrix merely to organize this cleanup.

## Checkpoints and verification
- Create small local Git commits for coherent refactor batches. Before a risky change, record the starting commit so it remains a clear recovery point.
- Reuse existing relevant tests. Choose checks for the behavior changed; documentation-only changes do not require a runtime test suite.
- Record exact checks and results in the checkpoint. Distinguish verified checkpoints from baseline or work-in-progress saves with known failures.
- For database-changing work, first make and verify a consistent snapshot outside Git. Use fictional databases during this refactor. A code revert does not restore database contents.
- If a batch fails, isolate its regression and restore or revert only its changes. Preserve unrelated work and existing history.
- Stage named files only. Keep private data and generated artifacts out of commits. Use the baseline's sanitized Git identity rather than an automatically configured private email or machine identity.
- Keep checkpoints local until publication privacy review permits a separately authorized push. GitHub is a remote copy of Git history, not a database backup.
