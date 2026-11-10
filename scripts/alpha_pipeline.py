#!/usr/bin/env python3
"""Convenience runner script for TickerWatch Alpha CLI."""

import sys
from pathlib import Path

# Ensure root directory is on PYTHONPATH
root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.alpha.cli import main

if __name__ == "__main__":
    main()
