"""Real loopback HTTP/WS acceptance tests; all credentials are generated fixtures."""
import asyncio
import base64
import json
import secrets
import tempfile
import unittest
from pathlib import Path

from aiohttp import DummyCookieJar, WSServerHandshakeError, WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from connect.protocol import MAX_BODY, safe_content_type, validate_request
from connect.registry import Registry
from connect.relay import COOKIE, STATE, create_app

THREAD = "12345678-1234-1234-1234-123456789abc"


class ProtocolTests(unittest.TestCase):
    def test_normal_operations_and_binary_upload(self):
        for method, path, size, mime in [
            ("GET", "/api/sessions?q=a%20b&offset=0", 0, "application/json"),
            ("POST", "/api/activity", 2, "application/json"),
            ("POST", "/api/sessions", 2, "application/json"),
            ("POST", f"/api/sessions/{THREAD}/send?host=local", 2, "application/json"),
            ("GET", f"/api/sessions/{THREAD}/poll?after=4", 0, "application/json"),
            ("POST", f"/api/sessions/{THREAD}/goal/status", 2, "application/json"),
            ("POST", f"/api/sessions/{THREAD}/uploads?id={THREAD}&name=a.txt", MAX_BODY, "application/octet-stream"),
        ]:
            self.assertTrue(validate_request(method, path, size, mime))

    def test_denied_routes_framing_and_escape(self):
        paths = ["/api/login", "/api/pair", "/api/auth", "/api/accounts", "/api/account",
                 "/api/accounts/switch", "/api/notifications/defaults", "/api/notifications/pushplus",
                 f"/api/sessions/{THREAD}/events", "/api/../private", "https://evil/api/sessions",
                 "//evil/api/sessions", "/api/%73essions", "/api/sessions?deviceId=other",
                 "/api/sessions?offset=0&offset=1", "/api/projects?url=http://private"]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_request("GET", path, 0, "application/json")
        for method, path, size, mime in [("DELETE", "/api/sessions", 0, "application/json"),
                ("GET", "/api/sessions", 1, "application/json"),
                ("POST", "/api/sessions", MAX_BODY + 1, "application/json"),
                ("POST", "/api/sessions", 1, "text/plain"),
                ("GET", "/api/sessions", 0, "text/plain\r\nX: injected")]:
            with self.assertRaises(ValueError):
                validate_request(method, path, size, mime)

    def test_response_mime_cannot_inject_headers_or_html(self):
        for value in ["text/html", "image/svg+xml", "x\r\nSet-Cookie: bad", None]:
            self.assertEqual(safe_content_type(value), "application/octet-stream")
        self.assertEqual(safe_content_type("application/json; charset=utf-8"), "application/json; charset=utf-8")


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.keys = {"sk-fixture-owner-one": (1, 10), "sk-fixture-owner-two": (2, 20)}
        self.eligible = True
        self.service_available = True

        async def eligibility(key):
            pair = self.keys.get(key)
            return {"eligible": bool(pair), "user_id": pair[0] if pair else 0, "token_id": pair[1] if pair else 0}

        async def introspect(owner, token):
            self.assertIsInstance(owner, int)
            self.assertIsInstance(token, int)
            return {"eligible": self.eligible and self.service_available, "user_id": owner, "token_id": token,
                    "reason": "ok" if self.service_available else "unavailable"}

        self.app = create_app("http://localhost", self.directory.name, eligibility,
            eligibility_introspect=introspect, qualification_cache_ttl=0,
            monitor_interval=0.05, request_timeout=0.2, max_inflight_per_phone=2)
        self.state = self.app[STATE]
        self.client = TestClient(TestServer(self.app), headers={"Host": "localhost"}, cookie_jar=DummyCookieJar())
        await self.client.start_server()
        self.sockets = []

    async def asyncTearDown(self):
        for socket in self.sockets:
            await socket.close()
        await self.client.close()
        self.directory.cleanup()

    async def register(self, key="sk-fixture-owner-one", name="Desktop"):
        response = await self.client.post("/connect/register", json={"apiKey": key, "deviceName": name})
        self.assertEqual(response.status, 201, await response.text())
        return await response.json()

    def device_headers(self, device):
        return {"Authorization": "Bearer " + device["deviceToken"]}

    def phone_headers(self, phone, write=False):
        headers = {"Cookie": COOKIE + "=" + phone["cookie"]}
        if write:
            headers.update({"Origin": "http://localhost", "X-CSRF-Token": phone["csrf"]})
        return headers

    async def pair(self, device, name="Phone"):
        headers = self.device_headers(device)
        response = await self.client.post("/connect/device/pair", json={}, headers=headers)
        self.assertEqual(response.status, 200, await response.text())
        pairing = await response.json()
        token = pairing["url"].split("#connect_pair=")[1]
        response = await self.client.post("/connect/pair/claim", json={"token": token, "phoneName": name}, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status, 200)
        claim = await response.json()
        response = await self.client.post("/connect/device/pairings/" + pairing["pairingId"] + "/approve", json={"approved": True}, headers=headers)
        self.assertEqual(response.status, 200)
        response = await self.client.post("/connect/pair/status", json=claim, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status, 200)
        result = await response.json()
        cookie = response.cookies[COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        return {"cookie": cookie.value, "csrf": result["csrf"], "claim": claim, "token": token}

    async def websocket(self, device):
        socket = await self.client.ws_connect("/connect/device/ws", headers=self.device_headers(device))
        self.sockets.append(socket)
        return socket

    async def response(self, socket, frame, data, status=200, **extra):
        await socket.send_json({"type": "response", "id": frame["id"], "status": status,
            "body": base64.b64encode(json.dumps(data).encode()).decode(), "contentType": "application/json", **extra})

    async def test_three_credentials_and_no_model_key_storage(self):
        device = await self.register()
        phone = await self.pair(device)
        self.assertNotIn(device["deviceToken"], {phone["cookie"], "sk-fixture-owner-one"})
        self.assertNotEqual(phone["cookie"], "sk-fixture-owner-one")
        dumped = "\n".join(self.state.registry.db.iterdump())
        for secret in ["sk-fixture-owner-one", device["deviceToken"], phone["cookie"], phone["token"], phone["claim"]["claimToken"]]:
            self.assertNotIn(secret, dumped)
        response = await self.client.get("/api/auth", headers=self.phone_headers(phone))
        body = await response.json()
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["transport"], "poll")
        self.assertEqual(body["deviceId"], device["deviceId"])
        response = await self.client.get("/connect/me", headers={"Authorization": "Bearer sk-fixture-owner-one", "Cookie": ""})
        self.assertFalse((await response.json())["authenticated"])

    async def test_claim_one_use_approval_required_consumption_and_cross_device(self):
        device, other = await self.register(), await self.register("sk-fixture-owner-two")
        response = await self.client.post("/connect/device/pair", json={}, headers=self.device_headers(device))
        pairing = await response.json()
        token = pairing["url"].split("#connect_pair=")[1]
        for index in range(2):
            response = await self.client.post("/connect/pair/claim", json={"token": token, "phoneName": "P"}, headers={"Origin": "http://localhost"})
            self.assertEqual(response.status, 200 if index == 0 else 403)
            if index == 0:
                claim = await response.json()
        response = await self.client.post("/connect/pair/status", json=claim, headers={"Origin": "http://localhost"})
        self.assertEqual((await response.json())["state"], "claimed")
        self.assertNotIn(COOKIE, response.cookies)
        response = await self.client.post(f"/connect/device/pairings/{pairing['pairingId']}/approve", json={"approved": True}, headers=self.device_headers(other))
        self.assertEqual(response.status, 403)
        response = await self.client.post(f"/connect/device/pairings/{pairing['pairingId']}/approve", json={"approved": True}, headers=self.device_headers(device))
        self.assertEqual(response.status, 200)
        response = await self.client.post("/connect/pair/status", json=claim, headers={"Origin": "http://localhost"})
        self.assertIn(COOKIE, response.cookies)
        response = await self.client.post("/connect/pair/status", json=claim, headers={"Origin": "http://localhost"})
        self.assertEqual((await response.json())["state"], "consumed")
        self.assertNotIn(COOKIE, response.cookies)

    async def test_pair_expiry_and_denial(self):
        device = await self.register()
        response = await self.client.post("/connect/device/pair", json={}, headers=self.device_headers(device))
        pairing = await response.json()
        self.state.registry.db.execute("UPDATE pairings SET expires=0 WHERE id=?", (pairing["pairingId"],))
        self.state.registry.db.commit()
        response = await self.client.post("/connect/pair/claim", json={"token": pairing["url"].split("#connect_pair=")[1], "phoneName": "P"}, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status, 403)
        response = await self.client.post("/connect/device/pair", json={}, headers=self.device_headers(device))
        pairing = await response.json()
        response = await self.client.post("/connect/pair/claim", json={"token": pairing["url"].split("#connect_pair=")[1], "phoneName": "P"}, headers={"Origin": "http://localhost"})
        claim = await response.json()
        response = await self.client.post(f"/connect/device/pairings/{pairing['pairingId']}/approve", json={"approved": False}, headers=self.device_headers(device))
        self.assertEqual(response.status, 200)
        response = await self.client.post("/connect/pair/status", json=claim, headers={"Origin": "http://localhost"})
        self.assertEqual((await response.json())["state"], "denied")
        self.assertNotIn(COOKIE, response.cookies)

    async def test_origin_host_csrf_and_registration_fields(self):
        response = await self.client.post("/connect/register", json={"apiKey": "sk-fixture-owner-one", "deviceName": "D", "user_id": 999})
        self.assertEqual(response.status, 400)
        response = await self.client.get("/health", headers={"Host": "evil.example"})
        self.assertEqual(response.status, 403)
        device = await self.register()
        phone = await self.pair(device)
        for headers in [self.phone_headers(phone), {**self.phone_headers(phone, True), "Origin": "https://evil.example"},
                        {**self.phone_headers(phone, True), "X-CSRF-Token": "wrong"}]:
            response = await self.client.post(f"/api/sessions/{THREAD}/stop", json={}, headers=headers)
            self.assertEqual(response.status, 403)
        response = await self.client.post("/connect/pair/claim", json={"token": "invalid-token-fixture", "phoneName": "P"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)

    async def test_two_tenants_requests_cannot_select_another_device(self):
        first, second = await self.register(), await self.register("sk-fixture-owner-two")
        phone1, phone2 = await self.pair(first), await self.pair(second)
        socket1, socket2 = await self.websocket(first), await self.websocket(second)
        request1 = asyncio.create_task(self.client.get("/api/sessions", headers=self.phone_headers(phone1)))
        frame1 = await socket1.receive_json()
        request2 = asyncio.create_task(self.client.get("/api/sessions", headers=self.phone_headers(phone2)))
        frame2 = await socket2.receive_json()
        await self.response(socket1, frame1, {"sessions": ["first"]})
        await self.response(socket2, frame2, {"sessions": ["second"]})
        self.assertEqual(await (await request1).json(), {"sessions": ["first"]})
        self.assertEqual(await (await request2).json(), {"sessions": ["second"]})
        response = await self.client.get("/api/sessions?deviceId=" + second["deviceId"], headers=self.phone_headers(phone1))
        self.assertEqual(response.status, 400)
        phones = await (await self.client.get("/connect/device/phones", headers=self.device_headers(second))).json()
        response = await self.client.post(f"/connect/device/phones/{phones['phones'][0]['id']}/revoke", json={}, headers=self.device_headers(first))
        self.assertEqual(response.status, 403)

    async def test_denied_management_never_forwarded(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        for path in ["/api/accounts", "/api/account/reset", "/api/login", "/api/pair", f"/api/sessions/{THREAD}/events"]:
            response = await self.client.get(path, headers=self.phone_headers(phone))
            self.assertEqual(response.status, 400)
        self.assertEqual(len(self.state.pending), 0)
        self.assertFalse(socket.closed)

    async def test_uncertain_write_timeout_is_never_replayed(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        pending = asyncio.create_task(self.client.post(f"/api/sessions/{THREAD}/send", json={"text": "test", "id": "existing-submission-id"}, headers=self.phone_headers(phone, True)))
        frame = await socket.receive_json()
        self.assertEqual(json.loads(base64.b64decode(frame["body"]))["id"], "existing-submission-id")
        result = await pending
        self.assertEqual(result.status, 504)
        body = await result.json()
        self.assertTrue(body["outcomeUnknown"])
        self.assertFalse(body["retryable"])
        self.assertEqual(self.state.pending, {})
        # No second frame was emitted, including after reconnect.
        await socket.close()
        await self.websocket(device)
        self.assertEqual(self.state.pending, {})

    async def test_phone_revoke_cancels_pending_and_authorization(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        pending = asyncio.create_task(self.client.get(f"/api/sessions/{THREAD}/poll", headers=self.phone_headers(phone)))
        await socket.receive_json()
        phones = await (await self.client.get("/connect/device/phones", headers=self.device_headers(device))).json()
        response = await self.client.post(f"/connect/device/phones/{phones['phones'][0]['id']}/revoke", json={}, headers=self.device_headers(device))
        self.assertEqual(response.status, 200)
        self.assertIn((await pending).status, {503, 504})
        response = await self.client.get("/api/sessions", headers=self.phone_headers(phone))
        self.assertEqual(response.status, 401)

    async def test_phone_expiry_cancels_long_poll(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        pending = asyncio.create_task(self.client.get(f"/api/sessions/{THREAD}/poll", headers=self.phone_headers(phone)))
        await socket.receive_json()
        self.state.registry.db.execute("UPDATE phones SET expires=0")
        self.state.registry.db.commit()
        self.assertIn((await pending).status, {503, 504})
        response = await self.client.get("/api/sessions", headers=self.phone_headers(phone))
        self.assertEqual(response.status, 401)

    async def test_wrong_device_cannot_resolve_other_tenant_request(self):
        first, second = await self.register(), await self.register("sk-fixture-owner-two")
        phone = await self.pair(first)
        socket1, socket2 = await self.websocket(first), await self.websocket(second)
        pending = asyncio.create_task(self.client.get("/api/sessions", headers=self.phone_headers(phone)))
        frame = await socket1.receive_json()
        await self.response(socket2, frame, {"sessions": ["attacker"]})
        message = await asyncio.wait_for(socket2.receive(), timeout=2)
        self.assertIn(message.type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
        await self.response(socket1, frame, {"sessions": ["correct"]})
        self.assertEqual(await (await pending).json(), {"sessions": ["correct"]})

    async def test_revoke_during_qualification_await_stops_forward(self):
        device = await self.register()
        phone = await self.pair(device)
        await self.websocket(device)
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed_qualification(owner, token):
            started.set()
            await release.wait()
            return {"eligible": True, "user_id": owner, "token_id": token}

        self.state.introspect = delayed_qualification
        pending = asyncio.create_task(self.client.post(f"/api/sessions/{THREAD}/send", json={"text": "must not execute", "id": "stable"}, headers=self.phone_headers(phone, True)))
        await started.wait()
        phones = self.state.registry.phones(device["deviceId"])
        self.state.registry.revoke_phone(device["deviceId"], phones[0]["id"])
        release.set()
        self.assertEqual((await pending).status, 401)
        self.assertEqual(self.state.pending, {})

    async def test_response_headers_and_html_are_sanitized(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        pending = asyncio.create_task(self.client.get("/api/sessions", headers=self.phone_headers(phone)))
        frame = await socket.receive_json()
        await socket.send_json({"type": "response", "id": frame["id"], "status": 200,
            "body": base64.b64encode(b"<script>evil</script>").decode(), "contentType": "text/html",
            "contentDisposition": "attachment; filename=test.txt\r\nSet-Cookie: bad=1"})
        response = await pending
        self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertNotIn("Content-Disposition", response.headers)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    async def test_aggregate_request_and_response_buffers_are_bounded(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        self.state.max_buffered = 2
        first = asyncio.create_task(self.client.post(f"/api/sessions/{THREAD}/stop", data=b"{}",
            headers={**self.phone_headers(phone, True), "Content-Type": "application/json"}))
        frame = await socket.receive_json()
        self.assertEqual(self.state.request_bytes, 2)
        response = await self.client.post(f"/api/sessions/{THREAD}/stop", data=b"{}",
            headers={**self.phone_headers(phone, True), "Content-Type": "application/json"})
        self.assertEqual(response.status, 429)
        await socket.send_json({"type": "response", "id": frame["id"], "status": 200,
            "body": base64.b64encode(b"{}").decode(), "contentType": "application/json"})
        self.assertEqual((await first).status, 200)
        self.assertEqual((self.state.request_bytes, self.state.response_bytes), (0, 0))
        second = asyncio.create_task(self.client.get("/api/sessions", headers=self.phone_headers(phone)))
        frame = await socket.receive_json()
        await self.response(socket, frame, {"far too big": "for a two-byte budget"})
        message = await socket.receive()
        self.assertIn(message.type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
        self.assertIn((await second).status, {503, 504})
        self.assertEqual((self.state.request_bytes, self.state.response_bytes, self.state.total_inflight), (0, 0, 0))

    async def test_file_download_preserves_only_safe_disposition(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        pending = asyncio.create_task(self.client.get(f"/api/sessions/{THREAD}/files/" + "a" * 64,
            headers=self.phone_headers(phone)))
        frame = await socket.receive_json()
        await socket.send_json({"type": "response", "id": frame["id"], "status": 200,
            "body": base64.b64encode(b"binary-file-content").decode(), "contentType": "application/octet-stream",
            "contentDisposition": "attachment; filename*=UTF-8''report.txt"})
        response = await pending
        self.assertEqual(await response.read(), b"binary-file-content")
        self.assertEqual(response.headers["Content-Disposition"], "attachment; filename*=UTF-8''report.txt")

    async def test_beta_online_and_wire_body_caps(self):
        first, second = await self.register(), await self.register("sk-fixture-owner-two")
        phone = await self.pair(first)
        self.state.max_online = 1
        self.state.max_body = 2
        self.state.max_frame = ((self.state.max_body + 2) // 3) * 4 + 16384
        socket = await self.websocket(first)
        with self.assertRaises(WSServerHandshakeError) as caught:
            await self.client.ws_connect("/connect/device/ws", headers=self.device_headers(second))
        self.assertEqual(caught.exception.status, 503)
        response = await self.client.post(f"/api/sessions/{THREAD}/uploads?id={THREAD}&name=test.txt",
            data=b"too-big", headers={**self.phone_headers(phone, True), "Content-Type": "application/octet-stream"})
        self.assertEqual(response.status, 413)
        pending = asyncio.create_task(self.client.get("/api/sessions", headers=self.phone_headers(phone)))
        frame = await socket.receive_json()
        await socket.send_json({"type": "response", "id": frame["id"], "status": 200,
            "body": base64.b64encode(b"larger-than-two-bytes").decode(), "contentType": "application/json"})
        self.assertIn((await socket.receive()).type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
        self.assertIn((await pending).status, {503, 504})
        self.assertEqual(self.state.response_bytes, 0)

    async def test_device_revocation_and_token_disabling_close_live_connections(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        self.eligible = False
        message = await asyncio.wait_for(socket.receive(), timeout=2)
        self.assertIn(message.type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
        response = await self.client.get("/api/sessions", headers=self.phone_headers(phone))
        self.assertEqual(response.status, 401)
        self.assertIsNone(self.state.registry.device(device["deviceToken"]))

    async def test_qualification_outage_fails_closed_without_permanent_revoke(self):
        device = await self.register()
        phone = await self.pair(device)
        self.service_available = False
        response = await self.client.get("/connect/me", headers=self.phone_headers(phone))
        self.assertEqual(response.status, 503)
        self.assertIsNotNone(self.state.registry.device(device["deviceToken"]))
        self.service_available = True
        response = await self.client.get("/connect/me", headers=self.phone_headers(phone))
        self.assertTrue((await response.json())["authenticated"])

    async def test_registration_qualification_outage_is_temporary_503(self):
        async def unavailable(key):
            return {"eligible": False, "reason": "unavailable"}

        self.state.eligibility = unavailable
        response = await self.client.post("/connect/register", json={"apiKey": "sk-fixture-owner-one", "deviceName": "Desktop"})
        self.assertEqual(response.status, 503)
        self.assertEqual(await response.json(), {"error": "qualification service unavailable"})
        self.assertEqual(self.state.registry.db.execute("SELECT count(*) FROM devices").fetchone()[0], 0)

    async def test_inflight_limit_and_logout(self):
        device = await self.register()
        phone = await self.pair(device)
        socket = await self.websocket(device)
        requests = []
        for _ in range(2):
            requests.append(asyncio.create_task(self.client.get("/api/sessions", headers=self.phone_headers(phone))))
            await socket.receive_json()
        response = await self.client.get("/api/sessions", headers=self.phone_headers(phone))
        self.assertEqual(response.status, 429)
        response = await self.client.post("/connect/logout", json={}, headers=self.phone_headers(phone, True))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.cookies[COOKIE]["max-age"], "0")
        await asyncio.gather(*requests)
        response = await self.client.get("/api/sessions", headers=self.phone_headers(phone))
        self.assertEqual(response.status, 401)

    async def test_landing_original_app_static_and_device_limit(self):
        response = await self.client.get("/")
        self.assertIn("Mhenwa Codex Connect", await response.text())
        device = await self.register()
        phone = await self.pair(device)
        response = await self.client.get("/", headers=self.phone_headers(phone))
        html = await response.text()
        self.assertIn('id="composer"', html)
        self.assertLess(html.index('/connect/platform.js'), html.index('/app.js'))
        response = await self.client.get("/connect/", headers=self.phone_headers(phone))
        self.assertIn("Mhenwa Codex Connect", await response.text())
        response = await self.client.get("/connect/pair.js")
        self.assertEqual(response.status, 200)
        for index in range(4):
            await self.register(name="D" + str(index))
        response = await self.client.post("/connect/register", json={"apiKey": "sk-fixture-owner-one", "deviceName": "too many"})
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
