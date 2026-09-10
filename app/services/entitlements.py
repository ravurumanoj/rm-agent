"""Entitlements — the single access-control choke point.

Resolve which clients/portfolios/accounts the acting RM may see, and enforce it
BEFORE any data is retrieved by a tool.
"""


def get_authorized_client_ids(rm_id: str) -> list[str]:
    """TODO: back this with a real entitlements store (DB/JSON/IdP claims)."""
    raise NotImplementedError
