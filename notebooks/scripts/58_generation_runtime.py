"""Resumable58 answer matrix and shared59 prompt guard; no report generation.

The notebooks own questions, source judgements and configuration selection.
This helper binds every paid identity to its evidence, prompt and retry schema
so interruption cannot mix experimental settings or spend twice silently.
"""
from pathlib import Path
from types import SimpleNamespace
import time

import importlib.util
import sys


def load(name, filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


T=load('evidence58','57_sequential_evidence_runtime.py')
R,P,X,Q=T.R,T.P,T.X,T.Q
C=load('contract58','58_generation_contract.py')
RECOVERY=load('recovery58','56_connection_budget_recovery.py')
RECOVERY.install(R)
M=load('answers58_preserved','52_retrieval_modes.py')
M.P,M.Q,M.R=P,Q,X.R
BASE=P.F.STAGES[58]
FIXED_QUERIES=('H05','H08','H11','H13','H19','H21','H23','H24','D36','D38','D39','D40')


class AnswerCache(R.Cache):
    def normalized(self, kwargs):
        value=super().normalized(kwargs)
        variant=self.manifest['prompt_variant_by_identity'][self.identity]
        value['instructions']=C.transform_prompt(value['instructions'],variant)
        return value

    def check_contract(self, kwargs):
        X.guard_manifest(self.manifest)
        R.guard_files(self.manifest['critical_data_sha256'])
        value=self.normalized(kwargs)
        allowed=self.manifest['answer_instructions_sha256_by_identity'][self.identity]
        if P.sha(value['instructions']) not in allowed:
            raise RuntimeError('Answer instructions differ from frozen base/retry contract')
        return value


M.X=SimpleNamespace(Cache=AnswerCache,frozen=X.frozen)


def validate_checkpoint_prompt(row,manifest,by_identity):
    """Operating restoration validates output ownership, this binds its prompt."""
    if row.get('reuse_source_sha256'):
        source_id=C.reuse_identity(row['identity'])
        source=by_identity.get(source_id)
        if source is None or row['reuse_source_sha256']!=P.sha(source):
            raise RuntimeError('Answer reuse lacks its validated same-setting A source')
        validate_checkpoint_prompt(source,manifest,by_identity)
        return
    events=row.get('events',[])
    if not events:return  # Empty evidence is checked by the operating validator.
    if len(events)>2:raise RuntimeError('Answer has more than two physical attempts')
    allowed=manifest['answer_instructions_sha256_by_identity'][row['identity']]
    for index,event in enumerate(events):
        raw=R.read(P.F.STAGES[manifest['stage']]/'api_cache'/(event['key']+'.json'))
        if (raw.get('experiment_identity')!=row['identity']
                or P.sha(raw['request']['instructions'])!=allowed[index]):
            raise RuntimeError('Saved answer prompt differs from ordered base/retry contract')


def payload_contract(identity, question, evidence, manifest):
    from pickcardu_rag.answering import serialize_answer_payload,_generated_answer_model
    return dict(execution_manifest_sha256=P.sha(manifest),
        input_sha256_by_identity={identity:P.sha([dict(role='user',content=serialize_answer_payload(question,evidence))])},
        schema_sha256_by_identity={identity:P.sha(_generated_answer_model(len(evidence)).model_json_schema()) if evidence else None})


def freeze_payloads(stage, jobs, manifest):
    combined=dict(execution_manifest_sha256=P.sha(manifest),input_sha256_by_identity={},schema_sha256_by_identity={})
    for job in jobs:
        contract=payload_contract(job['identity'],job['question'],job['result']['evidence'],manifest)
        X.frozen(P.F.STAGES[stage]/'payload_contracts'/(job['identity']+'.json'),contract)
        for name in ['input_sha256_by_identity','schema_sha256_by_identity']:
            combined[name].update(contract[name])
    X.frozen(P.F.STAGES[stage]/'answer_payload_contract.json',combined)


def validate_source_gate(gate, previous, facts):
    if (gate.get('status')!='completed_after_source_fact_review'
            or gate.get('retrieval_results_sha256')!=P.sha(previous)
            or gate.get('source_facts_sha256')!=P.sha(facts)
            or not gate.get('evaluation_files_sha256')):
        raise RuntimeError('Stage57 source-fact review is not finalized or bound to corrected facts')
    R.guard_files(gate['evaluation_files_sha256'])


def prepare(notebook):
    """Require completed57 and manually verified diagnostic excerpts before API."""
    previous=R.read(T.BASE/'retrieval_results.json')
    gate=R.read(T.BASE/'completion_status.json')
    diagnostic=R.read(BASE/'diagnostic_source_evidence.json')
    queries={q['query_id']:q for q in R.read(T.BASE/'queries.json')['queries']}
    source_base=P.DATA/'55_fact_retrieval_generation_diagnosis'
    facts=R.read(T.BASE/'source_facts.json')
    validate_source_gate(gate,previous,facts)
    reference_paths=[source_base/'adjudications'/(qid+'.json') for qid in FIXED_QUERIES]
    references={qid:C.diagnostic_reference(R.read(path)['records'],{f['fact_id']:f for f in facts['facts']},P.sha)
                for qid,path in zip(FIXED_QUERIES,reference_paths,strict=True)}
    reference_contract=dict(policy='diagnostic-only: fewer uncertain required source facts among two historical SEQ records; benefit on tie; no58 outputs used',
        source_facts_sha256=P.sha(facts),by_query=references)
    X.frozen(BASE/'diagnostic_reference_contract.json',reference_contract)
    required={qid:ref['required_fact_ids'] for qid,ref in references.items()}
    receipt_path=BASE/'diagnostic_source_review.json'
    receipts=R.read(receipt_path)
    evaluator=load('source58','55_fact_evaluation.py')
    C.validate_diagnostic(diagnostic,questions={qid:queries[qid]['question'] for qid in FIXED_QUERIES},
        retrieval_sha=P.sha(previous),source_facts_sha=P.sha(facts),required_ids=required,
        receipts=receipts,reference_contract=reference_contract,sha=P.sha,validate_source=lambda s:evaluator.validate_source_support(s,R.ROOT))
    rows={(r['query_id'],r['profile']):r for r in previous['records']}
    jobs=[]
    for qid in FIXED_QUERIES:
        for profile in X.PROFILES:
            for evidence in ['A','B']:
                for prompt in ['old','new']:
                    for repeat in [1,2]:
                        jobs.append(dict(identity=f'{qid}:{profile}:{evidence}:{prompt}:r{repeat}',query_id=qid,
                            profile=profile,evidence_variant=evidence,prompt_variant=prompt,repeat=repeat,
                            diagnostic=False,question=queries[qid]['question'],result=rows[(qid,profile)][evidence]))
        for prompt in ['old','new']:
            for repeat in [1,2]:
                jobs.append(dict(identity=f'{qid}:source:A:{prompt}:r{repeat}',query_id=qid,profile='source',
                    evidence_variant='A',prompt_variant=prompt,repeat=repeat,diagnostic=True,
                    question=queries[qid]['question'],result=diagnostic['by_query'][qid]['result']))
    if len(jobs)!=240 or len({j['identity'] for j in jobs})!=240:
        raise RuntimeError('Answer matrix differs from approved240 logical identities')
    for job in jobs:
        from pickcardu_rag.answering import measure_answer_payload
        if measure_answer_payload(job['question'],job['result']['evidence'])[0]>64000:
            raise RuntimeError('Frozen answer payload exceeds existing byte limit')
    X.frozen(BASE/'jobs.json',dict(records=jobs))
    old=C.operating_prompts()
    prompt_by_identity={j['identity']:j['prompt_variant'] for j in jobs}
    hashes={j['identity']:[P.sha(C.transform_prompt(prompt,j['prompt_variant'])) for prompt in old] for j in jobs}
    critical={str(p.relative_to(R.ROOT)):P.sha(p.read_bytes()) for p in [
        T.BASE/'retrieval_results.json',T.BASE/'execution_manifest.json',T.BASE/'completion_status.json',
        T.BASE/'queries.json',BASE/'diagnostic_source_evidence.json',BASE/'jobs.json',RECOVERY.POLICY]}
    for path in [receipt_path,BASE/'diagnostic_reference_contract.json',T.BASE/'source_facts.json',
                 T.BASE/'source_correction_contract.json',*reference_paths]:
        critical[str(path.relative_to(R.ROOT))]=P.sha(path.read_bytes())
    for row in diagnostic['by_query'].values():
        for proof in row['source_proofs'].values():
            for source in proof['source_spans']:
                critical[source['path']]=P.sha((R.ROOT/source['path']).read_bytes())
                if source.get('manual_review_path'):
                    path=R.ROOT/source['manual_review_path']
                    critical[source['manual_review_path']]=P.sha(path.read_bytes())
                    reviews=R.read(path)['records']
                    record=next(r for r in reviews if r['review_id']==source['manual_review_id'])
                    critical[record['image_path']]=P.sha((R.ROOT/record['image_path']).read_bytes())
    # Stage57 already binds source indexes, corpus, vectors and its private caches.
    critical.update(R.read(T.BASE/'execution_manifest.json')['critical_data_sha256'])
    critical.update(gate['evaluation_files_sha256'])
    sources=set(R.read(T.BASE/'execution_manifest.json')['source_sha256'])|{
        'notebooks/scripts/58_generation_contract.py','notebooks/scripts/58_generation_runtime.py',
        'notebooks/scripts/56_connection_budget_recovery.py','notebooks/scripts/52_retrieval_modes.py',
        'notebooks/scripts/58_configuration_selection.py'}
    manifest=X.make_manifest(58,notebook,[queries[qid] for qid in FIXED_QUERIES],
        dict(parser=[],answer=[j['identity'] for j in jobs]),maximum_answer=480,stage_cap='1',
        extra=dict(critical_data_sha256=critical,source_sha256={name:P.sha((R.ROOT/name).read_bytes()) for name in sorted(sources)},
            prompt_variant_by_identity=prompt_by_identity,answer_instructions_sha256_by_identity=hashes,
            logical_answers=240,ordinary_answers=192,source_diagnostic_answers=48,
            allowed_evidence_by_profile=gate['allowed_evidence_by_profile'],
            diagnostic_excluded_from_candidate_quality=True,
            identical_input_reuse='B may share A only within the same question/profile/prompt/repeat and exact serialized input/schema/evidence',
            selection_rule='eight global configurations; per-profile no PASS regression or new important errors/condition loss/scope violations; maximize query-macro PASS net gain; ties fewer changed axes, extra searches, input bytes, measured cost, config ID; no gain retains baseline'))
    freeze_payloads(58,jobs,manifest)
    return jobs,manifest


def run(jobs,manifest,*,allow_network=False):
    stage=manifest['stage'];expected={58:240,59:192}.get(stage)
    if expected is None or len(jobs)!=expected or manifest['logical_answers']!=expected:
        raise ValueError('Answer matrix differs from the fixed stage58/59 logical count')
    base=P.F.STAGES[stage]
    X.guard_manifest(manifest);R.guard_files(manifest['critical_data_sha256'])
    records=[];by_identity={};start,cpu=time.perf_counter(),time.process_time()
    for job in jobs:
        prior_id=C.reuse_identity(job['identity']);reuse=None
        if prior_id in by_identity:
            candidate=by_identity[prior_id]
            wanted=payload_contract(job['identity'],job['question'],job['result']['evidence'],manifest)
            if (candidate['evidence_sha256']==P.sha(job['result']['evidence']) and
                    candidate['input_sha256']==wanted['input_sha256_by_identity'][job['identity']]):
                reuse=candidate
        answer=M.answer(stage,job['identity'],job['question'],job['result'],allow_network=allow_network,reused=reuse)
        validate_checkpoint_prompt(answer,manifest,by_identity)
        by_identity[job['identity']]=answer
        records.append(dict(**{k:v for k,v in job.items() if k!='result'},answer=answer))
        R.write(base/'answer_progress.json',dict(completed=len(records),expected=expected,last_identity=job['identity'],
            invocation_wall_seconds=time.perf_counter()-start))
        if len(records)%8==0:print(stage,'answer',len(records),'/',expected,flush=True)
        if X.budget_upper_ledger()['unknown_cost_records']:
            raise RuntimeError('New unknown request usage; remaining answers stopped')
    R.write(base/'answer_results.json',dict(records=records,manifest_sha256=P.sha(manifest)))
    import resource
    resources=dict(logical_answers=len(records),reused_answers=sum(bool(r['answer']['reuse_source_sha256']) for r in records),
        physical_price=R.phase_price([stage]),invocation_wall_seconds=time.perf_counter()-start,
        invocation_cpu_seconds=time.process_time()-cpu,peak_process_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        answer_ready_timing=X.timing_summary([r['answer']['answer_ready_wall_seconds'] for r in records if not r['answer']['reuse_source_sha256']]),
        global_actual=X.ledger(),global_conservative_upper=X.budget_upper_ledger(),ttft_seconds=None,
        browser_display_seconds=None,actual_online_end_to_end_seconds=None,new_embeddings=0,
        status='answers_complete_awaiting_source_fact_and_configuration_review')
    R.write(base/'answer_execution_resources.json',resources)
    return resources
