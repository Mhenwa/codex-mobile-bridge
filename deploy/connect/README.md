# Mhenwa Connect 两节点部署

这是可选的多用户模式，不替换原来的个人 LAN/SSH 网关。电脑发起出站 WSS；手机通过固定 HTTPS 域名访问共享中继。模型调用仍由原 Codex 桌面执行，沿用用户自己的模型接入。

## 三种独立凭据

1. **模型 API Key**：仅在用户同意后由本机读取并发送给固定资格服务，用于确认真实 New API user/token 归属。公网中继不存储这个 Key，不在手机端输入。
2. **设备 Token**：注册生成的独立随机凭据，本机私有 `connect.json` 保存；中继仅保存摘要。每台电脑单独撤销。
3. **手机 Session**：一次性配对二维码在 URL fragment 中携带临时秘密；手机申领后须电脑明确批准。批准后生成独立的 HttpOnly、SameSite=Strict Cookie。电脑可以撤销某部手机。

服务资格和模型推理额度分开。令牌状态 4（额度耗尽）允许连接，以便查看和停止；停用、过期、删除以及账号停用不允许。默认所有符合条件的账号可注册，每个账号最多 5 台设备。生产可通过适配器 `--allowed-user-ids` 设置服务名单。令牌/账号状态按短缓存复核，不意味着永久注册即永久授权。

## 服务布局

- New API 服务器：资格适配器只读绑定数据库目录，宿主仅发布 `127.0.0.1:18788`。
- 现有 API 的 HTTPS Nginx：仅新增 `/_connect/eligible` 和 `/_connect/introspect`，不修改 `/v1` 路由。
- 固定服务器：中继宿主仅发布 `127.0.0.1:18790`（容器内部 18787）；`codex.mhenwa.cc` HTTPS 代理到它。18787 首次启动实际返回地址占用错误，随后检查未发现存活监听，未确认占用进程。为避免干扰旧个人回程，仍保留宿主 18787 不使用；回滚原 Nginx 后恢复原个人入口配置。
- 两服务共用一个**专门生成的 RPC 密钥**。它不是模型 API Key，也不是 New API 管理员 Token，不应提交到仓库。

本机实际已确认的 SQLite 路径是 `/opt/new-api/data/new-api.db`。适配器对索引 `idx_tokens_key` 做点查，只选择 ID、状态、删除标志和过期时间。不得用 `immutable=1`，否则可能忽略实时 WAL。若部署换成 PostgreSQL/MySQL，必须替换受信资格适配器；不能把不完整的 `/api/usage/token` 当作完整身份验证。

## 首次部署（由运维执行）

仓库完整复制到两节点的 `/opt/mhenwa-connect/source`。不要复制本机 `.codex`、模型凭据、私有 Connect 数据目录或任何 `.env`。

生成 32 字节以上随机 RPC 密钥，以权限 0400 保存到各节点 `/opt/mhenwa-connect/eligibility.secret`。中继节点该文件所属 UID 为 10001，资格节点为 root；安全复制时不输出秘密、不放入命令行参数。

New API 节点示例：

```sh
cd /opt/mhenwa-connect/source
export NEW_API_DATA=/opt/new-api/data
export CONNECT_RPC_SECRET_FILE=/opt/mhenwa-connect/eligibility.secret
docker compose -p mhenwa-connect-auth -f deploy/connect/eligibility.compose.yml up -d --build
```

现有数据库目录权限为 root:root 0700。因此资格容器以 UID 0 运行，但移除全部 Linux capabilities、设置 no-new-privileges、根文件系统只读、DB 绑定只读、只有 /tmp 可写；不要为方便放宽原数据库目录的权限。中继不是 root 运行。

将 `eligibility.nginx.inc` 安装为 `/opt/mhenwa-connect/eligibility.nginx.inc`，只在 `api.mhenwa.cc` 的 HTTPS `server {}` 加入：

```nginx
include /opt/mhenwa-connect/eligibility.nginx.inc;
```

先备份旧配置，执行 `nginx -t` 成功再 reload。适配器对两个入口仍检查独立 RPC Bearer；没有这个密钥必须返回 401。

固定服务器示例：

```sh
install -d -o 10001 -g 10001 -m 700 /opt/mhenwa-connect/relay-data
cd /opt/mhenwa-connect/source
export CONNECT_DATA=/opt/mhenwa-connect/relay-data
export CONNECT_RPC_SECRET_FILE=/opt/mhenwa-connect/eligibility.secret
export CONNECT_PUBLIC_ORIGIN=https://codex.mhenwa.cc
export CONNECT_ELIGIBILITY_URL=https://api.mhenwa.cc/_connect/eligible
docker compose -p mhenwa-connect-relay -f deploy/connect/relay.compose.yml up -d --build
```

