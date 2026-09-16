# Local MCP setup

The optional OpenHealthAtlas MCP server lets a compatible AI client inspect
and analyse an existing local health database. Choose the model in your client.
The server supplies deterministic records and evidence, and makes no model or
provider calls itself.

## Install and connect

Use Python 3.11 or newer on macOS or Linux. The current analytical engine uses
POSIX file locks; native Windows execution is not supported by this entry
point. A client's support for launching a server in a separate Linux
environment depends on that client.

From the downloaded OpenHealthAtlas directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mcp.txt
```

Use an existing OpenHealthAtlas health database, or follow the README's
initialization and import workflow first. This server does not initialize,
migrate or import a database. An arbitrary CSV, PDF or SQLite database must
be imported into OpenHealthAtlas through its existing supported import tools
before these features can query it.

Add a local **stdio** MCP server to your AI client. For clients that accept
the common `mcpServers` configuration format, replace the paths below with
absolute paths on your own computer:

```json
{
  "mcpServers": {
    "openhealthatlas": {
      "command": "/absolute/path/to/OpenHealthAtlas/.venv/bin/python",
      "args": [
        "-B",
        "/absolute/path/to/OpenHealthAtlas/scripts/openhealthatlas_mcp.py",
        "--database", "/absolute/path/to/health.db",
        "--timezone", "Europe/Paris"
      ]
    }
  }
}
```

Choose the IANA timezone that defines your data's calendar days. The default is
`UTC`. The client starts the MCP process and communicates over its standard
input/output; no web server or network listener is started. Provider
credentials belong in the AI client, not this configuration.

Clients that only accept remote MCP connections need a separately configured
transport bridge or tunnel. For example, OpenAI's Secure MCP Tunnel can forward
requests to a local stdio server when the relevant account and workspace access
are configured. The local server does not configure or activate a tunnel.

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
