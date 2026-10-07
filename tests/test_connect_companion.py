import asyncio
import base64
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import aiohttp
from aiohttp import web

from bridge.connect import (ConnectController, ConnectError, Connector, NoRedirect, enabled,
                            MAX_CONTROL_RESPONSE, private_json, read_config, relay_origin,
                            start_companion, stop_companion)
from bridge.httpd import GatewayServer

ROOT = Path(__file__).resolve().parents[1]
KEY = 'fixture-model-key-never-a-device-password'
DEVICE = 'fixture-device-token-independent-and-secret'
DEVICE_ID = 'fixture-device-id'
THREAD = '12345678-1234-1234-1234-123456789abc'


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.controller = ConnectController(self.directory, self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def configured(self):
        private_json(self.directory / 'connect.json',
                     {'enabled': True, 'relayUrl': 'https://codex.mhenwa.cc',
                      'deviceId': DEVICE_ID, 'deviceName': 'Fixture', 'deviceToken': DEVICE})

    def test_opt_in_registration_uses_key_once_and_returns_no_secret(self):
        with patch('connect.discovery.select_key', return_value=KEY) as select, \
                patch.object(self.controller, 'request', return_value={'deviceId': DEVICE_ID, 'deviceToken': DEVICE}) as call:
            with self.assertRaisesRegex(ValueError, '明确同意'):
                self.controller.control({'action': 'register', 'provider': 'mhenwa'})
            select.assert_not_called()
            value = self.controller.control({'action': 'register', 'provider': 'mhenwa',
                                             'consent': True, 'deviceName': 'Fixture'})
            call.assert_called_once_with('POST', '/connect/register',
                                         {'apiKey': KEY, 'deviceName': 'Fixture'}, device=False)
            self.assertTrue(value['enabled'])
            self.assertNotIn(KEY, json.dumps(value))
            self.assertNotIn(DEVICE, json.dumps(value))
            cfg = read_config(self.directory)
            self.assertEqual(cfg['deviceToken'], DEVICE)
            self.assertNotIn(KEY, (self.directory / 'connect.json').read_text())
            if os.name != 'nt':
                self.assertEqual((self.directory / 'connect.json').stat().st_mode & 0o777, 0o600)

    def test_duplicate_registration_retains_original_identity(self):
        self.configured()
        with patch('connect.discovery.select_key') as select:
            with self.assertRaises(ValueError):
                self.controller.control({'action': 'register', 'consent': True})
            select.assert_not_called()
        self.assertEqual(read_config(self.directory)['deviceToken'], DEVICE)

    def test_controller_rejects_endpoint_injection_and_bad_approval_id(self):
        self.configured()
        with self.assertRaises(ValueError):
            self.controller.control({'action': 'approve', 'id': '../admin', 'approved': True})
        with self.assertRaises(ValueError):
            self.controller.control({'action': 'approve', 'id': 'fixture-valid-id'})
        for url in ['http://codex.mhenwa.cc', 'https://x:y@codex.mhenwa.cc',
                    'https://codex.mhenwa.cc/a', 'https://codex.mhenwa.cc#x',
                    'https://codex.mhenwa.cc?x', 'file:///tmp/x']:
            with self.assertRaises(ValueError, msg=url):
                relay_origin(url)
        self.assertEqual(relay_origin('http://127.0.0.1:9988', True), 'http://127.0.0.1:9988')
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))

    def test_disable_fails_closed_locally_and_removes_credential(self):
        self.configured()
        with patch.object(self.controller, 'request', side_effect=ValueError('network unavailable')):
            value = self.controller.control({'action': 'disable'})
        self.assertFalse(value['enabled'])
        self.assertFalse(value['remoteRevoked'])
        self.assertFalse(enabled(self.directory))
        cfg = read_config(self.directory)
        self.assertNotIn('deviceToken', cfg)
        self.assertEqual(cfg['pendingRevocations'][0]['deviceToken'], DEVICE)
        self.assertNotIn(DEVICE, json.dumps(value))
        self.assertEqual(value['pendingRevocations'], 1)

    def test_disable_closes_local_gate_before_remote_rpc(self):
        self.configured()
        def revoke(*args, **kwargs):
            self.assertFalse(enabled(self.directory))
            self.assertEqual(kwargs['_config']['deviceToken'], DEVICE)
            return {'ok': True}
        with patch.object(self.controller, 'request', side_effect=revoke):
            result = self.controller.control({'action': 'disable'})
        self.assertTrue(result['remoteRevoked'])

    def test_repeated_offline_disable_retains_pending_and_retries_before_new_registration(self):
        self.configured()
        with patch.object(self.controller, 'request', side_effect=ValueError('offline')):
            self.controller.control({'action': 'disable'})
            self.controller.control({'action': 'disable'})
        self.assertEqual(len(read_config(self.directory)['pendingRevocations']), 1)
        calls = []
        def request(method, path, value=None, **kwargs):
            calls.append(path)
            if path.endswith('/revoke'):
                self.assertFalse(enabled(self.directory))
                self.assertEqual(kwargs['_config']['deviceToken'], DEVICE)
                return {'ok': True}
            return {'deviceId': 'fixture-new-device-id', 'deviceToken': 'new-independent-device-token-fixture'}
        with patch.object(self.controller, 'request', side_effect=request), \
                patch('connect.discovery.select_key', return_value=KEY):
            result = self.controller.control({'action': 'register', 'consent': True,
                                             'provider': 'fixture', 'deviceName': 'Fixture'})
        self.assertEqual(calls, ['/connect/device/revoke', '/connect/register'])
        self.assertEqual(result['pendingRevocations'], 0)
        self.assertEqual(read_config(self.directory)['pendingRevocations'], [])
        self.assertNotIn(DEVICE, (self.directory / 'connect.json').read_text())

    def test_offline_cleanup_does_not_block_new_qualified_registration_or_leak_pending(self):
        self.configured()
        with patch.object(self.controller, 'request', side_effect=ValueError('offline')):
            self.controller.control({'action': 'disable'})
        def request(method, path, value=None, **kwargs):
            if path.endswith('/revoke'):
                raise ValueError('still offline')
            return {'deviceId': 'fixture-new-device-id', 'deviceToken': 'new-independent-device-token-fixture'}
        with patch.object(self.controller, 'request', side_effect=request), \
                patch('connect.discovery.select_key', return_value=KEY):
            result = self.controller.control({'action': 'register', 'consent': True,
                                             'provider': 'fixture', 'deviceName': 'Fixture'})
        self.assertTrue(result['enabled'])
        self.assertEqual(result['pendingRevocations'], 1)
        self.assertIn('额度', result['message'])
        self.assertNotIn(DEVICE, json.dumps(result))
        self.assertNotIn(KEY, json.dumps(result))

    def test_revoke_already_invalid_device_is_idempotent_and_clears_pending(self):
        self.configured()
        with patch.object(self.controller, 'request', side_effect=ConnectError('already invalid', 401)):
            result = self.controller.control({'action': 'disable'})
        self.assertTrue(result['remoteRevoked'])
        self.assertEqual(result['pendingRevocations'], 0)

    def test_pending_disabled_identity_never_starts_outbound_network(self):
        self.configured()
        with patch.object(self.controller, 'request', side_effect=ValueError('offline')):
            self.controller.control({'action': 'disable'})
        with patch('bridge.httpd.GatewayServer') as listener, patch.object(Connector, 'start') as transport:
            self.assertIsNone(start_companion(self.directory, Mock()))
            listener.assert_not_called()
            transport.assert_not_called()

    def test_status_does_not_echo_untrusted_secret_fields(self):
        self.configured()
        private_json(self.directory / 'connect-status.json',
                     {'deviceId': DEVICE_ID, 'state': 'online', 'deviceToken': DEVICE, 'apiKey': KEY})
        value = self.controller.status()
        self.assertEqual(value['state'], 'online')
        self.assertEqual(self.controller.status(gateway_running=False)['state'], 'stopped')
        self.assertNotIn(KEY, json.dumps(value))
        self.assertNotIn(DEVICE, json.dumps(value))

    def test_phone_lists_strip_extra_secrets(self):
        self.configured()
        with patch.object(self.controller, 'request', return_value={
                'phones': [{'id': 'fixture-phone', 'name': 'Phone', 'expires': 3, 'token': KEY}]}):
            result = self.controller.control({'action': 'phones'})
        self.assertEqual(set(result['phones'][0]), {'id', 'name', 'expires'})
        self.assertNotIn(KEY, json.dumps(result))

    def test_pair_does_not_accept_external_claim_url(self):
        self.configured()
        with patch.object(self.controller, 'request', return_value={'url': 'https://evil.invalid/#connect_pair=fixture'}):
            with self.assertRaises(ValueError):
                self.controller.control({'action': 'pair'})

    def test_http_errors_never_return_upstream_body_or_key(self):
        response = HTTPError('https://codex.mhenwa.cc/connect/register', 403, KEY, {}, None)
        with patch('bridge.connect.build_opener') as opener:
            opener.return_value.open.side_effect = response
            with self.assertRaises(ValueError) as exc:
                self.controller.request('POST', '/connect/register', {'apiKey': KEY}, device=False)
        self.assertNotIn(KEY, str(exc.exception))

    def test_registration_and_device_commands_send_explicit_client_user_agent(self):
        with patch('connect.discovery.select_key', return_value=KEY), \
                patch('bridge.connect.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps({'deviceId': DEVICE_ID, 'deviceToken': DEVICE}).encode()
            self.controller.control({'action': 'register', 'consent': True,
                                     'provider': 'fixture', 'deviceName': 'Fixture'})
            registration = opener.return_value.open.call_args.args[0]
            self.assertEqual(registration.get_header('User-agent'), 'MhenwaConnect/1.4.0')
            self.assertIsNone(registration.get_header('Authorization'))
            self.assertEqual(json.loads(registration.data)['apiKey'], KEY)

            response.read.return_value = b'{"phones": []}'
            self.controller.control({'action': 'phones'})
            device = opener.return_value.open.call_args.args[0]
            self.assertEqual(device.get_header('User-agent'), 'MhenwaConnect/1.4.0')
            self.assertEqual(device.get_header('Authorization'), 'Bearer ' + DEVICE)
            self.assertEqual(device.full_url, 'https://codex.mhenwa.cc/connect/device/phones')
            self.assertIsNone(device.data)
            self.assertNotIn(KEY, str(device.headers))

    def forbidden_response(self, body, content_type):
        headers = Message()
        headers['Content-Type'] = content_type
        return HTTPError('https://codex.mhenwa.cc/connect/register', 403,
                         KEY, headers, BytesIO(body))

    def test_known_json_eligibility_denial_is_bounded_and_sanitized(self):
        body = json.dumps({'error': 'model credential is not eligible for this service',
                           'apiKey': KEY, 'deviceToken': DEVICE}).encode()
        response = self.forbidden_response(body, 'application/json; charset=utf-8')
        with patch('bridge.connect.build_opener') as opener, \
                patch.object(response, 'read', wraps=response.read) as read:
            opener.return_value.open.side_effect = response
            with self.assertRaises(ConnectError) as exc:
                self.controller.request('POST', '/connect/register', {'apiKey': KEY}, device=False)
        self.assertEqual(exc.exception.status, 403)
        self.assertIn('资格', str(exc.exception))
        self.assertNotIn(KEY, str(exc.exception))
        self.assertNotIn(DEVICE, str(exc.exception))
        read.assert_called_once_with(MAX_CONTROL_RESPONSE + 1)

    def test_front_network_and_unknown_forbidden_responses_are_not_eligibility_denials(self):
        known = b'{"error":"model credential is not eligible for this service"}'
        cases = [
            ('text/html', ('<html>Access denied ' + KEY + ' ' + DEVICE + '</html>').encode()),
            ('text/plain', ('error code: 403 ' + KEY + ' ' + DEVICE).encode()),
            ('text/plain', known),
            ('application/json', json.dumps({'error': KEY, 'deviceToken': DEVICE}).encode()),
            ('application/json', ('invalid-json ' + KEY + ' ' + DEVICE).encode()),
            ('application/json', json.dumps([KEY, DEVICE]).encode()),
            ('application/json', known + b' ' * MAX_CONTROL_RESPONSE),
        ]
        for content_type, body in cases:
            with self.subTest(content_type=content_type, size=len(body)):
                response = self.forbidden_response(body, content_type)
                with patch('bridge.connect.build_opener') as opener:
                    opener.return_value.open.side_effect = response
                    with self.assertRaises(ConnectError) as exc:
                        self.controller.request('POST', '/connect/register', {'apiKey': KEY}, device=False)
                message = str(exc.exception)
                self.assertEqual(exc.exception.status, 403)
                self.assertNotIn('资格', message)
                self.assertNotIn(KEY, message)
                self.assertNotIn(DEVICE, message)


class ForwardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        private_json(self.directory / 'connect.json',
                     {'enabled': True, 'relayUrl': 'https://codex.mhenwa.cc',
                      'deviceId': DEVICE_ID, 'deviceToken': DEVICE})
        self.bridge = SimpleNamespace(host_errors=[], list=Mock(return_value=[{'id': THREAD}]),
                                      send=Mock(return_value={'accepted': True}))
        self.bridge.for_host = lambda host: self.bridge
        self.server = GatewayServer(('127.0.0.1', 0), self.bridge,
                                    {'auth': {'mode': 'password', 'sessionHours': 1}, 'origins': []}, ROOT / 'web')
        self.connector = Connector(self.directory, self.server)
        self.listener = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.listener.start()
        self.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3))

    async def asyncTearDown(self):
        await self.client.close()
        self.server.shutdown()
        self.listener.join(2)
        self.server.server_close()
        self.temp.cleanup()

    def message(self, method='GET', path='/api/sessions', value=None):
        raw = json.dumps(value).encode() if value is not None else b''
        return {'type': 'request', 'id': 'fixture-request', 'method': method, 'path': path,
                'body': base64.b64encode(raw).decode(), 'contentType': 'application/json'}

    def body(self, result):
        return json.loads(base64.b64decode(result['body']))

    async def test_real_authenticated_gateway_get_and_matching_csrf_write(self):
        result = await self.connector.forward(self.client, self.message())
        self.assertEqual(result['status'], 200)
        self.assertEqual(self.body(result)['sessions'], [{'id': THREAD}])
        result = await self.connector.forward(self.client, self.message('POST', '/api/sessions/' + THREAD + '/send',
                                                                      {'text': 'fixture', 'id': 'stable-submission-id'}))
        self.assertEqual(result['status'], 200)
        self.assertTrue(self.body(result)['accepted'])
        self.assertEqual(self.bridge.send.call_args.args[2], 'stable-submission-id')
        self.assertNotIn(self.connector.token, json.dumps(result))
        self.assertNotIn(self.connector.session['csrf'], json.dumps(result))
        self.assertNotIn('Set-Cookie', result)

    async def test_double_allowlist_blocks_management_arbitrary_hosts_and_sse(self):
        paths = ['/api/accounts', '/api/accounts/switch', '/api/login', '/api/auth', '/api/health',
                 '/api/accounts/ignore-submission',
                 '/api/pair', '/api/logout', '/api/notification-settings',
                 '/api/sessions/' + THREAD + '/events', 'http://evil.invalid/api/sessions',
                 '/api/sessions/' + THREAD + '/catalog?host=local&host=other',
                 '/api/sessions/' + THREAD + '/catalog?' + '&'.join('id=skill-' + str(i) for i in range(9)),
                 '//evil.invalid/api/sessions', '/api/%2e%2e/admin', '/api/sessions?deviceId=another']
        for path in paths:
            result = await self.connector.forward(self.client, self.message(path=path))
            self.assertEqual(result['status'], 403, path)
        self.bridge.list.assert_not_called()

    async def test_disabled_device_stops_new_operations(self):
        private_json(self.directory / 'connect.json', {'enabled': False})
        result = await self.connector.forward(self.client, self.message())
        self.assertEqual(result['status'], 503)
        self.bridge.list.assert_not_called()

    async def test_expired_internal_session_is_rotated_privately(self):
        original = self.connector.token
        self.connector.session['expires'] = 1
        result = await self.connector.forward(self.client, self.message())
        self.assertEqual(result['status'], 200)
        self.assertNotEqual(self.connector.token, original)

    async def test_invalid_frame_and_oversize_cannot_reach_gateway(self):
        message = self.message()
        message['body'] = 'not-base64!'
        self.assertEqual((await self.connector.forward(self.client, message))['status'], 403)
        self.assertEqual((await self.connector.forward(self.client, []))['status'], 403)
        message = self.message()
        message['method'] = 'CONNECT'
        self.assertEqual((await self.connector.forward(self.client, message))['status'], 403)
        self.bridge.list.assert_not_called()

    async def test_failed_write_returns_unknown_and_is_not_replayed(self):
        class FailedClient:
            request = Mock(side_effect=asyncio.TimeoutError)
        client = FailedClient()
        result = await self.connector.forward(client, self.message('POST', '/api/sessions/' + THREAD + '/send',
                                                                 {'text': 'fixture', 'id': 'stable-id'}))
        self.assertEqual(result['status'], 504)
        self.assertEqual(self.body(result)['code'], 'outcome_unknown')
        client.request.assert_called_once()

    async def test_real_websocket_uses_independent_token_enforces_duplicate_and_stops_on_disable(self):
        done = asyncio.Event()
        results = []
        failures = []
        async def device(request):
            try:
                self.assertEqual(request.headers.get('Authorization'), 'Bearer ' + DEVICE)
                self.assertEqual(request.headers.get('User-Agent'), 'MhenwaConnect/1.4.0')
                self.assertNotIn(KEY, str(request.headers))
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                await ws.send_json(self.message())
                results.append(await ws.receive_json(timeout=3))
                await ws.send_json(self.message())
                results.append(await ws.receive_json(timeout=3))
                private_json(self.directory / 'connect.json', {'enabled': False})
                await ws.receive(timeout=3)
                await ws.close()
                return ws
            except Exception as exc:
                failures.append(exc)
                return web.Response(status=500)
            finally:
                done.set()
        app = web.Application()
        app.router.add_get('/connect/device/ws', device)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        origin = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        private_json(self.directory / 'connect.json',
                     {'enabled': True, 'relayUrl': origin, 'deviceId': DEVICE_ID, 'deviceToken': DEVICE})
        companion = Connector(self.directory, self.server, allow_loopback=True)
        task = asyncio.create_task(companion.run_async())
        try:
            await asyncio.wait_for(done.wait(), 6)
            await asyncio.wait_for(task, 3)
            self.assertEqual(failures, [])
            self.assertEqual([row['status'] for row in results], [200, 429])
            self.bridge.list.assert_called_once()
            self.assertEqual(json.loads((self.directory / 'connect-status.json').read_text())['state'], 'disabled')
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.cleanup()

    async def test_real_websocket_allows_more_than_eight_parallel_requests(self):
        """Observation mode no longer applies the legacy connector task cap."""
        done = asyncio.Event()
        results = []
        failures = []
        entered = threading.Event()
        release = threading.Event()

        def blocked_list(**_kwargs):
            # Hold every gateway operation open until all nine frames have
            # entered the local gateway.  With the old eight-task cap the
            # ninth frame would be rejected before this event could fire.
            if self.bridge.list.call_count >= 9:
                entered.set()
            release.wait(3)
            return [{'id': THREAD}]

        self.bridge.list.side_effect = blocked_list

        async def device(request):
            try:
                self.assertEqual(request.headers.get('Authorization'), 'Bearer ' + DEVICE)
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                for index in range(9):
                    message = self.message()
                    message['id'] = 'fixture-request-' + str(index)
                    await ws.send_json(message)
                for _ in range(9):
                    results.append(await ws.receive_json(timeout=3))
                private_json(self.directory / 'connect.json', {'enabled': False})
                await ws.receive(timeout=3)
                await ws.close()
                return ws
            except Exception as exc:
                failures.append(exc)
                return web.Response(status=500)
            finally:
                done.set()

        app = web.Application()
        app.router.add_get('/connect/device/ws', device)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        origin = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        private_json(self.directory / 'connect.json',
                     {'enabled': True, 'relayUrl': origin, 'deviceId': DEVICE_ID, 'deviceToken': DEVICE})
        companion = Connector(self.directory, self.server, allow_loopback=True)
        task = asyncio.create_task(companion.run_async())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            release.set()
            await asyncio.wait_for(done.wait(), 6)
            await asyncio.wait_for(task, 3)
            self.assertEqual(failures, [])
            self.assertEqual(len(results), 9)
            self.assertEqual([row['status'] for row in results], [200] * 9)
            self.assertEqual(self.bridge.list.call_count, 9)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.cleanup()

    async def test_full_real_relay_desktop_registration_pair_approval_write_and_phone_revoke(self):
        from connect.relay import create_app, COOKIE
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.setblocking(False)
        origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
        keys = []
        async def eligibility(key):
            keys.append(key)
            return {'eligible': key == KEY, 'user_id': 17, 'token_id': 29}
        app = create_app(origin, self.directory / 'relay', eligibility, request_timeout=3)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.SockSite(runner, sock)
        await site.start()
        controller = ConnectController(self.directory, self.directory, relay_url=origin, allow_loopback=True)
        private_json(self.directory / 'connect.json', {'enabled': False})
        task = None
        phone = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        try:
            with patch('connect.discovery.select_key', return_value=KEY):
                registered = await asyncio.to_thread(controller.control, {
                    'action': 'register', 'consent': True, 'provider': 'fixture', 'deviceName': 'Fixture'})
            self.assertEqual(keys, [KEY])
            self.assertTrue(registered['enabled'])
            cfg = read_config(self.directory)
            self.assertNotEqual(cfg['deviceToken'], KEY)
            connector = Connector(self.directory, self.server, allow_loopback=True)
            task = asyncio.create_task(connector.run_async())
            for _ in range(100):
                if controller.status()['state'] == 'online':
                    break
                await asyncio.sleep(.02)
            self.assertEqual(controller.status()['state'], 'online')
            async with phone.get(origin + '/connect/device/ws', headers={'Authorization': 'Bearer ' + KEY}) as response:
                self.assertEqual(response.status, 401)
            # Knowing the model key never creates an authenticated phone session.
            async with phone.get(origin + '/api/sessions') as response:
                self.assertEqual(response.status, 401)
            pair = await asyncio.to_thread(controller.control, {'action': 'pair'})
            token = pair['url'].split('#connect_pair=')[1]
            async with phone.post(origin + '/connect/pair/claim', headers={'Origin': origin},
                                  json={'token': token, 'phoneName': 'Fixture phone'}) as response:
                self.assertEqual(response.status, 200)
                claim = await response.json()
            async with phone.post(origin + '/connect/pair/status', headers={'Origin': origin}, json=claim) as response:
                self.assertEqual((await response.json())['state'], 'claimed')
            async with phone.get(origin + '/api/sessions') as response:
                self.assertEqual(response.status, 401)
            claims = await asyncio.to_thread(controller.control, {'action': 'pairings'})
            self.assertEqual(claims['pairings'][0]['phoneName'], 'Fixture phone')
            await asyncio.to_thread(controller.control, {'action': 'approve',
                                   'id': claims['pairings'][0]['id'], 'approved': True})
            async with phone.post(origin + '/connect/pair/status', headers={'Origin': origin}, json=claim) as response:
                self.assertEqual(response.status, 200)
                approved = await response.json()
                self.assertEqual(approved['state'], 'approved')
                phone_secret = response.cookies[COOKIE].value
                self.assertNotEqual(phone_secret, cfg['deviceToken'])
                self.assertNotEqual(phone_secret, KEY)
            async with phone.get(origin + '/api/auth') as response:
                auth = await response.json()
                self.assertTrue(auth['authenticated'])
                self.assertEqual(auth['transport'], 'poll')
            async with phone.get(origin + '/api/sessions') as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['sessions'], [{'id': THREAD}])
            # Exercise both allowlists and the real v1.4 HTTP handler, not only
            # protocol validation: selected Skill IDs must reach the gateway.
            self.bridge.catalog = Mock(return_value={'kind': 'models', 'models': []})
            async with phone.get(origin + '/api/sessions/' + THREAD + '/catalog?kind=models') as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['kind'], 'models')
            self.bridge.catalog.assert_called_once_with(
                THREAD, refresh=False, kind='models', query='', offset=0, limit=200, ids=[])
            skill_ids = ['skill-' + str(index) for index in range(8)]
            skill_query = '?host=local&kind=skills&q=selected%20skill&offset=200&limit=200&refresh=true'
            skill_query += ''.join('&id=' + value for value in skill_ids)
            self.bridge.catalog.return_value = {'kind': 'skills', 'skills': []}
            async with phone.get(origin + '/api/sessions/' + THREAD + '/catalog' + skill_query) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['kind'], 'skills')
            self.bridge.catalog.assert_called_with(
                THREAD, refresh=True, kind='skills', query='selected skill', offset=200, limit=200, ids=skill_ids)
            async with phone.get(origin + '/api/sessions/' + THREAD + '/catalog' + skill_query + '&id=ninth') as response:
                self.assertEqual(response.status, 400)
            self.assertEqual(self.bridge.catalog.call_count, 2)
            async with phone.post(origin + '/api/sessions/' + THREAD + '/send',
                                  headers={'Origin': origin, 'X-CSRF-Token': approved['csrf']},
                                  json={'text': 'fixture', 'id': 'stable-real-relay-submission'}) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())['accepted'])
            self.assertEqual(self.bridge.send.call_args.args[2], 'stable-real-relay-submission')
            phones = await asyncio.to_thread(controller.control, {'action': 'phones'})
            await asyncio.to_thread(controller.control, {'action': 'revoke-phone', 'id': phones['phones'][0]['id']})
            async with phone.get(origin + '/api/sessions') as response:
                self.assertEqual(response.status, 401)
            # Relay DB stores credential digests, never the model API key.
            self.assertNotIn(KEY.encode(), (self.directory / 'relay' / 'connect.sqlite3').read_bytes())
            await asyncio.to_thread(controller.control, {'action': 'disable'})
            await asyncio.wait_for(task, 3)
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await phone.close()
            await runner.cleanup()


