"""Execute the Recovery browser scripts with fictional source transitions."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_recovery_hrv_browser_contract():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for browser JavaScript checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).parent / "fixtures/recovery_hrv_browser.js")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
