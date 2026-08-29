"""Resumable OCR and field-structure runner for notebook 12.

Dry-run is the default. Provider calls require both --live-api and an explicit
--engines selection. Offline evaluation is an explicit stage and never calls a
provider.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import socket
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
import requests
from dotenv import load_dotenv
from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "notebooks/data/12_clean_end_to_end/current/runs"
UPSTAGE_URL = "https://api.upstage.ai/v1/document-digitization"
UPSTAGE_REQUEST_CONFIG = {
    "model": "document-parse",
    "ocr": "force",
    "coordinates": True,
    "output_formats": ["html", "markdown"],
}
OPENAI_DETAIL = "original"
OPENAI_MODELS = {"openai_luna": "gpt-5.6-luna", "openai_terra": "gpt-5.6-terra"}
ENGINES = (*OPENAI_MODELS, "upstage")
MAX_DOCUMENTS = 10
MAX_PAGES = 50
MAX_CALLS = 140
MAX_ATTEMPTS = 1
FIELD_EXTRACTION_MODEL = "gpt-5.6-luna"
MAX_STRUCTURE_CALLS = 60
STRUCTURE_TIMEOUT_SECONDS = 180.0
STRUCTURE_MAX_RETRIES = 0
STRUCTURE_SCHEMA_VERSION = "card_field_extraction_v1"
STRUCTURE_PROMPT = """OCR 전사에서 카드 혜택·수수료 필드를 추출하세요.
제공된 ID와 JSON shape만 사용하고, OCR에 없는 값은 추측하지 말고 null로 반환하세요.
모든 엔진에 같은 기준을 적용하세요. 숫자는 surface_text, normalized_value, unit을 분리하고
normalized_value와 unit은 가능한 경우 KRW, RATIO, COUNT_PER_DAY 같은 명시적 단위로 정규화하세요.
표는 원문의 열 순서와 행 순서를 그대로 보존하세요.""".strip()
NORMALIZED_SCHEMA = {
    "schema_version": "normalized_ocr_v2",
    "canonical_text": "pages[].text",
    "coordinates": "provider evidence only; omitted for OpenAI",
}
OCR_PROMPT = """카드 상품설명서 이미지를 원문 순서대로 정확히 전사하세요.
숫자, 단위, 부호, 적용 대상, 제외 조건, 전월 실적 조건과 표의 행·열 관계를 보존하세요.
보이지 않는 내용을 추측하거나 요약하지 말고, 읽을 수 없는 부분은 [ILLEGIBLE]로 표시하세요.""".strip()
CARD_SPECS = (
    ("BC", "BC_Biz_AirMoney"),
    ("NH", "NH_Namu_NH"),
    ("hana", "Hana_One_More_SOHO"),
    ("hyundai", "Hyundai_The_Orange_20260330"),
    ("ibk", "IBK_Point3.8(Credit)"),
    ("kookmin", "Kookmin_Friend_20210917"),
    ("lotte", "Lotte_LOCA_LIKIT_Eat"),
    ("samsung", "Samsung_iD_ALL"),
    ("shinhan", "Shinhan_Toss_Mr.Life_20251231"),
    ("woori", "Woori_Classic_EVERY_MILE_SKYPASS"),
)
COVERAGE_POLICY = {
    f"{issuer}/{card_name}": (
        {"annotation_scope": "selected_excerpt", "full_page_cer": "excluded"}
        if (issuer, card_name) == ("BC", "BC_Biz_AirMoney")
        else {"annotation_scope": "incomplete_or_ambiguous", "full_page_cer": "excluded"}
        if (issuer, card_name) == ("ibk", "IBK_Point3.8(Credit)")
        else {
            "annotation_scope": "full_page_candidate",
            "full_page_cer": "excluded_until_visual_audit",
            **({"audit_note": "PDF 1~2쪽 시각 확인 권장"} if issuer == "hyundai" else {}),
            **({"parser_note": "[page1]/[page 1] 공백 허용; 리터럴 검증 금지"} if issuer == "woori" else {}),
        }
    )
    for issuer, card_name in CARD_SPECS
}
PAGE_MARKER = re.compile(r"^\[PAGE\s*(\d+)\]\s*$", re.IGNORECASE | re.MULTILINE)
PANEL_MARKER = re.compile(r"^\[(?:left|center|right)_panel\]\s*$", re.IGNORECASE | re.MULTILINE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    else:
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(value)


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def file_reference(path: Path, optional: bool = False) -> dict[str, Any]:
    relative = path.resolve().relative_to(ROOT).as_posix()
    if not path.is_file():
        if optional:
            return {"path": relative, "available": False}
        raise FileNotFoundError(path)
    return {"path": relative, "available": True, "sha256": sha256_file(path), "bytes": path.stat().st_size}


def optional_reference_paths() -> tuple[Path, ...]:
    fixed = (
        ROOT / "notebooks/data/09_core_numeric_condition_ocr_evaluation/api_original_repeatability_summary.json",
        ROOT / "notebooks/data/09_core_numeric_condition_ocr_evaluation/runs/20260807T144000Z_upstage_baseline/summary.json",
        ROOT / "notebooks/data/10_relational_critical_fact_evaluation/runs/20260808T082345Z/summary.json",
        ROOT / "notebooks/data/11_numeric_error_attribution/runs/20260810T060240Z/summary.json",
    )
    upstage = tuple(
        ROOT / path_root / issuer / f"{card_name}{suffix}"
        for path_root, suffix in (
            ("data/ocr_benchmark/text/upstage", ".md"),
            ("data/ocr_benchmark/normalized/upstage", ".json"),
        )
        for issuer, card_name in CARD_SPECS
    )
    return fixed + upstage


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def atomic_write_new_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"기존 결과를 덮어쓰지 않습니다: {path}")
    atomic_write_json(path, value)


class RunLockError(RuntimeError):
    pass


def lock_path_for(output_root: Path, run_id: str) -> Path:
    return output_root / ".locks" / f"{run_id}.lock"


def read_lock_owner(lock_path: Path) -> dict[str, Any]:
    try:
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunLockError(f"run lock metadata를 읽을 수 없습니다: {lock_path} ({type(error).__name__})") from error
    if not isinstance(owner, dict):
        raise RunLockError(f"run lock metadata가 JSON 객체가 아닙니다: {lock_path}")
    return owner


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_start_ticks(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return int(fields[19])  # proc stat field 22; fields starts at field 3.
    except (IndexError, OSError, ValueError):
        return None


def boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class RunLock:
    def __init__(self, output_root: Path, run_id: str, scope: dict[str, Any], recover_stale: bool = False) -> None:
        self.path = lock_path_for(output_root, run_id)
        self.owner = {
            "lock_id": uuid.uuid4().hex,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "boot_id": boot_id(),
            "process_start_ticks": process_start_ticks(os.getpid()),
            "started_at": utc_now(),
            "scope": scope,
        }
        self.recover_stale = recover_stale
        self.acquired = False

    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(self.owner, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                try:
                    lock_stat = self.path.stat()
                except FileNotFoundError:
                    continue
                existing = read_lock_owner(self.path)
                if not self.recover_stale:
                    raise RunLockError(f"동일 run이 이미 실행 중이거나 lock 확인이 필요합니다: {existing}")
                if existing.get("host") != socket.gethostname():
                    raise RunLockError(f"다른 host의 stale 여부는 자동 확인하지 않습니다: {existing}")
                try:
                    existing_pid = int(existing["pid"])
                except (KeyError, TypeError, ValueError) as error:
                    raise RunLockError(f"lock PID를 안전하게 확인할 수 없습니다: {existing}") from error
                same_boot = not existing.get("boot_id") or existing.get("boot_id") == boot_id()
                recorded_start = existing.get("process_start_ticks")
                try:
                    recorded_start = int(recorded_start) if recorded_start is not None else None
                except (TypeError, ValueError) as error:
                    raise RunLockError(f"lock process identity를 안전하게 확인할 수 없습니다: {existing}") from error
                same_process = (
                    pid_is_alive(existing_pid)
                    if recorded_start is None
                    else same_boot and process_start_ticks(existing_pid) == recorded_start
                )
                if same_process:
                    raise RunLockError(f"lock 소유 PID가 현재 살아 있어 복구하지 않습니다: {existing}")
                try:
                    current_stat = self.path.stat()
                    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
                    if identity(current_stat) != identity(lock_stat):
                        raise RunLockError(f"stale 확인 중 lock 소유자가 바뀌어 복구하지 않습니다: {self.path}")
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            try:
                offset = 0
                while offset < len(payload):
                    offset += os.write(descriptor, payload[offset:])
                os.fsync(descriptor)
            except Exception:
                self.path.unlink(missing_ok=True)
                raise
            finally:
                os.close(descriptor)
            self.acquired = True
            return self
        raise RunLockError(f"stale lock 복구 중 다른 프로세스가 lock을 획득했습니다: {self.path}")

    def release(self) -> None:
        if not self.acquired:
            return
        existing = read_lock_owner(self.path)
        if existing.get("lock_id") != self.owner["lock_id"]:
            raise RunLockError(f"다른 소유자의 run lock은 해제하지 않습니다: {existing}")
        self.path.unlink()
        self.acquired = False

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        self.release()


def write_new_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def marker_collisions(text: str) -> dict[str, int]:
    found = {"page_marker": len(PAGE_MARKER.findall(text)), "panel_marker": len(PANEL_MARKER.findall(text))}
    return {key: count for key, count in found.items() if count}


def canonical_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("pages[].text는 문자열이어야 합니다.")
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalized_to_txt(document: dict[str, Any]) -> str:
    validate_normalized(document, len(document["pages"]))
    if any(page["marker_collisions"].get("page_marker") for page in document["pages"]):
        raise ValueError("provider text의 reserved PAGE marker collision")
    return "\n\n".join(f"[PAGE {page['page_num']}]\n{page['text']}" for page in document["pages"]) + "\n"


def parse_normalized_txt(value: str) -> list[dict[str, Any]]:
    matches = list(PAGE_MARKER.finditer(value))
    if not matches or value[: matches[0].start()].strip():
        raise ValueError("TXT는 PAGE marker로 시작해야 합니다.")
    pages = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        text = canonical_text(value[match.end() : end])
        pages.append({"page_num": int(match.group(1)), "text": text, "marker_collisions": marker_collisions(text)})
    return pages


def validate_normalized(document: dict[str, Any], expected_pages: int) -> dict[str, Any]:
    if document.get("schema_version") != "normalized_ocr_v2":
        raise ValueError("normalized_ocr_v2 schema가 아닙니다.")
    pages = document.get("pages")
    if not isinstance(pages, list) or len(pages) != expected_pages:
        raise ValueError(f"예상 페이지 수 불일치: expected={expected_pages}, actual={len(pages or [])}")
    if [page.get("page_num") for page in pages] != list(range(1, expected_pages + 1)):
        raise ValueError("page_num은 1부터 연속이어야 합니다.")
    for page in pages:
        if not canonical_text(page.get("text")):
            raise ValueError(f"빈 canonical page text: page {page.get('page_num')}")
        if page["text"] != canonical_text(page["text"]):
            raise ValueError(f"canonical text가 아님: page {page['page_num']}")
        if page.get("marker_collisions") != marker_collisions(page["text"]):
            raise ValueError(f"marker collision 기록 불일치: page {page['page_num']}")
        if document.get("provider") == "openai" and "coordinates" in page:
            raise ValueError("OpenAI normalized page에는 좌표를 만들지 않습니다.")
    return document


def request_fingerprint(
    provider: str,
    model: str,
    config: dict[str, Any],
    prompt: str,
    source_kind: str,
    source_sha256: str,
) -> dict[str, Any]:
    components = {
        "provider": provider,
        "model": model,
        "config": config,
        "prompt_sha256": sha256_value(prompt),
        "source": {"kind": source_kind, "sha256": source_sha256},
        "schema_sha256": sha256_value(NORMALIZED_SCHEMA),
    }
    return {"fingerprint": sha256_value(components), "components": components}


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def value_shape_schema(value: Any) -> dict[str, Any]:
    """Return a value-less JSON schema that preserves only type and shape."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        schemas = []
        for item in value:
            schema = value_shape_schema(item)
            if schema not in schemas:
                schemas.append(schema)
        item_schema = schemas[0] if len(schemas) == 1 else {"anyOf": schemas} if schemas else {}
        return {"type": "array", "items": item_schema}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {key: value_shape_schema(item) for key, item in value.items()},
            "required": list(value),
            "additionalProperties": False,
        }
    raise TypeError(f"지원하지 않는 gold value type: {type(value).__name__}")


