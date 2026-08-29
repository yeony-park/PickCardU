"""Run the 10-card semantic OCR repeatability experiment for notebook 09.

Each run evaluates API Luna and Terra with detail=original. OCR page responses,
OCR text, structured predictions, and fact-level results remain under
notebooks/data/09_core_numeric_condition_ocr_evaluation/runs/<run_id>/.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from dotenv import load_dotenv
from openai import OpenAI


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def schema_for(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str) or value is None:
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": nullable(schema_for(value[0]) if value else {"type": "string"})}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {key: nullable(schema_for(item)) for key, item in value.items()},
            "required": list(value),
            "additionalProperties": False,
        }
    raise TypeError(f"Unsupported schema value: {type(value)!r}")


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool) and isinstance(actual, (int, float)) and not isinstance(actual, bool):
        return abs(float(expected) - float(actual)) < 1e-12
    if isinstance(expected, str) and isinstance(actual, str):
        return normalized_text(expected) == normalized_text(actual)
    if isinstance(expected, list) and isinstance(actual, list):
        remaining = list(actual)
        for item in expected:
            found = next((index for index, candidate in enumerate(remaining) if equal(item, candidate)), None)
            if found is None:
                return False
            remaining.pop(found)
        return not remaining
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(expected) == set(actual) and all(equal(value, actual[key]) for key, value in expected.items())
    return expected == actual


def align(expected: Any, actual: Any) -> Any:
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: align(value, actual.get(key)) for key, value in expected.items()}
    return actual


def leaves(value: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return [leaf for key, item in value.items() for leaf in leaves(item, f"{path}.{key}".strip("."))]
    if isinstance(value, list):
        return [leaf for index, item in enumerate(value) for leaf in leaves(item, f"{path}[{index}]")]
    return [(path, value)]


def build_cards(root: Path) -> list[dict[str, Any]]:
    gold = read_json(root / "data/ocr_benchmark/gold/critical_rules/critical_rules_v1.json")
    cards = []
    for card in gold["cards"]:
        pdf = root / "data/raw" / card["issuer"] / f"{card['card_name']}.pdf"
        if not pdf.exists():
            raise FileNotFoundError(pdf)
        cards.append({**card, "pdf_path": pdf})
    return cards


def page_image(pdf_path: Path, page_num: int, destination: Path) -> Path:
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as document:
        pixmap = document[page_num - 1].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        pixmap.save(destination)
    return destination


def page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as document:
        return len(document)


def ocr_page(client: OpenAI, model: str, image_path: Path) -> dict[str, Any]:
    image = base64.b64encode(image_path.read_bytes()).decode("ascii")
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": "카드 안내 PDF 페이지의 모든 텍스트를 읽기 순서대로 전사하세요. 표는 Markdown 표로 작성하고, 요약이나 추론은 하지 마세요."},
            {"type": "input_image", "image_url": f"data:image/png;base64,{image}", "detail": "original"},
        ]}],
        store=False,
    )
    payload = response.model_dump()
    payload["page_text"] = response.output_text.strip()
    return payload


def selected_text(page_outputs: dict[int, str], card: dict[str, Any]) -> str:
    selected = sorted({fact["page_num"] for fact in card["facts"] if isinstance(fact.get("page_num"), int)})
    missing = [page for page in selected if page not in page_outputs]
    if missing:
        raise ValueError(f"{card['issuer']}/{card['card_name']}: missing OCR pages {missing}")
    return "\n\n".join(f"[PAGE {page}]\n{page_outputs[page]}" for page in selected)


def vision_pages(path: Path) -> dict[int, str]:
    pages: dict[int, str] = {}
    for chunk in re.split(r"(?=^\[PAGE\s+\d+\])", path.read_text(encoding="utf-8"), flags=re.MULTILINE):
        match = re.match(r"^\[PAGE\s+(\d+)\]\s*", chunk)
        if match:
            pages[int(match.group(1))] = chunk
    return pages


def upstage_pages(path: Path) -> dict[int, str]:
    pages: dict[int, str] = {}
    for page in read_json(path).get("pages", []):
        blocks = [block.get("text", "") for block in page.get("blocks", [])]
        tables = [table.get("content", "") for table in page.get("tables", [])]
        pages[int(page["page_num"])] = "\n".join(value for value in [*blocks, *tables] if value)
    return pages


def extraction_schema(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {fact["fact_id"]: nullable(schema_for(fact["expected"])) for fact in card["facts"]},
        "required": [fact["fact_id"] for fact in card["facts"]],
        "additionalProperties": False,
    }


def extract_facts(client: OpenAI, extraction_model: str, card: dict[str, Any], text: str) -> dict[str, Any]:
    guide = [{key: fact.get(key) for key in ("fact_id", "field_id", "page_num", "context_terms")} for fact in card["facts"]]
    prompt = (
        "아래 OCR 원문에서 지정된 카드 혜택 사실만 JSON으로 추출하세요. OCR 원문에 명시된 내용만 사용하고 "
        "추론, 외부지식, OCR 오탈자 보정은 하지 마세요. 대상·조건·수치·단위의 근거가 부족하면 해당 fact를 null로 반환하세요. "
        "숫자는 스키마 타입에 맞게 의미값으로 정규화하세요(예: 2%는 0.02, 금액은 원 단위 정수). "
        "정답값은 제공되지 않으며 context_terms는 위치 힌트일 뿐입니다.\n\n"
        f"카드: {card['issuer']} / {card['card_name']}\n추출 대상: {json.dumps(guide, ensure_ascii=False)}\n\nOCR 원문:\n{text}"
    )
    response = client.responses.create(
        model=extraction_model,
        input=prompt,
        text={"format": {"type": "json_schema", "name": "semantic_ocr_fact_prediction", "strict": True, "schema": extraction_schema(card)}},
        store=False,
    )
    return json.loads(response.output_text)


def evaluate(cards: list[dict[str, Any]], config_id: str, prediction_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for card in cards:
        prediction = read_json(prediction_root / card["issuer"] / f"{card['card_name']}.json")["predictions"]
        for fact in card["facts"]:
            expected = fact["expected"]
            actual = align(expected, prediction.get(fact["fact_id"]))
            expected_leaves = leaves(expected)
            actual_by_path = dict(leaves(actual)) if isinstance(actual, (dict, list)) else {}
            numbers = [(path, value) for path, value in expected_leaves if isinstance(value, (int, float)) and not isinstance(value, bool)]
            strings = [(path, value) for path, value in expected_leaves if isinstance(value, str)]
            unit_expected = expected.get("unit") if isinstance(expected, dict) and "unit" in expected else None
            unit_actual = actual.get("unit") if isinstance(actual, dict) else None
            details.append({
                "engine": config_id, "issuer": card["issuer"], "card_name": card["card_name"], "fact_id": fact["fact_id"],
                "fact_kind": fact["fact_kind"], "page_num": fact.get("page_num"),
                "status": "null_prediction" if actual is None else ("matched" if equal(expected, actual) else "mismatched"),
                "fact_exact": int(equal(expected, actual)), "numeric_leaves": len(numbers),
                "numeric_correct": sum(equal(value, actual_by_path.get(path)) for path, value in numbers),
                "string_leaves": len(strings), "string_correct": sum(equal(value, actual_by_path.get(path)) for path, value in strings),
                "unit_available": int(unit_expected is not None), "unit_correct": int(unit_expected is not None and equal(unit_expected, unit_actual)),
                "expected": json.dumps(expected, ensure_ascii=False), "actual": json.dumps(actual, ensure_ascii=False),
            })
    total = len(details)
    return details, {
        "engine": config_id, "facts": total,
        "fact_exact_match_rate": sum(row["fact_exact"] for row in details) / total,
        "null_prediction_rate": sum(row["status"] == "null_prediction" for row in details) / total,
        "numeric_leaf_accuracy": sum(row["numeric_correct"] for row in details) / max(1, sum(row["numeric_leaves"] for row in details)),
        "string_leaf_accuracy": sum(row["string_correct"] for row in details) / max(1, sum(row["string_leaves"] for row in details)),
        "unit_accuracy": sum(row["unit_correct"] for row in details) / max(1, sum(row["unit_available"] for row in details)),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def aggregate(runs_root: Path) -> None:
    summaries = []
    for path in sorted(runs_root.glob("*/summary.json")):
        summary = read_json(path)
        if summary.get("experiment") == "semantic_api_original_repeatability_v1":
            summaries.append(summary)
    metrics = ("fact_exact_match_rate", "numeric_leaf_accuracy", "string_leaf_accuracy", "unit_accuracy", "null_prediction_rate")
    configs = sorted({item["engine"] for summary in summaries for item in summary.get("results", [])})
    result = []
    for config in configs:
        rows = [item for summary in summaries for item in summary["results"] if item["engine"] == config]
        entry = {"engine": config, "runs": len(rows)}
        for metric in metrics:
            values = [row[metric] for row in rows]
            entry[metric] = {"mean": statistics.mean(values), "stddev_population": statistics.pstdev(values), "min": min(values), "max": max(values)}
        result.append(entry)
    write_json(runs_root.parent / "api_original_repeatability_summary.json", {
        "experiment": "semantic_api_original_repeatability_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_ids": [summary["run_id"] for summary in summaries],
        "results": result,
    })


def run_baselines(root: Path, run_id: str) -> None:
    output_root = root / "notebooks/data/09_core_numeric_condition_ocr_evaluation"
    run_root = output_root / "runs" / run_id
    load_dotenv(root / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 없습니다.")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    cards = build_cards(root)
    extraction_model = os.getenv("FIELD_EXTRACTION_MODEL", "gpt-5.4-mini")
    sources = {
        "baseline_upstage_document_parse": {
            "model": "document-parse", "loader": lambda card: upstage_pages(root / "data/ocr_benchmark/normalized/upstage" / card["issuer"] / f"{card['card_name']}.json"),
        },
    }
    write_json(run_root / "run_manifest.json", {
        "experiment": "semantic_api_original_repeatability_v1", "run_id": run_id,
        "source": "existing_ocr_raw_text", "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline_engines": {key: value["model"] for key, value in sources.items()}, "field_extraction_model": extraction_model,
    })
    rows, errors, results = [], [], []
    for config_id, source in sources.items():
        for card in cards:
            prediction_path = run_root / "predictions" / config_id / card["issuer"] / f"{card['card_name']}.json"
            try:
                if prediction_path.exists():
                    prediction, status = read_json(prediction_path), "cached"
                else:
                    pages = source["loader"](card)
                    prediction = {"predictions": extract_facts(client, extraction_model, card, selected_text(pages, card)), "config": config_id, "ocr_model": source["model"], "field_extraction_model": extraction_model}
                    write_json(prediction_path, prediction)
                    status = "created"
                rows.append({"config": config_id, "issuer": card["issuer"], "card_name": card["card_name"], "status": status, "path": str(prediction_path.relative_to(root))})
            except Exception as exc:
                errors.append({"stage": "baseline_field_extraction", "config": config_id, "issuer": card["issuer"], "card_name": card["card_name"], "error": f"{type(exc).__name__}: {exc}"})
        prediction_root = run_root / "predictions" / config_id
        if all((prediction_root / card["issuer"] / f"{card['card_name']}.json").exists() for card in cards):
            details, result = evaluate(cards, config_id, prediction_root)
            write_csv(run_root / "evaluation" / f"{config_id}_fact_details.csv", details)
            results.append(result)
    write_csv(run_root / "field_extraction_manifest.csv", rows)
    write_json(run_root / "errors.json", errors)
    write_json(run_root / "summary.json", {"experiment": "semantic_api_original_repeatability_v1", "run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(), "results": results, "errors": errors})
    aggregate(output_root / "runs")
    print(json.dumps({"run_id": run_id, "results": results, "errors": len(errors)}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=("api", "baseline"), default="api")
    parser.add_argument("--configs", help="Comma-separated API config IDs to resume; defaults to both.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if args.mode == "baseline":
        run_baselines(root, args.run_id)
        return
    output_root = root / "notebooks/data/09_core_numeric_condition_ocr_evaluation"
    run_root = output_root / "runs" / args.run_id
    models = {"api_luna_original": "gpt-5.6-luna", "api_terra_original": "gpt-5.6-terra"}
    if args.configs:
        requested = {item.strip() for item in args.configs.split(",") if item.strip()}
        unknown = requested - set(models)
        if unknown:
            raise ValueError(f"Unknown --configs values: {sorted(unknown)}")
        models = {key: value for key, value in models.items() if key in requested}
    load_dotenv(root / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 없습니다.")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    cards = build_cards(root)
    extraction_model = os.getenv("FIELD_EXTRACTION_MODEL", "gpt-5.4-mini")
    write_json(run_root / "run_manifest.json", {
        "experiment": "semantic_api_original_repeatability_v1", "run_id": args.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "ocr_models": models,
        "image_detail": "original", "reasoning_effort": None, "field_extraction_model": extraction_model,
        "cards": [{"issuer": card["issuer"], "card_name": card["card_name"], "pdf_path": str(card["pdf_path"].relative_to(root)), "page_count": page_count(card["pdf_path"])} for card in cards],
    })

    ocr_rows, extraction_rows, errors, results = [], [], [], []
    for config_id, model in models.items():
        for card in cards:
            texts: dict[int, str] = {}
            required_pages = sorted({fact["page_num"] for fact in card["facts"] if isinstance(fact.get("page_num"), int)})
            for page_num in required_pages:
                raw_path = run_root / "raw" / config_id / card["issuer"] / card["card_name"] / f"page_{page_num:03d}.json"
                try:
                    if raw_path.exists():
                        payload, status = read_json(raw_path), "cached"
                    else:
                        image = page_image(card["pdf_path"], page_num, run_root / "rendered_pages" / card["issuer"] / card["card_name"] / f"page_{page_num:03d}.png")
                        started = time.perf_counter()
                        payload, status = ocr_page(client, model, image), "created"
                        payload["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                        write_json(raw_path, payload)
                    texts[page_num] = payload["page_text"]
                    ocr_rows.append({"config": config_id, "issuer": card["issuer"], "card_name": card["card_name"], "page_num": page_num, "status": status, "path": str(raw_path.relative_to(root))})
                except Exception as exc:
                    errors.append({"stage": "ocr", "config": config_id, "issuer": card["issuer"], "card_name": card["card_name"], "page_num": page_num, "error": f"{type(exc).__name__}: {exc}"})
            text_path = run_root / "ocr_text" / config_id / card["issuer"] / f"{card['card_name']}.txt"
            if texts and all(page in texts for page in required_pages):
                text_path.parent.mkdir(parents=True, exist_ok=True)
                text_path.write_text("\n\n".join(f"[PAGE {page}]\n{texts[page]}" for page in sorted(texts)) + "\n", encoding="utf-8")
                prediction_path = run_root / "predictions" / config_id / card["issuer"] / f"{card['card_name']}.json"
                try:
                    if prediction_path.exists():
                        prediction, status = read_json(prediction_path), "cached"
                    else:
                        prediction, status = {"predictions": extract_facts(client, extraction_model, card, selected_text(texts, card))}, "created"
                        prediction.update({"config": config_id, "ocr_model": model, "field_extraction_model": extraction_model})
                        write_json(prediction_path, prediction)
                    extraction_rows.append({"config": config_id, "issuer": card["issuer"], "card_name": card["card_name"], "status": status, "path": str(prediction_path.relative_to(root))})
                except Exception as exc:
                    errors.append({"stage": "field_extraction", "config": config_id, "issuer": card["issuer"], "card_name": card["card_name"], "error": f"{type(exc).__name__}: {exc}"})
        prediction_root = run_root / "predictions" / config_id
        if all((prediction_root / card["issuer"] / f"{card['card_name']}.json").exists() for card in cards):
            details, result = evaluate(cards, config_id, prediction_root)
            write_csv(run_root / "evaluation" / f"{config_id}_fact_details.csv", details)
            results.append(result)

    write_csv(run_root / "ocr_manifest.csv", ocr_rows)
    write_csv(run_root / "field_extraction_manifest.csv", extraction_rows)
    write_json(run_root / "errors.json", errors)
    write_json(run_root / "summary.json", {
        "experiment": "semantic_api_original_repeatability_v1", "run_id": args.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(), "results": results, "errors": errors,
    })
    aggregate(output_root / "runs")
    print(json.dumps({"run_id": args.run_id, "results": results, "errors": len(errors)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
