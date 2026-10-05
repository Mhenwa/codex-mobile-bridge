"""Narrow read-only qualification adapter for New API, without model inference.

New API v1.0.0-rc.24 sources: model/token.go, model/user.go,
common/constants.go at https://github.com/QuantumNous/new-api/tree/v1.0.0-rc.24.
The usage/token endpoint is deliberately NOT used as full authentication.
Only token ownership/status/expiry and account status are read; model quota is
separate from remote-access eligibility. No API key is stored by this module.
"""
import argparse
import asyncio
import hmac
import inspect
import ipaddress
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web


MAX_REQUEST = 16384
MAX_RESPONSE = 4096
REASONS = frozenset(("invalid_key", "token_disabled", "token_expired", "user_disabled",
                     "not_entitled", "unavailable"))


def _secret(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 4096 or not value.isascii() or any(c.isspace() for c in value):
        raise ValueError("Eligibility adapter secret must be a private random ASCII value of at least 32 characters")
    return value


def valid_key(value):
    return (isinstance(value, str) and 4 <= len(value) <= 8192 and value.isascii()
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))


def _id(value):
    return type(value) is int and value > 0


def _denied(reason):
    return {"eligible": False, "reason": reason}


def qualify(row, *, now=None):
    """Apply Connect policy to trusted DB metadata, never caller-provided IDs.

    Exhausted status 4 is allowed (view/stop must not depend on model quota).
    Disabled/expired/unknown statuses are denied even if quota is available.
    """
    if not isinstance(row, dict) or not _id(row.get("user_id")) or not _id(row.get("token_id")):
        return _denied("invalid_key")
    if row.get("token_deleted_at") is not None or row.get("user_deleted_at") is not None:
        return _denied("invalid_key")
    token_status = row.get("token_status")
    if type(token_status) is not int or token_status not in (1, 4):
        return _denied("token_expired" if token_status == 3 else "token_disabled")
    expiry = row.get("expired_time")
    if type(expiry) is not int or (expiry != -1 and expiry <= (time.time() if now is None else now)):
        return _denied("token_expired")
    if type(row.get("user_status")) is not int or row["user_status"] != 1:
        return _denied("user_disabled")
    if row.get("remote_connect_enabled", True) is not True:
        return _denied("not_entitled")
    return {"eligible": True, "user_id": row["user_id"], "token_id": row["token_id"]}


class SQLiteLookup:
    """Open existing New API SQLite DB read-only for each narrowly scoped SELECT.

    The DB directory must also expose existing WAL/SHM sidecars; do not use
    immutable=1 (it can silently ignore live WAL updates). No write/checkpoint,
    quota update, schema mutation or credential enumeration is performed.
    """
    SQL = """SELECT t.id AS token_id, t.user_id AS user_id,
        t.status AS token_status, t.expired_time AS expired_time,
        t.deleted_at AS token_deleted_at, u.status AS user_status,
        u.deleted_at AS user_deleted_at
        FROM tokens AS t JOIN users AS u ON u.id = t.user_id
        WHERE t.key = ? LIMIT 1"""
    INTROSPECT_SQL = SQL.replace("WHERE t.key = ? LIMIT 1", "WHERE t.id = ? AND t.user_id = ? LIMIT 1")

    def __init__(self, path, *, allowed_user_ids=None):
        self.path = Path(path).resolve()
        self.allowed_user_ids = None if allowed_user_ids is None else frozenset(allowed_user_ids)

    def __call__(self, api_key):
        if not valid_key(api_key):
            return None
        # Exactly remove public prefix, never accept channel-suffixed credentials.
        key = api_key[3:] if api_key.startswith("sk-") else api_key
        return self._query(self.SQL, (key,))

    def introspect(self, user_id, token_id):
        """Recheck known ownership/status without retaining the enrollment key."""
        if not _id(user_id) or not _id(token_id):
            return None
        return self._query(self.INTROSPECT_SQL, (token_id, user_id))

    def _query(self, sql, parameters):
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.row_factory = sqlite3.Row
            row = connection.execute(sql, parameters).fetchone()
            if row is None:
                return None
            value = dict(row)
            if self.allowed_user_ids is not None:
                value["remote_connect_enabled"] = value["user_id"] in self.allowed_user_ids
            return value
        finally:
            connection.close()


def _endpoint(value, allow_loopback_http):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value) or "\\" in value:
        raise ValueError("Invalid fixed eligibility endpoint")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise ValueError("Invalid fixed eligibility endpoint") from None
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("/eligible", "/_connect/eligible")):
        raise ValueError("Eligibility endpoint must be a fixed /eligible or /_connect/eligible path without credentials or query")
    if parsed.scheme == "https":
        return value
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = False
    if parsed.scheme == "http" and allow_loopback_http and loopback:
        return value
    raise ValueError("Eligibility endpoint requires HTTPS or explicitly enabled numeric-loopback HTTP")


