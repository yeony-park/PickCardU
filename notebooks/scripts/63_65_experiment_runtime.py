"""Minimal resumable adapter for fixed-profile64/65; no HTML generation.

Reuses the sealed operating adapters and raw-provider journals. Notebooks own
stage boundaries and manual source judgments. Only new stage folders are written.
"""
from collections import Counter
import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = load('preserved60_for65', '60_62_experiment_runtime.py')
S = load('scope64_shared', '64_scope_repair.py')
E = load('core63_shared', '63_core_evaluation.py')
P, R, X, Q, G = M.P, M.R, M.X, M.Q, M.G
PROFILES = tuple(M.C.PROFILES)
LEAVES = {63: '63_core_evaluation_alignment', 64: '64_scope_boundary_comparison',
          65: '65_fixed_profile_confirmation'}
for stage, leaf in LEAVES.items():
    P.STAGES[stage] = X.STAGES[stage] = leaf
    for common in [P.F, P.A.F, Q.P.F, Q.P.A.F]:
        common.STAGES[stage] = P.DATA/leaf
B63, B64, B65 = (P.F.STAGES[s] for s in (63, 64, 65))
read, write, frozen = R.read, R.write, X.frozen


def guard_freeze():
    freeze = read(B65/'question_freeze.json')
    R.guard_files(freeze['files_sha256'])
    queries = read(B65/'questions.json')['queries']
    if (len(queries) != 48 or len({q['query_id'] for q in queries}) != 48
            or Counter(q['group'] for q in queries) != {'explicit': 36, 'general': 8, 'uncertain': 4}):
        raise RuntimeError('Approved48 question contract changed')
    return freeze, queries


def register_boundary():
    """Materialize fixed test fixtures, never use them as LLM extraction gold."""
    tests = load('boundary64_fixtures', '64_test_scope_repair.py')
    cases = [dict(query_id=f'B{i:02}', question=q, expected_scope=keys,
                  category=('merchant_payment' if i <= 8 else 'issuer_product' if i <= 16 else 'general_multiple'))
             for i, (q, keys) in enumerate(tests.SCOPE_CASES, 1)]
    frozen(B64/'boundary_cases.json', dict(records=cases, scope_cases=24, route_regressions=8,
        test_source_sha256=P.sha((ROOT/'notebooks/scripts/64_test_scope_repair.py').read_bytes())))
    return cases


def source_inventory():
    inventory = copy.deepcopy(read(M.B60/'input_inventory.json'))
    # Read-only inherited score records are explicitly covered by accepted61.
    inherited = read(M.B62/'completion.json')['files_sha256']
    for name, expected in inherited.items():
        if '/bge_cache/' in name or '/score_contracts/' in name:
            inventory['files_sha256'][name] = expected
    return inventory


def accepted64_closure():
    completion = read(B64/'completion.json')
    files = completion.get('files_sha256', {})
    if completion.get('status') != 'accepted_scope_and_retrieval_review' or not files:
        raise ValueError('Incomplete64 acceptance closure')
    names = ('evaluation.json','retrieval_results.json','jobs.json','scope_comparisons.json',
             'source_review/retrieval_judgments.json')
    required = {str((B64/n).relative_to(ROOT)) for n in names}
    review_path = completion.get('independent_review_path')
    if not required <= set(files) or review_path not in files:
        raise ValueError('Missing64 acceptance source/evaluation/independent closure')
    R.guard_files(files)
    for field, name in [('evaluation_raw_sha256','evaluation.json'), ('retrieval_raw_sha256','retrieval_results.json'),
                        ('source_review_raw_sha256','source_review/retrieval_judgments.json')]:
        if completion.get(field) != files[str((B64/name).relative_to(ROOT))]:
            raise ValueError('Stale64 acceptance binding: '+field)
    review = read(ROOT/review_path)
    if (review.get('status') != 'accepted' or completion.get('independent_review_raw_sha256') != files[review_path]
            or any(review.get('reviewed_files_sha256', {}).get(n) != files[n] for n in required)):
        raise ValueError('Unbound64 independent review')
    jobs = read(B64/'jobs.json')['records']
    retrievals = read(B64/'retrieval_results.json')['records']
    source = read(B64/'source_review/retrieval_judgments.json')['records']
    evaluation = read(B64/'evaluation.json')
    expected = {f"{q['query_id']}:{p}:{mode}:r{r}" for q in read(M.B60/'questions.json')['queries']
                for p in PROFILES for mode in ('U_old','U_new','SEQ_old','SEQ_new') for r in (1,2,3)}
    for rows in (jobs,retrievals,source,evaluation['records']):
        if len(rows) != 960 or {r['identity'] for r in rows} != expected:
            raise ValueError('Incomplete64 accepted identity matrix')
    byid = {r['identity']:r for r in retrievals}
    if (any(j['result'] != byid[j['identity']]['result'] for j in jobs)
            or evaluation['source_review_raw_sha256'] != files[str((B64/'source_review/retrieval_judgments.json').relative_to(ROOT))]):
        raise ValueError('Stale64 source/retrieval/evaluation')
    return {**files,str((B64/'completion.json').relative_to(ROOT)):P.sha((B64/'completion.json').read_bytes())}


