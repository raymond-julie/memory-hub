"""Shared fixtures for memoryhub-agents tests."""

import sys
from pathlib import Path

# Ensure the package source is importable without installing.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
