"""Read-only SQLite source for reconciliation exports."""

import sqlite3
import hashlib
from pathlib import Path
from typing import Any, Mapping


def snapshot_manifest(database_path: str | Path) -> dict[str, Any]:
    """Return reproducibility metadata without opening the database for write."""
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database export not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Database export is empty: {path}")

    digest = hashlib.sha256()
    with path.open("rb") as database_file:
        for chunk in iter(lambda: database_file.read(1024 * 1024), b""):
            digest.update(chunk)

    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()

    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "sha256": digest.hexdigest(),
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "integrity_check": integrity,
    }


def assert_snapshot_unchanged(
    database_path: str | Path,
    manifest: Mapping[str, Any],
) -> None:
    """Reject a report if a source changed during extraction."""
    current = snapshot_manifest(database_path)
    if current["sha256"] != manifest["sha256"]:
        raise RuntimeError(f"Database changed during reconciliation: {database_path}")


def read_records(
    database_path: str | Path,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> list[Mapping[str, Any]]:
    """Read rows into mappings and close the connection immediately.

    The query is supplied by the caller so schema-specific details stay out of
    the identity matcher. This function intentionally exposes no write API.
    """
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database export not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Database export is empty: {path}")

    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, parameters)]
    finally:
        connection.close()