def validate_registry_coverage(jobs, catalog, reviews, pdf_hashes, validate_source):
    """Require complete annotation, including bad candidates; never filter jobs."""
    expected = {(j['query_id'], c['card_key']) for j in jobs for c in j['result']['cards']}
    keys = [(r['query_id'],r['card_key']) for r in catalog['records']]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError('Top3 union source registry is empty/partial/extra')
    bycard = dict(zip(keys,catalog['records'],strict=True))
    ids = [r['identity'] for r in reviews['records']]
    if len(ids) != len(set(ids)) or set(ids) != {j['identity'] for j in jobs}:
        raise ValueError('Source review identity coverage differs from jobs')
    byid = {r['identity']:r for r in reviews['records']}
    signature = lambda f: {k:f[k] for k in ('condition_id','benefit_id','roles','source_statement','source_status','source_support')}
    proofs = set()
    for (qid,key), row in bycard.items():
        if row['source_pdf_sha256'] != pdf_hashes.get(key) or not row.get('reason'):
            raise ValueError('Source registry card ownership mismatch')
        facts = row['facts']
        if len({f['condition_id'] for f in facts}) != len(facts):
            raise ValueError('Duplicate registry source condition')
        for fact in facts:
            if (not fact.get('benefit_id') or not fact.get('roles')
                    or not set(fact['roles']) <= {'core','extended','assertion_guard'}
                    or fact['source_status'] not in ('verified','uncertain')
                    or fact['source_status']=='verified' and not fact['source_support']):
                raise ValueError('Incomplete registry benefit/source condition')
            for proof in fact['source_support']:
                if proof['sha256'] != pdf_hashes[key]:
                    raise ValueError('Registry source proof ownership mismatch')
                value = P.sha(proof)
                if value not in proofs:
                    validate_source(proof);proofs.add(value)
        core = {f['condition_id'] for f in facts if 'core' in f['roles']}
        paths = row.get('core_paths', [])
        if (any(not p or len(p)!=len(set(p)) or not set(p)<=core for p in paths)
                or row['relevant'] is True and (not paths or set().union(*(set(p) for p in paths))!=core)):
            raise ValueError('Registry has invalid or missing core paths')
    for job in jobs:
        review = byid[job['identity']]
        if (review['result_sha256'] != P.sha(job['result']) or not review.get('reviewed_by')
                or not review.get('reason')):
            raise ValueError('Source review has stale actual-result binding')
        cards = review['card_reviews']
        if len(cards) != len({c['card_key'] for c in cards}) or {c['card_key'] for c in cards} != {c['card_key'] for c in job['result']['cards']}:
            raise ValueError('Source review card coverage differs from actual Top3')
        for card in cards:
            fixed = bycard[job['query_id'],card['card_key']]
            if card['source_pdf_sha256'] != fixed['source_pdf_sha256'] or card['relevant'] != fixed['relevant']:
                raise ValueError('Registry/review card ownership or relevance changed')
            actual = {f['condition_id']:signature(f) for f in card['facts']}
            if len(actual) != len(card['facts']) or actual != {f['condition_id']:signature(f) for f in fixed['facts']}:
                raise ValueError('Registry source condition changed by search arm')
            text = '\n'.join(e['text'] for e in job['result']['evidence'] if e['card_key']==card['card_key'])
            compact = lambda s: ''.join(s.casefold().split())
            for fact in card['facts']:
                if (fact['input_status'] not in ('supported','absent','ambiguous','contradicted','unknown','not_applicable')
                        or fact['input_status']=='supported' and (not fact['input_quotes'] or fact['source_status']!='verified')
                        or any(not q.strip() or compact(q) not in compact(text) for q in fact['input_quotes'])):
                    raise ValueError('Unbound owning-card source-review input quote')
    return dict(jobs=len(jobs),query_cards=len(keys),source_proofs=len(proofs))


def validate_core_paths(row, catalog, candidate_keys):
    cards = {r['card_key']:r for r in catalog['records'] if r['query_id']==row['query_id'] and r['card_key'] in candidate_keys}
    expected = {(key,frozenset(path)) for key,card in cards.items() if card['relevant'] is True for path in card['core_paths']}
    if row['group']=='uncertain':
        expected=set()
    seen=set()
    for path in row['core_paths']:
        # All48 frozen65 questions concern one card, including same-card AND.
        if len(path['card_keys'])!=1:
            raise ValueError('A predeclared core path must stay within one card')
        key=path['card_keys'][0]
        ids=[f['condition_id'] for f in path['requirements']]
        item=key,frozenset(ids)
        if len(ids)!=len(set(ids)) or item not in expected or item in seen:
            raise ValueError('Answer grade changed a predeclared core path')
        seen.add(item)
        fixed={f['condition_id']:f for f in cards[key]['facts']}
        for condition in path['requirements']:
            source=fixed[condition['condition_id']]
            if (condition['card_key']!=key or condition['statement']!=source['source_statement']
                    or any(condition[k]!=source[k] for k in ('source_status','source_support'))):
                raise ValueError('Answer core source condition differs from registry')
    if seen!=expected:
        raise ValueError('Answer grade dropped a predeclared core path')
    for field,role in [('extended_conditions','extended'),('core_assertion_guard_conditions','assertion_guard')]:
        for condition in row.get(field,[]):
            card=cards.get(condition['card_key'])
            fixed={f['condition_id']:f for f in card['facts']} if card else {}
            source=fixed.get(condition['condition_id'])
            if (source is None or role not in source['roles'] or any(
                    condition[k]!=source[k] for k in ('source_status','source_support'))):
                raise ValueError('Answer condition is outside the predeclared registry')


def prepare(stage, notebook):
    freeze, queries65 = guard_freeze()
    acceptance = read(B65/'main_question_acceptance.json')
    if (acceptance['status'] != 'accepted_before_any65_retrieval_or_answer'
            or acceptance['question_freeze_raw_sha256'] != P.sha((B65/'question_freeze.json').read_bytes())):
        raise RuntimeError('Main source acceptance is missing or stale')
    if read(B63/'evaluation.json')['source_counts'] != {'52': 160, '62': 720}:
        raise RuntimeError('Stage63 regrading is incomplete')
    closure64 = accepted64_closure() if stage == 65 else {}
    base = P.F.STAGES[stage]
    queries = read(M.B60/'questions.json')['queries'] if stage == 64 else queries65
    if stage == 64:
        register_boundary()
    prompts = G.C.operating_prompts()
    parser_ids = [f"{q['query_id']}:parser:r{r}" for q in queries for r in (1, 2, 3)] if stage == 65 else []
    answer_ids = [f"{q['query_id']}:{p}:{mode}:r{r}" for q in queries for p in PROFILES
                  for mode in ('U', 'SEQ') for r in (1, 2, 3)] if stage == 65 else []
    sources = M.source_hashes()
    for name in ('63_65_experiment_runtime.py', '64_scope_repair.py', '63_core_evaluation.py',
                 '61_evaluate_evidence.py', '55_fact_evaluation.py',
                 '62_evaluate_answers.py', '62_validate_answer_prose.py'):
        sources['notebooks/scripts/'+name] = P.sha((ROOT/'notebooks/scripts'/name).read_bytes())
    critical = dict(freeze['files_sha256'])
    for path in (B65/'question_freeze.json', B65/'main_question_acceptance.json', B63/'evaluation.json', M.DB_NEW/'structured.sqlite',
                 M.DB_NEW/'facts.jsonl', M.DB_NEW/'documents.jsonl', M.B60/'query_vectors.npy',
                 M.B60/'parser_results.json', M.B62/'completion.json'):
        critical[str(path.relative_to(ROOT))] = P.sha(path.read_bytes())
    if stage == 65:
        critical.update(closure64)
        for path in (B64/'completion.json', B64/'evaluation.json'):
            critical[str(path.relative_to(ROOT))] = P.sha(path.read_bytes())
    manifest = X.make_manifest(stage, notebook, queries, dict(parser=parser_ids, answer=answer_ids),
        maximum_parser=288 if stage == 65 else 0, maximum_answer=1152 if stage == 65 else 0,
        maximum_embedding=2, stage_cap='8', extra=dict(source_sha256=sources,
            critical_data_sha256=critical, parser_instructions_sha256=P.sha(R.NEW_PROMPT),
            logical_answers=len(answer_ids), question_freeze_sha256=P.sha(freeze),
            prompt_variant_by_identity={i: 'old' for i in answer_ids},
            answer_instructions_sha256_by_identity={i: [P.sha(p) for p in prompts] for i in answer_ids},
            scope_change_only=True, profile_retuning=False, base_cumulative_debit_usd='1.289765390'))
    frozen(base/'source_inventory.json', source_inventory())
    return queries, manifest


