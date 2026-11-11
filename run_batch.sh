#!/bin/bash
# Overnight batch: run every extraction method on several vendors, verify each, then compare.
#
#   bash run_batch.sh "Vendor A" "Vendor B"
#   bash run_batch.sh                      # no names = every vendor folder (sample folders excluded)
#
# Order: the slow full transcriptions first, then the fast hybrid runs, so the hybrid runs pick up any
# parser updates committed while the batch is going. Each step is its own process; one failure doesn't
# stop the rest. Keeps the Mac awake (caffeinate) until done. Log: batch_<date-time>.log in this folder.
# Don't edit this file while it's running (bash reads it as it goes); the .py files are safe to update.
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SELF")"
MODEL="${MODEL:-gemma4:26b}"
PY=angel_tool/.venv/bin/python
[ -x "$PY" ] || PY=python3
LOG="batch_$(date +%Y-%m-%d_%H%M).log"

if [ $# -gt 0 ]; then
  VENDORS=("$@")
else
  VENDORS=()
  while IFS= read -r d; do case "$d" in Sample-*) ;; *) VENDORS+=("$d");; esac; done < <(ls Vendors)
fi

# Re-launch under caffeinate so the Mac doesn't sleep mid-run
if [ -z "${UNDER_CAFFEINATE:-}" ] && command -v caffeinate >/dev/null; then
  UNDER_CAFFEINATE=1 exec caffeinate -i bash "$SELF" "$@"
fi

if ! curl -sf http://localhost:11434/api/tags >/dev/null; then
  echo "Ollama isn't answering on localhost:11434. Start Ollama, then run this again."; exit 1
fi

SAFE="$(echo "$MODEL" | sed 's/[^A-Za-z0-9._-]/_/g')"
START=$(date +%s)
STATUS=()
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
step() {   # step <vendor> <label> <extraction file> <command...>
  local v="$1" label="$2" out="$3"; shift 3
  local t0=$(date +%s)
  log "START  $v :: $label"
  if "$PY" "$@" >>"$LOG" 2>&1; then
    local verdict
    verdict=$("$PY" soc2_review.py verify --vendor "$v" --extraction "Vendors/$v/$out.json" 2>>"$LOG" | tee -a "$LOG" | grep OVERALL || echo "verify failed")
    log "DONE   $v :: $label in $(( ($(date +%s) - t0) / 60 )) min :: $verdict"
    STATUS+=("$v :: $label :: $(( ($(date +%s) - t0) / 60 )) min :: $verdict")
  else
    log "FAILED $v :: $label (see log above)"
    STATUS+=("$v :: $label :: FAILED")
  fi
}

log "Batch: ${#VENDORS[@]} vendor(s): ${VENDORS[*]} | model $MODEL"
for v in "${VENDORS[@]}"; do
  step "$v" "A full transcription" "extraction_local_$SAFE" extract_local.py --vendor "$v" --model "$MODEL"
done
for v in "${VENDORS[@]}"; do
  step "$v" "C on D (gaps)" "extraction_hybrid_gaps_$SAFE" extract_hybrid.py --vendor "$v" --model "$MODEL" --criteria gaps
  step "$v" "C on D (all)"  "extraction_hybrid_all_$SAFE"  extract_hybrid.py --vendor "$v" --model "$MODEL" --criteria all
done
for v in "${VENDORS[@]}"; do
  "$PY" compare_methods.py --vendor "$v" >>"$LOG" 2>&1 && log "Comparison written for $v"
done

log "Finished in $(( ($(date +%s) - START) / 60 )) min"
printf '%s\n' "${STATUS[@]}" | tee -a "$LOG"
command -v osascript >/dev/null && osascript -e 'display notification "SOC 2 batch finished" with title "SOC-2-Reviews"' 2>/dev/null
exit 0
