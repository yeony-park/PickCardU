"""Card-ranking metrics over a clearly scoped, manually judged candidate pool."""
import math


def score(ranked, labels, k=3):
    if k != 3 or len(ranked) > k or len(ranked) != len(set(ranked)):
        raise ValueError('Use the frozen top-three unique cards; top-five was not captured')
    if any(v is not None and type(v) is not bool for v in labels.values()):
        raise ValueError('Labels are relevant, irrelevant, or unjudged')
    statuses = [labels.get(card) for card in ranked]
    known_ranks = [i for i, v in enumerate(statuses, 1) if v is True]
    possible_ranks = [i for i, v in enumerate(statuses, 1) if v is not False]
    lower_hit, upper_hit = int(bool(known_ranks)), int(bool(possible_ranks))
    lower_mrr = 1 / min(known_ranks) if known_ranks else 0.0
    upper_mrr = 1 / min(possible_ranks) if possible_ranks else 0.0
    positives = sum(v is True for v in labels.values())
    complete = all(card in labels for card in ranked) and all(v is not None for v in labels.values())
    if complete and positives:
        recall = len(known_ranks) / positives
        dcg = sum(1 / math.log2(rank + 1) for rank in known_ranks)
        ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, positives) + 1))
        ndcg = dcg / ideal
    else:
        recall = ndcg = None
    return dict(hit_lower=lower_hit, hit_upper=upper_hit,
                hit=lower_hit if lower_hit == upper_hit else None,
                mrr_lower=lower_mrr, mrr_upper=upper_mrr,
                mrr=lower_mrr if lower_mrr == upper_mrr else None,
                recall_judged_pool=recall, ndcg_judged_pool=ndcg,
                pool_positive_count=positives, pool_fully_judged=complete)
