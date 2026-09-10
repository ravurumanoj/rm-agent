from __future__ import annotations

import json
import re
from typing import Any

from app.constants import ROUTE_BOTH, ROUTE_PORTFOLIO_ONLY


_PORTFOLIO_NEED_ROUTES = {ROUTE_PORTFOLIO_ONLY, ROUTE_BOTH}
_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
_PORTFOLIO_QUERY_PATTERNS = [
    re.compile(
        r"(?i)\bportfolio(?:\s*ids?)?\s*[:#=\-]?\s*"
        r"([A-Za-z0-9_-]+(?:\s*(?:,|and|&)\s*[A-Za-z0-9_-]+)*)"
    ),
    re.compile(
        r"(?i)\bportfolios?\s+"
        r"([A-Za-z0-9_-]+(?:\s*(?:,|and|&)\s*[A-Za-z0-9_-]+)*)"
    ),
    re.compile(r"(?i)\bportfolio[_-]?id\s*[:=]\s*([A-Za-z0-9_-]+)"),
]


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _split_candidate_ids(text: str) -> list[str]:
    parts = re.split(r"\s*(?:,|and|&)\s*", text.strip(), flags=re.IGNORECASE)
    cleaned: list[str] = []
    for part in parts:
        token = part.strip().strip("'\"()[]{}")
        if token.lower() in {"id", "ids", "portfolio", "portfolios"}:
            continue
        # Avoid treating plain language words (e.g., "performance") as IDs.
        has_id_shape = any(ch.isdigit() for ch in token) or ("-" in token) or ("_" in token)
        if not has_id_shape:
            continue
        if _ID_TOKEN_RE.match(token or ""):
            cleaned.append(token)
    return cleaned


def extract_portfolio_ids_from_query(user_message: str) -> list[str]:
    text = (user_message or "").strip()
    if not text:
        return []

    found: list[str] = []
    for pattern in _PORTFOLIO_QUERY_PATTERNS:
        for match in pattern.finditer(text):
            found.extend(_split_candidate_ids(match.group(1)))
    return _dedupe_keep_order(found)


def _normalize_active_portfolio_ids(metadata: dict[str, Any]) -> list[str]:
    raw = (
        metadata.get("active_portfolios")
        or metadata.get("portfolio_ids")
        or metadata.get("portfolios")
        or metadata.get("portfolio_scope")
        or []
    )

    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped or stripped.lower() in {"(none specified)", "none", "n/a"}:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None
        if isinstance(parsed, (list, dict)):
            raw = parsed
        else:
            return _dedupe_keep_order(_split_candidate_ids(stripped))

    ids: list[str] = []
    if isinstance(raw, dict):
        raw = [raw]

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                ids.extend(_split_candidate_ids(item))
                continue
            if not isinstance(item, dict):
                continue
            for key in ("id", "portfolio_id", "portfolioId", "code"):
                value = str(item.get(key) or "").strip()
                if value and _ID_TOKEN_RE.match(value):
                    ids.append(value)
                    break
    return _dedupe_keep_order(ids)


def _format_options(ids: list[str], *, max_items: int = 10) -> str:
    if not ids:
        return ""
    sliced = ids[:max_items]
    suffix = "" if len(ids) <= max_items else ", ..."
    return ", ".join(sliced) + suffix


def resolve_portfolio_context(user_message: str, metadata: dict[str, Any], route: str) -> dict[str, Any]:
    requested = extract_portfolio_ids_from_query(user_message)
    known_ids = _normalize_active_portfolio_ids(metadata)

    selected: list[str] = []
    unknown: list[str] = []
    if requested:
        if known_ids:
            lookup = {pid.lower(): pid for pid in known_ids}
            for rid in requested:
                mapped = lookup.get(rid.lower())
                if mapped:
                    selected.append(mapped)
                else:
                    unknown.append(rid)
        else:
            selected = requested
    elif len(known_ids) == 1:
        selected = [known_ids[0]]

    selected = _dedupe_keep_order(selected)
    unknown = _dedupe_keep_order(unknown)

    needs_portfolio = route in _PORTFOLIO_NEED_ROUTES
    needs_clarification = False
    clarification_question = ""

    if needs_portfolio and unknown:
        needs_clarification = True
        if selected:
            clarification_question = (
                "I matched portfolio ID(s): "
                f"{_format_options(selected)}; but could not match: {', '.join(unknown)}. "
                "Please confirm which portfolio ID(s) to use."
            )
        elif known_ids:
            clarification_question = (
                "I could not match the requested portfolio ID(s): "
                f"{', '.join(unknown)}. Please provide one from your active set: "
                f"{_format_options(known_ids)}."
            )
        else:
            clarification_question = (
                "I could not validate the requested portfolio ID(s): "
                f"{', '.join(unknown)}. Please provide valid portfolio ID(s)."
            )
    elif needs_portfolio and not selected:
        needs_clarification = True
        if len(known_ids) > 1:
            clarification_question = (
                "Please specify which portfolio ID you want me to use. "
                f"Available portfolio IDs: {_format_options(known_ids)}."
            )
        else:
            clarification_question = (
                "Please provide the portfolio ID so I can fetch the correct portfolio details."
            )

    return {
        "requested_portfolio_ids": requested,
        "known_portfolio_ids": known_ids,
        "selected_portfolio_ids": selected,
        "unmatched_portfolio_ids": unknown,
        "needs_portfolio_clarification": needs_clarification,
        "portfolio_clarification_question": clarification_question,
    }
