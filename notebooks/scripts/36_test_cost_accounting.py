"""Cost accounting regressions; no network calls or production writes."""
import importlib.util
from pathlib import Path
import sys
from decimal import Decimal
import pytest


def module():
    path=Path(__file__).with_name('36_cost_accounting.py')
    spec=importlib.util.spec_from_file_location('cost_test_module',path)
    result=importlib.util.module_from_spec(spec);sys.modules[spec.name]=result;spec.loader.exec_module(result)
    return result


def usage():
    return {'input_tokens':1000,'output_tokens':300,'total_tokens':1300,
        'input_tokens_details':{'cached_tokens':200,'cache_write_tokens':400},
        'output_tokens_details':{'reasoning_tokens':50}}


def test_cache_writes_and_reasoning_are_not_counted_twice():
    C=module();row=C.cost_from_usage(usage(),'gpt-6-luna')
    assert row['ordinary_input_tokens']==400
    assert Decimal(row['cost_usd'])==Decimal('0.000242')


def test_unknown_usage_is_not_zero_cost():
    C=module()
    assert C.cost_from_usage({},'gpt-6-luna')['cost_usd'] is None
    incomplete=usage();del incomplete['input_tokens_details']['cache_write_tokens']
    assert C.cost_from_usage(incomplete,'gpt-6-luna')['cost_usd'] is None


def test_invalid_token_partition_stops_calculation():
    C=module();bad=usage();bad['input_tokens_details']['cache_write_tokens']=900
    with pytest.raises(ValueError,match='partition'):C.cost_from_usage(bad,'gpt-6-luna')


def test_embedding_tokens_and_long_context_rates():
    C=module()
    assert Decimal(C.cost_from_usage({'prompt_tokens':1000000,'total_tokens':1000000},'text-embedding-3-small')['cost_usd'])==Decimal('0.02')
    row=C.cost_from_usage({'input_tokens':273000,'output_tokens':1000,
        'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}},'gpt-6-luna')
    assert Decimal(row['cost_usd'])==Decimal('0.05535')


def test_distinct_revalidation_request_counts_even_with_same_payload_key():
    C=module()
    failed={'status':'failed','request':{'model':'gpt-6-luna'},'usage':{}}
    recovered={'status':'ok','request':{'model':'gpt-6-luna'},'usage':usage(),
        'raw_response':{'id':'new-response','model':'gpt-6-luna','service_tier':'default'}}
    entries=C.price_records([('30','same.json',failed),('35','same.json',recovered)])
    assert len(entries)==2 and entries[0]['cost_usd'] is None
    assert Decimal(entries[1]['cost_usd'])==Decimal('0.000242')


def test_duplicate_response_copy_has_no_new_charge():
    C=module();record={'status':'ok','request':{'model':'gpt-6-luna'},'usage':usage(),
        'raw_response':{'id':'same-response','model':'gpt-6-luna','service_tier':'default'}}
    rows=C.price_records([('30','a.json',record),('35','b.json',record)])
    assert sum(Decimal(r['cost_usd'] or '0') for r in rows)==Decimal('0.000242')
    assert rows[1]['saved_response_duplicate'] is True


def test_stage35_costs_work_without_stage36_outputs(tmp_path,monkeypatch):
    import json
    C=module();stage35=tmp_path/'stage35';stage36=tmp_path/'stage36'
    cache=stage35/'api_cache';cache.mkdir(parents=True)
    record={'status':'ok','request':{'model':'gpt-6-luna'},'usage':usage(),
        'raw_response':{'id':'standalone35','model':'gpt-6-luna','service_tier':'default','usage':usage()}}
    (cache/'one.json').write_text(json.dumps(record))
    monkeypatch.setattr(C.F,'STAGES',{35:stage35,36:stage36})
    result=C.build(stage='35')
    assert set(result['stages'])=={'35'}
    assert 'followup_34_36' not in result and 'all_recorded_30_36' not in result
    assert Decimal(result['stages']['35']['known_cost_usd'])==Decimal('0.000242')
    assert (stage35/'cost_accounting.json').exists() and not stage36.exists()


def test_full_ledger_keeps_stage36_destination_when_answers_use_old_cache(tmp_path,monkeypatch):
    import json
    C=module();paths={n:tmp_path/f'stage{n}' for n in [30,35,36]}
    for path in paths.values():(path/'api_cache').mkdir(parents=True)
    record={'status':'ok','request':{'model':'gpt-6-luna'},'usage':usage(),
        'raw_response':{'id':'historical-answer','model':'gpt-6-luna','service_tier':'default','usage':usage()}}
    (paths[30]/'api_cache/one.json').write_text(json.dumps(record))
    primary={'answers':[{'profile':'card_page_section_benefit','mode':'U',
        'events':[{'key':'one','usage':usage()}]}]}
    (paths[36]/'primary_results.json').write_text(json.dumps(primary))
    monkeypatch.setattr(C.F,'STAGES',{35:paths[35],36:paths[36]})
    monkeypatch.setattr(C.F,'OLD',{30:paths[30]})
    result=C.build()
    assert (paths[36]/'cost_accounting.json').exists()
    assert result['embedding_runner_sha256'] is not None
    assert result['answer_stage_only'][0]['record_replay_cost_usd']=='0.000242'
