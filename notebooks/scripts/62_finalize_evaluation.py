"""Revalidate raw/reused inputs, complete prose and sources before offline62 aggregation.

This reusable final gate stays separate from sealed paid execution cells so a
notebook rerun cannot silently skip provenance or overwrite accepted artifacts.
It never invokes a model and never produces HTML. Run from any directory:
python notebooks/scripts/62_finalize_evaluation.py [--check]
"""
if not __debug__:
    raise RuntimeError('Assertions must be enabled for the final provenance gate')

from pathlib import Path
import importlib.util, sys, json, hashlib, time, collections, statistics

def require_exact_binding(receipt, code_files, input_files):
    if (receipt.get('validator_code_sha256') != code_files
            or receipt.get('input_files_sha256') != input_files):
        raise RuntimeError('Finalizer code or input provenance differs from the saved receipt')


def main(check_only=False):
    root = Path(__file__).resolve().parents[2]
    sp = importlib.util.spec_from_file_location('final62', root / 'notebooks/scripts/60_62_experiment_runtime.py')
    m = importlib.util.module_from_spec(sp)
    sys.modules[sp.name] = m
    sp.loader.exec_module(m)
    E = m.load('final62_e', '62_evaluate_answers.py')
    V = m.load('final62_v', '62_validate_answer_prose.py')
    S = m.G.load('final62_s', '55_fact_evaluation.py')
    A = m.load('final62_a', '62_check_source_acceptance.py')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    dependencies = A.check(m, root / 'notebooks/62_fixed_prompt_answer_check.ipynb')
    manifest = m.read(m.B62 / 'execution_manifest.json')
    m.X.guard_manifest(manifest)
    m.R.guard_files(manifest['critical_data_sha256'])
    jobs = m.read(m.B61 / 'jobs.json')['records']
    byid = {j['identity']: j for j in jobs}
    questions = m.read(m.B60 / 'questions.json')['queries']
    qm = {q['query_id']: q for q in questions}
    groups = m.read(m.B61 / 'source_judgments.json')['group_by_query']
    raw = m.read(m.B62 / 'answer_results.json')['records']
    m.C.validate_matrix(raw, list(qm))
    answers = {r['identity']: r['answer'] for r in raw}
    evidence = m.read(m.B61 / 'evaluation.json')
    erows = {r['identity']: r for r in evidence['records']}
    oldjobs = {r['identity']: r for r in m.read(m.N.BASE / 'jobs.json')['records']}
    oldmanifest = m.read(m.N.BASE / 'execution_manifest.json')
    source_cache = {}
    proofs = {}

    validator_code = {str((root / 'notebooks/scripts' / name).relative_to(root)):
        sha(root / 'notebooks/scripts' / name) for name in (
        '62_finalize_evaluation.py', '62_validate_answer_prose.py', '62_evaluate_answers.py',
        '55_fact_evaluation.py', '62_check_source_acceptance.py', '60_62_experiment_runtime.py')}
    input_files = dict(m.read(m.B61 / 'completion.json')['files_sha256'])
    input_files.update(dependencies['files_sha256'])
    def track(path):
        path = path.resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Finalizer input escapes the repository')
        input_files[str(path.relative_to(root))] = sha(path)
    for path in (m.B61 / 'completion.json', m.B62 / 'execution_manifest.json',
                 m.B62 / 'answer_payload_contract.json', m.B62 / 'answer_results.json'):
        track(path)
    for folder in ('answers', 'api_cache', 'payload_contracts', 'source_review/answer_adjudications'):
        for path in sorted((m.B62 / folder).glob('*.json')):
            track(path)
    def proof(p):
        key = m.P.sha(p)
        if key not in proofs:
            S.validate_source_support(p, root)
            track(root / p['path'])
            if p.get('mode') == 'original_pdf_image':
                registry_path = root / p['manual_review_path']
                track(registry_path)
                image_review = next(row for row in m.read(registry_path)['records']
                    if row['review_id'] == p['manual_review_id'])
                track(root / image_review['image_path'])
            proofs[key] = True

    def validate_actual(job, answer):
        source = None
        if answer.get('reuse_source_sha256'):
            sourcepath = root / answer['reuse_source_path']
            assert sourcepath.resolve().is_relative_to(root.resolve())
            source = m.read(sourcepath)
            assert m.P.sha(source) == answer['reuse_source_sha256']
            if str(m.N.BASE.relative_to(root)) in answer['reuse_source_path']:
                sourcejob = oldjobs[source['identity']]
                key = 'old:' + source['identity']
                if key not in source_cache:
                    m.G.M.R.validate_generated_answer(source, sourcejob['question'], sourcejob['result']['evidence'], stage=59, identity=source['identity'], manifest=oldmanifest, contract=m.G.payload_contract(source['identity'], sourcejob['question'], sourcejob['result']['evidence'], oldmanifest))
                    m.G.validate_checkpoint_prompt(source, oldmanifest, {})
                    source_cache[key] = True
            else:
                assert sourcepath.parent == m.B62 / 'answers'
                sourcejob = byid[source['identity']]
                assert not source.get('reuse_source_sha256')
                m.validate_answer(source, sourcejob, manifest)
            assert m.C.same_payload(sourcejob, job, m.G.C.operating_prompts())
        m.validate_answer(answer, job, manifest, source=source)
    if not check_only and (m.B62 / 'completion.json').exists():
        raise RuntimeError('Accepted results are immutable; use --check')

    def persist(path, value):
        if check_only:
            existing = m.read(path)
            candidate = dict(value)
            existing.pop('checked_at_epoch', None)
            candidate.pop('checked_at_epoch', None)
            if existing != candidate:
                raise RuntimeError('Saved offline evaluation differs: ' + str(path))
        else:
            m.write(path, value)
    reviews = []
    scores = []
    manual_files = {}
    phase = []
    for qid in qm:
        p = m.B62 / 'source_review/answer_adjudications' / (qid + '.json')
        assert p.exists(), qid
        d = m.read(p)
        manual_files[str(p.relative_to(root))] = sha(p)
        prepath = m.B61 / 'source_review' / (qid + '.json')
        pre = m.read(prepath)
        prerows = {r['identity']: r for r in pre['records']}
        assert d['query_id'] == qid and d['question'] == qm[qid]['question']
        assert len(d['records']) == 18
        assert d['source_review_file_raw_sha256'] == sha(prepath) and d['source_catalog_sha256'] == pre['source_catalog_sha256']
        for r in d['records']:
            identity = r['identity']
            job = byid[identity]
            assert job['query_id'] == qid
            answer = answers[identity]
            answerpath = m.B62 / 'answers' / (identity + '.json')
            assert m.read(answerpath) == answer
            validate_actual(job, answer)
            pregrade = prerows[identity]
            score = V.score_answer(job, answer, r, pregrade, groups[qid], score_frozen=E.score_answer, answer_by_card=S.answer_by_card, validate_source_support=proof)
            scores.append(score)
            reviews.append(r)
            for audit in r['material_card_audits']:
                source = pre['source_card_catalog'][audit['card_key']]
                if 'source_pdf_sha256' in audit:
                    assert audit['source_pdf_sha256'] == source['source_pdf_sha256']
                for p0 in audit.get('source_support', []):
                    proof(p0)
            for extra in r.get('additional_claim_checks', []):
                for p0 in extra.get('source_support', []):
                    assert extra.get('card_key') in pre['source_card_catalog'] and p0['sha256'] == pre['source_card_catalog'][extra['card_key']]['source_pdf_sha256']
                    proof(p0)
            active = score['active_card_keys']
            complete = {c['card_key'] for c in erows[identity]['card_condition_counts'] if c['complete']}
            used_complete = bool(set(active) & complete)
            conditions = {c['card_key'] + ':' + f['condition_id']: f for c in pregrade['card_reviews'] for f in c['facts']}
            input_missing = [key for key, f in conditions.items() if any((key.startswith(k + ':') for k in active)) and f['source_status'] == 'verified' and (f['input_status'] != 'supported')]
            phase.append(dict(identity=identity, query_id=qid, profile=job['profile'], mode=job['mode'], repeat=job['repeat'], group=groups[qid], verdict=r['verdict'], passed=score['passed'], complete_candidate_keys=sorted(complete), actual_active_card_keys=active, used_complete_candidate=used_complete, complete_candidate_not_used=bool(complete) and (not used_complete), active_card_input_gaps=input_missing, given_input_not_reflected=score['generation_omissions'], correct_without_input=score['correct_without_input'], critical_error_ids=r['critical_error_ids'], note='Overlapping diagnostic flags; a missing compound diagnostic fact is not automatically the direct-question PASS cause. Source-correct-without-input is not evidence retrieval success.'))
        m.C.validate_matrix(d['records'], [qid])
    m.C.validate_matrix(reviews, list(qm))
    m.R.guard_files(input_files)
    m.R.guard_files(validator_code)
    if check_only:
        require_exact_binding(m.read(m.B62 / 'main_answer_validation.json'), validator_code, input_files)
    binding = dict(validator_code_sha256=validator_code, input_file_map_sha256=m.P.sha(input_files))
    summary = E.aggregate(scores, group_by_query=groups, profiles=m.C.PROFILES, modes=m.C.MODES)
    decision = E.select(evidence['summary'], summary, scores, profiles=m.C.PROFILES, group_by_query=groups)
    index = [dict(identity=r['identity'], manual_file=str((m.B62 / 'source_review/answer_adjudications' / (byid[r['identity']]['query_id'] + '.json')).relative_to(root)), manual_record_sha256=m.P.sha(r), answer_row_sha256=r['answer_row_sha256']) for r in reviews]
    persist(m.B62 / 'answer_judgments.json', dict(**binding, records=index, manual_files_sha256=manual_files, policy='Full manual judgments reside in bound per-question files; this is their identity/hash index.'))
    persist(m.B62 / 'evaluation.json', dict(**binding, records=scores, summary=summary, decisions=decision, group_by_query=groups, status='complete_manual_matrix', evaluation_policy='Frozen59 direct question policies+60 generic rubric; source/input reflection and question PASS are separate.'))
    persist(m.B62 / 'failure_phase_diagnostics.json', dict(**binding, records=phase, policy='Source-uncertain queries separate; no-complete-candidate, complete-candidate-not-used and given-condition-not-reflected are overlapping diagnostics, not an exhaustive causal partition.'))
    persist(m.B62 / 'main_answer_validation.json', dict(**binding, input_files_sha256=input_files, status='all720_manual_answer_input_source_bindings_verified', logical_rows=720, questions=40, manual_files_sha256=manual_files, prose_validator_sha256=sha(root / 'notebooks/scripts/62_validate_answer_prose.py'), all720_full_prose_and_comparison_audits_required=True, extra_source_proofs=len(proofs), underlying_reuse_sources_checked=len(source_cache), source_evaluation_sha256=sha(m.B61 / 'evaluation.json'), answer_evaluation_sha256=sha(m.B62 / 'evaluation.json'), checked_at_epoch=time.time(), generation_api_for_grading=0))
    print(json.dumps(dict(logical_rows=len(scores), decisions=decision, check_only=check_only), ensure_ascii=False, indent=2))
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate existing final artifacts without writes')
    main(check_only=parser.parse_args().check)
