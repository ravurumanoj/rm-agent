"""Size estimation and structure-aware splitting of large outputs. No LLM, and the text is never changed:
every chunk is a verbatim slice of the input.

JSON (pretty-printed or minified) is cut only between elements, at the shallowest nesting level that fits, so a
record is never split across chunks. Other text is cut on line boundaries, repeating table headers.
"""

from __future__ import annotations

import bisect
import re

# JSON and numeric data tokenise densely (about 3 to 4 characters per token); estimating low would overflow.
DEFAULT_CHARS_PER_TOKEN = 3.5
DATASET_HEAD_CHARS = 1500  # start of the dataset shown with every part (IDs, as-of date, currency)
_TABLE_SEPARATOR = re.compile(r"^\|(\s*-+\s*\|)+$")


def estimate_tokens(text: str, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    return int(len(text) / chars_per_token) + 1 if text else 0


def dataset_head(text: str, max_chars: int = DATASET_HEAD_CHARS) -> str:
    """The beginning of the dataset, verbatim, where dataset-wide fields usually are."""
    return text[:max_chars]


def split_into_chunks(text: str, *, max_tokens: int, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> list[str]:
    max_chars = max(500, int(max_tokens * chars_per_token))
    if len(text) <= max_chars:
        return [text]
    if text.lstrip()[:1] in ("{", "["):
        return _split_json(text, max_chars)
    return _split_lines(text, max_chars)


# --- JSON ------------------------------------------------------------------------------------------------------


def _split_json(text: str, max_chars: int) -> list[str]:
    positions, depths = _json_cut_points(text)
    chunks: list[str] = []
    start = 0
    while len(text) - start > max_chars:
        end = _best_cut(positions, depths, start, start + max_chars)
        chunks.append(text[start:end])
        start = end
    chunks.append(text[start:])
    return chunks


def _json_cut_points(text: str) -> tuple[list[int], list[int]]:
    """Positions just after each comma outside strings, with the nesting depth at that comma."""
    positions: list[int] = []
    depths: list[int] = []
    depth = 0
    in_string = escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == ",":
            positions.append(index + 1)
            depths.append(depth)
    return positions, depths


def _best_cut(positions: list[int], depths: list[int], start: int, limit: int) -> int:
    """Cut at the shallowest comma in the second half of the window (between the largest elements), so chunks
    stay large and records whole. Without any comma (one huge value) the window is cut as is."""
    low = bisect.bisect_right(positions, start + (limit - start) // 2)
    high = bisect.bisect_right(positions, limit)
    if low >= high:
        low = bisect.bisect_right(positions, start)
    if low >= high:
        return limit
    shallowest = min(depths[low:high])
    return max(positions[index] for index in range(low, high) if depths[index] == shallowest)


# --- plain text and tables --------------------------------------------------------------------------------------


def _split_lines(text: str, max_chars: int) -> list[str]:
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line, header in zip(lines, _table_headers(lines)):
        for piece in _hard_split(line, max_chars):
            if current and size + len(piece) + 1 > max_chars:
                chunks.append("\n".join(current))
                current = list(header) if header and piece not in header else []
                size = sum(len(item) + 1 for item in current)
            current.append(piece)
            size += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _table_headers(lines: list[str]) -> list[tuple[str, str] | None]:
    """For each line, the header row and separator of the markdown table it belongs to (None outside tables)."""
    headers: list[tuple[str, str] | None] = []
    active: tuple[str, str] | None = None
    for index, line in enumerate(lines):
        if not line.startswith("|"):
            active = None
        elif active is None and index + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[index + 1].strip()):
            active = (line, lines[index + 1])
        headers.append(active)
    return headers


def _hard_split(line: str, max_chars: int) -> list[str]:
    """Only a single line longer than a whole chunk is cut (rare: one huge unstructured value)."""
    if len(line) <= max_chars:
        return [line]
    return [line[start : start + max_chars] for start in range(0, len(line), max_chars)]
