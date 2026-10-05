"""Test-only local browser fixture. Never loads keys or the real runtime index."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import uvicorn
from pickcardu_rag.errors import LlmUnavailable
from pickcardu_rag.answering import RewriteOutput
from pickcardu_rag_api.chat_store import ChatStore
from pickcardu_rag_api.main import create_app
from support import FakeProvider, FakeReranker, build_release, settings


class BrowserProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.failed_queries = set()

    def rewrite(self, context, *, references=None):
        return RewriteOutput(standalone_query=context[-1]['content']), {'model': 'fixture', 'usage': {'total_tokens': 0}}

    def answer(self, query, evidence, *, comparison=False):
        if '대기' in query:
            time.sleep(3)
        if '실패' in query and query not in self.failed_queries:
            self.failed_queries.add(query)
            raise LlmUnavailable('테스트용 실패')
        return super().answer(query, evidence, comparison=comparison)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8100)
    args = parser.parse_args()
    root = args.database.resolve().parent
    if not (root / 'runtime/active-index.json').exists():
        build_release(root / 'runtime')
    app = create_app(settings(root), provider=BrowserProvider(), reranker=FakeReranker(), chat_store=ChatStore(args.database))
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
