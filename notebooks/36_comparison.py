"""Selected six-condition comparison and separate unknown/grounding diagnostics."""
from __future__ import annotations

from collections import Counter
import importlib.util
from pathlib import Path
import sys
import time

spec=importlib.util.spec_from_file_location('comparison_common',Path(__file__).with_name('34_followup_common.py'))
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)


def unknown_policy(plan, selection):
    applicable=(plan.get('route')=='structured_then_unstructured' and bool(plan.get('groups'))
                and not plan.get('unresolved'))
    strict=set(selection['eligible_cards']);unknown=set(selection['unknown_cards'])
    if plan.get('card_keys'):
        scope=set(plan['card_keys']);strict &= scope;unknown &= scope
    return {'applicable':applicable,'card_keys':sorted(strict|unknown) if applicable else [],
            'strict':sorted(strict),'uncertain_only':sorted(unknown-strict) if applicable else [],
            'uncertain_cards_are_confirmed_matches':False,
            'reason':'Retain unknown candidates for evidence search, not proof of condition satisfaction' if applicable
                     else 'Unstructured/unresolved/empty-group plan keeps original fallback'}


def union_texts(evidence, card_key):
    texts=[r['text'] for r in evidence if r['card_key']==card_key]
    # Keep each original span as well, so mixed period labels in the joined
    # diagnostic cannot erase an existing individual-span witness pass.
    return texts+['\n'.join(texts)] if texts else []


def union_witness(case, retrieval, E):
    if not case['witness_groups'] or not case['gold_cards']:
        return None
    if retrieval['status']!='ok':
        return 0.0
    facts=E.metric_facts(E.C.digest((F.OLD[30]/'structured.sqlite').read_bytes()))
    returned={r['card_key'] for r in retrieval['result']['cards']}
    count=0
    for card, groups in case['witness_groups'].items():
        if card not in returned:continue
        # Current33 OR groups have identical required field kinds. Preserve
        # original annotation/rule instead of modifying frozen witness scores.
        requirements=next((g for g in case['plan']['groups'] if len(g)==len(groups)),[])
        texts=union_texts(retrieval['result']['evidence'],card)
        if requirements and all(any(E.fact_witnessed(facts[fid],requirements[i],texts) for fid in choices)
                                for i,choices in enumerate(groups)):
            count+=1
    return count/len(case['gold_cards'])


def unique_chunks(rows):
    chunks={}
    for row in rows:
        if row['chunk_id'] in chunks:
            raise RuntimeError('Duplicate chunk ID in frozen corpus')
        chunks[row['chunk_id']]=row
    return chunks


def freeze_unknown_component(experiment, B, question, embedding, profile, keys, protocol, *, path=None):
    key=experiment.component_key(question,profile,keys)
    path=path or F.STAGES[36]/'components'/f'{key}.json'
    identity={'question_sha256':B.query_identity(question),'embedding_sha256':B.query_identity(embedding),
        'profile':profile,'scope':list(keys),
        'chunks_sha256':F.digest((F.OLD[31]/f'{profile}_chunks.jsonl').read_bytes())}
    if path.exists():
        record=F.read_json(path);check=dict(record);claimed=check.pop('sha256')
        if F.digest(check)!=claimed:
            raise RuntimeError('Unknown component saved hash mismatch')
        if (record['protocol_sha256']!=protocol['sha256'] or record['component_key']!=key
                or record['index_identity']!=experiment.base.index_identity
                or record['component']['identity']!=identity):
            raise RuntimeError('Unknown component identity mismatch')
        component=record['component']
    else:
        component=experiment.components.get(key)
        if component is None:
            component=experiment.capture_component(question,embedding,profile,keys)
            component['identity']=identity
        elif component['identity']!=identity:
            raise RuntimeError('Unknown component inherited identity mismatch')
        record={'component_key':key,'component':component,'index_identity':experiment.base.index_identity,
                'protocol_sha256':protocol['sha256']}
        record['sha256']=F.digest(record)
    owners={chunk.chunk_id:chunk.card_key for chunk in experiment.base.data[profile][0]}
    for name in ['bm25','vector']:
        rows=component[name]
        if (len(rows)>50 or [r['rank'] for r in rows]!=list(range(1,len(rows)+1))
                or len({r['chunk_id'] for r in rows})!=len(rows)
                or any(r['chunk_id'] not in owners or owners[r['chunk_id']] not in keys for r in rows)):
            raise RuntimeError('Unknown component candidate order/ownership/scope mismatch')
    if not path.exists():F.write_json(path,record)
    experiment.components[key]=component
    return {'key':key,'sha256':record['sha256'],'path':str(path.relative_to(F.ROOT))}


