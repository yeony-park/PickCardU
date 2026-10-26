"""Bound fresh answers for68/69, reusing operating generation/restoration.

No HTML or scoring is generated here. The notebook freezes jobs and manifest,
then this helper journals provider requests and validates resumable outputs.
"""
import importlib.util
from pathlib import Path
import sys
import time

spec=importlib.util.spec_from_file_location('execution68_answer',Path(__file__).with_name('67_69_execution_runtime.py'))
T=importlib.util.module_from_spec(spec);sys.modules[spec.name]=T;spec.loader.exec_module(T)


class Cache(T.Cache):
    def deliver(self,stored,schema):
        # A process may have stopped between the durable failed validation and
        # the operating service's second prompt. Replay that failure as the
        # same retriable validation category, never resend its physical call.
        if (stored['status']=='failed' and stored.get('usage')
                and stored.get('error','').startswith(('ValidationError:','ValueError:'))):
            raise ValueError('Saved technical structured-output validation failure')
        return super().deliver(stored,schema)


def payload_contract(jobs,manifest):
    from pickcardu_rag.answering import serialize_answer_payload,_generated_answer_model
    inputs={};schemas={}
    for job in jobs:
        evidence=job['result']['evidence'];identity=job['identity']
        if identity in inputs:raise ValueError('Duplicate logical answer identity')
        inputs[identity]=T.R.P.sha([dict(role='user',content=serialize_answer_payload(job['question'],evidence))])
        schemas[identity]=T.R.P.sha(_generated_answer_model(len(evidence)).model_json_schema())if evidence else None
    if set(inputs)!=set(manifest['allowed_identities']['answer']):raise ValueError('Answer job matrix differs from manifest')
    return dict(execution_manifest_sha256=T.R.P.sha(manifest),input_sha256_by_identity=inputs,schema_sha256_by_identity=schemas)


def identity_contract(contract,identity):
    return dict(execution_manifest_sha256=contract['execution_manifest_sha256'],
        input_sha256_by_identity={identity:contract['input_sha256_by_identity'][identity]},
        schema_sha256_by_identity={identity:contract['schema_sha256_by_identity'][identity]})


def validate_answer(row,job,manifest,contract,base):
    from pickcardu_rag.answering import _generated_answer_model,_restore_generated_answer
    from pickcardu_rag.errors import LlmUngrounded
    identity=job['identity'];evidence=job['result']['evidence']
    expected=dict(identity=identity,manifest_sha256=T.R.P.sha(manifest),payload_contract_sha256=T.R.P.sha(identity_contract(contract,identity)),
        input_sha256=contract['input_sha256_by_identity'][identity],retrieval_sha256=T.R.P.sha(job['result']),
        evidence_sha256=T.R.P.sha(evidence),reuse_source_sha256=None)
    if any(row.get(k)!=v for k,v in expected.items()):raise RuntimeError('Answer checkpoint differs from frozen job')
    if T.R.P.sha(row)!=T.read(base/'answers'/(identity+'.seal.json'))['row_sha256']:
        raise RuntimeError('Answer checkpoint seal changed')
    if job['result'].get('technical_failure'):
        if row['status']!='failed' or row['answer'] is not None or row['events']:
            raise RuntimeError('Retrieval technical failure must remain a failure')
        return
    if not evidence:
        if row['status']!='ok' or row['answer']['answer_status']!='insufficient_evidence':raise RuntimeError('Empty evidence answer changed')
        return
    schema=_generated_answer_model(len(evidence));events=row['events']
    if not 1<=len(events)<=2 or row['request_keys']!=[e['key']for e in events]:raise RuntimeError('Answer physical event matrix changed')
    final=None
    for index,event in enumerate(events):
        raw=T.read(base/'api_cache'/(event['key']+'.json'));request=raw['request'];final=raw
        api=T.Cache(manifest['stage'],'answer',identity,allow_network=False)
        kwargs={k:v for k,v in request.items()if k!='schema'};kwargs['text_format']=schema
        api.check_contract(kwargs)
        allowed=manifest['answer_instructions_sha256_by_identity'][identity]
        if (api.key(kwargs)!=event['key'] or T.R.P.sha(request['instructions'])!=allowed[index]
                or raw.get('experiment_identity')!=identity
                or raw.get('usage')!=(raw.get('raw_response')or{}).get('usage')):
            raise RuntimeError('Answer raw request, prompt order or usage changed')
    restored=None
    if final['status']=='ok':
        try:restored=_restore_generated_answer(schema.model_validate(final['parsed']),evidence).model_dump(mode='json')
        except (ValueError,LlmUngrounded):pass
    if (row['status']=='ok' and restored!=row['answer'])or(row['status']=='failed' and restored is not None):
        raise RuntimeError('Answer differs from restored provider output')


def generate(stage,job,*,allow_network=False):
    from pickcardu_rag.answering import OpenAIService
    base=T.R.P.F.STAGES[stage];manifest=T.read(base/'execution_manifest.json');T.guard_manifest(manifest)
    contract=T.read(base/'answer_payload_contract.json');identity=job['identity'];evidence=job['result']['evidence']
    own_contract=identity_contract(contract,identity)
    T.R.X.frozen(base/'payload_contracts'/(identity+'.json'),own_contract)
    target=base/'answers'/(identity+'.json')
    if target.exists():
        row=T.read(target);validate_answer(row,job,manifest,contract,base)
        if T.budget_ledger()['unknown_cost_records']:raise RuntimeError('Unknown new usage; stop before next call')
        return row
    api=Cache(stage,'answer',identity,allow_network=allow_network)
    started=time.perf_counter();cpu=time.process_time();value=error=None;usage={}
    if job['result'].get('technical_failure'):
        error='Retrieval failed: '+job['result']['technical_failure']
    elif not evidence:
        value=dict(answer_status='insufficient_evidence',answer_text='발급사와 정확한 카드명을 확인해 주세요.'if job['result'].get('clarify')else'검색된 근거가 없습니다.',recommendations=[],claims=[])
    else:
        try:
            parsed,usage=OpenAIService(api_key=None,client=api,llm_model='gpt-6-luna',answer_payload_bytes=64000).answer(job['question'],evidence)
            value=parsed.model_dump(mode='json')
        except Exception as exc:
            if not api.events:raise
            error=f'{type(exc).__name__}: {exc}'
    row=dict(identity=identity,query_id=job['query_id'],profile=job['profile'],mode=job['mode'],repeat=job['repeat'],
        status='ok'if value is not None else'failed',answer=value,usage=usage,error=error,events=api.events,
        request_keys=[e['key']for e in api.events],manifest_sha256=T.R.P.sha(manifest),payload_contract_sha256=T.R.P.sha(own_contract),
        input_sha256=contract['input_sha256_by_identity'][identity],retrieval_sha256=T.R.P.sha(job['result']),
        evidence_sha256=T.R.P.sha(evidence),reuse_source_sha256=None,answer_ready_wall_seconds=time.perf_counter()-started,
        answer_cpu_seconds=time.process_time()-cpu,first_token_seconds=None,browser_display_seconds=None,
        cache_policy='fresh logical response per mode/profile/repeat; exact same identity resumes only')
    T.write(target,row);T.R.X.frozen(target.with_suffix('.seal.json'),dict(row_sha256=T.R.P.sha(row)))
    if T.budget_ledger()['unknown_cost_records']:raise RuntimeError('Unknown new usage; stop before next call')
    validate_answer(row,job,manifest,contract,base)
    return row
