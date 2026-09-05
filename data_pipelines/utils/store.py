"""Writing into `data_store/` without a reader ever seeing half a file."""

import os
from pathlib import Path

import polars as pl


def write_atomic(frame: pl.DataFrame, path: Path) -> None:
    """Write via a sibling temp file and rename.

    The tables that rewrite themselves in full every run are read continuously
    by research sessions. `os.replace` is atomic on POSIX, so a reader gets
    either the old table or the new one, never a half-written one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    frame.write_parquet(temp_path)
    os.replace(temp_path, path)
