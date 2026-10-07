import http.server
import json
import os
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from bridge.access import read_auth
from bridge.connect import ConnectController
from bridge.notifications import publish
from bridge import tls

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT/'tests/fixtures/tls'


class TLSTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        empty = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'SSL_CERT_FILE': str(empty/'missing.pem'),
                                                   'SSL_CERT_DIR': str(empty), 'NO_PROXY': 'localhost,127.0.0.1'})
        self.environment.start()
        self.received = []
        received = self.received

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"instanceId":"test"}')

            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(FIXTURES/'server.pem', FIXTURES/'server-key.pem')
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'https://localhost:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.environment.stop()
        self.temporary.cleanup()

    def test_frozen_preflight_uses_bundle_and_keeps_hostname_verification(self):
        with patch.object(tls.sys, 'frozen', True, create=True), patch.object(tls, 'BUNDLED_CA', FIXTURES/'ca.pem'):
            context = tls.client_context()
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertEqual(read_auth(self.url)['instanceId'], 'test')
            self.assertNotIn('Authorization', self.received[0])
            self.assertNotIn('Cookie', self.received[0])
            with self.assertRaises(ssl.SSLCertVerificationError):
                read_auth(self.url.replace('localhost', '127.0.0.1'))

    def test_ntfy_https_uses_same_bundle_and_rejects_untrusted_certificates(self):
        config = {'server': self.url, 'topic': 'test'}
        with patch.object(tls.sys, 'frozen', True, create=True), patch.object(tls, 'BUNDLED_CA', FIXTURES/'ca.pem'):
            publish(config, 'Test', 'Local fixture only')
        self.assertEqual(len(self.received), 1)
        with patch.object(tls.sys, 'frozen', False, create=True), self.assertRaises(URLError) as error:
            publish(config, 'Test', 'Must not arrive')
        self.assertIsInstance(error.exception.reason, ssl.SSLCertVerificationError)
        self.assertEqual(len(self.received), 1)

    def test_missing_bundle_fails_closed_and_source_preserves_explicit_trust(self):
        with patch.object(tls, 'BUNDLED_CA', Path(self.temporary.name)/'missing.pem'):
            with patch.object(tls.sys, 'frozen', True, create=True), self.assertRaises(FileNotFoundError):
                tls.client_context()
            with patch.object(tls.sys, 'frozen', False, create=True):
                with self.assertRaises(ssl.SSLCertVerificationError):
                    read_auth(self.url)
                with patch.dict(os.environ, {'SSL_CERT_FILE': str(FIXTURES/'ca.pem')}):
                    self.assertEqual(read_auth(self.url)['instanceId'], 'test')

    def test_connect_https_uses_bundle_and_rejects_untrusted_or_wrong_hostname(self):
        controller = ConnectController(self.temporary.name, relay_url=self.url)
        with patch.object(tls.sys, 'frozen', True, create=True), patch.object(tls, 'BUNDLED_CA', FIXTURES/'ca.pem'):
            self.assertEqual(controller.request('POST', '/connect/device/notifications', {}, device=False), {})
            other = ConnectController(self.temporary.name, relay_url=self.url.replace('localhost', '127.0.0.1'))
            with self.assertRaisesRegex(ValueError, '无法连接 Connect'):
                other.request('POST', '/connect/device/notifications', {}, device=False)
        with patch.object(tls.sys, 'frozen', False, create=True), self.assertRaisesRegex(ValueError, '无法连接 Connect'):
            controller.request('POST', '/connect/device/notifications', {}, device=False)
        self.assertEqual(len(self.received), 1)

    def test_connect_source_honors_unicode_certificate_file(self):
        # OpenSSL's environment path lookup can lose characters outside the
        # Windows ANSI code page; Python must load the explicit Unicode path.
        directory = Path(self.temporary.name)/'custom-ca-\U0001f9ea'
        directory.mkdir()
        certificate = directory/'ca.pem'
        certificate.write_bytes((FIXTURES/'ca.pem').read_bytes())
        controller = ConnectController(self.temporary.name, relay_url=self.url)
        with patch.object(tls.sys, 'frozen', False, create=True), patch.dict(os.environ, {'SSL_CERT_FILE': str(certificate)}):
            self.assertEqual(controller.request('POST', '/connect/device/notifications', {}, device=False), {})
        self.assertEqual(len(self.received), 1)
