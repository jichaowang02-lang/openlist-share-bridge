# OpenList Share Bridge

把网盘分享链接接入 OpenList：粘贴分享链接，临时转存，直接下载，最后自动清理。

OpenList Share Bridge 是一个自托管的小工具，适合已经在服务器上使用 [OpenList](https://github.com/OpenListTeam/OpenList) 的用户。当前版本优先支持百度网盘分享：提交百度网盘分享链接和提取码后，服务会把分享文件临时转存到你的百度网盘，再通过 OpenList 提供下载入口，任务结束后自动删除临时目录。

> 当前先做好百度网盘。项目结构会继续往多网盘适配器方向演进。

## 功能

- 支持直接粘贴百度网盘分享文本，包括从 App 复制出来的整段内容。
- 自动识别分享链接和提取码。
- 把分享文件临时转存到 `/__openlist_tmp/{jobId}`。
- 通过 OpenList 本地 API 获取下载地址。
- 单文件直接下载，下载结束后自动删除百度网盘临时目录。
- 小文件夹后台生成 ZIP，并在页面显示进度。
- 默认超过 `15GB` 的文件夹不在服务器打包，防止 VPS 磁盘被撑满。
- 成功、失败、中断、打包失败都会尝试删除百度网盘临时目录。
- 提供登录系统：管理员登录后使用完整功能，游客只能临时体验说明页。

## 两种安装方式

### 方式一：全新机器安装 OpenList + Bridge

适合一台新服务器，还没有 OpenList。

推荐系统：Ubuntu 22.04 或 Ubuntu 24.04。

```bash
git clone https://github.com/jichaowang02-lang/openlist-share-bridge.git
cd openlist-share-bridge
sudo bash scripts/install-fresh.sh
```

脚本会安装：

- Python 3
- Nginx
- Docker
- OpenList Docker 容器
- BaiduPCS-Go
- OpenList Share Bridge
- systemd 服务
- Nginx 反向代理配置

安装后还需要你手动完成三件事：

1. 打开 OpenList，添加百度网盘存储，并挂载到 `/baidu`。
2. 把 OpenList 管理员 Token 写入 `/opt/openlist-share-bridge/baidu-openlist.env`。
3. 把百度浏览器 Cookie 写入 `/opt/openlist-share-bridge/browser_cookie.txt`。

如果这是新的 OpenList 容器，可以用下面命令查看初始管理员信息：

```bash
sudo docker logs openlist | grep -i -E 'password|token|admin' | tail -20
```

然后重启服务：

```bash
sudo systemctl restart baidu-openlist.service
```

### 方式二：已有 OpenList，只安装 Bridge

适合机器上已经有 OpenList，并且已经挂载了百度网盘。

推荐系统：Ubuntu 22.04 或 Ubuntu 24.04。

```bash
git clone https://github.com/jichaowang02-lang/openlist-share-bridge.git
cd openlist-share-bridge
sudo bash scripts/install-bridge-only.sh
```

安装时需要确认：

- OpenList API 地址默认是 `http://127.0.0.1:5244/openlist`。
- 百度网盘在 OpenList 中的挂载路径默认是 `/baidu`。
- 如果你的路径不同，修改 `/opt/openlist-share-bridge/baidu-openlist.env`。

## 非交互安装

也可以用环境变量一次性安装。

全新机器：

```bash
sudo env PUBLIC_URL=https://drive.example.com \
  SERVER_NAME=drive.example.com \
  OPENLIST_ADMIN_TOKEN=your-openlist-admin-token \
  UI_TOKEN=change-this-ui-token \
  bash scripts/install-fresh.sh
```

已有 OpenList：

```bash
sudo env PUBLIC_URL=https://drive.example.com \
  SERVER_NAME=drive.example.com \
  OPENLIST_ADMIN_TOKEN=your-openlist-admin-token \
  UI_TOKEN=change-this-ui-token \
  BAIDU_MOUNT=/baidu \
  bash scripts/install-bridge-only.sh
```

常用环境变量：

- `PUBLIC_URL`：对外访问域名，例如 `https://drive.example.com`
- `SERVER_NAME`：Nginx 的 `server_name`
- `UI_TOKEN`：Bridge 页面访问 Token
- `OPENLIST_ADMIN_TOKEN`：OpenList 管理员 Token
- `BAIDU_MOUNT`：OpenList 里的百度网盘挂载路径，默认 `/baidu`
- `APP_DIR`：安装目录，默认 `/opt/openlist-share-bridge`
- `OPENLIST_PORT`：OpenList 本地端口，默认 `5244`
- `BRIDGE_PORT`：Bridge 本地端口，默认 `9801`
- `ZIP_LIMIT_BYTES`：服务器 ZIP 上限，默认 `16106127360`，即 15 GiB

## 配置文件

主配置文件：

```bash
/opt/openlist-share-bridge/baidu-openlist.env
```

示例：

```env
BAIDU_OPENLIST_PORT=9801
BAIDU_OPENLIST_TTL_SECONDS=86400
BAIDU_OPENLIST_TOKEN=change-this-ui-token
BAIDU_OPENLIST_ADMIN_USERNAME=admin
BAIDU_OPENLIST_ADMIN_PASSWORD=change-this-admin-password
BAIDU_OPENLIST_SESSION_SECRET=change-this-random-session-secret
BAIDU_OPENLIST_GUEST_ENABLED=1
BAIDU_OPENLIST_GUEST_DAILY_LIMIT=3
BAIDU_OPENLIST_GUEST_GLOBAL_DAILY_LIMIT=100
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

百度 Cookie 文件：

```bash
/opt/openlist-share-bridge/browser_cookie.txt
```

不要把真实配置、浏览器 Cookie、访问 Token、账号凭据或私有部署信息提交到 GitHub。

登录说明：

- `BAIDU_OPENLIST_ADMIN_USERNAME` 和 `BAIDU_OPENLIST_ADMIN_PASSWORD` 是管理员登录账号和密码。
- 如果没有配置 `BAIDU_OPENLIST_ADMIN_PASSWORD`，会退回使用 `BAIDU_OPENLIST_TOKEN`。
- `BAIDU_OPENLIST_SESSION_SECRET` 用来签名浏览器会话 Cookie，建议使用随机长字符串。
- `BAIDU_OPENLIST_GUEST_ENABLED=1` 开启游客访问。
- `BAIDU_OPENLIST_GUEST_DAILY_LIMIT=3` 表示每个游客客户端指纹每天最多提交 3 个真实转存/下载任务。
- `BAIDU_OPENLIST_GUEST_GLOBAL_DAILY_LIMIT=100` 表示所有游客当天最多总共提交 100 个转存任务。
- 游客只能看到自己的任务，管理员可以看到所有任务。
- 公开首页是公益转存页；管理员入口是 `/baidu/admin/login`。

## Nginx 和 HTTPS

安装脚本会写入一个 HTTP 反向代理：

- Bridge：`/baidu/`
- OpenList：`/openlist/`

如果你要启用 HTTPS，可以在 DNS 指向服务器后执行：

```bash
sudo certbot --nginx -d drive.example.com
```

大文件下载不建议无脑走 Cloudflare 代理。页面可以放在 Cloudflare 后面，但大文件下载最好根据你的线路和代理限制单独设计，比如走源站、OpenList 直链或后续的专用下载策略。

## 使用流程

1. 打开 Bridge 页面。
2. 粘贴百度网盘分享链接和提取码。
3. 服务转存到 `/__openlist_tmp/{jobId}`。
4. 页面出现下载按钮或打开目录按钮。
5. 下载完成、失败或中断后，服务会尝试删除百度网盘临时目录。

## 故障排查

运行诊断脚本：

```bash
sudo bash scripts/doctor.sh
```

查看 Bridge 日志：

```bash
sudo journalctl -u baidu-openlist.service -f
```

查看 OpenList 日志：

```bash
sudo docker logs -f openlist
```

常见问题：

- 登录页一直提示密码不正确：检查 `BAIDU_OPENLIST_ADMIN_PASSWORD`；如果没配置，会退回使用 `BAIDU_OPENLIST_TOKEN`。
- 下载成 `.htm`：通常是旧版本误判单文件，更新到最新代码后重新提交任务。
- 百度接口超时：确认 `GODEBUG=http2client=0,netdns=cgo+1` 和 `BAIDU_OPENLIST_FORCE_IPV4=1` 已配置。
- OpenList 取不到文件：确认百度网盘挂载路径和 `BAIDU_OPENLIST_MOUNT` 一致。
- 分享校验失败：百度 Cookie 可能过期，需要重新复制浏览器 Cookie。

## 安全提醒

- 这个服务会操作你的网盘文件，只建议部署在你信任的服务器上。
- Web 页面一定要设置强管理员密码 `BAIDU_OPENLIST_ADMIN_PASSWORD`，并保护好 `BAIDU_OPENLIST_SESSION_SECRET`。
- 百度 Cookie 和 OpenList 管理员 Token 都等同于密码。
- 如果 Cookie、Token 或账号凭据曾经出现在聊天、日志、Issue 或 Git 提交里，建议立即更换。
- 临时目录根路径是 `/__openlist_tmp`，不要把清理逻辑指向你的私人文件夹。

## 项目状态

已实现：

- 百度网盘分享链接识别
- 百度网盘分享校验和转存
- OpenList 下载地址解析
- 单文件下载
- 小文件夹后台 ZIP
- 下载进度页面
- 成功/失败后清理临时目录
- 全新机器安装脚本
- 已有 OpenList 机器安装脚本

计划方向：

- 多网盘适配器接口
- 更独立的前端页面
- Docker Compose 部署
- 更完善的任务取消和状态持久化
- 大文件夹的 OpenList 原生下载策略

## 免责声明

请只下载你有权访问和保存的文件。网盘服务商可能随时调整接口、风控、限速或账号规则，本项目不保证长期兼容所有平台。
