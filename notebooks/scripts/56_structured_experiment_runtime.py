"""Shared resume, DB-version binding and paid-request guards for notebooks56–59.

Notebooks own experimental choices. This helper owns durable raw-before-parse
requests, combined old/new-parser budgets and SQLite/JSON version consistency;
duplicating those safeguards in several interactive notebooks risks mixed runs.
"""
from contextlib import contextmanager
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('experiment56_legacy', Path(__file__).with_name('51_experiment_runtime.py'))
X = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = X
spec.loader.exec_module(X)
X.load('recovery56', '51_budget_recovery.py').install(X)
X.load('predicate56', '52_query_predicate_correction.py').install(X)
P, Q = X.P, X.Q
BASE = P.DATA / '56_structured_normalization'
DB1 = P.DATA / '30_structured_benefit_db'
DB2 = BASE / 'db_v2'
LEAVES = {56: '56_structured_normalization',
          560: '56_structured_normalization/parser_old', 561: '56_structured_normalization/parser_new',
          57: '57_sequential_condition_evidence', 58: '58_generation_factor_comparison',
          59: '59_new_sequential_confirmation'}
for stage, directory in LEAVES.items():
    X.STAGES[stage] = P.STAGES[stage] = directory
    for common in [P.F, P.A.F, Q.P.F, Q.P.A.F]:
        common.STAGES[stage] = P.DATA / directory

NEW_PROMPT = Q.PROMPT + '''
추가 구분 규칙:
- 요청한 설명과 실제 검색 제약을 먼저 구분하세요. 어떤 값인지 묻는 실적·한도·횟수는 requested_fields입니다.
- '한도는 얼마', '월 한도를 설명'은 min_cap_krw=null이며 cap_period=any입니다. '월 한도 1만원 이상'만 min_cap_krw=10000, cap_period=month입니다.
- 전월실적 없는 카드를 명시적으로 찾으면 max_spend_krw=0입니다. 이용자의 전월실적이 0원이라는 상황 설명을 이 필터로 만들지 마세요.
- 지급 할인액 한도와 할인 대상 결제금액 한도는 다릅니다. 후자의 크기 요청은 cap 필터로 바꾸지 마세요.
- 할인받을 수 있는가를 묻는 고정 혜택 대상은 조건에 넣고, 요일·시간·결제경로의 적용 여부 질문은 설명 요청으로 둡니다.
- '특정 시간에 할인되는 카드', '정액 300원인 카드'처럼 스키마가 표현하지 못하는 명시적 제약은 unsupported_filters에 보존하세요.
- 카드명, 발급사, 원문에 없는 브랜드·동의어를 혜택 대상으로 추가하지 마세요. 기차와 철도는 서로 바꾸지 않습니다.
- 커피전문점은 질문에 커피가 명시된 경우 기존 커피 범주로 표현할 수 있습니다. 주유를 특정 정유사로 바꾸지 않습니다.
'''


def read(path):
    return P.F.read_json(path)


def write(path, value):
    P.F.write_json(path, value)


def guard_files(bindings):
    for relative, expected in bindings.items():
        if P.sha((ROOT / relative).read_bytes()) != expected:
            raise RuntimeError('Frozen data changed: ' + relative)


def fact_map(directory):
    return {r['fact_id']: r for r in map(json.loads, (directory / 'facts.jsonl').read_text().splitlines())}


@contextmanager
def bound_facts(module, facts):
    """The SQL UDF must see the same version as its SQLite file; always restore."""
    previous = module.facts_by_id
    try:
        module.facts_by_id = lambda: facts
        yield
    finally:
        module.facts_by_id = previous


def query_database(plan, scope, directory, *, facts=None):
    with bound_facts(Q, facts if facts is not None else fact_map(directory)):
        return Q.query(plan, scope, database=directory / 'structured.sqlite')


def route(plan, original, directory=DB2, *, facts=None, parser_failed=False):
    base = dict(route='U', scope=original['card_keys'], eligible_cards=[], matching_fact_ids={}, db_attempted=False)
    if original['clarify']:
        return {**base, 'route': 'clarify', 'reason': original['reason']}
    if parser_failed:
        return {**base, 'reason': 'parser_failure'}
    result = query_database(plan, original['card_keys'], directory, facts=facts)
    cards = result['eligible_cards']
    return {**base, **result, 'route': 'SEQ' if cards else 'U',
            'scope': cards or original['card_keys'], 'reason': 'matched' if cards else 'no_results'}


def phase_price(stages):
    records = [(str(s), str(p.relative_to(ROOT)), read(p)) for s in stages
               for p in sorted((P.F.STAGES[s] / 'api_cache').glob('*.json'))]
    return P.PRICE.aggregate(P.PRICE.price_records(records))


def request_upper_bound(wire, output):
    count = len(P.F.canonical(wire).encode()) + 8192
    return (Decimal(count) * Decimal('.25' if count > 272000 else '.125') +
            Decimal(output) * Decimal('.75' if count > 272000 else '.50')) / 1000000


