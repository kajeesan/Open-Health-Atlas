# Start here

Install Open Health Atlas, connect your preferred AI, and try a question with fictional records. You can do this without Telegram, Hermes, Google Health, Hevy or a rented server.

This guide covers the **0.2.7 Mac preview**. It is for **Apple silicon Macs**: open Apple menu → About This Mac and look for an Apple M-series chip. Other computers can use the [source setup](LOCAL_MCP.md), which needs an agent or technical help. There is no Windows or Linux desktop installer in this release.

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

1. Open the [0.2.7 release page](https://github.com/kajeesan/Open-Health-Atlas/releases/tag/v0.2.7).
2. Under **Assets**, download `openhealthatlas-0.2.7-macos-arm64-signed-notarized.dmg`. Do not choose a source-code archive or an older `local-unsigned` file.
3. Open the downloaded file. Drag the **Open Health Atlas** icon onto **Applications**. If replacing an older version, first [back up your workspaces](DESKTOP.md#update-back-up-or-remove-the-app), quit the app and disconnect its AI clients.
4. Eject the installer, then open **Open Health Atlas** from Applications.
5. Select **Try fictional data**, check the timezone, and choose **Continue**. Wait for the dashboard to open.

You do not need to install Python, use Terminal or buy a model subscription for this step. This is a signed and notarized **preview**, not a claim of support on every Mac or macOS version. Keep normal macOS security protections enabled. See [installation and release status](DESKTOP.md) if the app will not open.

**Finished when:** the dashboard opens and its workspace is labelled fictional.

### Copy to your setup agent

```text
Help me install Open Health Atlas's Mac preview and open fictional data.
Use https://github.com/kajeesan/Open-Health-Atlas/releases/tag/v0.2.7 and
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

For the guided ChatGPT Desktop flow, see
[Guided ChatGPT desktop setup](DESKTOP_MCP.md#guided-chatgpt-desktop-setup).

Do this while the **fictional workspace** is selected.

1. Open **Desktop help & AI connection**. In a narrow window, find it under **More**.
2. Select the **Connect AI** tab and check the workspace and scope shown. The timezone is included in the setup instructions.
3. Choose **Copy setup instructions**. Paste it into a local desktop agent that can access files and tools on this Mac.
4. When the agent reports that it finished, choose **It's connected**. The next message asks it to discover the five tools and run a small read-only check.
5. If the agent cannot access the Mac, choose **I need help**. Continue manually only after confirming ChatGPT Desktop shows Settings → MCP servers → Add server → STDIO.

**View setup instructions** shows the exact agent instruction before you copy it. After the agent reports completion, the guide offers a separate **Copy test message** for the verification step. The copied text includes local file paths, but no health records or passwords. Keep it private.

The agent should check that your AI app supports **local stdio MCP**. A remote-URL-only client, browser chat or phone chat cannot launch the bundled local connection. Model credentials belong in your AI app's secure settings, never in this instruction.

**Finished when:** the AI app can actually call `health_catalog`, `health_query`, `health_analyze`, `health_evidence` and `health_task_status`, and a fictional query and evidence check succeed. Saving settings alone is not a completed connection.

### Copy to your setup agent

```text
Help me connect my preferred AI app to Open Health Atlas. Ask which app only
if it is not clear from our conversation. Check its official local stdio MCP
setup instructions. If it only supports remote URLs, explain that limitation;
do not expose a server or upload my database as a workaround.

Help me open a fictional workspace, then Desktop help & AI connection >
Connect AI > Copy setup instructions. Use the connection JSON included in
that copied instruction. If I have not supplied it, guide me to copy it; do
not guess paths. Preserve the command, workspace and timezone. Back up and
merge the client's configuration without changing unrelated connections.
Use the installed app's bundled executable and leave the signed app intact.

Keep provider credentials in the AI client's secure settings. Explain where
its model processes tool results before connecting personal records. Verify
real tool discovery, a fictional query and evidence verification in the actual
client. Mark anything you could not test, and explain how to disconnect.
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

Open your personal workspace, copy its setup instructions again, and ask your agent to update the connection. **Changing the dashboard's workspace does not switch an existing AI connection.** Confirm the selected workspace before asking a health question.

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
After the selected workspace opens successfully, copy its setup instructions and update my AI connection. Verify the selected workspace without
printing health contents or private paths into chat. Explain backup and
restore steps and how to disconnect the client. Mark anything untested.
```

## If you get stuck

| What you see | What to do |
| --- | --- |
| The AI app asks only for a URL | Ask the agent to check for local stdio support. Do not paste the dashboard address. |
| No Open Health Atlas tools appear | Check the copied app location, reconnect the client, and confirm local-server support. |
| Tools appear but the answer has no records | Try a date range present in the selected workspace; an empty workspace has no health history. |
| The AI still sees fictional records | Copy the personal workspace's setup instructions and ask your agent to update that connection. |
| A calculation is busy | Wait on its existing task; do not repeatedly submit the same question. |
| A connection breaks after an update | Disconnect it, open the updated app and workspace successfully, then reconnect. Copy fresh instructions if the app location, workspace or timezone changed. |
| You want to stop access | Disconnect or remove Open Health Atlas in the AI client's MCP settings. Closing the dashboard alone does not disconnect the client. |

For technical detail, see [desktop MCP troubleshooting](DESKTOP_MCP.md#troubleshooting). Share only redacted diagnostics, never a database, connection file or API key.

## Optional next steps

You have completed the main setup once your chosen AI can answer a fictional question using the tools. Open **Full experience setup** in the app, or read **[Optional](OPTIONAL.md)**, for the extras you want: Hevy, Google Health and Cronometer, followed by advanced Hostinger, Hermes and Telegram setup.
