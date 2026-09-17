# Desktop acceptance record

Status on 2026-09-17: **not qualified for normal public distribution**.
The available local test host is macOS 26.6.2, Apple silicon (arm64). No Developer
ID signing identity is available. Nothing in this record substitutes an unsigned
engineering build for a signed and notarized download.

The source baseline for this work is public `main` at
`1dc2fcb889e5ecdaacdd05afbc109c041929d948`. The approved Body-page work remains
[PR #6](https://github.com/kajeesan/Open-Health-Atlas/pull/6) at
`8ab53324f5b5858314b4266326092f0ce5f0147d` until its normal integration completes.
The approved Body commit is preserved in this branch ancestry. Final artifact
receipts record the exact desktop source commit and checksums; neither starting
commit is the desktop release.

## Build and installation matrix

| Target/check | Status | Evidence or remaining requirement |
| --- | --- | --- |
| macOS 26.6.2 / arm64 local host | Available | Current development host; not a clean customer Mac |
| Self-contained `.app` and original icon | Passed locally | Bundled CPython, pinned libraries and local assets; native launch on the stated host |
| DMG and ZIP | Passed locally | Clean `acfcca18739fe54486f19bd9746c1242dfc0abb0` artifacts generated, checksummed, mounted read-only and installed to a disposable path |
| macOS 15 / arm64 CI build | Passed for packaged source | [Desktop build](https://github.com/kajeesan/Open-Health-Atlas/actions/runs/35172922094) built and tested the final package; native visual/Gatekeeper checks remain separate |
| Developer runtimes absent from command path | Passed locally | Packaged acceptance uses only bundled Python with system-only PATH; no first-use download |
| Fresh customer Mac/account | Untested | This host has development tools; isolated PATH is not a clean-machine certification |
| Developer ID signing | Blocked | No authorized signing identity present |
| Hardened runtime / notarization / stapling | Blocked | Requires authorized signing and notary facilities |
| Quarantined download / default Gatekeeper | Blocked | Requires qualified signed and notarized artifact |
| Other macOS versions | Untested | Build deployment target is not runtime verification |
| Intel macOS | Untested | Separate architecture artifact and tests required |
| Windows / Linux desktop installers | Not supported | Follow-up phases; POSIX constraints remain |

## Packaged journey

Only change a row to passed after executing the actual built artifact with
fictional, isolated data. Add the date, exact version/hash and compact evidence.
Source tests and development previews cannot mark these rows passed.

| Journey | Status | Required evidence |
| --- | --- | --- |
| Native application launch, window and guided setup | Passed locally | Actual `.app` opened by macOS LaunchServices; fictional choice and detected timezone completed in WKWebView |
| Fictional, empty and compatible-import setup | Passed locally | Packaged journey creates separate datasets; import row comparison preserves original exactly |
| Timezone, navigation, charts and existing controls | Passed locally | WKWebView Body map and Muscle Balance radar rendered; approved Body layout retained; Help copy/export works |
| Authenticated session and real validated write | Passed locally | Packaged panel → real broker → toolkit body entry persisted through restart |
| CSRF, foreign Host/Origin, unauthenticated requests | Passed locally | Packaged refusals and one-time token replay rejection; native opaque-origin bootstrap is capability protected |
| Offline local data operations | Passed locally | Full packaged HTTP journey under an OS sandbox denying external IP connections; localhost only |
| Deterministic analysis and evidence | Passed locally | Actual bundled MCP query, positive analysis and evidence replay |
| Actual bundled stdio MCP | Passed locally | SDK transport, status, analysis/evidence, active-worker disconnect cleanup and stable-workspace upgrade reconnect |
| Quit/reopen and duplicate runtime | Passed locally | Exact health-row preservation; second runtime refused without data change; native second launch activates existing window |
| Port/socket collisions and slow startup | Passed with stated scope | OS-assigned port and exclusive private socket directory; native 15-second waiting message observed during a deliberately locked upgrade |
| Launcher crash and startup recovery | Passed locally | Abrupt launcher termination/restart retains records; damaged settings recover through selection; native Try again recovered after deliberately killing its owned test broker |
| Native crash during a blocked upgrade | Passed locally | Real native process killed while a copied panel DB remained exclusively locked; fixed runtime exited in 0.07 s, originals stayed byte-identical, and reopening recovered |
| Upgrade and migration backup | Passed with stated scope | Packaged simulated code-version update snapshots both DBs and preserves all health rows; no older published desktop release exists |
| Interrupted/failed migration recovery | Passed in source tests | Failure before selection preserves original/backup; interruption after selection preserves later writes; v6→v7 import preserves named records |
| App removal | Passed locally | Deleted only a disposable DMG-installed app copy; every data file stayed byte-identical; reinstall retained records and theme |
| Redacted diagnostics | Passed locally | Explicit diagnostic field allowlist; native errors use domain/status codes; no raw child output exported |
| Final bundle privacy and integrity | Passed locally | 3,058 files, zero findings; post-acceptance deep/strict signature passes; all pre/post file hashes identical and zero bytecode files |
| Runtime/dependency license inventory | Passed locally | CPython/native-library license texts, upstream build metadata, wheel dist-info licenses and existing browser notices retained |

## Supporting checks

- Thirty focused desktop tests passed before the preference follow-up: server boundary/child lifecycle, workspace
  initialization/import/upgrade/recovery, stable MCP configuration and artifact
  auditing. Existing application run: 1,972 passed, one PATH-related failure,
  fourteen optional-native skips. The failed test and native checks were rerun
  with the required interpreter and compiled test kernel: 32 passed (overlapping
  checks; do not add these counts). Existing e2e/MCP partition: 19 passed and
  one PATH-related failure; that failure passed on the corrected environment.
- The early v2 engineering candidate later failed its resource seal because
  twenty-two empty dependency files were missing. It was rejected. The cause
  was not established; later candidates use isolated build copies and must
  pass full pre/post acceptance hash and signature checks.


- Five artifact-audit regressions passed (`5 passed in 0.03s`):
  nested-archive data/private-path detection, external-symlink/missing-notice
  rejection, certificate/private-key distinction, narrowly scoped upstream path
  exceptions, and exact-byte parser-marker exceptions. This tests the checker;
  it does not certify an artifact.
- Current protected `main` requirements were read on 2026-09-17: the required
  `Privacy, manifest and relevant tests` check must pass on an up-to-date branch;
  administrator enforcement and conversation resolution are enabled; force
  push and branch deletion are disabled. These are an observation, not a
  replacement for rechecking policy at publication.
- Independent source security review identified exact Host/Origin validation,
  one-time local bootstrap, isolated bridge paths, retained CSRF and restricted
  diagnostics as desktop acceptance requirements. This is internal review,
  not external-user feedback.

- Independent isolated WSGI checks passed: unauthenticated requests returned
  401; foreign Host/Origin, forwarded headers and cross-site requests returned
  403; native-token bootstrap returned 303 and replay returned 401; authenticated
  setup returned 200; a write without CSRF returned 400. These are source-level
  checks, not packaged WebKit acceptance.

## Evidence discipline

Keep generated databases, logs, screenshots with private paths, build caches,
installers and verbose diagnostics outside Git. Use only fictional fixtures.
Every final artifact needs its source commit, architecture, tested OS version,
build inputs, SHA-256, signature assessment and acceptance outcome recorded.

Never upload a failed or unchecked artifact as the recommended download.
Signing credentials must come from an authorized owner-managed facility; no
secret contents need to be pasted into a conversation.

The preference follow-up adds per-workspace persistence for existing display
choices and opaque analysis handles. Server/preferences/shell checks passed
(31 tests); final package verification additionally checks restart/upgrade
persistence. Local signing identity discovery and repository secret-name
inventory both found no existing Apple distribution facility.

## Final local evaluation artifacts

Packaged source: `acfcca18739fe54486f19bd9746c1242dfc0abb0` (clean).
Version `0.1.0`, macOS arm64, built on macOS 26.6.2. These are ad hoc
evaluation artifacts, not signed/notarized public downloads.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `openhealthatlas-0.1.0-macos-arm64-local-unsigned.dmg` | 49491758 | `c9d9c39a712736945575b070bf53d468b65b7869505ff3bbeb143fdc2b255c91` |
| `openhealthatlas-0.1.0-macos-arm64-local-unsigned.zip` | 35617170 | `7352179d28dfe6b74c01e56300a2d6b9cacfb55b6e94339979ac5ab736462ef5` |

The final DMG-installed copy passed twelve packaged HTTP lifecycle/data checks
with external IP networking denied, plus the real bundled MCP SDK journey
(query, positive analysis, evidence, task status, disconnect cleanup and
generation reconnect). A separate removal/reinstall journey preserved every
data file and restored the saved theme. Thirty-one focused desktop tests pass.
Theme persistence is verified at the packaged server boundary across restart
and upgrade. Its final native visual recheck was interrupted by the locked Mac;
no unlock or security bypass was attempted. Native long-migration interruption
and injected slow-start acceptance remain explicitly untested.

[Draft PR #7](https://github.com/kajeesan/Open-Health-Atlas/pull/7) contains the
implementation and preserves the approved Body layout dependency. No public
desktop release has been published.

## Cancellation-fixed local candidate

Source `5d18014bf126faef31e75578f2763646aecf252f`, clean. The earlier locked-Mac
visual gap is resolved: the final native window retained Ember through quit
and reopen, and through an interrupted-upgrade recovery. A real native-parent
crash during a blocked SQLite backup first reproduced a lifetime bug; the
corrected runtime stops in 0.07 seconds while the source remains locked. Both
original database files remain byte-identical, the old generation stays
selected until recovery succeeds, and recovered health rows match exactly.
This exercises cancellation during backup before the upgrade commit point;
source tests separately cover failed migration and post-commit recovery.

The packaged offline verifier now covers 13 checks, including native-owner pipe
loss during an actual locked backup. All 32 focused desktop tests pass. The
original project/third-party license inventory and pinned dependencies are
unchanged. Developer ID signing, notarization and default-Gatekeeper clean-Mac
acceptance remain unavailable; the owner confirmed there is no Developer ID
Application identity yet.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `openhealthatlas-0.1.0-macos-arm64-local-unsigned.dmg` | 50235772 | `37a44cca98f4370d95ce6495471fb30d5244922297371b35a31e0d4c9ce9bdb4` |
| `openhealthatlas-0.1.0-macos-arm64-local-unsigned.zip` | 35617267 | `677c4284b4b3a5f1428a8217f1e3298a6c3b96fe58aa265d138c3fc12a03af9c` |

GitHub email privacy was enabled under the owner's explicit instruction.
That setting applies to future web operations and does not erase earlier
metadata. Verify newly generated PR metadata after updating the branch; no
real email address belongs in reports or source.

### Publication metadata and build provenance

After the owner-requested privacy change, current PR7 generated author and
committer metadata use GitHub's masked no-reply identities. Source scanner
exceptions are restricted to the two reviewed exact Git email-field values;
no domain-wide or general file/message exemption is granted. Earlier generated
objects were not rewritten. The builder's source projection now requires
manifest membership and exact checked bytes; ignored/unlisted local data,
symlinked, missing, nonregular or altered inputs fail closed. Combined focused
scanner/build-input tests: 22 passed. The current app's 154 product source files
remain byte-identical under this stricter copying path.
