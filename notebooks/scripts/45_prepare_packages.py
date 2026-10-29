"""Offline construction of paired packages; no provider/search/model clients."""
from collections import Counter,defaultdict
import importlib.util
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('io45',Path(__file__).with_name('45_experiment_io.py'))
I=importlib.util.module_from_spec(spec);sys.modules[spec.name]=I;spec.loader.exec_module(I)
M=I.load('mapping45','45_evidence_supplement.py')
DB=I.load('database45','30_build_structured_db.py')
sys.path.insert(0,str(I.ROOT/'packages/rag-core/src'))
from pickcardu_rag.answering import measure_answer_payload,serialize_answer_payload


def payload(question,evidence):return serialize_answer_payload(question,evidence)


def validate_package(row, original, answer, chunks):
    def require(ok,message):
        if not ok:raise RuntimeError('Invalid fixed-card package: '+message)
    a,b=row['A'],row['B'];source=original['result'];q=row['question']
    require(a['cards']==source['cards'] and a['evidence']==source['evidence'],'A differs from saved retrieval')
    require(a['answer_row']==answer,'A answer changed')
    identity=lambda cards:[{k:v for k,v in c.items() if k!='evidence_count'} for c in cards]
    require(identity(b['cards'])==identity(a['cards']),'card identity/rank changed')
    keys=[c['card_key'] for c in a['cards']];ev=b['evidence']
    require(len({e['chunk_id'] for e in ev})==len(ev),'duplicate evidence')
    require(set(e['card_key'] for e in ev)==set(keys),'fixed card evidence scope changed')
    counts=Counter(e['card_key'] for e in ev)
    require(max(counts.values(),default=0)<=5,'per-card evidence cap')
    require(all(c['evidence_count']==counts[c['card_key']] for c in b['cards']),'evidence counts inconsistent')
    require(measure_answer_payload(q,ev)[0]<=64000,'payload budget')
    old={e['chunk_id']:e for e in a['evidence']};by_id={e['chunk_id']:e for e in ev}
    for card in keys:
        first=next(e for e in a['evidence'] if e['card_key']==card)
        require(first['chunk_id'] in by_id,'baseline first evidence lost')
    for e in ev:
        cid=e['chunk_id'];require(cid in chunks,'unknown chunk')
        require(chunks[cid]['document_id']==e['card_key'],'chunk ownership')
        card=next(c for c in a['cards'] if c['card_key']==e['card_key'])
        require(all(e[field]==card[field] for field in ['card_name','issuer']),'LLM-visible card identity changed')
        if cid in old:
            require({k:v for k,v in e.items() if k!='rank'}=={k:v for k,v in old[cid].items() if k!='rank'},'baseline evidence modified')
        else:
            require(e['text']==chunks[cid]['text'],'source chunk text modified')
            require(e['page_num']==min(chunks[cid]['metadata']['source_pages']),'source page mismatch')
    for side in [a,b]:require(side['payload_sha256']==I.digest(payload(q,side['evidence']).encode()),'payload hash')
    if original['fallback']:require(payload(q,a['evidence'])==payload(q,b['evidence']),'fallback must remain unchanged')


def validate_prepared(prepared):
    import json
    original=I.read(I.DATA/'42_expanded_query_evaluation/combined_results.json')
    retrieval={(r['query_id'],r['profile']):r for r in original['retrievals'] if r['mode']=='S_THEN_U'}
    answers={(r['query_id'],r['profile']):r for r in original['answers'] if r['mode']=='S_THEN_U'}
    questions={r['query_id']:r['question'] for r in I.read(I.DATA/'42_expanded_query_evaluation/questions.json')['queries']}
    chunks={p:{r['chunk_id']:r for r in map(json.loads,(I.DATA/'31_operational_chunking_comparison'/f'{p}_chunks.jsonl').read_text().splitlines())} for p in ['card_page_section_benefit','parent_child_bundle']}
    facts={r['fact_id']:r for r in DB.load_facts(I.DATA/'30_structured_benefit_db')}
    documents={r['document_id']:r for r in map(json.loads,(I.DATA/'30_structured_benefit_db/documents.jsonl').read_text().splitlines())}
    by_card={p:defaultdict(list) for p in chunks}
    for profile in chunks:
        for chunk in chunks[profile].values():by_card[profile][chunk['document_id']].append(chunk)
    for stored in prepared['mapping'].values():
        fact=facts[stored['fact_id']];profile=stored['profile'];card=fact['document_id']
        expected={'profile':profile,**M.map_fact(fact,documents[card]['pages'],by_card[profile][card])}
        if stored!=expected:raise RuntimeError('Stored mapping differs from source-bound reconstruction')
    keys=[(r['query_id'],r['profile']) for r in prepared['records']]
    if len(keys)!=96 or len(set(keys))!=96 or set(keys)!=set(retrieval):raise RuntimeError('Incomplete paired matrix')
    for row in prepared['records']:
        key=(row['query_id'],row['profile'])
        if row['question']!=questions[key[0]]:raise RuntimeError('Original question changed')
        validate_package(row,retrieval[key],answers[key],chunks[key[1]])
        allowed=set()
        for link in row['links']:
            m=prepared['mapping'][link['mapping_key']]
            if m['fact_id'] not in retrieval[key]['selection']['matching_fact_ids'].get(m['document_id'],[]):
                raise RuntimeError('Source fact not in fully matching structured selection')
            if m['status']=='verified':allowed.update(m['chunk_ids'])
        new_ids=set(e['chunk_id'] for e in row['B']['evidence'])-set(e['chunk_id'] for e in row['A']['evidence'])
        if not new_ids<=allowed:raise RuntimeError('Supplement outside verified source mapping')
    return {'validated_packages':96,'fixed_cards_and_payloads':True}


