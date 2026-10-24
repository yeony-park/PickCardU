"""Shared durable execution for notebooks60--62, not a report generator.

Reuses frozen provider journals, price/reservation and operating retrieval.
New code only owns the three-arm matrix, per-arm dependencies and exact reuse.
"""
from collections import Counter
from decimal import Decimal
import copy
import importlib.util
import json
from pathlib import Path
import resource
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


N = load('preserved59_for60', '59_confirmation_runtime.py')
G, R, P, X, Q = N.G, N.R, N.P, N.X, N.Q
C = load('contract60_shared', '60_experiment_contract.py')
K = load('literal60_shared', '60_query_repair.py')
LEAVES = {60: '60_structured_retrieval_repair', 61: '61_retrieval_evidence_comparison', 62: '62_fixed_prompt_answer_check'}
for number, name in LEAVES.items():
    X.STAGES[number] = P.STAGES[number] = name
    for shared in [P.F, P.A.F, Q.P.F, Q.P.A.F]:
        shared.STAGES[number] = P.DATA / name
B60, B61, B62 = (P.F.STAGES[i] for i in (60, 61, 62))
DB_OLD, DB_NEW = R.DB2, B60 / 'db_v3'
read, write, frozen = R.read, R.write, X.frozen
CACHE_SOURCES = [N.BASE / 'bge_cache'] + [P.DATA / name / 'bge_cache' for name in (
    '42_expanded_query_evaluation','41_query_expansion','40_new_query_comparison','34_bundle_comparison','32_structured_search_comparison')]


def guard_inventory(inventory, *, root=ROOT):
    for name, expected in inventory['files_sha256'].items():
        if P.sha((root / name).read_bytes()) != expected:
            raise RuntimeError('Frozen index/cache input changed: ' + name)
    for name, members in inventory['directory_members'].items():
        actual = sorted(str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
                        for p in (root / name).rglob('*') if p.is_file())
        if actual != members:
            raise RuntimeError('Frozen index/cache directory membership changed: ' + name)


def freeze_input_inventory():
    path = B60 / 'input_inventory.json'
    if path.exists():
        inventory = read(path); guard_inventory(inventory)
        return inventory
    directories = [P.DATA / '31_operational_chunking_comparison' / profile for profile in C.PROFILES] + CACHE_SOURCES
    members = {str(directory.relative_to(ROOT)):sorted(str(p.relative_to(ROOT)) for p in directory.rglob('*') if p.is_file())
               for directory in directories}
    names = {name for values in members.values() for name in values}
    names.update(str((P.DATA / '31_operational_chunking_comparison' / (profile+'_chunks.jsonl')).relative_to(ROOT)) for profile in C.PROFILES)
    from pickcardu_rag.retrieval import LocalReranker
    inventory = dict(files_sha256={n:P.sha((ROOT/n).read_bytes()) for n in sorted(names)},directory_members=members,
        reranker_contract=LocalReranker(str(ROOT/'.cache/reranker/bge-reranker-v2-m3')).artifact_contract('bge'),
        policy='Original index files and every allowed inherited score-cache file are immutable across initial execution and resume.')
    frozen(path,inventory); guard_inventory(inventory)
    return inventory


def guard59():
    seal = read(N.BASE / 'completion.json')
    if seal['status'] != 'completed_reviewed_experiment_not_production_promotion':
        raise RuntimeError('Upstream59 is not accepted')
    for name, expected in seal['files_sha256'].items():
        import hashlib
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Preserved59 artifact changed: ' + name)


def source_hashes():
    names = set(read(N.BASE / 'execution_manifest.json')['source_sha256'])
    names.update('notebooks/scripts/' + n for n in ['60_62_experiment_runtime.py', '60_experiment_contract.py',
                                                 '60_query_repair.py', '60_normalize_experiment_db.py'])
    return {name: P.sha((ROOT / name).read_bytes()) for name in sorted(names)}


