from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import pymupdf

from common import (
    ROOT,
    RUNTIME_DIR,
    SourceDocument,
    discover_documents,
    exclusive_run_lock,
    file_sha256,
    read_json,
    value_sha256,
    write_json,
)


OCR_SCRIPT_DIR = ROOT / "scripts" / "ocr_benchmark"
if str(OCR_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(OCR_SCRIPT_DIR))

from run_openai_ocr_benchmark import (  # noqa: E402
    DOCUMENT_SCHEMA_PATH,
    PROMPT_PATH,
    condition_by_key,
    document_prompt,
    normalize_document_output,
    run_cli,
    usage_from_raw,
)


MODEL = "gpt-5.6-luna"
REASONING = "max"
DPI = 200
CONDITION_KEY = "cli_luna_max"
PROMPT_VERSION = "yeony-park/v1"
RENDERER = "pymupdf"
BLANK_DETECTOR = "36dpi-gray-all-white-v1"
PARSER_VERSION = "full-corpus-v2"
OUTPUT_DIR = RUNTIME_DIR / "luna_200dpi"
BATCH_DIR = RUNTIME_DIR / "luna_200dpi_batches"
RENDER_LOCKS: dict[str, threading.Lock] = {}
RENDER_LOCKS_GUARD = threading.Lock()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def config(batch_pages: int) -> dict[str, Any]:
    return {
        "provider": "codex_cli",
        "model": MODEL,
        "reasoning": REASONING,
        "dpi": DPI,
        "renderer": RENDERER,
        "renderer_version": pymupdf.VersionBind,
        "blank_detector": BLANK_DETECTOR,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": file_sha256(PROMPT_PATH),
        "output_schema_sha256": file_sha256(DOCUMENT_SCHEMA_PATH),
        "parser_version": PARSER_VERSION,
        "batch_pages": batch_pages,
    }


def output_path(document: SourceDocument) -> Path:
    return OUTPUT_DIR / document.issuer / f"{document.card_name}.json"


def batch_path(document: SourceDocument, page_start: int, page_end: int) -> Path:
    return BATCH_DIR / document.issuer / document.card_name / f"pages_{page_start:04d}_{page_end:04d}.json"


def valid_pages(pages: Any, expected_numbers: list[int]) -> bool:
    if not isinstance(pages, list) or len(pages) != len(expected_numbers):
        return False
    numbers = [page.get("page_num") for page in pages if isinstance(page, dict)]
    if numbers != expected_numbers:
        return False
    return all(
        page.get("status") == "success"
        and isinstance(page.get("markdown"), str)
        and isinstance(page.get("uncertain_spans"), list)
        for page in pages
    )


def complete_artifact(path: Path, document: SourceDocument, config_sha256: str) -> bool:
    if not path.exists():
        return False
    try:
        artifact = read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        artifact.get("run_status") == "completed"
        and artifact.get("source", {}).get("sha256") == document.sha256
        and artifact.get("parser", {}).get("config_sha256") == config_sha256
        and valid_pages(artifact.get("pages"), list(range(1, document.page_count + 1)))
    )


def sum_usage(usages: list[dict[str, Any]]) -> dict[str, int | None]:
    keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
    result: dict[str, int | None] = {}
    for key in keys:
        values = [usage.get(key) for usage in usages]
        result[key] = sum(value for value in values if isinstance(value, int)) if any(isinstance(value, int) for value in values) else None
    return result


def render_lock(key: str) -> threading.Lock:
    with RENDER_LOCKS_GUARD:
        return RENDER_LOCKS.setdefault(key, threading.Lock())