def validate_change_audit(prepared,audit):
    if audit['sha256']!=I.digest({k:v for k,v in audit.items() if k!='sha256'}):raise RuntimeError('Effect audit digest')
    if audit['packages_sha256']!=I.digest((I.OUT/'prepared_packages.json').read_bytes()):raise RuntimeError('Stale effect audit')
    expected=[]
    for row in prepared['records']:
        a={e['chunk_id'] for e in row['A']['evidence']};b={e['chunk_id'] for e in row['B']['evidence']}
        kind=('replacement' if a-b else 'append_only') if row['A']['payload_sha256']!=row['B']['payload_sha256'] else row['treatment_status']
        expected.append({'query_id':row['query_id'],'profile':row['profile'],'effect_type':kind,
                         'added_chunk_ids':sorted(b-a),'removed_chunk_ids':sorted(a-b),
                         'A_evidence_count':len(row['A']['evidence']),'B_evidence_count':len(row['B']['evidence'])})
    if audit['records']!=expected or audit['counts']!=dict(Counter(r['effect_type'] for r in expected)):
        raise RuntimeError('Effect audit differs from actual fixed-card repacking')


def sources():
    names=['30_structured_benefit_db/structured.sqlite','30_structured_benefit_db/facts.jsonl',
           '30_structured_benefit_db/documents.jsonl','31_operational_chunking_comparison/fact_chunk_map.jsonl',
           '31_operational_chunking_comparison/card_page_section_benefit_chunks.jsonl',
           '31_operational_chunking_comparison/parent_child_bundle_chunks.jsonl',
           '42_expanded_query_evaluation/combined_results.json','42_expanded_query_evaluation/parser_results.json',
           '42_expanded_query_evaluation/questions.json','42_expanded_query_evaluation/dataset.json',
           '42_expanded_query_evaluation/execution_protocol.json','43_offline_validation/review_contract.json']
    return {str((I.DATA/name).relative_to(I.ROOT)):I.digest((I.DATA/name).read_bytes()) for name in names}


