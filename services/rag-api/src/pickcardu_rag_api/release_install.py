from __future__ import annotations

import gc
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import numpy as np

from .index import (
    ActiveIndexLoader,
    _canonical,
    _embedding_sha256,
    _read_regular_bytes,
    _sha256,
    _tree_entries,
    _tree_hash,
)


MAX_ARCHIVE_MEMBERS = 100_000
MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class ReleaseInstallResult:
    release_id: str
    release_path: Path
    serving_path: Path
    active_pointer: Path
    status: Literal["installed", "reused"]


def _read_manifest(
    release_root: Path,
    expected_release_id: str,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    if release_root.is_symlink() or not release_root.is_dir():
        raise RuntimeError("release root is unavailable")
    entries = _tree_entries(release_root)
    if {path.name for path in release_root.iterdir()} != {"manifest.json", "corpus.sqlite", "chroma"}:
        raise RuntimeError("release contains unexpected top-level entries")
    manifest_path = release_root / "manifest.json"
    corpus_path = release_root / "corpus.sqlite"
    chroma_root = release_root / "chroma"
    if not manifest_path.is_file() or not corpus_path.is_file() or not chroma_root.is_dir() or not entries:
        raise RuntimeError("release files are incomplete")
    if _sha256(manifest_path) != expected_manifest_sha256:
        raise RuntimeError("release manifest hash mismatch")
    try:
        manifest = json.loads(_read_regular_bytes(manifest_path))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("release manifest is invalid") from exc
    required = {
        "schema_version",
        "release_id",
        "release_status",
        "strategy",
        "corpus_hash",
        "corpus_sqlite_sha256",
        "chunk_ids",
        "embedding_dimension",
        "embedding_sha256",
        "chroma_tree_sha256",
    }
    if (
        not isinstance(manifest, dict)
        or not required <= set(manifest)
        or manifest.get("schema_version") != "rag_index_release_v1"
        or manifest.get("release_id") != expected_release_id
        or manifest.get("release_status") != "production"
    ):
        raise RuntimeError("release manifest identity is invalid")
    if manifest.get("corpus_sqlite_sha256") != _sha256(corpus_path):
        raise RuntimeError("release SQLite hash mismatch")
    if manifest.get("chroma_tree_sha256") != _tree_hash(chroma_root):
        raise RuntimeError("release Chroma tree hash mismatch")
    return manifest


def _safe_extract_archive(archive_path: Path, destination: Path, release_id: str) -> Path:
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("unsafe archive member count")
            names: set[str] = set()
            extracted_bytes = 0
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or not path.parts
                    or path.parts[0] != release_id
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or member.name in names
                    or not (member.isdir() or member.isfile())
                ):
                    raise ValueError("unsafe archive member")
                names.add(member.name)
                extracted_bytes += member.size
                if member.size < 0 or extracted_bytes > MAX_EXTRACTED_BYTES:
                    raise ValueError("unsafe archive size")
            for member in members:
                relative = PurePosixPath(member.name)
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() or target.is_symlink():
                    raise ValueError("unsafe archive duplicate target")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("unsafe archive file")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("unsafe archive or extraction failure") from exc
    return destination / release_id


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _open_exclusive_lock(path: Path):
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError("release lock is not a regular file")
    return os.fdopen(descriptor, "a+")


def _make_tree_writable(root: Path) -> None:
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)


def _validate_serving_chroma(chroma_root: Path, manifest: dict[str, object]) -> None:
    import chromadb

    ids = manifest["chunk_ids"]
    dimension = manifest["embedding_dimension"]
    if (
        not isinstance(ids, list)
        or any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in ids)
        or not isinstance(dimension, int)
        or isinstance(dimension, bool)
        or dimension < 1
    ):
        raise RuntimeError("release embedding contract is invalid")
    client = chromadb.PersistentClient(path=str(chroma_root))
    collection = client.get_collection(str(manifest["strategy"]))
    output = collection.get(include=["embeddings"])
    output_by_id = {
        chunk_id: embedding
        for chunk_id, embedding in zip(output["ids"], output["embeddings"], strict=True)
    }
    stored = np.asarray([output_by_id[chunk_id] for chunk_id in ids if chunk_id in output_by_id], dtype=np.float32)
    metadata = collection.metadata or {}
    valid = (
        set(output_by_id) == set(ids)
        and stored.shape == (len(ids), dimension)
        and metadata.get("corpus_hash") == manifest["corpus_hash"]
        and _embedding_sha256(ids, stored) == manifest["embedding_sha256"]
    )
    del collection, client
    gc.collect()
    if not valid:
        raise RuntimeError("serving Chroma identity mismatch")


