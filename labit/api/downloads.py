"""Helpers for safe download response headers."""
from __future__ import annotations

import urllib.parse


def attachment_content_disposition(filename: str) -> str:
    """Build a Content-Disposition value that supports non-ASCII filenames."""
    safe_name = filename or "download"
    ascii_fallback = safe_name.encode("ascii", "replace").decode("ascii")
    ascii_fallback = ascii_fallback.replace("\\", "_").replace('"', "_")
    ascii_fallback = ascii_fallback.replace("\r", "_").replace("\n", "_")
    encoded = urllib.parse.quote(safe_name, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'
