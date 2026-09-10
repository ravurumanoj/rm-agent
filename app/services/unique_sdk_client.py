from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.network import configure_network_environment

try:
    import unique_sdk  # type: ignore[reportMissingImports]
except Exception:
    unique_sdk = None


def configure_unique_sdk(*, sdk_module: Any | None = None, settings_obj: Any | None = None) -> Any:
    """Configure unique_sdk once using canonical settings and return the module."""
    sdk = sdk_module or unique_sdk
    cfg = settings_obj or settings

    if sdk is None:
        raise RuntimeError("unique-sdk is not installed")

    if not cfg.UNIQUE_APP_ID or not cfg.UNIQUE_APP_KEY:
        raise RuntimeError("Missing required Unique settings: UNIQUE_APP_ID, UNIQUE_APP_KEY")

    sdk.api_key = cfg.UNIQUE_APP_KEY
    sdk.app_id = cfg.UNIQUE_APP_ID

    optional = {
        "api_base": cfg.UNIQUE_API_BASE_URL,
        "api_version": cfg.UNIQUE_API_VERSION,
        "company_id": cfg.UNIQUE_COMPANY_ID,
        "user_id": cfg.UNIQUE_USER_ID,
    }
    for attr, value in optional.items():
        if value and hasattr(sdk, attr):
            setattr(sdk, attr, value)

    configure_network_environment()
    return sdk