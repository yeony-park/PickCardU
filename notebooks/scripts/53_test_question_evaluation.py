"""Question resolution and optional source-anchor coverage remain separate."""
import importlib.util
from pathlib import Path

import pytest


def validator():
    path = Path(__file__).with_name('53_question_evaluation.py')
    spec = importlib.util.spec_from_file_location('question_evaluation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_judgment


def sample():
    rubric = dict(must_answer=['월2회', '실적20만원', '한도3000원'], context_anchor=['10%', '입점제외'])
    judgment = dict(question_verdict='PASS', verdict='PASS', important_error=False,
                    material_omission=False, scope_error=False, rubric_sha256='rubric',
                    must_answer_coverage=dict(covered=rubric['must_answer'], missing=[], not_applicable=[]),
                    annotation_coverage=dict(covered=[], missing=rubric['context_anchor'], not_applicable=[]))
    return rubric, judgment


def test_optional_anchor_omissions_do_not_fail_a_correct_question_answer():
    rubric, judgment = sample()
    assert validator()(judgment, rubric, lambda _: 'rubric') == 0


@pytest.mark.parametrize('mutation', ['must_missing', 'must_uncertain', 'material', 'error', 'scope', 'rubric_hash', 'coverage_overlap', 'verdict_alias'])
def test_primary_pass_cannot_hide_material_or_binding_failures(mutation):
    rubric, judgment = sample()
    if mutation == 'must_missing':
        judgment['must_answer_coverage']['missing'] = [judgment['must_answer_coverage']['covered'].pop()]
    elif mutation == 'must_uncertain':
        judgment['must_answer_coverage']['uncertain'] = [judgment['must_answer_coverage']['covered'].pop()]
    elif mutation == 'material': judgment['material_omission'] = True
    elif mutation == 'error': judgment['important_error'] = True
    elif mutation == 'scope': judgment['scope_error'] = True
    elif mutation == 'rubric_hash': judgment['rubric_sha256'] = 'stale'
    elif mutation == 'coverage_overlap': judgment['annotation_coverage']['covered'] = ['10%']
    else: judgment['verdict'] = 'PARTIAL'
    with pytest.raises(AssertionError):
        validator()(judgment, rubric, lambda _: 'rubric')
