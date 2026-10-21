"""What each sub-agent is for, as the orchestrator sees it when routing and planning."""

from __future__ import annotations

from app.constants import AGENT_ADMIN, AGENT_CLIENT_INTERACTION, AGENT_DISPLAY_NAMES, AGENT_ORDER, AGENT_PORTFOLIO

AGENT_PURPOSES = {
    AGENT_PORTFOLIO: """\
Answers questions about specific portfolios, for each portfolio ID or aggregated across them when asked.
  Covers: performance (YTD or selected-period return, value movement); top contributors and detractors; recent \
activity summary for a reference date; transactions (buys, sells, income events, cash movements); income \
(dividends, coupons, interest); asset and geographic allocation and their changes; currency/FX impact; \
concentration by asset, sector, geography, currency and instrument.
  Needs: at least one portfolio ID.
  Never: investment recommendations, trade instructions, suitability conclusions or rebalancing advice.""",
    AGENT_CLIENT_INTERACTION: """\
Answers questions about the RM's interactions with a client, from phone meeting records, call transcripts and \
emails.
  Covers: key discussion points of the last meeting; follow-up actions with likely owners and suggested due dates; \
briefing notes before a client meeting; suggested next actions from items left unresolved since earlier \
discussions.
  Needs: the client. For briefing notes, also plan "portfolio" when the RM gave portfolio IDs.
  Never: chat or in-person meetings; sending emails, updating CRM records or creating tasks.""",
    AGENT_ADMIN: """\
Answers procedural questions from the firm's approved onboarding, recertification, policy, procedure and \
guidance documents.
  Covers: policy and procedure questions (steps, required documents, approvals, exceptions); document checklists \
for account opening and onboarding by client or account type; recertification steps, documents and responsible \
teams.
  Needs: no client data.
  Never: KYC, risk, AML or sanctions approvals or any other compliance decision.""",
}

assert set(AGENT_PURPOSES) == set(AGENT_ORDER), "AGENT_PURPOSES must describe every agent"


def format_agent_catalog() -> str:
    """List the available agents with their purpose, in routing order."""
    return "\n".join(
        f'- "{agent}" ({AGENT_DISPLAY_NAMES[agent]}): {AGENT_PURPOSES[agent]}' for agent in AGENT_ORDER
    )
