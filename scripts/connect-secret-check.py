#!/usr/bin/env python3
"""Check a privately supplied test key is absent from publishable source."""
import getpass
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
needle = getpass.getpass('Private test key to check (hidden): ').encode()
if len(needle) < 16:
    raise SystemExit('A real private test input is required; no output was inspected')
names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
matches, checked = 0, 0
for name in set(names) - {''}:
    path = root / name
    if path.is_file():
        matches += needle in path.read_bytes()
        checked += 1
needle = None
print('PUBLISHABLE_FILES_CHECKED=' + str(checked))
print('PRIVATE_TEST_KEY_MATCHES=' + str(matches))
raise SystemExit(1 if matches else 0)
