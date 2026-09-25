from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TypeAlias

import duckdb

from .bond_pricing import (
    BondPricingResult,
    BondPricingValidationPolicy,
    QuantLibFixedRateBondAdapter,
)
from .interest_rate_curves import (
    CurveConstructionPolicy,
    DiscountCurveBuilder,
)
from .pricing_quantlib import (
    PricingResult,
    QuantLibPricingAdapter,
)
from .pricing_risk_contracts import (
    EquityInstrument,
    EuropeanOptionInstrument,
    FixedFloatSwapInstrument,
    FixedRateBondInstrument,
    MarketDataSnapshot,
    MarketDataSnapshotBuilder,
    MarketQuote,
    MarketShock,
    PricingInstrument,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequest,
    PricingRequestBuilder,
    RiskScenario,
    ShockKind,
    market_data_snapshot_identity,
    pricing_request_identity,
)
from .swap_pricing import (
    QuantLibFixedFloatSwapAdapter,
    SwapPricingResult,
    SwapPricingValidationPolicy,
)


ScenarioPricedResult: TypeAlias = (
    PricingResult | BondPricingResult | SwapPricingResult
)


@dataclass(frozen=True)
class ScenarioQuoteTransformation:
    base_quote_id: str
    shocked_quote_id: str
    quote_type: str
    market_key: str
    tenor: str | None
    currency: str | None
    shock_kind: ShockKind
    shock_value: Decimal
    base_value: Decimal
    shocked_value: Decimal


@dataclass(frozen=True)
class ScenarioMarketState:
    state_id: str
    scenario_id: str
    base_snapshot_id: str
    shocked_snapshot_id: str
    transformations: tuple[ScenarioQuoteTransformation, ...]


class ScenarioMarketTransformer:
    def apply(
        self,
        *,
        base_snapshot: MarketDataSnapshot,
        scenario: RiskScenario,
    ) -> tuple[MarketDataSnapshot, ScenarioMarketState]:
        if (
            base_snapshot.snapshot_id
            != market_data_snapshot_identity(base_snapshot)
        ):
            raise ValueError("base market snapshot identity mismatch")

        replacements: dict[str, MarketQuote] = {}
        transformations: list[ScenarioQuoteTransformation] = []
        for shock in scenario.shocks:
            matches = tuple(
                quote
                for quote in base_snapshot.quotes
                if _shock_matches_quote(shock, quote)
            )
            if len(matches) != 1:
                raise ValueError(
                    "each scenario shock must match exactly one base quote; "
                    f"target={shock.target_key}, matches={len(matches)}"
                )
            base = matches[0]
            if base.quote_id in replacements:
                raise ValueError(
                    "scenario attempts to transform one base quote twice"
                )
            shocked_value = _apply_shock(base.value, shock)
            synthetic_source = (
                f"derived:{scenario.scenario_id}:{base.quote_id}"
            )
            shocked = MarketQuote(
                quote_type=base.quote_type,
                market_key=base.market_key,
                value=shocked_value,
                unit=base.unit,
                event_time=base.event_time,
                knowledge_time=base.knowledge_time,
                source_fact_ids=tuple(
                    sorted(
                        {
                            *base.source_fact_ids,
                            synthetic_source,
                        }
                    )
                ),
                tenor=base.tenor,
                currency=base.currency,
            )
            replacements[base.quote_id] = shocked
            transformations.append(
                ScenarioQuoteTransformation(
                    base_quote_id=base.quote_id,
                    shocked_quote_id=shocked.quote_id,
                    quote_type=base.quote_type.value,
                    market_key=base.market_key,
                    tenor=base.tenor,
                    currency=(
                        base.currency.value
                        if base.currency is not None
                        else None
                    ),
                    shock_kind=shock.shock_kind,
                    shock_value=shock.shock_value,
                    base_value=base.value,
                    shocked_value=shocked_value,
                )
            )

        shocked_quotes = tuple(
            replacements.get(item.quote_id, item)
            for item in base_snapshot.quotes
        )
        shocked_snapshot = MarketDataSnapshotBuilder().build(
            valuation_time=base_snapshot.valuation_time,
            base_currency=base_snapshot.base_currency,
            quotes=shocked_quotes,
        )
        canonical = tuple(
            sorted(
                transformations,
                key=lambda item: (
                    item.quote_type,
                    item.market_key,
                    item.tenor or "",
                    item.currency or "",
                    item.base_quote_id,
                ),
            )
        )
        payload = {
            "scenario_id": scenario.scenario_id,
            "base_snapshot_id": base_snapshot.snapshot_id,
            "shocked_snapshot_id": shocked_snapshot.snapshot_id,
            "transformations": [
                _transformation_payload(item)
                for item in canonical
            ],
        }
        state = ScenarioMarketState(
            state_id=_content_id("scenario-market-state", payload),
            scenario_id=scenario.scenario_id,
            base_snapshot_id=base_snapshot.snapshot_id,
            shocked_snapshot_id=shocked_snapshot.snapshot_id,
            transformations=canonical,
        )
        return shocked_snapshot, state


