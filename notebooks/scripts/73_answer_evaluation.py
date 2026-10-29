"""Bind manually reviewed visible answers to their original jobs and outputs.

This validates the recorded decision, not semantic correctness. No provider is
called and hidden claims never compensate for a missing visible direct answer.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def visible(answer):
    if answer is None:
        return {}
    result = {'answer_text': answer['answer_text']}
    for i, recommendation in enumerate(answer['recommendations']):
        result[f'recommendations.{i}.reason'] = recommendation['reason']
    if any(not isinstance(value, str) or not value.strip() for value in result.values()):
        raise ValueError('Missing visible surface')
    return result


def score(job, row, note):
    if row['identity'] != job['identity'] or row['status'] not in ('ok', 'failed'):
        raise ValueError('Job and answer identity/status differ')
    identity = {key: job[key] for key in ('identity', 'query_id', 'profile', 'mode', 'repeat')}
    if row['status'] == 'failed':
        if row.get('answer') is not None or note is not None:
            raise ValueError('A failed answer cannot have visible content or a passing note')
        return dict(**identity, technical=False, direct=False, accurate=False, grounded=False,
                    passed=False, confirmed_wrong=False, uncertain=False, issues=['technical_failure'])
    if not isinstance(note, dict) or note.get('answer_sha256') != digest(row) or note.get('job_sha256') != digest(job):
        raise ValueError('Manual judgment binding differs from actual job or answer')
    if note.get('question') != job['question'] or note.get('technical_success') is not True:
        raise ValueError('Manual question or technical result differs')
    if note.get('visible_surfaces') != visible(row['answer']):
        raise ValueError('The audit must cover every unchanged visible surface')
    if (type(note.get('direct')) is not bool or note.get('accuracy') not in ('correct', 'wrong', 'uncertain')
            or note.get('input_support') not in ('supported', 'unsupported', 'uncertain')
            or not note.get('reason') or not note.get('reviewer') or not isinstance(note.get('issues'), list)):
        raise ValueError('Explicit manual judgments and reason are required')
    if note['accuracy'] == 'wrong' and not note['issues']:
        raise ValueError('Confirmed error must have an explicit issue')
    direct, accurate = note['direct'], note['accuracy'] == 'correct'
    return dict(**identity, technical=True, direct=direct, accurate=accurate,
                grounded=note['input_support'] == 'supported', passed=direct and accurate,
                confirmed_wrong=note['accuracy'] == 'wrong', uncertain=note['accuracy'] == 'uncertain',
                issues=note['issues'], manual_note_sha256=digest(note))