def hierarchy_audit():
    """Reference/ownership integrity, explicitly NOT semantic heading truth."""
    chunks=unique_chunks([__import__('json').loads(line) for line in
            (F.OLD[31]/'parent_child_bundle_chunks.jsonl').read_text().splitlines()])
    nodes={}
    for path in (F.OLD[31]/'hierarchy').glob('*.json'):
        for row in F.read_json(path):
            if row['node_id'] in nodes:raise RuntimeError('Duplicate hierarchy node')
            nodes[row['node_id']]=row
    problems=[]
    for key,node in nodes.items():
        parent=node['parent_id']
        if parent is not None and (parent not in nodes or nodes[parent]['document_id']!=node['document_id']):
            problems.append({'node_id':key,'kind':'parent_reference_or_owner'})
        visited={key};cursor=parent
        while cursor is not None and cursor in nodes:
            if cursor in visited:
                problems.append({'node_id':key,'kind':'cycle'});break
            visited.add(cursor);cursor=nodes[cursor]['parent_id']
        for child in node['search_chunk_ids']:
            if child not in chunks or chunks[child]['metadata']['node_id']!=key or chunks[child]['document_id']!=node['document_id']:
                problems.append({'node_id':key,'kind':'chunk_reference_or_owner'})
    related=0
    for key,row in chunks.items():
        meta=row['metadata']
        if meta['node_id'] not in nodes or nodes[meta['node_id']]['document_id']!=row['document_id']:
            problems.append({'chunk_id':key,'kind':'node_reference_or_owner'})
        for neighbor in meta['related_chunk_ids']:
            related+=1
            if neighbor not in chunks or chunks[neighbor]['document_id']!=row['document_id'] or neighbor==key:
                problems.append({'chunk_id':key,'kind':'related_reference_or_owner'})
    result={'nodes':len(nodes),'chunks':len(chunks),'documents':len({r['document_id'] for r in chunks.values()}),
        'related_references':related,'heading_levels':dict(Counter(str(r['heading_level']) for r in nodes.values())),
        'problems':problems,'reference_integrity_passed':not problems,
        'semantic_heading_accuracy':'미검증','source':'frozen31 operational structural output; no regeneration',
        'limitation':'Valid IDs/acyclic same-card references do not prove the OCR headings or parent-child meaning are correct.'}
    F.write_json(F.STAGES[36]/'hierarchy_reference_audit.json',result)
    return result


def tools():
    B=F.load_module('bundle_for_final_comparison',F.ROOT/'notebooks/34_bundle_experiment.py')
    A=F.load_module('answers_for_final_comparison',F.ROOT/'notebooks/35_answer_experiment.py')
    E,scoring=A.load_tools()
    F.initialize(36)
    return B,A,E,scoring