def critical_common():
    paths = [B60 / 'questions.json', B60 / 'grading_contract.json', B60 / 'manual_diagnostic_plans.json', B60 / 'normalization_result.json',
             B60 / 'db_source_review' / 'main_acceptance.json', N.BASE / 'completion.json',
             N.BASE / 'questions.json', N.BASE / 'embedding_contract.json', N.BASE / 'query_vectors.npy']
    paths += [directory / name for directory in (DB_OLD, DB_NEW) for name in ('structured.sqlite', 'facts.jsonl', 'documents.jsonl')]
    inventory=read(B60/'input_inventory.json')
    paths.append(B60/'input_inventory.json')
    return {**inventory['files_sha256'], **{str(path.relative_to(ROOT)): P.sha(path.read_bytes()) for path in paths}}


class Cache(G.AnswerCache):
    def normalized(self, kwargs):
        return R.Cache.normalized(self, kwargs) if self.kind == 'parser' else super().normalized(kwargs)

    def check_contract(self, kwargs):
        return R.Cache.check_contract(self, kwargs) if self.kind == 'parser' else super().check_contract(kwargs)

    def reserve(self, normalized):
        cost60, cost62, upper = R.phase_price([60]), R.phase_price([62]), X.budget_upper_ledger()
        C.check_budget(cost60['known_cost_usd'], cost62['known_cost_usd'], upper['known_cost_usd'],
                       R.request_upper_bound(self.wire_kwargs(normalized), normalized['max_output_tokens']), self.stage,
                       unknown=cost60['unknown_cost_records'] + cost62['unknown_cost_records'] + upper['unknown_cost_records'])
        return super().reserve(normalized)


def prepare60(notebook):
    guard59()
    gate = read(B60 / 'db_source_review' / 'main_acceptance.json')
    if gate['status'] != 'accepted_changed_derived_and_matching_rules':
        raise RuntimeError('Copied DB/source gate has not been accepted')
    R.guard_files(gate['files_sha256'])
    freeze_input_inventory()
    queries = read(B60 / 'questions.json')['queries']
    if len(queries) != 40 or len({q['query_id'] for q in queries}) != 40:
        raise RuntimeError('Approved40 question matrix changed')
    previous = read(N.BASE / 'questions.json')['queries']
    if queries[:32] != previous or any(q['subgroup'] != 'general_recommendation' for q in queries[32:]):
        raise RuntimeError('Original32/general8 strata changed')
    generic = queries[32:]
    ids = [f"{q['query_id']}:parser:r{r}" for q in generic for r in (1, 2, 3)]
    manifest = X.make_manifest(60, notebook, generic, dict(parser=ids, answer=[]), maximum_parser=24,
        maximum_embedding=1, stage_cap='.10', extra=dict(source_sha256=source_hashes(), critical_data_sha256=critical_common(),
        parser_instructions_sha256=P.sha(R.NEW_PROMPT), logical_queries=40, reused_parser_plans=96,
        prompt_variant_by_identity={}, answer_instructions_sha256_by_identity={}, combined_new_cost_cap_usd='1'))
    return queries, manifest


def extract60(q, repeat, manifest, *, allow_network):
    identity = f"{q['query_id']}:parser:r{repeat}"
    api = Cache(60, 'parser', identity, allow_network=allow_network)
    kwargs = dict(model='gpt-6-luna', instructions=R.NEW_PROMPT,
        input=[dict(role='user', content=q['question'])], text_format=Q.QueryPlan, tools=[], store=False, max_output_tokens=3000)
    api.check_contract(kwargs)
    path = B60 / 'plans' / (identity + '.json')
    if path.exists():
        row = read(path)
    else:
        start, cpu = time.perf_counter(), time.process_time()
        value, error = None, None
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
        manifest=manifest, request_key=api.key(kwargs), cache=api.cache)
    if X.budget_upper_ledger()['unknown_cost_records']:
        raise RuntimeError('New unknown parser usage')
    return row


