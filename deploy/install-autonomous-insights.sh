#!/usr/bin/env bash
# Install and deliberately activate Hermes autonomous insight schedules.
#
# The default invocation is inspection-only.  Installation creates or updates
# the three scheduler jobs in a disabled state and leaves the systemd timer
# disabled.  Each activation stage requires an explicit approval environment
# variable and enforces the preceding stage as a postcondition.

set -Eeuo pipefail

umask 077

readonly OWNER="hermes-open-source-insights-v1"
readonly TIMER_UNIT="hermes-insight-refresh.timer"
readonly EVENING_UNIT="hermes-evening-feedback.timer"
readonly WEEKLY_ID="hermes-autonomous-weekly-v1"
readonly MONTHLY_ID="hermes-autonomous-monthly-v1"
readonly TRIGGER_ID="hermes-autonomous-trigger-v1"

fail() {
    printf 'install-autonomous-insights: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: install-autonomous-insights.sh [MODE]

Modes:
  --dry-run                 Validate repository artifacts and report hashes (default)
  --install                 Install all artifacts and jobs, disabled
  --enable-nightly          Enable only the deterministic nightly systemd timer
  --enable-weekly           Enable the weekly scheduler job after nightly
  --enable-monthly-trigger  Enable monthly and trigger jobs after weekly

Every --enable-* mode also requires HERMES_AUTONOMOUS_ENABLE_APPROVED=YES.
There is intentionally no bulk --enable mode.
EOF
}

MODE="${1:---dry-run}"
if (( $# > 1 )); then
    usage >&2
    exit 2
fi
case "$MODE" in
    --dry-run|--inspect) MODE="--dry-run" ;;
    --install|--enable-nightly|--enable-weekly|--enable-monthly-trigger) ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown mode: $MODE" ;;
esac

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
PYTHON_BIN="${HERMES_AUTONOMOUS_PYTHON:-python3}"
TEST_MODE="${HERMES_AUTONOMOUS_TEST_MODE:-0}"
[[ "$TEST_MODE" == "0" || "$TEST_MODE" == "1" ]] \
    || fail "HERMES_AUTONOMOUS_TEST_MODE must be 0 or 1"

SERVICE_SOURCE="$SCRIPT_DIR/hermes-insight-refresh.service"
TIMER_SOURCE="$SCRIPT_DIR/hermes-insight-refresh.timer"
JOBS_SOURCE="$SCRIPT_DIR/hermes-autonomous-jobs.example.json"
ADAPTER_SOURCE="$SCRIPT_DIR/hermes-scheduler-adapter"
CONTEXT_SOURCE="$SCRIPT_DIR/hermes-autonomous-context"

SYSTEMD_ROOT="${HERMES_AUTONOMOUS_SYSTEMD_ROOT:-/etc/systemd/system}"
LIB_ROOT="${HERMES_AUTONOMOUS_LIB_ROOT:-/usr/local/lib/hermes-autonomous}"
CONTEXT_SCRIPT_ROOT="${HERMES_AUTONOMOUS_CONTEXT_SCRIPT_ROOT:-/etc/hermes}"
CONTEXT_FILE="${HERMES_AUTONOMOUS_CONTEXT_FILE:-/etc/hermes/autonomous-context.md}"
BACKUP_ROOT="${HERMES_AUTONOMOUS_BACKUP_ROOT:-/var/lib/hermes/autonomous-installer-backups}"

SERVICE_TARGET="$SYSTEMD_ROOT/hermes-insight-refresh.service"
TIMER_TARGET="$SYSTEMD_ROOT/hermes-insight-refresh.timer"
ADAPTER_TARGET="$LIB_ROOT/hermes-scheduler-adapter"
CONTEXT_TARGET="$CONTEXT_SCRIPT_ROOT/autonomous-context.sh"
JOBS_TARGET="$CONTEXT_SCRIPT_ROOT/hermes-autonomous-jobs.json"

for source in \
    "$SERVICE_SOURCE" "$TIMER_SOURCE" "$JOBS_SOURCE" \
    "$ADAPTER_SOURCE" "$CONTEXT_SOURCE"; do
    [[ -f "$source" && ! -L "$source" ]] \
        || fail "required repository artifact is not a regular non-symlink file: $(basename "$source")"
done

command -v "$PYTHON_BIN" >/dev/null 2>&1 \
    || fail "Python interpreter is unavailable: $PYTHON_BIN"