def render_pdf_pymupdf(document: SourceDocument, dpi: int, cache_dir: Path) -> tuple[list[Path], set[int]]:
    destination = cache_dir / f"{RENDERER}-{pymupdf.VersionBind}" / document.issuer / document.card_name
    marker = destination / ".complete.json"
    expected_marker = {
        "source_sha256": document.sha256,
        "page_count": document.page_count,
        "dpi": dpi,
        "renderer": RENDERER,
        "renderer_version": pymupdf.VersionBind,
        "blank_detector": BLANK_DETECTOR,
    }
    with render_lock(str(destination)):
        existing = sorted(destination.glob("page-*.png"))
        if marker.exists() and len(existing) == document.page_count:
            try:
                marker_value = read_json(marker)
                if all(marker_value.get(key) == value for key, value in expected_marker.items()):
                    blank_pages = {int(page_num) for page_num in marker_value.get("blank_pages", [])}
                    return existing, blank_pages
            except (OSError, json.JSONDecodeError):
                pass

        destination.mkdir(parents=True, exist_ok=True)
        for page_path in existing:
            page_path.unlink()
        blank_pages = set()
        with pymupdf.open(document.path) as pdf:
            if pdf.page_count != document.page_count:
                raise RuntimeError(
                    f"PyMuPDF reported {pdf.page_count} pages for {document.document_id}; expected {document.page_count}"
                )
            for page_number, page in enumerate(pdf, start=1):
                preview = page.get_pixmap(dpi=36, colorspace=pymupdf.csGRAY, alpha=False)
                if min(preview.samples, default=255) == 255:
                    blank_pages.add(page_number)
                pixmap = page.get_pixmap(dpi=dpi, alpha=False)
                pixmap.save(destination / f"page-{page_number:04d}.png")

        pages = sorted(destination.glob("page-*.png"))
        if len(pages) != document.page_count:
            raise RuntimeError(f"rendered {len(pages)} pages for {document.document_id}; expected {document.page_count}")
        write_json(marker, {**expected_marker, "blank_pages": sorted(blank_pages)})
        return pages, blank_pages


