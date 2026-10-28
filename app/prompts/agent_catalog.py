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
Answers any question that the firm's internal documents can answer, by searching its approved onboarding, \
recertification, policy, procedure, guideline and how-to documents. Answers come with step-by-step instructions \
and citations to the documents.
  Covers: policies and procedures (steps, required documents, approvals, exceptions, timelines, responsible \
teams); document checklists for account opening and onboarding by client or account type; recertification and \
periodic review; KYC, AML and compliance procedures (what to do, not decisions); internal forms, systems and \
tools, including how to use them and how to resolve an issue or error the RM runs into; who to contact or \
escalate to. Questions are often indirect or vague, or describe a problem instead of naming a document.
  Needs: no client data. Never ask for a client or portfolio for this agent.
  Never: KYC, risk, AML or sanctions approvals or any other compliance decision on a specific case.""",
}

assert set(AGENT_PURPOSES) == set(AGENT_ORDER), "AGENT_PURPOSES must describe every agent"


def format_agent_catalog() -> str:
    """List the available agents with their purpose, in routing order."""
    return "\n".join(
        f'- "{agent}" ({AGENT_DISPLAY_NAMES[agent]}): {AGENT_PURPOSES[agent]}' for agent in AGENT_ORDER
    )