class LifecycleTests(unittest.TestCase):
    def test_production_connector_rejects_untrusted_origin_before_issuing_session(self):
        with tempfile.TemporaryDirectory() as directory:
            private_json(Path(directory) / 'connect.json',
                         {'enabled': True, 'relayUrl': 'https://evil.invalid',
                          'deviceId': DEVICE_ID, 'deviceToken': DEVICE})
            gateway = Mock()
            with self.assertRaisesRegex(ValueError, '可信服务'):
                Connector(directory, gateway)
            gateway.auth.new_session.assert_not_called()

    def test_disabled_companion_does_not_create_listener_or_import_aiohttp(self):
        with tempfile.TemporaryDirectory() as directory, patch('bridge.httpd.GatewayServer') as listener:
            self.assertIsNone(start_companion(directory, Mock()))
            listener.assert_not_called()

    def test_private_listener_preserves_primary_no_auth_and_local_access_config(self):
        with tempfile.TemporaryDirectory() as directory:
            private_json(Path(directory) / 'connect.json',
                         {'enabled': True, 'relayUrl': 'https://codex.mhenwa.cc',
                          'deviceId': DEVICE_ID, 'deviceToken': DEVICE})
            original = GatewayServer(('127.0.0.1', 0), Mock(),
                                     {'auth': {'mode': 'none'}, 'origins': [], 'localAccess': False}, ROOT / 'web')
            try:
                with patch.object(Connector, 'start'):
                    companion = start_companion(directory, original)
                self.assertIsNot(companion.gateway, original)
                self.assertEqual(companion.gateway.server_address[0], '127.0.0.1')
                self.assertEqual(companion.gateway.auth.config['mode'], 'password')
                self.assertIsNone(companion.gateway.auth.path)
                self.assertEqual(original.auth.config['mode'], 'none')
                self.assertFalse(original.local_access)
                self.assertEqual(original.origins, set())
                stop_companion(companion)
                self.assertFalse(companion.local_listener.is_alive())
            finally:
                original.server_close()


if __name__ == '__main__':
    unittest.main()
