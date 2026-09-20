# Start here

Install Open Health Atlas, connect your preferred AI, and try a question with fictional records. You can do this without Telegram, Hermes, Google Health, Hevy or a rented server.

This guide covers the **0.2.4 Mac preview**, checked on 20 September 2026. It is for **Apple silicon Macs**: open Apple menu → About This Mac and look for an Apple M-series chip. Other computers can use the [source setup](LOCAL_MCP.md), which needs an agent or technical help. There is no Windows or Linux desktop installer in this release.

**Your route:** Install → Try fictional data → Connect your AI → Ask your first question.

[Optional connections and always-on hosting →](OPTIONAL.md)

## Before you begin

You need an AI app that can connect to a local MCP server. MCP is the connection that lets your AI ask Open Health Atlas for records and calculations. The model stays in your chosen AI app.

The particular connection this release provides is called **local stdio MCP**. Your AI app must be able to launch a small program on the same computer as Open Health Atlas. A settings box that accepts only a website address is not enough. Ask your setup agent to check your exact app and version before changing anything.

The dashboard works without AI. Connecting AI is the main next step in this guide because it lets you ask questions about your records.

### What can the agent do for me?

Use an agent that can work with apps and files on your computer. A chat-only assistant can explain the steps but cannot install or configure them for you. Copy one whole prompt below into your setup agent; you do not need to understand the technical instructions inside it.

You still handle account sign-in, purchases, private credential entry and decisions about sharing personal records. Start with fictional data. A cloud AI can receive the records its tools return, even though Open Health Atlas stores the database locally.

## 1. Install Open Health Atlas