def structure_label_metadata(gold: dict[str, Any], pages: set[int] | None = None) -> dict[str, list[dict[str, Any]]]:
    metadata: dict[str, list[dict[str, Any]]] = {"field_labels": [], "numeric_labels": [], "table_labels": []}
    for kind in metadata:
        for label in gold.get(kind, []):
            page_num = label.get("page_num")
            if pages is not None and page_num not in pages:
                continue
            item = {
                "id": label["id"],
                "page_num": page_num,
            }
            if kind == "field_labels":
                item["value_shape"] = value_shape_schema(label.get("value"))
            elif kind == "numeric_labels":
                item["value_shape"] = value_shape_schema(label.get("normalized_value"))
            else:
                item["table_column_count"] = len(label.get("headers", []))
            metadata[kind].append(item)
    return metadata


def structure_output_schema(gold: dict[str, Any], pages: set[int] | None = None) -> dict[str, Any]:
    metadata = structure_label_metadata(gold, pages)
    fields = {
        item["id"]: nullable(item["value_shape"])
        for item in metadata["field_labels"]
    }
    numerics = {
        item["id"]: {
            "type": "object",
            "properties": {
                "surface_text": nullable({"type": "string"}),
                "normalized_value": nullable(item["value_shape"]),
                "unit": nullable({"type": "string"}),
            },
            "required": ["surface_text", "normalized_value", "unit"],
            "additionalProperties": False,
        }
        for item in metadata["numeric_labels"]
    }
    tables = {}
    for item in metadata["table_labels"]:
        column_count = item["table_column_count"]
        row = {"type": "array", "items": {"type": "string"}, "minItems": column_count, "maxItems": column_count}
        tables[item["id"]] = nullable(
            {
                "type": "object",
                "properties": {"headers": row, "rows": {"type": "array", "items": row}},
                "required": ["headers", "rows"],
                "additionalProperties": False,
            }
        )
    categories = {"field_labels": fields, "numeric_labels": numerics, "table_labels": tables}
    return {
        "type": "object",
        "properties": {
            kind: {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }
            for kind, properties in categories.items()
        },
        "required": list(categories),
        "additionalProperties": False,
    }