def prepare():
    I.preserve()
    src=sources();prior=I.DATA/'42_expanded_query_evaluation'
    I.load('bound_input45','42_input_binding.py').verify_inputs(prior)
    combined=I.read(prior/'combined_results.json')
    # The runner sees original public questions and actual plans, not gold plans.
    public=I.read(prior/'questions.json')['queries']
    plans={r['query_id']:r for r in I.read(prior/'parser_results.json')['records']}
    baseline={(r['query_id'],r['profile']):r for r in combined['retrievals'] if r['mode']=='S_THEN_U'}
    answers={(r['query_id'],r['profile']):r for r in combined['answers'] if r['mode']=='S_THEN_U'}
    assert len(public)==48 and len(baseline)==96 and len(answers)==96
    facts=DB.load_facts(I.DATA/'30_structured_benefit_db')
    by_card=defaultdict(list)
    for fact in facts:by_card[fact['document_id']].append(fact)
    import json
    documents={r['document_id']:r for r in map(json.loads,(I.DATA/'30_structured_benefit_db/documents.jsonl').read_text().splitlines())}
    profiles=['card_page_section_benefit','parent_child_bundle']
    chunks={p:{r['chunk_id']:r for r in map(json.loads,(I.DATA/'31_operational_chunking_comparison'/f'{p}_chunks.jsonl').read_text().splitlines())} for p in profiles}
    chunk_cards={p:defaultdict(list) for p in profiles}
    for profile in profiles:
        for chunk in chunks[profile].values():chunk_cards[profile][chunk['document_id']].append(chunk)
    mapping={};packages=[]
    for q in public:
        plan=plans[q['query_id']]['plan'];selection=DB.select_cards(facts,plan)
        for profile in profiles:
            key=(q['query_id'],profile);a=baseline[key];answer=answers[key]
            assert a['status']=='ok' and answer['status']=='ok'
            assert a['selection']==selection
            assert answer['retrieval_sha256']==I.digest({k:v for k,v in a.items() if k not in ['metrics','subgroup']})
            result=a['result'];groups=[];links=[];candidate_fact_ids=[]
            for card in result['cards']:
                card_key=card['card_key']
                if a['fallback']:continue
                matching=set(selection['matching_fact_ids'].get(card_key,[]))
                for gi,group in enumerate(plan['groups']):
                    hits=[[f for f in by_card[card_key] if f['fact_id'] in matching and DB._evaluate_fact(f,r).status=='true'] for r in group]
                    if not group or not all(hits):continue
                    for ri,req_facts in enumerate(hits):
                        rows=[]
                        for fact in sorted(req_facts,key=lambda f:f['ordinal']):
                            mk=profile+':'+fact['fact_id'];candidate_fact_ids.append(mk)
                            if mk not in mapping:
                                mapping[mk]={'profile':profile,**M.map_fact(fact,documents[card_key]['pages'],chunk_cards[profile][card_key])}
                            link=mapping[mk]
                            links.append({'card_key':card_key,'group':gi,'requirement':ri,'mapping_key':mk})
                            if link['status']!='verified':continue
                            for cid in link['chunk_ids']:
                                chunk=chunks[profile][cid];meta=chunk['metadata']
                                if cid in {e['chunk_id'] for e in result['evidence']}:
                                    row=next(e for e in result['evidence'] if e['chunk_id']==cid)
                                else:
                                    row={'chunk_id':cid,'card_key':card_key,'card_name':card['card_name'],'issuer':card['issuer'],
                                         'page_num':min(meta['source_pages']),'text':chunk['text'],'level':chunk['level'],
                                         'section':meta.get('section') or ' > '.join(meta.get('heading_path',[])),
                                         'rank':1,'score':card['score']}
                                rows.append(row)
                        groups.append(rows)
            b=M.pack(q['question'],result['cards'],result['evidence'],groups,
                     lambda e:measure_answer_payload(q['question'],e)[0])
            before=payload(q['question'],result['evidence']);after=payload(q['question'],b['evidence'])
            changed=before!=after
            assert [(c['card_key'],c['rank']) for c in b['cards']]==[(c['card_key'],c['rank']) for c in result['cards']]
            assert measure_answer_payload(q['question'],b['evidence'])[0]<=64000
            assert max(Counter(e['card_key'] for e in b['evidence']).values(),default=0)<=5
            assert all(e['card_key'] in {c['card_key'] for c in result['cards']} for e in b['evidence'])
            assert all(next(e for e in result['evidence'] if e['card_key']==c['card_key'])['chunk_id'] in {e['chunk_id'] for e in b['evidence']} for c in result['cards'])
            status=('fallback_unchanged' if a['fallback'] else 'changed' if changed else
                    'mapping_unavailable' if candidate_fact_ids and not any(mapping[x]['status']=='verified' for x in candidate_fact_ids) else 'same_payload')
            packages.append({'query_id':q['query_id'],'question':q['question'],'profile':profile,
                'fallback':a['fallback'],'treatment_status':status,'links':links,'excluded':b['excluded'],
                'A':{'cards':result['cards'],'evidence':result['evidence'],'payload_sha256':I.digest(before.encode()),'answer_row':answer},
                'B':{'cards':b['cards'],'evidence':b['evidence'],'payload_sha256':I.digest(after.encode())},
                'baseline_retrieval_sha256':I.digest(a),'baseline_answer_sha256':I.digest(answer)})
    output={'source_hashes':src,'records':packages,'mapping':mapping,'new_parser_calls':0,'new_embeddings':0,
            'search_and_reranker_calls':0,'logical_conditions':192,'mode':'fixed-card evidence replacement only'}
    I.write(I.OUT/'prepared_packages.json',output)
    I.freeze('review_contract.json',{'core_request_contract':I.read(I.DATA/'43_offline_validation/review_contract.json'),
        'criteria':['core_request_PASS_PARTIAL_FAIL_UNCERTAIN','source_supported_conditions','unsupported_numeric_or_condition_claims',
                    'owned_citations','answer_success','diagnostic_expected_behavior'],
        'guard':'any per-row PASS loss, added wrong-core/numeric/condition claim, diagnostic failure or success-to-failure blocks improvement selection',
        'mapping_metric':'source line coverage of planned fully-matching fact only; diagnostic, not independent relevance truth',
        'blindness':'mode names hidden; prior session knowledge possible, not human blind or independent holdout',
        'unchanged_payload':'reuse A answer; no new generation', 'comparison_limit':'A historical vs B newly generated, no causality or statistical promotion claim'})
    summary={'packages':len(packages),'logical_conditions':192,'treatment':dict(Counter(p['treatment_status'] for p in packages)),
             'mapping':dict(Counter(r['status'] for r in mapping.values())),
             'by_profile':{p:{'treatment':dict(Counter(r['treatment_status'] for r in packages if r['profile']==p)),
                              'mapping':dict(Counter(r['status'] for r in mapping.values() if r['profile']==p))} for p in profiles}}
    I.write(I.OUT/'preparation_summary.json',summary)
    print(summary)
    return output


if __name__=='__main__':prepare()
