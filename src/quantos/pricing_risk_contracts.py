from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TypeAlias

import duckdb


class Currency(str, Enum):
    USD = "USD"
    GBP = "GBP"
    EUR = "EUR"
    CNY = "CNY"
    JPY = "JPY"
    CHF = "CHF"
    CAD = "CAD"
    AUD = "AUD"
    HKD = "HKD"


class MarketQuoteType(str, Enum):
    EQUITY_SPOT = "EQUITY_SPOT"
    FX_SPOT = "FX_SPOT"
    DISCOUNT_FACTOR = "DISCOUNT_FACTOR"
    ZERO_RATE = "ZERO_RATE"
    FORWARD_RATE = "FORWARD_RATE"
    VOLATILITY = "VOLATILITY"
    FIXING = "FIXING"


class QuoteUnit(str, Enum):
    DECIMAL = "DECIMAL"
    PRICE = "PRICE"
    FX_RATE = "FX_RATE"
    DISCOUNT_FACTOR = "DISCOUNT_FACTOR"


@dataclass(frozen=True)
class MarketQuote:
    quote_type: MarketQuoteType
    market_key: str
    value: Decimal
    unit: QuoteUnit
    event_time: datetime
    knowledge_time: datetime
    source_fact_ids: tuple[str, ...]
    tenor: str | None = None
    currency: Currency | None = None

    def __post_init__(self) -> None:
        if not self.market_key.strip():
            raise ValueError("market quote requires market_key")
        if not self.value.is_finite():
            raise ValueError("market quote value must be finite")
        if self.event_time.tzinfo is None or self.knowledge_time.tzinfo is None:
            raise ValueError("market quote timestamps must be timezone-aware")
        if self.knowledge_time < self.event_time:
            raise ValueError("market quote cannot be known before event_time")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("market quote requires source fact IDs")
        if len(self.source_fact_ids) != len(set(self.source_fact_ids)):
            raise ValueError("market quote source fact IDs must be unique")
        if self.tenor is not None and not self.tenor.strip():
            raise ValueError("market quote tenor cannot be blank")
        if (
            self.quote_type is MarketQuoteType.DISCOUNT_FACTOR
            and self.value <= 0
        ):
            raise ValueError(
                "discount factor must be strictly positive; values above 1 "
                "are valid when rates are negative"
            )
        if (
            self.quote_type in {
                MarketQuoteType.EQUITY_SPOT,
                MarketQuoteType.FX_SPOT,
                MarketQuoteType.VOLATILITY,
            }
            and self.value <= 0
        ):
            raise ValueError(
                f"{self.quote_type.value} quote must be positive"
            )

    @property
    def semantic_key(self) -> tuple[str, str, str | None, str | None]:
        return (
            self.quote_type.value,
            self.market_key.strip(),
            self.tenor.strip() if self.tenor is not None else None,
            self.currency.value if self.currency is not None else None,
        )

    @property
    def quote_id(self) -> str:
        return _content_id(
            "market-quote",
            {
                "quote_type": self.quote_type.value,
                "market_key": self.market_key.strip(),
                "value": str(self.value),
                "unit": self.unit.value,
                "event_time": self.event_time.isoformat(),
                "knowledge_time": self.knowledge_time.isoformat(),
                "source_fact_ids": sorted(self.source_fact_ids),
                "tenor": (
                    self.tenor.strip()
                    if self.tenor is not None
                    else None
                ),
                "currency": (
                    self.currency.value
                    if self.currency is not None
                    else None
                ),
            },
        )


@dataclass(frozen=True)
class MarketDataSnapshot:
    snapshot_id: str
    valuation_time: datetime
    base_currency: Currency
    quote_ids: tuple[str, ...]
    quotes: tuple[MarketQuote, ...]
    lineage_fingerprint: str