"$PYTHON_BIN" - "$JOBS_SOURCE" "$TIMER_SOURCE" <<'PY'
import json
from pathlib import Path
import sys

jobs_path, timer_path = map(Path, sys.argv[1:])
try:
    contract = json.loads(jobs_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid autonomous job contract: {exc}")

owner = "hermes-open-source-insights-v1"
expected = {
    "hermes-autonomous-weekly-v1": "0 8 * * 0",
    "hermes-autonomous-monthly-v1": "30 8 1 * *",
    "hermes-autonomous-trigger-v1": "*/30 * * * *",
}
if (
    not isinstance(contract, dict)
    or contract.get("contract_version") != "hermes-autonomous-jobs-v1"
    or contract.get("owner") != owner
    or not isinstance(contract.get("jobs"), list)
    or len(contract["jobs"]) != len(expected)
):
    raise SystemExit("invalid autonomous job contract envelope")

seen = set()
for job in contract["jobs"]:
    if not isinstance(job, dict) or job.get("id") not in expected:
        raise SystemExit("invalid autonomous job identity")
    identifier = job["id"]
    if identifier in seen:
        raise SystemExit("duplicate autonomous job identity")
    seen.add(identifier)
    schedule = job.get("schedule")
    if (
        job.get("owner") != owner
        or job.get("enabled") is not False
        or job.get("workdir") != "/var/lib/hermes"
        or job.get("context_from") != "/etc/hermes/autonomous-context.md"
        or job.get("model") is not None
        or job.get("provider") is not None
        or job.get("delivery") is not None
        or not isinstance(job.get("task"), str)
        or not job["task"].strip()
        or not isinstance(schedule, dict)
        or schedule.get("kind") != "cron"
        or schedule.get("expression") != expected[identifier]
        or schedule.get("timezone") != "UTC"
    ):
        raise SystemExit(f"unsafe or unexpected autonomous job contract: {identifier}")
if seen != set(expected):
    raise SystemExit("autonomous job set is incomplete")

timer = timer_path.read_text(encoding="utf-8")
required_lines = {
    "OnCalendar=*-*-* 06:10 UTC",
    "RandomizedDelaySec=5m",
    "Persistent=true",
}
if not required_lines.issubset(set(timer.splitlines())):
    raise SystemExit("nightly timer does not have the expected explicit UTC safety contract")
PY

validate_paths() {
    "$PYTHON_BIN" - "$TEST_MODE" "$@" <<'PY'
import os
from pathlib import Path
import stat
import sys

test_mode = sys.argv[1] == "1"
allowed = tuple(Path(item).resolve() for item in (
    "/tmp", "/private/tmp", "/var/folders", "/private/var/folders"
))
for raw in sys.argv[2:]:
    path = Path(raw)
    if not path.is_absolute():
        raise SystemExit(f"deployment path must be absolute: {raw}")
    normalized = Path(os.path.normpath(str(path)))
    current = Path(normalized.anchor)
    for part in normalized.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise SystemExit(f"deployment path must not contain a symlink: {raw}")
    if test_mode:
        resolved = normalized.resolve(strict=False)
        if not any(resolved == base or base in resolved.parents for base in allowed):
            raise SystemExit(f"test-mode deployment path escapes temporary roots: {raw}")
PY
}

sha256_json() {
    "$PYTHON_BIN" - \
        "$SERVICE_SOURCE" "$TIMER_SOURCE" "$JOBS_SOURCE" \
        "$ADAPTER_SOURCE" "$CONTEXT_SOURCE" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

artifacts = {}
for raw in sys.argv[1:]:
    path = Path(raw)
    artifacts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps({
    "artifacts": artifacts,
    "jobs_enabled": False,
    "mode": "dry-run",
    "ok": True,
    "timer_enabled": False,
}, sort_keys=True))
PY
}

if [[ "$MODE" == "--dry-run" ]]; then
    sha256_json
    exit 0
fi

validate_paths \
    "$SYSTEMD_ROOT" "$LIB_ROOT" "$CONTEXT_SCRIPT_ROOT" \
    "$CONTEXT_FILE" "$BACKUP_ROOT"

if [[ "$TEST_MODE" == "0" && "$(id -u)" != "0" ]]; then
    fail "installation and activation modes must run as root"
fi

