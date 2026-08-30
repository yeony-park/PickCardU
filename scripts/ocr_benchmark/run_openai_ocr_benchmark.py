from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
PROMPT_PATH = ROOT / "prompts" / "ocr_transcription" / "yeony-park" / "v1.md"
DOCUMENT_SCHEMA_PATH = ROOT / "schemas" / "ocr_benchmark" / "document_output_v1.json"
OUTPUT_DIR = ROOT / "data" / "ocr_benchmark" / "openai"
RAW_OUTPUT_DIR = ROOT / "data" / "ocr_benchmark" / "openai_raw"
API_URL = "https://api.openai.com/v1/responses"

TARGETS = {
    "BC": "BC_Biz_AirMoney.pdf",
    "NH": "NH_Namu_NH.pdf",
    "hana": "Hana_One_More_SOHO.pdf",
    "hyundai": "Hyundai_The_Orange_20260330.pdf",
    "ibk": "IBK_Point3.8(Credit).pdf",
    "kookmin": "Kookmin_Friend_20210917.pdf",
    "lotte": "Lotte_LOCA_LIKIT_Eat.pdf",
    "samsung": "Samsung_iD_ALL.pdf",
    "shinhan": "Shinhan_Toss_Mr.Life_20251231.pdf",
    "woori": "Woori_Classic_EVERY_MILE_SKYPASS.pdf",
}


@dataclass(frozen=True)
class Condition:
    key: str
    surface: str
    model: str
    reasoning: str
    detail: str | None


CONDITIONS = [
    Condition("cli_luna_max", "cli", "gpt-5.6-luna", "max", None),
    Condition("cli_terra_medium", "cli", "gpt-5.6-terra", "medium", None),
    Condition("cli_terra_high", "cli", "gpt-5.6-terra", "high", None),
    Condition("cli_sol_medium", "cli", "gpt-5.6-sol", "medium", None),
    Condition("cli_sol_high", "cli", "gpt-5.6-sol", "high", None),
    Condition("api_luna_max_high", "api", "gpt-5.6-luna", "max", "high"),
]

PRINT_LOCK = threading.Lock()
RENDER_LOCKS: dict[str, threading.Lock] = {}
RENDER_LOCKS_GUARD = threading.Lock()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def condition_by_key(key: str) -> Condition:
    return next(condition for condition in CONDITIONS if condition.key == key)


def strip_json_fence(text: str) -> str:
    source = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", source, flags=re.DOTALL)
    return match.group(1) if match else source


def normalize_page_output(raw: str | dict[str, Any], page_num: int) -> dict[str, Any]:
    value = json.loads(strip_json_fence(raw)) if isinstance(raw, str) else dict(raw)
    result = {
        "page_num": page_num,
        "status": value.get("status", "failed"),
        "markdown": value.get("markdown", ""),
        "uncertain_spans": value.get("uncertain_spans", []),
    }
    if result["status"] not in {"success", "failed"}:
        raise ValueError(f"invalid page status: {result['status']}")
    if not isinstance(result["markdown"], str) or not isinstance(result["uncertain_spans"], list):
        raise ValueError("invalid page output types")
    return result


def normalize_document_output(raw: str | dict[str, Any], page_count: int) -> dict[str, Any]:
    value = json.loads(strip_json_fence(raw)) if isinstance(raw, str) else dict(raw)
    pages = value.get("pages")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise ValueError(f"expected {page_count} pages, received {len(pages) if isinstance(pages, list) else 'invalid'}")
    normalized = [normalize_page_output(page, page_num=index) for index, page in enumerate(pages, start=1)]
    return {"pages": normalized}


def extract_cli_usage(events: list[dict[str, Any]]) -> dict[str, int | None]:
    usage = next((event.get("usage", {}) for event in reversed(events) if event.get("type") == "turn.completed"), {})
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": output_tokens,
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "total_tokens": total_tokens,
    }


def extract_cli_message(events: list[dict[str, Any]]) -> str:
    messages = [
        event["item"].get("text", "")
        for event in events
        if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message"
    ]
    if not messages:
        raise ValueError("Codex CLI did not emit an agent_message")
    return messages[-1]


