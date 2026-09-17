from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PackageResult:
    release_id: str
    output_path: Path
    size_bytes: int
    sha256: str


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_tree(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("release path must be a regular directory")
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        if path.is_symlink():
            raise ValueError("release tree contains a symlink")
        if not path.is_file() and not path.is_dir():
            raise ValueError("release tree contains a non-regular entry")
    return paths


def _tree_hash(root: Path) -> str:
    rows = [
        {"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)}
        for path in _validated_tree(root)
        if path.is_file()
    ]
    return hashlib.sha256(_canonical(rows).encode()).hexdigest()


def _validate_release(release_root: Path) -> tuple[str, list[Path]]:
    paths = _validated_tree(release_root)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", release_root.name):
        raise ValueError("release directory name is invalid")
    if {path.name for path in release_root.iterdir()} != {"manifest.json", "corpus.sqlite", "chroma"}:
        raise ValueError("release must contain only manifest.json, corpus.sqlite, and chroma")
    manifest_path = release_root / "manifest.json"
    corpus_path = release_root / "corpus.sqlite"
    chroma_root = release_root / "chroma"
    if not manifest_path.is_file() or not corpus_path.is_file() or not chroma_root.is_dir():
        raise ValueError("release files are incomplete")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("release manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("release_id") != release_root.name:
        raise ValueError("manifest release ID does not match directory")
    if manifest.get("schema_version") != "rag_index_release_v1" or manifest.get("release_status") != "production":
        raise ValueError("release manifest contract is invalid")
    if manifest.get("corpus_sqlite_sha256") != _sha256(corpus_path):
        raise ValueError("release corpus hash does not match manifest")
    if manifest.get("chroma_tree_sha256") != _tree_hash(chroma_root):
        raise ValueError("release Chroma hash does not match manifest")
    return release_root.name, paths


def _tar_info(archive_name: str, *, is_directory: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(archive_name)
    info.type = tarfile.DIRTYPE if is_directory else tarfile.REGTYPE
    info.mode = 0o755 if is_directory else 0o444
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.size = 0 if is_directory else size
    return info


def package_release(release_root: Path, output_path: Path) -> PackageResult:
    release_root = release_root.resolve()
    output_path = output_path.resolve()
    release_id, paths = _validate_release(release_root)
    if output_path == release_root or release_root in output_path.parents:
        raise ValueError("archive output must be outside the release directory")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output_path.name}.", dir=output_path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=9) as zipped:
                with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    archive.addfile(_tar_info(release_id, is_directory=True))
                    for path in paths:
                        relative = path.relative_to(release_root).as_posix()
                        archive_name = f"{release_id}/{relative}"
                        if path.is_dir():
                            archive.addfile(_tar_info(archive_name, is_directory=True))
                        else:
                            info = _tar_info(archive_name, is_directory=False, size=path.stat().st_size)
                            with path.open("rb") as stream:
                                archive.addfile(info, stream)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return PackageResult(release_id, output_path, output_path.stat().st_size, _sha256(output_path))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic PickCardU RAG release archive.")
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = package_release(arguments.release_root, arguments.output)
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    print(_canonical(payload))


if __name__ == "__main__":
    main()
