# Independent source-release review

## Local evidence workflow review, 2 October 2026

Independent GPT-6 Astra reviewers examined the local candidate based on
`aff70dfeb8c16a81f5294ffa2e69ae67ff897181`. The implementers did not supply their
own independent approval. The reviewed scopes are accepted with no unresolved
blocking findings; this is not publication or clinical approval.

The write-boundary review verified closed broker operations, authentication,
CSRF, atomic profile saves and audit privacy. A malformed-argument disclosure
was reproduced and fixed by hashing complete sensitive write requests in both
audit surfaces.

The evidence review verified v8 migration refusal/upgrade behavior, immutable
source/context revisions, correction resolution, read-only access and replay.
It reproduced and verified fixes for mixed fitness protocols, import-order
identity drift, parentless quarterly derivations, rounded-zero baselines and
hidden partial nutrition observations.

The interface review verified record-specific prompts and corrections,
bilateral denominator presentation, source-specific denominators, visible
partial subtotals and asynchronous profile/weight refreshes. Reproduced target
ambiguity and stale-response races were fixed and rechecked. A later responsive
class change and the scoped ledger-trigger assertion were separately reviewed.

Exact content hashes and independent reproduction logs were retained outside
the checkout. The coordinator separately completed the full disjoint test
partitions and browser checks recorded in
[Verification](VERIFICATION.md#local-evidence-workflow-candidate-2-october-2026).
No live model, private health records or production service were used.

## Historical initial source-release review

Review date: 16 September 2026.

This is the historical review of the initial MIT release. It is not an
independent review of the later AGPL transition; see [LICENSING.md](LICENSING.md)
and the latest checkpoint in [docs/REFACTORING.md](docs/REFACTORING.md).

## Verdict

Accepted for the initial developer source release. Publication is conditional
on the final exported source matching the reviewed tree and passing the
clean-history and GitHub configuration checks below.

## Scope and evidence

Separate coding agents reviewed the first-party MIT license and creator
attribution, preserved third-party licenses, publication claims, privacy
boundaries and clean-history preparation. The release-review lane compared the
current source with the recorded passing verification snapshot. The final
publication review checks the exact exported candidate rather than relying on
an older source or release name.

The preceding implementation review and retained verification covered all
1,981 tests: 1,961 base tests, 14 trace replays, one panel/broker/fake-Hermes
journey and five real MCP SDK subprocess tests. Numerical fixtures and seals
were unchanged. Original-code license and publication-document updates do not
change runtime, test or dependency bytes. Exact partitions and qualifications
are in `VERIFICATION.md`.

## Required final checks

- Original project code uses the standard MIT license with Kajeesan Jeevendra's copyright notice.
- README, NOTICE and citation metadata retain visible creator credit and point to the new public repository.
- Every vendor file and its licenses/notices remains byte-identical.
- Tested runtime, tests and dependency declarations remain byte-identical.
- The complete current source and release manifest pass privacy and integrity checks.
- The new publication history has one root commit and contains no private development ancestry.
- Public documentation describes current model-independent MCP and existing Hermes behavior accurately.

These are separate-agent engineering reviews, not an external human security
audit. They do not establish clinical validation, universal model-answer
quality or untested platform support.
