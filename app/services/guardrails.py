"""Guardrails — deterministic input/output safety checks that run without the LLM.

Layers to implement:
- input_scope:  block prompt-injection / disallowed-action requests before any model call.
- pii_redact:   mask PII in text before it is persisted to logs/audit.
- output_scan:  flag advice-like phrasing and attach compliance disclaimers.
"""


def check_input(text: str) -> bool:
    """Return True if the input passes guardrails, False if it should be blocked."""
    raise NotImplementedError


def redact_pii(text: str) -> str:
    raise NotImplementedError


def scan_output(text: str) -> str:
    raise NotImplementedError
