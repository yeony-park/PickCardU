from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from common import RAG_DIR, ROOT, RUNTIME_DIR
from generation import GENERATION_MODEL, answer_question
from hybrid_index import EMBEDDING_MODEL, HybridIndex
from hybrid_rag import CHILDREN_PATH, CHUNK_SUMMARY_PATH, PARENTS_PATH, validate_chunk_corpus
from openai_client import OpenAIClient


INDEX_PATH = RUNTIME_DIR / "hybrid_index.sqlite3"
HTML_PATH = RAG_DIR / "reports" / "rag_search_tester.html"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 16 * 1024
MAX_QUERY_CHARS = 500
MAX_TOP_K = 20
MAX_CANDIDATE_K = 200
MAX_ANSWER_PARENT_IDS = 5
MAX_PARENT_ID_CHARS = 200
GENERATION_REASONING = "medium"
GENERATION_CONTEXT_CHARS = 24000
EXTERNAL_MODES = {"vector", "hybrid", "weighted"}
ALL_MODES = {"keyword", *EXTERNAL_MODES}


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "invalid_request",
            f"{name} must be an integer from {minimum} to {maximum}",
        )
    return value


def _optional_text(value: Any, name: str, maximum: int) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", f"{name} must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > maximum:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "invalid_request",
            f"{name} must be at most {maximum} characters",
        )
    return text


