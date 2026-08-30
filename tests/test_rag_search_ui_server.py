from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

from chunking import CHUNK_CORPUS_FINGERPRINT_VERSION, chunk_corpus_sha256
from hybrid_index import HybridIndex, child_index_fingerprint, encode_vector
from openai_client import MAX_GENERATION_OUTPUT_TOKENS, OpenAIClient
from serve_search_ui import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    SearchRequestHandler,
    SearchService,
    build_parser,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


class FakeEmbeddingClient:
    def __init__(self, generation_output: dict | None = None) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.generation_calls: list[dict] = []
        self.generation_output = generation_output or {
            "answer": "공항 라운지는 연 2회 무료로 이용할 수 있습니다.",
            "cited_source_ids": ["S1"],
            "insufficient_evidence": False,
        }

    def embeddings(self, texts: list[str], model: str):
        self.calls.append((texts, model))
        return [[0.0, 1.0] for _ in texts], {"prompt_tokens": 7}

    def structured_response(self, developer, user, schema, model, reasoning):
        self.generation_calls.append(
            {
                "developer": developer,
                "user": user,
                "schema": schema,
                "model": model,
                "reasoning": reasoning,
            }
        )
        return self.generation_output, {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}


def fixture_service(
    tmp_path: Path,
    *,
    external: bool = False,
    generation: bool = False,
    generation_output: dict | None = None,
):
    raw_dir = tmp_path / "data" / "raw" / "issuer"
    raw_dir.mkdir(parents=True)
    (raw_dir / "card-a.pdf").write_bytes(b"%PDF-fixture-a")
    (raw_dir / "card-b.pdf").write_bytes(b"%PDF-fixture-b")
    parents = [
        {
            "chunk_id": "p1",
            "parent_id": None,
            "kind": "parent",
            "document_id": "issuer/card-a",
            "issuer": "issuer",
            "card_name": "card-a",
            "source_path": "data/raw/issuer/card-a.pdf",
            "page_start": 1,
            "page_end": 1,
            "section_path": ["card-a", "주유"],
            "text": "주유 리터당 70원 할인",
        },
        {
            "chunk_id": "p2",
            "parent_id": None,
            "kind": "parent",
            "document_id": "issuer/card-b",
            "issuer": "issuer",
            "card_name": "card-b",
            "source_path": "data/raw/issuer/card-b.pdf",
            "page_start": 2,
            "page_end": 3,
            "section_path": ["card-b", "라운지"],
            "text": "공항 라운지 무료 이용",
        },
    ]
    children = [
        {**parents[0], "chunk_id": "c1", "parent_id": "p1", "kind": "child"},
        {
            **parents[1],
            "chunk_id": "c2",
            "parent_id": "p2",
            "kind": "child",
            "text": "공항 라운지 연 2회 무료",
        },
    ]
    parents_path = tmp_path / "parents.jsonl"
    children_path = tmp_path / "children.jsonl"
    write_jsonl(parents_path, parents)
    write_jsonl(children_path, children)
    index_path = tmp_path / "index.sqlite3"
    index = HybridIndex(index_path)
    index.rebuild(parents_path, children_path)
    metadata = {
        "chunk_corpus_fingerprint_version": CHUNK_CORPUS_FINGERPRINT_VERSION,
        "chunk_corpus_sha256": chunk_corpus_sha256(parents, children),
        "index_corpus_sha256": child_index_fingerprint(children),
    }
    index.rebuild(parents_path, children_path, metadata)
    with index.connection:
        for child_id, vector in [("c1", [1.0, 0.0]), ("c2", [0.0, 1.0])]:
            blob, norm = encode_vector(vector)
            index.connection.execute(
                """
                UPDATE children
                SET embedding=?, embedding_dim=2, embedding_norm=?, embedding_model='fake'
                WHERE child_id=?
                """,
                (blob, norm, child_id),
            )
    index.close()
    client = FakeEmbeddingClient(generation_output) if external or generation else None
    service = SearchService(
        index_path,
        metadata,
        enable_external_models=external,
        enable_generation=generation,
        embedding_model="fake",
        client=client,
        project_root=tmp_path,
    )
    return service, client


