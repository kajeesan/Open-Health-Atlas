#!/usr/bin/env python3
"""Explicit optional accelerator build; outputs remain outside the source tree."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolkit"))
from hermes_insights._native_stats import build_main

if __name__ == "__main__":
    build_main()
