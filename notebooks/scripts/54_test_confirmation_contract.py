"""Offline proof of mandatory review entry and complete confirmatory denominators."""
import ast
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import nbformat
import pytest

NB = Path(__file__).parents[1] / '54_new_question_confirmation.ipynb'


_spec=importlib.util.spec_from_file_location('question_evaluator',Path(__file__).with_name('53_question_evaluation.py'))
E=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(E)


def sha(v):
    return hashlib.sha256(v if isinstance(v,bytes) else json.dumps(v,sort_keys=True).encode()).hexdigest()


def write(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(v))


@pytest.mark.parametrize('index',[4,5])
def test_paid_cells_cannot_skip_the_actual_source_review_entry(tmp_path,index):
    nb=nbformat.read(NB,4)
    node=next(n for n in ast.parse(nb.cells[1].source).body if isinstance(n,ast.FunctionDef) and n.name=='require_reviewed_query_diff')
    ns=dict(PATH=tmp_path)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'actual entry guard','exec'),ns)
    with pytest.raises(RuntimeError,match='검색/답변 API'):
        exec(compile(nb.cells[index].source,'actual paid cell','exec'),ns)


def test_review_cannot_use_another_cards_PDF_for_the_same_fact(tmp_path):
    probe=dict(parser_results_sha256=sha({}),changed_pairs=[dict(fact_id='f1',card_key='a',source_pdf_sha256='pdf-a',requirement={})])
    write(tmp_path/'predicate_difference_probe.json',probe)
    review=dict(source_probe_sha256=sha((tmp_path/'predicate_difference_probe.json').read_bytes()),
        uncertain_or_entire_requested_target_exclusion_pairs=0,records=[dict(fact_id='f1',requirement_sha256=sha({}),card_key='b',source_confirmed_match=True,reason='fixture',
            source_support=[dict(path='data/raw/b.pdf',sha256='pdf-b',page=1,observation='fixture')])])
    write(tmp_path/'predicate_difference_review.json',review)
    write(tmp_path/'parser_results.json',{})
    P=SimpleNamespace(sha=sha,F=SimpleNamespace(read_json=lambda p:json.loads(p.read_text())))
    X=SimpleNamespace(load=lambda *a:None,frozen=write)
    Q=SimpleNamespace(facts_by_id=lambda:{'f1':dict(document_id='a',source_pdf_sha256='pdf-a')})
    ns=dict(PATH=tmp_path,X=X,P=P,Q=Q,source_inputs={'data/raw/b.pdf':'pdf-b'},source_page_counts={'b':1})
    with pytest.raises(AssertionError):
        exec(compile(nbformat.read(NB,4).cells[3].source,'actual source ownership gate','exec'),ns)