class Cache(M.Cache):
    def check_contract(self, kwargs):
        value = super().check_contract(kwargs)
        if self.kind == 'answer':
            phase = read(B65/'answer_phase_manifest.json')
            if phase['execution_manifest_sha256'] != P.sha(self.manifest):
                raise RuntimeError('Answer/source phase belongs to a different execution manifest')
            R.guard_files(phase['critical_data_sha256'])
        return value

    def reserve(self, normalized):
        self.check_contract(normalized)
        if normalized['model'] != 'gpt-6-luna' or normalized['reasoning'] != {'effort': 'medium'}:
            raise RuntimeError('Model/reasoning differs from fixed operating contract')
        records = [read(p) for p in self.cache.glob('*.json') if not p.name.startswith('embedding_batch_')]
        count = sum(r.get('experiment_identity') == self.identity for r in records)
        if count >= 2:
            raise RuntimeError('Per-identity physical attempt cap reached')
        kind_count = sum(r.get('request', {}).get('max_output_tokens') == normalized['max_output_tokens'] for r in records)
        if kind_count >= self.manifest['maximum_'+self.kind+'_requests']:
            raise RuntimeError('Per-kind physical cap reached')
        # Preserve47 payload binding and the global8 reserve; skip51's obsolete
        # one-parser-only cap, while retaining the same raw-before-parse cache.
        return X.R.BoundCache.reserve(self, normalized)


class Runtime(M.Runtime61):
    def __init__(self, stage):
        self.inventory = read(P.F.STAGES[stage]/'source_inventory.json')
        M.guard_inventory(self.inventory)
        X.Runtime.__init__(self, stage)
        if self.reranker_contract != self.inventory['reranker_contract']:
            raise RuntimeError('Operating reranker artifact changed')
        inherited = M.B61/'bge_cache'
        if inherited not in M.CACHE_SOURCES:
            M.CACHE_SOURCES.insert(0, inherited)


def embedding_batch(stage, queries, manifest, *, allow_network=False):
    import numpy as np
    base = P.F.STAGES[stage]
    request = dict(model='text-embedding-3-small', input=[q['question'] for q in queries], dimensions=1536)
    path = base/'api_cache'/('embedding_batch_'+P.sha(request)+'.json')
    if path.exists():
        raw = read(path)
        if raw['request'] != request or raw['status'] != 'ok':
            raise RuntimeError('Interrupted/failed embedding preserved; inspect before retry')
    else:
        if not allow_network:
            raise RuntimeError('Embedding cache miss with network disabled')
        X.guard_manifest(manifest); R.guard_files(manifest['critical_data_sha256'])
        bound = Decimal(len(P.F.canonical(request).encode())+8192)*Decimal('.02')/1000000
        R.require_budget(X.budget_upper_ledger(), bound, '8')
        own = list((base/'api_cache').glob('embedding_batch_*.json'))
        if len(own) >= manifest['maximum_embedding_requests']:
            raise RuntimeError('Embedding physical cap reached')
        raw = dict(status='started', request=request, reserved_max_cost_usd=str(bound))
        write(path, raw); start, cpu = time.perf_counter(), time.process_time()
        try:
            response = P.E.C.api_client().embeddings.create(**request)
            raw.update(status='ok', raw_response=response.model_dump(mode='json'))
            raw['usage'] = raw['raw_response'].get('usage', {})
        except Exception as exc:
            raw.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            raw.update(wall_seconds=time.perf_counter()-start, cpu_seconds=time.process_time()-cpu)
            write(path, raw)
    if raw['usage'] != raw['raw_response'].get('usage'):
        raise RuntimeError('Embedding usage differs from saved provider response')
    items = sorted(raw['raw_response']['data'], key=lambda v: v['index'])
    if [v['index'] for v in items] != list(range(len(queries))):
        raise RuntimeError('Embedding row count/order changed')
    vectors = np.asarray([v['embedding'] for v in items], dtype=np.float32)
    if vectors.shape != (len(queries), 1536) or not np.isfinite(vectors).all():
        raise RuntimeError('Invalid query vectors')
    frozen(base/'embedding_contract.json', dict(request_sha256=P.sha(request), raw_file_sha256=P.sha(path.read_bytes()),
                                               questions_sha256=P.sha(queries), matrix_sha256=P.sha(vectors.tobytes())))
    target = base/'query_vectors.npy'
    if target.exists():
        if not np.array_equal(np.load(target, allow_pickle=False), vectors):
            raise RuntimeError('Vectors differ from raw response')
    else:
        np.save(target, vectors)
    return vectors


