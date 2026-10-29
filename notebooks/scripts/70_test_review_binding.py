"""A saved manual judgment must follow the exact physical request and input."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

s = importlib.util.spec_from_file_location('review70_test', Path(__file__).with_name('70_review_binding.py'))
M = importlib.util.module_from_spec(s)
s.loader.exec_module(M)


@pytest.fixture
def request_case(tmp_path):
    base = tmp_path / 'experiment'
    (base / 'api_cache').mkdir(parents=True)
    payload = [{'role': 'user', 'content': 'original question and evidence'}]
    request = dict(input=payload, model='fixed-model')
    row = dict(request_keys=['first', 'retry'])
    saved = []
    for key, status in [('first', 'failed'), ('retry', 'ok')]:
        path = base / 'api_cache' / (key + '.json')
        raw = dict(status=status, request=request)
        path.write_text(json.dumps(raw))
        saved.append(dict(path=str(path.relative_to(tmp_path)), raw_sha256=M.E.file_digest(path),
                          request_sha256=M.E.digest(request), actual_input_sha256=M.E.digest(payload),
                          provider_status=status))
    return tmp_path, base, payload, row, saved


def call(case, saved=None):
    root, base, payload, row, original = case
    return M.verify_physical_requests(original if saved is None else saved, row, payload, base=base, root=root)


def test_all_attempts_bind_to_their_actual_request_status_and_input(request_case):
    assert call(request_case) == 2


@pytest.mark.parametrize('field', ['provider_status', 'request_sha256', 'actual_input_sha256', 'raw_sha256', 'path'])
def test_draft_cannot_claim_changed_physical_metadata(request_case, field):
    saved = copy.deepcopy(request_case[-1])
    saved[0][field] = 'changed'
    with pytest.raises(ValueError):
        call(request_case, saved)


def test_retry_order_and_complete_attempt_coverage_are_required(request_case):
    saved = request_case[-1]
    for changed in [saved[::-1], saved[:1], saved + saved[:1]]:
        with pytest.raises(ValueError):
            call(request_case, changed)


def test_unknown_draft_request_fields_are_rejected(request_case):
    saved = copy.deepcopy(request_case[-1])
    saved[0]['unverified_extra'] = 'must not silently pass'
    with pytest.raises(ValueError):
        call(request_case, saved)


def test_changed_input_is_rejected_even_with_consistent_new_draft_hashes(request_case):
    root, base, payload, row, saved = request_case
    path = base / 'api_cache/first.json'
    raw = json.loads(path.read_text())
    raw['request']['input'][0]['content'] = 'different evidence'
    path.write_text(json.dumps(raw))
    saved[0]['raw_sha256'] = M.E.file_digest(path)
    saved[0]['request_sha256'] = M.E.digest(raw['request'])
    saved[0]['actual_input_sha256'] = M.E.digest(raw['request']['input'])
    with pytest.raises(ValueError):
        call(request_case, saved)
