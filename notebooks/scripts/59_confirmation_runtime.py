"""Sequential59 resume adapter; questions/gold are created only after58 choice.

Owns one bounded embedding batch and parser/search checkpoints. Answer journals
and prompt/ownership/budget guards are reused from58, rather than copied.
"""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import time

import importlib.util
import sys

spec=importlib.util.spec_from_file_location('generation59',Path(__file__).with_name('58_generation_runtime.py'))
G=importlib.util.module_from_spec(spec);sys.modules[spec.name]=G;spec.loader.exec_module(G)
R,P,X,Q,T=G.R,G.P,G.X,G.Q,G.T
BASE=P.F.STAGES[59]
GROUP_COUNTS={'recommend_single':12,'recommend_multiple':8,'recommend_issuer_spend':4,
              'detail_spend_cap_frequency':3,'detail_exception_channel':2,'detail_calculation':1,'negative_uncertain':2}


def validate_selection_gate(selection, completion, answers, judgments):
    if (selection.get('status')!='selected_after_source_fact_review'
            or completion.get('status')!='completed_after_independent_review'
            or completion.get('configuration_selection_sha256')!=P.sha(selection)
            or completion.get('answer_results_sha256')!=P.sha(answers)
            or completion.get('judgments_sha256')!=P.sha(judgments)
            or not completion.get('evaluation_files_sha256')):
        raise RuntimeError('Stage58 configuration selection is not sealed')
    choices=[c for c in selection.get('configurations',[])
             if c.get('config_id')==selection.get('selected_config_id')]
    if (len(choices)!=1 or choices[0].get('eligible') is not True
            or choices[0].get('failures')!=[]):
        raise RuntimeError('Stage58 selected configuration is not eligible')
    review_path=completion.get('independent_review_path')
    review_hash=completion.get('independent_review_file_sha256')
    files=completion['evaluation_files_sha256']
    if not review_path or not review_hash or files.get(review_path)!=review_hash:
        raise RuntimeError('Stage58 independent review receipt is not sealed')
    R.guard_files(completion['evaluation_files_sha256'])
    review=R.read(R.ROOT/review_path)
    expected_bindings=dict(configuration_selection_sha256=P.sha(selection),
        answer_results_sha256=P.sha(answers),source_judgments_sha256=P.sha(judgments),
        fact_evaluation_sha256=P.sha(R.read(G.BASE/'fact_evaluation.json')))
    reviewed_files=review.get('reviewed_evaluation_files_sha256',{})
    core={str((G.BASE/name).relative_to(R.ROOT)) for name in [
        'configuration_selection.json','answer_results.json','source_judgments.json','fact_evaluation.json']}
    if (review.get('status')!='accepted' or not review.get('reviewer')
            or review.get('independent_review') is not True
            or review.get('blocking_findings')!=0 or review.get('major_findings')!=0
            or review.get('bindings')!=expected_bindings
            or completion.get('fact_evaluation_sha256')!=expected_bindings['fact_evaluation_sha256']
            or not core.issubset(reviewed_files) or review_path in reviewed_files
            or files!={**reviewed_files,review_path:review_hash}):
        raise RuntimeError('Stage58 independent review does not accept these evaluation artifacts')


def freeze_configuration(notebook):
    selection=R.read(G.BASE/'configuration_selection.json')
    completion=R.read(G.BASE/'completion_status.json')
    validate_selection_gate(selection,completion,R.read(G.BASE/'answer_results.json'),R.read(G.BASE/'source_judgments.json'))
    target=BASE/'configuration_contract.json'
    choice=next(c for c in selection['configurations'] if c['config_id']==selection['selected_config_id'])
    source_paths=set(R.read(G.BASE/'execution_manifest.json')['source_sha256'])|{
        'notebooks/scripts/59_confirmation_runtime.py'}
    contract=dict(config_id=choice['config_id'],prompt_variant=choice['prompt_variant'],
        evidence_variant_by_profile=choice['evidence_variant_by_profile'],
        stage58_selection_sha256=P.sha(selection),stage58_completion_sha256=P.sha(completion),
        source_sha256={p:P.sha((R.ROOT/p).read_bytes()) for p in sorted(source_paths)},
        notebook_code_sha256=X.notebook_code_hash(notebook))
    if target.exists():
        saved=R.read(target)
        if {k:v for k,v in saved.items() if k!='selected_at_epoch'}!=contract:
            raise RuntimeError('Confirmation configuration changed after freeze')
        return saved
    if (BASE/'questions.json').exists() or (BASE/'source_reference.json').exists():
        raise RuntimeError('New questions or gold exist before configuration freeze')
    contract['selected_at_epoch']=time.time();X.frozen(target,contract)
    return contract


