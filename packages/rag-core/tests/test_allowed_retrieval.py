import numpy as np
import pytest

from pickcardu_rag import Chunk, RagPipeline, SearchConfig
from pickcardu_rag.retrieval import Candidate, InMemoryBM25Searcher, InMemorySquaredL2Searcher


def pipeline():
    chunks = [Chunk(f'c{i}', '커피 할인', f'card{i}', f'카드{i}', '발급사', 'benefit', 1) for i in range(12)]
    return RagPipeline(chunks, lexical_searcher=InMemoryBM25Searcher(chunks),
        vector_searcher=InMemorySquaredL2Searcher([c.chunk_id for c in chunks], np.arange(12).reshape(-1, 1),
                                                 card_keys=[c.card_key for c in chunks]))


def test_allowed_cards_exceed_precision_limit_and_filter_before_depth():
    allowed = tuple(f'card{i}' for i in range(6, 12))
    result = pipeline().search('커피', np.zeros(1), SearchConfig(allowed_card_keys=allowed,
        component_depth=2, reranker='off'))
    assert result['cards']
    assert {r['card_key'] for r in result['evidence']} <= set(allowed)
    assert len(result['trace']['vector']) == 2
    assert all(int(r['chunk_id'][1:]) >= 6 for r in result['trace']['vector'])


def test_empty_allowed_scope_returns_no_cards():
    result = pipeline().search('커피', np.zeros(1), SearchConfig(allowed_card_keys=(), reranker='off'))
    assert not result['cards'] and not result['evidence']


def test_allowed_scope_cannot_remove_required_precision_target():
    with pytest.raises(ValueError):
        pipeline().search('커피', np.zeros(1), SearchConfig(target_card_keys=('card1', 'card2'),
            allowed_card_keys=('card1',), reranker='off'))


def test_allowed_scope_applies_to_both_wallet_groups():
    result = pipeline().search('커피', np.zeros(1), SearchConfig(wallet_operation='compare',
        wallet_card_keys=('card1', 'card2'), new_card_keys=('card7', 'card8'),
        allowed_card_keys=('card2', 'card8'), reranker='off'))
    assert {r['card_key'] for r in result['evidence']} == {'card2', 'card8'}
