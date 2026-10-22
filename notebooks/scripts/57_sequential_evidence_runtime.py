"""Resumable local retrieval for notebook57; no provider calls or report code.

Shares the exact whole-section supplement with58 and journals CPU inference so
an interrupted notebook does not repeat expensive local reranking.
"""
import importlib.util
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


R = load('shared57', '56_structured_experiment_runtime.py')
S = load('condition57', '57_condition_evidence.py')
P, X, Q = R.P, R.X, R.Q
BASE = P.F.STAGES[57]
OLD = P.DATA / '52_corrected_four_retrieval_modes'


def preserved_vectors(queries, *, with_receipt=False):
    """Bind existing vectors to the actual saved embedding-provider batch."""
    import numpy as np
    executed=R.read(OLD/'execution_results.json');upstream=R.read(OLD/'execution_manifest.json')
    summary=R.read(OLD/'evaluation_summary.json');completion=R.read(OLD/'completion_status.json')
    evaluator=load('seal57','55_fact_evaluation.py')
    evaluator.validate_seal(summary,completion,execution_sha=P.sha(executed),
        judgments_sha=P.sha(R.read(OLD/'question_source_judgments.json')),summary_sha=P.sha(summary))
    if executed['manifest_sha256']!=P.sha(upstream) or P.sha(queries)!=upstream['questions_sha256']:
        raise RuntimeError('Embedding questions or stage52 manifest differ from sealed execution')
    provenance=R.read(OLD/'embedding_reuse_provenance.json')
    vector_files={Path(item['path']).stem:ROOT/item['path'] for item in provenance['vector_files']}
    receipt={item['path']:item['sha256'] for item in provenance['vector_files']}
    R.guard_files(receipt)
    batch_paths=sorted({p for path in vector_files.values() for p in path.parent.parent.glob('embedding_batch_*.json')})
    receipt.update({str(p.relative_to(ROOT)):P.sha(p.read_bytes()) for p in batch_paths})
    batches = [R.read(p) for p in batch_paths]
    rows = []
    for q in queries:
        text = q['question'];key=P.sha(dict(text=text,model='text-embedding-3-small',dimension=1536))
        vector = np.load(vector_files[key],allow_pickle=False)
        proofs=[]
        for batch in batches:
            request=batch['request']
            if text not in request['input']:continue
            if batch['status']!='ok' or request['model']!='text-embedding-3-small' or request['dimensions']!=1536:
                raise RuntimeError('Prior embedding batch contract differs')
            index=request['input'].index(text)
            item=next(item for item in batch['raw_response']['data'] if item['index']==index)
            proofs.append(np.asarray(item['embedding'],dtype=np.float32))
        if not proofs or vector.shape!=(1536,) or not all(np.array_equal(vector,proof) for proof in proofs):
            raise RuntimeError('Vector lacks exact provider-response proof')
        used=[r for r in executed['retrievals'] if r['query_id']==q['query_id'] and r['mode']=='U'
              and r['result'].get('experiment_component_identity')]
        if not used and not Q.original_scope(text)['clarify']:
            raise RuntimeError('Used vector lacks sealed retrieval identity')
        for prior in used:
            expected=P.sha(dict(question=text,vector=P.sha(np.asarray(vector,dtype=np.float32).tobytes()),
                profile=prior['profile'],scope=prior['original_scope']['card_keys'],depth=50))
            if expected!=prior['result']['experiment_component_identity']:
                raise RuntimeError('Vector differs from the original sealed stage52 retrieval input')
        rows.append(vector)
    matrix=np.asarray(rows,dtype=np.float32)
    contract=R.read(OLD/'question_embedding_contract.json')
    if (contract['matrix_sha256']!=P.sha(matrix.tobytes()) or
            contract['texts_sha256']!=P.sha([q['question'] for q in queries]) or
            provenance['original_contract_sha256']!=P.sha(contract)):
        raise RuntimeError('Matrix differs from original stage52 embedding contract')
    for path in [OLD/'question_embedding_contract.json',OLD/'embedding_reuse_provenance.json']:
        receipt[str(path.relative_to(ROOT))]=P.sha(path.read_bytes())
    return (matrix,receipt) if with_receipt else matrix


