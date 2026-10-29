import importlib.util
from pathlib import Path
import copy
import pytest

spec=importlib.util.spec_from_file_location('evaluation_test45',Path(__file__).with_name('45_evaluate_results.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)


@pytest.mark.parametrize('outcomes,want',[
    (['win','loss','win'],'A'),(['tie','tie'],'A'),
    (['win','tie'],'B_development_candidate'),(['win','uncertain'],'A')])
def test_selection_never_hides_a_loss_or_uncertainty_in_the_average(outcomes,want):
    assert M.decide(outcomes)==want


def test_saved_baseline_scoring_uses_gold_plan_only_in_evaluation():
    dataset=M.I.read(M.I.DATA/'42_expanded_query_evaluation/dataset.json')
    combined=M.I.read(M.I.DATA/'42_expanded_query_evaluation/combined_results.json')
    answer=next(r for r in combined['answers'] if r['mode']=='S_THEN_U')
    retrieval=next(r for r in combined['retrievals'] if (r['query_id'],r['profile'],r['mode'])==
                   (answer['query_id'],answer['profile'],answer['mode']))
    label=next(r for r in dataset['queries'] if r['query_id']==answer['query_id'])
    case=M.evaluation_case(label)
    assert M.RUBRIC.score(case,answer['answer'],retrieval['result']['evidence'])==answer['scores']
    assert 'plan' not in label


@pytest.mark.parametrize('mutation',['unknown_side','wrong_source','invented_quote','empty_citation','invalid_kind'])
def test_source_review_rejects_unbound_or_unverifiable_findings(mutation):
    review=copy.deepcopy(M.I.read(M.I.OUT/'source_reviews_root.json')['records'][0])
    packet=M.I.read(M.I.OUT/'review_packets'/f"{review['packet_id']}.json")
    finding=review['findings'][0]
    if mutation=='unknown_side':finding['side']='middle'
    if mutation=='wrong_source':finding['source_path']=next(v for v in packet['original_source_paths'].values() if v!=finding['source_path'])
    if mutation=='invented_quote':finding['source_quote']='THIS QUOTE DOES NOT EXIST IN THE OCR SOURCE'
    if mutation=='empty_citation':finding['citations']=[]
    if mutation=='invalid_kind':finding['kind']='looks_better'
    with pytest.raises(RuntimeError):M.validate_review(review,packet)


def test_raw_response_reparse_accepts_sdk_dump_field_names():
    audit=M.I.load('audit45_alias_test','45_completion_audit.py')
    cached=M.I.read(next((M.I.OUT/'api_cache').glob('*.json')))
    evidence_count=len(__import__('json').loads(cached['request']['input'][0]['content'])['evidence'])
    assert audit.parse_saved_response(cached['raw_response'],evidence_count).model_dump(mode='json')==cached['parsed']
