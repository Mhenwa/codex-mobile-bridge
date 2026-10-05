# Connect implementation contract (v1)

This contract binds the parallel implementation. Tests and deployment evidence must be appended to the plan, not treated as a replacement goal. Model credentials must never be printed or committed.

## Components
- `connect/relay.py`, `connect/registry.py`, `connect/protocol.py`: aiohttp central relay, SQLite metadata; fixed public origin; loopback HTTP only for tests/deployment behind TLS.
- `connect/eligibility.py`: trusted New API qualification adapter, also aiohttp service entry point. Validates keys without inference, returns stable backend user/token IDs and eligibility; never trusts submitted user IDs.
- `bridge/connect.py`: optional local connector embedded in `run.py`, preserving existing standalone behavior. Uses fixed loopback `GatewayServer` with an internal authenticated session; no raw IPC or arbitrary HTTP targets.
- Desktop UI exposes local-only `connect` action through `desktop.py` and Electron IPC. Mobile landing/assets are in `connect/web/`; approved users reuse the existing `web/` app.

## Credentials
API key: registration only; no key in paths, logs, metadata DB or phone session. Device token: independent random secret, persisted privately only on local computer; relay stores digest. Phone cookie: independent random secret minted ONLY after local desktop approves a one-use claim; relay stores digest and CSRF.

## Relay HTTP
- `POST /connect/register`: `{apiKey, deviceName}` -> `{deviceId, deviceToken}`. Qualification provider is fixed server configuration, not chosen by request. Registration grants no access to existing devices.
- `GET /connect/device/ws`: Authorization Bearer deviceToken, outbound device WebSocket.
- `POST /connect/device/pair`: device auth -> `{pairingId, url, expires}`; fragment `#connect_pair=<one-use-secret>`.
- `GET /connect/device/pairings`: device auth -> `{pairings:[{id,phoneName,state,expires}]}`.
- `POST /connect/device/pairings/{id}/approve`: device auth `{approved:true|false}`.
- `GET /connect/device/phones`: device auth -> `{phones:[{id,name,expires}]}`.
- `POST /connect/device/phones/{id}/revoke`: device auth.
- `POST /connect/device/revoke`: device auth; stops remote operations and associated phone sessions.
- `POST /connect/pair/claim`: exact browser Origin, `{token,phoneName}` -> `{claimToken}`. No phone authorization yet.
- `POST /connect/pair/status`: exact browser Origin, `{claimToken}` -> `{state}`; approved consumes claim and sets independent HttpOnly SameSite Strict phone cookie and returns `{state:'approved',csrf}`.
- `GET /connect/me`: cookie -> `{authenticated,deviceId,deviceName,online,csrf}`.
- `POST /connect/logout`: cookie + Origin + X-CSRF-Token; revoke phone session.
- `/api/auth`: compatibility with original UI, authenticated phone and `transport:'poll'`; never forwarded.
- `/api/logout`: same as connect logout. `/api/login` and `/api/pair` denied (no shared model key login).
- Other explicit `/api/...` allowlisted routes forwarded to selected device from PHONE SESSION, never a caller device ID.
- Root shows pairing landing unless authorized; authorized root serves original `web/index.html`. Static paths use original web STATIC mapping, including fonts. No directory traversal.

## Wire protocol
JSON request `{type:'request',id:<random>,method:'GET'|'POST',path:<allowlisted-relative-api-path>,body:<base64>,contentType:<allowed>}`.
JSON response `{type:'response',id,status,body:<base64>,contentType}`. Strip credentials and Set-Cookie; never forward cookies from local server to mobile.
`connect/protocol.py` exports `MAX_BODY` (20MiB), `MAX_FRAME` (base64 body plus bounded JSON), `validate_request(method,path,body_size,content_type)`, raising ValueError for denied actions/unsafe paths.
Original `/api/sessions`/projects/activity/thread history/poll/timeline/send/stop/respond/reconnect/settings/queue/message-action/rename/goal/files/uploads permitted with exact shapes. SSE `events` denied, relay advertises poll. No management/credential/reset/notification-provider/account-switch endpoints. Per-thread notifications may be permitted but only original current-thread policy. Existing submission IDs are preserved; relay never automatically retries writes after disconnect/timeout.
Both relay AND local connector enforce allowlist and size. A disconnected/timeout write returns an explicit unknown outcome, not an automatic replay. Bounded in-flight requests, heartbeat and capped reconnect backoff.

## Connector/controller actions
`Desktop.connect(value)` is LOCAL ONLY. Supported actions: `status`, `discover`, `register` (explicit consent + provider choice; calls eligibility registration), `pair`, `pairings`, `approve`, `phones`, `revoke-phone`, `disable`.
Store private `connect.json` (enabled,relayUrl,deviceId,deviceToken) and nonsecret `connect-status.json`; connector starts only when enabled. Defaults relay `https://codex.mhenwa.cc`, allowed providers `https://api.mhenwa.cc`, `https://img.mhenwa.cc`; verify actual effective configuration. Python 3.9 TOML parsing requires guarded tomli fallback.

## Qualification contract
Relay accepts an injected callable for tests; production `EligibilityClient` sends API key over fixed HTTPS or explicitly configured loopback endpoint to adapter. Adapter own RPC authenticated by deployment secret; never caller-defined endpoint. Response contains only `{eligible,user_id,token_id,reason?}`. Strict checks include key status,expiry and account status; remote access eligibility is separate from model quota. Backend owner/token introspection must be read-only, narrowly scoped and require no root PAT in the public relay. Adapter may run alongside New API using a read-only DB identity if no supported narrow API exists; never mutate New API schema or tokens.

## Ownership for parallel work
- Relay agent owns `connect/relay.py`, `connect/registry.py`, `connect/protocol.py`, relay tests only.
- Connector agent owns `bridge/connect.py`, `run.py`, desktop controller/UI/preload/main and connector tests only.
- Eligibility agent owns `connect/eligibility.py`, eligibility tests only; discovery helpers can be in `connect/discovery.py` by coordination with connector agent.
- Root owns plan, integration/mobile assets/deployment/build dependency wiring and aggregate verification.
