from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal
from urllib.request import Request, urlopen
from urllib.parse import urlparse


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
RELEASE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class RagReleaseAsset:
    release_id: str
    url: str
    archive_sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class BgeAsset:
    repository: str
    revision: str
    install_path: Path
    files: dict[str, str]


@dataclass(frozen=True)
class AssetDescriptor:
    schema_version: str
    rag_release: RagReleaseAsset
    bge_reranker: BgeAsset


def _object(value: Any, expected_keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ValueError(f"{label} schema is invalid")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


def _sha(value: Any, label: str) -> str:
    text = _string(value, label)
    if not SHA256_PATTERN.fullmatch(text):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return text


def _relative_path(value: Any, label: str) -> PurePosixPath:
    text = _string(value, label)
    path = PurePosixPath(text)
    if "\\" in text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must stay under the repository")
    return path


def load_descriptor(path: Path, repository_root: Path) -> AssetDescriptor:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("asset descriptor is unreadable") from exc
    root = _object(document, {"schema_version", "rag_release", "bge_reranker"}, "asset descriptor")
    if root["schema_version"] != "pickcardu_dev_assets_v1":
        raise ValueError("asset descriptor schema version is unsupported")

    release = _object(
        root["rag_release"],
        {"release_id", "url", "archive_sha256", "manifest_sha256"},
        "RAG release",
    )
    release_id = _string(release["release_id"], "release ID")
    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise ValueError("release ID is invalid")
    url = _string(release["url"], "release URL")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or "/releases/download/" not in parsed.path
        or "/latest/" in parsed.path
        or release_id not in parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("release URL must be an immutable GitHub Release asset")

    bge = _object(
        root["bge_reranker"],
        {"repository", "revision", "install_path", "files"},
        "BGE reranker",
    )
    repository = _string(bge["repository"], "BGE repository")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("BGE repository is invalid")
    revision = _string(bge["revision"], "BGE revision")
    if not REVISION_PATTERN.fullmatch(revision):
        raise ValueError("BGE revision must be a full commit hash")
    relative_install = _relative_path(bge["install_path"], "BGE install path")
    resolved_root = repository_root.resolve()
    install_path = (resolved_root / Path(*relative_install.parts)).resolve()
    if not install_path.is_relative_to(resolved_root):
        raise ValueError("BGE install path escapes the repository")
    if not isinstance(bge["files"], dict) or not bge["files"]:
        raise ValueError("BGE files must be a non-empty object")
    files: dict[str, str] = {}
    for filename, digest in bge["files"].items():
        relative_file = _relative_path(filename, "BGE file path")
        normalized = relative_file.as_posix()
        files[normalized] = _sha(digest, f"BGE file hash for {normalized}")

    return AssetDescriptor(
        schema_version="pickcardu_dev_assets_v1",
        rag_release=RagReleaseAsset(
            release_id=release_id,
            url=url,
            archive_sha256=_sha(release["archive_sha256"], "release archive hash"),
            manifest_sha256=_sha(release["manifest_sha256"], "release manifest hash"),
        ),
        bge_reranker=BgeAsset(repository, revision, install_path, files),
    )


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"expected a regular model file: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_bge_files(root: Path, files: dict[str, str]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("BGE model path must be a regular directory")
    for relative, expected in sorted(files.items()):
        path = root / Path(*PurePosixPath(relative).parts)
        if _sha256_file(path) != expected:
            raise RuntimeError(f"BGE model hash mismatch for {relative}")


def ensure_bge_model(
    asset: BgeAsset,
    *,
    snapshot_download_fn: Callable[..., Any] | None = None,
) -> Literal["installed", "reused"]:
    target = asset.install_path
    if target.exists() or target.is_symlink():
        try:
            _verify_bge_files(target, asset.files)
        except RuntimeError as exc:
            raise RuntimeError("existing BGE model is invalid; refusing to replace it") from exc
        return "reused"

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        if snapshot_download_fn is None:
            from huggingface_hub import snapshot_download

            snapshot_download_fn = snapshot_download
        snapshot_download_fn(
            repo_id=asset.repository,
            revision=asset.revision,
            allow_patterns=sorted(asset.files),
            local_dir=str(staging),
        )
        metadata = staging / ".cache"
        if metadata.exists():
            shutil.rmtree(metadata)
        _verify_bge_files(staging, asset.files)
        if target.exists() or target.is_symlink():
            raise RuntimeError("BGE model target appeared during installation")
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return "installed"


def _download_url(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "PickCardU-setup/1"})
    with urlopen(request, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def ensure_rag_release(
    asset: RagReleaseAsset,
    repository_root: Path,
    runtime_root: Path,
    *,
    download_fn: Callable[[str, Path], Any] | None = None,
):
    from pickcardu_rag_api.release_install import install_release_archive

    del repository_root  # Reserved for future repository-relative cache policy.
    runtime_root = runtime_root.resolve()
    existing_release = runtime_root / "index-release" / asset.release_id
    if existing_release.exists() or existing_release.is_symlink():
        return install_release_archive(
            None,
            runtime_root,
            asset.release_id,
            asset.manifest_sha256,
        )

    downloads = runtime_root / ".downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{asset.release_id}.",
        suffix=".tar.gz.part",
        dir=downloads,
    )
    os.close(descriptor)
    archive_path = Path(temporary_name)
    try:
        (download_fn or _download_url)(asset.url, archive_path)
        if _sha256_file(archive_path) != asset.archive_sha256:
            raise RuntimeError("release archive hash mismatch")
        return install_release_archive(
            archive_path,
            runtime_root,
            asset.release_id,
            asset.manifest_sha256,
        )
    finally:
        archive_path.unlink(missing_ok=True)
        try:
            downloads.rmdir()
        except OSError:
            pass


def run_setup(config_path: Path, repository_root: Path, runtime_root: Path) -> dict[str, str]:
    descriptor = load_descriptor(config_path, repository_root)
    bge_status = ensure_bge_model(descriptor.bge_reranker)
    release = ensure_rag_release(
        descriptor.rag_release,
        repository_root,
        runtime_root,
    )
    return {
        "bge_reranker": bge_status,
        "rag_release": release.status,
        "release_id": release.release_id,
    }


def main() -> None:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Install PickCardU local development assets.")
    parser.add_argument("--repository-root", type=Path, default=default_root)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    arguments = parser.parse_args()
    repository_root = arguments.repository_root.resolve()
    config_path = arguments.config or repository_root / "config/dev-assets.json"
    runtime_root = arguments.runtime_root or repository_root / "data/rag/runtime"
    print(json.dumps(
        run_setup(config_path, repository_root, runtime_root),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ))


if __name__ == "__main__":
    main()
