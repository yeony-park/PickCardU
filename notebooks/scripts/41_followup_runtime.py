"""Sequential, resumable experiment runtime; operating sources stay read-only.

A script is required for paid request journaling, exact cache reuse, singleton
model calls and safe process-level resume; notebooks display the saved stages.
"""
if not __debug__:
    raise RuntimeError('Optimized Python cannot enforce experiment assertions')
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys
import time
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'notebooks/data'
STAGES = {41: DATA / '41_query_expansion', 42: DATA / '42_expanded_query_evaluation'}
HISTORICAL_PEAK_KIB = 2626724
PRIOR_SNAPSHOT_SHA256 = '6967099b28e0fdded6dbc88d0faef24db697bda75e37cc587bddbe1ed0b489b6'

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'notebooks/scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def digest(value):
    import hashlib
    return hashlib.sha256(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def read(path):
    return json.loads(Path(path).read_text())

def write(path, value):
    path = Path(path)
    if not any(path.absolute().is_relative_to(p) for p in STAGES.values()):
        raise RuntimeError('Output outside new experiment stages')
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise RuntimeError('Output symlink forbidden')
        if parent == ROOT:
            break
    if path.exists() and path.stat().st_nlink > 1:
        raise RuntimeError('Output hardlink forbidden')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.writing')
    if temporary.is_symlink():
        raise RuntimeError('Temporary symlink forbidden')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)

def freeze(path, value):
    result = {**value, 'sha256': digest(value)}
    if path.exists() and read(path) != result:
        raise RuntimeError('Frozen contract changed: ' + str(path))
    if not path.exists():
        write(path, result)
    return result

class ExperimentStop(BaseException):
    """A fatal resource stop bypasses ordinary answer-failure handlers."""

