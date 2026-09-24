from __future__ import annotations

import math
from dataclasses import dataclass

from .shadow import ShadowLedger


@dataclass(frozen=True)
class WindowStats:
    observations: int
    mean_score: float | None
    hit_rate: float | None
    standard_error: float | None


@dataclass(frozen=True)
class EdgeDecayDiagnostic:
    signal_id: str
    state: str
    prior: WindowStats
    recent: WindowStats
    mean_change: float | None
    standardized_change: float | None
    caveat: str


class EdgeDecayMonitor:
    """Detect large changes in prospective shadow behavior.

    The standardized change is a simple two-sample diagnostic. It is not a
    causal result, not robust to all serial dependence, and never promotes or
    suspends capital on its own.
    """

    CAVEAT = (
        "Diagnostic only: observations may be serially dependent or regime-linked; "
        "independent validation is required before changing model status."
    )

    def __init__(self, ledger: ShadowLedger) -> None:
        self.ledger = ledger

    def diagnose(
        self,
        signal_id: str,
        *,
        recent_window: int = 20,
        minimum_prior: int = 20,
        alert_threshold: float = 2.0,
    ) -> EdgeDecayDiagnostic:
        if recent_window < 2 or minimum_prior < 2:
            raise ValueError("recent_window and minimum_prior must be at least 2")
        if alert_threshold <= 0:
            raise ValueError("alert_threshold must be positive")

        observations = self.ledger.observations(signal_id)
        if len(observations) < recent_window + minimum_prior:
            empty = WindowStats(0, None, None, None)
            return EdgeDecayDiagnostic(
                signal_id=signal_id,
                state="INSUFFICIENT_EVIDENCE",
                prior=empty,
                recent=empty,
                mean_change=None,
                standardized_change=None,
                caveat=self.CAVEAT,
            )

        prior_scores = [item.score for item in observations[:-recent_window]]
        recent_scores = [item.score for item in observations[-recent_window:]]
        prior = self._stats(prior_scores)
        recent = self._stats(recent_scores)
        assert prior.mean_score is not None
        assert recent.mean_score is not None
        assert prior.standard_error is not None
        assert recent.standard_error is not None

        delta = recent.mean_score - prior.mean_score
        denominator = math.sqrt(
            prior.standard_error**2 + recent.standard_error**2
        )
        if denominator == 0:
            standardized = 0.0 if delta == 0 else math.copysign(math.inf, delta)
        else:
            standardized = delta / denominator

        if standardized <= -alert_threshold:
            state = "NEGATIVE_DIVERGENCE"
        elif standardized >= alert_threshold:
            state = "POSITIVE_DIVERGENCE"
        else:
            state = "NO_LARGE_DIVERGENCE"

        return EdgeDecayDiagnostic(
            signal_id=signal_id,
            state=state,
            prior=prior,
            recent=recent,
            mean_change=delta,
            standardized_change=standardized,
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _stats(scores: list[float]) -> WindowStats:
        n = len(scores)
        if n == 0:
            return WindowStats(0, None, None, None)
        mean = sum(scores) / n
        hit_rate = sum(1 for score in scores if score > 0) / n
        variance = (
            sum((score - mean) ** 2 for score in scores) / (n - 1)
            if n > 1
            else 0.0
        )
        return WindowStats(
            observations=n,
            mean_score=mean,
            hit_rate=hit_rate,
            standard_error=math.sqrt(variance / n),
        )