def primary_inputs():
    bundle=F.read_json(F.STAGES[34]/'retrieval_results.json')
    budget=F.read_json(F.STAGES[35]/'answer_results.json')
    selected_bundle=bundle['selection']['selected_policy'];selected_budget=budget['selection']['selected_budget_policy']
    benefit=[r for r in F.read_json(F.OLD[33]/'retrieval_results.json')['records'] if r['profile']=='card_page_section_benefit']
    parent=[r for r in bundle['records'] if r['policy']==selected_bundle]
    retrievals=sorted(benefit+parent,key=lambda r:(r['query_id'],r['profile'],r['mode']))
    answers=sorted([r for r in budget['records'] if r['budget_policy']==selected_budget],key=lambda r:(r['query_id'],r['profile'],r['mode']))
    if len(retrievals)!=198 or len(answers)!=198:raise RuntimeError('Incomplete selected six-condition input')
    for r,a in zip(retrievals,answers,strict=True):
        if (r['query_id'],r['profile'],r['mode'])!=(a['query_id'],a['profile'],a['mode']) or a['retrieval_sha256']!=F.digest(r):
            raise RuntimeError('Selected answer is bound to a different retrieval')
    freeze=F.read_json(F.OLD[32]/'dataset_freeze.json')
    plans={r['query_id']:r['plan'] for r in F.read_json(F.OLD[33]/'parser_results.json')['records']}
    protocol={'version':'selected-profile-search-and-unknown-diagnostics-v1',
        'bundle_selection_sha256':bundle['selection']['sha256'],'answer_selection_sha256':budget['selection']['sha256'],
        'retrieval_sha256':F.digest(retrievals),'answers_sha256':F.digest(answers),'dataset_sha256':freeze['sha256'],
        'selected_bundle_policy':selected_bundle,'selected_answer_budget_policy':selected_budget,
        'primary_modes':['U','S_THEN_U','S_PLUS_U'],'unknown_candidate_policy':'Separate preregistered diagnostic: strict union unknown if structured resolved; preserve explicit scope; never declare unknown a confirmed match; does not select a primary winner.',
        'additional_union_witness':'Post-hoc diagnosis only: original required raw fields may appear across several SAME-CARD evidence rows, retaining original single-row checks. Same27/24 relevance units; no semantic relation proof and no change to frozen primary metric.',
        'hierarchy_audit':'References/ownership/cycles and heading-level counts only; no OCR validator/heading repair or semantic truth claim.',
        'cohort':'Existing33 questions already seen;20 development froze settings then13 post-selection verification. No independent holdout.',
        'provenance':'Conditioned on34 frozen operational components; unknown diagnostic captures new scoped components once per scope before bundle reranking. Exact scoped L2 versus original global approximate backend remains a confound.',
        'code':{'36_comparison.py':F.digest(Path(__file__).read_bytes())},'model':F.MODEL,'reasoning':F.REASONING,
        'production_adopted':False}
    protocol['sha256']=F.digest(protocol)
    path=F.STAGES[36]/'protocol.json'
    if path.exists() and F.read_json(path)!=protocol:raise RuntimeError('Final comparison protocol changed after freeze')
    F.write_json(path,protocol)
    return freeze,plans,retrievals,answers,protocol


