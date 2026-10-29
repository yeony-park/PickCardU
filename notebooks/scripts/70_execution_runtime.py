"""Experiment70 adapter: same bounded runtime, explicit reuse and source gate.

No operating/66--69 module files are modified. Registration is process-local.
The notebook owns the matrix and judgments; this shared helper makes cache and
gate regressions runnable without API calls or executing paid notebook cells.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


V = module('retrieval70_adapter', '68_69_retrieval_runtime.py')
T, R, A = V.T, V.T.R, V.A
F = module('fact70_adapter', '68_fact_input.py')
LEAF = '70_cap_context_repair'


def register():
    """Include70 in the *same* canonical budget scan and actual cache globals."""
    if 70 in T.LEAVES and T.LEAVES[70] != LEAF:
        raise RuntimeError('Stage70 already belongs to another journal')
    T.LEAVES[70] = LEAF
    R.P.STAGES[70] = R.X.STAGES[70] = LEAF
    for shared in T.stage_registries():
        shared.STAGES[70] = R.P.DATA / LEAF
    T.E.STAGE_FOLDERS[70] = LEAF
    return R.P.F.STAGES[70]


BASE = register()


def search_key(question, vector, profile, scope, clarify):
    return R.P.sha(dict(question=question,vector=R.P.sha(vector.tobytes()),
                       profile=profile,scope=scope,clarify=clarify))


def validated_prior_searches(rows, source_base, source_manifest, target_manifest, queries, vectors, parsers):
    """Verify old row bindings before allowing exact-scope U reuse in70."""
    T.guard_manifest(source_manifest)
    T.guard_manifest(target_manifest)
    if source_manifest['parameters'] != target_manifest['parameters']:
        raise RuntimeError('Operating profiles changed; old U search cannot be reused')
    by_query = {q['query_id']:(q,v) for q,v in zip(queries,vectors,strict=True)}
    found = {}
    for row in rows:
        q, vector = by_query[row['query_id']]
        if row['question'] != q['question']:
            raise RuntimeError('Prior question changed')
        binding = R.P.sha(dict(question=q,vector=R.P.sha(vector.tobytes()),profile=row['profile'],repeat=row['repeat'],
            routing={k:v for k,v in row['routing'].items() if k!='db_wall_seconds'},original=row['original_scope'],
            parser_binding=R.P.sha(parsers[row['query_id'],row['repeat']]),
            manifest=R.P.sha(source_manifest),mode=row['mode']))
        seal = T.read(Path(source_base)/'retrievals'/(row['identity']+'.seal.json'))
        if binding != row['binding'] or seal['row_sha256'] != R.P.sha(row):
            raise RuntimeError('Prior retrieval binding or seal changed')
        R.G.T.S.validate_scope(row['result'],row['routing']['scope'])
        R.G.T.S.validate_scope(row['result'],row['original_scope']['card_keys'])
        if any(e.get('origin')=='structured' or str(e.get('chunk_id','')).startswith('s68:')
               for e in row['result']['evidence']):
            raise RuntimeError('Prior search contains an appended structured fact')
        key = search_key(q['question'],vector,row['profile'],row['routing']['scope'],row['original_scope']['clarify'])
        if key in found and found[key]['result'] != row['result']:
            raise RuntimeError('Conflicting results for the same fixed operating U search')
        found.setdefault(key,copy.deepcopy(row))
    return found


def reconstruct_inventory(jobs, searches, facts):
    """Read the actual suffix; do not trust a declared inventory's coverage."""
    by_search = {r['identity']:r for r in searches}
    if len(by_search) != len(searches) or {j['identity']for j in jobs} != set(by_search) or len(jobs)!=len(searches):
        raise RuntimeError('Job/search matrix changed')
    found = {}
    for job in jobs:
        row = by_search[job['identity']]
        assert job['fixed_search_identity']==row['identity'] and job['retrieval_row_sha256']==R.P.sha(row)
        assert all(job[k]==row[k]for k in('query_id','question','profile','mode','repeat'))
        before, after = row['result'], job['result']; prefix = before['evidence']
        assert after['evidence'][:len(prefix)]==prefix
        assert {k:v for k,v in before.items()if k!='evidence'}=={k:v for k,v in after.items()if k!='evidence'}
        suffix = after['evidence'][len(prefix):]
        if job['mode'] != 'SEQ_REPAIRED':
            assert after==before
            continue
        cards = {c['card_key']:c for c in after['cards']}
        expected = {r['fact_id']:r for r in F.canonical_records(before,row['routing'],facts)}
        seen=set()
        for evidence in suffix:
            parsed = json.loads(evidence['text']); assert len(parsed)==1
            item=parsed[0];fid=item['fact_id'];card=item['card_key'];fact=facts[fid]
            assert fid not in seen, 'Duplicate structured fact in one answer input'
            seen.add(fid)
            assert fid in expected and item==expected[fid]
            assert evidence['chunk_id']=='s68:'+fid and evidence['card_key']==card==fact['document_id']
            assert card in cards and item['card_name']==cards[card]['card_name'] and item['issuer']==cards[card]['issuer']
            assert evidence['text']==F.render_json([item])
            entry=found.setdefault(fid,dict(fact_id=fid,card_key=card,database_fact_sha256=R.P.sha(fact),database_fact=copy.deepcopy(fact),occurrences=[]))
            entry['occurrences'].append(dict(identity=job['identity'],chunk_id=evidence['chunk_id'],text_sha256=R.P.sha(evidence['text']),canonical_record=item,canonical_record_sha256=R.P.sha(item)))
    return [found[fid]for fid in sorted(found)]