def require_budget(priced, reservation, cap):
    if priced['unknown_cost_records']:
        raise RuntimeError('Unknown request cost: stop before another paid request')
    if Decimal(priced['known_cost_usd']) + reservation > Decimal(cap):
        raise RuntimeError('Combined phase cost allocation reached')


class Cache(X.Cache):
    def check_contract(self, kwargs):
        X.guard_manifest(self.manifest)
        guard_files(self.manifest.get('critical_data_sha256', {}))
        normalized = self.normalized(kwargs)
        if self.kind == 'answer':
            expected = self.manifest['answer_instructions_sha256_by_identity'][self.identity]
            if P.sha(normalized['instructions']) != expected:
                raise RuntimeError('Answer prompt differs from frozen identity')
        if self.kind == 'parser' and P.sha(normalized['instructions']) != self.manifest['parser_instructions_sha256']:
            raise RuntimeError('Parser prompt differs from frozen identity')
        return normalized

    def parse(self, **kwargs):
        self.check_contract(kwargs)
        return super().parse(**kwargs)

    def reserve(self, normalized):
        group = [560, 561] if self.stage in [560, 561] else [self.stage]
        cap = '.50' if self.stage in group and self.stage in [560, 561] else self.manifest['stage_cost_cap_usd']
        require_budget(phase_price(group), request_upper_bound(self.wire_kwargs(normalized), normalized['max_output_tokens']), cap)
        return super().reserve(normalized)


def extract(stage, question, repeat, *, allow_network=False):
    identity = f"{question['query_id']}:parser:r{repeat}"
    cache = Cache(stage, 'parser', identity, allow_network=allow_network)
    prompt = Q.PROMPT if stage == 560 else NEW_PROMPT
    kwargs = dict(model='gpt-6-luna', instructions=prompt,
                  input=[dict(role='user', content=question['question'])], text_format=Q.QueryPlan,
                  tools=[], store=False, max_output_tokens=3000)
    cache.check_contract(kwargs)
    target = P.F.STAGES[stage] / 'plans' / (identity + '.json')
    if target.exists():
        row = read(target)
    else:
        start, cpu = time.perf_counter(), time.process_time()
        value, error = None, None
        try:
            response = cache.parse(**kwargs)
            value = response.output_parsed.model_dump(mode='json') if response.output_parsed else None
        except Exception as exc:
            if not cache.events:
                raise
            error = f'{type(exc).__name__}: {exc}'
        row = dict(identity=identity, query_id=question['query_id'], repeat=repeat, question=question['question'],
                   question_sha256=P.sha(question['question']), manifest_sha256=P.sha(cache.manifest),
                   request_key=cache.key(kwargs), status='ok' if value is not None else 'failed', error=error,
                   plan=value, events=cache.events, parser_wall_seconds=time.perf_counter()-start,
                   parser_cpu_seconds=time.process_time()-cpu)
        write(target, row)
    P.validate_parser_row(row, identity=identity, query_id=question['query_id'], repeat=repeat,
        question=question['question'], manifest=cache.manifest, request_key=cache.key(kwargs), cache=cache.cache)
    return row


def validate_question_pairs(pairs):
    """Verify frozen manual equivalence receipts, not automatic semantic proof."""
    for pair in pairs:
        signatures = []
        for form in ['short', 'detailed']:
            receipt = pair['condition_review'][form]
            if receipt['question_sha256'] != P.sha(pair[form+'_question']):
                raise RuntimeError('Condition review refers to different question text')
            if not receipt.get('reviewer') or not receipt.get('reason'):
                raise RuntimeError('Missing manual condition review')
            signatures.append(receipt['explicit_condition_signature'])
        if signatures[0] != signatures[1]:
            raise RuntimeError('Paired question changed explicit condition: ' + pair['query_id'])


