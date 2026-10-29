"""Read-only usage ledger; official-rate calculation is not a billed invoice."""
from collections import Counter
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('cost_accounting_common',Path(__file__).with_name('34_followup_common.py'))
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)
RATES={'model':'gpt-6-luna','currency':'USD','processing':'Standard','verified_date':'2026-10-11',
    'per_million':{'ordinary_input':'0.10','cached_input':'0.01','cache_write':'0.125','output':'0.50','embedding':'0.02'},
    'long_context_threshold':272000,'sources':{
        'model':'https://developers.openai.com/api/docs/models/gpt-6-luna',
        'cache':'https://developers.openai.com/api/docs/guides/prompt-caching',
        'embedding':'https://developers.openai.com/api/docs/models/text-embedding-3-small',
        'billing':'https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs'}}


def money(value):return format(value,'f')


def cost_from_usage(usage,model,tier='default'):
    unknown={'cost_usd':None,'reason':'Provider usage or required cache breakdown unavailable'}
    if not usage:return unknown
    if model=='text-embedding-3-small':
        tokens=usage.get('prompt_tokens')
        if tokens is None:return unknown
        if not isinstance(tokens,int) or isinstance(tokens,bool) or tokens<0:raise ValueError('Invalid embedding token count')
        return {'cost_usd':money(Decimal(tokens)*Decimal('0.02')/1000000),'embedding_tokens':tokens}
    if model!='gpt-6-luna' or tier!='default':
        return {'cost_usd':None,'reason':'Model or processing tier outside verified rates'}
    details=usage.get('input_tokens_details') or {}
    if not all(k in usage for k in ['input_tokens','output_tokens']) or not all(k in details for k in ['cached_tokens','cache_write_tokens']):
        return unknown
    inp,out=usage['input_tokens'],usage['output_tokens'];cached,written=details['cached_tokens'],details['cache_write_tokens']
    if any(not isinstance(n,int) or isinstance(n,bool) or n<0 for n in [inp,out,cached,written]) or cached+written>inp:
        raise ValueError('Invalid input token partition')
    if 'total_tokens' in usage and usage['total_tokens']!=inp+out:raise ValueError('Invalid total token count')
    ordinary=inp-cached-written;long=inp>RATES['long_context_threshold']
    multiplier=Decimal(2 if long else 1);output_multiplier=Decimal('1.5' if long else '1')
    parts={'ordinary_input_usd':Decimal(ordinary)*Decimal('0.10')*multiplier/1000000,
        'cached_input_usd':Decimal(cached)*Decimal('0.01')*multiplier/1000000,
        'cache_write_usd':Decimal(written)*Decimal('0.125')*multiplier/1000000,
        'output_usd':Decimal(out)*Decimal('0.50')*output_multiplier/1000000}
    return {'cost_usd':money(sum(parts.values())),**{k:money(v) for k,v in parts.items()},
        'input_tokens':inp,'output_tokens':out,'ordinary_input_tokens':ordinary,
        'cached_input_tokens':cached,'cache_write_tokens':written,'long_context':long}


def price_records(records):
    seen={};rows=[]
    for stage,path,record in records:
        raw=record.get('raw_response') or {};request=record.get('request') or {}
        embedding=Path(path).name.startswith('embedding_batch_')
        model='text-embedding-3-small' if embedding else request.get('model')
        tier=raw.get('service_tier','default')
        price=cost_from_usage(record.get('usage') or {},model,tier)
        row={'stage':stage,'path':path,'status':record['status'],'model':model,'service_tier':tier,
            'embedding_model_from_verified_runner':embedding,'record_sha256':F.digest(record),
            'saved_response_duplicate':False,**price}
        response_id=raw.get('id')
        if response_id and response_id in seen:
            prior=seen[response_id]
            if prior['record_usage_sha256']!=F.digest(record.get('usage') or {}):raise ValueError('Conflicting duplicate response usage')
            row.update(saved_response_duplicate=True,duplicate_of=prior['path'],cost_usd='0')
        elif response_id:
            seen[response_id]={'path':path,'record_usage_sha256':F.digest(record.get('usage') or {})}
        rows.append(row)
    return rows


