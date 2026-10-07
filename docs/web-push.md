# Connect 网页系统通知

Connect 手机网页可以订阅 Web Push，在关闭网页、切换到其他 App 或锁屏后接收“待确认”和“运行完成”提醒。不需要安装 Bark 或 ntfy。通知由电脑网关产生，中继通过浏览器的推送服务投递；网页连接不需要保持打开。

这项功能同时需要新版电脑客户端和新版 Connect 中继。只替换 Windows App 不会更新公网手机网页。

本分支已合入上游 v1.4.0。部署该版本时，先升级所有需要使用 Connect 的电脑客户端，再更新中继；v1.4 网页的模型与 Skill 列表使用新的查询参数，旧版电脑连接器会拒绝它们。源码合并本身不会更新已安装客户端或线上中继。

## 手机开启

1. 用电脑批准配对后的手机打开 Connect 网页。
2. 进入右上角“设置”，找到“手机网页系统通知”，点击开启并允许通知。
3. 分别选择“待确认”和“运行完成”。这些选择只影响当前配对手机，不修改其他手机或原 Bark、ntfy、PushPlus 的设置。
4. 点击测试通知，确认手机实际收到，再关闭网页或锁屏测试。

Android 需要支持 Web Push 的浏览器和系统通知权限。iPhone/iPad 需要 iOS/iPadOS 16.4 或以上；先在浏览器分享菜单中“添加到主屏幕”，从这个图标打开，再授权通知。仅在普通 iPhone 浏览器标签页里打开不能完成订阅。系统专注模式、浏览器通知权限、后台限制和推送服务网络可达性都会影响实际提示。

电脑网关、Codex 和 Connect 服务仍需保持可用。网页授权被撤销、手机登出、电脑注册被撤销或服务资格失效时，中继清理订阅及待发通知。已被浏览器推送服务接受的消息无法追回。

## 中继升级

Docker 构建使用 `requirements-relay.txt`，其中包含 `pywebpush`。电脑连接器依然只使用 `requirements-connect.txt`，不需要浏览器推送的加密依赖。

将更新后的源代码放到现有中继的项目目录，继续使用原 `CONNECT_DATA`、`CONNECT_RPC_SECRET_FILE` 和其他部署配置，执行：

```sh
docker compose -p mhenwa-connect-relay -f deploy/connect/relay.compose.yml build relay
docker compose -p mhenwa-connect-relay -f deploy/connect/relay.compose.yml up -d --no-deps relay
```

已有 Nginx 路由覆盖 `/connect/`，无需额外开放端口。维持 HTTPS、原 Host 透传和 WebSocket 支持，不缓存配对、API 或 Service Worker 响应。推送还需要中继能够通过 HTTPS 访问受支持的浏览器推送服务。

首次启动在持久数据目录生成 `vapid-private.pem`。保留并备份这份私钥和 `connect.sqlite3`，私钥不要放到源码、安装包或公网。后续启动复用同一公钥；已有私钥损坏时关闭 Web Push，不会自动生成替代钥匙导致现有订阅全部失效。需要轮换密钥时，应明确安排所有手机重新订阅。

推送服务收到的是通用提示和当前服务内的聊天链接，不含会话标题、聊天正文、命令、模型 API Key 或设备 Token。推送订阅地址和订阅加密信息保存在中继私有数据库，不返回给其他手机。

## 开发与验证

```sh
python -m pip install -r requirements-relay.txt
npm ci
npm run test:web-push
python -B -m unittest discover -s tests -v
npm run test:connect
npm run test:web-push-browser
```

浏览器自测默认使用本机 Chrome 和 `.tmp/build-venv/Scripts/python.exe`，可以通过 `CMB_SMOKE_CHROME` 和 `CMB_SMOKE_PYTHON` 指定其他路径。它会创建独立浏览器资料目录和 loopback 中继，验证真实 Service Worker 注册、通知设置以及关闭聊天页面后停止的 Worker 被 push 事件唤醒。订阅和推送服务发送使用合成数据，消息通过 Chrome DevTools Protocol 注入；没有经过真实 FCM/APNs，也没有验证实体手机的系统通知。

Windows 打包后还可以运行 `python scripts/smoke-web-push-frozen.py dist/gateway/codex-mobile-gateway.exe`。该检查使用隔离的 Codex IPC、合成数据库和仅连接本机的 HTTPS 代理，在没有浏览器且其他通知渠道关闭的情况下，验证冻结 EXE 产生审批及完成事件。它不会访问真实中继或调用模型。

后台只推送实时观察到的新完成事件，首次发现聊天时的已完成历史会成为基线。全新聊天如果在后台首次发现之前就已完成，可能没有完成提醒；这项行为避免重启或开启通知后重新发送旧任务。

后端验证使用合成已批准手机、Mock 推送服务和临时数据库；浏览器验证使用独立测试资料目录。自动检查不能代替 iPhone/Android 真机在各自推送网络下的送达测试。“已加入推送队列”或“推送服务已接受”不代表手机已经展示，最终以测试通知实际到达为准。

标准参考：[Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)、[WebKit 的 iOS 主屏幕推送说明](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)。