def vectors60(queries, manifest, *, allow_network):
    import numpy as np
    generic = queries[32:]
    request = dict(model='text-embedding-3-small', input=[q['question'] for q in generic], dimensions=1536)
    path = B60 / 'api_cache' / ('embedding_batch_' + P.sha(request) + '.json')
    if path.exists():
        raw = read(path)
        if raw['request'] != request or raw['status'] != 'ok':
            raise RuntimeError('Failed/interrupted embedding needs inspection, not an automatic retry')
    else:
        if not allow_network:
            raise RuntimeError('No generic8 embedding cache')
        X.guard_manifest(manifest); R.guard_files(manifest['critical_data_sha256'])
        cost60, cost62, total = R.phase_price([60]), R.phase_price([62]), X.budget_upper_ledger()
        bound = Decimal(len(P.F.canonical(request).encode()) + 8192) * Decimal('.02') / 1000000
        C.check_budget(cost60['known_cost_usd'], cost62['known_cost_usd'], total['known_cost_usd'], bound, 60,
                       unknown=cost60['unknown_cost_records'] + cost62['unknown_cost_records'] + total['unknown_cost_records'])
        start, cpu = time.perf_counter(), time.process_time()
        raw = dict(status='started', request=request); write(path, raw)
        try:
            response = P.E.C.api_client().embeddings.create(**request)
            raw.update(raw_response=response.model_dump(mode='json'), status='ok')
            raw['usage'] = raw['raw_response'].get('usage', {})
        except Exception as exc:
            raw.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            raw.update(wall_seconds=time.perf_counter()-start, cpu_seconds=time.process_time()-cpu); write(path, raw)
    if raw['usage'] != raw['raw_response'].get('usage'):
        raise RuntimeError('Embedding usage changed')
    data = sorted(raw['raw_response']['data'], key=lambda v:v['index'])
    if [v['index'] for v in data] != list(range(8)):
        raise RuntimeError('Embedding8 index mismatch')
    new = np.asarray([v['embedding'] for v in data], dtype=np.float32)
    old = np.load(N.BASE / 'query_vectors.npy', allow_pickle=False)
    matrix = np.vstack([old, new])
    if matrix.shape != (40, 1536) or not np.isfinite(matrix).all():
        raise RuntimeError('Invalid40 query vectors')
    target = B60 / 'query_vectors.npy'
    if target.exists():
        if not np.array_equal(np.load(target, allow_pickle=False), matrix):
            raise RuntimeError('Saved vectors differ from raw provider response')
    else:
        np.save(target, matrix)
    frozen(B60 / 'embedding_contract.json', dict(matrix_sha256=P.sha(matrix.tobytes()),
        original32_matrix_file_sha256=P.sha((N.BASE / 'query_vectors.npy').read_bytes()),
        new8_raw_record_sha256=P.sha(raw), questions_sha256=P.sha(queries)))
    return matrix


