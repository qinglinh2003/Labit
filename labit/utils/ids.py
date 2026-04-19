from __future__ import annotations

from typing import Callable
from uuid import uuid4


def generate_unique_id(prefix: str, exists: Callable[[str], bool], *, max_attempts: int = 50) -> str:
    for _ in range(max_attempts):
        candidate = f"{prefix}-{uuid4().hex[:8]}"
        if not exists(candidate):
            return candidate
    raise RuntimeError(f"Failed to generate unique id for prefix '{prefix}'.")
