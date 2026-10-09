import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from pickcardu_rag.retrieval import Chunk, lexical_terms
from pickcardu_rag_api.index import ChromaVectorSearcher, SQLiteFTSSearcher


class ScopedIndexTest(unittest.TestCase):
    def test_sql_card_filter_is_before_limit_and_parameterized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'corpus.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE chunks(chunk_id TEXT, document_id TEXT); CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED,text);')
                for i in range(60):
                    db.execute('INSERT INTO chunks VALUES(?,?)', (f'x{i}', 'other'))
                    db.execute('INSERT INTO chunks_fts VALUES(?,?)', (f'x{i}', ' '.join(lexical_terms('전월실적'))))
                db.execute('INSERT INTO chunks VALUES(?,?)', ('target', "card'quoted"))
                db.execute('INSERT INTO chunks_fts VALUES(?,?)', ('target', ' '.join(lexical_terms('전월실적 30만원'))))
            rows = SQLiteFTSSearcher(path).search('전월실적', limit=1, card_keys=("card'quoted",))
            self.assertEqual([r.chunk_id for r in rows], ['target'])
            self.assertEqual(SQLiteFTSSearcher(path).search('전월실적', limit=1, card_keys=('missing',)), [])

    def test_scoped_vectors_use_validated_rows_not_chroma_metadata(self):
        class NoQuery:
            def query(self, **kwargs):
                raise AssertionError('scoped retrieval must not use global ANN/metadata')
        chunks = [Chunk('x', '혜택', 'other', 'X', '발급사', 'benefit', 1),
                  Chunk('b', '혜택', 'target', 'B', '발급사', 'benefit', 1),
                  Chunk('a', '혜택', 'target', 'A', '발급사', 'benefit', 1)]
        searcher = ChromaVectorSearcher(NoQuery(), 'model', chunks=chunks,
                                       embeddings=np.asarray([[0., 0.], [3., 3.], [1., 1.]], dtype=np.float32))
        self.assertEqual([r.chunk_id for r in searcher.search(np.zeros(2), limit=1, card_keys=('target',))], ['a'])
        self.assertEqual(searcher.search(np.zeros(2), limit=1, card_keys=('missing',)), [])
