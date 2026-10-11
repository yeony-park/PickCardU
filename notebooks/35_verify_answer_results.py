"""Audit frozen answer budgets, exact request records and published artifacts."""
import importlib.util
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('answer_audit_common',Path(__file__).with_name('34_followup_common.py'))
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)


def verify():
    import nbformat
    A=F.load_module('answer_audit_runner',F.ROOT/'notebooks/35_answer_experiment.py')
    R=F.load_module('answer_audit_report',F.ROOT/'notebooks/35_render_answer_report.py')
    L=F.load_module('answer_audit_links',F.ROOT/'notebooks/34_verify_bundle_results.py')
    E,score=A.load_tools()
    cases,retrievals,protocol=A.inputs()
    base=F.STAGES[35];result=F.read_json(base/'answer_results.json')
    queries={c['query_id']:c for c in cases['queries']}
    by={(r['query_id'],r['profile'],r['mode']):r for r in retrievals}
    expected={(*key,p) for key in by for p in ['2400','adaptive','4800']}
    keys=[(r['query_id'],r['profile'],r['mode'],r['budget_policy']) for r in result['records']]
    assert len(keys)==len(set(keys))==594 and set(keys)==expected
    selection=F.read_json(base/'selection.json');check=dict(selection);claimed=check.pop('sha256')
    assert F.digest(check)==claimed==result['selection']['sha256']
    assert selection['selected_before_remaining13'] and selection['selected_budget_policy']=='2400'
    selected_at=(base/'selection.json').stat().st_mtime_ns
    assert all((base/'answers'/('_'.join(key)+'.json')).stat().st_mtime_ns>selected_at
               for key in keys if queries[key[0]]['split']!='development')
    for row in result['records']:
        retrieval=by[(row['query_id'],row['profile'],row['mode'])]
        assert row['retrieval_sha256']==F.digest(retrieval) and row['protocol_sha256']==protocol['sha256']
        assert row['model']==F.MODEL and row['reasoning']==F.REASONING and row['status']=='ok'
        assert row['scores']==score.score_row(queries[row['query_id']],row,retrieval)
        assert all(e['max_output_tokens'] in [2400,4800] for e in row['events'])
    provider={p.stem:F.read_json(p) for p in (base/'api_cache').glob('*.json')}
    newkeys={e['key'] for r in result['records'] for e in r['events'] if not e['cache_hit']}
    assert len(provider)==len(newkeys)==166 and set(provider)==newkeys
    for key,record in provider.items():
        assert key==F.digest(record['request']) and record['status']=='ok'
        request=record['request']
        assert request['model']==F.MODEL and request['reasoning']=={'effort':F.REASONING}
        assert request['store'] is False and request['max_output_tokens'] in [2400,4800]
        assert record['raw_response']['usage']==record['usage']
        assert record['finished_at']>=record['started_at']
    summary=F.read_json(base/'report_summary.json')
    rows=summary['summaries']
    assert len(rows)==len({(r['split'],r['profile'],r['mode'],r['budget']) for r in rows})==54
    assert summary['resources']['new_provider_tokens']==R.token_totals(list(provider.values()))
    document=F.ROOT/'docs/report/35_answer_budget_report.html'
    parser=L.Links();parser.feed(document.read_text())
    broken=[link for link in parser.links if not link.startswith(('https://','#'))
            and not (document.parent/link).resolve().exists()]
    assert not broken,broken
    notebook=F.ROOT/'notebooks/35_answer_budget.ipynb'
    nb=nbformat.read(notebook,as_version=4);nbformat.validate(nb)
    cells=[c for c in nb.cells if c.cell_type=='code']
    assert len(cells)==4 and all(c.execution_count and all(o.output_type!='error' for o in c.outputs) for c in cells)
    replay=F.read_json(base/'offline_retry_replay.json')
    assert len(replay['records'])==6 and replay['new_provider_requests']==0
    audit={'passed':True,'grid_rows':594,'provider_requests':166,'provider_failures':0,
        'logical_successes':594,'offline_replay_rows':6,'summary_unique_rows':54,
        'selected_policy':selection['selected_budget_policy'],'protocol_sha256':protocol['sha256'],
        'source_and_original_scores_verified':True,'selection_precedes_remaining13':True,
        'notebook_cells_executed':4,'broken_links':broken,'html_sha256':F.digest(document.read_bytes())}
    F.write_json(base/'completion_audit.json',audit)
    F.write_json(base/'evaluation_summary.json',{'selection':selection,'resources':summary['resources'],
        'protocol_sha256':protocol['sha256'],'records':[{k:r[k] for k in
        ['query_id','profile','mode','budget_policy','split','subgroup','status','scores','metrics'] if k in r}
        for r in result['records']]})
    print(F.canonical(audit))
    return audit


if __name__=='__main__':
    F.initialize(35)
    verify()
