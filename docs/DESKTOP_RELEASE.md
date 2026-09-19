# macOS desktop build and release runbook

This is the maintainer procedure for **0.2.0, macOS Apple silicon (arm64)**.
Users should follow [Install Open Health Atlas](DESKTOP.md). Read the current
[acceptance record](DESKTOP_ACCEPTANCE.md) before making a support or release
claim. The existing `v0.1.0` release is source-only; do not overwrite it or reuse
its label for this desktop release.

Current project code is **AGPL-3.0-only**, copyright 2026 Kajeesan Jeevendra.
Preserve [LICENSE](../LICENSE), [LICENSING.md](../LICENSING.md), [NOTICE](../NOTICE)
and [third-party notices](../THIRD_PARTY_NOTICES.md). Earlier MIT permissions do
not supply an alternative license for new AGPL-only work. The matching source
archive must accompany the covered desktop version; personal health records,
credentials and deployment data must never enter it.

## Before running

Use a clean, reviewed checkout of the public repository, on an Apple silicon
Mac with a working Command Line Tools/Swift installation, Python 3.11 or newer,
and `uv` 0.11.25 available. Build dependency downloads require internet access;
the finished local application does not download its runtime on first use.
Do not accept legal agreements or install paid facilities on another person's
behalf. If full Xcode is unavailable, the procedure uses the already installed
Command Line Tools explicitly.

Keep staging on a local, non-synced volume outside source. The commands below
create a fresh `/private/tmp` directory. Synced/file-provider folders can add
Finder metadata and invalidate an unpacked app's signature. Copy only completed
archives, checksums and reviewed receipts to a delivery location. Never edit a
sealed app to correct a release: fix the source and rebuild.

Preview mode needs no Apple account. It creates an ad hoc `local-unsigned`
engineering artifact, which cannot qualify as the ordinary public download.
Release mode additionally needs an **already authorized Developer ID
Application certificate with its private key** and an **existing Keychain
notarization profile**. Enrollment alone proves neither. Establish those through
the owner's approved local flow; do not paste secrets into chat, source, logs or
CI. Do not expose signing credentials to pull-request workflows.

## Build and verify one exact candidate

Run this block from the repository root with Bash. It defaults to preview mode.
It captures your Python before adding Command Line Tools to PATH, because
Apple's bundled Python may be too old. Set `OHA_BUILD_PYTHON` to an existing
compatible interpreter's absolute path if `python3` does not select it; the
procedure checks the version and safe-archive support before doing work.
For a signed candidate, first set `OHA_BUILD_MODE=release`,
`OHA_SIGNING_IDENTITY` to the authorized Developer ID Application identity, and
`OHA_NOTARY_PROFILE` to its existing Keychain profile name in the maintainer's
local environment. These select existing facilities; the procedure does not
purchase, enroll, create credentials, merge, tag or publish anything.

