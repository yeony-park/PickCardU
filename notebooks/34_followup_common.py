"""Follow-up-only output paths, input copies and content/mtime preservation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat as stat_module
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'notebooks/data'
STAGES = {34: DATA / '34_bundle_comparison', 35: DATA / '35_answer_budget',
          36: DATA / '36_profile_search_comparison'}
OLD = {30: DATA / '30_structured_benefit_db', 31: DATA / '31_operational_chunking_comparison',
       32: DATA / '32_structured_search_comparison', 33: DATA / '33_query_routing'}
MODEL = 'gpt-6-luna'
REASONING = 'medium'


def assert_private_directory(path: Path) -> None:
    path = Path(path).absolute()
    if not path.is_relative_to(ROOT):
        raise RuntimeError('Private output directory escapes workspace')
    for candidate in [path, *path.parents]:
        if candidate.is_symlink():
            raise RuntimeError(f'Private directory contains a symlink: {candidate}')
        if candidate.exists() and not candidate.is_dir():
            raise RuntimeError('Private output ancestor must be a directory')
        if candidate == ROOT:
            break


def initialize(stage: int = 34) -> None:
    if Path(sys.prefix).name != 'skn25':
        raise RuntimeError('Only approved conda skn25 is allowed')
    sys.dont_write_bytecode = True
    cache = STAGES[stage] / 'execution_cache'
    assert_private_directory(cache)
    cache.mkdir(parents=True, exist_ok=True)
    for key in ['HF_HOME', 'XDG_CACHE_HOME', 'TMPDIR', 'MPLCONFIGDIR', 'JUPYTER_RUNTIME_DIR']:
        target = cache / key.lower()
        assert_private_directory(target)
        target.mkdir(exist_ok=True)
        os.environ[key] = str(target)
    os.environ.update(PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      TOKENIZERS_PARALLELISM='false', ANONYMIZED_TELEMETRY='false')
    for part in ['packages/rag-core/src', 'jobs/rag-indexer/src', 'services/rag-api/src']:
        value = str(ROOT / part)
        if value not in sys.path:
            sys.path.insert(0, value)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path: Path, value) -> None:
    path = Path(path)
    if not any(path.absolute().is_relative_to(p.absolute()) for p in STAGES.values()):
        raise RuntimeError(f'Follow-up output outside approved new stage: {path}')
    assert_private_directory(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.writing')
    for existing in [path, temporary]:
        if existing.is_symlink() or (existing.exists() and existing.stat().st_nlink > 1):
            raise RuntimeError('Output must not be a symlink or hardlink')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(path)


def file_record(path: Path) -> dict:
    stat = path.stat()
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2**20), b''):
            hasher.update(block)
    return {'path': str(path.relative_to(ROOT)), 'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns, 'sha256': hasher.hexdigest()}


def preservation_snapshot() -> dict:
    """Validate original manifests BEFORE freezing every prior experiment file."""
    destination = STAGES[34] / 'protected_files.json'
    if destination.exists():
        return read_json(destination)
    started = time.perf_counter()
    original = read_json(OLD[30] / 'original_files_manifest.json')['files']
    entries = read_json(ROOT / 'data/rag/runtime/worktree-copy-manifest.json')['entries']
    model = [{'path': r['path'], 'size': r['bytes'], 'mtime_ns': r['source_marker']['mtime_ns'],
              'sha256': r['sha256']} for r in entries
             if r['path'].startswith('.cache/reranker/bge-reranker-v2-m3/')]
    paths = {r['path']: r for r in original + model}
    for row in paths.values():
        current = file_record(ROOT / row['path'])
        if current != row:
            raise RuntimeError(f'Preexisting original change: {row["path"]}')
    previous = []
    for base in OLD.values():
        previous.extend(p for p in base.rglob('*') if p.is_file())
    previous.extend(p for p in (ROOT / 'notebooks').glob('*') if p.is_file() and
                    any(p.name.startswith(f'{n}_') for n in OLD))
    previous.extend(p for p in (ROOT / 'docs/report').glob('*') if p.is_file() and
                    any(p.name.startswith(f'{n}_') for n in OLD))
    # User-owned unstaged dependency changes are also protected as they are now.
    previous.extend(ROOT / p for p in ['jobs/rag-indexer/pyproject.toml', 'packages/rag-core/pyproject.toml'])
    for path in previous:
        key = str(path.relative_to(ROOT))
        if key not in paths:
            paths[key] = file_record(path)
    snapshot = {'files': sorted(paths.values(), key=lambda r: r['path']),
                'original_files': len(original), 'model_files': len(model),
                'prior_experiment_and_user_files': len(paths) - len(original) - len(model),
                'created_at': time.time(), 'wall_seconds': time.perf_counter() - started,
                'contract': 'sha256+size+mtime; original manifests validated before snapshot; no new stage paths included'}
    write_json(destination, snapshot)
    return snapshot


def verify_preservation(stage: int = 34) -> dict:
    started = time.perf_counter()
    snapshot = read_json(STAGES[34] / 'protected_files.json')
    changed, missing = [], []
    for row in snapshot['files']:
        path = ROOT / row['path']
        if not path.is_file():
            missing.append(row['path'])
        elif file_record(path) != row:
            changed.append(row['path'])
    summary = {'checked_files': len(snapshot['files']), 'changed': changed, 'missing': missing,
               'passed': not changed and not missing, 'wall_seconds': time.perf_counter() - started}
    write_json(STAGES[stage] / 'preservation_verification.json', summary)
    if not summary['passed']:
        raise RuntimeError(f'Original preservation failed: {summary}')
    return summary


def ensure_private_copy(source: Path, destination: Path, target: Path, expected_sha: str | None = None) -> None:
    destination, target = Path(destination), Path(target)
    assert_private_directory(target)
    # Test the lexical path AND every ancestor before any writable client opens.
    for candidate in [destination, *destination.parents]:
        if candidate.is_symlink():
            raise RuntimeError(f'Copy path contains a symlink: {candidate}')
        if candidate == target:
            break
    if not destination.resolve().is_relative_to(target.resolve()):
        raise RuntimeError('Copy escapes private index directory')
    dest_stat, source_stat = destination.lstat(), Path(source).stat()
    if not stat_module.S_ISREG(dest_stat.st_mode):
        raise RuntimeError('Copy must be a regular file')
    if (dest_stat.st_dev, dest_stat.st_ino) == (source_stat.st_dev, source_stat.st_ino):
        raise RuntimeError('Copy is an original hardlink')
    if expected_sha is not None and file_record(destination)['sha256'] != expected_sha:
        raise RuntimeError('Immutable copied input SHA mismatch')


def validate_copy_receipt(result: dict, target: Path) -> None:
    source_inodes = {(p.stat().st_dev, p.stat().st_ino) for p in OLD[31].rglob('*') if p.is_file()}
    for row in result['files']:
        source, destination = ROOT / row['source'], ROOT / row['copy']
        if file_record(source)['sha256'] != row['sha256']:
            raise RuntimeError('Prior index changed after snapshot')
        # The independently copied Chroma sqlite can change its housekeeping
        # data on open. Vector/ID/metadata identity is checked by Experiment;
        # all other original index inputs must still match exact copy SHA.
        expected = None if destination.name == 'chroma.sqlite3' else row['sha256']
        ensure_private_copy(source, destination, target, expected)
    for destination in target.rglob('*'):
        if destination.is_symlink():
            raise RuntimeError('Private index contains a symlink')
        if destination.is_file():
            entry = destination.lstat()
            if (entry.st_dev, entry.st_ino) in source_inodes:
                raise RuntimeError('Private index contains an original hardlink')
    if target.is_symlink():
        raise RuntimeError('Private index root is a symlink')


def prepare_inputs() -> dict:
    initialize()
    snapshot = preservation_snapshot()
    writers = []
    readable_processes = 0
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            handles = list((process / 'fd').iterdir())
            readable_processes += 1
        except (OSError, PermissionError):
            continue
        for handle in handles:
            try:
                value = handle.resolve()
                if value.is_relative_to(OLD[31]):
                    info = (process / 'fdinfo' / handle.name).read_text()
                    flags = int(next(line.split()[1] for line in info.splitlines() if line.startswith('flags:')), 8)
                    if flags & os.O_ACCMODE:
                        writers.append({'pid': int(process.name), 'path': str(value.relative_to(ROOT))})
            except (OSError, ValueError, StopIteration):
                continue
    if writers:
        raise RuntimeError(f'Prior index has open writers: {writers}')
    for sidecar in OLD[31].rglob('*'):
        if sidecar.name.endswith('-wal') and sidecar.stat().st_size:
            raise RuntimeError('Nonempty index WAL requires consistent SQLite backup')
    write_json(STAGES[34] / 'index_writer_check.json', {
        'open_writers': writers, 'readable_processes': readable_processes,
        'scope': 'Only processes visible in this execution namespace. Other sessions/users may be invisible; no system-wide writer absence claim. Source hashes before/after copy and nonempty WAL check also required.',
        'nonempty_wal': False})
    target = STAGES[34] / 'indexes'
    assert_private_directory(target)
    receipt = STAGES[34] / 'index_copy.json'
    if receipt.exists():
        result = read_json(receipt)
        validate_copy_receipt(result, target)
        return result
    target.mkdir(exist_ok=True)
    profiles = ['card_page_section_benefit', 'parent_child_bundle']
    copied = []
    for profile in profiles:
        chunk_file = OLD[31] / f'{profile}_chunks.jsonl'
        out = target / chunk_file.name
        if out.is_symlink():
            raise RuntimeError('Chunk copy is a symlink')
        if out.exists():
            ensure_private_copy(chunk_file, out, target)
        if not out.exists():
            shutil.copy2(chunk_file, out)
        sources = [chunk_file] + [p for p in (OLD[31] / profile).rglob('*') if p.is_file()]
        for source in sources:
            dest = target / source.relative_to(OLD[31])
            assert_private_directory(dest.parent)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_symlink():
                raise RuntimeError('Index copy is a symlink')
            if dest.exists():
                ensure_private_copy(source, dest, target)
            if not dest.exists():
                shutil.copy2(source, dest)
            if source.stat().st_ino == dest.stat().st_ino or file_record(source)['sha256'] != file_record(dest)['sha256']:
                raise RuntimeError('Index input copy mismatch or hardlink')
            copied.append({'source': str(source.relative_to(ROOT)), 'copy': str(dest.relative_to(ROOT)),
                           'sha256': file_record(source)['sha256'], 'bytes': source.stat().st_size})
    result = {'files': copied, 'bytes': sum(r['bytes'] for r in copied),
              'protected_files': len(snapshot['files']),
              'note': 'Independent mutable Chroma copies; original tokenizer, facts and exact response/score caches read only'}
    write_json(receipt, result)
    validate_copy_receipt(result, target)
    for profile in profiles:
        for filename in ['corpus.sqlite', 'chroma/chroma.sqlite3']:
            source = target / profile / filename
            with sqlite3.connect(f'{source.as_uri()}?mode=ro&immutable=1', uri=True) as connection:
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Copied SQLite integrity failed')
    return result


if __name__ == '__main__':
    print(canonical(prepare_inputs()))
