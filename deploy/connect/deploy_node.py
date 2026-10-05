#!/usr/bin/env python3
"""Narrow Nginx copy transaction on a previously authorized Connect node.

Compatible with the observed New API host's Python 3.6. No model credentials.
Docker containers are started separately before applying this routing change.
"""
import argparse
import base64
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', choices=('eligibility', 'relay'), required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    node = args.node
    target = Path('/etc/nginx/conf.d/' + ('new-api.conf' if node == 'eligibility' else 'codex.mhenwa.cc.conf'))
    root = Path('/root/mhenwa-connect-' + node + '-transaction')
    if root.exists():
        raise SystemExit('Existing transaction retained. Inspect its VERIFICATION.txt; do not replace its baseline.')
    root.mkdir(mode=0o700)
    original = target.read_bytes()
    (root / 'BASELINE').write_bytes(original)
    text = original.decode('utf-8')
    include_path = '/opt/mhenwa-connect/' + node + '.nginx.inc'
    if include_path in text:
        raise SystemExit('Target already includes Connect; original state preserved for review.')
    # Preserve the HTTP redirect, ACME and all existing sites/upstreams.
    https = text.find('listen 443')
    if https < 0:
        raise SystemExit('Expected HTTPS server not found')
    if node == 'eligibility':
        match = re.search(r'(?m)^\s*server_name\s+api\.mhenwa\.cc\s*;', text[https:])
        if not match:
            raise SystemExit('Expected api HTTPS server identity not found')
        offset = https + match.end()
        modified = text[:offset] + '\n    include ' + include_path + ';' + text[offset:]
    else:
        start = text.find('    location / {', https)
        if start < 0:
            raise SystemExit('Expected codex HTTPS location not found')
        end = text.find('\n    }', start)
        if end < 0 or 'proxy_pass http://127.0.0.1:18787;' not in text[start:end]:
            raise SystemExit('Unexpected codex upstream; refusing unrelated target')
        modified = text[:start] + '    include ' + include_path + ';' + text[end + len('\n    }'):]
    candidate = root / 'MODIFIED_FILE'
    candidate.write_bytes(modified.encode('utf-8'))
    (root / 'DIFF_FILE').write_text(''.join(difflib.unified_diff(
        text.splitlines(True), modified.splitlines(True),
        fromfile=str(target), tofile=str(candidate))), encoding='utf-8')
    rollback = root / 'ROLLBACK.sh'
    rollback.write_text('#!/bin/sh\nset -eu\n[ "$#" -eq 1 ] || { echo "usage: ROLLBACK.sh TARGET_COPY" >&2; exit 2; }\n'
        + "printf '%s' '" + base64.b64encode(original).decode('ascii')
        + "' | base64 -d > \"$1\"\n", encoding='utf-8')
    rollback.chmod(0o700)
    shutil.copyfile(str(source / (node + '.nginx.inc')), include_path)
    # Test the exact same copied target path under a copy of the top-level config.
    probe = root / 'PROBE_COPY'
    probe.write_bytes(original)
    config = Path('/etc/nginx/nginx.conf').read_text(encoding='utf-8')
    selector = 'include /etc/nginx/conf.d/*.conf;'
    if config.count(selector) != 1:
        raise SystemExit('Expected one standard conf.d include; no live change applied')
    imports = []
    for site in sorted(target.parent.glob('*.conf')):
        imports.append('include ' + str(probe if site == target else site) + ';')
    test_config = root / 'nginx-test.conf'
    test_config.write_text(config.replace(selector, '\n'.join(imports)), encoding='utf-8')
    ledger = root / 'VERIFICATION.txt'
    ledger.write_text('TARGET=' + str(target) + '\nCHANGED_FIELD=' + node + ' HTTPS location/include\n'
        + 'BASELINE_SHA256=' + digest(original) + '\nMODIFIED_SHA256=' + digest(candidate.read_bytes()) + '\n', encoding='utf-8')

    def command(label, argv):
        process = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        with ledger.open('a', encoding='utf-8') as stream:
            stream.write('\n' + label + '\nCOMMAND=' + json.dumps(argv) + '\nSTDOUT_BEGIN\n'
                + process.stdout + '\nSTDOUT_END\nSTDERR_BEGIN\n' + process.stderr
                + '\nSTDERR_END\nEXIT=' + str(process.returncode) + '\n')
        print(label + '_EXIT=' + str(process.returncode))
        if process.returncode:
            raise SystemExit('Command failed; inspect ledger, live target unchanged unless LIVE_APPLY recorded')

    argv = ['nginx', '-t', '-c', str(test_config)]
    command('BASELINE', argv)
    probe.write_bytes(candidate.read_bytes())
    command('MODIFIED', argv)
    command('ROLLBACK_EXEC', [str(rollback), str(probe)])
    command('ROLLBACK', argv)
    if digest(probe.read_bytes()) != digest(original):
        raise SystemExit('Restored hash mismatch; no live change')
    with ledger.open('a', encoding='utf-8') as stream:
        stream.write('RESTORED_SHA256=' + digest(probe.read_bytes()) + '\nRESTORED_EQUALS_BASELINE=true\n')
    # Reapply only to disposable probe; MODIFIED_FILE itself remains modified.
    probe.write_bytes(candidate.read_bytes())
    if args.apply:
        if target.read_bytes() != original:
            raise SystemExit('Live target changed during transaction; refusing overwrite')
        shutil.copyfile(str(candidate), str(target))
        try:
            command('LIVE_APPLY', ['nginx', '-t'])
            command('LIVE_RELOAD', ['systemctl', 'reload', 'nginx'])
        except BaseException:
            shutil.copyfile(str(root / 'BASELINE'), str(target))
            subprocess.run(['nginx', '-t'])
            subprocess.run(['systemctl', 'reload', 'nginx'])
            raise
    metadata = {'ACTIVE_OBJECT': str(target), 'LAST_CONFIRMED_RESULT': 'nginx copy baseline/modified/rollback verified',
        'NEXT_EXECUTABLE_ACTION': 'verify live Connect HTTP and WebSocket flow',
        'INPUT_PATHS': [str(target), str(candidate), str(ledger)], 'ACCEPTANCE_EVENT': 'live HTTPS paired operation',
        'command': ['python3', str(source / 'deploy_node.py'), '--node', node, '--apply'],
        'transaction': 'complete' if args.apply else 'prepared'}
    (root / 'CONTROLLER.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    for name in ('MODIFIED_FILE', 'DIFF_FILE', 'VERIFICATION.txt', 'ROLLBACK.sh'):
        path = root / name
        data = path.read_bytes()
        print(str(path) + ' SHA256=' + digest(data) + ' BYTES=' + str(len(data)))


if __name__ == '__main__':
    main()
