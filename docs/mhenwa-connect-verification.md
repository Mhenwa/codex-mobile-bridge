# Mhenwa Connect 实施验证记录（2026-10-05）

这是二开内测实现，不是对原上游 v1.3.3 的功能声明。项目始终绑定 `E:\codex-mobile-bridge`；原始基线提交为 `ac4eab6c0866e77e42f08b29994759d907fcd94f`。模型测试 Key 不写入本文件、源码、命令参数或日志；真实 Key 通过关闭回显的 getpass 输入。

## 实际完成

- 本机 Controller 的 discover/register/approve/revoke/disable，私有设备凭据及离线撤销队列。
- 保留原个人模式：禁用 Connect 不建立其监听或网络连接；启用时使用独立、仅回环、内存认证的内部 GatewayServer，不改变主网关 localAccess/免密规则。
- 共享 HTTP/WSS Relay、SQLite metadata registry、三种独立凭据、电脑批准的一次性手机配对、固定 phone→device 路由。
- 精确双端 allowlist、Origin/CSRF、过期和撤销、后端资格复核、未知写操作不自动重放，平台附件也不自动重试。
- New API SQLite 只读适配器，不调用模型；额度耗尽和服务资格分开，停用/过期/删除/账号停用会拒绝。
- 桌面/手机 UI、可选依赖与冻结打包、Fork 更新源隔离、部署脚手架。

## 自动测试

以下为实际观察，后续追加测试不覆盖既有结果：

| 命令 | 观察结果 |
|---|---|
| `python -m unittest discover -s tests` | 480 tests，OK，5 skipped，exit 0 |
| 最终 `python -B -m unittest discover -s tests` | 481 tests，OK，5 skipped，exit 0，121.981 秒 |
| `npm run test:desktop` | 131 pass，0 fail，exit 0 |
| `npm run test:connect` | 当次 71 Python tests + 13 JS tests，全部通过，exit 0 |
| `npm run test:updater` | 15 pass，0 fail，exit 0 |
| 最后追加 Relay 暂时不可用注册测试 | Relay 23 tests，OK，exit 0 |
| 最后 `python -m unittest discover -s tests -p test_connect*.py` | 72 tests，OK，exit 0 |
| 停止按钮主机路由修复后 Connect JS | 15 pass，0 fail，exit 0 |
| `npm audit --json` | 修复继承的 http-cache-semantics 4.2.0 后为 0 vulnerabilities，exit 0 |
| `git diff --check`、Python 编译及 JS 语法检查 | exit 0 |

Python 全链路测试使用真实 aiohttp HTTP/WS 和真实 GatewayServer，但底层桌面任务执行对象是 fixture/mock：注册→独立 Connector→手机先 pending→电脑批准→独立 Cookie→允许的 GET/POST→提交 ID 保留→手机撤销→设备关闭。没有付费推理。

CI 保留 Python 3.9 的个人标准库模式；Connect 需要 Python 3.10+，在 Python 3.13 的 CI 安装可选依赖运行全套。CI 配置已更改，但不把尚未观察的远程 CI 结果计作通过。

## 双服务器部署

### New API：srv-new-api-164

- 现有容器 `new-api`：v1.0.0-rc.24，数据库目录 `/opt/new-api/data`，只读资格点查使用 `idx_tokens_key`。
- 资格容器 `mhenwa-connect-auth-eligibility-1`：宿主 `127.0.0.1:18788`，数据库绑定只读；不放宽原 0700 数据目录 ACL。
- HTTPS 新增精确 `/_connect/eligible` 与 `/_connect/introspect`，带独立 RPC 凭据才可调用；未认证实际 401。
- New API 原 `/api/status` 仍 200。
- 资格服务实际空闲约 29 MiB，限制 128 MiB；这是空闲测量，不是容量承诺。

### 固定服务器：mhenwa-draw-seoul