def prepare56(notebook):
    """Freeze the complete paired matrix only after the independently checked DB gate."""
    gate = read(BASE / 'db_source_review' / 'validation.json')
    acceptance = read(BASE / 'db_source_review' / 'main_acceptance.json')
    if (gate['source_mismatch_count'] or gate['source_uncertain_count'] or
            acceptance['status'] != 'accepted' or acceptance['validation_sha256'] != P.sha(gate)):
        raise RuntimeError('DB source gate not accepted')
    pairs = read(BASE / 'question_pairs_draft.json')['records']
    validate_question_pairs(pairs)
    queries = []
    for q in pairs:
        scope = Q.original_scope(q['detailed_question'])
        if scope != Q.original_scope(q['short_question']):
            raise RuntimeError('Paired question changed original card/issuer scope: ' + q['query_id'])
        for form in ['short', 'detailed']:
            queries.append(dict(query_id=q['query_id'] + '_' + form,
                                question=q[form + '_question'], source_query_id=q['query_id'], form=form))
    assert len(queries) == 74
    critical = {str(p.relative_to(ROOT)): P.sha(p.read_bytes()) for p in [
        BASE / 'question_pairs_draft.json', BASE / 'db_source_review' / 'validation.json',
        BASE / 'db_source_review' / 'main_acceptance.json',
        BASE / 'db_source_review' / 'normalization_source_review.json',
        BASE / 'db_source_review' / 'manual_image_reviews.json',
        BASE / 'db_comparison_input_contract.json',
        BASE / 'stored_plan_db_comparison.json', BASE / 'preserved54_db_comparison.json',
        BASE / 'manual_condition_db_comparison.json',
        P.DATA / '52_corrected_four_retrieval_modes' / 'rerouted_repeat1_plans.json',
        P.DATA / '54_new_question_confirmation' / 'parser_results.json'] + [d / n for d in [DB1, DB2]
                                                              for n in ['structured.sqlite', 'facts.jsonl', 'documents.jsonl']]}
    root = dict(purpose='separate wording, parser and DB effects', logical_parser_requests=444,
                combined_cost_cap_usd='.50', leaf_stages=[560, 561], questions_sha256=P.sha(queries),
                critical_data_sha256=critical, same_card_and=True, no_synonym_expansion=True,
                parser_selection='highest human-reviewed explicit-condition accuracy, mean over forms/repeats within question then 37-question macro; tie retains old parser',
                downstream_plan='selected parser detailed wording repeat1; original three nonanswerable questions retain preserved plans')
    X.frozen(BASE / 'execution_manifest.json', root)
    sources = set(read(P.F.STAGES[49]/'execution_manifest.json')['source_sha256']) | {
        'notebooks/scripts/50_query_contract.py', 'notebooks/scripts/51_experiment_runtime.py',
        'notebooks/scripts/56_structured_experiment_runtime.py', 'notebooks/scripts/56_normalize_experiment_db.py'}
    for stage, prompt in [(560, Q.PROMPT), (561, NEW_PROMPT)]:
        identities = [f"{q['query_id']}:parser:r{r}" for q in queries for r in [1, 2, 3]]
        X.make_manifest(stage, notebook, queries, dict(parser=identities, answer=[]),
            maximum_parser=222, stage_cap='.50', extra=dict(critical_data_sha256=critical,
                parent_execution_manifest_sha256=P.sha(root), parser_instructions_sha256=P.sha(prompt),
                source_sha256={name:P.sha((ROOT/name).read_bytes()) for name in sorted(sources)}))
    X.frozen(BASE / 'questions.json', dict(queries=queries))
    return dict(root=root, queries=queries)


def run56(*, allow_network=False):
    queries = read(BASE / 'questions.json')['queries']
    root = read(BASE / 'execution_manifest.json')
    guard_files(root['critical_data_sha256'])
    maps = [fact_map(DB1), fact_map(DB2)]
    records = []
    start, cpu = time.perf_counter(), time.process_time()
    for stage in [560, 561]:
        for q in queries:
            for repeat in [1, 2, 3]:
                parser = extract(stage, q, repeat, allow_network=allow_network)
                original = Q.original_scope(q['question'])
                routes = [route(parser['plan'], original, directory, facts=facts,
                                parser_failed=parser['status'] != 'ok')
                          for directory, facts in zip([DB1, DB2], maps)]
                records.append(dict(source_query_id=q['source_query_id'], form=q['form'],
                    parser_version='old' if stage == 560 else 'new', parser=parser,
                    original_scope=original, db_v1=routes[0], db_v2=routes[1]))
                write(BASE / 'progress.json', dict(completed=len(records), expected=444,
                    last_identity=f'{stage}:' + parser['identity'], wall_seconds=time.perf_counter()-start))
                if len(records) % 12 == 0:
                    print('56 parser', len(records), '/444', flush=True)
                # A new failure with unknown usage must stop immediately, even
                # if it happened on the final request in the matrix.
                if X.budget_upper_ledger()['unknown_cost_records']:
                    raise RuntimeError('New request has unknown usage; remaining matrix stopped')
    assert len(records) == 444
    write(BASE / 'parser_results.json', dict(records=records))
    result = dict(logical_requests=444, physical_price=phase_price([560,561]),
                  invocation_wall_seconds=time.perf_counter()-start,
                  invocation_cpu_seconds=time.process_time()-cpu,
                  parser_timing=X.timing_summary([r['parser']['parser_wall_seconds'] for r in records]),
                  sql_v1_timing=X.timing_summary([r['db_v1'].get('db_wall_seconds') for r in records]),
                  sql_v2_timing=X.timing_summary([r['db_v2'].get('db_wall_seconds') for r in records]))
    import resource
    result.update(peak_process_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  available_memory_end_kib=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))),
                  memory_method='RSS process high-water mark; end-only host available memory, not continuous minimum',
                  ttft_seconds=None, browser_display_seconds=None, new_embeddings=0)
    write(BASE / 'execution_resources.json', result)
    return result
