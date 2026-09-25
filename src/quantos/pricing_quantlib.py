from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path

import duckdb
import QuantLib as ql

from .pricing_risk_contracts import (
    Currency,
    EquityInstrument,
    EuropeanOptionInstrument,
    MarketDataSnapshot,
    MarketQuote,
    MarketQuoteType,
    PricingInstrument,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequest,
    market_data_snapshot_identity,
    pricing_request_identity,
)


from .quantlib_runtime import QUANTLIB_LOCK

_QUANTLIB_LOCK = QUANTLIB_LOCK


@dataclass(frozen=True)
class PricingMeasureValue:
    measure: PricingMeasure
    value: Decimal
    unit: str


@dataclass(frozen=True)
class PricingResult:
    result_id: str
    request_id: str
    instrument_id: str
    market_snapshot_id: str
    model_spec_id: str
    engine_name: str
    engine_version: str
    engine_evaluation_date: str
    quote_ids_used: tuple[str, ...]
    measures: tuple[PricingMeasureValue, ...]
    diagnostics: tuple[str, ...]
    order_authority: str
    capital_authority: str


class QuantLibPricingAdapter:
    """Narrow, fail-closed QuantLib adapter for Stage 11.2."""

    ENGINE_NAME = "QuantLib"

    def __init__(self) -> None:
        self.engine_version = package_version("QuantLib")

    def price(
        self,
        *,
        request: PricingRequest,
        instrument: PricingInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
    ) -> PricingResult:
        self._validate_binding(
            request=request,
            instrument=instrument,
            market_snapshot=market_snapshot,
            model=model,
        )
        if isinstance(instrument, EquityInstrument):
            return self._price_equity(
                request=request,
                instrument=instrument,
                market_snapshot=market_snapshot,
                model=model,
            )
        if isinstance(instrument, EuropeanOptionInstrument):
            return self._price_european_option(
                request=request,
                instrument=instrument,
                market_snapshot=market_snapshot,
                model=model,
            )
        raise ValueError(
            "Stage 11.2 QuantLib adapter supports only equity and "
            "European vanilla option instruments"
        )

    def _price_equity(
        self,
        *,
        request: PricingRequest,
        instrument: EquityInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
    ) -> PricingResult:
        if model.model_family != "SPOT_MARK_TO_MARKET":
            raise ValueError(
                "equity pricing requires SPOT_MARK_TO_MARKET model family"
            )
        if model.parameters:
            raise ValueError(
                "SPOT_MARK_TO_MARKET does not accept model parameters"
            )
        supported = {PricingMeasure.NPV}
        self._require_supported(request.measures, supported)
        self._require_same_currency(
            instrument.currency,
            request.reporting_currency,
        )
        spot = _find_unique_quote(
            market_snapshot,
            quote_type=MarketQuoteType.EQUITY_SPOT,
            market_key=instrument.security_id,
            currency=instrument.currency,
        )
        npv = spot.value * instrument.multiplier
        return self._result(
            request=request,
            quote_ids=(spot.quote_id,),
            values=(
                PricingMeasureValue(
                    measure=PricingMeasure.NPV,
                    value=npv,
                    unit=request.reporting_currency.value,
                ),
            ),
            diagnostics=(
                "equity marked directly from frozen point-in-time spot",
                "QuantLib global evaluation date was not required",
            ),
            evaluation_date=market_snapshot.valuation_time.date().isoformat(),
        )

    def _price_european_option(
        self,
        *,
        request: PricingRequest,
        instrument: EuropeanOptionInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
    ) -> PricingResult:
        if model.model_family != "BLACK_SCHOLES_MERTON":
            raise ValueError(
                "European option requires BLACK_SCHOLES_MERTON model family"
            )
        parameters = _parameter_map(model)
        expected_names = {
            "risk_free_rate_key",
            "dividend_yield_key",
            "volatility_key",
            "day_count",
            "calendar",
        }
        if set(parameters) != expected_names:
            raise ValueError(
                "BLACK_SCHOLES_MERTON parameters must be exactly "
                f"{sorted(expected_names)}"
            )
        if parameters["day_count"] != "ACT_365_FIXED":
            raise ValueError(
                "Stage 11.2 BSM adapter supports ACT_365_FIXED only"
            )
        if parameters["calendar"] != "NULL_CALENDAR":
            raise ValueError(
                "Stage 11.2 BSM adapter supports NULL_CALENDAR only"
            )
        supported = {
            PricingMeasure.NPV,
            PricingMeasure.DELTA,
            PricingMeasure.GAMMA,
            PricingMeasure.VEGA,
            PricingMeasure.THETA,
        }
        self._require_supported(request.measures, supported)
        self._require_same_currency(
            instrument.currency,
            request.reporting_currency,
        )
        if instrument.expiry.date() <= market_snapshot.valuation_time.date():
            raise ValueError(
                "QuantLib date-based option pricing requires expiry after "
                "the valuation calendar date"
            )

        spot = _find_unique_quote(
            market_snapshot,
            quote_type=MarketQuoteType.EQUITY_SPOT,
            market_key=instrument.underlying_security_id,
            currency=instrument.currency,
        )
        risk_free = _find_unique_quote(
            market_snapshot,
            quote_type=MarketQuoteType.ZERO_RATE,
            market_key=parameters["risk_free_rate_key"],
            currency=instrument.currency,
        )
        dividend = _find_unique_quote(
            market_snapshot,
            quote_type=MarketQuoteType.ZERO_RATE,
            market_key=parameters["dividend_yield_key"],
            currency=instrument.currency,
        )
        volatility = _find_unique_quote(
            market_snapshot,
            quote_type=MarketQuoteType.VOLATILITY,
            market_key=parameters["volatility_key"],
            currency=instrument.currency,
        )
        if volatility.value <= 0:
            raise ValueError("option volatility must be positive")

        evaluation_date = _ql_date(
            market_snapshot.valuation_time.date()
        )
        expiry_date = _ql_date(instrument.expiry.date())
        quote_ids = tuple(
            sorted(
                (
                    spot.quote_id,
                    risk_free.quote_id,
                    dividend.quote_id,
                    volatility.quote_id,
                )
            )
        )

        with _QUANTLIB_LOCK:
            settings = ql.Settings.instance()
            previous_date = settings.evaluationDate
            try:
                settings.evaluationDate = evaluation_date
                day_count = ql.Actual365Fixed()
                calendar = ql.NullCalendar()
                spot_handle = ql.QuoteHandle(
                    ql.SimpleQuote(float(spot.value))
                )
                risk_free_curve = ql.YieldTermStructureHandle(
                    ql.FlatForward(
                        evaluation_date,
                        float(risk_free.value),
                        day_count,
                        ql.Continuous,
                        ql.Annual,
                    )
                )
                dividend_curve = ql.YieldTermStructureHandle(
                    ql.FlatForward(
                        evaluation_date,
                        float(dividend.value),
                        day_count,
                        ql.Continuous,
                        ql.Annual,
                    )
                )
                vol_curve = ql.BlackVolTermStructureHandle(
                    ql.BlackConstantVol(
                        evaluation_date,
                        calendar,
                        float(volatility.value),
                        day_count,
                    )
                )
                process = ql.BlackScholesMertonProcess(
                    spot_handle,
                    dividend_curve,
                    risk_free_curve,
                    vol_curve,
                )
                payoff = ql.PlainVanillaPayoff(
                    (
                        ql.Option.Call
                        if instrument.option_type.value == "CALL"
                        else ql.Option.Put
                    ),
                    float(instrument.strike),
                )
                exercise = ql.EuropeanExercise(expiry_date)
                option = ql.VanillaOption(payoff, exercise)
                option.setPricingEngine(
                    ql.AnalyticEuropeanEngine(process)
                )

                raw_values: dict[PricingMeasure, float] = {
                    PricingMeasure.NPV: option.NPV(),
                    PricingMeasure.DELTA: option.delta(),
                    PricingMeasure.GAMMA: option.gamma(),
                    PricingMeasure.VEGA: option.vega(),
                    PricingMeasure.THETA: option.theta(),
                }
            except Exception as exc:
                raise ValueError(
                    f"QuantLib European option pricing failed: {exc}"
                ) from exc
            finally:
                settings.evaluationDate = previous_date

        values: list[PricingMeasureValue] = []
        for measure in request.measures:
            raw = raw_values[measure]
            if not math.isfinite(raw):
                raise ValueError(
                    f"QuantLib returned non-finite {measure.value}"
                )
            scale = (
                instrument.multiplier
                if measure
                in {
                    PricingMeasure.NPV,
                    PricingMeasure.VEGA,
                    PricingMeasure.THETA,
                }
                else Decimal("1")
            )
            value = Decimal(str(raw)) * scale
            unit = (
                request.reporting_currency.value
                if measure
                in {
                    PricingMeasure.NPV,
                    PricingMeasure.VEGA,
                    PricingMeasure.THETA,
                }
                else "DECIMAL"
            )
            values.append(
                PricingMeasureValue(
                    measure=measure,
                    value=value,
                    unit=unit,
                )
            )

        return self._result(
            request=request,
            quote_ids=quote_ids,
            values=tuple(values),
            diagnostics=(
                "analytic European Black-Scholes-Merton engine",
                "flat continuously compounded risk-free and dividend curves",
                "constant Black volatility",
                "ACT_365_FIXED day count",
                "NULL_CALENDAR",
                "QuantLib global evaluation date serialized by process lock",
                "prior QuantLib evaluation date restored after pricing",
            ),
            evaluation_date=market_snapshot.valuation_time.date().isoformat(),
        )

    def _result(
        self,
        *,
        request: PricingRequest,
        quote_ids: tuple[str, ...],
        values: tuple[PricingMeasureValue, ...],
        diagnostics: tuple[str, ...],
        evaluation_date: str,
    ) -> PricingResult:
        ordered_values = tuple(
            sorted(values, key=lambda item: item.measure.value)
        )
        payload = {
            "request_id": request.request_id,
            "instrument_id": request.instrument_id,
            "market_snapshot_id": request.market_snapshot_id,
            "model_spec_id": request.model_spec_id,
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.engine_version,
            "engine_evaluation_date": evaluation_date,
            "quote_ids_used": sorted(quote_ids),
            "measures": [
                {
                    "measure": item.measure.value,
                    "value": str(item.value),
                    "unit": item.unit,
                }
                for item in ordered_values
            ],
            "diagnostics": list(diagnostics),
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PricingResult(
            result_id=_content_id("pricing-result", payload),
            request_id=request.request_id,
            instrument_id=request.instrument_id,
            market_snapshot_id=request.market_snapshot_id,
            model_spec_id=request.model_spec_id,
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            engine_evaluation_date=evaluation_date,
            quote_ids_used=tuple(sorted(quote_ids)),
            measures=ordered_values,
            diagnostics=diagnostics,
            order_authority="NONE",
            capital_authority="NONE",
        )

    @staticmethod
    def _validate_binding(
        *,
        request: PricingRequest,
        instrument: PricingInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
    ) -> None:
        if request.request_id != pricing_request_identity(request):
            raise ValueError("pricing request identity mismatch")
        if (
            market_snapshot.snapshot_id
            != market_data_snapshot_identity(market_snapshot)
        ):
            raise ValueError("market snapshot identity mismatch")
        if request.instrument_id != instrument.instrument_id:
            raise ValueError(
                "pricing request binds another instrument"
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
                "pricing request valuation time differs from market snapshot"
            )
        if (
            request.order_authority != "NONE"
            or request.capital_authority != "NONE"
        ):
            raise ValueError(
                "pricing request unexpectedly carries trading authority"
            )

    @staticmethod
    def _require_supported(
        requested: tuple[PricingMeasure, ...],
        supported: set[PricingMeasure],
    ) -> None:
        unsupported = sorted(
            {
                item.value
                for item in requested
                if item not in supported
            }
        )
        if unsupported:
            raise ValueError(
                f"unsupported pricing measures: {unsupported}"
            )

    @staticmethod
    def _require_same_currency(
        instrument_currency: Currency,
        reporting_currency: Currency,
    ) -> None:
        if instrument_currency is not reporting_currency:
            raise ValueError(
                "Stage 11.2 does not perform implicit FX conversion"
            )


class PricingResultStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS pricing_results (
                result_id VARCHAR PRIMARY KEY,
                request_id VARCHAR NOT NULL,
                instrument_id VARCHAR NOT NULL,
                market_snapshot_id VARCHAR NOT NULL,
                model_spec_id VARCHAR NOT NULL,
                engine_name VARCHAR NOT NULL,
                engine_version VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, result: PricingResult) -> bool:
        if result.result_id != pricing_result_identity(result):
            raise ValueError(
                "pricing result content does not match result_id"
            )
        payload = json.dumps(
            pricing_result_payload(result),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM pricing_results
            WHERE result_id = ?
            """,
            [result.result_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("pricing result identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO pricing_results
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.result_id,
                result.request_id,
                result.instrument_id,
                result.market_snapshot_id,
                result.model_spec_id,
                result.engine_name,
                result.engine_version,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def pricing_result_identity(result: PricingResult) -> str:
    return _content_id(
        "pricing-result",
        pricing_result_payload(result),
    )


def pricing_result_payload(
    result: PricingResult,
) -> dict[str, object]:
    return {
        "request_id": result.request_id,
        "instrument_id": result.instrument_id,
        "market_snapshot_id": result.market_snapshot_id,
        "model_spec_id": result.model_spec_id,
        "engine_name": result.engine_name,
        "engine_version": result.engine_version,
        "engine_evaluation_date": result.engine_evaluation_date,
        "quote_ids_used": list(result.quote_ids_used),
        "measures": [
            {
                "measure": item.measure.value,
                "value": str(item.value),
                "unit": item.unit,
            }
            for item in result.measures
        ],
        "diagnostics": list(result.diagnostics),
        "order_authority": result.order_authority,
        "capital_authority": result.capital_authority,
    }


def _parameter_map(
    model: PricingModelSpecification,
) -> dict[str, str]:
    return {name: value for name, value in model.parameters}


def _find_unique_quote(
    snapshot: MarketDataSnapshot,
    *,
    quote_type: MarketQuoteType,
    market_key: str,
    currency: Currency,
) -> MarketQuote:
    matches = tuple(
        item
        for item in snapshot.quotes
        if item.quote_type is quote_type
        and item.market_key == market_key
        and item.currency is currency
        and item.tenor is None
    )
    if len(matches) != 1:
        raise ValueError(
            "pricing requires exactly one quote for "
            f"{quote_type.value}:{market_key}:{currency.value}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _ql_date(value) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
