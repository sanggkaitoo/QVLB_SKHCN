#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Khong tim thay Docker Compose. Hay cai Docker va Docker Compose truoc." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker chua san sang. Hay khoi dong Docker truoc." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Khong tim thay Python tai: $PYTHON_BIN" >&2
  exit 1
fi

# Uvicorn, Playwright and extraction workers inherit this bounded high limit.
HARD_NOFILE="$(ulimit -Hn)"
TARGET_NOFILE=262144
if (( HARD_NOFILE < TARGET_NOFILE )); then
  TARGET_NOFILE="$HARD_NOFILE"
fi
ulimit -Sn "$TARGET_NOFILE" || true
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"

wait_for_port() {
  local name="$1"
  local port="$2"
  local attempts="${3:-60}"

  for ((i = 1; i <= attempts; i++)); do
    if (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1; then
      echo "$name da san sang tren cong $port."
      return 0
    fi
    sleep 1
  done

  echo "$name khong san sang sau ${attempts} giay." >&2
  "${COMPOSE[@]}" ps >&2
  return 1
}

echo "Dang khoi dong PostgreSQL va Qdrant..."
# Bind mounts in docker-compose.yml preserve data/postgres and data/qdrant.
"${COMPOSE[@]}" up -d postgres qdrant

wait_for_port "PostgreSQL" 5432
wait_for_port "Qdrant" 6333

echo "Dang ap dung migration..."
"$PYTHON_BIN" scripts/apply_migrations.py

APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8081}"
UVICORN_ARGS=(src.main:app --host "$APP_HOST" --port "$APP_PORT")

if [[ "${APP_RELOAD:-false}" == "true" ]]; then
  UVICORN_ARGS+=(--reload)
fi

echo "DocNexus dang chay tai http://127.0.0.1:${APP_PORT}"
echo "Cloudflare Tunnel origin: http://127.0.0.1:${APP_PORT}"
exec "$PYTHON_BIN" -m uvicorn "${UVICORN_ARGS[@]}"
