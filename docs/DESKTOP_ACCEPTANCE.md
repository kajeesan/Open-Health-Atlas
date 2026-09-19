# Desktop acceptance record

Updated 2026-09-20. **No normal public desktop release is qualified.** The
`0.2.1` candidate adds the first-run action/theme repair to the current AGPL-3.0-only public source. Its exact
results belong in candidate-specific receipts attached to
[the desktop pull request](https://github.com/kajeesan/Open-Health-Atlas/pull/7)
and, when qualified, the matching release. Earlier passing checks below are
historical evidence, not certification of changed source or a new installer.

The public release inspected on 2026-09-19 was the `v0.1.0` source-only
prerelease. It is not an end-user Mac download. The subsequent 0.2.0 candidate
from `61c8a199ef6f367d7925bf77d92f12bad48667c0` passed Developer ID signing,
app/DMG notarization, stapling and Gatekeeper checks on the development Mac.
Those receipts do not qualify the changed 0.2.1 artifact. A normal browser
download and clean-Mac installation remain separate acceptance gates. No
purchase, account change or security bypass is part of these checks.

## Current candidate gates

This table defines the gates. Candidate receipts and the exact source commit's
PR checks carry the changing results; a new passing receipt does not require
editing and rebuilding the source merely to insert its own commit hash here.
A receipt for one artifact cannot qualify different bytes.

| Gate | Current status | Evidence required before release |
| --- | --- | --- |
| Current public source and license integration | Track the exact commit in the desktop PR | Exact reviewed source commit, passing required checks, retained approved Body layout |
| Version and artifact identity | `0.2.0`; exact bytes identified by receipt | Build receipt binds source, runtime, dependency lock, architecture and final artifact hashes |
| Project and third-party materials | Check each candidate receipt | Current LICENSE, LICENSING.md, NOTICE, browser/runtime licenses and matching corresponding source |
| Self-contained app, icon, DMG and ZIP | Check each candidate receipt | Build and actual packaged journey using only fictional isolated data |
| macOS Apple silicon support | Historical coverage below | Recheck new candidate; publish only tested OS/architecture claims |
| Developer ID and hardened runtime | Unverified | Authorized certificate/private-key pairing and final nested-code verification |
| Notarization and stapling | Unverified | Accepted app/container submissions and valid final tickets |
| Default Gatekeeper / quarantined download | Untested | Download the final signed files through an ordinary browser with default security settings |
| Clean customer Mac | Untested | Normal Applications/icon launch without developer dependencies, including offline local use |
| Intel macOS | Untested | Separate architecture artifact and full journey |
| Windows / Linux desktop installers | Unsupported | Later phases; Windows POSIX lock/socket constraints remain |

Source-only checks, ad hoc signatures and a system-only PATH do not establish
a clean customer installation. A clean account on a development Mac and a
separate clean machine provide different evidence; state which was used.
The build's minimum deployment target is not a tested minimum OS claim.

## Historical evaluated candidate

The following results belong to clean packaged source
`5d18014bf126faef31e75578f2763646aecf252f`, version `0.1.0`, built and evaluated
on **macOS 26.6.2 arm64** with an **ad hoc signature**. These earlier artifacts
predate integration of the current AGPL source/license materials. They are not
the new `0.2.0` candidate and are not recommended public downloads.

| Historical artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `openhealthatlas-0.1.0-macos-arm64-local-unsigned.dmg` | 50235772 | `37a44cca98f4370d95ce6495471fb30d5244922297371b35a31e0d4c9ce9bdb4` |
| `openhealthatlas-0.1.0-macos-arm64-local-unsigned.zip` | 35617267 | `677c4284b4b3a5f1428a8217f1e3298a6c3b96fe58aa265d138c3fc12a03af9c` |

A read-only checksum comparison on 2026-09-19 confirmed those retained files
still match their saved checksum receipt. No acceptance journey was rerun by
that comparison.

| Historical journey | Result and scope |
| --- | --- |
| Native first launch and setup | LaunchServices opened the actual app; fictional choice and detected timezone completed in WKWebView |
| Empty / fictional / compatible import | Packaged verification kept workspaces separate and original imported records unchanged |
| Navigation and charts | Native Body map and Muscle Balance radar rendered; approved layout and controls retained |
| Authentication and writes | Packaged panel → broker → toolkit entry persisted through restart; unauthenticated, foreign Host/Origin, CSRF-free and replayed-bootstrap requests were refused |
| Offline core use | Thirteen packaged checks passed with external IP connections denied and localhost allowed; not a named-client or clean-Mac claim |
| Local MCP | Nine real SDK checks passed, including discovery/query, positive analysis, task status, evidence, active-worker disconnect and generation reconnect |
| Native connection controls | Clipboard copy, native save dialog and valid configuration export were exercised |
| Restart / duplicate launch | Exact record preservation; duplicate runtime refused safely; second native launch activated the existing window |
| Startup isolation and feedback | OS-assigned loopback port and exclusive private socket; 15-second waiting message observed during a deliberately blocked upgrade |
| Failure and retry | Native Try again recovered after its test broker was deliberately terminated; abrupt launcher restart preserved data |
| Crash during blocked backup | Real native-parent crash reproduced then fixed a lifetime bug; runtime exited in 0.07 seconds while the source remained locked, with original health/panel files byte-identical |
| Reopen after interruption | Old generation remained selected until successful recovery; health rows and Ember appearance survived |
| Simulated upgrade | Packaged code-version change snapshots both databases and retains all health rows/preferences; no older published desktop release existed |
| Failed / post-commit migration recovery | Source tests only: pre-selection failure retains original/backup; post-selection interruption retains later writes; v6→v7 import preserves named records |
| Removal / reinstall | Removing only a disposable DMG-installed app preserved every data file; reinstall restored records and theme |
| Diagnostics | Explicit field allowlist; no raw child output exported; native errors use status/domain codes |
| Post-acceptance integrity | 3,058 files, zero audit findings, zero bytecode, deep/strict signature passed and all pre/post file hashes were identical |
| Dependency review | Runtime and third-party notices retained; historical pinned dependency audit reported zero known vulnerabilities, not proof of no vulnerabilities |

An earlier candidate failed its resource seal with missing empty dependency
files and was rejected. Its old receipts and unresolved native-check notes do
not supersede the cancellation-fixed candidate above.

## Historical hosted verification

At `a5c14f4dee13b93e06b14c81512e6f977149c3b4`, later policy/build/documentation
changes left the 154 selected product files byte-identical to the evaluated
`5d18014` runtime. That equivalence applies only to those two historical states.

- [Repository checks](https://github.com/kajeesan/Open-Health-Atlas/actions/runs/35205495306)
  passed: 2,010 application tests, 15 fictional end-to-end tests and five MCP
  tests, totaling 2,030.
- [Desktop build](https://github.com/kajeesan/Open-Health-Atlas/actions/runs/35205495313)
  passed on macOS 15 arm64 on attempt 2. The first attempt encountered a
  transient disk-image resource error; no source change was needed for retry.
- Thirty-two focused local desktop tests and 22 combined scanner/build-input
  tests passed. These overlap other partitions and must not be added to the
  hosted total.
- Historical internal source/security reviews covered origin/CSRF/bootstrap,
  child ownership, manifest-only source copying and narrow privacy exceptions.
  Internal agent verification is not external-user feedback.

GitHub email privacy was enabled under the owner's instruction. Newly generated
metadata passed the exact-field no-reply policy; earlier generated objects were
not rewritten. This resolved issue is not a new release approval request.

## Recording the new result

For each `0.2.0` artifact, attach its exact source commit, filenames, SHA-256
values, tested OS/architecture and redacted build/acceptance receipts to the
reviewed PR or release. Keep them beside the artifact locally as well. Report
the gate outcomes only from those receipts. A new source change needs a new
candidate identity; do not relabel prior verification. Use the
[release runbook](DESKTOP_RELEASE.md) for commands and the final signed-install
checklist. Keep private records, paths, credentials, raw crash output and this
task's local working notes out of public receipts and Git history.
