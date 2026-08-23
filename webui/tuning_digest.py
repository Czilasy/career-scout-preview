"""调优摘要工具：稳定 SHA-256 文件/目录摘要（021 B7 自 tuning.py 搬运）。"""

from __future__ import annotations

import hashlib
from pathlib import Path

_SHA256_PREFIX = "sha256:"


def sha256_path(path: str | Path) -> str:
    """Return a stable SHA-256 digest for one file or directory.

    Directory digests hash sorted ``relative path + file-content digest``
    records, so filesystem enumeration order and absolute workspace paths do
    not affect the result.
    """
    target = Path(path)

    def file_digest(file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    if target.is_file():
        return _SHA256_PREFIX + file_digest(target)
    if not target.is_dir():
        raise FileNotFoundError(target)

    digest = hashlib.sha256()
    files = sorted(
        (item for item in target.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(target).as_posix(),
    )
    for file_path in files:
        relative_path = file_path.relative_to(target).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest(file_path).encode("ascii"))
        digest.update(b"\n")
    return _SHA256_PREFIX + digest.hexdigest()