def validate_json_shape(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate_json_shape(value, option, path)
                return
            except (TypeError, ValueError):
                pass
        raise TypeError(f"schema anyOf 불일치: {path}")
    expected = schema.get("type")
    valid = {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, True)
    if not valid:
        raise TypeError(f"schema type 불일치: {path} expected={expected}")
    if expected == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"array 길이 불일치: {path}")
        for index, item in enumerate(value):
            validate_json_shape(item, schema.get("items", {}), f"{path}[{index}]")
    if expected == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ValueError(f"필수 key 누락: {path} {sorted(missing)}")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"허용되지 않은 key: {path} {sorted(set(value) - set(properties))}")
        for key, item in value.items():
            if key in properties:
                validate_json_shape(item, properties[key], f"{path}.{key}")


def structure_fingerprint(
    model: str, normalized_sha256: str, schema: dict[str, Any], prompt: str
) -> dict[str, Any]:
    components = {
        "provider": "openai",
        "model": model,
        "config": {"store": False, "strict": True},
        "prompt_sha256": sha256_value(prompt),
        "normalized_sha256": normalized_sha256,
        "schema_sha256": sha256_value(schema),
    }
    return {"fingerprint": sha256_value(components), "components": components}


def validate_cache(path: Path, expected_fingerprint: str, validator: Any) -> dict[str, Any]:
    if not path.is_file():
        return {"hit": False, "reason": "missing"}
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"hit": False, "reason": "invalid_json"}
    if not isinstance(entry, dict) or entry.get("status") != "succeeded":
        return {"hit": False, "reason": "incomplete_status"}
    if entry.get("request_fingerprint") != expected_fingerprint:
        return {"hit": False, "reason": "fingerprint_mismatch"}
    try:
        validator(entry)
    except (KeyError, TypeError, ValueError) as error:
        return {"hit": False, "reason": f"invalid_payload:{type(error).__name__}"}
    return {"hit": True, "reason": "validated", "entry": entry}


class ItemRunError(RuntimeError):
    def __init__(
        self,
        item_id: str,
        error: Exception,
        fingerprint: str | None = None,
        page_num: int | None = None,
        cache_hits: int = 0,
        created_calls: int = 0,
    ) -> None:
        super().__init__(f"{type(error).__name__}: {error}")
        self.item_id = item_id
        self.original = error
        self.fingerprint = fingerprint
        self.page_num = page_num
        self.cache_hits = cache_hits
        self.created_calls = created_calls


class UpstagePageError(ValueError):
    def __init__(self, message: str, page_num: int | None = None) -> None:
        super().__init__(message)
        self.page_num = page_num


def status_paths(run_root: Path, item_id: str, engine: str) -> tuple[Path, Path]:
    safe_id = re.sub(r"[^0-9A-Za-z._-]+", "__", item_id)
    root = run_root / "status" / engine / safe_id
    return root / "current.json", root / "attempts"


def record_current_status(run_root: Path, item_id: str, engine: str, state: dict[str, Any]) -> dict[str, Any]:
    current_path, attempts_root = status_paths(run_root, item_id, engine)
    state = {"item_id": item_id, "engine": engine, **state, "recorded_at": utc_now()}
    attempt_path = attempts_root / f"{time.time_ns()}.json"
    atomic_write_new_json(attempt_path, state)
    atomic_write_json(current_path, state)
    return state


def record_failure(
    run_root: Path,
    item_id: str,
    engine: str,
    error: Exception,
    fingerprint: str | None = None,
    page_num: int | None = None,
    cache_hits: int = 0,
    created_calls: int = 0,
) -> dict[str, Any]:
    original = error.original if isinstance(error, ItemRunError) else error
    return record_current_status(
        run_root,
        item_id,
        engine,
        {
            "status": "failed",
            "attempt": MAX_ATTEMPTS,
            "request_fingerprint": fingerprint,
            "page_num": page_num,
            "cache_hits": cache_hits,
            "created_calls": created_calls,
            "error_type": type(original).__name__,
            "error_message": str(original),
        },
    )


def record_success(run_root: Path, item_id: str, engine: str, result: dict[str, Any]) -> dict[str, Any]:
    current_path, _ = status_paths(run_root, item_id, engine)
    previous = json.loads(current_path.read_text(encoding="utf-8")) if current_path.is_file() else None
    status = "recovered" if previous and previous.get("status") in {"failed", "recovered"} else "succeeded"
    return record_current_status(run_root, item_id, engine, {"status": status, **result})


def card_records() -> list[dict[str, Any]]:
    records = []
    critical_rules = ROOT / "data/ocr_benchmark/gold/critical_rules/critical_rules_v2.json"
    if not isinstance(json.loads(critical_rules.read_text(encoding="utf-8")), dict):
        raise ValueError("critical_rules_v2.json은 JSON 객체여야 합니다.")
    for issuer, card_name in CARD_SPECS:
        pdf_path = ROOT / "data/raw" / issuer / f"{card_name}.pdf"
        gold_raw = ROOT / "data/ocr_benchmark/gold/raw" / issuer / f"{card_name}.txt"
        gold_structured = ROOT / "data/ocr_benchmark/gold/structured" / issuer / f"{card_name}.json"
        if not pdf_path.is_file():
            raise FileNotFoundError(pdf_path)
        if not gold_raw.read_text(encoding="utf-8").strip():
            raise ValueError(f"빈 gold raw: {gold_raw}")
        if not isinstance(json.loads(gold_structured.read_text(encoding="utf-8")), dict):
            raise ValueError(f"gold structured는 JSON 객체여야 합니다: {gold_structured}")
        with fitz.open(pdf_path) as document:
            page_count = len(document)
        records.append(
            {
                "issuer": issuer,
                "card_name": card_name,
                "key": f"{issuer}/{card_name}",
                "pdf_path": pdf_path,
                "pdf_sha256": sha256_file(pdf_path),
                "page_count": page_count,
            }
        )
    if len(records) != MAX_DOCUMENTS or sum(card["page_count"] for card in records) != MAX_PAGES:
        raise ValueError("필수 입력은 10개 문서, 총 50페이지여야 합니다.")
    return records


