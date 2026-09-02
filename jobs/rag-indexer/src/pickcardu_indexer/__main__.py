from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .ocr import LiveLaneAdapter, LunaFactStructurer, LunaOcrTranscriber, UpstageOcrTranscriber
from .pipeline import Indexer, OpenAIEmbeddingAdapter


def json_output(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def add_profile(command: argparse.ArgumentParser) -> None:
    command.add_argument("--profile", choices=("card_page_section_benefit", "parent_child_bundle"), default="card_page_section_benefit")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="pickcardu-indexer")
    command.add_argument("--runtime-root", type=Path, default=Path("data/rag/runtime"))
    subcommands = command.add_subparsers(dest="command", required=True)

    ocr = subcommands.add_parser("ocr", help="run or import dual OCR, structure both lanes, and validate")
    ocr.add_argument("--source-manifest", type=Path, required=True)
    ocr.add_argument("--luna-json-dir", type=Path)
    ocr.add_argument("--upstage-json-dir", type=Path)
    ocr.add_argument("--confirm-luna", action="store_true", help="allow 200 DPI PDF page images and both OCR texts to be sent to OpenAI")
    ocr.add_argument("--confirm-upstage", action="store_true", help="allow source PDFs to be sent to Upstage")
    ocr.add_argument("--luna-model", default="gpt-5.6-luna")
    ocr.add_argument("--luna-reasoning", default="max")
    ocr.add_argument("--structure-model", default="gpt-5.6-luna")
    ocr.add_argument("--structure-reasoning", default="max")

    index = subcommands.add_parser("index", help="build an index release from an OCR run")
    index.add_argument("--run-id", required=True)
    add_profile(index)
    index.add_argument("--confirm-embedding", action="store_true", help="allow retrieval_text to be sent to OpenAI embeddings")
    index.add_argument("--fake-vectors", action="store_true", help="explicit test-only deterministic vectors")
    index.add_argument("--allow-preview", action="store_true", help="build a non-activatable partial preview from approved documents")

    legacy = subcommands.add_parser("run", help="legacy local-fixture OCR+index command")
    legacy.add_argument("--source-manifest", type=Path, required=True)
    legacy.add_argument("--luna-json-dir", type=Path, required=True)
    legacy.add_argument("--upstage-json-dir", type=Path, required=True)
    legacy.add_argument("--fake-vectors", action="store_true")
    legacy.add_argument("--allow-partial", action="store_true")
    add_profile(legacy)
    legacy.add_argument("--confirm-embedding", action="store_true")

    status = subcommands.add_parser("status")
    status.add_argument("--run-id")

    review = subcommands.add_parser("review")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_list = review_commands.add_parser("list")
    review_list.add_argument("--run-id", required=True)
    review_show = review_commands.add_parser("show")
    review_show.add_argument("review_id", type=int)
    resolve = review_commands.add_parser("resolve")
    resolve.add_argument("review_id", type=int)
    resolve.add_argument("--reviewer", required=True)
    resolve.add_argument("--reason", required=True)
    resolve.add_argument("--after-json", type=Path, required=True)
    resolve.add_argument("--luna-json-dir", type=Path)
    resolve.add_argument("--upstage-json-dir", type=Path)

    activate = subcommands.add_parser("activate")
    activate.add_argument("release_id")
    rollback = subcommands.add_parser("rollback")
    rollback.add_argument("release_id")
    return command


def embedding_adapter(arguments: argparse.Namespace) -> OpenAIEmbeddingAdapter | None:
    if arguments.fake_vectors and arguments.confirm_embedding:
        raise RuntimeError("fake vectors and approved external embedding are mutually exclusive")
    if not arguments.confirm_embedding:
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for approved document embedding")
    return OpenAIEmbeddingAdapter(api_key=api_key)


def run_ocr(indexer: Indexer, arguments: argparse.Namespace) -> dict[str, Any]:
    local = bool(arguments.luna_json_dir or arguments.upstage_json_dir)
    live = bool(arguments.confirm_luna or arguments.confirm_upstage)
    if local and live:
        raise RuntimeError("local OCR artifacts and live OCR approvals are mutually exclusive")
    if local:
        if not (arguments.luna_json_dir and arguments.upstage_json_dir):
            raise RuntimeError("both local Luna and Upstage directories are required")
        return indexer.ocr(arguments.source_manifest, arguments.luna_json_dir, arguments.upstage_json_dir, config={"mode": "local_dual_lane_v1"})
    if not (arguments.confirm_luna and arguments.confirm_upstage):
        raise RuntimeError("live OCR requires both --confirm-luna and --confirm-upstage")
    openai_key, upstage_key = os.environ.get("OPENAI_API_KEY"), os.environ.get("UPSTAGE_API_KEY")
    if not openai_key or not upstage_key:
        raise RuntimeError("OPENAI_API_KEY and UPSTAGE_API_KEY are required for approved live OCR")
    luna = LunaOcrTranscriber(openai_key, model=arguments.luna_model, reasoning=arguments.luna_reasoning)
    upstage = UpstageOcrTranscriber(upstage_key)
    structurer = LunaFactStructurer(openai_key, model=arguments.structure_model, reasoning=arguments.structure_reasoning)
    config = {"mode": "live_dual_ocr_v1", "luna": luna.config, "upstage": upstage.config, "structure": structurer.config}

    def providers(_run_id: str, documents: list[dict[str, str]]):
        sources = {row["document_id"]: Path(row["source_pdf"]) for row in documents}
        root = arguments.runtime_root / "ocr-cache"
        return {
            "luna": LiveLaneAdapter("luna", sources, root, luna, structurer),
            "upstage": LiveLaneAdapter("upstage", sources, root, upstage, structurer),
        }

    return indexer.ocr(arguments.source_manifest, None, None, config=config, providers=providers)


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    indexer = Indexer(arguments.runtime_root)
    try:
        if arguments.command == "ocr":
            json_output(run_ocr(indexer, arguments))
        elif arguments.command == "index":
            adapter = embedding_adapter(arguments)
            if not arguments.fake_vectors and adapter is None:
                raise RuntimeError("index requires --confirm-embedding or explicit test-only --fake-vectors")
            json_output(indexer.index(arguments.run_id, allow_preview=arguments.allow_preview, fake_vectors=arguments.fake_vectors, profile=arguments.profile, embedding_adapter=adapter))
        elif arguments.command == "run":
            adapter = embedding_adapter(arguments)
            config = {"profile": arguments.profile, "fake_vectors": arguments.fake_vectors, "allow_partial": arguments.allow_partial, "embedding_model": adapter.model if adapter else None, "embedding_dimension": adapter.dimension if adapter else None}
            json_output(indexer.run(arguments.source_manifest, arguments.luna_json_dir, arguments.upstage_json_dir, fake_vectors=arguments.fake_vectors, allow_partial=arguments.allow_partial, config=config, embedding_adapter=adapter))
        elif arguments.command == "status":
            json_output(indexer.state.status(arguments.run_id))
        elif arguments.command == "review":
            if arguments.review_command == "list":
                json_output([dict(row) for row in indexer.state.reviews(arguments.run_id)])
            elif arguments.review_command == "show":
                json_output(dict(indexer.state.review(arguments.review_id)))
            else:
                json_output(indexer.resolve_review(arguments.review_id, arguments.reviewer, arguments.reason, arguments.after_json, arguments.luna_json_dir, arguments.upstage_json_dir))
        elif arguments.command == "activate":
            json_output({"active_pointer": str(indexer.activate(arguments.release_id))})
        else:
            json_output({"active_pointer": str(indexer.rollback(arguments.release_id))})
    finally:
        indexer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
