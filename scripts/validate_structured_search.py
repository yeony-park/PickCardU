"""Bounded integration verification with durable raw usage before SDK parsing.

A script owns interruption/budget guards and serial API requests; a notebook
alone cannot reliably enforce these across resumed or failed requests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
QUERY_IDS = ('C01','C02','C03','C05','C09','C10','C11','C12','C13','C14','C16','C18')
PROFILES = ('card_page_section_benefit', 'parent_child_bundle')
DEFAULT_OUTPUT = ROOT / 'data/rag/validation/structured_search_integration'
RATES = {'gpt-6-luna': ('.10','.01','.125','.50'), 'gpt-5.6-luna': ('.20','.02','.25','1.20')}
RATE_SOURCES = [f'https://developers.openai.com/api/docs/models/{model}' for model in RATES]


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def response_payload(response):
    try:
        return response.json()
    except ValueError:
        return {'code': 'NON_JSON_RESPONSE', 'body': response.text}


def price(usage, model):
    if not usage:
        return None
    if model == 'text-embedding-3-small':
        value = usage.get('prompt_tokens')
        return Decimal(value) * Decimal('.02') / 1000000 if type(value) is int and value >= 0 else None
    if model not in RATES:
        return None
    details = usage.get('input_tokens_details') or {}
    values = [usage.get('input_tokens'), usage.get('output_tokens'), details.get('cached_tokens'), details.get('cache_write_tokens')]
    if any(type(v) is not int or v < 0 for v in values):
        return None
    inp, out, cached, written = values
    if cached + written > inp:
        return None
    ordinary_rate, cached_rate, written_rate, output_rate = map(Decimal, RATES[model])
    input_cost = (inp-cached-written)*ordinary_rate + cached*cached_rate + written*written_rate
    return (input_cost*(2 if inp > 272000 else 1) + out*output_rate*(Decimal('1.5') if inp > 272000 else 1))/1000000


class Budget:
    def __init__(self, directory: Path, historical: Decimal):
        self.directory, self.historical = directory, historical
        directory.mkdir(parents=True, exist_ok=True)

    def rows(self):
        return [read(p) for p in sorted(self.directory.glob('*.json'))]

    def check(self):
        rows = self.rows()
        if any(r.get('cost_usd') is None for r in rows):
            raise RuntimeError('unknown or pending usage: no subsequent paid calls')
        return self.historical + sum((Decimal(r['cost_usd']) for r in rows), Decimal(0))

    def reserve(self, identity, phase, request):
        if (self.directory.parent / 'HALTED.json').exists():
            raise RuntimeError('validation halted by quality guard; no further network calls')
        debit = self.check()
        rows = self.rows()
        if phase not in ('extract', 'embedding', 'answer'):
            raise RuntimeError('unknown request phase')
        if len({r['identity'] for r in rows} | {identity}) > 24:
            raise RuntimeError('24 logical answers limit reached')
        attempt = 1 + sum(r['identity'] == identity and r['phase'] == phase for r in rows)
        if attempt > (2 if phase == 'answer' else 1):
            raise RuntimeError('physical attempt limit reached')
        model = request['model']
        input_upper = len(json.dumps(request, ensure_ascii=False).encode()) + 8192
        if model == 'text-embedding-3-small':
            upper = Decimal(input_upper)*Decimal('.02')/1000000
        else:
            if model not in RATES:
                raise RuntimeError('unpriced model: no network call')
            _, _, written, output = map(Decimal, RATES[model])
            upper = (input_upper*written*(2 if input_upper > 272000 else 1) +
                     request['max_output_tokens']*output*(Decimal('1.5') if input_upper > 272000 else 1))/1000000
        if debit + upper > 8:
            raise RuntimeError('cumulative $8 budget exceeded')
        path = self.directory / f'{identity}:{phase}:{attempt}.json'
        if path.exists():
            raise RuntimeError('request journal already exists')
        save(path, dict(identity=identity, phase=phase, attempt=attempt, request=request,
                        status='pending', cost_usd=None, reserved_usd=str(upper)))
        return path


def journal_transport(budget):
    import httpx2

    class JournalTransport(httpx2.BaseTransport):
        def __init__(self):
            self.delegate, self.identity = httpx2.HTTPTransport(retries=0), None

        def handle_request(self, request):
            body = json.loads(request.content)
            phase = ('embedding' if request.url.path.endswith('/embeddings') else
                     'extract' if body.get('text', {}).get('format', {}).get('name') == 'query_plan' else 'answer')
            path = budget.reserve(self.identity, phase, body)
            row, started = read(path), time.perf_counter()
            try:
                response = self.delegate.handle_request(request)
                response.read()
                raw = response.json()
                cost = price(raw.get('usage'), body['model'])
                if raw.get('service_tier', 'default') != 'default' or raw.get('model', body['model']) != body['model']:
                    cost = None
                row.update(status='received', http_status=response.status_code, raw_response=raw,
                           usage=raw.get('usage'), cost_usd=None if cost is None else str(cost))
                return response
            except Exception as error:
                row.update(status='failed', error_type=type(error).__name__)
                raise
            finally:
                row['wall_ms'] = round((time.perf_counter()-started)*1000, 3)
                save(path, row)

        def close(self):
            self.delegate.close()

    return JournalTransport()


def offline(output):
    from pickcardu_rag.structured import QueryPlan, StructuredStore, narrowing_reason, explicit_scope
    store = StructuredStore(ROOT / 'notebooks/data/60_structured_retrieval_repair/db_v3/structured.sqlite')
    db = store.connect()
    try:
        catalog = [dict(card_key=k, card_name=n, issuer=i) for k,n,i in db.execute('SELECT document_id,card_name,issuer_name FROM documents')]
    finally:
        db.close()
    source = ROOT / 'notebooks/data/70_cap_context_repair/retrieval_results.json'
    records = []
    for r in read(source)['records']:
        if r['mode'] != 'SEQ65' or 'plan' not in r['routing']:
            continue
        plan = QueryPlan.model_validate(r['routing']['plan'])
        scope = r['original_scope']['card_keys']
        actual = list(store.query(plan, None if scope is None else tuple(scope)))
        literal = explicit_scope(r['question'], catalog)
        records.append(dict(identity=r['identity'], query_id=r['query_id'], candidates=actual,
            candidates_equal=actual == r['routing']['eligible_cards'],
            scope_equal=(None if literal is None else list(literal)) == scope,
            operational_bypass=narrowing_reason(plan)))
    result = dict(source_sha256=digest(source), records=records, count=len(records),
                  candidate_mismatches=sum(not r['candidates_equal'] for r in records),
                  scope_mismatches=sum(not r['scope_equal'] for r in records))
    save(output / 'offline_replay.json', result)
    if not records or result['candidate_mismatches'] or result['scope_mismatches']:
        raise RuntimeError('offline DB3 replay mismatch')
    return result


def run(output):
    import sys
    import httpx2
    from dotenv import load_dotenv
    from openai import OpenAI
    from fastapi.testclient import TestClient
    from pickcardu_rag import LocalReranker, OpenAIService, SearchConfig
    from pickcardu_rag.structured_extraction import StructuredExtractor
    from pickcardu_rag_api.config import load_settings
    from pickcardu_rag_api.index import ActiveIndexLoader
    from pickcardu_rag_api.main import create_app

    if (output / 'HALTED.json').exists():
        raise RuntimeError('validation halted by quality guard; inspect the recorded failure')
    offline(output)
    load_dotenv(ROOT / '.env', override=False)
    settings = load_settings()
    historical_path = ROOT / 'notebooks/data/73_repaired_card_scope/completion_audit.json'
    historical = Decimal(str(read(historical_path)['cumulative_conservative_usd']))
    budget = Budget(output / 'requests', historical)
    budget.check()
    questions_path = ROOT / 'notebooks/data/70_cap_context_repair/questions.json'
    questions = {r['query_id']: r['question'] for r in read(questions_path)['queries']}
    manifest = dict(questions={k:questions[k] for k in QUERY_IDS}, profiles=PROFILES, max_answers=24,
        historical_usd=str(historical), historical_sha256=digest(historical_path),
        rates=RATES, rate_sources=RATE_SOURCES, rate_verified='2026-10-28',
        code_sha256={str(p.relative_to(ROOT)):digest(p) for base in (
            ROOT/'packages/rag-core/src', ROOT/'services/rag-api/src') for p in sorted(base.rglob('*.py'))},
        runner_sha256=digest(Path(__file__)), llm_model=settings.llm_model, structured_model=settings.structured_model,
        answer_settings='unchanged; output 2400, reasoning provider default, max 2 attempts',
        releases={p:read(ROOT/'data/rag/runtime/structured-validation'/p/'active-index.json') for p in PROFILES})
    manifest_path = output / 'run_manifest.json'
    if manifest_path.exists() and read(manifest_path) != json.loads(json.dumps(manifest)):
        raise RuntimeError('validation manifest changed; do not silently resume')
    save(manifest_path, manifest)
    transport = journal_transport(budget)
    client = OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=60,
                    http_client=httpx2.Client(transport=transport, timeout=60))
    reranker = LocalReranker(str(settings.bge_model_path))
    started, cpu_started = time.perf_counter(), time.process_time()
    for profile in PROFILES:
        runtime = ROOT / 'data/rag/runtime/structured-validation' / profile
        real_loader = ActiveIndexLoader(runtime, reranker=reranker, require_structured=True)
        handle = real_loader.load()
        last_search, last_embedding = {}, {}
        def search(q, embedding, config):
            result = handle.search(q, embedding, config)
            last_search.update(result=result, config=config)
            return result
        view = SimpleNamespace(**{**handle.__dict__, 'search': search})
        loader = SimpleNamespace(load=lambda: view)
        provider = OpenAIService(api_key=settings.openai_api_key, embedding_model=settings.embedding_model,
                                 llm_model=settings.llm_model, client=client, answer_payload_bytes=settings.answer_payload_bytes)
        embed = provider.embed
        def embedding(q):
            value, usage = embed(q)
            last_embedding.update(value=value, usage=usage)
            return value, usage
        provider.embed = embedding
        app = create_app(replace(settings, index_runtime_root=runtime, retrieval_mode='structured_then_hybrid'),
            provider=provider, reranker=reranker, index_loader=loader,
            structured_extractor=StructuredExtractor(api_key=settings.openai_api_key, model=settings.structured_model, client=client))
        with TestClient(app, raise_server_exceptions=False) as api:
            assert api.get('/v1/health/ready').status_code == 200
            for qid in QUERY_IDS:
                identity = qid + ':' + profile
                destination = output / 'answers' / (identity + '.json')
                if destination.exists():
                    continue
                transport.identity = identity
                last_search.clear(); last_embedding.clear()
                tick = time.perf_counter()
                response = api.post('/v1/answer', json={'query':questions[qid], 'profile':profile, 'top_k':3})
                api_ms = (time.perf_counter()-tick)*1000
                record = dict(identity=identity, query_id=qid, question=questions[qid], profile=profile,
                              http_status=response.status_code, response=response_payload(response), api_ms=api_ms)
                if last_search and last_embedding:
                    result = last_search['result']
                    record['sequential'] = result
                    baseline = handle.search(questions[qid], last_embedding['value'], SearchConfig(
                        profile=profile, reranker='bge', reranker_route='all' if profile=='parent_child_bundle' else 'selective',
                        answer_query=questions[qid], answer_payload_bytes=settings.answer_payload_bytes))
                    record['hybrid'] = baseline
                    allowed = last_search['config'].allowed_card_keys
                    record['scope_escape'] = allowed is not None and any(e['card_key'] not in allowed for e in result['evidence'])
                    record['missing_required_groups'] = result['trace']['scope'].get('missing_required_groups', [])
                save(destination, record)
                debit = budget.check()
                print(identity, response.status_code, record['response'].get('usage',{}).get('structured',{}).get('reason'), 'cumulative', debit, flush=True)
                save(output / 'progress.json', dict(completed=len(list((output/'answers').glob('*.json'))),
                    cumulative_usd=str(debit), wall_seconds=time.perf_counter()-started,
                    cpu_seconds=time.process_time()-cpu_started, peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
                if response.status_code != 200:
                    raise RuntimeError('API integration failure; inspect saved raw response before continuing')
    client.close()


def main():
    import sys
    sys.path[:0] = [str(ROOT/'packages/rag-core/src'), str(ROOT/'services/rag-api/src')]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args()
    if args.allow_network:
        run(args.output)
    else:
        result = offline(args.output)
        print(json.dumps({k:v for k,v in result.items() if k != 'records'}))


if __name__ == '__main__':
    main()
