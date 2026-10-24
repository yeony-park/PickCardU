"""Paid notebook entry gate; keep the already executed60/61 runtime immutable.

Resolve the61 index into the actual manual records and original PDF/image proof
files. A completion that omits one of these dependencies is not sufficient.
"""
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def require_coverage(actual, required):
    if any(actual.get(name)!=sha for name,sha in required.items()):
        raise ValueError('Completion omits or changes a required source/code dependency')


def require_record(index, record, result_sha256):
    if (index['identity']!=record['identity']or index['manual_record_sha256']!=digest(record)
            or index['result_sha256']!=result_sha256 or record['result_sha256']!=result_sha256):
        raise ValueError('Manual record or actual result differs from the source index')


def require_status(status):
    if status!='completed_after_pooled_source_and_independent_review':
        raise ValueError('Pooled source review has not received independent acceptance')


def collect_dependencies(stage, notebook):
    root=stage.ROOT;read=stage.read;base=stage.B61;paths=set();proof_cache=set()
    def track(name):
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()):raise ValueError('Source dependency escapes repository')
        paths.add(path);return path
    index=read(base/'source_judgments.json');jobs=read(base/'jobs.json')['records']
    queries=read(stage.B60/'questions.json')['queries'];qmap={q['query_id']:q for q in queries}
    stage.C.validate_matrix(jobs,list(qmap));stage.C.validate_matrix(index['records'],list(qmap))
    expected_manual={str((base/'source_review'/(q+'.json')).relative_to(root))for q in qmap}
    if set(index['manual_files_sha256'])!=expected_manual:raise ValueError('Missing or extra manual question file')
    stage.R.guard_files(index['manual_files_sha256'])
    catalogs={q:read(track(str((base/'source_review'/(q+'.json')).relative_to(root))))for q in qmap}
    for q,catalog in catalogs.items():
        actual={c['card_key']for j in jobs if j['query_id']==q for c in j['result']['cards']}
        if set(catalog['source_card_catalog'])!=actual:raise ValueError('Pooled source catalog differs from actual card union')
    records={r['identity']:r for d in catalogs.values()for r in d['records']}
    if len(records)!=len(jobs)or sum(len(d['records'])for d in catalogs.values())!=len(jobs):
        raise ValueError('Incomplete or duplicate manual records')
    byid={r['identity']:r for r in index['records']};evaluator=stage.load('sourcegate62_e','61_evaluate_evidence.py')
    source=stage.G.load('sourcegate62_s','55_fact_evaluation.py')
    documents=stage.DB_OLD/'documents.jsonl';track(str(documents.relative_to(root)))
    owners={d['document_id']:d['source_pdf_sha256']for d in(json.loads(l)for l in documents.read_text().splitlines())}
    def validate_proof(proof):
        key=digest(proof)
        if key in proof_cache:return
        track(proof['path']);source.validate_source_support(proof,root)
        if proof.get('mode')=='original_pdf_image':
            registry=read(track(proof['manual_review_path']))
            row=next(r for r in registry['records']if r['review_id']==proof['manual_review_id'])
            track(row['image_path'])
        proof_cache.add(key)
    manifest_sha=digest(read(base/'execution_manifest.json'));scores=[]
    for job in jobs:
        qid=job['query_id'];manual=records[job['identity']];reference=byid[job['identity']];catalog=catalogs[qid]
        expected=str((base/'source_review'/(qid+'.json')).relative_to(root))
        if any(row.get(k)!=job[k]for row in (manual,reference)for k in ('query_id','profile','mode','repeat')):
            raise ValueError('Manual/index row metadata differs from its actual job')
        if (reference['manual_file']!=expected or manual['query_id']!=qid
                or manual['question_sha256']!=digest(job['question'])or manual['manifest_sha256']!=manifest_sha
                or catalog['question']!=job['question']or catalog['source_catalog_sha256']!=digest(catalog['source_card_catalog'])):
            raise ValueError('Manual question/catalog/manifest ownership mismatch')
        require_record(reference,manual,digest(job['result']))
        retrieval=track(manual['retrieval_file'])
        if (retrieval!=base/'retrievals'/(job['identity']+'.json')or
                hashlib.sha256(retrieval.read_bytes()).hexdigest()!=manual['retrieval_file_raw_sha256']or
                read(retrieval)['result']!=job['result']):
            raise ValueError('Raw retrieval differs from its bound manual record')
        for card in manual['card_reviews']:
            basis=catalog['source_card_catalog'][card['card_key']];facts={f['condition_id']:f for f in basis['facts']}
            if card['source_pdf_sha256']!=basis['source_pdf_sha256']or card['relevant']is not basis['relevant']:
                raise ValueError('Source relevance changes between comparison arms')
            if set(facts)!={f['condition_id']for f in card['facts']}:raise ValueError('Material source condition changed')
            text='\n'.join(e['text']for e in job['result']['evidence']if e['card_key']==card['card_key'])
            for fact in card['facts']:
                if any(fact.get(k)!=v for k,v in facts[fact['condition_id']].items()):raise ValueError('Material source definition changed')
                if any(not q or q not in text for q in fact.get('input_quotes',[])):raise ValueError('Quote absent from actual input')
                for proof in fact.get('source_support',[]):validate_proof(proof)
        group=index['group_by_query'][qid]
        scores.append(evaluator.score_retrieval(job,manual,group,validate_source=validate_proof,source_pdf_sha_by_card=owners))
    evaluation=read(base/'evaluation.json')
    if digest(sorted(scores,key=lambda r:r['identity']))!=digest(sorted(evaluation['records'],key=lambda r:r['identity'])):
        raise ValueError('Stored evidence evaluation differs from actual source judgments')
    summary=evaluator.aggregate(scores,group_by_query=index['group_by_query'],profiles=stage.C.PROFILES,modes=stage.C.MODES)
    if summary!=evaluation['summary']:raise ValueError('Evidence summary differs from complete matrix')
    for name in ['jobs.json','source_judgments.json','evaluation.json','execution_manifest.json','main_source_validation.json']:
        track(str((base/name).relative_to(root)))
    for name in ['61_evaluate_evidence.py','62_evaluate_answers.py','62_check_source_acceptance.py']:
        track(str((Path(__file__).parent/name).relative_to(root)))
    files={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()for path in sorted(paths)}
    notebook_json=read(notebook);code=[c['source']for c in notebook_json['cells']if c['cell_type']=='code']
    return dict(files_sha256=files,notebook_code_sha256=digest(code),logical_rows=len(jobs),source_proofs=len(proof_cache))


def check(stage, notebook, *, require_accepted=True):
    completion=stage.read(stage.B61/'completion.json')
    if require_accepted:require_status(completion['status'])
    stage.guard59();stage.R.guard_files(completion['files_sha256'])
    dependencies=collect_dependencies(stage,notebook)
    require_coverage(completion['files_sha256'],dependencies['files_sha256'])
    if completion['next_notebook_code_sha256']!=dependencies['notebook_code_sha256']:
        raise ValueError('Paid notebook code changed after source acceptance')
    return dependencies


def prepare(stage, notebook):
    check(stage,notebook)
    return stage.prepare62(notebook)


def run(stage, notebook, jobs, manifest, *, allow_network=False):
    check(stage,notebook)
    return stage.run62(jobs,manifest,allow_network=allow_network)
