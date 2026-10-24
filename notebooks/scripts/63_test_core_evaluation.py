import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('core63_test', Path(__file__).with_name('63_core_evaluation.py'))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def condition(**changes):
    return dict(source_status='verified', input_status='supported', answer_status='supported', **changes)


def row():
    return dict(dataset='62', identity='q:p:SEQ:r1', query_id='q', profile='p', mode='SEQ', repeat=1,
                group='explicit', core_paths=[dict(card_keys=['a'], requirements=[condition()])],
                core_evidence_complete=True, core_grounded_success=True, legacy_pass=False,
                extended_conditions=[], critical_error_ids=[], false_certainty=False, scope_violation=False)


def test_card_hit_cannot_replace_missing_input_condition():
    r = row(); r['core_paths'][0]['requirements'][0]['input_status'] = 'absent'
    r.update(core_evidence_complete=False, core_grounded_success=False)
    assert not S.score(r)['core_grounded_success']


def test_different_cards_cannot_complete_same_card_and():
    r = row()
    r['core_paths'] = [dict(card_keys=[card], requirements=[condition(),
                       dict(source_status='verified', input_status='absent', answer_status='absent')])
                       for card in ['a', 'b']]
    r.update(core_evidence_complete=False, core_grounded_success=False)
    assert not S.score(r)['core_evidence_complete']


def test_complete_candidate_does_not_imply_answer_selected_it():
    r = row(); r['core_paths'][0]['requirements'][0]['answer_status'] = 'absent'
    r['core_grounded_success'] = False
    result = S.score(r)
    assert result['core_evidence_complete'] and not result['core_grounded_success']


def test_legacy_pass_is_preserved_and_empty_extended_is_not_success():
    result = S.score(row())
    assert result['core_grounded_success'] and not result['legacy_pass']
    assert result['extended_completeness'] is None


def test_unexplained_manual_success_is_rejected():
    r = row(); r['core_paths'][0]['requirements'][0]['answer_status'] = 'absent'
    with pytest.raises(ValueError, match='disagrees'):
        S.score(r)


def test_uncertain_safe_abstention_is_separate_from_positive_retrieval():
    r = row(); r.update(group='uncertain', core_paths=[], appropriate_abstention=True,
                        core_evidence_complete=None)
    result = S.score(r)
    assert result['core_evidence_complete'] is None and result['core_grounded_success']


def test_bootstrap_preserves_query_blocks():
    rows = []
    for q, baseline, candidate in [('a', [0, 0, 0], [1, 1, 1]), ('b', [1, 1, 1], [0, 0, 0])]:
        for mode, outcomes in [('U', baseline), ('SEQ', candidate)]:
            for repeat, value in enumerate(outcomes, 1):
                rows.append(dict(query_id=q, mode=mode, repeat=repeat, passed=value))
    result = S.paired(rows, 'U', 'SEQ', metric='passed')
    assert result['questions'] == 2 and result['mean_difference'] == 0
    assert result['wins'] == result['losses'] == 1
    assert result == S.paired(rows, 'U', 'SEQ', metric='passed')


def test_bound_condition_rejects_another_cards_pdf_or_input():
    c = dict(condition_id='coffee', reason='reviewed', source_status='verified',
             source_support=[{'sha256': 'other'}], input_status='supported', input_quotes=['30만원'],
             answer_status='supported', answer_quotes=['30만원'])
    with pytest.raises(ValueError, match='source ownership'):
        S.validate_bound_condition(c, 'card-a', {'card-a': '30만원'}, {'card-a': '30만원'}, {'card-a': 'expected'})
    c['source_support'][0]['sha256'] = 'expected'
    with pytest.raises(ValueError, match='input quotation'):
        S.validate_bound_condition(c, 'card-a', {'card-a': '다른 근거', 'card-b': '30만원'},
                                   {'card-a': '30만원'}, {'card-a': 'expected'})


def test_bound_condition_rejects_wrong_answer_ownership():
    c = dict(condition_id='coffee', reason='reviewed', source_status='verified',
             source_support=[{'sha256': 'expected'}], input_status='supported', input_quotes=['30만원'],
             answer_status='supported', answer_quotes=['30만원'])
    with pytest.raises(ValueError, match='answer quotation'):
        S.validate_bound_condition(c, 'card-a', {'card-a': '30만원'}, {'card-a': '확인 불가'}, {'card-a': 'expected'})


def test_direct_answer_and_unsafe_extra_numeric_omission_are_separate():
    r = row()
    r['core_assertion_guard_conditions'] = [dict(source_status='verified', input_status='supported', answer_status='absent')]
    r.update(core_assertion_guard_complete=False, core_grounded_success=False)
    result = S.score(r)
    assert result['core_direct_supported'] is True
    assert result['core_evidence_complete'] is True
    assert not result['core_grounded_success'] and not result['core_assertion_guard_complete']


def test_difficult_positive_question_cannot_be_relabelled_uncertain():
    r = row(); r['group'] = 'uncertain'
    with pytest.raises(ValueError, match='question group'):
        S.validate_groups([r], {'q': 'explicit'})