class MarketDataSnapshotBuilder:
    def build(
        self,
        *,
        valuation_time: datetime,
        base_currency: Currency,
        quotes: tuple[MarketQuote, ...],
    ) -> MarketDataSnapshot:
        if valuation_time.tzinfo is None:
            raise ValueError("valuation_time must be timezone-aware")
        if not quotes:
            raise ValueError("market snapshot requires at least one quote")
        if len({item.quote_id for item in quotes}) != len(quotes):
            raise ValueError("duplicate market quote identities")
        semantic_keys = [item.semantic_key for item in quotes]
        if len(set(semantic_keys)) != len(semantic_keys):
            raise ValueError(
                "market snapshot contains duplicate semantic quote keys"
            )
        if any(item.knowledge_time > valuation_time for item in quotes):
            raise ValueError(
                "market snapshot contains future-known market data"
            )

        canonical = tuple(
            sorted(
                quotes,
                key=lambda item: (
                    item.semantic_key,
                    item.quote_id,
                ),
            )
        )
        quote_ids = tuple(item.quote_id for item in canonical)
        lineage_fingerprint = _content_id(
            "market-lineage",
            {
                "quote_ids": list(quote_ids),
                "source_fact_ids": sorted(
                    {
                        source_id
                        for quote in canonical
                        for source_id in quote.source_fact_ids
                    }
                ),
            },
        )
        payload = {
            "valuation_time": valuation_time.isoformat(),
            "base_currency": base_currency.value,
            "quote_ids": list(quote_ids),
            "lineage_fingerprint": lineage_fingerprint,
        }
        return MarketDataSnapshot(
            snapshot_id=_content_id("market-data-snapshot", payload),
            valuation_time=valuation_time,
            base_currency=base_currency,
            quote_ids=quote_ids,
            quotes=canonical,
            lineage_fingerprint=lineage_fingerprint,
        )


class DayCountConvention(str, Enum):
    ACT_360 = "ACT_360"
    ACT_365_FIXED = "ACT_365_FIXED"
    THIRTY_360 = "THIRTY_360"


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class PayReceive(str, Enum):
    PAY = "PAY"
    RECEIVE = "RECEIVE"


@dataclass(frozen=True)
class EquityInstrument:
    security_id: str
    currency: Currency
    multiplier: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not self.security_id.strip():
            raise ValueError("equity instrument requires security_id")
        _require_positive_finite(self.multiplier, "equity multiplier")

    @property
    def instrument_id(self) -> str:
        return _content_id(
            "pricing-instrument",
            {
                "kind": "EQUITY",
                "security_id": self.security_id.strip(),
                "currency": self.currency.value,
                "multiplier": str(self.multiplier),
            },
        )


@dataclass(frozen=True)
class FixedRateBondInstrument:
    contract_id: str
    currency: Currency
    face_value: Decimal
    issue_date: date
    maturity_date: date
    coupon_rate: Decimal
    coupon_frequency_months: int
    day_count: DayCountConvention
    settlement_days: int

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise ValueError("bond requires contract_id")
        _require_positive_finite(self.face_value, "bond face_value")
        if self.issue_date >= self.maturity_date:
            raise ValueError("bond maturity must follow issue_date")
        if not self.coupon_rate.is_finite() or self.coupon_rate < 0:
            raise ValueError(
                "bond coupon_rate must be finite and non-negative"
            )
        if self.coupon_frequency_months not in {1, 3, 6, 12}:
            raise ValueError(
                "bond coupon frequency must be 1, 3, 6, or 12 months"
            )
        if self.settlement_days < 0:
            raise ValueError("bond settlement_days cannot be negative")

    @property
    def instrument_id(self) -> str:
        return _content_id(
            "pricing-instrument",
            {
                "kind": "FIXED_RATE_BOND",
                "contract_id": self.contract_id.strip(),
                "currency": self.currency.value,
                "face_value": str(self.face_value),
                "issue_date": self.issue_date.isoformat(),
                "maturity_date": self.maturity_date.isoformat(),
                "coupon_rate": str(self.coupon_rate),
                "coupon_frequency_months": self.coupon_frequency_months,
                "day_count": self.day_count.value,
                "settlement_days": self.settlement_days,
            },
        )


@dataclass(frozen=True)
class EuropeanOptionInstrument:
    contract_id: str
    underlying_security_id: str
    currency: Currency
    option_type: OptionType
    strike: Decimal
    expiry: datetime
    multiplier: Decimal

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise ValueError("option requires contract_id")
        if not self.underlying_security_id.strip():
            raise ValueError("option requires underlying_security_id")
        _require_positive_finite(self.strike, "option strike")
        _require_positive_finite(self.multiplier, "option multiplier")
        if self.expiry.tzinfo is None:
            raise ValueError("option expiry must be timezone-aware")

    @property
    def instrument_id(self) -> str:
        return _content_id(
            "pricing-instrument",
            {
                "kind": "EUROPEAN_OPTION",
                "contract_id": self.contract_id.strip(),
                "underlying_security_id": (
                    self.underlying_security_id.strip()
                ),
                "currency": self.currency.value,
                "option_type": self.option_type.value,
                "strike": str(self.strike),
                "expiry": self.expiry.isoformat(),
                "multiplier": str(self.multiplier),
            },
        )


