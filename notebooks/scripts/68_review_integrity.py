"""Extra evaluation guards for68/69; frozen66 scoring semantics stay unchanged.

The notebook owns manual truth judgments. This gate binds supplemental PDF
proofs and whole-input absence reviews; it never guesses truth from keywords.
It is separate because an unattended run must reject stale review checkpoints.
"""
import importlib.util
from pathlib import Path
import re


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


E = _module('review_integrity_e66', '66_visible_answer_evaluation.py')
V = _module('review_integrity_v55', '55_fact_evaluation.py')


def _catalog(primary, supplement):
    cards = {r['card_key']: r for r in primary['records']}
    empty = {k for k, r in cards.items() if not r['facts']}
    extra = supplement['records']
    if len(extra) != len(empty) or {r['card_key'] for r in extra} != empty:
        raise ValueError('Supplement must cover exactly every native-empty card')
    catalog = []
    for record in [*primary['records'], *extra]:
        card = record['card_key']
        if record['source_pdf_sha256'] != cards[card]['source_pdf_sha256']:
            raise ValueError('Supplement PDF differs from owning primary card')
        for fact in record['facts']:
            support = fact.get('source_support', [])
            if (not support or not fact['condition_id'].startswith(card + ':')
                    or fact.get('source_status', 'verified') not in {'verified', 'uncertain'}):
                raise ValueError('Missing owning fact/source status')
            if any(s['path'] != 'data/raw/' + card + '.pdf'
                   or s['sha256'] != record['source_pdf_sha256'] for s in support):
                raise ValueError('Source proof belongs to another card/PDF')
            catalog.append(dict(card_key=card, condition_id=fact['condition_id'],
                                source_pdf_sha256=record['source_pdf_sha256'],
                                fact_sha256=E.digest(fact), source_status=fact.get('source_status', 'verified'),
                                proof_sha256=sorted({E.digest(s) for s in support}),
                                literal_quotes=[s['quote'] for s in support]))
    if len({(r['card_key'], r['condition_id']) for r in catalog}) != len(catalog):
        raise ValueError('Duplicate source reference')
    return sorted(catalog, key=lambda r: (r['card_key'], r['condition_id']))


def source_union(primary_path, supplement_path, root=E.ROOT):
    """Validate real PDF proofs once, then seal their complete file closure."""
    root = Path(root).resolve()
    paths = [Path(primary_path).resolve(), Path(supplement_path).resolve()]
    if any(not p.is_relative_to(root) for p in paths):
        raise ValueError('Reference path escapes root')
    primary, supplement = map(E.read, paths)
    records = _catalog(primary, supplement)
    files = {str(p.relative_to(root)): E.file_digest(p) for p in paths}
    files.update(supplement.get('files_sha256', {}))
    proofs = {}
    for record in [*primary['records'], *supplement['records']]:
        for fact in record['facts']:
            for source in fact['source_support']:
                proofs[E.digest(source)] = source
    for source in proofs.values():
        V.validate_source_support(source, root)
        files[source['path']] = E.file_digest(root / source['path'])
        if source.get('mode') == 'original_pdf_image':
            name = source['manual_review_path']
            files[name] = E.file_digest(root / name)
            review = next(r for r in E.read(root / name)['records']
                          if r['review_id'] == source['manual_review_id'])
            name = review['image_path']
            files[name] = E.file_digest(root / name)
    E.verify_file_map(files, root)
    return dict(status='source_union_all_proofs_verified', primary_path=str(paths[0].relative_to(root)),
                supplement_path=str(paths[1].relative_to(root)), records=records,
                proof_validator_sha256=E.file_digest(V.__file__),
                proof_receipts={key: dict(source_sha256=key, status='validated_original_PDF_proof') for key in proofs},
                files_sha256=files, semantics='Manual source interpretation retained; hashes prove identity, not meaning.')


