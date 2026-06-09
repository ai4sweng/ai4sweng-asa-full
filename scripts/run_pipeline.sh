#!/usr/bin/env bash
# Usage: ./scripts/run_pipeline.sh [working_directory]
# Runs the full KIO pipeline, monitors status (with token renewal), and auto-approves HITL.

set -euo pipefail

ORCH="http://localhost:8000"
USERNAME="${PIPELINE_USER:-admin}"
PASSWORD="${PIPELINE_PASS:-admin123}"
WORKING_DIR="${1:-buggy_fastapi_repo}"
DESCRIPTION="${2:-Pipeline run}"
POLL_INTERVAL=10
MAX_POLLS=120  # 120 * 10s = 20 minutes max

# ── helpers ────────────────────────────────────────────────────────────────────

get_token() {
  curl -sf -X POST "$ORCH/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"
}

api_get() {
  local path="$1"
  local tok
  tok=$(get_token)
  curl -sf -H "Authorization: Bearer $tok" "$ORCH$path"
}

api_post() {
  local path="$1"
  local body="$2"
  local tok
  tok=$(get_token)
  curl -sf -X POST "$ORCH$path" \
    -H "Authorization: Bearer $tok" \
    -H "Content-Type: application/json" \
    -d "$body"
}

# ── start pipeline ─────────────────────────────────────────────────────────────

echo "Starting pipeline: working_directory=$WORKING_DIR"
RESPONSE=$(api_post "/workflow/run" "{
  \"kio_sequence\": [\"kio2\",\"kio3\",\"kio4\",\"kio5\",\"kio6\",\"kio7\",\"kio8\"],
  \"hitl_after\": [\"kio5\"],
  \"owner\": \"$USERNAME\",
  \"description\": \"$DESCRIPTION\",
  \"working_directory\": \"$WORKING_DIR\"
}")

SESSION=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session: $SESSION"
echo ""

# ── poll loop ──────────────────────────────────────────────────────────────────

for i in $(seq 1 $MAX_POLLS); do
  sleep $POLL_INTERVAL

  STATUS_JSON=$(api_get "/workflow/$SESSION/status" 2>/dev/null || echo '{}')
  STATE=$(echo "$STATUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status') or '')" 2>/dev/null || echo "")
  PROG=$(echo "$STATUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d.get('progress_current',0)}/{d.get('progress_total',0)}\")" 2>/dev/null || echo "?/?")
  KIO=$(echo "$STATUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('active_kio') or '-')" 2>/dev/null || echo "?")
  ARTS=$(echo "$STATUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print([a['producer_kio'] for a in d.get('artifacts',[])])" 2>/dev/null || echo "[]")

  printf "[%3d] %-16s  progress=%s  active=%s  done=%s\n" "$i" "$STATE" "$PROG" "$KIO" "$ARTS"

  if [[ "$STATE" == "PENDING_REVIEW" ]]; then
    CKPT=$(echo "$STATUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pending_checkpoint_id') or '')" 2>/dev/null || echo "")
    echo ""
    echo ">>> HITL checkpoint: $CKPT — auto-approving..."
    api_post "/workflow/$SESSION/approve" \
      '{"actor":"pipeline_script","feedback":"Auto-approved by run_pipeline.sh"}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print('  approved:', d.get('status'))"
    echo ""
    continue
  fi

  if [[ "$STATE" == "COMPLETED" ]]; then
    echo ""
    echo "═══════════════════════════════════════════════"
    echo "  PIPELINE COMPLETED"
    echo "═══════════════════════════════════════════════"
    echo "$STATUS_JSON" | python3 -c "
import sys,json
d=json.load(sys.stdin)
arts={a['producer_kio']: a['artifact_data'] for a in d.get('artifacts',[])}

if 'kio3' in arts:
  fs=arts['kio3'].get('findings',[])
  print(f'kio3  {len(fs)} findings: {[f[\"category\"] for f in fs]}')

if 'kio6' in arts:
  ps=arts['kio6'].get('patches',[])
  print(f'kio6  {len(ps)} patches: {[p[\"path\"] for p in ps]}')

if 'kio7' in arts:
  k7=arts['kio7']
  ps=k7.get('pytest_summary',{})
  note=ps.get('note','')
  flag=' [FALLBACK]' if note else ''
  print(f'kio7  {k7.get(\"passed\",0)}/{k7.get(\"total\",0)} passed, {k7.get(\"failed\",0)} failed{flag}')
  if note: print(f'      note: {note}')
  print(f'      {(ps.get(\"stdout\") or \"\")[-600:]}')

if 'kio8' in arts:
  k8=arts['kio8']
  try:
    summ=k8.get('summary') or k8.get('report') or k8.get('interpretation') or ''
    print(f'kio8  {str(summ)[:400]}')
  except Exception as e:
    print(f'kio8  (parse error: {e})')
"
    echo "═══════════════════════════════════════════════"
    exit 0
  fi

  if [[ "$STATE" == "FAILED" ]]; then
    echo ""
    echo "PIPELINE FAILED"
    echo "$STATUS_JSON" | python3 -m json.tool
    exit 1
  fi

done

echo "Timeout after $((MAX_POLLS * POLL_INTERVAL))s — session $SESSION still running."
exit 2
