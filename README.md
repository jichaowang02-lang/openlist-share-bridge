# OpenList Share Bridge

Paste a cloud-drive share link, bridge it into OpenList, download it from your own server, then automatically clean up the temporary files.

OpenList Share Bridge is a lightweight self-hosted helper for people who already use [OpenList](https://github.com/OpenListTeam/OpenList). It currently focuses on Baidu Netdisk shares: submit a Baidu share URL and extraction code, the service transfers the shared files into a temporary Baidu Netdisk folder, exposes a download flow through OpenList, and deletes the temporary folder after success or failure.

> Early-stage project. Baidu Netdisk is implemented first; the internal flow is intentionally shaped so other cloud drives can be added later.

## Features

- Paste Baidu Netdisk share text directly, including links copied from the Baidu app.
- Automatically extracts share URL and extraction code when possible.
- Transfers shared files to `/__openlist_tmp/{jobId}` in your own Baidu Netdisk.
- Uses OpenList's local API to resolve downloadable file URLs.
- Proxies single-file downloads through this service, then deletes the temporary Baidu folder.
- Builds ZIP files in the background for small folders, with a progress page.
- Refuses to server-side ZIP folders larger than `15GB` by default to avoid filling your VPS disk.
- Deletes temporary Baidu folders on success, failure, interruption, and ZIP preparation failure.
- Provides a simple token-protected web UI that can be reverse-proxied under a path such as `/baidu/`.

## How It Works

1. A user submits a Baidu Netdisk share link and extraction code.
2. The service verifies the share with a browser cookie from your own Baidu account.
3. The shared files are transferred into `/__openlist_tmp/{jobId}`.
4. OpenList sees the temporary folder through your existing Baidu mount.
5. The service downloads through OpenList's API and streams the file to the browser.
6. The temporary Baidu folder is deleted after the task finishes, even if the task fails.

## Repository Name

Recommended GitHub repository name:

```text
openlist-share-bridge
```

Why this name works:

- It is not limited to Baidu, so future cloud-drive adapters fit naturally.
- It explains the job clearly: bridging share links into OpenList.
- It is short enough for CLI commands, Docker images, and GitHub URLs.

Other workable names:

- `openlist-share-gateway`
- `share2openlist`
- `cloud-share-bridge`

## Requirements

- Linux server with Python 3.10+
- OpenList already running
- A Baidu Netdisk mount configured in OpenList
- BaiduPCS-Go available at `bin/BaiduPCS-Go`
- A valid Baidu browser cookie saved outside Git
- OpenList admin token
- Nginx, Caddy, or another reverse proxy if exposing the UI publicly

## Configuration

Copy the example env file:

```bash
cp baidu-openlist.env.example baidu-openlist.env
```

Edit `baidu-openlist.env`:

```env
BAIDU_OPENLIST_PORT=9801
BAIDU_OPENLIST_TTL_SECONDS=86400
BAIDU_OPENLIST_TOKEN=change-this-ui-token
BAIDU_OPENLIST_BASE_PATH=/baidu
BAIDU_OPENLIST_MOUNT=/baidu
BAIDU_OPENLIST_SITE_URL=https://your-domain.example/openlist
BAIDU_OPENLIST_ADMIN_TOKEN=change-this-openlist-admin-token
BAIDU_OPENLIST_API=http://127.0.0.1:5244/openlist
BAIDU_OPENLIST_MAX_SERVER_ZIP_BYTES=16106127360
```

Save your Baidu browser cookie here:

```bash
/opt/baidu-openlist/browser_cookie.txt
```

Do not commit `baidu-openlist.env`, `browser_cookie.txt`, cookies, tokens, server IPs, SSH passwords, or personal domains.

## Reverse Proxy

The included `nginx-baidu-location.conf` shows a path-based proxy example:

```nginx
location = /baidu {
    return 301 /baidu/;
}

location /baidu/ {
    proxy_pass http://127.0.0.1:9801/baidu/;
}
```

For large downloads, avoid proxying the actual file stream through Cloudflare unless you know your plan and limits. The service is designed to keep the UI behind a proxy, while actual large-file strategies may need direct origin access, OpenList links, or future adapter-specific handling.

## Security Notes

- This project controls files in your cloud drive. Run it only on a trusted server.
- Protect the UI with `BAIDU_OPENLIST_TOKEN` and do not expose it without authentication.
- Treat Baidu cookies and OpenList admin tokens like passwords.
- If a cookie or token is pasted into chat, logs, issues, or commits, rotate it.
- The temporary folder root is `/__openlist_tmp`; cleanup code should never be pointed at a personal folder.

## Project Status

Current adapter:

- Baidu Netdisk share transfer and download flow

Planned direction:

- Adapter interface for more cloud drives
- Cleaner frontend separation
- Docker packaging
- Better job persistence and task cancellation
- Optional direct OpenList-only mode for very large folders

## Disclaimer

Use this project only with files you have the right to access and download. Cloud-drive providers may change their web APIs, rate limits, or account rules at any time.
