"""Automatic DB-fact assembly shared by68/69; never consumes answer gold.

The original U evidence is an exact prefix. B/C carry identical DB row fields,
relations and source bindings, differing only in their textual representation.
Existing DB row relations are disclosed as stored, never strengthened by hand.
"""
import copy
import json

VERSION='automatic_same_fact_relation68_v1'
LABELS={'card_key':'카드 키','card_name':'카드명','issuer':'발급사','fact_id':'사실 식별자',
        'ordinal':'원래 행 순서','fields':'혜택과 조건 필드','derived':'수치 정규화와 상태',
        'relation_scope':'DB에 저장된 적용 관계','field_evidence':'필드별 출처 문구',
        'source_relative_path':'추출 자료 경로','source_path_sha256':'추출 자료 해시',
        'source_pdf_sha256':'원본 PDF 해시','requirement_links':'일치한 질문 조건'}


def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def canonical_records(result,routing,facts):
    keys=[card['card_key']for card in result['cards']]
    if len(keys)!=len(set(keys)):raise ValueError('Duplicate selected card owner')
    records=[]
    for card in result['cards']:
        key=card['card_key']
        ids=set(routing.get('matching_fact_ids',{}).get(key,[]))
        for fid in sorted(ids,key=lambda f:(facts[f]['ordinal'],f)):
            fact=facts[fid]
            if fact['document_id']!=key:raise ValueError('Fact owner differs from selected card')
            links=[]
            for group,requirements in routing.get('requirement_fact_ids',{}).get(key,{}).items():
                for index,matches in enumerate(requirements):
                    if fid in matches:
                        links.append(dict(group=int(group),requirement=index,
                            requirement_value=copy.deepcopy(routing['plan']['groups'][int(group)][index])))
            item=dict(card_key=key,card_name=card['card_name'],issuer=card['issuer'],fact_id=fid,
                ordinal=fact['ordinal'],**{k:copy.deepcopy(fact[k])for k in ('fields','derived','relation_scope','field_evidence',
                   'source_relative_path','source_path_sha256','source_pdf_sha256')},requirement_links=links)
            records.append(item)
    return records


def render_json(records):
    return compact(records)


def render_text(records):
    blocks=[]
    for index,row in enumerate(records,1):
        blocks.append('\n'.join([f'구조화 사실 {index}',*[LABELS[k]+': '+compact(v)for k,v in row.items()]]))
    return '\n\n'.join(blocks)


def parse_text(text):
    inverse={v:k for k,v in LABELS.items()};records=[]
    for index,block in enumerate(text.split('\n\n'),1):
        lines=block.splitlines()
        if lines[0]!=f'구조화 사실 {index}':raise ValueError('Invalid fact header')
        row={}
        for line in lines[1:]:
            label,value=line.split(': ',1);key=inverse[label]
            if key in row:raise ValueError('Duplicate fact field')
            row[key]=json.loads(value)
        records.append(row)
    return records


def measure(question,evidence):
    from pickcardu_rag.answering import measure_answer_payload
    return measure_answer_payload(question,evidence)[0]


def assemble(question,result,routing,facts,*,limit=64000):
    if measure(question,result['evidence'])>limit:raise ValueError('Original U payload exceeds limit')
    canonical=canonical_records(result,routing,facts)
    outputs={arm:copy.deepcopy(result)for arm in ('A','B','C')}
    if not canonical:
        return dict(results=outputs,canonical=canonical,applicable=False,reason='no_matching_fact_for_selected_cards')
    assert json.loads(render_json(canonical))==parse_text(render_text(canonical))==canonical
    # One self-contained fact per evidence item keeps citations owned by its card.
    for arm,renderer in [('B',render_json),('C',render_text)]:
        for index,row in enumerate(canonical,1):
            outputs[arm]['evidence'].append(dict(chunk_id='s68:'+row['fact_id'],card_key=row['card_key'],
                card_name=row['card_name'],issuer=row['issuer'],text=renderer([row]),page_num=None,
                section='구조화 DB의 저장된 사실과 관계',level='benefit',rank=len(outputs[arm]['evidence'])+1,
                origin='structured',score=None))
    sizes={arm:measure(question,row['evidence'])for arm,row in outputs.items()}
    applicable=max(sizes['B'],sizes['C'])<=limit
    if not applicable:outputs={arm:copy.deepcopy(result)for arm in ('A','B','C')}
    assert all(row['cards']==result['cards'] and row['evidence'][:len(result['evidence'])]==result['evidence']for row in outputs.values())
    return dict(results=outputs,canonical=canonical,applicable=applicable,
        reason='automatic_equal_content_formats'if applicable else'paired_payload_limit',candidate_payload_bytes=sizes,
        automatic_only=True,stored_row_relations_assumed_valid=True,no_U_eviction=True)