def audit60(queries, manifest, *, allow_network=False):
    X.guard_manifest(manifest); R.guard_files(manifest['critical_data_sha256'])
    documents = [json.loads(line) for line in (DB_OLD / 'documents.jsonl').read_text().splitlines()]
    versions = dict(old=(DB_OLD, R.fact_map(DB_OLD)), new=(DB_NEW, R.fact_map(DB_NEW)))
    records, plans, diagnostic = [], [], []
    manual = read(B60 / 'manual_diagnostic_plans.json')['by_query']
    for q in queries:
        scopes = dict(old=Q.original_scope(q['question']), new=K.original_scope(q['question'], documents))
        for repeat in (1, 2, 3):
            if q['query_id'].startswith('N'):
                path = N.BASE / 'plans' / f"{q['query_id']}:parser:r{repeat}.json"
                row = read(path)
                oldmanifest = read(N.BASE / 'execution_manifest.json')
                P.validate_parser_row(row, identity=row['identity'], query_id=q['query_id'], repeat=repeat,
                    question=q['question'], manifest=oldmanifest, request_key=row['request_key'], cache=N.BASE / 'api_cache')
                provenance = dict(path=str(path.relative_to(ROOT)), file_sha256=P.sha(path.read_bytes()), reused=True)
            else:
                row = extract60(q, repeat, manifest, allow_network=allow_network)
                provenance = dict(path=str((B60 / 'plans' / (row['identity']+'.json')).relative_to(ROOT)), reused=False)
            plans.append(dict(query_id=q['query_id'], repeat=repeat, parser=row, provenance=provenance, scopes=scopes))
            control = K.route(Q, manual[q['query_id']], scopes['new'], DB_NEW, versions['new'][1], repaired=True)
            diagnostic.append(dict(query_id=q['query_id'],repeat=repeat,manual_plan=manual[q['query_id']],
                routing=control,policy='diagnostic only; never used to select retrieval candidates or LLM input'))
            for dbname, (directory, facts) in versions.items():
                for predicate in ('old', 'new'):
                    for scope_name, scope in scopes.items():
                        result = K.route(Q, row['plan'], scope, directory, facts, repaired=predicate=='new', parser_failed=row['status']!='ok')
                        records.append(dict(query_id=q['query_id'], repeat=repeat, db_version=dbname, predicate=predicate,
                                            scope_version=scope_name, routing=result))
        if len(plans) % 12 == 0:
            write(B60 / 'audit_progress.json', dict(completed_plans=len(plans), expected=120)); print('60 plans', len(plans), '/120', flush=True)
    if len(records) != 960 or len(plans) != 120:
        raise RuntimeError('Factorial matrix differs from960/120')
    frozen(B60 / 'parser_results.json', dict(records=plans, manifest_sha256=P.sha(manifest)))
    write(B60 / 'factorial_results.json', dict(records=records, dimensions=dict(db=2, predicate=2, scope=2),
                                               logical_comparisons=960, same_source_plans=120))
    write(B60 / 'manual_plan_diagnostic_results.json',dict(records=diagnostic,not_operational_or_model_input=True))
    vectors60(queries, manifest, allow_network=allow_network)
    resources = dict(status='factorial_and_extraction_complete_pending_source_and_code_acceptance',
        parser_plans=120, reused_plans=96, new_plan_attempts=24, new_query_vectors=8, new_chunk_vectors=0,
        physical_price=R.phase_price([60]), global_upper=X.budget_upper_ledger())
    write(B60 / 'execution_resources.json', resources)
    return resources


def prepare61(notebook):
    acceptance = read(B60 / 'completion.json')
    if acceptance['status'] != 'completed_after_source_and_independent_review':
        raise RuntimeError('Stage60 not accepted')
    R.guard_files(acceptance['files_sha256'])
    guard_inventory(read(B60/'input_inventory.json'))
    queries = read(B60 / 'questions.json')['queries']
    critical = critical_common()
    for p in (B60 / 'completion.json', B60 / 'parser_results.json', B60 / 'query_vectors.npy', B60 / 'embedding_contract.json'):
        critical[str(p.relative_to(ROOT))] = P.sha(p.read_bytes())
    manifest = X.make_manifest(61, notebook, queries, dict(parser=[], answer=[]), stage_cap='0', extra=dict(
        source_sha256=source_hashes(), critical_data_sha256=critical, logical_retrievals=720,
        prompt_variant_by_identity={}, answer_instructions_sha256_by_identity={}))
    return queries, manifest


