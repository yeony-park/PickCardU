from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .pipeline import Indexer, OpenAIEmbeddingAdapter


def json_output(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="pickcardu-indexer")
    command.add_argument("--runtime-root", type=Path, default=Path("data/rag/runtime"))
    subcommands = command.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run")
    run.add_argument("--source-manifest", type=Path, required=True)
    run.add_argument("--luna-json-dir", type=Path)
    run.add_argument("--upstage-json-dir", type=Path)
    run.add_argument("--fake-vectors", action="store_true", help="explicit test-only deterministic vectors")
    run.add_argument("--allow-partial", action="store_true")
    run.add_argument(
        "--profile",
        choices=("card_page_section_benefit", "parent_child_bundle"),
        default="card_page_section_benefit",
    )
    run.add_argument("--confirm-luna", action="store_true")
    run.add_argument("--confirm-upstage", action="store_true")
    run.add_argument(
        "--confirm-embedding",
        action="store_true",
        help="explicitly allow retrieval_text to be sent to OpenAI text-embedding-3-small",
    )

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
    resolve.add_argument("--luna-json-dir", type=Path, required=True)
    resolve.add_argument("--upstage-json-dir", type=Path, required=True)

    activate = subcommands.add_parser("activate")
    activate.add_argument("release_id")
    rollback = subcommands.add_parser("rollback")
    rollback.add_argument("release_id")
    return command


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    indexer = Indexer(arguments.runtime_root)
    try:
        if arguments.command == "run":
            if arguments.confirm_luna or arguments.confirm_upstage:
                raise RuntimeError("live OCR provider adapters are intentionally unavailable; inject local lane artifacts instead")
            if not (arguments.luna_json_dir and arguments.upstage_json_dir):
                raise RuntimeError("both local Luna and Upstage artifact directories are required")
            if arguments.fake_vectors and arguments.confirm_embedding:
                raise RuntimeError("fake vectors and approved external embedding are mutually exclusive")
            api_key = os.environ.get("OPENAI_API_KEY")
            if arguments.confirm_embedding and not api_key:
                raise RuntimeError("OPENAI_API_KEY is required for approved document embedding")
            embedding_adapter = (
                OpenAIEmbeddingAdapter(api_key=api_key)
                if arguments.confirm_embedding
                else None
            )
            config = {
                "profile": arguments.profile,
                "fake_vectors": arguments.fake_vectors,
                "allow_partial": arguments.allow_partial,
                "embedding_model": embedding_adapter.model if embedding_adapter else None,
                "embedding_dimension": embedding_adapter.dimension if embedding_adapter else None,
            }
            result = indexer.run(
                arguments.source_manifest,
                arguments.luna_json_dir,
                arguments.upstage_json_dir,
                fake_vectors=arguments.fake_vectors,
                allow_partial=arguments.allow_partial,
                config=config,
                embedding_adapter=embedding_adapter,
            )
            json_output(result)
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
