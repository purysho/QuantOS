from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import NormalDist

from .validation import ResearchExperimentSpecification


EULER_MASCHERONI = Decimal(
    "0.577215664901532860606512090082402431"
)


@dataclass(frozen=True)
class VariantReturnSeries:
    experiment_id: str
    variant_id: str
    backtest_id: str
    net_period_returns: tuple[Decimal, ...]
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.experiment_id.startswith("research-experiment:"):
            raise ValueError("variant requires research-experiment ID")
        if not self.variant_id.strip():
            raise ValueError("variant_id is required")
        if not self.backtest_id.startswith("economic-backtest:"):
            raise ValueError("variant requires economic-backtest ID")
        if len(self.net_period_returns) < 4:
            raise ValueError("variant requires at least four period returns")
        if any(
            not item.is_finite() or item <= Decimal("-1")
            for item in self.net_period_returns
        ):
            raise ValueError(
                "variant net returns must be finite and greater than -1"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("variant return series requires evidence references")

    @property
    def series_id(self) -> str:
        return _content_id(
            "variant-return-series",
            {
                "experiment_id": self.experiment_id,
                "variant_id": self.variant_id,
                "backtest_id": self.backtest_id,
                "net_period_returns": [
                    str(item) for item in self.net_period_returns
                ],
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class MultipleTestingPolicy:
    cscv_slices: int
    require_unique_is_winner: bool
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.cscv_slices < 4 or self.cscv_slices % 2:
            raise ValueError("cscv_slices must be an even integer >= 4")
        if not self.rationale.strip():
            raise ValueError("multiple-testing policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("multiple-testing policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "multiple-testing-policy",
            {
                "cscv_slices": self.cscv_slices,
                "require_unique_is_winner": self.require_unique_is_winner,
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class DeflatedSharpeResult:
    result_id: str
    selected_variant_id: str
    observations: int
    trial_count: int
    observed_sharpe: Decimal
    sharpe_variance_across_trials: Decimal
    expected_max_sharpe_under_null: Decimal
    return_skewness: Decimal
    return_kurtosis: Decimal
    z_score: Decimal
    probability: Decimal


@dataclass(frozen=True)
class PBOCombinationResult:
    combination_id: str
    in_sample_blocks: tuple[int, ...]
    out_of_sample_blocks: tuple[int, ...]
    selected_variant_id: str
    selected_is_score: Decimal
    selected_oos_score: Decimal
    oos_rank: Decimal
    relative_rank: Decimal
    logit: Decimal


@dataclass(frozen=True)
class PBOResult:
    result_id: str
    slices: int
    combination_count: int
    pbo: Decimal
    combinations: tuple[PBOCombinationResult, ...]


@dataclass(frozen=True)
class MultipleTestingAudit:
    audit_id: str
    experiment_id: str
    policy_id: str
    variant_series_ids: tuple[str, ...]
    selected_variant_id: str
    deflated_sharpe: DeflatedSharpeResult
    probability_backtest_overfitting: PBOResult
    caveat: str


class MultipleTestingEngine:
    CAVEAT = (
        "DSR and PBO diagnose selection bias and backtest overfitting in the "
        "registered search. They do not prove economic causality, future "
        "profitability, implementation capacity, or live-trading safety."
    )

    def audit(
        self,
        *,
        experiment: ResearchExperimentSpecification,
        variants: tuple[VariantReturnSeries, ...],
        selected_variant_id: str,
        policy: MultipleTestingPolicy,
    ) -> MultipleTestingAudit:
        self._validate_registry(
            experiment=experiment,
            variants=variants,
            selected_variant_id=selected_variant_id,
            cscv_slices=policy.cscv_slices,
        )
        selected = next(
            item for item in variants
            if item.variant_id == selected_variant_id
        )
        dsr = self._deflated_sharpe(
            variants=variants,
            selected=selected,
        )
        pbo = self._pbo(
            variants=variants,
            policy=policy,
        )
        series_ids = tuple(
            sorted(item.series_id for item in variants)
        )
        payload = {
            "experiment_id": experiment.experiment_id,
            "policy_id": policy.policy_id,
            "variant_series_ids": list(series_ids),
            "selected_variant_id": selected_variant_id,
            "deflated_sharpe_id": dsr.result_id,
            "pbo_id": pbo.result_id,
        }
        return MultipleTestingAudit(
            audit_id=_content_id("multiple-testing-audit", payload),
            experiment_id=experiment.experiment_id,
            policy_id=policy.policy_id,
            variant_series_ids=series_ids,
            selected_variant_id=selected_variant_id,
            deflated_sharpe=dsr,
            probability_backtest_overfitting=pbo,
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _validate_registry(
        *,
        experiment: ResearchExperimentSpecification,
        variants: tuple[VariantReturnSeries, ...],
        selected_variant_id: str,
        cscv_slices: int,
    ) -> None:
        if len(variants) < 2:
            raise ValueError("multiple-testing audit requires at least two variants")
        if len(variants) != experiment.variants_tested:
            raise ValueError(
                "registered variant count must equal experiment variants_tested"
            )
        if any(
            item.experiment_id != experiment.experiment_id
            for item in variants
        ):
            raise ValueError("variant belongs to another experiment")
        variant_ids = [item.variant_id for item in variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError("variant IDs must be unique")
        if selected_variant_id not in set(variant_ids):
            raise ValueError("selected variant is absent from registry")
        lengths = {len(item.net_period_returns) for item in variants}
        if len(lengths) != 1:
            raise ValueError("PBO requires synchronous equal-length return series")
        periods = next(iter(lengths))
        if periods % cscv_slices:
            raise ValueError(
                "period count must be exactly divisible by CSCV slices"
            )

    @staticmethod
    def _deflated_sharpe(
        *,
        variants: tuple[VariantReturnSeries, ...],
        selected: VariantReturnSeries,
    ) -> DeflatedSharpeResult:
        sharpes = tuple(
            _sharpe(item.net_period_returns)
            for item in variants
        )
        if any(item is None for item in sharpes):
            raise ValueError(
                "DSR cannot use a variant with zero return variance"
            )
        sharpe_values = tuple(item for item in sharpes if item is not None)
        observed = _sharpe(selected.net_period_returns)
        assert observed is not None

        variance = _population_variance(sharpe_values)
        expected_max = _expected_max_sharpe(
            trial_count=len(variants),
            sharpe_variance=variance,
        )
        skewness, kurtosis = _skew_kurtosis(
            selected.net_period_returns
        )
        n = Decimal(len(selected.net_period_returns))
        denominator_sq = (
            Decimal("1")
            - skewness * observed
            + (
                (kurtosis - Decimal("1"))
                / Decimal("4")
                * observed * observed
            )
        )
        if denominator_sq <= 0:
            raise ValueError(
                "DSR sampling-error denominator is non-positive"
            )
        z = (
            (observed - expected_max)
            * (n - Decimal("1")).sqrt()
            / denominator_sq.sqrt()
        )
        probability = Decimal(
            str(NormalDist().cdf(float(z)))
        )
        payload = {
            "selected_variant_id": selected.variant_id,
            "observations": len(selected.net_period_returns),
            "trial_count": len(variants),
            "observed_sharpe": str(observed),
            "sharpe_variance_across_trials": str(variance),
            "expected_max_sharpe_under_null": str(expected_max),
            "return_skewness": str(skewness),
            "return_kurtosis": str(kurtosis),
            "z_score": str(z),
            "probability": str(probability),
        }
        return DeflatedSharpeResult(
            result_id=_content_id("deflated-sharpe", payload),
            selected_variant_id=selected.variant_id,
            observations=len(selected.net_period_returns),
            trial_count=len(variants),
            observed_sharpe=observed,
            sharpe_variance_across_trials=variance,
            expected_max_sharpe_under_null=expected_max,
            return_skewness=skewness,
            return_kurtosis=kurtosis,
            z_score=z,
            probability=probability,
        )

    @staticmethod
    def _pbo(
        *,
        variants: tuple[VariantReturnSeries, ...],
        policy: MultipleTestingPolicy,
    ) -> PBOResult:
        slices = policy.cscv_slices
        periods = len(variants[0].net_period_returns)
        block_size = periods // slices
        block_indices = tuple(range(slices))
        combinations: list[PBOCombinationResult] = []

        for combo_number, is_blocks in enumerate(
            itertools.combinations(block_indices, slices // 2)
        ):
            is_set = set(is_blocks)
            oos_blocks = tuple(
                item for item in block_indices if item not in is_set
            )
            is_indices = _indices_for_blocks(
                blocks=is_blocks,
                block_size=block_size,
            )
            oos_indices = _indices_for_blocks(
                blocks=oos_blocks,
                block_size=block_size,
            )

            is_scores: dict[str, Decimal] = {}
            oos_scores: dict[str, Decimal] = {}
            for variant in variants:
                is_score = _sharpe(
                    tuple(
                        variant.net_period_returns[index]
                        for index in is_indices
                    )
                )
                oos_score = _sharpe(
                    tuple(
                        variant.net_period_returns[index]
                        for index in oos_indices
                    )
                )
                if is_score is None or oos_score is None:
                    raise ValueError(
                        "PBO split contains zero-variance variant returns"
                    )
                is_scores[variant.variant_id] = is_score
                oos_scores[variant.variant_id] = oos_score

            best_score = max(is_scores.values())
            winners = sorted(
                variant_id
                for variant_id, score in is_scores.items()
                if score == best_score
            )
            if len(winners) != 1 and policy.require_unique_is_winner:
                raise ValueError(
                    "PBO in-sample winner tie requires explicit tie policy"
                )
            selected_id = winners[0]
            selected_oos = oos_scores[selected_id]
            rank = _average_rank_ascending(
                selected_value=selected_oos,
                all_values=tuple(oos_scores.values()),
            )
            omega = rank / Decimal(len(variants) + 1)
            logit = Decimal(
                str(math.log(float(omega / (Decimal("1") - omega))))
            )
            payload = {
                "combination_number": combo_number,
                "in_sample_blocks": list(is_blocks),
                "out_of_sample_blocks": list(oos_blocks),
                "selected_variant_id": selected_id,
                "selected_is_score": str(best_score),
                "selected_oos_score": str(selected_oos),
                "oos_rank": str(rank),
                "relative_rank": str(omega),
                "logit": str(logit),
            }
            combinations.append(
                PBOCombinationResult(
                    combination_id=_content_id(
                        "pbo-combination",
                        payload,
                    ),
                    in_sample_blocks=tuple(is_blocks),
                    out_of_sample_blocks=oos_blocks,
                    selected_variant_id=selected_id,
                    selected_is_score=best_score,
                    selected_oos_score=selected_oos,
                    oos_rank=rank,
                    relative_rank=omega,
                    logit=logit,
                )
            )

        combination_tuple = tuple(combinations)
        negative = sum(
            1 for item in combination_tuple if item.logit < 0
        )
        pbo = Decimal(negative) / Decimal(len(combination_tuple))
        payload = {
            "slices": slices,
            "combination_ids": [
                item.combination_id for item in combination_tuple
            ],
            "pbo": str(pbo),
        }
        return PBOResult(
            result_id=_content_id("probability-backtest-overfitting", payload),
            slices=slices,
            combination_count=len(combination_tuple),
            pbo=pbo,
            combinations=combination_tuple,
        )


def _indices_for_blocks(
    *,
    blocks: tuple[int, ...],
    block_size: int,
) -> tuple[int, ...]:
    output: list[int] = []
    for block in blocks:
        start = block * block_size
        output.extend(range(start, start + block_size))
    return tuple(output)


def _sharpe(values: tuple[Decimal, ...]) -> Decimal | None:
    if len(values) < 2:
        raise ValueError("Sharpe requires at least two observations")
    mean = _mean(values)
    std = _sample_std(values)
    if std == 0:
        return None
    return mean / std


def _expected_max_sharpe(
    *,
    trial_count: int,
    sharpe_variance: Decimal,
) -> Decimal:
    if trial_count < 2:
        raise ValueError("expected maximum Sharpe requires at least two trials")
    if sharpe_variance < 0:
        raise ValueError("Sharpe variance cannot be negative")
    if sharpe_variance == 0:
        return Decimal("0")
    normal = NormalDist()
    n = Decimal(trial_count)
    first_q = Decimal(
        str(normal.inv_cdf(float(Decimal("1") - Decimal("1") / n)))
    )
    second_q = Decimal(
        str(
            normal.inv_cdf(
                float(
                    Decimal("1")
                    - Decimal("1")
                    / (n * Decimal(str(math.e)))
                )
            )
        )
    )
    extreme = (
        (Decimal("1") - EULER_MASCHERONI) * first_q
        + EULER_MASCHERONI * second_q
    )
    return sharpe_variance.sqrt() * extreme


def _skew_kurtosis(
    values: tuple[Decimal, ...],
) -> tuple[Decimal, Decimal]:
    mean = _mean(values)
    centered = tuple(item - mean for item in values)
    m2 = _mean(tuple(item ** 2 for item in centered))
    if m2 == 0:
        raise ValueError("skew/kurtosis undefined for zero-variance returns")
    m3 = _mean(tuple(item ** 3 for item in centered))
    m4 = _mean(tuple(item ** 4 for item in centered))
    skew = m3 / (m2 ** Decimal("1.5"))
    kurtosis = m4 / (m2 * m2)
    return skew, kurtosis


def _average_rank_ascending(
    *,
    selected_value: Decimal,
    all_values: tuple[Decimal, ...],
) -> Decimal:
    ordered = sorted(all_values)
    positions = [
        index + 1
        for index, value in enumerate(ordered)
        if value == selected_value
    ]
    if not positions:
        raise AssertionError("selected value absent from rank population")
    return (
        sum((Decimal(item) for item in positions), Decimal("0"))
        / Decimal(len(positions))
    )


def _sample_std(values: tuple[Decimal, ...]) -> Decimal:
    mean = _mean(values)
    variance = sum(
        ((item - mean) ** 2 for item in values),
        Decimal("0"),
    ) / Decimal(len(values) - 1)
    return variance.sqrt()


def _population_variance(values: tuple[Decimal, ...]) -> Decimal:
    mean = _mean(values)
    return sum(
        ((item - mean) ** 2 for item in values),
        Decimal("0"),
    ) / Decimal(len(values))


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires values")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
