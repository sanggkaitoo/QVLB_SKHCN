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
# Tat thanh tien trinh cua thu vien (tai/nap mo hinh) de khong lam day log.
export HF_HUB_DISABLE_PROGRESS_BARS=1 TQDM_DISABLE=1 TOKENIZERS_PARALLELISM=false

# --- Log: ghi ra tep trong LOG_DIR (mac dinh data/logs), khong in ra console ---
env_value() { [[ -f .env ]] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d "\"'\r" || true; }
LOG_DIR="${LOG_DIR:-$(env_value LOG_DIR)}"
LOG_DIR="${LOG_DIR:-data/logs}"
LOG_CONSOLE="${LOG_CONSOLE:-$(env_value LOG_CONSOLE)}"
LOG_MAX_MB="${LOG_MAX_MB:-$(env_value LOG_MAX_MB)}"
LOG_MAX_MB="${LOG_MAX_MB:-20}"
mkdir -p "$LOG_DIR"
STARTUP_LOG="$LOG_DIR/startup.log"
STDOUT_LOG="$LOG_DIR/stdout.log"

# Du phong khi chua cai logrotate: xoay vong tep qua LOG_MAX_MB moi lan khoi dong (giu 3 ban nen).
rotate_if_big() {
  local file="$1" limit=$((LOG_MAX_MB * 1024 * 1024))
  [[ -f "$file" ]] || return 0
  (( $(stat -c %s "$file") > limit )) || return 0
  for n in 2 1; do [[ -f "$file.$n.gz" ]] && mv -f "$file.$n.gz" "$file.$((n + 1)).gz"; done
  gzip -c "$file" > "$file.1.gz" && : > "$file"
}
rotate_if_big "$STARTUP_LOG"
rotate_if_big "$STDOUT_LOG"

# Lenh phu (docker compose, migration) ghi vao startup.log; console chi hien trang thai ngan gon.
quiet() {
  echo "=== $(date '+%F %T') $*" >> "$STARTUP_LOG"
  if ! "$@" >> "$STARTUP_LOG" 2>&1; then
    echo "LOI khi chay: $* — xem chi tiet: $STARTUP_LOG" >&2
    tail -n 20 "$STARTUP_LOG" >&2
    return 1
  fi
}

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
  echo "Log container: docker logs --tail 50 qlvb_postgres (hoac qlvb_qdrant)" >&2
  return 1
}

# Canh bao mat khau mac dinh (docker-compose dung gia tri nay neu .env khong dat).
if [[ -f .env ]]; then
  if grep -Eq '^(QDRANT_API_KEY=changeme_qdrant_key|PG_DSN=.*password=changeme_pg)' .env; then
    echo "CANH BAO: QDRANT_API_KEY hoac mat khau PostgreSQL dang la gia tri mac dinh. Hay doi truoc khi mo dich vu ra Internet." >&2
  fi
  if ! grep -Eq '^ADMIN_PASS=.{10,}' .env || grep -Eq '^ADMIN_PASS=(matkhau123|admin|password|changeme)$' .env; then
    echo "CANH BAO: ADMIN_PASS chua dat hoac qua yeu; trang /admin se bi khoa cho den khi doi mat khau (toi thieu 10 ky tu)." >&2
  fi
fi

echo "Dang khoi dong PostgreSQL va Qdrant..."
# Bind mounts in docker-compose.yml preserve data/postgres and data/qdrant.
quiet "${COMPOSE[@]}" up -d postgres qdrant

wait_for_port "PostgreSQL" 5432
wait_for_port "Qdrant" 6333

echo "Dang ap dung migration..."
quiet "$PYTHON_BIN" scripts/apply_migrations.py
quiet "$PYTHON_BIN" -W ignore -c 'from src.core import store; store.ensure_collection()'

APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8081}"
UVICORN_ARGS=(src.main:app --host "$APP_HOST" --port "$APP_PORT")

if [[ "${APP_RELOAD:-false}" == "true" ]]; then
  UVICORN_ARGS+=(--reload)
fi

echo "DocNexus dang chay tai http://127.0.0.1:${APP_PORT}"
echo "Cloudflare Tunnel origin: http://127.0.0.1:${APP_PORT}"
if [[ "${LOG_CONSOLE,,}" == "true" ]]; then
  exec "$PYTHON_BIN" -m uvicorn "${UVICORN_ARGS[@]}"
fi
echo "Log: $LOG_DIR/app.log (loi: error.log, truy cap: access.log). Theo doi: tail -f $LOG_DIR/app.log — Ctrl+C de dung."
# Uvicorn va ung dung tu ghi log vao LOG_DIR; stdout.log chi giu phan ghi thang ra console (thu vien C, su co).
exec "$PYTHON_BIN" -m uvicorn "${UVICORN_ARGS[@]}" >> "$STDOUT_LOG" 2>&1