def validate_source_records(actual, review, root):
    """Require one real field/proof review per transmitted fact, not a map alone."""
    root=Path(root)
    expected={r['fact_id']:r for r in actual};records=review['records']
    if len(records)!=len(expected) or len({r['fact_id']for r in records})!=len(records) or {r['fact_id']for r in records}!=set(expected):
        raise ValueError('Actual source-review record coverage differs from transmitted facts')
    T.E.verify_file_map(review['files_sha256'],root)
    proofs=review['source_proof_catalog'];visuals=review.get('registered_visual_reviews',{})
    validator=module('source70_gate_validator','55_fact_evaluation.py')
    for key,proof in proofs.items():
        assert key==R.P.sha(proof)
        validator.validate_source_support(proof,root)
    import fitz,hashlib
    for key,visual in visuals.items():
        assert key==R.P.sha(visual)
        assert visual['visually_reviewed'] is True
        assert visual['pdf_path']=='data/raw/'+visual['card_key']+'.pdf'
        assert T.E.file_digest(root/visual['pdf_path'])==visual['pdf_sha256']
        assert T.E.file_digest(root/visual['image_path'])==visual['image_sha256']
        assert visual['render_scale']==2 and visual.get('reviewed_by')and visual.get('observation')
        with fitz.open(root/visual['pdf_path'])as doc:
            assert 1<=visual['page']<=len(doc)
            rendered=doc[visual['page']-1].get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
        assert hashlib.sha256(rendered).hexdigest()==visual['image_sha256']
    allowed_record={'reviewed_source_supported_with_disclosed_omissions','reviewed_with_relationship_ambiguity','supported_with_disclosed_omissions'}
    allowed_field={'source_supported','relationship_ambiguous','conservative_state_preserved','not_stated_in_fact','representation_metadata_reviewed'}
    visual_only={(record['fact_id'],field['field_path'])for record in records
                 for field in record['field_reviews']if not field.get('source_proof_ids')}
    observation_map={}
    if visual_only:
        receipt=review.get('visual_field_receipt')
        assert isinstance(receipt,dict), 'Visual-only source audit has no bound receipt file'
        path=(root/receipt['path']).resolve()
        assert path.is_relative_to(root.resolve())
        assert T.E.file_digest(path)==receipt['raw_sha256']==review['files_sha256'][receipt['path']]
        observations=T.read(path)['observations']
        observation_map={(o['fact_id'],o['field_path']):o for o in observations}
        assert len(observation_map)==len(observations)and set(observation_map)==visual_only
    def check_support(item,owner,pdf_sha):
        ids=item.get('source_proof_ids',[]);images=item.get('registered_visual_review_ids',[])
        assert ids or images, 'Source review has no proof'
        for key in ids:
            assert proofs[key]['path']=='data/raw/'+owner+'.pdf'and proofs[key]['sha256']==pdf_sha
        for key in images:
            assert visuals[key]['card_key']==owner and visuals[key]['pdf_sha256']==pdf_sha
    for record in records:
        inventory=expected[record['fact_id']];fact=inventory['database_fact'];owner=inventory['card_key']
        assert record['card_key']==owner==fact['document_id']
        assert record['database_fact_sha256']==inventory['database_fact_sha256']==R.P.sha(fact)
        assert record['source_status']in allowed_record and record['confirmed_conflict_fields']==[]
        assert record['fields']==fact['fields']and record['derived']==fact['derived']
        assert record['relation_scope']==fact['relation_scope']and record['field_evidence_sha256']==R.P.sha(fact['field_evidence'])
        expected_fields={section+'.'+key:value for section in('fields','derived')for key,value in fact[section].items()}
        fields=record['field_reviews']
        assert len(fields)==len(expected_fields)and {f['field_path']for f in fields}==set(expected_fields)
        check_support(record,owner,fact['source_pdf_sha256'])
        for field in fields:
            assert field['stored_value']==expected_fields[field['field_path']]and field['status']in allowed_field
            check_support(field,owner,fact['source_pdf_sha256'])
            if not field.get('source_proof_ids'):
                observation=field.get('visual_field_observation')
                assert isinstance(observation,dict), 'Visual-only field has no manual observation receipt'
                assert observation==observation_map[fact['fact_id'],field['field_path']]
                assert observation['fact_id']==fact['fact_id']and observation['field_path']==field['field_path']
                assert observation['database_fact_sha256']==R.P.sha(fact)
                assert observation['stored_value_sha256']==R.P.sha(field['stored_value'])
                assert observation['relation_scope_sha256']==R.P.sha(fact['relation_scope'])
                assert observation['field_evidence_sha256']==R.P.sha(fact['field_evidence'])
                assert observation['visual_review_ids']==field['registered_visual_review_ids']
                assert observation.get('reviewed_by')and observation.get('observed_text')and observation.get('manual_relation_reason')


