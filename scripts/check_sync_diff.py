#!/usr/bin/env python3
"""Restrict automatic commits to generated additions and index changes."""
import re
import subprocess
import sys
from pathlib import Path
import sync_validation as validation

root = Path(__file__).resolve().parents[1]
base = sys.argv[1]
if not re.fullmatch('[0-9a-f]{40}', base):
    raise SystemExit('Invalid baseline')
if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() != base:
    raise SystemExit('Unexpected history before sync commit')
validation.check_legacy(root)
raw = subprocess.check_output(['git', 'diff', '--cached', '--no-renames', '--name-status', '-z', base], cwd=root)
# Query the baseline tree, not the worktree/index: an existing revision is
# immutable even when a newly staged file did not exist in that revision.
baseline = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', '-z', base, '--', 'actions'], cwd=root)
existing_revisions = set()
for entry in baseline.split(b'\0'):
    match = re.match(rb'(actions/[a-z0-9-]+/revisions/[a-f0-9]{16})(?:/|$)', entry)
    if match:
        existing_revisions.add(match[1].decode())
parts = raw.split(b'\0')
if parts[-1] != b'' or (len(parts) - 1) % 2:
    raise SystemExit('Malformed staged diff')
for index in range(0, len(parts) - 1, 2):
    status, path = parts[index].decode(), parts[index + 1].decode()
    if path in ('catalog.json', 'upstream-state.json') and status in ('A', 'M'):
        continue
    if status == 'A' and re.fullmatch(r'actions/[a-z0-9-]+/revisions/[a-f0-9]{16}/(?:manifest\.json|LICENSE\.txt|frames/[0-9]{3}\.webp)', path):
        if path.rsplit('/', 2)[0] in existing_revisions or path.rsplit('/', 1)[0] in existing_revisions:
            raise SystemExit('Existing revision is immutable: ' + path)
        continue
    raise SystemExit('Automatic commit exceeds resource allowlist: ' + status + ' ' + path)
print('PASS automatic commit allowlist; legacy v1 unchanged; main parent preserved')
