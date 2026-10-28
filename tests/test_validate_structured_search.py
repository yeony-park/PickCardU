import json
from decimal import Decimal

import pytest

from scripts.validate_structured_search import Budget, price, response_payload


def test_unknown_or_pending_usage_stops_before_another_request(tmp_path):
    budget = Budget(tmp_path, Decimal('2.826878200'))
    budget.reserve('C01:benefit', 'extract', {'model': 'gpt-6-luna', 'max_output_tokens': 3000})
    with pytest.raises(RuntimeError, match='unknown'):
        budget.reserve('C01:benefit', 'embedding', {'model': 'text-embedding-3-small'})


def test_budget_and_one_extraction_attempt_are_enforced(tmp_path):
    budget = Budget(tmp_path, Decimal('7.99999'))
    with pytest.raises(RuntimeError, match='budget'):
        budget.reserve('C01:benefit', 'extract', {'model': 'gpt-6-luna', 'max_output_tokens': 3000})
    budget = Budget(tmp_path, Decimal('2'))
    path = budget.reserve('C01:benefit', 'extract', {'model': 'gpt-6-luna', 'max_output_tokens': 3000})
    row = json.loads(path.read_text());row.update(cost_usd='0.0001', status='ok')
    path.write_text(json.dumps(row))
    with pytest.raises(RuntimeError, match='attempt'):
        budget.reserve('C01:benefit', 'extract', {'model': 'gpt-6-luna', 'max_output_tokens': 3000})


def test_price_uses_actual_cache_partition_and_separate_models():
    usage = {'input_tokens': 1000, 'output_tokens': 100,
             'input_tokens_details': {'cached_tokens': 400, 'cache_write_tokens': 100}}
    assert price(usage, 'gpt-6-luna') == Decimal('0.0001165')
    assert price(usage, 'gpt-5.6-luna') == Decimal('0.000253')
    assert price({'input_tokens': 10, 'output_tokens': 10}, 'gpt-6-luna') is None


def test_quality_halt_blocks_network_and_non_json_failure_can_be_preserved(tmp_path):
    (tmp_path / 'HALTED.json').write_text('{}')
    budget = Budget(tmp_path / 'requests', Decimal('2'))
    with pytest.raises(RuntimeError, match='halted'):
        budget.reserve('C01:benefit', 'extract', {'model':'gpt-6-luna','max_output_tokens':3000})
    class Failure:
        text = 'Internal Server Error'
        def json(self):
            raise ValueError('invalid JSON')
    assert response_payload(Failure()) == {'code':'NON_JSON_RESPONSE', 'body':'Internal Server Error'}