@dataclass(frozen=True)
class ScenarioRevaluationResult:
    revaluation_id: str
    scenario_id: str
    scenario_market_state_id: str
    instrument_id: str
    base_request_id: str
    shocked_request_id: str
    base_snapshot_id: str
    shocked_snapshot_id: str
    base_pricing_result_id: str
    shocked_pricing_result_id: str
    base_curve_ids: tuple[str, ...]
    shocked_curve_ids: tuple[str, ...]
    reporting_currency: str
    base_npv: Decimal
    shocked_npv: Decimal
    scenario_pnl: Decimal
    transformations: tuple[ScenarioQuoteTransformation, ...]
    diagnostics: tuple[str, ...]
    order_authority: str
    capital_authority: str


class ScenarioRevaluationEngine:
    def __init__(self) -> None:
        self._transformer = ScenarioMarketTransformer()
        self._simple_adapter = QuantLibPricingAdapter()
        self._curve_builder = DiscountCurveBuilder()
        self._bond_adapter = QuantLibFixedRateBondAdapter()
        self._swap_adapter = QuantLibFixedFloatSwapAdapter()

    def revalue_spot_or_option(
        self,
        *,
        request: PricingRequest,
        instrument: EquityInstrument | EuropeanOptionInstrument,
        base_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        scenario: RiskScenario,
    ) -> ScenarioRevaluationResult:
        self._require_base_request(
            request=request,
            instrument=instrument,
            base_snapshot=base_snapshot,
            model=model,
        )
        shocked_snapshot, state = self._transformer.apply(
            base_snapshot=base_snapshot,
            scenario=scenario,
        )
        shocked_request = _shocked_request(
            base_request=request,
            instrument=instrument,
            shocked_snapshot=shocked_snapshot,
            model=model,
        )
        base_result = self._simple_adapter.price(
            request=request,
            instrument=instrument,
            market_snapshot=base_snapshot,
            model=model,
        )
        shocked_result = self._simple_adapter.price(
            request=shocked_request,
            instrument=instrument,
            market_snapshot=shocked_snapshot,
            model=model,
        )
        return self._result(
            scenario=scenario,
            state=state,
            request=request,
            shocked_request=shocked_request,
            instrument=instrument,
            base_result=base_result,
            shocked_result=shocked_result,
            base_curve_ids=(),
            shocked_curve_ids=(),
            diagnostics=(
                "scenario revaluation from exact base/shocked snapshots",
                "no curve rebuild required for this pricing path",
            ),
        )

    def revalue_bond(
        self,
        *,
        request: PricingRequest,
        instrument: FixedRateBondInstrument,
        base_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        scenario: RiskScenario,
        curve_policy: CurveConstructionPolicy,
        validation_policy: BondPricingValidationPolicy,
    ) -> ScenarioRevaluationResult:
        self._require_base_request(
            request=request,
            instrument=instrument,
            base_snapshot=base_snapshot,
            model=model,
        )
        shocked_snapshot, state = self._transformer.apply(
            base_snapshot=base_snapshot,
            scenario=scenario,
        )
        shocked_request = _shocked_request(
            base_request=request,
            instrument=instrument,
            shocked_snapshot=shocked_snapshot,
            model=model,
        )
        base_curve = self._curve_builder.build(
            snapshot=base_snapshot,
            policy=curve_policy,
        )
        shocked_curve = self._curve_builder.build(
            snapshot=shocked_snapshot,
            policy=curve_policy,
        )
        base_result = self._bond_adapter.price(
            request=request,
            instrument=instrument,
            market_snapshot=base_snapshot,
            model=model,
            discount_curve=base_curve,
            validation_policy=validation_policy,
        )
        shocked_result = self._bond_adapter.price(
            request=shocked_request,
            instrument=instrument,
            market_snapshot=shocked_snapshot,
            model=model,
            discount_curve=shocked_curve,
            validation_policy=validation_policy,
        )
        return self._result(
            scenario=scenario,
            state=state,
            request=request,
            shocked_request=shocked_request,
            instrument=instrument,
            base_result=base_result,
            shocked_result=shocked_result,
            base_curve_ids=(base_curve.curve_id,),
            shocked_curve_ids=(shocked_curve.curve_id,),
            diagnostics=(
                "scenario revaluation rebuilt discount curve under frozen policy",
                "base and shocked bond results independently reference-verified",
            ),
        )

    def revalue_swap(
        self,
        *,
        request: PricingRequest,
        instrument: FixedFloatSwapInstrument,
        base_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        scenario: RiskScenario,
        discount_curve_policy: CurveConstructionPolicy,
        forwarding_curve_policy: CurveConstructionPolicy,
        validation_policy: SwapPricingValidationPolicy,
    ) -> ScenarioRevaluationResult:
        self._require_base_request(
            request=request,
            instrument=instrument,
            base_snapshot=base_snapshot,
            model=model,
        )
        shocked_snapshot, state = self._transformer.apply(
            base_snapshot=base_snapshot,
            scenario=scenario,
        )
        shocked_request = _shocked_request(
            base_request=request,
            instrument=instrument,
            shocked_snapshot=shocked_snapshot,
            model=model,
        )
        base_discount = self._curve_builder.build(
            snapshot=base_snapshot,
            policy=discount_curve_policy,
        )
        base_forwarding = self._curve_builder.build(
            snapshot=base_snapshot,
            policy=forwarding_curve_policy,
        )
        shocked_discount = self._curve_builder.build(
            snapshot=shocked_snapshot,
            policy=discount_curve_policy,
        )
        shocked_forwarding = self._curve_builder.build(
            snapshot=shocked_snapshot,
            policy=forwarding_curve_policy,
        )
        base_result = self._swap_adapter.price(
            request=request,
            instrument=instrument,
            market_snapshot=base_snapshot,
            model=model,
            discount_curve=base_discount,
            forwarding_curve=base_forwarding,
            validation_policy=validation_policy,
        )
        shocked_result = self._swap_adapter.price(
            request=shocked_request,
            instrument=instrument,
            market_snapshot=shocked_snapshot,
            model=model,
            discount_curve=shocked_discount,
            forwarding_curve=shocked_forwarding,
            validation_policy=validation_policy,
        )
        return self._result(
            scenario=scenario,
            state=state,
            request=request,
            shocked_request=shocked_request,
            instrument=instrument,
            base_result=base_result,
            shocked_result=shocked_result,
            base_curve_ids=(
                base_discount.curve_id,
                base_forwarding.curve_id,
            ),
            shocked_curve_ids=(
                shocked_discount.curve_id,
                shocked_forwarding.curve_id,
            ),
            diagnostics=(
                "scenario revaluation rebuilt discount and forwarding curves independently",
                "base and shocked swap results independently reference-verified",
            ),
        )

    @staticmethod
    def _require_base_request(
        *,
        request: PricingRequest,
        instrument: PricingInstrument,
        base_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
    ) -> None:
        if request.request_id != pricing_request_identity(request):
            raise ValueError("base pricing request identity mismatch")
        if (
            base_snapshot.snapshot_id
            != market_data_snapshot_identity(base_snapshot)
        ):
            raise ValueError("base market snapshot identity mismatch")
        if request.instrument_id != instrument.instrument_id:
            raise ValueError(
                "base pricing request binds another instrument"
            )
        if request.market_snapshot_id != base_snapshot.snapshot_id:
            raise ValueError(
                "base pricing request binds another market snapshot"
            )
        if request.model_spec_id != model.model_spec_id:
            raise ValueError(
                "base pricing request binds another model specification"
            )
        if PricingMeasure.NPV not in request.measures:
            raise ValueError(
                "scenario revaluation requires NPV in base pricing request"
            )
        if (
            request.order_authority != "NONE"
            or request.capital_authority != "NONE"
        ):
            raise ValueError(
                "base pricing request unexpectedly carries trading authority"
            )

    @staticmethod
    def _result(
        *,
        scenario: RiskScenario,
        state: ScenarioMarketState,
        request: PricingRequest,
        shocked_request: PricingRequest,
        instrument: PricingInstrument,
        base_result: ScenarioPricedResult,
        shocked_result: ScenarioPricedResult,
        base_curve_ids: tuple[str, ...],
        shocked_curve_ids: tuple[str, ...],
        diagnostics: tuple[str, ...],
    ) -> ScenarioRevaluationResult:
        base_npv = _npv(base_result)
        shocked_npv = _npv(shocked_result)
        pnl = shocked_npv - base_npv
        payload = {
            "scenario_id": scenario.scenario_id,
            "scenario_market_state_id": state.state_id,
            "instrument_id": instrument.instrument_id,
            "base_request_id": request.request_id,
            "shocked_request_id": shocked_request.request_id,
            "base_snapshot_id": state.base_snapshot_id,
            "shocked_snapshot_id": state.shocked_snapshot_id,
            "base_pricing_result_id": base_result.result_id,
            "shocked_pricing_result_id": shocked_result.result_id,
            "base_curve_ids": list(base_curve_ids),
            "shocked_curve_ids": list(shocked_curve_ids),
            "reporting_currency": request.reporting_currency.value,
            "base_npv": str(base_npv),
            "shocked_npv": str(shocked_npv),
            "scenario_pnl": str(pnl),
            "transformations": [
                _transformation_payload(item)
                for item in state.transformations
            ],
            "diagnostics": list(diagnostics),
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return ScenarioRevaluationResult(
            revaluation_id=_content_id(
                "scenario-revaluation",
                payload,
            ),
            scenario_id=scenario.scenario_id,
            scenario_market_state_id=state.state_id,
            instrument_id=instrument.instrument_id,
            base_request_id=request.request_id,
            shocked_request_id=shocked_request.request_id,
            base_snapshot_id=state.base_snapshot_id,
            shocked_snapshot_id=state.shocked_snapshot_id,
            base_pricing_result_id=base_result.result_id,
            shocked_pricing_result_id=shocked_result.result_id,
            base_curve_ids=base_curve_ids,
            shocked_curve_ids=shocked_curve_ids,
            reporting_currency=request.reporting_currency.value,
            base_npv=base_npv,
            shocked_npv=shocked_npv,
            scenario_pnl=pnl,
            transformations=state.transformations,
            diagnostics=diagnostics,
            order_authority="NONE",
            capital_authority="NONE",
        )


class ScenarioRevaluationStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_revaluations (
                revaluation_id VARCHAR PRIMARY KEY,
                scenario_id VARCHAR NOT NULL,
                scenario_market_state_id VARCHAR NOT NULL,
                instrument_id VARCHAR NOT NULL,
                base_snapshot_id VARCHAR NOT NULL,
                shocked_snapshot_id VARCHAR NOT NULL,
                base_pricing_result_id VARCHAR NOT NULL,
                shocked_pricing_result_id VARCHAR NOT NULL,
                scenario_pnl VARCHAR NOT NULL,
                reporting_currency VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, result: ScenarioRevaluationResult) -> bool:
        if result.revaluation_id != scenario_revaluation_identity(result):
            raise ValueError(
                "scenario revaluation content does not match revaluation_id"
            )
        payload = json.dumps(
            scenario_revaluation_payload(result),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM scenario_revaluations
            WHERE revaluation_id = ?
            """,
            [result.revaluation_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "scenario revaluation identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO scenario_revaluations
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.revaluation_id,
                result.scenario_id,
                result.scenario_market_state_id,
                result.instrument_id,
                result.base_snapshot_id,
                result.shocked_snapshot_id,
                result.base_pricing_result_id,
                result.shocked_pricing_result_id,
                str(result.scenario_pnl),
                result.reporting_currency,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def scenario_revaluation_identity(
    result: ScenarioRevaluationResult,
) -> str:
    return _content_id(
        "scenario-revaluation",
        {
            key: value
            for key, value in scenario_revaluation_payload(result).items()
            if key != "revaluation_id"
        },
    )


def scenario_revaluation_payload(
    result: ScenarioRevaluationResult,
) -> dict[str, object]:
    return {
        "revaluation_id": result.revaluation_id,
        "scenario_id": result.scenario_id,
        "scenario_market_state_id": result.scenario_market_state_id,
        "instrument_id": result.instrument_id,
        "base_request_id": result.base_request_id,
        "shocked_request_id": result.shocked_request_id,
        "base_snapshot_id": result.base_snapshot_id,
        "shocked_snapshot_id": result.shocked_snapshot_id,
        "base_pricing_result_id": result.base_pricing_result_id,
        "shocked_pricing_result_id": result.shocked_pricing_result_id,
        "base_curve_ids": list(result.base_curve_ids),
        "shocked_curve_ids": list(result.shocked_curve_ids),
        "reporting_currency": result.reporting_currency,
        "base_npv": str(result.base_npv),
        "shocked_npv": str(result.shocked_npv),
        "scenario_pnl": str(result.scenario_pnl),
        "transformations": [
            _transformation_payload(item)
            for item in result.transformations
        ],
        "diagnostics": list(result.diagnostics),
        "order_authority": result.order_authority,
        "capital_authority": result.capital_authority,
    }


def scenario_market_state_identity(
    state: ScenarioMarketState,
) -> str:
    return _content_id(
        "scenario-market-state",
        {
            "scenario_id": state.scenario_id,
            "base_snapshot_id": state.base_snapshot_id,
            "shocked_snapshot_id": state.shocked_snapshot_id,
            "transformations": [
                _transformation_payload(item)
                for item in state.transformations
            ],
        },
    )


def _shocked_request(
    *,
    base_request: PricingRequest,
    instrument: PricingInstrument,
    shocked_snapshot: MarketDataSnapshot,
    model: PricingModelSpecification,
) -> PricingRequest:
    return PricingRequestBuilder().build(
        instrument=instrument,
        market_snapshot=shocked_snapshot,
        model=model,
        measures=base_request.measures,
        reporting_currency=base_request.reporting_currency,
    )


def _npv(result: ScenarioPricedResult) -> Decimal:
    values = tuple(
        item.value
        for item in result.measures
        if item.measure is PricingMeasure.NPV
    )
    if len(values) != 1:
        raise ValueError(
            "priced result must contain exactly one NPV measure"
        )
    return values[0]


def _shock_matches_quote(
    shock: MarketShock,
    quote: MarketQuote,
) -> bool:
    return (
        quote.quote_type is shock.quote_type
        and quote.market_key == shock.market_key
        and quote.tenor == shock.tenor
        and (
            shock.currency is None
            or quote.currency is shock.currency
        )
    )


def _apply_shock(
    base_value: Decimal,
    shock: MarketShock,
) -> Decimal:
    if shock.shock_kind is ShockKind.ABSOLUTE:
        output = base_value + shock.shock_value
    elif shock.shock_kind is ShockKind.RELATIVE:
        output = base_value * (
            Decimal("1") + shock.shock_value
        )
    else:
        raise ValueError("unsupported scenario shock kind")
    if not output.is_finite():
        raise ValueError("scenario shock produced non-finite quote")
    return output


def _transformation_payload(
    item: ScenarioQuoteTransformation,
) -> dict[str, object]:
    return {
        "base_quote_id": item.base_quote_id,
        "shocked_quote_id": item.shocked_quote_id,
        "quote_type": item.quote_type,
        "market_key": item.market_key,
        "tenor": item.tenor,
        "currency": item.currency,
        "shock_kind": item.shock_kind.value,
        "shock_value": str(item.shock_value),
        "base_value": str(item.base_value),
        "shocked_value": str(item.shocked_value),
    }


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
