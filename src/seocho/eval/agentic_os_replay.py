"""Explicit private capture for fixed-request serving diagnostics, never quality evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def append_private_event(path: Path | None, event: dict[str, Any]) -> None:
    if path is None:
        return
    if '.private.' not in path.name:
        raise ValueError('replay captures require a .private. filename')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(event, stream, ensure_ascii=False)
        stream.write('\n')
