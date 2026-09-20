# Optional

[← Back to the main setup](GETTING_STARTED.md)

You do not need anything on this page to use Open Health Atlas with a compatible local AI client. Add one connection at a time and check it before adding another.

| I want to… | Optional section | What it needs |
| --- | --- | --- |
| Use Hermes as my AI assistant | [Hermes](#hermes) | Hermes and a model configured in it. |
| Talk to that assistant from my phone | [Telegram](#telegram) | A working Hermes connection and your own Telegram bot. |
| Import supported wearable measurements | [Google Health](#google-health) | Google account/API access and a separately configured collector. |
| Import workouts | [Hevy](#hevy) | Hevy API access and a separately configured collector. |
| Keep services running when my laptop is off | [Hostinger VPS](#hostinger-vps) | A Hostinger KVM VPS, upkeep and a separate server installation. |

Each section has a complete prompt you can copy into a setup agent. You handle sign-in, private credential entry and purchases. The prompts ask the agent to verify the result and explain how to stop the connection.

## Before adding collectors or hosting

**The Mac app and a server are separate installations.** A server cannot reach your Mac's local workspace just because it uses the same account name. Importing an existing database into the Mac app makes a copy; it does not create live synchronization.

The supplied Hevy and Google Health collectors target Linux services. They are not installed by the Mac app and should not be pasted into a Mac terminal. An agent must first establish a compatible Linux installation and explicitly choose the database those collectors will update. If you only want the Mac app, keep using its supported local entry/import paths until a desktop collector route is verified.

For a local Hermes connection, finish [Connect your preferred AI](GETTING_STARTED.md#2-connect-your-preferred-ai) on the same computer first. For a server installation, use the [Hostinger VPS section](#hostinger-vps) before adding its collectors or Telegram gateway. Do not set up an empty server and assume it already contains your Mac records.

## Hermes

**Choose this if:** you want Hermes to be your AI assistant, or you plan to add its Telegram gateway later. Hermes is an external agent product; Open Health Atlas does not install it.

### What you do

1. Follow the [official Hermes installation guidance](https://hermes-agent.nousresearch.com/docs/getting-started/installation/), with your setup agent's help.
2. Choose and configure a model in Hermes. Sign in or enter its credential through the local setup flow.
3. On the same Mac, export your fictional Open Health Atlas workspace's connection settings.
4. Ask the agent to add that local MCP connection to Hermes and verify a fictional question.

Hermes uses `mcp_servers` in its configuration, while the app exports `mcpServers` JSON. Your agent can translate the structure while keeping the command and arguments unchanged. See the [official Hermes MCP guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/).

**Finished when:** Hermes actually calls Open Health Atlas's tools and answers from the fictional workspace. This simple connection is read-only. It does not enable check-ins, record editing, reminders or the separate protected Hermes workflow adapters.

### Copy to your setup agent

```text
Set up optional Hermes as my Open Health Atlas AI client on this computer.
Use the official NousResearch/hermes-agent project and its current docs:
https://hermes-agent.nousresearch.com/docs/getting-started/installation/
https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/
Check for an existing installation first and preserve its settings.
Help me select a model and enter credentials privately, without printing
secrets or putting them in source control. Explain any subscription/API cost
and where model requests are processed. Do not purchase anything for me.

Use my installed Mac app's fictional workspace and locally exported
connection settings. Translate mcpServers JSON into Hermes's supported
mcp_servers structure, retaining the exact command and arguments. Back up
and merge configuration without replacing unrelated settings. Restrict this
connection to the five Open Health Atlas health tools. Do not enable Telegram,
schedules, broad filesystem access or record-writing tools as part of this.

Verify tool discovery, a fictional query, and evidence verification in the
actual Hermes session. Preserve dates, units and missing-data limitations.
If Hermes runs on another host, stop the local-path setup and explain the
separate source installation it needs; do not upload my Mac workspace.
Do not substitute deploy/hermes-openhealthatlas-mcp for the portable server:
that is a separate protected adapter with its own fictional/governance rules.
Report verified and unverified steps and how to remove only this connection.
```

**To stop:** remove or disable this MCP entry in Hermes and reconnect its session. If you later use other Hermes features, stop their services separately.

## Telegram

**Choose this if:** you want to message your Hermes assistant from your phone. Telegram is the messaging channel; Hermes still needs to run on a reachable computer or server.

### What you do

1. Complete the Hermes connection above and test it before adding messaging.
2. Open Telegram's official [@BotFather](https://core.telegram.org/bots/features#creating-a-new-bot). Send `/newbot` and follow its prompts to choose a bot name and username.
3. Store the bot token privately when BotFather supplies it. Do not paste it into an AI conversation or a public support report.
4. Open your new bot and press **Start**. Let the setup agent help identify your own numeric Telegram user ID and that private chat using the gateway's supported setup.
5. Configure Hermes's Telegram connection, restricted to your user ID. Send a simple test message yourself, followed by a question about fictional records.

Telegram messages and replies pass through Telegram and your configured Hermes/model setup. Decide what you are comfortable sharing before using personal health information. The [Hermes Telegram guide](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram/) covers its gateway configuration.

**Finished when:** your bot replies in the intended private chat and answers a fictional question using the tools. Check-ins, reaction buttons, recording health entries and scheduled reminders are additional workflows; a working chat alone does not prove those are configured.

### Copy to your setup agent

```text
Connect my already-working Hermes/Open Health Atlas fictional setup to my
own private Telegram bot. Follow the current official Hermes Telegram guide:
https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram/
First confirm which machine runs Hermes and that it can query the fictional
database. Help me create a bot through the official @BotFather if needed,
then let me enter its token privately in the local credential flow.
Never echo the token or include it in a command transcript or shared file.

Verify my own numeric user ID and the intended private chat. Set an explicit
user allowlist, never a wildcard or public group permission. Preserve any
existing bot configuration; do not start a second poller for the same token.
Explain the Telegram and model-provider data destinations before activation.
Ask me to send the test messages. Verify that the gateway replies and that
a fictional health answer includes a successful Open Health Atlas tool call.
Verify unauthorized-user rejection with a local synthetic test where possible;
do not claim it passed if it was only a configuration inspection.

Do not enable schedules, unsolicited health messages or record writes.
If I separately request Open Health Atlas action buttons/check-ins, inspect
config/telegram.env.example, deploy/hermes-telegram-integration.md,
deploy/hermes_telegram_gateway_extension.py and deploy/install-telegram-actions.sh
from the matching source release. These are separate from ordinary Hermes
chat: verify gateway compatibility and fictional callbacks before activation,
and preserve all existing real-data governance restrictions.
Finish with verified results, remaining gaps, and how to stop the gateway or
revoke this bot token through BotFather.
```

**If the bot is silent:** check that Hermes and its gateway are running, your own chat was started, and your user ID is allowed. Do not open the bot to everyone to troubleshoot it.

## Google Health

**Choose this if:** you have supported wearable data available through the Google Health API and want to import it. This is a health-data connection, not the AI model connection.

### What you do

1. Have your agent confirm a compatible Linux collector installation and the exact Open Health Atlas database it will update.
2. In [Google's setup guide](https://developers.google.com/health/setup), create or select a Cloud project, enable the Google Health API and create a web OAuth client. OAuth is the Google sign-in that lets you approve access.
3. Use the exact redirect address required by Open Health Atlas's helper: `http://localhost:8385`, unless your agent deliberately configures another supported port. The example address in Google's general tutorial is not this helper's callback.
4. Configure the requested read-only health permissions. Enter the client secret privately and complete Google sign-in yourself.
5. Ask the agent to run one agreed import, check what arrived and report missing categories. Enable a recurring schedule only if you want one.

Google's current API setup and account eligibility must be checked for your account. Do not assume every device or historical record is available. Google also documents seven-day refresh-token expiry for external OAuth apps in Testing mode with these scopes. The agent should check publishing and verification requirements before promising unattended access. See [Google's token rules](https://developers.google.com/identity/protocols/oauth2#expiration).

**Finished when:** an authorized import reaches the intended database and the imported dates and source labels are correct. A successful Google login alone is not a completed integration.

### Copy to your setup agent

```text
Set up the optional Google Health importer for Open Health Atlas.
First confirm my host, data source, selected database and permission to import.
Use the matching Open Health Atlas source release and read docs/DEPLOYMENT.md,
deploy/ghealth-auth, deploy/ghealth-sync, deploy/install-ghealth-sync.sh and
its service/timer files. These are Linux deployment tools, not Mac app setup.
If I only have the Mac app, explain that limitation before proposing a server;
do not point a Linux collector at a guessed desktop database generation.

Check https://developers.google.com/health/setup and Google's current OAuth
rules. Guide me through project/API enablement and a Web application OAuth
client. Match the helper's exact redirect http://localhost:8385 (or its
explicitly chosen port). For a headless server, use a reviewed SSH local
forward so the callback stays loopback-only. Request only the three scopes
used by this collector: googlehealth.sleep.readonly,
googlehealth.activity_and_fitness.readonly, and
googlehealth.health_metrics_and_measurements.readonly under the
https://www.googleapis.com/auth/ prefix. Verify current availability.

Let me enter the client secret privately and perform Google consent myself.
Check Testing-mode refresh-token expiry and actual publishing/verification
requirements; do not blindly promise permanent access or bypass warnings.
Keep OAuth configuration outside source with the helper's required root
ownership and 0600 permissions. Never print tokens or raw health payloads.

Review the service sandbox, credential loading, executable paths, interpreter,
timezone and effective health database under the actual collector and import
users. Confirm sudo's environment handling and writable data/cache paths.
Back up existing data consistently. Test the adapter with fictional input,
then run only the import I authorize through the validated health.py path.
Verify imported dates, source labels, partial-failure reporting and a repeat
run without duplicates. Leave the timer disabled until I approve a specific
schedule. State where the records now live and how I can view them; server
imports do not automatically synchronize the Mac app. Explain disabling the
timer and revoking Google access, and mark any unavailable API/data category.
```

**If syncing stops:** check credential expiry, revoked permission and per-category errors. Never replace missing measurements with zero to make a chart appear complete.

## Hevy

**Choose this if:** you log workouts in Hevy and want them in Open Health Atlas.

### What you do

1. Have your agent confirm the Linux collector setup and destination database.
2. Sign in to [Hevy's developer settings](https://hevy.com/settings?developer). Check whether your account can create an API key and what current access terms apply.
3. Enter the key into the agent-prepared private credential file, not into chat.
4. Approve one import and compare a known workout's date, exercises, sets and units.
5. Choose whether to enable scheduled imports after that check passes.

The [Hevy API documentation](https://api.hevyapp.com/docs/) describes provider access. Open Health Atlas's collector imports workouts, routines and exercise templates. Its separate Hevy MCP gateway is not the main Open Health Atlas AI connection, and is not needed simply to import workouts.

**Finished when:** the selected database contains the expected workout with correct units, and repeating the import does not duplicate it.

### Copy to your setup agent

```text
Connect optional Hevy workout imports to my selected Open Health Atlas
installation. Confirm my host and intended database first. Read the matching
source release's config/hevy.env.example, deploy/hevy-collector,
deploy/install-hevy-sync.sh and hermes-hevy-sync service/timer files.
These require a compatible Linux service setup; do not run them as Mac
installer steps or guess the desktop workspace's active database path.

Check https://api.hevyapp.com/docs/ and help me open my Hevy developer settings
at https://hevy.com/settings?developer. Verify account eligibility without
buying a plan. Let me enter my API key privately into the external credential
file. Preserve root:root 0600 protection required by the collector; never
print the key or give it to the model, panel or unprivileged data account.

Review interpreter/toolkit paths, timezone, sudo environment handling and
the actual database selected by the import user. Preserve existing services
and back up existing records consistently. Test with fictional data first,
then perform the import I approve through the validated CLI. Compare a known
workout's timestamp, exercises, sets and units. Run again and check for
unwanted duplicates. Check failure reporting without exposing raw records.
Leave the sync timer disabled until I approve its schedule. Explain how to
view the destination data, stop the timer and revoke the key. Do not promise
Mac/server synchronization. Do not add the separate Hevy MCP gateway,
modify workouts in Hevy or enable quarterly routine automation for this task.
```

**If nothing imports:** check API access, the selected account, collector status and destination database before retrying. An empty workout history is different from failed authentication.

## Hostinger VPS

**Choose this if:** you need an assistant or collectors to keep running when your computer is off. This guide uses a **Hostinger KVM VPS**, a rented Linux computer. You pay Hostinger for hosting and maintain the software running on it.

You do not need a VPS for the Mac dashboard or local MCP connection. A server running Hermes still needs a working connection to its selected Open Health Atlas database; your Mac's local file path will not work there.

### What you do

1. Sign in to Hostinger and choose **VPS hosting / KVM VPS**. Ordinary website hosting is a different product. Have your agent check the current plan resources, renewal price, billing term, backup costs and region against your budget before you buy. If you already have a VPS, use its existing dashboard instead.
2. For a new server, choose a **Plain OS** installation of **Ubuntu 24.04 LTS**, subject to current availability. Open Health Atlas does not need a website control panel or graphical desktop. Do not reinstall the operating system on an existing server to follow this guide: that can erase its data.
3. In Hostinger's **hPanel → VPS**, open your server's dashboard. Wait until it is running. Use its SSH connection details to let your agent set up access. SSH is the secure connection used to manage the server. Keep passwords and private keys out of chat; only the public SSH key belongs in the server's key settings.
4. Have the agent configure restricted access and prepare a separate **fictional** Open Health Atlas installation. Keep hPanel's browser terminal available while access is being configured, so you have a recovery route.
5. Check **Backups & Monitoring → Snapshots & Backups**, review the available schedule and any extra charge, and agree on a private backup arrangement. Have the agent test an application-data restore to a separate location. Restoring the entire VPS can overwrite its current contents.
6. Add Hermes and only the collectors you want. Approve their schedules, test a restart, and record who will maintain the server. Your Mac workspace is not automatically copied or synchronized.

Hostinger's official guides cover the [VPS dashboard](https://www.hostinger.com/support/5726606-how-to-use-the-vps-dashboard-in-hostinger/), [SSH access](https://www.hostinger.com/support/5723772-how-to-connect-to-your-vps-via-ssh-at-hostinger/) and [operating-system choices](https://www.hostinger.com/support/1583571-what-are-the-available-operating-systems-for-vps-at-hostinger/). Menu names can change; your agent should check the current dashboard rather than guess.

The [deployment guide](DEPLOYMENT.md) is the technical reference. The project supplies adaptable service examples; it does not supply a managed hosting service or automatic synchronization between installations.

**Finished when:** the selected fictional workflows survive a restart, access is restricted, a backup can be restored, and you know how to stop the services and their costs. “The server is running” is not enough.

### Copy to your setup agent

```text
Set up optional Open Health Atlas hosting on a Hostinger KVM VPS.
Ask whether I already have a Hostinger VPS, my preferred region and budget,
and which integrations I want. Check current Hostinger plan resources,
initial and renewal costs, billing term and backup charges before proposing
a plan. Let me handle sign-in and purchasing. Do not choose shared website
hosting, buy add-ons or transfer personal health data without my approval.

For a new VPS, use Hostinger's available Plain OS Ubuntu 24.04 LTS template
if compatible with the chosen release. Reuse an existing server only after
checking its OS, data and other workloads. Never reinstall its OS, reset it
or restore a whole-server snapshot as a routine setup step.

Follow the current official Hostinger VPS documentation:
https://www.hostinger.com/support/5726606-how-to-use-the-vps-dashboard-in-hostinger/
Use hPanel > VPS to locate the server and its real SSH connection details.
Help me set up SSH keys locally; never request my private key or password
in chat. Verify an administrative login and hPanel's browser-terminal
recovery route before restricting access. Use a separate unprivileged
service account. Review both Hostinger's VPS firewall and the OS firewall;
allow only the administration/private-access traffic this setup needs.
Check hPanel's Snapshots & Backups options and agree on the schedule/cost.
Keep a consistent, private application-data backup as well, and test restore
to a separate path without overwriting current records.

Use the reviewed matching public source release
from https://github.com/kajeesan/Open-Health-Atlas. Read docs/DEPLOYMENT.md,
docs/PRIVACY.md, docs/LOCAL_MCP.md and the selected deploy/ templates.
Prepare a separate fictional installation first. Review every hardcoded path,
service user, Python environment, timezone, database and writable cache path.
Keep credentials and data outside source with restricted permissions.
Restrict administration and dashboard access through an authenticated private
connection. Preserve passkeys/CSRF for a web deployment. Do not expose the
Flask development server, dev-login, database or unauthenticated MCP publicly.

For Hermes on this server, use a supported source-based local stdio MCP
process on the same host with an explicitly selected fictional database.
Do not use the Mac app executable or its local paths. Do not weaken the
separate protected Hermes adapters' fictional-data/governance gates.
Do not create remote-MCP exposure just to accommodate a remote-only client.

Verify actual tool discovery, a fictional query/analysis/evidence round trip,
service restart and backup restoration to a separate location. If I select
Google Health, Hevy or Telegram, follow its separate guide and confirm which
database/chat it targets. The server and Mac app are separate installations;
no automatic two-way database synchronization is provided. Propose a concrete
supported viewing/import approach instead of inventing one.

Leave timers disabled until I approve their schedules. Before any personal
data transfer, identify the selected data, Hostinger server/region, any model
provider, access
and backup arrangement and ask for approval. Finish with a plain-language
record of enabled services, Hostinger renewal and backup costs, any model
fees, maintenance tasks, verified
results, gaps, and exact stop/restart/backup/recovery steps for my installation.
Explain how to manage renewal/cancellation in Hostinger. Do not assume that
stopping a server or service cancels its subscription. Do not install a local
LLM on the VPS unless I request it and its resource requirements are checked.
```

## Check one connection at a time

Keep a short private note for each completed setup: where it runs, which workspace or database it uses, the last successful check, whether a schedule is enabled, and how to turn it off. Keep secrets out of that note.

An agent should distinguish **configured**, **tested with fictional data**, and **verified with the source you authorized**. Installing files or displaying a green settings badge does not establish all three.
