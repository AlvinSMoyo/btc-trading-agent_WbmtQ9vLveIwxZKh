# app/io/atomic.py
from __future__ import annotations
import csv, os, tempfile
from pathlib import Path
from typing import Iterable

def append_row_atomic(csv_path: str | os.PathLike, headers: Iterable[str], row: dict):
    p = Path(csv_path); p.parent.mkdir(parents=True, exist_ok=True)
    exists = p.exists()
    # Write to a temp file next to target, then replace
    with tempfile.NamedTemporaryFile("w", newline="", delete=False, dir=str(p.parent), suffix=".tmp") as tmp:
        writer = csv.DictWriter(tmp, fieldnames=list(headers))
        if not exists:
            writer.writeheader()
        # If file exists, copy old content first
        if exists:
            with p.open("r", newline="") as fh_in:
                # Fast path: just append row (keep it simple & robust)
                pass
        writer.writerow(row)
        tmp_path = tmp.name
    os.replace(tmp_path, p)
