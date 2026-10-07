"""Multi-tenant outbound-only Codex Connect relay.

TLS must terminate at the fixed public origin. Desktop device authorization and
browser phone authorization are deliberately independent of the model API key.
"""
import argparse
import asyncio
import base64
import hmac
import inspect
import json
import logging
import re
import secrets
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from .protocol import MAX_BODY, safe_content_type, validate_request
from .registry import Registry
from .push import (EVENT_FIELDS, PushService, notification_payload,
                   validate_events, validate_notification, validate_subscription)

COOKIE = "mhenwa_connect_phone"
STATE = web.AppKey("connect_state", object)
LOG = logging.getLogger(__name__)
SECURITY_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"}


class Disconnected(Exception):
    pass


class RateLimit:
    """Fixed window counts, with bounded cardinality for unauthenticated IPs."""
    def __init__(self, maximum=4096):
        self.entries, self.maximum = OrderedDict(), maximum

    def allow(self, key, count=60, period=60):
        now = time.monotonic()
        self.entries.setdefault(key, deque())
        bucket = self.entries[key]
        while bucket and bucket[0] <= now - period:
            bucket.popleft()
        self.entries.move_to_end(key)
        while len(self.entries) > self.maximum:
            self.entries.popitem(last=False)
        if len(bucket) >= count:
            return False
        bucket.append(now)
        return True


async def _call(function, *args):
    value = function(*args)
    return await value if inspect.isawaitable(value) else value


