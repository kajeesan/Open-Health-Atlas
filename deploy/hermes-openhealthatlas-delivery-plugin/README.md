# OpenHealthAtlas governed delivery renderers

This external-Hermes plugin renders server-owned fictional OpenHealthAtlas
disclosure before optional Hermes prose. It retains the accepted Recovery
renderer and adds a separately versioned generic-evidence renderer. Both are
restricted to Telegram and contain no model or receipt lookup.

The prose validator checks only explicit reserved patterns for the accepted
fixture, range, policy, overall score/status, write-back, diagnosis, causation,
and public hashes. It does not claim semantic understanding of every possible
natural-language contradiction. Projection exclusion and fictional canary
tests provide the separate privacy boundary.

When optional prose survives those bounded checks, the renderer prefixes it
with the visible heading `Hermes interpretation`. Hermes validates the complete
governed text before Telegram formatting and the first Bot API send. Telegram
formatting and multi-message delivery are not atomic wire-byte validation;
trusted-disclosure chunks must all precede optional-prose chunks, and a partial
failure invalidates the plan.

The installer requires an explicit `--hermes-home ABSOLUTE_PATH` (or an
explicitly set `HERMES_HOME`) and copies this directory atomically to:

`$HERMES_HOME/plugins/openhealthatlas-recovery-delivery/`

Recovery renderer `openhealthatlas-recovery-delivery@1.0.0` remains bound to
the exact Hermes registry identities
`mcp_openhealthatlas_fictional_openhealthatlas_recovery_snapshot`,
`mcp_openhealthatlas_fictional_openhealthatlas_recovery_detail`.

Generic renderer `openhealthatlas-generic-evidence-delivery@1.0.2` is separately
bound to catalog, query, analyze, and final evidence under the same exact MCP
server prefix. Intermediate calls create a same-turn obligation; only the final
`mcp_openhealthatlas_fictional_openhealthatlas_health_evidence` completion
satisfies it. The accepted MCP server alias must therefore remain exactly
`openhealthatlas_fictional`.

The generic renderer places the server-owned deterministic disclosure first and
labels all following Hermes prose as model-generated. Hermes may naturally
reference, round, compare, and combine numbers and dates; the deterministic
block remains the authoritative record. `allowed_scalar_claims` remains only as
backward-compatible contract metadata and does not restrict model prose. Do not
add a new numeric, date, wording, or exact-line restriction without explicit
owner approval. If an unchanged non-numeric safety boundary rejects optional
prose, the trusted deterministic disclosure is still delivered with a
server-owned omission note.
A different alias, a bare upstream tool name, or a suffix collision fails
closed and requires a new reviewed renderer version rather than an in-place
widening.

This renderer version also pins the accepted Recovery snapshot to schema v5.
A development-v6 completion is safe-tested to produce Hermes' fixed no-prose
failure payload; it is deterministic development evidence, not accepted
Telegram delivery evidence.

Installation deliberately does not enable the plugin, edit Hermes
configuration, restart a service, or deploy anything. Before a separately
approved deployment, merge the exact entry in `enablement.example.yaml` into
the existing `$HERMES_HOME/config.yaml`. Preserve every other enabled plugin.
Never run the installer as root without the explicit Hermes home: it must not
infer `/root/.hermes` from the invoking account.
The resulting opt-in allow-list must contain:

```yaml
plugins:
  enabled:
    - openhealthatlas-recovery-delivery
```

Do not enable or restart until the pinned OpenHealthAtlas and Hermes gateway
artifacts have both been verified.