if [[ "$MODE" == --enable-* && "${HERMES_AUTONOMOUS_ENABLE_APPROVED:-}" != "YES" ]]; then
    fail "activation requires HERMES_AUTONOMOUS_ENABLE_APPROVED=YES"
fi

STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-autonomous.XXXXXXXX")"
cleanup_stage() {
    rm -rf -- "$STAGE_DIR"
}
trap cleanup_stage EXIT

install -m 0644 "$SERVICE_SOURCE" "$STAGE_DIR/hermes-insight-refresh.service"
install -m 0644 "$TIMER_SOURCE" "$STAGE_DIR/hermes-insight-refresh.timer"
install -m 0644 "$JOBS_SOURCE" "$STAGE_DIR/hermes-autonomous-jobs.json"
install -m 0755 "$ADAPTER_SOURCE" "$STAGE_DIR/hermes-scheduler-adapter"
install -m 0755 "$CONTEXT_SOURCE" "$STAGE_DIR/hermes-autonomous-context.sh"

if [[ "$TEST_MODE" == "1" ]]; then
    [[ -n "${HERMES_AUTONOMOUS_SCHEDULER:-}" ]] \
        || fail "test mode requires HERMES_AUTONOMOUS_SCHEDULER"
    [[ -n "${HERMES_AUTONOMOUS_SYSTEMCTL:-}" ]] \
        || fail "test mode requires HERMES_AUTONOMOUS_SYSTEMCTL"
fi

SCHEDULER="${HERMES_AUTONOMOUS_SCHEDULER:-$STAGE_DIR/hermes-scheduler-adapter}"
if [[ -n "${HERMES_AUTONOMOUS_SYSTEMCTL:-}" ]]; then
    SYSTEMCTL="$HERMES_AUTONOMOUS_SYSTEMCTL"
else
    SYSTEMCTL="$(command -v systemctl || true)"
fi
[[ -n "$SYSTEMCTL" ]] || fail "systemctl is unavailable"
validate_paths "$SCHEDULER" "$SYSTEMCTL"
for command_path in "$SCHEDULER" "$SYSTEMCTL"; do
    [[ -f "$command_path" && ! -L "$command_path" && -x "$command_path" ]] \
        || fail "deployment command must be an executable regular non-symlink file: $command_path"
done

scheduler_stage() {
    HERMES_AUTONOMOUS_JOBS_FILE="$STAGE_DIR/hermes-autonomous-jobs.json" \
    HERMES_AUTONOMOUS_CONTEXT_SCRIPT="$STAGE_DIR/hermes-autonomous-context.sh" \
        "$SCHEDULER" "$@"
}

scheduler_installed() {
    HERMES_AUTONOMOUS_JOBS_FILE="$JOBS_TARGET" \
    HERMES_AUTONOMOUS_CONTEXT_SCRIPT="$CONTEXT_TARGET" \
        "$SCHEDULER" "$@"
}

systemctl_value() {
    local value
    value="$($SYSTEMCTL "$@" 2>/dev/null || true)"
    printf '%s\n' "$value" | sed -n '1p'
}

timer_is_on() {
    [[ "$(systemctl_value is-enabled "$TIMER_UNIT")" == "enabled" \
       && "$(systemctl_value is-active "$TIMER_UNIT")" == "active" ]]
}

timer_is_off() {
    [[ "$(systemctl_value is-enabled "$TIMER_UNIT")" != "enabled" \
       && "$(systemctl_value is-active "$TIMER_UNIT")" != "active" ]]
}

evening_is_on() {
    [[ "$(systemctl_value is-enabled "$EVENING_UNIT")" == "enabled" \
       && "$(systemctl_value is-active "$EVENING_UNIT")" == "active" ]]
}

inspect_target_jobs() {
    local listing="$1"
    local require_disabled="$2"
    "$PYTHON_BIN" - "$OWNER" "$require_disabled" "$listing" <<'PY'
import json
import sys

owner, require_disabled, raw = sys.argv[1:]
expected = {
    "hermes-autonomous-weekly-v1",
    "hermes-autonomous-monthly-v1",
    "hermes-autonomous-trigger-v1",
}
try:
    value = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"scheduler list is not valid JSON: {exc}")
if not isinstance(value, dict) or not isinstance(value.get("jobs"), list):
    raise SystemExit("scheduler list does not contain a jobs array")