class Relay:
    def __init__(self, public_origin, registry, eligibility, web_dir, landing_dir,
                 *, request_timeout=30, max_inflight_per_device=0,
                 max_inflight_per_phone=0, max_total_inflight=0, max_buffered_body_bytes=MAX_BODY, eligibility_introspect=None,
                 qualification_cache_ttl=30, monitor_interval=5, max_body_bytes=MAX_BODY,
                 max_online_devices=0):
        origin = urlsplit(public_origin)
        if (origin.scheme not in {"http", "https"} or not origin.netloc or origin.username
                or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}):
            raise ValueError("public_origin must be a fixed root HTTP(S) origin")
        if origin.scheme == "http" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("HTTP public_origin is allowed only for loopback tests")
        self.origin = public_origin.rstrip("/")
        self.host = origin.netloc
        self.secure = origin.scheme == "https"
        self.registry, self.eligibility = registry, eligibility
        self.introspect = eligibility_introspect or getattr(eligibility, "introspect", None)
        self.web_dir, self.landing_dir = Path(web_dir), Path(landing_dir)
        self.timeout = request_timeout
        limits = (max_inflight_per_device, max_inflight_per_phone, max_total_inflight)
        if any(value is not None and value < 0 for value in limits):
            raise ValueError("invalid request concurrency limit")
        self.max_device = None if max_inflight_per_device in (None, 0) else max_inflight_per_device
        self.max_phone = None if max_inflight_per_phone in (None, 0) else max_inflight_per_phone
        self.max_total = None if max_total_inflight in (None, 0) else max_total_inflight
        self.total_inflight = 0
        self.max_buffered, self.request_bytes, self.response_bytes = max_buffered_body_bytes, 0, 0
        if (not 0 < max_body_bytes <= MAX_BODY or
                max_online_devices is not None and max_online_devices < 0):
            raise ValueError("invalid body or online-device limit")
        # A zero value explicitly disables the online-device admission cap for
        # deployments that want to observe real resource usage before choosing
        # a capacity limit.  Body-size, aggregate-buffer and timeout protections
        # remain enforced even when admission caps are disabled.
        self.max_body = max_body_bytes
        self.max_online = None if max_online_devices in (None, 0) else max_online_devices
        self.max_frame = ((self.max_body + 2) // 3) * 4 + 16384
        self.registrations = 0
        self.cache_ttl, self.monitor_interval = qualification_cache_ttl, monitor_interval
        self.qualification_cache = OrderedDict()
        self.rates, self.devices, self.pending = RateLimit(), {}, {}
        self.connecting = set()
        self.device_inflight, self.phone_inflight = defaultdict(int), defaultdict(int)
        self.monitor_task = None
        self.instance_id = secrets.token_hex(16)

    def origin_check(self, request, required=True):
        supplied = request.headers.getall("Origin", [])
        if ((required and supplied != [self.origin]) or
                (not required and supplied and supplied != [self.origin])):
            raise web.HTTPForbidden(text='{"error":"request origin is not permitted"}', content_type="application/json")

    async def check_qualification(self, device):
        if self.introspect is None:
            return
        key = (device["owner_id"], device["token_id"])
        now = time.monotonic()
        item = self.qualification_cache.get(key)
        if item and item[0] > now:
            result = item[1]
        else:
            try:
                result = await asyncio.wait_for(_call(self.introspect, int(key[0]), int(key[1])), timeout=10)
            except Exception:
                # A backend outage does not permanently revoke a device, but fails closed.
                raise web.HTTPServiceUnavailable(text='{"error":"qualification service unavailable"}', content_type="application/json") from None
            if not isinstance(result, dict):
                raise web.HTTPServiceUnavailable()
            if result.get("reason") == "unavailable":
                raise web.HTTPServiceUnavailable(text='{"error":"qualification service unavailable"}', content_type="application/json")
            self.qualification_cache[key] = (now + self.cache_ttl, result)
            self.qualification_cache.move_to_end(key)
            while len(self.qualification_cache) > 4096:
                self.qualification_cache.popitem(last=False)
        if (result.get("eligible") is not True or str(result.get("user_id")) != key[0]
                or str(result.get("token_id")) != key[1]):
            self.registry.revoke_device(device["id"])
            await self.close_device(device["id"])
            raise web.HTTPUnauthorized(text='{"error":"remote connection qualification revoked"}', content_type="application/json")

    async def device_auth(self, request):
        authorization = request.headers.getall("Authorization", [])
        if len(authorization) != 1 or not authorization[0].startswith("Bearer "):
            raise web.HTTPUnauthorized()
        device = self.registry.device(authorization[0][7:])
        if not device:
            raise web.HTTPUnauthorized()
        self.origin_check(request, required=False)
        await self.check_qualification(device)
        if self.registry.get_device(device["id"]) is None:
            raise web.HTTPUnauthorized()
        if not self.rates.allow("device:" + device["id"], 240):
            raise web.HTTPTooManyRequests()
        return device

    async def phone_auth(self, request, write=False):
        phone = self.registry.phone(request.cookies.get(COOKIE, ""))
        if not phone:
            raise web.HTTPUnauthorized(text='{"error":"phone authorization required","code":"connect_unauthorized"}', content_type="application/json")
        self.origin_check(request, required=write)
        if write and not hmac.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), phone["csrf"].encode()):
            raise web.HTTPForbidden(text='{"error":"CSRF validation failed"}', content_type="application/json")
        device = self.registry.get_device(phone["device_id"])
        if device is None:
            raise web.HTTPUnauthorized()
        await self.check_qualification(device)
        if self.registry.phone(request.cookies.get(COOKIE, "")) is None:
            raise web.HTTPUnauthorized()
        if not self.rates.allow("phone:" + phone["id"], 240):
            raise web.HTTPTooManyRequests()
        return phone, device

    def fail_pending(self, device_id=None, phone_id=None):
        for pending in list(self.pending.values()):
            if ((device_id is None or pending["device"] == device_id) and
                    (phone_id is None or pending["phone"] == phone_id)):
                if not pending["future"].done():
                    pending["future"].set_exception(Disconnected())

    async def close_device(self, device_id):
        self.fail_pending(device_id=device_id)
        ws = self.devices.get(device_id)
        if ws is not None:
            await ws.close(code=1008, message=b"device authorization no longer available")

    async def monitor(self):
        while True:
            await asyncio.sleep(self.monitor_interval)
            for identifier in list(self.devices):
                device = self.registry.get_device(identifier)
                if device is None:
                    await self.close_device(identifier)
                    continue
                try:
                    await self.check_qualification(device)
                except web.HTTPException:
                    await self.close_device(identifier)
            # Expired/revoked phone authorization ends its in-flight long poll.
            now = self.registry.clock()
            for pending in list(self.pending.values()):
                row = self.registry.db.execute("SELECT revoked,expires FROM phones WHERE id=?", (pending["phone"],)).fetchone()
                if not row or row["revoked"] or row["expires"] <= now:
                    self.fail_pending(phone_id=pending["phone"])

    async def json(self, request, allowed, required=()):
        if request.content_type != "application/json" or request.content_length is None or request.content_length > 8192:
            raise ValueError("small JSON body required")
        body = await request.json()
        if not isinstance(body, dict) or set(body) - set(allowed) or not set(required) <= set(body):
            raise ValueError("unsupported or missing request fields")
        return body

    async def register(self, request):
        self.origin_check(request, required=False)
        if not self.rates.allow("register:" + (request.remote or "unknown"), 10):
            raise web.HTTPTooManyRequests()
        if self.registrations >= 8:
            raise web.HTTPTooManyRequests()
        self.registrations += 1
        try:
            return await self._register(request)
        finally:
            self.registrations -= 1

    async def _register(self, request):
        body = await self.json(request, {"apiKey", "deviceName"}, {"apiKey", "deviceName"})
        key = body["apiKey"]
        if not isinstance(key, str) or not 8 <= len(key) <= 512:
            raise ValueError("invalid model credential")
        name = self.registry.name(body["deviceName"])
        try:
            eligible = await asyncio.wait_for(_call(self.eligibility, key), timeout=10)
        except Exception:
            raise web.HTTPServiceUnavailable(text='{"error":"qualification service unavailable"}', content_type="application/json") from None
        finally:
            # No raw key is retained on state, metadata, cache or log records.
            body.pop("apiKey", None)
        if isinstance(eligible, dict) and eligible.get("reason") == "unavailable":
            raise web.HTTPServiceUnavailable(text='{"error":"qualification service unavailable"}', content_type="application/json")
        if not isinstance(eligible, dict) or eligible.get("eligible") is not True:
            raise web.HTTPForbidden(text='{"error":"model credential is not eligible for this service"}', content_type="application/json")
        if "user_id" not in eligible or "token_id" not in eligible:
            raise web.HTTPServiceUnavailable()
        return web.json_response(self.registry.register(eligible["user_id"], eligible["token_id"], name), status=201)

    async def device_ws(self, request):
        device = await self.device_auth(request)
        if device["id"] in self.connecting:
            raise web.HTTPServiceUnavailable()
        if (device["id"] not in self.devices and self.max_online is not None and
                len(set(self.devices) | self.connecting) >= self.max_online):
            raise web.HTTPServiceUnavailable()
        self.connecting.add(device["id"])
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=self.max_frame, compress=False)
        try:
            await ws.prepare(request)
        except BaseException:
            self.connecting.discard(device["id"])
            raise
        old = self.devices.get(device["id"])
        self.devices[device["id"]] = ws
        self.connecting.discard(device["id"])
        if old is not None:
            self.fail_pending(device_id=device["id"])
            await old.close(code=1012, message=b"new device connection established")
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        if not self.rates.allow("frame:" + device["id"], 240):
                            raise ValueError("device frame rate exceeds limit")
                        frame = json.loads(message.data)
                        if not isinstance(frame, dict) or frame.get("type") != "response":
                            raise ValueError("response frame required")
                        if set(frame) - {"type", "id", "status", "body", "contentType", "contentDisposition"}:
                            raise ValueError("unsupported response fields")
                        identifier = frame.get("id")
                        if not isinstance(identifier, str) or len(identifier) > 128:
                            raise ValueError("invalid request identity")
                        pending = self.pending.get(identifier)
                        if pending is None:
                            continue
                        if pending["device"] != device["id"] or self.devices.get(device["id"]) is not ws:
                            raise ValueError("response is not owned by device connection")
                        status, encoded = frame.get("status"), frame.get("body")
                        if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status <= 599 or not isinstance(encoded, str):
                            raise ValueError("invalid response")
                        if pending["future"].done():
                            continue
                        estimated_size = len(encoded) // 4 * 3 - (2 if encoded.endswith("==") else 1 if encoded.endswith("=") else 0)
                        if estimated_size > self.max_body or self.response_bytes + estimated_size > self.max_buffered:
                            raise ValueError("aggregate response buffer limit reached")
                        data = base64.b64decode(encoded, validate=True)
                        if len(data) > self.max_body:
                            raise ValueError("response body exceeds limit")
                        # Authorization might have expired while the request was executing.
                        phone = self.registry.db.execute("SELECT revoked,expires FROM phones WHERE id=?", (pending["phone"],)).fetchone()
                        if (not self.registry.get_device(device["id"]) or not phone or phone["revoked"] or phone["expires"] <= self.registry.clock()):
                            self.fail_pending(phone_id=pending["phone"])
                            continue
                        result = (status, data, safe_content_type(frame.get("contentType")), frame.get("contentDisposition"))
                        if not pending["future"].done():
                            pending["response_size"] = len(data)
                            self.response_bytes += len(data)
                            pending["future"].set_result(result)
                    except (ValueError, TypeError):
                        await ws.close(code=1008, message=b"invalid response frame")
                        break
                elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
        finally:
            if self.devices.get(device["id"]) is ws:
                del self.devices[device["id"]]
                self.fail_pending(device_id=device["id"])
        return ws

    async def device_action(self, request):
        device = await self.device_auth(request)
        tail = request.match_info["tail"]
        if request.method == "POST" and tail == "pair":
            await self.json(request, set())
            result = self.registry.pairing(device["id"])
            result["url"] = self.origin + "/#connect_pair=" + result.pop("token")
        elif request.method == "GET" and tail == "pairings":
            result = {"pairings": self.registry.pairings(device["id"])}
        elif request.method == "POST" and re.fullmatch(r"pairings/[A-Za-z0-9_-]{8,128}/approve", tail):
            body = await self.json(request, {"approved"}, {"approved"})
            self.registry.approve(device["id"], tail.split("/")[1], body["approved"])
            result = {"ok": True}
        elif request.method == "GET" and tail == "phones":
            result = {"phones": self.registry.phones(device["id"])}
        elif request.method == "POST" and re.fullmatch(r"phones/[A-Za-z0-9_-]{8,128}/revoke", tail):
            await self.json(request, set())
            identifier = tail.split("/")[1]
            self.registry.revoke_phone(device["id"], identifier)
            self.fail_pending(phone_id=identifier)
            result = {"ok": True}
        elif request.method == "POST" and tail == "revoke":
            await self.json(request, set())
            self.registry.revoke_device(device["id"])
            await self.close_device(device["id"])
            result = {"ok": True}
        elif request.method == "POST" and tail == "notifications":
            body = await self.json(request, EVENT_FIELDS, EVENT_FIELDS)
            event = validate_notification(body, self.registry.clock())
            if not self.push.available:
                raise web.HTTPServiceUnavailable(text='{"error":"web push is unavailable"}', content_type="application/json")
            accepted = self.registry.enqueue_push(device["id"], event, notification_payload(event))
            self.push.wake.set()
            result = {"ok": True, "accepted": accepted}
        else:
            raise web.HTTPNotFound()
        return web.json_response(result)

    async def push_status(self, request):
        phone, _ = await self.phone_auth(request)
        return web.json_response(self.push.status(phone["id"]))

    async def push_action(self, request):
        phone, _ = await self.phone_auth(request, write=True)
        action = request.match_info["action"]
        if action == "unsubscribe":
            await self.json(request, set())
            self.registry.unsubscribe_push(phone["id"])
        elif action == "preferences":
            body = await self.json(request, {"events"}, {"events"})
            self.registry.set_push_preferences(phone["id"], validate_events(body["events"]))
        elif action == "subscribe":
            body = await self.json(request, {"subscription", "events"}, {"subscription", "events"})
            if not self.push.available:
                raise web.HTTPServiceUnavailable(text='{"error":"web push is unavailable"}', content_type="application/json")
            subscription = validate_subscription(body["subscription"], self.registry.clock())
            self.registry.subscribe_push(phone["id"], subscription, validate_events(body["events"]))
        elif action == "test":
            await self.json(request, set())
            if not self.push.available:
                raise web.HTTPServiceUnavailable(text='{"error":"web push is unavailable"}', content_type="application/json")
            if not self.registry.push_subscription(phone["id"]):
                raise web.HTTPConflict(text='{"error":"enable web push before sending a test"}', content_type="application/json")
            if not self.rates.allow("push-test:" + phone["id"], 1, 10):
                raise web.HTTPTooManyRequests()
            self.registry.enqueue_push_test(phone["id"], {"title": "Codex · 通知测试", "body": "网页关闭后，也可以收到这里的通知。", "url": "/", "tag": "codex-push-test"})
            self.push.wake.set()
        else:
            raise web.HTTPNotFound()
        return web.json_response({"ok": True, **self.push.status(phone["id"])})

    async def pair_claim(self, request):
        self.origin_check(request)
        if not self.rates.allow("claim:" + (request.remote or "unknown"), 30):
            raise web.HTTPTooManyRequests()
        body = await self.json(request, {"token", "phoneName"}, {"token", "phoneName"})
        return web.json_response(self.registry.claim(body["token"], body["phoneName"]))

    async def pair_status(self, request):
        self.origin_check(request)
        if not self.rates.allow("status:" + (request.remote or "unknown"), 120):
            raise web.HTTPTooManyRequests()
        body = await self.json(request, {"claimToken"}, {"claimToken"})
        result, token = self.registry.claim_status(body["claimToken"])
        response = web.json_response(result)
        if token:
            # If this browser previously controlled another device, revoke old authorization.
            old = self.registry.phone(request.cookies.get(COOKIE, ""))
            if old:
                self.registry.revoke_phone(old["device_id"], old["id"])
                self.fail_pending(phone_id=old["id"])
            response.set_cookie(COOKIE, token, httponly=True, secure=self.secure,
                                samesite="Strict", path="/", max_age=int(self.registry.phone_ttl))
        return response

    async def me(self, request):
        phone = self.registry.phone(request.cookies.get(COOKIE, ""))
        if not phone:
            result = {"authenticated": False, "csrf": None}
        else:
            phone, device = await self.phone_auth(request)
            result = {"authenticated": True, "deviceId": device["id"], "deviceName": device["name"],
                      "online": device["id"] in self.devices, "csrf": phone["csrf"]}
        if request.path == "/api/auth":
            result.update({"transport": "poll", "instanceId": self.instance_id,
                           "notifications": False, "passwordless": False, "connect": True})
        return web.json_response(result)

    async def logout(self, request):
        phone, _ = await self.phone_auth(request, write=True)
        await self.json(request, set())
        self.registry.revoke_phone(phone["device_id"], phone["id"])
        self.fail_pending(phone_id=phone["id"])
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE, path="/", secure=self.secure, httponly=True, samesite="Strict")
        return response

    async def forward(self, request):
        phone, device = await self.phone_auth(request, write=request.method != "GET")
        if request.headers.get("Transfer-Encoding"):
            raise ValueError("streaming request bodies are not supported")
        reservation = request.content_length or 0
        if request.content_length is None and request.can_read_body:
            raise ValueError("explicit request size required")
        if reservation > self.max_body:
            raise web.HTTPRequestEntityTooLarge(max_size=self.max_body, actual_size=reservation)
        if ((self.max_device is not None and self.device_inflight[device["id"]] >= self.max_device) or
                (self.max_phone is not None and self.phone_inflight[phone["id"]] >= self.max_phone) or
                (self.max_total is not None and self.total_inflight >= self.max_total) or
                self.request_bytes + reservation > self.max_buffered):
            raise web.HTTPTooManyRequests(text='{"error":"too many in-flight requests"}', content_type="application/json")
        identifier = secrets.token_urlsafe(24)
        future = asyncio.get_running_loop().create_future()
        self.device_inflight[device["id"]] += 1
        self.phone_inflight[phone["id"]] += 1
        self.total_inflight += 1
        self.request_bytes += reservation
        sent, ws, response = False, None, None
        try:
            # Reserve slots before buffering an upload, then revalidate after any await.
            body = await asyncio.wait_for(request.read(), timeout=self.timeout)
            content_type = request.headers.get("Content-Type", "application/json")
            path = request.rel_url.raw_path_qs
            validate_request(request.method, path, len(body), content_type)
            if (self.registry.phone(request.cookies.get(COOKIE, "")) is None or
                    self.registry.get_device(device["id"]) is None):
                raise web.HTTPUnauthorized()
            ws = self.devices.get(device["id"])
            if ws is None or ws.closed:
                return web.json_response({"error": "desktop device is offline", "code": "connect_offline", "outcomeUnknown": False}, status=503)
            self.pending[identifier] = {"future": future, "device": device["id"], "phone": phone["id"], "response_size": 0}
            sent = True  # A send failure can occur after partial transmission.
            await ws.send_json({"type": "request", "id": identifier, "method": request.method,
                                "path": path, "body": base64.b64encode(body).decode(), "contentType": content_type})
            status, data, mime, disposition = await asyncio.wait_for(future, timeout=self.timeout)
            response = web.StreamResponse(status=status, headers={**SECURITY_HEADERS, "Content-Type": mime,
                                                                  "Content-Length": str(len(data))})
            if (isinstance(disposition, str) and len(disposition) <= 1024 and
                    "\r" not in disposition and "\n" not in disposition and
                    disposition.startswith(("attachment;", "inline;"))):
                response.headers["Content-Disposition"] = disposition
            await response.prepare(request)
            for offset in range(0, len(data), 1024 * 1024):
                await response.write(data[offset:offset + 1024 * 1024])
            await response.write_eof()
            return response
        except (asyncio.TimeoutError, Disconnected, ConnectionError, RuntimeError):
            if response is not None and response.prepared:
                # Headers/body may already be on the wire: close the truncated stream,
                # never append a second JSON response or imply successful delivery.
                response.force_close()
                return response
            unknown = request.method == "POST" and sent
            return web.json_response({"error": "desktop response unavailable; do not automatically retry" if unknown else "desktop response unavailable",
                                      "code": "connect_outcome_unknown" if unknown else "connect_unavailable",
                                      "outcomeUnknown": unknown, "retryable": False}, status=504 if ws and not ws.closed else 503)
        finally:
            record = self.pending.pop(identifier, None)
            self.response_bytes -= record["response_size"] if record else 0
            self.total_inflight -= 1
            self.request_bytes -= reservation
            if not future.done():
                future.cancel()
            # Retrieve a race-delivered exception even if the send itself failed.
            if future.done() and not future.cancelled():
                future.exception()
            for mapping, key in ((self.device_inflight, device["id"]), (self.phone_inflight, phone["id"])):
                mapping[key] -= 1
                if not mapping[key]:
                    del mapping[key]

    async def static(self, request):
        from bridge.httpd import FONT_ROUTE, STATIC
        if request.method != "GET":
            raise web.HTTPMethodNotAllowed(request.method, ["GET"])
        path = request.path
        if path in {"/", "/connect/"}:
            phone = self.registry.phone(request.cookies.get(COOKIE, ""))
            if phone and path == "/":
                await self.phone_auth(request)
                html = (self.web_dir / "index.html").read_text(encoding="utf-8")
                injected = "".join(f'<script src="/connect/{script}" defer></script>' for script in ("platform.js", "push.js") if (self.landing_dir / script).is_file())
                if injected:
                    html = html.replace("<script ", injected + "<script ", 1)
                if (self.landing_dir / "platform.css").is_file():
                    html = html.replace("</head>", '<link rel="stylesheet" href="/connect/platform.css"></head>')
                return web.Response(text=html, content_type="text/html")
            file, content_type = self.landing_dir / "index.html", "text/html"
        elif path in {"/connect/pair.js", "/connect/pair.css", "/connect/platform.js", "/connect/platform.css", "/connect/push.js", "/connect/push-sw.js"}:
            file = self.landing_dir / path.rsplit("/", 1)[1]
            content_type = "text/javascript" if path.endswith(".js") else "text/css"
        elif path in STATIC:
            filename, content_type = STATIC[path]
            file = self.web_dir / filename
        else:
            match = FONT_ROUTE.fullmatch(path)
            if not match:
                raise web.HTTPNotFound()
            file, content_type = self.web_dir / "vendor" / "katex" / "fonts" / match[1], "font/" + match[2]
        if not file.is_file():
            raise web.HTTPNotFound()
        headers = {"Content-Type": content_type}
        if path == "/connect/push-sw.js":
            headers["Service-Worker-Allowed"] = "/"
        return web.Response(body=file.read_bytes(), headers=headers)


