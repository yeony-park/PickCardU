from __future__ import annotations

import argparse
from datetime import datetime

from common import RAG_DIR, discover_documents, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a stable manifest for every source PDF.")
    parser.add_argument("--issuer", action="append", dest="issuers")
    parser.add_argument("--document", action="append", dest="documents")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    documents = discover_documents(args.issuers, args.documents, args.limit)
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "document_count": len(documents),
        "page_count": sum(document.page_count for document in documents),
        "documents": [document.as_dict() for document in documents],
    }
    destination = RAG_DIR / "manifest.json"
    write_json(destination, manifest)
    print(f"{destination}: {manifest['document_count']} documents / {manifest['page_count']} pages")


if __name__ == "__main__":
    main()