def select_cards(records: list[dict[str, Any]], requested: str | None) -> list[dict[str, Any]]:
    if not requested:
        return records
    names = {item.strip() for item in requested.split(",") if item.strip()}
    selected = [card for card in records if card["key"] in names or card["card_name"] in names]
    if len(selected) != len(names):
        matched = {card["key"] for card in selected} | {card["card_name"] for card in selected}
        raise ValueError(f"알 수 있는 --cards 값: {sorted(names - matched)}")
    return selected


def select_engines(requested: str | None) -> tuple[str, ...]:
    if requested is None:
        return ENGINES
    names = tuple(dict.fromkeys(item.strip() for item in requested.split(",") if item.strip()))
    if not names:
        raise ValueError("--engines에는 하나 이상의 엔진이 필요합니다.")
    unknown = set(names) - set(ENGINES)
    if unknown:
        raise ValueError(f"알 수 없는 --engines 값: {sorted(unknown)}")
    return names


def dry_run_plan(cards: list[dict[str, Any]], engines: tuple[str, ...]) -> dict[str, Any]:
    pages = sum(card["page_count"] for card in cards)
    openai_calls = pages * sum(engine in OPENAI_MODELS for engine in engines)
    upstage_calls = len(cards) if "upstage" in engines else 0
    future_structuring = len(cards) * len(engines)
    return {
        "documents": len(cards),
        "pages": pages,
        "engines": list(engines),
        "ocr_calls": {"openai_pages": openai_calls, "upstage_documents": upstage_calls},
        "ocr_total_calls": openai_calls + upstage_calls,
        "structure_calls": future_structuring,
        "full_pipeline_total": openai_calls + upstage_calls + future_structuring,
        "max_attempts": MAX_ATTEMPTS,
    }


def render_page(pdf_path: Path, page_num: int, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as document:
        document[page_num - 1].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(destination)
    return destination


def openai_request(client: Any, model: str, image_path: Path) -> tuple[dict[str, Any], str]:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": OCR_PROMPT},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": OPENAI_DETAIL},
                ],
            }
        ],
        store=False,
    )
    payload = response.model_dump()
    return payload, canonical_text(response.output_text)


def validate_openai_raw(entry: dict[str, Any]) -> None:
    if not canonical_text(entry["page_text"]):
        raise ValueError("빈 OpenAI page_text")
    if not isinstance(entry["response"], dict):
        raise TypeError("OpenAI raw response가 객체가 아님")


def upstage_request(pdf_path: Path, api_key: str, http: Any = requests) -> dict[str, Any]:
    http_data = {
        "model": UPSTAGE_REQUEST_CONFIG["model"],
        "ocr": UPSTAGE_REQUEST_CONFIG["ocr"],
        "coordinates": json.dumps(UPSTAGE_REQUEST_CONFIG["coordinates"]),
        "output_formats": json.dumps(UPSTAGE_REQUEST_CONFIG["output_formats"], separators=(",", ":")),
    }
    with pdf_path.open("rb") as document:
        response = http.post(
            UPSTAGE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"document": (pdf_path.name, document, "application/pdf")},
            data=http_data,
            timeout=600,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("Upstage response가 JSON 객체가 아닙니다.")
    return payload


