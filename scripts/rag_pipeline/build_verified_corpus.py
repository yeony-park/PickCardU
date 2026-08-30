from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from common import RAG_DIR, RUNTIME_DIR, discover_documents, read_json, value_sha256, write_json
from run_luna_parse import complete_artifact as complete_luna_artifact
from run_luna_parse import config as luna_config
from run_upstage_validation import complete_artifact as complete_upstage_artifact
from run_upstage_validation import config as upstage_config
from run_upstage_validation import validate_resolved_models
from verification import verify_document


PRIMARY_DIR = RUNTIME_DIR / "luna_200dpi"
LAYOUT_DIR = RUNTIME_DIR / "upstage"
OUTPUT_DIR = RUNTIME_DIR / "canonical"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Luna text against Upstage layout without overwriting the primary text.")
    parser.add_argument("--issuer", action="append", dest="issuers")
    parser.add_argument("--document", action="append", dest="documents")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    source_documents = discover_documents(args.issuers, args.documents)
    pairs = []
    missing = []
    for document in source_documents:
        relative = Path(document.issuer) / f"{document.card_name}.json"
        primary_path = PRIMARY_DIR / relative
        layout_path = LAYOUT_DIR / relative
        if primary_path.exists() and layout_path.exists():
            pairs.append((document, primary_path, layout_path))
        else:
            missing.append(document.document_id)

    verdicts: Counter[str] = Counter()
    issues: Counter[str] = Counter()
    pp_pages = 0
    failures = []
    verified_artifacts = []
    for source_document, primary_path, layout_path in pairs:
        try:
            primary = read_json(primary_path)
            batch_pages = int(primary.get("parser", {}).get("batch_pages", 0))
            luna_config_sha256 = value_sha256(luna_config(batch_pages)) if batch_pages > 0 else ""
            if not complete_luna_artifact(primary_path, source_document, luna_config_sha256):
                raise ValueError("Luna artifact does not match the current parser configuration")
            if not complete_upstage_artifact(layout_path, source_document, value_sha256(upstage_config())):
                raise ValueError("Upstage artifact does not match the current parser configuration")
            verified = verify_document(primary, read_json(layout_path))
            if verified.get("source", {}).get("sha256") != source_document.sha256:
                raise ValueError("verified artifacts do not match the current source hash")
        except Exception as error:
            failures.append({"document": primary_path.stem, "error": f"{type(error).__name__}: {error}"})
            continue
        destination = OUTPUT_DIR / primary_path.relative_to(PRIMARY_DIR)
        verified_artifacts.append((destination, verified))
        verdicts[verified["verdict"]] += 1
        issues.update(verified["issue_counts"])
        pp_pages += len(verified["pp_structure_v3"]["pages"])
    if (missing or failures) and not args.allow_partial:
        raise SystemExit(
            f"full verification requires all {len(source_documents)} current documents: "
            f"{len(missing)} missing pairs, {len(failures)} invalid pairs"
        )

    try:
        validate_resolved_models(
            artifact.get("layout_parser", {}).get("resolved_model")
            for _, artifact in verified_artifacts
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    for destination, verified in verified_artifacts:
        write_json(destination, verified)
        print(json.dumps({"document_id": verified["document_id"], "verdict": verified["verdict"], "review_pages": verified["review_pages"]}, ensure_ascii=False))

    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "expected_documents": len(source_documents),
        "paired_documents": len(pairs),
        "verified_documents": sum(verdicts.values()),
        "missing_documents": missing,
        "verdicts": dict(sorted(verdicts.items())),
        "issue_counts": dict(sorted(issues.items())),
        "pp_structure_v3_deferred_pages": pp_pages,
        "failures": failures,
    }
    destination = RAG_DIR / "reports" / "verification_summary.json"
    write_json(destination, report)
    print(f"{destination}: {report['verified_documents']} verified / {len(failures)} failed")


if __name__ == "__main__":
    main()
