"""The panel's ONLY write path: every mutation is a request to the hermes-bridge
broker over its Unix socket (app/bridge.py never opens the DB and never shells
out to a privileged binary). No SQL write connection exists in the panel.

This bridge is the VALIDATED WRITE path, not a read-only channel: ALLOWED
mixes ~15 daily-loop writers (log, day-rating, log-set, eat, routine-set, …) with
the reads. Every one is a whitelist-and-validate health.py subcommand — the panel
never issues raw SQL. Only the heavier writers are collector-only and kept OUT of
this allowlist (labs ingest/capture/catalog, fitness capture/void + targets, the
imports, deployment configuration); a test pins them out.

The broker (deploy/hermes-bridge, running as hermes) is the trusted side: it
whitelists subcommands/flags, runs health.py, and writes the hermes-owned
append-only audit log this process cannot touch. This client mirrors the
shared command declaration and mirrors every call — including refusals —
into panel.db for the activity feed.

Why a socket and not sudo: the panel unit is sandboxed with read-only health
and vault bind mounts and holds no sudo grants, so it cannot write them. The broker runs outside that
sandbox. See docs/ARCHITECTURE.md.
"""
import json
import socket
import time

from flask import current_app

from app.panel_db import get_db
from deploy.bridge_commands import COMMAND_FLAGS, CLIENT_FLAG_CHECKS, NO_VALUE_FLAGS

ALLOWED = set(COMMAND_FLAGS)
ALLOWED_FLAGS = {
    command: set(COMMAND_FLAGS[command]) for command in CLIENT_FLAG_CHECKS
}


class BridgeError(RuntimeError):
    """Raised when the bridge refuses or health.py fails; message is user-safe."""

    def __init__(self, message, *, code=None):
        super().__init__(str(message))
        self.code = code


def run(
    subcmd: str,
    *args,
    stdin: str | None = None,
    timeout: float = 30.0,
    socket_path: str | None = None,
) -> dict:
    """Send a whitelisted health.py subcommand to the broker; return its JSON.
    `stdin` carries note content for write-note (too large/private for argv).

    Client-side allowlist is defense in depth — the broker enforces its own.
    """
    str_args = [str(a) for a in args]
    if subcmd not in ALLOWED:
        _audit(subcmd, str_args, "refused-client")
        raise BridgeError(f"'{subcmd}' is not allowed through the panel bridge")
    if subcmd in ALLOWED_FLAGS:
        allowed = ALLOWED_FLAGS[subcmd]
        i = 0
        while i < len(str_args):
            value = str_args[i]
            if value.startswith("-"):
                flag = value.split("=", 1)[0]
                if flag not in allowed:
                    _audit(subcmd, str_args, "refused-client-flag")
                    raise BridgeError(f"flag '{flag}' is not allowed for '{subcmd}'")
                if flag in NO_VALUE_FLAGS and "=" in value:
                    _audit(subcmd, str_args, "refused-client-flag-value")
                    raise BridgeError(f"flag '{flag}' takes no value")
                if "=" not in value and flag not in NO_VALUE_FLAGS:
                    i += 1  # skip the flag value, even when that data begins with '-'
            i += 1

    payload = {"subcmd": subcmd, "args": str_args}
    if stdin is not None:
        payload["stdin"] = stdin
    request = json.dumps(payload) + "\n"
    # A dedicated socket may be selected only by trusted server code.  The
    # localhost fictional Green-day display uses this to reach its isolated
    # read-only fixture broker without switching the product/Recovery DB.
    sock_path = socket_path or current_app.config["BRIDGE_SOCKET"]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(sock_path)
            s.sendall(request.encode("utf-8"))
            buf = bytearray()
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf.extend(chunk)
    except (OSError, socket.timeout) as exc:
        _audit(subcmd, str_args, f"transport-error:{exc.__class__.__name__}")
        raise BridgeError(f"write bridge unavailable: {exc}")

    try:
        resp = json.loads(bytes(buf).decode("utf-8").strip())
    except ValueError:
        _audit(subcmd, str_args, "bad-response")
        raise BridgeError("write bridge returned a malformed response")

    _audit(subcmd, str_args, f"exit:{resp.get('code')}")
    if not resp.get("ok"):
        raw = (resp.get("stderr") or resp.get("stdout") or "").strip()
        # hermesctl emits JSON on both success and controlled failure. Surface
        # its short `error` value instead of dumping the serialized object into
        # the UI (and, importantly, never expose a Python traceback).
        try:
            nested = json.loads(raw) if raw else {}
        except ValueError:
            nested = {}
        error = nested.get("error") if isinstance(nested, dict) else None
        if isinstance(error, dict):
            message = error.get("message") or "health command failed"
            code = error.get("code") if isinstance(error.get("code"), str) else None
            raise BridgeError(message, code=code)
        raise BridgeError(
            error if isinstance(error, str) and error else (
                raw or f"bridge exited {resp.get('code')}"
            )
        )
    stdout = resp.get("stdout", "")
    try:
        return json.loads(stdout) if stdout.strip() else {}
    except ValueError:
        return {"raw": stdout.strip()}


def _audit(subcmd, args, outcome) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO audit_log(ts, event, detail) VALUES(?,?,?)",
        (int(time.time()), f"bridge.{subcmd}",
         json.dumps({"args": args, "outcome": outcome})),
    )
    db.commit()
