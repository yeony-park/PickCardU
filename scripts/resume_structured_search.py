"""Resume only the 17 ungenerated integrations and replay C11 without billing.

The prior stopped batch stays immutable. A new manifest binds both its bytes and
this runner, while inheriting the existing journal's conservative budget.
"""
from scripts.validate_structured_search import *
import shutil

OLD = ROOT / 'data/rag/validation/structured_search_integration'
DEFAULT_OUTPUT = ROOT / 'data/rag/validation/structured_search_activation'
REPLAY_ID = 'C11:card_page_section_benefit'
GENERATED = tuple(q + ':' + PROFILES[0] for q in QUERY_IDS[:7])


# Pinned final seal, separate from the frozen code used during paid execution.
COMPLETION_SEAL_SHA256 = 'b811b845047bc284de1dae1e09a6aa3719272ccf486b8b0f7a933cd029510f1e'


def verify_completion(output):
    path = output / 'completion_seal.json'
    if not path.is_file() or path.is_symlink():
        raise RuntimeError('completion seal is missing; paid execution remains disabled')
    if digest(path) != COMPLETION_SEAL_SHA256:
        raise RuntimeError('completion seal changed; paid execution remains disabled')
    seal = read(path)
    expected = {q + ':' + p for p in PROFILES for q in QUERY_IDS}
    wanted_answers = {'answers/' + i + '.json' for i in expected}
    files = seal['files_sha256']
    if {n for n in files if n.startswith('answers/')} != wanted_answers:
        raise RuntimeError('completion seal answer matrix mismatch')
    if len([n for n in files if n.startswith('requests/')]) != 52:
        raise RuntimeError('completion seal request matrix mismatch')
    for name, expected_hash in files.items():
        item = output / name
        if not item.is_file() or item.is_symlink() or digest(item) != expected_hash:
            kind = 'completed answer' if name.startswith('answers/') else 'completed request'
            raise RuntimeError(f'{kind} missing or changed: {name}; regeneration is forbidden')
    return seal


def remaining_identities():
    """Historical selection only; the completed runner authorizes no requests."""
    return [q + ':' + p for p in PROFILES for q in QUERY_IDS if q + ':' + p not in GENERATED]


def prior_files():
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(OLD.rglob('*')) if p.is_file()}


def prior_debit():
    rows = [read(p) for p in (OLD/'requests').glob('*.json')]
    if len(rows) != 21 or any(r.get('cost_usd') is None for r in rows):
        raise RuntimeError('prior journal is not the fully priced 21-request batch')
    value = Decimal('2.826878200') + sum((Decimal(r['cost_usd']) for r in rows), Decimal(0))
    if value != Decimal('2.854166615'):
        raise RuntimeError('prior debit changed')
    return value


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    for p in (OLD/'answers').glob('*.json'):
        target=output/'answers'/p.name
        if target.exists() and target.read_bytes() != p.read_bytes():
            raise RuntimeError('inherited answer changed')
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(p,target)
    offline(output)


class ResumeBudget(Budget):
    def halted(self):
        root=self.directory.parent
        halt=root/'HALTED.json'
        if not halt.exists():
            return False
        policy=root/'continue_known_output_failure.json'
        if not policy.exists():
            return True
        decision=read(policy)
        row=read(halt)
        return not (decision.get('halt_sha256') == digest(halt)
            and decision.get('policy') == 'count_known_grounding_failure_without_regeneration'
            and row.get('identity') == 'C01:parent_child_bundle'
            and row.get('response',{}).get('code') == 'LLM_UNGROUNDED'
            and 'mixed_card_citations' in row.get('response',{}).get('message',''))
    def reserve(self, identity, phase, request):
        if identity not in remaining_identities():
            raise RuntimeError('identity is not an approved remaining answer')
        verify_completion(self.directory.parent)
        raise RuntimeError('completed batch: all network reservations are disabled')


class ReplayTransport:
    def __init__(self, directory=OLD/'requests'):
        self.directory, self.identity, self.used = directory, None, []
    def handle_request(self, request):
        import httpx2
        body=json.loads(request.content)
        phase=('embedding' if request.url.path.endswith('/embeddings') else
               'answer' if body.get('text',{}).get('format',{}).get('name') != 'query_plan' and body.get('model') == 'gpt-5.6-luna' else 'extract')
        row=read(self.directory/f'{self.identity}:{phase}:1.json')
        if row['request'] != body:
            raise RuntimeError('replay request differs from saved original')
        self.used.append(phase)
        return httpx2.Response(row['http_status'],json=row['raw_response'],request=request)
    def close(self):
        pass


def run(output):
    # This completed batch is retired, including when its files are damaged.
    verify_completion(output)
    raise RuntimeError('completed batch: all network execution is disabled')


def main():
    import sys
    sys.path[:0]=[str(ROOT/'packages/rag-core/src'),str(ROOT/'services/rag-api/src')]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-network',action='store_true')
    a=p.parse_args()
    if a.allow_network:
        run(DEFAULT_OUTPUT)
    else:
        seal=verify_completion(DEFAULT_OUTPUT)
        print(json.dumps({'completed_answers':seal['answers'],'network_allowed':False}))

if __name__ == '__main__':
    main()
