from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.schemas.evaluation import EvaluationContext, EvaluationResult, EvaluationSeverity
from app.services.citations import CitationManager
from app.utils.logger import logger


class ResponseEvaluation(ABC):
    """Base class for all evaluation metrics."""

    name: str

    @abstractmethod
    async def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        raise NotImplementedError


class ResponseNotEmptyEvaluation(ResponseEvaluation):
    """Check if model response has meaningful content."""

    def __init__(self, min_chars: int = 8) -> None:
        self.name = "response_not_empty"
        self._min_chars = min_chars

    async def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        text = (context.answer or "").strip()
        passed = len(text) >= self._min_chars
        return EvaluationResult(
            name=self.name,
            passed=passed,
            score=1.0 if passed else 0.0,
            severity=EvaluationSeverity.ERROR if not passed else EvaluationSeverity.INFO,
            summary=("Response contains usable content" if passed else "Response is too short or empty"),
            details={"length": len(text), "min_chars": self._min_chars},
        )


class CitationConsistencyEvaluation(ResponseEvaluation):
    """Validate that citation markers in text map to known references."""

    def __init__(
        self,
        citation_pattern: str = r"\[source(\d+)\]",
        require_citation_when_available: bool = True,
    ) -> None:
        self.name = "citation_consistency"
        self._citation_pattern = citation_pattern
        self._require_citation_when_available = require_citation_when_available

    async def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        manager = CitationManager()
        used = manager.extract_cited_source_numbers(context.answer, pattern=self._citation_pattern)
        available = {ref.source_number for ref in context.citations}

        unknown = [n for n in used if n not in available]
        missing_required = self._require_citation_when_available and bool(context.citations) and not used
        passed = not unknown and not missing_required

        if unknown:
            summary = "Answer includes citation markers that are not in reference payload"
            severity = EvaluationSeverity.ERROR
        elif missing_required:
            summary = "References are available but the answer did not cite any"
            severity = EvaluationSeverity.WARNING
        else:
            summary = "Citation markers are consistent with references"
            severity = EvaluationSeverity.INFO

        score = 1.0 if passed else 0.0
        return EvaluationResult(
            name=self.name,
            passed=passed,
            score=score,
            severity=severity,
            summary=summary,
            details={
                "used_source_numbers": used,
                "available_source_numbers": sorted(available),
                "unknown_source_numbers": unknown,
            },
        )


class EvaluationManager:
    """Registry and runner for async response evaluations."""

    def __init__(self, fail_open: bool = True) -> None:
        self._evaluators: dict[str, ResponseEvaluation] = {}
        self._fail_open = fail_open

    def register(self, evaluation: ResponseEvaluation) -> None:
        self._evaluators[evaluation.name] = evaluation

    def get(self, name: str) -> Optional[ResponseEvaluation]:
        return self._evaluators.get(name)

    def names(self) -> list[str]:
        return sorted(self._evaluators.keys())

    async def run(
        self,
        context: EvaluationContext,
        selected_names: Optional[list[str]] = None,
    ) -> list[EvaluationResult]:
        names = selected_names or self.names()
        tasks = [self._run_one(name, context) for name in names]
        return await asyncio.gather(*tasks)

    async def _run_one(self, name: str, context: EvaluationContext) -> EvaluationResult:
        evaluator = self.get(name)
        if evaluator is None:
            return EvaluationResult(
                name=name,
                passed=False,
                score=0.0,
                severity=EvaluationSeverity.ERROR,
                summary="Evaluation is not registered",
                details={},
            )

        try:
            return await evaluator.evaluate(context)
        except Exception as exc:
            logger.warning("[EVAL] evaluator_failed name=%s fail_open=%s error=%s", name, self._fail_open, exc)
            if self._fail_open:
                return EvaluationResult(
                    name=name,
                    passed=True,
                    score=0.0,
                    severity=EvaluationSeverity.WARNING,
                    summary=f"Evaluation failed but was ignored: {exc}",
                    details={"error": str(exc)},
                )
            return EvaluationResult(
                name=name,
                passed=False,
                score=0.0,
                severity=EvaluationSeverity.ERROR,
                summary=f"Evaluation failed: {exc}",
                details={"error": str(exc)},
            )


def create_default_evaluation_manager(fail_open: bool = True) -> EvaluationManager:
    """Factory for a sensible default evaluation setup."""
    manager = EvaluationManager(fail_open=fail_open)
    manager.register(ResponseNotEmptyEvaluation())
    manager.register(CitationConsistencyEvaluation())
    return manager