class EligibilityClient:
    """Fixed trusted endpoint client. Redirects, proxies, cookies are disabled."""
    def __init__(self, endpoint, secret, *, allow_loopback_http=False, timeout=10):
        self.endpoint = _endpoint(endpoint, allow_loopback_http)
        self.secret = _secret(secret)
        self.timeout = timeout

    async def __call__(self, api_key):
        if not valid_key(api_key):
            return _denied("invalid_key")
        return await self._post(self.endpoint, {"apiKey": api_key})

    async def introspect(self, user_id, token_id):
        if not _id(user_id) or not _id(token_id):
            return _denied("invalid_key")
        value = await self._post(self.endpoint.rsplit("/", 1)[0] + "/introspect",
                                 {"userId": user_id, "tokenId": token_id})
        if value.get("eligible") and (value.get("user_id") != user_id or value.get("token_id") != token_id):
            return _denied("unavailable")
        return value

    async def _post(self, endpoint, body):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout),
                                             trust_env=False, cookie_jar=aiohttp.DummyCookieJar()) as session:
                async with session.post(endpoint, json=body,
                        headers={"Authorization": "Bearer " + self.secret}, allow_redirects=False) as response:
                    if response.status != 200:
                        return _denied("unavailable")
                    if response.content_type != "application/json":
                        return _denied("unavailable")
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(1024):
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE:
                            return _denied("unavailable")
                    value = json.loads(data)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
            return _denied("unavailable")
        if not isinstance(value, dict) or type(value.get("eligible")) is not bool:
            return _denied("unavailable")
        if value["eligible"]:
            if not _id(value.get("user_id")) or not _id(value.get("token_id")):
                return _denied("unavailable")
            return {"eligible": True, "user_id": value["user_id"], "token_id": value["token_id"]}
        # No arbitrary adapter text/key/error can escape into a relay response.
        reason = value.get("reason")
        return _denied(reason if isinstance(reason, str) and reason in REASONS else "invalid_key")


def create_adapter(lookup, secret, *, introspect=None):
    """Create a private RPC app; lookup is injected for tests or trusted DB IO."""
    secret = _secret(secret)
    app = web.Application(client_max_size=MAX_REQUEST)

    async def eligible(request):
        auth = request.headers.get("Authorization", "")
        if not auth.isascii() or not hmac.compare_digest(auth, "Bearer " + secret):
            return web.json_response({"error": "unauthorized"}, status=401)
        if request.content_type != "application/json":
            return web.json_response({"error": "invalid_request"}, status=400)
        try:
            value = await request.json()
        except (ValueError, web.HTTPRequestEntityTooLarge):
            return web.json_response({"error": "invalid_request"}, status=400)
        if request.path == "/introspect":
            if (not isinstance(value, dict) or set(value) != {"userId", "tokenId"}
                    or not _id(value.get("userId")) or not _id(value.get("tokenId"))):
                return web.json_response({"error": "invalid_request"}, status=400)
            operation = introspect or getattr(lookup, "introspect", None)
            arguments = (value["userId"], value["tokenId"])
            if not callable(operation):
                return web.json_response(_denied("unavailable"), status=503)
        elif not isinstance(value, dict) or set(value) != {"apiKey"}:
            return web.json_response({"error": "invalid_request"}, status=400)
        else:
            key = value["apiKey"]
            if not valid_key(key):
                return web.json_response(_denied("invalid_key"))
            operation, arguments = lookup, (key,)
        try:
            # Keep SQLite/DB IO off the network event loop. No exception payload
            # is returned or logged (drivers may include bound credentials).
            if inspect.iscoroutinefunction(operation):
                row = await operation(*arguments)
            else:
                row = await asyncio.to_thread(operation, *arguments)
                if inspect.isawaitable(row):
                    row = await row
            result = qualify(row)
        except Exception:
            return web.json_response(_denied("unavailable"), status=503)
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    app.router.add_post("/eligible", eligible)
    app.router.add_post("/introspect", eligible)
    return app


def main():
    parser = argparse.ArgumentParser(description="Private read-only New API Connect qualification adapter")
    parser.add_argument("--db", "--sqlite", dest="sqlite", required=True, help="Existing New API SQLite database path (read-only)")
    parser.add_argument("--secret-file", help="Private adapter RPC secret file; alternative to CONNECT_ELIGIBILITY_SECRET")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18789)
    parser.add_argument("--allow-container-bind", action="store_true", help="Explicit container-only 0.0.0.0 bind; publish host port on loopback only")
    parser.add_argument("--allowed-user-ids", help="Optional comma-separated entitlement allowlist; default all active accounts")
    args = parser.parse_args()
    try:
        if not ipaddress.ip_address(args.host).is_loopback and not (args.allow_container_bind and args.host == "0.0.0.0"):
            raise ValueError()
    except ValueError:
        parser.error("Adapter must bind numeric loopback, or explicitly --allow-container-bind on 0.0.0.0 with loopback-only Docker publishing")
    try:
        secret = (Path(args.secret_file).read_text(encoding="utf-8").strip() if args.secret_file
                  else os.environ.get("CONNECT_ELIGIBILITY_SECRET", ""))
        secret = _secret(secret)
        ids = [int(value) for value in args.allowed_user_ids.split(",")] if args.allowed_user_ids is not None else None
        if ids is not None and any(not _id(value) for value in ids):
            raise ValueError()
    except (OSError, ValueError):
        parser.error("Set a private CONNECT_ELIGIBILITY_SECRET (32+ ASCII characters) and valid entitlement IDs")
    web.run_app(create_adapter(SQLiteLookup(args.sqlite, allowed_user_ids=ids), secret),
                host=args.host, port=args.port, access_log=None, print=None)


if __name__ == "__main__":
    main()