found = {identifier: [] for identifier in expected}
for job in value["jobs"]:
    if not isinstance(job, dict):
        continue
    identities = {job.get("id"), job.get("name")} & expected
    if len(identities) > 1:
        raise SystemExit("scheduler row has conflicting Hermes identities")
    if not identities:
        continue
    identifier = next(iter(identities))
    found[identifier].append(job)
for identifier, rows in found.items():
    if len(rows) > 1:
        raise SystemExit(f"duplicate scheduler job identity: {identifier}")
    if not rows:
        continue
    job = rows[0]
    actual_owner = job.get("owner")
    if actual_owner is None and isinstance(job.get("origin"), dict):
        actual_owner = job["origin"].get("owner")
    if actual_owner != owner:
        raise SystemExit(f"same-ID/different-owner scheduler collision: {identifier}")
    if require_disabled == "1" and job.get("enabled") is not False:
        raise SystemExit(f"pre-existing Hermes scheduler job is enabled: {identifier}")
print(json.dumps({
    identifier: (rows[0].get("enabled") if rows else None)
    for identifier, rows in sorted(found.items())
}, sort_keys=True))
PY
}

job_payload() {
    local contract="$1"
    local identifier="$2"
    local enabled="$3"
    "$PYTHON_BIN" - "$contract" "$identifier" "$enabled" <<'PY'
import json
from pathlib import Path
import sys

contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
identifier = sys.argv[2]
enabled = sys.argv[3] == "true"
matches = [job for job in contract["jobs"] if job.get("id") == identifier]
if len(matches) != 1:
    raise SystemExit(f"missing or duplicate job contract: {identifier}")
payload = dict(matches[0])
payload["enabled"] = enabled
print(json.dumps(payload, sort_keys=True))
PY
}

upsert_job() {
    local identifier="$1"
    local enabled="$2"
    local payload
    payload="$(job_payload "$JOBS_TARGET" "$identifier" "$enabled")" || return 1
    printf '%s\n' "$payload" | scheduler_installed cron upsert --stdin >/dev/null
}

enable_job() {
    scheduler_installed cron enable "$1" >/dev/null
}

verify_jobs() {
    local mode="$1"
    local listing
    listing="$(scheduler_installed cron list --json)" || return 1
    "$PYTHON_BIN" - "$JOBS_TARGET" "$mode" "$listing" <<'PY'
import json
from pathlib import Path
import sys

contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = sys.argv[2]
listed = json.loads(sys.argv[3])
enabled_by_mode = {
    "disabled": set(),
    "nightly": set(),
    "weekly": {"hermes-autonomous-weekly-v1"},
    "full": {
        "hermes-autonomous-weekly-v1",
        "hermes-autonomous-monthly-v1",
        "hermes-autonomous-trigger-v1",
    },
}
if mode not in enabled_by_mode:
    raise SystemExit("unknown verification mode")
expected = {job["id"]: dict(job) for job in contract["jobs"]}
for identifier, job in expected.items():
    job["enabled"] = identifier in enabled_by_mode[mode]
found = {identifier: [] for identifier in expected}
for job in listed.get("jobs", []):
    if not isinstance(job, dict):
        continue
    identities = {job.get("id"), job.get("name")} & set(expected)
    for identifier in identities:
        found[identifier].append(job)
for identifier, rows in found.items():
    if len(rows) != 1:
        raise SystemExit(f"missing or duplicate scheduler job: {identifier}")
    if rows[0] != expected[identifier]:
        raise SystemExit(f"scheduler job differs from installed contract: {identifier}")
PY
}

compensate_all_disabled() {
    upsert_job "$WEEKLY_ID" false >/dev/null 2>&1 || true
    upsert_job "$MONTHLY_ID" false >/dev/null 2>&1 || true
    upsert_job "$TRIGGER_ID" false >/dev/null 2>&1 || true
    "$SYSTEMCTL" disable --now "$TIMER_UNIT" >/dev/null 2>&1 || true
}

compensate_weekly_disabled() {
    upsert_job "$WEEKLY_ID" false >/dev/null 2>&1 || true
}

compensate_final_disabled() {
    upsert_job "$MONTHLY_ID" false >/dev/null 2>&1 || true
    upsert_job "$TRIGGER_ID" false >/dev/null 2>&1 || true
}

