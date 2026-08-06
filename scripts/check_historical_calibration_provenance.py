#!/usr/bin/env python3
"""Validate quarantined historical calibration provenance and recompute budgets."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.calibration_provenance import (  # noqa: E402
    CalibrationProvenanceError,
    validate_historical_calibration_files,
)

DEFAULT_MANIFEST = (
    ROOT / "data/calibration/historical/skill-bettor-4b2de858.json"
)
DEFAULT_SCHEMA = ROOT / "contracts/historical-calibration-provenance.schema.json"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    value.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        errors = validate_historical_calibration_files(args.manifest, args.schema)
    except CalibrationProvenanceError as exc:
        print(f"FAIL: historical calibration provenance: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("FAIL: historical calibration provenance contract", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    print(
        "PASS: historical calibration provenance is quarantined and budgets "
        "recompute to 900000 ppm / 62847 ms / 1.615403 USD"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
