from __future__ import annotations

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
from typing import Any, Iterable, Protocol

import numpy as np

from .state import StateStore, canonical_json


RELATION_FIELDS = ("target", "condition", "value", "unit", "cap", "frequency", "period", "exceptions")
NUMBER = re.compile(r"\d+(?:[,.]\d+)?")


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


def tree_hash(root: Path) -> str:
    rows = [{"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)} for path in sorted(root.rglob("*")) if path.is_file()]
    return sha256_bytes(canonical_json(rows).encode())


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
        "sources": {document["document_id"]: sha256_file(Path(document["source_pdf"])) for document in documents},
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


def validate_fact_evidence(fact: dict[str, str], quote: str, context: str) -> None:
    for field in ("target", "condition", "unit", "cap", "frequency", "period", "exceptions"):
        if fact[field] and fact[field] not in quote:
            raise ValueError(f"{context} {field} is not linked to its evidence quote")
    value_numbers = numbers(fact["value"])
    if value_numbers and not value_numbers <= numbers(quote):
        raise ValueError(f"{context} has a value not linked to its evidence quote")
    if fact["value"] == "0" and "0" not in numbers(quote):
        raise ValueError(f"{context} zero is not explicit in its evidence quote")


def lane_path(root: Path, document_id: str) -> Path:
    return root / f"{document_id.replace('/', '__')}.json"


class ProviderAdapter(Protocol):
    provider: str

    def load(self, document_id: str) -> tuple[Path, dict[str, Any]]: ...


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
        if disposition["kind"] == "ignore" and not normalized(disposition.get("reason", "")):
            raise ValueError(f"{provider} ignored span reason is required")
        if disposition["kind"] == "ignore":
            raise ValueError(f"{provider} ignored span requires review")
        mapped.add(item)
    if mapped != lines:
        raise ValueError(f"{provider} OCR line lacks explicit disposition")
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
    ) -> dict[str, Any]:
        documents = read_source_manifest(source_manifest)
        input_hash = input_fingerprint(source_manifest, documents, luna_dir, upstage_dir)
        config_hash = sha256_bytes(canonical_json(config).encode())
        run_id = "run_" + sha256_bytes(f"{input_hash}:{config_hash}".encode())[:16]
        run_id = self.state.find_or_create_run(run_id, input_hash, config_hash, now())
        for document in documents:
            self._process_document(run_id, document, luna_dir, upstage_dir, providers)
        document_statuses = {str(row["status"]) for row in self.state.documents(run_id)}
        if "review" in document_statuses:
            self.state.set_run_status(run_id, "review", now())
        elif "blocked" in document_statuses:
            self.state.set_run_status(run_id, "blocked", now())
        elif document_statuses != {"canonical_approved"}:
            self.state.set_run_status(run_id, "failed", now())
        release_id = None
        if fake_vectors:
            release_id = self.publish(run_id, allow_partial=allow_partial, fake_vectors=True)
            self.state.set_run_status(run_id, "test_only_published", now())
        elif document_statuses == {"canonical_approved"}:
            self.state.set_run_status(run_id, "canonical_approved", now())
        return {"run_id": run_id, "release_id": release_id, "status": self.state.status(run_id)}

    def _process_document(self, run_id: str, document: dict[str, str], luna_dir: Path | None, upstage_dir: Path | None, providers: dict[str, ProviderAdapter] | None) -> None:
        document_id, source_path = document["document_id"], Path(document["source_pdf"])
        source_hash = sha256_file(source_path)
        try:
            existing = self.state.document(run_id, document_id)
            if existing["source_hash"] == source_hash and existing["status"] == "canonical_approved":
                return
        except KeyError:
            pass
        self.state.upsert_document(run_id, document_id, str(source_path), source_hash, "running")
        self.state.record_stage(run_id, document_id, "source", source_hash, "completed", {"source_sha256": source_hash}, now())
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
            luna = validate_lane("luna", luna_payload)
            upstage = validate_lane("upstage", upstage_payload)
            self.state.record_stage(run_id, document_id, "structured", sha256_bytes(canonical_json([luna, upstage]).encode()), "completed", {"lanes": ["luna", "upstage"]}, now())
            self.state.record_stage(run_id, document_id, "grounding", sha256_bytes(canonical_json([luna, upstage]).encode()), "completed", {"lanes": ["luna", "upstage"]}, now())
        except (ValueError, FileNotFoundError) as error:
            signature = sha256_bytes(str(error).encode())
            self.state.open_review(run_id, document_id, "grounding_or_rule", signature, {"error": str(error)})
            self.state.set_document_status(run_id, document_id, "review")
            self.state.record_stage(run_id, document_id, "grounding", source_hash, "review", {"error": str(error)}, now())
            return
        canonical, mismatch, identity_mismatch = canonical_from_lanes(luna, upstage, luna_payload, upstage_payload)
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

    def resolve_review(self, review_id: int, reviewer: str, reason: str, after_path: Path, luna_dir: Path, upstage_dir: Path) -> dict[str, Any]:
        review = self.state.review(review_id)
        if review["status"] != "open":
            raise RuntimeError("review is not open")
        document = self.state.document(str(review["run_id"]), str(review["document_id"]))
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

    def _chunks(self, run_id: str, documents: Iterable[Any]) -> tuple[list[dict[str, Any]], list[str]]:
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
            for index, item in enumerate(payload["facts"]):
                fact = item["fact"]
                text = " | ".join(f"{field}: {fact[field]}" for field in RELATION_FIELDS if fact[field])
                chunk_id = sha256_bytes(f"{document_id}:{index}:{canonical_json(fact)}".encode())[:32]
                chunks.append({"chunk_id": chunk_id, "document_id": document_id, "level": "benefit", "text": text, "metadata": {"document_id": document_id, "level": "benefit", "issuer_name": identity.get("issuer_name"), "card_name": identity.get("card_name"), "evidence_refs": item["evidence_refs"]}})
        return sorted(chunks, key=lambda item: item["chunk_id"]), sorted(document_ids)

    def publish(self, run_id: str, *, allow_partial: bool, fake_vectors: bool) -> str:
        if not fake_vectors:
            raise RuntimeError("embedding is blocked: inject an approved adapter or use explicit test-only --fake-vectors")
        all_documents = self.state.documents(run_id)
        approved = [row for row in all_documents if row["status"] == "canonical_approved"]
        omitted = [str(row["document_id"]) for row in all_documents if row["status"] != "canonical_approved"]
        if omitted and not allow_partial:
            raise RuntimeError("release blocked: documents are not canonical_approved")
        document_ids = [str(row["document_id"]) for row in approved]
        if not approved or self.state.unresolved_count(run_id, document_ids):
            raise RuntimeError("release blocked: unresolved review or no approved documents")
        chunks, document_ids = self._chunks(run_id, approved)
        if not chunks:
            raise RuntimeError("release blocked: no benefit chunks")
        corpus_hash = sha256_bytes(canonical_json(chunks).encode())
        release_id = "release_" + sha256_bytes(f"{run_id}:{corpus_hash}".encode())[:16]
        final_root = self.runtime_root / "index-release" / release_id
        if final_root.exists():
            manifest = json.loads((final_root / "manifest.json").read_text(encoding="utf-8"))
            embeddings = np.asarray([self._fake_embedding(chunk["text"], int(manifest["embedding_dimension"])) for chunk in chunks], dtype=np.float32)
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
            dimension = 16
            embeddings = np.asarray([self._fake_embedding(chunk["text"], dimension) for chunk in chunks], dtype=np.float32)
            self._build_chroma(temporary_root / "chroma", chunks, embeddings, corpus_hash)
            manifest = {
                "schema_version": "rag_index_release_v1",
                "release_id": release_id,
                "run_id": run_id,
                "strategy": "benefit_hierarchy",
                "vector_mode": "test_fake_vector",
                "release_status": "test_only",
                "distance_contract": "squared_l2",
                "corpus_hash": corpus_hash,
                "scope_document_ids": document_ids,
                "requested_document_ids": sorted(str(row["document_id"]) for row in all_documents),
                "document_ids": document_ids,
                "catalog": [{"document_id": key[0], "issuer_name": key[1], "card_name": key[2]} for key in sorted({(chunk["document_id"], chunk["metadata"].get("issuer_name"), chunk["metadata"].get("card_name")) for chunk in chunks})],
                "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
                "embedding_dimension": dimension,
                "chroma_tree_sha256": tree_hash(temporary_root / "chroma"),
                "coverage": {"included_document_ids": document_ids, "omitted_document_ids": omitted, "partial": bool(omitted)},
            }
            manifest_path = temporary_root / "manifest.json"
            manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
            self._verify_release(temporary_root, manifest, chunks, embeddings)
            os.replace(temporary_root, final_root)
            self._make_read_only(final_root)
            self._materialize_serving(final_root, manifest, chunks, embeddings)
            self.state.record_release(release_id, run_id, str(final_root), sha256_file(final_root / "manifest.json"), now(), "test_only")
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
                connection.execute("INSERT INTO chunks_fts VALUES(?,?)", (chunk["chunk_id"], normalized(chunk["text"])))
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _build_chroma(path: Path, chunks: list[dict[str, Any]], embeddings: np.ndarray, corpus_hash: str = "test") -> None:
        import chromadb

        client = chromadb.PersistentClient(path=str(path))
        collection = client.get_or_create_collection("benefit_hierarchy", metadata={"hnsw:space": "l2", "corpus_hash": corpus_hash})
        collection.add(
            ids=[chunk["chunk_id"] for chunk in chunks],
            documents=[chunk["text"] for chunk in chunks],
            metadatas=[{"document_id": chunk["document_id"], "level": chunk["level"]} for chunk in chunks],
            embeddings=embeddings.tolist(),
        )

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
        import chromadb

        collection = chromadb.PersistentClient(path=str(chroma_root or root / "chroma")).get_collection("benefit_hierarchy")
        output = collection.get(include=["embeddings"])
        if sorted(output["ids"]) != expected_ids or np.asarray(output["embeddings"]).shape != embeddings.shape:
            raise RuntimeError("FTS5-Chroma identity or dimension mismatch")
        if manifest["chunk_ids"] != expected_ids or manifest["document_ids"] != sorted({chunk["document_id"] for chunk in chunks}):
            raise RuntimeError("release manifest identity mismatch")
        if collection.metadata.get("corpus_hash") != manifest.get("corpus_hash", "test"):
            raise RuntimeError("Chroma corpus hash mismatch")
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
        }
        lock_root = serving_root / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        with (lock_root / f"{release_id}.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if version_root.exists():
                    marker_path = version_root / "version.json"
                    existing_marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else None
                    if not isinstance(existing_marker, dict) or {key: existing_marker.get(key) for key in marker_contract} != marker_contract or set(existing_marker) != {*marker_contract, "serving_tree_sha256"}:
                        raise RuntimeError("existing serving version marker mismatch")
                    if tree_hash(version_root / "chroma") != existing_marker["serving_tree_sha256"]:
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
                    marker = {**marker_contract, "serving_tree_sha256": tree_hash(staging / "chroma")}
                    (staging / "version.json").write_text(canonical_json(marker) + "\n", encoding="utf-8")
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
        if manifest.get("release_status") == "test_only":
            raise RuntimeError("test-only release cannot be activated")
        raise RuntimeError("live embedding/activation unavailable")
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
