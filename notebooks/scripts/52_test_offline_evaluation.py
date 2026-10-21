"""Execute the actual offline aggregation cell; no API or model load."""
import contextlib
import io
import json
import math
import statistics
import collections
from pathlib import Path
from types import SimpleNamespace

import nbformat


def test_sequential_scope_diagnostics_do_not_label_other_search_modes(tmp_path):
    profiles=['benefit','parent'];queries=[dict(query_id=f'Q{i}',subgroup='fixture') for i in range(40)]
    rows=[];retrieved={};judged={}
    for q in queries:
        for pf,mode in [('common','S_ONLY')]+[(p,m) for p in profiles for m in ['U','SEQ','PAR']]:
            identity=f"{q['query_id']}:{pf}:{mode}"
            rows.append(dict(identity=identity,query_id=q['query_id'],profile=pf,mode=mode,
                             events=[],answer_ready_wall_seconds=1.,status='ok'))
            retrieved[identity]=dict(result=dict(cards=[dict(card_key='card')],evidence=[]),
                original_scope=dict(card_keys=None),route=dict(scope=['card'],reason='matched'))
            judged[identity]=dict(question_verdict='PASS',annotation_coverage={},important_error=False,scope_error=False)
    price=SimpleNamespace(price_records=lambda _:[],aggregate=lambda _:dict(record_count=0,known_cost_usd='0'))
    P=SimpleNamespace(PRICE=price,F=SimpleNamespace(read_json=lambda _:None))
    ns=dict(rows=rows,retrieved=retrieved,judged=judged,queries=queries,
        qmap={q['query_id']:q for q in queries},source_questions=[q['query_id'] for q in queries[:37]],
        valid={q['query_id']:{'card'} for q in queries},PATH=tmp_path,ROOT=tmp_path,
        P=P,X=SimpleNamespace(PROFILES=profiles,Q=SimpleNamespace(documents=lambda:[dict(document_id=str(i)) for i in range(106)])),
        math=math,statistics=statistics,collections=collections,json=json)
    path=Path(__file__).parents[1]/'52_corrected_four_retrieval_modes_evaluation.ipynb'
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(nbformat.read(path,4).cells[2].source,'actual offline aggregation','exec'),ns)
    assert sum(r['logical_denominator'] for r in ns['quality'])==280
    for comparison in ns['pairs']:
        for row in comparison['records']:
            assert row['actual_scope_reduction'] is (True if comparison['mode']=='SEQ' else None)
            assert row['same_U_SEQ_body'] is (True if comparison['mode']=='SEQ' else None)
