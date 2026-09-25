"""Content-addressed store for the raw source bodies behind every published claim.

Every fetched source (official API responses, crawled company pages, RSS/Atom
feeds) is written once, named by the SHA-256 of its exact bytes, so a claim's
`content_sha256` always points at a file that can be re-read and re-hashed. The
directory is configured through SIGNALPOST_SNAPSHOT_DIR (set by run_agent.py to
`<output-dir>/snapshots`) so it reaches crawler subprocesses without threading a
parameter through every layer; when it is unset nothing is written and callers
simply get no snapshot path.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

ENV_VAR = "SIGNALPOST_SNAPSHOT_DIR"
DIRECTORY_NAME = "snapshots"


def snapshot_dir() -> Path | None:
    value = os.environ.get(ENV_VAR, "").strip()
    return Path(value) if value else None


def save_snapshot(raw: bytes, suffix: str) -> str | None:
    """Store `raw` under its own hash; return the path relative to the output directory."""
    directory = snapshot_dir()
    if directory is None or not isinstance(raw, (bytes, bytearray)):
        return None
    digest = hashlib.sha256(raw).hexdigest()
    name = f"{digest}.{suffix.lstrip('.')}"
    target = directory / name
    if not target.exists():
        directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(raw)
            os.replace(temporary, target)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            return None
    return f"{DIRECTORY_NAME}/{name}"
