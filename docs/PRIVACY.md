# Privacy

OpenHealthAtlas processes health information. Treat every populated database, vault,
import, export, transcript, attachment, audit log, and model prompt as sensitive
even when a field is not obviously medical.

## Repository privacy guarantee

The clean repository is intended to contain only product source, empty schemas,
generic configuration templates, and fictional synthetic fixtures. It must not
contain:

- personal health records or user-authored raw notes;
- populated SQLite databases, exports, backups, logs, transcripts, or traces;
- credentials, tokens, private keys, authentication state, or populated `.env`
  files;
- private hosts, personal absolute paths, live account identifiers, or service
  endpoints;
- real names, contact details, national identifiers, or ambiguous seed scripts;
- caches, bytecode, virtual environments, or generated test artifacts.

`scripts/init_hermes.py` creates databases with no user, health, authentication,
or conversation records; it seeds only the bundled public exercise-anatomy
configuration. `scripts/make_demo_db.py` creates only fixed-anchor fictional
records and uses obvious synthetic labels.

## Deployment data layout

Keep the following outside the source tree in a user-controlled directory:

- `health.db` — health records, derived evidence, provenance, and ledgers;
- `panel.db` — passkeys, sessions, audit events, conversations, and UI state;
- `vault/` — user-authored notes;
- provider configuration and credential files;
- bridge and collector logs;
- exports, backups, and migration checkpoints.

The defaults use the platform user's local data directory. Service examples use
generic `/var/lib/hermes` and `/etc/hermes` paths and should be adapted before
deployment. Database files should be mode `0600`; containing directories should
be `0700` or equivalently restricted.

## Data flows

The web panel reads `health.db` in read-only mode. Writes go through a local
Unix-socket broker and validated toolkit commands. External collectors connect
only when a user configures and runs them. No telemetry, analytics beacon, or
default remote model call is built into the retained product.

The obsolete local synthetic interpretation runtime has been removed.
OpenHealthAtlas performs no model reasoning; genuine external Hermes is the
sole reasoning layer. Removing the reference code changes no existing external
data-sharing or validated-write boundary. Any
future expansion of the governed evidence sent to that external service
requires evidence minimization, user consent, provider-retention, geographic
transfer, prompt-logging, deletion, and breach-handling review before real
records are processed.

## Fictional Recovery model disclosure

The accepted `comprehensive-persona-v1` Recovery profile is fictional-only and
fixed to 2026-03-02 through 2026-06-30. Its MCP projection sends genuine Hermes
only derived Recovery components, derived training detail, derived soreness
flags, explicit component missingness/exclusions, governed evidence identity,
source locators, policy metadata, and the bounded fixture identity and range.
The delivery contract requires the gateway to render the trusted disclosure
from the successful same-turn MCP completion before optional Hermes prose. Its
public result identity hashes only the privacy-safe MCP projection, not the raw
deterministic result.

The projection excludes private health data, database and vault paths or
contents, raw history rows, the private ancestry sidecar, database identity,
canonical-row digests, and the verbatim soreness note. The note's date, source
locator and source label may be shared with derived sore flags; neither its
text nor a content fingerprint is model- or gateway-facing. No Hermes
interpretation is written back. An empty
`missing` list means no expected field is absent; it does not make
policy-excluded HRV/resting-HR components eligible or make source ancestry
complete.

That qualitative summary scope is authorized separately from the deterministic
readiness fingerprint by
`openhealthatlas-fictional-soreness-summary-telegram` version `1.0.0`. The
authorization lists the exact response-kind field paths, explicitly denies raw
note and note-derived-secret sharing, and binds delivery to the same
authenticated gateway event source. The OHA plugin never receives a Telegram
chat ID or a hash of one; generic Hermes core owns the transient destination
binding and whole-payload digest.

Real or private health data remains outside this approval. Before any such
profile can send evidence to external Hermes, the owner must separately approve
the exact field/range consent, provider retention and transfer terms, local raw
retention, deletion handling, and the policy for incomplete ancestry.
The machine-readable `openhealthatlas-real-data-governance-gate-v1` registry
keeps all four decisions unresolved and makes missing, unknown, or unapproved
decisions deny activation. Its tests use fictional data only; it is not a real
data activation or legal-compliance claim.

## User responsibilities

- Obtain any consent or legal basis required for imported data.
- Verify provider terms and regional health/privacy requirements.
- Encrypt devices and backups; test restoration without overwriting the only
  copy.
- Restrict network exposure and filesystem access.
- Rotate credentials and revoke external integrations when no longer needed.
- Review exports before sharing; they may contain more history than the current
  screen shows.
- Do not use fictional demo records as clinical examples or decisions.

## Retention and deletion

OpenHealthAtlas does not impose a universal retention policy. SQLite records and exports
remain until the user deliberately removes or archives them. Append-only tables
and provenance are designed for auditability, so deletion requirements need a
deployment-specific policy and verified backup handling. Do not delete records
through ad hoc SQL while collectors or the panel are running.

## Publication gate

Before any public release, scan both the working tree and every commit for PII,
secret patterns, private paths, populated data, unsafe symlinks, nested
repositories, unexplained binaries, and live configuration. A clean working
tree alone is insufficient if a secret exists in Git history.
