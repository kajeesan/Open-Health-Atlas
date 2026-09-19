# Open Health Atlas for Mac

Open Health Atlas brings its existing dashboard, records and local data tools
into a Mac app. Created by Kajeesan Jeevendra, the current project is licensed
under **AGPL-3.0-only**, with third-party notices preserved. See the
[licensing policy](../LICENSING.md) and [license](../LICENSE).

**A signed, notarized public Mac release has not yet qualified.** Files marked
`local-unsigned` are engineering previews. The existing `v0.1.0` GitHub release
contains source code, not a Mac installer. The next desktop candidate is
`0.2.0`; its [acceptance record](DESKTOP_ACCEPTANCE.md) distinguishes earlier
verification from the new candidate. Do not disable Gatekeeper to install a
preview.

## Download and install

A qualified Mac installer will appear on
[GitHub Releases](https://github.com/kajeesan/Open-Health-Atlas/releases), with
its tested macOS versions and architecture stated beside the download. Choose
the **macOS Apple silicon** disk image (`macos-arm64`). Intel Macs and
Windows/Linux desktop installers are not yet supported.

1. Open the downloaded disk image.
2. Drag **Open Health Atlas** to **Applications**.
3. Eject the disk image, then open **Open Health Atlas** from Applications.
4. Choose your starting workspace in the app.

The app bundles its runtime, libraries and dashboard assets. You do not need
Terminal, developer tools, a model subscription or an API key for the dashboard
and local data tools. Local operations work offline after installation; an
optional external AI client or connected service has its own requirements.

## Choose your data

Setup offers three separate starting points:

- **Fictional example:** explore invented records, clearly marked as fictional.
- **Empty personal workspace:** begin with your own records using the existing
  entry forms and supported import tools.
- **Existing Open Health Atlas data:** select a compatible database. The app
  checks it and works on a copy, preserving the original.

A PDF, arbitrary spreadsheet or unrelated SQLite database is not a compatible
database. Use each supported importer for its documented format. A database
import does not copy another installation's separate notes, attachments,
provider secrets, services or Telegram configuration.

Choose the timezone in which your days should be grouped. It controls daily
boundaries without rewriting measurement timestamps. Fictional and personal
workspaces remain separate. Use the workspace selector to change between them.

## Update, back up or remove the app

Records and settings live in your account's
`~/Library/Application Support/Open Health Atlas/`, outside the app bundle.
For a full backup, quit the app, disconnect any local AI clients and copy that
entire folder to a private backup location. An installer or GitHub source copy
is not a backup of your records.

Before upgrading, quit the app and disconnect local AI clients. Replace the app
in Applications, reopen it, and then reconnect the clients. Before changing a
database schema, the app makes and checks recovery copies, prepares a separate
generation and selects it only after successful checks. Existing workspaces
and preferences are retained. Keep recovery copies if an update fails; see
the [verified recovery scope](DESKTOP_ACCEPTANCE.md).

Moving the app to Trash does not delete its records or settings. The app does
not install a system background service or silently remove personal data.

## Optional AI connection

The dashboard and local data tools work without an AI account. To connect an
AI client, open **Help** in the app and copy or export the selected workspace's
connection settings. Follow [Connect a local AI client](DESKTOP_MCP.md).

The client must support launching a **local stdio MCP server**. The installed
app supplies a stable executable and its own runtime. Configuration remains
attached to the workspace it names; switching the dashboard does not silently
switch the client's data. On reconnect, it resolves the workspace's current
database generation, including after an upgrade.

Remote-only clients cannot use this connection directly. SDK protocol testing
does not establish universal ChatGPT or named-client compatibility. Your
chosen AI client may send returned records to its model provider; review its
privacy settings before connecting personal data. Provider credentials belong
in that client's secure settings. The exported connection settings contain
local paths and should remain private.

Existing Hermes and Telegram installations operate independently. Installing
the desktop app does not install, activate or reconfigure those integrations.

## If something goes wrong

Allow the startup window to report its result. If it offers **Try again**, use
that action, or quit and reopen the app. Keep the workspace folder intact. Do
not replace database files with copies of uncertain origin. A rejected import
needs a complete compatible database or a healthy backup.

The app's Help page provides redacted diagnostics. Review the summary before
sharing it. Report the app version, macOS version, whether you used fictional
data, and the action that failed. Do not upload databases, panel audit records,
provider settings, exported connection settings or raw crash reports.

Maintainers should use the single [build and release runbook](DESKTOP_RELEASE.md).
