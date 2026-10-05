"""Qualification is private, read-only, fail-closed, and independent of quota."""
import asyncio
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

from connect.eligibility import EligibilityClient, SQLiteLookup, create_adapter, qualify


SECRET = "fixture-private-adapter-secret-0000000000"
KEY = "sk-fixture-key-never-log"


def row(**values):
    return {"user_id": 19, "token_id": 28, "token_status": 1,
            "expired_time": -1, "user_status": 1,
            "token_deleted_at": None, "user_deleted_at": None, **values}


class PolicyTests(unittest.TestCase):
    def test_stable_owner_ids_only_and_no_key_echo(self):
        result = qualify(row(key=KEY, username="private-owner"))
        self.assertEqual(result, {"eligible": True, "user_id": 19, "token_id": 28})
        self.assertNotIn(KEY, json.dumps(result))

    def test_quota_is_not_remote_eligibility(self):
        self.assertTrue(qualify(row(remain_quota=0, token_status=4))["eligible"])
        self.assertTrue(qualify(row(remain_quota=-1, quota=0))["eligible"])

    def test_disabled_expired_deleted_unentitled_invalid_ids_denied(self):
        for values in ({"token_status": 2}, {"token_status": 3}, {"token_status": 0},
                       {"token_status": True}, {"expired_time": 0}, {"expired_time": "-1"},
                       {"expired_time": 100}, {"user_status": 2}, {"user_status": True},
                       {"token_deleted_at": "2026-01-01"}, {"user_deleted_at": "2026-01-01"},
                       {"remote_connect_enabled": False}, {"user_id": True}, {"token_id": 0}):
            with self.subTest(values=values):
                self.assertFalse(qualify(row(**values), now=100)["eligible"])
        self.assertFalse(qualify(None)["eligible"])
        self.assertTrue(qualify(row(expired_time=101), now=100)["eligible"])


class SQLiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "new-api.db"
        db = sqlite3.connect(self.path)
        db.executescript("""CREATE TABLE users (id INTEGER PRIMARY KEY, status INTEGER, deleted_at TEXT);
            CREATE TABLE tokens (id INTEGER PRIMARY KEY, user_id INTEGER, key TEXT UNIQUE,
                status INTEGER, expired_time INTEGER, deleted_at TEXT, remain_quota INTEGER);
            INSERT INTO users VALUES(19,1,NULL);
            INSERT INTO tokens VALUES(28,19,'fixture-key-never-log',1,-1,NULL,0);""")
        db.commit()
        db.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_narrow_parameterized_read_and_unchanged_bytes(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        lookup = SQLiteLookup(self.path)
        self.assertEqual(qualify(lookup(KEY)), {"eligible": True, "user_id": 19, "token_id": 28})
        self.assertEqual(qualify(lookup(KEY[3:])), {"eligible": True, "user_id": 19, "token_id": 28})
        self.assertIsNone(lookup("sk-' OR 1=1; --"))
        self.assertIsNone(lookup(KEY + "-channel"))
        self.assertNotIn("key", lookup(KEY))
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())

    def test_missing_db_not_created_and_entitlement(self):
        missing = Path(self.temp.name) / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            SQLiteLookup(missing)(KEY)
        self.assertFalse(missing.exists())
        self.assertFalse(qualify(SQLiteLookup(self.path, allowed_user_ids=[])(KEY))["eligible"])
        self.assertTrue(qualify(SQLiteLookup(self.path, allowed_user_ids=[19])(KEY))["eligible"])

    def test_live_revocation_is_seen_without_cache(self):
        lookup = SQLiteLookup(self.path)
        self.assertTrue(qualify(lookup(KEY))["eligible"])
        db = sqlite3.connect(self.path)
        db.execute("UPDATE users SET status=2 WHERE id=19")
        db.commit()
        db.close()
        self.assertEqual(qualify(lookup(KEY))["reason"], "user_disabled")

    def test_introspection_owner_scope_and_expiry_without_api_key(self):
        lookup = SQLiteLookup(self.path)
        self.assertTrue(qualify(lookup.introspect(19, 28))["eligible"])
        self.assertIsNone(lookup.introspect(20, 28))
        self.assertIsNone(lookup.introspect(True, 28))
        db = sqlite3.connect(self.path)
        db.execute("UPDATE tokens SET expired_time=0 WHERE id=28")
        db.commit()
        db.close()
        self.assertEqual(qualify(lookup.introspect(19, 28))["reason"], "token_expired")

    def test_live_wal_state_is_not_ignored(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("UPDATE tokens SET status=2 WHERE id=28")
        db.commit()
        try:
            # Keep writer open: latest revocation may still be in the WAL.
            self.assertTrue(Path(str(self.path) + "-wal").exists())
            self.assertEqual(qualify(SQLiteLookup(self.path)(KEY))["reason"], "token_disabled")
        finally:
            db.close()


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lookups = []

        def lookup(key):
            self.lookups.append(key)
            if key == "sk-error-fixture":
                raise ValueError("DB failure accidentally includes " + key)
            return row() if key == KEY else None

        def introspect(user_id, token_id):
            return row() if user_id == 19 and token_id == 28 else None

        self.server = TestServer(create_adapter(lookup, SECRET, introspect=introspect))
        await self.server.start_server()
        self.client = EligibilityClient(str(self.server.make_url("/eligible")), SECRET, allow_loopback_http=True)

    async def asyncTearDown(self):
        await self.server.close()

    async def test_adapter_client_end_to_end(self):
        self.assertEqual(await self.client(KEY), {"eligible": True, "user_id": 19, "token_id": 28})
        self.assertFalse((await self.client("sk-invalid-fixture"))["eligible"])
        self.assertEqual(await self.client("sk-error-fixture"), {"eligible": False, "reason": "unavailable"})
        self.assertNotIn("sk-error-fixture", json.dumps(await self.client("sk-error-fixture")))

    async def test_adapter_secret_and_caller_ids_cannot_be_spoofed(self):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = self.server.make_url("/eligible")
            async with session.post(url, json={"apiKey": KEY}) as response:
                self.assertEqual(response.status, 401)
            self.assertEqual(self.lookups, [])
            async with session.post(url, json={"apiKey": KEY, "user_id": 999},
                                    headers={"Authorization": "Bearer " + SECRET}) as response:
                self.assertEqual(response.status, 400)
            self.assertEqual(self.lookups, [])
        wrong = EligibilityClient(str(url), "other-private-secret-000000000000000", allow_loopback_http=True)
        self.assertFalse((await wrong(KEY))["eligible"])

    async def test_invalid_key_never_reaches_adapter(self):
        for value in (None, "", "sk-fixture\nsecret", "Bearer x", "sk-fixture\x00secret", "密钥"):
            self.assertFalse((await self.client(value))["eligible"])
        self.assertEqual(self.lookups, [])

    async def test_oversized_request_denied_before_db_lookup(self):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(self.server.make_url("/eligible"),
                                    json={"apiKey": "sk-" + "x" * 20000},
                                    headers={"Authorization": "Bearer " + SECRET}) as response:
                self.assertEqual(response.status, 400)
        self.assertEqual(self.lookups, [])

    async def test_introspection_returns_backend_ownership_not_supplied_ids(self):
        self.assertEqual(await self.client.introspect(19, 28), {"eligible": True, "user_id": 19, "token_id": 28})
        self.assertFalse((await self.client.introspect(20, 28))["eligible"])
        self.assertFalse((await self.client.introspect(True, 28))["eligible"])
        self.assertEqual(self.lookups, [])

    async def test_client_ignores_extra_response_secrets_bad_ids_and_redirects(self):
        hit = []
        app = web.Application()

        async def redirect(request):
            raise web.HTTPFound("/leak")

        async def leak(request):
            hit.append(True)
            return web.json_response({"eligible": True, "user_id": 1, "token_id": 1})

        app.router.add_post("/eligible", redirect)
        app.router.add_get("/leak", leak)
        server = TestServer(app)
        await server.start_server()
        try:
            client = EligibilityClient(str(server.make_url("/eligible")), SECRET, allow_loopback_http=True)
            self.assertFalse((await client(KEY))["eligible"])
            self.assertEqual(hit, [])
        finally:
            await server.close()

        for data, expected in (({"eligible": True, "user_id": 19, "token_id": 28, "key": KEY},
                               {"eligible": True, "user_id": 19, "token_id": 28}),
                              ({"eligible": True, "user_id": True, "token_id": 28}, {"eligible": False, "reason": "unavailable"}),
                              ({"eligible": False, "reason": KEY}, {"eligible": False, "reason": "invalid_key"})):
            app = web.Application()

            async def reply(request, data=data):
                return web.json_response(data)

            app.router.add_post("/eligible", reply)
            server = TestServer(app)
            await server.start_server()
            try:
                client = EligibilityClient(str(server.make_url("/eligible")), SECRET, allow_loopback_http=True)
                self.assertEqual(await client(KEY), expected)
            finally:
                await server.close()

    def test_fixed_transport_only(self):
        for url in ("http://example.com/eligible", "http://localhost/eligible", "https://x/eligible?key=x",
                    "https://u:p@x/eligible", "https://x/other", "https://x/eligible#x", "file:///eligible"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                EligibilityClient(url, SECRET, allow_loopback_http=True)
        with self.assertRaises(ValueError):
            EligibilityClient("http://127.0.0.1/eligible", SECRET)
        with self.assertRaises(ValueError):
            EligibilityClient("https://trusted.example/eligible", "short")


if __name__ == "__main__":
    unittest.main()
