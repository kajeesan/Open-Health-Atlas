"""Run range-selection regressions against the shipped browser scripts."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_data_range_browser_behavior():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for browser-code checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).parent / "fixtures/data_ranges_browser.js")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