def search_matrix(stage, queries, vectors, parsers, manifest, *, boundary=False):
    """Exactly one operating U search per logical path; no global U union."""
    from pickcardu_rag.errors import RerankerUnavailable, EvidencePackageTooLarge
    X.guard_manifest(manifest); R.guard_files(manifest['critical_data_sha256'])
    docs = [json.loads(s) for s in (M.DB_NEW/'documents.jsonl').read_text().splitlines()]
    facts = R.fact_map(M.DB_NEW)
    runtime = Runtime(stage)
    saved = {}
    rows = []
    modes = ('U_old', 'U_new', 'SEQ_old', 'SEQ_new') if stage == 64 else ('U', 'SEQ')
    for qi, (q, vector) in enumerate(zip(queries, vectors, strict=True)):
        scopes = dict(old=M.K.original_scope(q['question'], docs), new=S.original_scope(q['question'], docs))
        repeats = (1,) if boundary else (1, 2, 3)
        for repeat in repeats:
            parser = parsers[q['query_id'], repeat]
            profiles = PROFILES if (qi+repeat)%2 else tuple(reversed(PROFILES))
            shift = (qi+repeat)%len(modes)
            for profile in profiles:
                for mode in modes[shift:]+modes[:shift]:
                    scope_name = 'old' if mode.endswith('_old') else 'new'
                    original = scopes[scope_name]
                    route = (M.K.route(Q, parser['plan'], original, M.DB_NEW, facts, repaired=True,
                                      parser_failed=parser['status'] != 'ok') if mode.startswith('SEQ') else
                             dict(route='U', scope=original['card_keys'], reason='independent_unstructured', db_attempted=False))
                    identity = f"{q['query_id']}:{profile}:{mode}:r{repeat}"
                    target = P.F.STAGES[stage]/('boundary_retrievals' if boundary else 'retrievals')/(identity+'.json')
                    binding = P.sha(dict(q=q, profile=profile, mode=mode, repeat=repeat, parser=parser,
                                         original=original, scope=route['scope'], manifest=P.sha(manifest)))
                    if target.exists():
                        row = read(target)
                        if row['binding'] != binding or read(target.with_suffix('.seal.json'))['row_sha256'] != P.sha(row):
                            raise RuntimeError('Saved retrieval differs from fixed inputs')
                    else:
                        start, cpu = time.perf_counter(), time.process_time()
                        search_key = P.sha(dict(question=q['question'], vector=P.sha(vector.tobytes()),
                                                profile=profile, scope=route['scope'], clarify=original['clarify']))
                        reused = saved.get(search_key)
                        try:
                            result = (copy.deepcopy(reused['result']) if reused else dict(cards=[], evidence=[], clarify=True)
                                      if original['clarify'] else runtime.search(q['question'], vector, profile, scope=route['scope']))
                        except (RerankerUnavailable, EvidencePackageTooLarge) as exc:
                            result = dict(cards=[], evidence=[], technical_failure=f'{type(exc).__name__}: {exc}')
                        G.T.S.validate_scope(result, route['scope']); G.T.S.validate_scope(result, original['card_keys'])
                        row = dict(identity=identity, query_id=q['query_id'], question=q['question'], profile=profile,
                            mode=mode, repeat=repeat, binding=binding, original_scope=original, routing=route, result=result,
                            retrieval_status='failed' if result.get('technical_failure') else 'ok',
                            reused_search_identity=reused['identity'] if reused else None,
                            retrieval_wall_seconds=time.perf_counter()-start, retrieval_cpu_seconds=time.process_time()-cpu)
                        write(target, row); frozen(target.with_suffix('.seal.json'), dict(row_sha256=P.sha(row)))
                        saved.setdefault(search_key, row)
                    rows.append(row)
        write(P.F.STAGES[stage]/('boundary_progress.json' if boundary else 'retrieval_progress.json'),
              dict(questions_done=qi+1, questions=len(queries), logical_rows=len(rows)))
        print(stage, 'boundary' if boundary else 'search', qi+1, '/', len(queries), 'rows', len(rows), flush=True)
    write(P.F.STAGES[stage]/('boundary_retrieval_results.json' if boundary else 'retrieval_results.json'), dict(records=rows))
    if not boundary:
        frozen(P.F.STAGES[stage]/'jobs.json', dict(records=[{k: r[k] for k in
               ('identity', 'query_id', 'question', 'profile', 'mode', 'repeat', 'result', 'retrieval_status')} for r in rows]))
    write(P.F.STAGES[stage]/('boundary_resources.json' if boundary else 'retrieval_resources.json'), runtime.resources())
    return rows


def run64(queries, manifest, *, allow_network=False):
    import numpy as np
    parsers = {(r['query_id'], r['repeat']): r['parser'] for r in read(M.B60/'parser_results.json')['records']}
    rows = search_matrix(64, queries, np.load(M.B60/'query_vectors.npy', allow_pickle=False), parsers, manifest)
    if len(rows) != 960:
        raise RuntimeError('Expected960 development retrieval rows')
    boundary = read(B64/'boundary_cases.json')['records'][:8]
    vectors = embedding_batch(64, boundary, manifest, allow_network=allow_network)
    manual = {(q['query_id'], 1): dict(status='ok', plan=Q.plan([Q.requirement(
                ['롯데시네마' if i < 2 else '삼성페이' if i < 4 else '현대백화점' if i < 6 else '롯데마트'],
                action='earn' if 2 <= i < 4 else 'discount')]),
                policy='fixed diagnostic scope-transfer fixture; not LLM extraction') for i, q in enumerate(boundary)}
    brows = search_matrix(64, boundary, vectors, manual, manifest, boundary=True)
    docs = [json.loads(s) for s in (M.DB_NEW/'documents.jsonl').read_text().splitlines()]
    comparisons = []
    for case in read(B64/'boundary_cases.json')['records']:
        old, new = M.K.original_scope(case['question'], docs), S.original_scope(case['question'], docs)
        if new['card_keys'] != case['expected_scope'] or new['clarify'] != (case['expected_scope'] == []):
            raise RuntimeError('Scope64 regression: '+case['query_id'])
        comparisons.append(dict(**case, old=old, new=new,
            old_allowed_cards=len(old['card_keys']) if old['card_keys'] is not None else len(docs),
            new_allowed_cards=len(new['card_keys']) if new['card_keys'] is not None else len(docs)))
    write(B64/'scope_comparisons.json', dict(records=comparisons, scope_regressions=24, route_regressions=8,
        inherited_route_tests='64_test_scope_repair.py', real_boundary_retrievals=len(brows)))
    return dict(development_rows=len(rows), boundary_rows=len(brows),
                status='awaiting_source_and_independent_acceptance', price=R.phase_price([64]))


