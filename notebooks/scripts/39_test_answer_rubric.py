"""Regression fixtures independent of the new-question answer outputs."""
import importlib.util
from pathlib import Path
import sys
s=importlib.util.spec_from_file_location('rubric_under_test',Path(__file__).with_name('39_answer_rubric.py'))
M=importlib.util.module_from_spec(s);sys.modules[s.name]=M;s.loader.exec_module(M)

def claim(text='스타벅스 1.5% 적립',value='1.5',citations=None,conditions=None):
    return {'card_key':'a','text':text,'value':value,'unit':'%','citations':citations or ['e1'],'conditions':conditions or []}
E=[{'card_key':'a','chunk_id':'e1','text':'스타벅스 기본 1% 적립. 추가 0.5% 적립.'}]
RULE={'card_key':'a','target':'스타벅스','unit':'%','operands':['1','0.5'],'anchors':['기본 1%','추가 0.5%'],'operation':'sum','rounding':'none'}

def test_sum_requires_explicit_rule():
    assert not M.percent_supported(claim(),E,[])
    assert M.percent_supported(claim(),E,[RULE])
def test_sum_requires_all_operands_in_owned_citations():
    assert not M.percent_supported(claim(),[{**E[0],'text':'스타벅스 기본 1% 적립'}],[RULE])
    assert not M.percent_supported(claim(),[{**E[0],'card_key':'b'}],[RULE])
def test_other_target_sum_rejected():
    assert not M.percent_supported(claim(text='편의점 1.5% 적립'),E,[RULE])
def test_unregistered_sum_rejected():
    assert not M.percent_supported(claim(value='2',text='스타벅스 2% 적립'),E,[RULE])
def test_split_claim_conditions_can_pass_same_card():
    req={'targets':['스타벅스'],'action':'discount','min_percent':10,'max_spend_krw':300000,'min_cap_krw':5000,'cap_period':'month','exclude_targets':[]}
    evidence=[{'card_key':'a','chunk_id':'e1','text':'스타벅스 10% 할인. 전월 실적 30만원 이상. 월 할인한도 5천원.'}]
    claims=[claim('스타벅스 10% 할인','10'),claim('전월 실적 30만원 이상, 월 할인한도 5천원',None)]
    assert M.requirement_complete(req,claims,evidence,'a')
    claims[1]['card_key']='b'
    assert not M.requirement_complete(req,claims,evidence,'a')
def test_day_month_mismatch_rejected():
    req={'targets':['스타벅스'],'action':'discount','min_percent':10,'max_spend_krw':None,'min_cap_krw':5000,'cap_period':'month','exclude_targets':[]}
    evidence=[{'card_key':'a','chunk_id':'e1','text':'스타벅스 10% 할인. 일 할인한도 5천원.'}]
    assert not M.requirement_complete(req,[claim('스타벅스 10% 할인','10',conditions=['일 할인한도 5천원'])],evidence,'a')
def test_detail_does_not_require_recommendation_array():
    case={'task':'detail','gold_cards':['a'],'plan':{'groups':[[{'targets':['스타벅스'],'action':'earn','min_percent':1,'max_spend_krw':None,'min_cap_krw':None,'cap_period':'any','exclude_targets':[]}]],'card_keys':['a']},'answer_checks':[],'calculations':[]}
    answer={'answer_status':'answered','recommendations':[],'claims':[claim('스타벅스 기본 1% 적립','1')],'answer_text':'스타벅스 기본 1% 적립'}
    assert M.score(case,answer,E)['known_positive_complete']==1
    case['task']='recommend'
    assert M.score(case,answer,E)['known_positive_complete']==0
def test_percentage_value_not_in_citation_rejected():
    assert not M.percent_supported(claim('스타벅스 99% 적립','99'),E,[])

def test_scope_violation_blocks_complete_even_with_correct_detail():
    case={'task':'detail','gold_cards':['a'],'plan':{'groups':[],'card_keys':['a']},'answer_checks':[],'calculations':[]}
    evidence=E+[{'card_key':'b','chunk_id':'e2','text':'스타벅스 1% 적립'}]
    answer={'answer_status':'answered','answer_text':'스타벅스 1% 적립','recommendations':[],
      'claims':[claim('스타벅스 기본 1% 적립','1'),{**claim('스타벅스 1% 적립','1',citations=['e2']),'card_key':'b'}]}
    result=M.score(case,answer,evidence)
    assert result['explicit_scope_preserved']==0
    assert result['known_positive_complete']==0

def test_required_condition_fragment_failure_blocks_gold():
    s=importlib.util.spec_from_file_location('source_gate_test',Path(__file__).with_name('39_freeze_new_questions.py'))
    F=importlib.util.module_from_spec(s);sys.modules[s.name]=F;s.loader.exec_module(F)
    req={'max_spend_krw':300000,'min_cap_krw':None,'min_percent':10,'cap_period':'any'}
    audit={'core_fragments_pass':True,'spans':[{'field':'target','found_in_ocr_page':True},
      {'field':'action','found_in_ocr_page':True},{'field':'value','found_in_ocr_page':True},
      {'field':'condition','found_in_ocr_page':False}]}
    assert not F.required_fragments_pass(req,audit)
    audit['spans'][-1]['found_in_ocr_page']=True
    assert F.required_fragments_pass(req,audit)

def test_period_missing_requires_explicit_period_in_cap_fragment():
    s=importlib.util.spec_from_file_location('period_gate_test',Path(__file__).with_name('39_freeze_new_questions.py'))
    F=importlib.util.module_from_spec(s);sys.modules[s.name]=F;s.loader.exec_module(F)
    req={'max_spend_krw':None,'min_cap_krw':5000,'min_percent':10,'cap_period':'month'}
    audit={'core_fragments_pass':True,'spans':[{'field':f,'found_in_ocr_page':True,'fragment':'5천원' if f=='cap' else '근거'} for f in ['target','action','value','cap']]}
    assert not F.required_fragments_pass(req,audit)
    audit['spans'][-1]['fragment']='월 할인한도 5천원'
    assert F.required_fragments_pass(req,audit)

def test_counter_period_source_fragment_rejected():
    s=importlib.util.spec_from_file_location('counter_period_gate',Path(__file__).with_name('39_freeze_new_questions.py'))
    F=importlib.util.module_from_spec(s);sys.modules[s.name]=F;s.loader.exec_module(F)
    req={'max_spend_krw':None,'min_cap_krw':5000,'min_percent':10,'cap_period':'month'}
    audit={'core_fragments_pass':True,'spans':[{'field':f,'found_in_ocr_page':True,'fragment':'근거'} for f in ['target','action','value','cap']]}
    audit['spans'].append({'field':'period','found_in_ocr_page':True,'fragment':'일 한도'})
    assert not F.required_fragments_pass(req,audit)
    audit['spans'][3]['fragment']='월 할인한도 5천원'
    assert not F.required_fragments_pass(req,audit)
