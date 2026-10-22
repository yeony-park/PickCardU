"""Offline checks for experimental prompt changes and answer reuse boundaries."""
import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/rag-core/src'))
spec = importlib.util.spec_from_file_location('generation58', Path(__file__).with_name('58_generation_contract.py'))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


def test_captures_both_actual_operating_attempts_without_provider():
    prompts = C.operating_prompts()
    assert len(prompts) == 2 and prompts[1].startswith(prompts[0])
    assert '이전 출력이 잘렸거나' in prompts[1]
    assert 'claims는 answered 응답에서 1~5개' in prompts[0]


def test_new_prompt_preserves_original_and_retry_instructions():
    old = C.operating_prompts()
    for original in old:
        newer = C.transform_prompt(original, 'new')
        assert newer == original + C.CONDITION_GUIDE
        assert C.transform_prompt(newer, 'new') == newer
        assert C.transform_prompt(original, 'old') == original
    with pytest.raises(ValueError):
        C.transform_prompt(old[0], 'unknown')


def test_reuse_requires_same_question_profile_prompt_and_repeat():
    assert C.reuse_identity('H08:benefit:B:new:r2') == 'H08:benefit:A:new:r2'
    assert C.reuse_identity('H08:benefit:A:new:r2') is None
    with pytest.raises(ValueError):
        C.reuse_identity('H08:benefit:B:old:r2:extra')


def test_diagnostic_rejects_stale_inputs_foreign_cards_and_non_source_text():
    import copy,hashlib,json
    sha=lambda v:hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    source=dict(path='data/raw/a/card.pdf',sha256='pdf',page=1,quote='원문 전체 구역')
    result=dict(cards=[dict(card_key='a/card')],evidence=[dict(chunk_id='source1',card_key='a/card',text=source['quote'])])
    reference_contract=dict(by_query={'q':dict(source_identity='source',source_record_sha256='record',card_keys=['a/card'])})
    review=dict(question_sha256=sha('질문'),result_sha256=sha(result),required_fact_ids=['fact'],
        reference_contract_sha256=sha(reference_contract),
        reviewed_by='main Codex',observation='같은 카드의 직접 요구와 중요 조건을 원문 구역에서 대조',
        verified_required_fact_coverage_complete=True)
    receipts={'q':review}
    data=dict(status='source_excerpts_manually_verified',retrieval_results_sha256='retrieval',source_facts_sha256='facts',
        reference_contract_sha256=sha(reference_contract),
        review_receipts_sha256=sha(receipts),by_query={'q':dict(question_sha256=sha('질문'),result=result,
            reference_identity='source',reference_adjudication_sha256='record',
            source_proofs={'source1':dict(card_key='a/card',excerpt_sha256=sha(source['quote']),source_spans=[source])})})
    seen=[]
    kwargs=dict(questions={'q':'질문'},retrieval_sha='retrieval',source_facts_sha='facts',required_ids={'q':['fact']},
        receipts=receipts,reference_contract=reference_contract,sha=sha,validate_source=lambda s:seen.append(s))
    C.validate_diagnostic(data,**kwargs)
    assert seen==[source]
    with pytest.raises(ValueError,match='seal changed'):
        C.validate_diagnostic(data,**{**kwargs,'retrieval_sha':'changed'})
    foreign=copy.deepcopy(data);foreign['by_query']['q']['source_proofs']['source1']['source_spans'][0]['path']='data/raw/b/other.pdf'
    with pytest.raises(ValueError,match='another card'):
        C.validate_diagnostic(foreign,**kwargs)
    invented=copy.deepcopy(data);invented['by_query']['q']['source_proofs']['source1']['source_spans'][0]['quote']='채점 정답 주석'
    with pytest.raises(ValueError,match='non-source annotations'):
        C.validate_diagnostic(invented,**kwargs)
    wrong=copy.deepcopy(data);wrong['by_query']['q']['reference_identity']='other-profile'
    with pytest.raises(ValueError,match='fixed SEQ source'):
        C.validate_diagnostic(wrong,**kwargs)


def test_source_diagnostic_prefers_verifiable_seq_reference_and_benefit_on_tie():
    rows=[dict(identity='benefit',config_id='card_page_section_benefit:SEQ',required_fact_ids=['unknown']),
          dict(identity='parent',config_id='parent_child_bundle:SEQ',required_fact_ids=['known'])]
    facts={'unknown':dict(source_status='uncertain',card_key='a'),'known':dict(source_status='verified',card_key='b')}
    assert C.diagnostic_reference(rows,facts,str)['source_config_id']=='parent_child_bundle:SEQ'
    facts['unknown']['source_status']='verified'
    assert C.diagnostic_reference(rows,facts,str)['source_config_id']=='card_page_section_benefit:SEQ'