def validate_questions(payload,contract,previous):
    if (payload.get('configuration_contract_sha256')!=P.sha(contract)
            or payload.get('created_at_epoch',0)<=contract['selected_at_epoch']):
        raise ValueError('Question creation chronology is not bound to frozen configuration')
    queries=payload['queries']
    if len(queries)!=32 or Counter(q['subgroup'] for q in queries)!=Counter(GROUP_COUNTS):
        raise ValueError('New32 question strata differ from the approved counts')
    if len({q['query_id'] for q in queries})!=32 or len({q['question'].strip() for q in queries})!=32:
        raise ValueError('Confirmation questions and IDs must be unique')
    if ({q['query_id'] for q in queries}&{q['query_id'] for q in previous}
            or {q['question'].strip() for q in queries}&{q['question'].strip() for q in previous}):
        raise ValueError('Confirmation overlaps a previous development question')
    if any(not q['question'].strip() or len(q['question'])>500 for q in queries):
        raise ValueError('Invalid confirmation question text')


def validate_reference(q,ref):
    if ref['question_sha256']!=P.sha(q['question']) or not ref.get('reviewed_by') or not ref.get('reason'):
        raise RuntimeError('Source reference does not match its question')
    if not ref['facts'] or any(f['source_status'] not in ['verified','uncertain'] for f in ref['facts']):
        raise RuntimeError('Confirmation source facts are missing or unreviewed')
    if q['subgroup']!='negative_uncertain' and any(f['source_status']!='verified' for f in ref['facts']):
        raise RuntimeError('Answerable confirmation question has unresolved source facts')


def prepare(notebook):
    contract=freeze_configuration(notebook)
    payload=R.read(BASE/'questions.json');previous=R.read(T.OLD/'questions.json')['queries']
    validate_questions(payload,contract,previous);queries=payload['queries']
    reference=R.read(BASE/'source_reference.json')
    if (reference['status']!='accepted_before_api' or reference['questions_sha256']!=P.sha(payload)
            or set(reference['by_query'])!={q['query_id'] for q in queries}):
        raise RuntimeError('New question source reference is not accepted and frozen')
    evaluator=G.load('source59','55_fact_evaluation.py')
    critical={str(p.relative_to(R.ROOT)):P.sha(p.read_bytes()) for p in [
        BASE/'configuration_contract.json',BASE/'questions.json',BASE/'source_reference.json',
        G.BASE/'configuration_selection.json',G.BASE/'completion_status.json',G.BASE/'source_judgments.json',
        G.BASE/'answer_results.json',G.BASE/'execution_manifest.json']}
    critical.update(R.read(G.BASE/'execution_manifest.json')['critical_data_sha256'])
    critical.update(R.read(G.BASE/'completion_status.json')['evaluation_files_sha256'])
    for q in queries:
        ref=reference['by_query'][q['query_id']]
        validate_reference(q,ref)
        for fact in ref['facts']:
            if fact['source_status'] not in ['verified','uncertain'] or not fact.get('source_support'):
                raise RuntimeError('Unreviewed source fact')
            for source in fact['source_support']:
                evaluator.validate_source_support(source,R.ROOT)
                for name in [source['path'],source.get('manual_review_path')]:
                    if name:critical[name]=P.sha((R.ROOT/name).read_bytes())
                if source.get('manual_review_path'):
                    record=next(r for r in R.read(R.ROOT/source['manual_review_path'])['records'] if r['review_id']==source['manual_review_id'])
                    critical[record['image_path']]=P.sha((R.ROOT/record['image_path']).read_bytes())
    parser_version=R.read(R.BASE/'parser_selection.json')['parser_version']
    prompt=Q.PROMPT if parser_version=='old' else R.NEW_PROMPT
    ids=[f"{q['query_id']}:parser:r{r}" for q in queries for r in [1,2,3]]
    answer_ids=[f"{q['query_id']}:{p}:{contract['evidence_variant_by_profile'][p]}:{contract['prompt_variant']}:r{r}"
                for q in queries for p in X.PROFILES for r in [1,2,3]]
    prompts=G.C.operating_prompts()
    manifest=X.make_manifest(59,notebook,queries,dict(parser=ids,answer=answer_ids),
        maximum_parser=96,maximum_answer=384,maximum_embedding=1,stage_cap='2',extra=dict(
            source_sha256=contract['source_sha256'],critical_data_sha256=critical,
            parser_instructions_sha256=P.sha(prompt),selected_parser_version=parser_version,
            logical_answers=192,prompt_variant_by_identity={i:contract['prompt_variant'] for i in answer_ids},
            answer_instructions_sha256_by_identity={i:[P.sha(G.C.transform_prompt(p,contract['prompt_variant'])) for p in prompts] for i in answer_ids},
            configuration_contract_sha256=P.sha(contract),question_quality_split='new wording on same106 source documents; no new U baseline or production promotion'))
    return queries,contract,manifest,prompt


