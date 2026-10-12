"""Rendered ratio labels and tooltip semantics remain descriptive."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_training_ratio_browser_presentation():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the training ratio browser contract")
    subprocess.run(
        [node, "tests/fixtures/training_ratio_browser.js"], cwd=Path(__file__).parents[1],
        check=True, capture_output=True, text=True, timeout=20,
    )
