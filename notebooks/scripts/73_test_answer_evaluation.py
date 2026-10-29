"""Bound visible-answer decisions; these checks do not infer semantic truth."""
import copy
import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location('answer_eval73_test', Path(__file__).with_name('73_answer_evaluation.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def fixture():
    m = module()
    job = dict(identity='C01:p:SEQ_CARDS:r1', query_id='C01', profile='p', mode='SEQ_CARDS', repeat=1,
               question='커피 할인 카드?', result={'cards': [], 'evidence': []})
    row = dict(identity=job['identity'], status='ok', answer={'answer_text': '커피 할인 카드입니다.',
               'recommendations': [], 'claims': [{'text': '화면에 없는 설명'}]})
    note = dict(answer_sha256=m.digest(row), job_sha256=m.digest(job), question=job['question'], direct=True,
                accuracy='correct', input_support='supported', reason='원문과 입력에서 커피 할인 확인', issues=[],
                visible_surfaces={'answer_text': '커피 할인 카드입니다.'}, reviewer='manual', technical_success=True)
    return m, job, row, note


def test_unasked_optional_condition_does_not_fail_direct_answer():
    m, job, row, note = fixture()
    assert m.score(job, row, note)['passed']


def test_hidden_claim_cannot_fill_missing_visible_answer():
    m, job, row, note = fixture()
    note['direct'] = False
    assert not m.score(job, row, note)['passed']


def test_source_unknown_and_confirmed_error_are_separate_failures():
    m, job, row, note = fixture()
    note['accuracy'] = 'uncertain'
    uncertain = m.score(job, row, note)
    note['accuracy'] = 'wrong'; note['issues'] = ['wrong_rate']
    wrong = m.score(job, row, note)
    assert not uncertain['passed'] and not uncertain['confirmed_wrong'] and uncertain['uncertain']
    assert not wrong['passed'] and wrong['confirmed_wrong'] and not wrong['uncertain']


def test_changed_answer_or_unreviewed_surface_invalidates_manual_note():
    m, job, row, note = fixture()
    altered = copy.deepcopy(row); altered['answer']['answer_text'] = '다른 내용'
    with pytest.raises(ValueError, match='binding'):
        m.score(job, altered, note)
    note['visible_surfaces'] = {}
    with pytest.raises(ValueError, match='surface'):
        m.score(job, row, note)


def test_technical_failure_is_kept_in_denominator():
    m, job, row, _ = fixture()
    row.update(status='failed', answer=None)
    result = m.score(job, row, None)
    assert not result['technical'] and not result['passed']
