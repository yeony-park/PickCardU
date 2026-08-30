from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
RAG_DIR = ROOT / "data" / "rag"
RUNTIME_DIR = RAG_DIR / "runtime"


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    issuer: str
    card_name: str
    path: Path
    relative_path: str
    sha256: str
    page_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "issuer": self.issuer,
            "card_name": self.card_name,
            "path": self.relative_path,
            "sha256": self.sha256,
            "page_count": self.page_count,
        }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def value_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def pdf_page_count(path: Path) -> int:
    result = subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, text=True)
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"pdfinfo did not report a page count for {path}")
    return int(match.group(1))


def discover_documents(
    issuers: Iterable[str] | None = None,
    names: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[SourceDocument]:
    issuer_filter = set(issuers or [])
    name_filter = set(names or [])
    documents: list[SourceDocument] = []
    for path in sorted(RAW_DIR.glob("*/*.pdf"), key=lambda item: item.as_posix().casefold()):
        issuer = path.parent.name
        document_id = f"{issuer}/{path.stem}"
        if issuer_filter and issuer not in issuer_filter:
            continue
        if name_filter and document_id not in name_filter and path.name not in name_filter and path.stem not in name_filter:
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        documents.append(
            SourceDocument(
                document_id=document_id,
                issuer=issuer,
                card_name=path.stem,
                path=path,
                relative_path=relative_path,
                sha256=file_sha256(path),
                page_count=pdf_page_count(path),
            )
        )
        if limit is not None and len(documents) >= limit:
            break
    return documents


def load_env_key(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    temporary.replace(path)
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@contextmanager
def exclusive_run_lock(name: str):
    import fcntl

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME_DIR / f".{name}.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.seek(0)
            owner = handle.read().strip() or "unknown"
            raise RuntimeError(f"{name} is already running (pid {owner})") from error
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