def verify_union(union, root=E.ROOT):
    E.verify_file_map(union['files_sha256'], root)
    primary = E.read(Path(root) / union['primary_path'])
    supplement = E.read(Path(root) / union['supplement_path'])
    expected = _catalog(primary, supplement)
    proofs = {p for r in expected for p in r['proof_sha256']}
    if (union.get('status') != 'source_union_all_proofs_verified'
            or union.get('proof_validator_sha256') != E.file_digest(V.__file__) or union['records'] != expected
            or set(union['proof_receipts']) != proofs
            or any(r != dict(source_sha256=key, status='validated_original_PDF_proof')
                   for key, r in union['proof_receipts'].items())):
        raise ValueError('Source union or proof receipt changed')
    return {(r['card_key'], r['condition_id']): r for r in expected}


def verify_review_sources(review, references):
    for audit in review['surface_audits']:
        for span in audit['spans']:
            if span['kind'] != 'factual':
                continue
            for ref in span['source_refs']:
                record = references.get((ref['card_key'], ref['condition_id']))
                if (not record or not isinstance(ref.get('quote'), str) or not ref['quote'].strip()
                        or not any(ref['quote'] in q for q in record['literal_quotes'])):
                    raise ValueError('Factual reference lacks an owning verified literal')
                if span['status'] == 'correct' and record['source_status'] != 'verified':
                    raise ValueError('Uncertain source cannot prove a correct factual claim')


def verify_absence_scope(job, scope):
    if (scope.get('question_sha256') != E.digest(job['question'])
            or not scope.get('attributes') or not scope.get('criterion')
            or scope.get('finding') not in {'not_stated', 'uncertain'}
            or not scope.get('reviewed_by') or not scope.get('reason')):
        raise ValueError('Missing/stale manual absence judgment')
    if scope.get('kind') == 'question':
        if scope.get('card_keys') or scope.get('evidence_sha256_by_id'):
            raise ValueError('Question absence must bind only the current question')
    elif scope.get('kind') == 'input':
        owners = scope.get('card_keys', [])
        supplied = [e for e in job['result']['evidence'] if e['card_key'] in owners]
        expected = {e['chunk_id']: E.digest(e['text']) for e in supplied}
        if (not owners or set(owners) != {e['card_key'] for e in supplied}
                or len(supplied) != len(expected) or scope.get('evidence_sha256_by_id') != expected):
            raise ValueError('Absence proof must bind all current owning-card evidence')
    else:
        raise ValueError('Unknown absence scope')


def verify_review_absences(job, review):
    for audit in review['surface_audits']:
        for span in audit['spans']:
            scopes = span.get('absence_scope', [])
            text = span['quote']
            negative_input = re.search(r'(?:근거|자료|입력).{0,100}(?:확인|제시|기재|명시).{0,15}(?:없|않|어렵|어려)', text)
            uncertainty = span['kind'] == 'question_context' and text.strip() not in job['question']
            if (uncertainty or negative_input or span.get('absence_scope_required')) and not scopes:
                raise ValueError('Visible absence/uncertainty statement needs a full-scope receipt')
            for scope in scopes:
                verify_absence_scope(job, scope)
                if scope['kind'] == 'input' and not set(scope['card_keys']) <= set(span.get('card_keys', scope['card_keys'])):
                    raise ValueError('Absence proof belongs to another factual owner')


def score_review(job, row, rubric, review, allowed_source_refs, *, context):
    """Notebook must bind this helper, union and transitive proofs in context."""
    name = 'notebooks/scripts/68_review_integrity.py'
    union_name = context['source_union_path']
    if context['files_sha256'].get(name) != E.file_digest(E.ROOT / name):
        raise ValueError('Integrity evaluator is not frozen in context')
    E.verify_file_map(context['files_sha256'], E.ROOT)
    if union_name not in context['files_sha256']:
        raise ValueError('Unbound source union')
    union = E.read(E.ROOT / union_name)
    if not set(union['files_sha256']) <= set(context['files_sha256']):
        raise ValueError('Union proof closure missing from context')
    references = verify_union(union)
    if set(references) != set(allowed_source_refs):
        raise ValueError('Caller source refs differ from the sealed source catalogs')
    verify_review_sources(review, references)
    verify_review_absences(job, review)
    return E.score_review(job, row, rubric, review, allowed_source_refs, context=context)
