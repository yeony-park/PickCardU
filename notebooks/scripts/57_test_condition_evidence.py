"""Whole-section supplements must preserve original scope, evidence and budget."""
import copy
import importlib.util
from pathlib import Path
import pytest


def helper():
    spec = importlib.util.spec_from_file_location('supplement57', Path(__file__).with_name('57_condition_evidence.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def unit(cid, card='a', section='공통 서비스 조건'):
    return dict(chunk_id=cid,card_key=card,card_name=card,issuer='issuer',text=cid+' complete source section',
                page_num=2,section=section,level='section',heading_path=[])


def initial():
    return dict(cards=[dict(card_key='a',rank=1)], evidence=[unit('original')])


def test_scope_heading_filter_and_exact_ties_are_deterministic():
    m = helper(); before = initial(); saved = copy.deepcopy(before)
    pool = [unit('z'),unit('a'),unit('wrong-card','b'),unit('irrelevant',section='상품 소개')]
    seen = []
    def score(query, units):
        seen.extend(u['chunk_id'] for u in units)
        return [1. for u in units]
    result = m.supplement('질문', before, pool, score, fits=lambda q,e:True)
    assert [e['chunk_id'] for e in result['evidence']] == ['original','a','z']
    assert seen == ['z','a'] and before == saved
    assert [(c['card_key'],c['rank']) for c in result['cards']] == [('a',1)]


def test_budget_refusal_never_evicts_or_truncates_original_evidence():
    m = helper(); before = initial()
    result = m.supplement('질문',before,[unit('new')],lambda q,u:[1.],fits=lambda q,e:False)
    assert result['evidence'] == before['evidence']
    assert result['condition_supplement']['omitted_for_budget'] == ['new']


def test_no_more_than_two_whole_sections_and_duplicate_text_is_skipped():
    m = helper(); before=initial(); pool=[unit('original'),unit('c'),unit('a'),unit('b')]
    result=m.supplement('질문',before,pool,lambda q,u:[1.]*len(u),fits=lambda q,e:True)
    assert [u['chunk_id'] for u in result['evidence']] == ['original','a','b']
    assert result['evidence'][1]['text'] == unit('a')['text']


def test_original_scope_checks_cards_even_when_their_evidence_is_empty():
    m=helper()
    with pytest.raises(RuntimeError,match='escaped'):
        m.validate_scope(dict(cards=[dict(card_key='outside')],evidence=[]),['inside'])
    with pytest.raises(RuntimeError,match='escaped'):
        m.validate_scope(dict(cards=[dict(card_key='inside')],evidence=[unit('wrong','outside')]),['inside'])
    m.validate_scope(dict(cards=[dict(card_key='inside')],evidence=[]),['inside'])