def extract_api_output_text(response: dict[str, Any]) -> str:
    texts = [
        content.get("text", "")
        for item in response.get("output", [])
        if item.get("type") == "message"
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    ]
    if not texts:
        raise ValueError("Responses API did not emit output_text")
    return "\n".join(texts)


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


def render_lock(key: str) -> threading.Lock:
    with RENDER_LOCKS_GUARD:
        return RENDER_LOCKS.setdefault(key, threading.Lock())


def render_pdf(issuer: str, pdf_path: Path, dpi: int, cache_dir: Path) -> list[Path]:
    destination = cache_dir / issuer / pdf_path.stem
    marker = destination / ".complete"
    with render_lock(str(destination)):
        existing = sorted(destination.glob("page-*.png"))
        if marker.exists() and existing:
            return existing
        destination.mkdir(parents=True, exist_ok=True)
        prefix = destination / "page"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png", str(pdf_path), str(prefix)],
            check=True,
            capture_output=True,
            text=True,
        )
        pages = sorted(destination.glob("page-*.png"))
        if not pages:
            raise RuntimeError(f"no pages rendered for {pdf_path}")
        marker.touch()
        return pages


def document_prompt(page_count: int, input_kind: str) -> str:
    base = PROMPT_PATH.read_text(encoding="utf-8")
    page_schema = re.search(r"다음 JSON 형식만 출력합니다\.[\s\S]*", base)
    if page_schema:
        base = base[: page_schema.start()].rstrip()
    input_description = (
        f"첨부 이미지 자체만 직접 보고 전사하세요. 이미지는 1페이지부터 {page_count}페이지까지 순서대로 첨부되어 있습니다."
        if input_kind == "images"
        else f"첨부 PDF 자체를 직접 보고 전사하세요. PDF는 총 {page_count}페이지입니다."
    )
    return f"""어떤 도구도 호출하지 마세요. shell, 웹 검색, 외부 파일 읽기, 입력 재가공을 하지 마세요.
{input_description}
모든 페이지를 빠짐없이 각각 전사하고 최종 JSON 이외에는 출력하지 마세요.
표지, 뒷표지, 로고만 있는 페이지, 빈 페이지도 독립된 페이지로 세어 반드시 pages 배열에 포함하세요.
pages 배열은 page_num 1부터 {page_count}까지 순서대로 정확히 {page_count}개 객체를 포함해야 합니다. 반환 전에 누락과 중복이 없는지 확인하세요.

{base}

다음 JSON 형식만 출력합니다.
{{
  "pages": [
    {{
      "page_num": 1,
      "status": "success",
      "markdown": "1페이지의 전사된 전체 Markdown",
      "uncertain_spans": [{{"text": "판독이 불확실한 문자열", "reason": "불확실한 이유"}}]
    }}
  ]
}}
"""


def cli_command(condition: Condition, images: list[Path], prompt: str, workdir: Path) -> list[str]:
    return [
        "codex",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--disable",
        "shell_tool",
        "--disable",
        "plugins",
        "--disable",
        "apps",
        "--disable",
        "browser_use",
        "--disable",
        "computer_use",
        "--disable",
        "multi_agent",
        "-C",
        str(workdir),
        "-m",
        condition.model,
        "-c",
        f'model_reasoning_effort="{condition.reasoning}"',
        "--image",
        *[str(image) for image in images],
        "--output-schema",
        str(DOCUMENT_SCHEMA_PATH),
        "--json",
        prompt,
    ]


