"""Centralized SQLite connection factories and utilities for yt-is.

Provides consistent timeout handling, WAL mode enforcement, and URI mode
connection scoping across all csf and ef services to prevent database lock contention.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Union

PathLike = Union[str, Path]


def open_sqlite_ro(path: PathLike, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a SQLite database in read-only mode with standard pragmas.

    Args:
        path: Path to SQLite file.
        timeout: SQLite busy timeout in seconds (default: 30.0s).

    Returns:
        sqlite3.Connection with sqlite3.Row row factory and busy timeout set.
    """
    path_obj = Path(path).resolve()
    uri_path = path_obj.as_posix()
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    return conn


def open_sqlite_rw(
    path: PathLike,
    timeout: float = 30.0,
    wal: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite database in read-write mode with standard pragmas.

    Args:
        path: Path to SQLite file.
        timeout: SQLite busy timeout in seconds (default: 30.0s).
        wal: If True, set PRAGMA journal_mode = WAL (default: True).

    Returns:
        sqlite3.Connection with sqlite3.Row row factory and WAL mode configured.
    """
    path_obj = Path(path).resolve()
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path_obj), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    if wal:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
    return conn


@contextmanager
def sqlite_ro_scope(
    path: PathLike,
    timeout: float = 30.0,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for scoped read-only SQLite connections."""
    conn = open_sqlite_ro(path, timeout=timeout)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def sqlite_rw_scope(
    path: PathLike,
    timeout: float = 30.0,
    wal: bool = True,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for scoped read-write SQLite connections."""
    conn = open_sqlite_rw(path, timeout=timeout, wal=wal)
    try:
        yield conn
    finally:
        conn.close()