@dataclass(frozen=True)
class FixedFloatSwapInstrument:
    contract_id: str
    currency: Currency
    notional: Decimal
    effective_date: date
    maturity_date: date
    fixed_rate: Decimal
    fixed_leg_frequency_months: int
    fixed_leg_day_count: DayCountConvention
    floating_leg_frequency_months: int
    floating_leg_day_count: DayCountConvention
    floating_index_id: str
    floating_spread: Decimal
    fixed_leg_direction: PayReceive

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise ValueError("swap requires contract_id")
        _require_positive_finite(self.notional, "swap notional")
        if self.effective_date >= self.maturity_date:
            raise ValueError("swap maturity must follow effective_date")
        if not self.fixed_rate.is_finite():
            raise ValueError("swap fixed_rate must be finite")
        if self.fixed_leg_frequency_months not in {1, 3, 6, 12}:
            raise ValueError(
                "swap fixed-leg frequency must be 1, 3, 6, or 12 months"
            )
        if self.floating_leg_frequency_months not in {1, 3, 6, 12}:
            raise ValueError(
                "swap floating-leg frequency must be 1, 3, 6, or 12 months"
            )
        if not self.floating_index_id.strip():
            raise ValueError("swap requires floating_index_id")
        if not self.floating_spread.is_finite():
            raise ValueError("swap floating_spread must be finite")

    @property
    def instrument_id(self) -> str:
        return _content_id(
            "pricing-instrument",
            {
                "kind": "FIXED_FLOAT_SWAP",
                "contract_id": self.contract_id.strip(),
                "currency": self.currency.value,
                "notional": str(self.notional),
                "effective_date": self.effective_date.isoformat(),
                "maturity_date": self.maturity_date.isoformat(),
                "fixed_rate": str(self.fixed_rate),
                "fixed_leg_frequency_months": (
                    self.fixed_leg_frequency_months
                ),
                "fixed_leg_day_count": self.fixed_leg_day_count.value,
                "floating_leg_frequency_months": (
                    self.floating_leg_frequency_months
                ),
                "floating_leg_day_count": (
                    self.floating_leg_day_count.value
                ),
                "floating_index_id": self.floating_index_id.strip(),
                "floating_spread": str(self.floating_spread),
                "fixed_leg_direction": self.fixed_leg_direction.value,
            },
        )


PricingInstrument: TypeAlias = (
    EquityInstrument
    | FixedRateBondInstrument
    | EuropeanOptionInstrument
    | FixedFloatSwapInstrument
)


class PricingMeasure(str, Enum):
    NPV = "NPV"
    CLEAN_PRICE = "CLEAN_PRICE"
    DIRTY_PRICE = "DIRTY_PRICE"
    YIELD = "YIELD"
    DELTA = "DELTA"
    GAMMA = "GAMMA"
    VEGA = "VEGA"
    THETA = "THETA"
    DV01 = "DV01"


