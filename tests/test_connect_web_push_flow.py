"""Real loopback desktop-event -> relay -> push queue, with no open phone page."""
import asyncio
import base64
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession, DummyCookieJar
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from bridge.connect import ConnectController, notification_identity, private_json, read_config
from bridge.notifications import Notifications
from bridge.service import LiveSession
from connect.relay import COOKIE, STATE, create_app

THREAD = '12345678-1234-1234-1234-123456789abc'


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


class WebPushFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_phone_page_receives_completion_and_approval_only_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            origin = f'http://127.0.0.1:{port}'
            delivered = []

            async def sender(subscription, payload):
                delivered.append((subscription, payload))
                return 201

            async def eligibility(_):
                return {'eligible': True, 'user_id': 1, 'token_id': 10}

            app = create_app(origin, directory / 'relay', eligibility,
                             push_sender=sender, push_auto_send=False)
            relay = app[STATE]
            device = relay.registry.register(1, 10, 'Synthetic computer')
            pair = relay.registry.pairing(device['deviceId'])
            claim = relay.registry.claim(pair['token'], 'Synthetic phone')
            relay.registry.approve(device['deviceId'], pair['pairingId'], True)
            result, cookie = relay.registry.claim_status(claim['claimToken'])
            receiver = ec.generate_private_key(ec.SECP256R1())
            subscription = {'endpoint': 'https://fcm.googleapis.com/fcm/send/closed-page-fixture',
                            'expirationTime': None,
                            'keys': {'p256dh': encoded(receiver.public_key().public_bytes(
                                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
                                     'auth': encoded(os.urandom(16))}}
            server = TestServer(app, host='127.0.0.1', port=port)
            await server.start_server()
            manager = None
            try:
                # The phone subscribes, then closes its only HTTP/browser session.
                async with ClientSession(cookie_jar=DummyCookieJar()) as phone:
                    async with phone.post(origin + '/connect/push/subscribe', json={
                            'subscription': subscription,
                            'events': {'requests': True, 'completion': True}}, headers={
                                'Cookie': COOKIE + '=' + cookie, 'Origin': origin,
                                'X-CSRF-Token': result['csrf']}) as response:
                        self.assertEqual(response.status, 200, await response.text())
                data = directory / 'computer'
                config = {'enabled': True, 'relayUrl': origin, **device}
                private_json(data / 'connect.json', config)
                session = LiveSession(THREAD)
                session.connected = True
                session.state = {'title': 'PRIVATE TITLE MUST NOT BE PUSHED', 'requests': [],
                                 'turns': [{'turnId': 'history', 'status': 'completed'},
                                           {'turnId': 'new-run', 'status': 'inProgress'}]}

                class Source:
                    def for_host(self, _):
                        return self

                    def notification_candidates(self):
                        return [{'host': 'local', 'id': THREAD}]

                    def notification_session(self, _):
                        return session

                manager = Notifications(Source(), data)
                manager.defaults({'requests': False, 'completion': False})
                controller = ConnectController(data, relay_url=origin, allow_loopback=True)
                identity = lambda path: notification_identity(read_config(path), origin)
                with patch('bridge.notifications.connect_destination', side_effect=identity), \
                        patch('bridge.connect.ConnectController', return_value=controller):
                    await asyncio.to_thread(manager.scan)
                    await relay.push.deliver(relay.check_qualification)
                    self.assertEqual(delivered, [], 'old completed history is not replayed')
                    session.state['turns'][-1]['status'] = 'completed'
                    await asyncio.to_thread(manager.scan)
                    await relay.push.deliver(relay.check_qualification)
                    self.assertEqual(len(delivered), 1)
                    self.assertIn('完成', delivered[0][1]['title'])
                    session.state['requests'] = [{'id': 'approval-1',
                        'method': 'item/commandExecution/requestApproval',
                        'params': {'command': 'PRIVATE COMMAND MUST NOT BE PUSHED'}}]
                    await asyncio.to_thread(manager.scan)
                    await relay.push.deliver(relay.check_qualification)
                    self.assertEqual(len(delivered), 2)
                    self.assertIn('处理', delivered[1][1]['title'])
                    await asyncio.to_thread(manager.scan)
                    await relay.push.deliver(relay.check_qualification)
                    self.assertEqual(len(delivered), 2, 'duplicate scans do not resend')
                    self.assertTrue(all(payload['url'] == '/#' + THREAD + '~local'
                                        for _, payload in delivered))
                    dump = json.dumps(delivered)
                    for secret in ('PRIVATE TITLE', 'PRIVATE COMMAND', device['deviceToken'], cookie):
                        self.assertNotIn(secret, dump)
                    # Revoking phone authorization removes queued delivery as well.
                    authorized = relay.registry.phone(cookie)
                    relay.registry.revoke_phone(device['deviceId'], authorized['id'])
                    session.state['requests'].append({'id': 'approval-2',
                        'method': 'item/commandExecution/requestApproval', 'params': {}})
                    await asyncio.to_thread(manager.scan)
                    await relay.push.deliver(relay.check_qualification)
                    self.assertEqual(len(delivered), 2)
            finally:
                if manager:
                    manager.close()
                await server.close()


if __name__ == '__main__':
    unittest.main()