- 容器 `mhenwa-connect-relay-relay-1`：宿主 `127.0.0.1:18790`→容器 18787，现有 HTTPS `codex.mhenwa.cc` 代理到它。
- 内测上限：8 台在线设备，2 MiB 单帧正文；8 个全局请求并发，聚合正文预算。超限明确拒绝，不承诺海量用户/大附件。
- 原 codex HTTPS 上游未连通，基线实际 502；新配对页面实际 200，未授权 `/api/sessions` 实际 401。
- `totp.mhenwa.cc` 与 `draw.mhenwa.cc` 实际仍 200，配置哈希分别仍为 `5e244968e84273d73f542bcef71527efa8564b6e99a868e59c0fc05541b8e52b` 与 `d93f9945ac09c7aa6d10e9d5a934a9aa694290cf0632860116b3bc0e73ab66c0`。
- 中继空闲约 28 MiB，限制 256 MiB；这不等于已完成压力测试。

首次 Docker 启动宿主 18787 观察到地址占用退出错误；随后的只读检查未确认存活占用进程。已改为 18790，不停止任何既有隧道。首次容器刚启动时 curl 收到连接重置，稍后独立复查为 200。首次资格 Nginx reload 后立即 curl 曾落到旧 worker 返回 200，后续独立 HTTPS 本地/公网检查均为 401。这些过渡结果不当作成功验证覆盖。

## 公网实际测试（真实私密测试 Key、模拟电脑响应）

实际观察：

```text
MODEL_KEY_ENROLLMENT=PASS; MODEL_KEY_AS_DEVICE_AUTH=401
PAIR_BEFORE_DESKTOP_APPROVAL=UNAUTHORIZED
DESKTOP_APPROVAL_TO_INDEPENDENT_PHONE_COOKIE=PASS
HTTPS_PHONE_TO_WSS_SYNTHETIC_DESKTOP=PASS
PHONE_REVOKE=401
DISPOSABLE_DEVICE_REVOKED=PASS
LIVE_STAGING_SMOKE=PASS; REAL_CODEX_IPC=NOT_TESTED; PHYSICAL_PHONE=NOT_TESTED; MODEL_CALLS=0
```

脚本为 `E:\codex-mobile-bridge\scripts\connect-live-smoke.py`。第一次因测试脚本预期注册 200，而实现正确返回 201，实际退出 1；修正测试预期后重跑退出 0。第一次创建的无手机 fixture 设备已明确撤销，第二次也在 finally 撤销，不遗留在线授权。

单独本机真实 IPC 只读探针：会话数据库健康；77 个非归档线程 metadata；initialize 成功；1 次现有线程 owner discovery 成功。未输出线程 ID/标题/正文/Key；未 send/follow/history/reconnect/激活或修改配置。其与上述公网模拟测试是不同验证范围，不能合并声称真实手机发送任务已经验证。

随后执行了**公网到真实本机 Bridge 的独立只读全链路验收**：真实测试 Key 注册→原 Connector 连接 WSS→合成手机 Cookie 经本地批准→公网 `GET /api/sessions`→原真实 GatewayServer/Bridge/SessionStore 返回 77 条 metadata（HTTP 200）→本机真实 IPC initialize 和 owner discovery 成功。探针只放行该 GET，native 守卫没有拦截到其他方法；不读取模型配置/认证，不发送任务、follow、history 或激活线程，不枚举 SSH 主机。手机和设备均撤销，自建资源均关闭，项目源码哈希前后相同。实际退出 0，`passed:true`。记录为 `E:\codex-mobile-bridge\.local\connect-verification\real-owner-relay-smoke.json`。这证明公网授权路径能到达真实本机组件；仍不等于物理手机或真实模型写操作的验收。

停止按钮修复部署后，又独立执行相同只读流程，实际退出 0，77 条 metadata，全部自建手机/设备撤销；已授权浏览器的 `/app.js` 实际 HTTP 200，SHA256 与当前本机源码一致（`2ac2a6227355bcf63f15ba7d975a4b2a365ce488c883c4ab5b3e1bd901de9e4b`）。结果追加为 `E:\codex-mobile-bridge\.local\connect-verification\real-owner-relay-after-ui.json`，不覆盖此前验收记录。

### 经用户单独授权的真实模型单回合验收

用户明确允许“仅测试新建的临时聊天，不触碰现有聊天”后，实际运行 ignored `real-owner-write-smoke.py`，exit 0，`passed:true`。公网注册/电脑批准手机配对后，手机通过真实 Relay/Connector/Gateway 创建新的 **Connect 真实公网验收临时聊天**，打开原桌面 App，跟随该唯一新 UUID 的状态，原生提交一次短文本请求，模型实际回复 `CONNECT_OK`，任务终态 `completed`。

