# Mhenwa Codex Connect implementation plan

## Objective
Build the user-authorized three-credential multi-tenant remote connection product in the Mhenwa fork of codex-mobile-bridge. Preserve local Codex execution. Provide automatic opt-in discovery of the user's Mhenwa model API key, service-eligibility registration, independent device credentials, independent phone authorization through short-lived one-use pairing with desktop confirmation, and an outbound authenticated WSS connection to a shared relay. Never use the model API key as a remote-control credential.

## Confirmed deployment context
- Fork: https://github.com/Mhenwa/codex-mobile-bridge
- New API: srv-new-api-164 (47.253.241.164); observed v1.0.0-rc.24.
- Relay candidate: mhenwa-draw-seoul (43.108.38.169), codex.mhenwa.cc.
- Existing codex.mhenwa.cc proxy: /etc/nginx/conf.d/codex.mhenwa.cc.conf -> 127.0.0.1:18787. Preserve totp/draw sites.
- Existing single-user transaction roles: /root/codex-mobile-bridge-codex-nginx/{MODIFIED_FILE,DIFF_FILE,VERIFICATION.txt,ROLLBACK.sh}.
- Test API key: provided privately in the goal; MUST NOT be persisted in source, plan, output, test fixtures, URLs or logs.

## Requirements
1. Preserve the current local single-user gateway and desktop Codex owner/IPC execution.
2. Separate API-key service eligibility, device authentication and phone sessions.
3. Relay identity derives from trusted backend user/token IDs, never caller-provided tenant IDs.
4. Device-local approval is required for new phone authorization. Keys alone never expose/control registered computers.
5. Multiplex explicit allowlisted gateway operations; do not expose arbitrary TCP/HTTP targets or raw desktop IPC.
6. Enforce tenant/device/session isolation, Origin/CSRF, revocation, rate/message/queue limits, offline status, reconnect and uncertain-write deduplication.
7. Opt-in credential discovery for approved Mhenwa providers only. Never upload unrelated providers or overwrite Codex authentication.
8. Do not persist plaintext model keys on relay. Eligibility includes expiry/status/account-disabled rules. Token-usage success alone is insufficient.
9. Durable documented deployment and recovery; preserve existing services and secrets.
10. Tests must cover real registration/pairing/relay flows and cross-tenant, replay, revoked/expired and reconnect failure cases, plus regression suite.

## Work packages
A. Inspect authoritative current state, bind repository, reuse unfinished work; establish fork remote without discarding uncommitted changes.
B. Implement eligibility adapter with version-aware New API integration and no paid model requests.
C. Implement relay registry/auth/pairing/phone sessions/revocation and bounded outbound WebSocket protocol.
D. Add local companion connection and approved operation dispatcher, preserving existing submission identities.
E. Integrate desktop opt-in/discovery/pairing and mobile device selection/remote transport.
F. Run isolated integration/regression/security tests; test provided key privately.
G. Deploy staging on authorized servers, verify TLS/online/phone authorization and unchanged existing sites; provide install/deployment documentation.

## Current checkpoint
- ACTIVE_OBJECT: E:\codex-mobile-bridge (Mhenwa Codex Connect fork development)
- LAST_CONFIRMED_RESULT: three-credential implementation deployed; final 481-test regression and latest 72 Connect tests passed; public synthetic/read-only flows and separately user-authorized real desktop-owner single-turn CONNECT_OK passed; refreshed Windows portable beta verified.
- NEXT_EXECUTABLE_ACTION: refresh final source verification and publish verified fork branch; exact latest execution state is recorded in .local/connect-checkpoint.json.
- INPUT_PATHS: connect/, bridge/connect.py, tests/test_connect*.py, docs/mhenwa-connect-verification.md, .local/connect-verification/real-owner-relay-smoke.py
- ACCEPTANCE_EVENT: verified multi-tenant registration + independently authenticated device connection + desktop-approved phone pairing + permitted mobile operations reaching the original desktop owner, with isolation/revocation/reconnect tests and deployment evidence.

## Progress ledger
- Initial goal turn: plan created. No completion claimed.
- 2026-10-05 continuation: branch codex/mhenwa-connect, origin=Mhenwa fork, upstream retained. Qualification/relay/companion/UI implemented with three independent credentials, opt-in discovery and local phone approval.
- Regression: initial 480 Python tests OK (5 skipped); final 481 tests OK (5 skipped); 131 existing desktop JS, final 15 Connect JS, 15 updater JS passed; latest 72 Connect Python tests passed. Real model smoke separately user-authorized: one temporary thread, one real model start, CONNECT_OK completed, no tools; inactive stop400, not active interrupt.
- New API node: qualification adapter deployed loopback18788, readonly SQLite lookup; Nginx copied baseline/modified/rollback exit0, restored hash matched; unauthorized HTTPS RPC401, existing status200.
- Relay node: copied Nginx baseline/modified/rollback exit0 and restored hash matched; public root changed502->200, unauthorized sessions401. New loopback18790 avoids observed18787 bind conflict. TOTP/draw200 and original conf hashes unchanged. Production staging limited8online and2MiB frames.
- Real test Key: hidden getpass input, public registration/independent device WSS/desktop-approved synthetic phone/request forwarding/revocation passed, temporary devices revoked. First smoke script expected200 rather than actual201; error retained and corrected.
- Real local IPC: readonly initialize and existing owner-discovery passed (77 metadata threads), no follow/history/send/reconnect/activation or model requests.
- Real owner relay acceptance: public registration/WSS/approved synthetic phone -> original GatewayServer/Bridge/SessionStore GET sessions200 and77metadata -> native initialize/owner-discovery success. Guarded readonly; no real task sent, no model auth/config read, no SSH enumeration; own phone/device revoked, own resources closed; original source hashes unchanged. Exit0, passed:true.
- Windows: isolated dependencies and actual frozen gateway/--dir desktop build passed. Complete portable ZIP independently verified321files+CRC+selected hashes, 175043840bytes, SHA256 432251670bc3c97670e676f6a6503671534ab2a12aaafc9648aef70e95632a3c. Unsigned beta, not official release.
- Preserve original remote four-role records; supplementary eligibility/relay and source transactions extend them. Source BASELINE/MODIFIED/ROLLBACK are 2/0/2, actual portable rollback exit0, original SHA256 restored exactly; diff reconstructs exact modified bytes. Initial shell lookup/unsupported-resume errors retained; D-drive Git discovery and UTF-8 capture corrected. Final review and source publication remain next.
- Real-write acceptance: one user-authorized new temporary thread created via public relay, followed by original GUI owner; one real send returned CONNECT_OK completed, tool0, existing threads untouched, own phone/device revoked, fresh runtime closed. Temporary chat retained. Inactive stop400; no claim of active interrupt.
- Public-release work (physical phone/UI/IPC matrix, active interrupt validation, load/chunking, self-service/quota and own release signing) remains explicitly outside a production-readiness claim.