def prepare(notebook):
    selection=R.read(R.BASE/'parser_selection.json')
    if selection['status']!='selected_after_manual_review':
        raise RuntimeError('Parser selection is not finalized')
    queries=R.read(OLD/'questions.json')['queries']
    stage=560 if selection['parser_version']=='old' else 561
    preserved={r['parser']['query_id']:r for r in R.read(OLD/'rerouted_repeat1_plans.json')['records']}
    facts=R.fact_map(R.DB2);plans={}
    for q in queries:
        qid=q['query_id']
        parser=(R.read(P.F.STAGES[stage]/'plans'/(qid+'_detailed:parser:r1.json'))
                if qid not in ['H25','H26','H32'] else preserved[qid]['parser'])
        original=Q.original_scope(q['question'])
        plans[qid]=dict(parser=parser,original_scope=original,
            routing=R.route(parser['plan'],original,R.DB2,facts=facts,parser_failed=parser['status']!='ok'))
    X.frozen(BASE/'queries.json',dict(queries=queries));X.frozen(BASE/'plans.json',plans)
    critical={str(p.relative_to(ROOT)):P.sha(p.read_bytes()) for p in [
        R.BASE/'parser_selection.json',R.BASE/'parser_results.json',BASE/'queries.json',BASE/'plans.json',
        R.DB2/'structured.sqlite',R.DB2/'facts.jsonl',OLD/'completion_status.json',OLD/'evaluation_summary.json',
        OLD/'execution_manifest.json',OLD/'execution_results.json',OLD/'question_source_judgments.json',OLD/'questions.json']}
    _,embedding_receipt=preserved_vectors(queries,with_receipt=True)
    X.frozen(BASE/'historical_embedding_receipt.json',embedding_receipt)
    critical.update(embedding_receipt)
    critical[str((BASE/'historical_embedding_receipt.json').relative_to(ROOT))]=P.sha((BASE/'historical_embedding_receipt.json').read_bytes())
    sources=set(R.read(P.F.STAGES[49]/'execution_manifest.json')['source_sha256']) | {
        'notebooks/scripts/50_query_contract.py','notebooks/scripts/51_experiment_runtime.py',
        'notebooks/scripts/56_structured_experiment_runtime.py','notebooks/scripts/57_condition_evidence.py',
        'notebooks/scripts/57_sequential_evidence_runtime.py','notebooks/scripts/55_fact_evaluation.py'}
    receipt=[]
    for path in sorted((OLD/'bge_cache').glob('*.json')):
        destination=BASE/'bge_cache'/path.name
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists() and P.sha(destination.read_bytes())!=P.sha(path.read_bytes()):
            raise RuntimeError('Conflicting previously copied rerank cache')
        if not destination.exists():shutil.copyfile(path,destination)
        receipt.append(dict(source=str(path.relative_to(ROOT)),sha256=P.sha(path.read_bytes()),copy=str(destination.relative_to(ROOT))))
    X.frozen(BASE/'historical_rerank_cache_receipt.json',dict(records=receipt))
    critical[str((BASE/'historical_rerank_cache_receipt.json').relative_to(ROOT))]=P.sha((BASE/'historical_rerank_cache_receipt.json').read_bytes())
    for record in receipt:
        critical[record['source']]=critical[record['copy']]=record['sha256']
    # The unchanged inherited scorer may also read these historical fallbacks.
    # Freeze every such file before the run, not just the newly copied52 cache.
    for name in ['42_expanded_query_evaluation','41_query_expansion','40_new_query_comparison',
                 '34_bundle_comparison','32_structured_search_comparison']:
        for path in sorted((P.DATA/name/'bge_cache').glob('*.json')):
            critical[str(path.relative_to(ROOT))]=P.sha(path.read_bytes())
    manifest=X.make_manifest(57,notebook,queries,dict(parser=[],answer=[]),stage_cap='0',
        extra=dict(critical_data_sha256=critical,source_sha256={name:P.sha((ROOT/name).read_bytes()) for name in sorted(sources)},
                   experiment='sequential only; A5 versus A5 plus at most two same-card condition sections',
                   evidence_selection_uses_gold=False,new_embeddings=0))
    return queries,plans,manifest


