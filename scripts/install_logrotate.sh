#!/usr/bin/env bash
# Cài cấu hình logrotate của DocNexus vào /etc/logrotate.d (logrotate chạy hằng ngày qua systemd timer/cron).
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $EUID -ne 0 ]]; then
  echo "Can quyen root: sudo $0" >&2
  exit 1
fi
LOG_DIR="${LOG_DIR:-$(grep -E '^LOG_DIR=' "$ROOT_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'\r" || true)}"
LOG_DIR="${LOG_DIR:-data/logs}"
OWNER="$(stat -c %U "$ROOT_DIR/data")"
GROUP="$(stat -c %G "$ROOT_DIR/data")"
TARGET=/etc/logrotate.d/docnexus
sed -e "s#@ROOT@#$ROOT_DIR#g" -e "s#@LOG_DIR@#${LOG_DIR%/}#g" -e "s#@USER@#$OWNER#g" -e "s#@GROUP@#$GROUP#g" \
  "$ROOT_DIR/config/logrotate/docnexus.conf" > "$TARGET"
chmod 644 "$TARGET"
logrotate --debug "$TARGET" >/dev/null 2>&1 || { echo "Cau hinh logrotate khong hop le:" >&2; logrotate --debug "$TARGET"; exit 1; }
echo "Da cai $TARGET (chu so huu log: $OWNER:$GROUP)."
