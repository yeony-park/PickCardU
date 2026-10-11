"""Resume with one explicitly approved reference-document change disclosed.

The original manifests, frozen scorer and retrieval sources remain immutable.
Only the committed metric guide is accepted, with old/new records preserved.
This separate runner is needed because the frozen runner cannot distinguish an
approved reference-doc edit from an experiment-input mutation.
"""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

DOCUMENT = 'docs/EVALUATION_METRICS.md'
APPROVED_COMMIT = '724338188c8e3c2c28fac2f05bfe9b9d7ec9f8aa'
ORIGINAL_SHA256 = '20500298d77085cdf06f570e524bd2484d9951ba8e1f858729fc6fc230b30d1d'
APPROVED_SHA256 = 'b3ef6e8b51d2a5afbd7869f5b7a7985ac233fedce9dc9aa259ec2bc0b740e219'
SNAPSHOT_SHA256 = '6967099b28e0fdded6dbc88d0faef24db697bda75e37cc587bddbe1ed0b489b6'


def verify_snapshot(common, stage, allowance):
    """Retain all differences in the audit; accept only the exact doc record."""
    if allowance['path'] != DOCUMENT:
        raise RuntimeError('Only the approved reference document can change')
    started = time.perf_counter()
    snapshot_path = common.STAGES[34] / 'protected_files.json'
    if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != allowance['snapshot_sha256']:
        raise RuntimeError('Original preservation snapshot changed')
    snapshot = common.read_json(snapshot_path)
    baseline = [row for row in snapshot['files'] if row['path'] == DOCUMENT]
    if baseline != [allowance['before']]:
        raise RuntimeError('Document preservation baseline mismatch')
    changed, missing, unauthorized, accepted = [], [], [], []
    for row in snapshot['files']:
        path = common.ROOT / row['path']
        if not path.is_file():
            missing.append(row['path'])
            continue
        current = common.file_record(path)
        if row['path'] == DOCUMENT and current != allowance['after']:
            unauthorized.append(row['path'])
        if current != row:
            changed.append(row['path'])
            if row['path'] == DOCUMENT and current == allowance['after']:
                accepted.append(allowance)
            elif row['path'] not in unauthorized:
                unauthorized.append(row['path'])
    summary = {'checked_files': len(snapshot['files']), 'changed': changed,
               'missing': missing, 'unauthorized_changes': unauthorized,
               'authorized_changes': accepted,
               'passed': not unauthorized and not missing,
               'wall_seconds': time.perf_counter() - started,
               'original_manifest_unchanged': True,
               'note': 'Reference-doc change is disclosed; all experiment-input records remain strict.'}
    common.write_json(common.STAGES[stage] / 'preservation_verification.json', summary)
    if not summary['passed']:
        raise RuntimeError(f'Original preservation failed: {summary}')
    return summary


def bind_allowance(common):
    snapshot_path = common.STAGES[34] / 'protected_files.json'
    if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != SNAPSHOT_SHA256:
        raise RuntimeError('Original preservation snapshot changed')
    baseline = common.read_json(snapshot_path)
    rows = [row for row in baseline['files'] if row['path'] == DOCUMENT]
    if len(rows) != 1 or rows[0]['sha256'] != ORIGINAL_SHA256:
        raise RuntimeError('Approved document has an unexpected original baseline')
    approved = subprocess.run(
        ['git', 'show', f'{APPROVED_COMMIT}:{DOCUMENT}'], cwd=common.ROOT,
        check=True, capture_output=True).stdout
    current = common.file_record(common.ROOT / DOCUMENT)
    if (hashlib.sha256(approved).hexdigest() != APPROVED_SHA256
            or current['sha256'] != APPROVED_SHA256 or current['size'] != len(approved)):
        raise RuntimeError('Reference document differs from the approved commit')
    allowance = {'path': DOCUMENT, 'before': rows[0], 'after': current,
                 'commit': APPROVED_COMMIT, 'snapshot_sha256': SNAPSHOT_SHA256}
    receipt = common.STAGES[40] / 'authorized_document_change_binding.json'
    if receipt.exists() and common.read_json(receipt) != allowance:
        raise RuntimeError('Approved reference document changed after its receipt')
    if not receipt.exists():
        common.write_json(receipt, allowance)
    return allowance


def install(common):
    """Use only in a stage-40 continuation or its audit/report process."""
    common.initialize(40)
    allowance = bind_allowance(common.F)
    common.F.verify_preservation = lambda stage=40: verify_snapshot(common.F, stage, allowance)
    return allowance


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Preservation check only; no model/API calls')
    args = parser.parse_args()
    path = Path(__file__).with_name('40_memory_recovery.py')
    spec = importlib.util.spec_from_file_location('approved_doc_recovery', path)
    recovery = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = recovery
    spec.loader.exec_module(recovery)
    runner = recovery.tools()
    install(runner.C)
    if args.check:
        print(runner.C.verify(), flush=True)
        return
    # Reuse the same common object so the disclosed preservation check is used
    # at every original verification boundary, without editing frozen sources.
    recovery.tools = lambda: runner
    recovery.resume()


if __name__ == '__main__':
    main()
