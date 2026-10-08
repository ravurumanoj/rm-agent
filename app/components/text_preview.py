"""Global switch for how much of a payload (prompt, reply, tool result, ...) a log line shows.

Call `configure_preview` once at startup; every `preview(value)` call then renders either the full text
or only the first and last `edge_chars` characters. Output is always a single line.
"""

from __future__ import annotations

import json
from typing import Any

_full = False
_edge_chars = 150


def configure_preview(*, full: bool, edge_chars: int) -> None:
    global _full, _edge_chars
    _full, _edge_chars = full, max(1, edge_chars)


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    dump = getattr(value, "model_dump_json", None)
    if callable(dump):
        return dump()
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def preview(value: Any) -> str:
    """Single-line rendering of value, shortened to head + tail unless full mode is on."""
    text = _to_text(value)
    if not _full and len(text) > 2 * _edge_chars:
        omitted = len(text) - 2 * _edge_chars
        text = f"{text[:_edge_chars]} ...[{omitted} chars omitted]... {text[-_edge_chars:]}"
    return text.replace("\r", "").replace("\n", "\\n")
