#!/usr/bin/env python3
"""Opt-in staging smoke against the fixed production origin; no model calls.

The test model key is supplied via getpass (never argv/files/stdout). It registers
one disposable device, exercises an explicitly synthetic desktop WS, then revokes
the phone/device in finally. This does NOT verify real Codex IPC or a physical phone.
"""
import asyncio
import base64
import getpass
import json

import aiohttp

ORIGIN = 'https://codex.mhenwa.cc'


async def smoke(key):
    device_token = None
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar()) as device, \
            aiohttp.ClientSession(timeout=timeout, trust_env=False) as phone:

        async def request(client, method, path, value=None, headers=None, expected=200):
            async with client.request(method, ORIGIN + path, json=value,
                    headers=headers, allow_redirects=False) as response:
                data = await response.json()
                if response.status != expected:
                    print('SMOKE_HTTP_MISMATCH=' + str(response.status) + '; EXPECTED=' + str(expected)
                        + '; ROUTE=' + ('register' if path == '/connect/register' else 'fixed-test-operation'), flush=True)
                    raise AssertionError('Unexpected HTTP status for fixed smoke operation')
                return data

        try:
            result = await request(device, 'POST', '/connect/register',
                {'apiKey': key, 'deviceName': 'Connect staging synthetic desktop'}, expected=201)
            device_token = result['deviceToken']
            assert device_token != key
            key_auth = {'Authorization': 'Bearer ' + key}
            rejected = False
            try:
                async with device.ws_connect(ORIGIN + '/connect/device/ws', headers=key_auth):
                    pass
            except aiohttp.WSServerHandshakeError as error:
                rejected = error.status == 401
            assert rejected
            key = None
            print('MODEL_KEY_ENROLLMENT=PASS; MODEL_KEY_AS_DEVICE_AUTH=401', flush=True)
            auth = {'Authorization': 'Bearer ' + device_token}
            async with device.ws_connect(ORIGIN + '/connect/device/ws', headers=auth,
                    heartbeat=20, compress=0) as ws:
                pair = await request(device, 'POST', '/connect/device/pair', {}, auth)
                token = pair['url'].split('#connect_pair=', 1)[1]
                origin = {'Origin': ORIGIN}
                claim = await request(phone, 'POST', '/connect/pair/claim',
                    {'token': token, 'phoneName': 'Staging synthetic phone'}, origin)
                token = None
                status = await request(phone, 'POST', '/connect/pair/status', claim, origin)
                assert status['state'] == 'claimed'
                await request(phone, 'GET', '/api/sessions', expected=401)
                print('PAIR_BEFORE_DESKTOP_APPROVAL=UNAUTHORIZED', flush=True)
                await request(device, 'POST', '/connect/device/pairings/' + pair['pairingId'] + '/approve',
                    {'approved': True}, auth)
                approved = await request(phone, 'POST', '/connect/pair/status', claim, origin)
                assert approved['state'] == 'approved'
                phone_auth = await request(phone, 'GET', '/api/auth')
                assert phone_auth['authenticated'] is True and phone_auth['transport'] == 'poll'
                print('DESKTOP_APPROVAL_TO_INDEPENDENT_PHONE_COOKIE=PASS', flush=True)

                async def reply():
                    message = await ws.receive_json(timeout=15)
                    assert message['type'] == 'request' and message['method'] == 'GET'
                    assert message['path'] == '/api/sessions'
                    body = json.dumps({'sessions': [], 'fixture': 'synthetic-desktop-only'}).encode()
                    await ws.send_json({'type': 'response', 'id': message['id'], 'status': 200,
                        'body': base64.b64encode(body).decode(), 'contentType': 'application/json'})
                responder = asyncio.create_task(reply())
                sessions = await request(phone, 'GET', '/api/sessions')
                await responder
                assert sessions['fixture'] == 'synthetic-desktop-only'
                print('HTTPS_PHONE_TO_WSS_SYNTHETIC_DESKTOP=PASS', flush=True)
                phones = await request(device, 'GET', '/connect/device/phones', headers=auth)
                assert len(phones['phones']) == 1
                await request(device, 'POST', '/connect/device/phones/' + phones['phones'][0]['id'] + '/revoke', {}, auth)
                await request(phone, 'GET', '/api/sessions', expected=401)
                print('PHONE_REVOKE=401', flush=True)
        finally:
            key = None
            if device_token:
                await request(device, 'POST', '/connect/device/revoke', {},
                    {'Authorization': 'Bearer ' + device_token})
                print('DISPOSABLE_DEVICE_REVOKED=PASS', flush=True)
    print('LIVE_STAGING_SMOKE=PASS; REAL_CODEX_IPC=NOT_TESTED; PHYSICAL_PHONE=NOT_TESTED; MODEL_CALLS=0', flush=True)


if __name__ == '__main__':
    try:
        secret = getpass.getpass('Private test key (hidden): ')
        asyncio.run(smoke(secret))
        secret = None
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        # No exception repr/payload/headers/credentials escape the evidence ledger.
        print('LIVE_STAGING_SMOKE=FAIL; ERROR_TYPE=' + type(error).__name__, flush=True)
        raise SystemExit(1)
