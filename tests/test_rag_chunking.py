import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

import build_chunks
from chunking import (
    CHUNK_CORPUS_FINGERPRINT_VERSION,
    CHUNK_SUMMARY_SCHEMA_VERSION,
    child_texts,
    chunk_build_sha256,
    chunk_config_sha256,
    chunk_corpus_sha256,
    chunk_document,
    chunk_rows_sha256,
)
from common import SourceDocument, value_sha256


def document(blocks, text):
    return {
        "document_id": "issuer/card",
        "source": {
            "issuer": "issuer",
            "card_name": "card",
            "path": "data/raw/issuer/card.pdf",
            "sha256": "abc",
        },
        "pages": [
            {
                "page_num": 1,
                "resolved_text": text,
                "layout": {"blocks": blocks, "tables": [], "coordinate_space": "normalized_0_1"},
                "verification": {"verdict": "pass", "issues": []},
            }
        ],
    }


def test_heading_layout_builds_deterministic_parent_child_tree():
    blocks = [
        {"block_id": "b1", "reading_order": 1, "type": "heading1", "text": "# 혜택"},
        {"block_id": "b2", "reading_order": 2, "type": "paragraph", "text": "본문"},
    ]
    source = document(blocks, "혜택\n\n본문 설명")

    parents, children = chunk_document(source, child_max_chars=100, child_overlap_chars=10)
    parents_again, children_again = chunk_document(source, child_max_chars=100, child_overlap_chars=10)

    assert parents == parents_again
    assert children == children_again
    assert len(parents) == 1
    assert all(child["parent_id"] == parents[0]["chunk_id"] for child in children)
    assert parents[0]["text_source"] == "gpt-5.6-luna-200dpi"


def test_page_fallback_is_used_without_layout_heading():
    parents, children = chunk_document(document([], "제목 없는 본문"), child_max_chars=100, child_overlap_chars=10)

    assert parents[0]["section_path"][-1] == "page 1"
    assert "page_parent_fallback" in parents[0]["quality_flags"]
    assert children


def test_markdown_table_remains_atomic_child():
    text = "표\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n설명"
    parents, children = chunk_document(document([], text), child_max_chars=25, child_overlap_chars=3)

    table_children = [child for child in children if child["table_atomic"]]
    assert len(table_children) == 1
    assert "| 1 | 2 |" in table_children[0]["text"]


def test_oversized_plain_paragraph_is_split_to_embedding_safe_children():
    children = child_texts("가" * 20_000, max_chars=1600, overlap_chars=160)

    assert len(children) > 1
    assert all(len(text) <= 1600 for text, table_atomic in children if not table_atomic)


def test_upstage_table_hint_keeps_non_markdown_luna_table_atomic():
    blocks = [
        {
            "block_id": "b1",
            "reading_order": 1,
            "type": "table",
            "text": "구분 연회비 국내전용 5천원 해외겸용 5천원",
        }
    ]
    source = document(blocks, "구분 연회비 국내전용 5천원 해외겸용 5천원")

    _, children = chunk_document(source, child_max_chars=12, child_overlap_chars=2)

    assert len(children) == 1
    assert children[0]["table_atomic"] is True


def test_build_chunks_records_current_config_and_corpus_fingerprints(tmp_path, monkeypatch):
    source = SourceDocument(
        document_id="issuer/card",
        issuer="issuer",
        card_name="card",
        path=tmp_path / "card.pdf",
        relative_path="data/raw/issuer/card.pdf",
        sha256="abc",
        page_count=1,
    )
    canonical = document([], "주유 리터당 70원 할인")
    primary_config_sha256 = value_sha256(build_chunks.luna_config(6))
    layout_config_sha256 = value_sha256(build_chunks.upstage_config())
    canonical["primary_parser"] = {"batch_pages": 6, "config_sha256": primary_config_sha256}
    canonical["layout_parser"] = {"config_sha256": layout_config_sha256}
    canonical_dir = tmp_path / "canonical"
    canonical_path = canonical_dir / "issuer" / "card.json"
    canonical_path.parent.mkdir(parents=True)
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "chunks"
    rag_dir = tmp_path / "rag"
    monkeypatch.setattr(build_chunks, "discover_documents", lambda _issuers, _documents: [source])
    monkeypatch.setattr(build_chunks, "CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(build_chunks, "RAG_DIR", rag_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_chunks.py", "--output-dir", str(output_dir)],
    )

    build_chunks.main()

    parents = [json.loads(line) for line in (output_dir / "parents.jsonl").read_text(encoding="utf-8").splitlines()]
    children = [json.loads(line) for line in (output_dir / "children.jsonl").read_text(encoding="utf-8").splitlines()]
    summary = json.loads((rag_dir / "reports" / "chunk_summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == CHUNK_SUMMARY_SCHEMA_VERSION
    assert summary["chunk_corpus_fingerprint_version"] == CHUNK_CORPUS_FINGERPRINT_VERSION
    assert summary["source_corpus_sha256"] == value_sha256([source.as_dict()])
    assert summary["parent_corpus_sha256"] == chunk_rows_sha256(parents)
    assert summary["child_corpus_sha256"] == chunk_rows_sha256(children)
    assert summary["chunk_corpus_sha256"] == chunk_corpus_sha256(parents, children)
    assert summary["chunk_config_sha256"] == chunk_config_sha256(summary)
    assert summary["chunk_build_sha256"] == chunk_build_sha256(summary)
    assert {row["child_max_chars"] for row in [*parents, *children]} == {1600}
    assert {row["child_overlap_chars"] for row in [*parents, *children]} == {160}
