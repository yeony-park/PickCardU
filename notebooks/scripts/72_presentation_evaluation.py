"""Bind manual72 semantic judgments; aggregate format quality and resources.

This validates recorded evidence and counts, never infers semantic correctness.
It does not generate HTML or make provider calls.
"""
from collections import Counter
from decimal import Decimal
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from itertools import product
from functools import lru_cache
import importlib.util
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'notebooks/data/72_input_presentation'


def read(path):return json.loads(Path(path).read_text())
def sha(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def source_catalog():
    import fitz
    records=read(ROOT/'notebooks/data/71_pre_generation_evidence_audit/source_catalog.json')['records']
    supplement=read(BASE/'source_catalog_supplement.json')
    for name,digest in supplement['files_sha256'].items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Supplemental source proof changed')
    for r in supplement['records']:
        if r['source_status']!='verified' or not r.get('reviewed_by') or r['render_sha256']!=r['image_sha256']:
            raise ValueError('Supplemental source not reviewed')
        pdf=ROOT/('data/raw/'+r['card_key']+'.pdf')
        if hashlib.sha256(pdf.read_bytes()).hexdigest()!=r['source_pdf_sha256']:raise ValueError('Supplemental source owner differs')
        if supplement['files_sha256'].get(r['image_path'])!=r['image_sha256'] or r['render_scale']!=2:
            raise ValueError('Supplemental image not bound')
        with fitz.open(pdf) as doc:
            if not 1<=r['source_page']<=len(doc):raise ValueError('Supplemental source page invalid')
            rendered=doc[r['source_page']-1].get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
        if hashlib.sha256(rendered).hexdigest()!=r['render_sha256']:raise ValueError('Supplemental image differs from actual PDF')
    all_records=records+supplement['records'];result={r['condition_id']:r for r in all_records}
    if len(result)!=len(all_records):raise ValueError('Duplicate source reference')
    return result


def surfaces(answer):
    if answer is None:return {}
    return dict(answer_text=answer['answer_text'],**{f'recommendations.{i}.reason':r['reason'] for i,r in enumerate(answer['recommendations'])})


def validate_review(review,row,contract,catalog,payload,owners):
    visible=surfaces(row['answer'])
    if row['query_id']!=contract['query_id'] or row['question']!=contract['question']:
        raise ValueError('Question contract differs from actual answer')
    actual_input=[dict(role='user',content=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')))]
    if row['payload_sha256']!=sha(actual_input):raise ValueError('Actual payload binding differs')
    if review['identity']!=row['identity'] or review['answer_row_sha256']!=sha(row) or review['visible_sha256']!=sha(visible):
        raise ValueError('Review identity or actual visible answer changed')
    if not review.get('reviewed_by') or not review.get('reason'):raise ValueError('Missing semantic reviewer/reason')
    wanted={r['id'] for r in contract['inherited_direct_requirements']}
    actual=review['requirements']
    if len(actual)!=len(wanted) or {r['id'] for r in actual}!=wanted:raise ValueError('Dropped direct requirement')
    for r in actual:
        if r['status'] not in ('satisfied','missing','uncertain') or not r.get('reason'):raise ValueError('Invalid requirement judgment')
        for q in r.get('quotes',[]):
            if q['surface'] not in visible or not q['quote'] or q['quote'] not in visible[q['surface']]:raise ValueError('Visible quote absent')
        if r['status']=='satisfied' and not r.get('quotes'):raise ValueError('Satisfied requirement has no visible quote')
    if row['status']=='ok' and not review['assertions']:raise ValueError('No visible assertion audit')
    for a in review['assertions']:
        if a['status'] not in ('correct','wrong','uncertain') or not a.get('reason'):raise ValueError('Invalid assertion judgment')
        if a['surface'] not in visible or not a['quote'] or a['quote']!=visible[a['surface']]:raise ValueError('Whole assertion surface audit absent')
        allowed_owners=set(owners.values()) if a['surface']=='answer_text' else {row['answer']['recommendations'][int(a['surface'].split('.')[1])]['card_key']}
        if not a.get('relation_review'):raise ValueError('Benefit/condition relationship not manually reviewed')
        if not a.get('source_refs') or not all(r in catalog for r in a['source_refs']):raise ValueError('Assertion lacks registered source')
        if not a.get('source_quotes'):raise ValueError('Assertion has no literal source review quote')
        if set(a['source_refs'])!={q['ref'] for q in a['source_quotes']}:raise ValueError('Unused source reference')
        for q in a['source_quotes']:
            if q['ref'] not in a['source_refs'] or not q['quote'] or not any(q['quote'] in text for text in catalog[q['ref']]['literal_quotes']):
                raise ValueError('Source quote absent')
            if catalog[q['ref']]['card_key'] not in allowed_owners or catalog[q['ref']]['source_status']!='verified':
                raise ValueError('Source quote owner or verified status differs')
        if a['input_support'] not in ('supported','unsupported','uncertain'):raise ValueError('Input support not reviewed')
        for q in a.get('input_quotes',[]):
            item=next((e for e in payload['evidence'] if e['evidence_id']==q['evidence_id']),None)
            if item is None or not q['quote'] or q['quote'] not in item['text']:raise ValueError('Actual input quote absent')
            if a['input_support']=='supported' and owners[item['card_ref']] not in allowed_owners:
                raise ValueError('Input quote owner differs from the reviewed surface')
        if a['input_support']=='supported' and not a.get('input_quotes'):raise ValueError('Supported assertion has no actual input quote')
        if a['status']=='wrong' and not a.get('error_type'):raise ValueError('Confirmed error has no type')
        if a['status']=='wrong' and (not a.get('error_quote') or a['error_quote'] not in a['quote']):raise ValueError('Confirmed error has no literal visible span')
        if a['status']=='uncertain' and (not a.get('uncertainty_type') or not a.get('uncertainty_quote') or a['uncertainty_quote'] not in a['quote']):raise ValueError('Uncertainty has no literal visible span')
    if row['status']=='ok' and {a['surface'] for a in review['assertions']}!=set(visible):
        raise ValueError('Visible body or recommendation reason omitted from assertion audit')
    direct=row['status']=='ok' and all(r['status']=='satisfied' for r in actual)
    accurate=row['status']=='ok' and all(a['status']=='correct' for a in review['assertions'])
    grounded=row['status']=='ok' and all(a['input_support']=='supported' for a in review['assertions'])
    leak=any(re.search(r'(?<![A-Za-z0-9])[CFP][1-9][0-9]*(?![A-Za-z0-9])',v) for v in visible.values())
    return dict(identity=row['identity'],query_id=row['query_id'],profile=row['profile'],arm=row['arm'],repeat=row['repeat'],
        technical=row['status']=='ok',direct=direct,accurate=accurate,grounded=grounded,passed=direct and accurate,
        alias_leak=leak,confirmed_errors=sorted({kind for a in review['assertions'] if a['status']=='wrong' for kind in a['error_type'].split('+')}),
        uncertain_assertions=sum(a['status']=='uncertain' for a in review['assertions']),
        uncertain_types=sorted({a.get('uncertainty_type','unspecified') for a in review['assertions'] if a['status']=='uncertain'}),
        payment_wording_sensitivity_pass=bool(direct and row['status']=='ok'
            and not any(a['status']=='wrong' for a in review['assertions'])
            and all(a['status']=='correct' or a.get('uncertainty_type')=='payment_scope_wording' for a in review['assertions'])))


def stats(values):
    if not values:return dict(n=0,mean=None,p95=None,stdev=None)
    v=sorted(values)
    return dict(n=len(v),mean=statistics.mean(v),p95=v[math.ceil(.95*len(v))-1],
                stdev=statistics.pstdev(v),stdev_denominator=len(v))


def quality_summary(scored):
    validate_matrix(scored,{'C01','C10','C12','C13','C14','C16','C17','C18'})
    result=[]
    for profile in sorted({r['profile'] for r in scored}):
        for arm in 'ABC':
            rows=[r for r in scored if r['profile']==profile and r['arm']==arm]
            if len(rows)!=24:raise ValueError('Incomplete quality matrix')
            counts={k:sum(r[k] for r in rows) for k in ('technical','direct','accurate','grounded','passed','alias_leak')}
            clean=[r for r in rows if r['query_id']!='C18']
            byq={q:sum(r['passed'] for r in rows if r['query_id']==q) for q in sorted({r['query_id'] for r in rows})}
            result.append(dict(profile=profile,arm=arm,n=24,**counts,pass_rate=counts['passed']/24,
                clean7_passed=sum(r['passed'] for r in clean),clean7_n=21,pass_by_question=byq,
                confirmed_wrong_answers=sum(bool(r['confirmed_errors']) for r in rows),
                uncertain_answers=sum(r['uncertain_assertions']>0 for r in rows),
                payment_wording_sensitivity_passed=sum(r['payment_wording_sensitivity_pass'] for r in rows),
                error_categories_by_question={q:sorted({e for r in rows if r['query_id']==q for e in r['confirmed_errors']}) for q in byq}))
    return result


def validate_matrix(rows,query_ids):
    expected=set(product(query_ids,('card_page_section_benefit','parent_child_bundle'),'ABC',(1,2,3)))
    actual=[(r['query_id'],r['profile'],r['arm'],r['repeat']) for r in rows]
    if len(actual)!=len(expected) or len(set(actual))!=len(actual) or set(actual)!=expected:
        raise ValueError('Exact question/profile/format/repeat matrix differs')
    for r in rows:
        if r['identity']!=f"{r['query_id']}:{r['profile']}:{r['arm']}:r{r['repeat']}":
            raise ValueError('Identity differs from matrix coordinates')
    return True


def candidate_guards(base,candidate):
    qloss=[q for q,v in base['pass_by_question'].items() if candidate['pass_by_question'][q]<v]
    newerrors={q:sorted(set(v)-set(base['error_categories_by_question'][q])) for q,v in candidate['error_categories_by_question'].items()}
    newerrors={q:v for q,v in newerrors.items() if v}
    reasons=[]
    if qloss:reasons.append('question_pass_regression')
    if candidate['direct']<base['direct']:reasons.append('direct_regression')
    if candidate['accurate']<base['accurate']:reasons.append('accuracy_regression')
    if candidate['confirmed_wrong_answers']>base['confirmed_wrong_answers']:reasons.append('more_confirmed_wrong_answers')
    if newerrors:reasons.append('new_question_error_category')
    if candidate['alias_leak']:reasons.append('alias_display_leak')
    if candidate['clean7_passed']<base['clean7_passed']:reasons.append('clean7_regression')
    return dict(eligible=not reasons,reasons=reasons,question_losses=qloss,new_error_categories=newerrors)


def bind_manual_notes():
    """Attach literal review context to already-authored manual meaning decisions.

    Every displayed surface and requirement needs its explicit manual judgment
    and selected literal input/source quotations. No default supported state.
    """
    notes=read(BASE/'manual_review_notes.json')['records'];jobs=read(BASE/'jobs.json')['records']
    contracts={r['query_id']:r for r in read(BASE/'question_contract.json')['records']}
    reviews=[]
    for job in jobs:
        if job['identity'] not in notes:continue
        note=notes[job['identity']];row=read(BASE/'answers'/(job['identity']+'.json'));visible=surfaces(row['answer'])
        expected=note.get('answer_row_sha256')
        if expected is None or expected!=sha(row):raise ValueError('Manual meaning note is not bound to the reviewed answer')
        requirements=[];assertions=[]
        if row['status']=='ok':
            if set(note.get('surface_reviews',{}))!=set(visible) or not note.get('requirement_reviews'):
                raise ValueError('Missing explicit surface/requirement citations and judgments')
            requirements=copy.deepcopy(note['requirement_reviews'])
            for surface,text in visible.items():
                assertion=copy.deepcopy(note['surface_reviews'][surface])
                if assertion.get('surface')!=surface or assertion.get('quote')!=text:
                    raise ValueError('Explicit assertion audit does not cover whole visible surface')
                if 'input_support' not in assertion or not assertion.get('relation_review'):
                    raise ValueError('Missing explicit input support or benefit-relation review')
                assertions.append(assertion)
        else:
            requirements=[dict(id=r['id'],status='missing',reason='No complete technical answer',quotes=[])
                for r in contracts[job['query_id']]['inherited_direct_requirements']]
        reviews.append(dict(identity=job['identity'],answer_row_sha256=sha(row),visible_sha256=sha(visible),
            reviewed_by=note['reviewed_by'],reason=note['reason'],requirements=requirements,assertions=assertions,
            manual_note_sha256=sha(note),context_disclosure='Explicit manual surface/requirement relations; code checks literal provenance, not semantic truth.'))
        direct=bool(visible) and all(r['status']=='satisfied' for r in requirements)
        accurate=bool(visible) and all(a['status']=='correct' for a in assertions)
        grounded=bool(visible) and all(a['input_support']=='supported' for a in assertions)
        if (direct,accurate,grounded)!=(note['direct'],note['accurate'],note['grounded']):raise ValueError('Manual note and detailed judgments disagree')
    return dict(records=reviews,semantics='Explicit manual meaning judgments plus mechanically bound quotations; not automatic semantic scoring')


def evaluate():
    jobs=read(BASE/'jobs.json')['records'];raw_rows=read(BASE/'execution_results.json')['records']
    rows={r['identity']:r for r in raw_rows}
    reviews=read(BASE/'manual_reviews.json')['records'];by_review={r['identity']:r for r in reviews}
    contract={r['query_id']:r for r in read(BASE/'question_contract.json')['records']}
    catalog=source_catalog()
    payloads=read(BASE/'payloads.json')
    sidecars=read(BASE/'audit_sidecars.json')
    validate_matrix(jobs,set(contract));validate_matrix(raw_rows,set(contract))
    notes=read(BASE/'manual_review_notes.json')['records']
    for identity,review in by_review.items():
        if identity not in notes or review['manual_note_sha256']!=sha(notes[identity]):raise ValueError('Manual review differs from latest adjudicated note')
        if notes[identity].get('semantic_review_status','').startswith('pending'):raise ValueError('Pending semantic boundary review')
    if len(rows)!=len(jobs) or len(reviews)!=len(jobs) or set(rows)!=set(by_review) or set(rows)!={j['identity'] for j in jobs}:
        raise ValueError('Missing, duplicate or extra output/review identity')
    scored=[validate_review(by_review[j['identity']],rows[j['identity']],contract[j['query_id']],catalog,payloads[j['payload_key']],sidecars[j['payload_key']]['reverse']['cards']) for j in jobs]
    summary=quality_summary(scored)
    selection=[]
    for profile in sorted({r['profile'] for r in summary}):
        baseline=next(r for r in summary if r['profile']==profile and r['arm']=='A')
        for arm in 'BC':
            candidate=next(r for r in summary if r['profile']==profile and r['arm']==arm)
            changes={q:candidate['pass_by_question'][q]-baseline['pass_by_question'][q] for q in baseline['pass_by_question']}
            selection.append(dict(profile=profile,arm=arm,**candidate_guards(baseline,candidate),
                wins=sum(v>0 for v in changes.values()),losses=sum(v<0 for v in changes.values()),ties=sum(v==0 for v in changes.values()),
                question_pass_change=changes))
    return dict(records=scored),dict(conditions=summary,candidate_guards=selection,semantic_judgments='Codex manual, provenance/counts validated by code',
        repeats='3 generations from one fixed search input; repeats are not independent questions',
        source_contamination='C18 retained in primary, excluded in clean7 sensitivity',production_adoption=False)


@lru_cache(maxsize=1)
def runtime():
    path=ROOT/'notebooks/scripts/72_presentation_runtime.py'
    spec=importlib.util.spec_from_file_location('runtime72_analysis',path)
    value=importlib.util.module_from_spec(spec);sys.modules[spec.name]=value;spec.loader.exec_module(value)
    return value


def resource_analysis():
    r=runtime();rows=read(BASE/'execution_results.json')['records'];jobs=read(BASE/'jobs.json')['records']
    validate_matrix(rows,{'C01','C10','C12','C13','C14','C16','C17','C18'})
    raw={p.stem:read(p) for p in (BASE/'api_cache').glob('*.json')}
    event_keys=[e['key'] for row in rows for e in row['events']]
    if len(event_keys)!=len(set(event_keys)) or set(event_keys)!=set(raw):raise ValueError('Physical journal coverage differs')
    prices=r.T.R.P.PRICE.price_records([('72',str((BASE/'api_cache'/(key+'.json')).relative_to(ROOT)),v) for key,v in raw.items()])
    price_by_key={Path(v['path']).stem:v for v in prices};total=r.T.R.P.PRICE.aggregate(prices)
    if total['unknown_cost_records'] or len(raw)>288:raise ValueError('Unknown cost or exceeded physical cap')
    starting=Decimal(read(BASE/'cost_reservation.json')['starting_budget_debit_usd'])
    cumulative=r.T.budget_ledger()['budget_debit_usd']
    if Decimal(cumulative)!=starting+Decimal(total['known_cost_usd']):raise ValueError('Stage and cumulative ledger differ')
    conditions=[];payloads=read(BASE/'payloads.json')
    for profile in ('card_page_section_benefit','parent_child_bundle'):
        for arm in 'ABC':
            rr=[v for v in rows if v['profile']==profile and v['arm']==arm];keys=[e['key'] for v in rr for e in v['events']]
            first=[raw[v['events'][0]['key']] for v in rr]
            priced=r.T.R.P.PRICE.aggregate([price_by_key[k] for k in keys])
            provider=[sum(raw[e['key']]['wall_seconds'] for e in v['events']) for v in rr]
            sizes=[len(json.dumps(payloads[j['payload_key']],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()) for j in jobs if j['profile']==profile and j['arm']==arm]
            conditions.append(dict(profile=profile,arm=arm,logical_answers=len(rr),physical_requests=len(keys),
                technical_successes=sum(v['status']=='ok' for v in rr),retried_answers=sum(len(v['events'])==2 for v in rr),
                cost_usd=priced['known_cost_usd'],per_logical_cost_usd=str(Decimal(priced['known_cost_usd'])/len(rr)),tokens=priced['tokens'],
                first_attempt_input_tokens=stats([v['usage']['input_tokens'] for v in first]),
                input_payload_bytes=stats(sizes),answer_ready_seconds=stats([v['answer_ready_wall_seconds'] for v in rr]),
                provider_and_parse_seconds=stats(provider),CPU_seconds=sum(v['answer_cpu_seconds'] for v in rr),
                output_reasoning_tokens=sum(raw[k]['usage']['output_tokens_details'].get('reasoning_tokens',0) for k in keys)))
    failures=[]
    if sum(Decimal(c['cost_usd']) for c in conditions)!=Decimal(total['known_cost_usd']) or sum(c['physical_requests'] for c in conditions)!=len(raw):
        raise ValueError('Condition resource partitions differ from the physical ledger')
    for row in rows:
        if row['status']=='ok':continue
        failures.append(dict(identity=row['identity'],physical_attempts=len(row['events']),
            failure_reasons=[raw[e['key']].get('failure_reason') for e in row['events']],
            output_tokens=[raw[e['key']]['usage']['output_tokens'] for e in row['events']],
            reasoning_tokens=[raw[e['key']]['usage']['output_tokens_details'].get('reasoning_tokens') for e in row['events']],
            final_answer_absent=True))
    return dict(conditions=conditions,stage72=total,cumulative_budget_debit_usd=cumulative,
        starting_budget_debit_usd=str(starting),maximum_reserved_new_usd=read(BASE/'cost_reservation.json')['maximum_new_usd'],
        remaining_budget_usd=str(Decimal(8)-Decimal(cumulative)),physical_requests=len(raw),logical_answers=len(rows),
        final_technical_failures=failures,resources=read(BASE/'answer_resources.json'),
        TTFT=None,browser_display_seconds=None,online_full_pipeline_seconds=None,
        manual_review_time=None,invoice_cost_usd=None,new_parser_calls=0,new_embedding_calls=0,new_retrievals=0,
        rates=read(BASE/'cost_reservation.json')['pricing'],
        note='Frozen historical unit rates, observed cache mix and all physical attempts; not invoice, cold-cache serving cost or operating SLA.')


def verify_execution():
    """Read-only replay; never calls run(), which would replace timing receipts."""
    r=runtime();start=time.perf_counter();manifest=read(BASE/'execution_manifest.json')
    r.T.guard_manifest(manifest)
    r.T.E.verify_file_map(read(BASE/'pre_paid_acceptance.json')['files_sha256'],ROOT,fresh=True)
    jobs=read(BASE/'jobs.json')['records'];rows=read(BASE/'execution_results.json')['records'];contract=read(BASE/'answer_payload_contract.json')
    validate_matrix(jobs,set(read(BASE/'question_contract.json')['records'][i]['query_id'] for i in range(8)))
    byid={v['identity']:v for v in rows}
    for job in jobs:
        row=read(BASE/'answers'/(job['identity']+'.json'))
        if row!=byid[job['identity']]:raise ValueError('Execution union differs from answer checkpoint')
        r.validate_saved(row,job,manifest,contract)
    protected=r.T.E.verify_file_map(read(BASE/'preservation_before.json')['files_sha256'],ROOT,fresh=True)
    scored,summary=evaluate()
    if scored!=read(BASE/'evaluation_results.json') or summary!=read(BASE/'evaluation_summary.json'):
        raise ValueError('Saved quality aggregation differs from current manual reviews')
    return dict(status='passed_actual_requests_outputs_and_manual_binding',logical_answers=len(rows),protected_files=protected,
        actual_requests=len(list((BASE/'api_cache').glob('*.json'))),new_API_requests=0,
        read_only_verification_wall_seconds=time.perf_counter()-start,original_generation_resources_not_overwritten=True)
