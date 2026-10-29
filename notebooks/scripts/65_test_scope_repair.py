"""Catalog-backed issuer scope regressions; no network or operating edits."""
import importlib.util
from pathlib import Path
import json
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('scope65_regression', Path(__file__).with_name('65_scope_repair.py'))
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)
DOCS = [json.loads(s) for s in (ROOT/'notebooks/data/60_structured_retrieval_repair/db_v3/documents.jsonl').read_text().splitlines()]


def keys(*issuers):
    return sorted(d['document_id'] for d in DOCS if d['document_id'].split('/')[0] in issuers)


@pytest.mark.parametrize('question', [
    'IBK K-패스 신용카드의 철도 할인은 하루·한 달에 몇 번, 얼마까지야? 실적 조건도 알려줘.',
    'IBK 아이올로 동물병원에서 할인받을 때 최근 석 달 실적에 따라 할인율과 월 한도가 어떻게 달라져?',
    'ibk 아이올의 혜택을 알려줘',
])
def test_short_catalog_issuer_preserves_baseline_without_product_alias(question):
    actual = K.original_scope(question, DOCS)
    assert actual['card_keys'] == keys('ibk')
    assert actual['issuer'] == 'ibk' and not actual['clarify']
    assert actual['exact_products'] == []  # No invented transliteration/product mapping.


@pytest.mark.parametrize('question', [
    'Samsung Pay로 적립되는 카드 추천해줘',
    'Lotte Mart 할인 카드 추천해줘',
    'Hyundai Department Store 할인 카드 추천해줘',
    'IBKPay로 적립되는 카드 추천해줘',
])
def test_long_brand_or_joined_payment_name_is_not_a_short_issuer_token(question):
    assert K.original_scope(question, DOCS)['card_keys'] is None


def test_short_and_explicit_issuers_preserve_the_union():
    assert K.original_scope('IBK와 삼성카드 중 커피 카드 추천해줘', DOCS)['card_keys'] == keys('ibk','samsung')


def test_install_binds_only_this_runtime_and_records_source_hash():
    from types import SimpleNamespace
    import hashlib
    runtime = SimpleNamespace(ROOT=ROOT, S=SimpleNamespace(original_scope=lambda q,d:None),
                              M=SimpleNamespace(source_hashes=lambda:{'original':'unchanged'}))
    K.install(runtime)
    installed = runtime.M.source_hashes
    assert runtime.S.original_scope is K.original_scope
    assert installed()['original'] == 'unchanged'
    assert installed()['notebooks/scripts/65_scope_repair.py'] == hashlib.sha256(
        (ROOT/'notebooks/scripts/65_scope_repair.py').read_bytes()).hexdigest()
    K.install(runtime)
    assert runtime.M.source_hashes is installed