class Runtime:
    def __init__(self, stage):
        self.stage = stage
        self.path = STAGES[stage]
        self.path.mkdir(parents=True, exist_ok=True)
        lock = STAGES[41] / 'sequential.lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        self.lock = lock.open('a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExperimentStop('Another follow-up experiment is running')
        self.F = load('followup_io_' + str(stage), '34_followup_common.py')
        self.F.STAGES.update(STAGES)
        self.F.initialize(stage)
        self.A = load('followup_answer_' + str(stage), '35_answer_experiment.py')
        self.A.F.STAGES.update(STAGES)
        self.E = load('followup_schema_' + str(stage), '32_search_experiment.py')
        self.F.initialize(stage)
        self.M = load('followup_rubric_' + str(stage), '39_answer_rubric.py')
        self.PRICE = load('followup_price_' + str(stage), '36_cost_accounting.py')
        self.RECOVERY = load('followup_singleton_' + str(stage), '40_memory_recovery.py')
        self.begin = time.perf_counter()
        self.cpu_begin = resource.getrusage(resource.RUSAGE_SELF)
        self.telemetry = {'minimum_available_kib': None, 'peak_rss_kib': 0, 'singleton_calls': 0}
        self.cost_cap = Decimal(1 if stage == 41 else 3)
        self.request_cap = 100 if stage == 41 else 850

    def memory(self, starting=False):
        available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
        resident = self.RECOVERY.process_rss_kib()
        peak = max(HISTORICAL_PEAK_KIB, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        required = HISTORICAL_PEAK_KIB + 524288 if starting else max(1048576, max(peak - resident, 0) + 524288)
        self.telemetry.update(peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                              minimum_available_kib=min(self.telemetry['minimum_available_kib'] or available, available),
                              last_available_kib=available, last_required_kib=required)
        write(self.path / 'memory_telemetry.json', self.telemetry)
        if available < required:
            raise ExperimentStop(f'Memory available {available}KiB < required {required}KiB')

    def snapshot(self):
        original_path = DATA / '34_bundle_comparison/protected_files.json'
        if digest(original_path.read_bytes()) != PRIOR_SNAPSHOT_SHA256:
            raise RuntimeError('Original preservation snapshot changed')
        receipt_path = DATA / '40_new_query_comparison/authorized_document_change_binding.json'
        if not receipt_path.is_file():
            raise RuntimeError('Prior approved document receipt missing')
        boundary = load('followup_approved_boundary_' + str(self.stage), '40_authorized_document_change.py')
        if boundary.SNAPSHOT_SHA256 != PRIOR_SNAPSHOT_SHA256:
            raise RuntimeError('Prior snapshot pin differs from approved boundary')
        self.F.STAGES[40] = DATA / '40_new_query_comparison'
        # Existing receipt is mandatory above: bind_allowance only reads it and
        # independently verifies the literal SHA, approved Git blob and document.
        allowance = boundary.bind_allowance(self.F)
        path = self.path / 'protected_files.json'
        if path.exists():
            value = read(path)
            if digest({k: v for k, v in value.items() if k != 'sha256'}) != value['sha256']:
                raise RuntimeError('Preservation manifest digest mismatch')
            if value['approved_reference_change'] != allowance:
                raise RuntimeError('Approved reference binding changed')
            return value
        old = read(original_path)
        paths = {}
        for row in old['files']:
            expected = allowance['after'] if row['path'] == allowance['path'] else row
            if self.F.file_record(ROOT / row['path']) != expected:
                raise RuntimeError('Preexisting protected change: ' + row['path'])
            paths[row['path']] = expected
        for number in range(30, self.stage):
            for base in [ROOT / 'notebooks', ROOT / 'notebooks/scripts', ROOT / 'notebooks/notes', ROOT / 'docs/report']:
                for p in base.glob(str(number) + '_*'):
                    if p.is_file():
                        paths[str(p.relative_to(ROOT))] = self.F.file_record(p)
            for directory in DATA.glob(str(number) + '_*'):
                if not directory.is_dir():
                    continue
                for p in directory.rglob('*'):
                    if p.is_file() and 'execution_cache' not in p.parts and 'indexes' not in p.parts and p.name != 'sequential.lock':
                        paths[str(p.relative_to(ROOT))] = self.F.file_record(p)
        return freeze(path, {'files': list(paths.values()), 'approved_reference_change': allowance,
                              'prior_results_and_operating_sources_preserved': True})

    def verify(self):
        snapshot = self.snapshot()
        changed = [r['path'] for r in snapshot['files'] if not (ROOT / r['path']).is_file() or self.F.file_record(ROOT / r['path']) != r]
        result = {'passed': not changed, 'checked_files': len(snapshot['files']), 'changed': changed}
        write(self.path / 'preservation.json', result)
        if changed:
            raise RuntimeError('Protected source/result changed: ' + repr(changed[:5]))
        return result

    def cost(self):
        files = sorted((self.path / 'api_cache').glob('*.json'))
        rows = self.PRICE.price_records([(str(self.stage), str(p.relative_to(ROOT)), read(p)) for p in files])
        result = {'records': rows, 'summary': self.PRICE.aggregate(rows), 'rates': self.PRICE.RATES}
        write(self.path / 'cost_accounting.json', result)
        return result

    def reserve(self, request, output=2400, attempts=2):
        self.memory()
        summary = self.cost()['summary']
        if summary['unknown_cost_records']:
            raise ExperimentStop('Unpriced/interrupted request requires inspection before another call')
        if summary['record_count'] + attempts > self.request_cap:
            raise ExperimentStop('Physical request cap reservation reached')
        estimated = attempts * (Decimal(len(json.dumps(request, ensure_ascii=False).encode()) + 4096) * Decimal('.125') + Decimal(output) * Decimal('.50')) / 1000000
        if Decimal(summary['known_cost_usd']) + estimated > self.cost_cap:
            raise ExperimentStop('Known-cost cap reservation reached')

    def cache(self, kind='answer'):
        runtime = self
        class Cache(self.A.ResponseCache):
            def normalized(self, kwargs):
                value = super().normalized(kwargs)
                if kind == 'parser':
                    value['max_output_tokens'] = 3000
                return value
            def parse(self, **kwargs):
                key = self.key(kwargs)
                if not (self.cache / (key + '.json')).exists() and not (self.old_cache / (key + '.json')).exists():
                    normalized = self.normalized(kwargs)
                    request = {k: v for k, v in normalized.items() if k not in ['text_format', 'timeout']}
                    request['schema'] = kwargs['text_format'].model_json_schema()
                    runtime.reserve(request, normalized['max_output_tokens'])
                return super().parse(**kwargs)
        return Cache(cache=self.path / 'api_cache', old_cache=DATA / '40_new_query_comparison/api_cache',
                     policy='2400', allow_network=True, revalidate_failed=False)

    def embeddings(self, texts):
        import numpy as np
        directory = self.path / 'api_cache/embeddings'
        directory.mkdir(parents=True, exist_ok=True)
        keys = [digest({'text': t, 'model': 'text-embedding-3-small', 'dimension': 1536}) for t in texts]
        paths = {}
        for key in dict.fromkeys(keys):
            options = [directory / (key + '.npy'), DATA / '40_new_query_comparison/api_cache/embeddings' / (key + '.npy'),
                       DATA / '30_structured_benefit_db/api_cache/embeddings' / (key + '.npy')]
            paths[key] = next((p for p in options if p.exists()), None)
        missing = list(dict.fromkeys(key for key in keys if paths[key] is None))
        for offset in range(0, len(missing), 32):
            batch = missing[offset:offset + 32]
            request = {'model': 'text-embedding-3-small', 'input': [texts[keys.index(key)] for key in batch], 'dimensions': 1536}
            record = directory.parent / ('embedding_batch_' + digest(request) + '.json')
            if record.exists():
                raise ExperimentStop('Interrupted embedding request needs explicit recovery')
            self.reserve(request, 0, 1)
            write(record, {'status': 'started', 'request': request, 'started_at': time.time()})
            response = self.E.C.api_client().embeddings.create(**request)
            self.F.initialize(self.stage)
            raw = response.model_dump(mode='json')
            # Journal raw usage before validation/normalization, including failed rows.
            write(record, {'status': 'started', 'request': request, 'raw_response': raw, 'usage': raw.get('usage', {})})
            try:
                values = sorted(response.data, key=lambda r: r.index)
                if len(values) != len(batch):
                    raise ValueError('Embedding count mismatch')
                for key, value in zip(batch, values, strict=True):
                    vector = np.asarray(value.embedding, dtype=np.float32)
                    if vector.shape != (1536,) or not np.isfinite(vector).all():
                        raise ValueError('Invalid embedding')
                    paths[key] = directory / (key + '.npy')
                    np.save(paths[key], vector)
                write(record, {'status': 'ok', 'request': request, 'raw_response': raw, 'usage': raw['usage'], 'finished_at': time.time()})
            except Exception as error:
                write(record, {'status': 'failed', 'request': request, 'raw_response': raw, 'usage': raw.get('usage', {}), 'error': str(error)})
                raise
        vectors = [np.load(paths[key], allow_pickle=False) for key in keys]
        if any(v.shape != (1536,) or not np.isfinite(v).all() for v in vectors):
            raise RuntimeError('Cached embedding invalid')
        return np.asarray(vectors, dtype=np.float32)

    def experiment(self):
        parity = read(DATA / '40_new_query_comparison/memory_parity.json')
        if not parity['passed']:
            raise RuntimeError('Prior singleton parity did not pass')
        self.RECOVERY.validate_parity_baselines(type('C', (), {'ROOT': ROOT, 'digest': staticmethod(digest), 'read': staticmethod(read)}), parity)
        B = load('followup_bundle_' + str(self.stage), '34_bundle_experiment.py')
        B.F.STAGES.update(STAGES)
        B.F.STAGES[34] = self.path
        B.F.preservation_snapshot = self.snapshot
        x = B.FollowupExperiment()
        self.F.initialize(self.stage)
        def monitor(mode, question, document, phase, trace):
            if phase == 'before':
                self.memory()
            else:
                self.telemetry['singleton_calls'] += 1
                self.memory()
        x.scorer.delegate = self.RECOVERY.OneDocumentReranker(x.scorer.delegate, monitor=monitor)
        self.x, self.B = x, B
        return x

    def search(self, question, embedding, profile, keys=None):
        key = self.x.component_key(question, profile, keys)
        path = self.path / 'components' / (key + '.json')
        identity = {'question_sha256': self.B.query_identity(question), 'embedding_sha256': self.B.query_identity(embedding),
                    'profile': profile, 'scope': list(keys) if keys is not None else None,
                    'chunks_sha256': digest((DATA / '31_operational_chunking_comparison' / (profile + '_chunks.jsonl')).read_bytes())}
        if path.exists():
            value = read(path)
        else:
            value = freeze(path, {**self.x.capture_component(question, embedding, profile, keys), 'identity': identity})
        if value['identity'] != identity or digest({k: v for k, v in value.items() if k != 'sha256'}) != value['sha256']:
            raise RuntimeError('Frozen component binding mismatch')
        self.x.components[key] = {k: v for k, v in value.items() if k != 'sha256'}
        return self.x.search(question, embedding, profile, keys, 'budget5')

    def answer(self, question, evidence):
        from pickcardu_rag.answering import OpenAIService
        api = self.cache()
        if evidence:
            answer, usage = OpenAIService(api_key=None, client=api, llm_model='gpt-6-luna', answer_payload_bytes=64000).answer(question, evidence)
            return answer.model_dump(mode='json'), usage, api.events
        return {'answer_status': 'insufficient_evidence', 'answer_text': '검색 근거가 없습니다.', 'claims': [], 'recommendations': []}, {}, []

    def resources(self):
        now = resource.getrusage(resource.RUSAGE_SELF)
        return {**self.telemetry, 'wall_seconds_this_process': time.perf_counter() - self.begin,
                'cpu_seconds_this_process': now.ru_utime + now.ru_stime - self.cpu_begin.ru_utime - self.cpu_begin.ru_stime,
                'new_bge_pairs_this_process': sum(r['new_pairs'] for r in self.x.scorer.calls) if hasattr(self, 'x') else 0,
                'gpu': 'CPU 실행', 'billed_cost': '미측정', 'transmitted_tokens': '미측정'}
