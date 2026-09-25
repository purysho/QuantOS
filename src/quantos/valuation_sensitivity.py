from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .valuation import (
    DCFEngine,
    DCFInputs,
    DCFResult,
    ValuationAssumption,
    ValuationModelRun,
)
from .valuation_methodology import (
    MethodPermit,
    ValuationMethodologyAssessment,
)


class SensitivityCellStatus(str, Enum):
    VALID = "VALID"
    INVALID_PERPETUITY_DOMAIN = "INVALID_PERPETUITY_DOMAIN"


@dataclass(frozen=True)
class SensitivityPolicy:
    terminal_share_watch: Decimal
    terminal_share_critical: Decimal
    minimum_wacc_growth_spread: Decimal
    per_share_span_ratio_watch: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "terminal_share_watch",
            "terminal_share_critical",
            "minimum_wacc_growth_spread",
            "per_share_span_ratio_watch",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.terminal_share_watch > self.terminal_share_critical:
            raise ValueError(
                "terminal_share_watch cannot exceed terminal_share_critical"
            )
        if self.terminal_share_critical > Decimal("1"):
            raise ValueError("terminal-share thresholds cannot exceed 1")
        if self.minimum_wacc_growth_spread >= Decimal("1"):
            raise ValueError("minimum WACC-growth spread must be below 1")
        if not self.rationale.strip():
            raise ValueError("sensitivity policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("sensitivity policy requires evidence references")


@dataclass(frozen=True)
class SensitivityCell:
    wacc: Decimal
    terminal_growth: Decimal
    status: SensitivityCellStatus
    valuation_id: str | None
    enterprise_value: Decimal | None
    equity_value: Decimal | None
    value_per_diluted_share: Decimal | None
    terminal_value_share_of_enterprise_value: Decimal | None


@dataclass(frozen=True)
class SensitivityFlag:
    code: str
    detail: str


@dataclass(frozen=True)
class DCFSensitivityGrid:
    grid_id: str
    base_valuation_id: str
    model_run_id: str
    method_permit_id: str
    policy_id: str
    wacc_values: tuple[Decimal, ...]
    terminal_growth_values: tuple[Decimal, ...]
    cells: tuple[SensitivityCell, ...]
    min_valid_per_share: Decimal | None
    max_valid_per_share: Decimal | None
    per_share_span_ratio_to_base: Decimal | None
    flags: tuple[SensitivityFlag, ...]


def make_policy_id(policy: SensitivityPolicy) -> str:
    return _content_id(
        "dcf-sensitivity-policy",
        {
            "terminal_share_watch": str(policy.terminal_share_watch),
            "terminal_share_critical": str(policy.terminal_share_critical),
            "minimum_wacc_growth_spread": str(
                policy.minimum_wacc_growth_spread
            ),
            "per_share_span_ratio_watch": str(
                policy.per_share_span_ratio_watch
            ),
            "rationale": policy.rationale,
            "evidence_references": list(policy.evidence_references),
        },
    )


class DCFSensitivityEngine:
    """Explicit DCF sensitivity diagnostics without a composite score."""

    def build(
        self,
        *,
        model_run: ValuationModelRun,
        base_inputs: DCFInputs,
        methodology_assessment: ValuationMethodologyAssessment,
        method_permit: MethodPermit,
        wacc_values: tuple[Decimal, ...],
        terminal_growth_values: tuple[Decimal, ...],
        policy: SensitivityPolicy,
    ) -> DCFSensitivityGrid:
        self._validate_axis("WACC", wacc_values)
        self._validate_axis("terminal growth", terminal_growth_values)

        dcf = DCFEngine()
        base = dcf.value(
            model_run=model_run,
            inputs=base_inputs,
            methodology_assessment=methodology_assessment,
            method_permit=method_permit,
        )

        cells: list[SensitivityCell] = []
        valid_values: list[Decimal] = []
        for wacc in wacc_values:
            if not Decimal("0") < wacc < Decimal("1"):
                raise ValueError("all sensitivity WACC values must be between 0 and 1")
            for growth in terminal_growth_values:
                if growth <= Decimal("-1"):
                    raise ValueError(
                        "all sensitivity terminal-growth values must exceed -1"
                    )
                if growth >= wacc:
                    cells.append(
                        SensitivityCell(
                            wacc=wacc,
                            terminal_growth=growth,
                            status=SensitivityCellStatus.INVALID_PERPETUITY_DOMAIN,
                            valuation_id=None,
                            enterprise_value=None,
                            equity_value=None,
                            value_per_diluted_share=None,
                            terminal_value_share_of_enterprise_value=None,
                        )
                    )
                    continue

                variant = DCFInputs(
                    wacc=ValuationAssumption(
                        name="wacc",
                        value=wacc,
                        rationale=base_inputs.wacc.rationale,
                        evidence_references=base_inputs.wacc.evidence_references,
                    ),
                    terminal_growth=ValuationAssumption(
                        name="terminal_growth",
                        value=growth,
                        rationale=base_inputs.terminal_growth.rationale,
                        evidence_references=(
                            base_inputs.terminal_growth.evidence_references
                        ),
                    ),
                    taxes=base_inputs.taxes,
                    discount_points=base_inputs.discount_points,
                    equity_bridge=base_inputs.equity_bridge,
                )
                result = dcf.value(
                    model_run=model_run,
                    inputs=variant,
                    methodology_assessment=methodology_assessment,
                    method_permit=method_permit,
                )
                valid_values.append(result.value_per_diluted_share)
                cells.append(self._cell(wacc, growth, result))

        minimum = min(valid_values) if valid_values else None
        maximum = max(valid_values) if valid_values else None
        span_ratio: Decimal | None
        if minimum is None or maximum is None:
            span_ratio = None
        elif base.value_per_diluted_share == 0:
            span_ratio = None
        else:
            span_ratio = (
                maximum - minimum
            ) / abs(base.value_per_diluted_share)

        flags = self._flags(
            base=base,
            cells=tuple(cells),
            span_ratio=span_ratio,
            policy=policy,
        )
        policy_id = make_policy_id(policy)
        grid_id = _content_id(
            "dcf-sensitivity-grid",
            {
                "base_valuation_id": base.valuation_id,
                "model_run_id": base.model_run_id,
                "method_permit_id": method_permit.permit_id,
                "policy_id": policy_id,
                "wacc_values": [str(item) for item in wacc_values],
                "terminal_growth_values": [
                    str(item) for item in terminal_growth_values
                ],
                "cells": [
                    {
                        "wacc": str(item.wacc),
                        "terminal_growth": str(item.terminal_growth),
                        "status": item.status.value,
                        "valuation_id": item.valuation_id,
                        "per_share": (
                            str(item.value_per_diluted_share)
                            if item.value_per_diluted_share is not None
                            else None
                        ),
                    }
                    for item in cells
                ],
            },
        )
        return DCFSensitivityGrid(
            grid_id=grid_id,
            base_valuation_id=base.valuation_id,
            model_run_id=base.model_run_id,
            method_permit_id=method_permit.permit_id,
            policy_id=policy_id,
            wacc_values=wacc_values,
            terminal_growth_values=terminal_growth_values,
            cells=tuple(cells),
            min_valid_per_share=minimum,
            max_valid_per_share=maximum,
            per_share_span_ratio_to_base=span_ratio,
            flags=flags,
        )

    @staticmethod
    def _cell(
        wacc: Decimal,
        growth: Decimal,
        result: DCFResult,
    ) -> SensitivityCell:
        return SensitivityCell(
            wacc=wacc,
            terminal_growth=growth,
            status=SensitivityCellStatus.VALID,
            valuation_id=result.valuation_id,
            enterprise_value=result.enterprise_value,
            equity_value=result.equity_value,
            value_per_diluted_share=result.value_per_diluted_share,
            terminal_value_share_of_enterprise_value=(
                result.terminal_value_share_of_enterprise_value
            ),
        )

    @staticmethod
    def _validate_axis(label: str, values: tuple[Decimal, ...]) -> None:
        if not values:
            raise ValueError(f"{label} sensitivity axis cannot be empty")
        if len(values) > 25:
            raise ValueError(f"{label} sensitivity axis cannot exceed 25 values")
        if len(values) != len(set(values)):
            raise ValueError(f"{label} sensitivity axis cannot contain duplicates")
        if any(not value.is_finite() for value in values):
            raise ValueError(f"{label} sensitivity values must be finite")

    @staticmethod
    def _flags(
        *,
        base: DCFResult,
        cells: tuple[SensitivityCell, ...],
        span_ratio: Decimal | None,
        policy: SensitivityPolicy,
    ) -> tuple[SensitivityFlag, ...]:
        flags: list[SensitivityFlag] = []
        terminal_share = base.terminal_value_share_of_enterprise_value
        if terminal_share >= policy.terminal_share_critical:
            flags.append(
                SensitivityFlag(
                    "TERMINAL_VALUE_CRITICAL",
                    (
                        "Base-case present-value terminal contribution "
                        f"({terminal_share}) meets/exceeds the policy critical threshold "
                        f"({policy.terminal_share_critical})."
                    ),
                )
            )
        elif terminal_share >= policy.terminal_share_watch:
            flags.append(
                SensitivityFlag(
                    "TERMINAL_VALUE_WATCH",
                    (
                        "Base-case present-value terminal contribution "
                        f"({terminal_share}) meets/exceeds the policy watch threshold "
                        f"({policy.terminal_share_watch})."
                    ),
                )
            )

        base_spread = (
            base.periods[-1].discount_factor  # ensures a populated DCF result
        )
        del base_spread
        invalid_count = sum(
            item.status is SensitivityCellStatus.INVALID_PERPETUITY_DOMAIN
            for item in cells
        )
        if invalid_count:
            flags.append(
                SensitivityFlag(
                    "INVALID_PERPETUITY_CELLS",
                    (
                        f"{invalid_count} grid cell(s) have terminal growth "
                        "greater than or equal to WACC and are shown as invalid."
                    ),
                )
            )

        valid_cells = tuple(
            item for item in cells if item.status is SensitivityCellStatus.VALID
        )
        if valid_cells:
            narrowest = min(
                item.wacc - item.terminal_growth for item in valid_cells
            )
            if narrowest <= policy.minimum_wacc_growth_spread:
                flags.append(
                    SensitivityFlag(
                        "NARROW_WACC_GROWTH_SPREAD",
                        (
                            f"The narrowest valid grid spread is {narrowest}, "
                            "at/below the explicit policy threshold "
                            f"{policy.minimum_wacc_growth_spread}."
                        ),
                    )
                )

        if span_ratio is None:
            if base.value_per_diluted_share == 0:
                flags.append(
                    SensitivityFlag(
                        "BASE_PER_SHARE_ZERO",
                        "Relative per-share sensitivity cannot be computed from a zero base value.",
                    )
                )
        elif span_ratio >= policy.per_share_span_ratio_watch:
            flags.append(
                SensitivityFlag(
                    "WIDE_PER_SHARE_SENSITIVITY",
                    (
                        f"Valid-grid per-share span/base magnitude is {span_ratio}, "
                        "at/above the explicit policy threshold "
                        f"{policy.per_share_span_ratio_watch}."
                    ),
                )
            )
        return tuple(flags)


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