```bash
bash <<'BASH'
set -euo pipefail
oha_build_python=${OHA_BUILD_PYTHON:-$(command -v python3)}
"$oha_build_python" -B -c 'import sys, tarfile; assert sys.version_info >= (3, 11) and hasattr(tarfile, "data_filter"), "Use security-patched Python 3.11 or newer"'
export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
export DEVELOPER_DIR=/Library/Developer/CommandLineTools
export PYTHONDONTWRITEBYTECODE=1

test "$(uname -s)" = Darwin
test "$(uname -m)" = arm64
test -f scripts/build_desktop.py
test -z "$(git status --porcelain)"
command -v uv >/dev/null
"$oha_build_python" -B scripts/release_manifest.py verify
"$oha_build_python" -B scripts/release_scan.py --history

oha_version=0.2.0
oha_commit=$(git rev-parse HEAD)
oha_output=$(mktemp -d /private/tmp/oha-release-0.2.0.XXXXXX)
oha_mode=${OHA_BUILD_MODE:-preview}
oha_build_args=(--output-dir "$oha_output" --version "$oha_version" --arch arm64)
case "$oha_mode" in
  preview) oha_flavor=local-unsigned ;;
  release)
    : "${OHA_SIGNING_IDENTITY:?Set the authorized local signing identity}"
    : "${OHA_NOTARY_PROFILE:?Set the existing local Keychain profile name}"
    oha_build_args+=(--identity "$OHA_SIGNING_IDENTITY" --notary-profile "$OHA_NOTARY_PROFILE")
    oha_flavor=signed-notarized
    ;;
  *) echo 'OHA_BUILD_MODE must be preview or release.' >&2; exit 1 ;;
esac

"$oha_build_python" -B scripts/build_desktop.py "${oha_build_args[@]}"

oha_base="openhealthatlas-$oha_version-macos-arm64-$oha_flavor"
test -f "$oha_output/openhealthatlas-$oha_version-source-$oha_commit.zip"
(cd "$oha_output" && shasum -a 256 -c "$oha_base-SHA256SUMS.txt")

oha_verify=$(mktemp -d /private/tmp/oha-installed-check.XXXXXX)
oha_mount="$oha_verify/mount"
mkdir -p "$oha_mount" "$oha_verify/Applications"
trap '/usr/bin/hdiutil detach "$oha_mount" >/dev/null 2>&1 || true' EXIT
/usr/bin/hdiutil attach "$oha_output/$oha_base.dmg" \
  -readonly -nobrowse -mountpoint "$oha_mount" >/dev/null
/usr/bin/ditto "$oha_mount/Open Health Atlas.app" \
  "$oha_verify/Applications/Open Health Atlas.app"
oha_app="$oha_verify/Applications/Open Health Atlas.app"
oha_python="$oha_app/Contents/Resources/PythonRuntime/bin/python3"
/usr/bin/codesign --verify --deep --strict "$oha_app"
"$oha_build_python" -B scripts/audit_desktop_bundle.py "$oha_app" \
  --report "$oha_output/bundle-audit-before.json"

/usr/bin/sandbox-exec \
  -p '(version 1)(allow default)(deny network-outbound (remote ip "*:*"))(allow network-outbound (remote ip "localhost:*"))' \
  "$oha_build_python" -B scripts/verify_desktop.py --app "$oha_app" \
  --output "$oha_output/packaged-acceptance.json"
"$oha_python" -I -B scripts/verify_desktop_mcp.py \
  --executable "$oha_app/Contents/MacOS/openhealthatlas-mcp" \
  --desktop-workspace > "$oha_output/mcp-acceptance.json"

/usr/bin/codesign --verify --deep --strict "$oha_app"
"$oha_build_python" -B scripts/audit_desktop_bundle.py "$oha_app" \
  --report "$oha_output/bundle-audit-after.json"
"$oha_build_python" -B - "$oha_output" <<'PY'
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
before = json.loads((root / 'bundle-audit-before.json').read_text())
after = json.loads((root / 'bundle-audit-after.json').read_text())
assert not before['findings'] and not after['findings']
assert before['file_sha256'] == after['file_sha256'], 'Package changed during verification'
print('Post-acceptance bundle hashes are unchanged.')
PY

if [ "$oha_mode" = release ]; then
  /usr/bin/xcrun stapler validate "$oha_app"
  /usr/sbin/spctl --assess --type execute "$oha_app"
  /usr/bin/xcrun stapler validate "$oha_output/$oha_base.dmg"
  /usr/sbin/spctl --assess --type open --context context:primary-signature \
    "$oha_output/$oha_base.dmg"
fi
/usr/bin/hdiutil detach "$oha_mount" >/dev/null
trap - EXIT
printf 'Candidate files and local receipts: %s\n' "$oha_output"
printf 'Disposable installed test app: %s\n' "$oha_app"
printf 'Source commit: %s\n' "$oha_commit"
printf 'Automated checks finished. Native and clean signed-install gates still apply.\n'
BASH
```

The bundled Python runs the real MCP SDK verifier, so this procedure does not
install a separate model or client. It tests a local stdio executable with
fictional records, not a named AI client's interface or model responses.
The packaged HTTP verifier is explicitly not a native-window test. The network
sandbox denies external IP connections while allowing localhost; it does not
claim the developer machine lacks installed tools. Automation may need ordinary
permission to launch local processes; do not weaken macOS security to force a
pass.

## What the build binds together

The builder requires reviewed manifest membership and verifies regular-file
status, size and SHA-256 before copying source. Runtime archives and dependency
wheels are pinned and hash checked. Unlisted files, private checkouts, caches,
databases and developer secrets are not build inputs.

