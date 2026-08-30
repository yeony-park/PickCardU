import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

from generation import answer_question


class FakeClient:
    def __init__(self, output):
        self.output = output
        self.calls = 0
        self.schema = None

    def structured_response(self, developer, user, schema, model, reasoning):
        self.calls += 1
        self.schema = schema
        assert "SOURCE" in user
        assert "페이지나 파일 경로를 직접 생성하지 마세요" in developer
        assert "insufficient_evidence=true이면 cited_source_ids는 빈 배열" in developer
        return self.output, {"total_tokens": 10}


def parent():
    return {
        "chunk_id": "p1",
        "document_id": "issuer/card",
        "source_path": "data/raw/issuer/card.pdf",
        "page_start": 2,
        "page_end": 2,
        "section_path": ["card", "혜택"],
        "text": "연 2회 무료",
        "supporting_children": ["c1"],
    }


def test_generation_resolves_model_source_id_to_server_metadata():
    client = FakeClient({"answer": "연 2회입니다.", "cited_source_ids": ["S1"], "insufficient_evidence": False})

    result = answer_question(client, "몇 회인가요?", [parent()])

    assert result["answer"] == "연 2회입니다."
    assert result["citations"][0]["document_id"] == "issuer/card"
    assert result["citations"][0]["page_start"] == 2
    assert result["citations"][0]["supporting_children"] == ["c1"]
    assert "uniqueItems" not in client.schema["properties"]["cited_source_ids"]


def test_duplicate_model_source_ids_are_deduplicated_server_side():
    client = FakeClient({"answer": "연 2회입니다.", "cited_source_ids": ["S1", "S1"], "insufficient_evidence": False})

    result = answer_question(client, "몇 회인가요?", [parent()])

    assert len(result["citations"]) == 1


def test_server_source_id_cannot_be_overridden_by_parent_metadata():
    client = FakeClient({"answer": "연 2회입니다.", "cited_source_ids": ["S1"], "insufficient_evidence": False})
    value = {**parent(), "source_id": "attacker-controlled"}

    result = answer_question(client, "몇 회인가요?", [value])

    assert result["citations"][0]["source_id"] == "S1"


def test_generation_does_not_call_model_without_context():
    client = FakeClient({})

    result = answer_question(client, "질문", [])

    assert result["insufficient_evidence"] is True
    assert client.calls == 0


def test_unknown_model_source_id_is_rejected():
    client = FakeClient({"answer": "답", "cited_source_ids": ["S9"], "insufficient_evidence": False})

    with pytest.raises(ValueError, match="unknown source"):
        answer_question(client, "질문", [parent()])


def test_supported_answer_without_citation_is_rejected():
    client = FakeClient({"answer": "근거 없는 답", "cited_source_ids": [], "insufficient_evidence": False})

    with pytest.raises(ValueError, match="cite at least one"):
        answer_question(client, "질문", [parent()])


def test_insufficient_answer_with_citation_is_rejected():
    client = FakeClient({"answer": "모르겠습니다.", "cited_source_ids": ["S1"], "insufficient_evidence": True})

    with pytest.raises(ValueError, match="must not cite"):
        answer_question(client, "질문", [parent()])


def test_empty_model_answer_is_rejected():
    client = FakeClient({"answer": "   ", "cited_source_ids": ["S1"], "insufficient_evidence": False})

    with pytest.raises(ValueError, match="empty answer"):
        answer_question(client, "질문", [parent()])
