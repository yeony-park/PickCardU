from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src"), str(ROOT)]

from pickcardu_rag_api.index import ActiveIndexLoader, _canonical, _sha256  # noqa: E402
from scripts.package_rag_release import package_release  # noqa: E402
from support import FakeReranker, build_release  # noqa: E402


class ReleaseInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_runtime = self.root / "source-runtime"
        self.manifest = build_release(self.source_runtime)
        self.source_release = self.source_runtime / "index-release/release_fixture"
        self.archive = self.root / "release.tar.gz"
        package_release(self.source_release, self.archive)

    def tearDown(self) -> None:
        for path in self.root.rglob("*"):
            try:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            except FileNotFoundError:
                pass
        self.temporary.cleanup()

    def test_installs_release_materializes_serving_and_loads_it(self) -> None:
        from pickcardu_rag_api.release_install import install_release_archive

        runtime = self.root / "target-runtime"
        result = install_release_archive(
            self.archive,
            runtime,
            expected_release_id="release_fixture",
            expected_manifest_sha256=_sha256(self.source_release / "manifest.json"),
        )

        self.assertEqual(result.status, "installed")
        self.assertEqual(result.release_id, "release_fixture")
        self.assertEqual(json.loads(result.active_pointer.read_text()), {
            "manifest_sha256": _sha256(result.release_path / "manifest.json"),
            "release_id": "release_fixture",
        })
        self.assertEqual(result.release_path.stat().st_mode & 0o222, 0)
        self.assertEqual((result.release_path / "corpus.sqlite").stat().st_mode & 0o222, 0)
        handle = ActiveIndexLoader(runtime, reranker=FakeReranker()).load()
        self.assertEqual(handle.release_id, "release_fixture")
        self.assertEqual(len(handle.chunks), 2)

    def test_valid_existing_install_is_reused(self) -> None:
        from pickcardu_rag_api.release_install import install_release_archive

        runtime = self.root / "target-runtime"
        expected_hash = _sha256(self.source_release / "manifest.json")
        install_release_archive(self.archive, runtime, "release_fixture", expected_hash)

        second = install_release_archive(None, runtime, "release_fixture", expected_hash)

        self.assertEqual(second.status, "reused")

    def test_archive_is_required_for_a_new_release(self) -> None:
        from pickcardu_rag_api.release_install import install_release_archive

        with self.assertRaisesRegex(ValueError, "archive is required"):
            install_release_archive(
                None,
                self.root / "empty-runtime",
                "release_fixture",
                _sha256(self.source_release / "manifest.json"),
            )

    def test_unsafe_archive_members_are_rejected(self) -> None:
        from pickcardu_rag_api.release_install import install_release_archive

        cases = {
            "absolute": ("/escape", tarfile.REGTYPE),
            "traversal": ("release_fixture/../../escape", tarfile.REGTYPE),
            "symlink": ("release_fixture/link", tarfile.SYMTYPE),
            "hardlink": ("release_fixture/link", tarfile.LNKTYPE),
        }
        for name, (member_name, member_type) in cases.items():
            with self.subTest(name=name):
                archive = self.root / f"{name}.tar.gz"
                with tarfile.open(archive, "w:gz") as output:
                    member = tarfile.TarInfo(member_name)
                    member.type = member_type
                    if member_type == tarfile.REGTYPE:
                        member.size = 1
                        output.addfile(member, io.BytesIO(b"x"))
                    else:
                        member.linkname = "manifest.json"
                        output.addfile(member)
                with self.assertRaisesRegex(ValueError, "unsafe archive"):
                    install_release_archive(archive, self.root / f"runtime-{name}", "release_fixture", "0" * 64)

    def test_invalid_existing_release_is_not_replaced(self) -> None:
        from pickcardu_rag_api.release_install import install_release_archive

        runtime = self.root / "target-runtime"
        existing = runtime / "index-release/release_fixture"
        existing.mkdir(parents=True)
        marker = existing / "user-data.txt"
        marker.write_text("preserve", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "existing release"):
            install_release_archive(
                self.archive,
                runtime,
                "release_fixture",
                _sha256(self.source_release / "manifest.json"),
            )

        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_loader_failure_restores_previous_active_pointer(self) -> None:
        from pickcardu_rag_api.release_install import install_release_archive

        runtime = self.root / "target-runtime"
        build_release(runtime)
        old_pointer = (runtime / "active-index.json").read_bytes()

        bad_release = self.source_runtime / "index-release/release_bad"
        self.source_release.rename(bad_release)
        manifest_path = bad_release / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["release_id"] = "release_bad"
        manifest.pop("chunking_contract")
        manifest_path.write_text(_canonical(manifest) + "\n", encoding="utf-8")
        bad_archive = self.root / "bad-release.tar.gz"
        package_release(bad_release, bad_archive)

        with self.assertRaisesRegex(RuntimeError, "active release manifest contract"):
            install_release_archive(
                bad_archive,
                runtime,
                "release_bad",
                _sha256(manifest_path),
            )

        self.assertEqual((runtime / "active-index.json").read_bytes(), old_pointer)


if __name__ == "__main__":
    unittest.main()
