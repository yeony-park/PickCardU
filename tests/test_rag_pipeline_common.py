import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

from common import discover_documents, read_json, write_json


def test_full_pdf_corpus_is_discovered_in_stable_order():
    documents = discover_documents()

    assert len(documents) == 106
    assert sum(document.page_count for document in documents) == 617
    assert max(document.page_count for document in documents) == 48
    assert [document.relative_path.casefold() for document in documents] == sorted(
        document.relative_path.casefold() for document in documents
    )
    assert len({document.sha256 for document in documents}) == 106


def test_atomic_json_writes_do_not_share_a_thread_temp_path(tmp_path):
    destination = tmp_path / "artifact.json"

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda value: write_json(destination, {"value": value}), range(100)))

    assert read_json(destination)["value"] in range(100)
    assert not list(tmp_path.glob("*.tmp"))
