# OpenHealthAtlas project map

Local, read-only visualization of the audited product architecture and user journeys. It does not import or modify product code, databases, or private health data.

## Run

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/project_map/app.py
```

Open <http://127.0.0.1:5127/>. The server always binds to `127.0.0.1`. Set `OHA_PROJECT_MAP_PORT` to use another local port.

The default audit directory is `tools/project_map/forensic-audit`. Set
`OHA_FORENSIC_AUDIT_DIR` if the six audit files live elsewhere. The map retains
its curated evidence references when the source directory is unavailable, but
the header will show that live audit checks could not be loaded.

## Evidence rule

Audit and runtime evidence determine status. `graphify-out/graph.json` supplies bounded file-navigation context only; its snapshot is deliberately labeled stale for the audited branch and is never treated as acceptance proof.

The curated map includes a sanitized fictional Telegram acceptance snapshot in
`evidence/`. It contains deterministic receipt identifiers and proof flags only.
The localhost server does not connect to the Oracle VPS, Telegram, or any health
database.

## Historical receipt redaction

Files under `evidence/` are historical records, not current deployment proof.
Private runtime turn/session identifiers and concrete home, backup and working
directory locators have been replaced with explicit redaction labels. Repeated
identities keep the same label. Recorded outcomes, dates, source commits and
artifact hashes remain unchanged.

Historical receipt-byte hashes refer to the original unredacted records; they
do not attest to the redacted copies. `RELEASE_MANIFEST.tsv` identifies the bytes
actually included in this source release. Reproducing the product does not
require the private development commits referenced by those historical records.
