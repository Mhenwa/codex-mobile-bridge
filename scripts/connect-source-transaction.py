#!/usr/bin/env python3
"""Create/reopen four source roles and execute reversible archive verification.

Native ZIP snapshots and a native Git binary diff reconstruct the exact changed
copy. No private runtime data is included; only git-visible source is packaged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--baseline', default='ac4eab6c0866e77e42f08b29994759d907fcd94f')
parser.add_argument('--output', default='.local/connect-source-transaction')
parser.add_argument('--resume', action='store_true', help='Preserve baseline, earlier candidates and evidence')
args = parser.parse_args()
repository = Path(__file__).resolve().parents[1]
output = (repository / args.output).resolve()
if not output.is_relative_to(repository / '.local'):
    parser.error('Source transaction output must be disposable under repository .local')
output.mkdir(parents=True, exist_ok=args.resume)
baseline = output / 'BASELINE.zip'
modified = output / 'MODIFIED_FILE.zip'
diff = output / 'DIFF_FILE.patch'
ledger = output / 'VERIFICATION.txt'
rollback = output / 'ROLLBACK.sh'
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

if args.resume:
    if not all(path.is_file() for path in (baseline, modified, diff, ledger, rollback)):
        parser.error('Resume requires the existing baseline and all four roles')
    original_record = ledger.read_text(encoding='utf-8')
    if ('BASELINE_REF=' + args.baseline + '\n' not in original_record
            or 'BASELINE_SHA256=' + digest(baseline) + '\n' not in original_record):
        parser.error('Immutable baseline identity/hash does not match existing evidence')
    history = output / 'history' / digest(modified)
    history.mkdir(parents=True, exist_ok=True)
    for role in (modified, diff, ledger, rollback):
        if not (history / role.name).exists():
            shutil.copyfile(role, history / role.name)
else:
    subprocess.run(['git', 'archive', '--format=zip', '--output=' + str(baseline), args.baseline], cwd=repository, check=True)
files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=repository).decode().split('\0')
with zipfile.ZipFile(modified, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for name in sorted(set(files) - {''}):
        path = repository / name
        if not path.is_file():
            continue
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100644 << 16)
        archive.writestr(info, path.read_bytes())
temporary_old, temporary_new = output / 'old', output / 'new'
temporary_old.mkdir(exist_ok=args.resume); temporary_new.mkdir(exist_ok=args.resume)
shutil.copyfile(baseline, temporary_old / 'source.zip')
shutil.copyfile(modified, temporary_new / 'source.zip')
patch = subprocess.run(['git', 'diff', '--no-index', '--binary', 'old/source.zip', 'new/source.zip'], cwd=output, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if patch.returncode != 1:
    raise RuntimeError('Expected observed Git binary difference')
diff.write_bytes(patch.stdout.replace(b'a/old/source.zip', b'a/source.zip').replace(b'b/new/source.zip', b'b/source.zip'))
rollback.write_text('#!/bin/sh\nset -eu\n[ "$#" -eq 1 ] || { echo "usage: ROLLBACK.sh TARGET_COPY.zip" >&2; exit 2; }\n'
    + 'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\ncp "$here/BASELINE.zip" "$1"\n', encoding='utf-8', newline='\n')
rollback.chmod(0o755)
with ledger.open('a' if args.resume else 'w', encoding='utf-8') as stream:
    stream.write(('\nRESUME=true\n' if args.resume else '') + 'TARGET=' + str(repository)
        + '\nBASELINE_REF=' + args.baseline + '\nBASELINE_SHA256=' + digest(baseline)
        + '\nMODIFIED_SHA256=' + digest(modified) + '\n'
        + 'CHANGED_SYMBOLS=ConnectController;Connector;Relay;Registry;EligibilityClient;SQLiteLookup;Desktop.connect;run.main;uploadAttachmentWithRetry;showLogin;updater.REPO\n')
def command(label, argv, cwd=repository, expected_exit=0):
    result = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    with ledger.open('a', encoding='utf-8') as stream:
        stream.write('\n' + label + '\nCOMMAND=' + json.dumps([str(item) for item in argv]) + '\nCWD=' + str(cwd)
            + '\nENV=PYTHONIOENCODING=utf-8;PYTHONUTF8=1'
            + '\nSTDOUT_BEGIN\n' + result.stdout.decode('utf-8', errors='replace') + '\nSTDOUT_END\nSTDERR_BEGIN\n'
            + result.stderr.decode('utf-8', errors='replace') + '\nSTDERR_END\nEXIT=' + str(result.returncode) + '\n')
    print(label + '_EXIT=' + str(result.returncode), flush=True)
    if result.returncode != expected_exit:
        raise RuntimeError(label + ' expected exit ' + str(expected_exit) + ', observed ' + str(result.returncode))

reconstruction = output / 'reconstruction'
reconstruction.mkdir(exist_ok=args.resume)
shutil.copyfile(baseline, reconstruction / 'source.zip')
command('DIFF_RECONSTRUCT', ['git', 'apply', '--binary',
    '--directory=' + reconstruction.relative_to(repository).as_posix(), str(diff)])
assert digest(reconstruction / 'source.zip') == digest(modified)
probe = output / 'PROBE_COPY.zip'
argv = [sys.executable, str(repository / 'scripts/connect-transaction-probe.py'), '--archive', str(probe)]
shutil.copyfile(baseline, probe)
command('BASELINE', argv, expected_exit=2)
shutil.copyfile(modified, probe)
command('MODIFIED', argv)
# Git for Windows supplies a portable sh; execute the actual rollback script.
shell = shutil.which('sh')
if shell is None:
    roots = [Path('C:/Program Files/Git')]
    git = shutil.which('git')
    if git:
        roots.insert(0, Path(git).resolve().parent.parent)
    for root in roots:
        for candidate in (root / 'bin/sh.exe', root / 'usr/bin/sh.exe'):
            if candidate.is_file():
                shell = str(candidate); break
        if shell:
            break
if shell is None:
    raise RuntimeError('Portable sh unavailable; rollback execution not verified')
command('ROLLBACK_EXEC', [shell, str(rollback).replace('\\', '/'), str(probe).replace('\\', '/')])
command('ROLLBACK', argv, expected_exit=2)
assert digest(probe) == digest(baseline)
with ledger.open('a', encoding='utf-8') as stream:
    stream.write('RESTORED_SHA256=' + digest(probe) + '\nRESTORED_EQUALS_BASELINE=true\nDIFF_RECONSTRUCT_EQUALS_MODIFIED=true\n')
shutil.copyfile(modified, probe)
with ledger.open('a', encoding='utf-8') as stream:
    stream.write('GOAL_REAPPLIED_SHA256=' + digest(probe) + '\n')
for role in (modified, diff, ledger, rollback):
    data = role.read_bytes()
    print(str(role) + ' SHA256=' + hashlib.sha256(data).hexdigest() + ' BYTES=' + str(len(data)), flush=True)
