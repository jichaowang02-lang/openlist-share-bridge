#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/openlist-share-bridge}"
APP_USER="${APP_USER:-ubuntu}"
APP_GROUP="${APP_GROUP:-ubuntu}"
OPENLIST_DATA_DIR="${OPENLIST_DATA_DIR:-/opt/openlist/data}"
OPENLIST_CONTAINER="${OPENLIST_CONTAINER:-openlist}"
OPENLIST_IMAGE="${OPENLIST_IMAGE:-openlistteam/openlist:latest}"
OPENLIST_PORT="${OPENLIST_PORT:-5244}"
BRIDGE_PORT="${BRIDGE_PORT:-9801}"
BASE_PATH="${BASE_PATH:-/baidu}"
OPENLIST_BASE_PATH="${OPENLIST_BASE_PATH:-/openlist}"
BAIDU_MOUNT="${BAIDU_MOUNT:-/baidu}"
ZIP_LIMIT_BYTES="${ZIP_LIMIT_BYTES:-16106127360}"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root." >&2
    exit 1
  fi
}

ensure_user() {
  if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$APP_USER"
  fi
}

install_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl unzip python3 nginx
}

install_docker_if_missing() {
  if command -v docker >/dev/null 2>&1; then
    return
  fi
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" >/etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

copy_app_files() {
  local src_dir
  src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  mkdir -p "$APP_DIR"/{bin,downloads,jobs,logs,zip_cache,tmp}
  install -m 0755 -o "$APP_USER" -g "$APP_GROUP" "$src_dir/app.py" "$APP_DIR/app.py"
  if [ ! -f "$APP_DIR/baidu-openlist.env" ]; then
    install -m 0600 -o "$APP_USER" -g "$APP_GROUP" "$src_dir/baidu-openlist.env.example" "$APP_DIR/baidu-openlist.env"
  fi
  chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
}

download_baidupcs_go() {
  local arch asset api_url download_url tmp_dir bin_path
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) asset="linux-amd64" ;;
    aarch64|arm64) asset="linux-arm64" ;;
    *) echo "Unsupported architecture: $arch" >&2; exit 1 ;;
  esac
  api_url="https://api.github.com/repos/qjfoidnh/BaiduPCS-Go/releases/latest"
  download_url="$(python3 - "$asset" "$api_url" <<'PY'
import json, sys, urllib.request
asset = sys.argv[1]
url = sys.argv[2]
data = json.load(urllib.request.urlopen(url, timeout=30))
for item in data.get("assets", []):
    name = item.get("name", "")
    if asset in name and name.endswith(".zip"):
        print(item["browser_download_url"])
        break
else:
    raise SystemExit(f"No BaiduPCS-Go asset found for {asset}")
PY
)"
  tmp_dir="$APP_DIR/tmp/baidupcs-go"
  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir"
  curl -fL "$download_url" -o "$tmp_dir/BaiduPCS-Go.zip"
  unzip -q "$tmp_dir/BaiduPCS-Go.zip" -d "$tmp_dir"
  bin_path="$(find "$tmp_dir" -type f -name BaiduPCS-Go | head -1)"
  install -m 0755 -o "$APP_USER" -g "$APP_GROUP" "$bin_path" "$APP_DIR/bin/BaiduPCS-Go"
}

