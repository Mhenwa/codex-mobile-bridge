"""Web Push authorization, durable delivery and encryption; no public I/O."""
import asyncio
import base64
import hashlib
import json
import secrets
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import http_ece
from aiohttp import DummyCookieJar
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from connect.push import (EncryptedSender, PublicResolver, PushService,
                          notification_payload, validate_endpoint,
                          validate_events, validate_notification, validate_subscription)
from connect.registry import Registry
from connect.relay import COOKIE, STATE, create_app

THREAD = "12345678-1234-1234-1234-123456789abc"


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def subscription(suffix="fixture"):
    key = ec.generate_private_key(ec.SECP256R1())
    auth = secrets.token_bytes(16)
    point = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": "https://fcm.googleapis.com/fcm/send/" + suffix,
            "keys": {"p256dh": b64(point), "auth": b64(auth)}, "expirationTime": None}, key, auth


def event(value="fixture", kind="request", created=None, host="local"):
    return {"id": hashlib.sha256(value.encode()).hexdigest(), "kind": kind,
            "threadId": THREAD, "host": host, "count": 1, "createdAt": int(time.time()) if created is None else created}


class PushValidationTests(unittest.TestCase):
    def test_only_supported_https_providers_and_default_port(self):
        for endpoint in ["https://fcm.googleapis.com/a", "https://updates.push.services.mozilla.com/wpush/v2/a",
                         "https://web.push.apple.com/a", "https://wns2-db5p.notify.windows.com/w/?token=a",
                         "https://fcm.googleapis.com:443/a"]:
            self.assertEqual(validate_endpoint(endpoint), endpoint)
        for endpoint in ["http://fcm.googleapis.com/a", "https://fcm.googleapis.com:8443/a",
                         "https://fcm.googleapis.com.evil.invalid/a", "https://push.apple.com.evil.invalid/a",
                         "https://localhost/a", "https://127.0.0.1/a", "https://[::1]/a",
                         "https://username@fcm.googleapis.com/a", "https://fcm.googleapis.com/a#secret",
                         "https://fcm.googleapis.com:/a", "https://FCM.GOOGLEAPIS.COM\\@evil.invalid/a",
                         "https://fcm.googleapis.com/a\r\nX: secret", "https://fcm.googleapis.com./a",
                         "https://evil.invalid/a", "https://push.apple.com/a"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_endpoint(endpoint)

    def test_subscription_requires_valid_ec_point_auth_and_expiration(self):
        sub, _, _ = subscription()
        self.assertEqual(validate_subscription(sub, time.time()), sub)
        for changes in [{"keys": {}}, {"keys": {**sub["keys"], "auth": b64(b"short")}},
                        {"keys": {**sub["keys"], "p256dh": b64(b"\x04" + b"\0" * 64)}},
                        {"keys": {**sub["keys"], "extra": "secret"}}, {"expirationTime": True},
                        {"expirationTime": float("inf")}, {"expirationTime": 1}, {"chat": "private"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_subscription({**sub, **changes}, time.time())

    def test_strict_event_schema_does_not_accept_chat_content(self):
        item = event()
        self.assertEqual(validate_notification(item, time.time()), item)
        for changes in [{"id": "x"}, {"kind": []}, {"kind": "message"}, {"threadId": "other"},
                        {"host": "\n"}, {"host": "a" * 257}, {"host": ""}, {"host": "ssh:a~b"},
                        {"host": "local/other"}, {"host": "local#other"}, {"host": "local other"},
                        {"host": "local|other"}, {"count": True},
                        {"count": 4097}, {"createdAt": True}, {"createdAt": int(time.time()) - 86401},
                        {"createdAt": int(time.time()) + 301}, {"body": "private text"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_notification({**item, **changes}, time.time())
        with self.assertRaises(ValueError):
            validate_notification({"events": [item]}, time.time())
        for preferences in [{"requests": 1, "completion": True}, {"requests": True}, {"requests": True, "completion": False, "host": "local"}]:
            with self.assertRaises(ValueError):
                validate_events(preferences)

    def test_fixed_notification_content_and_safe_chat_locator(self):
        host = "ssh:电脑"
        payload = notification_payload(event(host=host))
        self.assertEqual(set(payload), {"title", "body", "url", "tag"})
        self.assertEqual(payload["url"], "/#" + THREAD + "~ssh%3A%E7%94%B5%E8%84%91")
        self.assertNotIn(host, payload["tag"])
        self.assertNotIn("count", payload)
        self.assertNotEqual(payload["body"], notification_payload(event(kind="completion"))["body"])


class PushNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_dns_private_loopback_link_local_and_mixed_answers_fail(self):
        resolver = PublicResolver()
        try:
            for addresses in [["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["::1"],
                              ["fc00::1"], ["fe80::1"], ["8.8.8.8", "192.168.1.1"], []]:
                rows = [{"host": address, "hostname": "fcm.googleapis.com", "port": 443, "family": socket.AF_INET, "proto": 0, "flags": 0} for address in addresses]
                with patch("aiohttp.resolver.ThreadedResolver.resolve", new=AsyncMock(return_value=rows)):
                    with self.subTest(addresses=addresses), self.assertRaises(OSError):
                        await resolver.resolve("fcm.googleapis.com", 443)
            records = [{"host": "8.8.8.8"}]
            with patch("aiohttp.resolver.ThreadedResolver.resolve", new=AsyncMock(return_value=records)):
                self.assertIs(await resolver.resolve("fcm.googleapis.com", 443), records)
        finally:
            await resolver.close()

    async def test_http_redirect_is_returned_without_following_and_proxy_env_is_disabled(self):
        sub, _, _ = subscription()
        sender = EncryptedSender(ec.generate_private_key(ec.SECP256R1()), "mailto:fixture@example.invalid")
        calls = []

        class Response:
            status = 307
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class Session:
            closed = False
            def __init__(self, **kwargs):
                calls.append(kwargs)
            def post(self, endpoint, **kwargs):
                calls.append({"endpoint": endpoint, **kwargs})
                return Response()
            async def close(self):
                self.closed = True

        with patch("connect.push.ClientSession", Session), patch("connect.push.TCPConnector") as connector:
            self.assertEqual(await sender(sub, {"title": "Test"}), 307)
            self.assertFalse(calls[0]["trust_env"])
            self.assertFalse(calls[1]["allow_redirects"])
            self.assertFalse(connector.call_args.kwargs["use_dns_cache"])
            self.assertIsInstance(connector.call_args.kwargs["resolver"], PublicResolver)
            await connector.call_args.kwargs["resolver"].close()
        await sender.close()


class PushEncryptionTests(unittest.TestCase):
    def test_payload_decrypts_with_phone_key_and_vapid_signature_verifies(self):
        sub, phone_key, auth = subscription()
        server_key = ec.generate_private_key(ec.SECP256R1())
        sender = EncryptedSender(server_key, "mailto:fixture@example.invalid")
        payload = notification_payload(event())
        endpoint, encrypted, headers = sender.encode(sub, payload)
        decrypted = http_ece.decrypt(encrypted, private_key=phone_key, auth_secret=auth, version="aes128gcm")
        self.assertEqual(json.loads(decrypted), payload)
        self.assertEqual(endpoint, sub["endpoint"])
        self.assertEqual(headers["Content-Encoding"], "aes128gcm")
        self.assertEqual(headers["TTL"], "300")
        token = headers["Authorization"].split("t=", 1)[1].split(",k=", 1)[0]
        header, body, signature = token.split(".")
        claims = json.loads(unb64(body))
        self.assertEqual(json.loads(unb64(header))["alg"], "ES256")
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
        self.assertEqual(claims["sub"], "mailto:fixture@example.invalid")
        self.assertTrue(time.time() < claims["exp"] <= time.time() + 12 * 3600)
        raw = unb64(signature)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        server_key.public_key().verify(der, (header + "." + body).encode(), ec.ECDSA(hashes.SHA256()))

    def test_vapid_is_persistent_and_corrupt_key_fails_closed_without_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = Registry(Path(folder) / "registry.sqlite3")
            path = Path(folder) / "vapid-private.pem"
            try:
                one = PushService(registry, "https://connect.example.invalid", path)
                contents = path.read_bytes()
                two = PushService(registry, "https://connect.example.invalid", path)
                self.assertTrue(one.available)
                self.assertEqual(one.public_key, two.public_key)
                self.assertEqual(contents, path.read_bytes())
                path.write_bytes(b"corrupt deployment key")
                broken = PushService(registry, "https://connect.example.invalid", path)
                self.assertFalse(broken.available)
                self.assertIsNone(broken.public_key)
                self.assertEqual(path.read_bytes(), b"corrupt deployment key")
            finally:
                registry.close()


class PushRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.sent, self.send_status, self.eligible, self.backend = [], 201, True, True

        async def eligibility(_key):
            return {"eligible": True, "user_id": 1, "token_id": 10}

        async def introspect(owner, token):
            return {"eligible": self.eligible, "user_id": owner, "token_id": token,
                    "reason": "ok" if self.backend else "unavailable"}

        async def sender(sub, payload):
            self.sent.append((sub, payload))
            return self.send_status

        self.app = create_app("http://localhost", self.directory.name, eligibility, push_sender=sender,
                              push_auto_send=False, eligibility_introspect=introspect,
                              qualification_cache_ttl=0, monitor_interval=3600,
                              vapid_subject="mailto:fixture@example.invalid")
        self.state = self.app[STATE]
        self.registry = self.state.registry
        self.client = TestClient(TestServer(self.app), headers={"Host": "localhost"}, cookie_jar=DummyCookieJar())
        await self.client.start_server()
        self.device = self.registry.register(1, 10, "Fixture Desktop")
        self.phone = self.pair(self.device)

    async def asyncTearDown(self):
        await self.client.close()
        self.directory.cleanup()

    def pair(self, device, name="Phone"):
        pairing = self.registry.pairing(device["deviceId"])
        claim = self.registry.claim(pairing["token"], name)
        self.registry.approve(device["deviceId"], pairing["pairingId"], True)
        status, cookie = self.registry.claim_status(claim["claimToken"])
        phone = self.registry.phone(cookie)
        return {"cookie": cookie, "csrf": status["csrf"], "id": phone["id"]}

    def phone_headers(self, phone=None, write=False):
        phone = phone or self.phone
        headers = {"Cookie": COOKIE + "=" + phone["cookie"]}
        if write:
            headers.update({"Origin": "http://localhost", "X-CSRF-Token": phone["csrf"]})
        return headers

    def device_headers(self, device=None):
        return {"Authorization": "Bearer " + (device or self.device)["deviceToken"]}

    async def subscribe(self, phone=None, suffix="fixture", preferences=None):
        sub, _, _ = subscription(suffix)
        response = await self.client.post("/connect/push/subscribe", headers=self.phone_headers(phone, True),
                                          json={"subscription": sub, "events": preferences or {"requests": True, "completion": True}})
        self.assertEqual(response.status, 200, await response.text())
        return sub

    async def notify(self, item=None, device=None):
        response = await self.client.post("/connect/device/notifications", headers=self.device_headers(device), json=item or event())
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def drain(self):
        await self.state.push.deliver(self.state.check_qualification)

    async def test_push_requires_approved_phone_exact_origin_and_csrf(self):
        response = await self.client.get("/connect/push")
        self.assertEqual(response.status, 401)
        response = await self.client.get("/connect/push", headers=self.device_headers())
        self.assertEqual(response.status, 401)
        response = await self.client.get("/connect/push", headers=self.phone_headers())
        body = await response.json()
        self.assertTrue(body["available"])
        self.assertFalse(body["subscribed"])
        self.assertEqual(body["events"], {"requests": True, "completion": True})
        for headers in [self.phone_headers(), {**self.phone_headers(write=True), "Origin": "https://evil.invalid"},
                        {**self.phone_headers(write=True), "X-CSRF-Token": "wrong"}]:
            response = await self.client.post("/connect/push/unsubscribe", headers=headers, json={})
            self.assertEqual(response.status, 403)
        response = await self.client.post("/connect/device/notifications", json=event(), headers=self.phone_headers(write=True))
        self.assertEqual(response.status, 401)

    async def test_device_dedup_survives_restart_and_outbox_contains_only_fixed_content(self):
        await self.subscribe()
        self.assertEqual((await self.notify())["accepted"], 1)
        self.assertEqual((await self.notify())["accepted"], 0)
        second = Registry(Path(self.directory.name) / "connect.sqlite3")
        try:
            self.assertEqual(second.enqueue_push(self.device["deviceId"], event(), notification_payload(event())), 0)
            self.assertEqual(len(second.due_push()), 1)
        finally:
            second.close()
        await self.drain()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], notification_payload(event()))
        self.assertEqual(len(self.registry.due_push()), 0)

    async def test_phone_preferences_are_independent_and_device_cannot_address_another_phone(self):
        await self.subscribe(preferences={"requests": False, "completion": True})
        phone2 = self.pair(self.device, "Phone two")
        await self.subscribe(phone2, "phone2", {"requests": True, "completion": False})
        device2 = self.registry.register(2, 20, "Other desktop")
        phone3 = self.pair(device2)
        await self.subscribe(phone3, "other-device")
        await self.notify(event("request"))
        await self.notify(event("complete", "completion"))
        await self.drain()
        self.assertEqual({(s[0]["endpoint"].rsplit("/", 1)[1], s[1]["title"]) for s in self.sent},
                         {("fixture", "Codex · 任务完成"), ("phone2", "Codex · 需要处理")})
        self.assertEqual((await self.notify(event("request"), device2))["accepted"], 1)
        await self.drain()
        self.assertEqual(self.sent[-1][0]["endpoint"].rsplit("/", 1)[1], "other-device")
        response = await self.client.post("/connect/device/notifications", json={**event(), "phoneId": phone3["id"]}, headers=self.device_headers())
        self.assertEqual(response.status, 400)

    async def test_endpoint_cannot_be_stolen_by_other_active_phone(self):
        sub = await self.subscribe()
        other = self.pair(self.device)
        response = await self.client.post("/connect/push/subscribe", headers=self.phone_headers(other, True),
                                          json={"subscription": sub, "events": {"requests": True, "completion": True}})
        self.assertEqual(response.status, 400)
        self.assertIsNotNone(self.registry.push_subscription(self.phone["id"]))
        self.assertIsNone(self.registry.push_subscription(other["id"]))

    async def test_disabling_preference_discards_queued_event_and_unsubscribe_clears_queue(self):
        await self.subscribe()
        await self.notify()
        response = await self.client.post("/connect/push/preferences", headers=self.phone_headers(write=True),
                                          json={"events": {"requests": False, "completion": True}})
        self.assertEqual(response.status, 200)
        await self.drain()
        self.assertEqual(self.sent, [])
        await self.notify(event("complete", "completion"))
        response = await self.client.post("/connect/push/unsubscribe", headers=self.phone_headers(write=True), json={})
        self.assertEqual(response.status, 200)
        self.assertFalse((await response.json())["subscribed"])
        self.assertEqual(self.registry.due_push(), [])
        self.assertFalse(self.registry.push_preferences(self.phone["id"])["requests"])

    async def test_revoke_phone_device_and_logout_delete_subscription_and_outbox_atomically(self):
        await self.subscribe()
        await self.notify()
        self.registry.revoke_phone(self.device["deviceId"], self.phone["id"])
        self.assertIsNone(self.registry.push_subscription(self.phone["id"]))
        self.assertEqual(self.registry.due_push(), [])
        self.phone = self.pair(self.device)
        await self.subscribe(suffix="logout")
        await self.notify(event("logout"))
        response = await self.client.post("/connect/logout", headers=self.phone_headers(write=True), json={})
        self.assertEqual(response.status, 200)
        self.assertIsNone(self.registry.push_subscription(self.phone["id"]))
        self.assertEqual(self.registry.due_push(), [])
        self.phone = self.pair(self.device)
        await self.subscribe(suffix="device-revoke")
        await self.notify(event("device-revoke"))
        self.registry.revoke_device(self.device["deviceId"])
        self.assertIsNone(self.registry.push_subscription(self.phone["id"]))
        self.assertEqual(self.registry.db.execute("SELECT count(*) FROM push_outbox").fetchone()[0], 0)
        self.assertEqual(self.registry.db.execute("SELECT count(*) FROM push_events").fetchone()[0], 0)

    async def test_phone_or_subscription_expiration_prevents_delivery(self):
        await self.subscribe()
        await self.notify()
        with self.registry.db:
            self.registry.db.execute("UPDATE phones SET expires=0 WHERE id=?", (self.phone["id"],))
        await self.drain()
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.registry.push_subscription(self.phone["id"]))
        self.phone = self.pair(self.device)
        await self.subscribe(suffix="expired-sub")
        await self.notify(event("expire-sub"))
        with self.registry.db:
            self.registry.db.execute("UPDATE push_subscriptions SET expires=0 WHERE phone_id=?", (self.phone["id"],))
        await self.drain()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.registry.due_push(), [])

    async def test_qualification_rechecked_before_delivery_and_outage_retries_finitely(self):
        await self.subscribe()
        await self.notify()
        self.backend = False
        await self.drain()
        self.assertEqual(self.sent, [])
        item = self.registry.db.execute("SELECT * FROM push_outbox").fetchone()
        self.assertEqual(item["attempts"], 1)
        self.backend, self.eligible = True, False
        with self.registry.db:
            self.registry.db.execute("UPDATE push_outbox SET next_attempt=0")
        await self.drain()
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.registry.get_device(self.device["deviceId"]))
        self.assertEqual(self.registry.due_push(), [])

    async def test_phone_revoked_during_qualification_is_not_sent(self):
        await self.subscribe()
        await self.notify()

        async def qualification(_device):
            self.registry.revoke_phone(self.device["deviceId"], self.phone["id"])
            await asyncio.sleep(0)

        await self.state.push.deliver(qualification)
        self.assertEqual(self.sent, [])

    async def test_resubscribe_same_endpoint_during_qualification_does_not_send_old_snapshot(self):
        sub = await self.subscribe()
        await self.notify()

        async def qualification(_device):
            replacement, _, _ = subscription()
            replacement["endpoint"] = sub["endpoint"]
            self.registry.subscribe_push(self.phone["id"], replacement, {"requests": True, "completion": True})
            await asyncio.sleep(0)

        await self.state.push.deliver(qualification)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.registry.due_push(), [])

    async def test_unsubscribe_during_qualification_does_not_send_old_snapshot(self):
        await self.subscribe()
        await self.notify()

        async def qualification(_device):
            self.registry.unsubscribe_push(self.phone["id"])
            await asyncio.sleep(0)

        await self.state.push.deliver(qualification)
        self.assertEqual(self.sent, [])

    async def test_reused_outbox_row_id_does_not_send_or_delete_new_event(self):
        sub = await self.subscribe()
        await self.notify()
        old = self.registry.due_push()[0]

        async def qualification(_device):
            self.registry.unsubscribe_push(self.phone["id"])
            self.registry.subscribe_push(self.phone["id"], sub, {"requests": True, "completion": True})
            replacement = event("replacement", "completion")
            self.registry.enqueue_push(self.device["deviceId"], replacement, notification_payload(replacement))
            self.assertEqual(self.registry.due_push()[0]["id"], old["id"])
            await asyncio.sleep(0)

        await self.state.push.deliver(qualification)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.registry.due_push()), 1)
        self.assertEqual(self.registry.due_push()[0]["event_id"], event("replacement")["id"])
        await self.drain()
        self.assertEqual(self.sent[0][1]["title"], "Codex · 任务完成")

    async def test_expired_old_provider_response_does_not_delete_new_subscription(self):
        sub = await self.subscribe()
        await self.notify()
        replacement, _, _ = subscription("replacement")

        async def sender(_subscription, _payload):
            self.registry.subscribe_push(self.phone["id"], replacement, {"requests": True, "completion": True})
            await asyncio.sleep(0)
            return 410

        self.state.push.sender = sender
        await self.drain()
        current = self.registry.push_subscription(self.phone["id"])
        self.assertIsNotNone(current)
        self.assertEqual(current["endpoint"], replacement["endpoint"])

    async def test_changing_pairing_revokes_old_push_authorization(self):
        await self.subscribe()
        await self.notify()
        other = self.registry.register(2, 20, "Other desktop")
        pairing = self.registry.pairing(other["deviceId"])
        claim = self.registry.claim(pairing["token"], "Same browser")
        self.registry.approve(other["deviceId"], pairing["pairingId"], True)
        response = await self.client.post("/connect/pair/status", json=claim, headers={**self.phone_headers(), "Origin": "http://localhost"})
        self.assertEqual(response.status, 200)
        self.assertIsNone(self.registry.get_phone(self.phone["id"]))
        self.assertIsNone(self.registry.push_subscription(self.phone["id"]))
        self.assertEqual(self.registry.due_push(), [])

    async def test_background_worker_dispatches_without_open_browser_page(self):
        await self.subscribe()
        self.state.push.task = asyncio.create_task(self.state.push.run(self.state.check_qualification))
        await self.notify()
        # Only the device made the notification call. No phone polling/page is
        # needed to wake the sender and deliver the durable outbox record.
        for _ in range(50):
            if self.sent:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.registry.due_push(), [])

    async def test_event_expiration_during_qualification_prevents_delivery(self):
        await self.subscribe()
        now = int(time.time())
        self.registry.clock = lambda: now
        await self.notify(event(created=now))

        async def qualification(_device):
            self.registry.clock = lambda: now + 301
            await asyncio.sleep(0)

        await self.state.push.deliver(qualification)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.registry.due_push(), [])

    async def test_404_and_410_remove_expired_subscription_while_redirect_drops_event(self):
        for status in [404, 410, 307]:
            with self.subTest(status=status):
                await self.subscribe(suffix=str(status))
                await self.notify(event(str(status)))
                self.send_status = status
                await self.drain()
                self.assertEqual(self.registry.due_push(), [])
                self.assertEqual(self.registry.push_subscription(self.phone["id"]) is None, status in {404, 410})

    async def test_transient_failure_retries_at_most_three_times(self):
        await self.subscribe()
        await self.notify()
        self.send_status = 503
        for attempt in range(3):
            with self.registry.db:
                self.registry.db.execute("UPDATE push_outbox SET next_attempt=0")
            await self.drain()
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.registry.due_push(), [])

    async def test_test_notification_targets_current_phone_and_is_rate_limited(self):
        await self.subscribe()
        phone2 = self.pair(self.device)
        await self.subscribe(phone2, "other")
        response = await self.client.post("/connect/push/test", json={}, headers=self.phone_headers(write=True))
        self.assertEqual(response.status, 200)
        response = await self.client.post("/connect/push/test", json={}, headers=self.phone_headers(write=True))
        self.assertEqual(response.status, 429)
        await self.drain()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0]["endpoint"].rsplit("/", 1)[1], "fixture")
        self.assertEqual(self.sent[0][1]["url"], "/")

    async def test_unavailable_key_exposes_false_and_cannot_accept_subscription(self):
        self.state.push.public_key = None
        response = await self.client.get("/connect/push", headers=self.phone_headers())
        body = await response.json()
        self.assertFalse(body["available"])
        sub, _, _ = subscription()
        response = await self.client.post("/connect/push/subscribe", headers=self.phone_headers(write=True),
                                          json={"subscription": sub, "events": {"requests": True, "completion": True}})
        self.assertEqual(response.status, 503)
        response = await self.client.post("/connect/device/notifications", headers=self.device_headers(), json=event())
        self.assertEqual(response.status, 503)

    async def test_per_phone_queue_is_bounded_and_old_events_do_not_deliver(self):
        await self.subscribe()
        now = time.time()
        for value in range(270):
            item = event(str(value), created=int(now))
            self.registry.enqueue_push(self.device["deviceId"], item, notification_payload(item))
        self.assertEqual(self.registry.db.execute("SELECT count(*) FROM push_outbox").fetchone()[0], 256)
        with self.registry.db:
            self.registry.db.execute("DELETE FROM push_outbox")
        old = event("old", created=int(now) - 4000)
        self.registry.enqueue_push(self.device["deviceId"], old, notification_payload(old))
        self.assertEqual(self.registry.due_push(), [])

    async def test_sw_is_served_with_root_scope_and_authenticated_html_has_push_script(self):
        response = await self.client.get("/connect/push-sw.js")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
        self.assertIn("no-store", response.headers["Cache-Control"])
        response = await self.client.get("/", headers=self.phone_headers())
        html = await response.text()
        self.assertIn('src="/connect/platform.js"', html)
        self.assertIn('src="/connect/push.js"', html)
        self.assertLess(html.index('src="/connect/platform.js"'), html.index('src="/connect/push.js"'))


if __name__ == "__main__":
    unittest.main()
