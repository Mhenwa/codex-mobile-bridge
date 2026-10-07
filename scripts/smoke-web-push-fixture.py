"""Loopback-only synthetic relay for the real Chromium Web Push smoke.

No fixture route is installed by the product. Credentials and push encryption
keys are ephemeral; the mock sender cannot contact a real push provider.
"""
import argparse
import asyncio
import base64
import hmac
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from connect.relay import COOKIE, STATE, create_app
from connect.registry import Registry


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--control-token", required=True)
    args = parser.parse_args()
    origin = "http://127.0.0.1:" + str(args.port)
    registry = Registry(Path(args.data_dir) / "connect.sqlite3")
    device = registry.register(1, 1, "Synthetic smoke computer")
    pairing = registry.pairing(device["deviceId"])
    claim = registry.claim(pairing["token"], "Synthetic smoke phone")
    registry.approve(device["deviceId"], pairing["pairingId"], True)
    _, token = registry.claim_status(claim["claimToken"])
    phone = registry.phone(token)
    sent = []

    async def sender(subscription, payload):
        sent.append({"payload": payload, "endpoint": subscription["endpoint"]})
        return 201

    app = create_app(origin, args.data_dir, lambda _: {"eligible": True, "user_id": 1, "token_id": 1},
                     registry=registry, push_sender=sender, push_auto_send=False)
    state = app[STATE]

    @web.middleware
    async def fixture(request, handler):
        if request.path.startswith("/__fixture/"):
            if (request.remote != "127.0.0.1" or not hmac.compare_digest(
                    request.headers.get("X-Fixture-Token", ""), args.control_token)):
                raise web.HTTPForbidden()
            if request.path == "/__fixture/flush" and request.method == "POST":
                await state.push.deliver(state.check_qualification)
            elif request.path != "/__fixture/state" or request.method != "GET":
                raise web.HTTPNotFound()
            return web.json_response({"status": state.push.status(phone["id"]), "sent": sent,
                                      "pending": registry.db.execute("SELECT count(*) FROM push_outbox").fetchone()[0]})
        # The real authentication and all /connect/push handlers remain intact.
        # Only unrelated desktop data is synthetic so the mobile UI can load.
        if request.path == "/api/sessions":
            await state.phone_auth(request)
            return web.json_response({"sessions": [], "unavailableHosts": []})
        if request.path == "/api/accounts":
            await state.phone_auth(request)
            return web.json_response({"accounts": [], "current": None})
        thread = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/(timeline|changes|reconnect|notifications)", request.path)
        if thread:
            await state.phone_auth(request, write=request.method != "GET")
            identifier, action = thread.groups()
            if action == "reconnect":
                return web.json_response({"ok": True})
            if action == "notifications":
                return web.json_response({"available": False, "watching": False, "notifyOnCompletion": False})
            if action == "changes":
                await asyncio.sleep(0.25)
            meta = {"id": identifier, "title": "Fixture chat " + identifier, "host": "local", "cwd": "Synthetic project",
                    "connected": True, "status": "idle", "requests": [], "submissions": [], "historyComplete": True,
                    "model": "fixture-model", "provider": "fixture-provider", "collaborationMode": "default"}
            return web.json_response({"epoch": "fixture", "sequence": 1, "meta": meta, "rows": [],
                                      "files": [], "before": None, "hasMore": False})
        return await handler(request)

    app.middlewares.append(fixture)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", args.port).start()
    public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    print(json.dumps({"origin": origin, "cookie": {"name": COOKIE, "value": token},
                      "subscription": {"endpoint": "https://fcm.googleapis.com/fcm/send/synthetic-smoke-no-delivery",
                                       "keys": {"p256dh": encode(public), "auth": encode(b"0" * 16)}}}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
