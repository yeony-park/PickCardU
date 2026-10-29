"""Behavior tests for whole-chunk budget selection and search isolation; zero API."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packages/rag-core/src'))
from pickcardu_rag.retrieval import (Candidate, Chunk, RagPipeline, SearchConfig,
                                    PARENT_CHILD_BUNDLE, parent_child_bundles)

spec = importlib.util.spec_from_file_location('bundle_followup_test', Path(__file__).with_name('34_bundle_experiment.py'))
B = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = B
spec.loader.exec_module(B)


def chunk(key, text, related=(), card='card'):
    return Chunk(key, text, card, card, '발급사', 'structural', 1,
                 node_id=key, related_chunk_ids=tuple(related), heading_path=('혜택',))


def test_skips_oversized_related_without_slicing_short_chunk():
    chunks = {'seed': chunk('seed', 'seed text', ['large', 'short']),
              'large': chunk('large', 'LARGE full text'), 'short': chunk('short', 'short full text')}
    original = dict(chunks)
    rows = [Candidate('seed', 1, 1)]
    result = B.build_budgeted(rows, chunks, max_chunks=5, fits=lambda text: 'LARGE' not in text)
    assert result[0]['selected_chunk_ids'] == ['seed', 'short']
    assert 'short full text' in result[0]['text']
    assert 'LARGE' not in result[0]['text']
    assert chunks == original


def test_seed_itself_over_budget_fails_explicitly():
    chunks = {'seed': chunk('seed', 'LARGE')}
    with pytest.raises(Exception, match='seed.*token limit'):
        B.build_budgeted([Candidate('seed', 1, 1)], chunks, max_chunks=8, fits=lambda text: False)


def test_filters_other_card_and_repeated_ids_but_keeps_candidate_order():
    chunks = {'seed': chunk('seed', 'seed', ['foreign', 'same', 'same']),
              'foreign': chunk('foreign', 'foreign', card='other'),
              'same': chunk('same', 'same'), 'candidate': chunk('candidate', 'candidate')}
    result = B.build_budgeted([Candidate('seed', 1, 1), Candidate('candidate', .5, 2)],
                             chunks, max_chunks=8, fits=lambda text: True)
    assert result[0]['selected_chunk_ids'] == ['seed', 'same', 'candidate']
    assert 'foreign' not in result[0]['text']


def test_all_fit_budget5_equals_original_whole_chunk_formatter():
    chunks = {str(i): chunk(str(i), f'본문{i}') for i in range(6)}
    chunks['0'] = chunk('0', '본문0', ['1', '2', '3', '4', '5'])
    rows = [Candidate('0', 1, 1)]
    original = parent_child_bundles(rows, chunks, max_chunks=5)
    result = B.build_budgeted(rows, chunks, max_chunks=5, fits=lambda text: True)
    assert result == original


def test_budget8_really_can_include_eight_whole_chunks():
    chunks = {str(i): chunk(str(i), f'본문{i}') for i in range(10)}
    chunks['0'] = chunk('0', '본문0', list(chunks)[1:])
    result = B.build_budgeted([Candidate('0', 1, 1)], chunks, max_chunks=8, fits=lambda text: True)
    assert result[0]['selected_chunk_ids'] == list(chunks)[:8]


def test_search_dependency_injection_preserves_operational_globals_and_fixed5_output():
    class Lexical:
        def search(self, query, *, limit):
            return [Candidate('seed', 1, 1)]
    class Scorer:
        def score(self, mode, query, documents, **kwargs):
            return [1.0] * len(documents), {'fake': True}
    chunks = [chunk('seed', '편의점 10% 할인')]
    pipeline = RagPipeline(chunks, Lexical(), None, Scorer(), profile=PARENT_CHILD_BUNDLE)
    config = SearchConfig(profile='parent_child_bundle', vector_weight=0, reranker_route='all')
    before = dict(RagPipeline.search.__globals__)
    reference = pipeline.search('편의점 혜택', None, config)
    calls = []
    def builder(rows, mapping):
        calls.append(True)
        return parent_child_bundles(rows, mapping, max_chunks=5)
    isolated = B.isolated_search(builder)
    actual = isolated(pipeline, '편의점 혜택', None, config)
    assert calls == [True]
    assert actual['cards'] == reference['cards'] and actual['evidence'] == reference['evidence']
    assert isolated.__code__ is RagPipeline.search.__code__
    assert RagPipeline.search.__globals__ == before
    assert RagPipeline.search.__globals__['parent_child_bundles'] is parent_child_bundles


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_resumed_copy_rejects_links_to_original(tmp_path, kind):
    source = tmp_path / 'source.sqlite'
    source.write_bytes(b'original data')
    target = tmp_path / 'copies'
    target.mkdir()
    dest = target / 'copy.sqlite'
    if kind == 'symlink':
        dest.symlink_to(source)
    else:
        dest.hardlink_to(source)
    with pytest.raises(RuntimeError, match='link|regular'):
        B.F.ensure_private_copy(source, dest, target)


def test_resumed_copy_rejects_symlinked_parent_directory(tmp_path):
    source = tmp_path / 'source.sqlite'
    source.write_bytes(b'original data')
    original_dir = tmp_path / 'original'
    original_dir.mkdir()
    (original_dir / 'copy.sqlite').write_bytes(b'copy')
    target = tmp_path / 'copies'
    target.mkdir()
    (target / 'nested').symlink_to(original_dir, target_is_directory=True)
    with pytest.raises(RuntimeError, match='link'):
        B.F.ensure_private_copy(source, target / 'nested/copy.sqlite', target)


def test_first_copy_rejects_symlink_directory_before_writing(tmp_path):
    original_dir = tmp_path / 'original'
    original_dir.mkdir()
    target = tmp_path / 'indexes'
    target.symlink_to(original_dir, target_is_directory=True)
    with pytest.raises(RuntimeError, match='symlink'):
        B.F.assert_private_directory(target)
    assert list(original_dir.iterdir()) == []


def test_prepare_inputs_rejects_link_before_any_copy2(tmp_path, monkeypatch):
    stage, original = tmp_path / 'stage', tmp_path / 'original'
    stage.mkdir()
    original.mkdir()
    (stage / 'indexes').symlink_to(original, target_is_directory=True)
    monkeypatch.setitem(B.F.STAGES, 34, stage)
    monkeypatch.setattr(B.F, 'preservation_snapshot', lambda: {'files': []})
    called = []
    monkeypatch.setattr(B.F.shutil, 'copy2', lambda *args: called.append(args))
    with pytest.raises(RuntimeError, match='symlink'):
        B.F.prepare_inputs()
    assert called == [] and list(original.iterdir()) == []


def test_frozen_component_rejects_different_scope_or_question():
    rows = [Candidate('seed', 1, 1)]
    searcher = B.FrozenSearcher(rows, expected_query='q', expected_scope=('a',))
    assert searcher.search('q', limit=50, card_keys=('a',)) == rows
    with pytest.raises(ValueError):
        searcher.search('other', limit=50, card_keys=('a',))
    with pytest.raises(ValueError):
        searcher.search('q', limit=50, card_keys=('b',))
