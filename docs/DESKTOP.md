# Open Health Atlas for Mac

The desktop edition packages the existing Open Health Atlas interface and its
local data tools. Open Health Atlas was created by Kajeesan Jeevendra and remains
MIT licensed, with the included third-party notices preserved.

**Release status: evaluation build in development. A signed, notarized public
Mac release has not yet qualified.** The [acceptance record](DESKTOP_ACCEPTANCE.md)
separates completed checks from work still required. Files labelled
`local-unsigned` are engineering evaluation artifacts, not the ordinary download
for non-technical users. Do not disable Gatekeeper to install them.

## Install a qualified release

When a release is qualified, its files and supported Mac versions will appear
on [GitHub Releases](https://github.com/kajeesan/Open-Health-Atlas/releases).
Download the disk image matching your Mac. The first target is Apple silicon
(`macos-arm64`); an Intel Mac is not covered by that filename.

1. Open the downloaded disk image.
2. Drag **Open Health Atlas** to **Applications**.
3. Eject the disk image, then open **Open Health Atlas** from Applications.
4. Follow the setup choices in its window.

The installed app bundles Python, its libraries and the dashboard's assets.
Users do not install developer tools or run a terminal command. No model
subscription or API key is needed for the dashboard or local data tools.

## Choose your data

First-run setup offers three separate starting points:

- **Fictional example:** explore invented records, clearly marked as fictional.
- **Empty personal workspace:** begin with your own records through the existing
  supported entry and import tools.
- **Existing Open Health Atlas data:** select a compatible Open Health Atlas
  database. The application works on a copy, retaining the selected original.

A PDF, arbitrary spreadsheet or unrelated SQLite database is not an Open Health
Atlas database. Use the product's supported import paths for their documented
formats. Selecting an existing database does not import a separate installation's
provider secrets, services or Telegram configuration.

Choose the timezone in which your days should be grouped. This controls daily
boundaries; it does not rewrite the original measurements' timestamps. Fictional
and personal workspaces remain separate.

## Where your records live

The app stores data in your account's
`~/Library/Application Support/Open Health Atlas/`, outside the app bundle.
Replacing the app during an upgrade or moving the app to Trash does not remove
this folder. Keep private backups of this folder; an app download or GitHub copy
is not a backup of your records.

Before migration, the workspace manager creates and checks database snapshots.
It migrates a separate generation and selects it only after successful checks.
An interrupted or failed migration retains the earlier generation and recovery
copies. Do not delete recovery copies while investigating a failed upgrade.
The acceptance record specifies which recovery scenarios have actually passed.

To upgrade a qualified version, quit the app, install the new app in Applications
and reopen it. Existing workspaces and settings are retained. Removal of the app
alone preserves data. This release does not silently delete personal records or
install a background system service.

## Optional connection to an AI client

Use the desktop connection screen to copy or export configuration for a client
that can launch a **local stdio MCP server**. Keep the app in Applications so the
bundled executable remains at a stable location. The client starts
`Open Health Atlas.app/Contents/MacOS/openhealthatlas-mcp`; it does not need a
separate Python installation or developer checkout.

The generated configuration selects one workspace and its timezone. It contains
local paths, so review it before sharing it publicly. No provider credentials
belong in this configuration. Choose any model through your compatible client.
That client may send returned records to its model provider; enabling the local
connection is not a claim that external interpretation stays on your Mac.

Remote-only clients cannot use this stdio configuration directly. No universal
ChatGPT or third-party client compatibility is claimed. See [Local MCP
setup](LOCAL_MCP.md) for the protocol, available tools and evidence workflow.
Existing Hermes and Telegram installations continue independently.

## Help and recovery

If startup takes longer than expected, let the startup window report its result.
If the local service fails, quit and reopen the app. Keep the workspace folder
intact. Do not replace database files with copies of uncertain origin.

Use the app's Help surface for redacted diagnostics. Share only that intended
summary after reviewing it. Do not upload health databases, panel audit records,
provider settings or raw crash reports. A failure report can usually begin with
the app version, macOS version, whether the data is fictional, and the action
that failed, without disclosing any records.

## Maintainer build

These steps are for maintainers, not app users. Build only from the reviewed
public repository. Never add private development history or live health records.
Use a Mac with Command Line Tools, Python 3.11 or newer for the build script,
`uv` 0.11.25, and internet access to fetch the exact pinned runtime and wheels. All generated
output must remain outside the checkout in a local, non-synced directory, such
as `/private/tmp/oha-desktop-release`. File-provider or synced folders can add
Finder metadata to an unpacked `.app` and invalidate its strict signature after
signing. Keep the app, staging files and signing work in the local build location;
copy only the completed DMG, ZIP, checksums and reviewed receipts to a delivery
folder. Check the signature again if the app bundle has been moved or modified.

```bash
python3 scripts/build_desktop.py \
  --output-dir /absolute/path/outside-checkout/desktop-build \
  --version 0.1.0 --arch arm64
```

The builder verifies the Python archive's recorded SHA-256 and installs the
hash-pinned `requirements-desktop.lock`. It copies only manifest-reviewed files
within the explicit source scopes, checking regular-file status, size and
SHA-256 through no-follow file descriptors. Ignored or unlisted local files
never enter this source projection. It
retains notices and makes a self-contained `.app`, disk image, archive and
checksums. The build receipt must identify the exact source commit and build
platform. Reproducibility here means pinned, reviewable build inputs; Apple
signatures, timestamps and disk-image metadata prevent a promise of byte-identical
signed outputs.

Run the artifact privacy audit against the unpacked app and keep the report
outside source:

```bash
python3 scripts/audit_desktop_bundle.py \
  '/absolute/path/outside-checkout/desktop-build/Open Health Atlas.app' \
  --report /absolute/path/outside-checkout/bundle-audit.json
```

This catches known data/secret patterns, embedded databases, private build-home
paths, unexpected symlinks and missing project notices. It also inspects zip
members. It is bounded verification, not exhaustive privacy certification or a
complete dependency-license audit. The scanner has a reviewed exception for
public upstream paths in 15 exact runtime/license files: each requires an exact
fingerprint of all full matching path bytes, including repetitions. This lets
native signatures change without allowing new personal paths. A separate exact
file hash recognizes the OpenSSH delimiter literal in cryptography's parser; no
key material is exempted. Updating dependencies or matching bytes requires a
fresh review. Unused pip and ensurepip are omitted from the runtime.
Source inventory/history checks remain separate. Runtime license files, wheel metadata and vendor notices must be
reviewed in the final bundle before distribution.

The [Desktop build workflow](../.github/workflows/desktop.yml) builds an Apple
silicon evaluation artifact and retains it temporarily. It has read-only
repository permissions and no release publishing or signing credentials. A CI
build alone does not establish that a human can install the app from its icon.

## Signing and release gate

Ordinary direct distribution requires an authorized **Developer ID Application**
identity, hardened runtime, notarization, and a stapled ticket. Apple documents
these requirements in [Notarizing macOS software before
distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
and [Packaging Mac software for
distribution](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution).

Use existing authorized credentials from the Keychain; never put certificates,
passwords or API keys in source, logs, issue text or chat. Do not purchase an
account or accept Apple agreements on the owner's behalf. Once the facilities
exist, the builder accepts a signing identity and existing notary Keychain
profile:

```bash
python3 scripts/build_desktop.py \
  --output-dir /absolute/path/outside-checkout/desktop-release \
  --version 0.1.0 --arch arm64 \
  --identity 'Developer ID Application: AUTHORIZED_IDENTITY' \
  --notary-profile AUTHORIZED_KEYCHAIN_PROFILE
```

Only after signing, notarization, ticket validation, Gatekeeper assessment and
the packaged acceptance journey succeed may filenames claim
`signed-notarized`, and only those reviewed files may become the normal release
download. Test a quarantined download on a clean Mac/account with ordinary
security settings, including an offline launch after installation. Preserve the
installer's SHA-256 and exact source identity alongside the acceptance record.

Public source changes follow the protected branch's normal pull-request checks.
Do not bypass protection or import another repository's private ancestry. A
pending UI pull request remains a separate dependency until reviewed and merged.

## Later platforms

macOS Apple silicon is the first target. Intel macOS needs its own bundled
runtime, native build and complete artifact journey before support is claimed.
Linux needs an equivalent desktop window, packaging and lifecycle verification.
Windows additionally needs replacements for the current POSIX file-lock and
Unix-socket assumptions. Portable workspace interfaces are preparation, not
proof that Windows or Linux installers work.