def _materialize_serving(runtime_root: Path, release_root: Path, manifest: dict[str, object]) -> Path:
    import fcntl

    release_id = str(manifest["release_id"])
    tree_hash = str(manifest["chroma_tree_sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", tree_hash):
        raise RuntimeError("release Chroma tree hash is invalid")
    lock_root = runtime_root / "serving/.locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{release_id}.lock"
    version_parent = runtime_root / "serving" / release_id
    version_parent.mkdir(parents=True, exist_ok=True)
    version_root = version_parent / tree_hash
    marker = {
        "release_id": release_id,
        "chroma_tree_sha256": tree_hash,
        "corpus_hash": manifest["corpus_hash"],
        "chunk_ids": manifest["chunk_ids"],
        "embedding_dimension": manifest["embedding_dimension"],
        "embedding_sha256": manifest["embedding_sha256"],
    }
    with _open_exclusive_lock(lock_path) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if version_root.exists() or version_root.is_symlink():
                if version_root.is_symlink() or not version_root.is_dir():
                    raise RuntimeError("existing serving release is invalid")
                return version_root
            staging = Path(tempfile.mkdtemp(prefix=f".{tree_hash}.", dir=version_parent))
            try:
                source_chroma = release_root / "chroma"
                if _tree_hash(source_chroma) != tree_hash:
                    raise RuntimeError("immutable release changed before serving materialization")
                shutil.copytree(source_chroma, staging / "chroma", copy_function=shutil.copy2)
                _make_tree_writable(staging / "chroma")
                _validate_serving_chroma(staging / "chroma", manifest)
                if _tree_hash(source_chroma) != tree_hash:
                    raise RuntimeError("immutable release changed during serving materialization")
                (staging / "version.json").write_text(_canonical(marker) + "\n", encoding="utf-8")
                if version_root.exists() or version_root.is_symlink():
                    raise RuntimeError("serving release target appeared during installation")
                os.replace(staging, version_root)
                version_root.chmod(0o555)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            return version_root
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _activate_and_validate(runtime_root: Path, release_id: str, manifest_hash: str) -> Path:
    import fcntl

    pointer_path = runtime_root / "active-index.json"
    lock_path = runtime_root / ".active-index.lock"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with _open_exclusive_lock(lock_path) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        temporary = pointer_path.with_name(f".active-index.{os.getpid()}.tmp")
        old_pointer: bytes | None = None
        try:
            if pointer_path.exists() or pointer_path.is_symlink():
                if pointer_path.is_symlink() or not pointer_path.is_file():
                    raise RuntimeError("existing active pointer is invalid")
                old_pointer = _read_regular_bytes(pointer_path)
            pointer_bytes = (
                _canonical({"release_id": release_id, "manifest_sha256": manifest_hash}) + "\n"
            ).encode()
            temporary.write_bytes(pointer_bytes)
            os.replace(temporary, pointer_path)
            try:
                handle = ActiveIndexLoader(runtime_root).load()
                if handle.release_id != release_id:
                    raise RuntimeError("active release validation returned a different release")
            except Exception:
                if old_pointer is None:
                    pointer_path.unlink(missing_ok=True)
                else:
                    temporary.write_bytes(old_pointer)
                    os.replace(temporary, pointer_path)
                raise
            return pointer_path
        finally:
            temporary.unlink(missing_ok=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def install_release_archive(
    archive_path: Path | None,
    runtime_root: Path,
    expected_release_id: str,
    expected_manifest_sha256: str,
) -> ReleaseInstallResult:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", expected_release_id):
        raise ValueError("expected release ID is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise ValueError("expected manifest hash is invalid")
    runtime_root = runtime_root.resolve()
    releases_root = runtime_root / "index-release"
    releases_root.mkdir(parents=True, exist_ok=True)
    release_root = releases_root / expected_release_id
    installed = False

    if release_root.exists() or release_root.is_symlink():
        try:
            manifest = _read_manifest(release_root, expected_release_id, expected_manifest_sha256)
        except RuntimeError as exc:
            raise RuntimeError("existing release is invalid; refusing to replace it") from exc
    else:
        if archive_path is None:
            raise ValueError("archive is required for a new release")
        staging_parent = Path(tempfile.mkdtemp(prefix=f".{expected_release_id}.", dir=releases_root))
        try:
            extracted = _safe_extract_archive(archive_path, staging_parent, expected_release_id)
            manifest = _read_manifest(extracted, expected_release_id, expected_manifest_sha256)
            if release_root.exists() or release_root.is_symlink():
                raise RuntimeError("release target appeared during installation")
            os.replace(extracted, release_root)
            _make_read_only(release_root)
            installed = True
        finally:
            if staging_parent.exists():
                shutil.rmtree(staging_parent)

    serving_root = _materialize_serving(runtime_root, release_root, manifest)
    pointer = _activate_and_validate(runtime_root, expected_release_id, expected_manifest_sha256)
    return ReleaseInstallResult(
        release_id=expected_release_id,
        release_path=release_root,
        serving_path=serving_root,
        active_pointer=pointer,
        status="installed" if installed else "reused",
    )
