"""Independent arithmetic/provenance checks for the cost report addition."""
if not __debug__:
    raise RuntimeError('Verification requires assertions enabled')

from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('cost_audit_common',Path(__file__).with_name('34_followup_common.py'))
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)


def verify():
    base=F.STAGES[36];ledger=F.read_json(base/'cost_accounting.json')
    totals={};counts={};unknown=0
    for row in ledger['entries']:
        raw=F.read_json(F.ROOT/row['path'])
        assert F.digest(raw)==row['record_sha256']
        usage=raw.get('usage') or {}
        if row['cost_usd'] is None:
            assert not usage;unknown+=1;continue
        assert not row['saved_response_duplicate']
        if row['model']=='text-embedding-3-small':
            expected=Decimal(usage['prompt_tokens'])*Decimal('0.02')/1000000
        else:
            assert raw['request']['model']==raw['raw_response']['model']=='gpt-6-luna'
            assert raw['raw_response']['service_tier']=='default' and usage['input_tokens']<=272000
            d=usage['input_tokens_details'];regular=usage['input_tokens']-d['cached_tokens']-d['cache_write_tokens']
            assert regular>=0
            expected=(Decimal(regular)*Decimal('0.10')+Decimal(d['cached_tokens'])*Decimal('0.01')+
                      Decimal(d['cache_write_tokens'])*Decimal('0.125')+Decimal(usage['output_tokens'])*Decimal('0.50'))/1000000
        assert expected==Decimal(row['cost_usd'])
        totals[row['stage']]=totals.get(row['stage'],Decimal(0))+expected
        counts[row['stage']]=counts.get(row['stage'],0)+1
    assert counts=={'30-33':363,'35':166,'36':44} and unknown==4
    for stage,total in totals.items():assert total==Decimal(ledger['stages'][stage]['known_cost_usd'])
    assert sum(totals.values())==Decimal('0.550730735')==Decimal(ledger['all_recorded_30_36']['known_cost_usd'])
    assert totals['35']+totals['36']==Decimal('0.266199825')
    stage35=F.read_json(F.STAGES[35]/'cost_accounting.json')
    assert stage35['ledger_stage']=='35' and set(stage35['stages'])=={'35'}
    assert 'all_recorded_30_36' not in stage35 and 'followup_34_36' not in stage35
    assert Decimal(stage35['ledger_total']['known_cost_usd'])==totals['35']
    primary=F.read_json(base/'primary_results.json')
    benefit={(r['query_id'],r['mode']):r for r in primary['answers'] if r['profile']=='card_page_section_benefit'}
    differences=[q for q in sorted({r['query_id'] for r in primary['answers']})
        if benefit[(q,'S_THEN_U')]['scores']['known_positive_complete']!=benefit[(q,'S_PLUS_U')]['scores']['known_positive_complete']]
    assert differences==['Q10']
    for stage,filename in [(35,'35_answer_budget_report.html'),(36,'36_profile_search_comparison_report.html')]:
        summary=F.read_json(F.STAGES[stage]/'report_summary.json');path=F.ROOT/'docs/report'/filename
        assert summary['html_sha256']==F.digest(path.read_bytes())
        resources=summary['resources'];field='usage_based_cost_usd' if stage==35 else 'usage_based_new_cost_usd'
        assert Decimal(resources[field])==totals[str(stage)] and resources['billed_cost']=='미측정'
        content=path.read_text()
        assert 'API 사용량 기준 비용' in content and '요금 환산액·알려진 소계(USD)' in content
        assert '$'+format(totals[str(stage)],'.6f') in content
        assert '실제 청구액은 미측정' in content
        if stage==36:
            assert '$0.266200' in content and '$0.550731' in content
            assert 'Q10 한 질문' in content and '합산값1.5%' in content
            assert '우선 검증할 운영 후보는 benefit' in content
        import re
        assert not re.search('[\u4e00-\u9fff]',content)
    audit={'passed':True,'request_records':577,'priced_records':573,'unknown_cost_records':4,
        'known_subtotal_usd':'0.550730735','followup_usd':'0.266199825','provider_billed_cost':'미측정',
        'new_generation_api_requests':0,'benefit_s_then_vs_fusion_changed_queries':differences,
        'rates_verified_date':ledger['rates']['verified_date'],'pricing_sources':ledger['rates']['sources'],
        'ledger_sha256':F.digest(ledger),'original_source_records_verified':577,
        'html_cost_values_billing_limit_and_q10_caveat_verified':True,
        'code_sha256':{name:F.digest((F.ROOT/'notebooks'/name).read_bytes()) for name in
            ['36_cost_accounting.py','36_test_cost_accounting.py','36_verify_cost_accounting.py','35_render_answer_report.py','36_render_comparison_report.py']}}
    F.write_json(base/'cost_accounting_audit.json',audit)
    print(F.canonical(audit))
    return audit


if __name__=='__main__':
    F.initialize(36)
    verify()
