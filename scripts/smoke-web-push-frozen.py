#!/usr/bin/env python3
"""Exercise native Web Push events from the frozen executable, without a browser.

The fake desktop uses an isolated named pipe and SQLite database. A loopback-only
CONNECT proxy terminates HTTPS for the compiled relay hostname using an ephemeral
test CA; it never resolves or forwards to that hostname. Connect starts disabled
and is enabled after startup so the unrelated WSS companion cannot contact a real
relay. Only synthetic notification HTTP is exercised; no model is invoked.

Run with the Python environment containing requirements-relay.txt:
    python scripts/smoke-web-push-frozen.py dist/gateway/codex-mobile-gateway.exe
"""
import argparse
import copy
import datetime
import http.server
import json
import os
import re
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests'))
from test_bridge import DesktopFixture, THREAD

RELAY = 'https://codex.mhenwa.cc'
DEVICE_ID = 'frozen-synthetic-device'
DEVICE_TOKEN = 'frozen-synthetic-device-token-no-real-authority'
EVENT_FIELDS = {'id', 'kind', 'threadId', 'host', 'count', 'createdAt'}


def write_json(path, value):
    temporary = path.with_suffix('.smoke-tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def until(read, label, process=None, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise AssertionError(f'Frozen process exited {process.returncode} while waiting for {label}')
        result = read()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError(f'Timed out waiting for {label}')


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def tls_context(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Ephemeral frozen Web Push test')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(minutes=1))
            .not_valid_after(now+datetime.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('codex.mhenwa.cc')]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    certificate = directory/'ephemeral-test-ca.pem'
    private_key = directory/'ephemeral-test-key.pem'
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private_key.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    return context, certificate


def make_proxy(context):
    events, errors, tunnels = [], [], []
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_CONNECT(self):
            if self.path != 'codex.mhenwa.cc:443':
                errors.append('Unexpected proxy target: '+self.path)
                self.send_error(403)
                return
            tunnels.append(self.path)
            self.send_response(200, 'Connection established')
            self.end_headers()
            self.wfile.flush()
            try:
                # Terminate the TLS tunnel on this same loopback socket. There is
                # deliberately no DNS lookup, upstream connection, or forwarding.
                self.connection = context.wrap_socket(self.connection, server_side=True)
                self.rfile = self.connection.makefile('rb')
                self.wfile = self.connection.makefile('wb')
                self.close_connection = False
                while not self.close_connection:
                    self.handle_one_request()
            except (OSError, ValueError) as exc:
                errors.append(type(exc).__name__+': '+str(exc))
            finally:
                self.close_connection = True

        def do_POST(self):
            try:
                assert self.path == '/connect/device/notifications', self.path
                assert self.headers.get('Authorization') == 'Bearer '+DEVICE_TOKEN
                assert self.headers.get('User-Agent', '').startswith('MhenwaConnect/')
                size = int(self.headers.get('Content-Length', '0'))
                assert 0 < size <= 8192
                event = json.loads(self.rfile.read(size))
                assert set(event) == EVENT_FIELDS, event
                assert re.fullmatch(r'[a-f0-9]{64}', event['id']), event
                assert event['kind'] in ('request', 'completion'), event
                assert event['threadId'] == THREAD and event['host'] == 'local', event
                assert type(event['count']) is int and event['count'] == 1, event
                assert type(event['createdAt']) is int and abs(time.time()-event['createdAt']) < 120, event
                events.append(event)
                # No phone is registered in this smoke. Acknowledgement with zero
                # recipients still must persist native deduplication in the EXE.
                self.reply(200, {'ok': True, 'accepted': 0})
            except Exception as exc:
                errors.append(type(exc).__name__+': '+str(exc))
                self.reply(400, {'error': 'synthetic fixture rejected event'})

        def do_GET(self):
            errors.append('Unexpected GET: '+self.path)
            self.reply(403, {'error': 'only synthetic notification POST is allowed'})

        def reply(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker, events, errors, tunnels


def verify(runtime, keep=False):
    if not runtime.is_file() or runtime.suffix.lower() == '.py':
        raise ValueError('Pass an existing frozen gateway executable, not Python source')
    (ROOT/'.tmp').mkdir(exist_ok=True)
    # Exercise explicit CA loading even when the Windows ANSI code page cannot
    # represent the data path, independently of the runner's language setting.
    temporary = Path(tempfile.mkdtemp(prefix='web-push-frozen-中文 \U0001f9ea ', dir=ROOT/'.tmp'))
    process = fixture = proxy = proxy_worker = None
    success = False
    log = None
    try:
        home, data = temporary/'synthetic-codex-home', temporary/'gateway'
        home.mkdir(); data.mkdir()
        fixture = DesktopFixture(home)
        fixture.state.update({'title': 'Synthetic private title must not be sent',
                              'turns': [{'turnId': 'old-completed', 'status': 'completed'},
                                        {'turnId': 'new-run', 'status': 'inProgress'}],
                              'requests': []})
        with closing(sqlite3.connect(home/'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,cwd TEXT,updated_at INT,archived INT,originator TEXT,source TEXT,rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',
                       (THREAD, 'Synthetic saved title', str(home), 1, 0, 'Codex Desktop', 'vscode', 'unused'))
        context, certificate = tls_context(temporary)
        proxy, proxy_worker, events, errors, tunnels = make_proxy(context)
        proxy_url = 'http://127.0.0.1:'+str(proxy.server_port)
        write_json(data/'config.json', {'auth': {'mode': 'none'}, 'origins': [], 'localAccess': False})
        write_json(data/'desktop.json', {'codexHome': str(home), 'ipcPath': fixture.path,
                   'port': free_port(), 'lan': False, 'localAccess': False, 'tunnel': False,
                   'connections': [], 'autoStart': False, 'codexBin': ''})
        write_json(data/'notifications.json', {'enabled': False, 'barkEnabled': False, 'pushplusEnabled': False})
        write_json(data/'notification-policies.json', {'requests': False, 'completion': False,
                   'chats': {'local|'+THREAD: {'requests': 'off', 'completion': 'off'}}})
        write_json(data/'connect.json', {'enabled': False})
        environment = {**os.environ, 'CODEX_HOME': str(home), 'PYINSTALLER_RESET_ENVIRONMENT': '1',
                       'SSL_CERT_FILE': str(certificate), 'SSL_CERT_DIR': str(temporary/'empty-certs'),
                       'HTTPS_PROXY': proxy_url, 'https_proxy': proxy_url,
                       'HTTP_PROXY': proxy_url, 'http_proxy': proxy_url,
                       'ALL_PROXY': proxy_url, 'all_proxy': proxy_url,
                       'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
        (temporary/'empty-certs').mkdir()
        for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY'):
            environment.pop(name, None)
        log = (temporary/'frozen-gateway.log').open('wb')
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        process = subprocess.Popen([str(runtime), 'serve', '--data-dir', str(data)],
                                   cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, **kwargs)
        control = until(lambda: read_json(data/'gateway-control.json'), 'frozen gateway startup', process)
        assert control['pid'] == process.pid
        assert not events and not tunnels
        # Startup has passed the optional-companion gate. The existing native
        # notification scanner supports new identity configuration dynamically.
        write_json(data/'connect.json', {'enabled': True, 'relayUrl': RELAY,
                   'deviceId': DEVICE_ID, 'deviceToken': DEVICE_TOKEN})
        baseline = until(lambda: read_json(data/'notification-completions.json'),
                         'Connect live completion boundary', process)
        assert any(row.get('running') == ['new-run'] for row in baseline.values()), baseline
        assert not events, events
        print('PASS: frozen native notification discovery with no browser and all legacy channels/policies off', flush=True)

        def publish():
            fixture.revision += 1
            fixture.snapshot()

        fixture.state['requests'] = [{'id': 'synthetic-approval',
            'method': 'item/commandExecution/requestApproval',
            'params': {'command': 'Never send this synthetic command body'}}]
        publish()
        until(lambda: len(events) >= 1, 'frozen approval HTTP event', process)
        assert events[0]['kind'] == 'request', events
        until(lambda: any(row.get('delivered') for row in (read_json(data/'notification-delivery.json') or {}).values()),
              'accepted-zero approval persistence', process)
        print('PASS: frozen pending approval reaches loopback HTTPS fixture with device auth and content-free payload', flush=True)

        fixture.state['turns'][-1]['status'] = 'completed'
        publish()
        until(lambda: len(events) >= 2, 'frozen completion HTTP event', process)
        assert events[1]['kind'] == 'completion', events
        assert events[0]['id'] != events[1]['id'], events
        until(lambda: len(read_json(data/'notification-delivery.json') or {}) == 2 and
              all(row.get('delivered') for row in (read_json(data/'notification-delivery.json') or {}).values()),
              'accepted-zero completion persistence', process)
        print('PASS: frozen completion emits only the new native turn; saved completed history is not replayed', flush=True)

        captured = copy.deepcopy(events)
        publish()
        time.sleep(5)
        assert events == captured, events
        ledger = read_json(data/'notification-delivery.json')
        assert len(ledger) == 2 and all(row['delivered'] for row in ledger.values()), ledger
        assert DEVICE_TOKEN not in json.dumps(ledger), ledger
        assert all(key.startswith('connect:') for key in ledger), ledger
        print('PASS: accepted=0 persists stable per-event deduplication across repeated live snapshots', flush=True)

        write_json(data/'connect.json', {'enabled': False})
        fixture.state['requests'].append({'id': 'after-disable',
            'method': 'item/tool/requestUserInput', 'params': {'questions': []}})
        publish()
        time.sleep(5)
        assert events == captured, events
        assert not errors, errors
        assert len(tunnels) == 2, tunnels
        assert not (data/'connect-status.json').exists(), 'unrelated companion unexpectedly started'
        assert all(call['method'] in ('initialize', 'thread-owner-discovery') for call in fixture.requests), fixture.requests
        print('PASS: disabling identity stops further events; no webpage, native write, registration, model or real relay call', flush=True)

        write_json(data/'gateway.stop', control)
        process.wait(timeout=20)
        assert process.returncode == 0, process.returncode
        assert not (data/'gateway-control.json').exists()
        success = True
        print(json.dumps({'ok': True, 'frozenRuntime': str(runtime), 'events': 2,
                          'request': 1, 'completion': 1, 'browserPages': 0,
                          'realRelayCalls': 0, 'realModelCalls': 0, 'shutdown': 'clean'}), flush=True)
    except Exception:
        if log:
            log.flush()
        path = temporary/'frozen-gateway.log'
        if path.is_file():
            print(path.read_text(encoding='utf-8', errors='replace')[-12000:], file=sys.stderr)
        if proxy:
            print(json.dumps({'proxyErrors': errors, 'httpsTunnels': len(tunnels),
                              'receivedEvents': len(events)}, ensure_ascii=True), file=sys.stderr)
        print('Smoke evidence retained at '+str(temporary), file=sys.stderr)
        raise
    finally:
        if process and process.poll() is None:
            control = read_json(temporary/'gateway/gateway-control.json')
            if control:
                write_json(temporary/'gateway/gateway.stop', control)
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=10)
            else:
                process.terminate(); process.wait(timeout=10)
        if fixture:
            fixture.close()
        if proxy:
            proxy.shutdown(); proxy.server_close(); proxy_worker.join(timeout=2)
        if log:
            log.close()
        if success and not keep:
            import shutil
            shutil.rmtree(temporary)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('--keep', action='store_true', help='Keep synthetic data and gateway log after success')
    args = parser.parse_args()
    verify(args.runtime.resolve(), args.keep)