def require_source_gate(base, facts):
    """Reject incomplete, conflicting or stale source review before any API."""
    base=Path(base);manifest=T.read(base/'execution_manifest.json');T.guard_manifest(manifest)
    jobs=T.read(base/'jobs.json')['records'];searches=T.read(base/'retrieval_results.json')['records']
    gate=T.read(base/'pre_answer_acceptance.json');T.E.verify_file_map(gate['files_sha256'],T.ROOT)
    assert gate['status']=='accepted_source_and_input_assembly_before_generation'
    assert gate['confirmed_source_conflicts']==0 and gate['unreviewed_fact_ids']==[]
    assert gate['manual_gold_in_assembler'] is False and gate['all_transmitted_unique_facts_reviewed'] is True
    for name in ('jobs.json','retrieval_results.json','additional_fact_inventory.json','answer_payload_contract.json',
                 'planned_input_verification.json','execution_manifest.json'):
        assert gate['files_sha256'][str((base/name).relative_to(T.ROOT))]==T.E.file_digest(base/name)
    actual=reconstruct_inventory(jobs,searches,facts)
    assert T.read(base/'additional_fact_inventory.json')['records']==actual
    assert gate['reconstructed_additional_inventory_sha256']==R.P.sha(actual)
    assert gate['reviewed_fact_sha256_by_id']=={r['fact_id']:r['database_fact_sha256']for r in actual}
    assert A.payload_contract(jobs,manifest)==T.read(base/'answer_payload_contract.json')
    assert len(jobs)==manifest['logical_answers']==432
    assert {j['identity']for j in jobs}==set(manifest['allowed_identities']['answer'])
    review=T.read(T.ROOT/gate['source_review_path'])
    assert gate['files_sha256'][gate['source_review_path']]==T.E.file_digest(T.ROOT/gate['source_review_path'])
    assert review['reviewed_fact_sha256_by_id']==gate['reviewed_fact_sha256_by_id']
    assert review['reconstructed_additional_inventory_sha256']==R.P.sha(actual)
    assert review['confirmed_source_conflicts']==[]
    assert all(gate['files_sha256'].get(name)==digest for name,digest in review['files_sha256'].items())
    validate_source_records(actual,review,T.ROOT)
    return manifest,jobs,gate
