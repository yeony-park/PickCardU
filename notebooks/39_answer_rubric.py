"""Frozen source-relative rubric: owned citations, split claims and registered sums.
This is a bounded lexical evaluator, not semantic entailment or an LLM judge.
"""
from decimal import Decimal
import re
import unicodedata

VERSION='source-relative-split-detail-registered-sum-v1'
PERCENT=re.compile(r'(\d+(?:\.\d+)?)\s*(?:%|퍼센트)')
MONEY=re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*(만|천)?\s*원')
ALIASES={'여행':['여행','항공','호텔','숙박','공항','라운지','면세점'],
         '커피':['커피','카페','스타벅스','투썸','이디야','폴바셋','커피빈']}

def norm(text):return ''.join(unicodedata.normalize('NFKC',str(text)).casefold().split())
def targets(req):return [norm(x) for t in req['targets'] for x in ALIASES.get(t,[t])]
def amount(m):return Decimal(m[1].replace(',',''))*{None:1,'만':10000,'천':1000}[m[2]]
def percentages(text):return {Decimal(m[1]) for m in PERCENT.finditer(text)}
def owned_text(claim,evidence):
    by={e['chunk_id']:e for e in evidence}
    if not claim['citations'] or any(c not in by or by[c]['card_key']!=claim['card_key'] for c in claim['citations']):return None
    return '\n'.join(by[c]['text'] for c in claim['citations'])
def claim_text(c):return c['text']+' '+' '.join(c.get('conditions',[]))

def percent_supported(claim,evidence,rules):
    cited=owned_text(claim,evidence)
    if cited is None:return False
    values=percentages(claim['text'])
    if norm(claim.get('unit') or '') in ['%','퍼센트','percent'] and claim.get('value') is not None:
        try:values.add(Decimal(str(claim['value']).replace('%','')))
        except Exception:pass
    allowed=percentages(cited)
    for rule in rules:
        if rule['card_key']!=claim['card_key'] or rule['unit']!='%' or rule['operation']!='sum' or rule['rounding']!='none':continue
        if norm(rule['target']) not in norm(claim_text(claim)) or norm(rule['target']) not in norm(cited):continue
        if not all(norm(a) in norm(cited) for a in rule['anchors']):continue
        operands=list(map(Decimal,rule['operands']))
        if set(operands)<=allowed:allowed.add(sum(operands,Decimal(0)))
    return values<=allowed

def period(text):
    month=bool(re.search(r'월\s*(?:할인|적립|통합)?\s*(?:한도|최대)|매월|월간|월별',text))
    day=bool(re.search(r'일\s*(?:할인|적립|통합)?\s*(?:한도|최대)|하루|매일|일일',text))
    return 'month' if month and not day else 'day' if day and not month else None

def requirement_complete(req,claims,evidence,card):
    owned=[c for c in claims if c['card_key']==card and owned_text(c,evidence) is not None]
    wanted=targets(req)
    relevant=[c for c in owned if not wanted or any(t in norm(claim_text(c)) for t in wanted)]
    if not relevant:return False
    refs={c for r in relevant for c in r['citations']}
    # A condition-only claim can extend a benefit only through a shared owned citation.
    extras=[c for c in owned if c not in relevant and bool(refs&set(c['citations'])) and
            re.search(r'실적|이용금액|이용 금액|한도|최대',claim_text(c))]
    description='\n'.join(claim_text(c) for c in relevant+extras)
    cited='\n'.join(owned_text(c,evidence) for c in relevant+extras)
    action=req['action']
    action_words=['할인'] if action=='discount' else ['적립','캐시백'] if action=='earn' else []
    if action_words and not any(w in description and w in cited for w in action_words):return False
    if req['min_percent'] is not None:
        if not any(v>=Decimal(str(req['min_percent'])) and v in percentages(cited) for v in percentages(description)):return False
    if req['max_spend_krw'] is not None:
        pattern=r'(?:전월\s*)?(?:실적|이용\s*금액)[^\d]{0,12}(\d[\d,]*(?:\.\d+)?)\s*(만|천)?\s*원\s*(?:이상|필요)'
        vals={amount(m) for m in re.finditer(pattern,description)};support={amount(m) for m in re.finditer(pattern,cited)}
        if not any(v<=req['max_spend_krw'] and v in support for v in vals):return False
    if req['min_cap_krw'] is not None:
        fragments=lambda t:' '.join(x for x in re.split(r'[\n.;]',t) if re.search(r'한도|최대',x))
        d,e=fragments(description),fragments(cited)
        vals={amount(m) for m in MONEY.finditer(d)};support={amount(m) for m in MONEY.finditer(e)}
        if not any(v>=req['min_cap_krw'] and v in support for v in vals):return False
        if req['cap_period']!='any' and not (period(d)==period(e)==req['cap_period']):return False
    return True

def score(case,answer,evidence):
    claims=answer['claims'];ownership=all(owned_text(c,evidence) is not None for c in claims+answer['recommendations'])
    unsupported=sum(not percent_supported(c,evidence,case.get('calculations',[])) for c in claims)
    chosen={c['card_key'] for c in (claims if case['task']=='detail' else answer['recommendations'])}
    valid=chosen&set(case['gold_cards']);full=[]
    all_output_cards={c['card_key'] for c in claims+answer['recommendations']}
    scope=case['plan']['card_keys'];scope_ok=not scope or all_output_cards<=set(scope)
    for card in valid:
        groups=case['plan']['groups']
        covered=not groups or any(all(requirement_complete(r,claims,evidence,card) for r in group) for group in groups)
        text=answer['answer_text']+'\n'+'\n'.join(claim_text(c) for c in claims if c['card_key']==card)
        extra=all(re.search(pattern,text,re.I) for pattern in case.get('answer_checks',[]))
        full.append(covered and extra)
    complete=int(bool(full) and any(full) and ownership and scope_ok and unsupported==0 and answer['answer_status']=='answered') if case['gold_cards'] else None
    return {'known_positive_complete':complete,'citation_ownership':int(ownership),'unsupported_percent_claims':unsupported,
            'explicit_scope_preserved':int(scope_ok),'task':'detail allows claims without recommendations' if case['task']=='detail' else case['task']}
