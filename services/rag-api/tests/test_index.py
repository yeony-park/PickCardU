from __future__ import annotations

import json
import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chromadb


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_rag import SearchConfig  # noqa: E402
from pickcardu_rag_api.index import ActiveIndexLoader, _canonical, _sha256, _tree_hash  # noqa: E402
from support import FakeReranker, build_release  # noqa: E402


class ActiveIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.manifest = build_release(self.runtime)
        self.loader = ActiveIndexLoader(self.runtime, reranker=FakeReranker())

    def tearDown(self) -> None:
        for path in self.root.rglob("*"):
            try:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            except FileNotFoundError:
                pass
        self.temporary.cleanup()

    def test_loads_read_only_fts_and_fake_chroma(self) -> None:
        handle = self.loader.load()
        result = handle.search("카페 혜택", [0.0, 0.0], SearchConfig(reranker="off"))
        self.assertEqual(handle.release_id, "release_fixture")
        self.assertEqual(result["cards"][0]["card_key"], "issuer/card-a")
        self.assertEqual(result["evidence"][0]["page_num"], 2)
        self.assertIs(self.loader.load(), handle)

    def test_cached_handle_is_revalidated_only_after_pointer_change(self) -> None:
        handle = self.loader.load()
        with patch.object(self.loader, "_load_uncached", side_effect=AssertionError("unexpected reload")):
            self.assertIs(self.loader.load(), handle)
        pointer = self.runtime / "active-index.json"
        pointer.write_bytes(pointer.read_bytes() + b" ")
        with patch.object(self.loader, "_load_uncached", side_effect=RuntimeError("revalidation reached")):
            with self.assertRaisesRegex(RuntimeError, "revalidation reached"):
                self.loader.load()

    def test_pointer_manifest_dimension_and_tree_mismatch_fail_closed(self) -> None:
        cases = ("pointer", "dimension", "tree", "corpus", "chunks", "chunking_contract")
        for case in cases:
            with self.subTest(case=case):
                self.tearDown(); self.setUp()
                release = self.runtime / "index-release/release_fixture"
                if case == "pointer":
                    pointer = json.loads((self.runtime / "active-index.json").read_text())
                    pointer["manifest_sha256"] = "0" * 64
                    (self.runtime / "active-index.json").write_text(_canonical(pointer))
                else:
                    manifest = json.loads((release / "manifest.json").read_text())
                    if case == "dimension":
                        manifest["embedding_dimension"] = 3
                    elif case == "tree":
                        manifest["chroma_tree_sha256"] = "0" * 64
                    elif case == "corpus":
                        manifest["corpus_hash"] = "0" * 64
                    elif case == "chunking_contract":
                        manifest["chunking_contract"] = "wrong_contract"
                    else:
                        manifest["chunk_ids"] = ["wrong"]
                    (release / "manifest.json").write_text(_canonical(manifest) + "\n")
                    pointer = {"release_id": "release_fixture", "manifest_sha256": _sha256(release / "manifest.json")}
                    (self.runtime / "active-index.json").write_text(_canonical(pointer))
                with self.assertRaises(RuntimeError):
                    self.loader.load()

    def test_writable_corpus_is_rejected(self) -> None:
        corpus = self.runtime / "index-release/release_fixture/corpus.sqlite"
        os.chmod(corpus, 0o644)
        with self.assertRaisesRegex(RuntimeError, "read-only"):
            self.loader.load()

    def test_modified_sqlite_corpus_is_rejected(self) -> None:
        corpus = self.runtime / "index-release/release_fixture/corpus.sqlite"
        os.chmod(corpus, 0o644)
        corpus.write_bytes(corpus.read_bytes() + b"tampered")
        os.chmod(corpus, 0o444)
        with self.assertRaisesRegex(RuntimeError, "SQLite corpus hash mismatch"):
            self.loader.load()

    def test_modified_serving_vector_tree_is_rejected(self) -> None:
        serving = self.runtime / "serving/release_fixture" / self.manifest["chroma_tree_sha256"] / "chroma"
        client = chromadb.PersistentClient(path=str(serving))
        collection = client.get_collection("card_page_section_benefit")
        collection.delete(ids=["chunk-cafe"])
        del collection, client
        gc.collect()
        with self.assertRaisesRegex(RuntimeError, "serving Chroma identity mismatch"):
            self.loader.load()

    def test_serving_descendant_symlink_is_rejected(self) -> None:
        serving = self.runtime / "serving/release_fixture" / self.manifest["chroma_tree_sha256"] / "chroma"
        target = next(path for path in serving.rglob("*") if path.is_file())
        renamed = target.with_name(target.name + ".real")
        target.rename(renamed)
        target.symlink_to(renamed.name)
        with self.assertRaisesRegex(RuntimeError, "symlink or non-regular"):
            self.loader.load()

    def test_manifest_swap_during_chroma_open_is_rejected(self) -> None:
        manifest_path = self.runtime / "index-release/release_fixture/manifest.json"
        persistent_client = chromadb.PersistentClient

        def swap_manifest(*args, **kwargs):
            manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
            return persistent_client(*args, **kwargs)

        with patch("chromadb.PersistentClient", side_effect=swap_manifest), self.assertRaisesRegex(RuntimeError, "active release changed"):
            self.loader.load()

    def test_release_manifest_symlink_is_rejected(self) -> None:
        manifest_path = self.runtime / "index-release/release_fixture/manifest.json"
        renamed = manifest_path.with_name("manifest.real.json")
        manifest_path.rename(renamed)
        manifest_path.symlink_to(renamed.name)
        with self.assertRaises((OSError, RuntimeError)):
            self.loader.load()


if __name__ == "__main__":
    unittest.main()
