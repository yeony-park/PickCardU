from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src"), str(ROOT / "services/rag-api/tests")]

from pickcardu_rag_api.index import _sha256  # noqa: E402
from scripts.package_rag_release import package_release  # noqa: E402
from support import build_release  # noqa: E402


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class AssetDescriptorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / "assets.json"
        self.document = {
            "schema_version": "pickcardu_dev_assets_v1",
            "rag_release": {
                "release_id": "release_fixture",
                "url": "https://github.com/example/project/releases/download/rag-index-release_fixture/release_fixture.tar.gz",
                "archive_sha256": "a" * 64,
                "manifest_sha256": "b" * 64,
            },
            "bge_reranker": {
                "repository": "BAAI/bge-reranker-v2-m3",
                "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
                "install_path": ".cache/reranker/bge-reranker-v2-m3",
                "files": {"model.safetensors": "c" * 64},
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self) -> None:
        self.config.write_text(json.dumps(self.document), encoding="utf-8")

    def test_loads_exact_descriptor_and_resolves_model_under_repository(self) -> None:
        from scripts.setup_assets import load_descriptor

        self.write()
        descriptor = load_descriptor(self.config, self.root)

        self.assertEqual(descriptor.rag_release.release_id, "release_fixture")
        self.assertEqual(descriptor.bge_reranker.install_path, self.root / ".cache/reranker/bge-reranker-v2-m3")
        self.assertEqual(descriptor.bge_reranker.files, {"model.safetensors": "c" * 64})

    def test_rejects_unknown_schema_blank_hash_and_unsafe_paths(self) -> None:
        from scripts.setup_assets import load_descriptor

        baseline = deepcopy(self.document)
        cases = {
            "schema": lambda: self.document.update(schema_version="future"),
            "blank_hash": lambda: self.document["rag_release"].update(archive_sha256=""),
            "absolute_install": lambda: self.document["bge_reranker"].update(install_path="/tmp/model"),
            "escaping_install": lambda: self.document["bge_reranker"].update(install_path="../model"),
            "escaping_file": lambda: self.document["bge_reranker"].update(files={"../model.bin": "c" * 64}),
            "mutable_url": lambda: self.document["rag_release"].update(
                url="https://github.com/example/project/releases/latest/download/release.tar.gz"
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                self.document = deepcopy(baseline)
                mutate()
                self.write()
                with self.assertRaises(ValueError):
                    load_descriptor(self.config, self.root)


class BgeInstallerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.model_path = self.root / ".cache/reranker/model"
        self.files = {
            "config.json": _sha256_bytes(b"config"),
            "model.safetensors": _sha256_bytes(b"weights"),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def asset(self):
        from scripts.setup_assets import BgeAsset

        return BgeAsset(
            repository="BAAI/bge-reranker-v2-m3",
            revision="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
            install_path=self.model_path,
            files=self.files,
        )

    def test_valid_existing_model_is_reused_without_download(self) -> None:
        from scripts.setup_assets import ensure_bge_model

        self.model_path.mkdir(parents=True)
        (self.model_path / "config.json").write_bytes(b"config")
        (self.model_path / "model.safetensors").write_bytes(b"weights")

        def unexpected_download(**kwargs):
            raise AssertionError(f"unexpected download: {kwargs}")

        self.assertEqual(ensure_bge_model(self.asset(), snapshot_download_fn=unexpected_download), "reused")

    def test_absent_model_is_staged_verified_and_installed(self) -> None:
        from scripts.setup_assets import ensure_bge_model

        calls = []

        def download(**kwargs):
            calls.append(kwargs)
            destination = Path(kwargs["local_dir"])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "config.json").write_bytes(b"config")
            (destination / "model.safetensors").write_bytes(b"weights")
            (destination / ".cache/huggingface").mkdir(parents=True)

        status = ensure_bge_model(self.asset(), snapshot_download_fn=download)

        self.assertEqual(status, "installed")
        self.assertEqual((self.model_path / "model.safetensors").read_bytes(), b"weights")
        self.assertFalse((self.model_path / ".cache").exists())
        self.assertEqual(calls[0]["repo_id"], "BAAI/bge-reranker-v2-m3")
        self.assertEqual(calls[0]["revision"], "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
        self.assertEqual(calls[0]["allow_patterns"], ["config.json", "model.safetensors"])

    def test_bad_download_hash_leaves_final_path_absent(self) -> None:
        from scripts.setup_assets import ensure_bge_model

        def download(**kwargs):
            destination = Path(kwargs["local_dir"])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "config.json").write_bytes(b"wrong")
            (destination / "model.safetensors").write_bytes(b"weights")

        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            ensure_bge_model(self.asset(), snapshot_download_fn=download)

        self.assertFalse(self.model_path.exists())

    def test_invalid_existing_model_is_not_replaced(self) -> None:
        from scripts.setup_assets import ensure_bge_model

        self.model_path.mkdir(parents=True)
        (self.model_path / "config.json").write_bytes(b"wrong")
        (self.model_path / "model.safetensors").write_bytes(b"weights")

        with self.assertRaisesRegex(RuntimeError, "existing BGE model"):
            ensure_bge_model(self.asset(), snapshot_download_fn=lambda **kwargs: None)

        self.assertEqual((self.model_path / "config.json").read_bytes(), b"wrong")


class RagReleaseInstallerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_runtime = self.root / "source-runtime"
        build_release(self.source_runtime)
        self.source_release = self.source_runtime / "index-release/release_fixture"
        self.archive = self.root / "release_fixture.tar.gz"
        packaged = package_release(self.source_release, self.archive)
        from scripts.setup_assets import RagReleaseAsset

        self.asset = RagReleaseAsset(
            release_id="release_fixture",
            url="https://github.com/example/project/releases/download/rag-index-release_fixture/release_fixture.tar.gz",
            archive_sha256=packaged.sha256,
            manifest_sha256=_sha256(self.source_release / "manifest.json"),
        )

    def tearDown(self) -> None:
        for path in self.root.rglob("*"):
            try:
                path.chmod(0o755 if path.is_dir() else 0o644)
            except FileNotFoundError:
                pass
        self.temporary.cleanup()

    def test_valid_installed_release_is_reused_without_download(self) -> None:
        from pickcardu_rag_api.release_install import install_release_archive
        from scripts.setup_assets import ensure_rag_release

        runtime = self.root / "runtime"
        install_release_archive(
            self.archive,
            runtime,
            self.asset.release_id,
            self.asset.manifest_sha256,
        )

        def unexpected_download(url, destination):
            raise AssertionError(f"unexpected download: {url} -> {destination}")

        result = ensure_rag_release(self.asset, self.root, runtime, download_fn=unexpected_download)

        self.assertEqual(result.status, "reused")
        self.assertEqual(result.release_id, "release_fixture")

    def test_absent_release_downloads_verifies_and_installs(self) -> None:
        from scripts.setup_assets import ensure_rag_release

        runtime = self.root / "runtime"
        calls = []

        def download(url, destination):
            calls.append((url, destination))
            shutil.copyfile(self.archive, destination)

        result = ensure_rag_release(self.asset, self.root, runtime, download_fn=download)

        self.assertEqual(result.status, "installed")
        self.assertTrue((runtime / "active-index.json").is_file())
        self.assertEqual(calls[0][0], self.asset.url)
        self.assertFalse(calls[0][1].exists())

    def test_archive_hash_mismatch_does_not_install_release(self) -> None:
        from scripts.setup_assets import ensure_rag_release

        runtime = self.root / "runtime"

        def download(url, destination):
            destination.write_bytes(b"corrupt")

        with self.assertRaisesRegex(RuntimeError, "archive hash mismatch"):
            ensure_rag_release(self.asset, self.root, runtime, download_fn=download)

        self.assertFalse((runtime / "index-release/release_fixture").exists())
        self.assertFalse((runtime / "active-index.json").exists())

    def test_download_failure_preserves_existing_pointer(self) -> None:
        from scripts.setup_assets import RagReleaseAsset, ensure_rag_release

        runtime = self.root / "runtime"
        build_release(runtime)
        pointer = (runtime / "active-index.json").read_bytes()
        other = RagReleaseAsset(
            release_id="release_other",
            url="https://github.com/example/project/releases/download/rag-index-release_other/release_other.tar.gz",
            archive_sha256="d" * 64,
            manifest_sha256="e" * 64,
        )

        def download(url, destination):
            raise OSError("network unavailable")

        with self.assertRaisesRegex(OSError, "network unavailable"):
            ensure_rag_release(other, self.root, runtime, download_fn=download)

        self.assertEqual((runtime / "active-index.json").read_bytes(), pointer)


class SetupRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source_runtime = self.root / "source-runtime"
        build_release(source_runtime)
        release = source_runtime / "index-release/release_fixture"
        archive = self.root / "release.tar.gz"
        packaged = package_release(release, archive)
        self.runtime = self.root / "runtime"
        from pickcardu_rag_api.release_install import install_release_archive

        manifest_hash = _sha256(release / "manifest.json")
        install_release_archive(archive, self.runtime, "release_fixture", manifest_hash)
        model = self.root / ".cache/reranker/model"
        model.mkdir(parents=True)
        (model / "config.json").write_bytes(b"config")
        self.config = self.root / "assets.json"
        self.config.write_text(json.dumps({
            "schema_version": "pickcardu_dev_assets_v1",
            "rag_release": {
                "release_id": "release_fixture",
                "url": "https://github.com/example/project/releases/download/rag-index-release_fixture/release_fixture.tar.gz",
                "archive_sha256": packaged.sha256,
                "manifest_sha256": manifest_hash,
            },
            "bge_reranker": {
                "repository": "BAAI/bge-reranker-v2-m3",
                "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
                "install_path": ".cache/reranker/model",
                "files": {"config.json": _sha256_bytes(b"config")},
            },
        }), encoding="utf-8")

    def tearDown(self) -> None:
        for path in self.root.rglob("*"):
            try:
                path.chmod(0o755 if path.is_dir() else 0o644)
            except FileNotFoundError:
                pass
        self.temporary.cleanup()

    def test_run_setup_reports_reused_verified_assets(self) -> None:
        from scripts.setup_assets import run_setup

        result = run_setup(self.config, self.root, self.runtime)

        self.assertEqual(result, {
            "bge_reranker": "reused",
            "rag_release": "reused",
            "release_id": "release_fixture",
        })


if __name__ == "__main__":
    unittest.main()
