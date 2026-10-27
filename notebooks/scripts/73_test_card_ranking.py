"""Ranking metric boundaries for the fixed 73 candidate pool."""
import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location('ranking73', Path(__file__).with_name('73_card_ranking.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_rank_metrics_use_card_rank_and_complete_judged_pool():
    score = module().score(['bad', 'good', 'other'], {'bad': False, 'good': True, 'other': True}, 3)
    assert score['hit'] == 1
    assert score['mrr'] == 0.5
    assert score['recall_judged_pool'] == 1
    assert score['ndcg_judged_pool'] == pytest.approx((1 / __import__('math').log2(3) + 0.5) / (1 + 1 / __import__('math').log2(3)))


def test_unjudged_rank_is_not_assumed_incorrect():
    score = module().score(['unknown', 'good'], {'good': True}, 3)
    assert (score['hit_lower'], score['hit_upper']) == (1, 1)
    assert (score['mrr_lower'], score['mrr_upper']) == (0.5, 1)
    assert score['ndcg_judged_pool'] is None
    assert score['recall_judged_pool'] is None


def test_duplicate_cards_and_false_top_k_are_rejected():
    m = module()
    with pytest.raises(ValueError):
        m.score(['a', 'a'], {'a': True}, 3)
    with pytest.raises(ValueError):
        m.score(['a'], {'a': True}, 5)


def test_no_positive_ground_truth_is_not_reported_as_recall_zero():
    score = module().score(['bad'], {'bad': False}, 3)
    assert score['hit'] == 0 and score['mrr'] == 0
    assert score['recall_judged_pool'] is None and score['ndcg_judged_pool'] is None