class Runtime61(X.Runtime):
    def __init__(self):
        self.inventory=read(B60/'input_inventory.json')
        guard_inventory(self.inventory)
        super().__init__(61)
        if self.reranker_contract!=self.inventory['reranker_contract']:
            raise RuntimeError('Local reranker artifact differs from frozen input')

    def score(self,mode,query,documents,**kwargs):
        from pickcardu_rag.errors import RerankerUnavailable
        if self.delegate.artifact_contract(mode)!=self.reranker_contract:
            raise RuntimeError('Local reranker artifact changed during execution')
        started=time.perf_counter();values=[];hits=0
        for document in documents:
            lengths=self.guard.lengths(query,document)
            if lengths['pair_tokens']>self.guard.max_length or kwargs.get('document_token_limit')is not None and lengths['document_tokens']>kwargs['document_token_limit']:
                raise RerankerUnavailable('Unchanged reranker token bound exceeded; truncation forbidden')
            key=P.sha(dict(mode=mode,q=query,doc=document,kwargs=kwargs,contract=self.reranker_contract))
            current=self.path/'bge_cache'/(key+'.json')
            found=next((p for p in [current,*[directory/current.name for directory in CACHE_SOURCES]] if p.exists()),None)
            if found:
                relative=str(found.relative_to(ROOT));expected=self.inventory['files_sha256'].get(relative)
                if found==current:
                    receipt=read(self.path/'score_contracts'/(key+'.json'))
                    expected=receipt['record_sha256']
                if expected is None or P.sha(found.read_bytes())!=expected:
                    raise RuntimeError('Unbound or changed score cache record: '+relative)
                value=C.checked_score(read(found),P.sha(query),P.sha(document));hits+=1
            else:
                self.memory()
                if not self.model_loaded and self.minimum_available_kib<3145728:
                    raise RuntimeError('First reranker allocation requires3 GiB available')
                scores,trace=self.delegate.score(mode,query,[document],**kwargs);self.model_loaded=True
                if len(scores)!=1:
                    raise RuntimeError('Operating singleton reranker result count changed')
                record=dict(score=scores[0],query_sha256=P.sha(query),document_sha256=P.sha(document),measured_trace=trace)
                value=C.checked_score(record,P.sha(query),P.sha(document))
                self.write('bge_cache/'+current.name,record)
                frozen(self.path/'score_contracts'/current.name,dict(record_sha256=P.sha(current.read_bytes()),
                    query_sha256=P.sha(query),document_sha256=P.sha(document),reranker_contract_sha256=P.sha(self.reranker_contract)))
            values.append(value)
        trace=dict(cache_hits=hits,new_pairs=len(values)-hits,device='cpu',singleton=True,wall_seconds=time.perf_counter()-started)
        self.scorer_calls.append(trace)
        return values,trace


def search61(queries, manifest):
    import numpy as np
    X.guard_manifest(manifest); R.guard_files(manifest['critical_data_sha256'])
    guard_inventory(read(B60/'input_inventory.json'))
    parsers = {(r['query_id'],r['repeat']):r for r in read(B60 / 'parser_results.json')['records']}
    facts_old, facts_new = R.fact_map(DB_OLD), R.fact_map(DB_NEW)
    vectors = np.load(B60 / 'query_vectors.npy', allow_pickle=False)
    runtime = Runtime61()
    rows, jobs = [], []
    for qi, (q, vector) in enumerate(zip(queries, vectors, strict=True)):
        for repeat in (1, 2, 3):
            item = parsers[q['query_id'], repeat]; parser = item['parser']; scope = item['scopes']['new']
            routes = dict(U=dict(route='U',scope=scope['card_keys'], reason='independent_unstructured',db_attempted=False))
            for mode, db, facts in [('SEQ_BASE',DB_OLD,facts_old), ('SEQ_REPAIRED',DB_NEW,facts_new)]:
                routes[mode] = K.route(Q, parser['plan'], scope, db, facts, repaired=mode=='SEQ_REPAIRED', parser_failed=parser['status']!='ok')
            mode_order = list(C.MODES)
            shift = (qi+repeat-1) % 3; mode_order = mode_order[shift:] + mode_order[:shift]
            profile_order = list(C.PROFILES) if (qi+repeat)%2 else list(reversed(C.PROFILES))
            for profile in profile_order:
                for mode in mode_order:
                    identity = f"{q['query_id']}:{profile}:{mode}:r{repeat}"
                    route = routes[mode]
                    # Routing timings vary on replay; identity binds semantic SQL
                    # inputs, with measured SQL time retained separately.
                    binding = P.sha(dict(question=q, parser=parser, original_scope=scope,
                        scope=route['scope'], mode=mode, manifest=P.sha(manifest), profile=profile, repeat=repeat))
                    path = B61 / 'retrievals' / (identity + '.json')
                    if path.exists():
                        row = read(path); seal = read(B61 / 'retrieval_contracts' / (identity+'.json'))
                        if row['binding'] != binding or seal['row_sha256'] != P.sha(row):
                            raise RuntimeError('Saved retrieval identity/input changed')
                    else:
                        start, cpu = time.perf_counter(), time.process_time()
                        result = (dict(cards=[],evidence=[],clarify=True) if scope['clarify'] else
                                  runtime.search(q['question'],vector,profile,scope=route['scope']))
                        G.T.S.validate_scope(result, route['scope']); G.T.S.validate_scope(result, scope['card_keys'])
                        row = dict(identity=identity,query_id=q['query_id'],question=q['question'],profile=profile,mode=mode,
                            repeat=repeat,binding=binding,routing=route,original_scope=scope,result=result,
                            retrieval_wall_seconds=time.perf_counter()-start,retrieval_cpu_seconds=time.process_time()-cpu)
                        write(path,row); frozen(B61 / 'retrieval_contracts' / (identity+'.json'), dict(row_sha256=P.sha(row),binding=binding))
                    rows.append(row)
                    jobs.append({k:row[k] for k in ['identity','query_id','question','profile','mode','repeat','result']})
                    if len(rows)%12==0:
                        write(B61/'retrieval_progress.json',dict(completed=len(rows),expected=720)); print('61 retrieval',len(rows),'/720',flush=True)
    C.validate_matrix(rows,[q['query_id'] for q in queries])
    guard_inventory(read(B60/'input_inventory.json'))
    write(B61/'retrieval_results.json',dict(records=rows,manifest_sha256=P.sha(manifest)))
    frozen(B61/'jobs.json',dict(records=jobs))
    write(B61/'execution_resources.json',runtime.resources())
    return dict(retrievals=len(rows),status='completed_pending_pooled_source_review')


