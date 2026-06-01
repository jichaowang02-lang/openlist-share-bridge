# Quick Install

Choose one path.

## Fresh Server

Use this if OpenList is not installed yet.

```bash
git clone https://github.com/jichaowang02-lang/openlist-share-bridge.git
cd openlist-share-bridge
sudo env PUBLIC_URL=https://drive.example.com \
  SERVER_NAME=drive.example.com \
  OPENLIST_ADMIN_TOKEN=your-openlist-admin-token \
  UI_TOKEN=change-this-ui-token \
  bash scripts/install-fresh.sh
```

Then:

1. Open `https://drive.example.com/openlist/`.
2. Add Baidu Netdisk storage in OpenList and mount it at `/baidu`.
3. Put a Baidu browser Cookie in `/opt/openlist-share-bridge/browser_cookie.txt`.
4. Restart the bridge:

```bash
sudo systemctl restart baidu-openlist.service
```

## Existing OpenList

Use this if OpenList is already running.

```bash
git clone https://github.com/jichaowang02-lang/openlist-share-bridge.git
cd openlist-share-bridge
sudo env PUBLIC_URL=https://drive.example.com \
  SERVER_NAME=drive.example.com \
  OPENLIST_ADMIN_TOKEN=your-openlist-admin-token \
  UI_TOKEN=change-this-ui-token \
  BAIDU_MOUNT=/baidu \
  bash scripts/install-bridge-only.sh
```

Then put a Baidu browser Cookie in:

```bash
/opt/openlist-share-bridge/browser_cookie.txt
```

## Check

```bash
sudo bash scripts/doctor.sh
sudo journalctl -u baidu-openlist.service -f
```
