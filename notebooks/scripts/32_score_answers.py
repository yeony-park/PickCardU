"""Frozen conservative answer-condition diagnostics, not an LLM judge."""
from pathlib import Path
import importlib.util
import sys
import re
import unicodedata

s=importlib.util.spec_from_file_location('search_experiment',Path(__file__).with_name('32_search_experiment.py'))
E=importlib.util.module_from_spec(s);sys.modules[s.name]=E;s.loader.exec_module(E);C=E.C
VERSION='answer-source-relative-constraints-v3'
PERCENT=re.compile(r'(\d+(?:\.\d+)?)\s*(?:%|퍼센트)')
MONEY=re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*(만|천)?\s*원')

def norm(text):return ''.join(unicodedata.normalize('NFKC',str(text)).casefold().split())
def money(match):return round(float(match[1].replace(',',''))*{None:1,'만':10000,'천':1000}[match[2]])

def cap_amounts(text):
    amount=r'(\d[\d,]*(?:\.\d+)?)\s*(만|천)?\s*원'
    prefix=r'(?:한도\s*(?:는|:)?|(?<![가-힣])(?:월|매월|하루|매일|일)\s*(?:(?:통합|할인|적립)\s*)?(?:한도|최대)?\s*(?:는|:)?)'
    values={money(m) for m in re.finditer(prefix+r'\s*'+amount,text)
            if not re.match(r'\s*(?:이용금액|이용액|결제금액|결제액|승인금액|매출)',text[m.end():])}
    values.update(money(m) for m in re.finditer(amount+r'\s*(?:까지\s*)?(?:매월|월\s*(?:할인|적립)?\s*한도|일\s*한도|하루)',text))
    return values

def percent_values(claim):
    values=[float(m[1]) for m in PERCENT.finditer(claim['text'])]
    unit=norm(claim.get('unit') or '')
    if unit in {'%','퍼센트','percent'}:
        try:values.append(float(str(claim['value']).replace('%','')))
        except (ValueError,TypeError):pass
    return sorted(set(values))

def component_support(claim,requirement,evidence):
    text=claim['text'];conditions=' '.join(claim.get('conditions',[]));combined=text+' '+conditions
    targets=E.DB._expanded_targets(requirement['targets']);action=requirement['action']
    flags=[]
    if targets:flags.append(any(target in norm(combined) for target in targets))
    if action!='any':flags.append(('할인' in combined) if action=='discount' else any(t in combined for t in ['적립','캐시백']))
    cited=' '.join(evidence[c]['text'] for c in claim['citations'] if c in evidence and evidence[c]['card_key']==claim['card_key'])
    if not cited:return 0.0
    if requirement['min_percent'] is not None:
        values=percent_values(claim);supported=[float(m[1]) for m in PERCENT.finditer(cited)]
        flags.append(any(v>=requirement['min_percent'] and v in supported for v in values))
    if requirement['max_spend_krw'] is not None:
        match=re.search(r'(?:전월\s*)?(?:실적|이용금액)[^\d]{0,12}(\d[\d,]*(?:\.\d+)?)\s*(만|천)?\s*원\s*(?:이상|필요)',combined)
        supported={money(m) for m in MONEY.finditer(cited)}
        flags.append(bool(match and money(match)<=requirement['max_spend_krw'] and money(match) in supported))
    if requirement['min_cap_krw'] is not None:
        period=requirement['cap_period']
        cap_text=' '.join(fragment for fragment in re.split(r'[。\n;]',combined)
                          if re.search(r'한도|최대|매월|하루|매일',fragment))
        values=cap_amounts(cap_text);supported=cap_amounts(cited)
        period_supported=period=='any' or E.explicit_cap_period(cap_text)==E.explicit_cap_period(cited)==period
        flags.append(bool(any(v>=requirement['min_cap_krw'] and v in supported for v in values) and period_supported))
    if requirement['exclude_targets']:
        flags.append(not any(t in norm(combined) for t in E.DB._expanded_targets(requirement['exclude_targets'])))
    return sum(map(bool,flags))/len(flags) if flags else 1.0