def prepare62(notebook):
    acceptance = read(B61/'completion.json')
    if acceptance['status'] != 'completed_after_pooled_source_and_independent_review':
        raise RuntimeError('Stage61 pooled source/safety review is incomplete')
    R.guard_files(acceptance['files_sha256'])
    jobs=read(B61/'jobs.json')['records'];queries=read(B60/'questions.json')['queries']
    C.validate_matrix(jobs,[q['query_id'] for q in queries])
    critical=critical_common()
    for p in (B61/'completion.json',B61/'jobs.json',B61/'source_judgments.json',B61/'evaluation.json'):
        critical[str(p.relative_to(ROOT))]=P.sha(p.read_bytes())
    prompts=G.C.operating_prompts(); ids=[j['identity']for j in jobs]
    manifest=X.make_manifest(62,notebook,queries,dict(parser=[],answer=ids),maximum_answer=1440,stage_cap='.90',extra=dict(
        source_sha256=source_hashes(),critical_data_sha256=critical,logical_answers=720,combined_new_cost_cap_usd='1',
        prompt_variant_by_identity={i:'old'for i in ids},answer_instructions_sha256_by_identity={i:[P.sha(p)for p in prompts]for i in ids}))
    G.freeze_payloads(62,jobs,manifest)
    return jobs,manifest


def validate_answer(row,job,manifest,*,source=None):
    contract=G.payload_contract(job['identity'],job['question'],job['result']['evidence'],manifest)
    G.M.R.validate_generated_answer(row,job['question'],job['result']['evidence'],stage=62,identity=job['identity'],
        manifest=manifest,contract=contract,reused=source)
    if not source:
        G.validate_checkpoint_prompt(row,manifest,{})
    if row['evidence_sha256']!=P.sha(job['result']['evidence']) or row['retrieval_sha256']!=P.sha(job['result']):
        raise RuntimeError('Answer/retrieval provenance changed')