def parser65(q, repeat, manifest, *, allow_network=False):
    identity = f"{q['query_id']}:parser:r{repeat}"
    api = Cache(65, 'parser', identity, allow_network=allow_network)
    kwargs = dict(model='gpt-6-luna', instructions=R.NEW_PROMPT,
        input=[dict(role='user', content=q['question'])], text_format=Q.QueryPlan, tools=[], store=False, max_output_tokens=3000)
    api.check_contract(kwargs)
    path = B65/'plans'/(identity+'.json')
    if path.exists():
        row = read(path)
    else:
        start, cpu = time.perf_counter(), time.process_time()
        value = error = None
        try:
            response = api.parse(**kwargs)
            value = response.output_parsed.model_dump(mode='json') if response.output_parsed else None
        except Exception as exc:
            if not api.events:
                raise
            error = f'{type(exc).__name__}: {exc}'
        row = dict(identity=identity, query_id=q['query_id'], repeat=repeat, question=q['question'],
            question_sha256=P.sha(q['question']), manifest_sha256=P.sha(manifest), request_key=api.key(kwargs),
            status='ok' if value is not None else 'failed', error=error, plan=value, events=api.events,
            parser_wall_seconds=time.perf_counter()-start, parser_cpu_seconds=time.process_time()-cpu)
        write(path, row)
    P.validate_parser_row(row, identity=identity, query_id=q['query_id'], repeat=repeat, question=q['question'],
        manifest=manifest, request_key=row['request_key'], cache=api.cache)
    if X.budget_upper_ledger()['unknown_cost_records']:
        raise RuntimeError('Unknown new parser usage; inspect before another call')
    return row


def search65(queries, manifest, *, allow_network=False):
    parsers = {}
    for i, q in enumerate(queries, 1):
        for repeat in (1, 2, 3):
            parsers[q['query_id'], repeat] = parser65(q, repeat, manifest, allow_network=allow_network)
        print('65 parser', i, '/48', flush=True)
    write(B65/'parser_results.json', dict(records=list(parsers.values())))
    vectors = embedding_batch(65, queries, manifest, allow_network=allow_network)
    rows = search_matrix(65, queries, vectors, parsers, manifest)
    if len(rows) != 576:
        raise RuntimeError('Expected576 confirmation retrieval rows')
    return dict(status='search_complete_awaiting_preanswer_source_registry', logical_rows=len(rows),
                parser_plans=len(parsers), price=R.phase_price([65]))


def reuse_signature(job):
    from pickcardu_rag.answering import serialize_answer_payload, _generated_answer_model
    evidence = job['result']['evidence']
    return P.sha(dict(question=job['question'], repeat=job['repeat'], profile=job['profile'],
        technical_failure=job['result'].get('technical_failure'), clarify=job['result'].get('clarify', False),
        evidence=evidence, input=serialize_answer_payload(job['question'], evidence),
        schema=_generated_answer_model(len(evidence)).model_json_schema() if evidence else None,
        instructions=G.C.operating_prompts(), model='gpt-6-luna', reasoning='medium', output=2400))


def registry_required_files(catalog):
    paths = {B65/'source_registry.json', B65/'source_review/retrieval_judgments.json',
             B65/'jobs.json', B65/'retrieval_results.json'}
    for card in catalog['records']:
        for fact in card['facts']:
            for proof in fact['source_support']:
                paths.add(ROOT/proof['path'])
                if proof.get('manual_review_path'):
                    path = ROOT/proof['manual_review_path']; paths.add(path)
                    records = read(path)['records']
                    matches = [r for r in records if r['review_id']==proof['manual_review_id']]
                    if len(matches) != 1:
                        raise ValueError('Missing source image review')
                    paths.add(ROOT/matches[0]['image_path'])
    if any(not p.resolve().is_relative_to(ROOT.resolve()) for p in paths):
        raise ValueError('Registry source path escapes repository')
    return {str(p.relative_to(ROOT)):P.sha(p.read_bytes()) for p in sorted(paths)}


def preanswer_closure(jobs, manifest):
    X.guard_manifest(manifest); R.guard_files(manifest['critical_data_sha256'])
    gate_path = B65/'source_registry_acceptance.json'
    gate = read(gate_path)
    declared = gate.get('files_sha256', {})
    if gate.get('status') != 'accepted_before_answer_generation' or not declared:
        raise ValueError('Incomplete pre-answer source registry acceptance')
    expected=set(manifest['allowed_identities']['answer'])
    if len(jobs)!=576 or len(expected)!=576 or {j['identity'] for j in jobs}!=expected:
        raise ValueError('Pre-answer registry requires the fixed576 job identities')
    questions={q['query_id']:q['question'] for q in read(B65/'questions.json')['queries']}
    if any(j['identity']!=f"{j['query_id']}:{j['profile']}:{j['mode']}:r{j['repeat']}"
           or j['question']!=questions[j['query_id']] for j in jobs):
        raise ValueError('Pre-answer question/profile/mode/repeat changed')
    catalog = read(B65/'source_registry.json')
    required = registry_required_files(catalog)
    if (not set(required) <= set(declared) or any(declared[n]!=h for n,h in required.items())
            or gate.get('required_file_map_sha256') != P.sha(required)
            or gate.get('jobs_raw_sha256') != required[str((B65/'jobs.json').relative_to(ROOT))]):
        raise ValueError('Incomplete/stale pre-answer source registry closure')
    R.guard_files(declared)
    if read(B65/'jobs.json')['records'] != jobs:
        raise ValueError('Pre-answer jobs changed')
    retrieved = read(B65/'retrieval_results.json')['records']
    byid = {r['identity']:r for r in retrieved}
    if len(retrieved)!=len(jobs) or set(byid)!={j['identity'] for j in jobs} or any(
            j['result']!=byid[j['identity']]['result'] for j in jobs):
        raise ValueError('Pre-answer registry has stale retrievals')
    source = load('source55_registry65', '55_fact_evaluation.py')
    documents = [json.loads(s) for s in (M.DB_NEW/'documents.jsonl').read_text().splitlines()]
    counts = validate_registry_coverage(jobs, catalog, read(B65/'source_review/retrieval_judgments.json'),
        {d['document_id']:d['source_pdf_sha256'] for d in documents},
        lambda proof:source.validate_source_support(proof,ROOT))
    phase_path = B65/'answer_phase_manifest.json'
    if not phase_path.exists() and (any((B65/'answers').glob('*.json')) or any(
            read(p).get('request',{}).get('max_output_tokens')==2400 for p in (B65/'api_cache').glob('*.json'))):
        raise ValueError('Cannot first freeze source registry after answer generation')
    phase = dict(execution_manifest_sha256=P.sha(manifest), registry_coverage=counts,
        source_registry_acceptance_raw_sha256=P.sha(gate_path.read_bytes()),
        critical_data_sha256={**declared,str(gate_path.relative_to(ROOT)):P.sha(gate_path.read_bytes())},
        helper_raw_sha256=P.sha((ROOT/'notebooks/scripts/63_65_experiment_runtime.py').read_bytes()),
        policy='Annotation completeness only: no candidate/benefit passes are required or used to filter retrievals.')
    frozen(phase_path, phase)
    return phase