def aggregate(rows):
    known=[r for r in rows if r['cost_usd'] is not None and not r['saved_response_duplicate']]
    sums=Counter()
    for r in known:
        for k in ['input_tokens','output_tokens','ordinary_input_tokens','cached_input_tokens','cache_write_tokens','embedding_tokens']:
            sums[k]+=r.get(k,0)
    return {'record_count':len(rows),'known_priced_records':len(known),
        'unknown_cost_records':sum(r['cost_usd'] is None for r in rows),
        'duplicate_saved_responses':sum(r['saved_response_duplicate'] for r in rows),
        'known_cost_usd':money(sum((Decimal(r['cost_usd']) for r in known),Decimal(0))),
        'tokens':dict(sums),'billed_cost_usd':None}


def build(stage='36'):
    if stage not in ['35','36']:raise ValueError('Unknown cost ledger stage')
    output_stage=int(stage)
    paths={'30-33':F.OLD[30]/'api_cache','35':F.STAGES[35]/'api_cache','36':F.STAGES[36]/'api_cache'}
    if stage=='35':paths={'35':paths['35']}
    runner=F.ROOT/'notebooks/scripts/30_structured_search_common.py'
    if stage=='36' and "EMBEDDING_MODEL='text-embedding-3-small'" not in runner.read_text().replace(' ',''):
        raise RuntimeError('Embedding model no longer matches verified runner')
    records=[];raw_by_key={}
    for cache_stage,base in paths.items():
        for path in sorted(base.glob('*.json')):
            record=F.read_json(path)
            if record.get('request',{}).get('instructions')=='test':continue
            if record['status']=='started':raise RuntimeError('Unfinished API request in cost ledger')
            if record.get('raw_response') and record['raw_response'].get('usage')!=record.get('usage'):
                raise RuntimeError('Raw response usage differs from saved accounting usage')
            records.append((cache_stage,str(path.relative_to(F.ROOT)),record))
            raw_by_key[(cache_stage,path.stem)]=record
    rows=price_records(records)
    per_stage={stage:aggregate([r for r in rows if r['stage']==stage]) for stage in paths}
    primary=F.read_json(F.STAGES[36]/'primary_results.json') if stage=='36' else {'answers':[]}
    answer_costs=[]
    for profile in ['card_page_section_benefit','parent_child_bundle']:
        for mode in ['U','S_THEN_U','S_PLUS_U']:
            answers=[a for a in primary['answers'] if a['profile']==profile and a['mode']==mode]
            if not answers:continue
            costs=[];unknown=0
            for answer in answers:
                for event in answer['events']:
                    key=event['key'];source_stage='35' if ('35',key) in raw_by_key else '30-33'
                    record=raw_by_key.get((source_stage,key))
                    if record is None:raise RuntimeError('Answer cost provenance missing')
                    if (record.get('usage') or {})!=event.get('usage',{}):raise RuntimeError('Answer event usage differs from source')
                    cost=cost_from_usage(record.get('usage') or {},record.get('request',{}).get('model'),
                        (record.get('raw_response') or {}).get('service_tier','default'))['cost_usd']
                    if cost is None:unknown+=1
                    else:costs.append(Decimal(cost))
            subtotal=sum(costs,Decimal(0))
            answer_costs.append({'profile':profile,'mode':mode,'questions':len(answers),
                'logical_attempts':sum(len(a['events']) for a in answers),'unknown_usage_attempts':unknown,
                'record_replay_cost_usd':money(subtotal),'per_answer_usd':money(subtotal/len(answers))})
    output={'rates':RATES,'stages':per_stage,'ledger_stage':stage,'ledger_total':aggregate(rows),
        'entries':rows,'answer_stage_only':answer_costs,
        'pricing_method':'(input - cached - writes)*input_rate + cached*cached_rate + writes*write_rate + output*output_rate; all rates per1M. Reasoning included in output, never added twice.',
        'cost_kind':'Usage-based current official-rate calculation; NOT provider billed measurement.',
        'billing_unavailable_reason':'No administrator credential configured when inspected; organization Costs API not called.',
        'scope':('Only35 newly saved requests; does not read or write36 outputs. ' if stage=='35' else
            'Current top-level30 shared cache plus35/36 newly saved requests. ')+
            'Historical failed requests without usage, uncached/overwritten attempts, prior OCR, Codex conversation, CPU/electricity, tax/credits/regional premiums and FX excluded.',
        'serving_cost_limit':'Answer-stage replay preserves observed cache mix; not cold-cache or whole-serving cost. Local retrieval/embedding/query-planner costs excluded from per-answer table.',
        'embedding_runner_sha256':F.digest(runner.read_bytes()) if stage=='36' else None,
        'new_generation_api_requests_for_accounting':0}
    if stage=='36':
        output.update(followup_34_36=aggregate([r for r in rows if r['stage'] in ['35','36']]),
            all_recorded_30_36=aggregate(rows),stage34_new_api_cost_usd='0')
    F.write_json(F.STAGES[output_stage]/'cost_accounting.json',output)
    return output