def answer62(job,manifest,*,allow_network=False,source_job=None,source=None,source_path=None):
    if source is None:
        G.M.X.Cache=Cache
        row=G.M.answer(62,job['identity'],job['question'],job['result'],allow_network=allow_network)
    else:
        if not C.same_payload(source_job,job,G.C.operating_prompts()) or source['status']!='ok':
            raise RuntimeError('Reuse source differs in repeat/query/profile/evidence')
        contract=G.payload_contract(job['identity'],job['question'],job['result']['evidence'],manifest)
        if source['input_sha256']!=contract['input_sha256_by_identity'][job['identity']]:
            raise RuntimeError('Exact serialized input mismatch on reuse')
        path=B62/'answers'/(job['identity']+'.json')
        if path.exists():
            row=read(path)
        else:
            start=time.perf_counter()
            row=dict(identity=job['identity'],status=source['status'],answer=copy.deepcopy(source['answer']),usage={},error=source.get('error'),
                events=[],request_keys=[],manifest_sha256=P.sha(manifest),payload_contract_sha256=P.sha(contract),
                input_sha256=source['input_sha256'],retrieval_sha256=P.sha(job['result']),evidence_sha256=P.sha(job['result']['evidence']),
                reuse_source_sha256=P.sha(source),reuse_source_path=source_path,source_answer_ready_wall_seconds=source['answer_ready_wall_seconds'],
                answer_ready_wall_seconds=time.perf_counter()-start,answer_cpu_seconds=0,first_token_seconds=None,browser_display_seconds=None,
                cache_policy='same-repeat exact verified serialized provider payload; original generation timing separate')
            write(path,row)
    validate_answer(row,job,manifest,source=source)
    return row


def run62(jobs,manifest,*,allow_network=False):
    X.guard_manifest(manifest);R.guard_files(manifest['critical_data_sha256'])
    historical={(r['query_id'],r['profile'],r['repeat']):r for r in read(N.BASE/'answer_results.json')['records']}
    historical_jobs={j['identity']:j for j in read(N.BASE/'jobs.json')['records']}
    seen={};records=[];start,cpu=time.perf_counter(),time.process_time()
    for job in jobs:
        signature=(job['query_id'],job['profile'],job['repeat'],P.sha(job['result']['evidence']))
        source_job=source=source_path=None
        prior=historical.get(signature[:3])
        if prior and C.same_payload(historical_jobs[prior['identity']],job,G.C.operating_prompts()) and prior['answer']['status']=='ok':
            source_job=historical_jobs[prior['identity']];source=prior['answer'];source_path=str((N.BASE/'answers'/(prior['identity']+'.json')).relative_to(ROOT))
            oldmanifest=read(N.BASE/'execution_manifest.json')
            G.M.R.validate_generated_answer(source,source_job['question'],source_job['result']['evidence'],stage=59,identity=source['identity'],
                manifest=oldmanifest,contract=G.payload_contract(source['identity'],source_job['question'],source_job['result']['evidence'],oldmanifest))
            G.validate_checkpoint_prompt(source,oldmanifest,{})
        elif signature in seen:
            source_job,source,source_path=seen[signature]
        row=answer62(job,manifest,allow_network=allow_network,source_job=source_job,source=source,source_path=source_path)
        if row['status']=='ok' and not row.get('reuse_source_sha256'):
            seen[signature]=(job,row,str((B62/'answers'/(job['identity']+'.json')).relative_to(ROOT)))
        records.append({**{k:v for k,v in job.items()if k!='result'},'answer':row})
        write(B62/'answer_progress.json',dict(completed=len(records),expected=720,last_identity=job['identity'],invocation_wall_seconds=time.perf_counter()-start))
        if len(records)%12==0:print('62 answer',len(records),'/720',flush=True)
        if X.budget_upper_ledger()['unknown_cost_records']:
            raise RuntimeError('New unknown answer usage; stop remaining requests')
    C.validate_matrix(records,[q['query_id']for q in read(B60/'questions.json')['queries']])
    write(B62/'answer_results.json',dict(records=records,manifest_sha256=P.sha(manifest)))
    resources=dict(status='answers_complete_pending_manual_adjudication',logical_answers=len(records),
        reused_answers=sum(bool(r['answer'].get('reuse_source_sha256'))for r in records),physical_price=R.phase_price([62]),
        invocation_wall_seconds=time.perf_counter()-start,invocation_cpu_seconds=time.process_time()-cpu,
        peak_process_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        ttft_seconds=None,browser_display_seconds=None,online_end_to_end_seconds=None,global_upper=X.budget_upper_ledger())
    write(B62/'execution_resources.json',resources)
    return resources
