#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

need_root
ensure_user
install_packages
install_docker_if_missing
copy_app_files
download_baidupcs_go
write_env
install_baidu_cookie

mkdir -p "$OPENLIST_DATA_DIR"
chown -R "$APP_USER:$APP_GROUP" "$(dirname "$OPENLIST_DATA_DIR")"

docker rm -f "$OPENLIST_CONTAINER" >/dev/null 2>&1 || true
docker pull "$OPENLIST_IMAGE"
docker run -d \
  --name "$OPENLIST_CONTAINER" \
  --restart unless-stopped \
  -p "$OPENLIST_PORT:5244" \
  -v "$APP_DIR/downloads:/downloads/openlist-share-bridge" \
  -v "$OPENLIST_DATA_DIR:/opt/openlist/data" \
  "$OPENLIST_IMAGE"

install_systemd_service
install_nginx_site

systemctl restart baidu-openlist.service
systemctl restart nginx

print_next_steps