def load_batch(
    path: Path,
    document: SourceDocument,
    config_sha256: str,
    expected_numbers: list[int],
    images: list[Path] | None = None,
    blank_page_numbers: set[int] | None = None,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        artifact = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(artifact, dict):
        return None
    if (
        not expected_numbers
        or expected_numbers[0] < 1
        or expected_numbers[-1] > document.page_count
        or expected_numbers != list(range(expected_numbers[0], expected_numbers[-1] + 1))
    ):
        return None
    if not (
        artifact.get("document_id") == document.document_id
        and artifact.get("source_sha256") == document.sha256
        and artifact.get("config_sha256") == config_sha256
        and artifact.get("page_start") == expected_numbers[0]
        and artifact.get("page_end") == expected_numbers[-1]
    ):
        return None
    if artifact.get("run_status") == "completed" and valid_pages(artifact.get("pages"), expected_numbers):
        return artifact
    if artifact.get("run_status") != "failed" or images is None or blank_page_numbers is None:
        return None
    recovered = recover_failed_batch(artifact, images, expected_numbers, blank_page_numbers)
    if recovered is not None:
        write_json(path, recovered)
        return recovered
    return None


def synthetic_blank_page(page_num: int) -> dict[str, Any]:
    return {
        "page_num": page_num,
        "status": "success",
        "is_blank": True,
        "markdown": "",
        "uncertain_spans": [],
    }


def visually_uniform_image(path: Path, tolerance: int = 1) -> bool:
    source = pymupdf.Pixmap(path)
    grayscale = pymupdf.Pixmap(pymupdf.csGRAY, source)
    return max(grayscale.samples, default=255) - min(grayscale.samples, default=255) <= tolerance


def recover_failed_batch(
    artifact: dict[str, Any],
    images: list[Path],
    expected_numbers: list[int],
    blank_page_numbers: set[int],
) -> dict[str, Any] | None:
    if artifact.get("run_status") != "failed" or len(images) != len(expected_numbers):
        return None
    inference_inputs = [
        (page_num, image)
        for page_num, image in zip(expected_numbers, images, strict=True)
        if page_num not in blank_page_numbers
    ]
    try:
        normalized = normalize_document_output(artifact.get("output"), len(inference_inputs))
        pages = normalized["pages"]
        for page, (page_num, image) in zip(pages, inference_inputs, strict=True):
            page["page_num"] = page_num
            page["is_blank"] = not page["markdown"].strip() and visually_uniform_image(image)
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        return None

    synthetic_numbers = sorted(set(expected_numbers) & blank_page_numbers)
    pages.extend(synthetic_blank_page(page_num) for page_num in synthetic_numbers)
    pages.sort(key=lambda page: page["page_num"])
    if not valid_pages(pages, expected_numbers):
        return None

    recovered = dict(artifact)
    recovered["run_status"] = "completed"
    recovered["pages"] = pages
    recovered["synthetic_blank_pages"] = synthetic_numbers
    recovered["recovered_from_failed_artifact"] = {
        "recovered_at": now(),
        "error": artifact.get("error"),
    }
    recovered.pop("error", None)
    return recovered


def run_batch(
    document: SourceDocument,
    images: list[Path],
    page_start: int,
    blank_page_numbers: set[int],
    config_sha256: str,
    timeout: int,
    max_attempts: int,
    force: bool,
) -> dict[str, Any]:
    page_end = page_start + len(images) - 1
    expected_numbers = list(range(page_start, page_end + 1))
    destination = batch_path(document, page_start, page_end)
    if not force:
        cached = load_batch(
            destination,
            document,
            config_sha256,
            expected_numbers,
            images,
            blank_page_numbers,
        )
        if cached is not None:
            return cached

    started_at = now()
    started = time.perf_counter()
    inference_inputs = [
        (page_num, image)
        for page_num, image in zip(expected_numbers, images, strict=True)
        if page_num not in blank_page_numbers
    ]
    if not inference_inputs:
        artifact = {
            "schema_version": "1.0",
            "run_status": "completed",
            "document_id": document.document_id,
            "source_sha256": document.sha256,
            "config_sha256": config_sha256,
            "page_start": page_start,
            "page_end": page_end,
            "started_at": started_at,
            "finished_at": now(),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "usage": sum_usage([]),
            "pages": [synthetic_blank_page(page_num) for page_num in expected_numbers],
            "synthetic_blank_pages": expected_numbers,
        }
        write_json(destination, artifact)
        return artifact

    inference_page_numbers = [page_num for page_num, _ in inference_inputs]
    inference_images = [image for _, image in inference_inputs]
    condition = condition_by_key(CONDITION_KEY)
    prompt = document_prompt(len(inference_images), "images")
    last_error = ""
    last_output: dict[str, Any] | None = None
    last_raw: dict[str, Any] | None = None
    attempt_usages: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        try:
            output, raw = run_cli(condition, inference_images, prompt, timeout)
            last_output = output
            last_raw = raw
            attempt_usages.append(usage_from_raw(condition, raw))
            normalized = normalize_document_output(output, len(inference_images))
            pages = normalized["pages"]
            for page, (page_num, image) in zip(pages, inference_inputs, strict=True):
                page["page_num"] = page_num
                page["is_blank"] = not page["markdown"].strip() and visually_uniform_image(image)
            pages.extend(synthetic_blank_page(page_num) for page_num in expected_numbers if page_num in blank_page_numbers)
            pages.sort(key=lambda page: page["page_num"])
            if not valid_pages(pages, expected_numbers):
                raise ValueError(f"batch pages {page_start}-{page_end} contain a failed or empty page")
            elapsed = round(time.perf_counter() - started, 3)
            artifact = {
                "schema_version": "1.0",
                "run_status": "completed",
                "document_id": document.document_id,
                "source_sha256": document.sha256,
                "config_sha256": config_sha256,
                "page_start": page_start,
                "page_end": page_end,
                "started_at": started_at,
                "finished_at": now(),
                "elapsed_seconds": elapsed,
                "usage": sum_usage(attempt_usages),
                "pages": pages,
                "synthetic_blank_pages": sorted(set(expected_numbers) & blank_page_numbers),
                "raw": raw,
            }
            write_json(destination, artifact)
            return artifact
        except Exception as error:
            last_error = f"{type(error).__name__}: {error}"
            if attempt < max_attempts:
                time.sleep(min(30.0, 2**attempt + random.random()))

    failure = {
        "schema_version": "1.0",
        "run_status": "failed",
        "document_id": document.document_id,
        "source_sha256": document.sha256,
        "config_sha256": config_sha256,
        "page_start": page_start,
        "page_end": page_end,
        "started_at": started_at,
        "finished_at": now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "usage": sum_usage(attempt_usages),
        "error": last_error,
    }
    if last_output is not None:
        failure["output"] = last_output
    if last_raw is not None:
        failure["raw"] = last_raw
    write_json(destination, failure)
    return failure


def run_batch_with_page_fallback(
    document: SourceDocument,
    images: list[Path],
    page_start: int,
    blank_page_numbers: set[int],
    config_sha256: str,
    timeout: int,
    max_attempts: int,
    force: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    if len(images) > 1 and not force:
        cached_pages = [
            load_batch(
                batch_path(document, page_start + offset, page_start + offset),
                document,
                config_sha256,
                [page_start + offset],
                [images[offset]],
                blank_page_numbers,
            )
            for offset in range(len(images))
        ]
        if all(cached_pages):
            completed = [artifact for artifact in cached_pages if artifact is not None]
            return completed, completed, {
                "page_start": page_start,
                "page_end": page_start + len(images) - 1,
                "status": "reused_page_fallback",
            }

    primary = run_batch(
        document,
        images,
        page_start,
        blank_page_numbers,
        config_sha256,
        timeout,
        max_attempts,
        force,
    )
    if primary.get("run_status") == "completed" or len(images) == 1:
        return [primary], [primary], None

    page_artifacts = [
        run_batch(
            document,
            [image],
            page_start + offset,
            blank_page_numbers,
            config_sha256,
            timeout,
            max_attempts,
            force,
        )
        for offset, image in enumerate(images)
    ]
    recovery = {
        "page_start": page_start,
        "page_end": page_start + len(images) - 1,
        "status": "page_fallback",
        "original_error": primary.get("error"),
    }
    return page_artifacts, [primary, *page_artifacts], recovery


def process_document(
    document: SourceDocument,
    batch_pages: int,
    timeout: int,
    max_attempts: int,
    force: bool,
) -> dict[str, Any]:
    parser_config = config(batch_pages)
    config_sha256 = value_sha256(parser_config)
    destination = output_path(document)
    if not force and complete_artifact(destination, document, config_sha256):
        return {"status": "skipped", "document_id": document.document_id, "pages": document.page_count}
    cache_dir = Path(tempfile.gettempdir()) / "pickcardu-rag-luna-200dpi" / document.sha256[:16]
    images, blank_page_numbers = render_pdf_pymupdf(document, DPI, cache_dir)
    if len(images) != document.page_count:
        raise RuntimeError(f"rendered {len(images)} pages for {document.document_id}; expected {document.page_count}")

    started_at = now()
    started = time.perf_counter()
    artifacts: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    for offset in range(0, len(images), batch_pages):
        batch_artifacts, batch_attempts, recovery = run_batch_with_page_fallback(
            document,
            images[offset : offset + batch_pages],
            offset + 1,
            blank_page_numbers,
            config_sha256,
            timeout,
            max_attempts,
            force,
        )
        artifacts.extend(batch_artifacts)
        attempts.extend(batch_attempts)
        if recovery is not None:
            recoveries.append(recovery)

    completed_batches = [artifact for artifact in artifacts if artifact.get("run_status") == "completed"]
    pages = sorted((page for artifact in completed_batches for page in artifact["pages"]), key=lambda page: page["page_num"])
    complete = valid_pages(pages, list(range(1, document.page_count + 1)))
    artifact = {
        "schema_version": "2.0",
        "document_id": document.document_id,
        "source": document.as_dict(),
        "parser": {**parser_config, "config_sha256": config_sha256},
        "run_status": "completed" if complete else "partial",
        "started_at": started_at,
        "finished_at": now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "usage": sum_usage([attempt.get("usage", {}) for attempt in attempts]),
        "pages": pages,
        "batch_recoveries": recoveries,
        "failed_batches": [
            {"page_start": batch["page_start"], "page_end": batch["page_end"], "error": batch.get("error")}
            for batch in artifacts
            if batch.get("run_status") != "completed"
        ],
    }
    write_json(destination, artifact)
    return {
        "status": artifact["run_status"],
        "document_id": document.document_id,
        "pages": len(pages),
        "expected_pages": document.page_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse the full PDF corpus with GPT-5.6 Luna at 200 DPI.")
    parser.add_argument("--issuer", action="append", dest="issuers")
    parser.add_argument("--document", action="append", dest="documents")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--batch-pages", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.batch_pages < 1 or args.workers < 1 or args.max_attempts < 1:
        raise SystemExit("--batch-pages, --workers, and --max-attempts must be positive")
    documents = discover_documents(args.issuers, args.documents, args.limit)
    if args.dry_run:
        print(json.dumps({"documents": len(documents), "pages": sum(item.page_count for item in documents), "config": config(args.batch_pages)}, ensure_ascii=False))
        return

    counts = {"completed": 0, "partial": 0, "skipped": 0, "failed": 0}
    with exclusive_run_lock("luna-full-corpus"):
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    process_document,
                    document,
                    args.batch_pages,
                    args.timeout,
                    args.max_attempts,
                    args.force,
                ): document
                for document in documents
            }
            try:
                for future in as_completed(futures):
                    document = futures[future]
                    try:
                        result = future.result()
                    except Exception as error:
                        result = {"status": "failed", "document_id": document.document_id, "error": f"{type(error).__name__}: {error}"}
                    counts[result["status"]] += 1
                    print(json.dumps(result, ensure_ascii=False), flush=True)
            except KeyboardInterrupt:
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=True, cancel_futures=True)
                raise
    print(json.dumps(counts, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
