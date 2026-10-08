<p align="center">
  <img src="site/assets/icon.png" alt="Mhenwa Connect" width="96" height="96">
</p>

# Codex Mobile Bridge · Mhenwa Connect

在手机上接着使用电脑里的 Codex 聊天：查看结果、发送任务、选择模型与 Skill，并处理等待确认的操作。

本仓库是 [try2love/codex-mobile-bridge](https://github.com/try2love/codex-mobile-bridge) 的二次开发版本，加入 **Mhenwa Connect 远程接入、电脑批准手机配对和 Web Push 通知**。模型请求和任务执行仍由原来的 Codex App 完成，沿用对应聊天的提供商、认证和工作目录。

**[下载最新正式版](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest)** · **[手机 Connect 入口](https://codex.mhenwa.cc)** · **[全部发行版本](https://github.com/Mhenwa/codex-mobile-bridge/releases)**

## 当前版本

首个正式 Release 为 **v1.4.0**，已合并上游 v1.4.0，并包含 Connect、Web Push、Windows HTTPS 证书加载修复和 Linux x64 / ARM64 安装包。

`main` 后续新增了注册成功的绿色提示框：显示电脑名称与设备 ID，方便确认注册结果。该界面改动暂未包含在已发布的 v1.4.0 安装包中；源码和 Actions 构建可能领先于正式 Release。Release 页面中的版本说明是安装包功能的依据。

本仓库安装包包含 Mhenwa Connect。上游安装包属于个人网关版本，下载时请确认仓库为 **Mhenwa/codex-mobile-bridge**。

## 下载桌面 App

日常使用直接下载安装包，无需安装 Python、Node.js 或配置开发环境。

| 系统 | 推荐下载 | 其他形式 |
| --- | --- | --- |
| Windows x64 | [Setup.exe 安装版](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-Windows-x64-Setup.exe) | [ZIP 便携版](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-Windows-x64.zip) |
| macOS Apple Silicon / M 系列 | [ARM64 DMG](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-macOS-arm64.dmg) | [ARM64 ZIP](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-macOS-arm64.zip) |
| macOS Intel | [x64 DMG](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-macOS-x64.dmg) | [x64 ZIP](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-macOS-x64.zip) |
| Ubuntu x64 / amd64（实验性） | [DEB](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-Linux-amd64.deb) | [AppImage](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-Linux-x86_64.AppImage) |
| Ubuntu ARM64（实验性） | [DEB](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-Linux-arm64.deb) | [AppImage](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/Codex-Mobile-Bridge-Linux-arm64.AppImage) |

[下载 SHA256SUMS.txt](https://github.com/Mhenwa/codex-mobile-bridge/releases/latest/download/SHA256SUMS.txt)。Release 同时保留带版本号的原文件和不带版本号的副本，二者内容一致；上表固定链接跟随最新正式 Release。

- Windows 安装版从快捷方式启动。便携版需要完整解压，再运行 `Codex Mobile Bridge.exe`，不要单独移动 EXE。
- macOS 打开 DMG 后，将 App 拖入“应用程序”。当前为 ad-hoc 签名，未做 Apple 公证；首次打开可能需要在“系统设置 → 隐私与安全性”允许运行。
- Linux 构建基线为 Ubuntu 22.04，支持 x64 与 ARM64，仍需确认目标机器上的 Codex App IPC 兼容性。详见 [Linux 使用说明](docs/linux.md)。
- Windows 包没有代码签名证书，系统可能显示未知发布者提示；Windows ARM64 和 32 位系统没有专用安装包。

## 快速开始：Mhenwa Connect

### 使用前准备

- 电脑已安装并打开 **Codex App**，能够正常使用聊天。
- Codex 当前生效的配置使用受支持的 Mhenwa 提供商：`https://api.mhenwa.cc` 或 `https://img.mhenwa.cc` 下的 Responses API 配置。
- 电脑、网关和手机能访问 Connect 服务。电脑主动建立出站 WSS 连接，不需要给电脑开放公网端口或配置 SSH 回程。

注册时由电脑读取所选配置的 API Key，提交给服务资格验证接口。手机不需要填写 API Key，也不需要登录同一个 OpenAI 账号。模型 API Key、电脑设备凭据和手机配对会话分别管理。

### 1. 注册电脑

1. 打开 **Codex Mobile Bridge → 网络与登录**。
2. 点击 **检测当前 Mhenwa 配置**，选择检测到的提供商。
3. 填写电脑名称，点击 **注册这台电脑**。
4. 上方显示电脑名称、设备 ID 和当前连接状态，确认注册结果。
5. 前往 **连接与状态 → 启动网关**。如果网关已经运行，先停止再启动，使新的 Connect 注册生效。

`main` 版本会用绿色框突出注册成功信息；“请启动网关”或“请重新启动网关”表示电脑已注册，但远程连接还没有就绪。

### 2. 配对手机

1. 回到 **网络与登录**，等待 Connect 状态变成 **已连接**。
2. 点击 **生成配对二维码与链接**。
3. 手机扫描二维码，或打开复制的一次性链接，填写手机名称并点击 **请求配对**。
4. 回到电脑的 **待确认配对**，核对手机后点击 **批准这台手机**。
5. 手机获得授权后，即可选择已有聊天或在已保存项目中新建聊天。

二维码和链接短时有效、只能认领一次，并且需要电脑明确批准。不要公开分享配对链接；仅打开手机入口首页不能代替电脑生成和批准配对。Connect 配对不使用个人网关的 `admin` 登录密码。

### 3. 日常使用

手机可以阅读聊天、发送消息、选择模型与 Skill、停止任务，以及回应命令或文件操作确认。电脑需要保持唤醒，Codex App 和网关需要继续运行。

Windows 关闭控制面板窗口会收进托盘，网关仍可继续运行。托盘提供 **停止网关并退出** 和 **退出控制面板（保留网关）**；退出方式不同，后台网关的状态也不同。

在 **网络与登录 → 已授权手机** 可撤销单部手机；**关闭并撤销远程连接** 会关闭本机 Connect，并尝试撤销对应电脑及关联手机。离线撤销尚未完成时界面会提示，重新注册前会重试清理。

## 手机通知

### Connect 网页系统通知（Web Push）

配对成功后，在手机网页 **设置 → 手机网页系统通知** 开启通知，允许系统权限，分别选择 **待确认** 和 **运行完成**，再发送测试通知。

Web Push 可在网页关闭后发送系统提醒。电脑网关、Codex 和中继仍需要可用；系统权限、后台限制、专注模式及浏览器推送服务的网络可达性会影响实际送达。

- Android 需要支持 Web Push 的浏览器及系统通知权限。
- iPhone / iPad 需要 iOS / iPadOS 16.4 或以上，先 **添加到主屏幕**，从主屏幕图标打开后再授权通知。参见 [WebKit 官方说明](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)。
- Web Push 仍依赖浏览器厂商的推送服务，不能保证所有国内网络都可用。请先确认测试通知实际到达，再测试锁屏和关闭网页的场景。
- 每部配对手机有独立的订阅设置。通知仅包含通用提醒与聊天链接，不携带聊天正文、标题、API Key 或设备 Token。

完整条件、服务端配置和验证范围见 [Web Push 文档](docs/web-push.md)。客户端与中继都需要对应版本；仅更新电脑 App 不会更新公网手机网页。

### Bark、ntfy 与 PushPlus

桌面 **手机通知** 页还保留 Bark、ntfy 和 PushPlus，可独立配置和发送测试通知。它们与 Connect 手机 Web Push 分别设置；开启其中一个通道不会自动为手机网页订阅 Web Push。

桌面还可配置网关启动和入口变化提醒。使用个人临时 HTTPS 入口时，可用这些通知获知重启后的新地址。

## 桌面功能与个人连接方式

| 页面 | 主要用途 |
| --- | --- |
| 连接与状态 | 启停网关、查看手机访问地址和运行状态 |
| 网络与登录 | 注册 Connect、配对与撤销手机；在高级选项中设置个人网络与登录 |
| 登录设备 | 管理个人网关的浏览器登录、IP 规则与封禁记录 |
| 手机通知 | 配置 Bark、ntfy、PushPlus 及入口变化通知 |
| 运行配置 | 数据目录、打开 App 自动启动网关、Codex 路径与 Cloudflare 组件 |
| 账号与接入 | 在电脑端管理官方账号、自定义 API 和本机接入切换 |
| 应用更新 | 查看更新状态；本次正式发行需要手动安装 |
| 运行日志 | 查看网关与各连接的日志 |

Connect 手机授权设备在 **网络与登录** 中管理，与个人网关的 **登录设备** 页分开。Connect 手机网页不提供本机账号管理与切换入口，相关操作请使用电脑端；账号切换会重启原 Codex App，详见 [账号与接入说明](docs/account-switching.md)，其中 Web 切换的说明适用于个人模式。

个人连接方式仍然保留：在 **网络与登录 → 高级选项** 中开启局域网访问、本机访问、临时 Cloudflare HTTPS、固定 Cloudflare 域名、自有服务器 SSH 或 NAS / 反向代理。

新安装默认关闭局域网和本机网页访问。只有需要个人模式时才开启相应入口、保存配置并重启网关；手机按个人入口的二维码或账号密码登录。Connect 不要求开启这些个人入口。固定域名配置见 [固定域名说明](docs/fixed-domain.md)、[SSH 服务器连接](docs/server-ssh.md)。

## 开机启动与更新

**目前没有内置系统开机或登录自启动。** “运行配置 → 打开 App 时自动启动网关”默认关闭，勾选并保存后，只在打开 App 时尝试启动网关。收进托盘继续运行不会跨系统重启生效。

当前正式 Release 没有签名的 `bridge-update.json`，因此暂不支持通过 App 内更新到这版。请从本仓库 Release 手动安装，升级前：

1. 停止网关，并从托盘退出旧 App。
2. 保留原数据目录，在原位置安装新版本，或替换完整的便携目录。
3. 启动后确认仍使用原数据目录，再启动网关；手机重新加载网页。

数据目录独立于程序目录，登录、Connect 设备凭据和通知配置保存在其中。可以在 **运行配置 → 打开目录** 找到；不要把含凭据的数据目录提交到 Git 或公开分享。

Release 发布、签名要求与恢复流程见 [桌面更新文档](docs/desktop-updates.md)。该文档描述的是完整签名更新机制；当前 fork 尚未配置对应的发布签名密钥。

## 常见问题

**检测不到 Mhenwa 配置？**

确认 Codex 当前生效的提供商、API 地址和认证配置，且对应 Key 可以被电脑上的 Bridge 读取。支持的来源和协议有限制，不能用任意 API 地址注册 Connect。

**已注册，但配对按钮不可点击？**

先在“连接与状态”启动或重新启动网关，再等待 Connect 显示“已连接”。绿色注册提示表示注册信息有效，连接状态需要单独确认。

**手机关闭网页后没有通知？**

确认手机已经配对、订阅通知并允许系统权限，先测通知是否实际到达；iPhone 需要从主屏幕 Web App 打开。电脑睡眠、网关退出、服务资格失效或推送服务不可达时，无法保证提醒。开启通知不会补发旧的已完成历史。

**切换到自己的 API 或官方账号后，Connect 还能一直用吗？**

聊天的模型配置与 Connect 服务资格是独立的。Connect 注册凭据对应的账号或 Key 被停用、过期或删除时，服务会拒绝后续接入；注册成功不代表永久授权。

**发送结果不确定，能直接再点一次吗？**

先检查聊天和运行状态。断线后的写操作不会自动重试，重复手动发送可能成为新的请求；不要把“未确认结果”当成“没有执行”。

## 源码运行与构建

建议使用 **Python 3.13** 与 **Node.js 24**，与 CI 构建环境一致。下面在项目目录执行；Windows 上的 Python 命令也可以换成已激活虚拟环境的 `python`。

```sh
python -m pip install -r requirements-desktop.txt
npm ci
npm run desktop
```

`requirements-desktop.txt` 已包含电脑 Connect 的依赖。中继部署或完整后端测试还需要：

```sh
python -m pip install -r requirements-relay.txt
```

在目标系统构建安装包：

```sh
python scripts/build-desktop.py

# Windows x64
npm run build:windows

# macOS：选择与构建机器对应的架构
npm run build:mac -- --arm64
# 或 npm run build:mac -- --x64

# Linux：选择目标架构
npm run build:linux -- --x64
# 或 npm run build:linux -- --arm64
```

产物位于 `dist/desktop/`。冻结网关必须与桌面包的操作系统、架构和版本一致；跨平台包使用对应的 CI runner 构建。

常用验证命令：

```sh
python -B -m unittest discover -s tests -v
npm run test:desktop
npm run test:connect
npm run test:updater
```

开发构建见 [Desktop builds](https://github.com/Mhenwa/codex-mobile-bridge/actions/workflows/desktop.yml)。正式发布使用 [Publish verified desktop build](https://github.com/Mhenwa/codex-mobile-bridge/actions/workflows/publish-build.yml)，手动选择 `main` 上已成功且五个平台完整的构建运行 ID；先上传和校验草稿，再发布并生成固定下载文件名。

## 部署与技术资料

- [Connect 中继与资格服务部署](deploy/connect/README.md)
- [Connect 协议与权限边界](docs/mhenwa-connect-contract.md)
- [Connect 验证记录](docs/mhenwa-connect-verification.md)
- [Web Push 配置与验证](docs/web-push.md)
- [个人网关实现说明](ARCHITECTURE.md)
- [桌面与 IPC 验证范围](VERIFICATION.md)
- [保留的原 README](README_tryl2love.md)

部署文档包含早期内测记录；安装包版本和当前发行状态以本 README 与 Release 页面为准。更新服务器前，先确认参与连接的电脑客户端版本兼容；源码提交或客户端更新不会自动部署中继。

Connect 使用 HTTPS / WSS 传输加密，**尚未提供端到端加密**，中继运营者技术上可以读取经过的聊天内容。中继不持久化聊天正文或模型 API Key，但持有设备、手机授权及推送订阅元数据。具体权限和数据边界见上述技术文档。

## 致谢与许可

感谢 [try2love](https://github.com/try2love) 的原项目及贡献者。本仓库基于其 Codex Mobile Bridge 持续开发，保留原始许可和版权声明，采用 [MIT License](LICENSE)。

这是社区项目，与 OpenAI 无隶属关系。项目依赖 Codex App 内部 IPC，Codex 更新后可能需要适配；安装包构建通过不代表所有 Codex 版本和实体设备场景均已验证。
