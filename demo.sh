#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=src

python3 -m skypoints.pipeline --input data/sample --legacy data/legacy --out out/day1 --as-of 2024-01-15
echo
python3 -m skypoints.pipeline --input data/sample data/day2 --legacy data/legacy --out out/day2 --as-of 2024-01-16
