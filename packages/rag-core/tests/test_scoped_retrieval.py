import unittest
from unittest.mock import patch

import numpy as np

from pickcardu_rag import Candidate, Chunk, InMemoryBM25Searcher, InMemorySquaredL2Searcher, RagPipeline, SearchConfig
from pickcardu_rag.answering import measure_answer_payload


class ScopedRetrievalTest(unittest.TestCase):
    def test_wallet_lookup_filters_before_candidate_cut_without_precision_limit(self):
        chunks = [Chunk(f'x{i}', '카페 할인', 'other', '다른 카드', '발급사', 'benefit', 1) for i in range(60)]
        chunks.append(Chunk('owned', '카페 할인', 'owned', '보유 카드', '발급사', 'benefit', 1))
        vectors = np.asarray([[0., 0.]] * 60 + [[1., 1.]])
        pipeline = RagPipeline(chunks, InMemoryBM25Searcher(chunks),
            InMemorySquaredL2Searcher([c.chunk_id for c in chunks], vectors, card_keys=[c.card_key for c in chunks]))
        keys = ('owned', *(f'wallet{i}' for i in range(105)))
        result = pipeline.search('카페', np.zeros(2), SearchConfig(
            vector_weight=1, component_depth=1, candidate_depth=1, reranker='off',
            wallet_card_keys=keys, wallet_operation='lookup'))
        self.assertEqual([e['chunk_id'] for e in result['evidence']], ['owned'])
        with self.assertRaises(ValueError):
            SearchConfig(target_card_keys=keys)

    def test_compare_excludes_owned_before_cut_and_keeps_both_whole_evidence(self):
        chunks = [Chunk(f'o{i}', '카페 10% 할인', 'owned', '보유 카드', '발급사', 'benefit', 1) for i in range(60)]
        chunks.append(Chunk('new', '카페 15% 할인', 'new', '새 카드', '발급사', 'benefit', 1))
        pipeline = RagPipeline(chunks, InMemoryBM25Searcher(chunks),
            InMemorySquaredL2Searcher([c.chunk_id for c in chunks], np.asarray([[0., 0.]] * 60 + [[1., 1.]]),
                                      card_keys=[c.card_key for c in chunks]))
        result = pipeline.search('카페', np.zeros(2), SearchConfig(
            vector_weight=1, component_depth=1, candidate_depth=2, reranker='off', top_k=5,
            wallet_card_keys=('owned',), new_card_keys=('new',), wallet_operation='compare'))
        self.assertEqual({e['card_key'] for e in result['evidence']}, {'owned', 'new'})
        self.assertEqual(result['trace']['scope']['missing_required_groups'], [])
        self.assertIn('카페 15% 할인', [e['text'] for e in result['evidence']])

    def test_compare_does_not_allow_one_sided_answer_after_payload_budget_cut(self):
        chunks = [Chunk('a', '가' * 100, 'a', 'A', '발급사', 'benefit', 1),
                  Chunk('b', '나' * 100, 'b', 'B', '발급사', 'benefit', 1)]
        pipeline = RagPipeline(chunks, InMemoryBM25Searcher(chunks),
            InMemorySquaredL2Searcher(['a', 'b'], np.asarray([[0., 0.], [1., 1.]]), card_keys=['a', 'b']))
        result = pipeline.search('혜택', np.zeros(2), SearchConfig(
            vector_weight=1, reranker='off', top_k=5, answer_payload_bytes=450,
            wallet_card_keys=('a',), new_card_keys=('b',), wallet_operation='compare'))
        self.assertTrue(result['trace']['scope']['missing_required_groups'])
        self.assertLessEqual(result['trace']['evidence_budget']['payload_size'], 450)

    def test_105_owned_cards_leave_only_the_last_catalog_card_in_new_branch(self):
        keys = tuple(f'card{i}' for i in range(106))
        chunks = [Chunk(f'chunk{i}', '카페 할인', key, key, '발급사', 'benefit', 1)
                  for i, key in enumerate(keys)]
        delegate = InMemoryBM25Searcher(chunks)
        calls = []

        class RecordingSearcher:
            def search(self, query, *, limit, card_keys=None):
                calls.append(set(card_keys))
                return delegate.search(query, limit=limit, card_keys=card_keys)

        result = RagPipeline(chunks, RecordingSearcher()).search('카페', config=SearchConfig(
            vector_weight=0, reranker='off', component_depth=1, candidate_depth=2, top_k=5,
            wallet_card_keys=keys[:105], new_card_keys=keys[105:], wallet_operation='compare'))
        self.assertEqual(calls, [set(keys[:105]), {keys[105]}])
        self.assertEqual({e['card_key'] for e in result['evidence']}, {keys[0], keys[105]})
        self.assertEqual(result['trace']['scope']['missing_required_groups'], [])

    def test_personalization_and_answer_query_are_included_in_the_actual_budget(self):
        context = {'survey_context': {'monthly_spending': '50-100', 'spending_categories': ['카페'],
                                     'preferred_benefits': ['할인']}}
        chunks = [Chunk('a', '카페 10% 할인', 'a', 'A', '발급사', 'benefit', 1)]
        result = RagPipeline(chunks, InMemoryBM25Searcher(chunks)).search(
            '내게 맞는 카드\n검색 힌트: 카페 할인', config=SearchConfig(vector_weight=0, reranker='off',
                answer_query='내게 맞는 카드', personalization_context=context))
        size, _ = measure_answer_payload('내게 맞는 카드', result['evidence'], context)
        self.assertEqual(result['trace']['evidence_budget']['payload_size'], size)

    def test_wallet_contract_rejects_empty_duplicate_and_overlapping_new_scope(self):
        for wallet in ((), ('a', 'a'), ('',), tuple(str(i) for i in range(107))):
            with self.subTest(wallet=wallet), self.assertRaises(ValueError):
                SearchConfig(wallet_card_keys=wallet, wallet_operation='lookup')
        with self.assertRaises(ValueError):
            SearchConfig(wallet_card_keys=('a',), new_card_keys=('a',), wallet_operation='compare')
        with self.assertRaises(ValueError):
            SearchConfig(wallet_card_keys=('a',), wallet_operation='none')

    def test_adapter_cannot_return_owned_card_in_new_branch(self):
        chunks = [Chunk('owned', '카페 할인', 'a', 'A', '발급사', 'benefit', 1),
                  Chunk('new', '카페 할인', 'b', 'B', '발급사', 'benefit', 1)]

        class EscapingSearcher:
            def search(self, query, *, limit, card_keys=None):
                return [Candidate('owned', 1., 1)]

        with self.assertRaisesRegex(ValueError, 'trusted scope'):
            RagPipeline(chunks, EscapingSearcher()).search('카페', config=SearchConfig(
                vector_weight=0, reranker='off', wallet_card_keys=('a',), new_card_keys=('b',),
                wallet_operation='compare'))

    def test_runtime_budget_default_and_environment_override_control_evidence(self):
        chunks = [Chunk('a', '가' * 2500, 'a', 'A', 'Issuer', 'benefit', 1),
                  Chunk('b', '나' * 2500, 'b', 'B', 'Issuer', 'benefit', 1)]
        vectors = np.asarray([[0., 0.], [1., 1.]])
        pipeline = RagPipeline(chunks, InMemoryBM25Searcher(chunks),
                               InMemorySquaredL2Searcher([c.chunk_id for c in chunks], vectors,
                                                         card_keys=[c.card_key for c in chunks]))
        for limit, expected in ((None, {'a', 'b'}), ('12000', {'a'}), ('64000', {'a', 'b'})):
            with self.subTest(limit=limit), patch.dict('os.environ', {}, clear=True):
                if limit is not None:
                    import os
                    os.environ['PICKCARDU_ANSWER_PAYLOAD_BYTES'] = limit
                result = pipeline.search('혜택', np.zeros(2), SearchConfig(
                    vector_weight=1, reranker='off', target_card_keys=('a', 'b')))
                self.assertEqual({e['card_key'] for e in result['evidence']}, expected)
                self.assertEqual(result['trace']['evidence_budget']['payload_unit_limit'], int(limit or 64000))

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
            vector_weight=0, reranker='off', target_card_keys=('a', 'b'), answer_payload_bytes=12000))
        self.assertEqual({e['card_key'] for e in result['evidence']}, {'a', 'b'})
        self.assertTrue(result['trace']['evidence_budget']['budget_truncated'])
        self.assertLessEqual(result['trace']['evidence_budget']['payload_size'], 12000)

    def test_scope_rejects_empty_duplicate_and_unbounded_sets(self):
        for keys in ((), ('a', 'a'), ('',), tuple(str(i) for i in range(6))):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                SearchConfig(target_card_keys=keys)