- 公网 create/send 实际均 HTTP 200；native start count=1，create/send attempt count 均为 1，真实 follow count>0。
- 守卫只允许测试新线程；其他线程的 store/history/follow/send/stop 与工具审批全部拒绝，实际未发生守卫拒绝或现有线程操作。SSH、账号管理、goal 禁用。
- 工具 item=0，空测试工作目录文件数仍为 0；手机/设备均撤销，自建资源均关闭，项目源码哈希未改变。
- 注册使用私密测试 Key；模型任务沿用当前桌面的模型提供商与凭据，不替换全局认证/配置，没有硬性单次费用上限的虚假声明。
- 回答已完成后发送一次 stop，实际 HTTP 400，错误为“当前没有可停止的任务”。这是终态保护，native interrupt count=0；没有为测试停止追加第二模型回合。
- 临时聊天和它的空工作目录保留；私有 ledger 保存其 UUID，不纳入公开源码。本次记录：`E:\codex-mobile-bridge\.local\connect-verification\real-owner-write-smoke.json`。

这是真实公网到原桌面 owner 的创建/跟随/单回合模型发送与完成验收，但手机端仍是合成 HTTP 客户端，不等于物理手机 UI、所有模型/系统版本或运行中 interrupt 已经验收。

## Windows 实物构建

- 隔离 venv 安装 PyInstaller 6.20.0、certifi 2026.7.22、aiohttp 3.14.3。
- 实际 `python scripts/build-desktop.py` 与 `npm run pack:desktop` 都 exit 0。
- frozen 网关：隔离假 Codex home/IPC/runtime 验证 connect.status、help、personal health 200、未授权 sessions 401、禁用不连网、正常停止回收。
- ASAR 中桌面 Connect/私有 IPC/Fork updater 源文件实际字节匹配。
- **未代码签名**，Authenticode 实际 `NotSigned`，不称签名发行版。

完整免安装包：

```text
E:\codex-mobile-bridge\dist\Mhenwa-Codex-Connect-Windows-x64-beta.zip
175043840 bytes
SHA256 432251670bc3c97670e676f6a6503671534ab2a12aaafc9648aef70e95632a3c
```

ZIP 已独立重开，321 文件完整，全部 CRC 正确，无 `.local`/`.tmp`，主 EXE、ASAR、内置网关哈希与构建源一致。完整目录解压后运行，不能只取 EXE。

停止按钮修复后重新构建/打包。中断曾导致新 PyInstaller `dist/codex-mobile-gateway` 已构建成功而尚未 promote，Electron 最初仍读旧 `dist/gateway`；字节核验发现该错误后保留旧资源并完成 promote，再实际 pack。新 frozen smoke 不仅验证禁用模式，还通过 HTTP `GET /app.js` 确认停止按钮资源与当前源码一致；错误 Host 和两种顺序的重复 Host 都实际 403。旧 ZIP/manifest/日志保留在 ignored `.local/connect-build-history/before-stop-host-refresh-20261005`，不以旧包宣称后来的改动已经包含。

## 四角色事务与保留的原记录

原个人反代四角色不覆盖，固定服务器仍保留：

```text
/root/codex-mobile-bridge-codex-nginx/MODIFIED_FILE
/root/codex-mobile-bridge-codex-nginx/DIFF_FILE
/root/codex-mobile-bridge-codex-nginx/VERIFICATION.txt
/root/codex-mobile-bridge-codex-nginx/ROLLBACK.sh
```

本轮新增资格服务器事务：

```text
/root/mhenwa-connect-eligibility-transaction/MODIFIED_FILE
/root/mhenwa-connect-eligibility-transaction/DIFF_FILE
/root/mhenwa-connect-eligibility-transaction/VERIFICATION.txt
/root/mhenwa-connect-eligibility-transaction/ROLLBACK.sh
```

本轮新增固定服务器事务：

