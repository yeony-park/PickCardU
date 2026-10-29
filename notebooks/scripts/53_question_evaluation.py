"""Shared offline validation for 53/54 question and source-coverage judgments.

Two notebooks use this small helper so the same acceptance rules can receive
regression tests. It does not judge prose, call an API, or generate reports.
"""


def validate_judgment(judgment, rubric, sha):
    assert judgment['rubric_sha256'] == sha(rubric)
    assert judgment['question_verdict'] in ['PASS', 'PARTIAL', 'FAIL', 'UNCERTAIN']
    assert judgment['verdict'] == judgment['question_verdict']
    assert all(type(judgment[k]) is bool for k in
               ['important_error', 'material_omission', 'scope_error'])
    rates = {}
    for field, expected in [('must_answer_coverage', rubric['must_answer']),
                            ('annotation_coverage', rubric['context_anchor'])]:
        coverage = judgment[field]
        assert {'covered', 'missing', 'not_applicable'} <= set(coverage)
        assert set(coverage) <= {'covered', 'missing', 'not_applicable', 'uncertain'}
        flat = [item for values in coverage.values() for item in values]
        assert len(flat) == len(set(flat)) and set(flat) == set(expected)
        applicable = len(coverage['covered']) + len(coverage['missing'])
        rates[field] = len(coverage['covered']) / applicable if applicable else None
    if judgment['question_verdict'] == 'PASS':
        assert not judgment['must_answer_coverage']['missing']
        assert not judgment['must_answer_coverage']['not_applicable']
        assert not judgment['must_answer_coverage'].get('uncertain')
        assert not any(judgment[k] for k in ['important_error', 'material_omission', 'scope_error'])
    return rates['annotation_coverage']
