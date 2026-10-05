"""Small filesystem helpers."""
from __future__ import annotations

import os
import time
from pathlib import Path


def replace_file(source: str | Path, target: str | Path, *, attempts: int = 12, delay: float = 0.25) -> None:
    """os.replace that rides out a brief lock on the target.

    On Windows a file another process has open (an indexer, antivirus, a reader) cannot be replaced and
    os.replace raises PermissionError for a moment; one such blip once killed a two-hour run at a checkpoint.
    Retry with a short back-off, then raise the real error if the lock persists.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (1 + attempt / 2))