def provider_elements(response: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(response.get("elements"), list):
        return response["elements"]
    content = response.get("content")
    if isinstance(content, dict) and isinstance(content.get("elements"), list):
        return content["elements"]
    return []


def upstage_element_text(element: dict[str, Any]) -> str:
    content = element.get("content")
    if isinstance(content, dict):
        return canonical_text(content.get("markdown") or content.get("html") or content.get("text") or "")
    return canonical_text(content or element.get("markdown") or element.get("html") or element.get("text") or "")


def validate_upstage_raw(entry: dict[str, Any]) -> None:
    if not isinstance(entry["response"], dict) or not provider_elements(entry["response"]):
        raise ValueError("Upstage elements가 없습니다.")


def validate_upstage_card_raw(entry: dict[str, Any], expected_pages: int) -> None:
    validate_upstage_raw(entry)
    if entry.get("pages") != expected_pages:
        raise ValueError(f"Upstage usage.pages 불일치: expected={expected_pages}, actual={entry.get('pages')}")
    for element in provider_elements(entry["response"]):
        page_num = upstage_element_page(element)
        if page_num > expected_pages:
            raise UpstagePageError(f"Upstage element page 범위 초과: {page_num}", page_num)


def upstage_element_page(element: dict[str, Any]) -> int:
    value = element.get("page")
    if value is None:
        value = element.get("page_number")
    if value is None:
        raise UpstagePageError(f"Upstage element page가 없음: id={element.get('id')}")
    page_num = int(value)
    if page_num < 1:
        raise UpstagePageError(f"Upstage page는 1-based 양수여야 함: id={element.get('id')}, page={value}", page_num)
    return page_num


def normalize_openai(entries: list[dict[str, Any]], card: dict[str, Any], engine: str) -> dict[str, Any]:
    pages = [
        {
            "page_num": page_num,
            "text": canonical_text(entry["page_text"]),
            "marker_collisions": marker_collisions(entry["page_text"]),
        }
        for page_num, entry in enumerate(entries, start=1)
    ]
    document = {
        "schema_version": "normalized_ocr_v2",
        "provider": "openai",
        "requested_model": OPENAI_MODELS[engine],
        "effective_models": sorted({entry["effective_model"] for entry in entries}),
        "source_pdf": str(card["pdf_path"].relative_to(ROOT)),
        "request_fingerprints": [entry["request_fingerprint"] for entry in entries],
        "pages": pages,
    }
    return validate_normalized(document, card["page_count"])


def normalize_upstage(entry: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    page_parts = {page_num: [] for page_num in range(1, card["page_count"] + 1)}
    coordinates = {page_num: [] for page_num in page_parts}
    for position, element in enumerate(provider_elements(entry["response"])):
        page_num = upstage_element_page(element)
        if page_num not in page_parts:
            raise ValueError(f"Upstage element page 범위 초과: {page_num}")
        text = upstage_element_text(element)
        if text:
            page_parts[page_num].append(text)
        if "coordinates" in element:
            coordinates[page_num].append(
                {"element_position": position, "element_id": element.get("id"), "coordinates": element["coordinates"]}
            )
    pages = []
    for page_num, parts in page_parts.items():
        text = canonical_text("\n\n".join(parts))
        page = {"page_num": page_num, "text": text, "marker_collisions": marker_collisions(text)}
        if coordinates[page_num]:
            page["coordinates"] = coordinates[page_num]
        pages.append(page)
    document = {
        "schema_version": "normalized_ocr_v2",
        "provider": "upstage",
        "requested_model": UPSTAGE_REQUEST_CONFIG["model"],
        "effective_model": entry["effective_model"],
        "source_pdf": str(card["pdf_path"].relative_to(ROOT)),
        "request_fingerprint": entry["request_fingerprint"],
        "pages": pages,
    }
    return validate_normalized(document, card["page_count"])


def save_normalized(run_root: Path, engine: str, card: dict[str, Any], document: dict[str, Any]) -> None:
    json_path = run_root / "normalized" / engine / card["issuer"] / f"{card['card_name']}.json"
    text_path = run_root / "normalized" / engine / card["issuer"] / f"{card['card_name']}.txt"
    text = normalized_to_txt(document)
    if json_path.is_file() and json.loads(json_path.read_text(encoding="utf-8")) != document:
        raise FileExistsError(f"기존 normalized JSON과 불일치: {engine}/{card['key']}")
    if text_path.is_file() and text_path.read_text(encoding="utf-8") != text:
        raise FileExistsError(f"기존 normalized TXT와 불일치: {engine}/{card['key']}")
    if not json_path.exists():
        atomic_write_new_json(json_path, document)
    if not text_path.exists():
        write_new_text(text_path, text)


def run_openai_card(run_root: Path, engine: str, card: dict[str, Any], client: Any, image_root: Path) -> dict[str, Any]:
    model = OPENAI_MODELS[engine]
    entries = []
    cache_hits = 0
    created = 0
    for page_num in range(1, card["page_count"] + 1):
        item_id = f"{card['key']}/page_{page_num:03d}"
        raw_path = run_root / "raw" / engine / card["issuer"] / card["card_name"] / f"page_{page_num:03d}.json"
        fingerprint_value = None
        try:
            image_path = render_page(card["pdf_path"], page_num, image_root / engine / card["issuer"] / card["card_name"] / f"page_{page_num:03d}.png")
            image_hash = sha256_file(image_path)
            fingerprint = request_fingerprint(
                "openai", model, {"detail": OPENAI_DETAIL, "store": False, "pdf_sha256": card["pdf_sha256"]}, OCR_PROMPT, "image", image_hash
            )
            fingerprint_value = fingerprint["fingerprint"]
            cached = validate_cache(raw_path, fingerprint_value, validate_openai_raw)
            if cached["hit"]:
                entries.append(cached["entry"])
                cache_hits += 1
                continue
            if raw_path.exists():
                raise FileExistsError(f"검증되지 않은 raw cache를 덮어쓰지 않습니다: {raw_path} ({cached['reason']})")
            started = time.perf_counter()
            response, page_text = openai_request(client, model, image_path)
            elapsed = round(time.perf_counter() - started, 3)
            if not page_text:
                raise ValueError(f"빈 OpenAI OCR: {item_id}")
            entry = {
                "status": "succeeded",
                "provider": "openai",
                "requested_model": model,
                "effective_model": response.get("model") or model,
                "detail": OPENAI_DETAIL,
                "store": False,
                "prompt_sha256": sha256_value(OCR_PROMPT),
                "image_sha256": image_hash,
                "pdf_sha256": card["pdf_sha256"],
                "request_fingerprint": fingerprint_value,
                "usage": response.get("usage"),
                "elapsed_seconds": elapsed,
                "page_text": page_text,
                "response": response,
            }
            validate_openai_raw(entry)
            atomic_write_new_json(raw_path, entry)
            entries.append(entry)
            created += 1
        except Exception as error:
            raise ItemRunError(item_id, error, fingerprint_value, page_num, cache_hits, created) from error
    save_normalized(run_root, engine, card, normalize_openai(entries, card, engine))
    return {"cache_hits": cache_hits, "created_calls": created, "page_count": card["page_count"]}


def run_upstage_card(run_root: Path, card: dict[str, Any], api_key: str, http: Any = requests) -> dict[str, Any]:
    engine = "upstage"
    raw_path = run_root / "raw" / engine / card["issuer"] / f"{card['card_name']}.json"
    fingerprint = request_fingerprint("upstage", UPSTAGE_REQUEST_CONFIG["model"], UPSTAGE_REQUEST_CONFIG, "", "pdf", card["pdf_sha256"])
    fingerprint_value = fingerprint["fingerprint"]
    validator = lambda entry: validate_upstage_card_raw(entry, card["page_count"])
    cached = validate_cache(raw_path, fingerprint_value, validator)
    if cached["hit"]:
        entry = cached["entry"]
        cache_hits, created = 1, 0
    else:
        if raw_path.exists():
            raise FileExistsError(f"검증되지 않은 raw cache를 덮어쓰지 않습니다: {raw_path} ({cached['reason']})")
        started = time.perf_counter()
        response = upstage_request(card["pdf_path"], api_key, http)
        elapsed = round(time.perf_counter() - started, 3)
        entry = {
            "status": "succeeded",
            "provider": "upstage",
            "requested_model": UPSTAGE_REQUEST_CONFIG["model"],
            "effective_model": response.get("model") or UPSTAGE_REQUEST_CONFIG["model"],
            "config": UPSTAGE_REQUEST_CONFIG,
            "pdf_sha256": card["pdf_sha256"],
            "request_fingerprint": fingerprint_value,
            "usage": response.get("usage"),
            "pages": (response.get("usage") or {}).get("pages"),
            "elapsed_seconds": elapsed,
            "response": response,
        }
        validate_upstage_card_raw(entry, card["page_count"])
        atomic_write_new_json(raw_path, entry)
        cache_hits, created = 0, 1
    save_normalized(run_root, engine, card, normalize_upstage(entry, card))
    return {"cache_hits": cache_hits, "created_calls": created, "page_count": card["page_count"]}


def load_gold_structured(card: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / "data/ocr_benchmark/gold/structured" / card["issuer"] / f"{card['card_name']}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"gold structured는 JSON 객체여야 합니다: {path}")
    return value


def structure_prompt(metadata: dict[str, Any], pages: list[dict[str, Any]]) -> str:
    value_less_contract = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    page_text = "\n\n".join(f"[PAGE {page['page_num']}]\n{page['text']}" for page in pages)
    return f"{STRUCTURE_PROMPT}\n\nVALUE-LESS CONTRACT:\n{value_less_contract}\n\nOCR:\n{page_text}"


def openai_structure_request(client: Any, model: str, prompt: str, schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        text={
            "format": {
                "type": "json_schema",
                "name": "card_field_extraction",
                "schema": schema,
                "strict": True,
            }
        },
        store=False,
        timeout=STRUCTURE_TIMEOUT_SECONDS,
    )
    payload = response.model_dump()
    parsed = json.loads(response.output_text)
    validate_json_shape(parsed, schema)
    return payload, parsed


def context_limit_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(token in message for token in ("context_length", "context window", "maximum context", "too many tokens"))


def page_halves(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(pages) < 2:
        raise ValueError("단일 페이지는 더 분할할 수 없습니다.")
    middle = len(pages) // 2
    return pages[:middle], pages[middle:]


def merge_structure_parts(parts: list[dict[str, Any]], full_schema: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {"field_labels": {}, "numeric_labels": {}, "table_labels": {}}
    for part in parts:
        for kind in merged:
            collisions = set(merged[kind]) & set(part[kind])
            if collisions:
                raise ValueError(f"분할 결과 label 충돌: {kind} {sorted(collisions)}")
            merged[kind].update(part[kind])
    validate_json_shape(merged, full_schema)
    return merged


def validate_structure_raw(entry: dict[str, Any], schema: dict[str, Any]) -> None:
    if not isinstance(entry.get("attempts"), list) or not entry["attempts"]:
        raise ValueError("구조화 attempt metadata가 없습니다.")
    if any(attempt.get("status") not in {"succeeded", "failed"} for attempt in entry["attempts"]):
        raise ValueError("구조화 attempt status가 유효하지 않습니다.")
    if not isinstance(entry.get("responses"), list) or not entry["responses"]:
        raise ValueError("구조화 raw response가 없습니다.")
    if not all(isinstance(part.get("response"), dict) for part in entry["responses"]):
        raise TypeError("구조화 full response가 JSON 객체가 아닙니다.")
    validate_json_shape(entry["result"], schema)


def structure_card(
    run_root: Path,
    engine: str,
    card: dict[str, Any],
    client: Any,
    model: str,
    call_budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    item_id = card["key"]
    normalized_path = run_root / "normalized" / engine / card["issuer"] / f"{card['card_name']}.json"
    raw_path = run_root / "raw/field_extraction" / engine / card["issuer"] / f"{card['card_name']}.json"
    structured_path = run_root / "structured" / engine / card["issuer"] / f"{card['card_name']}.json"
    fingerprint_value = None
    call_count = 0
    call_budget = call_budget or {"used": 0, "limit": MAX_STRUCTURE_CALLS}
    try:
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
        validate_normalized(normalized, card["page_count"])
        gold = load_gold_structured(card)
        full_schema = structure_output_schema(gold)
        metadata = structure_label_metadata(gold)
        prompt = structure_prompt(metadata, normalized["pages"])
        normalized_hash = sha256_file(normalized_path)
        fingerprint = structure_fingerprint(model, normalized_hash, full_schema, prompt)
        fingerprint_value = fingerprint["fingerprint"]
        cached = validate_cache(raw_path, fingerprint_value, lambda entry: validate_structure_raw(entry, full_schema))
        if cached["hit"]:
            entry = cached["entry"]
            cache_hits = 1
        else:
            if raw_path.exists():
                raise FileExistsError(f"검증되지 않은 structure raw cache를 덮어쓰지 않습니다: {raw_path} ({cached['reason']})")
            responses: list[dict[str, Any]] = []
            attempts: list[dict[str, Any]] = []

            def extract_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
                nonlocal call_count
                page_numbers = {page["page_num"] for page in pages}
                part_metadata = structure_label_metadata(gold, page_numbers)
                part_schema = structure_output_schema(gold, page_numbers)
                part_prompt = structure_prompt(part_metadata, pages)
                part_fingerprint = structure_fingerprint(model, normalized_hash, part_schema, part_prompt)
                if call_budget["used"] >= call_budget["limit"]:
                    raise RuntimeError(f"structure call budget exceeded before provider call: {call_budget}")
                started = time.perf_counter()
                call_count += 1
                call_budget["used"] += 1
                attempt_number = call_budget["used"]
                attempt_path = run_root / "raw/field_extraction_attempts" / engine / card["issuer"] / card["card_name"] / f"{time.time_ns()}_attempt_{attempt_number:03d}.json"
                try:
                    response, parsed = openai_structure_request(client, model, part_prompt, part_schema)
                except Exception as error:
                    attempt = {
                        "status": "failed", "attempt_number": attempt_number, "pages": sorted(page_numbers),
                        "request_fingerprint": part_fingerprint["fingerprint"],
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                        "error_type": type(error).__name__, "error_message": str(error),
                    }
                    atomic_write_new_json(attempt_path, attempt)
                    attempts.append({**attempt, "path": display_path(attempt_path)})
                    if context_limit_error(error) and len(pages) > 1:
                        left, right = page_halves(pages)
                        return merge_structure_parts([extract_pages(left), extract_pages(right)], part_schema)
                    raise
                attempt = {
                        "status": "succeeded", "attempt_number": attempt_number,
                        "pages": sorted(page_numbers),
                        "request_fingerprint": part_fingerprint["fingerprint"],
                        "prompt_sha256": sha256_value(part_prompt),
                        "schema_sha256": sha256_value(part_schema),
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                        "usage": response.get("usage"),
                        "effective_model": response.get("model") or model,
                        "response": response,
                    }
                atomic_write_new_json(attempt_path, attempt)
                attempts.append({key: value for key, value in attempt.items() if key != "response"} | {"path": display_path(attempt_path)})
                responses.append(attempt)
                return parsed

            result = extract_pages(normalized["pages"])
            validate_json_shape(result, full_schema)
            entry = {
                "status": "succeeded",
                "schema_version": STRUCTURE_SCHEMA_VERSION,
                "provider": "openai",
                "source_ocr_engine": engine,
                "requested_model": model,
                "effective_models": sorted({part["effective_model"] for part in responses}),
                "store": False,
                "strict": True,
                "normalized_path": display_path(normalized_path),
                "normalized_sha256": normalized_hash,
                "prompt_sha256": sha256_value(prompt),
                "schema_sha256": sha256_value(full_schema),
                "request_fingerprint": fingerprint_value,
                "call_budget": {"limit": call_budget["limit"], "used_after_card": call_budget["used"]},
                "attempts": attempts,
                "responses": responses,
                "result": result,
            }
            validate_structure_raw(entry, full_schema)
            atomic_write_new_json(raw_path, entry)
            cache_hits = 0

        structured = {
            "schema_version": STRUCTURE_SCHEMA_VERSION,
            "source_ocr_engine": engine,
            "issuer": card["issuer"],
            "card_name": card["card_name"],
            "requested_model": entry["requested_model"],
            "effective_models": entry["effective_models"],
            "request_fingerprint": fingerprint_value,
            "normalized_sha256": entry["normalized_sha256"],
            "result": entry["result"],
        }
        if structured_path.is_file() and json.loads(structured_path.read_text(encoding="utf-8")) != structured:
            raise FileExistsError(f"기존 structured 결과와 불일치: {engine}/{item_id}")
        if not structured_path.exists():
            atomic_write_new_json(structured_path, structured)
        return {"cache_hits": cache_hits, "created_calls": call_count, "page_count": card["page_count"]}
    except Exception as error:
        raise ItemRunError(item_id, error, fingerprint_value, None, 0, call_count) from error


def structure_plan(cards: list[dict[str, Any]], engines: tuple[str, ...], run_root: Path) -> dict[str, Any]:
    expected = [
        run_root / "normalized" / engine / card["issuer"] / f"{card['card_name']}.json"
        for engine in engines
        for card in cards
    ]
    return {
        "stage": "structure",
        "documents": len(cards),
        "engines": list(engines),
        "model": FIELD_EXTRACTION_MODEL,
        "structure_calls": len(expected),
        "max_structure_calls": MAX_STRUCTURE_CALLS,
        "timeout_seconds": STRUCTURE_TIMEOUT_SECONDS,
        "sdk_max_retries": STRUCTURE_MAX_RETRIES,
        "normalized_inputs": len(expected),
        "missing_normalized_inputs": [path.relative_to(ROOT).as_posix() for path in expected if not path.is_file()],
        "context_limit_policy": "single call first; deterministic page halves only on context-limit error",
        "max_attempts": MAX_ATTEMPTS,
    }


def execute_structure(
    run_root: Path,
    cards: list[dict[str, Any]],
    engines: tuple[str, ...],
    client: Any,
    model: str,
) -> dict[str, Any]:
    results, failures = [], []
    call_budget = {"used": 0, "limit": MAX_STRUCTURE_CALLS}
    for engine in engines:
        for card in cards:
            item_id = card["key"]
            status_engine = f"field_extraction/{engine}"
            try:
                result = structure_card(run_root, engine, card, client, model, call_budget)
                state = record_success(run_root, item_id, status_engine, result)
                results.append({"item_id": item_id, "engine": engine, **result, "status": state["status"]})
            except Exception as error:
                context = error if isinstance(error, ItemRunError) else None
                state = record_failure(
                    run_root,
                    item_id,
                    status_engine,
                    error,
                    context.fingerprint if context else None,
                    context.page_num if context else None,
                    context.cache_hits if context else 0,
                    context.created_calls if context else 0,
                )
                failures.append(
                    {
                        "item_id": item_id,
                        "engine": engine,
                        "request_fingerprint": state["request_fingerprint"],
                        "error_type": state["error_type"],
                        "error_message": state["error_message"],
                        "cache_hits": state["cache_hits"],
                        "created_calls": state["created_calls"],
                    }
                )
                print(f"[FAILED] structure {engine} {item_id}: {state['error_type']}: {state['error_message']}")
    return {
        "successful_card_engines": len(results),
        "failed_card_engines": len(failures),
        "cache_hits": sum(item["cache_hits"] for item in results + failures),
        "created_calls": sum(item["created_calls"] for item in results + failures),
        "max_structure_calls": MAX_STRUCTURE_CALLS,
        "structure_calls_used": call_budget["used"],
        "results": results,
        "failures": failures,
    }


def ensure_run(
    run_root: Path,
    run_id: str,
    cards: list[dict[str, Any]],
    engines: tuple[str, ...],
    all_cards: list[dict[str, Any]] | None = None,
) -> None:
    manifest_path = run_root / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("run_id") != run_id:
            raise ValueError("기존 run manifest의 run_id가 일치하지 않습니다.")
        expected_scope = {"cards": [card["key"] for card in cards], "engines": list(engines)}
        if manifest.get("scope") != expected_scope:
            raise ValueError(f"기존 run scope가 현재 선택과 다릅니다: {manifest.get('scope')}")
        return
    for stage in ("raw", "normalized", "structured", "evaluated", "status"):
        (run_root / stage).mkdir(parents=True, exist_ok=True)
    all_cards = all_cards or cards
    manifest = {
        "schema_version": "clean_end_to_end_ocr_run_v1",
        "run_id": run_id,
        "created_at": utc_now(),
        "scope": {"cards": [card["key"] for card in cards], "engines": list(engines)},
        "plan": dry_run_plan(cards, engines),
        "required_inputs": {
            "cards": [
                {
                    "issuer": card["issuer"],
                    "card_name": card["card_name"],
                    "page_count": card["page_count"],
                    "pdf": file_reference(card["pdf_path"]),
                    "gold_raw": file_reference(ROOT / "data/ocr_benchmark/gold/raw" / card["issuer"] / f"{card['card_name']}.txt"),
                    "gold_structured": file_reference(ROOT / "data/ocr_benchmark/gold/structured" / card["issuer"] / f"{card['card_name']}.json"),
                }
                for card in all_cards
            ],
            "critical_rules": file_reference(ROOT / "data/ocr_benchmark/gold/critical_rules/critical_rules_v2.json"),
        },
        "optional_references": [file_reference(path, optional=True) for path in optional_reference_paths()],
        "coverage_policy": COVERAGE_POLICY,
        "settings": {
            "openai_models": OPENAI_MODELS,
            "openai_detail": OPENAI_DETAIL,
            "openai_store": False,
            "upstage_endpoint": UPSTAGE_URL,
            "upstage_request_config": UPSTAGE_REQUEST_CONFIG,
            "field_extraction_model": FIELD_EXTRACTION_MODEL,
            "max_structure_calls": MAX_STRUCTURE_CALLS,
            "structure_timeout_seconds": STRUCTURE_TIMEOUT_SECONDS,
            "structure_sdk_max_retries": STRUCTURE_MAX_RETRIES,
            "max_attempts": MAX_ATTEMPTS,
        },
    }
    atomic_write_new_json(manifest_path, manifest)


def require_existing_run(run_root: Path, run_id: str) -> dict[str, Any]:
    manifest_path = run_root / "run_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"기존 run manifest가 없습니다: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("run_id") != run_id:
        raise ValueError("기존 run manifest의 run_id가 일치하지 않습니다.")
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notebook 12 staged OCR/structure/evaluate runner (dry-run by default).")
    parser.add_argument("--stage", choices=("ocr", "structure", "evaluate"), default="ocr")
    parser.add_argument("--run-id", help="Run ID (YYYYMMDDTHHMMSSZ); defaults to current UTC time.")
    parser.add_argument("--cards", help="Comma-separated issuer/card or card names; defaults to all 10.")
    parser.add_argument("--engines", help="Explicit comma-separated selection: openai_luna,openai_terra,upstage.")
    parser.add_argument("--live-api", action="store_true", help="ALLOW PAID API calls; also requires explicit --engines.")
    parser.add_argument(
        "--execute-offline",
        action="store_true",
        help="Write offline evaluation outputs; valid only with --stage evaluate.",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Return zero for an explicitly accepted incomplete offline evaluation.",
    )
    parser.add_argument(
        "--recover-stale-lock",
        action="store_true",
        help="Recover only a same-host lock whose PID is confirmed not running.",
    )
    args = parser.parse_args(argv)
    if args.live_api and not args.engines:
        parser.error("--live-api requires an explicit --engines selection")
    if args.live_api and args.stage == "evaluate":
        parser.error("--stage evaluate never accepts --live-api")
    if args.execute_offline and args.stage != "evaluate":
        parser.error("--execute-offline is valid only with --stage evaluate")
    if args.allow_incomplete and not (args.stage == "evaluate" and args.execute_offline):
        parser.error("--allow-incomplete requires --stage evaluate --execute-offline")
    if args.stage in {"structure", "evaluate"} and not args.run_id:
        parser.error("--stage structure/evaluate requires --run-id")
    if args.engines is not None and not any(item.strip() for item in args.engines.split(",")):
        parser.error("--engines requires at least one engine")
    if args.recover_stale_lock and not (args.live_api or args.execute_offline):
        parser.error("--recover-stale-lock requires a live structure/OCR or executed evaluation")
    if args.run_id and not re.fullmatch(r"\d{8}T\d{6}Z", args.run_id):
        parser.error("--run-id must use YYYYMMDDTHHMMSSZ")
    return args


def execute_live(
    run_root: Path,
    cards: list[dict[str, Any]],
    engines: tuple[str, ...],
    openai_client: Any,
    upstage_key: str | None,
    image_root: Path,
    upstage_http: Any = requests,
) -> dict[str, Any]:
    results = []
    failures = []
    for engine in engines:
        for card in cards:
            item_id = card["key"]
            try:
                if engine in OPENAI_MODELS:
                    result = run_openai_card(run_root, engine, card, openai_client, image_root)
                else:
                    result = run_upstage_card(run_root, card, upstage_key, upstage_http)
                state = record_success(run_root, item_id, engine, result)
                results.append({"item_id": item_id, "engine": engine, **result, "status": state["status"]})
            except Exception as error:
                context = error if isinstance(error, ItemRunError) else None
                fingerprint = context.fingerprint if context else None
                page_num = context.page_num if context else getattr(error, "page_num", None)
                partial_cache_hits = context.cache_hits if context else 0
                partial_created = context.created_calls if context else 0
                failed_item = context.item_id if context else item_id
                if engine == "upstage" and fingerprint is None:
                    fingerprint = request_fingerprint(
                        "upstage", UPSTAGE_REQUEST_CONFIG["model"], UPSTAGE_REQUEST_CONFIG, "", "pdf", card["pdf_sha256"]
                    )["fingerprint"]
                state = record_failure(
                    run_root, item_id, engine, error, fingerprint, page_num, partial_cache_hits, partial_created
                )
                failures.append({
                    "item_id": item_id,
                    "failed_item": failed_item,
                    "engine": engine,
                    "page_num": page_num,
                    "request_fingerprint": fingerprint,
                    "error_type": state["error_type"],
                    "error_message": state["error_message"],
                    "cache_hits": partial_cache_hits,
                    "created_calls": partial_created,
                })
                print(f"[FAILED] {engine} {failed_item}: {state['error_type']}: {state['error_message']}")
    return {
        "successful_card_engines": len(results),
        "failed_card_engines": len(failures),
        "cache_hits": sum(result["cache_hits"] for result in results) + sum(item["cache_hits"] for item in failures),
        "created_calls": sum(result["created_calls"] for result in results) + sum(item["created_calls"] for item in failures),
        "results": results,
        "failures": failures,
    }


def finalize_live_summary(
    run_root: Path, run_id: str, plan: dict[str, Any], outcome: dict[str, Any], stage: str = "ocr"
) -> int:
    summary = {"mode": "live-api", "stage": stage, "run_id": run_id, "finished_at": utc_now(), **plan, **outcome}
    summary_path = run_root / ("summary.json" if stage == "ocr" else f"{stage}_summary.json")
    atomic_write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if outcome["failures"] else 0


def execute_evaluation(run_root: Path, engines: tuple[str, ...], cards: list[dict[str, Any]]) -> dict[str, Any]:
    module_path = Path(__file__).with_name("12_clean_end_to_end_evaluator.py")
    spec = importlib.util.spec_from_file_location("clean_end_to_end_evaluator", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"evaluator를 불러올 수 없습니다: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate_run(run_root, engines, cards)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    records = card_records()
    cards = select_cards(records, args.cards)
    engines = select_engines(args.engines)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = OUTPUT_ROOT / run_id
    if args.stage == "structure":
        load_dotenv(ROOT / ".env")
    if args.stage == "ocr":
        plan = {"stage": "ocr", **dry_run_plan(cards, engines)}
        if plan["full_pipeline_total"] > MAX_CALLS:
            raise RuntimeError(f"call plan exceeds MAX_CALLS={MAX_CALLS}: {plan}")
    elif args.stage == "structure":
        plan = structure_plan(cards, engines, run_root)
    else:
        plan = {
            "stage": "evaluate",
            "run_id": run_id,
            "engines": list(engines),
            "cards": [card["key"] for card in cards],
            "provider_calls": 0,
            "outputs": ["evaluation_summary.json", "integrity.csv", "text_metrics.csv", "structured_metrics.csv", "critical_facts.csv"],
        }

    should_execute = args.live_api or (args.stage == "evaluate" and args.execute_offline)
    if not should_execute:
        print(json.dumps({"mode": "dry-run", **plan}, ensure_ascii=False, indent=2))
        return 0

    scope = {"stage": args.stage, "cards": [card["key"] for card in cards], "engines": list(engines)}
    try:
        with RunLock(OUTPUT_ROOT, run_id, scope, args.recover_stale_lock):
            if args.stage == "ocr":
                ensure_run(run_root, run_id, cards, engines, records)
            else:
                require_existing_run(run_root, run_id)
            if args.stage == "evaluate":
                outcome = execute_evaluation(run_root, engines, cards)
                print(json.dumps({
                    "stage": "evaluate",
                    "run_id": run_id,
                    "status": outcome.get("status"),
                    "expected_structured_documents": outcome.get("expected_structured_documents"),
                    "available_structured_documents": outcome.get("available_structured_documents"),
                    "output": (run_root / "evaluated/evaluation_summary.json").relative_to(ROOT).as_posix(),
                }, ensure_ascii=False, indent=2))
                return 0 if outcome.get("status") == "complete" or (outcome.get("status") == "incomplete" and args.allow_incomplete) else 1

            load_dotenv(ROOT / ".env")
            if args.stage == "structure":
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise RuntimeError("OPENAI_API_KEY가 없습니다.")
                structure_client = OpenAI(
                    api_key=api_key,
                    timeout=STRUCTURE_TIMEOUT_SECONDS,
                    max_retries=STRUCTURE_MAX_RETRIES,
                )
                outcome = execute_structure(run_root, cards, engines, structure_client, FIELD_EXTRACTION_MODEL)
                return finalize_live_summary(run_root, run_id, plan, outcome, "structure")

            openai_client = None
            if any(engine in OPENAI_MODELS for engine in engines):
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise RuntimeError("OPENAI_API_KEY가 없습니다.")
                openai_client = OpenAI(api_key=api_key)
            upstage_key = os.getenv("UPSTAGE_API_KEY") if "upstage" in engines else None
            if "upstage" in engines and not upstage_key:
                raise RuntimeError("UPSTAGE_API_KEY가 없습니다.")

            with tempfile.TemporaryDirectory(prefix="pickcardu_ocr_") as temp_dir:
                outcome = execute_live(run_root, cards, engines, openai_client, upstage_key, Path(temp_dir))
            return finalize_live_summary(run_root, run_id, plan, outcome, "ocr")
    except RunLockError as error:
        print(f"[RUN LOCK] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