Each app includes its exact manifest-backed source archive at
`Contents/Resources/CorrespondingSource.zip`. The same archive is delivered as
`openhealthatlas-0.2.0-source-<full-source-commit>.zip` alongside the installer.
The app's build information binds the source commit, manifest, source archive,
runtime and dependency lock. The checksum file covers the source archive,
application ZIP and DMG. Preserve those bindings when preparing release assets;
a generic link to a different revision is not the exact released source.
The source archive also includes the two pinned supplemental dependency source
archives; its inventory records source URLs and hashes for all locked packages.
The auditor uses the receipt's immutable Git commit, even if the verification
checkout has advanced. To locate that commit in another public checkout, pass
`--source-root` to `audit_desktop_bundle.py`. Keep `build-receipt.json` with the
artifacts: its final signing assessments and artifact sizes/hashes supplement
the immutable source information inside the sealed app.

To rebuild, prefer a clean checkout of the recorded public source commit and
run the procedure above. The supplied source ZIP contains the full reviewed
source and build inputs, without Git history. A rebuild from that ZIP instead of a public clone
must first enter its `Open-Health-Atlas/` subdirectory, then initialize a local
Git repository and make a commit using a sanitized
`example.invalid` author/committer identity before the build; this creates a
new local source identity. Preserve the original archive and its checksum so
the upstream source mapping remains available. Runtime/wheel downloads still
require connectivity unless their verified caches already exist.

The app menu offers **License and notices** and **Show corresponding source**,
including access to the bundled source archive offline. Retain its creator
credit, current license and all bundled third-party materials. The source
archive and runtime audit are separate checks; narrow reviewed upstream
examples/build-path exceptions do not authorize new private data.

Build reproducibility here means pinned, reviewable inputs and exact receipt
mapping. Different SDK versions, signature timestamps and disk-image metadata
can change final bytes. No bit-for-bit identity across build hosts is promised.
An ad hoc deep/strict signature pass does not verify Developer ID hardened
runtime, notarization or Gatekeeper. Any required entitlement or package-layout
repair needs a reviewed source change and a new candidate.

## Finish the native and signed-install gates

Use only fictional workspaces. Record the exact artifact hash, source commit,
OS version, CPU and test scope for every check. Do not transfer passes from the
historical candidate to this one.

1. Open the actual installed app from its icon. Complete setup; check the
   fictional label, empty workspace and compatible import; navigate Body and
   its charts; save a real entry, quit and reopen. Exercise Help, copy/export,
   workspace switching and the two license/source menu actions. Verify failure
   feedback, duplicate launch and preference persistence on this candidate.
2. Confirm the relevant upgrade/interruption checks and exact data preservation
   in the automated receipts. Distinguish a simulated code-version upgrade from
   an upgrade from an older published desktop release. Removing only the app
   must preserve records/settings; reinstall and verify them.
3. For a signed candidate, inspect the accepted notarization result and final
   app/container signature and ticket assessments. Keep raw notary output local
   because it may contain account identifiers. Publish only redacted results.
4. Download the exact final artifact through an ordinary browser on a clean
   supported Mac, install through the disk image and Applications, and launch
   its icon with default Gatekeeper settings. Test offline launch/local use
   after installation. Do not strip quarantine, turn off Gatekeeper or use a
   developer override. A clean account on a development Mac is narrower evidence
   than a clean machine; record which was tested.

Apple's [notarization guidance](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
and [distribution packaging guidance](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution)
are the authority for that final distribution path. If active membership,
certificate/private-key access or a notary profile is missing, finish all
independent work and report the smallest specific owner action. Do not claim
that enrollment has activated without verifying it when access is available.

## Prepare the reviewed release

Keep the preview private to engineering evaluation. Public CI artifacts remain
clearly labelled evaluation builds and receive no release/signing credentials.
When all final gates pass, attach exact candidate receipts against the
[acceptance gates](DESKTOP_ACCEPTANCE.md) in the reviewed PR or release, then
use the normal protected pull-request process. Preserve the tested source
identity; changing documentation just to insert the candidate's own hash would
create a different source revision and require a new candidate.
Do not bypass checks, rewrite shared history or publish a normal download merely
because a build or membership activation succeeded.

The reviewed release should include the tested `macos-arm64` DMG, optional ZIP,
matching source archive, checksum file and redacted acceptance/build receipt.
Release notes should state version/source commit, exactly tested macOS versions,
Apple silicon requirement, signature/notarization status, normal installation
steps, data-preserving upgrade/removal behavior and any remaining limitations.
Link to [the user guide](DESKTOP.md), [local AI connection](DESKTOP_MCP.md) and
[licensing policy](../LICENSING.md). Keep Intel and Windows/Linux explicitly
unsupported until their own artifacts pass. Publish only through the authorized
release workflow after reviewing these concrete files; this runbook does not
publish or make a tag.
