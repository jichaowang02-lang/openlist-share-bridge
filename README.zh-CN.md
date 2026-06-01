# OpenList Share Bridge

把网盘分享链接接入 OpenList：粘贴分享链接，临时转存，直接下载，最后自动清理。

OpenList Share Bridge 是一个自托管的小工具，适合已经在服务器上使用 [OpenList](https://github.com/OpenListTeam/OpenList) 的用户。当前版本优先支持百度网盘分享：你把百度网盘分享链接和提取码粘贴进去，服务会把分享文件临时转存到你的百度网盘，再通过 OpenList 提供下载入口，任务结束后自动删除临时目录。

> 项目还在早期阶段。现在先做好百度网盘，后续会按适配器的方式接入更多网盘。

## 主要功能

- 支持直接粘贴百度网盘分享文本，包括从 App 复制出来的整段内容。
- 自动识别分享链接和提取码。
- 把分享文件临时转存到 `/__openlist_tmp/{jobId}`。
- 通过 OpenList 本地 API 获取下载地址。
- 单文件可直接通过本服务下载，下载结束后自动删除百度网盘临时目录。
- 小文件夹可后台打包 ZIP，并在页面显示进度。
- 默认超过 `15GB` 的文件夹不在服务器打包，防止 VPS 磁盘被撑满。
- 成功、失败、中断、打包失败都会尝试删除百度网盘临时目录。
- 提供简单的 Token 保护 Web 页面，可挂在 `/baidu/` 这类路径下。

## 工作流程

1. 用户提交百度网盘分享链接和提取码。
2. 服务使用你自己的百度浏览器 Cookie 校验分享。
3. 分享文件被转存到你的百度网盘临时目录 `/__openlist_tmp/{jobId}`。
4. OpenList 通过已有的百度网盘挂载看到这个临时目录。
5. 服务通过 OpenList API 解析下载地址，并把文件流式传给浏览器。
6. 任务结束后，不管成功还是失败，都会删除临时目录。

## 环境要求

- Linux 服务器
- Python 3.10+
- 已运行的 OpenList
- OpenList 中已经配置好百度网盘挂载
- `bin/BaiduPCS-Go`
- 百度浏览器 Cookie
- OpenList 管理员 Token
- Nginx、Caddy 或其他反向代理，用于公开访问 Web 页面

## 配置方式

复制示例配置：

```bash
cp baidu-openlist.env.example baidu-openlist.env
```

编辑 `baidu-openlist.env`：

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

把百度浏览器 Cookie 保存到：

```bash
/opt/baidu-openlist/browser_cookie.txt
```

不要把下面这些内容提交到 GitHub：

- `baidu-openlist.env`
- `browser_cookie.txt`
- 百度 Cookie
- OpenList 管理员 Token
- 服务器 IP
- SSH 密码
- 个人域名

## Nginx 示例

仓库里提供了 `nginx-baidu-location.conf`，可以挂到 HTTPS server block 里：

```nginx
location = /baidu {
    return 301 /baidu/;
}

location /baidu/ {
    proxy_pass http://127.0.0.1:9801/baidu/;
}
```

如果你要下载很大的文件，不建议无脑走 Cloudflare 代理。页面可以放在 Cloudflare 后面，但大文件下载最好根据你的线路和代理限制单独设计，比如走源站、OpenList 直链或后续的专用下载策略。

## 安全提醒

- 这个服务会操作你的网盘文件，只建议部署在你信任的服务器上。
- Web 页面一定要设置 `BAIDU_OPENLIST_TOKEN`。
- 百度 Cookie 和 OpenList 管理员 Token 都等同于密码。
- 如果 Cookie、Token、服务器密码曾经出现在聊天、日志、Issue 或 Git 提交里，建议立即更换。
- 临时目录根路径是 `/__openlist_tmp`，不要把清理逻辑指向你的私人文件夹。

## 当前状态

已实现：

- 百度网盘分享链接识别
- 百度网盘分享校验和转存
- OpenList 下载地址解析
- 单文件下载
- 小文件夹后台 ZIP
- 下载进度页面
- 成功/失败后清理临时目录

计划方向：

- 多网盘适配器接口
- 更独立的前端页面
- Docker 部署
- 更完善的任务取消和状态持久化
- 大文件夹的 OpenList 原生下载策略

## 免责声明

请只下载你有权访问和保存的文件。网盘服务商可能随时调整接口、风控、限速或账号规则，本项目不保证长期兼容所有平台。
