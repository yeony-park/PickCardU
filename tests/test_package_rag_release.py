from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path


class PackageRagReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release = self.root / "release_fixture"
        (self.release / "chroma").mkdir(parents=True)
        (self.release / "corpus.sqlite").write_bytes(b"fixture-corpus")
        (self.release / "chroma/vectors.bin").write_bytes(b"fixture-vectors")
        manifest = {
            "schema_version": "rag_index_release_v1",
            "release_id": "release_fixture",
            "release_status": "production",
            "corpus_sqlite_sha256": "664224b57cf72996f61e8528967bb92c271a7999c08db8b63b5e81219b6a9f2f",
            "chroma_tree_sha256": "ae669c3f23f239a919f536c00ee5228aa5481aaf5ebef38391986b85b2f70796",
        }
        (self.release / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_same_release_produces_identical_archive(self) -> None:
        from scripts.package_rag_release import package_release

        first = package_release(self.release, self.root / "first.tar.gz")
        second = package_release(self.release, self.root / "second.tar.gz")

        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.size_bytes, second.size_bytes)
        self.assertEqual(first.release_id, "release_fixture")
        self.assertEqual(first.output_path.read_bytes(), second.output_path.read_bytes())
        with tarfile.open(first.output_path, "r:gz") as archive:
            self.assertEqual(
                archive.getnames(),
                [
                    "release_fixture",
                    "release_fixture/chroma",
                    "release_fixture/chroma/vectors.bin",
                    "release_fixture/corpus.sqlite",
                    "release_fixture/manifest.json",
                ],
            )

    def test_manifest_release_id_must_match_directory(self) -> None:
        from scripts.package_rag_release import package_release

        manifest_path = self.release / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["release_id"] = "release_other"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "release ID"):
            package_release(self.release, self.root / "invalid.tar.gz")

    def test_symlink_descendant_is_rejected(self) -> None:
        from scripts.package_rag_release import package_release

        (self.release / "chroma/link").symlink_to("vectors.bin")

        with self.assertRaisesRegex(ValueError, "symlink"):
            package_release(self.release, self.root / "invalid.tar.gz")


if __name__ == "__main__":
    unittest.main()
