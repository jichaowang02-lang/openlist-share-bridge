#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

need_root
ensure_user
install_packages
copy_app_files
download_baidupcs_go
write_env
install_baidu_cookie
install_systemd_service
install_nginx_site

systemctl restart baidu-openlist.service
systemctl restart nginx

print_next_steps
