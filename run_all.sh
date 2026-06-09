#!/usr/bin/env bash
# Start all Phase 2 services. Run from the EnisAliMerge root.
# Each service opens its own terminal tab (macOS: osascript) or runs in background.
# Usage: bash run_all.sh [--bg]   # --bg = background, no new terminals

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
BG="${1:-}"

# shared/ and apps/kio_shells/ must be importable everywhere
export PYTHONPATH="$ROOT:$ROOT/apps/kio_shells"

run_service() {
  local name="$1"
  local dir="$2"
  local cmd="$3"
  local port="$4"

  echo "▶  Starting $name on port $port…"
  if [[ "$BG" == "--bg" ]]; then
    mkdir -p "$ROOT/logs"
    (cd "$dir" && PYTHONPATH="$PYTHONPATH" eval "$cmd" > "$ROOT/logs/${name}.log" 2>&1) &
    echo "   PID $! → logs/${name}.log"
  else
    # macOS: open new Terminal tab
    osascript -e "tell app \"Terminal\" to do script \"cd '$dir' && export PYTHONPATH='$PYTHONPATH' && $cmd\"" 2>/dev/null || \
      (cd "$dir" && eval "$cmd" &)
  fi
}

# Core services — ports match defaults in shared/config.py Settings
run_service "session_manager" "$ROOT/apps/session_manager" \
  "uvicorn main:app --host 0.0.0.0 --port 8002 --reload" 8002

run_service "lm_engine" "$ROOT/apps/lm_engine" \
  "uvicorn main:app --host 0.0.0.0 --port 8001 --reload" 8001

run_service "orchestrator" "$ROOT/apps/orchestrator" \
  "uvicorn main:app --host 0.0.0.0 --port 8000 --reload" 8000

# KIO shells — ports match kio_port_map defaults in shared/config.py Settings
for kio_num in 2 3 4 5 6 7 8 9 10 11 12 13; do
  port=$((8010 + kio_num))
  run_service "kio${kio_num}" "$ROOT/apps/kio_shells/kio${kio_num}" \
    "uvicorn main:app --host 0.0.0.0 --port $port" $port
  sleep 0.2
done

# React dashboard
DASHBOARD_DIR="$ROOT/apps/dashboard"
if [[ -d "$DASHBOARD_DIR/node_modules" ]]; then
  echo "▶  Starting React dashboard on port 5173…"
  if [[ "$BG" == "--bg" ]]; then
    (cd "$DASHBOARD_DIR" && npm run dev > "$ROOT/logs/dashboard.log" 2>&1) &
    echo "   PID $! → logs/dashboard.log"
  else
    osascript -e "tell app \"Terminal\" to do script \"cd '$DASHBOARD_DIR' && npm run dev\"" 2>/dev/null || \
      (cd "$DASHBOARD_DIR" && npm run dev &)
  fi
else
  echo "⚠  Dashboard deps not installed — run: cd apps/dashboard && npm install"
fi

echo ""
echo "All services started."
echo ""
echo "Endpoints:"
echo "  Dashboard:       http://localhost:5173"
echo "  Orchestrator:    http://localhost:8000/docs"
echo "  LM Engine:       http://localhost:8001/docs"
echo "  Session Manager: http://localhost:8002/docs"
echo "  KIO3 (Repo):     http://localhost:8013/docs"
echo "  KIO5 (Bugs):     http://localhost:8015/docs"
echo ""
echo "First run — auth setup:"
echo "  1. POST /auth/register  {username, password}"
echo "  2. POST /auth/login     → get Bearer token"
echo "  3. POST /workflow/run   with Authorization: Bearer <token>"
