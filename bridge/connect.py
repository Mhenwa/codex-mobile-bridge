"""Opt-in Mhenwa Connect companion. Management is private desktop stdio only.

The model key is used once for registration, never as a remote-control password.
Remote operations use an authenticated, self-created loopback GatewayServer;
neither the relay nor a phone can select an HTTP destination or raw IPC method.
"""
import asyncio
import base64
import binascii
import getpass
import json
import os
import random
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

DEFAULT_RELAY = 'https://codex.mhenwa.cc'
CONNECT_USER_AGENT = 'MhenwaConnect/1.3.3'
MAX_CONTROL_RESPONSE = 256 * 1024


class ConnectError(ValueError):
    """Sanitized remote failure with a status for idempotent local cleanup."""
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def relay_origin(value, allow_loopback=False):
    try:
        parsed = urlsplit(value)
        valid = (isinstance(value, str) and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.path and not parsed.query
                 and not parsed.fragment and parsed.scheme == 'https')
        if allow_loopback and parsed.hostname in ('127.0.0.1', 'localhost', '::1'):
            valid = (parsed.scheme == 'http' and not parsed.username and not parsed.password
                     and not parsed.path and not parsed.query and not parsed.fragment) or valid
        if not valid or parsed.port == 0:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Connect 入口必须是可信 HTTPS 源') from None