class SearchService:
    def __init__(
        self,
        index_path: Path,
        expected_index_metadata: dict[str, Any],
        *,
        enable_external_models: bool = False,
        enable_generation: bool = False,
        embedding_model: str = EMBEDDING_MODEL,
        generation_model: str = GENERATION_MODEL,
        generation_reasoning: str = GENERATION_REASONING,
        client: OpenAIClient | None = None,
        project_root: Path = ROOT,
    ):
        self.index_path = index_path
        self.expected_index_metadata = expected_index_metadata
        self.enable_external_models = enable_external_models
        self.enable_generation = enable_generation
        self.embedding_model = embedding_model
        self.generation_model = generation_model
        self.generation_reasoning = generation_reasoning
        self.client = client
        self.project_root = project_root.resolve()
        self.raw_root = (self.project_root / "data" / "raw").resolve()

        index = HybridIndex(index_path)
        try:
            index.require_build_metadata(expected_index_metadata)
            embedding_status = index.embedding_status(embedding_model)
            if (enable_external_models or enable_generation) and client is None:
                raise ValueError("enabled external features require an OpenAI client and API key")
            if enable_external_models:
                embedding_status = index.require_embedding_coverage(embedding_model)
            self._status, self._source_paths = self._build_status(index, embedding_status)
        finally:
            index.close()

    def _build_status(
        self,
        index: HybridIndex,
        embedding_status: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Path]]:
        rows = index.connection.execute(
            """
            SELECT document_id, issuer, card_name, source_path
            FROM parents
            GROUP BY document_id, issuer, card_name, source_path
            ORDER BY issuer COLLATE NOCASE, card_name COLLATE NOCASE, document_id
            """
        ).fetchall()
        source_paths: dict[str, Path] = {}
        issuers: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            document_id = str(row["document_id"])
            issuer = str(row["issuer"])
            card_name = str(row["card_name"])
            issuers.setdefault(issuer, []).append(
                {"id": card_name, "label": card_name, "document_id": document_id}
            )
            source = (self.project_root / str(row["source_path"])).resolve()
            if source.is_relative_to(self.raw_root) and source.suffix.casefold() == ".pdf" and source.is_file():
                source_paths[document_id] = source

        filters = {
            "issuers": [
                {"id": issuer, "label": issuer, "cards": cards}
                for issuer, cards in issuers.items()
            ]
        }
        parent_count = int(index.connection.execute("SELECT count(*) FROM parents").fetchone()[0])
        child_count = int(index.connection.execute("SELECT count(*) FROM children").fetchone()[0])
        complete_embeddings = (
            embedding_status["total_children"] > 0
            and embedding_status["embedded_children"] == embedding_status["total_children"]
            and len(embedding_status["embedding_dimensions"]) == 1
        )
        modes = {
            "keyword": {"available": True, "external": False},
            **{
                mode: {"available": self.enable_external_models, "external": True}
                for mode in sorted(EXTERNAL_MODES)
            },
        }
        status = {
            "ok": True,
            "service": "rag-search",
            "index": {"documents": len(rows), "parents": parent_count, "children": child_count},
            "embedding": {
                "available": self.enable_external_models,
                "indexed": complete_embeddings,
                "model": self.embedding_model,
            },
            "generation": {
                "available": self.enable_generation,
                "external": True,
                "provider": "OpenAI",
                "model": self.generation_model,
                "reasoning": self.generation_reasoning,
                "context_char_budget": GENERATION_CONTEXT_CHARS,
                "max_parent_ids": MAX_ANSWER_PARENT_IDS,
                "transmitted_data": [
                    "query_text",
                    "retrieved_parent_text",
                    "document_id",
                    "page_range",
                    "section_path",
                ],
            },
            "modes": modes,
            "filters": filters,
            "limits": {
                "query_chars": MAX_QUERY_CHARS,
                "top_k_max": MAX_TOP_K,
                "candidate_k_max": MAX_CANDIDATE_K,
                "answer_parent_ids_max": MAX_ANSWER_PARENT_IDS,
            },
        }
        return status, source_paths

    def status(self) -> dict[str, Any]:
        return self._status

    def source_path(self, document_id: str | None) -> Path:
        if not document_id or document_id not in self._source_paths:
            raise APIError(HTTPStatus.NOT_FOUND, "source_not_found", "source PDF was not found")
        return self._source_paths[document_id]

    def _validate_search(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "JSON body must be an object")
        allowed = {"query", "mode", "top_k", "candidate_k", "issuer", "card_name", "alpha"}
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                f"unexpected fields: {', '.join(unexpected)}",
            )

        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "query must be a non-empty string")
        query = query.strip()
        if len(query) > MAX_QUERY_CHARS:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                f"query must be at most {MAX_QUERY_CHARS} characters",
            )

        mode = payload.get("mode", "keyword")
        if not isinstance(mode, str) or mode not in ALL_MODES:
            raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "unsupported search mode")
        if mode in EXTERNAL_MODES and not self.enable_external_models:
            raise APIError(
                HTTPStatus.FORBIDDEN,
                "external_models_disabled",
                "this mode sends the query text to OpenAI; restart with --enable-external-models to allow it",
            )

        top_k = _integer(payload.get("top_k", 5), "top_k", 1, MAX_TOP_K)
        candidate_k = _integer(payload.get("candidate_k", 50), "candidate_k", 1, MAX_CANDIDATE_K)
        if candidate_k < top_k:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                "candidate_k must be greater than or equal to top_k",
            )
        alpha = payload.get("alpha", 0.5)
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 <= float(alpha) <= 1:
            raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "alpha must be from 0 to 1")
        return {
            "query": query,
            "mode": mode,
            "top_k": top_k,
            "candidate_k": candidate_k,
            "issuer": _optional_text(payload.get("issuer"), "issuer", 100),
            "card_name": _optional_text(payload.get("card_name"), "card_name", 300),
            "alpha": float(alpha),
        }

    def search(self, payload: Any) -> dict[str, Any]:
        request = self._validate_search(payload)
        raw, metrics = self._retrieve(request)
        results = self._format_results(raw, request["top_k"])
        return {
            "ok": True,
            "query": request["query"],
            "mode": request["mode"],
            "top_k": request["top_k"],
            "candidate_k": request["candidate_k"],
            "alpha": request["alpha"],
            **metrics,
            "results": results,
        }

    def _retrieve(self, request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.perf_counter()
        vector: list[float] | None = None
        embedding_latency_ms: float | None = None
        embedding_tokens = 0
        embedding_used = request["mode"] in EXTERNAL_MODES
        if embedding_used:
            embedding_started = time.perf_counter()
            try:
                vectors, usage = self.client.embeddings([request["query"]], model=self.embedding_model)  # type: ignore[union-attr]
            except Exception as error:
                raise APIError(
                    HTTPStatus.BAD_GATEWAY,
                    "external_embedding_failed",
                    "OpenAI query embedding failed; inspect the server log for details",
                ) from error
            if len(vectors) != 1 or not vectors[0]:
                raise APIError(
                    HTTPStatus.BAD_GATEWAY,
                    "external_embedding_failed",
                    "OpenAI query embedding returned no vector",
                )
            vector = vectors[0]
            embedding_latency_ms = round((time.perf_counter() - embedding_started) * 1000, 3)
            embedding_tokens = int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)

        index = HybridIndex(self.index_path)
        try:
            raw = index.search(
                request["query"],
                mode=request["mode"],
                top_k=request["candidate_k"],
                candidate_k=request["candidate_k"],
                query_vector=vector,
                issuer=request["issuer"],
                card_name=request["card_name"],
                alpha=request["alpha"],
            )
        finally:
            index.close()

        return raw, {
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "index_latency_ms": raw["latency_ms"],
            "embedding_used": embedding_used,
            "embedding_latency_ms": embedding_latency_ms,
            "embedding_tokens": embedding_tokens,
        }

    def _format_results(self, raw: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
        children = {child["chunk_id"]: child for child in raw.get("child_hits", [])}
        results = []
        for parent in raw["parents"][:top_k]:
            supporting_ids = parent.get("supporting_children", [])
            child = next(
                (children[chunk_id] for chunk_id in supporting_ids if chunk_id in children),
                None,
            )
            page_start = int(parent["page_start"])
            page_end = int(parent["page_end"])
            pages = f"p.{page_start}" if page_start == page_end else f"pp.{page_start}-{page_end}"
            source_url = None
            if parent["document_id"] in self._source_paths:
                query_string = urllib.parse.urlencode({"document_id": parent["document_id"]})
                source_url = f"/api/source?{query_string}#page={page_start}"
            results.append(
                {
                    "rank": int(parent["rank"]),
                    "score": float(parent["score"]) if parent.get("score") is not None else None,
                    "keyword_score": child.get("keyword_score") if child else None,
                    "vector_score": child.get("vector_score") if child else None,
                    "document_id": parent["document_id"],
                    "issuer": parent["issuer"],
                    "card_name": parent["card_name"],
                    "page_start": page_start,
                    "page_end": page_end,
                    "section_path": parent.get("section_path", []),
                    "child": (
                        {
                            "chunk_id": child["chunk_id"],
                            "text": child["text"],
                            "page_start": int(child["page_start"]),
                            "page_end": int(child["page_end"]),
                        }
                        if child
                        else None
                    ),
                    "parent": {
                        "chunk_id": parent["chunk_id"],
                        "text": parent["text"],
                        "supporting_children": supporting_ids,
                    },
                    "citation": f"{parent['issuer']} / {parent['card_name']}, {pages}",
                    "source_path": parent.get("source_path"),
                    "source_url": source_url,
                }
            )

        return results

    def _validate_answer(self, payload: Any) -> tuple[dict[str, Any], list[str] | None]:
        if not self.enable_generation:
            raise APIError(
                HTTPStatus.FORBIDDEN,
                "generation_disabled",
                "answer generation sends the question and retrieved parent text to OpenAI; "
                "restart with --enable-generation to allow it",
            )
        if not isinstance(payload, dict):
            raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "JSON body must be an object")
        search_payload = {key: value for key, value in payload.items() if key != "parent_ids"}
        request = self._validate_search(search_payload)
        value = payload.get("parent_ids")
        if value is None:
            return request, None
        if not isinstance(value, list) or not value or len(value) > MAX_ANSWER_PARENT_IDS:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                f"parent_ids must be a non-empty array of at most {MAX_ANSWER_PARENT_IDS} IDs",
            )
        parent_ids = []
        for parent_id in value:
            if (
                not isinstance(parent_id, str)
                or not parent_id.strip()
                or len(parent_id.strip()) > MAX_PARENT_ID_CHARS
                or parent_id.strip() in parent_ids
            ):
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "parent_ids must contain unique valid IDs")
            parent_ids.append(parent_id.strip())
        if len(parent_ids) > request["top_k"]:
            raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "parent_ids cannot exceed top_k")
        return request, parent_ids

    def _load_parents(self, parent_ids: list[str]) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        placeholders = ",".join("?" for _ in parent_ids)
        index = HybridIndex(self.index_path)
        try:
            rows = index.connection.execute(
                f"SELECT parent_id, metadata FROM parents WHERE parent_id IN ({placeholders})",
                parent_ids,
            ).fetchall()
        finally:
            index.close()
        by_id = {str(row["parent_id"]): json.loads(row["metadata"]) for row in rows}
        missing = [parent_id for parent_id in parent_ids if parent_id not in by_id]
        if missing:
            raise APIError(
                HTTPStatus.CONFLICT,
                "stale_parent_ids",
                "selected search results are no longer present; run the search again",
            )
        parents = [
            {
                "rank": rank,
                "score": None,
                "best_child_rank": rank,
                "supporting_children": [],
                **by_id[parent_id],
            }
            for rank, parent_id in enumerate(parent_ids, start=1)
        ]
        return parents, round((time.perf_counter() - started) * 1000, 3)

    def _citation(self, citation: dict[str, Any], parents: list[dict[str, Any]]) -> dict[str, Any]:
        parent = next((row for row in parents if row["chunk_id"] == citation.get("parent_id")), None)
        if parent is None or parent.get("document_id") != citation.get("document_id"):
            raise ValueError("generation citation does not match retrieved context")
        page_start = int(citation["page_start"])
        page_end = int(citation["page_end"])
        pages = f"p.{page_start}" if page_start == page_end else f"pp.{page_start}-{page_end}"
        source_url = None
        if citation["document_id"] in self._source_paths:
            query_string = urllib.parse.urlencode({"document_id": citation["document_id"]})
            source_url = f"/api/source?{query_string}#page={page_start}"
        return {
            **citation,
            "issuer": parent["issuer"],
            "card_name": parent["card_name"],
            "page": pages,
            "label": f"{parent['issuer']} / {parent['card_name']}, {pages}",
            "source_url": source_url,
            "quote": str(parent.get("text", ""))[:1200],
        }

    def answer(self, payload: Any) -> dict[str, Any]:
        request, parent_ids = self._validate_answer(payload)
        started = time.perf_counter()
        if parent_ids is not None:
            parents, retrieval_latency_ms = self._load_parents(parent_ids)
            raw = {"parents": parents, "child_hits": [], "latency_ms": retrieval_latency_ms}
            retrieval_metrics = {
                "latency_ms": retrieval_latency_ms,
                "index_latency_ms": retrieval_latency_ms,
                "embedding_used": False,
                "embedding_latency_ms": None,
                "embedding_tokens": 0,
            }
        else:
            raw, retrieval_metrics = self._retrieve(request)
            parents = raw["parents"][: request["top_k"]]

        generation_started = time.perf_counter()
        try:
            generated = answer_question(
                self.client,  # type: ignore[arg-type]
                request["query"],
                parents,
                model=self.generation_model,
                reasoning=self.generation_reasoning,
            )
            citations = [self._citation(citation, parents) for citation in generated["citations"]]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise APIError(
                HTTPStatus.BAD_GATEWAY,
                "generation_validation_failed",
                "Luna returned an invalid grounded answer; no answer was returned",
            ) from error
        except Exception as error:
            raise APIError(
                HTTPStatus.BAD_GATEWAY,
                "external_generation_failed",
                "OpenAI Luna answer generation failed; inspect the server log for details",
            ) from error
        generation_latency_ms = round((time.perf_counter() - generation_started) * 1000, 3)
        total_latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return {
            "ok": True,
            "query": request["query"],
            "answer": generated["answer"],
            "citations": citations,
            "insufficient_evidence": generated["insufficient_evidence"],
            "usage": generated["usage"],
            "model": self.generation_model,
            "reasoning": self.generation_reasoning,
            "latency_ms": total_latency_ms,
            "retrieval_latency_ms": retrieval_metrics["latency_ms"],
            "generation_latency_ms": generation_latency_ms,
            "index_latency_ms": retrieval_metrics["index_latency_ms"],
            "embedding_used": retrieval_metrics["embedding_used"],
            "embedding_latency_ms": retrieval_metrics["embedding_latency_ms"],
            "embedding_tokens": retrieval_metrics["embedding_tokens"],
            "external_transmission": {
                "used": bool(parents),
                "provider": "OpenAI",
                "data": self._status["generation"]["transmitted_data"],
            },
            "retrieval": {
                "mode": request["mode"],
                "top_k": request["top_k"],
                "candidate_k": request["candidate_k"],
                "selected_parent_ids": parent_ids is not None,
                "reexecuted": parent_ids is None,
                "requested_parent_count": len(parent_ids) if parent_ids is not None else request["top_k"],
                "retrieved_parent_count": len(parents),
                "results": self._format_results(raw, request["top_k"]),
            },
        }


