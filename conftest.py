"""Make repo-root packages (anchor_oracle, skill_arena) importable in tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
