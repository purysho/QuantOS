from __future__ import annotations

import calendar
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
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
    FixedRateBondInstrument,
    MarketDataSnapshot,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequest,
    market_data_snapshot_identity,
    pricing_request_identity,
)
from .quantlib_runtime import QUANTLIB_LOCK


@dataclass(frozen=True)
class BondPricingValidationPolicy:
    maximum_absolute_npv_difference: Decimal
    maximum_absolute_price_difference: Decimal
    maximum_absolute_accrued_difference: Decimal
    maximum_absolute_dv01_difference: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "maximum_absolute_npv_difference",
            "maximum_absolute_price_difference",
            "maximum_absolute_accrued_difference",
            "maximum_absolute_dv01_difference",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{name} must be finite and non-negative"
                )
        if not self.rationale.strip():
            raise ValueError(
                "bond pricing validation rationale is required"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "bond pricing validation requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "bond-pricing-validation-policy",
            {
                "maximum_absolute_npv_difference": str(
                    self.maximum_absolute_npv_difference
                ),
                "maximum_absolute_price_difference": str(
                    self.maximum_absolute_price_difference
                ),
                "maximum_absolute_accrued_difference": str(
                    self.maximum_absolute_accrued_difference
                ),
                "maximum_absolute_dv01_difference": str(
                    self.maximum_absolute_dv01_difference
                ),
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class BondReferenceCashflow:
    accrual_start: date
    accrual_end: date
    payment_date: date
    coupon_amount: Decimal
    redemption_amount: Decimal
    total_amount: Decimal


@dataclass(frozen=True)
class BondPricingResult:
    result_id: str
    request_id: str
    instrument_id: str
    market_snapshot_id: str
    model_spec_id: str
    discount_curve_id: str
    validation_policy_id: str
    engine_name: str
    engine_version: str
    engine_evaluation_date: date
    settlement_date: date
    schedule_dates: tuple[date, ...]
    schedule_fingerprint: str
    reference_cashflows: tuple[BondReferenceCashflow, ...]
    measures: tuple[PricingMeasureValue, ...]
    quantlib_accrued_amount_per_100: Decimal
    reference_npv: Decimal
    reference_dirty_price: Decimal
    reference_clean_price: Decimal
    reference_accrued_amount_per_100: Decimal
    reference_dv01: Decimal
    absolute_npv_difference: Decimal
    absolute_dirty_price_difference: Decimal
    absolute_clean_price_difference: Decimal
    absolute_accrued_difference: Decimal
    absolute_dv01_difference: Decimal
    reference_verified: bool
    diagnostics: tuple[str, ...]
    order_authority: str
    capital_authority: str


class QuantLibFixedRateBondAdapter:
    ENGINE_NAME = "QuantLib"
    REQUIRED_MODEL_FAMILY = "DISCOUNT_CURVE_FIXED_RATE_BOND"
    REQUIRED_MEASURES = frozenset(
        {
            PricingMeasure.NPV,
            PricingMeasure.DIRTY_PRICE,
            PricingMeasure.CLEAN_PRICE,
            PricingMeasure.DV01,
        }
    )

    def __init__(self) -> None:
        self.engine_version = package_version("QuantLib")

    def price(
        self,
        *,
        request: PricingRequest,
        instrument: FixedRateBondInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        discount_curve: DiscountCurveArtifact,
        validation_policy: BondPricingValidationPolicy,
    ) -> BondPricingResult:
        parameters = self._validate_binding(
            request=request,
            instrument=instrument,
            market_snapshot=market_snapshot,
            model=model,
            discount_curve=discount_curve,
        )
        dv01_bump = Decimal(parameters["dv01_bump"])
        if dv01_bump != Decimal("0.0001"):
            raise ValueError(
                "Stage 11.4 DV01 requires an exact one-basis-point bump"
            )

        schedule_dates = _regular_schedule_dates(instrument)
        settlement_date = (
            market_snapshot.valuation_time.date()
            + timedelta(days=instrument.settlement_days)
        )
        if settlement_date >= instrument.maturity_date:
            raise ValueError(
                "bond settlement date must precede maturity"
            )
        if (
            not discount_curve.allow_extrapolation
            and instrument.maturity_date
            > discount_curve.pillars[-1].pillar_date
        ):
            raise ValueError(
                "bond maturity exceeds non-extrapolating discount curve"
            )

        cashflows = _reference_cashflows(
            instrument=instrument,
            schedule_dates=schedule_dates,
        )
        reference = _reference_values(
            instrument=instrument,
            curve=discount_curve,
            settlement_date=settlement_date,
            cashflows=cashflows,
            dv01_bump=dv01_bump,
        )
        quantlib = self._quantlib_values(
            instrument=instrument,
            curve=discount_curve,
            settlement_date=settlement_date,
            schedule_dates=schedule_dates,
            dv01_bump=dv01_bump,
        )

        if quantlib["settlement_date"] != settlement_date:
            raise ValueError(
                "QuantLib settlement date differs from QuantOS contract"
            )

        differences = {
            "npv": abs(quantlib["npv"] - reference["npv"]),
            "dirty": abs(
                quantlib["dirty_price"]
                - reference["dirty_price"]
            ),
            "clean": abs(
                quantlib["clean_price"]
                - reference["clean_price"]
            ),
            "accrued": abs(
                quantlib["accrued"]
                - reference["accrued"]
            ),
            "dv01": abs(
                quantlib["dv01"] - reference["dv01"]
            ),
        }
        reference_verified = (
            differences["npv"]
            <= validation_policy.maximum_absolute_npv_difference
            and differences["dirty"]
            <= validation_policy.maximum_absolute_price_difference
            and differences["clean"]
            <= validation_policy.maximum_absolute_price_difference
            and differences["accrued"]
            <= validation_policy.maximum_absolute_accrued_difference
            and differences["dv01"]
            <= validation_policy.maximum_absolute_dv01_difference
        )
        if not reference_verified:
            raise ValueError(
                "QuantLib bond result differs from independent cash-flow "
                f"reference: {differences}"
            )

        measure_map = {
            PricingMeasure.NPV: PricingMeasureValue(
                measure=PricingMeasure.NPV,
                value=quantlib["npv"],
                unit=request.reporting_currency.value,
            ),
            PricingMeasure.DIRTY_PRICE: PricingMeasureValue(
                measure=PricingMeasure.DIRTY_PRICE,
                value=quantlib["dirty_price"],
                unit="PRICE_PER_100",
            ),
            PricingMeasure.CLEAN_PRICE: PricingMeasureValue(
                measure=PricingMeasure.CLEAN_PRICE,
                value=quantlib["clean_price"],
                unit="PRICE_PER_100",
            ),
            PricingMeasure.DV01: PricingMeasureValue(
                measure=PricingMeasure.DV01,
                value=quantlib["dv01"],
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
        schedule_fingerprint = _content_id(
            "bond-schedule",
            {
                "instrument_id": instrument.instrument_id,
                "calendar": parameters["calendar"],
                "business_day_convention": (
                    parameters["business_day_convention"]
                ),
                "date_generation": parameters["date_generation"],
                "end_of_month": parameters["end_of_month"],
                "dates": [
                    item.isoformat()
                    for item in schedule_dates
                ],
            },
        )
        diagnostics = (
            "NULL_CALENDAR",
            "UNADJUSTED payment dates",
            "FORWARD regular no-stub schedule",
            "ACT_365_FIXED coupon accrual",
            "redemption = 100 percent of face",
            "discount factors sourced from frozen Stage 11.3 curve",
            "central parallel zero-curve one-basis-point DV01",
            "QuantLib global evaluation date serialized and restored",
            "independent cash-flow differential validation passed",
        )
        payload = {
            "request_id": request.request_id,
            "instrument_id": instrument.instrument_id,
            "market_snapshot_id": market_snapshot.snapshot_id,
            "model_spec_id": model.model_spec_id,
            "discount_curve_id": discount_curve.curve_id,
            "validation_policy_id": validation_policy.policy_id,
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.engine_version,
            "engine_evaluation_date": (
                market_snapshot.valuation_time.date().isoformat()
            ),
            "settlement_date": settlement_date.isoformat(),
            "schedule_dates": [
                item.isoformat() for item in schedule_dates
            ],
            "schedule_fingerprint": schedule_fingerprint,
            "reference_cashflows": [
                _cashflow_payload(item)
                for item in cashflows
            ],
            "measures": [
                {
                    "measure": item.measure.value,
                    "value": str(item.value),
                    "unit": item.unit,
                }
                for item in measures
            ],
            "quantlib_accrued_amount_per_100": str(
                quantlib["accrued"]
            ),
            "reference_npv": str(reference["npv"]),
            "reference_dirty_price": str(
                reference["dirty_price"]
            ),
            "reference_clean_price": str(
                reference["clean_price"]
            ),
            "reference_accrued_amount_per_100": str(
                reference["accrued"]
            ),
            "reference_dv01": str(reference["dv01"]),
            "absolute_npv_difference": str(differences["npv"]),
            "absolute_dirty_price_difference": str(
                differences["dirty"]
            ),
            "absolute_clean_price_difference": str(
                differences["clean"]
            ),
            "absolute_accrued_difference": str(
                differences["accrued"]
            ),
            "absolute_dv01_difference": str(
                differences["dv01"]
            ),
            "reference_verified": reference_verified,
            "diagnostics": list(diagnostics),
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return BondPricingResult(
            result_id=_content_id("bond-pricing-result", payload),
            request_id=request.request_id,
            instrument_id=instrument.instrument_id,
            market_snapshot_id=market_snapshot.snapshot_id,
            model_spec_id=model.model_spec_id,
            discount_curve_id=discount_curve.curve_id,
            validation_policy_id=validation_policy.policy_id,
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            engine_evaluation_date=(
                market_snapshot.valuation_time.date()
            ),
            settlement_date=settlement_date,
            schedule_dates=schedule_dates,
            schedule_fingerprint=schedule_fingerprint,
            reference_cashflows=cashflows,
            measures=measures,
            quantlib_accrued_amount_per_100=quantlib["accrued"],
            reference_npv=reference["npv"],
            reference_dirty_price=reference["dirty_price"],
            reference_clean_price=reference["clean_price"],
            reference_accrued_amount_per_100=reference["accrued"],
            reference_dv01=reference["dv01"],
            absolute_npv_difference=differences["npv"],
            absolute_dirty_price_difference=differences["dirty"],
            absolute_clean_price_difference=differences["clean"],
            absolute_accrued_difference=differences["accrued"],
            absolute_dv01_difference=differences["dv01"],
            reference_verified=reference_verified,
            diagnostics=diagnostics,
            order_authority="NONE",
            capital_authority="NONE",
        )

    @staticmethod
    def _validate_binding(
        *,
        request: PricingRequest,
        instrument: FixedRateBondInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        discount_curve: DiscountCurveArtifact,
    ) -> dict[str, str]:
        if request.request_id != pricing_request_identity(request):
            raise ValueError("pricing request identity mismatch")
        if (
            market_snapshot.snapshot_id
            != market_data_snapshot_identity(market_snapshot)
        ):
            raise ValueError("market snapshot identity mismatch")
        if discount_curve.curve_id != discount_curve_identity(
            discount_curve
        ):
            raise ValueError("discount curve identity mismatch")
        if request.instrument_id != instrument.instrument_id:
            raise ValueError(
                "pricing request binds another bond instrument"
            )
        if request.market_snapshot_id != market_snapshot.snapshot_id:
            raise ValueError(
                "pricing request binds another market snapshot"
            )
        if request.model_spec_id != model.model_spec_id:
            raise ValueError(
                "pricing request binds another pricing model"
            )
        if (
            request.valuation_time
            != market_snapshot.valuation_time
        ):
            raise ValueError(
                "pricing request valuation time differs from market snapshot"
            )
        if (
            discount_curve.snapshot_id
            != market_snapshot.snapshot_id
        ):
            raise ValueError(
                "discount curve belongs to another market snapshot"
            )
        if (
            discount_curve.valuation_date
            != market_snapshot.valuation_time.date()
        ):
            raise ValueError(
                "discount curve valuation date differs from request"
            )
        if discount_curve.currency is not instrument.currency:
            raise ValueError(
                "bond and discount curve currencies differ"
            )
        if request.reporting_currency is not instrument.currency:
            raise ValueError(
                "Stage 11.4 does not perform implicit FX conversion"
            )
        if not discount_curve.quantlib_verified:
            raise ValueError(
                "bond pricing requires QuantLib-verified frozen curve"
            )
        if (
            discount_curve.order_authority != "NONE"
            or discount_curve.capital_authority != "NONE"
            or request.order_authority != "NONE"
            or request.capital_authority != "NONE"
        ):
            raise ValueError(
                "pricing inputs unexpectedly carry trading authority"
            )
        if instrument.day_count is not DayCountConvention.ACT_365_FIXED:
            raise ValueError(
                "Stage 11.4 supports ACT_365_FIXED bonds only"
            )
        valuation_date = market_snapshot.valuation_time.date()
        if (
            valuation_date < instrument.issue_date
            or valuation_date >= instrument.maturity_date
        ):
            raise ValueError(
                "bond valuation date must be on/after issue and before maturity"
            )
        supported = QuantLibFixedRateBondAdapter.REQUIRED_MEASURES
        if not request.measures:
            raise ValueError("bond pricing requires measures")
        unsupported = sorted(
            item.value
            for item in request.measures
            if item not in supported
        )
        if unsupported:
            raise ValueError(
                f"unsupported Stage 11.4 bond measures: {unsupported}"
            )
        if model.model_family != (
            QuantLibFixedRateBondAdapter.REQUIRED_MODEL_FAMILY
        ):
            raise ValueError(
                "bond requires DISCOUNT_CURVE_FIXED_RATE_BOND model family"
            )
        parameters = {
            name: value for name, value in model.parameters
        }
        expected = {
            "calendar",
            "business_day_convention",
            "date_generation",
            "end_of_month",
            "redemption",
            "dv01_bump",
        }
        if set(parameters) != expected:
            raise ValueError(
                "bond model parameters must be exactly "
                f"{sorted(expected)}"
            )
        required_values = {
            "calendar": "NULL_CALENDAR",
            "business_day_convention": "UNADJUSTED",
            "date_generation": "FORWARD",
            "end_of_month": "FALSE",
            "redemption": "100",
        }
        for name, expected_value in required_values.items():
            if parameters[name] != expected_value:
                raise ValueError(
                    f"Stage 11.4 requires {name}={expected_value}"
                )
        try:
            bump = Decimal(parameters["dv01_bump"])
        except Exception as exc:
            raise ValueError("dv01_bump must be decimal text") from exc
        if not bump.is_finite() or bump <= 0:
            raise ValueError("dv01_bump must be finite and positive")
        return parameters

    def _quantlib_values(
        self,
        *,
        instrument: FixedRateBondInstrument,
        curve: DiscountCurveArtifact,
        settlement_date: date,
        schedule_dates: tuple[date, ...],
        dv01_bump: Decimal,
    ) -> dict[str, Decimal | date]:
        evaluation_date = curve.valuation_date
        with QUANTLIB_LOCK:
            settings = ql.Settings.instance()
            previous = settings.evaluationDate
            try:
                settings.evaluationDate = _ql_date(evaluation_date)
                schedule = ql.Schedule(
                    _ql_date(schedule_dates[0]),
                    _ql_date(schedule_dates[-1]),
                    ql.Period(
                        instrument.coupon_frequency_months,
                        ql.Months,
                    ),
                    ql.NullCalendar(),
                    ql.Unadjusted,
                    ql.Unadjusted,
                    ql.DateGeneration.Forward,
                    False,
                )
                bond = ql.FixedRateBond(
                    instrument.settlement_days,
                    float(instrument.face_value),
                    schedule,
                    [float(instrument.coupon_rate)],
                    ql.Actual365Fixed(),
                    ql.Unadjusted,
                    100.0,
                    _ql_date(instrument.issue_date),
                )

                base_curve = _ql_discount_curve(curve, Decimal("0"))
                bond.setPricingEngine(
                    ql.DiscountingBondEngine(
                        ql.YieldTermStructureHandle(base_curve)
                    )
                )
                npv = Decimal(str(float(bond.NPV())))
                dirty = Decimal(str(float(bond.dirtyPrice())))
                clean = Decimal(str(float(bond.cleanPrice())))
                accrued = Decimal(
                    str(float(bond.accruedAmount()))
                )
                observed_settlement = _py_date(
                    bond.settlementDate()
                )

                down_curve = _ql_discount_curve(
                    curve,
                    -dv01_bump,
                )
                bond.setPricingEngine(
                    ql.DiscountingBondEngine(
                        ql.YieldTermStructureHandle(down_curve)
                    )
                )
                npv_down = Decimal(str(float(bond.NPV())))

                up_curve = _ql_discount_curve(
                    curve,
                    dv01_bump,
                )
                bond.setPricingEngine(
                    ql.DiscountingBondEngine(
                        ql.YieldTermStructureHandle(up_curve)
                    )
                )
                npv_up = Decimal(str(float(bond.NPV())))
                dv01 = (npv_down - npv_up) / Decimal("2")
            except Exception as exc:
                raise ValueError(
                    f"QuantLib fixed-rate bond pricing failed: {exc}"
                ) from exc
            finally:
                settings.evaluationDate = previous

        for name, value in (
            ("npv", npv),
            ("dirty_price", dirty),
            ("clean_price", clean),
            ("accrued", accrued),
            ("dv01", dv01),
        ):
            if not value.is_finite():
                raise ValueError(
                    f"QuantLib returned non-finite bond {name}"
                )
        return {
            "npv": npv,
            "dirty_price": dirty,
            "clean_price": clean,
            "accrued": accrued,
            "dv01": dv01,
            "settlement_date": observed_settlement,
        }


class BondPricingResultStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS bond_pricing_results (
                result_id VARCHAR PRIMARY KEY,
                request_id VARCHAR NOT NULL,
                instrument_id VARCHAR NOT NULL,
                market_snapshot_id VARCHAR NOT NULL,
                discount_curve_id VARCHAR NOT NULL,
                validation_policy_id VARCHAR NOT NULL,
                engine_version VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, result: BondPricingResult) -> bool:
        if result.result_id != bond_pricing_result_identity(result):
            raise ValueError(
                "bond pricing result content does not match result_id"
            )
        payload = json.dumps(
            bond_pricing_result_payload(result),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM bond_pricing_results
            WHERE result_id = ?
            """,
            [result.result_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("bond pricing result identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO bond_pricing_results
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.result_id,
                result.request_id,
                result.instrument_id,
                result.market_snapshot_id,
                result.discount_curve_id,
                result.validation_policy_id,
                result.engine_version,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def bond_pricing_result_identity(
    result: BondPricingResult,
) -> str:
    return _content_id(
        "bond-pricing-result",
        {
            key: value
            for key, value in bond_pricing_result_payload(result).items()
            if key != "result_id"
        },
    )


def bond_pricing_result_payload(
    result: BondPricingResult,
) -> dict[str, object]:
    return {
        "result_id": result.result_id,
        "request_id": result.request_id,
        "instrument_id": result.instrument_id,
        "market_snapshot_id": result.market_snapshot_id,
        "model_spec_id": result.model_spec_id,
        "discount_curve_id": result.discount_curve_id,
        "validation_policy_id": result.validation_policy_id,
        "engine_name": result.engine_name,
        "engine_version": result.engine_version,
        "engine_evaluation_date": (
            result.engine_evaluation_date.isoformat()
        ),
        "settlement_date": result.settlement_date.isoformat(),
        "schedule_dates": [
            item.isoformat() for item in result.schedule_dates
        ],
        "schedule_fingerprint": result.schedule_fingerprint,
        "reference_cashflows": [
            _cashflow_payload(item)
            for item in result.reference_cashflows
        ],
        "measures": [
            {
                "measure": item.measure.value,
                "value": str(item.value),
                "unit": item.unit,
            }
            for item in result.measures
        ],
        "quantlib_accrued_amount_per_100": str(
            result.quantlib_accrued_amount_per_100
        ),
        "reference_npv": str(result.reference_npv),
        "reference_dirty_price": str(
            result.reference_dirty_price
        ),
        "reference_clean_price": str(
            result.reference_clean_price
        ),
        "reference_accrued_amount_per_100": str(
            result.reference_accrued_amount_per_100
        ),
        "reference_dv01": str(result.reference_dv01),
        "absolute_npv_difference": str(
            result.absolute_npv_difference
        ),
        "absolute_dirty_price_difference": str(
            result.absolute_dirty_price_difference
        ),
        "absolute_clean_price_difference": str(
            result.absolute_clean_price_difference
        ),
        "absolute_accrued_difference": str(
            result.absolute_accrued_difference
        ),
        "absolute_dv01_difference": str(
            result.absolute_dv01_difference
        ),
        "reference_verified": result.reference_verified,
        "diagnostics": list(result.diagnostics),
        "order_authority": result.order_authority,
        "capital_authority": result.capital_authority,
    }


def _regular_schedule_dates(
    instrument: FixedRateBondInstrument,
) -> tuple[date, ...]:
    dates = [instrument.issue_date]
    step = instrument.coupon_frequency_months
    index = 1
    while True:
        candidate = _add_months(
            instrument.issue_date,
            index * step,
        )
        if candidate > instrument.maturity_date:
            raise ValueError(
                "Stage 11.4 requires a regular no-stub coupon schedule"
            )
        dates.append(candidate)
        if candidate == instrument.maturity_date:
            break
        index += 1
        if index > 1200:
            raise ValueError("bond schedule exceeds safety bound")
    return tuple(dates)


def _reference_cashflows(
    *,
    instrument: FixedRateBondInstrument,
    schedule_dates: tuple[date, ...],
) -> tuple[BondReferenceCashflow, ...]:
    output = []
    for start, end in zip(schedule_dates, schedule_dates[1:]):
        year_fraction = _year_fraction(start, end)
        coupon = (
            instrument.face_value
            * instrument.coupon_rate
            * year_fraction
        )
        redemption = (
            instrument.face_value
            if end == instrument.maturity_date
            else Decimal("0")
        )
        output.append(
            BondReferenceCashflow(
                accrual_start=start,
                accrual_end=end,
                payment_date=end,
                coupon_amount=coupon,
                redemption_amount=redemption,
                total_amount=coupon + redemption,
            )
        )
    return tuple(output)


def _reference_values(
    *,
    instrument: FixedRateBondInstrument,
    curve: DiscountCurveArtifact,
    settlement_date: date,
    cashflows: tuple[BondReferenceCashflow, ...],
    dv01_bump: Decimal,
) -> dict[str, Decimal]:
    future = tuple(
        item
        for item in cashflows
        if item.payment_date > settlement_date
    )
    if not future:
        raise ValueError(
            "bond has no cash flows after settlement"
        )
    npv = sum(
        (
            item.total_amount
            * curve.discount_factor(item.payment_date)
            for item in future
        ),
        Decimal("0"),
    )
    settlement_df = curve.discount_factor(settlement_date)
    dirty = (
        npv
        / settlement_df
        / instrument.face_value
        * Decimal("100")
    )
    accrued = _reference_accrued_per_100(
        instrument=instrument,
        settlement_date=settlement_date,
        cashflows=cashflows,
    )
    clean = dirty - accrued

    npv_down = _reference_shifted_npv(
        curve=curve,
        cashflows=future,
        bump=-dv01_bump,
    )
    npv_up = _reference_shifted_npv(
        curve=curve,
        cashflows=future,
        bump=dv01_bump,
    )
    dv01 = (npv_down - npv_up) / Decimal("2")
    return {
        "npv": npv,
        "dirty_price": dirty,
        "clean_price": clean,
        "accrued": accrued,
        "dv01": dv01,
    }


def _reference_accrued_per_100(
    *,
    instrument: FixedRateBondInstrument,
    settlement_date: date,
    cashflows: tuple[BondReferenceCashflow, ...],
) -> Decimal:
    for item in cashflows:
        if (
            item.accrual_start
            < settlement_date
            < item.accrual_end
        ):
            elapsed = _year_fraction(
                item.accrual_start,
                settlement_date,
            )
            return (
                instrument.coupon_rate
                * elapsed
                * Decimal("100")
            )
        if settlement_date == item.accrual_end:
            return Decimal("0")
    return Decimal("0")


def _reference_shifted_npv(
    *,
    curve: DiscountCurveArtifact,
    cashflows: tuple[BondReferenceCashflow, ...],
    bump: Decimal,
) -> Decimal:
    return sum(
        (
            item.total_amount
            * curve.discount_factor(item.payment_date)
            * Decimal(
                str(
                    math.exp(
                        -float(bump)
                        * float(
                            _year_fraction(
                                curve.valuation_date,
                                item.payment_date,
                            )
                        )
                    )
                )
            )
            for item in cashflows
        ),
        Decimal("0"),
    )


def _ql_discount_curve(
    curve: DiscountCurveArtifact,
    zero_rate_bump: Decimal,
):
    dates = [_ql_date(curve.valuation_date)]
    discounts = [1.0]
    for pillar in curve.pillars:
        shifted = (
            pillar.discount_factor
            * Decimal(
                str(
                    math.exp(
                        -float(zero_rate_bump)
                        * float(pillar.year_fraction)
                    )
                )
            )
        )
        dates.append(_ql_date(pillar.pillar_date))
        discounts.append(float(shifted))
    term_structure = ql.DiscountCurve(
        dates,
        discounts,
        ql.Actual365Fixed(),
        ql.NullCalendar(),
    )
    if curve.allow_extrapolation:
        term_structure.enableExtrapolation()
    return term_structure


def _cashflow_payload(
    item: BondReferenceCashflow,
) -> dict[str, object]:
    return {
        "accrual_start": item.accrual_start.isoformat(),
        "accrual_end": item.accrual_end.isoformat(),
        "payment_date": item.payment_date.isoformat(),
        "coupon_amount": str(item.coupon_amount),
        "redemption_amount": str(item.redemption_amount),
        "total_amount": str(item.total_amount),
    }


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(
        value.day,
        calendar.monthrange(year, month)[1],
    )
    return date(year, month, day)


def _year_fraction(start: date, end: date) -> Decimal:
    return Decimal((end - start).days) / Decimal("365")


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