def private_json(path, value):
    """Atomically persist secrets with owner-only ACL before writing any bytes."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Connect 配置不能是符号链接')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.connect-', dir=path.parent)
    try:
        if os.name == 'nt':
            result = subprocess.run(['icacls', name, '/inheritance:r', '/grant:r',
                                     getpass.getuser() + ':(F)'], capture_output=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
            if result.returncode:
                raise ValueError('无法保护 Connect 设备凭据，请检查数据目录权限')
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            fd = None
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if fd is not None:
            os.close(fd)
        Path(name).unlink(missing_ok=True)


def read_config(data_dir):
    path = Path(data_dir) / 'connect.json'
    try:
        if path.is_symlink():
            raise ValueError('Connect 配置不能是符号链接')
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def enabled(data_dir):
    return read_config(data_dir).get('enabled') is True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ConnectController:
    """Short-lived local actions; never expose this object as an HTTP endpoint."""
    def __init__(self, data_dir, codex_home=None, effective_config=None,
                 relay_url=DEFAULT_RELAY, allow_loopback=False):
        self.directory = Path(data_dir)
        self.home = Path(codex_home) if codex_home else Path.home() / '.codex'
        self.effective_config = effective_config
        self.relay_url = relay_origin(relay_url, allow_loopback)
        self.allow_loopback = allow_loopback

    def status(self, gateway_running=None):
        cfg = read_config(self.directory)
        try:
            status = json.loads((self.directory / 'connect-status.json').read_text(encoding='utf-8'))
            if not isinstance(status, dict):
                status = {}
        except (OSError, ValueError):
            status = {}
        # Never return arbitrary file properties or stored device/model secrets.
        state = status.get('state', 'restart-required') if cfg.get('enabled') else 'disabled'
        if gateway_running is False and cfg.get('enabled'):
            state = 'stopped'
        if status.get('deviceId') not in (None, cfg.get('deviceId')) and cfg.get('enabled'):
            state = 'restart-required'
        pending = cfg.get('pendingRevocations', [])
        pending_count = len(pending) if isinstance(pending, list) else 0
        result = {'enabled': cfg.get('enabled') is True, 'registered': bool(cfg.get('deviceId')),
                'relayUrl': self.relay_url, 'deviceId': cfg.get('deviceId', ''),
                'deviceName': cfg.get('deviceName', ''), 'state': state,
                'pendingRevocations': pending_count,
                'updated': status.get('updated'),
                'message': {'online': '已连接，手机配对须在电脑确认',
                            'connecting': '正在连接 Mhenwa Connect',
                            'offline': '连接断开，正在重连；写操作不会自动重试',
                            'disabled': '远程连接已关闭', 'stopped': '请启动网关',
                            'restart-required': '请重新启动网关使 Connect 生效',
                            'revoked': '设备授权已失效，请关闭后重新注册',
                            'unavailable': 'Connect 组件不可用，请检查安装依赖'}.get(state, '请刷新连接状态')}
        if pending_count:
            result['message'] += '；尚有 ' + str(pending_count) + ' 个离线撤销待重试，重新注册前会尝试清理（可能占用设备额度）'
        return result

    def request(self, method, path, value=None, device=True, _config=None, _timeout=15):
        # _config is an internal captured identity used ONLY for revocation after
        # the local gate is already closed. No desktop payload can populate it.
        cfg = read_config(self.directory) if _config is None else _config
        headers = {'Content-Type': 'application/json', 'User-Agent': CONNECT_USER_AGENT}
        if device:
            if not cfg.get('enabled') or not isinstance(cfg.get('deviceToken'), str):
                raise ValueError('请先注册并开启 Connect')
            # The configured endpoint is pinned to the compiled/trusted origin.
            if cfg.get('relayUrl') != self.relay_url:
                raise ValueError('Connect 入口已变化，请重新注册')
            headers['Authorization'] = 'Bearer ' + cfg['deviceToken']
        request = Request(self.relay_url + path,
                          data=json.dumps(value or {}).encode() if method == 'POST' else None,
                          headers=headers, method=method)
        try:
            with build_opener(NoRedirect()).open(request, timeout=_timeout) as response:
                raw = response.read(MAX_CONTROL_RESPONSE + 1)
                if len(raw) > MAX_CONTROL_RESPONSE:
                    raise ValueError('Connect 响应超过限制')
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError('Connect 响应格式错误')
                return result
        except HTTPError as exc:
            messages = {401: 'Connect 凭据无效，请关闭后重新注册',
                        403: 'Connect 请求被入口或前置网络拒绝，请检查网络代理或联系服务管理员',
                        409: '配对已处理或设备状态变化，请刷新',
                        429: '操作过于频繁，请稍后重试'}
            message = messages.get(exc.code, 'Connect 服务暂不可用')
            try:
                # Only recognize a bounded, known Relay error. CDN/proxy 403s
                # are not qualification decisions; never echo response text.
                content_type = (exc.headers or {}).get('Content-Type', '').split(';', 1)[0].strip().lower()
                if exc.code == 403 and content_type == 'application/json':
                    raw = exc.read(MAX_CONTROL_RESPONSE + 1)
                    body = json.loads(raw) if len(raw) <= MAX_CONTROL_RESPONSE else None
                    if isinstance(body, dict) and body.get('error') == 'model credential is not eligible for this service':
                        message = '所选中转站 Key 未通过 Connect 资格验证，请检查 Key 和账号状态'
            except (OSError, ValueError):
                pass
            finally:
                exc.close()
            raise ConnectError(message, exc.code) from None
        except (URLError, OSError, json.JSONDecodeError):
            raise ValueError('无法连接 Connect 服务，请检查网络') from None

    def retry_revocations(self):
        """Best-effort cleanup, never authorize a disabled identity or expose it.

        The queue lives inside the same private connect.json, so an interrupted
        disable cannot discard its identity between closing the local gate and
        recording the pending revoke. Status projects only the queue's count.
        """
        cfg = read_config(self.directory)
        pending = cfg.get('pendingRevocations', [])
        if not isinstance(pending, list):
            pending = []
        remaining = []
        for index, identity in enumerate(pending):
            if index >= 16 or not isinstance(identity, dict):
                remaining.append(identity)
                continue
            try:
                self.request('POST', '/connect/device/revoke', _config=identity, _timeout=3)
            except ConnectError as exc:
                # A no-longer-accepted device token already lacks all authority.
                if exc.status != 401:
                    remaining.append(identity)
            except ValueError:
                remaining.append(identity)
        if remaining != pending:
            # Local-only controllers own connect.json; the transport only reads.
            cfg = read_config(self.directory)
            cfg['pendingRevocations'] = remaining
            private_json(self.directory / 'connect.json', cfg)
        return len(remaining)

    def control(self, value):
        if not isinstance(value, dict):
            raise ValueError('Connect 操作格式错误')
        action = value.get('action', 'status')
        if action == 'status':
            return self.status()
        if action == 'discover':
            from connect.discovery import discover
            return {'providers': discover(self.home, effective_config=self.effective_config)}
        if action == 'register':
            if value.get('consent') is not True:
                raise ValueError('开启前需要明确同意发送所选中转站 Key 验证资格')
            if read_config(self.directory).get('enabled'):
                raise ValueError('Connect 已注册，请先关闭后重新注册')
            self.retry_revocations()
            from connect.discovery import select_key
            name = value.get('deviceName', socket.gethostname())
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ValueError('电脑名称需为 1–80 个字符')
            key = select_key(self.home, value.get('provider'), effective_config=self.effective_config)
            try:
                result = self.request('POST', '/connect/register',
                                      {'apiKey': key, 'deviceName': name.strip()}, device=False)
            finally:
                key = None
            identifier, token = result.get('deviceId'), result.get('deviceToken')
            if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', identifier)
                    or not isinstance(token, str) or not 20 <= len(token) <= 512
                    or any(ord(c) < 33 or ord(c) > 126 for c in token)):
                raise ValueError('Connect 注册响应格式错误')
            private_json(self.directory / 'connect.json',
                         {'enabled': True, 'relayUrl': self.relay_url,
                          'deviceId': identifier, 'deviceToken': token, 'deviceName': name.strip(),
                          'pendingRevocations': read_config(self.directory).get('pendingRevocations', [])})
            return self.status()
        if action == 'disable':
            cfg = read_config(self.directory)
            pending = cfg.get('pendingRevocations', [])
            pending = list(pending) if isinstance(pending, list) else []
            if cfg.get('enabled') and cfg.get('deviceToken'):
                identity = {k: cfg.get(k) for k in ('relayUrl', 'deviceId', 'deviceToken')}
                identity['enabled'] = True  # RPC only, not startup/forward config.
                if not any(isinstance(row, dict) and row.get('deviceToken') == identity['deviceToken'] for row in pending):
                    pending.append(identity)
            # Close the local execution gate before any potentially slow RPC.
            # The live connector notices this within its one-second receive tick.
            private_json(self.directory / 'connect.json', {'enabled': False, 'pendingRevocations': pending})
            remaining = self.retry_revocations()
            revoked = remaining == 0
            # Fail closed locally even when the relay cannot be reached.
            return {**self.status(), 'remoteRevoked': revoked,
                    'message': '本机已关闭；远端设备及手机已撤销' if revoked
                    else '本机已关闭；远端撤销未确认，离线设备不能执行新操作。待撤销身份仅私密保留，重新注册前重试清理；可能占用设备额度。'}
        if action == 'pair':
            result = self.request('POST', '/connect/device/pair')
            url = result.get('url', '')
            if not isinstance(url, str) or not url.startswith(self.relay_url + '/#connect_pair='):
                raise ValueError('Connect 配对地址格式错误')
            return {key: result.get(key) for key in ('pairingId', 'url', 'expires')}
        if action in ('pairings', 'phones'):
            result = self.request('GET', '/connect/device/' + action)
            columns = ('id', 'phoneName', 'state', 'expires') if action == 'pairings' else ('id', 'name', 'expires')
            rows = result.get(action, [])
            if not isinstance(rows, list) or len(rows) > 256:
                raise ValueError('Connect 设备列表格式错误')
            return {action: [{k: row.get(k) for k in columns} for row in rows if isinstance(row, dict)]}
        if action in ('approve', 'revoke-phone'):
            identifier = value.get('id', '')
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', identifier):
                raise ValueError('Connect 授权编号格式错误')
            if action == 'approve':
                if type(value.get('approved')) is not bool:
                    raise ValueError('请明确批准或拒绝配对')
                self.request('POST', '/connect/device/pairings/' + identifier + '/approve',
                             {'approved': value['approved']})
            else:
                self.request('POST', '/connect/device/phones/' + identifier + '/revoke')
            return {'ok': True}
        raise ValueError('未知 Connect 操作')


class Connector:
    """Outbound device transport with a fixed local target and double allowlist."""
    def __init__(self, data_dir, gateway, allow_loopback=False):
        self.directory = Path(data_dir)
        self.config = read_config(self.directory)
        self.origin = relay_origin(self.config.get('relayUrl'), allow_loopback)
        if not allow_loopback and self.origin != DEFAULT_RELAY:
            raise ValueError('Connect 设备入口与可信服务不匹配，请重新注册')
        self.gateway = gateway
        self.closed = threading.Event()
        self.thread = None
        self.loop = None
        self.task = None
        self.token, self.session = self.gateway.auth.new_session('127.0.0.1', 'Mhenwa Connect internal')
        self.local_origin = 'http://127.0.0.1:' + str(gateway.server_port)
        self.gateway.origins.add(self.local_origin)
        self.gateway.hosts.add(urlsplit(self.local_origin).netloc)

    def status(self, state):
        private_json(self.directory / 'connect-status.json',
                     {'state': state, 'deviceId': self.config.get('deviceId'),
                      'pid': os.getpid(), 'updated': time.time()})

    def still_enabled(self):
        cfg = read_config(self.directory)
        return (not self.closed.is_set() and cfg.get('enabled') is True
                and cfg.get('deviceToken') == self.config.get('deviceToken'))

    async def forward(self, client, message):
        from connect.protocol import MAX_BODY, validate_request
        identifier = message.get('id', '') if isinstance(message, dict) else ''
        def output(status, value):
            return {'type': 'response', 'id': identifier, 'status': status,
                    'contentType': 'application/json; charset=utf-8',
                    'body': base64.b64encode(json.dumps(value).encode()).decode()}
        try:
            if (not isinstance(message, dict) or message.get('type') != 'request'
                    or not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier)):
                raise ValueError()
            encoded = message.get('body', '')
            if not isinstance(encoded, str) or len(encoded) > (MAX_BODY + 2) // 3 * 4:
                raise ValueError()
            body = base64.b64decode(encoded, validate=True)
            method, path = message.get('method'), message.get('path')
            content_type = message.get('contentType', '')
            validate_request(method, path, len(body), content_type)
        except (ValueError, TypeError, binascii.Error):
            return output(403, {'error': '远程操作不在允许范围内', 'code': 'forbidden'})
        if not self.still_enabled():
            return output(503, {'error': '本机远程连接已关闭', 'code': 'device_disabled'})
        if not self.gateway.auth.get(self.token):
            self.token, self.session = self.gateway.auth.new_session('127.0.0.1', 'Mhenwa Connect internal')
        headers = {'Cookie': self.gateway.auth.COOKIE + '=' + self.token,
                   'Origin': self.local_origin, 'X-CSRF-Token': self.session['csrf'],
                   'Content-Type': content_type or 'application/json', 'Accept-Encoding': 'identity'}
        try:
            async with client.request(method, self.local_origin + path, data=body if method == 'POST' else None,
                                      headers=headers, allow_redirects=False) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_BODY:
                        return output(413, {'error': '响应超过 Connect 限制', 'code': 'response_too_large'})
                if 300 <= response.status < 400:
                    return output(502, {'error': '本机返回了不允许的重定向'})
                result = {'type': 'response', 'id': identifier, 'status': response.status,
                          'body': base64.b64encode(raw).decode(),
                          'contentType': response.headers.get('Content-Type', 'application/octet-stream')[:200]}
                disposition = response.headers.get('Content-Disposition', '')
                if (len(disposition) <= 1024 and '\r' not in disposition and '\n' not in disposition
                        and disposition.startswith(('attachment;', 'inline;'))):
                    result['contentDisposition'] = disposition
                return result
        except (asyncio.TimeoutError, OSError):
            return output(504, {'error': '本机操作超时；写操作结果未知，请刷新后确认，勿自动重试',
                                'code': 'outcome_unknown' if method == 'POST' else 'device_timeout'})
        except Exception:
            # Never serialize aiohttp exception reprs or requests/credentials.
            return output(502, {'error': '本机连接中断；写操作结果未知，请刷新后确认',
                                'code': 'outcome_unknown' if method == 'POST' else 'device_unavailable'})

    async def run_async(self):
        import aiohttp
        from connect.protocol import MAX_FRAME
        delay = 1
        timeout = aiohttp.ClientTimeout(total=35)
        async with aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.DummyCookieJar(),
                                         trust_env=False) as client:
            while self.still_enabled():
                self.status('connecting')
                tasks = set()
                try:
                    ws_url = self.origin.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
                    async with client.ws_connect(ws_url + '/connect/device/ws',
                                                 headers={'Authorization': 'Bearer ' + self.config['deviceToken'],
                                                          'User-Agent': CONNECT_USER_AGENT},
                                                 heartbeat=20, max_msg_size=MAX_FRAME) as ws:
                        self.status('online')
                        delay = 1
                        seen = set()
                        async def handle(message):
                            result = await self.forward(client, message)
                            if not ws.closed:
                                await ws.send_json(result)
                        while self.still_enabled():
                            try:
                                frame = await ws.receive(timeout=1)
                            except asyncio.TimeoutError:
                                continue
                            if frame.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            if frame.type != aiohttp.WSMsgType.TEXT:
                                continue
                            try:
                                message = json.loads(frame.data)
                            except ValueError:
                                await ws.close(code=1008)
                                break
                            identifier = message.get('id') if isinstance(message, dict) else None
                            if not isinstance(identifier, str) or identifier in seen:
                                await ws.send_json({'type': 'response', 'id': identifier, 'status': 429,
                                                    'contentType': 'application/json',
                                                    'body': base64.b64encode(b'{"error":"request rejected; not executed"}').decode()})
                                continue
                            if isinstance(identifier, str):
                                if len(seen) >= 4096:
                                    # Close rather than forget replay IDs on a live connection.
                                    await ws.close(code=1012)
                                    break
                                seen.add(identifier)
                            task = asyncio.create_task(handle(message))
                            tasks.add(task)
                            def finished(completed):
                                tasks.discard(completed)
                                if not completed.cancelled():
                                    # Consume transport failures without asyncio
                                    # dumping a credential-bearing task repr.
                                    completed.exception()
                            task.add_done_callback(finished)
                except aiohttp.WSServerHandshakeError as exc:
                    if exc.status in (401, 403):
                        self.status('revoked')
                        return
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                    pass
                finally:
                    for task in tasks:
                        task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                if self.still_enabled():
                    self.status('offline')
                    remaining = delay + random.uniform(0, .25 * delay)
                    while remaining > 0 and self.still_enabled():
                        await asyncio.sleep(min(remaining, .5))
                        remaining -= .5
                    delay = min(delay * 2, 30)
        self.status('disabled')

    def start(self):
        def worker():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.task = self.loop.create_task(self.run_async())
            try:
                self.loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                pass
            except Exception:
                self.status('unavailable')
            finally:
                self.gateway.auth.logout(self.token)
                self.loop.close()
        self.thread = threading.Thread(target=worker, name='mhenwa-connect', daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.loop and self.task and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.task.cancel)
        if self.thread:
            self.thread.join(timeout=5)


def start_companion(data_dir, gateway):
    """Use a separate authenticated loopback listener, also when LAN auth is off."""
    if not enabled(data_dir):
        return None
    from .httpd import GatewayServer
    config = {'auth': {'mode': 'password', 'sessionHours': 1}, 'origins': [], 'localAccess': True}
    local = GatewayServer(('127.0.0.1', 0), gateway.bridge, config, gateway.web_dir)
    local.notifications = gateway.notifications
    try:
        connector = Connector(data_dir, local)
    except Exception:
        local.server_close()
        raise
    listener = threading.Thread(target=local.serve_forever, kwargs={'poll_interval': .2}, daemon=True)
    listener.start()
    connector.start()
    connector.local_listener = listener
    return connector


def stop_companion(connector):
    if connector:
        connector.close()
        connector.gateway.shutdown()
        connector.local_listener.join(timeout=3)
        connector.gateway.server_close()