def run_cli(condition: Condition, images: list[Path], prompt: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="pickcardu-codex-ocr-") as directory:
        completed = subprocess.run(
            cli_command(condition, images, prompt, Path(directory)),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    events = []
    for line in completed.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    raw = {
        "command": {"model": condition.model, "reasoning": condition.reasoning, "image_count": len(images)},
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if completed.returncode != 0:
        raise RuntimeError(f"Codex CLI exited {completed.returncode}: {completed.stderr[-1000:]}")
    output = json.loads(strip_json_fence(extract_cli_message(events)))
    return output, {"events": events, **raw, "usage": extract_cli_usage(events)}


def api_payload(condition: Condition, pdf_path: Path, prompt: str) -> dict[str, Any]:
    schema = json.loads(DOCUMENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    encoded = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    content: list[dict[str, Any]] = [
        {
            "type": "input_file",
            "filename": pdf_path.name,
            "file_data": f"data:application/pdf;base64,{encoded}",
            "detail": condition.detail,
        },
        {"type": "input_text", "text": prompt},
    ]
    return {
        "model": condition.model,
        "reasoning": {"effort": condition.reasoning},
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "ocr_document_transcription",
                "strict": True,
                "schema": schema,
            }
        },
        "store": False,
    }


def api_ssl_context() -> ssl.SSLContext:
    verify_paths = ssl.get_default_verify_paths()
    system_bundle = Path("/etc/ssl/cert.pem")
    cafile = verify_paths.cafile or (str(system_bundle) if system_bundle.is_file() else None)
    return ssl.create_default_context(cafile=cafile)


def run_api(condition: Condition, pdf_path: Path, prompt: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    key = load_env_key("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment or .env")
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(api_payload(condition, pdf_path, prompt), ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=api_ssl_context()) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Responses API HTTP {error.code}: {body[-2000:]}") from error
    output = json.loads(strip_json_fence(extract_api_output_text(raw)))
    return output, raw


def usage_from_raw(condition: Condition, raw: dict[str, Any]) -> dict[str, Any]:
    if condition.surface == "cli":
        return raw.get("usage", {})
    usage = raw.get("usage", {})
    details = usage.get("output_tokens_details", {})
    return {
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("input_tokens_details", {}).get("cached_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


def output_path(base: Path, condition: Condition, issuer: str, pdf_path: Path) -> Path:
    return base / condition.key / issuer / f"{pdf_path.stem}.json"


def is_complete(path: Path, expected_pages: int) -> bool:
    if not path.exists():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return value.get("run_status") == "completed" and len(value.get("pages", [])) == expected_pages


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_task(
    condition: Condition,
    issuer: str,
    pdf_path: Path,
    images: list[Path],
    page_count: int,
    dpi: int,
    timeout: int,
    max_attempts: int,
    force: bool,
) -> dict[str, Any]:
    destination = output_path(OUTPUT_DIR, condition, issuer, pdf_path)
    if not force and is_complete(destination, page_count):
        return {"status": "skipped", "condition": condition.key, "issuer": issuer, "card": pdf_path.stem}

    prompt = document_prompt(page_count, "images" if condition.surface == "cli" else "pdf")
    started_at = now()
    started = time.perf_counter()
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            if condition.surface == "cli":
                output, raw = run_cli(condition, images, prompt, timeout)
            else:
                output, raw = run_api(condition, pdf_path, prompt, timeout)
            raw_destination = output_path(RAW_OUTPUT_DIR, condition, issuer, pdf_path)
            write_json(raw_destination, raw)
            normalized = normalize_document_output(output, page_count)
            elapsed = round(time.perf_counter() - started, 3)
            pages = normalized["pages"]
            usage = usage_from_raw(condition, raw)
            result = {
                "schema_version": "1.0",
                "run_status": "completed",
                "tool": "codex_cli" if condition.surface == "cli" else "openai_responses_api",
                "condition": asdict(condition),
                "dpi": dpi if condition.surface == "cli" else None,
                "input_format": "png_pages" if condition.surface == "cli" else "pdf",
                "issuer": issuer,
                "card_name": pdf_path.stem,
                "source_pdf": str(pdf_path.relative_to(ROOT)),
                "started_at": started_at,
                "finished_at": now(),
                "elapsed_seconds": elapsed,
                "page_count": page_count,
                "usage": usage,
                "pages": pages,
                "metrics": {
                    "processing_success_rate": sum(page["status"] == "success" for page in pages) / page_count,
                    "page_coverage": sum(bool(page["markdown"].strip()) for page in pages) / page_count,
                    "empty_output_rate": sum(not page["markdown"].strip() for page in pages) / page_count,
                    "seconds_per_page": elapsed / page_count,
                    "tokens_per_page": usage.get("total_tokens") / page_count if usage.get("total_tokens") is not None else None,
                    "schema_valid": True,
                },
            }
            write_json(destination, result)
            return {"status": "completed", "condition": condition.key, "issuer": issuer, "card": pdf_path.stem, "elapsed": elapsed}
        except Exception as error:
            last_error = f"{type(error).__name__}: {error}"
            if attempt < max_attempts:
                time.sleep(min(30.0, 2**attempt + random.random()))

    elapsed = round(time.perf_counter() - started, 3)
    failure = {
        "schema_version": "1.0",
        "run_status": "failed",
        "condition": asdict(condition),
        "dpi": dpi if condition.surface == "cli" else None,
        "input_format": "png_pages" if condition.surface == "cli" else "pdf",
        "issuer": issuer,
        "card_name": pdf_path.stem,
        "source_pdf": str(pdf_path.relative_to(ROOT)),
        "started_at": started_at,
        "finished_at": now(),
        "elapsed_seconds": elapsed,
        "page_count": page_count,
        "error": last_error,
    }
    write_json(destination, failure)
    return {"status": "failed", "condition": condition.key, "issuer": issuer, "card": pdf_path.stem, "error": last_error}


def selected_conditions(keys: list[str] | None, surface: str) -> list[Condition]:
    conditions = CONDITIONS
    if surface != "all":
        conditions = [condition for condition in conditions if condition.surface == surface]
    if keys:
        unknown = sorted(set(keys) - {condition.key for condition in CONDITIONS})
        if unknown:
            raise ValueError(f"unknown conditions: {', '.join(unknown)}")
        conditions = [condition for condition in conditions if condition.key in keys]
    return conditions


def build_parser(default_surface: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the OpenAI OCR benchmark in parallel.")
    parser.add_argument("--surface", choices=["all", "cli", "api"], default=default_surface)
    parser.add_argument("--condition", action="append", dest="conditions")
    parser.add_argument("--issuer", action="append", dest="issuers")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def pdf_page_count(pdf_path: Path) -> int:
    result = subprocess.run(["pdfinfo", str(pdf_path)], check=True, capture_output=True, text=True)
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"pdfinfo did not report a page count for {pdf_path}")
    return int(match.group(1))


def main(default_surface: str = "all") -> None:
    args = build_parser(default_surface).parse_args()
    conditions = selected_conditions(args.conditions, args.surface)
    issuers = args.issuers or list(TARGETS)
    unknown_issuers = sorted(set(issuers) - set(TARGETS))
    if unknown_issuers:
        raise ValueError(f"unknown issuers: {', '.join(unknown_issuers)}")
    if not args.dry_run and any(condition.surface == "api" for condition in conditions) and not load_env_key("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for API conditions; set it in the environment or .env")

    cache_dir = Path(tempfile.gettempdir()) / f"pickcardu-openai-ocr-{args.dpi}dpi"
    rendered: dict[str, tuple[Path, list[Path], int]] = {}
    needs_cli_images = any(condition.surface == "cli" for condition in conditions)
    for issuer in issuers:
        pdf_path = RAW_DIR / issuer / TARGETS[issuer]
        images = render_pdf(issuer, pdf_path, args.dpi, cache_dir) if needs_cli_images else []
        page_count = len(images) if images else pdf_page_count(pdf_path)
        rendered[issuer] = (pdf_path, images, page_count)

    tasks = []
    for issuer_index, issuer in enumerate(issuers):
        rotated = conditions[issuer_index % len(conditions) :] + conditions[: issuer_index % len(conditions)]
        for condition in rotated:
            pdf_path, images, page_count = rendered[issuer]
            tasks.append((condition, issuer, pdf_path, images if condition.surface == "cli" else [], page_count))

    if args.dry_run:
        for condition, issuer, pdf_path, images, page_count in tasks:
            source = f"{args.dpi}dpi PNG" if condition.surface == "cli" else "PDF direct, detail=high"
            print(f"{condition.key}\t{issuer}\t{pdf_path.name}\t{page_count} pages\t{source}")
        return

    with PRINT_LOCK:
        print(f"Running {len(tasks)} document tasks with {args.workers} workers at {args.dpi} DPI", flush=True)
    counts = {"completed": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                run_task,
                condition,
                issuer,
                pdf_path,
                images,
                page_count,
                args.dpi,
                args.timeout,
                args.max_attempts,
                args.force,
            )
            for condition, issuer, pdf_path, images, page_count in tasks
        ]
        for future in as_completed(futures):
            result = future.result()
            counts[result["status"]] += 1
            with PRINT_LOCK:
                print(json.dumps(result, ensure_ascii=False), flush=True)
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
