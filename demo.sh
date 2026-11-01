#!/usr/bin/env bash
# Live demo: day 1 (brief's sample + legacy), then day 2 (moves, rejects, orphans), then replay.
set -euo pipefail
export PYTHONPATH=src
summary() { python3 -c "import json,sys; d=json.load(open('$1/dq_report.json')); \
print('tables :', d['table_counts']); print('moves  :', [(m['member_id'], m['from_country'], m['to_country']) for m in d['moves']]); \
print('rejects:', d['reject_reasons']); print('rdm    :', d['redemptions']); print('checks :', d['invariants'])"; }

echo "== Day 1 =="; python3 -m skypoints.pipeline --input data/sample --legacy data/legacy --out out/day1 --as-of 2024-01-15 >/dev/null; summary out/day1
echo; echo "== Day 1 + Day 2 =="; python3 -m skypoints.pipeline --input data/sample data/demo_day2 --legacy data/legacy --out out/day2 --as-of 2024-01-16 >/dev/null; summary out/day2
echo; echo "== Same files, folders passed in reverse order =="; python3 -m skypoints.pipeline --input data/demo_day2 data/sample --legacy data/legacy --out out/replay --as-of 2024-01-16 >/dev/null; summary out/replay
diff -q out/day2/TABLE_INDIA.csv out/replay/TABLE_INDIA.csv && echo "identical result regardless of order"