write_env() {
  local public_url ui_token admin_token
  public_url="${PUBLIC_URL:-}"
  ui_token="${UI_TOKEN:-}"
  admin_token="${OPENLIST_ADMIN_TOKEN:-}"

  if [ -z "$public_url" ]; then
    read -r -p "Public base URL, e.g. https://drive.example.com: " public_url
  fi
  if [ -z "$ui_token" ]; then
    ui_token="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"
  fi
  if [ -z "$admin_token" ]; then
    read -r -p "OpenList admin token (leave empty if OpenList is not initialized yet): " admin_token
  fi

  cat >"$APP_DIR/baidu-openlist.env" <<EOF
BAIDU_OPENLIST_PORT=$BRIDGE_PORT
BAIDU_OPENLIST_TTL_SECONDS=86400
BAIDU_OPENLIST_TOKEN=$ui_token
BAIDU_OPENLIST_ADMIN_PASSWORD=${ADMIN_PASSWORD:-$ui_token}
BAIDU_OPENLIST_SESSION_SECRET=${SESSION_SECRET:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)}
BAIDU_OPENLIST_GUEST_ENABLED=${GUEST_ENABLED:-1}
BAIDU_OPENLIST_GUEST_DAILY_LIMIT=${GUEST_DAILY_LIMIT:-3}
BAIDU_OPENLIST_BASE_PATH=$BASE_PATH
GODEBUG=http2client=0,netdns=cgo+1
BAIDU_OPENLIST_FORCE_IPV4=1
BAIDU_OPENLIST_MOUNT=$BAIDU_MOUNT
BAIDU_OPENLIST_SITE_URL=${public_url%/}$OPENLIST_BASE_PATH
BAIDU_OPENLIST_ADMIN_TOKEN=$admin_token
BAIDU_OPENLIST_API=http://127.0.0.1:$OPENLIST_PORT$OPENLIST_BASE_PATH
BAIDU_OPENLIST_MAX_SERVER_ZIP_BYTES=$ZIP_LIMIT_BYTES
BAIDU_OPENLIST_PAGE_SIZE=200
EOF
  chmod 600 "$APP_DIR/baidu-openlist.env"
  chown "$APP_USER:$APP_GROUP" "$APP_DIR/baidu-openlist.env"
}

install_systemd_service() {
  local src_dir
  src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  sed "s#/opt/openlist-share-bridge#$APP_DIR#g; s#User=ubuntu#User=$APP_USER#g; s#Group=ubuntu#Group=$APP_GROUP#g" \
    "$src_dir/systemd/baidu-openlist.service" >/etc/systemd/system/baidu-openlist.service
  systemctl daemon-reload
  systemctl enable baidu-openlist.service
}

install_nginx_site() {
  local src_dir server_name conf
  src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  server_name="${SERVER_NAME:-_}"
  conf="/etc/nginx/sites-available/openlist-share-bridge"
  cp "$src_dir/nginx/openlist-share-bridge.conf" "$conf"
  sed -i "s/server_name _;/server_name $server_name;/" "$conf"
  ln -sfn "$conf" /etc/nginx/sites-enabled/openlist-share-bridge
  nginx -t
  systemctl reload nginx
}

install_baidu_cookie() {
  if [ -n "${BAIDU_COOKIE_FILE:-}" ] && [ -f "$BAIDU_COOKIE_FILE" ]; then
    install -m 0600 -o "$APP_USER" -g "$APP_GROUP" "$BAIDU_COOKIE_FILE" "$APP_DIR/browser_cookie.txt"
    return
  fi
  if [ ! -f "$APP_DIR/browser_cookie.txt" ]; then
    cat >"$APP_DIR/browser_cookie.txt" <<'EOF'
# Paste your Baidu browser Cookie here, then remove this comment line.
EOF
    chmod 600 "$APP_DIR/browser_cookie.txt"
    chown "$APP_USER:$APP_GROUP" "$APP_DIR/browser_cookie.txt"
  fi
}

print_next_steps() {
  local public_url
  public_url="$(sed -n 's#^BAIDU_OPENLIST_SITE_URL=##p' "$APP_DIR/baidu-openlist.env" | sed "s#$OPENLIST_BASE_PATH\$##")"
  cat <<EOF

Install finished.

Bridge UI:
  ${public_url%/}$BASE_PATH/

OpenList:
  ${public_url%/}$OPENLIST_BASE_PATH/

Before testing a Baidu share:
  1. Put a valid Baidu browser Cookie in $APP_DIR/browser_cookie.txt
  2. Make sure OpenList has a Baidu storage mounted at $BAIDU_MOUNT
  3. Make sure BAIDU_OPENLIST_ADMIN_TOKEN matches your OpenList admin token

Useful commands:
  systemctl status baidu-openlist.service
  journalctl -u baidu-openlist.service -f
  docker logs -f $OPENLIST_CONTAINER
EOF
}
