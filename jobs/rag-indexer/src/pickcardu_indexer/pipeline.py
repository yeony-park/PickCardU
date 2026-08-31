from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

import numpy as np

from .state import StateStore, canonical_json
from .ocr import OcrProviderError, pages_text


RELATION_FIELDS = ("target", "condition", "value", "unit", "cap", "frequency", "period", "exceptions")
NUMBER = re.compile(r"\d+(?:[,.]\d+)?")
RISKY_IGNORED_LINE = re.compile(r"할인|적립|캐시백|마일|포인트|연회비|실적|한도|제외|무료|면제|혜택|이용금액|건당|\d+(?:[,.]\d+)?\s*(?:%|원|회|개월|만원)")
CHUNKING_PROFILES = {"card_page_section_benefit", "parent_child_bundle"}
DEFAULT_CHUNKING_PROFILE = "card_page_section_benefit"


class LaneRestructureRequired(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_immutable(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != value:
            raise RuntimeError(f"immutable artifact changed: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def file_fingerprint(path: Path) -> str:
    try:
        return sha256_file(path)
    except OSError as error:
        return f"unreadable:{type(error).__name__}"


def tree_hash(root: Path) -> str:
    rows = [{"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)} for path in sorted(root.rglob("*")) if path.is_file()]
    return sha256_bytes(canonical_json(rows).encode())


def embedding_sha256(chunk_ids: list[str], embeddings: np.ndarray) -> str:
    array = np.asarray(embeddings, dtype=np.float32)
    if array.ndim != 2 or array.shape[0] != len(chunk_ids) or not np.isfinite(array).all():
        raise ValueError("embedding identity shape or finiteness mismatch")
    digest = hashlib.sha256(canonical_json(chunk_ids).encode())
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def input_fingerprint(source_manifest: Path, documents: list[dict[str, str]], luna_dir: Path | None, upstage_dir: Path | None) -> str:
    """Hashes every local input lane independently, so changed input invalidates resume."""
    lanes: dict[str, dict[str, str] | None] = {}
    for provider, root in (("luna", luna_dir), ("upstage", upstage_dir)):
        lanes[provider] = None if root is None else {
            document["document_id"]: sha256_file(lane_path(root, document["document_id"])) if lane_path(root, document["document_id"]).is_file() else "missing"
            for document in documents
        }
    return sha256_bytes(canonical_json({
        "manifest": sha256_file(source_manifest),
        "sources": {document["document_id"]: file_fingerprint(Path(document["source_pdf"])) for document in documents},
        "lanes": lanes,
    }).encode())


def normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def relation_tuple(fact: dict[str, str]) -> tuple[str, ...]:
    return tuple(fact[field] for field in RELATION_FIELDS)


def normalise_fact(raw: dict[str, Any]) -> dict[str, str]:
    fact = {field: normalized(raw.get(field, "")) for field in RELATION_FIELDS}
    required = ("target", "value", "unit")
    missing = [field for field in required if not fact[field]]
    if missing:
        raise ValueError(f"required fact fields missing: {','.join(missing)}")
    if fact["value"] in {"-", "—"}:
        raise ValueError("blank or dash value is a rule failure")
    return fact


def numbers(value: str) -> set[str]:
    return {token.replace(",", "") for token in NUMBER.findall(value)}


def evidence_pages(value: Any) -> list[int]:
    pages: set[int] = set()
    if isinstance(value, dict):
        page = value.get("page")
        if isinstance(page, int) and not isinstance(page, bool) and page >= 1:
            pages.add(page)
        for nested in value.values():
            pages.update(evidence_pages(nested))
    elif isinstance(value, list):
        for nested in value:
            pages.update(evidence_pages(nested))
    return sorted(pages)


def validate_fact_evidence(fact: dict[str, str], quote: str, context: str) -> None:
    for field in ("target", "condition", "unit", "cap", "frequency", "period", "exceptions"):
        if fact[field] and fact[field] not in quote:
            raise ValueError(f"{context} {field} is not linked to its evidence quote")
    value_numbers = numbers(fact["value"])
    if value_numbers and not value_numbers <= numbers(quote):
        raise ValueError(f"{context} has a value not linked to its evidence quote")
    if not value_numbers and fact["value"] not in quote:
        raise ValueError(f"{context} has a non-numeric value not linked to its evidence quote")
    if fact["value"] == "0" and "0" not in numbers(quote):
        raise ValueError(f"{context} zero is not explicit in its evidence quote")


def compare_ocr_outputs(luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> dict[str, Any]:
    """Page-aligned OCR comparison audit; relation validation remains the correctness gate."""
    lanes: dict[str, dict[int, str]] = {}
    for provider, payload in (("luna", luna_payload), ("upstage", upstage_payload)):
        pages: dict[int, str] = {}
        for row in payload.get("pages", []):
            page = row.get("page", row.get("number")) if isinstance(row, dict) else None
            text = row.get("text") if isinstance(row, dict) else None
            if isinstance(page, bool) or not isinstance(page, int) or not isinstance(text, str):
                raise ValueError(f"{provider} page format invalid")
            if page in pages:
                raise ValueError(f"{provider} page number is duplicated")
            pages[page] = normalized(text)
        lanes[provider] = pages
    page_numbers = sorted(set(lanes["luna"]) | set(lanes["upstage"]))
    rows = []
    for page in page_numbers:
        luna_text, upstage_text = lanes["luna"].get(page, ""), lanes["upstage"].get(page, "")
        luna_tokens, upstage_tokens = set(luna_text.split()), set(upstage_text.split())
        union = luna_tokens | upstage_tokens
        rows.append({
            "page": page,
            "luna_present": page in lanes["luna"],
            "upstage_present": page in lanes["upstage"],
            "normalized_text_equal": luna_text == upstage_text,
            "token_jaccard": 1.0 if not union else len(luna_tokens & upstage_tokens) / len(union),
            "luna_numbers": sorted(numbers(luna_text)),
            "upstage_numbers": sorted(numbers(upstage_text)),
        })
    return {
        "purpose": "diagnostic_only_not_a_correctness_or_selection_gate",
        "page_count_equal": set(lanes["luna"]) == set(lanes["upstage"]),
        "all_normalized_text_equal": all(row["normalized_text_equal"] for row in rows),
        "pages": rows,
    }


def lane_path(root: Path, document_id: str) -> Path:
    return root / f"{document_id.replace('/', '__')}.json"


class ProviderAdapter(Protocol):
    provider: str

    def load(self, document_id: str) -> tuple[Path, dict[str, Any]]: ...


class EmbeddingAdapter(Protocol):
    model: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> tuple[np.ndarray, dict[str, Any]]: ...


class OpenAIEmbeddingAdapter:
    """Explicitly constructed document-embedding boundary; construction performs no I/O."""

    model = "text-embedding-3-small"
    dimension = 1536

    def __init__(
        self,
        *,
        api_key: str | None,
        batch_size: int = 64,
        client: Any = None,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("embedding batch size must be positive")
        self.api_key = api_key
        self.batch_size = batch_size
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for approved document embedding")
        from openai import OpenAI

        self._client = OpenAI(api_key=self.api_key, max_retries=0)
        return self._client

    def embed_documents(self, texts: list[str]) -> tuple[np.ndarray, dict[str, Any]]:
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("document embedding requires non-empty texts")
        vectors: list[list[float]] = []
        provider_usage: list[dict[str, Any]] = []
        client = self._get_client()
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = client.embeddings.create(
                input=batch,
                model=self.model,
                dimensions=self.dimension,
                encoding_format="float",
                timeout=60.0,
            )
            data = sorted(response.data, key=lambda row: row.index)
            if [row.index for row in data] != list(range(len(batch))):
                raise ValueError("embedding response indices do not match the request batch")
            vectors.extend(row.embedding for row in data)
            usage = getattr(response, "usage", None)
            if hasattr(usage, "model_dump"):
                provider_usage.append(usage.model_dump())
            elif isinstance(usage, dict):
                provider_usage.append(dict(usage))
            else:
                provider_usage.append({})
        array = np.asarray(vectors, dtype=np.float32)
        if array.shape != (len(texts), self.dimension) or not np.isfinite(array).all():
            raise ValueError("embedding response shape or finiteness mismatch")
        return array, {
            "provider_called": True,
            "model": self.model,
            "dimension": self.dimension,
            "item_count": len(texts),
            "request_count": len(provider_usage),
            "batch_size": self.batch_size,
            "provider_usage": provider_usage,
        }


class LocalJsonAdapter:
    """Injection boundary for a provider lane; it never reads another lane."""

    def __init__(self, provider: str, root: Path) -> None:
        self.provider, self.root = provider, root

    def load(self, document_id: str) -> tuple[Path, dict[str, Any]]:
        path = lane_path(self.root, document_id)
        if not path.is_file():
            raise FileNotFoundError(f"{self.provider} artifact missing: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("document_id") != document_id:
            raise ValueError(f"{self.provider} artifact document_id mismatch")
        if not isinstance(payload.get("pages"), list) or not isinstance(payload.get("facts"), list):
            raise ValueError(f"{self.provider} artifact requires pages and facts arrays")
        return path, payload


def load_lane(provider: str, root: Path, document_id: str) -> tuple[Path, dict[str, Any]]:
    return LocalJsonAdapter(provider, root).load(document_id)


def validate_lane(provider: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("provider") != provider or not isinstance(payload.get("source_pdf_sha256"), str):
        raise ValueError(f"{provider} provenance is invalid")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or not all(isinstance(provenance.get(key), str) and provenance[key] for key in ("endpoint", "model", "config_hash")):
        raise ValueError(f"{provider} endpoint/model/config provenance is required")
    pages: dict[int, str] = {}
    for page in payload["pages"]:
        page_number = page.get("page", page.get("number"))
        text = page.get("text")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1 or not isinstance(text, str):
            raise ValueError(f"{provider} page format invalid")
        if page_number in pages:
            raise ValueError(f"{provider} page number is duplicated")
        pages[page_number] = text
    if not pages:
        raise ValueError(f"{provider} has no pages")
    identity = payload.get("identity")
    if not isinstance(identity, dict) or not all(isinstance(identity.get(key), str) and normalized(identity[key]) for key in ("issuer_name", "card_name")):
        raise ValueError(f"{provider} identity is required")
    for key, label in (("issuer", "issuer_name"), ("card", "card_name")):
        evidence = identity.get(f"{key}_evidence")
        if not isinstance(evidence, dict):
            raise ValueError(f"{provider} {key} identity evidence is required")
        page, quote = evidence.get("page"), normalized(evidence.get("quote", ""))
        if page not in pages or not quote or quote not in normalized(pages[page]) or normalized(identity[label]) not in quote:
            raise ValueError(f"{provider} {key} identity is not grounded")
    validated: list[dict[str, Any]] = []
    for ordinal, raw_fact in enumerate(payload["facts"]):
        if not isinstance(raw_fact, dict):
            raise ValueError(f"{provider} fact {ordinal} is not an object")
        fact = normalise_fact(raw_fact)
        evidence = raw_fact.get("evidence")
        if not isinstance(evidence, dict):
            raise ValueError(f"{provider} fact {ordinal} has no evidence")
        page = evidence.get("page")
        quote = normalized(evidence.get("quote", evidence.get("text", "")))
        if page not in pages or not quote or quote not in normalized(pages[page]):
            raise ValueError(f"{provider} fact {ordinal} evidence is not grounded in its own OCR text")
        validate_fact_evidence(fact, quote, f"{provider} fact {ordinal}")
        validated.append({"fact": fact, "evidence": {"provider": provider, "page": page, "quote": quote}})
    covered = {item["evidence"]["quote"] for item in validated}
    identity_quotes = {normalized(identity[f"{key}_evidence"]["quote"]) for key in ("issuer", "card")}
    dispositions = payload.get("span_dispositions")
    if not isinstance(dispositions, list):
        raise ValueError(f"{provider} span_dispositions is required")
    lines = {(page, normalized(line)) for page, text in pages.items() for line in text.splitlines() if normalized(line)}
    mapped: set[tuple[int, str]] = set()
    for disposition in dispositions:
        if not isinstance(disposition, dict) or disposition.get("kind") not in {"fact", "identity", "ignore"}:
            raise ValueError(f"{provider} invalid span disposition")
        item = (disposition.get("page"), normalized(disposition.get("quote", "")))
        if item not in lines:
            raise ValueError(f"{provider} disposition is not an exact page line")
        if item in mapped:
            raise ValueError(f"{provider} OCR line has duplicate dispositions")
        if disposition["kind"] == "ignore" and not normalized(disposition.get("reason", "")):
            raise ValueError(f"{provider} ignored span reason is required")
        if disposition["kind"] == "ignore" and RISKY_IGNORED_LINE.search(item[1]):
            raise LaneRestructureRequired(f"{provider} benefit-like ignored span requires a new structuring run")
        line = item[1]
        if disposition["kind"] == "fact" and not any(quote in line or line in quote for quote in covered):
            raise ValueError(f"{provider} fact disposition is not linked to a validated fact")
        if disposition["kind"] == "identity" and not any(quote in line or line in quote for quote in identity_quotes):
            raise ValueError(f"{provider} identity disposition is not linked to validated identity")
        mapped.add(item)
    if mapped != lines:
        raise ValueError(f"{provider} OCR line lacks explicit disposition")
    fact_lines = {normalized(row.get("quote", "")) for row in dispositions if row.get("kind") == "fact"}
    identity_lines = {normalized(row.get("quote", "")) for row in dispositions if row.get("kind") == "identity"}
    if any(not any(quote in line or line in quote for line in fact_lines) for quote in covered):
        raise ValueError(f"{provider} validated fact lacks a fact disposition")
    if any(not any(quote in line or line in quote for line in identity_lines) for quote in identity_quotes):
        raise ValueError(f"{provider} validated identity lacks an identity disposition")
    return validated


def canonical_from_lanes(luna: list[dict[str, Any]], upstage: list[dict[str, Any]], luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None, dict[str, Any] | None]:
    luna_by_tuple = {relation_tuple(item["fact"]): item for item in luna}
    upstage_by_tuple = {relation_tuple(item["fact"]): item for item in upstage}
    if set(luna_by_tuple) != set(upstage_by_tuple):
        return None, {"luna_only": sorted(set(luna_by_tuple) - set(upstage_by_tuple)), "upstage_only": sorted(set(upstage_by_tuple) - set(luna_by_tuple))}, None
    luna_identity, upstage_identity = luna_payload["identity"], upstage_payload["identity"]
    identity_pair = (normalized(luna_identity["issuer_name"]), normalized(luna_identity["card_name"]))
    if identity_pair != (normalized(upstage_identity["issuer_name"]), normalized(upstage_identity["card_name"])):
        return None, None, {"luna": identity_pair, "upstage": (normalized(upstage_identity["issuer_name"]), normalized(upstage_identity["card_name"]))}
    canonical = [
        {
            "fact": luna_by_tuple[key]["fact"],
            "evidence_refs": {"luna": luna_by_tuple[key]["evidence"], "upstage": upstage_by_tuple[key]["evidence"]},
        }
        for key in sorted(luna_by_tuple)
    ]
    return canonical, None, None


def _identity_evidence(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    identity = payload["identity"]
    return {
        key: {
            "provider": provider,
            "page": identity[f"{key}_evidence"]["page"],
            "quote": normalized(identity[f"{key}_evidence"]["quote"]),
        }
        for key in ("issuer", "card")
    }


def strict_resolution(value: Any, luna_payload: dict[str, Any], upstage_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("resolution"), dict) or not isinstance(value.get("canonical"), list) or not isinstance(value.get("identity"), dict):
        raise ValueError("resolution envelope requires resolution, identity, and canonical")
    resolution = value["resolution"]
    selected = resolution.get("selected_provider")
    selected_identity = resolution.get("selected_identity_provider")
    if selected not in {"luna", "upstage"} or selected_identity not in {"luna", "upstage"} or not isinstance(resolution.get("reason"), str) or not resolution["reason"].strip() or not isinstance(resolution.get("rejected_relations"), list):
        raise ValueError("resolution selected_provider, selected_identity_provider, reason, and rejected_relations are required")
    lanes = {"luna": validate_lane("luna", luna_payload), "upstage": validate_lane("upstage", upstage_payload)}
    by_tuple = {provider: {relation_tuple(item["fact"]): item for item in items} for provider, items in lanes.items()}
    canonical: list[dict[str, Any]] = []
    canonical_keys: list[tuple[str, ...]] = []
    for item in value["canonical"]:
        if not isinstance(item, dict) or not isinstance(item.get("fact"), dict) or not isinstance(item.get("evidence_refs"), dict):
            raise ValueError("canonical item requires fact and evidence_refs")
        fact = normalise_fact(item["fact"])
        key = relation_tuple(fact)
        if key not in by_tuple[selected]:
            raise ValueError("canonical fact is not an exact selected-provider relation")
        references: dict[str, Any] = {}
        for provider in ("luna", "upstage"):
            supplied = item["evidence_refs"].get(provider)
            if not isinstance(supplied, dict) or not isinstance(supplied.get("supports_selected"), bool):
                raise ValueError("each lane provenance requires supports_selected")
            candidates = by_tuple[provider]
            supported = key in candidates
            if supplied["supports_selected"] is not supported:
                raise ValueError("false supports_selected provenance")
            evidence = {k: supplied.get(k) for k in ("provider", "page", "quote")}
            expected = candidates[key] if supported else next((candidate for candidate in candidates.values() if candidate["evidence"] == evidence), None)
            if expected is None or expected["evidence"] != evidence:
                raise ValueError("lane evidence is not an exact validated lane evidence")
            references[provider] = {**evidence, "supports_selected": supported}
        canonical.append({"fact": fact, "evidence_refs": references})
        canonical_keys.append(key)
    if len(canonical_keys) != len(set(canonical_keys)) or set(canonical_keys) != set(by_tuple[selected]):
        raise ValueError("canonical relations must exactly equal the selected-provider relation set")

    other = "upstage" if selected == "luna" else "luna"
    expected_rejected = set(by_tuple[other]) - set(by_tuple[selected])
    supplied_rejected: list[tuple[str, ...]] = []
    rejected_relations: list[dict[str, Any]] = []
    for rejected in resolution["rejected_relations"]:
        if not isinstance(rejected, dict) or set(rejected) != {"provider", "tuple", "reason"}:
            raise ValueError("each rejected relation requires exactly provider, tuple, and reason")
        raw_tuple = rejected["tuple"]
        if rejected["provider"] != other or not isinstance(raw_tuple, list) or len(raw_tuple) != len(RELATION_FIELDS) or not all(isinstance(part, str) for part in raw_tuple) or not isinstance(rejected["reason"], str) or not rejected["reason"].strip():
            raise ValueError("rejected relation provider, tuple, or reason is invalid")
        key = tuple(normalized(part) for part in raw_tuple)
        supplied_rejected.append(key)
        rejected_relations.append({"provider": other, "tuple": list(key), "reason": rejected["reason"].strip()})
    if len(supplied_rejected) != len(set(supplied_rejected)) or set(supplied_rejected) != expected_rejected:
        raise ValueError("rejected relations must exactly equal the non-selected relation difference")

    identities = {
        provider: (normalized(payload["identity"]["issuer_name"]), normalized(payload["identity"]["card_name"]))
        for provider, payload in (("luna", luna_payload), ("upstage", upstage_payload))
    }
    identity = value["identity"]
    if set(identity) != {"issuer_name", "card_name", "evidence_refs"} or not isinstance(identity["evidence_refs"], dict):
        raise ValueError("canonical identity requires issuer_name, card_name, and evidence_refs")
    selected_pair = identities[selected_identity]
    if (normalized(identity["issuer_name"]), normalized(identity["card_name"])) != selected_pair:
        raise ValueError("canonical identity is not the exact selected-provider identity")
    identity_refs: dict[str, Any] = {}
    for provider in ("luna", "upstage"):
        supplied = identity["evidence_refs"].get(provider)
        expected = _identity_evidence(provider, luna_payload if provider == "luna" else upstage_payload)
        supports_selected = identities[provider] == selected_pair
        if not isinstance(supplied, dict) or set(supplied) != {"issuer", "card", "supports_selected"} or supplied.get("supports_selected") is not supports_selected:
            raise ValueError("identity provenance requires exact supports_selected")
        if supplied.get("issuer") != expected["issuer"] or supplied.get("card") != expected["card"]:
            raise ValueError("identity evidence is not exact validated lane evidence")
        identity_refs[provider] = {**expected, "supports_selected": supports_selected}
    canonical_identity = {"issuer_name": selected_pair[0], "card_name": selected_pair[1], "evidence_refs": identity_refs}
    canonical = sorted(canonical, key=lambda item: relation_tuple(item["fact"]))
    audit = {
        "resolution": {"selected_provider": selected, "selected_identity_provider": selected_identity, "reason": resolution["reason"].strip(), "rejected_relations": rejected_relations},
        "identity": canonical_identity,
        "canonical": canonical,
    }
    return canonical, canonical_identity, audit


def read_source_manifest(path: Path) -> list[dict[str, str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    documents = value.get("documents") if isinstance(value, dict) else value
    if not isinstance(documents, list) or not documents:
        raise ValueError("source manifest must be a non-empty document list or object.documents")
    result = []
    for row in documents:
        if not isinstance(row, dict) or not isinstance(row.get("document_id"), str) or not isinstance(row.get("source_pdf"), str):
            raise ValueError("source manifest document requires document_id and source_pdf")
        source = Path(row["source_pdf"])
        if not source.is_absolute():
            source = (path.parent / source).resolve()
        if not source.is_file() or source.suffix.lower() != ".pdf":
            raise ValueError(f"source PDF unavailable: {source}")
        result.append({"document_id": row["document_id"], "source_pdf": str(source)})
    if len({row["document_id"] for row in result}) != len(result):
        raise ValueError("source manifest document IDs must be unique")
    return sorted(result, key=lambda row: row["document_id"])


class Indexer:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.state = StateStore(runtime_root / "indexer-state.sqlite")

    def close(self) -> None:
        self.state.close()

    def run(
        self,
        source_manifest: Path,
        luna_dir: Path | None,
        upstage_dir: Path | None,
        *,
        fake_vectors: bool,
        allow_partial: bool,
        config: dict[str, Any],
        providers: dict[str, ProviderAdapter] | None = None,
        embedding_adapter: EmbeddingAdapter | None = None,
    ) -> dict[str, Any]:
        prepared = self.ocr(
            source_manifest,
            luna_dir,
            upstage_dir,
            config=config,
            providers=providers,
        )
        release_id = None
        if fake_vectors or embedding_adapter is not None:
            indexed = self.index(
                prepared["run_id"],
                allow_preview=allow_partial,
                fake_vectors=fake_vectors,
                profile=str(config.get("profile", config.get("strategy", DEFAULT_CHUNKING_PROFILE))),
                embedding_adapter=embedding_adapter,
            )
            release_id = indexed["release_id"]
        return {"run_id": prepared["run_id"], "release_id": release_id, "status": self.state.status(prepared["run_id"])}

    def ocr(
        self,
        source_manifest: Path,
        luna_dir: Path | None,
        upstage_dir: Path | None,
        *,
        config: dict[str, Any],
        providers: dict[str, ProviderAdapter] | Callable[[str, list[dict[str, str]]], dict[str, ProviderAdapter]] | None = None,
    ) -> dict[str, Any]:
        documents = read_source_manifest(source_manifest)
        input_hash = input_fingerprint(source_manifest, documents, luna_dir, upstage_dir)
        config_hash = sha256_bytes(canonical_json(config).encode())
        run_id = "run_" + sha256_bytes(f"{input_hash}:{config_hash}".encode())[:16]
        run_id = self.state.find_or_create_run(run_id, input_hash, config_hash, now())
        active_providers = providers(run_id, documents) if callable(providers) else providers
        for document in documents:
            self._process_document(run_id, document, luna_dir, upstage_dir, active_providers)
        document_statuses = {str(row["status"]) for row in self.state.documents(run_id)}
        if "review" in document_statuses:
            self.state.set_run_status(run_id, "review", now())
        elif "blocked" in document_statuses:
            self.state.set_run_status(run_id, "blocked", now())
        elif document_statuses != {"canonical_approved"}:
            self.state.set_run_status(run_id, "failed", now())
        elif document_statuses == {"canonical_approved"}:
            self.state.set_run_status(run_id, "canonical_approved", now())
        status = self.state.status(run_id)
        differences = []
        for stage in status["stages"]:
            if stage["stage"] != "ocr_comparison":
                continue
            detail = json.loads(stage["detail_json"])
            if not detail.get("page_count_equal") or not detail.get("all_normalized_text_equal"):
                differences.append(stage["document_id"])
        return {"run_id": run_id, "ocr_difference_documents": differences, "status": status}

    def index(
        self,
        run_id: str,
        *,
        allow_preview: bool,
        fake_vectors: bool,
        profile: str = DEFAULT_CHUNKING_PROFILE,
        embedding_adapter: EmbeddingAdapter | None = None,
    ) -> dict[str, Any]:
        release_id = self.publish(
            run_id,
            allow_partial=allow_preview,
            fake_vectors=fake_vectors,
            profile=profile,
            embedding_adapter=embedding_adapter,
        )
        manifest = json.loads((Path(str(self.state.release(release_id)["path"])) / "manifest.json").read_text(encoding="utf-8"))
        self.state.set_run_status(run_id, f"{manifest['release_status']}_published", now())
        return {"run_id": run_id, "release_id": release_id, "status": self.state.status(run_id)}

    def _document_root(self, run_id: str, document_id: str) -> Path:
        return self.runtime_root / "working" / run_id / "documents" / document_id.replace("/", "__")

    def _record_json_artifact(self, run_id: str, document_id: str, kind: str, provider: str | None, path: Path, value: Any) -> Path:
        encoded = (canonical_json(value) + "\n").encode()
        write_immutable(path, encoded)
        self.state.record_artifact(run_id, document_id, kind, provider, str(path), sha256_bytes(encoded), {})
        return path

    def _materialize_lane(self, run_id: str, document_id: str, provider: str, payload: dict[str, Any], adapter: ProviderAdapter) -> None:
        root = self._document_root(run_id, document_id) / provider
        pages = [{"page": row.get("page", row.get("number")), "text": row.get("text", "")} for row in payload.get("pages", [])]
        self._record_json_artifact(run_id, document_id, "ocr_pages", provider, root / "pages.json", {"document_id": document_id, "provider": provider, "pages": pages})
        text = pages_text(pages).encode("utf-8")
        write_immutable(root / "ocr.txt", text)
        self.state.record_artifact(run_id, document_id, "ocr_text", provider, str(root / "ocr.txt"), sha256_bytes(text), {})
        self._record_json_artifact(run_id, document_id, "normalized_json", provider, root / "normalized.json", payload)
        artifact_paths = getattr(adapter, "artifact_paths", None)
        if callable(artifact_paths):
            for kind, path in artifact_paths(document_id).items():
                if path.is_file():
                    self.state.record_artifact(run_id, document_id, kind, provider, str(path), sha256_file(path), {})

    def _validation_artifact(self, run_id: str, document_id: str, name: str, value: Any) -> Path:
        return self._record_json_artifact(
            run_id,
            document_id,
            name,
            None,
            self._document_root(run_id, document_id) / "validation" / f"{name}.json",
            value,
        )

    def _process_document(self, run_id: str, document: dict[str, str], luna_dir: Path | None, upstage_dir: Path | None, providers: dict[str, ProviderAdapter] | None) -> None:
        document_id, source_path = document["document_id"], Path(document["source_pdf"])
        try:
            source_hash = sha256_file(source_path)
        except OSError as error:
            source_hash = f"unreadable:{type(error).__name__}"
            self.state.upsert_document(run_id, document_id, str(source_path), source_hash, "blocked")
            self.state.record_stage(run_id, document_id, "source", source_hash, "blocked", {"error": type(error).__name__}, now(), retryable=False)
            return
        try:
            existing = self.state.document(run_id, document_id)
            if existing["source_hash"] == source_hash and existing["status"] == "canonical_approved":
                return
        except KeyError:
            pass
        self.state.upsert_document(run_id, document_id, str(source_path), source_hash, "running")
        self.state.record_stage(run_id, document_id, "source", source_hash, "completed", {"source_sha256": source_hash}, now())
        self._record_json_artifact(run_id, document_id, "source", None, self._document_root(run_id, document_id) / "source.json", {"document_id": document_id, "source_pdf": str(source_path), "source_pdf_sha256": source_hash})
        if providers is None and (luna_dir is None or upstage_dir is None):
            self.state.set_document_status(run_id, document_id, "blocked")
            self.state.record_stage(run_id, document_id, "ocr", source_hash, "blocked", {"reason": "local dual OCR artifacts required; live adapters fail closed"}, now())
            return
        try:
            adapters = providers or {"luna": LocalJsonAdapter("luna", luna_dir), "upstage": LocalJsonAdapter("upstage", upstage_dir)}
            if set(adapters) != {"luna", "upstage"} or adapters["luna"].provider != "luna" or adapters["upstage"].provider != "upstage":
                raise ValueError("exactly independent luna and upstage adapters are required")
            luna_path, luna_payload = adapters["luna"].load(document_id)
            upstage_path, upstage_payload = adapters["upstage"].load(document_id)
            if luna_path.resolve() == upstage_path.resolve() or sha256_file(luna_path) == sha256_file(upstage_path):
                raise ValueError("provider artifacts must be distinct")
            if luna_payload.get("source_pdf_sha256") != source_hash or upstage_payload.get("source_pdf_sha256") != source_hash:
                raise ValueError("provider artifact source hash mismatch")
            # Each lane is loaded and validated only from its own provider directory.
            self.state.record_artifact(run_id, document_id, "ocr_json", "luna", str(luna_path), sha256_file(luna_path), {"provider": "luna"})
            self.state.record_artifact(run_id, document_id, "ocr_json", "upstage", str(upstage_path), sha256_file(upstage_path), {"provider": "upstage"})
            self._materialize_lane(run_id, document_id, "luna", luna_payload, adapters["luna"])
            self._materialize_lane(run_id, document_id, "upstage", upstage_payload, adapters["upstage"])
            ocr_comparison = compare_ocr_outputs(luna_payload, upstage_payload)
            self._validation_artifact(run_id, document_id, "ocr_comparison", ocr_comparison)
            self.state.record_stage(
                run_id,
                document_id,
                "ocr_comparison",
                sha256_bytes(canonical_json(ocr_comparison).encode()),
                "completed",
                ocr_comparison,
                now(),
            )
            luna = validate_lane("luna", luna_payload)
            self._validation_artifact(run_id, document_id, "luna_text_to_json", {"status": "pass", "facts": len(luna), "source_pdf_sha256": source_hash})
            upstage = validate_lane("upstage", upstage_payload)
            self._validation_artifact(run_id, document_id, "upstage_text_to_json", {"status": "pass", "facts": len(upstage), "source_pdf_sha256": source_hash})
            self.state.record_stage(run_id, document_id, "structured", sha256_bytes(canonical_json([luna, upstage]).encode()), "completed", {"lanes": ["luna", "upstage"]}, now())
            self.state.record_stage(run_id, document_id, "grounding", sha256_bytes(canonical_json([luna, upstage]).encode()), "completed", {"lanes": ["luna", "upstage"]}, now())
        except OcrProviderError as error:
            self.state.set_document_status(run_id, document_id, "blocked")
            self.state.record_stage(run_id, document_id, "ocr", source_hash, "blocked", {"error": str(error)}, now(), retryable=error.retryable)
            return
        except LaneRestructureRequired as error:
            self.state.set_document_status(run_id, document_id, "blocked")
            self._validation_artifact(run_id, document_id, "restructure_required", {"status": "blocked", "error": str(error)})
            self.state.record_stage(run_id, document_id, "structured", source_hash, "blocked", {"error": str(error), "action": "new_structuring_run"}, now(), retryable=False)
            return
        except (ValueError, FileNotFoundError) as error:
            signature = sha256_bytes(str(error).encode())
            self.state.open_review(run_id, document_id, "grounding_or_rule", signature, {"error": str(error)})
            self.state.set_document_status(run_id, document_id, "review")
            self._validation_artifact(run_id, document_id, "grounding_failure", {"status": "review", "error": str(error)})
            self.state.record_stage(run_id, document_id, "grounding", source_hash, "review", {"error": str(error)}, now())
            return
        canonical, mismatch, identity_mismatch = canonical_from_lanes(luna, upstage, luna_payload, upstage_payload)
        comparison = {"status": "review" if mismatch or identity_mismatch else "pass", "relation_mismatch": mismatch, "identity_mismatch": identity_mismatch}
        self._validation_artifact(run_id, document_id, "normalized_json_comparison", comparison)
        if identity_mismatch:
            signature = sha256_bytes(canonical_json(identity_mismatch).encode())
            self.state.open_review(run_id, document_id, "identity_mismatch", signature, identity_mismatch)
            self.state.set_document_status(run_id, document_id, "review")
            return
        if mismatch:
            signature = sha256_bytes(canonical_json(mismatch).encode())
            self.state.open_review(run_id, document_id, "relation_mismatch", signature, mismatch)
            self.state.set_document_status(run_id, document_id, "review")
            self.state.record_stage(run_id, document_id, "relation", signature, "review", mismatch, now())
            return
        self._write_canonical(run_id, document_id, canonical, {"issuer_name": normalized(luna_payload["identity"]["issuer_name"]), "card_name": normalized(luna_payload["identity"]["card_name"]), "evidence_refs": {"luna": luna_payload["identity"], "upstage": upstage_payload["identity"]}})

    def _write_canonical(self, run_id: str, document_id: str, canonical: list[dict[str, Any]], identity: dict[str, Any] | None = None) -> Path:
        encoded = (canonical_json({"document_id": document_id, "identity": identity or {}, "facts": canonical}) + "\n").encode()
        canonical_sha256 = sha256_bytes(encoded)
        root = self.runtime_root / "working" / run_id / "canonical" / document_id.replace("/", "__")
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{canonical_sha256}.json"
        if path.exists():
            if sha256_file(path) != canonical_sha256:
                raise RuntimeError("content-addressed canonical path hash mismatch")
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
        self.state.approve_canonical(run_id, document_id, str(path), canonical_sha256, len(canonical), now())
        return path

    def resolve_review(self, review_id: int, reviewer: str, reason: str, after_path: Path, luna_dir: Path | None = None, upstage_dir: Path | None = None) -> dict[str, Any]:
        review = self.state.review(review_id)
        if review["status"] != "open":
            raise RuntimeError("review is not open")
        document = self.state.document(str(review["run_id"]), str(review["document_id"]))
        if (luna_dir is None) != (upstage_dir is None):
            raise ValueError("both local lane directories or neither are required")
        if luna_dir is None:
            luna_path = self.state.artifact_path(str(review["run_id"]), str(document["document_id"]), "ocr_json", "luna")
            upstage_path = self.state.artifact_path(str(review["run_id"]), str(document["document_id"]), "ocr_json", "upstage")
            luna_payload = json.loads(luna_path.read_text(encoding="utf-8"))
            upstage_payload = json.loads(upstage_path.read_text(encoding="utf-8"))
        else:
            luna_path, luna_payload = load_lane("luna", luna_dir, str(document["document_id"]))
            upstage_path, upstage_payload = load_lane("upstage", upstage_dir, str(document["document_id"]))
        if sha256_file(Path(str(document["source_path"]))) != document["source_hash"]:
            raise RuntimeError("stale source PDF")
        for provider, path, payload in (("luna", luna_path, luna_payload), ("upstage", upstage_path, upstage_payload)):
            if sha256_file(path) != self.state.artifact_hash(str(review["run_id"]), str(document["document_id"]), "ocr_json", provider):
                raise RuntimeError(f"stale {provider} artifact")
            if payload.get("source_pdf_sha256") != document["source_hash"]:
                raise RuntimeError(f"stale {provider} payload source hash")
        resolution = json.loads(after_path.read_text(encoding="utf-8"))
        canonical, identity, audit = strict_resolution(resolution, luna_payload, upstage_payload)
        encoded = (canonical_json({"document_id": document["document_id"], "identity": identity, "facts": canonical}) + "\n").encode()
        canonical_sha256 = sha256_bytes(encoded)
        root = self.runtime_root / "working" / str(review["run_id"]) / "canonical" / str(document["document_id"]).replace("/", "__")
        root.mkdir(parents=True, exist_ok=True)
        canonical_path = root / f"{canonical_sha256}.json"
        if canonical_path.exists():
            if sha256_file(canonical_path) != canonical_sha256:
                raise RuntimeError("content-addressed canonical path hash mismatch")
        else:
            descriptor = os.open(canonical_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
        approved = self.state.resolve_with_canonical(review_id, reviewer, reason, audit, str(canonical_path), canonical_sha256, len(canonical), now())
        return {"review_id": review_id, "canonical_path": str(canonical_path), "canonical_approved": approved, "luna_sha256": sha256_file(luna_path), "upstage_sha256": sha256_file(upstage_path)}

    @staticmethod
    def _chunk_record(
        document_id: str,
        identity: dict[str, Any],
        level: str,
        local_key: str,
        text: str,
        evidence_refs: Any,
        *,
        section: str | None = None,
        parent_id: str | None = None,
        child_ids: list[str] | None = None,
        source_pages: list[int] | None = None,
    ) -> dict[str, Any]:
        chunk_id = sha256_bytes(f"{document_id}:{level}:{local_key}:{text}".encode())[:32]
        title = " | ".join(value for value in (identity.get("issuer_name"), identity.get("card_name"), section) if value)
        pages = evidence_pages(evidence_refs) if source_pages is None else sorted(set(source_pages))
        if not pages:
            raise ValueError("chunk requires source page provenance")
        augmented_text = f"{title}\n{text}" if title else text
        return {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "level": level,
            "text": text,
            "metadata": {
                "document_id": document_id,
                "level": level,
                "issuer_name": identity.get("issuer_name"),
                "card_name": identity.get("card_name"),
                "section": section,
                "parent_id": parent_id,
                "child_ids": child_ids or [],
                "source_pages": pages,
                "retrieval_text": augmented_text,
                "reranker_text": augmented_text,
                "evidence_refs": evidence_refs,
            },
        }

    def _chunks(
        self,
        run_id: str,
        documents: Iterable[Any],
        profile: str = DEFAULT_CHUNKING_PROFILE,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if profile not in CHUNKING_PROFILES:
            raise ValueError(f"unsupported chunking profile: {profile}")
        chunks: list[dict[str, Any]] = []
        document_ids: list[str] = []
        for document in documents:
            document_id = str(document["document_id"])
            canonical_path = Path(str(document["canonical_path"]))
            if not document["canonical_sha256"] or sha256_file(canonical_path) != document["canonical_sha256"] or self.state.artifact_hash(run_id, document_id, "canonical", None) != document["canonical_sha256"]:
                raise RuntimeError("canonical artifact hash mismatch")
            payload = json.loads(canonical_path.read_text(encoding="utf-8"))
            identity = payload.get("identity", {})
            document_ids.append(document_id)
            facts = list(payload["facts"])
            if not facts:
                continue
            card_text = " ".join(value for value in (identity.get("issuer_name"), identity.get("card_name")) if value)
            chunks.append(
                self._chunk_record(
                    document_id,
                    identity,
                    "card",
                    "card",
                    card_text,
                    identity.get("evidence_refs") or facts[0]["evidence_refs"],
                )
            )
            grouped: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
            pages: dict[int, list[tuple[int, dict[str, Any], str]]] = {}
            for index, item in enumerate(facts):
                fact = item["fact"]
                text = " | ".join(f"{field}: {fact[field]}" for field in RELATION_FIELDS if fact[field])
                grouped.setdefault(fact["target"], []).append((index, item, text))
                page_values = [ref.get("page") for ref in item["evidence_refs"].values() if isinstance(ref, dict)]
                valid_pages = [
                    value
                    for value in page_values
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 1
                ]
                if not valid_pages:
                    raise ValueError("canonical fact requires source page provenance")
                page = min(valid_pages)
                pages.setdefault(page, []).append((index, item, text))
            if profile == "card_page_section_benefit":
                for page, rows in pages.items():
                    chunks.append(
                        self._chunk_record(
                            document_id,
                            identity,
                            "page",
                            str(page),
                            "\n".join(row[2] for row in rows),
                            [row[1]["evidence_refs"] for row in rows],
                            source_pages=[page],
                        )
                    )
                for section, rows in grouped.items():
                    child_ids = [
                        sha256_bytes(f"{document_id}:benefit:{index}:{text}".encode())[:32]
                        for index, _item, text in rows
                    ]
                    section_record = self._chunk_record(
                        document_id,
                        identity,
                        "section",
                        section,
                        "\n".join(row[2] for row in rows),
                        [row[1]["evidence_refs"] for row in rows],
                        section=section,
                        child_ids=child_ids,
                    )
                    chunks.append(section_record)
                    for index, item, text in rows:
                        chunks.append(self._chunk_record(
                            document_id, identity, "benefit", str(index), text, item["evidence_refs"], section=section, parent_id=section_record["chunk_id"]
                        ))
            else:
                for section, rows in grouped.items():
                    child_ids = [
                        sha256_bytes(f"{document_id}:benefit:{index}:{text}".encode())[:32]
                        for index, _item, text in rows
                    ]
                    bundle = self._chunk_record(
                        document_id,
                        identity,
                        "bundle",
                        section,
                        "\n".join(row[2] for row in rows),
                        [row[1]["evidence_refs"] for row in rows],
                        section=section,
                        child_ids=child_ids,
                    )
                    chunks.append(bundle)
                    for index, item, text in rows:
                        chunks.append(self._chunk_record(
                            document_id, identity, "benefit", str(index), text, item["evidence_refs"], section=section, parent_id=bundle["chunk_id"]
                        ))
        return sorted(chunks, key=lambda item: item["chunk_id"]), sorted(document_ids)

    def publish(
        self,
        run_id: str,
        *,
        allow_partial: bool,
        fake_vectors: bool,
        profile: str = DEFAULT_CHUNKING_PROFILE,
        embedding_adapter: EmbeddingAdapter | None = None,
    ) -> str:
        if fake_vectors and embedding_adapter is not None:
            raise ValueError("fake vectors and an embedding adapter are mutually exclusive")
        if not fake_vectors and embedding_adapter is None:
            raise RuntimeError("embedding is blocked: inject an approved adapter or use explicit test-only --fake-vectors")
        all_documents = self.state.documents(run_id)
        approved = [row for row in all_documents if row["status"] == "canonical_approved"]
        omitted = [str(row["document_id"]) for row in all_documents if row["status"] != "canonical_approved"]
        if omitted and not allow_partial:
            raise RuntimeError("release blocked: documents are not canonical_approved")
        document_ids = [str(row["document_id"]) for row in approved]
        if not approved or self.state.unresolved_count(run_id, document_ids):
            raise RuntimeError("release blocked: unresolved review or no approved documents")
        chunks, document_ids = self._chunks(run_id, approved, profile)
        if not chunks:
            raise RuntimeError("release blocked: no benefit chunks")
        if fake_vectors:
            embedding_model = "test-only-sha256-16"
            dimension = 16
            release_status = "test_only"
            vector_mode = "test_fake_vector"
        else:
            embedding_model = str(getattr(embedding_adapter, "model", "")).strip()
            dimension = getattr(embedding_adapter, "dimension", 0)
            if not embedding_model or isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
                raise ValueError("embedding adapter model and dimension are required")
            release_status = "preview" if omitted else "production"
            vector_mode = "approved_adapter"
        corpus_hash = sha256_bytes(canonical_json(chunks).encode())
        release_id = "release_" + sha256_bytes(
            f"{run_id}:{corpus_hash}:{embedding_model}:{dimension}:{release_status}".encode()
        )[:16]
        final_root = self.runtime_root / "index-release" / release_id
        if final_root.exists():
            manifest = json.loads((final_root / "manifest.json").read_text(encoding="utf-8"))
            if (
                manifest.get("embedding_model") != embedding_model
                or manifest.get("embedding_dimension") != dimension
                or manifest.get("release_status") != release_status
            ):
                raise RuntimeError("existing release embedding contract mismatch")
            embeddings = self._read_embeddings(
                final_root / "chroma",
                profile,
                [chunk["chunk_id"] for chunk in chunks],
            )
            self._materialize_serving(final_root, manifest, chunks, embeddings)
            try:
                self.state.release(release_id)
            except KeyError:
                self.state.record_release(release_id, run_id, str(final_root), sha256_file(final_root / "manifest.json"), now(), str(manifest["release_status"]))
            return release_id
        final_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=final_root.parent))
        try:
            corpus_path = temporary_root / "corpus.sqlite"
            self._build_fts(corpus_path, chunks)
            retrieval_texts = [str(chunk["metadata"]["retrieval_text"]) for chunk in chunks]
            if fake_vectors:
                embeddings = np.asarray(
                    [self._fake_embedding(text, dimension) for text in retrieval_texts],
                    dtype=np.float32,
                )
                embedding_usage: dict[str, Any] = {
                    "provider_called": False,
                    "item_count": len(chunks),
                }
            else:
                adapter_embeddings, embedding_usage = embedding_adapter.embed_documents(retrieval_texts)
                embeddings = np.asarray(adapter_embeddings, dtype=np.float32)
                if not isinstance(embedding_usage, dict):
                    raise ValueError("embedding adapter usage must be a dictionary")
            if embeddings.shape != (len(chunks), dimension) or not np.isfinite(embeddings).all():
                raise ValueError("embedding adapter result shape or finiteness mismatch")
            self._build_chroma(temporary_root / "chroma", chunks, embeddings, corpus_hash, profile)
            manifest = {
                "schema_version": "rag_index_release_v1",
                "release_id": release_id,
                "run_id": run_id,
                "strategy": profile,
                "vector_mode": vector_mode,
                "release_status": release_status,
                "distance_contract": "squared_l2",
                "corpus_hash": corpus_hash,
                "scope_document_ids": document_ids,
                "requested_document_ids": sorted(str(row["document_id"]) for row in all_documents),
                "document_ids": document_ids,
                "catalog": [{"document_id": key[0], "issuer_name": key[1], "card_name": key[2]} for key in sorted({(chunk["document_id"], chunk["metadata"].get("issuer_name"), chunk["metadata"].get("card_name")) for chunk in chunks})],
                "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
                "embedding_dimension": dimension,
                "embedding_model": embedding_model,
                "embedding_usage": embedding_usage,
                "embedding_sha256": embedding_sha256(
                    [chunk["chunk_id"] for chunk in chunks], embeddings
                ),
                "corpus_sqlite_sha256": sha256_file(corpus_path),
                "chroma_tree_sha256": tree_hash(temporary_root / "chroma"),
                "coverage": {"included_document_ids": document_ids, "omitted_document_ids": omitted, "partial": bool(omitted)},
            }
            manifest_path = temporary_root / "manifest.json"
            manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
            self._verify_release(temporary_root, manifest, chunks, embeddings)
            os.replace(temporary_root, final_root)
            self._make_read_only(final_root)
            self._materialize_serving(final_root, manifest, chunks, embeddings)
            self.state.record_release(
                release_id,
                run_id,
                str(final_root),
                sha256_file(final_root / "manifest.json"),
                now(),
                release_status,
            )
            return release_id
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise

    @staticmethod
    def _fake_embedding(text: str, dimension: int) -> np.ndarray:
        values = bytearray()
        counter = 0
        while len(values) < dimension:
            values.extend(hashlib.sha256(f"{counter}:{text}".encode()).digest())
            counter += 1
        return (np.frombuffer(bytes(values[:dimension]), dtype=np.uint8).astype(np.float32) / 255.0) - 0.5

    @staticmethod
    def _read_embeddings(path: Path, collection_name: str, chunk_ids: list[str]) -> np.ndarray:
        import chromadb

        with tempfile.TemporaryDirectory() as temporary:
            working = Path(temporary) / "chroma"
            shutil.copytree(path, working, copy_function=shutil.copy2)
            working.chmod(0o755)
            for item in working.rglob("*"):
                item.chmod(0o755 if item.is_dir() else 0o644)
            client = chromadb.PersistentClient(path=str(working))
            collection = client.get_collection(collection_name)
            output = collection.get(ids=chunk_ids, include=["embeddings"])
            by_id = {
                chunk_id: embedding
                for chunk_id, embedding in zip(output["ids"], output["embeddings"], strict=True)
            }
            if set(by_id) != set(chunk_ids):
                raise RuntimeError("stored embedding identity mismatch")
            embeddings = np.asarray([by_id[chunk_id] for chunk_id in chunk_ids], dtype=np.float32)
            del collection, client
            gc.collect()
        if embeddings.ndim != 2 or not np.isfinite(embeddings).all():
            raise RuntimeError("stored embeddings are invalid")
        return embeddings

    @staticmethod
    def _build_fts(path: Path, chunks: list[dict[str, Any]]) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.executescript(
                """CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL, metadata_json TEXT NOT NULL) STRICT;
                   CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61');"""
            )
            for chunk in chunks:
                connection.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", (chunk["chunk_id"], chunk["document_id"], chunk["level"], chunk["text"], canonical_json(chunk["metadata"])))
                connection.execute(
                    "INSERT INTO chunks_fts VALUES(?,?)",
                    (
                        chunk["chunk_id"],
                        normalized(str(chunk["metadata"].get("retrieval_text", chunk["text"]))),
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _build_chroma(
        path: Path,
        chunks: list[dict[str, Any]],
        embeddings: np.ndarray,
        corpus_hash: str = "test",
        collection_name: str = DEFAULT_CHUNKING_PROFILE,
    ) -> None:
        import chromadb

        client = chromadb.PersistentClient(path=str(path))
        collection = client.get_or_create_collection(collection_name, metadata={"hnsw:space": "l2", "corpus_hash": corpus_hash})
        collection.add(
            ids=[chunk["chunk_id"] for chunk in chunks],
            documents=[str(chunk["metadata"].get("retrieval_text", chunk["text"])) for chunk in chunks],
            metadatas=[{"document_id": chunk["document_id"], "level": chunk["level"]} for chunk in chunks],
            embeddings=embeddings.tolist(),
        )
        del collection, client
        gc.collect()

    @staticmethod
    def _verify_release(root: Path, manifest: dict[str, Any], chunks: list[dict[str, Any]], embeddings: np.ndarray, chroma_root: Path | None = None) -> None:
        connection = sqlite3.connect(root / "corpus.sqlite")
        try:
            rows = connection.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id").fetchall()
            fts_count = connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        finally:
            connection.close()
        expected_ids = [chunk["chunk_id"] for chunk in chunks]
        if [row[0] for row in rows] != expected_ids or fts_count != len(expected_ids):
            raise RuntimeError("FTS identity mismatch")
        if manifest.get("corpus_sqlite_sha256") and sha256_file(root / "corpus.sqlite") != manifest["corpus_sqlite_sha256"]:
            raise RuntimeError("SQLite corpus hash mismatch")
        import chromadb

        client = chromadb.PersistentClient(path=str(chroma_root or root / "chroma"))
        collection = client.get_collection(manifest.get("strategy", DEFAULT_CHUNKING_PROFILE))
        output = collection.get(include=["embeddings"])
        collection_corpus_hash = collection.metadata.get("corpus_hash")
        del collection, client
        gc.collect()
        output_by_id = {
            chunk_id: embedding
            for chunk_id, embedding in zip(output["ids"], output["embeddings"], strict=True)
        }
        stored_embeddings = np.asarray(
            [output_by_id[chunk_id] for chunk_id in expected_ids if chunk_id in output_by_id],
            dtype=np.float32,
        )
        if set(output_by_id) != set(expected_ids) or stored_embeddings.shape != embeddings.shape:
            raise RuntimeError("FTS5-Chroma identity or dimension mismatch")
        if manifest["chunk_ids"] != expected_ids or manifest["document_ids"] != sorted({chunk["document_id"] for chunk in chunks}):
            raise RuntimeError("release manifest identity mismatch")
        if collection_corpus_hash != manifest.get("corpus_hash", "test"):
            raise RuntimeError("Chroma corpus hash mismatch")
        if manifest.get("embedding_sha256") and embedding_sha256(expected_ids, stored_embeddings) != manifest["embedding_sha256"]:
            raise RuntimeError("Chroma embedding hash mismatch")
        if manifest.get("chroma_tree_sha256") and tree_hash(root / "chroma") != manifest["chroma_tree_sha256"]:
            raise RuntimeError("immutable Chroma source hash mismatch")

    def _materialize_serving(self, release_root: Path, manifest: dict[str, Any], chunks: list[dict[str, Any]], embeddings: np.ndarray) -> Path:
        source = release_root / "chroma"
        if tree_hash(source) != manifest["chroma_tree_sha256"]:
            raise RuntimeError("serving copy source hash mismatch")
        release_id = str(manifest["release_id"])
        version = str(manifest["chroma_tree_sha256"])
        if not re.fullmatch(r"[0-9a-f]{64}", version):
            raise RuntimeError("invalid Chroma tree hash")
        serving_root = self.runtime_root / "serving"
        version_parent = serving_root / release_id
        version_root = version_parent / version
        marker_contract = {
            "release_id": release_id,
            "chroma_tree_sha256": version,
            "corpus_hash": manifest["corpus_hash"],
            "chunk_ids": manifest["chunk_ids"],
            "embedding_dimension": manifest["embedding_dimension"],
            "embedding_sha256": manifest["embedding_sha256"],
        }
        lock_root = serving_root / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        with (lock_root / f"{release_id}.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if version_root.exists():
                    marker_path = version_root / "version.json"
                    existing_marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else None
                    if not isinstance(existing_marker, dict) or existing_marker != marker_contract:
                        raise RuntimeError("existing serving version marker mismatch")
                    stored = self._read_embeddings(
                        version_root / "chroma",
                        str(manifest["strategy"]),
                        list(manifest["chunk_ids"]),
                    )
                    if embedding_sha256(list(manifest["chunk_ids"]), stored) != manifest["embedding_sha256"]:
                        raise RuntimeError("existing serving version content mismatch")
                    return version_root
                version_parent.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=version_parent))
                try:
                    shutil.copytree(source, staging / "chroma", copy_function=shutil.copy2)
                    for path in (staging / "chroma").rglob("*"):
                        path.chmod(0o755 if path.is_dir() else 0o644)
                    (staging / "chroma").chmod(0o755)
                    self._verify_release(release_root, manifest, chunks, embeddings, staging / "chroma")
                    if tree_hash(source) != version:
                        raise RuntimeError("immutable Chroma source changed while materializing serving copy")
                    (staging / "version.json").write_text(canonical_json(marker_contract) + "\n", encoding="utf-8")
                    os.replace(staging, version_root)
                    (version_root / "version.json").chmod(0o444)
                    version_root.chmod(0o555)
                except Exception:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise
                return version_root
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _make_read_only(root: Path) -> None:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.chmod(0o444)
            elif path.is_dir():
                path.chmod(0o555)
        root.chmod(0o555)

    def activate(self, release_id: str) -> Path:
        import fcntl

        release = self.state.release(release_id)
        root = Path(str(release["path"]))
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("release_id") != release_id:
            raise RuntimeError("release manifest ID mismatch")
        if manifest.get("release_status") != "production":
            raise RuntimeError("only a production release can be activated")
        if sha256_file(root / "manifest.json") != release["manifest_sha256"]:
            raise RuntimeError("release manifest hash mismatch")
        target = self.runtime_root / "active-index.json"
        lock_path = self.runtime_root / ".active-index.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            temporary = target.with_name(f".active-index.{os.getpid()}.tmp")
            temporary.write_text(canonical_json({"release_id": release_id, "manifest_sha256": sha256_file(root / "manifest.json")}) + "\n", encoding="utf-8")
            os.replace(temporary, target)
            self.state.record_activation(release_id, "activate", now())
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return target

    def rollback(self, release_id: str) -> Path:
        pointer = self.activate(release_id)
        self.state.record_activation(release_id, "rollback", now())
        return pointer
