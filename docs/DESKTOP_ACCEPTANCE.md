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
| DMG and ZIP | Pending final build | Rebuild from the clean reviewed commit; record hashes |
| macOS 15 / arm64 CI build | Untested | Workflow configured; a configuration is not a passing run |
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
| Port/socket collisions and slow startup | Partial | OS-assigned port and exclusive private socket directory avoid fixed collisions; native waiting/retry implemented, long-delay injection still untested |
| Launcher crash and startup recovery | Passed locally | Abrupt launcher termination/restart retains records; damaged settings recover through healthy workspace selection |
| Native crash during a long migration | Untested | Dedicated interruption of a live native migration remains required |
| Upgrade and migration backup | Passed with stated scope | Packaged simulated code-version update snapshots both DBs and preserves all health rows; no older published desktop release exists |
| Interrupted/failed migration recovery | Passed in source tests | Failure before selection preserves original/backup; interruption after selection preserves later writes; v6→v7 import preserves named records |
| App removal | Pending final archive journey | Data lives outside bundle; verify deletion of a disposable installed copy |
| Redacted diagnostics | Passed locally | Explicit diagnostic field allowlist; native errors use domain/status codes; no raw child output exported |
| Candidate bundle privacy and integrity | Passed locally; final artifact pending | 3,057 files scanned with zero findings; strict signature valid after native use; exact final source/history/artifacts rerun before upload |
| Runtime/dependency license inventory | Passed locally | CPython/native-library license texts, upstream build metadata, wheel dist-info licenses and existing browser notices retained |

## Supporting checks

- Thirty focused desktop tests passed: server boundary/child lifecycle, workspace
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
