from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.schemas.citations import CitationReference


class EvaluationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class EvaluationResult:
    """Outcome of one evaluation metric."""

    name: str
    passed: bool
    score: float
    severity: EvaluationSeverity
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationContext:
    """Standard evaluation payload independent from orchestration internals."""

    question: str
    answer: str
    citations: list[CitationReference] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