def run(queries, plans, manifest):
    from pickcardu_rag.answering import measure_answer_payload
    X.guard_manifest(manifest);R.guard_files(manifest['critical_data_sha256'])
    vectors=preserved_vectors(queries);runtime=X.Runtime(57);records=[]
    start=time.perf_counter()
    for q,vector in zip(queries,vectors,strict=True):
        route=plans[q['query_id']]['routing']
        for profile in X.PROFILES:
            identity=q['query_id']+':'+profile
            target=BASE/'retrievals'/(identity+'.json')
            binding=P.sha(dict(question=q,plan=plans[q['query_id']],manifest=manifest))
            if target.exists():
                row=R.read(target)
                contract=R.read(BASE/'retrieval_contracts'/(identity+'.json'))
                if (row['binding']!=binding or row['query_id']!=q['query_id'] or row['profile']!=profile
                        or row['A_sha256']!=P.sha(row['A']) or row['B_sha256']!=P.sha(row['B'])
                        or contract!=dict(binding=binding,row_sha256=P.sha(row))):
                    raise RuntimeError('Retrieval checkpoint changed inputs or results')
            else:
                began,cpu=time.perf_counter(),time.process_time()
                a=(dict(cards=[],evidence=[],clarify=True) if route['route']=='clarify' else
                   runtime.search(q['question'],vector,profile,scope=route['scope']))
                supplement_start,supplement_cpu=time.perf_counter(),time.process_time()
                pool=[]
                if a['cards']:
                    chunks,lexical,*_=runtime.profile(profile);mapping={c.chunk_id:c for c in chunks}
                    for card in a['cards']:
                        for candidate in lexical.search(q['question']+' '+S.SUFFIX,limit=50,card_keys=(card['card_key'],)):
                            c=mapping[candidate.chunk_id]
                            pool.append(dict(chunk_id=c.chunk_id,card_key=c.card_key,card_name=c.card_name,
                                issuer=c.issuer,text=c.text,page_num=c.page_num,section=c.section,level=c.level,
                                heading_path=list(c.heading_path)))
                b=S.supplement(q['question'],a,pool,
                    lambda query,units:runtime.score('bge',query,[u['text'] for u in units],document_token_limit=4096)[0],
                    fits=lambda question,evidence:measure_answer_payload(question,evidence)[0]<=64000)
                S.validate_scope(a,route['scope']);S.validate_scope(b,route['scope'])
                row=dict(query_id=q['query_id'],profile=profile,binding=binding,A=a,B=b,
                    retrieval_plus_supplement_wall_seconds=time.perf_counter()-began,
                    retrieval_plus_supplement_cpu_seconds=time.process_time()-cpu,
                    supplement_wall_seconds=time.perf_counter()-supplement_start,
                    supplement_cpu_seconds=time.process_time()-supplement_cpu,
                    supplement_lexical_candidate_count=len(pool),
                    A_payload_bytes=measure_answer_payload(q['question'],a['evidence'])[0],
                    B_payload_bytes=measure_answer_payload(q['question'],b['evidence'])[0],
                    A_sha256=P.sha(a),B_sha256=P.sha(b))
                R.write(target,row)
                X.frozen(BASE/'retrieval_contracts'/(identity+'.json'),dict(binding=binding,row_sha256=P.sha(row)))
            S.validate_scope(row['A'],route['scope']);S.validate_scope(row['B'],route['scope'])
            records.append(row)
            R.write(BASE/'progress.json',dict(completed=len(records),expected=80,last_identity=identity,
                    invocation_wall_seconds=time.perf_counter()-start))
            print('57 retrieval',len(records),'/80',identity,flush=True)
    R.write(BASE/'retrieval_results.json',dict(records=records,manifest_sha256=P.sha(manifest)))
    resources=runtime.resources();resources.update(new_provider_calls=0,new_embeddings=0,
        status='retrieval_complete_awaiting_source_fact_review',ttft_seconds=None,browser_display_seconds=None)
    R.write(BASE/'execution_resources.json',resources)
    return resources
