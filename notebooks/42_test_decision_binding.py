"""Regression: a stale or contradictory decision must not be published."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location('decision42_report', Path(__file__).with_name('42_render_report.py'))
V = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = V
spec.loader.exec_module(V)

def inputs():
    path = V.R.STAGES[42]
    values = [V.R.read(path / name) for name in ['evaluation_summary.json', 'quality_review.json', 'decision.json']]
    summary, review, decision = values
    sha = V.R.digest((path / 'combined_results.json').read_bytes())
    decision['binding'] = {'combined_results_sha256':sha, 'evaluation_summary_sha256':V.R.digest(summary),
                           'quality_review_sha256':V.R.digest(review)}
    return summary, review, decision, sha

def test_current_decision_matches_results():
    assert V.validate_decision(*inputs()) is True

@pytest.mark.parametrize('mutation', ['stale_review', 'stale_results', 'wrong_score', 'wrong_pair', 'production',
                                      'equal_score_wrong_selection', 'enrichment', 'semantic_superiority', 'decision_code'])
def test_stale_or_contradictory_decision_is_rejected(mutation):
    summary, review, decision, sha = inputs()
    if mutation == 'stale_review':
        review['records'][0]['finding_ko'] += 'changed'
    elif mutation == 'stale_results':
        sha = '0' * 64
    elif mutation == 'wrong_score':
        decision['raw_best']['complete'] += 1
    elif mutation == 'wrong_pair':
        decision['baseline_paired_raw']['wins'] = []
    elif mutation == 'production':
        decision['production_adopted'] = True
    elif mutation == 'equal_score_wrong_selection':
        decision['selected_configuration']['mode'] = 'Q_EXPAND_U'
    elif mutation == 'enrichment':
        decision['enrichment_selected'] = True
    elif mutation == 'semantic_superiority':
        decision['semantic_superiority_confirmed'] = True
    else:
        decision['decision'] = 'promote_enrichment'
    with pytest.raises(RuntimeError):
        V.validate_decision(summary, review, decision, sha)
