"""Offline denominator and evidence-binding checks shared by notebooks55–59.

Notebooks own reference facts and source adjudication. This small helper keeps
the same scoring contract across resumptions; it never infers semantic support
from word overlap or generates a report. Supported quotes only prove that the
reviewer's cited text was actually present, not that the reviewer was correct.
"""
from collections import defaultdict
import statistics
import unicodedata


STATUSES = {'supported', 'absent', 'ambiguous', 'contradicted'}
RATIOS = ('retrieval_recall', 'input_recall', 'answer_reflection',
          'end_to_end_fulfillment', 'complete_retrieval', 'complete_input')


def canonical_text(text: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', text).casefold().split())


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _validate_support(support, text):
    if support.get('status') not in STATUSES:
        raise ValueError('Missing or unreviewed fact support')
    quotes = support.get('quotes', [])
    if not isinstance(quotes, list) or any(not isinstance(q, str) or not q.strip() for q in quotes):
        raise ValueError('Support quotes must be nonempty strings')
    if support['status'] == 'supported' and not quotes:
        raise ValueError('Supported fact requires a reviewed quote')
    normalized = canonical_text(text)
    if any(canonical_text(q) not in normalized for q in quotes):
        raise ValueError('Quoted support is absent from the actual bound text')


def score_row(row: dict, *, retrieved_text: dict[str, str], input_text: dict[str, str],
              answer_text: dict[str, str], expected_binding: dict) -> dict:
    if row.get('binding') != expected_binding:
        raise ValueError('Fact adjudication is not bound to the actual result/input/answer')
    if not row.get('identity') or not row.get('query_id') or not row.get('config_id'):
        raise ValueError('Missing experiment identity')
    facts = row['facts']
    ids = [f['fact_id'] for f in facts]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate fact IDs')
    required = [f for f in facts if f['kind'] == 'required']
    expected = row['required_fact_ids']
    if len(set(expected)) != len(expected) or set(expected) != {f['fact_id'] for f in required}:
        raise ValueError('Required fact reference was dropped or added during adjudication')
    for fact in facts:
        if fact['kind'] not in {'required', 'context'} or not fact.get('reason'):
            raise ValueError('Invalid fact classification or missing source reason')
        if not fact.get('card_key'):
            raise ValueError('Fact support requires an explicit owning card')
        if fact['source_status'] not in {'verified', 'uncertain'}:
            raise ValueError('Source fact has not been reviewed')
        if fact['source_status'] == 'verified':
            if not fact.get('source_support'):
                raise ValueError('Verified fact requires source support')
            for source in fact['source_support']:
                if not all(source.get(k) for k in ['path', 'sha256', 'page', 'quote']):
                    raise ValueError('Incomplete original source support')
        for key, text in [('retrieved', retrieved_text), ('input', input_text), ('answer', answer_text)]:
            _validate_support(fact[key], text.get(fact['card_key'], ''))
    uncertain = [f['fact_id'] for f in required if f['source_status'] == 'uncertain']
    verified = [f for f in required if f['source_status'] == 'verified']
    supported = {key: {f['fact_id'] for f in verified if f[key]['status'] == 'supported'}
                 for key in ['retrieved', 'input', 'answer']}
    all_ids = {f['fact_id'] for f in verified}
    r, i, a = [supported[k] for k in ['retrieved', 'input', 'answer']]
    n = len(verified)
    return dict(identity=row['identity'], query_id=row['query_id'], config_id=row['config_id'],
        original_verdict=row['original_verdict'],
        source_reference_total=len(required), source_denominator=n, source_uncertain_fact_ids=uncertain,
        retrieved_supported=len(r), input_supported=len(i), answered_correctly=len(a),
        grounded_reflected=len(i & a), retrieval_recall=_ratio(len(r), n), input_recall=_ratio(len(i), n),
        answer_reflection=_ratio(len(i & a), len(i)), end_to_end_fulfillment=_ratio(len(a), n),
        complete_retrieval=(r == all_ids and not uncertain) if required else None,
        complete_input=(i == all_ids and not uncertain) if required else None,
        retrieval_gaps=sorted(all_ids-r), transport_gaps=sorted(r-i),
        generation_omissions=sorted(i-a), unsupported_required_answers=sorted(a-i),
        input_contradictions=[f['fact_id'] for f in verified if f['input']['status'] == 'contradicted'],
        answer_contradictions=[f['fact_id'] for f in verified if f['answer']['status'] == 'contradicted'])


def aggregate(rows: list[dict]) -> list[dict]:
    if len({r['identity'] for r in rows}) != len(rows):
        raise ValueError('Duplicate logical result rows')
    configurations = defaultdict(list)
    for row in rows:
        configurations[row['config_id']].append(row)
    result = []
    for config, values in sorted(configurations.items()):
        questions = defaultdict(list)
        for row in values:
            questions[row['query_id']].append(row)
        macro, denominators = {}, {}
        for metric in RATIOS:
            query_means = [statistics.mean(float(r[metric]) for r in group if r[metric] is not None)
                           for group in questions.values() if any(r[metric] is not None for r in group)]
            macro[metric] = statistics.mean(query_means) if query_means else None
            denominators[metric] = len(query_means)
        counts = {name: sum(v[name] for v in values) for name in
                  ['source_reference_total', 'source_denominator', 'retrieved_supported',
                   'input_supported', 'answered_correctly', 'grounded_reflected']}
        result.append(dict(config_id=config, question_denominator=len(questions), logical_rows=len(values),
            macro=macro, macro_question_denominators=denominators, diagnostic_micro_counts=counts,
            uncertain_source_fact_rows=sum(len(v['source_uncertain_fact_ids']) for v in values)))
    return result


def validate_identities(identities, expected):
    if len(identities) != len(set(identities)) or set(identities) != set(expected):
        raise ValueError('Experiment identity grid is duplicated, missing, or unexpected')


def validate_seal(summary, completion, *, execution_sha, judgments_sha, summary_sha):
    checks = [summary['execution_results_sha256'] == execution_sha,
              summary['source_judgments_sha256'] == judgments_sha,
              completion['evaluation_summary_sha256'] == summary_sha,
              completion['question_source_judgments_sha256'] == judgments_sha]
    if not all(checks):
        raise ValueError('Prior evaluation seal differs from current inputs')


def validate_source_support(source, root):
    """Check PDF text, or a separately recorded visual review of its exact render.

    Image transcription is a manual semantic judgment, not automated OCR
    validation. Hashes bind it to the original page and inspected image.
    """
    import hashlib
    import fitz
    path = (root/source['path']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Source path escapes the repository')
    if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
        raise ValueError('Source PDF hash mismatch')
    with fitz.open(path) as doc:
        page = source['page']
        if type(page) is not int or not 1 <= page <= len(doc):
            raise ValueError('Source PDF page out of bounds')
        quote = source['quote']
        if source.get('mode') == 'original_pdf_image':
            import json
            review_path = (root/source['manual_review_path']).resolve()
            if not review_path.is_relative_to(root.resolve()):
                raise ValueError('Manual review path escapes repository')
            records = json.loads(review_path.read_text())['records']
            matches = [r for r in records if r['review_id'] == source['manual_review_id']]
            if len(matches) != 1:
                raise ValueError('Missing or duplicate manual image review')
            review = matches[0]
            record_sha=hashlib.sha256(json.dumps(review,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if record_sha != source.get('manual_record_sha256'):
                raise ValueError('Manual source review changed after fact adjudication')
            if not (review.get('visually_reviewed') is True and review.get('reviewed_by') == 'main Codex'
                    and review.get('reviewed_at') and review.get('observation')
                    and review['pdf_path'] == source['path'] and review['pdf_sha256'] == source['sha256']
                    and review['page'] == page and review['render_scale'] == 2
                    and quote in review['quotes'] and isinstance(quote,str) and quote.strip()):
                raise ValueError('Source quote is not covered by the bound manual image review')
            image_path = (root/review['image_path']).resolve()
            if not image_path.is_relative_to(root.resolve()):
                raise ValueError('Manual image path escapes repository')
            actual_render = doc[page-1].get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
            if not (hashlib.sha256(actual_render).hexdigest() == review['image_sha256']
                    == hashlib.sha256(image_path.read_bytes()).hexdigest()):
                raise ValueError('Manually inspected image is not the bound PDF page render')
            return
        if source.get('mode','original_pdf_text') != 'original_pdf_text':
            raise ValueError('Unknown source verification mode')
        if not isinstance(quote, str) or not quote.strip() or canonical_text(quote) not in canonical_text(doc[page-1].get_text()):
            raise ValueError('Source quote absent from bound PDF page; manual image review needed')


def answer_by_card(answer, candidate_cards, prose_spans=()):
    """Explicit claims plus reviewed non-overlapping prose spans, never shared prose.

    Single-card prose has unambiguous ownership. For multi-card answers, spans
    require manual contextual attribution; quote presence does not prove meaning.
    """
    import json
    result = defaultdict(list)
    for item in [*answer.get('claims', []), *answer.get('recommendations', [])]:
        result[item['card_key']].append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    owners = set(candidate_cards) | set(result)
    prose = answer.get('answer_text', '')
    if len(owners) == 1 and not prose_spans:
        result[next(iter(owners))].append(prose)
    used = set()
    for span in prose_spans:
        start, end, owner = span['start'], span['end'], span['card_key']
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(prose)
                or owner not in owners or not span.get('reason') or used.intersection(range(start, end))):
            raise ValueError('Invalid or multiply owned answer prose span')
        used.update(range(start, end))
        result[owner].append(prose[start:end])
    return {k: '\n'.join(v) for k,v in result.items()}


def validate_material_audit(records, expected_pairs, case_bindings):
    pairs=[(r['identity'],r['card_key']) for r in records]
    if len(pairs)!=len(set(pairs)) or set(pairs)!=set(expected_pairs):
        raise ValueError('Material recommendation audit is missing or duplicates a card result')
    for row in records:
        if row['binding']!=case_bindings[row['identity']]:
            raise ValueError('Material audit is not bound to the actual answer')
        if row['review_status'] not in {'reviewed','reviewed_with_source_uncertainty'}:
            raise ValueError('Material audit still has unreviewed recommendations')
        if row['actual_role'] not in {'recommendation','claim_only','explicitly_rejected_or_comparison'}:
            raise ValueError('Material audit lacks the actual role of the mentioned card')
        if not row.get('checked_material_conditions'):
            raise ValueError('Material audit lacks the conditions actually checked')
        if row['review_status']=='reviewed_with_source_uncertainty' and not row.get('uncertainties'):
            raise ValueError('Uncertain material review lacks its uncertainty reason')
