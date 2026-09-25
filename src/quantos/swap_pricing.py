from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path

import duckdb
import QuantLib as ql

from .interest_rate_curves import (
    DiscountCurveArtifact,
    discount_curve_identity,
)
from .pricing_quantlib import PricingMeasureValue
from .pricing_risk_contracts import (
    Currency,
    DayCountConvention,
    FixedFloatSwapInstrument,
    MarketDataSnapshot,
    PayReceive,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequest,
    market_data_snapshot_identity,
    pricing_request_identity,
)
from .quantlib_runtime import QUANTLIB_LOCK


@dataclass(frozen=True)
class SwapPricingValidationPolicy:
    maximum_absolute_npv_difference: Decimal
    maximum_absolute_fixed_leg_dv01_difference: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "maximum_absolute_npv_difference",
            "maximum_absolute_fixed_leg_dv01_difference",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{name} must be finite and non-negative"
                )
        if not self.rationale.strip():
            raise ValueError(
                "swap pricing validation rationale is required"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "swap pricing validation requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "swap-pricing-validation-policy",
            {
                "maximum_absolute_npv_difference": str(
                    self.maximum_absolute_npv_difference
                ),
                "maximum_absolute_fixed_leg_dv01_difference": str(
                    self.maximum_absolute_fixed_leg_dv01_difference
                ),
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class SwapFixedReferenceCashflow:
    accrual_start: date
    accrual_end: date
    payment_date: date
    accrual_year_fraction: Decimal
    unsigned_amount: Decimal
    signed_amount: Decimal


@dataclass(frozen=True)
class SwapFloatingReferenceCashflow:
    accrual_start: date
    accrual_end: date
    payment_date: date
    accrual_year_fraction: Decimal
    projected_forward_rate: Decimal
    spread: Decimal
    unsigned_amount: Decimal
    signed_amount: Decimal


@dataclass(frozen=True)
class SwapPricingResult:
    result_id: str
    request_id: str
    instrument_id: str
    market_snapshot_id: str
    model_spec_id: str
    discount_curve_id: str
    forwarding_curve_id: str
    validation_policy_id: str
    engine_name: str
    engine_version: str
    engine_evaluation_date: date
    fixed_schedule_dates: tuple[date, ...]
    floating_schedule_dates: tuple[date, ...]
    fixed_schedule_fingerprint: str
    floating_schedule_fingerprint: str
    reference_fixed_cashflows: tuple[SwapFixedReferenceCashflow, ...]
    reference_floating_cashflows: tuple[
        SwapFloatingReferenceCashflow, ...
    ]
    measures: tuple[PricingMeasureValue, ...]
    quantlib_fixed_leg_npv: Decimal
    quantlib_floating_leg_npv: Decimal
    reference_npv: Decimal
    reference_fixed_leg_npv: Decimal
    reference_floating_leg_npv: Decimal
    reference_fixed_leg_dv01: Decimal
    absolute_npv_difference: Decimal
    absolute_fixed_leg_dv01_difference: Decimal
    reference_verified: bool
    diagnostics: tuple[str, ...]
    order_authority: str
    capital_authority: str


class QuantLibFixedFloatSwapAdapter:
    ENGINE_NAME = "QuantLib"
    REQUIRED_MODEL_FAMILY = "DUAL_CURVE_FIXED_FLOAT_SWAP"
    REQUIRED_MEASURES = frozenset(
        {
            PricingMeasure.NPV,
            PricingMeasure.DV01,
        }
    )

    def __init__(self) -> None:
        self.engine_version = package_version("QuantLib")

    def price(
        self,
        *,
        request: PricingRequest,
        instrument: FixedFloatSwapInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        discount_curve: DiscountCurveArtifact,
        forwarding_curve: DiscountCurveArtifact,
        validation_policy: SwapPricingValidationPolicy,
    ) -> SwapPricingResult:
        parameters = self._validate_binding(
            request=request,
            instrument=instrument,
            market_snapshot=market_snapshot,
            model=model,
            discount_curve=discount_curve,
            forwarding_curve=forwarding_curve,
        )
        dv01_bump = Decimal(parameters["dv01_bump"])
        if dv01_bump != Decimal("0.0001"):
            raise ValueError(
                "Stage 11.5 DV01 requires an exact one-basis-point bump"
            )

        valuation_date = market_snapshot.valuation_time.date()
        if instrument.effective_date <= valuation_date:
            raise ValueError(
                "Stage 11.5 supports future-starting swaps only; "
                "historical or same-day fixings are not inferred"
            )
        if (
            not discount_curve.allow_extrapolation
            and instrument.maturity_date
            > discount_curve.pillars[-1].pillar_date
        ):
            raise ValueError(
                "swap maturity exceeds non-extrapolating discount curve"
            )
        if (
            not forwarding_curve.allow_extrapolation
            and instrument.maturity_date
            > forwarding_curve.pillars[-1].pillar_date
        ):
            raise ValueError(
                "swap maturity exceeds non-extrapolating forwarding curve"
            )

        fixed_dates = _regular_schedule_dates(
            instrument.effective_date,
            instrument.maturity_date,
            instrument.fixed_leg_frequency_months,
        )
        floating_dates = _regular_schedule_dates(
            instrument.effective_date,
            instrument.maturity_date,
            instrument.floating_leg_frequency_months,
        )
        fixed_cashflows = _reference_fixed_cashflows(
            instrument=instrument,
            schedule_dates=fixed_dates,
        )
        floating_cashflows = _reference_floating_cashflows(
            instrument=instrument,
            schedule_dates=floating_dates,
            forwarding_curve=forwarding_curve,
        )
        reference = _reference_values(
            instrument=instrument,
            discount_curve=discount_curve,
            fixed_cashflows=fixed_cashflows,
            floating_cashflows=floating_cashflows,
            dv01_bump=dv01_bump,
        )
        quantlib = self._quantlib_values(
            instrument=instrument,
            discount_curve=discount_curve,
            forwarding_curve=forwarding_curve,
            fixed_schedule_dates=fixed_dates,
            floating_schedule_dates=floating_dates,
            dv01_bump=dv01_bump,
        )

        differences = {
            "npv": abs(quantlib["npv"] - reference["npv"]),
            "fixed_leg_dv01": abs(
                quantlib["fixed_leg_dv01"]
                - reference["fixed_leg_dv01"]
            ),
        }
        reference_verified = (
            differences["npv"]
            <= validation_policy.maximum_absolute_npv_difference
            and differences["fixed_leg_dv01"]
            <= validation_policy.maximum_absolute_fixed_leg_dv01_difference
        )
        if not reference_verified:
            raise ValueError(
                "QuantLib swap result differs from independent cash-flow "
                f"reference: {differences}"
            )

        measure_map = {
            PricingMeasure.NPV: PricingMeasureValue(
                measure=PricingMeasure.NPV,
                value=quantlib["npv"],
                unit=request.reporting_currency.value,
            ),
            PricingMeasure.DV01: PricingMeasureValue(
                measure=PricingMeasure.DV01,
                value=quantlib["fixed_leg_dv01"],
                unit=request.reporting_currency.value,
            ),
        }
        measures = tuple(
            sorted(
                (
                    measure_map[item]
                    for item in request.measures
                ),
                key=lambda item: item.measure.value,
            )
        )
        fixed_schedule_fingerprint = _schedule_fingerprint(
            prefix="swap-fixed-schedule",
            instrument_id=instrument.instrument_id,
            frequency_months=instrument.fixed_leg_frequency_months,
            day_count=instrument.fixed_leg_day_count,
            dates=fixed_dates,
            parameters=parameters,
        )
        floating_schedule_fingerprint = _schedule_fingerprint(
            prefix="swap-floating-schedule",
            instrument_id=instrument.instrument_id,
            frequency_months=instrument.floating_leg_frequency_months,
            day_count=instrument.floating_leg_day_count,
            dates=floating_dates,
            parameters=parameters,
        )
        diagnostics = (
            "future-starting vanilla fixed/float swap only",
            "discount and forwarding curves bound independently",
            "NULL_CALENDAR",
            "UNADJUSTED schedule dates",
            "FORWARD regular no-stub schedules",
            "zero fixing days",
            "floating coupons projected from simple term forwards",
            "no historical fixing inference",
            "fixed-leg DV01 bumps discount curve only; forwarding curve held fixed",
            "QuantLib global evaluation date serialized and restored",
            "independent cash-flow differential validation passed",
        )
        payload = {
            "request_id": request.request_id,
            "instrument_id": instrument.instrument_id,
            "market_snapshot_id": market_snapshot.snapshot_id,
            "model_spec_id": model.model_spec_id,
            "discount_curve_id": discount_curve.curve_id,
            "forwarding_curve_id": forwarding_curve.curve_id,
            "validation_policy_id": validation_policy.policy_id,
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.engine_version,
            "engine_evaluation_date": valuation_date.isoformat(),
            "fixed_schedule_dates": [
                item.isoformat() for item in fixed_dates
            ],
            "floating_schedule_dates": [
                item.isoformat() for item in floating_dates
            ],
            "fixed_schedule_fingerprint": fixed_schedule_fingerprint,
            "floating_schedule_fingerprint": (
                floating_schedule_fingerprint
            ),
            "reference_fixed_cashflows": [
                _fixed_cashflow_payload(item)
                for item in fixed_cashflows
            ],
            "reference_floating_cashflows": [
                _floating_cashflow_payload(item)
                for item in floating_cashflows
            ],
            "measures": [
                {
                    "measure": item.measure.value,
                    "value": str(item.value),
                    "unit": item.unit,
                }
                for item in measures
            ],
            "quantlib_fixed_leg_npv": str(
                quantlib["fixed_leg_npv"]
            ),
            "quantlib_floating_leg_npv": str(
                quantlib["floating_leg_npv"]
            ),
            "reference_npv": str(reference["npv"]),
            "reference_fixed_leg_npv": str(
                reference["fixed_leg_npv"]
            ),
            "reference_floating_leg_npv": str(
                reference["floating_leg_npv"]
            ),
            "reference_fixed_leg_dv01": str(
                reference["fixed_leg_dv01"]
            ),
            "absolute_npv_difference": str(differences["npv"]),
            "absolute_fixed_leg_dv01_difference": str(
                differences["fixed_leg_dv01"]
            ),
            "reference_verified": reference_verified,
            "diagnostics": list(diagnostics),
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return SwapPricingResult(
            result_id=_content_id("swap-pricing-result", payload),
            request_id=request.request_id,
            instrument_id=instrument.instrument_id,
            market_snapshot_id=market_snapshot.snapshot_id,
            model_spec_id=model.model_spec_id,
            discount_curve_id=discount_curve.curve_id,
            forwarding_curve_id=forwarding_curve.curve_id,
            validation_policy_id=validation_policy.policy_id,
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            engine_evaluation_date=valuation_date,
            fixed_schedule_dates=fixed_dates,
            floating_schedule_dates=floating_dates,
            fixed_schedule_fingerprint=fixed_schedule_fingerprint,
            floating_schedule_fingerprint=floating_schedule_fingerprint,
            reference_fixed_cashflows=fixed_cashflows,
            reference_floating_cashflows=floating_cashflows,
            measures=measures,
            quantlib_fixed_leg_npv=quantlib["fixed_leg_npv"],
            quantlib_floating_leg_npv=(
                quantlib["floating_leg_npv"]
            ),
            reference_npv=reference["npv"],
            reference_fixed_leg_npv=reference["fixed_leg_npv"],
            reference_floating_leg_npv=(
                reference["floating_leg_npv"]
            ),
            reference_fixed_leg_dv01=(
                reference["fixed_leg_dv01"]
            ),
            absolute_npv_difference=differences["npv"],
            absolute_fixed_leg_dv01_difference=(
                differences["fixed_leg_dv01"]
            ),
            reference_verified=reference_verified,
            diagnostics=diagnostics,
            order_authority="NONE",
            capital_authority="NONE",
        )

    @staticmethod
    def _validate_binding(
        *,
        request: PricingRequest,
        instrument: FixedFloatSwapInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        discount_curve: DiscountCurveArtifact,
        forwarding_curve: DiscountCurveArtifact,
    ) -> dict[str, str]:
        if request.request_id != pricing_request_identity(request):
            raise ValueError("pricing request identity mismatch")
        if (
            market_snapshot.snapshot_id
            != market_data_snapshot_identity(market_snapshot)
        ):
            raise ValueError("market snapshot identity mismatch")
        for curve, label in (
            (discount_curve, "discount"),
            (forwarding_curve, "forwarding"),
        ):
            if curve.curve_id != discount_curve_identity(curve):
                raise ValueError(f"{label} curve identity mismatch")
            if not curve.quantlib_verified:
                raise ValueError(
                    f"{label} curve is not QuantLib-verified"
                )
            if curve.snapshot_id != market_snapshot.snapshot_id:
                raise ValueError(
                    f"{label} curve belongs to another market snapshot"
                )
            if curve.currency is not instrument.currency:
                raise ValueError(
                    f"{label} curve currency differs from swap currency"
                )
            if (
                curve.order_authority != "NONE"
                or curve.capital_authority != "NONE"
            ):
                raise ValueError(
                    f"{label} curve unexpectedly carries authority"
                )
        if request.instrument_id != instrument.instrument_id:
            raise ValueError(
                "pricing request binds another swap instrument"
            )
        if request.market_snapshot_id != market_snapshot.snapshot_id:
            raise ValueError(
                "pricing request binds another market snapshot"
            )
        if request.model_spec_id != model.model_spec_id:
            raise ValueError(
                "pricing request binds another model specification"
            )
        if request.valuation_time != market_snapshot.valuation_time:
            raise ValueError(
                "pricing request valuation time differs from snapshot"
            )
        if request.reporting_currency is not instrument.currency:
            raise ValueError(
                "Stage 11.5 does not perform implicit FX conversion"
            )
        if (
            request.order_authority != "NONE"
            or request.capital_authority != "NONE"
        ):
            raise ValueError(
                "pricing request unexpectedly carries trading authority"
            )
        unsupported = sorted(
            item.value
            for item in request.measures
            if item not in QuantLibFixedFloatSwapAdapter.REQUIRED_MEASURES
        )
        if unsupported:
            raise ValueError(
                f"unsupported swap pricing measures: {unsupported}"
            )
        if model.model_family != (
            QuantLibFixedFloatSwapAdapter.REQUIRED_MODEL_FAMILY
        ):
            raise ValueError(
                "unsupported swap pricing model family"
            )
        parameters = {
            name: value for name, value in model.parameters
        }
        expected = {
            "calendar",
            "business_day_convention",
            "date_generation",
            "end_of_month",
            "fixing_days",
            "floating_index_mode",
            "discount_curve_key",
            "forwarding_curve_key",
            "dv01_bump",
        }
        if set(parameters) != expected:
            raise ValueError(
                "swap pricing model parameters must be exactly "
                f"{sorted(expected)}"
            )
        required_values = {
            "calendar": "NULL_CALENDAR",
            "business_day_convention": "UNADJUSTED",
            "date_generation": "FORWARD",
            "end_of_month": "FALSE",
            "fixing_days": "0",
            "floating_index_mode": "PROJECTED_SIMPLE_FORWARD",
        }
        for name, required in required_values.items():
            if parameters[name] != required:
                raise ValueError(
                    f"Stage 11.5 requires {name}={required}"
                )
        if parameters["discount_curve_key"] != discount_curve.curve_key:
            raise ValueError(
                "discount curve key differs from pricing model"
            )
        if (
            parameters["forwarding_curve_key"]
            != forwarding_curve.curve_key
        ):
            raise ValueError(
                "forwarding curve key differs from pricing model"
            )
        if instrument.fixed_leg_day_count not in {
            DayCountConvention.ACT_360,
            DayCountConvention.ACT_365_FIXED,
        }:
            raise ValueError(
                "unsupported fixed-leg day-count convention"
            )
        if instrument.floating_leg_day_count not in {
            DayCountConvention.ACT_360,
            DayCountConvention.ACT_365_FIXED,
        }:
            raise ValueError(
                "unsupported floating-leg day-count convention"
            )
        if instrument.currency is not Currency.USD:
            raise ValueError(
                "Stage 11.5 generic term-forward QuantLib fixture "
                "supports USD swaps only"
            )
        return parameters

    @staticmethod
    def _quantlib_values(
        *,
        instrument: FixedFloatSwapInstrument,
        discount_curve: DiscountCurveArtifact,
        forwarding_curve: DiscountCurveArtifact,
        fixed_schedule_dates: tuple[date, ...],
        floating_schedule_dates: tuple[date, ...],
        dv01_bump: Decimal,
    ) -> dict[str, Decimal]:
        evaluation_date = discount_curve.valuation_date
        with QUANTLIB_LOCK:
            settings = ql.Settings.instance()
            previous_date = settings.evaluationDate
            try:
                settings.evaluationDate = _ql_date(evaluation_date)
                forward_handle = ql.YieldTermStructureHandle(
                    _ql_curve(forwarding_curve)
                )
                discount_handle = ql.YieldTermStructureHandle(
                    _ql_curve(discount_curve)
                )
                swap = _ql_swap(
                    instrument=instrument,
                    forward_handle=forward_handle,
                    discount_handle=discount_handle,
                )
                ql_fixed_dates = tuple(
                    _py_date(item)
                    for item in swap.fixedSchedule()
                )
                ql_float_dates = tuple(
                    _py_date(item)
                    for item in swap.floatingSchedule()
                )
                if ql_fixed_dates != fixed_schedule_dates:
                    raise ValueError(
                        "QuantLib fixed schedule differs from QuantOS schedule"
                    )
                if ql_float_dates != floating_schedule_dates:
                    raise ValueError(
                        "QuantLib floating schedule differs from QuantOS schedule"
                    )
                npv = Decimal(str(swap.NPV()))
                fixed_leg_npv = Decimal(str(swap.fixedLegNPV()))
                floating_leg_npv = Decimal(
                    str(swap.floatingLegNPV())
                )

                discount_down = ql.YieldTermStructureHandle(
                    _ql_curve(discount_curve, shift=-dv01_bump)
                )
                discount_up = ql.YieldTermStructureHandle(
                    _ql_curve(discount_curve, shift=dv01_bump)
                )
                swap_down = _ql_swap(
                    instrument=instrument,
                    forward_handle=forward_handle,
                    discount_handle=discount_down,
                )
                swap_up = _ql_swap(
                    instrument=instrument,
                    forward_handle=forward_handle,
                    discount_handle=discount_up,
                )
                fixed_leg_dv01 = (
                    Decimal(str(swap_down.fixedLegNPV()))
                    - Decimal(str(swap_up.fixedLegNPV()))
                ) / Decimal("2")
            except Exception as exc:
                raise ValueError(
                    f"QuantLib fixed/float swap pricing failed: {exc}"
                ) from exc
            finally:
                settings.evaluationDate = previous_date

        for name, value in (
            ("npv", npv),
            ("fixed_leg_npv", fixed_leg_npv),
            ("floating_leg_npv", floating_leg_npv),
            ("fixed_leg_dv01", fixed_leg_dv01),
        ):
            if not value.is_finite():
                raise ValueError(
                    f"QuantLib returned non-finite {name}"
                )
        return {
            "npv": npv,
            "fixed_leg_npv": fixed_leg_npv,
            "floating_leg_npv": floating_leg_npv,
            "fixed_leg_dv01": fixed_leg_dv01,
        }


class SwapPricingResultStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS swap_pricing_results (
                result_id VARCHAR PRIMARY KEY,
                request_id VARCHAR NOT NULL,
                instrument_id VARCHAR NOT NULL,
                market_snapshot_id VARCHAR NOT NULL,
                model_spec_id VARCHAR NOT NULL,
                discount_curve_id VARCHAR NOT NULL,
                forwarding_curve_id VARCHAR NOT NULL,
                validation_policy_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, result: SwapPricingResult) -> bool:
        if result.result_id != swap_pricing_result_identity(result):
            raise ValueError(
                "swap pricing result content does not match result_id"
            )
        payload = json.dumps(
            swap_pricing_result_payload(result),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM swap_pricing_results
            WHERE result_id = ?
            """,
            [result.result_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("swap pricing result identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO swap_pricing_results
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.result_id,
                result.request_id,
                result.instrument_id,
                result.market_snapshot_id,
                result.model_spec_id,
                result.discount_curve_id,
                result.forwarding_curve_id,
                result.validation_policy_id,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def swap_pricing_result_identity(
    result: SwapPricingResult,
) -> str:
    return _content_id(
        "swap-pricing-result",
        {
            key: value
            for key, value in swap_pricing_result_payload(result).items()
            if key != "result_id"
        },
    )


def swap_pricing_result_payload(
    result: SwapPricingResult,
) -> dict[str, object]:
    return {
        "result_id": result.result_id,
        "request_id": result.request_id,
        "instrument_id": result.instrument_id,
        "market_snapshot_id": result.market_snapshot_id,
        "model_spec_id": result.model_spec_id,
        "discount_curve_id": result.discount_curve_id,
        "forwarding_curve_id": result.forwarding_curve_id,
        "validation_policy_id": result.validation_policy_id,
        "engine_name": result.engine_name,
        "engine_version": result.engine_version,
        "engine_evaluation_date": (
            result.engine_evaluation_date.isoformat()
        ),
        "fixed_schedule_dates": [
            item.isoformat() for item in result.fixed_schedule_dates
        ],
        "floating_schedule_dates": [
            item.isoformat() for item in result.floating_schedule_dates
        ],
        "fixed_schedule_fingerprint": (
            result.fixed_schedule_fingerprint
        ),
        "floating_schedule_fingerprint": (
            result.floating_schedule_fingerprint
        ),
        "reference_fixed_cashflows": [
            _fixed_cashflow_payload(item)
            for item in result.reference_fixed_cashflows
        ],
        "reference_floating_cashflows": [
            _floating_cashflow_payload(item)
            for item in result.reference_floating_cashflows
        ],
        "measures": [
            {
                "measure": item.measure.value,
                "value": str(item.value),
                "unit": item.unit,
            }
            for item in result.measures
        ],
        "quantlib_fixed_leg_npv": str(
            result.quantlib_fixed_leg_npv
        ),
        "quantlib_floating_leg_npv": str(
            result.quantlib_floating_leg_npv
        ),
        "reference_npv": str(result.reference_npv),
        "reference_fixed_leg_npv": str(
            result.reference_fixed_leg_npv
        ),
        "reference_floating_leg_npv": str(
            result.reference_floating_leg_npv
        ),
        "reference_fixed_leg_dv01": str(
            result.reference_fixed_leg_dv01
        ),
        "absolute_npv_difference": str(
            result.absolute_npv_difference
        ),
        "absolute_fixed_leg_dv01_difference": str(
            result.absolute_fixed_leg_dv01_difference
        ),
        "reference_verified": result.reference_verified,
        "diagnostics": list(result.diagnostics),
        "order_authority": result.order_authority,
        "capital_authority": result.capital_authority,
    }


def _regular_schedule_dates(
    start: date,
    end: date,
    frequency_months: int,
) -> tuple[date, ...]:
    dates = [start]
    index = 1
    while True:
        candidate = _add_months(start, index * frequency_months)
        if candidate > end:
            raise ValueError(
                "Stage 11.5 requires regular no-stub swap schedules"
            )
        dates.append(candidate)
        if candidate == end:
            break
        index += 1
        if index > 2400:
            raise ValueError("swap schedule exceeds safety bound")
    return tuple(dates)


def _reference_fixed_cashflows(
    *,
    instrument: FixedFloatSwapInstrument,
    schedule_dates: tuple[date, ...],
) -> tuple[SwapFixedReferenceCashflow, ...]:
    sign = (
        Decimal("-1")
        if instrument.fixed_leg_direction is PayReceive.PAY
        else Decimal("1")
    )
    output = []
    for start, end in zip(schedule_dates, schedule_dates[1:]):
        alpha = _year_fraction(
            start,
            end,
            instrument.fixed_leg_day_count,
        )
        amount = (
            instrument.notional
            * instrument.fixed_rate
            * alpha
        )
        output.append(
            SwapFixedReferenceCashflow(
                accrual_start=start,
                accrual_end=end,
                payment_date=end,
                accrual_year_fraction=alpha,
                unsigned_amount=amount,
                signed_amount=sign * amount,
            )
        )
    return tuple(output)


def _reference_floating_cashflows(
    *,
    instrument: FixedFloatSwapInstrument,
    schedule_dates: tuple[date, ...],
    forwarding_curve: DiscountCurveArtifact,
) -> tuple[SwapFloatingReferenceCashflow, ...]:
    sign = (
        Decimal("1")
        if instrument.fixed_leg_direction is PayReceive.PAY
        else Decimal("-1")
    )
    output = []
    for start, end in zip(schedule_dates, schedule_dates[1:]):
        alpha = _year_fraction(
            start,
            end,
            instrument.floating_leg_day_count,
        )
        start_df = forwarding_curve.discount_factor(start)
        end_df = forwarding_curve.discount_factor(end)
        forward = (
            start_df / end_df - Decimal("1")
        ) / alpha
        amount = (
            instrument.notional
            * (forward + instrument.floating_spread)
            * alpha
        )
        output.append(
            SwapFloatingReferenceCashflow(
                accrual_start=start,
                accrual_end=end,
                payment_date=end,
                accrual_year_fraction=alpha,
                projected_forward_rate=forward,
                spread=instrument.floating_spread,
                unsigned_amount=amount,
                signed_amount=sign * amount,
            )
        )
    return tuple(output)


def _reference_values(
    *,
    instrument: FixedFloatSwapInstrument,
    discount_curve: DiscountCurveArtifact,
    fixed_cashflows: tuple[SwapFixedReferenceCashflow, ...],
    floating_cashflows: tuple[SwapFloatingReferenceCashflow, ...],
    dv01_bump: Decimal,
) -> dict[str, Decimal]:
    fixed_leg_npv = sum(
        (
            item.signed_amount
            * discount_curve.discount_factor(item.payment_date)
            for item in fixed_cashflows
        ),
        Decimal("0"),
    )
    floating_leg_npv = sum(
        (
            item.signed_amount
            * discount_curve.discount_factor(item.payment_date)
            for item in floating_cashflows
        ),
        Decimal("0"),
    )
    fixed_down = _shifted_leg_npv(
        curve=discount_curve,
        cashflows=fixed_cashflows,
        bump=-dv01_bump,
    )
    fixed_up = _shifted_leg_npv(
        curve=discount_curve,
        cashflows=fixed_cashflows,
        bump=dv01_bump,
    )
    return {
        "npv": fixed_leg_npv + floating_leg_npv,
        "fixed_leg_npv": fixed_leg_npv,
        "floating_leg_npv": floating_leg_npv,
        "fixed_leg_dv01": (
            fixed_down - fixed_up
        ) / Decimal("2"),
    }


def _shifted_leg_npv(
    *,
    curve: DiscountCurveArtifact,
    cashflows: tuple[SwapFixedReferenceCashflow, ...],
    bump: Decimal,
) -> Decimal:
    return sum(
        (
            item.signed_amount
            * _shifted_discount_factor(
                curve=curve,
                target_date=item.payment_date,
                bump=bump,
            )
            for item in cashflows
        ),
        Decimal("0"),
    )


def _shifted_discount_factor(
    *,
    curve: DiscountCurveArtifact,
    target_date: date,
    bump: Decimal,
) -> Decimal:
    base = curve.discount_factor(target_date)
    t = Decimal(
        (target_date - curve.valuation_date).days
    ) / Decimal("365")
    return base * Decimal(
        str(
            __import__("math").exp(
                -float(bump) * float(t)
            )
        )
    )


def _ql_swap(
    *,
    instrument: FixedFloatSwapInstrument,
    forward_handle: ql.YieldTermStructureHandle,
    discount_handle: ql.YieldTermStructureHandle,
) -> ql.VanillaSwap:
    fixed_schedule = _ql_schedule(
        instrument.effective_date,
        instrument.maturity_date,
        instrument.fixed_leg_frequency_months,
    )
    floating_schedule = _ql_schedule(
        instrument.effective_date,
        instrument.maturity_date,
        instrument.floating_leg_frequency_months,
    )
    index = ql.IborIndex(
        instrument.floating_index_id,
        ql.Period(
            instrument.floating_leg_frequency_months,
            ql.Months,
        ),
        0,
        ql.USDCurrency(),
        ql.NullCalendar(),
        ql.Unadjusted,
        False,
        _ql_day_count(instrument.floating_leg_day_count),
        forward_handle,
    )
    swap_type = (
        ql.VanillaSwap.Payer
        if instrument.fixed_leg_direction is PayReceive.PAY
        else ql.VanillaSwap.Receiver
    )
    swap = ql.VanillaSwap(
        swap_type,
        float(instrument.notional),
        fixed_schedule,
        float(instrument.fixed_rate),
        _ql_day_count(instrument.fixed_leg_day_count),
        floating_schedule,
        index,
        float(instrument.floating_spread),
        _ql_day_count(instrument.floating_leg_day_count),
    )
    swap.setPricingEngine(
        ql.DiscountingSwapEngine(discount_handle)
    )
    return swap


def _ql_curve(
    curve: DiscountCurveArtifact,
    shift: Decimal = Decimal("0"),
):
    dates = [_ql_date(curve.valuation_date)]
    discounts = [1.0]
    for pillar in curve.pillars:
        shifted = pillar.discount_factor * Decimal(
            str(
                __import__("math").exp(
                    -float(shift)
                    * float(pillar.year_fraction)
                )
            )
        )
        dates.append(_ql_date(pillar.pillar_date))
        discounts.append(float(shifted))
    output = ql.DiscountCurve(
        dates,
        discounts,
        ql.Actual365Fixed(),
        ql.NullCalendar(),
    )
    if curve.allow_extrapolation:
        output.enableExtrapolation()
    return output


def _ql_schedule(
    start: date,
    end: date,
    frequency_months: int,
) -> ql.Schedule:
    return ql.Schedule(
        _ql_date(start),
        _ql_date(end),
        ql.Period(frequency_months, ql.Months),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Forward,
        False,
    )


def _ql_day_count(
    convention: DayCountConvention,
):
    if convention is DayCountConvention.ACT_360:
        return ql.Actual360()
    if convention is DayCountConvention.ACT_365_FIXED:
        return ql.Actual365Fixed()
    raise ValueError("unsupported swap day-count convention")


def _year_fraction(
    start: date,
    end: date,
    convention: DayCountConvention,
) -> Decimal:
    days = Decimal((end - start).days)
    if convention is DayCountConvention.ACT_360:
        return days / Decimal("360")
    if convention is DayCountConvention.ACT_365_FIXED:
        return days / Decimal("365")
    raise ValueError("unsupported swap day-count convention")


def _schedule_fingerprint(
    *,
    prefix: str,
    instrument_id: str,
    frequency_months: int,
    day_count: DayCountConvention,
    dates: tuple[date, ...],
    parameters: dict[str, str],
) -> str:
    return _content_id(
        prefix,
        {
            "instrument_id": instrument_id,
            "frequency_months": frequency_months,
            "day_count": day_count.value,
            "calendar": parameters["calendar"],
            "business_day_convention": (
                parameters["business_day_convention"]
            ),
            "date_generation": parameters["date_generation"],
            "end_of_month": parameters["end_of_month"],
            "fixing_days": parameters["fixing_days"],
            "dates": [item.isoformat() for item in dates],
        },
    )


def _fixed_cashflow_payload(
    item: SwapFixedReferenceCashflow,
) -> dict[str, object]:
    return {
        "accrual_start": item.accrual_start.isoformat(),
        "accrual_end": item.accrual_end.isoformat(),
        "payment_date": item.payment_date.isoformat(),
        "accrual_year_fraction": str(item.accrual_year_fraction),
        "unsigned_amount": str(item.unsigned_amount),
        "signed_amount": str(item.signed_amount),
    }


def _floating_cashflow_payload(
    item: SwapFloatingReferenceCashflow,
) -> dict[str, object]:
    return {
        "accrual_start": item.accrual_start.isoformat(),
        "accrual_end": item.accrual_end.isoformat(),
        "payment_date": item.payment_date.isoformat(),
        "accrual_year_fraction": str(item.accrual_year_fraction),
        "projected_forward_rate": str(
            item.projected_forward_rate
        ),
        "spread": str(item.spread),
        "unsigned_amount": str(item.unsigned_amount),
        "signed_amount": str(item.signed_amount),
    }


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _py_date(value: ql.Date) -> date:
    return date(value.year(), value.month(), value.dayOfMonth())


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
