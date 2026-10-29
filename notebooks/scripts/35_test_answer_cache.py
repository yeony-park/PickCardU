"""Saved failure replay, raw-response preservation and confirmed-budget retry.

Run with --basetemp=notebooks/data/35_answer_budget/execution_cache/test_tmp
so every mocked cache write respects the experiment's output scope.
"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packages/rag-core/src'))
spec = importlib.util.spec_from_file_location('answer_budget_test', Path(__file__).with_name('35_answer_experiment.py'))
A = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = A
spec.loader.exec_module(A)


class Schema(BaseModel):
    value: int


def request():
    return dict(model='wrong', reasoning={'effort': 'high'}, text_format=Schema,
                input='합성 질문', instructions='test', max_output_tokens=2400, tools=[], store=False)


def eof():
    return ValidationError.from_exception_data('StoredFailure', [
        {'type': 'json_invalid', 'loc': (), 'input': None,
         'ctx': {'error': 'EOF while parsing a value'}}])


def test_cached_eof_is_validation_error_so_original_retry_can_continue(tmp_path):
    api = A.ResponseCache(cache=tmp_path / 'new', old_cache=tmp_path / 'old', allow_network=False)
    req = request()
    key = api.key(req)
    api.old_cache.mkdir()
    api.write_fixture(api.old_cache / (key + '.json'), {
        'status': 'failed', 'error': 'ValidationError: EOF while parsing a value'})
    before = (api.old_cache / (key + '.json')).read_bytes()
    with pytest.raises(ValidationError):
        api.parse(**req)
    assert (api.old_cache / (key + '.json')).read_bytes() == before
    assert api.events[0]['cache_hit'] is True


def test_raw_status_and_usage_are_saved_before_parse_failure(tmp_path):
    api = A.ResponseCache(cache=tmp_path / 'new', old_cache=tmp_path / 'old')
    raw = {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
           'usage': {'input_tokens': 10, 'output_tokens': 2400}, 'output': []}
    api.fetch = lambda kwargs: SimpleNamespace(model_dump=lambda **kw: raw)
    api.parse_raw = lambda response, schema: (_ for _ in ()).throw(eof())
    with pytest.raises(ValidationError):
        api.parse(**request())
    stored = api.read_fixture(api.cache / (api.key(request()) + '.json'))
    assert stored['raw_response']['status'] == 'incomplete'
    assert stored['usage']['output_tokens'] == 2400
    assert stored['failure_reason'] == 'max_output_tokens'


@pytest.mark.parametrize('raw,expected', [
    ({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}, 4800),
    ({'status': 'incomplete', 'incomplete_details': {'reason': 'content_filter'}}, 2400),
    ({'status': 'completed', 'output': []}, 2400),
])
def test_adaptive_budget_only_increases_after_confirmed_token_truncation(tmp_path, raw, expected):
    api = A.ResponseCache(cache=tmp_path / 'new', old_cache=tmp_path / 'old', policy='adaptive')
    budgets = []
    def fetch(kwargs):
        budgets.append(kwargs['max_output_tokens'])
        return SimpleNamespace(model_dump=lambda **kw: raw)
    api.fetch = fetch
    api.parse_raw = lambda response, schema: (_ for _ in ()).throw(eof())
    with pytest.raises(ValidationError):
        api.parse(**request())
    followup = request()
    followup['instructions'] = 'original retry instruction'
    with pytest.raises(ValidationError):
        api.parse(**followup)
    assert budgets == [2400, expected]


def test_request_body_keeps_explicit_model_reasoning_and_schema(tmp_path):
    api = A.ResponseCache(cache=tmp_path / 'new', old_cache=tmp_path / 'old')
    wire = api.wire_kwargs(request())
    assert wire['model'] == 'gpt-6-luna' and wire['reasoning'] == {'effort': 'medium'}
    assert wire['text']['format']['type'] == 'json_schema'
    assert wire['max_output_tokens'] == 2400 and wire['store'] is False
    assert 'text_format' not in wire


def test_original_answer_retries_with_faithful_cached_validation_failure(tmp_path):
    from pickcardu_rag.answering import OpenAIService
    api = A.ResponseCache(cache=tmp_path / 'new', old_cache=tmp_path / 'old', allow_network=False)
    attempts = []
    generated = {'answer_status': 'answered', 'answer_text': '편의점 10% 할인입니다.',
                 'recommendations': [{'reason': '편의점 할인', 'citations': ['e1']}],
                 'claims': [{'text': '편의점 10% 할인', 'value': 10, 'unit': '%',
                             'conditions': [], 'citations': ['e1']}]}
    def parse(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise eof()
        return SimpleNamespace(output_parsed=kwargs['text_format'].model_validate(generated), usage={})
    api.parse = parse
    service = OpenAIService(api_key=None, client=api, llm_model='gpt-6-luna', answer_payload_bytes=64000)
    evidence = [{'card_key': 'card', 'chunk_id': 'chunk', 'card_name': '카드', 'issuer': '발급사',
                 'text': '편의점 10% 할인'}]
    answer, usage = service.answer('편의점 할인 카드 추천', evidence)
    assert usage['attempt_count'] == 2 and len(attempts) == 2
    assert attempts[0]['instructions'] != attempts[1]['instructions']
    assert answer.recommendations[0].card_key == 'card'