def extract(q,repeat,prompt,*,allow_network=False):
    identity=f"{q['query_id']}:parser:r{repeat}";cache=R.Cache(59,'parser',identity,allow_network=allow_network)
    kwargs=dict(model='gpt-6-luna',instructions=prompt,input=[dict(role='user',content=q['question'])],
        text_format=Q.QueryPlan,tools=[],store=False,max_output_tokens=3000)
    cache.check_contract(kwargs);target=BASE/'plans'/(identity+'.json')
    if target.exists():row=R.read(target)
    else:
        start,cpu=time.perf_counter(),time.process_time();value,error=None,None
        try:
            response=cache.parse(**kwargs);value=response.output_parsed.model_dump(mode='json') if response.output_parsed else None
        except Exception as exc:
            if not cache.events:raise
            error=f'{type(exc).__name__}: {exc}'
        row=dict(identity=identity,query_id=q['query_id'],repeat=repeat,question=q['question'],
            question_sha256=P.sha(q['question']),manifest_sha256=P.sha(cache.manifest),request_key=cache.key(kwargs),
            status='ok' if value is not None else 'failed',error=error,plan=value,events=cache.events,
            parser_wall_seconds=time.perf_counter()-start,parser_cpu_seconds=time.process_time()-cpu)
        R.write(target,row)
    P.validate_parser_row(row,identity=identity,query_id=q['query_id'],repeat=repeat,question=q['question'],
        manifest=cache.manifest,request_key=cache.key(kwargs),cache=cache.cache)
    if X.budget_upper_ledger()['unknown_cost_records']:raise RuntimeError('New unknown parser cost')
    return row


def embeddings(queries,manifest,*,allow_network=False):
    """One raw-before-parse batch; successful raw can restore unfinished .npy."""
    import numpy as np
    X.guard_manifest(manifest);R.guard_files(manifest['critical_data_sha256'])
    request=dict(model='text-embedding-3-small',input=[q['question'] for q in queries],dimensions=1536)
    path=BASE/'api_cache'/('embedding_batch_'+P.sha(request)+'.json')
    if path.exists():
        raw=R.read(path)
        if raw['request']!=request or raw['status']!='ok':raise RuntimeError('Failed/incomplete embedding requires inspection; no automatic retry')
    else:
        if not allow_network:raise RuntimeError('No offline confirmation embedding batch')
        bound=Decimal(len(P.F.canonical(request).encode())+8192)*Decimal('.02')/1000000
        R.require_budget(X.budget_upper_ledger(),bound,'8');R.require_budget(R.phase_price([59]),bound,'2')
        if len(list((BASE/'api_cache').glob('*.json')))>=manifest['maximum_physical_requests']:
            raise RuntimeError('Confirmation physical request cap reached')
        start,cpu=time.perf_counter(),time.process_time()
        raw=dict(status='started',request=request);R.write(path,raw)
        try:
            response=P.E.C.api_client().embeddings.create(**request)
            raw.update(raw_response=response.model_dump(mode='json'),status='ok')
            raw['usage']=raw['raw_response'].get('usage',{})
        except Exception as exc:
            raw.update(status='failed',error=f'{type(exc).__name__}: {exc}');raise
        finally:
            raw.update(wall_seconds=time.perf_counter()-start,cpu_seconds=time.process_time()-cpu);R.write(path,raw)
    if raw.get('usage')!=raw['raw_response'].get('usage'):raise RuntimeError('Embedding usage differs from original provider raw')
    items=sorted(raw['raw_response']['data'],key=lambda x:x['index'])
    if [item['index'] for item in items]!=list(range(32)):raise RuntimeError('Embedding provider row count/index changed')
    matrix=np.asarray([item['embedding'] for item in items],dtype=np.float32)
    if matrix.shape!=(32,1536) or not np.isfinite(matrix).all():raise RuntimeError('Invalid confirmation vectors')
    target=BASE/'query_vectors.npy'
    if target.exists():
        if not np.array_equal(np.load(target,allow_pickle=False),matrix):raise RuntimeError('Saved vectors differ from provider raw')
    else:np.save(target,matrix)
    X.frozen(BASE/'embedding_contract.json',dict(raw_request_sha256=P.sha(raw),matrix_sha256=P.sha(matrix.tobytes()),questions_sha256=P.sha(queries)))
    if X.budget_upper_ledger()['unknown_cost_records']:raise RuntimeError('New unknown embedding usage')
    return matrix


