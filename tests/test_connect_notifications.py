"""Native Connect events continue when every phone page and legacy push is closed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge.connect import ConnectController, DEFAULT_RELAY, notification_identity
from bridge.notifications import Notifications, read_json, save_settings, write_json
from bridge.service import LiveSession

ROOT = Path(__file__).resolve().parents[1]
THREAD = '11111111-1111-4111-8111-111111111111'


class ConnectNotificationTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = {'enabled': True, 'relayUrl': DEFAULT_RELAY,
                       'deviceId': 'test-device-one', 'deviceToken': 'private-device-token-123456'}
        write_json(self.directory/'connect.json', self.config)
        self.session = LiveSession(THREAD)
        self.session.connected = True
        self.session.state = {'title': 'Never send this private title', 'requests': [],
                              'turns': [{'turnId': 'old', 'status': 'completed'}]}
        session = self.session
        class Source:
            changes = [{'host': 'local', 'id': THREAD}]
            dirty = []
            calls = 0
            releases = 0
            update_reads = 0
            def notification_candidates(self):
                result, self.changes = self.changes, []
                return result
            def notification_updates(self):
                self.update_reads += 1
                result, self.dirty = self.dirty, []
                return result
            def for_host(self, host): return self
            def notification_session(self, identifier):
                self.calls += 1
                session.connected = True
                return session
            def release_notification_session(self, value):
                self.releases += 1
                value.watched = False
                value.connected = False
                return True
            store = type('Store', (), {'get': lambda _, identifier: {}})()
        self.source = Source()
        self.manager = Notifications(self.source, self.directory)
        self.addCleanup(self.manager.close)
        self.manager.defaults({'requests': False, 'completion': False})
        self.manager.policy(THREAD, 'local', {'requests': 'off', 'completion': 'off'})

    def request(self, identifier='approval'):
        self.session.state['requests'] = [
            {'id': identifier, 'method': 'item/commandExecution/requestApproval',
             'params': {'command': 'Never send this secret command'}}]

    def rediscover(self):
        self.source.dirty = [{'host': 'local', 'id': THREAD}]

    def event(self, send):
        self.assertEqual(send.call_args.args[:2], ('POST', '/connect/device/notifications'))
        value = send.call_args.args[2]
        self.assertEqual(set(value), {'id', 'kind', 'threadId', 'host', 'count', 'createdAt'})
        self.assertRegex(value['id'], r'^[a-f0-9]{64}$')
        self.assertEqual(value['threadId'], THREAD)
        self.assertEqual(value['host'], 'local')
        self.assertEqual(value['count'], 1)
        self.assertIs(type(value['createdAt']), int)
        self.assertNotIn('private', json.dumps(value).lower())
        self.assertNotIn('secret', json.dumps(value).lower())
        self.assertEqual(send.call_args.kwargs['_config']['deviceToken'], self.config['deviceToken'])
        return value

    def test_pending_approval_without_web_viewers_or_legacy_channels(self):
        self.request()
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 1}) as send, \
                patch('bridge.notifications.publish') as ntfy, \
                patch('bridge.notifications.publish_bark') as bark, \
                patch('bridge.notifications.publish_pushplus') as pushplus:
            self.manager.scan(); self.manager.scan()
            send.assert_called_once()
            self.assertEqual(self.event(send)['kind'], 'request')
            ntfy.assert_not_called(); bark.assert_not_called(); pushplus.assert_not_called()
        self.assertEqual(self.session.viewers, 0)
        self.assertTrue(self.session.watched)
        self.assertEqual(self.source.update_reads, 2)

    def test_completion_baseline_then_new_run_without_phone(self):
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 1}) as send:
            self.manager.scan(); send.assert_not_called()
            self.session.state['turns'].append({'turnId': 'new-run', 'status': 'inProgress'})
            self.rediscover(); self.manager.scan(); send.assert_not_called()
            self.session.state['turns'][-1]['status'] = 'completed'
            self.manager.scan(); self.manager.scan()
            send.assert_called_once()
            self.assertEqual(self.event(send)['kind'], 'completion')
            self.assertEqual(self.source.releases, 2)
        self.assertEqual(self.session.viewers, 0)

    def test_no_subscription_acceptance_still_deduplicates_after_restart(self):
        self.request()
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 0}) as send:
            self.manager.scan()
            self.event(send)
            restarted = Notifications(self.source, self.directory)
            self.addCleanup(restarted.close)
            restarted.discovery_at = 0
            self.source.changes = [{'host': 'local', 'id': THREAD}]
            restarted.scan()
            send.assert_called_once()
        ledger = read_json(self.directory/'notification-delivery.json', {})
        self.assertTrue(all(row['delivered'] for row in ledger.values()))
        self.assertNotIn(self.config['deviceToken'], json.dumps(ledger))

    def test_completion_accepted_zero_is_not_replayed_to_later_subscription(self):
        self.session.state['turns'][-1]['status'] = 'inProgress'
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 0}) as send:
            self.manager.scan()
            self.session.state['turns'][-1]['status'] = 'completed'
            self.manager.scan()
            self.rediscover(); self.manager.scan()
            send.assert_called_once()
            self.assertEqual(self.event(send)['kind'], 'completion')

    def test_stable_retry_identity_and_timestamp_and_resolved_approval_stops(self):
        self.request()
        with patch.object(ConnectController, 'request', side_effect=OSError('offline')) as send:
            self.manager.scan()
            first = dict(self.event(send))
        for row in self.manager.ledger.values(): row['next'] = 0
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 1}) as send:
            self.manager.scan()
            self.assertEqual(self.event(send), first)
        self.request('resolved')
        with patch.object(ConnectController, 'request', side_effect=OSError('offline')) as send:
            self.manager.scan(); send.assert_called_once()
        for row in self.manager.ledger.values(): row['next'] = 0
        self.session.state['requests'] = []
        with patch.object(ConnectController, 'request') as send:
            self.manager.scan(); send.assert_not_called()

    def test_local_policy_changes_preserve_connect_completion_boundary(self):
        self.session.state['turns'][-1]['status'] = 'inProgress'
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 1}) as send:
            self.manager.scan()
            self.manager.policy(THREAD, 'local', {'requests': 'off', 'completion': 'off'})
            self.manager.defaults({'requests': False, 'completion': False})
            self.session.state['turns'][-1]['status'] = 'completed'
            self.manager.scan(); send.assert_called_once()
            self.assertEqual(self.event(send)['kind'], 'completion')

    def test_failed_completion_retries_detached_and_after_restart(self):
        self.session.state['turns'][-1]['status'] = 'inProgress'
        self.manager.scan()
        self.session.state['turns'][-1]['status'] = 'completed'
        with patch.object(ConnectController, 'request', side_effect=OSError('offline')):
            self.manager.scan()
        calls = self.source.calls
        for row in self.manager.ledger.values(): row['next'] = 0
        write_json(self.directory/'notification-delivery.json', self.manager.ledger)
        restarted = Notifications(self.source, self.directory)
        self.addCleanup(restarted.close)
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 1}) as send:
            restarted.scan(); send.assert_called_once()
            self.assertEqual(self.event(send)['kind'], 'completion')
        self.assertEqual(self.source.calls, calls)

    def test_disable_device_cancels_old_retry_and_releases_subscription(self):
        self.request()
        with patch.object(ConnectController, 'request', side_effect=OSError('offline')):
            self.manager.scan()
        write_json(self.directory/'connect.json', {'enabled': False})
        for row in self.manager.ledger.values(): row['next'] = 0
        with patch.object(ConnectController, 'request') as send:
            self.manager.scan(); send.assert_not_called()
        self.assertFalse(self.session.watched)

    def test_device_change_discards_old_completion_retry_and_starts_new_baseline(self):
        self.session.state['turns'][-1]['status'] = 'inProgress'
        self.manager.scan()
        self.session.state['turns'][-1]['status'] = 'completed'
        with patch.object(ConnectController, 'request', side_effect=OSError('offline')):
            self.manager.scan()
        old_target = notification_identity(self.config)
        replacement = {**self.config, 'deviceId': 'replacement-device', 'deviceToken': 'replacement-token-123456'}
        write_json(self.directory/'connect.json', replacement)
        for row in self.manager.ledger.values(): row['next'] = 0
        with patch.object(ConnectController, 'request') as send:
            self.manager.scan(); send.assert_not_called()
        self.assertTrue(all(json.loads(key)[-1] != old_target for key in self.manager.completions))

    def test_changed_device_between_scan_and_send_does_not_use_old_identity(self):
        self.request()
        self.session.state['requests'].append(
            {'id': 'second', 'method': 'item/commandExecution/requestApproval', 'params': {}})
        def change_identity(*args, **kwargs):
            write_json(self.directory/'connect.json', {'enabled': False})
            return {'ok': True, 'accepted': 1}
        with patch.object(ConnectController, 'request', side_effect=change_identity) as send:
            self.manager.scan()
            send.assert_called_once()
        controller = ConnectController(self.directory)
        value = {'id': 'a'*64, 'kind': 'request', 'threadId': THREAD, 'host': 'local', 'count': 1, 'createdAt': 1}
        with patch.object(controller, 'request') as send:
            self.assertFalse(controller.publish_notification(value, notification_identity(self.config)))
            send.assert_not_called()

    def test_scan_budget_and_separate_native_events(self):
        self.session.state['requests'] = [
            {'id': f'approval-{n}', 'method': 'item/commandExecution/requestApproval', 'params': {}}
            for n in range(35)]
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 1}) as send:
            self.manager.scan(); self.assertEqual(send.call_count, 32)
            first_ids = {call.args[2]['id'] for call in send.call_args_list}
            self.assertEqual(len(first_ids), 32)
            self.assertTrue(all(call.args[2]['count'] == 1 for call in send.call_args_list))
            self.manager.scan(); self.assertEqual(send.call_count, 35)
            self.assertEqual(len({call.args[2]['id'] for call in send.call_args_list}), 35)

    def test_first_connect_failure_pauses_stream_without_blocking_next_scan_ntfy(self):
        self.session.state['requests'] = [
            {'id': f'approval-{n}', 'method': 'item/commandExecution/requestApproval', 'params': {}}
            for n in range(35)]
        save_settings(self.directory, {'enabled': True, 'topic': 'fixture'})
        self.manager.policy(THREAD, 'local', {'requests': 'on', 'completion': 'off'})
        with patch.object(ConnectController, 'request', side_effect=OSError('offline')) as connect, \
                patch('bridge.notifications.publish') as ntfy:
            self.manager.scan(); connect.assert_called_once(); ntfy.assert_called_once()
            self.assertEqual(self.manager.connect_budget, 0)
            for row in self.manager.ledger.values(): row['next'] = 0
            self.manager.scan(); self.assertEqual(connect.call_count, 2)
            ntfy.assert_called_once()

    def test_failed_native_events_expire_from_stable_timestamp_and_persist(self):
        for completed, ttl in ((False, 300), (True, 3600)):
            with self.subTest(completed=completed):
                self.manager.ledger.clear()
                self.manager.completions.clear()
                self.manager.candidates.clear()
                self.manager.dormant.clear()
                self.manager.discovery_at = 0
                self.source.changes = [{'host': 'local', 'id': THREAD}]
                self.session.state['requests'] = []
                self.session.state['turns'] = [{'turnId': 'expiry-run', 'status': 'inProgress'}]
                with patch('bridge.notifications.time.time', return_value=10000):
                    self.manager.scan()
                if completed:
                    self.session.state['turns'][-1]['status'] = 'completed'
                else:
                    self.request('expiring-request')
                with patch('bridge.notifications.time.time', return_value=10000), \
                        patch.object(ConnectController, 'request', side_effect=OSError('offline')) as send:
                    self.manager.scan(); send.assert_called_once()
                for row in self.manager.ledger.values(): row['next'] = 0
                with patch('bridge.notifications.time.time', return_value=10000+ttl-1), \
                        patch.object(ConnectController, 'request', side_effect=OSError('offline')) as send:
                    self.manager.scan(); send.assert_called_once()
                for row in self.manager.ledger.values(): row['next'] = 0
                with patch('bridge.notifications.time.time', return_value=10000+ttl), \
                        patch.object(ConnectController, 'request') as send:
                    self.manager.scan(); send.assert_not_called()
                records = list(read_json(self.directory/'notification-delivery.json', {}).values())
                self.assertTrue(records)
                self.assertTrue(all(row.get('expired') and row['delivered'] for row in records))
                self.assertTrue(all(row['createdAt'] == 10000 for row in records))
                self.source.changes = [{'host': 'local', 'id': THREAD}]
                restarted = Notifications(self.source, self.directory)
                self.addCleanup(restarted.close)
                with patch('bridge.notifications.time.time', return_value=10000+ttl+1), \
                        patch.object(ConnectController, 'request') as send:
                    restarted.scan(); send.assert_not_called()

    def test_no_connect_configuration_does_not_load_companion(self):
        (self.directory/'connect.json').unlink()
        self.request()
        import builtins
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name == 'connect' and kwargs.get('level', args[3] if len(args) > 3 else 0):
                raise AssertionError('disabled companion was imported')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded):
            self.manager.scan()
        self.assertEqual(self.source.calls, 0)

    def test_third_party_preferences_continue_to_work_independently(self):
        self.request()
        save_settings(self.directory, {'enabled': True, 'topic': 'fixture'})
        with patch.object(ConnectController, 'request', return_value={'ok': True, 'accepted': 1}) as connect, \
                patch('bridge.notifications.publish') as ntfy:
            self.manager.scan(); connect.assert_called_once(); ntfy.assert_not_called()
            self.manager.policy(THREAD, 'local', {'requests': 'on', 'completion': 'off'})
            self.manager.scan(); connect.assert_called_once(); ntfy.assert_called_once()

    def test_damaged_connect_settings_do_not_disable_existing_ntfy(self):
        self.request()
        save_settings(self.directory, {'enabled': True, 'topic': 'fixture'})
        self.manager.policy(THREAD, 'local', {'requests': 'on', 'completion': 'off'})
        (self.directory/'connect.json').write_text('{invalid', encoding='utf-8')
        with patch.object(ConnectController, 'request') as connect, \
                patch('bridge.notifications.publish') as ntfy:
            self.manager.scan(); connect.assert_not_called(); ntfy.assert_called_once()

    def test_controller_validates_payload_response_and_device_auth(self):
        controller = ConnectController(self.directory)
        target = notification_identity(self.config)
        value = {'id': 'a'*64, 'kind': 'request', 'threadId': THREAD, 'host': 'local', 'count': 1, 'createdAt': 1}
        for malformed in ({**value, 'title': 'private'}, {**value, 'id': 'native-id'},
                          {**value, 'kind': 'other'}, {**value, 'threadId': 'invalid'},
                          {**value, 'host': 'bad\nhost'}, {**value, 'host': 'bad host'},
                          {**value, 'host': 'ssh:work~wrong'}, {**value, 'host': 'ssh:work/other'},
                          {**value, 'host': 'ssh:work#other'}, {**value, 'host': 'ssh:work|other'},
                          {**value, 'host': 'x'*257},
                          {**value, 'count': True}, {**value, 'createdAt': 1.5}):
            with self.subTest(value=malformed), patch.object(controller, 'request') as send:
                with self.assertRaises(ValueError): controller.publish_notification(malformed, target)
                send.assert_not_called()
        for malformed in ({'ok': False, 'accepted': 1}, {'ok': True, 'accepted': True}, {'ok': True}):
            with patch.object(controller, 'request', return_value=malformed):
                with self.assertRaises(ValueError): controller.publish_notification(value, target)
        response = MagicMock(); response.read.return_value = b'{"ok":true,"accepted":0}'
        with patch('bridge.connect.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value = response
            self.assertTrue(controller.publish_notification(value, target))
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, DEFAULT_RELAY+'/connect/device/notifications')
            self.assertEqual(request.get_header('Authorization'), 'Bearer '+self.config['deviceToken'])
            self.assertEqual(json.loads(request.data), value)


if __name__ == '__main__':
    unittest.main()
