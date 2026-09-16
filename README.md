# Open Health Atlas

**See the pattern. Trace the evidence. Own your data.**

Created by **[Kajeesan Jeevendra](https://github.com/kajeesan)**.

Physical health, mental wellbeing, and daily behaviour do not exist in separate
boxes. Training affects recovery. Sleep can change mood and performance. Pain
can alter movement and routine. What we intend to do and what we actually do
are often different—and the useful signal is hidden across disconnected apps,
notes, measurements, and memories.

OpenHealthAtlas is an open-source, local-first system for bringing those data
points into one time-aware view. It helps you examine training, sleep,
recovery, nutrition, mood, symptoms, laboratory results, habits, and goals
together, while preserving where each fact came from and how each conclusion
was calculated.

For example, the system can show uneven training exposure or a possible
relative muscle imbalance alongside workload, recovery, sleep, and pain. That
does **not** prove why an injury happened. It creates a traceable hypothesis
that a person and their clinician or coach can inspect instead of presenting a
black-box answer.

The goal is simple: make patterns that are difficult to see manually visible,
without giving up ownership of sensitive health data.

> **Important boundary:** OpenHealthAtlas is not a medical device, diagnosis
> service, emergency service, or substitute for professional care. Its
> deterministic analyses describe logged data and bounded associations; they
> do not establish causation. Model reasoning belongs to an externally supplied
> AI client, such as Hermes or a compatible MCP application.

## OpenHealthAtlas and AI clients

OpenHealthAtlas and its connected AI client have different responsibilities:

- **OpenHealthAtlas is the governed health system.** It owns the local data,
  validated writes, calculations, provenance, evidence, privacy boundaries,
  and user interface.
- **The external AI client owns conversation and interpretation.** The optional
  local MCP server lets a compatible client use its chosen model with
  OpenHealthAtlas. Hermes remains the runtime for the existing integrated chat,
  Telegram, scheduled checks and approved automation workflows.

The dashboard is the visual and evidence-inspection surface. It displays the
user's recorded data and deterministic findings, but it is not the
conversational AI and does not independently decide why a symptom occurred.
Hermes is where the user describes symptoms, answers follow-up questions, and
asks the system to examine the governed evidence for possible explanations.

The panel, fictional demo, imports, manual workflows, deterministic analysis
and standalone MCP connection can run without Hermes. For MCP conversations,
the user supplies a compatible AI client and configures its model there.
The existing continuously available Telegram and integrated-agent experience
requires a separately configured Hermes runtime on an always-on machine,
the required model/provider and external services, and deliberately enabled
schedules.

Hermes itself, model access, provider accounts, credentials, and a hosted
server are **not bundled** with this repository. The included adapters and
service templates default to disabled. Therefore, OpenHealthAtlas should not be
described as an out-of-the-box autonomous 24/7 service. Once those external
dependencies are configured and kept available, the system can execute the
specific background workflows the user has approved.

| Use mode | Hermes required? |
|---|---|
| Explore the fictional local demo | No |
| Use the panel and deterministic health analytics | No |
| Ask a compatible AI client about local data through MCP | No; the client supplies the model and conversation |
| Use the integrated chat and Telegram workflows | Yes, a compatible external Hermes runtime |
| Run approved scheduled workflows continuously | Yes, plus an always-on host, scheduler, and configured integrations |

### Example: exploring knee pain

A user could tell Hermes where the knee hurts, when it began, what the pain
feels like, and which movements make it better or worse. Hermes can ask for
missing details and then inspect the governed evidence produced by
OpenHealthAtlas—for example pain history, recent workload, exercise exposure,
left/right measures, recovery, sleep, and earlier symptoms.

Hermes can then propose evidence-grounded **possible explanations**, show what
supports or weakens each hypothesis, identify missing information, and suggest
useful next questions or appropriate next steps. The dashboard lets the user
inspect the underlying records and deterministic findings that informed that
response.

This workflow does not establish a diagnosis or prove that one factor caused
the pain. Hermes should distinguish recorded facts from calculations and
hypotheses, avoid inventing absent data, and direct the user to appropriate
professional or urgent care when the symptoms warrant it.

## What makes it different

- **Whole-person structure:** physical, mental, behavioural, recovery, and
  clinical data can be viewed through one shared timeline.
- **Evidence before answers:** findings retain source, timing, units,
  transformations, coverage, and limitations.
- **Local-first ownership:** private databases and credentials live outside the
  source checkout and remain under the user's control.
- **Deterministic core:** validation, calculations, provenance, and writes are
  owned by ordinary software rather than delegated to a language model.
- **Honest uncertainty:** insufficient evidence stays insufficient; association
  is not labelled as diagnosis or cause.
- **Fictional public demo:** every included example is generated, deterministic,
  and fictional—not a copy of the creator's health history.

## What is included

- A passkey-protected web application with dashboard, body/training, pain and
  rehabilitation, mind, nutrition, recovery, external-care, labs, data,
  models, exports, conversations, plans, and insight surfaces.
- An append-oriented health schema, separate panel-state schema, and seven
  checksum-validated migrations.
- A 117-command validated health CLI and least-privilege Unix-socket write
  broker.
- Generic Hevy, Google Health, Telegram, scheduler, OCR, transcription, and
  Hermes service templates. External products, the Hermes runtime, accounts,
  and credentials are not bundled.
- A 517-feature deterministic registry, identity/provenance framework,
  readiness, statistics, bounded associations, pairwise interactions, goals,
  timing, hypothesis/synthesis ledgers, triggers, and notifications.
- Empty initialization, a deterministic fictional demo, and comprehensive
  schema, validation, security, API, UI-contract, failure, and end-to-end tests.
- A fictional nine-exercise starter sub-muscle map plus an
  uncertainty-labelled relative subregion strength theory. It separates
  training exposure from inferred capacity and suppresses rankings until at
  least five distinct recurring dates exist.

The current public file inventory is in
[RELEASE_MANIFEST.md](RELEASE_MANIFEST.md), and capability-level status is in
[FEATURE_RETENTION_MATRIX.md](FEATURE_RETENTION_MATRIX.md).

## Current limitations

OpenHealthAtlas is a substantial reference implementation, not a finished
hosted product. Multi-user operation and several production integrations are
not implemented. Real-model invocation and live conversational reasoning belong
to the selected external AI client. Integrated chat and Telegram use Hermes;
standalone MCP uses the user's compatible client. The obsolete local synthetic
interpretation runtime has been retired; deterministic evidence preparation,
validated optional synthesis storage, and existing browser conversations remain.
Read [UNIMPLEMENTED.md](UNIMPLEMENTED.md) before relying on or extending the
system, and [docs/PRODUCT_AUTHORITY.md](docs/PRODUCT_AUTHORITY.md) for ownership.

## Quick start

Prerequisites: Python 3.11 or newer, SQLite, and a modern browser with WebAuthn
support. The release candidate was verified with Python 3.14.6.

```bash
git clone https://github.com/kajeesan/Open-Health-Atlas.git
cd Open-Health-Atlas
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
```

The default `main` branch contains the current public source. Tagged releases
provide fixed versions for reproducible installation.

Initialize private, empty databases outside the checkout:

```bash
export HERMES_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/openhealthatlas"
.venv/bin/python scripts/init_hermes.py --data-dir "$HERMES_DATA_DIR"
```

`HERMES_*` names are retained as internal compatibility identifiers for the
current release; they do not indicate that any private source data is included.
The initializer refuses to overwrite either database. Set a persistent secret,
use local HTTP cookie settings, then run the development Flask server:

```bash
export HEALTH_DB="$HERMES_DATA_DIR/health.db"
export PANEL_DB="$HERMES_DATA_DIR/panel.db"
export PANEL_SECRET_KEY="$(openssl rand -hex 32)"
export PANEL_COOKIE_SECURE=0
.venv/bin/flask --app wsgi run --host 127.0.0.1 --port 5111
```

In a second terminal, create a one-time enrollment URL:

```bash
HERMES_DATA_DIR="$HERMES_DATA_DIR" \
HEALTH_DB="$HEALTH_DB" PANEL_DB="$PANEL_DB" \
PANEL_SECRET_KEY="$PANEL_SECRET_KEY" PANEL_COOKIE_SECURE=0 \
.venv/bin/flask --app wsgi enroll-token
```

Open the printed local URL, enroll a passkey, then sign in. For HTTPS or service
deployment, review [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and every template
before enabling it.

## Connect a local MCP client

Install the optional MCP dependencies in the same environment:

```bash
.venv/bin/python -m pip install -r requirements-mcp.txt
```

Configure your client's local MCP command to run
`scripts/openhealthatlas_mcp.py` with `--database` pointing to your initialized
OpenHealthAtlas health database and `--timezone` set to your data's civil
timezone. The client starts the server; Hermes and a separate web server are
not required. Configure any model or provider credentials in that client.

The connection exposes data discovery, bounded queries, analysis and evidence
verification. Analysis runs as background work that the client can check.
Canonical records are read-only through this connection. Existing imports and
record-entry workflows remain available separately.

See [Local MCP setup](docs/LOCAL_MCP.md) for a client configuration example,
first-use checks, supported platforms and background-task behaviour.

## Fictional demonstration

The complete product-boundary proof initializes empty databases, imports 45
fictional Google Health-format days, writes fictional ratings through the
validated CLI, runs associations and the durable analysis ledger, exercises
authenticated UI/API reads:

```bash
export HERMES_DEMO_DIR="$(mktemp -d)/openhealthatlas-flow"
.venv/bin/python scripts/demo_flow.py --data-dir "$HERMES_DEMO_DIR"
```

The JSON report records the fictional import, deterministic ledger and UI/API
results. No model, network or external account is used.

For interactive browser exploration, use an isolated development data
directory:

```bash
export HERMES_DEV_DATA_DIR="$(mktemp -d)/openhealthatlas-demo"
.venv/bin/python scripts/devserver.py
```

Open <http://localhost:5111/dev-login>. The generated dataset covers wearable
metrics, subjective logs, training, laterality, nutrition, labs, mobility,
pain/rehabilitation, recovery, provenance, subregion exposure, and relative
strength-theory surfaces. Every record is fictional mock data. The Recovery
breakdown is pinned by server configuration to 2026-06-30 and shows the
current deterministic result with explicit fictional-data and evidence metadata;
browser query parameters cannot select another fixture or range. Use `Ctrl-C`
to stop the server.

## Test

```bash
PATH=".venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

Some Unix-socket tests require a platform that permits local `AF_UNIX` sockets.
Commands, partitions, and the latest release evidence are documented in
[docs/TESTING.md](docs/TESTING.md).

## Creator and attribution

OpenHealthAtlas was created and is maintained by **Kajeesan Jeevendra**. The
project's product architecture, privacy and provenance model, validation
workflow, deterministic analysis integration, interpretation boundaries, and
user experience were developed and assembled through an AI-assisted
development process.

The MIT license requires retaining its copyright and permission notices in
copies or substantial portions of the software. For academic or portfolio citation, use
[CITATION.cff](CITATION.cff). Third-party work and research sources are recorded
separately so that credit remains clear.

## License and third-party work

Open Health Atlas project code is licensed under the [MIT License](LICENSE).
Bundled third-party components retain their own licenses and notices.

- Project attribution: [NOTICE](NOTICE)
- Vendored software and body-map attribution:
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- Research and method references: [REFERENCES.md](REFERENCES.md)

## Read next

- [Architecture](docs/ARCHITECTURE.md)
- [System design](docs/SYSTEM_DESIGN.md)
- [Subregion inference boundary](docs/SUBREGION_INFERENCE.md)
- [Privacy](docs/PRIVACY.md)
- [Security policy](SECURITY.md)
- [Testing](docs/TESTING.md)
- [Release verification](VERIFICATION.md)
- [Independent integrated review](INDEPENDENT_REVIEW.md)
- [Feature retention matrix](FEATURE_RETENTION_MATRIX.md)
- [Public release manifest](RELEASE_MANIFEST.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Known unfinished behaviour](UNIMPLEMENTED.md)
- [Contributing](CONTRIBUTING.md)
