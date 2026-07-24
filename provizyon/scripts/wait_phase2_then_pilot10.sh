#!/usr/bin/env bash
# Phase-2 kalan işler bitsin → pilot-10 koş.
# Durum dosyaları: data/pilots/ ( /tmp elektrik kesintisinde silinmesin )
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PILOTS="$ROOT/data/pilots"
LOG="$PILOTS/wait_phase2_then_pilot10.log"
DONE_FLAG="$PILOTS/wait_phase2_then_pilot10.done"
REMAINING="$PILOTS/phase2_100_remaining.json"
JSONL="/home/monassist1/GemmaApp/logs/provizyon-results.jsonl"
API="http://127.0.0.1:8020"

mkdir -p "$PILOTS"
exec >>"$LOG" 2>&1
echo "waiter start $(date -Is)"

BASE=$(wc -l < "$JSONL" | tr -d ' ')
echo "baseline_lines=$BASE"

need=$(python3 - <<PY
import json
from pathlib import Path
p=Path("$REMAINING")
if not p.exists():
    print(0)
else:
    print(len(json.loads(p.read_text()).get("remaining") or []))
PY
)
echo "need_remaining=$need"

while true; do
  pending=$(curl -sf "$API/queue/stats" | python3 -c 'import json,sys;d=json.load(sys.stdin);print((d.get("depth")or{}).get("pending",-1))' 2>/dev/null || echo -1)
  processing=$(curl -sf "$API/queue/stats" | python3 -c 'import json,sys;d=json.load(sys.stdin);print((d.get("depth")or{}).get("processing",-1))' 2>/dev/null || echo -1)
  new=$(( $(wc -l < "$JSONL" | tr -d ' ') - BASE ))
  echo "$(date -Is) pending=$pending processing=$processing new_jsonl=$new"
  if [[ "$pending" == "0" && "$processing" == "0" && "$new" -ge "$need" ]]; then
    echo "REMAINING_DONE"
    break
  fi
  if [[ "$pending" == "0" && "$processing" == "0" && "$need" -eq 0 ]]; then
    echo "NOTHING_REMAINING"
    break
  fi
  sleep 30
done

echo "Running pilot10 with current worker (enriched summary already loaded)..."
cd "$ROOT"
.venv/bin/python scripts/run_docless_pilot.py \
  --manifest data/pilots/belgesiz_pilot10_v1.json \
  --enqueue --wait --timeout-s 3600
echo "PILOT_DONE $(date -Is)"
touch "$DONE_FLAG"
