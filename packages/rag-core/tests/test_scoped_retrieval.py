import unittest

import numpy as np

from pickcardu_rag import Chunk, InMemoryBM25Searcher, InMemorySquaredL2Searcher, RagPipeline, SearchConfig


class ScopedRetrievalTest(unittest.TestCase):
    def test_ineligible_chunks_do_not_exhaust_scoped_fused_worklist(self):
        for card_count, page_count in ((5, 10), (2, 25)):
            with self.subTest(card_count=card_count):
                keys = tuple(f'card{i}' for i in range(card_count))
                chunks = [Chunk(f'{key}_{i}', '전월실적', key, key, '발급사',
                                'page' if i < page_count else 'benefit', 1)
                          for key in keys for i in range(page_count + 1)]
                vectors = np.asarray([[float(i), 0.] for key in keys for i in range(page_count + 1)])
                pipeline = RagPipeline(chunks, InMemoryBM25Searcher(chunks),
                                       InMemorySquaredL2Searcher([c.chunk_id for c in chunks], vectors,
                                                                 card_keys=[c.card_key for c in chunks]))
                result = pipeline.search('전월실적', np.zeros(2), SearchConfig(
                    vector_weight=1, reranker='off', target_card_keys=keys))
                self.assertEqual({e['chunk_id'] for e in result['evidence']},
                                 {f'{key}_{page_count}' for key in keys})
                self.assertEqual(result['trace']['scope']['missing_card_keys'], [])

    def test_targets_beyond_global_cutoff_have_independent_candidates(self):
        chunks = [Chunk(f'x{i}', '전월실적 없음', 'other', '다른 카드', '발급사', 'benefit', 1) for i in range(60)]
        chunks += [Chunk('a', '전월실적 30만원', 'a', 'A', '발급사', 'benefit', 1),
                   Chunk('b', '전월실적 50만원', 'b', 'B', '발급사', 'benefit', 1)]
        vectors = np.asarray([[0., 0.]] * 60 + [[1., 1.], [2., 2.]])
        pipeline = RagPipeline(chunks, InMemoryBM25Searcher(chunks),
                               InMemorySquaredL2Searcher([c.chunk_id for c in chunks], vectors,
                                                         card_keys=[c.card_key for c in chunks]))
        result = pipeline.search('전월실적', np.zeros(2), SearchConfig(reranker='off', top_k=1,
                                 component_depth=2, candidate_depth=2, target_card_keys=('a', 'b')))
        self.assertEqual([c['card_key'] for c in result['cards']], ['a', 'b'])
        self.assertEqual({e['chunk_id'] for e in result['evidence']}, {'a', 'b'})
        self.assertEqual(result['trace']['scope']['evidence_counts'], {'a': 1, 'b': 1})
        self.assertEqual(result['trace']['scope']['missing_card_keys'], [])

    def test_missing_target_never_expands_to_other_cards(self):
        chunks = [Chunk('a', '카페 할인', 'a', 'A', '발급사', 'benefit', 1)]
        result = RagPipeline(chunks, InMemoryBM25Searcher(chunks)).search('카페', config=SearchConfig(
            vector_weight=0, reranker='off', target_card_keys=('a', 'missing')))
        self.assertEqual([e['card_key'] for e in result['evidence']], ['a'])
        self.assertEqual(result['trace']['scope']['missing_card_keys'], ['missing'])

    def test_large_chunk_does_not_prevent_another_target_from_fitting(self):
        chunks = [Chunk('a1', '전월실적 ' + '가' * 3500, 'a', 'A', '발급사', 'benefit', 1),
                  Chunk('a2', '전월실적 ' + '나' * 3500, 'a', 'A', '발급사', 'benefit', 2),
                  Chunk('b', '전월실적 30만원', 'b', 'B', '발급사', 'benefit', 1)]
        result = RagPipeline(chunks, InMemoryBM25Searcher(chunks)).search('전월실적', config=SearchConfig(
            vector_weight=0, reranker='off', target_card_keys=('a', 'b')))
        self.assertEqual({e['card_key'] for e in result['evidence']}, {'a', 'b'})
        self.assertTrue(result['trace']['evidence_budget']['budget_truncated'])
        self.assertLessEqual(result['trace']['evidence_budget']['payload_size'], 12000)

    def test_scope_rejects_empty_duplicate_and_unbounded_sets(self):
        for keys in ((), ('a', 'a'), ('',), tuple(str(i) for i in range(6))):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                SearchConfig(target_card_keys=keys)
