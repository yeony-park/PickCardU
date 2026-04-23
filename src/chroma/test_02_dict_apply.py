"""
test_02_dict_apply.py

Apply card_domain_dict.json exact_mappings to postprocessed OCR text files.
Input:  data/vision/vision_raw_text/**/*.txt
Output: data/vision/vision_dict_applied/**/*.txt
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR    = PROJECT_ROOT / "data" / "vision" / "vision_raw_text"
OUTPUT_DIR   = PROJECT_ROOT / "data" / "vision" / "vision_dict_applied"
DICT_PATH    = Path(__file__).parent / "card_domain_dict.json"


def load_dict(dict_path: Path) -> dict[str, str]:
    with open(dict_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("exact_mappings", {})


def apply_dict(text: str, mappings: dict[str, str]) -> tuple[str, int]:
    """
    Replace all exact_mappings occurrences in text.
    Longer keys are matched first to avoid partial replacement conflicts.
    Returns (corrected_text, fix_count).
    """
    fix_count = 0
    for wrong, correct in sorted(mappings.items(), key=lambda x: -len(x[0])):
        count = text.count(wrong)
        if count:
            text = text.replace(wrong, correct)
            fix_count += count
    return text, fix_count


def process_file(input_path: Path, output_path: Path, mappings: dict[str, str]) -> None:
    raw = input_path.read_text(encoding="utf-8")
    corrected, fix_count = apply_dict(raw, mappings)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(corrected, encoding="utf-8")

    status = f"fixed={fix_count}" if fix_count else "no changes"
    print(f"  [{status}] → {output_path.relative_to(PROJECT_ROOT)}")


def run_all() -> None:
    mappings = load_dict(DICT_PATH)
    print(f"[INFO] loaded {len(mappings)} exact mappings from {DICT_PATH.name}")

    txt_paths = sorted(INPUT_DIR.glob("*/*.txt"))
    if not txt_paths:
        print(f"[WARN] no .txt files found in {INPUT_DIR}")
        return

    print(f"[INFO] found {len(txt_paths)} postprocessed files")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for input_path in txt_paths:
        issuer = input_path.parent.name
        output_path = OUTPUT_DIR / issuer / input_path.name

        if output_path.exists():
            print(f"[SKIP] already exists: {output_path.relative_to(PROJECT_ROOT)}")
            continue

        print(f"[FILE] {issuer}/{input_path.name}")
        try:
            process_file(input_path, output_path, mappings)
        except Exception as e:
            print(f"[ERROR] {input_path}: {e}")


if __name__ == "__main__":
    run_all()
