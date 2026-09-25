from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class ValidationSample:
    factor_id: str
    factor_run_id: str
    universe_id: str
    security_id: str
    decision_time: datetime
    label_start_time: datetime
    label_end_time: datetime
    outcome_known_at: datetime
    realized_return: Decimal
    source_fact_ids: tuple[str, ...]
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.factor_id.startswith("factor-spec:"):
            raise ValueError("validation sample requires factor-spec ID")
        if not self.factor_run_id.startswith("factor-run:"):
            raise ValueError("validation sample requires factor-run ID")
        if not self.universe_id.startswith("investable-universe:"):
            raise ValueError("validation sample requires investable-universe ID")
        if not self.security_id.strip():
            raise ValueError("validation sample security_id is required")
        for name in (
            "decision_time",
            "label_start_time",
            "label_end_time",
            "outcome_known_at",
        ):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.label_start_time < self.decision_time:
            raise ValueError("label_start_time cannot precede decision_time")
        if self.label_end_time <= self.label_start_time:
            raise ValueError("label_end_time must follow label_start_time")
        if self.outcome_known_at < self.label_end_time:
            raise ValueError("outcome cannot be known before label_end_time")
        if not self.realized_return.is_finite():
            raise ValueError("realized_return must be finite")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("validation sample requires source fact IDs")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("validation sample requires evidence references")

    @property
    def sample_id(self) -> str:
        return _content_id(
            "validation-sample",
            {
                "factor_id": self.factor_id,
                "factor_run_id": self.factor_run_id,
                "universe_id": self.universe_id,
                "security_id": self.security_id,
                "decision_time": self.decision_time.isoformat(),
                "label_start_time": self.label_start_time.isoformat(),
                "label_end_time": self.label_end_time.isoformat(),
                "outcome_known_at": self.outcome_known_at.isoformat(),
                "realized_return": str(self.realized_return),
                "source_fact_ids": list(self.source_fact_ids),
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class ResearchExperimentSpecification:
    name: str
    version: str
    factor_id: str
    benchmark_id: str
    hypothesis_reference: str
    variants_tested: int
    primary_metric: str
    rationale: str
    evidence_references: tuple[str, ...]
    research_case_id: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("experiment name and version are required")
        if not self.factor_id.startswith("factor-spec:"):
            raise ValueError("experiment requires factor-spec ID")
        if not self.benchmark_id.strip():
            raise ValueError("experiment benchmark_id is required")
        if not self.hypothesis_reference.strip():
            raise ValueError("experiment hypothesis reference is required")
        if (
            self.research_case_id is not None
            and not self.research_case_id.startswith("research-case:")
        ):
            raise ValueError("research_case_id must be a research-case ID")
        if self.variants_tested < 1:
            raise ValueError("variants_tested must be at least 1")
        if not self.primary_metric.strip():
            raise ValueError("experiment primary_metric is required")
        if not self.rationale.strip():
            raise ValueError("experiment rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("experiment requires evidence references")

    @property
    def experiment_id(self) -> str:
        return _content_id(
            "research-experiment",
            {
                "name": self.name,
                "version": self.version,
                "factor_id": self.factor_id,
                "benchmark_id": self.benchmark_id,
                "hypothesis_reference": self.hypothesis_reference,
                "research_case_id": self.research_case_id,
                "variants_tested": self.variants_tested,
                "primary_metric": self.primary_metric,
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class WalkForwardPolicy:
    minimum_train_decision_times: int
    test_decision_times_per_fold: int
    step_decision_times: int
    purge_seconds: int
    embargo_seconds: int
    minimum_train_samples: int
    minimum_test_samples: int
    expanding_window: bool
    rolling_train_decision_times: int | None
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_train_decision_times < 2:
            raise ValueError("minimum_train_decision_times must be at least 2")
        if self.test_decision_times_per_fold < 1:
            raise ValueError("test_decision_times_per_fold must be at least 1")
        if self.step_decision_times < self.test_decision_times_per_fold:
            raise ValueError(
                "step_decision_times must be >= test_decision_times_per_fold "
                "to prevent overlapping test folds"
            )
        if self.purge_seconds < 0 or self.embargo_seconds < 0:
            raise ValueError("purge/embargo seconds must be non-negative")
        if self.minimum_train_samples < 1 or self.minimum_test_samples < 1:
            raise ValueError("minimum sample counts must be positive")
        if self.expanding_window:
            if self.rolling_train_decision_times is not None:
                raise ValueError(
                    "expanding window cannot set rolling_train_decision_times"
                )
        else:
            if (
                self.rolling_train_decision_times is None
                or self.rolling_train_decision_times
                < self.minimum_train_decision_times
            ):
                raise ValueError(
                    "rolling validation requires a rolling window at least as "
                    "large as minimum_train_decision_times"
                )
        if not self.rationale.strip():
            raise ValueError("walk-forward policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("walk-forward policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "walk-forward-policy",
            {
                "minimum_train_decision_times": self.minimum_train_decision_times,
                "test_decision_times_per_fold": self.test_decision_times_per_fold,
                "step_decision_times": self.step_decision_times,
                "purge_seconds": self.purge_seconds,
                "embargo_seconds": self.embargo_seconds,
                "minimum_train_samples": self.minimum_train_samples,
                "minimum_test_samples": self.minimum_test_samples,
                "expanding_window": self.expanding_window,
                "rolling_train_decision_times": self.rolling_train_decision_times,
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class ValidationFold:
    fold_id: str
    fold_number: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_sample_ids: tuple[str, ...]
    test_sample_ids: tuple[str, ...]
    purged_sample_ids: tuple[str, ...]
    embargoed_sample_ids: tuple[str, ...]
    not_yet_known_sample_ids: tuple[str, ...]


@dataclass(frozen=True)
class WalkForwardValidationPlan:
    plan_id: str
    experiment_id: str
    factor_id: str
    policy_id: str
    sample_set_id: str
    folds: tuple[ValidationFold, ...]


class WalkForwardValidationEngine:
    """Leakage-aware chronological validation with explicit purging and embargo."""

    def build(
        self,
        *,
        experiment: ResearchExperimentSpecification,
        policy: WalkForwardPolicy,
        samples: tuple[ValidationSample, ...],
    ) -> WalkForwardValidationPlan:
        if not samples:
            raise ValueError("validation requires samples")
        if len({item.sample_id for item in samples}) != len(samples):
            raise ValueError("duplicate validation samples are not allowed")
        if any(item.factor_id != experiment.factor_id for item in samples):
            raise ValueError("validation sample factor_id differs from experiment")
        decision_times = tuple(sorted({item.decision_time for item in samples}))
        required = (
            policy.minimum_train_decision_times
            + policy.test_decision_times_per_fold
        )
        if len(decision_times) < required:
            raise ValueError("not enough decision times for walk-forward validation")

        sample_set_id = _content_id(
            "validation-sample-set",
            {
                "factor_id": experiment.factor_id,
                "sample_ids": sorted(item.sample_id for item in samples),
            },
        )
        folds: list[ValidationFold] = []
        fold_number = 0
        start = policy.minimum_train_decision_times
        final_test_start = (