"""Reject private artifacts or obvious credentials in Git-tracked lab source."""
import re
import subprocess
from pathlib import Path

site = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '-z', '--', '.'], cwd=site).decode().split('\0')
blocked = ('public/corpus/', '.sites-runtime/', '.wrangler/', 'private-inputs/', 'node_modules/', 'dist/')
records = {'tests/parity.json', 'tests/runtime-report.json'}
patterns = [
    rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}',
    rb'gh[pousr]_[A-Za-z0-9]{20,}',
    rb'github_pat_[A-Za-z0-9_]{20,}',
    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    rb'appgprj_[A-Za-z0-9]{20,}',
    rb'[A-Za-z0-9._%+-]+@(?:gmail|naver|daum|outlook)\.(?:com|net)',
    rb'https://[a-z0-9.-]+\.chatgpt\.site',
    rb'/Users/[A-Za-z0-9_-]+/',
]
failures = []
for name in filter(None, paths):
    path = site / name
    if name.startswith(blocked) or name in records or path.name.startswith('.env') or path.suffix in {'.db', '.sqlite', '.sqlite3', '.pem', '.log'}:
        failures.append(name + ': private/generated file')
    if path.is_file() and any(re.search(pattern, path.read_bytes()) for pattern in patterns):
        failures.append(name + ': credential or account identifier pattern')
if failures:
    raise SystemExit('\n'.join(failures))
print(f'Public source check passed for {sum(bool(p) for p in paths)} tracked files; private artifacts excluded.')