def guard_answer_phase(manifest, *, allow_unstamped=False):
    path=B65/'answer_phase_manifest.json'
    phase=read(path)
    if phase['execution_manifest_sha256']!=P.sha(manifest):
        raise ValueError('Different execution manifest in answer phase')
    R.guard_files(phase['critical_data_sha256'])
    raw_sha=P.sha(path.read_bytes())
    for answer_path in (B65/'answers').glob('*.json'):
        stamp=read(answer_path).get('answer_phase_manifest_sha256')
        if stamp is None and allow_unstamped:
            continue
        if stamp!=raw_sha:
            raise ValueError('Saved answer phase differs from current registry/gate')
    return raw_sha


def validate_and_stamp(row, job, manifest, phase_sha, *, source=None):
    evidence=job['result']['evidence']
    if row.get('answer_phase_manifest_sha256',phase_sha)!=phase_sha:
        raise ValueError('Saved answer belongs to a different source phase')
    if row['evidence_sha256']!=P.sha(evidence) or row['retrieval_sha256']!=P.sha(job['result']):
        raise RuntimeError('Answer/retrieval binding changed')
    contract=G.payload_contract(job['identity'],job['question'],evidence,manifest)
    G.M.R.validate_generated_answer(row,job['question'],evidence,stage=65,identity=job['identity'],
                                   manifest=manifest,contract=contract,reused=source)
    if source is None:
        G.validate_checkpoint_prompt(row,manifest,{})
    # A crash after the operating writer but before this stamp is recovered
    # only after raw provider/payload validation, without another API call.
    if 'answer_phase_manifest_sha256' not in row:
        row['answer_phase_manifest_sha256']=phase_sha
        write(B65/'answers'/(job['identity']+'.json'),row)
    return row


def answer65(jobs, manifest, *, allow_network=False):
    preanswer_closure(jobs, manifest)
    phase_sha=guard_answer_phase(manifest,allow_unstamped=True)
    if len(jobs) != 576 or len({j['identity'] for j in jobs}) != 576:
        raise RuntimeError('Partial or duplicate576 answer matrix')
    G.freeze_payloads(65, jobs, manifest)
    G.M.X.Cache = Cache
    records, exact = [], {}
    start_all, cpu_all = time.perf_counter(), time.process_time()
    for i, job in enumerate(jobs, 1):
        identity = job['identity']
        evidence = job['result']['evidence']
        reuse_key = reuse_signature(job)
        source = exact.get(reuse_key)
        target = B65/'answers'/(identity+'.json')
        if target.exists():
            row = read(target)
            if row.get('reuse_source_identity'):
                source = read(B65/'answers'/(row['reuse_source_identity']+'.json'))
        elif job.get('retrieval_status') != 'failed' and source is not None and source['status'] == 'ok':
            start = time.perf_counter()
            contract = G.payload_contract(identity, job['question'], evidence, manifest)
            row = dict(identity=identity, status=source['status'], answer=copy.deepcopy(source['answer']), usage={},
                error=source.get('error'), events=[], request_keys=[], manifest_sha256=P.sha(manifest),
                payload_contract_sha256=P.sha(contract), input_sha256=contract['input_sha256_by_identity'][identity],
                evidence_sha256=P.sha(evidence), retrieval_sha256=P.sha(job['result']),
                reuse_source_sha256=P.sha(source), reuse_source_identity=source['identity'],
                source_answer_ready_wall_seconds=source['answer_ready_wall_seconds'],
                answer_ready_wall_seconds=time.perf_counter()-start, answer_cpu_seconds=0,
                first_token_seconds=None, browser_display_seconds=None,
                cache_policy='same profile/question/repeat and exact input/evidence/schema/prompts only')
            write(target, row)
        elif job.get('retrieval_status') == 'failed':
            contract = G.payload_contract(identity, job['question'], evidence, manifest)
            row = dict(identity=identity, status='failed',
                answer=dict(answer_status='insufficient_evidence', answer_text='검색 실행에 실패했습니다.', recommendations=[], claims=[]),
                usage={}, error=job['result']['technical_failure'], events=[], request_keys=[],
                manifest_sha256=P.sha(manifest), payload_contract_sha256=P.sha(contract),
                input_sha256=contract['input_sha256_by_identity'][identity], evidence_sha256=P.sha(evidence),
                retrieval_sha256=P.sha(job['result']), reuse_source_sha256=None,
                answer_ready_wall_seconds=0, answer_cpu_seconds=0, first_token_seconds=None,
                browser_display_seconds=None, cache_policy='technical retrieval failure; no API call')
            write(target, row)
        else:
            row = G.M.answer(65, identity, job['question'], job['result'], allow_network=allow_network)
        if row.get('reuse_source_identity'):
            source_job = next(j for j in jobs if j['identity'] == row['reuse_source_identity'])
            if (source_job['repeat'] != job['repeat'] or source_job['profile'] != job['profile']
                    or source_job['question'] != job['question'] or source_job['result']['evidence'] != evidence
                    or row['answer'] != source['answer'] or row['reuse_source_sha256'] != P.sha(source)):
                raise RuntimeError('Invalid same-repeat answer reuse')
        validate_and_stamp(row,job,manifest,phase_sha,source=source if row.get('reuse_source_identity') else None)
        if row['status']=='ok' and not row.get('reuse_source_identity'):
            exact.setdefault(reuse_key,row)
        records.append({k: v for k, v in job.items() if k != 'result'} | dict(answer=row))
        write(B65/'answer_progress.json', dict(completed=i, expected=576,
            successful=sum(r['answer']['status']=='ok' for r in records),
            reused=sum(bool(r['answer'].get('reuse_source_identity')) for r in records)))
        if i%4 == 0:
            print('65 answers', i, '/576', flush=True)
        if X.budget_upper_ledger()['unknown_cost_records']:
            raise RuntimeError('Unknown new answer usage; preserve it before any further call')
    write(B65/'answer_results.json', dict(records=records, manifest_sha256=P.sha(manifest)))
    import resource
    resources = dict(logical_answers=len(records), status_counts=dict(Counter(r['answer']['status'] for r in records)),
        reused_answers=sum(bool(r['answer'].get('reuse_source_identity')) for r in records),
        price=R.phase_price([65]), global_debit=X.budget_upper_ledger(),
        invocation_wall_seconds=time.perf_counter()-start_all, invocation_cpu_seconds=time.process_time()-cpu_all,
        peak_process_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        ttft_seconds=None, browser_display_seconds=None, online_end_to_end_seconds=None)
    write(B65/'answer_resources.json', resources)
    return resources