保留原证书、ACME、HTTP 跳转；将原 HTTPS 的 `location /` 换成 `relay.nginx.inc`。TLS 代理不要自动重试写请求。Cloudflare 需支持 WebSocket，不要缓存 `/api/` 或 `/connect/`。限流使用可信连接来源；不要把客户端自报 X-Forwarded-For 当真实身份。若要按真实访客 IP 限流，另行配置 Cloudflare 官方 IP 段的 real_ip 信任链。

## 验证及恢复

- 未授权访问只能得到配对页面，`/api/sessions` 返回未授权；手机页面没有 API Key 登录框。
- API Key 合格注册返回新的设备凭据；不能访问同账号其他设备。
- 手机扫码先进入 pending；电脑批准后才发放 Cookie；同一申领不能重复消费。
- 关闭电脑/网关应显示离线；断线写请求返回不确定结果而不是静默重发。
- 撤销手机/设备、停用或过期模型 Key 后不能继续操作；检查资格服务不修改 New API 数据库。
- 同时检查 TOTP、draw、New API 原 API 服务的状态和旧配置哈希。

数据库只保存设备/手机/配对元数据，不保存聊天正文或模型 Key。默认无 HTTP access log、无请求体日志。TLS/WSS 是传输加密，**不是端到端加密**；中继运营者技术上能够读取经过的对话。若要对外宣称 E2EE，必须另行实现客户端密钥交换、帧加密和浏览器可信代码分发，不能只改文案。

回滚先停止 Connect 容器，再恢复对应 Nginx 配置并 `nginx -t && systemctl reload nginx`。不删除用户模型 Key，不修改 New API 配置或数据库，不删除 relay-data（保留可恢复元数据）。本次实际部署日志和四角色回滚证据见项目验证记录。

## 本机源码运行

### 网页系统通知（Web Push）

新版中继和新版电脑客户端一起支持关闭手机网页后的任务提醒。手机在已授权的网页“设置 → 手机网页系统通知”中订阅，分别选择待确认与运行完成；每部手机独立保存开关。iPhone/iPad 需 iOS/iPadOS 16.4+，先添加到主屏幕，再从图标打开并授权。Android 需浏览器支持 Web Push 并允许系统通知。

中继 Docker 构建安装 `requirements-relay.txt`，持久 `/data/vapid-private.pem` 和 SQLite 订阅/发送队列。保留原数据卷与资格服务密钥，升级中继镜像和 Windows 客户端即可，不需要修改 Nginx 或添加手机原生 App。详细使用、升级与验证见 [Web Push 文档](../../docs/web-push.md)。

个人模式依然只需 Python 标准库。Connect 模式要求 Python 3.10+（建议 3.13），额外执行 `python -m pip install -r requirements-connect.txt`，然后通过桌面 Mhenwa Connect 页明确同意检测/注册，启动网关后生成配对二维码。现有上游 v1.3.3 安装包不包含这项二开；需要从本分支构建。客户端数据目录由原 Bridge 配置决定，不应把设备凭据放入工作仓库。

## 开放给用户前

当前观测模式不设置在线设备数上限、不设置转发请求并发上限，也不在 Relay compose 中设置容器 `mem_limit` 或 `cpus`；实际可承载量由宿主机、Docker、反代和 Python 进程资源共同决定。仍保留**单帧正文最多 2 MiB**、请求/响应超时、聚合正文缓冲预算和 WebSocket 心跳；大附件/过大的历史响应会拒绝，不会静默转发。注册/认领入口仍按保守共享来源限流，并保留注册并发保护，不宣称支持海量注册。需要恢复保护上限时，可通过 `--max-online-devices N` 和 `--max-inflight N` 重新设置；`0` 表示不限制。

自动更新已锁定 `Mhenwa/codex-mobile-bridge`，不会回到上游个人版。正式 Release 仍需你自己的 Ed25519 发布签名私钥及对应客户端公钥；当前未签名 Windows 本地测试包不是正式 Release，不要把继承的上游签名公钥当作你已经拥有的签名体系。

应先限量内测，完成真正手机浏览器、Windows/macOS 桌面及各 Codex 版本的兼容性验收。还需配额/付费开关、撤销自助页、依赖更新、备份轮换、容量压测、运营隐私说明。共享中继不等于安全沙箱：获准手机拥有允许范围内的 Codex 操作能力，电脑仍需处理审批。
