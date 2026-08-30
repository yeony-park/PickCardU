from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import RAG_DIR, ROOT, write_jsonl


GOLD_DIR = ROOT / "data" / "ocr_benchmark" / "gold" / "structured"


def scalar_terms(value) -> list[str]:
    if isinstance(value, dict):
        return [term for nested in value.values() for term in scalar_terms(nested)]
    if isinstance(value, list):
        return [term for nested in value for term in scalar_terms(nested)]
    if isinstance(value, (str, int, float)) and str(value).strip():
        return [str(value).strip()]
    return []


def build_queries() -> list[dict]:
    queries = []
    seen = set()
    for path in sorted(GOLD_DIR.glob("*/*.json")):
        gold = json.loads(path.read_text(encoding="utf-8"))
        document_id = f"{gold['issuer']}/{gold['card_name']}"
        for label in [*gold.get("numeric_labels", []), *gold.get("field_labels", [])]:
            key = (document_id, int(label["page_num"]), label["id"])
            if key in seen:
                continue
            seen.add(key)
            context = " ".join(label.get("context_terms", [])) or label["id"].replace("_", " ")
            expected_answer = label.get("surface_text", label.get("value"))
            expected_terms = list(
                dict.fromkeys(
                    [
                        *[str(term).strip() for term in label.get("context_terms", []) if str(term).strip()],
                        *scalar_terms(expected_answer),
                    ]
                )
            )
            queries.append(
                {
                    "query_id": f"{gold['issuer']}:{gold['card_name']}:{label['id']}",
                    "question": f"{gold['card_name']}에서 {context}에 해당하는 내용은 무엇인가요?",
                    "expected_document_id": document_id,
                    "expected_page": int(label["page_num"]),
                    "expected_answer": expected_answer,
                    "expected_terms": expected_terms,
                    "critical": bool(label.get("critical", False)),
                    "kind": "gold_context_seed",
                }
            )
    return queries


def main() -> None:
    parser = argparse.ArgumentParser(description="Build retrieval-evaluation queries from existing OCR gold labels.")
    parser.add_argument("--output", type=Path, default=RAG_DIR / "eval" / "gold_queries.jsonl")
    args = parser.parse_args()
    queries = build_queries()
    count = write_jsonl(args.output, queries)
    print(f"{args.output}: {count} queries")


if __name__ == "__main__":
    main()
