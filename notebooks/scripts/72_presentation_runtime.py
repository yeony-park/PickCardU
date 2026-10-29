"""Resumable72 request journal; notebook owns review and the final HTML.

Reuse the bounded raw-before-parse client, pricing and citation restoration.
Only this experiment's serialized input is replaced; operating files are read-only.
"""
import copy
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import sqlite3
import sys
import time


def module(name,filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename))
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value)
    return value


F=module('format72','72_input_presentation.py')
A=module('answer72','68_69_answer_runtime.py');T=A.T
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'notebooks/data/72_input_presentation'
PRIOR=ROOT/'notebooks/data/70_cap_context_repair'
QUERIES=('C01','C10','C12','C13','C14','C16','C17','C18')
PROFILES=('card_page_section_benefit','parent_child_bundle')
GUIDE='''
[이번 입력 형식 안내]
cards의 card_ref는 evidence의 card_ref와 같은 카드입니다. C/F/P 식별자는 내부 연결용이며 화면 본문과 추천 이유에는 카드명을 사용하세요. citations에는 e1 같은 evidence_id만 쓰세요.
구조화 사실은 같은 행의 대상·혜택·수치·조건·한도·기간·횟수·제외사항을 묶습니다. 다른 행의 숫자와 조건을 섞지 마세요. 짧은 상태값 known/true는 해당 해석의 저장 상태이고 unknown은 미확인입니다. not_applicable, 빈 값 또는 생략된 필드는 그 행에 기록되지 않았다는 뜻이며 무실적·무제한을 뜻하지 않습니다.
한도기간 상태는 추출된 기간 해석의 상태일 뿐 지급한도의 주기를 확정하지 않습니다. 기간이 실적 관찰기간인지, 이용기간인지, 지급한도 주기인지는 원문과 같은 카드의 본문으로 확인하세요. 적용 관계가 불명확하면 단정하지 마세요.
원질문의 직접 요구를 먼저 답하고, 묻지 않은 조건을 모두 나열할 필요는 없습니다. 추가로 말하는 사실도 반드시 근거가 뒷받침해야 합니다.
'''


for stage,leaf in [(70,'70_cap_context_repair'),(72,'72_input_presentation')]:
    T.LEAVES[stage]=leaf;T.R.P.STAGES[stage]=T.R.X.STAGES[stage]=leaf
    for shared in T.stage_registries():shared.STAGES[stage]=ROOT/'notebooks/data'/leaf
    T.E.STAGE_FOLDERS[stage]=leaf


def read(path):return json.loads(Path(path).read_text())
def write(path,value):T.write(path,value)
def sha(value):return T.R.P.sha(value)
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def operating_prompts(question,evidence):
    from pickcardu_rag.answering import OpenAIService
    class Capture:
        def __init__(self):self.responses=self;self.requests=[]
        def parse(self,**kwargs):
            self.requests.append(kwargs);raise ValueError('offline prompt capture')
    client=Capture()
    try:OpenAIService(api_key=None,client=client,llm_model='gpt-6-luna',answer_payload_bytes=64000).answer(question,evidence)
    except Exception:pass
    if len(client.requests)!=2:raise RuntimeError('Operating technical retry contract changed')
    return [r['instructions']+GUIDE for r in client.requests]


