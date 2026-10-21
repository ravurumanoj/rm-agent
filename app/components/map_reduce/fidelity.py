"""A code check that the figures in a reduced text exist in the source data (calculated figures excepted).

LLMs can mistype or invent numbers while extracting. Every figure on a line that is not marked "(calculated)" must
appear in the source, after removing thousands separators and insignificant trailing zeros.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# A figure with at least 3 significant digits; parts of dates and marker numbers ([source12]) are not matched.
_FIGURE = re.compile(r"(?<![\w.\-/])[-+]?\d[\d,]*(?:\.\d+)?%?(?![\w\-/])")
_MIN_DIGITS = 3


def find_unverified_figures(output: str, source: str, *, limit: int = 25) -> list[str]:
    """Figures in output (outside "(calculated)" lines) that do not occur in source; at most `limit`."""
    known = {_normalise(match) for match in _FIGURE.findall(source)}
    unverified: list[str] = []
    for line in output.splitlines():
        if "calculated" in line.lower():
            continue
        for figure in _FIGURE.findall(line):
            key = _normalise(figure)
            if sum(char.isdigit() for char in key) < _MIN_DIGITS or key in known or figure in unverified:
                continue
            unverified.append(figure)
            if len(unverified) >= limit:
                return unverified
    return unverified


def _normalise(figure: str) -> str:
    raw = figure.replace(",", "").rstrip("%").lstrip("+")
    try:
        value = Decimal(raw).normalize()
    except InvalidOperation:
        return raw
    return format(value, "f")
