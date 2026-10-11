"""Scope preservation and same-card diagnostic assembly; no network/model."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
import subprocess

spec=importlib.util.spec_from_file_location('comparison_diagnostics_test',Path(__file__).with_name('36_comparison.py'))
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)


def test_unknown_union_retains_strict_and_marks_uncertain_separately():
    plan={'route':'structured_then_unstructured','groups':[[{'targets':['편의점']}]],'card_keys':[],'unresolved':[]}
    result=C.unknown_policy(plan,{'eligible_cards':['b','a'],'unknown_cards':['c','b']})
    assert result['card_keys']==['a','b','c']
    assert result['uncertain_only']==['c']
    assert result['uncertain_cards_are_confirmed_matches'] is False


def test_unresolved_or_unstructured_plan_does_not_apply_unknown_filter():
    selection={'eligible_cards':[],'unknown_cards':['a','b']}
    for plan in [{'route':'unstructured','groups':[],'unresolved':[]},
                 {'route':'structured_then_unstructured','groups':[[{}]],'unresolved':['모호함']}]:
        assert C.unknown_policy(plan,selection)['applicable'] is False


def test_unknown_union_keeps_explicit_card_scope():
    plan={'route':'structured_then_unstructured','groups':[[{}]],'card_keys':['a'],'unresolved':[]}
    result=C.unknown_policy(plan,{'eligible_cards':['a'],'unknown_cards':['foreign']})
    assert result['card_keys']==['a'] and result['uncertain_only']==[]


def test_card_union_texts_do_not_mix_foreign_cards_and_keep_single_spans():
    evidence=[{'card_key':'a','text':'편의점10% 할인'}, {'card_key':'a','text':'전월실적30만원'},
              {'card_key':'b','text':'다른 카드 월5만원 한도'}]
    texts=C.union_texts(evidence,'a')
    assert texts[:2]==['편의점10% 할인','전월실적30만원']
    assert '편의점10% 할인' in texts[-1] and '전월실적30만원' in texts[-1]
    assert all('다른 카드' not in text for text in texts)
    assert C.union_texts(evidence,'absent')==[]


def test_component_resume_reuses_saved_capture_and_rejects_identity_change(tmp_path):
    class Experiment:
        def __init__(self):
            self.components={};self.captures=0
            self.base=SimpleNamespace(index_identity={'stable':'index'}, data={
                'card_page_section_benefit':([SimpleNamespace(chunk_id='c',card_key='a')],)})
        def component_key(self,*args):return 'test-component'
        def capture_component(self,*args):
            self.captures+=1
            return {'bm25':[{'chunk_id':'c','rank':1}], 'vector':[], 'provenance':'test capture'}
    B=SimpleNamespace(query_identity=C.F.digest)
    path=tmp_path/'component.json'
    first=Experiment()
    reference=C.freeze_unknown_component(first,B,'question',[1.0],'card_page_section_benefit',
        ['a'],{'sha256':'protocol'},path=path)
    content=path.read_bytes();second=Experiment()
    again=C.freeze_unknown_component(second,B,'question',[1.0],'card_page_section_benefit',
        ['a'],{'sha256':'protocol'},path=path)
    assert first.captures==1 and second.captures==0
    assert reference==again and path.read_bytes()==content
    with pytest.raises(RuntimeError,match='identity'):
        C.freeze_unknown_component(Experiment(),B,'changed',[1.0],'card_page_section_benefit',
            ['a'],{'sha256':'protocol'},path=path)


def test_chunk_lookup_rejects_duplicate_ids_before_dictionary_creation():
    with pytest.raises(RuntimeError,match='Duplicate chunk'):
        C.unique_chunks([{'chunk_id':'same'},{'chunk_id':'same'}])
    assert list(C.unique_chunks([{'chunk_id':'one'},{'chunk_id':'two'}]))==['one','two']


@pytest.mark.parametrize('name',['34_verify_bundle_results.py','35_verify_answer_results.py','36_verify_followups.py'])
def test_optimized_python_cannot_load_assert_based_verifier(name):
    path=Path(__file__).with_name(name)
    probe=subprocess.run([sys.executable,'-B','-O','-c',
        'import runpy,sys; runpy.run_path(sys.argv[1],run_name="optimized_verifier_import_probe")',str(path)],
        capture_output=True,text=True,check=False)
    assert probe.returncode!=0
    assert 'Verification requires assertions enabled' in probe.stderr


def test_quality_inspection_contract_rejects_new_judge_scores():
    V=C.F.load_module('inspection_contract_test',Path(__file__).with_name('36_verify_followups.py'))
    V.require_inspection_contract({'old_scores_changed':False,'new_truth_or_judge_scores':False})
    with pytest.raises(RuntimeError,match='inspection contract'):
        V.require_inspection_contract({'old_scores_changed':False,'new_truth_or_judge_scores':True})
