import json
from types import SimpleNamespace

from pickcardu_rag.structured_extraction import StructuredExtractor


def test_extractor_has_independent_bounded_settings_and_preserves_failed_usage():
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(id='response-fixture', status='completed', output_text='invalid json',
                               usage=SimpleNamespace(model_dump=lambda **_: {'input_tokens': 10, 'output_tokens': 3}))
    extractor = StructuredExtractor(client=SimpleNamespace(responses=SimpleNamespace(create=create)))
    plan, usage = extractor.extract('월 한도는 얼마인가요?')
    assert plan is None and usage['usage_complete'] and usage['attempt_count'] == 1
    assert requests[0]['model'] == 'gpt-6-luna'
    assert requests[0]['reasoning'] == {'effort': 'medium'}
    assert requests[0]['max_output_tokens'] == 3000


def test_failed_provider_is_not_retried():
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        raise TimeoutError()
    plan, usage = StructuredExtractor(client=SimpleNamespace(responses=SimpleNamespace(create=create))).extract('커피 할인')
    assert plan is None and len(requests) == 1
    assert not usage['usage_complete'] and usage['error_type'] == 'TimeoutError'
