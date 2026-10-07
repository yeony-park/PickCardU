"""Verify a Git checkout against the PDF snapshot used by the deployed index."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('repository', type=Path)
args = parser.parse_args()
root = args.repository.resolve()
site = Path(__file__).resolve().parents[1]
source = json.loads((site / 'public/corpus/document-source.json').read_text())
corpus = json.loads((site / 'public/corpus/index.json').read_text())
manifest = json.loads((site / 'public/corpus/manifest.json').read_text())

def git(*parts):
    return subprocess.check_output(['git', '-C', str(root), *parts])

commit = git('rev-parse', 'HEAD').decode().strip()
expected = {d['path']: d['sha256'] for d in source['documents']}
tracked = {p for p in git('ls-tree', '-r', '--name-only', 'HEAD', '--', 'data/raw').decode().splitlines() if p.endswith('.pdf')}
if git('status', '--porcelain', '--', 'data/raw').strip():
    raise SystemExit('원문 폴더에 저장되지 않은 변경이 있습니다. 깨끗한 Git 사본으로 검증하세요.')
if tracked != set(expected):
    raise SystemExit('원문 목록이 바뀌었습니다. 문서 처리와 인덱스 생성을 다시 실행해야 합니다.')
different = [p for p, digest in expected.items() if hashlib.sha256((root / p).read_bytes()).hexdigest() != digest]
if different:
    raise SystemExit('원문 내용 변경: ' + ', '.join(different) + '. 기존 인덱스를 새 문서의 결과로 배포하지 마세요.')
if {p['source_path'] for p in corpus['parents']} != tracked:
    raise SystemExit('검색 인덱스의 문서 목록이 원문 목록과 다릅니다.')
if manifest['metadata']['chunk_corpus_sha256'] != source['chunk_corpus_sha256']:
    raise SystemExit('검색 청크가 바뀌었습니다. 문서 출처 검증 기록도 다시 생성해야 합니다.')
for name, digest in manifest['assets'].items():
    if hashlib.sha256((site / 'public/corpus' / name).read_bytes()).hexdigest() != digest:
        raise SystemExit('인덱스 파일 해시 불일치: ' + name)
print(json.dumps({'verified_documents': len(expected), 'checked_commit': commit,
                  'published_document_commit': source['commit'], 'same_pdf_snapshot': True}))