@pytest.fixture
def journal(tmp_path):
    pdf=tmp_path/'data/raw/test/card.pdf';pdf.parent.mkdir(parents=True);pdf.write_bytes(b'fixture')
    groups=['benefit']*8+['compound_detail']*8+['issuer_product']*8+['numeric']*4+['negative']*4
    queries=[dict(query_id=f'F{i+1:02}',question=f'q{i+1}',subgroup=g) for i,g in enumerate(groups)]
    rows=[];judgments=[];retrievals=[];docs=[dict(document_id='test/card')]
    for q in queries:
        for repeat in [1,2,3]:
            for pf,mode in [('common','S_ONLY')]+[(pf,m) for pf in ['benefit','parent'] for m in ['U','SEQ','PAR']]:
                identity=f"{q['query_id']}:S_ONLY:r{repeat}" if mode=='S_ONLY' else f"{q['query_id']}:{pf}:{mode}:r{repeat}"
                raw=dict(identity=identity,status='ok',answer={'text':'fixture'})
                write(tmp_path/'answers'/(identity+'.json'),raw)
                rows.append({**raw,'query_id':q['query_id'],'profile':pf,'mode':mode,'repeat':repeat})
                retrievals.append(dict(identity=identity,result=dict(cards=[dict(card_key='test/card')],evidence=[])))
                profiles=['benefit','parent'] if mode=='S_ONLY' else [pf]
                judgments.append(dict(identity=identity,answer_sha256=sha(rows[-1]),question_sha256=sha(q['question']),reason='fixture',verdict='PARTIAL',important_error=False,scope_error=False,
                    new_important_error_vs_U={p:False for p in profiles},condition_loss_vs_U={p:False for p in profiles},
                    source_support=[dict(card_key='test/card',path='data/raw/test/card.pdf',sha256=sha(pdf.read_bytes()),page=1,relation='retrieved_card',observation='fixture')]))
    manifest=dict(allowed_identities={'answer':[r['identity'] for r in rows]},source_input_sha256={'data/raw/test/card.pdf':sha(pdf.read_bytes())},
        source_page_counts={'test/card':1},corpus_documents_sha256=sha(docs),decision='frozen rule')
    completed=dict(answers=rows,retrievals=retrievals,manifest_sha256=sha(manifest))
    P=SimpleNamespace(sha=sha,F=SimpleNamespace(read_json=lambda p:json.loads(p.read_text()),write_json=write))
    X=SimpleNamespace(PROFILES=['benefit','parent'],frozen=write)
    M=SimpleNamespace(answer=lambda stage,identity,*a,**kw:json.loads((tmp_path/'answers'/(identity+'.json')).read_text()))

    def evaluate():
        rubric_by_query={q['query_id']:dict(question=q['question'],must_answer=['requested fact'],context_anchor=['extra context']) for q in queries}
        rubrics=dict(records=list(rubric_by_query.values()))
        for j in judgments:
            j.update(question_verdict=j['verdict'],material_omission=False,rubric_sha256=sha(rubric_by_query[j['identity'].split(':')[0]]),
                must_answer_coverage=dict(covered=['requested fact'] if j['verdict']=='PASS' else [],missing=[] if j['verdict']=='PASS' else ['requested fact'],not_applicable=[]),
                annotation_coverage=dict(covered=[],missing=['extra context'],not_applicable=[]))
        write(tmp_path/'execution_results.json',completed);write(tmp_path/'source_judgments.json',dict(records=judgments))
        ns=dict(PATH=tmp_path,ROOT=tmp_path,P=P,X=X,M=M,Q=SimpleNamespace(documents=lambda:docs),manifest=manifest,queries=queries,json=json,
                gold_by_query={q['query_id']:['test/card'] for q in queries},require_reviewed_query_diff=lambda:None,E=E,rubrics=rubrics,rubric_by_query=rubric_by_query)
        exec(compile(nbformat.read(NB,4).cells[-1].source,'actual 54 evaluation','exec'),ns)
        return json.loads((tmp_path/'evaluation_summary.json').read_text())['configurations']

    def get(q,mode,pf='benefit',repeat=1):
        identity=f'{q}:S_ONLY:r{repeat}' if mode=='S_ONLY' else f'{q}:{pf}:{mode}:r{repeat}'
        return next(j for j in judgments if j['identity']==identity)

    return SimpleNamespace(rows=rows,judgments=judgments,completed=completed,evaluate=evaluate,get=get)


def test_exact_full_denominators_and_net_gain_are_required(journal):
    journal.get('F01','SEQ')['verdict']='PASS'
    results=journal.evaluate();seq=next(r for r in results if r['profile']=='benefit' and r['mode']=='SEQ')
    assert seq['eligible_operating_candidate'] and seq['logical_denominator']==96 and seq['question_denominator']==32
    assert seq['question_wins_losses_ties']==dict(win=1,loss=0,tie=31)
    assert sum(g['questions'] for g in seq['subgroups'].values())==32


def test_aggregate_gain_cannot_hide_subgroup_decline(journal):
    for q in ['F01','F02']:journal.get(q,'SEQ')['verdict']='PASS'
    journal.get('F17','U')['verdict']='PASS'
    seq=next(r for r in journal.evaluate() if r['profile']=='benefit' and r['mode']=='SEQ')
    assert seq['question_wins_losses_ties']['win']-seq['question_wins_losses_ties']['loss']==1
    assert seq['subgroup_declines']==['issuer_product'] and not seq['eligible_operating_candidate']


@pytest.mark.parametrize('field',['new_important_error_vs_U','condition_loss_vs_U','scope_error'])
def test_gain_does_not_override_content_or_scope_guards(journal,field):
    journal.get('F01','SEQ')['verdict']='PASS'
    row=journal.get('F02','SEQ')
    if field=='scope_error':row[field]=True
    else:row[field]['benefit']=True
    if field=='new_important_error_vs_U':
        row['important_error']=True;journal.get('F02','U')['important_error']=True
    seq=next(r for r in journal.evaluate() if r['profile']=='benefit' and r['mode']=='SEQ')
    assert not seq['eligible_operating_candidate']


@pytest.mark.parametrize('mutation',['incomplete_repeat','missing_judgment','answer_hash','source_hash','page'])
def test_partial_or_unbound_evidence_cannot_pass_confirmation(journal,mutation):
    j=journal.judgments[0]
    if mutation=='incomplete_repeat':journal.completed['answers']=[r for r in journal.rows if r['repeat']<3]
    elif mutation=='missing_judgment':journal.judgments.pop()
    elif mutation=='answer_hash':j['answer_sha256']='stale'
    elif mutation=='source_hash':j['source_support'][0]['sha256']='stale'
    else:j['source_support'][0]['page']=2
    with pytest.raises(AssertionError):journal.evaluate()