def score_row(case,row,retrieval):
    if row['status']!='ok':return {'status':'failed','constraint_coverage':0 if case['plan']['groups'] else None,'known_positive_complete':0 if case['gold_cards'] else None}
    answer=row['answer'];evidence={e['chunk_id']:e for e in retrieval['result']['evidence']}
    claims=answer['claims'];recommended={r['card_key'] for r in answer['recommendations']}
    groups=case['plan']['groups'];coverage=None;card_coverage={}
    if groups:
        # A full AND group must be described for the same recommended card.
        candidates=[]
        for card in recommended:
            owned=[claim for claim in claims if claim['card_key']==card]
            for group in groups:
                value=sum(max([component_support(claim,r,evidence) for claim in owned] or [0]) for r in group)/len(group)
                candidates.append(value);card_coverage[card]=max(card_coverage.get(card,0),value)
        coverage=max(candidates or [0])
    numeric_claims=unsupported=0
    for claim in claims:
        values=percent_values(claim)
        if not values:continue
        numeric_claims+=1
        cited=' '.join(evidence[c]['text'] for c in claim['citations'] if c in evidence and evidence[c]['card_key']==claim['card_key'])
        supported={float(m[1]) for m in PERCENT.finditer(cited)}
        unsupported+=any(v not in supported for v in values)
    complete=None
    if case['gold_cards']:
        complete=int(any(coverage is None or card_coverage.get(card,0)==1 for card in recommended&set(case['gold_cards']))
                     and row['metrics']['citation_ownership']==1 and unsupported==0)
    return {'status':'ok','constraint_coverage':coverage,'known_positive_complete':complete,
            'numeric_percent_claims':numeric_claims,'unsupported_percent_claims':unsupported}

def freeze_protocol():
    contract={'version':VERSION,'created_before_answer_results':True,'scorer_sha256':C.digest(Path(__file__).read_bytes()),
      'rules':'Require owned citation. Match target/action/numeric/spend/cap components in same-card claims; OR groups choose best; AND requirements averaged within one recommended card. Percent and monetary values must also appear in owned cited evidence; requested cap period must appear. Failed rows score zero. Source-relative lexical diagnostic only.',
      'limitations':['No independent PDF or external LLM judge','Paraphrase and split-claim false negatives possible',
                     'Numbers elsewhere in a large cited passage can be false positive support','Semantic causal relationships are not proven',
                     'Known-positive complete is a conservative proxy, not population answer accuracy']}
    path=C.STAGES[32]/'answer_scoring_contract.json'
    if path.exists() and C.read_json(path)!=contract:raise RuntimeError('Scoring protocol changed: explicit version required')
    C.write_json(path,contract);return contract

def run(source):
    contract=freeze_protocol();directory=C.STAGES[32] if source=='oracle' else C.STAGES[33]
    answers=C.read_json(directory/'answer_results.json');retrieval=C.read_json(directory/'retrieval_results.json')
    freeze=E.freeze_dataset();cases={q['query_id']:q for q in freeze['queries']}
    lookup={(r['query_id'],r['profile'],r['mode']):r for r in retrieval['records']}
    rows=[]
    for row in answers['records']:
        key=(row['query_id'],row['profile'],row['mode']);scored=score_row(cases[row['query_id']],row,lookup[key])
        rows.append({'query_id':row['query_id'],'profile':row['profile'],'mode':row['mode'],**scored})
    result={'version':VERSION,'source':source,'protocol_sha256':C.digest(contract),'answer_results_sha256':C.digest(answers),
            'retrieval_results_sha256':C.digest(retrieval),'records':rows}
    C.write_json(directory/'answer_constraint_scores.json',result);print(source,'scored',len(rows));return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',choices=['oracle','llm']);args=p.parse_args()
    run(args.source) if args.source else freeze_protocol()
