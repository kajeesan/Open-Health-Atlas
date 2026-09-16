# Local MCP setup

The optional OpenHealthAtlas MCP server lets a compatible AI client inspect
and analyse an existing local health database. Choose the model in your client.
The server supplies deterministic records and evidence, and makes no model or
provider calls itself.

## Install and create a fictional database

Use Git and Python 3.11 or newer on macOS or Linux. The current analytical
engine uses POSIX file locks; native Windows execution is not supported by
this entry point. A client's support for launching a server in a separate
Linux environment depends on that client.

Run this in one terminal. If you already cloned the repository, start from
its root and skip the first two lines. Dependency installation needs internet
access; generating and querying these fictional records does not.

```bash
git clone https://github.com/kajeesan/Open-Health-Atlas.git
cd Open-Health-Atlas
oha_mcp_env="$(mktemp -d)/venv"
python3 -m venv "$oha_mcp_env"
"$oha_mcp_env/bin/python" -m pip install -r requirements-mcp.txt
export PYTHONDONTWRITEBYTECODE=1
export HERMES_TIMEZONE=UTC
unset HERMES_HEVY_QUARTERLY_CONFIG
oha_mcp_data="$(mktemp -d)"
"$oha_mcp_env/bin/python" scripts/make_demo_db.py \
  --output "$oha_mcp_data/health-demo.db" --anchor-date 2026-06-30
printf 'Python: %s\nServer: %s\nDatabase: %s\n' \
  "$oha_mcp_env/bin/python" "$PWD/scripts/openhealthatlas_mcp.py" \
  "$oha_mcp_data/health-demo.db"
```

Use the three printed absolute paths in the configuration below. Temporary
paths are for this trial; move to a persistent external environment and data
directory for ongoing use, then update the client configuration.

To use your own data later, select an existing OpenHealthAtlas database or
follow the [initialization and import guidance](DEVELOPMENT.md#verify-the-setup).
The MCP server does not initialize, migrate or import a database. An arbitrary
CSV, PDF or SQLite file is not an OpenHealthAtlas database; use a supported
import format and inspect its CLI help first.

## Connect your client

Add a local **stdio** MCP server to your AI client. For clients that accept
the common `mcpServers` configuration format, replace the paths below with
absolute paths on your own computer:

```json
{
  "mcpServers": {
    "openhealthatlas": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": [
        "-B",
        "/absolute/path/to/Open-Health-Atlas/scripts/openhealthatlas_mcp.py",
        "--database", "/absolute/path/to/health-demo.db",
        "--timezone", "UTC"
      ]
    }
  }
}
```

Choose the IANA timezone that defines your data's calendar days. The default is
`UTC`. The client starts the MCP process and communicates over its standard
input/output; no web server or network listener is started. Provider
credentials belong in the AI client, not this configuration.

Clients that only accept remote MCP connections cannot use this stdio
configuration directly. Any remote transport requires a separate setup; this
guide verifies only the local server. The server itself makes no model calls,
but your client may send returned records to its configured model provider.
A local database is not a guarantee that the client processes results locally.

## First use

After connecting, check that the client lists the tools below. Start with a
question such as “Which sleep and training measurements are available?” Then
ask about a specific date range present in your records.

| Tool | Purpose |
| --- | --- |
| `health_catalog` | Discover available registered features, their units and supported operations. |
| `health_query` | Read latest values, daily series, summaries or a period comparison. |
| `health_analyze` | Start a bounded analysis for an outcome and selected exposures. |
| `health_evidence` | Start verification of complete evidence references returned by earlier results. |
| `health_task_status` | Wait briefly for a background task and retrieve its result. |

The client should discover feature keys before querying, use explicit date
ranges and pass complete returned evidence-reference objects when verifying
an answer. Returned records preserve source, time, units, missingness and
calculation limitations. The model supplies the interpretation.

### Concrete trial requests

For the fictional database above, ask your client to use these tool calls in
order. Clients expose tool invocation differently; these are tool names and
argument objects, not text to paste into a shell.

1. Discover sleep features with `health_catalog`:

   ```json
   {"search": "sleep", "limit": 5}
   ```

2. Read the final week with `health_query`:

   ```json
   {"feature_keys": ["sleep.duration_hours"], "range": {"from": "2026-06-24", "to": "2026-06-30"}, "view": "summary"}
   ```

   Expect an `ok` result with facts, units and `evidence_refs`.

3. Request the retained full-range example with `health_analyze`:

   ```json
   {"outcome_key": "subjective.energy", "exposure_keys": ["sleep.duration_hours"], "range": {"from": "2026-03-02", "to": "2026-06-30"}, "mode": "ordinal", "top": 1}
   ```

   Pass the returned `task_id` to `health_task_status` with `wait_seconds: 5`
   until it completes. The bundled fixture has a successful result with
   findings; a shorter or sparse range may correctly return `insufficient_data`.

4. Call `health_evidence` with an `evidence_refs` array containing a complete
   reference object from the query or completed analysis. Poll its task in the
   same way. Do not substitute a hash or a text summary for the object.

The [existing SDK tests](DEVELOPMENT.md#run-the-right-checks) exercise discovery,
query, positive analysis, evidence verification and refusals without a model.
A model client's own tool selection and final wording require separate review.

## Long analyses

Analysis and evidence verification return a task identifier promptly. The
client checks `health_task_status` until it receives the completed result or
an error. Status supports a bounded wait to avoid rapid polling. One heavy
task runs at a time; repeated submissions of the same pending request reuse
that task. A different heavy request receives a busy response and can be
submitted again after the current task finishes.

Task handles belong to the running MCP process. If the client restarts it,
submit the request again. Exact analytical caches can still be reused when
their database, code, request and configuration match. Completed task results
are snapshots from their calculation; submit a fresh request or verify its
evidence when you need to check for changed records.

The server retains up to eight completed task results for ten minutes. A worker
has a 570-second execution limit. Normal client disconnect shuts down its
worker; if the server is terminated abruptly, the worker's own execution limit
still applies.

## Data and existing workflows

The database is selected at startup. A tool request cannot switch databases,
provide raw SQL, open an arbitrary file or write health records. The canonical
health database is opened read-only. Private derived cache and lock files may
be created beside it; they are not additional canonical health records.

Each database has an opaque dataset identity. Evidence from another configured
database, or from the separate fictional Hermes adapter, cannot be reused as
if it belonged to this connection. Moving the database to another path changes
that identity; ask for fresh evidence after moving it.

Existing panel, import, manual-entry, Hermes and Telegram paths continue to
use their own entry points. The standalone MCP server does not install Hermes,
change service configuration, enable schedules, or add built-in API-key chat.
The client's normal provider settings determine where returned tool results
are processed.