def diagnose_primary(cases, retrievals, answers, E, scoring):
    rows=[]
    by={(r['query_id'],r['profile'],r['mode']):r for r in retrievals}
    for answer in answers:
        case=cases[answer['query_id']];retrieval=by[(answer['query_id'],answer['profile'],answer['mode'])]
        union=union_witness(case,retrieval,E)
        rows.append({'query_id':answer['query_id'],'profile':answer['profile'],'mode':answer['mode'],
            'single_witness':retrieval.get('metrics',{}).get('witness_recall_at_3'),
            'same_card_union_witness':union,'answer_status':answer['status'],
            'complete':answer['scores'].get('known_positive_complete'),
            'recommendation_count':len(answer.get('answer',{}).get('recommendations',[])),
            'claim_count':len(answer.get('answer',{}).get('claims',[])),
            'empty_recommendation_on_named_detail':case['subgroup']=='proper_noun' and answer['status']=='ok'
                and not answer['answer']['recommendations'],
            'unsupported_percent_claims':answer['scores'].get('unsupported_percent_claims',0)})
    differences=[]
    by_answer={(r['query_id'],r['profile'],r['mode']):r for r in answers}
    for case in cases.values():
        a=by_answer[(case['query_id'],'card_page_section_benefit','S_THEN_U')]
        b=by_answer[(case['query_id'],'parent_child_bundle','S_THEN_U')]
        if a['scores'].get('known_positive_complete')==b['scores'].get('known_positive_complete'):
            continue
        pair=[]
        for answer in [a,b]:
            retrieval=by[(answer['query_id'],answer['profile'],answer['mode'])]
            evidence=retrieval.get('result',{}).get('evidence',[])
            by_evidence={e['chunk_id']:e for e in evidence}
            claims=[]
            for claim in answer.get('answer',{}).get('claims',[]):
                claims.append({**claim,'cited_texts':[by_evidence[key]['text'] for key in claim['citations'] if key in by_evidence]})
            supports=[]
            for recommendation in answer.get('answer',{}).get('recommendations',[]):
                owned=[claim for claim in answer.get('answer',{}).get('claims',[])
                       if claim['card_key']==recommendation['card_key']]
                groups=[]
                for group in case['plan']['groups']:
                    groups.append([{'requirement':requirement,
                        'max_claim_component_support':max([scoring.component_support(claim,requirement,by_evidence)
                                                           for claim in owned] or [0])}
                                   for requirement in group])
                supports.append({'card_key':recommendation['card_key'],'requirement_groups':groups})
            pair.append({'profile':answer['profile'],'status':answer['status'],'scores':answer['scores'],
                'returned_cards':[r['card_key'] for r in retrieval.get('result',{}).get('cards',[])],
                'recommendations':answer.get('answer',{}).get('recommendations',[]),'claims_with_evidence':claims,
                'per_card_requirement_support':supports,
                'evidence':evidence,'union_witness':union_witness(case,retrieval,E)})
        differences.append({'query_id':case['query_id'],'question':case['question'],'subgroup':case['subgroup'],
                            'gold_cards':case['gold_cards'],'comparison':pair})
    result={'records':rows,'s_then_complete_disagreements':differences,
            'limit':'Post-hoc diagnostics. No score rewriting, no LLM judge or new PDF-wide correctness labels.'}
    F.write_json(F.STAGES[36]/'primary_diagnostics.json',result)
    return result