def evaluate_retrieval(stage):
    module = load('evidence61_for64_65', '61_evaluate_evidence.py')
    source = load('source55_for64_65', '55_fact_evaluation.py')
    base = P.F.STAGES[stage]
    jobs = read(base/'jobs.json')['records']
    phase_sha=None
    if stage==65:
        manifest=read(B65/'execution_manifest.json')
        preanswer_closure(jobs,manifest)
        phase_sha=guard_answer_phase(manifest)
    reviewed = read(base/'source_review/retrieval_judgments.json')
    by_identity = {r['identity']: r for r in reviewed['records']}
    if len(by_identity) != len(jobs) or set(by_identity) != {j['identity'] for j in jobs}:
        raise RuntimeError('Missing, duplicate or extra source review')
    docs = [json.loads(s) for s in (M.DB_NEW/'documents.jsonl').read_text().splitlines()]
    source_hashes = {d['document_id']: d['source_pdf_sha256'] for d in docs}
    seen = set()
    def proof(value):
        key = P.sha(value)
        if key not in seen:
            source.validate_source_support(value, ROOT)
            seen.add(key)
    queries = read(M.B60/'questions.json')['queries'] if stage == 64 else read(B65/'questions.json')['queries']
    groups = (read(M.B61/'source_judgments.json')['group_by_query'] if stage == 64 else
              {q['query_id']: q['group'] for q in queries})
    rows = []
    catalog = {(r['query_id'],r['card_key']):r for r in read(B65/'source_registry.json')['records']} if stage==65 else {}
    for job in jobs:
        review = copy.deepcopy(by_identity[job['identity']])
        if stage == 65:
            for card in review['card_reviews']:
                card['facts'] = [f for f in card['facts'] if 'core' in f['roles']]
        score = module.score_retrieval(job, review, groups[job['query_id']],
                    validate_source=proof, source_pdf_sha_by_card=source_hashes)
        if stage==65 and groups[job['query_id']]!='uncertain':
            complete=[]
            for card,count in zip(review['card_reviews'],score['card_condition_counts'],strict=True):
                facts={f['condition_id']:f for f in card['facts']}
                paths=catalog[job['query_id'],card['card_key']]['core_paths']
                count['complete']=card['relevant'] is True and any(all(
                    facts[fid]['source_status']=='verified' and facts[fid]['input_status']=='supported' for fid in path) for path in paths)
                complete.append(count['complete'])
            score['complete_evidence']=any(complete)
        rows.append(score)
    modes = ('U_old', 'U_new', 'SEQ_old', 'SEQ_new') if stage == 64 else ('U', 'SEQ')
    summary = module.aggregate(rows, group_by_query=groups, profiles=PROFILES, modes=modes)
    output = dict(records=rows, summary=summary, source_review_raw_sha256=P.sha((base/'source_review/retrieval_judgments.json').read_bytes()),
                  verified_source_proofs=len(seen), answer_phase_manifest_raw_sha256=phase_sha,
                  status='retrieval_source_reviewed_pending_independent_acceptance')
    if stage == 64:
        indexed = {(r['profile'], r['mode'], r['group']): r for r in summary}
        contrasts = []
        for profile in PROFILES:
            for group in ('explicit', 'general'):
                for metric in ('complete_evidence_macro', 'hit_at3_macro', 'mrr_at3_macro'):
                    values = {m: indexed[profile,m,group][metric] for m in modes}
                    if any(v is None for v in values.values()):
                        continue
                    contrasts.append(dict(profile=profile, group=group, metric=metric,
                        scope_effect_u=values['U_new']-values['U_old'],
                        scope_effect_seq=values['SEQ_new']-values['SEQ_old'],
                        structured_effect_old=values['SEQ_old']-values['U_old'],
                        structured_effect_new=values['SEQ_new']-values['U_new'],
                        interaction=(values['SEQ_new']-values['U_new'])-(values['SEQ_old']-values['U_old'])))
        output['factorial_contrasts'] = contrasts
    write(base/'evaluation.json', output)
    return output


