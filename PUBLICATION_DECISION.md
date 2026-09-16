# Public source release decision

## Owner decision

On 16 September 2026, the owner authorized a separate public repository named
**Open Health Atlas**, the **MIT License** for original project code, and clear
creator credit for **Kajeesan Jeevendra**.

The public repository is `kajeesan/Open-Health-Atlas`, with the current release
on `main`. It starts from one reviewed source snapshot with no inherited Git
parents. The older development repository remains private and retains its own
history and remote configuration. Do not mirror, import, or fetch that private
history into the public repository.

Source publication does not activate a service, expose a gateway, change a
model/provider, or deploy to an existing installation. Personal health data,
credentials and deployment state remain outside source and publication history.

## Product and release scope

Open Health Atlas owns local records, deterministic calculations, validated
writes and evidence. External AI clients own conversation and interpretation.
The optional local MCP server supports a client-selected model; Hermes supplies
the existing integrated conversation and Telegram workflows. Existing browser
controls and supported secondary paths remain part of the product.

The initial release is a developer-oriented source distribution. Installation
and data import are documented. A desktop launcher, bundled model runtime,
built-in API-key chat and multi-user hosted service are not included. Known
capability limits remain in `UNIMPLEMENTED.md`; measured behavior and exact
verification scopes are recorded in `VERIFICATION.md`.

## License and creator credit

The original project code is licensed under MIT, with
`Copyright (c) 2026 Kajeesan Jeevendra` in `LICENSE`. The README, `NOTICE` and
`CITATION.cff` identify Kajeesan Jeevendra as the creator. MIT's copyright and
permission-notice retention condition is preserved without adding a separate
attribution restriction.

Third-party components retain their original licenses and notices. The
vendored browser bundles, Apache/BSD notices and embedded permission text are
unchanged. Their exact files and hashes are recorded in
`THIRD_PARTY_NOTICES.md` and `app/static/vendor/VENDOR.md`. Installed Python
packages retain their upstream licenses; no dependency source is relicensed.

## Publication checks

The final source candidate must pass:

- Exact comparison with the tested runtime, tests and dependency files.
- Release-manifest verification and source/privacy scans.
- A scan of every commit and ref in the new parentless publication repository.
- License, creator-attribution and third-party-notice review.
- Updated public setup, security and verification documentation.
- Independent review of the candidate and its remaining limitations.

The existing 1,981-test verification remains applicable when only publication
metadata, documentation and the original-code license change. Do not rerun or
inflate runtime test counts for a documentation-only release batch.

## GitHub publication sequence

Prepare the new repository privately, push only the reviewed clean `main`, and
verify its exact file tree and history. Then publish the repository under the
owner's authorization and enable and verify GitHub private vulnerability
reporting as part of the same publication operation. GitHub provides that
reporting feature for public repositories, so it cannot be claimed as verified
while the repository is still private. Publication is complete only when the
public repository, default branch, license, reporting channel and release are
verified. Record the resulting commit and release identifiers separately.
