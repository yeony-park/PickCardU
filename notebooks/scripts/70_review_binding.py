"""Experiment70 physical-request checks for source-bound manual drafts.

The notebook owns manual truth, final context and aggregation. This helper only
checks metadata that the older68 binder did not know about. It is kept separate
so retry/status/input tampering has API-free regression coverage. No HTML or
semantic labels are generated here; earlier evaluator files stay unchanged.
"""
import importlib.util
from pathlib import Path


s = importlib.util.spec_from_file_location('review70_e66', Path(__file__).with_name('66_visible_answer_evaluation.py'))
E = importlib.util.module_from_spec(s)
s.loader.exec_module(E)


def verify_physical_requests(saved, answer_row, expected_input, *, base, root=E.ROOT):
    """Check every physical attempt in order, including failed first attempts."""
    root, base = Path(root).resolve(), Path(base).resolve()
    if not base.is_relative_to(root):
        raise ValueError('Physical request base escapes the workspace')
    expected = []
    for key in answer_row['request_keys']:
        path = (base / 'api_cache' / (key + '.json')).resolve()
        if not path.is_relative_to(base / 'api_cache'):
            raise ValueError('Physical request path escapes its journal')
        raw = E.read(path)
        if raw['request']['input'] != expected_input:
            raise ValueError('Actual physical request differs from the current input')
        expected.append(dict(path=str(path.relative_to(root)), raw_sha256=E.file_digest(path),
                             request_sha256=E.digest(raw['request']),
                             actual_input_sha256=E.digest(expected_input), provider_status=raw['status']))
    if saved != expected:
        raise ValueError('Manual draft physical request metadata changed')
    return len(expected)