def invoke_handler(
    tmp_path: Path,
    service: SearchService,
    method: str,
    path: str,
    payload: dict | None = None,
    host: str = "127.0.0.1:8765",
):
    html = tmp_path / "rag_search_tester.html"
    html.write_text("<!doctype html><title>search tester</title>", encoding="utf-8")
    html.with_name("rag_pipeline_dashboard.html").write_text(
        "<!doctype html><title>pipeline dashboard</title>", encoding="utf-8"
    )
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
    handler = object.__new__(SearchRequestHandler)
    handler.server = SimpleNamespace(
        search_service=service,
        html_path=html,
        allowed_hosts={"127.0.0.1:8765", "localhost:8765"},
    )
    handler.path = path
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.client_address = ("127.0.0.1", 12345)
    handler.headers = {
        "Host": host,
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    getattr(handler, f"do_{method}")()
    raw = handler.wfile.getvalue()
    head, response_body = raw.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {
        name.casefold(): value.strip()
        for name, value in (line.split(":", 1) for line in lines[1:] if ":" in line)
    }
    return status, headers, response_body


def test_keyword_search_is_local_and_returns_parent_child_scores_and_source(tmp_path):
    service, client = fixture_service(tmp_path)
    assert client is None
    status_code, headers, body = invoke_handler(tmp_path, service, "GET", "/api/status")
    status = json.loads(body)
    assert status_code == 200
    assert "access-control-allow-origin" not in headers
    assert status["index"] == {"documents": 2, "parents": 2, "children": 2}
    assert status["modes"]["keyword"] == {"available": True, "external": False}
    assert status["modes"]["hybrid"] == {"available": False, "external": True}
    assert status["generation"]["available"] is False
    assert status["generation"]["model"] == "gpt-5.6-luna"
    assert status["filters"]["issuers"][0]["cards"][0]["document_id"] == "issuer/card-a"

    status_code, _, body = invoke_handler(
        tmp_path,
        service,
        "POST",
        "/api/search",
        {"query": "주유 70원", "mode": "keyword", "top_k": 1},
    )
    result = json.loads(body)
    assert status_code == 200
    assert result["embedding_used"] is False
    assert result["embedding_latency_ms"] is None
    assert result["index_latency_ms"] >= 0
    assert result["latency_ms"] >= result["index_latency_ms"]
    hit = result["results"][0]
    assert hit["document_id"] == "issuer/card-a"
    assert hit["parent"]["text"] == "주유 리터당 70원 할인"
    assert hit["child"]["chunk_id"] == "c1"
    assert hit["keyword_score"] is not None
    assert hit["vector_score"] is None
    assert hit["source_url"].startswith("/api/source?document_id=issuer%2Fcard-a")

    status_code, headers, body = invoke_handler(
        tmp_path, service, "GET", hit["source_url"].split("#", 1)[0]
    )
    assert status_code == 200
    assert headers["content-type"] == "application/pdf"
    assert body == b"%PDF-fixture-a"


def test_only_two_allowlisted_html_files_are_served(tmp_path):
    service, _ = fixture_service(tmp_path)
    for path in ["/", "/rag_search_tester.html"]:
        status, _, body = invoke_handler(tmp_path, service, "GET", path)
        assert status == 200
        assert b"search tester" in body
    status, _, body = invoke_handler(tmp_path, service, "GET", "/rag_pipeline_dashboard.html")
    assert status == 200
    assert b"pipeline dashboard" in body
    status, _, _ = invoke_handler(tmp_path, service, "GET", "/../parents.jsonl")
    assert status == 404


def test_unknown_host_is_rejected_before_local_api_or_html_is_served(tmp_path):
    service, _ = fixture_service(tmp_path)
    for method, path, payload in [
        ("GET", "/api/status", None),
        ("GET", "/", None),
        ("POST", "/api/search", {"query": "주유", "mode": "keyword"}),
    ]:
        status, _, body = invoke_handler(
            tmp_path,
            service,
            method,
            path,
            payload,
            host="attacker.example:8765",
        )
        assert status == 421
        assert json.loads(body)["error"]["code"] == "invalid_host"


def test_external_mode_is_rejected_without_server_opt_in(tmp_path):
    service, _ = fixture_service(tmp_path)
    status, _, body = invoke_handler(
        tmp_path,
        service,
        "POST",
        "/api/search",
        {"query": "쉴 수 있는 곳", "mode": "vector", "top_k": 1},
    )
    assert status == 403
    assert json.loads(body)["error"]["code"] == "external_models_disabled"


def test_answer_is_rejected_without_explicit_generation_opt_in(tmp_path):
    service, client = fixture_service(tmp_path)
    status, _, body = invoke_handler(
        tmp_path,
        service,
        "POST",
        "/api/answer",
        {"query": "라운지는 몇 회인가요?", "mode": "keyword"},
    )
    assert status == 403
    assert json.loads(body)["error"]["code"] == "generation_disabled"
    assert client is None


def test_opted_in_vector_search_uses_fake_client_and_reports_embedding_cost_scope(tmp_path):
    service, client = fixture_service(tmp_path, external=True)
    status, _, body = invoke_handler(
        tmp_path,
        service,
        "POST",
        "/api/search",
        {"query": "쉴 수 있는 곳", "mode": "vector", "top_k": 1},
    )
    result = json.loads(body)
    assert status == 200
    assert client.calls == [(["쉴 수 있는 곳"], "fake")]
    assert result["embedding_used"] is True
    assert result["embedding_tokens"] == 7
    assert result["embedding_latency_ms"] >= 0
    assert result["results"][0]["document_id"] == "issuer/card-b"
    assert result["results"][0]["vector_score"] == 1.0


def test_grounded_luna_answer_reuses_selected_server_parents_without_embedding(tmp_path):
    service, client = fixture_service(tmp_path, generation=True)
    status, _, body = invoke_handler(
        tmp_path,
        service,
        "POST",
        "/api/answer",
        {
            "query": "공항 라운지는 몇 회인가요?",
            "mode": "keyword",
            "top_k": 2,
            "parent_ids": ["p2"],
        },
    )
    result = json.loads(body)
    assert status == 200
    assert client.calls == []
    assert len(client.generation_calls) == 1
    call = client.generation_calls[0]
    assert call["model"] == "gpt-5.6-luna"
    assert call["reasoning"] == "medium"
    assert "공항 라운지 무료 이용" in call["user"]
    assert "주유 리터당 70원 할인" not in call["user"]
    assert result["answer"] == "공항 라운지는 연 2회 무료로 이용할 수 있습니다."
    assert result["model"] == "gpt-5.6-luna"
    assert result["usage"]["total_tokens"] == 30
    assert result["embedding_used"] is False
    assert result["generation_latency_ms"] >= 0
    assert result["retrieval"]["selected_parent_ids"] is True
    assert result["retrieval"]["reexecuted"] is False
    assert result["retrieval"]["results"][0]["parent"]["chunk_id"] == "p2"
    citation = result["citations"][0]
    assert citation["document_id"] == "issuer/card-b"
    assert citation["source_url"].startswith("/api/source?document_id=issuer%2Fcard-b#page=2")
    assert citation["label"] == "issuer / card-b, pp.2-3"
    assert result["external_transmission"]["used"] is True


def test_answer_limits_fallback_generation_context_to_visible_top_k(tmp_path):
    service, client = fixture_service(tmp_path, generation=True)
    status, _, body = invoke_handler(
        tmp_path,
        service,
        "POST",
        "/api/answer",
        {"query": "주유 라운지", "mode": "keyword", "top_k": 1, "candidate_k": 2},
    )
    result = json.loads(body)
    assert status == 200
    assert client.calls == []
    assert client.generation_calls[0]["user"].count("[S1]") == 1
    assert "[S2]" not in client.generation_calls[0]["user"]
    assert result["retrieval"]["reexecuted"] is True
    assert result["retrieval"]["retrieved_parent_count"] == 1


def test_empty_retrieval_returns_local_abstention_without_luna_call(tmp_path):
    service, client = fixture_service(tmp_path, generation=True)
    status, _, body = invoke_handler(
        tmp_path,
        service,
        "POST",
        "/api/answer",
        {"query": "존재하지않는검색어", "mode": "keyword"},
    )
    result = json.loads(body)
    assert status == 200
    assert client.generation_calls == []
    assert result["insufficient_evidence"] is True
    assert result["citations"] == []
    assert result["external_transmission"]["used"] is False


def test_invalid_luna_citation_fails_closed_without_partial_answer(tmp_path):
    service, _ = fixture_service(
        tmp_path,
        generation=True,
        generation_output={
            "answer": "근거 없는 답",
            "cited_source_ids": ["S9"],
            "insufficient_evidence": False,
        },
    )
    status, _, body = invoke_handler(
        tmp_path,
        service,
        "POST",
        "/api/answer",
        {"query": "몇 회인가요?", "mode": "keyword", "parent_ids": ["p2"]},
    )
    result = json.loads(body)
    assert status == 502
    assert result["ok"] is False
    assert result["error"]["code"] == "generation_validation_failed"
    assert "answer" not in result


def test_answer_parent_ids_are_bounded_and_must_exist(tmp_path):
    service, client = fixture_service(tmp_path, generation=True)
    for payload, expected_status, expected_code in [
        (
            {"query": "질문", "mode": "keyword", "parent_ids": ["p1", "p1"]},
            400,
            "invalid_request",
        ),
        (
            {"query": "질문", "mode": "keyword", "parent_ids": ["missing"]},
            409,
            "stale_parent_ids",
        ),
    ]:
        status, _, body = invoke_handler(tmp_path, service, "POST", "/api/answer", payload)
        assert status == expected_status
        assert json.loads(body)["error"]["code"] == expected_code
    assert client.generation_calls == []


def test_search_request_validation_rejects_oversized_query_and_unknown_fields(tmp_path):
    service, _ = fixture_service(tmp_path)
    for payload in [
        {"query": "x" * 501, "mode": "keyword"},
        {"query": "주유", "mode": "keyword", "unknown": True},
        {"query": "주유", "mode": "keyword", "top_k": 10, "candidate_k": 5},
    ]:
        status, _, body = invoke_handler(tmp_path, service, "POST", "/api/search", payload)
        assert status == 400
        assert json.loads(body)["error"]["code"] == "invalid_request"


def test_cli_defaults_to_fixed_loopback_address():
    args = build_parser().parse_args([])
    assert args.host == DEFAULT_HOST == "127.0.0.1"
    assert args.port == DEFAULT_PORT == 8765
    assert args.enable_external_models is False
    assert args.enable_generation is False


def test_structured_luna_response_has_a_bounded_output_budget(monkeypatch):
    client = OpenAIClient(api_key="test")
    captured = {}

    def fake_post(endpoint, payload):
        captured.update({"endpoint": endpoint, "payload": payload})
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}
            ],
            "usage": {},
        }

    monkeypatch.setattr(client, "post_json", fake_post)
    client.structured_response("developer", "user", {"type": "object"})

    assert captured["endpoint"] == "/responses"
    assert captured["payload"]["model"] == "gpt-5.6-luna"
    assert captured["payload"]["max_output_tokens"] == MAX_GENERATION_OUTPUT_TOKENS == 1600
    assert captured["payload"]["store"] is False
