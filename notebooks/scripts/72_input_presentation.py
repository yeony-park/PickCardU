"""Experiment-only format projection; never edits or summarizes stored facts.

The helper makes exact-field, multiline and ownership checks reproducible without
running paid notebook cells. Audit identifiers remain in the backend sidecar.
"""
import copy
import json

FIELDS=('target','action','benefit_type','value','unit','condition','cap','period','frequency','exceptions')
STATES=('action_status','percent_state','spend_state','cap_state','cap_period_state')
LABELS=dict(zip(FIELDS,('대상','혜택 방식','혜택 분류','값','단위','적용조건','한도 원문','기간','횟수','제외사항')))
LABELS.update(dict(zip(STATES,('행위 상태','비율 상태','실적 상태','한도 상태','한도기간 상태'))))


def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False,sort_keys=True)


def project(record):
    if set(record['fields'])!=set(FIELDS) or any(k not in record['derived'] for k in STATES):
        raise ValueError('Missing or unexpected canonical business field/state')
    return dict(fields={k:record['fields'][k] for k in FIELDS if record['fields'][k] not in ('',None)},
                states={k:record['derived'][k] for k in STATES})


def render_text(projection):
    lines=[]
    for section in ('fields','states'):
        for key,value in projection[section].items():
            if not isinstance(value,str):
                raise ValueError('Text projection requires original string values')
            first,*rest=value.split('\n')
            lines.append(LABELS[key]+': '+first)
            lines.extend('  '+line for line in rest)
    return '\n'.join(lines)


def parse_text(text):
    result=dict(fields={},states={});inverse={v:k for k,v in LABELS.items()};section=key=None
    for line in text.split('\n'):
        if line.startswith('  '):
            if key is None:raise ValueError('Orphan continuation')
            result[section][key]+='\n'+line[2:]
        else:
            label,value=line.split(': ',1);key=inverse[label]
            section='fields' if key in FIELDS else 'states'
            if key in result[section]:raise ValueError('Duplicate text field')
            result[section][key]=value
    return result


def alias_records(records,card_keys=None):
    reverse=dict(cards={},facts={},paths={});forward={k:{} for k in reverse}
    def alias(kind,value,prefix):
        if value not in forward[kind]:
            token=prefix+str(len(forward[kind])+1)
            forward[kind][value]=token;reverse[kind][token]=value
        return forward[kind][value]
    for key in card_keys or []:alias('cards',key,'C')
    output=copy.deepcopy(records)
    for row in output:
        row['card_key']=alias('cards',row['card_key'],'C')
        row['fact_id']=alias('facts',row['fact_id'],'F')
        row['source_relative_path']=alias('paths',row['source_relative_path'],'P')
    return output,reverse


def restore_records(records,reverse):
    output=copy.deepcopy(records)
    for row in output:
        for field,kind in [('card_key','cards'),('fact_id','facts'),('source_relative_path','paths')]:
            row[field]=reverse[kind][row[field]]
    return output


def assert_sql_fields(record,sql_fields):
    if record['fields']!=sql_fields:raise ValueError('Canonical fields differ from current SQL raw columns')


def build_payload(question,cards,evidence,records,arm):
    if arm not in 'ABC' or len(arm)!=1:raise ValueError('Unknown format')
    owners=[c['card_key'] for c in cards]
    if len(owners)!=len(set(owners)):raise ValueError('Duplicate card owner')
    if any(e['card_key'] not in owners for e in evidence) or any(r['card_key'] not in owners for r in records):
        raise ValueError('Unknown evidence/fact owner')
    aliased,reverse=alias_records(records,owners)
    lookup={v:k for k,v in reverse['cards'].items()}
    registry=[dict(card_ref=lookup[c['card_key']],card_name=c['card_name'],issuer=c['issuer']) for c in cards]
    out=[];index=0
    for i,e in enumerate(evidence,1):
        item=dict(evidence_id=f'e{i}',card_ref=lookup[e['card_key']],text=e['text'])
        if e.get('origin')=='structured':
            if index>=len(records) or records[index]['card_key']!=e['card_key']:
                raise ValueError('Structured suffix order/owner changed')
            row=aliased[index];projection=project(row)
            item['fact_ref']=row['fact_id']
            item['text']=compact([row]) if arm=='A' else compact(projection) if arm=='B' else render_text(projection)
            if arm=='C' and parse_text(item['text'])!=projection:raise ValueError('Text roundtrip lost a field')
            index+=1
        elif index:raise ValueError('Unstructured evidence must precede the structured suffix')
        out.append(item)
    if index!=len(records):raise ValueError('Structured records not fully transmitted')
    assert restore_records(aliased,reverse)==records
    return dict(standalone_query=question,cards=registry,evidence=out),reverse
