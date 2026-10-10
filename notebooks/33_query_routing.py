"""GPT-6 Luna medium query plans and held-configuration confirmation."""
from pathlib import Path
import importlib.util
import sys
import time

spec=importlib.util.spec_from_file_location('search_experiment',Path(__file__).with_name('32_search_experiment.py'))
E=importlib.util.module_from_spec(spec);sys.modules[spec.name]=E;spec.loader.exec_module(E);C=E.C

INSTRUCTIONS='''카드 혜택 질문을 검색 조건으로 해석하세요. 원문에 없는 숫자 조건을 만들지 마세요.
groups는 OR 그룹 목록이며 각 그룹 안 requirement들은 한 카드가 모두 만족해야 하는 AND입니다.
한 requirement의 대상·혜택종류·비율은 같은 혜택을 설명합니다. 여러 업종의 혜택은 별도 requirement로 만드세요.
targets는 같은 requirement 내부 OR 대상입니다. 금액은 원, min_percent는 퍼센트값(10%는 10)입니다.
명시적 요구가 없는 수치·실적·한도는 null로 둡니다. '실적 기준 30만원 이하'는 max_spend_krw=300000입니다.
한도에 월/매월이 명시되면 cap_period=month, 일/매일은 day, 기간 지정이 없으면 any입니다. 한도 요청이 없으면 any입니다.
지원하지 않는 조건이나 기간은 unresolved에 원문과 사유를 기록하세요.
구조화필드로 표현할 수 있으면 structured_then_unstructured, 포괄적 취향/선택 조언이면 unstructured입니다.
'여행 혜택이 있는 카드'는 여행category로 구조화가능합니다. '여행할 때 유용한 카드 추천'도 여행category를 사용할 수 있지만
구체적 숫자조건은 만들지 마세요. '유용한'을 최저연회비/최대할인 등으로 임의해석하지 마세요.
여행category는 여행/항공/호텔/숙박/공항/라운지/면세점으로 고정합니다.
커피category는 커피/카페/스타벅스/투썸/이디야/폴바셋/커피빈입니다.
카드명으로 대상을 명확히 지정하면 아래 catalog의 정확한 key를 card_keys에 사용하세요. 임의key를 만들지 마세요.
discount는 할인, earn은 적립/캐시백, any는 혜택종류 제한없음입니다.
지원하지 않는 사용자 예산·최적화 요청은 그 상태를 unresolved에 남겨 비구조화 검색으로 보완합니다.
'''

def parse_questions():
    freeze=E.freeze_dataset();api=C.CachedAPI();records=[]
    instructions=INSTRUCTIONS+'\n카드 catalog:\n'+C.canonical(freeze['catalog'])
    contract={'instructions':instructions,'schema':E.QueryPlan.model_json_schema(),
                    'model':C.MODEL,'reasoning':C.REASONING,'dataset_sha256':freeze['sha256']}
    contract['sha256']=C.digest(contract);C.write_json(C.STAGES[33]/'parser_contract.json',contract)
    for case in freeze['queries']:
        path=C.STAGES[33]/'plans'/f"{case['query_id']}.json"
        binding=C.digest({'contract':contract['sha256'],'question':case['question']})
        if path.exists():
            record=C.read_json(path)
            if record.get('input_fingerprint')!=binding:raise RuntimeError('Stale parser cache: '+case['query_id'])
            records.append(record);continue
        t=time.perf_counter()
        try:
            response=api.parse(model=C.MODEL,instructions=instructions,input=[{'role':'user','content':case['question']}],
                               text_format=E.QueryPlan,tools=[],store=False,max_output_tokens=3000)
            plan=response.output_parsed.model_dump(mode='json')
            if not set(plan['card_keys'])<=set(freeze['catalog']):raise ValueError('Unknown card scope')
            expected=case['plan']
            actual=E.normalized_plan(plan);gold=E.normalized_plan(expected)
            field_errors={}
            for field in ['targets','action','min_percent','max_spend_krw','min_cap_krw','cap_period','exclude_targets']:
                a=sorted(C.canonical(r[field]) for g in actual['groups'] for r in g)
                b=sorted(C.canonical(r[field]) for g in gold['groups'] for r in g)
                field_errors[field]=int(a!=b)
            facts=E.DB.load_facts(C.STAGES[30])
            selected=E.DB.select_cards(facts,plan);oracle_selected=E.DB.select_cards(facts,expected)
            metrics={'route_match':int(plan['route']==expected['route']),
                     'condition_exact_match':int(actual['groups']==gold['groups']),
                     'card_scope_match':int(set(plan['card_keys'])==set(expected['card_keys'])),
                     'unresolved':int(bool(plan['unresolved'])),
                     'candidate_set_match':int(set(selected['eligible_cards'])==set(oracle_selected['eligible_cards'])),
                     'boolean_group_match':int(sorted(map(len,plan['groups']))==sorted(map(len,expected['groups']))),
                     'field_errors':field_errors}
            record={'query_id':case['query_id'],'status':'ok','plan':plan,'oracle_plan':expected,'metrics':metrics,
                    'wall_seconds':time.perf_counter()-t}
        except Exception as exc:
            record={'query_id':case['query_id'],'status':'failed','error':f'{type(exc).__name__}: {exc}',
                    'wall_seconds':time.perf_counter()-t}
        record['input_fingerprint']=binding;C.write_json(path,record);records.append(record);print('parser',case['query_id'],record['status'],flush=True)
        C.write_json(C.STAGES[33]/'parser_progress.json',{'completed':len(records),'expected':len(freeze['queries'])})
    C.write_json(C.STAGES[33]/'parser_results.json',{'records':records,'model':C.MODEL,'reasoning':C.REASONING})
    return records

if __name__=='__main__':parse_questions()
