"""Encrypted Web Push, with persistent keys and a credential-aware outbox.

Only fixed notification text and a chat locator leave the relay.  Browser supplied
endpoints never get a general-purpose HTTP client: hosts are provider allowlisted,
DNS is checked at connection time, redirects and ambient proxies are disabled.
"""
import asyncio
import base64
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from aiohttp.resolver import DefaultResolver

EVENT_FIELDS = {"id", "kind", "threadId", "host", "count", "createdAt"}
THREAD = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def validate_events(value):
    if not isinstance(value, dict) or set(value) != {"requests", "completion"} or any(type(v) is not bool for v in value.values()):
        raise ValueError("notification preferences must be two booleans")
    return dict(value)


def validate_endpoint(endpoint):
    if (not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 2048 or
            any(ord(c) < 33 or ord(c) > 126 for c in endpoint) or "\\" in endpoint):
        raise ValueError("invalid push endpoint")
    try:
        url = urlsplit(endpoint)
        host = (url.hostname or "").lower()
        allowed = (host in {"fcm.googleapis.com", "updates.push.services.mozilla.com"} or
                   host.endswith(".push.apple.com") or host.endswith(".notify.windows.com"))
        if (url.scheme != "https" or not allowed or url.username is not None or
                url.password is not None or url.port not in {None, 443} or url.fragment or
                url.netloc.lower() not in {host, host + ":443"}):
            raise ValueError("push endpoint must belong to a supported HTTPS push service")
    except (TypeError, ValueError):
        raise ValueError("push endpoint must belong to a supported HTTPS push service") from None
    return endpoint


