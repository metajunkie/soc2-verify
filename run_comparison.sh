#!/bin/bash
# Compare extraction methods on one vendor's SOC 2 report, then verify and tabulate each.
#   ./run_comparison.sh ExampleCo                 # hybrid (gaps) + hybrid (all); reuses the earlier full run
#   ./run_comparison.sh ExampleCo --with-full     # also re-runs the full transcription (~15 min) for real token counts
# Ollama must be running.
set -euo pipefail
cd "$(dirname "$0")"
VENDOR="${1:?usage: ./run_comparison.sh <Vendor> [--with-full]}"
MODEL="${MODEL:-gemma4:26b}"
PY=angel_tool/.venv/bin/python
[ -x "$PY" ] || PY=python3

run() { echo; echo "=== $* ==="; /usr/bin/time -p "$PY" "$@"; }

run extract_hybrid.py --vendor "$VENDOR" --model "$MODEL" --criteria gaps
run extract_hybrid.py --vendor "$VENDOR" --model "$MODEL" --criteria all
if [ "${2:-}" = "--with-full" ]; then
  run extract_local.py --vendor "$VENDOR" --model "$MODEL"
fi

SAFE="$(echo "$MODEL" | sed 's/[^A-Za-z0-9._-]/_/g')"
for f in "extraction_hybrid_gaps_$SAFE" "extraction_hybrid_all_$SAFE" "extraction_local_$SAFE"; do
  if [ -f "Vendors/$VENDOR/$f.json" ]; then
    "$PY" soc2_review.py verify --vendor "$VENDOR" --extraction "Vendors/$VENDOR/$f.json" | grep OVERALL || true
  fi
done
echo
"$PY" compare_methods.py --vendor "$VENDOR"