@web.middleware
async def protections(request, handler):
    state = request.app[STATE]
    if request.headers.getall("Host", []) != [state.host]:
        return web.json_response({"error": "public Host is not permitted"}, status=403)
    try:
        response = await handler(request)
    except PermissionError as exc:
        response = web.json_response({"error": str(exc)}, status=403)
    except (ValueError, json.JSONDecodeError):
        response = web.json_response({"error": "invalid or unsupported request"}, status=400)
    except web.HTTPException as exc:
        response = web.Response(status=exc.status, body=exc.body, headers=exc.headers)
    except Exception:
        # No exception repr, body, headers, query strings or credentials enter logs.
        LOG.error("Connect request failed")
        response = web.json_response({"error": "connection service unavailable"}, status=500)
    response.headers.update(SECURITY_HEADERS)
    return response


def create_app(public_origin, data_dir, eligibility, web_dir=None, landing_dir=None, *,
               registry=None, request_timeout=30, pair_ttl=300, phone_ttl=2592000,
               max_devices_per_owner=5, max_inflight_per_device=0,
               max_inflight_per_phone=0, max_total_inflight=0, max_buffered_body_bytes=MAX_BODY, eligibility_introspect=None,
               eligibility_recheck=None, qualification_cache_ttl=30, monitor_interval=5,
               max_body_bytes=MAX_BODY, max_online_devices=0, push_sender=None,
               vapid_subject=None, vapid_key_file=None, push_auto_send=True, push_interval=5):
    """Build a testable relay; qualification provider is configuration, not input."""
    root = Path(__file__).resolve().parent.parent
    registry = registry or Registry(Path(data_dir) / "connect.sqlite3", pair_ttl=pair_ttl,
                                    phone_ttl=phone_ttl, max_devices_per_owner=max_devices_per_owner)
    state = Relay(public_origin, registry, eligibility, web_dir or root / "web", landing_dir or root / "connect" / "web",
                  request_timeout=request_timeout, max_inflight_per_device=max_inflight_per_device,
                  max_inflight_per_phone=max_inflight_per_phone, max_total_inflight=max_total_inflight,
                  max_buffered_body_bytes=max_buffered_body_bytes,
                  eligibility_introspect=eligibility_introspect or eligibility_recheck,
                  qualification_cache_ttl=qualification_cache_ttl, monitor_interval=monitor_interval,
                  max_body_bytes=max_body_bytes, max_online_devices=max_online_devices)
    state.push = PushService(registry, state.origin, vapid_key_file or Path(data_dir) / "vapid-private.pem",
                             subject=vapid_subject, sender=push_sender, interval=push_interval)
    app = web.Application(middlewares=[protections], client_max_size=MAX_BODY)
    app[STATE] = state
    app.router.add_post("/connect/register", state.register)
    app.router.add_get("/connect/device/ws", state.device_ws)
    app.router.add_route("*", "/connect/device/{tail:.*}", state.device_action)
    app.router.add_post("/connect/pair/claim", state.pair_claim)
    app.router.add_post("/connect/pair/status", state.pair_status)
    app.router.add_get("/connect/me", state.me)
    app.router.add_post("/connect/logout", state.logout)
    app.router.add_get("/connect/push", state.push_status)
    app.router.add_post("/connect/push/{action}", state.push_action)
    app.router.add_get("/api/auth", state.me)
    app.router.add_post("/api/logout", state.logout)
    app.router.add_route("*", "/api/{tail:.*}", state.forward)
    async def health(request):
        return web.json_response({"ok": True, "service": "mhenwa-connect"})
    app.router.add_get("/health", health)
    app.router.add_route("*", "/{tail:.*}", state.static)

    async def startup(application):
        state.monitor_task = asyncio.create_task(state.monitor())
        if push_auto_send and state.push.available:
            state.push.task = asyncio.create_task(state.push.run(state.check_qualification))

    async def cleanup(application):
        state.monitor_task.cancel()
        await asyncio.gather(state.monitor_task, return_exceptions=True)
        for identifier in list(state.devices):
            await state.close_device(identifier)
        await state.push.close()
        registry.close()
        close = getattr(eligibility, "close", None)
        if close:
            await _call(close)

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def main(argv=None):
    from .eligibility import EligibilityClient
    parser = argparse.ArgumentParser(description="Mhenwa Codex Connect relay (run behind HTTPS)")
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--eligibility-url", required=True)
    parser.add_argument("--eligibility-secret-file", required=True)
    parser.add_argument("--vapid-subject", help="VAPID contact HTTPS URL or mailto URI; defaults to public origin")
    parser.add_argument("--vapid-key-file", help="persistent P-256 private key; defaults to data-dir/vapid-private.pem")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18787)
    parser.add_argument("--max-inflight", type=int, default=0,
                        help="maximum total in-flight requests; 0 means unlimited")
    parser.add_argument("--max-buffered-body-mib", type=int, default=20)
    parser.add_argument("--max-body-mib", type=int, default=2,
                        help="staging body cap; protocol maximum is 20 MiB")
    parser.add_argument("--max-online-devices", type=int, default=0,
                        help="maximum simultaneously connected computers; 0 means unlimited")
    args = parser.parse_args(argv)
    shared_secret = Path(args.eligibility_secret_file).read_text(encoding="utf-8").strip()
    provider = EligibilityClient(args.eligibility_url, shared_secret)
    if (args.max_inflight < 0 or args.max_buffered_body_mib < 1 or
            not 0 < args.max_body_mib <= 20 or args.max_online_devices < 0):
        parser.error("resource limits must be non-negative, with zero meaning unlimited")
    app = create_app(args.public_origin, args.data_dir, provider,
                     max_total_inflight=args.max_inflight,
                     max_buffered_body_bytes=args.max_buffered_body_mib * 1024 * 1024,
                     max_body_bytes=args.max_body_mib * 1024 * 1024,
                     max_online_devices=args.max_online_devices,
                     vapid_subject=args.vapid_subject, vapid_key_file=args.vapid_key_file)
    web.run_app(app, host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