1. Open the [0.2.4 release page](https://github.com/kajeesan/Open-Health-Atlas/releases/tag/v0.2.4).
2. Under **Assets**, download `openhealthatlas-0.2.4-macos-arm64-signed-notarized.dmg`. Do not choose a source-code archive or an older `local-unsigned` file.
3. Open the downloaded file. Drag the **Open Health Atlas** icon onto **Applications**. If replacing an older version, first [back up your workspaces](DESKTOP.md#update-back-up-or-remove-the-app), quit the app and disconnect its AI clients.
4. Eject the installer, then open **Open Health Atlas** from Applications.
5. Select **Try fictional data**, check the timezone, and choose **Continue**. Wait for the dashboard to open.

You do not need to install Python, use Terminal or buy a model subscription for this step. This is a signed and notarized **preview**, not a claim of support on every Mac or macOS version. Keep normal macOS security protections enabled. See [installation and release status](DESKTOP.md) if the app will not open.

**Finished when:** the dashboard opens and its workspace is labelled fictional.

### Copy to your setup agent

```text
Help me install Open Health Atlas's Mac preview and open fictional data.
Use https://github.com/kajeesan/Open-Health-Atlas/releases/tag/v0.2.4 and
its matching documentation. Check my OS and Apple silicon compatibility.
For this release, use the signed-notarized macos-arm64 DMG, verify it
against the published checksum, and preserve normal Gatekeeper checks.
If I already have the app, identify its version and preserve its workspaces;
do not overwrite an existing installation or database without checking it.
Install in Applications, then help me select Try fictional data, my correct
timezone and Continue. Verify that the fictional dashboard actually opens.
Do not import personal records, connect a provider, enable background jobs,
or access unrelated private folders. Do not edit the signed app bundle.
If my computer is unsupported, explain the source-install alternative
before doing anything else. Finish with how to open and quit the app.
```

## 2. Connect your preferred AI

Do this while the **fictional workspace** is selected.

1. Choose **Desktop help & AI connection** in Open Health Atlas. In a narrow window, find it under **More**. During initial setup, the link is called **Help**.
2. Find **Connect an AI client**. Check the workspace named there.
3. Choose **Download settings** to save a local connection file. **Copy connection settings** also works if your AI app accepts pasted configuration.
4. In your AI app, add a **local MCP server** using that file. If it asks for a command and arguments separately, let your setup agent translate the file into those fields.
5. Reconnect or restart the AI app if it requests it. Choose your model in that app.

Keep the downloaded settings file private: it contains local file paths. There are no API keys to enter in Open Health Atlas. Any model credentials belong in the AI app's own settings.

**Finished when:** the AI app discovers these tools from the Open Health Atlas connection:

| Tool name | What it does |
| --- | --- |
| `health_catalog` | Finds the measurements you can ask about. |
| `health_query` | Reads values, summaries and comparisons. |
| `health_analyze` | Calculates patterns across selected measurements. |
| `health_task_status` | Checks whether a calculation has finished. |
| `health_evidence` | Checks the evidence behind a result. |

The connection reads health records; it cannot add or edit them. Use the app's entry forms and supported imports for that. Tool names may have a client-added prefix.

### Copy to your setup agent

```text
Connect my preferred AI app to Open Health Atlas using local stdio MCP.
Ask which AI app I want if I have not named it. Check that exact app/version's
official local-MCP instructions. If it only supports remote URLs, explain
that this release's local connection will not work directly; do not invent
an endpoint, expose a port or upload my database as a workaround.

Use the fictional workspace in my installed Open Health Atlas app. Help me
open Help > Connect an AI client > Download settings. Read the local settings
file I select; do not ask me to paste it into an online chat. Preserve the
exported command, --workspace and --timezone arguments. Back up the AI
client's existing configuration locally and merge only the openhealthatlas
server entry, adapting its format if required. Preserve other connections.
Use the bundled executable; do not install another Python or edit the app.

Keep any provider credentials in the AI client's own secure settings.
Explain whether my chosen model processes tool results locally or in a
cloud before connecting personal data. Configure fictional data only now.
Reconnect the client and verify discovery of health_catalog, health_query,
health_analyze, health_evidence and health_task_status. Test a fictional
query through the actual client, not just a settings-file check. If you
cannot access that client, give me the exact final steps and mark the
connection unverified. Tell me how to disconnect and restore the backup.
```

## 3. Ask your first question

Paste this into the **connected AI app**:

```text
Use the Open Health Atlas tools connected to my fictional workspace.
First discover the available sleep and training measurements with
health_catalog. Use health_query to find available dates, then summarize
a week that actually contains records. Show the dates, units, missing data
and sources. Verify a complete returned evidence reference with
health_evidence and wait for it with health_task_status.
If a tool is unavailable or fails, tell me; do not make up an answer.
Label the records fictional and distinguish recorded facts from your
interpretation. Do not change records or describe an association as a cause.
```

A useful answer includes the period it inspected and the source of the values. A confident paragraph without any successful tool calls does not prove the connection works.

For a second question, ask whether two available measurements appear related. Calculations may take several minutes. The AI should check the existing task, rather than repeatedly starting new analyses. “Insufficient data” can be the correct answer.

## 4. Move to your own records when you are ready

Create an empty personal workspace or import a **copy of a compatible Open Health Atlas database**. An arbitrary PDF, spreadsheet or another app's database is not a workspace. The [installation guide's data choices](DESKTOP.md#choose-your-data) explain this distinction.

Before connecting personal records, decide which AI provider may receive them. If you want processing to stay on your computer, your client and model must both support and use local processing; the words “local MCP” alone do not establish this.

Open your personal workspace, export its connection settings, and reconnect the client with those settings. **Changing the dashboard's workspace does not switch an existing AI connection.** Confirm the selected workspace before asking a health question.

### Copy to your setup agent

```text
Help me move from a fictional Open Health Atlas trial to a personal workspace.
First ask which data source I want and where AI processing may happen. Do
not read, import or transmit personal records until I have selected that
source and explicitly approved the destination. Explain any cloud transfer.

Use the app's empty-workspace or compatible-copy import flow. Preserve the
original database and keep fictional records separate. For other formats,
check the supported importer instead of assuming a PDF or spreadsheet works.
Do not write directly to SQLite or repoint collectors at a guessed path.
After the selected workspace opens successfully, export its connection
settings and reconnect my AI client. Verify the selected workspace without
printing health contents or private paths into chat. Explain backup and
restore steps and how to disconnect the client. Mark anything untested.
```

## If you get stuck

| What you see | What to do |
| --- | --- |
| The AI app asks only for a URL | Ask the agent to check for local stdio support. Do not paste the dashboard address. |
| No Open Health Atlas tools appear | Check the exported app location, reconnect the client, and confirm local-server support. |
| Tools appear but the answer has no records | Try a date range present in the selected workspace; an empty workspace has no health history. |
| The AI still sees fictional records | Export the personal workspace's settings and replace that connection explicitly. |
| A calculation is busy | Wait on its existing task; do not repeatedly submit the same question. |
| A connection breaks after an update | Disconnect it, open the updated app and workspace successfully, then reconnect. Export again if the app location, workspace or timezone changed. |
| You want to stop access | Disconnect or remove Open Health Atlas in the AI client's MCP settings. Closing the dashboard alone does not disconnect the client. |

For technical detail, see [desktop MCP troubleshooting](DESKTOP_MCP.md#troubleshooting). Share only redacted diagnostics, never a database, connection file or API key.

## Optional next steps

You have completed the main setup once your chosen AI can answer a fictional question using the tools. Continue to **[Optional](OPTIONAL.md)** only for the extras you want: Hermes, Telegram, Google Health, Hevy or an always-on Hostinger VPS.
