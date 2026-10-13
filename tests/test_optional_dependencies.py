"""Personal-mode discovery must remain runnable without optional relay packages."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OptionalDependencyTests(unittest.TestCase):
    def test_stdlib_discovery_skips_optional_transport_but_keeps_security_units(self):
        code = '''
import importlib.util, json, pathlib, sys, unittest
root = pathlib.Path.cwd()
assert importlib.util.find_spec('aiohttp') is None
import run, bridge.app.desktop, bridge.connect
modules = ('test_connect_companion', 'test_connect_relay',
           'test_connect_eligibility', 'test_connect_push',
           'test_connect_web_push_flow', 'test_shared_relay', 'test_relay_admin')
suite = unittest.TestSuite()
for name in modules:
    suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests'), pattern=name+'.py'))
class Result(unittest.TextTestResult):
    def __init__(self, *args):
        super().__init__(*args)
        self.passed_classes = set()
    def addSuccess(self, test):
        self.passed_classes.add(type(test).__name__)
        super().addSuccess(test)
result = unittest.TextTestRunner(verbosity=0, resultclass=Result).run(suite)
print(json.dumps({'ok': result.wasSuccessful(), 'tests': result.testsRun,
                  'skipped': len(result.skipped), 'errors': len(result.errors),
                  'classes': sorted(result.passed_classes)}))
'''
        child = subprocess.run([sys.executable, '-S', '-B', '-c', code],
                               cwd=ROOT, capture_output=True, text=True, timeout=45)
        self.assertEqual(child.returncode, 0, child.stderr)
        result = json.loads(child.stdout.splitlines()[-1])
        self.assertTrue(result['ok'], child.stderr)
        self.assertEqual(result['errors'], 0, child.stderr)
        self.assertGreater(result['skipped'], 0)
        self.assertTrue({'ControllerTests', 'LifecycleTests', 'ProtocolTests', 'RegistryTests'}
                        <= set(result['classes']), result)