def finalize65():
    retrieval = evaluate_retrieval(65)
    source = load('source55_final65', '55_fact_evaluation.py')
    prose = load('prose62_final65', '62_validate_answer_prose.py')
    legacy_scorer = load('legacy62_final65', '62_evaluate_answers.py')
    pregrades = {r['identity']: r for r in read(B65/'source_review/retrieval_judgments.json')['records']}
    document = read(B65/'manual_judgments.json')
    catalog = read(B65/'source_registry.json')
    R.guard_files(document['source_files_sha256'])
    reviews = document['records']
    E.validate_groups(reviews, {q['query_id']: q['group'] for q in read(B65/'questions.json')['queries']})
    jobs = {j['identity']: j for j in read(B65/'jobs.json')['records']}
    expected = {f"R{qi:02}:{p}:{mode}:r{r}" for qi in range(1,49) for p in PROFILES
                for mode in ('U','SEQ') for r in (1,2,3)}
    if len(reviews) != 576 or {r['identity'] for r in reviews} != expected or set(jobs) != expected:
        raise RuntimeError('Confirmation scoring requires the complete576 matrix')
    docs = [json.loads(s) for s in (M.DB_NEW/'documents.jsonl').read_text().splitlines()]
    pdf_hashes = {d['document_id']: d['source_pdf_sha256'] for d in docs}
    checked = set()
    def proof(value):
        key = P.sha(value)
        if key not in checked:
            source.validate_source_support(value, ROOT)
            checked.add(key)
    for row in reviews:
        if row['dataset'] != '65':
            raise RuntimeError('Wrong scoring dataset')
        job = jobs[row['identity']]
        validate_core_paths(row,catalog,{c['card_key'] for c in job['result']['cards']})
        raw = (ROOT/row['answer_path']).read_bytes()
        answer = json.loads(raw)
        if P.sha(raw) != row['answer_raw_sha256'] or P.sha(answer) != row['answer_row_sha256']:
            raise RuntimeError('Manual grade belongs to a different answer')
        if any(row[k] != job[k] for k in ('identity', 'query_id', 'profile', 'mode', 'repeat', 'question')):
            raise RuntimeError('Changed answer scoring identity')
        if answer['status'] != 'ok' and row['core_grounded_success']:
            raise RuntimeError('Technical failure cannot be scored successful')
        inputs = {}
        for item in job['result']['evidence']:
            inputs[item['card_key']] = inputs.get(item['card_key'], '')+'\n'+item['text']
        outputs = source.answer_by_card(answer.get('answer') or {},
            {c['card_key'] for c in job['result']['cards']}, row.get('answer_prose_spans', []))
        conditions = [c for path in row['core_paths'] for c in path['requirements']]
        conditions += row['extended_conditions']+row.get('core_assertion_guard_conditions', [])
        for condition in conditions:
            E.validate_condition(condition, proof)
            E.validate_bound_condition(condition, condition['card_key'], inputs, outputs, pdf_hashes)
        legacy = prose.score_answer(job, answer, row['legacy_review'], pregrades[row['identity']], row['group'],
            score_frozen=legacy_scorer.score_answer, answer_by_card=source.answer_by_card,
            validate_source_support=proof)
        if legacy['passed'] != row['legacy_pass']:
            raise RuntimeError('Legacy PASS differs from full-prose/source/input audit')
    rows = [E.score(r) for r in reviews]
    summary = E.aggregate(rows)
    summaries = {(r['profile'],r['mode'],r['group']): r for r in summary}
    evidence = {(r['profile'],r['mode'],r['group']): r for r in retrieval['summary']}
    decisions, comparisons = [], []
    for profile in PROFILES:
        failures = []
        for group in ('explicit', 'general', 'all'):
            for metric in ('core_grounded_success', 'legacy_pass'):
                if summaries[profile,'SEQ',group][metric] < summaries[profile,'U',group][metric]:
                    failures.append(group+':'+metric+'_regression')
            subset = [r for r in rows if r['profile'] == profile and (group == 'all' or r['group'] == group)]
            for metric in ('core_grounded_success','legacy_pass'):
                comparisons.append(dict(profile=profile, group=group, metric=metric,
                    **E.paired(subset,'U','SEQ',metric=metric)))
        a, b = evidence[profile,'SEQ','explicit'], evidence[profile,'U','explicit']
        if a['complete_evidence_macro'] <= b['complete_evidence_macro']:
            failures.append('explicit:core_evidence_not_improved')
        for metric in ('hit_at3_macro','mrr_at3_macro'):
            if a[metric] < b[metric]:
                failures.append('explicit:'+metric+'_regression')
        evrows = [dict(r, core_evidence_complete=r['complete_evidence']) for r in retrieval['records']
                  if r['profile'] == profile and r['group'] == 'explicit']
        comparisons.append(dict(profile=profile,group='explicit',metric='core_evidence_complete',
                                **E.paired(evrows,'U','SEQ',metric='core_evidence_complete')))
        manual = {(r['query_id'],r['mode'],r['repeat']): r for r in reviews if r['profile'] == profile}
        for qi in range(1,49):
            qid = f'R{qi:02}'
            old = [r for r in rows if r['profile']==profile and r['query_id']==qid and r['mode']=='U']
            new = [r for r in rows if r['profile']==profile and r['query_id']==qid and r['mode']=='SEQ']
            if sum(r['core_grounded_success'] for r in new) < sum(r['core_grounded_success'] for r in old):
                failures.append('new_core_loss:'+qid)
            if any(r['scope_violation'] for r in new):
                failures.append('scope_violation:'+qid)
            if sum(r['false_certainty'] for r in new) > sum(r['false_certainty'] for r in old):
                failures.append('false_certainty:'+qid)
            old_errors = {e for rep in (1,2,3) for e in manual[qid,'U',rep]['critical_error_ids']}
            new_errors = {e for rep in (1,2,3) for e in manual[qid,'SEQ',rep]['critical_error_ids']}
            if new_errors-old_errors:
                failures.append('new_critical_error:'+qid)
        decisions.append(dict(profile=profile, eligible=not failures, failures=failures,
                              production_adopted=False, decision='integration_validation_candidate' if not failures else 'retain_U'))
    result = dict(records=rows, summary=summary, comparisons=comparisons, decisions=decisions,
                  status='manual_source_input_answer_scored_pending_independent_review',
                  manual_judgments_raw_sha256=P.sha((B65/'manual_judgments.json').read_bytes()),
                  answer_phase_manifest_raw_sha256=retrieval['answer_phase_manifest_raw_sha256'],
                  eec_policy='diagnostic only; actual selected-card denominators vary', production_adopted=False)
    write(B65/'answer_evaluation.json', result)
    return result
