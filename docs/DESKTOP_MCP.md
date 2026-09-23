# Connect a local AI client

For a walkthrough with copy-paste setup-agent prompts, use
[Start here](GETTING_STARTED.md#2-connect-your-preferred-ai). This page owns
the desktop connection details and troubleshooting.

Open Health Atlas works without an AI account, subscription or API key. The
optional connection lets a compatible AI client read your selected workspace
and request deterministic calculations and supporting evidence. Your client
provides the model and conversation.

## Guided ChatGPT desktop setup (unreleased)

The Connect AI page starts with **Connect with your agent**. Copy the prepared
instructions into a desktop agent that can access local files and tools, such
as a local Codex task. The agent can back up and merge the connection settings,
reconnect the client and check the health tools. A browser or phone chat cannot
perform this local setup just from the copied text.

Choose **It's connected** to try a small query in the selected workspace. This
records your report; it does not verify the connection automatically. Personal
records still require explicit sharing approval. Choose **I need help** to go
directly to the manual steps. The MCP settings step includes help for clients
without **MCP servers** or **STDIO** settings.
Back follows the route you took. The
[official OpenAI MCP guide](https://learn.chatgpt.com/docs/extend/mcp) describes
the supported desktop connection.

The manual route keeps the existing workspace, command, arguments and restart
instructions. Copying never advances the guide. The app itself does not edit
another client's settings, create a tunnel or read health records. The published
0.2.6 installer remains unchanged until a new release is built. Other local AI
clients can still use the secondary **Other AI apps / setup agent** instructions.

## Connect from the installed macOS app

1. Install Open Health Atlas and open its fictional workspace.
2. Open **Desktop help & AI connection → Connect AI** and choose **Copy setup instructions**. The copied text combines the task for your setup agent with the selected workspace's actual connection JSON.
3. Paste into a setup agent such as Codex. It should verify local stdio support, adapt the configuration to your preferred AI client, preserve unrelated settings and reconnect that client.
4. Verify that `health_catalog`, `health_query`, `health_analyze`, `health_evidence` and `health_task_status` are available and a fictional query and evidence check succeed.

**View connection settings** reveals exactly what the button copies. The underlying configuration endpoint remains available for existing clients and packaged verification, but the help page uses one copy action. The common `mcpServers` JSON may need conversion to the chosen client's configuration format. Remote-only clients cannot use this local executable directly.

The command points to the installed app's stable
`Contents/MacOS/openhealthatlas-mcp` executable. It includes its own runtime;
there is no Python installation, source checkout, Terminal command or model
installation for the person connecting it. Its arguments select the persistent
workspace and calendar timezone explicitly. Each new connection resolves that
workspace's current database generation. After an app update, open that
workspace successfully in the updated app before reconnecting the client, so
it selects the prepared database. Keep the app in its installed location. If
you move the app or want the client to use a different workspace or timezone,
copy the setup instructions again and reconnect the client. An existing client
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
  was when you copied the instructions. Copy fresh instructions after moving it.
- **The client still shows the previous workspace:** switching the dashboard
  does not change the client's selection. Open the workspace you want, copy
  its setup instructions and reconnect the client with those settings.
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