@dataclass(frozen=True)
class PricingModelSpecification:
    model_family: str
    model_version: str
    parameters: tuple[tuple[str, str], ...]
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.model_family.strip():
            raise ValueError("pricing model_family is required")
        if not self.model_version.strip():
            raise ValueError("pricing model_version is required")
        names = [name for name, _ in self.parameters]
        if any(not name.strip() for name in names):
            raise ValueError("pricing model parameter names cannot be blank")
        if len(names) != len(set(names)):
            raise ValueError(
                "pricing model parameters cannot contain duplicate names"
            )
        if not self.rationale.strip():
            raise ValueError("pricing model rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "pricing model specification requires evidence references"
            )

    @property
    def model_spec_id(self) -> str:
        return _content_id(
            "pricing-model-spec",
            {
                "model_family": self.model_family.strip(),
                "model_version": self.model_version.strip(),
                "parameters": [
                    [name, value]
                    for name, value in sorted(self.parameters)
                ],
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class PricingRequest:
    request_id: str
    instrument_id: str
    market_snapshot_id: str
    model_spec_id: str
    valuation_time: datetime
    measures: tuple[PricingMeasure, ...]
    reporting_currency: Currency
    order_authority: str
    capital_authority: str


class PricingRequestBuilder:
    def build(
        self,
        *,
        instrument: PricingInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        measures: tuple[PricingMeasure, ...],
        reporting_currency: Currency,
    ) -> PricingRequest:
        if not measures:
            raise ValueError("pricing request requires at least one measure")
        if len(set(measures)) != len(measures):
            raise ValueError("pricing request measures must be unique")
        if isinstance(instrument, EuropeanOptionInstrument):
            if instrument.expiry <= market_snapshot.valuation_time:
                raise ValueError(
                    "cannot price an expired European option"
                )
        canonical_measures = tuple(
            sorted(measures, key=lambda item: item.value)
        )
        payload = {
            "instrument_id": instrument.instrument_id,
            "market_snapshot_id": market_snapshot.snapshot_id,
            "model_spec_id": model.model_spec_id,
            "valuation_time": (
                market_snapshot.valuation_time.isoformat()
            ),
            "measures": [
                item.value for item in canonical_measures
            ],
            "reporting_currency": reporting_currency.value,
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PricingRequest(
            request_id=_content_id("pricing-request", payload),
            instrument_id=instrument.instrument_id,
            market_snapshot_id=market_snapshot.snapshot_id,
            model_spec_id=model.model_spec_id,
            valuation_time=market_snapshot.valuation_time,
            measures=canonical_measures,
            reporting_currency=reporting_currency,
            order_authority="NONE",
            capital_authority="NONE",
        )


class ShockKind(str, Enum):
    ABSOLUTE = "ABSOLUTE"
    RELATIVE = "RELATIVE"


@dataclass(frozen=True)
class MarketShock:
    quote_type: MarketQuoteType
    market_key: str
    shock_kind: ShockKind
    shock_value: Decimal
    tenor: str | None = None
    currency: Currency | None = None

    def __post_init__(self) -> None:
        if not self.market_key.strip():
            raise ValueError("market shock requires market_key")
        if not self.shock_value.is_finite():
            raise ValueError("market shock value must be finite")
        if self.tenor is not None and not self.tenor.strip():
            raise ValueError("market shock tenor cannot be blank")

    @property
    def target_key(self) -> tuple[str, str, str | None, str | None]:
        return (
            self.quote_type.value,
            self.market_key.strip(),
            self.tenor.strip() if self.tenor is not None else None,
            self.currency.value if self.currency is not None else None,
        )


@dataclass(frozen=True)
class RiskScenario:
    name: str
    shocks: tuple[MarketShock, ...]
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("risk scenario name is required")
        if not self.shocks:
            raise ValueError("risk scenario requires at least one shock")
        targets = [item.target_key for item in self.shocks]
        if len(targets) != len(set(targets)):
            raise ValueError(
                "risk scenario cannot shock one market target twice"
            )
        if not self.rationale.strip():
            raise ValueError("risk scenario rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("risk scenario requires evidence references")

    @property
    def scenario_id(self) -> str:
        canonical_shocks = sorted(
            self.shocks,
            key=lambda item: (
                item.target_key,
                item.shock_kind.value,
                str(item.shock_value),
            ),
        )
        return _content_id(
            "risk-scenario",
            {
                "name": self.name.strip(),
                "shocks": [
                    {
                        "quote_type": item.quote_type.value,
                        "market_key": item.market_key.strip(),
                        "shock_kind": item.shock_kind.value,
                        "shock_value": str(item.shock_value),
                        "tenor": (
                            item.tenor.strip()
                            if item.tenor is not None
                            else None
                        ),
                        "currency": (
                            item.currency.value
                            if item.currency is not None
                            else None
                        ),
                    }
                    for item in canonical_shocks
                ],
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


class PricingContractStore:
    """Immutable persistence for Stage 11.1 contract artifacts."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS pricing_market_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                valuation_time TIMESTAMPTZ NOT NULL,
                base_currency VARCHAR NOT NULL,
                lineage_fingerprint VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS pricing_instruments (
                instrument_id VARCHAR PRIMARY KEY,
                instrument_kind VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS pricing_requests (
                request_id VARCHAR PRIMARY KEY,
                instrument_id VARCHAR NOT NULL,
                market_snapshot_id VARCHAR NOT NULL,
                model_spec_id VARCHAR NOT NULL,
                valuation_time TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_scenarios (
                scenario_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add_market_snapshot(self, snapshot: MarketDataSnapshot) -> bool:
        expected = market_data_snapshot_identity(snapshot)
        if snapshot.snapshot_id != expected:
            raise ValueError(
                "market snapshot content does not match snapshot_id"
            )
        payload = json.dumps(
            _market_snapshot_json(snapshot),
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._insert_idempotent(
            table="pricing_market_snapshots",
            id_column="snapshot_id",
            artifact_id=snapshot.snapshot_id,
            payload=payload,
            insert_sql=(
                "INSERT INTO pricing_market_snapshots "
                "VALUES (?, ?, ?, ?, ?)"
            ),
            values=[
                snapshot.snapshot_id,
                snapshot.valuation_time,
                snapshot.base_currency.value,
                snapshot.lineage_fingerprint,
                payload,
            ],
        )

    def add_instrument(self, instrument: PricingInstrument) -> bool:
        payload_obj = instrument_payload(instrument)
        payload = json.dumps(
            payload_obj,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._insert_idempotent(
            table="pricing_instruments",
            id_column="instrument_id",
            artifact_id=instrument.instrument_id,
            payload=payload,
            insert_sql="INSERT INTO pricing_instruments VALUES (?, ?, ?)",
            values=[
                instrument.instrument_id,
                payload_obj["kind"],
                payload,
            ],
        )

    def add_request(self, request: PricingRequest) -> bool:
        expected = pricing_request_identity(request)
        if request.request_id != expected:
            raise ValueError(
                "pricing request content does not match request_id"
            )
        payload = json.dumps(
            pricing_request_payload(request),
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._insert_idempotent(
            table="pricing_requests",
            id_column="request_id",
            artifact_id=request.request_id,
            payload=payload,
            insert_sql=(
                "INSERT INTO pricing_requests VALUES (?, ?, ?, ?, ?, ?)"
            ),
            values=[
                request.request_id,
                request.instrument_id,
                request.market_snapshot_id,
                request.model_spec_id,
                request.valuation_time,
                payload,
            ],
        )

    def add_scenario(self, scenario: RiskScenario) -> bool:
        payload = json.dumps(
            risk_scenario_payload(scenario),
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._insert_idempotent(
            table="risk_scenarios",
            id_column="scenario_id",
            artifact_id=scenario.scenario_id,
            payload=payload,
            insert_sql="INSERT INTO risk_scenarios VALUES (?, ?, ?)",
            values=[
                scenario.scenario_id,
                scenario.name.strip(),
                payload,
            ],
        )

    def _insert_idempotent(
        self,
        *,
        table: str,
        id_column: str,
        artifact_id: str,
        payload: str,
        insert_sql: str,
        values: list[object],
    ) -> bool:
        row = self._con.execute(
            f"SELECT payload_json FROM {table} WHERE {id_column} = ?",
            [artifact_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    f"{table} artifact identity conflict"
                )
            return False
        self._con.execute(insert_sql, values)
        return True

    def close(self) -> None:
        self._con.close()


def market_data_snapshot_identity(
    snapshot: MarketDataSnapshot,
) -> str:
    quote_ids = tuple(item.quote_id for item in snapshot.quotes)
    if quote_ids != snapshot.quote_ids:
        raise ValueError(
            "market snapshot quote_ids differ from nested quote content"
        )
    expected_lineage = _content_id(
        "market-lineage",
        {
            "quote_ids": list(snapshot.quote_ids),
            "source_fact_ids": sorted(
                {
                    source_id
                    for quote in snapshot.quotes
                    for source_id in quote.source_fact_ids
                }
            ),
        },
    )
    if expected_lineage != snapshot.lineage_fingerprint:
        raise ValueError(
            "market snapshot lineage fingerprint does not match quote content"
        )
    return _content_id(
        "market-data-snapshot",
        {
            "valuation_time": snapshot.valuation_time.isoformat(),
            "base_currency": snapshot.base_currency.value,
            "quote_ids": list(snapshot.quote_ids),
            "lineage_fingerprint": snapshot.lineage_fingerprint,
        },
    )


def pricing_request_identity(request: PricingRequest) -> str:
    return _content_id(
        "pricing-request",
        pricing_request_payload(request),
    )


def pricing_request_payload(
    request: PricingRequest,
) -> dict[str, object]:
    return {
        "instrument_id": request.instrument_id,
        "market_snapshot_id": request.market_snapshot_id,
        "model_spec_id": request.model_spec_id,
        "valuation_time": request.valuation_time.isoformat(),
        "measures": [item.value for item in request.measures],
        "reporting_currency": request.reporting_currency.value,
        "order_authority": request.order_authority,
        "capital_authority": request.capital_authority,
    }


def instrument_payload(
    instrument: PricingInstrument,
) -> dict[str, object]:
    if isinstance(instrument, EquityInstrument):
        return {
            "kind": "EQUITY",
            "security_id": instrument.security_id.strip(),
            "currency": instrument.currency.value,
            "multiplier": str(instrument.multiplier),
        }
    if isinstance(instrument, FixedRateBondInstrument):
        return {
            "kind": "FIXED_RATE_BOND",
            "contract_id": instrument.contract_id.strip(),
            "currency": instrument.currency.value,
            "face_value": str(instrument.face_value),
            "issue_date": instrument.issue_date.isoformat(),
            "maturity_date": instrument.maturity_date.isoformat(),
            "coupon_rate": str(instrument.coupon_rate),
            "coupon_frequency_months": (
                instrument.coupon_frequency_months
            ),
            "day_count": instrument.day_count.value,
            "settlement_days": instrument.settlement_days,
        }
    if isinstance(instrument, EuropeanOptionInstrument):
        return {
            "kind": "EUROPEAN_OPTION",
            "contract_id": instrument.contract_id.strip(),
            "underlying_security_id": (
                instrument.underlying_security_id.strip()
            ),
            "currency": instrument.currency.value,
            "option_type": instrument.option_type.value,
            "strike": str(instrument.strike),
            "expiry": instrument.expiry.isoformat(),
            "multiplier": str(instrument.multiplier),
        }
    if isinstance(instrument, FixedFloatSwapInstrument):
        return {
            "kind": "FIXED_FLOAT_SWAP",
            "contract_id": instrument.contract_id.strip(),
            "currency": instrument.currency.value,
            "notional": str(instrument.notional),
            "effective_date": instrument.effective_date.isoformat(),
            "maturity_date": instrument.maturity_date.isoformat(),
            "fixed_rate": str(instrument.fixed_rate),
            "fixed_leg_frequency_months": (
                instrument.fixed_leg_frequency_months
            ),
            "fixed_leg_day_count": (
                instrument.fixed_leg_day_count.value
            ),
            "floating_leg_frequency_months": (
                instrument.floating_leg_frequency_months
            ),
            "floating_leg_day_count": (
                instrument.floating_leg_day_count.value
            ),
            "floating_index_id": (
                instrument.floating_index_id.strip()
            ),
            "floating_spread": str(instrument.floating_spread),
            "fixed_leg_direction": instrument.fixed_leg_direction.value,
        }
    raise ValueError("unsupported pricing instrument")


def risk_scenario_payload(
    scenario: RiskScenario,
) -> dict[str, object]:
    canonical_shocks = sorted(
        scenario.shocks,
        key=lambda item: (
            item.target_key,
            item.shock_kind.value,
            str(item.shock_value),
        ),
    )
    return {
        "name": scenario.name.strip(),
        "shocks": [
            {
                "quote_type": item.quote_type.value,
                "market_key": item.market_key.strip(),
                "shock_kind": item.shock_kind.value,
                "shock_value": str(item.shock_value),
                "tenor": (
                    item.tenor.strip()
                    if item.tenor is not None
                    else None
                ),
                "currency": (
                    item.currency.value
                    if item.currency is not None
                    else None
                ),
            }
            for item in canonical_shocks
        ],
        "rationale": scenario.rationale,
        "evidence_references": sorted(
            scenario.evidence_references
        ),
    }


def _market_snapshot_json(
    snapshot: MarketDataSnapshot,
) -> dict[str, object]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "valuation_time": snapshot.valuation_time.isoformat(),
        "base_currency": snapshot.base_currency.value,
        "quote_ids": list(snapshot.quote_ids),
        "quotes": [
            {
                "quote_id": item.quote_id,
                "quote_type": item.quote_type.value,
                "market_key": item.market_key,
                "value": str(item.value),
                "unit": item.unit.value,
                "event_time": item.event_time.isoformat(),
                "knowledge_time": item.knowledge_time.isoformat(),
                "source_fact_ids": list(item.source_fact_ids),
                "tenor": item.tenor,
                "currency": (
                    item.currency.value
                    if item.currency is not None
                    else None
                ),
            }
            for item in snapshot.quotes
        ],
        "lineage_fingerprint": snapshot.lineage_fingerprint,
    }


def _require_positive_finite(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