def section(data,stage,table):
    stages=data['stages'];rows=[]
    selected=[('35','35번')] if stage=='35' else [('30-33','30~33 공유 기록'),('35','35번'),('36','36번'),('followup','34~36 신규'),('all','현재 확인 가능한 누적 소계')]
    for key,label in selected:
        item=data['followup_34_36'] if key=='followup' else data['all_recorded_30_36'] if key=='all' else stages[key]
        rows.append([label,item['record_count'],item['known_priced_records'],item['unknown_cost_records'],
            '$'+format(Decimal(item['known_cost_usd']),'.6f'),'미측정'])
    body='<h2>API 사용량 기준 비용</h2>'+table(['범위','현재 요청 기록','요금 계산 가능','비용 미확인','요금 환산액·알려진 소계(USD)','실제 청구액'],rows)
    body+='<p class="guide">Usage-based cost / 사용량 기준 요금 환산액은 실제 API 사용 토큰에 공식 단가를 적용한 계산값으로 실제 청구액과 구분합니다. 분모는 해당 범위의 저장 요청 기록이며 성공·실패 모두 확인하고 사용량이 없으면0원으로 바꾸지 않습니다. 동일한 응답 저장본은 중복 청구로 세지 않고 같은 payload의 실패 후 새 호출은 별도 요청으로 셉니다.34번은 신규API0회이며 CPU·전력 비용은 미측정입니다. 예를 들어 같은 카드 근거를 재사용한 입력 캐시는 일반 입력보다 저렴하지만 캐시 쓰기는 별도 단가로 계산합니다. 기록에 없는 실패·세금·할인·환율·기존OCR·Codex 대화 비용은 포함하지 않습니다.</p>'
    body+='<p>2026-10-11 확인한 Standard / 일반 처리·100만 토큰당 단가는 gpt-6-luna 일반 입력$0.10, 캐시 읽기$0.01, 캐시 쓰기$0.125, 출력$0.50입니다. text-embedding-3-small은 입력$0.02입니다. 입력에서 캐시 읽기·쓰기를 빼 일반 입력을 계산하고 추론 토큰은 이미 출력에 포함돼 다시 더하지 않습니다. 현재 LLM 원래 응답은 default / 일반 처리이며 긴 입력272,000토큰 초과 요청은 없습니다. 조직 청구 조회용 관리자 인증이 없어 실제 청구액은 미측정입니다. '
    body+=f'<a href="{RATES["sources"]["model"]}">OpenAI 공식 모델 요금</a> · <a href="{RATES["sources"]["cache"]}">캐시 요금 계산 안내</a> · <a href="{RATES["sources"]["embedding"]}">임베딩 단가</a>.</p>'
    if stage=='36':
        labels={'card_page_section_benefit':'benefit / 혜택 청킹','parent_child_bundle':'parent-child / 관계 묶음',
            'U':'벡터+BM25','S_THEN_U':'구조화→비구조화','S_PLUS_U':'두 결과 융합'}
        body+='<h3>선택한6조건의 답변 단계 비용 진단</h3>'+table(['프로필','검색','답변 질문','논리 시도','사용량 미확인','33질문 답변 요금 재계산','질문당 답변 요금'],[
            [labels[r['profile']],labels[r['mode']],r['questions'],r['logical_attempts'],r['unknown_usage_attempts'],
             '$'+format(Decimal(r['record_replay_cost_usd']),'.6f'),'$'+format(Decimal(r['per_answer_usd']),'.6f')] for r in data['answer_stage_only']])
        body+='<p class="guide">분모는 프로필·검색마다33답변입니다. 같은 요청을 여러 조건에서 사용한 횟수만큼 재계산한 논리 비용이며 위의 실제 신규 요청 소계에 다시 더하지 않습니다. 실제 관측된 입력 캐시 비율이 섞여 있어 운영의 cold cache / 처음 쓰는 입력이나 전체 요청 요금이 아닙니다. 질문 조건 추출·질문 임베딩·로컬 검색/재정렬·CPU 비용을 제외한 답변 단계만 비교합니다. 낮은 금액만으로 품질이 낮은 설정을 선택하지 않습니다.</p>'
    return body


if __name__=='__main__':
    F.initialize(36)
    data=build()
    print(F.canonical({k:data[k] for k in ['stages','followup_34_36','all_recorded_30_36','answer_stage_only']}))