verify_installed_artifacts() {
    local source target
    local sources=(
        "$SERVICE_SOURCE" "$TIMER_SOURCE" "$ADAPTER_SOURCE"
        "$CONTEXT_SOURCE" "$JOBS_SOURCE"
    )
    local targets=(
        "$SERVICE_TARGET" "$TIMER_TARGET" "$ADAPTER_TARGET"
        "$CONTEXT_TARGET" "$JOBS_TARGET"
    )
    for ((i = 0; i < ${#sources[@]}; i++)); do
        source="${sources[$i]}"
        target="${targets[$i]}"
        [[ -f "$target" && ! -L "$target" ]] || return 1
        cmp -s "$source" "$target" || return 1
    done
}

if [[ "$MODE" == "--install" ]]; then
    preflight_listing="$(scheduler_stage cron list --json)" \
        || fail "scheduler preflight list failed"
    inspect_target_jobs "$preflight_listing" 1 >/dev/null \
        || fail "scheduler collision or enabled-job preflight failed"
    timer_is_off || fail "insight timer is already enabled or active"

    validate_paths \
        "$SERVICE_TARGET" "$TIMER_TARGET" "$ADAPTER_TARGET" \
        "$CONTEXT_TARGET" "$JOBS_TARGET"
    targets=(
        "$SERVICE_TARGET" "$TIMER_TARGET" "$ADAPTER_TARGET"
        "$CONTEXT_TARGET" "$JOBS_TARGET"
    )
    sources=(
        "$STAGE_DIR/hermes-insight-refresh.service"
        "$STAGE_DIR/hermes-insight-refresh.timer"
        "$STAGE_DIR/hermes-scheduler-adapter"
        "$STAGE_DIR/hermes-autonomous-context.sh"
        "$STAGE_DIR/hermes-autonomous-jobs.json"
    )
    modes=(0644 0644 0755 0755 0644)

    for target in "${targets[@]}"; do
        if [[ -e "$target" || -L "$target" ]]; then
            [[ -f "$target" && ! -L "$target" ]] \
                || fail "refusing to replace a non-regular or symlink target: $target"
        fi
    done

    mkdir -p -- "$SYSTEMD_ROOT" "$LIB_ROOT" "$CONTEXT_SCRIPT_ROOT" "$BACKUP_ROOT"
    stamp="$(date -u +%Y%m%dT%H%M%SZ).$$"
    backup_dir="$BACKUP_ROOT/$stamp"
    mkdir -m 0700 -- "$backup_dir"
    existed=()
    backups=()
    mutated=0
    for ((i = 0; i < ${#targets[@]}; i++)); do
        target="${targets[$i]}"
        if [[ -e "$target" ]]; then
            existed+=(1)
            backup="$backup_dir/$i.$(basename "$target")"
            cp -p -- "$target" "$backup"
            backups+=("$backup")
        else
            existed+=(0)
            backups+=("")
        fi
    done

    rollback_files() {
        local index target temporary
        for ((index = mutated - 1; index >= 0; index--)); do
            target="${targets[$index]}"
            temporary="$target.hermes-rollback.$$"
            if [[ "${existed[$index]}" == "1" ]]; then
                cp -p -- "${backups[$index]}" "$temporary" 2>/dev/null \
                    && mv -f -- "$temporary" "$target" 2>/dev/null || true
            else
                rm -f -- "$target" 2>/dev/null || true
            fi
        done
        "$SYSTEMCTL" daemon-reload >/dev/null 2>&1 || true
    }

    install_failed=0
    for ((i = 0; i < ${#targets[@]}; i++)); do
        target="${targets[$i]}"
        temporary="$target.hermes-new.$$"
        if [[ -e "$temporary" || -L "$temporary" ]]; then
            install_failed=1
            break
        fi
        if ! install -m "${modes[$i]}" "${sources[$i]}" "$temporary" \
            || ! mv -f -- "$temporary" "$target"; then
            rm -f -- "$temporary" 2>/dev/null || true
            install_failed=1
            break
        fi
        mutated=$((mutated + 1))
    done

    if [[ "$install_failed" == "0" ]] \
        && ! "$SYSTEMCTL" daemon-reload >/dev/null; then
        install_failed=1
    fi
    if [[ "$install_failed" == "0" ]]; then
        for identifier in "$WEEKLY_ID" "$MONTHLY_ID" "$TRIGGER_ID"; do
            if ! upsert_job "$identifier" false; then
                install_failed=1
                break
            fi
        done
    fi
    if [[ "$install_failed" == "0" ]] \
        && { ! verify_jobs disabled || ! timer_is_off; }; then
        install_failed=1
    fi
    if [[ "$install_failed" != "0" ]]; then
        compensate_all_disabled
        rollback_files
        fail "installation failed; file changes were rolled back and schedules were compensated disabled"
    fi

    if ! find "$backup_dir" -mindepth 1 -print -quit | grep -q .; then
        rmdir -- "$backup_dir"
    fi
    printf '%s\n' '{"jobs_enabled":false,"mode":"installed-disabled","ok":true,"timer_enabled":false}'
    exit 0
fi

verify_installed_artifacts \
    || fail "installed autonomous artifacts are missing, symlinked, or differ from this repository"
[[ -f "$CONTEXT_FILE" && ! -L "$CONTEXT_FILE" ]] \
    || fail "operator context must exist as a regular non-symlink file before activation"
if ! HERMES_AUTONOMOUS_CONTEXT_FILE="$CONTEXT_FILE" "$CONTEXT_TARGET" >/dev/null; then
    fail "operator context failed ownership, permissions, size, or text validation"
fi
evening_is_on \
    || fail "the evening feedback timer must be enabled and active before autonomous activation"

installed_listing="$(scheduler_installed cron list --json)" \
    || fail "scheduler list failed"
inspect_target_jobs "$installed_listing" 0 >/dev/null \
    || fail "scheduler collision preflight failed"

case "$MODE" in
    --enable-nightly)
        if verify_jobs nightly 2>/dev/null && timer_is_on; then
            printf '%s\n' '{"mode":"nightly-enabled","ok":true}'
            exit 0
        fi
        verify_jobs disabled \
            || fail "nightly activation requires all scheduler jobs disabled"
        timer_is_off \
            || fail "nightly timer state is inconsistent; disable it before retrying"
        if ! "$SYSTEMCTL" enable --now "$TIMER_UNIT" >/dev/null \
            || ! timer_is_on || ! verify_jobs nightly; then
            "$SYSTEMCTL" disable --now "$TIMER_UNIT" >/dev/null 2>&1 || true
            timer_is_off || fail "nightly activation failed and timer compensation could not be verified"
            verify_jobs disabled \
                || fail "nightly activation failed and disabled-job postcondition was lost"
            fail "nightly activation failed; timer was compensated disabled"
        fi
        printf '%s\n' '{"mode":"nightly-enabled","ok":true}'
        ;;
    --enable-weekly)
        timer_is_on || fail "weekly activation requires the nightly timer enabled and active"
        if verify_jobs weekly 2>/dev/null; then
            printf '%s\n' '{"mode":"weekly-enabled","ok":true}'
            exit 0
        fi
        verify_jobs nightly \
            || fail "weekly activation requires only the nightly stage"
        if ! enable_job "$WEEKLY_ID" || ! verify_jobs weekly; then
            compensate_weekly_disabled
            verify_jobs nightly \
                || fail "weekly activation failed and compensation could not be verified"
            timer_is_on \
                || fail "weekly activation failed and the nightly postcondition was lost"
            fail "weekly activation failed; weekly job was compensated disabled"
        fi
        printf '%s\n' '{"mode":"weekly-enabled","ok":true}'
        ;;
    --enable-monthly-trigger)
        timer_is_on || fail "final activation requires the nightly timer enabled and active"
        if verify_jobs full 2>/dev/null; then
            printf '%s\n' '{"mode":"all-enabled","ok":true}'
            exit 0
        fi
        if ! verify_jobs weekly 2>/dev/null; then
            # A prior interruption can leave exactly one final-stage job enabled.
            compensate_final_disabled
        fi
        verify_jobs weekly \
            || fail "final activation requires weekly-only scheduler state"
        if ! enable_job "$MONTHLY_ID" \
            || ! enable_job "$TRIGGER_ID" \
            || ! verify_jobs full; then
            compensate_final_disabled
            verify_jobs weekly \
                || fail "final activation failed and compensation could not be verified"
            timer_is_on \
                || fail "final activation failed and prior stages were not preserved"
            fail "final activation failed; monthly and trigger jobs were compensated disabled"
        fi
        printf '%s\n' '{"mode":"all-enabled","ok":true}'
        ;;
esac
