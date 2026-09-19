"""Private desktop host for the existing validated write broker.

This process and its calculation children form one owned process group. Losing
the launcher's private pipe ends the group, including on an abrupt launcher
crash. No system Hermes socket, service, or installation is contacted.
"""
from __future__ import annotations

import ctypes
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import signal
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "deploy")]


def peer_uid(connection):
    if hasattr(socket, "SO_PEERCRED"):
        import struct
        return struct.unpack("3i", connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[1]
    if sys.platform == "darwin":
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)):
            raise OSError("Cannot verify local connection")
        return uid.value
    raise OSError("Local peer verification is unavailable")


def main():
    os.umask(0o077)
    loader = importlib.machinery.SourceFileLoader("desktop_write_broker", str(ROOT / "deploy/hermes-bridge"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    broker = importlib.util.module_from_spec(spec)
    loader.exec_module(broker)
    broker._peer_uid = peer_uid
    audit = broker._audit

    def redacted_audit(event):
        # Values, stdin, filenames and notes do not belong in diagnostics.
        audit({key: event[key] for key in ("ts", "phase", "code") if key in event})

    broker._audit = redacted_audit
    handle = broker.handle

    def desktop_handle(command, arguments, uid, stdin=None):
        if command in broker.HERMESCTL_CMDS:
            return {"ok": False, "code": 2, "stdout": "", "stderr":
                    "Hermes is a separate connection. See Desktop Help for supported local AI clients."}
        return handle(command, arguments, uid, stdin)

    broker.handle = desktop_handle

    def parent_lifetime():
        try:
            while os.read(sys.stdin.fileno(), 1):
                pass
        finally:
            # SIGKILL is intentional on pipe loss: the entire group is ours,
            # and SQLite's transaction journal protects interrupted writes.
            os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=parent_lifetime, daemon=True).start()
    broker.main()


if __name__ == "__main__":
    main()
