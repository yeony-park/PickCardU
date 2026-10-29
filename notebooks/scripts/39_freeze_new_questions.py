"""Author reference questions before retrieval; cross-check fact fragments on OCR pages."""
import importlib.util
from pathlib import Path
import re
import sys
import time
from collections import defaultdict
s=importlib.util.spec_from_file_location('freeze_common',Path(__file__).with_name('37_followup_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)
M=C.load('frozen_rubric','39_answer_rubric.py')

def req(target,action='any',rate=None,spend=None,cap=None,period='any'):
    return {'targets':[target],'action':action,'min_percent':rate,'max_spend_krw':spend,'min_cap_krw':cap,'cap_period':period,'exclude_targets':[]}
def matches(f,r):
    fields=f['fields'];action=fields['action']+' '+fields['benefit_type'];target=M.norm(fields['target'])
    if M.targets(r) and not any(t in target for t in M.targets(r)):return False
    if re.search(r'제외|불가|되지 않|않습니다',fields['action']):return False
    if r['action']=='discount' and '할인' not in action:return False
    if r['action']=='earn' and not re.search(r'적립|캐시백',action):return False
    if r['min_percent'] is not None:
        value=fields['value'].strip()
        if M.norm(fields['unit'])!='%' or not re.fullmatch(r'\d+(?:\.\d+)?\s*%?',value):return False
        if float(value.rstrip('%'))<r['min_percent']:return False
    if r['max_spend_krw'] is not None:
        m=re.search(r'전월[^\d]{0,35}(\d[\d,]*(?:\.\d+)?)\s*(만|천)?\s*원\s*이상',fields['condition'])
        if not m or M.amount(m)>r['max_spend_krw']:return False
    if r['min_cap_krw'] is not None:
        vals=[M.amount(m) for m in M.MONEY.finditer(fields['cap'])]
        if len(vals)!=1 or vals[0]<r['min_cap_krw']:return False
        if r['cap_period']!='any' and M.period(fields['cap']+' '+fields['period'])!=r['cap_period']:return False
    return True

def raw_audit(f,documents):
    document=documents[f['document_id']];pages={p['page']:p for p in document['pages']};rows=[]
    for field in ['target','action','value','condition','cap','period','frequency','exceptions']:
        for e in f['field_evidence'].get(field,[]):
            page=int(re.match(r'P(\d+)',e['line_id'])[1]);text=pages[page]['text'];fragment=e['fragment']
            found=M.norm(fragment) in M.norm(text)
            rows.append({'field':field,'line_id':e['line_id'],'fragment':fragment,'page':page,'found_in_ocr_page':found,
                         'page_text_sha256':C.digest(text),'source_json':f['source_relative_path']})
    core=[r for r in rows if r['field'] in ['target','action','value']]
    return {'fact_id':f['fact_id'],'card_key':f['document_id'],'fields':f['fields'],'spans':rows,
            'core_fragments_pass':bool(core) and all(r['found_in_ocr_page'] for r in core),
            'limits':'OCR page fragment integrity, not independent PDF truth or proof of all cross-cell relations.'}

def required_fragments_pass(requirement,audit):
    fields={'target','action','value'}
    if requirement.get('max_spend_krw') is not None:fields.add('condition')
    if requirement.get('min_cap_krw') is not None:fields.add('cap')
    if requirement.get('cap_period','any')!='any':fields.add('period')
    rows=[r for r in audit['spans'] if r['field'] in fields]
    required_nonempty=fields-{'period'}
    present={r['field'] for r in rows}
    requested=requirement.get('cap_period','any')
    periods=[M.period(r.get('fragment','')) for r in rows if r['field'] in ['cap','period']]
    period_proved=requested=='any' or (requested in periods and not any(p and p!=requested for p in periods))
    return audit['core_fragments_pass'] and required_nonempty<=present and period_proved and all(r['found_in_ocr_page'] for r in rows)

def build():
    C.initialize(39)
    facts=[__import__('json').loads(l) for l in (C.F.OLD[30]/'facts.jsonl').read_text().splitlines()]
    documents={}
    for f in facts:
        if f['document_id'] not in documents:documents[f['document_id']]=C.read(C.ROOT/f['source_relative_path'])
    catalog={k:v['identity']['card_name'] for k,v in documents.items()}
    def plan(groups=None,card=None,unresolved=None):return {'route':'structured_then_unstructured' if groups else 'unstructured',
        'groups':groups or [],'card_keys':[card] if card else [],'unresolved':unresolved or []}
    specifications=[
      ('넷플릭스 구독료를 7% 이상 할인받을 수 있는 카드 후보와 조건을 알려줘',plan([[req('넷플릭스','discount',7)]]),'numeric','recommend',[]),
      ('쿠팡 쇼핑 결제액의 5% 이상을 적립하는 카드를 찾고 싶어',plan([[req('쿠팡','earn',5)]]),'numeric','recommend',[]),
      ('택시 요금에 10% 이상 할인이 적용되는 카드를 추천해줘',plan([[req('택시','discount',10)]]),'numeric','recommend',[]),
      ('넷플릭스와 스타벅스에서 각각 10% 이상 할인되는 카드 한 장을 찾고 있어',plan([[req('넷플릭스','discount',10),req('스타벅스','discount',10)]]),'numeric','recommend',[]),
      ('넷플릭스나 쿠팡 중 한 곳만이라도 7% 이상 할인되면 되는 카드가 있을까',plan([[req('넷플릭스','discount',7)],[req('쿠팡','discount',7)]]),'numeric','recommend',[]),
      ('스타벅스 50% 이상 할인에 월 할인한도가 1만원 이상인 카드를 알려줘',plan([[req('스타벅스','discount',50,cap=10000,period='month')]]),'numeric','recommend',[]),
      ('케이뱅크 SIMPLE 카드로 넷플릭스를 결제할 때 할인율과 결제 경로 제한을 설명해줘',plan([[req('넷플릭스','discount',1.5)]],'BC/BC_KBank_SIMPLE'),'proper_noun','detail',[r'직접|공식',r'제외|불가']),
      ('삼성 taptap O의 스타벅스 할인율, 전월 이용금액 조건, 월 할인한도를 설명해줘',plan([[req('스타벅스','discount',50,spend=300000,cap=10000,period='month')]],'samsung/Samsung_taptap_O'),'proper_noun','detail',[]),
      ('현대카드 M으로 쿠팡에서 적립받는 비율과 전월 이용금액 및 적립한도는 어떻게 돼',plan([[req('쿠팡','earn',5,spend=1000000)]],'hyundai/Hyundai_M'),'proper_noun','detail',[r'1\s*만|10,?000']),
      ('나무 NH농협카드 공항 라운지는 월 몇 회와 연 몇 회 이용할 수 있고 실적 조건은 뭐야',plan([[req('라운지')]],'NH/NH_Namu_NH'),'proper_noun','detail',[r'월\s*1\s*회',r'연\s*2\s*회',r'40\s*만']),
      ('BC 바로 KaPick의 스타벅스 혜택이 할인인지 적립인지와 비율, 실적 조건을 알려줘',plan([[req('스타벅스','earn',7,spend=150000)]],'BC/BC_Baro_KaPick'),'proper_noun','detail',[]),
      ('케이뱅크 SIMPLE의 기본 할인과 플러스 할인을 더해서 받을 수 있는지 설명해줘',plan(card='BC/BC_KBank_SIMPLE'),'proper_noun','detail',[r'중복|함께|동시|합산',r'불가|않|아니|안\s*되']),
      ('공항 라운지 같은 여행 관련 혜택이 있는 카드 후보를 살펴보고 싶어',plan([[req('여행')]]),'semantic','recommend',[]),
      ('멜론으로 음악을 듣는데 관련 혜택을 제공하는 카드들을 알려줘',plan([[req('멜론')]]),'semantic','recommend',[]),
      ('마트 장보기와 대중교통을 모두 할인받을 수 있는 카드가 필요해',plan([[req('마트','discount'),req('대중교통','discount')]]),'semantic','recommend',[]),
      ('첫 신용카드를 고르려는데 소비 패턴을 아직 몰라. 어떤 조건부터 비교하면 좋을까',plan()),
      ('한 달 생활비가 25만원 정도인데 무조건 가장 이득인 카드 한 장을 골라줘',plan(unresolved=['소비 예산과 최적화 요청은 전월실적 조건으로 변환할 수 없음'])),
      ('호텔에서 결제 금액의 567%를 할인해 주는 카드가 있는지 근거로 확인해줘',plan([[req('호텔','discount',567)]]))]
    old=C.read(C.F.OLD[32]/'dataset_freeze.json');old_questions={q['question'] for q in old['queries']}
    by_card=defaultdict(list)
    for f in facts:by_card[f['document_id']].append(f)
    queries=[];audits={};excluded_requirements=[]
    def verified_match(f,r):
        if not matches(f,r):return False
        fid=f['fact_id']
        if fid not in audits:audits[fid]=raw_audit(f,documents)
        passed=required_fragments_pass(r,audits[fid])
        if not passed:excluded_requirements.append({'fact_id':fid,'requirement':r,'reason':'Required source fragments absent or mismatched'})
        return passed
    for index,spec in enumerate(specifications,1):
        question,p,*rest=spec
        subgroup,task,checks=rest if rest else ('semantic','advice' if index<18 else 'no_source_positive',[])
        assert question not in old_questions
        gold=[];witness={}
        for card,rows in by_card.items():
            if p['card_keys'] and card not in p['card_keys']:continue
            groups=[]
            for group in p['groups']:
                choices=[[f['fact_id'] for f in rows if verified_match(f,r)] for r in group]
                if all(choices):groups.append(choices)
            if groups:gold.append(card);witness[card]=groups
        if task=='detail' and not p['groups']:gold=p['card_keys']
        for groups in witness.values():
            for group in groups:
                for ids in group:
                    for fid in ids:
                        if fid not in audits:audits[fid]=raw_audit(next(f for f in facts if f['fact_id']==fid),documents)
        queries.append({'query_id':f'N{index:02}','question':question,'plan':p,'subgroup':subgroup,'task':task,
          'answer_checks':checks,'calculations':[],'gold_cards':sorted(gold),'witness_groups':witness,
          'split':'new_authored_confirmatory','negative_control':False})
    assert len(queries)==18 and all(q['gold_cards'] for q in queries[:15])
    bad=[k for k,v in audits.items() if not v['core_fragments_pass']]
    contract={'version':M.VERSION,'scorer_sha256':C.digest((C.ROOT/'notebooks/scripts/39_answer_rubric.py').read_bytes()),
      'source_builder_sha256':C.digest(Path(__file__).read_bytes()),
      'primary':'known_positive_complete on15 positive queries, failures zero; per-query paired wins/losses/ties',
      'guardrails':['citation ownership','explicit card scope','unsupported percent claims separately; semantic correctness not guaranteed'],
      'formula':'Only registered same-card same-target sum; exact Decimal, no rounding; all operand anchors in owned citations',
      'split':'Same-card condition-only claim must share a citation with target benefit claim',
      'detail':'Claims may answer specified-card detail even if recommendations is empty',
      'limits':['New authored wording on same source corpus, not sealed holdout','Partial conservative OCR-source labels',
       'Numeric evidence text can contain unrelated values; lexical checks do not establish semantic entailment','No configuration promotion or production adoption'],
      'prior_dataset_sha256':old['sha256'],'new_api_requests':0}
    # Preserve failed provenance checks as exclusions; never silently certify them.
    for q in queries:
        for card,groups in list(q['witness_groups'].items()):
            kept=[]
            for group in groups:
                cleaned=[[fid for fid in ids if fid not in bad] for ids in group]
                if all(cleaned):kept.append(cleaned)
            if kept:q['witness_groups'][card]=kept
            else:
                del q['witness_groups'][card]
                q['gold_cards'].remove(card)
    assert all(q['gold_cards'] for q in queries[:15])
    C.freeze(C.STAGES[39]/'scorer_contract.json',contract)
    dataset=C.freeze(C.STAGES[39]/'dataset.json',{'queries':queries,'catalog':catalog,'source_audit':list(audits.values()),
      'source_fragment_exclusions':bad,
      'required_fragment_exclusions':excluded_requirements,
      'source_sha256':{'facts':C.digest((C.F.OLD[30]/'facts.jsonl').read_bytes()),'documents':C.digest((C.F.OLD[30]/'documents.jsonl').read_bytes())},
      'scorer_contract_sha256':C.read(C.STAGES[39]/'scorer_contract.json')['sha256'],'gold_positive_queries':15,
      'authorship':'Main authored questions/requirements before retrieval; raw OCR fragments mechanically verified before answer generation',
      'no_source_positive_is_universal_negative':False})
    if not (C.STAGES[39]/'freeze_receipt.json').exists():C.write(C.STAGES[39]/'freeze_receipt.json',{'frozen_at':time.time(),
      'dataset_sha256':dataset['sha256'],'scorer_contract_sha256':dataset['scorer_contract_sha256'],
      'stage40_api_records_at_freeze':len(list((C.STAGES[40]/'api_cache').glob('*.json')))})
    print('New questions',len(queries),'positive',sum(bool(q['gold_cards']) for q in queries),'gold fact source audits',len(audits))
    return dataset
if __name__=='__main__':build()
