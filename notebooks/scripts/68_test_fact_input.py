"""Information and ownership invariants of the automatic68 assembler."""
import importlib.util
from pathlib import Path
import copy
import json

import pytest

s=importlib.util.spec_from_file_location('fact68',Path(__file__).with_name('68_fact_input.py'))
F=importlib.util.module_from_spec(s);s.loader.exec_module(F)


def fixture():
    f={'fact_id':'f1','document_id':'a','ordinal':1,'fields':{'target':'동물병원','value':'1','unit':'%','condition':'전월30만원'},
       'derived':{'percent_state':'known','percent':1,'spend_state':'known','spend_threshold_krw':300000},
       'relation_scope':{'scope_type':'bounded_block','line_ids':['P1-L1']},
       'field_evidence':{'target':[{'line_id':'P1-L1','fragment':'동물병원'}]},'source_relative_path':'source.json',
       'source_path_sha256':'abc','source_pdf_sha256':'pdf'}
    cards=[dict(card_key='a',card_name='A카드',issuer='은행',rank=1)]
    result=dict(cards=cards,evidence=[dict(chunk_id='u1',card_key='a',card_name='A카드',issuer='은행',
        text='기존 근거',page_num=1,section='생활',level='benefit',rank=1,score=1)])
    route=dict(matching_fact_ids={'a':['f1']},requirement_fact_ids={'a':{'0':[['f1']]}},
        plan={'groups':[[{'targets':['동물']}]]})
    return result,route,{'f1':f}


def test_B_C_are_same_full_canonical_facts_and_relations():
    result,route,facts=fixture();canonical=F.canonical_records(result,route,facts)
    b,c=F.render_json(canonical),F.render_text(canonical)
    assert json.loads(b)==F.parse_text(c)==canonical
    assert canonical[0]['fields']==facts['f1']['fields']
    assert canonical[0]['relation_scope']==facts['f1']['relation_scope']
    assert canonical[0]['requirement_links']==[{'group':0,'requirement':0,'requirement_value':{'targets':['동물']}}]


def test_original_U_cards_ranks_and_every_byte_remain_unchanged():
    result,route,facts=fixture();before=copy.deepcopy(result)
    out=F.assemble('동물병원 카드',result,route,facts)
    assert out['applicable']
    for arm in ('A','B','C'):
        assert out['results'][arm]['cards']==before['cards']
        assert out['results'][arm]['evidence'][:1]==before['evidence']
    assert result==before


def test_an_outside_card_fact_is_never_transferred():
    result,route,facts=fixture();facts['f1']['document_id']='b'
    with pytest.raises(ValueError,match='owner'):F.canonical_records(result,route,facts)


def test_no_match_produces_no_fabricated_fact():
    result,route,facts=fixture();route['matching_fact_ids']={}
    out=F.assemble('동물병원 카드',result,route,facts)
    assert not out['applicable'] and out['reason']=='no_matching_fact_for_selected_cards'
    assert all(v==result for v in out['results'].values())


def test_if_either_format_overflows_both_fall_back_to_exact_A(monkeypatch):
    result,route,facts=fixture()
    monkeypatch.setattr(F,'measure',lambda q,e:100 if '구조화 사실' in e[-1]['text'] else 10)
    out=F.assemble('질문',result,route,facts,limit=50)
    assert not out['applicable'] and out['reason']=='paired_payload_limit'
    assert all(v==result for v in out['results'].values())


def test_assembler_has_no_question_gold_parameter():
    import inspect
    assert set(inspect.signature(F.canonical_records).parameters)=={'result','routing','facts'}
    assert 'gold' not in inspect.signature(F.assemble).parameters


def test_duplicate_card_owner_and_oversize_original_are_rejected(monkeypatch):
    result,route,facts=fixture();result['cards']*=2
    with pytest.raises(ValueError,match='Duplicate'):F.canonical_records(result,route,facts)
    result,route,facts=fixture();monkeypatch.setattr(F,'measure',lambda q,e:64001)
    with pytest.raises(ValueError,match='Original U'):F.assemble('질문',result,route,facts)
