from pathlib import Path
from types import SimpleNamespace

import numpy as np

from pickcardu_rag import Chunk, RagPipeline
from pickcardu_rag.retrieval import InMemoryBM25Searcher, InMemorySquaredL2Searcher
from pickcardu_rag.structured import QueryPlan
from pickcardu_rag_api.main import _search
from pickcardu_rag_api.models import QueryRequest
from pickcardu_rag_api.index import ActiveIndexLoader


class Provider:
    embedding_model = 'model'
    def __init__(self):
        self.calls = []
    def embed(self, query):
        self.calls.append(query)
        return np.zeros(1), {}


class Extractor:
    def __init__(self):
        self.calls = []
    def extract(self, query):
        self.calls.append(query)
        return QueryPlan.model_validate({'groups': [[dict(targets=['커피'], action='any', min_percent=None,
            max_spend_krw=None, min_cap_krw=None, cap_period='any', exclude_targets=[],
            min_frequency=None, frequency_period='any')]], 'requested_fields': [], 'unsupported_filters': []}), {}


def test_sequential_search_narrows_before_search_and_embeds_only_once():
    chunks = [Chunk(k, '커피 할인', k, k, '발급사', 'benefit', 1) for k in ('a', 'b', 'c')]
    class Reranker:
        def score(self, mode, query, documents, **kwargs):
            return [1.] * len(documents), {}
    pipeline = RagPipeline(chunks, InMemoryBM25Searcher(chunks),
        InMemorySquaredL2Searcher(['a','b','c'], np.zeros((3,1)), card_keys=['a','b','c']), Reranker())
    configs = []
    def search(q, v, cfg):
        configs.append(cfg)
        return pipeline.search(q, v, cfg)
    handle = SimpleNamespace(manifest={'strategy': 'card_page_section_benefit', 'embedding_model': 'model',
                            'document_ids': ['a','b','c']}, catalog=tuple(dict(card_key=k, card_name=k, issuer='발급사') for k in ('a','b','c')),
                            structured_store=SimpleNamespace(query=lambda plan, scope: ('b',)), search=search)
    loader = SimpleNamespace(load=lambda: handle)
    provider, extractor = Provider(), Extractor()
    _, result, _ = _search(QueryRequest(query='커피 추천'), loader, provider,
                          retrieval_mode='structured_then_hybrid', structured_extractor=extractor,
                          retrieval_query='커피 추천\n검색 힌트: 카페 할인')
    assert [c['card_key'] for c in result['cards']] == ['b']
    assert configs[0].allowed_card_keys == ('b',)
    assert configs[0].target_card_keys is None
    assert provider.calls == ['커피 추천\n검색 힌트: 카페 할인']
    assert extractor.calls == ['커피 추천']
    assert result['structured_usage']['reason'] == 'matched'


def test_old_release_is_compatible_only_with_hybrid(tmp_path):
    import pytest
    from support import build_release, FakeReranker
    build_release(tmp_path)
    assert ActiveIndexLoader(tmp_path, reranker=FakeReranker()).load().structured_store is None
    with pytest.raises(RuntimeError, match='structured'):
        ActiveIndexLoader(tmp_path, require_structured=True).load()


def test_structured_manifest_hash_and_document_mismatch_fail_ready(tmp_path):
    import json
    import pytest
    from pickcardu_rag.structured_release import validate_structured_release
    source = Path(__file__).resolve().parents[3] / 'data/rag/runtime/structured-validation/structured.sqlite'
    if not source.exists():
        pytest.skip('local DB3 integration artifact is not installed')
    import shutil
    shutil.copyfile(source, tmp_path / 'structured.sqlite')
    from pickcardu_rag_api.index import _sha256
    from pickcardu_rag.structured import StructuredStore
    db = StructuredStore(source).connect()
    contract = json.loads(db.execute("SELECT value_json FROM metadata WHERE key='contract'").fetchone()[0])
    db.close()
    with pytest.raises(RuntimeError, match='hash'):
        validate_structured_release(tmp_path, {'structured': {**contract, 'sha256': '0' * 64}}, read_only=False)
    with pytest.raises(RuntimeError, match='document identities'):
        validate_structured_release(tmp_path, {'structured': {**contract, 'sha256': _sha256(source)},
                                             'document_ids': [], 'catalog': []}, read_only=False)