def _decode_key(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value) or len(value) > 128:
        raise ValueError("invalid subscription encryption key")
    try:
        decoded = base64.b64decode(value.rstrip("=") + "=" * (-len(value.rstrip("=")) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise ValueError("invalid subscription encryption key") from None
    if len(decoded) != length:
        raise ValueError("invalid subscription encryption key")
    return decoded


def validate_subscription(value, now):
    if not isinstance(value, dict) or set(value) - {"endpoint", "keys", "expirationTime"} or not {"endpoint", "keys"} <= set(value):
        raise ValueError("invalid push subscription")
    endpoint = validate_endpoint(value["endpoint"])
    keys = value["keys"]
    if not isinstance(keys, dict) or set(keys) != {"p256dh", "auth"}:
        raise ValueError("invalid subscription keys")
    public_key = _decode_key(keys["p256dh"], 65)
    _decode_key(keys["auth"], 16)
    # Reject off-curve public keys before any subscription is stored or queued.
    from cryptography.hazmat.primitives.asymmetric import ec
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
    expires = value.get("expirationTime")
    if expires is not None and (isinstance(expires, bool) or not isinstance(expires, (float, int)) or
                               not math.isfinite(expires) or not now * 1000 < expires <= (now + 5 * 366 * 86400) * 1000):
        raise ValueError("invalid subscription expiry")
    return {"endpoint": endpoint, "keys": dict(keys), "expirationTime": expires}


def validate_notification(event, now):
    if (not isinstance(event, dict) or set(event) != EVENT_FIELDS or
            not isinstance(event["id"], str) or not re.fullmatch(r"[0-9a-f]{64}", event["id"]) or
            not isinstance(event["kind"], str) or event["kind"] not in {"request", "completion"} or
            not isinstance(event["threadId"], str) or not THREAD.fullmatch(event["threadId"]) or
            not isinstance(event["host"], str) or not 1 <= len(event["host"]) <= 256 or not event["host"].isprintable() or
            any(c.isspace() or c in "~/#|" for c in event["host"]) or
            type(event["count"]) is not int or not 1 <= event["count"] <= 4096 or
            type(event["createdAt"]) is not int or not now - 86400 <= event["createdAt"] <= now + 300):
        raise ValueError("invalid notification event")
    return dict(event)


def notification_payload(event):
    request = event["kind"] == "request"
    return {"title": "Codex · 需要处理" if request else "Codex · 任务完成",
            "body": "有任务需要你确认，点击打开聊天。" if request else "任务已完成，点击查看聊天。",
            "url": "/#" + event["threadId"] + "~" + quote(event["host"], safe=""),
            "tag": "codex-" + event["kind"] + "-" + event["threadId"] + "-" + hashlib.sha256(event["host"].encode()).hexdigest()[:16]}


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        # These exact checked addresses are passed to TCPConnector. A separate
        # validation lookup followed by a second DNS lookup would permit rebinding.
        records = await super().resolve(host, port, family)
        if not records or any(not ipaddress.ip_address(row["host"]).is_global for row in records):
            raise OSError("push provider did not resolve to public addresses")
        return records


class EncryptedSender:
    def __init__(self, key, subject):
        self.key, self.subject = key, subject
        self.session = None

    def encode(self, subscription, payload, ttl=300):
        from py_vapid import Vapid
        from pywebpush import WebPusher
        endpoint = validate_endpoint(subscription["endpoint"])
        origin = urlsplit(endpoint)
        headers = Vapid(self.key).sign({"sub": self.subject, "aud": "https://" + origin.netloc,
                                       "exp": int(time.time()) + 12 * 3600})
        data = WebPusher(subscription).encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), "aes128gcm")["body"]
        headers.update({"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                        "TTL": str(ttl), "Urgency": "normal"})
        return endpoint, data, headers

    async def __call__(self, subscription, payload):
        ttl = 3600 if payload.get("tag", "").startswith("codex-completion-") else 300
        endpoint, data, headers = self.encode(subscription, payload, ttl=ttl)
        if self.session is None or self.session.closed:
            self.session = ClientSession(connector=TCPConnector(resolver=PublicResolver(), use_dns_cache=False, limit=8),
                                         trust_env=False, timeout=ClientTimeout(total=10), raise_for_status=False)
        async with self.session.post(endpoint, data=data, headers=headers, allow_redirects=False) as response:
            # No body, subscription URL, auth headers, or provider details enter logs.
            return response.status

    async def close(self):
        if self.session is not None:
            await self.session.close()


class PushService:
    def __init__(self, registry, public_origin, key_path, *, subject=None, sender=None, interval=5):
        self.registry, self.origin = registry, public_origin
        self.public_key, self.key = None, None
        self.sender, self.task = sender, None
        self.interval, self.wake = interval, asyncio.Event()
        subject = subject or public_origin
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            import pywebpush  # noqa: F401 -- availability includes the encryption runtime
            parsed = urlsplit(subject)
            if (parsed.scheme not in {"https", "mailto"} or not parsed.path and parsed.scheme == "mailto" or
                    parsed.scheme == "https" and (not parsed.hostname or parsed.username or parsed.password or parsed.fragment)):
                # Loopback origins are useful for acceptance tests; production is HTTPS.
                if subject != public_origin or not public_origin.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]")):
                    raise ValueError("invalid VAPID subject")
            path = Path(key_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                generated = ec.generate_private_key(ec.SECP256R1())
                encoded = generated.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                try:
                    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass  # Another process generated the deployment key first.
                else:
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(encoded)
                        output.flush()
                        os.fsync(output.fileno())
            self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(self.key, ec.EllipticCurvePrivateKey) or not isinstance(self.key.curve, ec.SECP256R1):
                raise ValueError("VAPID key must be P-256")
            if os.name != "nt":
                path.chmod(0o600)
            self.public_key = base64.urlsafe_b64encode(self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
            self.sender = sender or EncryptedSender(self.key, subject)
        except Exception:
            # Never rotate a missing dependency/corrupt key and invalidate phones.
            self.public_key, self.key = None, None

    @property
    def available(self):
        return bool(self.public_key and self.sender)

    def status(self, phone_id):
        return {"available": self.available, "publicKey": self.public_key,
                "subscribed": bool(self.registry.push_subscription(phone_id)),
                "events": self.registry.push_preferences(phone_id)}

    async def deliver(self, qualification):
        if not self.available:
            return
        for item in self.registry.due_push():
            phone = self.registry.get_phone(item["phone_id"])
            device = self.registry.get_device(item["device_id"])
            if not phone or not device:
                self.registry.finish_push(item)
                continue
            try:
                await qualification(device)
                # Recheck after qualification I/O, immediately before starting send.
                subscription = self.registry.push_subscription(item["phone_id"])
                preferences = self.registry.push_preferences(item["phone_id"])
                preference = "requests" if item["kind"] == "request" else "completion"
                if (not self.registry.push_pending(item) or
                        not self.registry.get_phone(item["phone_id"]) or not self.registry.get_device(item["device_id"]) or
                        not subscription or subscription["endpoint_hash"] != item["endpoint_hash"] or
                        subscription["keys_json"] != item["keys_json"] or
                        item["kind"] != "test" and not preferences[preference]):
                    self.registry.finish_push(item)
                    continue
                status = await self.sender({"endpoint": item["endpoint"], "keys": json.loads(item["keys_json"])}, json.loads(item["payload"]))
                if not self.registry.push_pending(item):
                    continue
                current = self.registry.push_subscription(item["phone_id"])
                if status in {404, 410} and current and current["endpoint_hash"] == item["endpoint_hash"] and current["keys_json"] == item["keys_json"]:
                    self.registry.unsubscribe_push(item["phone_id"])
                else:
                    self.registry.finish_push(item, retry=status == 429 or 500 <= status <= 599)
            except web.HTTPUnauthorized:
                self.registry.finish_push(item)
            except Exception:
                self.registry.finish_push(item, retry=True)

    async def run(self, qualification):
        while True:
            await self.deliver(qualification)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        close = getattr(self.sender, "close", None)
        if close:
            await close()
