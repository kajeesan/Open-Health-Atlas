# Connect a local AI client

Open Health Atlas works without an AI account, subscription or API key. The
optional connection lets a compatible AI client read your selected workspace
and request deterministic calculations and supporting evidence. Your client
provides the model and conversation.

## Connect from the installed macOS app

1. Install Open Health Atlas in Applications and complete its workspace setup.
   Start with the clearly labelled fictional workspace to try the connection.
2. Open the app's help and local AI connection page. Copy or download the
   connection configuration for the current workspace.
3. In an AI client that supports **local stdio MCP servers**, add the exported
   server configuration using that client's instructions, then restart or
   reconnect the client if required.
4. Check that the client lists `health_catalog`, `health_query`,
   `health_analyze`, `health_evidence` and `health_task_status`.

The exported JSON uses the common `mcpServers` format. Some clients need the
command and arguments entered separately. This format alone does not certify
any particular client's support. Remote-only clients cannot launch this local
executable; no remote transport or universal ChatGPT compatibility is claimed.

The command points to the installed app's stable
`Contents/MacOS/openhealthatlas-mcp` executable. It includes its own runtime;
there is no Python installation, source checkout, Terminal command or model
installation for the person connecting it. Its arguments select the persistent
workspace and calendar timezone explicitly. Each new connection resolves that
workspace's current database generation. After an app update, open that
workspace successfully in the updated app before reconnecting the client, so
it selects the prepared database. Keep the app in its installed location. If
you move the app or want the client to use a different workspace or timezone,
export the configuration again and reconnect the client. An existing client
connection stays attached to the workspace named in its configuration, even
when a different workspace is selected in the dashboard.

The client starts this server independently of the dashboard window. Closing
the dashboard does not end an AI client's connection; disconnect it in the
client. Quit/disconnect AI clients before replacing the app during an upgrade.
Then open the updated app and the workspace you want to connect. Wait until
that workspace opens successfully before reconnecting the client. The MCP
connection does not prepare or migrate workspaces itself.

## What is shared

The server opens the selected canonical database read-only, starts no network
listener and makes no provider calls. Separate derived analysis cache and lock
files can be created next to the database. Health record changes continue
through the app's validated entry and import paths. Existing Hermes and
Telegram setups remain independent.

Your AI client can send returned records and evidence to its chosen provider.
Review its data and provider settings before connecting personal records.
Exported configuration contains local file paths but no API keys or health
records. Keep it private and do not attach it to public support reports.
Provider credentials belong in the AI client's secure settings, never in this
configuration. Open Health Atlas does not silently connect a provider or
download model weights.

The local protocol treats the explicitly selected database as its dataset;
fictional desktop records remain fictional even if a protocol result uses the
legacy local-surface `data_class: personal` field. The dashboard's fictional
workspace label and the selected connection are the source of that distinction.
Use the separate fictional workspace for demonstrations and verification.

## Try it with fictional records

Ask the client which sleep and training measurements are available, then ask
for a summary of a date range present in the fictional workspace. Have it use
feature keys discovered by `health_catalog` and preserve source, date, units,
missingness and evidence when describing results.

Analysis returns a task identifier. The client should wait with
`health_task_status` and then verify a complete returned evidence reference
with `health_evidence`. Task handles last only for that server process. A
restart needs a new request. The [local protocol guide](LOCAL_MCP.md#concrete-trial-requests)
contains exact tool arguments for the pinned verification fixture; the
installed demonstration may use a different date range.

## Troubleshooting

- **The client cannot find the server:** confirm the app is installed where it
  was when you exported the configuration. Export again after moving it.
- **The client still shows the previous workspace:** switching the dashboard
  does not change the client's selection. Open the workspace you want, export
  its connection settings and reconnect the client with those settings.
- **No tools appear:** confirm the client supports local stdio servers. A box
  that accepts only an HTTPS URL is a different transport.
- **Analysis says insufficient data:** this is a valid result for short,
  missing or sparse observations. Try a suitable date range; do not treat it
  as a finding.
- **The server is busy:** check the existing task first. One heavy analysis or
  evidence task runs at a time.
- **The app was just upgraded:** disconnect the client, then open the updated
  app and the workspace you want to connect. Once it opens successfully,
  reconnect the client. If you already reconnected before opening the updated
  app, reconnect once more. An unchanged workspace, timezone and app location
  do not need a new configuration.

## Packaged protocol verification

Release maintainers run the real official Python MCP SDK against the produced
app executable, using generated fictional records and an isolated working
directory. The acceptance command is:

```bash
python scripts/verify_desktop_mcp.py \
  --executable '/absolute/path/Open Health Atlas.app/Contents/MacOS/openhealthatlas-mcp' \
  --desktop-workspace
```

This developer-side verification needs `requirements-mcp.txt` in its test
environment. The installed server uses only its bundled runtime. The verifier
checks discovery, query, successful analysis with findings, evidence replay,
task polling, an unchanged canonical database, reconnection after a workspace
generation change and cleanup on client disconnect, including a real running
worker. This protocol check advances the workspace selection directly; the
app's migration and recovery journey is verified separately. The protocol
verifier's JSON summary contains no records or private paths. Release evidence must name the artifact actually
exercised; source-level tests do not prove packaged compatibility. No named
consumer AI client's interface or model interpretation is certified by this
SDK verification.
