"""Run a supported setup command while its launcher pipe remains alive."""
import os
from pathlib import Path
import runpy
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "toolkit")]


def watch_parent():
    while os.read(sys.stdin.fileno(), 1):
        pass
    os.killpg(os.getpgrp(), signal.SIGKILL)


if __name__ == "__main__":
    script = sys.argv[1]
    if script not in {"scripts/init_hermes.py", "scripts/make_demo_db.py", "toolkit/health.py"}:
        raise SystemExit(2)
    sys.argv = [str(ROOT / script), *sys.argv[2:]]
    threading.Thread(target=watch_parent, daemon=True).start()
    runpy.run_path(sys.argv[0], run_name="__main__")
