#!/bin/sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
python3 "$ROOT/scripts/check_plan_package_compat.py" "$@"