def run(queries,contract,manifest,prompt,*,allow_network=False):
    from pickcardu_rag.answering import measure_answer_payload
    X.guard_manifest(manifest);R.guard_files(manifest['critical_data_sha256'])
    parsers={}
    for q in queries:
        for repeat in [1,2,3]:parsers[(q['query_id'],repeat)]=extract(q,repeat,prompt,allow_network=allow_network)
    R.write(BASE/'parser_results.json',dict(records=list(parsers.values())))
    vectors=embeddings(queries,manifest,allow_network=allow_network)
    runtime=X.Runtime(59);facts=R.fact_map(R.DB2);jobs=[];retrievals=[]
    for q,vector in zip(queries,vectors,strict=True):
        original=Q.original_scope(q['question'])
        for repeat in [1,2,3]:
            parser=parsers[(q['query_id'],repeat)]
            route=R.route(parser['plan'],original,R.DB2,facts=facts,parser_failed=parser['status']!='ok')
            for profile in X.PROFILES:
                evidence_variant=contract['evidence_variant_by_profile'][profile]
                identity=f"{q['query_id']}:{profile}:{evidence_variant}:{contract['prompt_variant']}:r{repeat}"
                binding=P.sha(dict(question=q,parser=parser,scope=route['scope'],manifest=manifest,
                    identity=identity,profile=profile,evidence_variant=evidence_variant,repeat=repeat))
                path=BASE/'retrievals'/(identity+'.json')
                if path.exists():
                    row=R.read(path);seal=R.read(BASE/'retrieval_contracts'/(identity+'.json'))
                    validate_retrieval_checkpoint(row,seal,binding,identity,q['query_id'],profile,repeat,route['scope'])
                else:
                    start=time.perf_counter()
                    result=(dict(cards=[],evidence=[],clarify=True) if route['route']=='clarify' else runtime.search(q['question'],vector,profile,scope=route['scope']))
                    if evidence_variant=='B' and result['cards']:
                        chunks,lexical,*_=runtime.profile(profile);mapping={c.chunk_id:c for c in chunks};pool=[]
                        for card in result['cards']:
                            for candidate in lexical.search(q['question']+' '+T.S.SUFFIX,limit=50,card_keys=(card['card_key'],)):
                                c=mapping[candidate.chunk_id]
                                pool.append(dict(chunk_id=c.chunk_id,card_key=c.card_key,card_name=c.card_name,issuer=c.issuer,
                                    text=c.text,page_num=c.page_num,section=c.section,level=c.level,heading_path=list(c.heading_path)))
                        result=T.S.supplement(q['question'],result,pool,
                            lambda query,units:runtime.score('bge',query,[u['text'] for u in units],document_token_limit=4096)[0],
                            fits=lambda question,evidence:measure_answer_payload(question,evidence)[0]<=64000)
                    T.S.validate_scope(result,route['scope'])
                    row=dict(identity=identity,query_id=q['query_id'],profile=profile,repeat=repeat,binding=binding,
                        routing=route,result=result,retrieval_wall_seconds=time.perf_counter()-start)
                    R.write(path,row);X.frozen(BASE/'retrieval_contracts'/(identity+'.json'),dict(binding=binding,row_sha256=P.sha(row)))
                retrievals.append(row)
                jobs.append(dict(identity=identity,query_id=q['query_id'],profile=profile,repeat=repeat,
                    evidence_variant=evidence_variant,prompt_variant=contract['prompt_variant'],diagnostic=False,
                    question=q['question'],result=row['result']))
                if len(jobs)%8==0:print('59 retrieval',len(jobs),'/192',flush=True)
    R.write(BASE/'retrieval_results.json',dict(records=retrievals,manifest_sha256=P.sha(manifest)))
    X.frozen(BASE/'jobs.json',dict(records=jobs));G.freeze_payloads(59,jobs,manifest)
    R.write(BASE/'retrieval_execution_resources.json',runtime.resources())
    return G.run(jobs,manifest,allow_network=allow_network)


def validate_retrieval_checkpoint(row,seal,binding,identity,qid,profile,repeat,scope):
    if (row['binding']!=binding or row['identity']!=identity or row['query_id']!=qid
            or row['profile']!=profile or row['repeat']!=repeat
            or seal!=dict(binding=binding,row_sha256=P.sha(row))):
        raise RuntimeError('Confirmation retrieval checkpoint changed identity/profile/evidence setting')
    T.S.validate_scope(row['result'],scope)
