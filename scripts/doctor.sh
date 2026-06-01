#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/openlist-share-bridge}"

echo "== System =="
uname -a
python3 --version || true
command -v docker >/dev/null 2>&1 && docker --version || echo "docker: missing"
command -v nginx >/dev/null 2>&1 && nginx -v || echo "nginx: missing"

echo
echo "== Services =="
systemctl is-active baidu-openlist.service 2>/dev/null || true
systemctl is-active nginx 2>/dev/null || true
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true

echo
echo "== Files =="
ls -lah "$APP_DIR" 2>/dev/null || true
test -f "$APP_DIR/baidu-openlist.env" && echo "env: ok" || echo "env: missing"
test -f "$APP_DIR/browser_cookie.txt" && echo "browser_cookie.txt: ok" || echo "browser_cookie.txt: missing"
test -x "$APP_DIR/bin/BaiduPCS-Go" && "$APP_DIR/bin/BaiduPCS-Go" who 2>&1 | head -20 || echo "BaiduPCS-Go: missing"

echo
echo "== HTTP =="
if [ -f "$APP_DIR/baidu-openlist.env" ]; then
  # shellcheck disable=SC1090
  . "$APP_DIR/baidu-openlist.env"
  curl -sS -I "http://127.0.0.1:${BAIDU_OPENLIST_PORT:-9801}${BAIDU_OPENLIST_BASE_PATH:-/baidu}/" | head -10 || true
  curl -sS "http://127.0.0.1:5244/openlist/api/public/settings" | head -20 || true
fi
