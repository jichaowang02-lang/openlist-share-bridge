# OpenList Share Bridge

[简体中文](README.zh-CN.md)

Paste a cloud-drive share link, bridge it into OpenList, download it from your own server, then automatically clean up the temporary files.

OpenList Share Bridge is a lightweight self-hosted helper for people who already use [OpenList](https://github.com/OpenListTeam/OpenList). It currently focuses on Baidu Netdisk shares: submit a Baidu share URL and extraction code, the service transfers the shared files into a temporary Baidu Netdisk folder, exposes a download flow through OpenList, and deletes the temporary folder after success or failure.

> Baidu Netdisk is implemented first. The project is expected to evolve toward a multi-provider adapter model.

## Features

- Paste Baidu Netdisk share text directly, including links copied from the Baidu app.
- Automatically extracts share URL and extraction code when possible.
- Transfers shared files to `/__openlist_tmp/{jobId}` in your own Baidu Netdisk.
- Uses OpenList's local API to resolve downloadable file URLs.
- Proxies single-file downloads through this service, then deletes the temporary Baidu folder.
- Builds ZIP files in the background for small folders, with a progress page.
- Refuses to server-side ZIP folders larger than `15GB` by default to avoid filling your VPS disk.
- Deletes temporary Baidu folders on success, failure, interruption, and ZIP preparation failure.
- Provides a login system: admins can use the full workflow, guests only get a temporary demo page.

## Installation Paths

### Option 1: Fresh Server, Install OpenList + Bridge

Use this when the server does not have OpenList yet.

Recommended OS: Ubuntu 22.04 or 24.04.

```bash
git clone https://github.com/jichaowang02-lang/openlist-share-bridge.git
cd openlist-share-bridge
sudo bash scripts/install-fresh.sh
```

The script installs:

- Python 3
- Nginx
- Docker
- OpenList Docker container
- BaiduPCS-Go
- OpenList Share Bridge
- systemd service
- Nginx reverse proxy config

After installation, you still need to:

1. Open OpenList, add a Baidu Netdisk storage, and mount it at `/baidu`.
2. Put the OpenList admin token in `/opt/openlist-share-bridge/baidu-openlist.env`.
3. Put a valid Baidu browser Cookie in `/opt/openlist-share-bridge/browser_cookie.txt`.

If this is a new OpenList container, get the initial admin information with:

```bash
sudo docker logs openlist | grep -i -E 'password|token|admin' | tail -20
```

Then restart the service:

```bash
sudo systemctl restart baidu-openlist.service
```

### Option 2: Existing OpenList, Install Bridge Only

Use this when OpenList is already running and Baidu Netdisk is already mounted.

Recommended OS: Ubuntu 22.04 or 24.04.

```bash
git clone https://github.com/jichaowang02-lang/openlist-share-bridge.git
cd openlist-share-bridge
sudo bash scripts/install-bridge-only.sh
```

Check these assumptions:

- OpenList API defaults to `http://127.0.0.1:5244/openlist`.
- Baidu Netdisk is mounted at `/baidu` in OpenList.
- If your paths differ, edit `/opt/openlist-share-bridge/baidu-openlist.env`.

## Non-Interactive Install

Fresh server:

```bash
sudo env PUBLIC_URL=https://drive.example.com \
  SERVER_NAME=drive.example.com \
  OPENLIST_ADMIN_TOKEN=your-openlist-admin-token \
  UI_TOKEN=change-this-ui-token \
  bash scripts/install-fresh.sh
```

Existing OpenList:

```bash
sudo env PUBLIC_URL=https://drive.example.com \
  SERVER_NAME=drive.example.com \
  OPENLIST_ADMIN_TOKEN=your-openlist-admin-token \
  UI_TOKEN=change-this-ui-token \
  BAIDU_MOUNT=/baidu \
  bash scripts/install-bridge-only.sh
```

Common environment variables:

- `PUBLIC_URL`: public base URL, for example `https://drive.example.com`
- `SERVER_NAME`: Nginx `server_name`
- `UI_TOKEN`: Bridge web UI token
- `OPENLIST_ADMIN_TOKEN`: OpenList admin token
- `BAIDU_MOUNT`: Baidu mount path in OpenList, default `/baidu`
- `APP_DIR`: install directory, default `/opt/openlist-share-bridge`
- `OPENLIST_PORT`: local OpenList port, default `5244`
- `BRIDGE_PORT`: local Bridge port, default `9801`
- `ZIP_LIMIT_BYTES`: server ZIP limit, default `16106127360` or 15 GiB

## Configuration

Main config:

```bash
/opt/openlist-share-bridge/baidu-openlist.env
```

Example:

```env
BAIDU_OPENLIST_PORT=9801
BAIDU_OPENLIST_TTL_SECONDS=86400
BAIDU_OPENLIST_TOKEN=change-this-ui-token
BAIDU_OPENLIST_ADMIN_PASSWORD=change-this-admin-password
BAIDU_OPENLIST_SESSION_SECRET=change-this-random-session-secret
BAIDU_OPENLIST_GUEST_ENABLED=1
BAIDU_OPENLIST_GUEST_DAILY_LIMIT=3
BAIDU_OPENLIST_BASE_PATH=/baidu
GODEBUG=http2client=0,netdns=cgo+1
BAIDU_OPENLIST_FORCE_IPV4=1
BAIDU_OPENLIST_MOUNT=/baidu
BAIDU_OPENLIST_SITE_URL=https://drive.example.com/openlist
BAIDU_OPENLIST_ADMIN_TOKEN=change-this-openlist-admin-token
BAIDU_OPENLIST_API=http://127.0.0.1:5244/openlist
BAIDU_OPENLIST_MAX_SERVER_ZIP_BYTES=16106127360
BAIDU_OPENLIST_PAGE_SIZE=200
```

Baidu browser Cookie file:

```bash
/opt/openlist-share-bridge/browser_cookie.txt
```

Do not commit real environment files, browser cookies, access tokens, account credentials, or private deployment details.

Login notes:

- `BAIDU_OPENLIST_ADMIN_PASSWORD` is the admin login password.
- If `BAIDU_OPENLIST_ADMIN_PASSWORD` is not set, the service falls back to `BAIDU_OPENLIST_TOKEN`.
- `BAIDU_OPENLIST_SESSION_SECRET` signs browser session cookies. Use a long random value.
- `BAIDU_OPENLIST_GUEST_ENABLED=1` enables guest access.
- `BAIDU_OPENLIST_GUEST_DAILY_LIMIT=3` lets each guest session submit up to 3 real transfer/download tasks per day.
- Guests can only see their own tasks. Admins can see all tasks.

## Nginx and HTTPS

The install scripts create an HTTP reverse proxy:

- Bridge: `/baidu/`
- OpenList: `/openlist/`

After DNS points to your server, enable HTTPS with:

```bash
sudo certbot --nginx -d drive.example.com
```

For large downloads, avoid proxying the actual file stream through Cloudflare unless you know your plan and limits. The service is designed to keep the UI behind a proxy, while actual large-file strategies may need direct origin access, OpenList links, or future adapter-specific handling.

## Usage

1. Open the Bridge page.
2. Paste a Baidu Netdisk share link and extraction code.
3. The service transfers the share into `/__openlist_tmp/{jobId}`.
4. The page shows a direct download button or an OpenList directory button.
5. When the task succeeds, fails, or is interrupted, the service attempts to delete the temporary Baidu folder.

## Troubleshooting

Run the doctor script:

```bash
sudo bash scripts/doctor.sh
```

Bridge logs:

```bash
sudo journalctl -u baidu-openlist.service -f
```

OpenList logs:

```bash
sudo docker logs -f openlist
```

Common issues:

- Login page keeps rejecting the password: check `BAIDU_OPENLIST_ADMIN_PASSWORD`. If it is not set, the password falls back to `BAIDU_OPENLIST_TOKEN`.
- Browser downloads `.htm`: update to the latest version and submit the task again.
- Baidu requests time out: check `GODEBUG=http2client=0,netdns=cgo+1` and `BAIDU_OPENLIST_FORCE_IPV4=1`.
- OpenList cannot find files: make sure the OpenList Baidu mount path matches `BAIDU_OPENLIST_MOUNT`.
- Share verification fails: the Baidu browser Cookie may be expired.

## Security Notes

- This project controls files in your cloud drive. Run it only on a trusted server.
- Protect the UI with a strong `BAIDU_OPENLIST_ADMIN_PASSWORD` and keep `BAIDU_OPENLIST_SESSION_SECRET` private.
- Treat Baidu cookies and OpenList admin tokens like passwords.
- If a cookie or token is pasted into chat, logs, issues, or commits, rotate it.
- The temporary folder root is `/__openlist_tmp`; cleanup code should never be pointed at a personal folder.

## Project Status

Implemented:

- Baidu Netdisk share link parsing
- Baidu share verification and transfer
- OpenList download URL resolution
- Single-file download
- Small-folder background ZIP
- Download progress page
- Cleanup after success or failure
- Fresh server install script
- Existing OpenList install script

Planned:

- Multi-provider adapter interface
- Cleaner frontend separation
- Docker Compose deployment
- Better job cancellation and persistence
- OpenList-native strategy for large folders

## Disclaimer

Use this project only with files you have the right to access and download. Cloud-drive providers may change their web APIs, rate limits, or account rules at any time.
