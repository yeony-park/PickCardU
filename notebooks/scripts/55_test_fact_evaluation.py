"""Regression checks for the fact-level denominators shared by experiments55–59."""
import copy
import importlib.util
from pathlib import Path

import pytest


def evaluator():
    path = Path(__file__).with_name('55_fact_evaluation.py')
    spec = importlib.util.spec_from_file_location('fact_evaluation55', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    return dict(identity='Q1:benefit:SEQ:r1', query_id='Q1', config_id='benefit:SEQ',
        original_verdict='PARTIAL', required_fact_ids=['rate', 'spend'],
        binding=dict(retrieval_sha256='r', input_sha256='i', answer_sha256='a'),
        facts=[dict(fact_id='rate', card_key='issuer/card', kind='required', source_status='verified',
            reason='원문 할인율 확인', source_support=[dict(path='card.pdf', sha256='s', page=1, quote='커피10%')],
            retrieved=dict(status='supported', quotes=['커피10%']),
            input=dict(status='supported', quotes=['커피10%']),
            answer=dict(status='supported', quotes=['커피10%'])),
            dict(fact_id='spend', card_key='issuer/card', kind='required', source_status='verified',
            reason='원문 전월실적 확인, 검색에서 누락', source_support=[dict(path='card.pdf', sha256='s', page=1, quote='전월30만원')],
            retrieved=dict(status='absent', quotes=[]), input=dict(status='absent', quotes=[]),
            answer=dict(status='absent', quotes=[]))])


def score(row=None, **kwargs):
    return evaluator().score_row(row or fixture(), retrieved_text={'issuer/card': '커피 10%'},
        input_text={'issuer/card': '커피 10%'}, answer_text={'issuer/card': '커피 10%'},
        expected_binding=dict(retrieval_sha256='r', input_sha256='i', answer_sha256='a'), **kwargs)


def test_answer_reflection_cannot_hide_missing_retrieved_condition():
    result = score()
    assert result['input_recall'] == .5
    assert result['answer_reflection'] == 1
    assert result['end_to_end_fulfillment'] == .5
    assert not result['complete_input']
    assert result['retrieval_gaps'] == ['spend']


def test_condition_present_in_input_but_omitted_is_generation_failure():
    row = fixture()
    for key in ['retrieved', 'input']:
        row['facts'][1][key] = dict(status='supported', quotes=['전월30만원'])
    result = evaluator().score_row(row, retrieved_text={'issuer/card': '커피10% 전월30만원'},
        input_text={'issuer/card': '커피10% 전월30만원'}, answer_text={'issuer/card': '커피10%'}, expected_binding=row['binding'])
    assert result['input_recall'] == 1 and result['answer_reflection'] == .5
    assert result['generation_omissions'] == ['spend']


def test_correct_answer_without_input_support_is_not_grounded_reflection():
    row = fixture(); row['facts'][1]['answer'] = dict(status='supported', quotes=['전월30만원'])
    result = evaluator().score_row(row, retrieved_text={'issuer/card': '커피10%'}, input_text={'issuer/card': '커피10%'},
        answer_text={'issuer/card': '커피10% 전월30만원'}, expected_binding=row['binding'])
    assert result['end_to_end_fulfillment'] == 1
    assert result['answer_reflection'] == 1
    assert result['unsupported_required_answers'] == ['spend']


def test_zero_denominators_are_unmeasured_and_not_complete():
    row = fixture(); row['required_fact_ids'] = []; row['facts'] = []
    result = score(row)
    assert result['input_recall'] is None and result['answer_reflection'] is None
    assert result['complete_input'] is None


@pytest.mark.parametrize('change', ['binding', 'missing_fact', 'invented_quote', 'unreviewed'])
def test_unbound_or_unreviewed_judgments_cannot_be_scored(change):
    row = fixture()
    if change == 'binding': row['binding']['answer_sha256'] = 'stale'
    elif change == 'missing_fact': row['facts'].pop()
    elif change == 'invented_quote': row['facts'][0]['input']['quotes'] = ['전월실적없음']
    else: row['facts'][0]['input']['status'] = 'unreviewed'
    with pytest.raises(ValueError): score(row)


def test_unverified_source_is_reported_without_becoming_a_positive_fact():
    row = fixture(); row['facts'][1]['source_status'] = 'uncertain'
    result = score(row)
    assert result['source_uncertain_fact_ids'] == ['spend']
    assert result['source_denominator'] == 1
    assert result['input_recall'] == 1
    assert result['complete_input'] is False


def test_context_details_do_not_change_required_fact_recall():
    row = fixture(); extra = copy.deepcopy(row['facts'][1]); extra['fact_id'] = 'annual_fee'
    extra['kind'] = 'context'; row['facts'].append(extra)
    result = score(row)
    assert result['source_denominator'] == 2 and result['input_recall'] == .5


def test_missing_input_has_a_separate_transport_diagnosis():
    row = fixture(); row['facts'][0]['input'] = dict(status='absent', quotes=[])
    assert score(row)['transport_gaps'] == ['rate']


def test_macro_aggregation_does_not_treat_repeats_as_independent_questions():
    E = evaluator(); a = score(); b = copy.deepcopy(a); c = copy.deepcopy(a)
    b.update(identity='Q1:benefit:SEQ:r2', input_recall=1)
    c.update(identity='Q2:benefit:SEQ:r1', query_id='Q2', input_recall=0)
    result = E.aggregate([a, b, c])[0]
    assert result['question_denominator'] == 2 and result['logical_rows'] == 3
    assert result['macro']['input_recall'] == .375


def test_duplicate_rows_cannot_inflate_denominators():
    with pytest.raises(ValueError): evaluator().aggregate([score(), score()])


def test_another_cards_matching_number_is_not_fact_support():
    row = fixture()
    with pytest.raises(ValueError, match='bound text'):
        evaluator().score_row(row, retrieved_text={'other/card': '커피10%'},
            input_text={'issuer/card': '커피10%'}, answer_text={'issuer/card': '커피10%'},
            expected_binding=row['binding'])


def test_duplicate_or_missing_experiment_condition_is_rejected():
    E = evaluator()
    expected = {'a', 'b'}
    for rows in [['a', 'a'], ['a'], ['a', 'c']]:
        with pytest.raises(ValueError, match='identity'):
            E.validate_identities(rows, expected)
    E.validate_identities(['b', 'a'], expected)


def test_changed_results_cannot_replace_sealed_results():
    E = evaluator()
    summary = dict(execution_results_sha256='e', source_judgments_sha256='j')
    completion = dict(evaluation_summary_sha256='s', question_source_judgments_sha256='j')
    E.validate_seal(summary, completion, execution_sha='e', judgments_sha='j', summary_sha='s')
    for kwargs in [dict(execution_sha='changed', judgments_sha='j', summary_sha='s'),
                   dict(execution_sha='e', judgments_sha='changed', summary_sha='s'),
                   dict(execution_sha='e', judgments_sha='j', summary_sha='changed')]:
        with pytest.raises(ValueError, match='seal'):
            E.validate_seal(summary, completion, **kwargs)


def test_source_quote_must_exist_on_the_bound_pdf_page(tmp_path):
    import fitz, hashlib
    E = evaluator(); path = tmp_path/'card.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((72,72), 'Coffee discount 10 percent')
        doc.save(path)
    source = dict(path='card.pdf', sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  page=1, quote='Coffee discount 10 percent')
    E.validate_source_support(source, tmp_path)
    with pytest.raises(ValueError, match='quote'):
        E.validate_source_support({**source, 'quote':'Coffee discount 90 percent'}, tmp_path)


def test_multi_card_prose_is_not_copied_between_cards():
    E = evaluator()
    answer = dict(answer_text='B card coffee 10 percent', claims=[
        dict(card_key='a', text='coffee benefit'), dict(card_key='b', text='coffee 10 percent')])
    texts = E.answer_by_card(answer, {'a', 'b'})
    assert '10 percent' not in texts['a']
    assert '10 percent' in texts['b']
    with pytest.raises(ValueError, match='span'):
        E.answer_by_card(answer, {'a', 'b'}, [dict(card_key='b', start=0, end=23, reason='B explicit'),
                                            dict(card_key='a', start=0, end=23, reason='wrong reuse')])


def test_manual_image_support_requires_bound_review_and_exact_reviewed_quote(tmp_path):
    import fitz, hashlib, json
    E = evaluator(); path=tmp_path/'card.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((72,72),'Coffee 10 percent')
        doc.save(path)
    source=dict(path='card.pdf',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),page=1,
                quote='manually transcribed coffee benefit',mode='original_pdf_image',
                manual_review_path='review.json',manual_review_id='page1')
    with pytest.raises((ValueError,FileNotFoundError)):
        E.validate_source_support(source,tmp_path)
    with fitz.open(path) as doc: png=doc[0].get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
    (tmp_path/'page.png').write_bytes(png)
    record=dict(review_id='page1',pdf_path='card.pdf',pdf_sha256=source['sha256'],page=1,
                render_scale=2,image_path='page.png',image_sha256=hashlib.sha256(png).hexdigest(),
                reviewed_by='main Codex',reviewed_at='2026-10-23',visually_reviewed=True,
                quotes=[source['quote']],observation='Original image inspected; text confirmed manually')
    source['manual_record_sha256']=hashlib.sha256(json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    (tmp_path/'review.json').write_text(json.dumps(dict(records=[record])))
    E.validate_source_support(source,tmp_path)
    for change in [dict(quote='unreviewed number 90'),dict(page=2)]:
        with pytest.raises(ValueError):E.validate_source_support({**source,**change},tmp_path)
    record['observation']='changed after adjudication'
    (tmp_path/'review.json').write_text(json.dumps(dict(records=[record])))
    with pytest.raises(ValueError):E.validate_source_support(source,tmp_path)
    record['image_sha256']='wrong'
    source['manual_record_sha256']=hashlib.sha256(json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    (tmp_path/'review.json').write_text(json.dumps(dict(records=[record])))
    with pytest.raises(ValueError):E.validate_source_support(source,tmp_path)


def test_material_audit_cannot_omit_or_reuse_unbound_recommendations():
    E=evaluator();binding={'answer_sha256':'a'}
    row=dict(identity='Q1:SEQ',card_key='card1',binding=binding,review_status='reviewed',
             actual_role='recommendation',checked_material_conditions=['minimum spend reviewed'],findings=[],uncertainties=[])
    E.validate_material_audit([row],{('Q1:SEQ','card1')},{'Q1:SEQ':binding})
    for records in [[],[row,row],[{**row,'review_status':'pending'}],
                    [{**row,'binding':{'answer_sha256':'stale'}}]]:
        with pytest.raises(ValueError):
            E.validate_material_audit(records,{('Q1:SEQ','card1')},{'Q1:SEQ':binding})
