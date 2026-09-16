# Security policy

OpenHealthAtlas is security-sensitive health software. Do not include personal records,
credentials, exploit proof containing real data, or a live service URL in a
public issue.

## Reporting a vulnerability

Use **Security → Report a vulnerability** in
[kajeesan/Open-Health-Atlas](https://github.com/kajeesan/Open-Health-Atlas/security/advisories/new).
The public release enables GitHub private vulnerability reporting. If that
action is unavailable, do not post sensitive details in a public issue; ask
the maintainer to restore the private reporting channel.

A useful private report should contain the affected version/commit, component,
impact, minimal synthetic reproduction, and suggested mitigation. Use fictional
data and redact tokens, hosts, paths, account IDs, and authentication material.

## Supported version

Only the latest tagged release is supported. The initial `v0.1.0` release is a
developer preview; known product and platform limits are documented in the
README and `UNIMPLEMENTED.md`.

## Security architecture

- WebAuthn passkeys and server-side sessions protect the panel.
- State-changing browser requests use CSRF protection.
- Cookies are HTTP-only, `SameSite=Strict`, and secure by default.
- A restrictive CSP, frame denial, MIME sniffing protection, and same-origin
  referrer policy are emitted on responses.
- Login/enrollment endpoints are rate-limited.
- The panel reads the health database through a read-only connection.
- Writes cross a local Unix socket, peer/shape validation, an operation
  allowlist, and the validated CLI surface.
- Schema migrations use checksums, exact-shape preflight, transactions,
  foreign-key checks, and `PRAGMA quick_check`.
- Deterministic evidence replay rejects stale or mismatched references.
- The standalone MCP server binds requests to one configured local database,
  exposes read-only tools and bounds long-running work.
- The integrated Hermes adapters retain their own evidence and delivery checks;
  those checks do not govern prose produced by every external MCP client.

These controls do not make an Internet-exposed, multi-tenant service safe.
OpenHealthAtlas is designed for a single-user private deployment behind an owner-chosen
secure access layer.

## Secrets and configuration

Never commit a populated `.env`, token, passkey state, OAuth response, private
key, service credential, database, export, log, or provider configuration.
Generate a persistent random `PANEL_SECRET_KEY`; store it in a protected
external environment file or secret manager. Keep `PANEL_RP_ID` and
`PANEL_ORIGIN` exact and use HTTPS outside localhost.

External adapters must be run with the minimum network and filesystem access
they need. The supplied service files are examples, not an activated security
baseline. Review users, groups, paths, socket permissions, environment files,
and sandbox directives on the target host.

## Health and model safety

Deterministic findings describe recorded data and bounded associations. The
retired synthetic interpretation runtime is not a current security boundary.
Conversation and interpretation belong to the user's external AI client:
Hermes for the existing integrated routes, or a compatible client using the
standalone MCP server. That client controls model/provider selection and the
handling of returned tool results.

The MCP connection does not expose arbitrary SQL, arbitrary file access or
health-record writes. Selecting a local database does not ensure that a
client using a hosted model keeps returned data on the same machine. Existing
fictional Hermes acceptance adapters retain their separate fixed-data and
delivery constraints.

## Dependency and asset policy

Python dependencies are pinned. Vendored browser assets have version, source,
hash, and license records in `app/static/vendor/` and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Before release, review known
vulnerabilities, rebuild or re-download assets from their official sources, and
verify hashes. Do not silently update a minified bundle.

## Release checks

Every release should include:

1. full deterministic, schema, migration, security, adversarial, API, and UI
   test execution in a fresh environment;
2. empty initialization plus fixed-anchor fictional end-to-end flow;
3. working-tree and full-history privacy/secret/path/binary scans;
4. dependency and third-party license review;
5. a documented backup/restore test; and
6. independent integrated review of claims, known limitations, and evidence.