```text
/root/mhenwa-connect-relay-transaction/MODIFIED_FILE
/root/mhenwa-connect-relay-transaction/DIFF_FILE
/root/mhenwa-connect-relay-transaction/VERIFICATION.txt
/root/mhenwa-connect-relay-transaction/ROLLBACK.sh
```

两次本轮 Nginx 事务均在独立副本上用相同命令/输入执行 BASELINE、MODIFIED、ROLLBACK 的 `nginx -t`，均 exit 0；实际执行自包含 ROLLBACK.sh，恢复哈希等于原始，再保留修改版。配置检查成功后才应用 live/reload，均 exit 0。未把 live 用户服务进行破坏性的现场回滚。所有上述四角色均重新读取了字节、大小和 SHA256。

源码四角色由 `scripts/connect-source-transaction.py` 生成：原提交 ZIP、当前 git-visible 源码 ZIP、重建精确修改 ZIP 的 Git binary patch、原字节恢复脚本、相同输入三状态 probe 的字面 stdout/stderr/exit 和哈希。记录位置为 `E:\codex-mobile-bridge\.local\connect-source-transaction`；不把私有 runtime 目录纳入源码 ZIP。

该 probe 对三状态执行相同原生 `desktop.py connect` 命令、相同 `{"action":"status"}` 输入：原基线/恢复版没有该新增 CLI action，预期拒绝 exit 2；修改版返回安全的 `disabled` Connect 状态，exit 0，随后执行完整 Connect 测试。恢复比较是 ZIP 的原始字节 SHA256，差异重建也比较 ZIP 的精确字节，而不只比较路径或文件数。Windows `sh` 实际执行 portable ROLLBACK.sh。

首次源码验收已完成差异重建、BASELINE exit 2、MODIFIED exit 0，但回滚阶段因脚本只搜索 C 盘默认 Git 安装目录而退出 1；本机 Git 实际安装在 D 盘。续跑第一次实际返回 `--resume` 参数不支持（exit 2）。现脚本增加 `--resume` 和根据真实 git.exe 路径定位 sh：原基线与早期候选/记录保留，再验证更新后的源码候选。两次错误及后续字面命令结果保留在同一事务目录，不用新的目录替换原记录。

### 停止按钮补充事务

最终复核发现原手机 UI 的停止按钮遗漏所选 SSH 主机参数。精确修改 `web/app.js` 的 `$('stop').onclick` 为 `api(sessionUrl(currentId,'stop'),{})`，保留原提交语义；新增测试验证编码后的主机目标、local 目标以及未知结果不自动重发。补充四角色，不替换既有源码事务：

```text
E:\codex-mobile-bridge\.local\connect-stop-ui-transaction\MODIFIED_FILE.js
E:\codex-mobile-bridge\.local\connect-stop-ui-transaction\DIFF_FILE.patch
E:\codex-mobile-bridge\.local\connect-stop-ui-transaction\VERIFICATION.txt
E:\codex-mobile-bridge\.local\connect-stop-ui-transaction\ROLLBACK.sh
```

同一 probe 的 BASELINE/MODIFIED/ROLLBACK 退出码为 1/0/1（路由用例 0/2→2/2→0/2），实际 portable rollback exit 0，恢复 SHA256 等于原始 `4306f1ae61e9b19d8207a4ae14de14186ecb3de17a23161f8e4d06937d40fddb`；之后重放修复，diff 精确重建成功，四角色均重开。中继源码与运行容器已重建为新静态资源，Docker compose exit 0。这是主机路由回归验收，不把该 probe 当作真实模型 interrupt 的证据。

## 尚未声称完成的项目

- 真实物理手机浏览器操作、运行中 native interrupt、Windows/macOS 各版本完整 UI/IPC 实机矩阵；合成手机已完成一次真实模型回合，不替代这些范围。
- 并发压力/断线长跑、分块传输、真实访客限流信任链、付费/独立配额、自助恢复台、备份轮换。
- Mhenwa 自有更新签名密钥/对应公钥、正式安装包与代码签名发行。
- 端到端加密。当前仅 TLS/WSS；Relay 运营方技术上能看到经过的对话内容。

请先内测，不作为无限量公众正式版推广。具体本机源码/构建证据位于 ignored `.local/connect-verification`，私有测试输入不进入 public 文档。