class SearchHTTPServer(HTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        service: SearchService,
        html_path: Path,
    ):
        self.search_service = service
        self.html_path = html_path
        host, port = server_address
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            self.allowed_hosts.update({"127.0.0.1", "localhost"})
        super().__init__(server_address, SearchRequestHandler)


class SearchRequestHandler(BaseHTTPRequestHandler):
    server: SearchHTTPServer

    def _require_local_host(self) -> None:
        host = self.headers.get("Host", "").strip().casefold()
        if host not in self.server.allowed_hosts:
            raise APIError(
                HTTPStatus.MISDIRECTED_REQUEST,
                "invalid_host",
                "Host must be localhost on the configured server port",
            )

    def _send_headers(self, status: int, content_type: str, length: int, *, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _error(self, error: APIError) -> None:
        self._json(
            error.status,
            {"ok": False, "error": {"code": error.code, "message": error.message}},
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        try:
            self._require_local_host()
            if parsed.path in {"/", "/rag_search_tester.html"}:
                self._serve_html(self.server.html_path)
                return
            if parsed.path == "/rag_pipeline_dashboard.html":
                self._serve_html(self.server.html_path.with_name("rag_pipeline_dashboard.html"))
                return
            if parsed.path == "/api/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "status": "ready",
                        "external_models_enabled": self.server.search_service.enable_external_models,
                        "generation_enabled": self.server.search_service.enable_generation,
                    },
                )
                return
            if parsed.path == "/api/status":
                self._json(HTTPStatus.OK, self.server.search_service.status())
                return
            if parsed.path == "/api/source":
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                values = query.get("document_id", [])
                if len(values) != 1:
                    raise APIError(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_request",
                        "exactly one document_id is required",
                    )
                self._serve_pdf(self.server.search_service.source_path(values[0]))
                return
            raise APIError(HTTPStatus.NOT_FOUND, "not_found", "endpoint was not found")
        except APIError as error:
            self._error(error)

    def do_POST(self) -> None:  # noqa: N802
        operation = "request"
        try:
            self._require_local_host()
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path not in {"/api/search", "/api/answer"}:
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "endpoint was not found")
            operation = "answer" if parsed.path == "/api/answer" else "search"
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            if content_type != "application/json":
                raise APIError(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "unsupported_media_type",
                    "Content-Type must be application/json",
                )
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise APIError(HTTPStatus.LENGTH_REQUIRED, "length_required", "Content-Length is required")
            try:
                length = int(raw_length)
            except ValueError as error:
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "invalid Content-Length") from error
            if length < 1 or length > MAX_BODY_BYTES:
                raise APIError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "request_too_large",
                    f"JSON body must be from 1 to {MAX_BODY_BYTES} bytes",
                )
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_json", "request body is not valid JSON") from error
            response = (
                self.server.search_service.answer(payload)
                if operation == "answer"
                else self.server.search_service.search(payload)
            )
            self._json(HTTPStatus.OK, response)
        except APIError as error:
            if error.__cause__ is not None:
                self.log_error("%s: %s", error.code, error.__cause__)
            self._error(error)
        except Exception as error:
            self.log_error("unexpected %s error: %s", operation, error)
            self._error(APIError(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", f"{operation} failed"))

    def _serve_html(self, path: Path) -> None:
        if not path.is_file():
            raise APIError(
                HTTPStatus.NOT_FOUND,
                "ui_not_found",
                f"search UI was not found at {path}",
            )
        body = path.read_bytes()
        self._send_headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
        self.wfile.write(body)

    def _serve_pdf(self, path: Path) -> None:
        size = path.stat().st_size
        self._send_headers(HTTPStatus.OK, "application/pdf", size, cache="private, max-age=300")
        with path.open("rb") as source:
            for block in iter(lambda: source.read(64 * 1024), b""):
                self.wfile.write(block)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}", file=sys.stderr)


def build_service(
    *,
    index_path: Path,
    parents_path: Path,
    children_path: Path,
    chunk_summary_path: Path,
    enable_external_models: bool,
    enable_generation: bool,
    embedding_model: str,
) -> SearchService:
    validation = validate_chunk_corpus(parents_path, children_path, False, chunk_summary_path)
    client = OpenAIClient() if enable_external_models or enable_generation else None
    return SearchService(
        index_path,
        validation["index_metadata"],
        enable_external_models=enable_external_models,
        enable_generation=enable_generation,
        embedding_model=embedding_model,
        client=client,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the local natural-language RAG search tester.")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--index", type=Path, default=INDEX_PATH)
    parser.add_argument("--parents", type=Path, default=PARENTS_PATH)
    parser.add_argument("--children", type=Path, default=CHILDREN_PATH)
    parser.add_argument("--chunk-summary", type=Path, default=CHUNK_SUMMARY_PATH)
    parser.add_argument("--html", type=Path, default=HTML_PATH)
    parser.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    parser.add_argument(
        "--enable-external-models",
        action="store_true",
        help="Enable vector/hybrid/weighted modes; each query text is sent to the OpenAI Embeddings API.",
    )
    parser.add_argument(
        "--enable-generation",
        action="store_true",
        help=(
            "Enable grounded GPT-5.6 Luna answers; each request sends the question and retrieved "
            "parent text/metadata to the OpenAI Responses API."
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be from 1 to 65535")
    if args.enable_external_models:
        print(
            "WARNING: vector, hybrid, and weighted searches send each query text to the OpenAI Embeddings API.",
            file=sys.stderr,
        )
    if args.enable_generation:
        print(
            "WARNING: answer generation sends the question and retrieved parent text/metadata "
            "to the OpenAI Responses API (gpt-5.6-luna).",
            file=sys.stderr,
        )
    try:
        service = build_service(
            index_path=args.index,
            parents_path=args.parents,
            children_path=args.children,
            chunk_summary_path=args.chunk_summary,
            enable_external_models=args.enable_external_models,
            enable_generation=args.enable_generation,
            embedding_model=args.embedding_model,
        )
        server = SearchHTTPServer((args.host, args.port), service, args.html)
    except (OSError, ValueError) as error:
        raise SystemExit(f"Search UI failed to start: {error}") from error
    url = f"http://{args.host}:{args.port}/"
    print(f"RAG search UI: {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping RAG search UI.", file=sys.stderr)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