def unknown_diagnostic(cases, plans, retrievals, E, scoring, B, A, protocol):
    import shutil
    original_cache_class=A.ResponseCache
    class DiagnosticCache(original_cache_class):
        def __init__(self, **kwargs):
            F.assert_private_directory(Path(kwargs['cache']))
            super().__init__(**kwargs)
        def parse(self, **kwargs):
            key=self.key(kwargs)
            prior35=F.STAGES[35]/'api_cache'
            self.old_cache=prior35 if (prior35/(key+'.json')).exists() else F.OLD[30]/'api_cache'
            before=len(self.events)
            try:
                return super().parse(**kwargs)
            finally:
                if len(self.events)>before and self.events[-1]['cache_hit'] and self.old_cache==prior35:
                    self.events[-1]['cache_origin']='prior35'
    # Only this isolated experiment module's adapter factory is replaced.
    # Operational OpenAIService and the completed35 source remain unchanged.
    A.ResponseCache=DiagnosticCache
    experiment=B.FollowupExperiment()
    experiment.components=F.read_json(F.STAGES[34]/'component_freeze.json')['components']
    # Working index is34's independent copy; new scores and outputs only36.
    target=F.STAGES[36]/'bge_cache';F.assert_private_directory(target);target.mkdir(exist_ok=True)
    for source in (F.STAGES[34]/'bge_cache').glob('*.json'):
        destination=target/source.name
        if not destination.exists():shutil.copy2(source,destination)
    experiment.scorer.cache=target
    strict={(r['query_id'],r['profile']):r for r in retrievals if r['mode']=='S_THEN_U'}
    outputs=[]
    for case in cases.values():
        plan=plans[case['query_id']];selection=E.DB.select_cards(experiment.facts,plan)
        policy=unknown_policy(plan,selection)
        if not policy['applicable']:continue
        embedding=experiment.api.embeddings([case['question']])[0]
        for profile in E.PROFILES:
            key=f'{case["query_id"]}_{profile}'
            path=F.STAGES[36]/'unknown'/f'{key}.json'
            if path.exists():
                record=F.read_json(path)
                if record['protocol_sha256']!=protocol['sha256']:raise RuntimeError('Stale unknown diagnostic')
                if record.get('component_reference'):
                    reference=freeze_unknown_component(experiment,B,case['question'],embedding,profile,
                                                       policy['card_keys'],protocol)
                    if reference!=record['component_reference']:raise RuntimeError('Unknown result component binding mismatch')
                outputs.append(record);continue
            baseline=strict[(case['query_id'],profile)]
            started=time.perf_counter()
            keys=policy['card_keys']
            component_reference=None
            if keys!=selection['eligible_cards'] and keys:
                # Freeze before the catch: corrupt/stale components are a hard
                # gate, not a failed quality row that can be silently resumed.
                component_reference=freeze_unknown_component(experiment,B,case['question'],embedding,profile,keys,protocol)
            try:
                if keys==selection['eligible_cards'] or not keys:
                    retrieval=baseline
                else:
                    result=experiment.search(case['question'],embedding,profile,keys,protocol['selected_bundle_policy'])
                    retrieval={'query_id':case['query_id'],'profile':profile,'mode':'S_THEN_UNKNOWN_U','status':'ok',
                               'result':result,'metrics':E.evaluate_retrieval(case,result,selection)}
                answer=A.answer_row(case,retrieval,policy=protocol['selected_answer_budget_policy'],E=E,scoring=scoring,
                                     revalidate_failed=False,cache=F.STAGES[36]/'api_cache')
                record={'status':'ok','retrieval':retrieval,'answer':answer,
                        'same_card_union_witness':union_witness(case,retrieval,E)}
            except Exception as exc:
                record={'status':'failed','error':f'{type(exc).__name__}: {exc}'}
            gold=set(case['gold_cards'])
            record.update(query_id=case['query_id'],profile=profile,split=case['split'],subgroup=case['subgroup'],
                candidate_policy=policy,strict_candidate_count=len(selection['eligible_cards']),
                union_candidate_count=len(policy['card_keys']),
                strict_known_retained=len(gold&set(selection['eligible_cards'])),
                union_known_retained=len(gold&set(policy['card_keys'])),known_gold_count=len(gold),
                baseline_metrics=baseline.get('metrics',{}),wall_seconds=time.perf_counter()-started,
                component_reference=component_reference,
                protocol_sha256=protocol['sha256'],excluded_from_primary_winner_selection=True)
            F.write_json(path,record);outputs.append(record)
            print('unknown diagnostic',key,record['status'],round(record['wall_seconds'],2),flush=True)
    F.write_json(F.STAGES[36]/'unknown_results.json',{'records':outputs,'scorer_calls':experiment.scorer.calls,
        'component_manifest':[r['component_reference'] for r in outputs if r.get('component_reference')],
        'protocol_sha256':protocol['sha256'],'scope':'Separate preregistered diagnostic; no settings selection/promotion'})
    return outputs


def run():
    B,A,E,scoring=tools()
    freeze,plans,retrievals,answers,protocol=primary_inputs()
    cases={case['query_id']:case for case in freeze['queries']}
    F.write_json(F.STAGES[36]/'primary_results.json',{'retrievals':retrievals,'answers':answers,
        'protocol_sha256':protocol['sha256'],'new_primary_api_calls':0,
        'note':'Exact selected35 answers bound to selected34/33 retrievals; no additional primary generation'})
    diagnose_primary(cases,retrievals,answers,E,scoring)
    hierarchy_audit()
    unknown_diagnostic(cases,plans,retrievals,E,scoring,B,A,protocol)
    print('final preservation',F.canonical(F.verify_preservation(36)),flush=True)


if __name__=='__main__':run()
