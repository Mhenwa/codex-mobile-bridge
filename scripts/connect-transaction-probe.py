#!/usr/bin/env python3
"""Same-input behavior probe for baseline/modified/rollback source ZIPs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--archive', required=True)
args = parser.parse_args()
with zipfile.ZipFile(args.archive) as source:
    present = 'connect/relay.py' in source.namelist()
    print(json.dumps({'connectFeatureAvailable': present, 'personalGatewayPresent': 'run.py' in source.namelist()}), flush=True)
    with tempfile.TemporaryDirectory(prefix='connect-transaction-') as directory:
        target = Path(directory).resolve()
        for name in source.namelist():
            resolved = (target / name).resolve()
            if not resolved.is_relative_to(target):
                raise ValueError('Unsafe archive path')
        source.extractall(target)
        # Execute the same native desktop action and the same literal input in
        # all three states. Baseline/rollback argparse rejects the new action;
        # modified accepts it and returns sanitized disabled status.
        argv = [sys.executable, '-B', 'desktop.py', 'connect', '--data-dir', str(target / '.probe-data')]
        print('NATIVE_COMMAND=' + json.dumps(argv) + '\nNATIVE_INPUT={"action":"status"}', flush=True)
        cli = subprocess.run(argv, input='{"action":"status"}', encoding='utf-8', cwd=target)
        print('NATIVE_EXIT=' + str(cli.returncode), flush=True)
        if not present:
            print('OBSERVED_BEHAVIOR=CONNECT_ACTION_ABSENT_IN_ORIGINAL_PERSONAL_GATEWAY', flush=True)
            raise SystemExit(cli.returncode)
        if cli.returncode:
            raise SystemExit(cli.returncode)
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_connect*.py'], cwd=target)
        print('OBSERVED_BEHAVIOR=CONNECT_THREE_CREDENTIAL_REGISTRATION_PAIRING_AND_ALLOWED_GATEWAY_FLOW', flush=True)
        raise SystemExit(result.returncode)
