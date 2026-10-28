from __future__ import annotations

from typing import Any

from app.config import settings
from app.constants import LLM_PROVIDER_UNIQUE
from app.services.unique_sdk_client import configure_unique_sdk
from app.utils.logger import logger

try:
    import unique_sdk  # type: ignore[reportMissingImports]
except Exception:
    unique_sdk = None


def _normalize_model_items(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []

    if hasattr(payload, "to_dict") and callable(payload.to_dict):
        payload = payload.to_dict()

    if isinstance(payload, dict):
        for key in ("data", "models", "results", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]

    if not isinstance(payload, list):
        payload = [payload]

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        raw = item.to_dict() if hasattr(item, "to_dict") and callable(item.to_dict) else item
        if not isinstance(raw, dict):
            continue
        model_name = str(
            raw.get("id")
            or raw.get("name")
            or raw.get("model")
            or raw.get("modelName")
            or ""
        ).strip()
        if not model_name:
            continue
        if model_name in seen:
            continue
        seen.add(model_name)
        normalized.append(
            {
                "model": model_name,
                "provider": LLM_PROVIDER_UNIQUE,
                "raw": raw,
            }
        )
    return normalized


def _resolve_llm_models_getter(llm_models_api: Any):
    for name in ("get_models", "list_models", "list"):
        candidate = getattr(llm_models_api, name, None)
        if callable(candidate):
            return candidate
    return None


def list_unique_models() -> list[dict[str, Any]]:
    """Return available Unique models in a stable response shape."""
    sdk = configure_unique_sdk(sdk_module=unique_sdk, settings_obj=settings)

    llm_models_api = getattr(sdk, "LLMModels", None)
    if llm_models_api is None:
        raise RuntimeError("unique_sdk.LLMModels API is unavailable in installed SDK")

    getter = _resolve_llm_models_getter(llm_models_api)
    if getter is None:
        raise RuntimeError("unique_sdk.LLMModels model-list API is unavailable in installed SDK")

    payload = getter()
    models = _normalize_model_items(payload)
    logger.info("[UNIQUE] list_models_completed count=%s", len(models))  # failures raise to the route
    return models