def prepare():
    from pickcardu_rag.answering import _generated_answer_model
    if (BASE/'cost_reservation.json').exists():
        manifest=read(BASE/'execution_manifest.json');T.guard_manifest(manifest)
        reservation=read(BASE/'cost_reservation.json')
        if (BASE/'pre_paid_acceptance.json').exists():
            T.E.verify_file_map(read(BASE/'pre_paid_acceptance.json')['files_sha256'],ROOT)
        return dict(inputs=48,jobs=144,maximum_new_usd=reservation['maximum_new_usd'],
                    ledger_at_reservation=reservation['starting_budget_debit_usd'],reused_frozen_preparation=True)
    snapshots=[r for r in read(PRIOR/'jobs.json')['records'] if r['mode']=='SEQ_REPAIRED' and r['repeat']==2 and r['query_id'] in QUERIES]
    assert len(snapshots)==16
    jobs=[];checks=[];payloads={};sidecars={};prompts=None
    con=sqlite3.connect((PRIOR/'db_v5/structured.sqlite').resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    con.row_factory=sqlite3.Row
    try:
        for qid in QUERIES:
            for profile in PROFILES:
                src=next(r for r in snapshots if r['query_id']==qid and r['profile']==profile)
                evidence=src['result']['evidence'];records=[json.loads(e['text'])[0] for e in evidence if e.get('origin')=='structured']
                assert records
                prompts=prompts or operating_prompts(src['question'],evidence)
                for record in records:
                    sql=con.execute('SELECT * FROM facts WHERE fact_id=?',(record['fact_id'],)).fetchone()
                    if sql is None or sql['document_id']!=record['card_key']:raise ValueError('SQL owner missing')
                    F.assert_sql_fields(record,{k:sql[k+'_raw'] for k in F.FIELDS})
                for arm in 'ABC':
                    payload,reverse=F.build_payload(src['question'],src['result']['cards'],evidence,records,arm)
                    payload_key=f'{qid}:{profile}:{arm}';payloads[payload_key]=payload
                    sidecars[payload_key]=dict(reverse=reverse,canonical_records=records,source_job=src['identity'],source_job_sha256=sha(src))
                    checks.append(dict(payload_key=payload_key,original_evidence=len(evidence)-len(records),structured_facts=len(records),
                        bytes=len(F.compact(payload).encode()),sql_raw_fields_exact=True,business_projection_sha256=sha([F.project(r) for r in records]),
                        U_exact=True,source_job_sha256=sha(src)))
        for repeat in (1,2,3):
            order=['ABC','BCA','CAB'][repeat-1]
            for qid in QUERIES:
                for profile in PROFILES:
                    src=next(r for r in snapshots if r['query_id']==qid and r['profile']==profile)
                    for arm in order:
                        jobs.append(dict(identity=f'{qid}:{profile}:{arm}:r{repeat}',query_id=qid,profile=profile,arm=arm,repeat=repeat,
                            question=src['question'],payload_key=f'{qid}:{profile}:{arm}',evidence=src['result']['evidence']))
    finally:con.close()
    source_paths=['notebooks/scripts/72_input_presentation.py','notebooks/scripts/72_presentation_runtime.py',
                  'packages/rag-core/src/pickcardu_rag/answering.py','notebooks/scripts/68_69_answer_runtime.py',
                  'notebooks/scripts/67_69_execution_runtime.py','notebooks/scripts/35_answer_experiment.py',
                  'notebooks/scripts/36_cost_accounting.py']
    critical=['jobs.json','source_review.json','pre_answer_acceptance.json','db_v5/structured.sqlite','db_v5/facts.jsonl']
    manifest=dict(stage=72,logical_answers=144,maximum_answer_requests=288,maximum_physical_requests=288,
        maximum_parser_requests=0,maximum_embedding_requests=0,stage_cost_cap_usd='5.429902040',model='gpt-6-luna',reasoning='medium',
        source_sha256={p:digest(ROOT/p) for p in source_paths},
        critical_data_sha256={str((PRIOR/p).relative_to(ROOT)):digest(PRIOR/p) for p in critical},
        allowed_identities=dict(answer=[j['identity'] for j in jobs]),
        prompt_variant_by_identity={j['identity']:'old' for j in jobs},
        answer_instructions_sha256_by_identity={j['identity']:[sha(p) for p in prompts] for j in jobs},
        questions=list(QUERIES),fixed_search_repeat=2,generation_repeats=3,profile_retuning=False,
        new_parser_requests=0,new_embedding_requests=0,new_retrievals=0,
        limits=dict(output_tokens=2400,physical_per_logical=2,sdk_retries=0),
        evaluation_policy='66 visible direct request plus source accuracy; unasked omission not failure',
        candidate_rule='no question PASS loss, no accuracy/direct regression or new confirmed error category; no alias leakage; clean7 direction stable; PASS then tokens then mean latency then B',
        development_only=True,known_source_contamination='C18 Monthlyreward 150만원 PDF versus 50만원 OCR; primary all8 plus clean7 sensitivity')
    contract=dict(execution_manifest_sha256=sha(manifest),
        input_sha256_by_identity={j['identity']:sha([dict(role='user',content=F.compact(payloads[j['payload_key']]))]) for j in jobs},
        schema_sha256_by_identity={j['identity']:sha(_generated_answer_model(len(j['evidence'])).model_json_schema()) for j in jobs})
    for name,value in [('payloads.json',payloads),('audit_sidecars.json',sidecars),('jobs.json',dict(records=jobs)),
                       ('offline_field_verification.json',dict(records=checks)),('prompts.json',prompts),
                       ('execution_manifest.json',manifest),('answer_payload_contract.json',contract)]:
        T.R.X.frozen(BASE/name,value)
    ledger=T.budget_ledger();assert ledger['unknown_cost_records']==0
    api=Cache(72,'answer',jobs[0]['identity'],allow_network=False)
    reservations=[]
    for job in jobs:
        schema=_generated_answer_model(len(job['evidence']))
        wire=dict(model='gpt-6-luna',reasoning={'effort':'medium'},input=[dict(role='user',content=F.compact(payloads[job['payload_key']]))],
                  instructions=prompts[1],schema=schema.model_json_schema(),max_output_tokens=2400,tools=[],store=False)
        count=len(F.compact(wire).encode())+8192
        upper=(Decimal(count)*Decimal('.25' if count>272000 else '.125')+Decimal(2400)*Decimal('.75' if count>272000 else '.50'))/1000000
        reservations.append(dict(identity=job['identity'],per_physical_upper_usd=str(upper)))
    total=sum((Decimal(r['per_physical_upper_usd'])*2 for r in reservations),Decimal(0))
    if Decimal(ledger['budget_debit_usd'])+total>Decimal(8):raise RuntimeError('Whole288 request reservation exceeds8 USD; do not start')
    T.R.X.frozen(BASE/'cost_reservation.json',dict(starting_budget_debit_usd=ledger['budget_debit_usd'],
        maximum_new_usd=str(total),maximum_cumulative_usd=str(Decimal(ledger['budget_debit_usd'])+total),
        logical_answers=144,maximum_physical_requests=288,records=reservations,pricing=T.R.P.PRICE.RATES,
        method='UTF8 serialized full request bytes plus8192 protocol/schema overhead, max output, both attempts; historical frozen unit rates, not invoice'))
    return dict(inputs=len(payloads),jobs=len(jobs),maximum_new_usd=str(total),ledger=ledger['budget_debit_usd'])


class Cache(A.Cache):
    def normalized(self,kwargs):
        result=super().normalized(kwargs)
        payloads=read(BASE/'payloads.json');key=self.identity.rsplit(':r',1)[0]
        result['input']=[dict(role='user',content=F.compact(payloads[key]))]
        if not result['instructions'].endswith(GUIDE):result['instructions']+=GUIDE
        return result

    def reserve(self,normalized):
        gate=read(BASE/'pre_paid_acceptance.json')
        T.E.verify_file_map(gate['files_sha256'],ROOT)
        if gate['status']!='accepted' or not gate['relation_review_accepted'] or not gate['independent_code_review_accepted']:
            raise RuntimeError('72 source/code gate not accepted')
        return super().reserve(normalized)


def validate_saved(row,job,manifest,contract):
    from pickcardu_rag.answering import _generated_answer_model,_restore_generated_answer
    from pickcardu_rag.errors import LlmUngrounded
    path=BASE/'answers'/(job['identity']+'.seal.json')
    if row['job_sha256']!=sha(job) or row['manifest_sha256']!=sha(manifest) or read(path)['row_sha256']!=sha(row):
        raise RuntimeError('Checkpoint binding/seal changed')
    if row['payload_sha256']!=contract['input_sha256_by_identity'][job['identity']] or not 1<=len(row['events'])<=2:
        raise RuntimeError('Checkpoint payload or physical attempts changed')
    schema=_generated_answer_model(len(job['evidence']));last=None
    api=Cache(72,'answer',job['identity'],allow_network=False)
    for index,event in enumerate(row['events']):
        raw=read(BASE/'api_cache'/(event['key']+'.json'));request=raw['request'];last=raw
        if (sha(request['input'])!=row['payload_sha256'] or sha(request['schema'])!=contract['schema_sha256_by_identity'][job['identity']]
                or sha(request['instructions'])!=manifest['answer_instructions_sha256_by_identity'][job['identity']][index]
                or request['model']!='gpt-6-luna' or request['reasoning']!={'effort':'medium'} or request['max_output_tokens']!=2400
                or raw.get('experiment_identity')!=job['identity'] or raw.get('usage')!=(raw.get('raw_response') or {}).get('usage',{})):
            raise RuntimeError('Saved actual provider request or usage changed')
        kwargs={k:v for k,v in request.items() if k!='schema'};kwargs['text_format']=schema
        if api.key(kwargs)!=event['key']:raise RuntimeError('Saved provider cache key changed')
    restored=None
    if last['status']=='ok':
        try:restored=_restore_generated_answer(schema.model_validate(last['parsed']),job['evidence']).model_dump(mode='json')
        except (ValueError,LlmUngrounded):pass
    if (row['status']=='ok' and restored!=row['answer']) or (row['status']=='failed' and restored is not None):
        raise RuntimeError('Saved answer differs from operating restoration')
    return True


def generate(job,allow_network=False):
    from pickcardu_rag.answering import OpenAIService,_generated_answer_model,_restore_generated_answer
    target=BASE/'answers'/(job['identity']+'.json');manifest=read(BASE/'execution_manifest.json');T.guard_manifest(manifest)
    contract=read(BASE/'answer_payload_contract.json');own=A.identity_contract(contract,job['identity'])
    T.R.X.frozen(BASE/'payload_contracts'/(job['identity']+'.json'),own)
    if target.exists():
        saved=read(target)
        validate_saved(saved,job,manifest,contract)
        if T.budget_ledger()['unknown_cost_records']:raise RuntimeError('Unknown usage still unresolved; do not continue')
        return saved
    api=Cache(72,'answer',job['identity'],allow_network=allow_network);value=error=None
    wall=time.perf_counter();cpu=time.process_time()
    try:
        answer,usage=OpenAIService(api_key=None,client=api,llm_model='gpt-6-luna',answer_payload_bytes=64000).answer(job['question'],job['evidence'])
        value=answer.model_dump(mode='json')
    except Exception as exc:
        if not api.events:raise
        error=f'{type(exc).__name__}: {exc}'
    ready=time.perf_counter()-wall
    row=dict(**{k:job[k] for k in ('identity','query_id','profile','arm','repeat','question')},status='ok' if value else 'failed',
        answer=value,error=error,events=api.events,job_sha256=sha(job),manifest_sha256=sha(manifest),
        payload_sha256=contract['input_sha256_by_identity'][job['identity']],answer_ready_wall_seconds=ready,
        answer_cpu_seconds=time.process_time()-cpu,first_token_seconds=None,browser_display_seconds=None)
    write(target,row);T.R.X.frozen(target.with_suffix('.seal.json'),dict(row_sha256=sha(row)))
    if T.budget_ledger()['unknown_cost_records']:raise RuntimeError('New usage unknown; stop before next call')
    validate_saved(row,job,manifest,contract)
    return row


def run(allow_network=False):
    started=time.perf_counter();cpu=time.process_time();jobs=read(BASE/'jobs.json')['records']
    for i,job in enumerate(jobs,1):
        row=generate(job,allow_network)
        print(json.dumps(dict(progress=i,total=len(jobs),identity=job['identity'],status=row['status'],seconds=round(row['answer_ready_wall_seconds'],2)),ensure_ascii=False),flush=True)
        write(BASE/'progress.json',dict(completed=i,total=len(jobs),latest_identity=job['identity'],ledger=T.budget_ledger()))
    rows=[read(BASE/'answers'/(j['identity']+'.json')) for j in jobs]
    write(BASE/'execution_results.json',dict(records=rows))
    write(BASE/'answer_resources.json',dict(segment_wall_seconds=time.perf_counter()-started,segment_cpu_seconds=time.process_time()-cpu,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,ledger=T.budget_ledger(),logical_answers=len(rows),
        physical_requests=sum(len(r['events']) for r in rows),TTFT=None,browser_display=None,online_full_pipeline=None))


if __name__=='__main__':
    if sys.argv[1:] == ['prepare']:print(prepare())
    elif sys.argv[1:] == ['run','--allow-network']:run(True)
    elif sys.argv[1:] == ['replay']:run(False)
    else:raise SystemExit('Use prepare, replay, or run --allow-network')
