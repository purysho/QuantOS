"""Stages 14.3–14.6 — broader ORE differential coverage.

* 14.3 fixed-rate bond NPV versus the Stage 11.4 independent reference;
* 14.4 European equity option NPV versus a QuantOS closed-form
  Black-Scholes-Merton reference and the Stage 11.2 QuantLib result;
* 14.5 ORE's own SENSITIVITY analytic (bucketed zero-rate deltas) versus
  QuantOS single-pillar scenario revaluations;
* 14.6 ORE's own STRESS analytic versus a Stage 11.6 scenario revaluation.

Every comparison uses a content-addressed ORE input bundle, runs ORE only in
the isolated worker process, and preserves mismatches. For 14.5 and 14.6
ORE's simulation-market grid is set to exactly the QuantOS curve
pillars, so ORE's zero-rate shifts at grid points are economically the same
as QuantOS shocking the pillar quote and rebuilding the curve; a curve
whose pillars are not tenor-aligned is refused.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum

from .bond_pricing import BondPricingResult, bond_pricing_result_identity
from .interest_rate_curves import (
    CurveConstructionPolicy,
    DiscountCurveArtifact,
    DiscountCurveBuilder,
    _tenor_date,
    discount_curve_identity,
)
from .ore_risk import (
    ORE_ADAPTER_VERSION,
    OREDifferentialPolicy,
    OREDifferentialState,
    OREEngineRunner,
    OREInputBuilder,
    OREInputBundle,
    _ORE_DAY_COUNTS,
    _plain,
    with_bundle_identity,
)
from .pricing_quantlib import PricingResult, pricing_result_identity
from .pricing_risk_contracts import (
    EuropeanOptionInstrument,
    FixedFloatSwapInstrument,
    FixedRateBondInstrument,
    MarketDataSnapshot,
    MarketQuoteType,
    OptionType,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequest,
    RiskScenario,
    ShockKind,
)
from .scenario_revaluation import (
    ScenarioMarketTransformer,
    ScenarioRevaluationResult,
    scenario_revaluation_identity,
)

PRODUCTS_ADAPTER_VERSION = "14.6"
BUCKET_SHIFT = Decimal("0.0001")

_ZERO_CONVENTIONS = (
    "  <Zero>\n"
    "    <Id>FC-ZERO-CONTINUOUS-A365F</Id>\n"
    "    <TenorBased>false</TenorBased>\n"
    "    <DayCounter>A365F</DayCounter>\n"
    "    <Compounding>Continuous</Compounding>\n"
    "  </Zero>\n"
)

PRICING_ENGINES_XML = (
    "<?xml version=\"1.0\"?>\n<PricingEngines>\n"
    "  <Product type=\"Swap\"><Model>DiscountedCashflows</Model><ModelParameters/>"
    "<Engine>DiscountingSwapEngine</Engine><EngineParameters/></Product>\n"
    "  <Product type=\"Bond\"><Model>DiscountedCashflows</Model><ModelParameters/>"
    "<Engine>DiscountingRiskyBondEngine</Engine><EngineParameters>"
    "<Parameter name=\"TimestepPeriod\">6M</Parameter></EngineParameters></Product>\n"
    "  <Product type=\"EquityOption\"><Model>BlackScholesMerton</Model><ModelParameters/>"
    "<Engine>AnalyticEuropeanEngine</Engine><EngineParameters/></Product>\n"
    "</PricingEngines>\n"
)


@dataclass(frozen=True)
class OREProductDifferentialResult:
    result_id: str
    product: str
    source_result_id: str
    instrument_id: str
    input_bundle_id: str
    policy_id: str
    ore_version: str
    adapter_version: str
    ore_npv: Decimal
    reference_npv: Decimal
    quantlib_npv: Decimal
    absolute_difference_vs_reference: Decimal
    absolute_difference_vs_quantlib: Decimal
    state: OREDifferentialState
    trust_authority: str
    order_authority: str
    capital_authority: str


@dataclass(frozen=True)
class OREBucketDelta:
    factor: str
    scenario_id: str
    revaluation_id: str
    reference_pnl: Decimal
    ore_delta: Decimal
    absolute_difference: Decimal


@dataclass(frozen=True)
class ORESensitivityDifferentialResult:
    result_id: str
    instrument_id: str
    input_bundle_id: str
    policy_id: str
    ore_version: str
    buckets: tuple[OREBucketDelta, ...]
    maximum_absolute_difference: Decimal
    state: OREDifferentialState
    trust_authority: str
    order_authority: str
    capital_authority: str


@dataclass(frozen=True)
class OREStressDifferentialResult:
    result_id: str
    revaluation_id: str
    scenario_id: str
    instrument_id: str
    input_bundle_id: str
    policy_id: str
    ore_version: str
    reference_pnl: Decimal
    ore_pnl: Decimal
    absolute_difference: Decimal
    state: OREDifferentialState
    trust_authority: str
    order_authority: str
    capital_authority: str


# ----------------------------------------------------------------- 14.3 bond


class OREBondDifferential:
    TRADE_ID = "FC-BOND"

    def bundle(
        self,
        *,
        instrument: FixedRateBondInstrument,
        discount_curve: DiscountCurveArtifact,
        settlement_date: date,
    ) -> OREInputBundle:
        _require_curve(discount_curve, instrument.currency.value, instrument.maturity_date)
        if instrument.day_count not in _ORE_DAY_COUNTS:
            raise ValueError(f"{instrument.day_count.value} has no frozen ORE mapping")
        if instrument.issue_date.day > 28:
            raise ValueError(
                "ORE bond overlap requires issue day <= 28 so month-end rolling "
                "cannot differ between schedule generators"
            )
        valuation = discount_curve.valuation_date
        months = instrument.coupon_frequency_months
        dates = _regular_dates(instrument.issue_date, instrument.maturity_date, months)
        if any(valuation < d <= settlement_date for d in dates[1:]):
            raise ValueError(
                "a bond cash flow falls between valuation and settlement; the "
                "reference excludes it while ORE's valuation-date NPV includes it"
            )
        if dates[-1] <= valuation:
            raise ValueError("bond has no future cash flows")
        ccy = instrument.currency.value
        portfolio = (
            "<?xml version=\"1.0\"?>\n<Portfolio>\n"
            f"  <Trade id=\"{self.TRADE_ID}\"><TradeType>Bond</TradeType>"
            "<Envelope><CounterParty>FC-RESEARCH</CounterParty><NettingSetId>FC-RESEARCH</NettingSetId><AdditionalFields/></Envelope>\n"
            "    <BondData><IssuerId>FC-ISSUER</IssuerId><SecurityId>FC-SECURITY</SecurityId>"
            "<ReferenceCurveId>FCDISC</ReferenceCurveId><SettlementDays>0</SettlementDays>"
            f"<Calendar>NullCalendar</Calendar><IssueDate>{instrument.issue_date.isoformat()}</IssueDate>\n"
            f"      <LegData><LegType>Fixed</LegType><Payer>false</Payer><Currency>{ccy}</Currency>"
            f"<Notionals><Notional>{_plain(instrument.face_value)}</Notional></Notionals>"
            f"<DayCounter>{_ORE_DAY_COUNTS[instrument.day_count]}</DayCounter>"
            "<PaymentConvention>Unadjusted</PaymentConvention>"
            f"<FixedLegData><Rates><Rate>{_plain(instrument.coupon_rate)}</Rate></Rates></FixedLegData>"
            f"{_schedule_xml(instrument.issue_date, instrument.maturity_date, months)}</LegData>\n"
            "    </BondData>\n  </Trade>\n</Portfolio>\n"
        )
        todays_market = (
            "<?xml version=\"1.0\"?>\n<TodaysMarket>\n"
            "  <Configuration id=\"default\"><DiscountingCurvesId>default</DiscountingCurvesId>"
            "<YieldCurvesId>default</YieldCurvesId></Configuration>\n"
            f"  <DiscountingCurves id=\"default\"><DiscountingCurve currency=\"{ccy}\">Yield/{ccy}/FCDISC</DiscountingCurve></DiscountingCurves>\n"
            f"  <YieldCurves id=\"default\"><YieldCurve name=\"FCDISC\">Yield/{ccy}/FCDISC</YieldCurve></YieldCurves>\n"
            "</TodaysMarket>\n"
        )
        return with_bundle_identity(
            OREInputBundle(
                bundle_id="",
                asof=valuation,
                conventions_xml=f"<?xml version=\"1.0\"?>\n<Conventions>\n{_ZERO_CONVENTIONS}</Conventions>\n",
                curve_config_xml=_curve_config(((("FCDISC", discount_curve)),), ccy),
                todays_market_xml=todays_market,
                pricing_engine_xml=PRICING_ENGINES_XML,
                portfolio_xml=portfolio,
                market_lines=_curve_market_lines((("FCDISC", discount_curve),), ccy),
                trade_id=self.TRADE_ID,
                base_currency=ccy,
            )
        )

    def compare(
        self,
        *,
        bond_result: BondPricingResult,
        instrument: FixedRateBondInstrument,
        discount_curve: DiscountCurveArtifact,
        policy: OREDifferentialPolicy,
        runner: OREEngineRunner | None = None,
    ) -> OREProductDifferentialResult:
        if bond_result.result_id != bond_pricing_result_identity(bond_result):
            raise ValueError("bond pricing result identity mismatch")
        if not bond_result.reference_verified:
            raise ValueError("bond reference was not verified against QuantLib")
        if bond_result.instrument_id != instrument.instrument_id:
            raise ValueError("bond result belongs to another instrument")
        if bond_result.discount_curve_id != discount_curve.curve_id:
            raise ValueError("bond result used another curve")
        bundle = self.bundle(
            instrument=instrument,
            discount_curve=discount_curve,
            settlement_date=bond_result.settlement_date,
        )
        return _product_result(
            product="FIXED_RATE_BOND",
            source_result_id=bond_result.result_id,
            instrument_id=instrument.instrument_id,
            bundle=bundle,
            policy=policy,
            reference_npv=bond_result.reference_npv,
            quantlib_npv=_measure(bond_result.measures, PricingMeasure.NPV),
            runner=runner,
        )


# --------------------------------------------------------------- 14.4 option


def black_scholes_merton_npv(
    *,
    option_type: OptionType,
    spot: Decimal,
    strike: Decimal,
    risk_free_rate: Decimal,
    dividend_yield: Decimal,
    volatility: Decimal,
    time_years: Decimal,
) -> Decimal:
    """QuantOS closed-form European BSM price per unit (independent)."""

    if time_years <= 0 or volatility <= 0:
        raise ValueError("BSM requires positive time and volatility")
    s, k, r, q, v, t = (float(x) for x in (spot, strike, risk_free_rate, dividend_yield, volatility, time_years))
    d1 = (math.log(s / k) + (r - q + 0.5 * v * v) * t) / (v * math.sqrt(t))
    d2 = d1 - v * math.sqrt(t)

    def cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    if option_type is OptionType.CALL:
        value = s * math.exp(-q * t) * cdf(d1) - k * math.exp(-r * t) * cdf(d2)
    else:
        value = k * math.exp(-r * t) * cdf(-d2) - s * math.exp(-q * t) * cdf(-d1)
    return Decimal(repr(value))


class OREEuropeanOptionDifferential:
    TRADE_ID = "FC-OPTION"

    def compare(
        self,
        *,
        pricing_result: PricingResult,
        request: PricingRequest,
        instrument: EuropeanOptionInstrument,
        market_snapshot: MarketDataSnapshot,
        model: PricingModelSpecification,
        policy: OREDifferentialPolicy,
        runner: OREEngineRunner | None = None,
    ) -> OREProductDifferentialResult:
        if pricing_result.result_id != pricing_result_identity(pricing_result):
            raise ValueError("pricing result identity mismatch")
        if (
            pricing_result.request_id != request.request_id
            or pricing_result.instrument_id != instrument.instrument_id
            or pricing_result.market_snapshot_id != market_snapshot.snapshot_id
            or pricing_result.model_spec_id != model.model_spec_id
        ):
            raise ValueError("pricing result is bound to other inputs")
        if model.model_family != "BLACK_SCHOLES_MERTON":
            raise ValueError("ORE option overlap requires the BSM model family")
        params = dict(model.parameters)
        if params.get("day_count") != "ACT_365_FIXED" or params.get("calendar") != "NULL_CALENDAR":
            raise ValueError("ORE option overlap requires ACT_365_FIXED and NULL_CALENDAR")
        valuation = market_snapshot.valuation_time.date()
        expiry = instrument.expiry.date()
        if expiry <= valuation:
            raise ValueError("option expiry must follow the valuation date")
        ccy = instrument.currency.value

        def quote(kind: MarketQuoteType, key: str) -> Decimal:
            matches = [
                q
                for q in market_snapshot.quotes
                if q.quote_type is kind and q.market_key == key and q.currency is instrument.currency
            ]
            if len(matches) != 1:
                raise ValueError(f"expected exactly one {kind.value} quote for {key}")
            return matches[0].value

        spot = quote(MarketQuoteType.EQUITY_SPOT, instrument.underlying_security_id)
        rate = quote(MarketQuoteType.ZERO_RATE, params["risk_free_rate_key"])
        dividend = quote(MarketQuoteType.ZERO_RATE, params["dividend_yield_key"])
        vol = quote(MarketQuoteType.VOLATILITY, params["volatility_key"])
        time_years = Decimal((expiry - valuation).days) / Decimal("365")
        reference = black_scholes_merton_npv(
            option_type=instrument.option_type,
            spot=spot,
            strike=instrument.strike,
            risk_free_rate=rate,
            dividend_yield=dividend,
            volatility=vol,
            time_years=time_years,
        ) * instrument.multiplier
        name = "FCEQ" + hashlib.sha256(instrument.underlying_security_id.encode()).hexdigest()[:10].upper()
        pillars = (expiry, expiry + timedelta(days=365))
        asof = valuation.strftime("%Y%m%d")
        market_lines = tuple(
            [f"{asof} ZERO/RATE/{ccy}/FCRF/A365F/{d.isoformat()} {_plain(rate)}" for d in pillars]
            + [f"{asof} EQUITY/PRICE/{name}/{ccy} {_plain(spot)}"]
            + [f"{asof} EQUITY_DIVIDEND/RATE/{name}/{ccy}/{d.isoformat()} {_plain(dividend)}" for d in pillars]
            + [f"{asof} EQUITY_OPTION/RATE_LNVOL/{name}/{ccy}/{expiry.isoformat()}/ATMF {_plain(vol)}"]
        )
        quotes = "".join(f"<Quote>ZERO/RATE/{ccy}/FCRF/A365F/{d.isoformat()}</Quote>" for d in pillars)
        dividend_quotes = "".join(
            f"<Quote>EQUITY_DIVIDEND/RATE/{name}/{ccy}/{d.isoformat()}</Quote>" for d in pillars
        )
        curve_config = (
            "<?xml version=\"1.0\"?>\n<CurveConfiguration>\n<YieldCurves><YieldCurve>"
            f"<CurveId>FCRF</CurveId><CurveDescription>flat risk-free</CurveDescription><Currency>{ccy}</Currency><DiscountCurve/>"
            f"<Segments><Direct><Type>Zero</Type><Quotes>{quotes}</Quotes><Conventions>FC-ZERO-CONTINUOUS-A365F</Conventions></Direct></Segments>"
            "<InterpolationVariable>Discount</InterpolationVariable><InterpolationMethod>LogLinear</InterpolationMethod>"
            "<YieldCurveDayCounter>A365F</YieldCurveDayCounter><Extrapolation>false</Extrapolation></YieldCurve></YieldCurves>\n"
            f"<EquityCurves><EquityCurve><CurveId>{name}</CurveId><CurveDescription>flat dividend yield</CurveDescription>"
            f"<Currency>{ccy}</Currency><ForecastingCurve>FCRF</ForecastingCurve><Type>DividendYield</Type>"
            f"<SpotQuote>EQUITY/PRICE/{name}/{ccy}</SpotQuote><Quotes>{dividend_quotes}</Quotes><DayCounter>A365F</DayCounter>"
            "<DividendInterpolation><InterpolationVariable>Zero</InterpolationVariable><InterpolationMethod>Linear</InterpolationMethod></DividendInterpolation>"
            "</EquityCurve></EquityCurves>\n"
            f"<EquityVolatilities><EquityVolatility><CurveId>{name}</CurveId><CurveDescription>constant vol</CurveDescription>"
            f"<Currency>{ccy}</Currency><Dimension>ATM</Dimension><Expiries>{expiry.isoformat()}</Expiries></EquityVolatility></EquityVolatilities>\n"
            "</CurveConfiguration>\n"
        )
        todays_market = (
            "<?xml version=\"1.0\"?>\n<TodaysMarket>\n"
            "  <Configuration id=\"default\"><DiscountingCurvesId>default</DiscountingCurvesId>"
            "<EquityCurvesId>default</EquityCurvesId><EquityVolatilitiesId>default</EquityVolatilitiesId></Configuration>\n"
            f"  <DiscountingCurves id=\"default\"><DiscountingCurve currency=\"{ccy}\">Yield/{ccy}/FCRF</DiscountingCurve></DiscountingCurves>\n"
            f"  <EquityCurves id=\"default\"><EquityCurve name=\"{name}\">Equity/{ccy}/{name}</EquityCurve></EquityCurves>\n"
            f"  <EquityVolatilities id=\"default\"><EquityVolatility name=\"{name}\">EquityVolatility/{ccy}/{name}</EquityVolatility></EquityVolatilities>\n"
            "</TodaysMarket>\n"
        )
        portfolio = (
            "<?xml version=\"1.0\"?>\n<Portfolio>\n"
            f"  <Trade id=\"{self.TRADE_ID}\"><TradeType>EquityOption</TradeType>"
            "<Envelope><CounterParty>FC-RESEARCH</CounterParty><NettingSetId>FC-RESEARCH</NettingSetId><AdditionalFields/></Envelope>"
            "<EquityOptionData><OptionData><LongShort>Long</LongShort>"
            f"<OptionType>{'Call' if instrument.option_type is OptionType.CALL else 'Put'}</OptionType>"
            "<Style>European</Style><Settlement>Cash</Settlement><PayOffAtExpiry>false</PayOffAtExpiry>"
            f"<ExerciseDates><ExerciseDate>{expiry.isoformat()}</ExerciseDate></ExerciseDates></OptionData>"
            f"<Name>{name}</Name><Currency>{ccy}</Currency><Strike>{_plain(instrument.strike)}</Strike>"
            f"<Quantity>{_plain(instrument.multiplier)}</Quantity></EquityOptionData></Trade>\n</Portfolio>\n"
        )
        bundle = with_bundle_identity(
            OREInputBundle(
                bundle_id="",
                asof=valuation,
                conventions_xml=f"<?xml version=\"1.0\"?>\n<Conventions>\n{_ZERO_CONVENTIONS}</Conventions>\n",
                curve_config_xml=curve_config,
                todays_market_xml=todays_market,
                pricing_engine_xml=PRICING_ENGINES_XML,
                portfolio_xml=portfolio,
                market_lines=market_lines,
                trade_id=self.TRADE_ID,
                base_currency=ccy,
            )
        )
        return _product_result(
            product="EUROPEAN_EQUITY_OPTION",
            source_result_id=pricing_result.result_id,
            instrument_id=instrument.instrument_id,
            bundle=bundle,
            policy=policy,
            reference_npv=reference,
            quantlib_npv=_measure(pricing_result.measures, PricingMeasure.NPV),
            runner=runner,
        )


# ---------------------------------------------- 14.5 / 14.6 swap risk analytics


class ORESwapRiskAnalytics:
    """ORE SENSITIVITY and STRESS analytics on the Stage 14.1 swap overlap."""

    def sensitivity(
        self,
        *,
        instrument: FixedFloatSwapInstrument,
        base_snapshot: MarketDataSnapshot,
        discount_curve_policy: CurveConstructionPolicy,
        forwarding_curve_policy: CurveConstructionPolicy,
        bucket_revaluations: tuple[ScenarioRevaluationResult, ...],
        bucket_scenarios: tuple[RiskScenario, ...],
        policy: OREDifferentialPolicy,
        runner: OREEngineRunner | None = None,
    ) -> ORESensitivityDifferentialResult:
        discount, forwarding = _base_curves(base_snapshot, discount_curve_policy, forwarding_curve_policy)
        tenors = _aligned_tenors(discount, forwarding)
        index = _index_name(instrument)
        factors = {}
        for tenor_index, tenor in enumerate(tenors):
            factors[(discount_curve_policy.curve_key, tenor)] = (
                f"DiscountCurve/{instrument.currency.value}/{tenor_index}/{tenor}"
            )
            factors[(forwarding_curve_policy.curve_key, tenor)] = (
                f"IndexCurve/{index}/{tenor_index}/{tenor}"
            )
        scenarios = {s.scenario_id: s for s in bucket_scenarios}
        if len(bucket_revaluations) != len(factors):
            raise ValueError("bucket revaluations must cover every curve pillar exactly once")
        mapped: dict[str, tuple[ScenarioRevaluationResult, RiskScenario]] = {}
        for revaluation in bucket_revaluations:
            scenario = scenarios.get(revaluation.scenario_id)
            if scenario is None:
                raise ValueError("bucket revaluation has no matching scenario")
            _verify_revaluation(
                revaluation, instrument, base_snapshot, scenario,
                discount_curve_policy, forwarding_curve_policy, (discount, forwarding),
            )
            if len(scenario.shocks) != 1:
                raise ValueError("a bucket scenario must shock exactly one pillar")
            shock = scenario.shocks[0]
            if (
                shock.quote_type is not MarketQuoteType.ZERO_RATE
                or shock.shock_kind is not ShockKind.ABSOLUTE
                or shock.shock_value != BUCKET_SHIFT
            ):
                raise ValueError("bucket shocks must be absolute +1bp zero-rate shocks")
            factor = factors.get((shock.market_key, shock.tenor))
            if factor is None or factor in mapped:
                raise ValueError("bucket shock does not map to a unique curve pillar")
            mapped[factor] = (revaluation, scenario)
        grid = ",".join(tenors)
        ccy = instrument.currency.value
        sim = _simulation_xml(ccy, grid, index)
        scenario_xml = (
            "<?xml version=\"1.0\"?>\n<SensitivityAnalysis>\n"
            f"  <DiscountCurves><DiscountCurve ccy=\"{ccy}\"><ShiftType>Absolute</ShiftType>"
            f"<ShiftSize>{_plain(BUCKET_SHIFT)}</ShiftSize><ShiftScheme>Forward</ShiftScheme>"
            f"<ShiftTenors>{grid}</ShiftTenors></DiscountCurve></DiscountCurves>\n"
            f"  <IndexCurves><IndexCurve index=\"{index}\"><ShiftType>Absolute</ShiftType>"
            f"<ShiftSize>{_plain(BUCKET_SHIFT)}</ShiftSize><ShiftScheme>Forward</ShiftScheme>"
            f"<ShiftTenors>{grid}</ShiftTenors></IndexCurve></IndexCurves>\n"
            "  <ComputeGamma>false</ComputeGamma>\n</SensitivityAnalysis>\n"
        )
        bundle = _swap_bundle_with(
            instrument, discount, forwarding,
            analytics=("NPV", "SENSITIVITY"),
            sensitivity_sim_xml=sim,
            sensitivity_scenario_xml=scenario_xml,
        )
        runner = runner or OREEngineRunner()
        report = runner.reports(bundle, ("sensitivity",))["sensitivity"]
        headers = report["headers"]
        ore = {
            row[headers.index("Factor_1")]: Decimal(row[headers.index("Delta")])
            for row in report["rows"]
            if row[headers.index("Factor_2")] == ""
        }
        buckets = []
        for factor, (revaluation, scenario) in sorted(mapped.items()):
            if factor not in ore:
                raise ValueError(f"ORE did not report factor {factor}")
            difference = abs(ore[factor] - revaluation.scenario_pnl)
            buckets.append(
                OREBucketDelta(
                    factor=factor,
                    scenario_id=scenario.scenario_id,
                    revaluation_id=revaluation.revaluation_id,
                    reference_pnl=revaluation.scenario_pnl,
                    ore_delta=ore[factor],
                    absolute_difference=difference,
                )
            )
        worst = max(b.absolute_difference for b in buckets)
        matched = worst <= policy.maximum_absolute_npv_difference
        result = ORESensitivityDifferentialResult(
            result_id="",
            instrument_id=instrument.instrument_id,
            input_bundle_id=bundle.bundle_id,
            policy_id=policy.policy_id,
            ore_version=runner.ore_version,
            buckets=tuple(buckets),
            maximum_absolute_difference=worst,
            state=OREDifferentialState.MATCH if matched else OREDifferentialState.MISMATCH,
            trust_authority="REFERENCE_MATCH_ONLY" if matched else "NONE",
            order_authority="NONE",
            capital_authority="NONE",
        )
        return replace(result, result_id=_content_id("ore-sensitivity-differential", _payload(result)))

    def stress(
        self,
        *,
        revaluation: ScenarioRevaluationResult,
        instrument: FixedFloatSwapInstrument,
        base_snapshot: MarketDataSnapshot,
        scenario: RiskScenario,
        discount_curve_policy: CurveConstructionPolicy,
        forwarding_curve_policy: CurveConstructionPolicy,
        policy: OREDifferentialPolicy,
        runner: OREEngineRunner | None = None,
    ) -> OREStressDifferentialResult:
        discount, forwarding = _base_curves(base_snapshot, discount_curve_policy, forwarding_curve_policy)
        _verify_revaluation(
            revaluation, instrument, base_snapshot, scenario,
            discount_curve_policy, forwarding_curve_policy, (discount, forwarding),
        )
        tenors = _aligned_tenors(discount, forwarding)
        shifts = {
            discount_curve_policy.curve_key: {t: Decimal("0") for t in tenors},
            forwarding_curve_policy.curve_key: {t: Decimal("0") for t in tenors},
        }
        for shock in scenario.shocks:
            if shock.quote_type is not MarketQuoteType.ZERO_RATE or shock.shock_kind is not ShockKind.ABSOLUTE:
                raise ValueError("ORE stress overlap maps absolute zero-rate shocks only")
            if shock.market_key not in shifts or shock.tenor not in tenors:
                raise ValueError("stress shock targets a quote outside the swap curves")
            shifts[shock.market_key][shock.tenor] += shock.shock_value
        grid = ",".join(tenors)
        ccy = instrument.currency.value
        index = _index_name(instrument)

        def listing(key: str) -> str:
            return ",".join(_plain(shifts[key][t]) for t in tenors)

        label = "FC-" + scenario.scenario_id.split(":")[-1][:16]
        stress_xml = (
            "<?xml version=\"1.0\"?>\n<StressTesting>\n"
            f"  <StressTest id=\"{label}\">\n"
            f"    <DiscountCurves><DiscountCurve ccy=\"{ccy}\"><ShiftType>Absolute</ShiftType>"
            f"<Shifts>{listing(discount_curve_policy.curve_key)}</Shifts><ShiftTenors>{grid}</ShiftTenors></DiscountCurve></DiscountCurves>\n"
            f"    <IndexCurves><IndexCurve index=\"{index}\"><ShiftType>Absolute</ShiftType>"
            f"<Shifts>{listing(forwarding_curve_policy.curve_key)}</Shifts><ShiftTenors>{grid}</ShiftTenors></IndexCurve></IndexCurves>\n"
            "  </StressTest>\n</StressTesting>\n"
        )
        bundle = _swap_bundle_with(
            instrument, discount, forwarding,
            analytics=("NPV", "STRESS"),
            stress_sim_xml=_simulation_xml(ccy, grid, index),
            stress_scenario_xml=stress_xml,
        )
        runner = runner or OREEngineRunner()
        report = runner.reports(bundle, ("stress",))["stress"]
        headers = report["headers"]
        rows = [r for r in report["rows"] if r[headers.index("ScenarioLabel")] == label]
        if len(rows) != 1:
            raise ValueError("ORE stress report lacks the scenario row")
        ore_pnl = Decimal(rows[0][headers.index("Sensitivity")])
        difference = abs(ore_pnl - revaluation.scenario_pnl)
        matched = difference <= policy.maximum_absolute_npv_difference
        result = OREStressDifferentialResult(
            result_id="",
            revaluation_id=revaluation.revaluation_id,
            scenario_id=scenario.scenario_id,
            instrument_id=instrument.instrument_id,
            input_bundle_id=bundle.bundle_id,
            policy_id=policy.policy_id,
            ore_version=runner.ore_version,
            reference_pnl=revaluation.scenario_pnl,
            ore_pnl=ore_pnl,
            absolute_difference=difference,
            state=OREDifferentialState.MATCH if matched else OREDifferentialState.MISMATCH,
            trust_authority="REFERENCE_MATCH_ONLY" if matched else "NONE",
            order_authority="NONE",
            capital_authority="NONE",
        )
        return replace(result, result_id=_content_id("ore-stress-differential", _payload(result)))


# ------------------------------------------------------------------ helpers


def ore_product_result_identity(result: OREProductDifferentialResult) -> str:
    return _content_id("ore-product-differential", _payload(result))


def _product_result(
    *,
    product: str,
    source_result_id: str,
    instrument_id: str,
    bundle: OREInputBundle,
    policy: OREDifferentialPolicy,
    reference_npv: Decimal,
    quantlib_npv: Decimal,
    runner: OREEngineRunner | None,
) -> OREProductDifferentialResult:
    runner = runner or OREEngineRunner()
    ore_npv = runner.npv(bundle)
    vs_reference = abs(ore_npv - reference_npv)
    vs_quantlib = abs(ore_npv - quantlib_npv)
    matched = max(vs_reference, vs_quantlib) <= policy.maximum_absolute_npv_difference
    result = OREProductDifferentialResult(
        result_id="",
        product=product,
        source_result_id=source_result_id,
        instrument_id=instrument_id,
        input_bundle_id=bundle.bundle_id,
        policy_id=policy.policy_id,
        ore_version=runner.ore_version,
        adapter_version=PRODUCTS_ADAPTER_VERSION,
        ore_npv=ore_npv,
        reference_npv=reference_npv,
        quantlib_npv=quantlib_npv,
        absolute_difference_vs_reference=vs_reference,
        absolute_difference_vs_quantlib=vs_quantlib,
        state=OREDifferentialState.MATCH if matched else OREDifferentialState.MISMATCH,
        trust_authority="REFERENCE_MATCH_ONLY" if matched else "NONE",
        order_authority="NONE",
        capital_authority="NONE",
    )
    return replace(result, result_id=ore_product_result_identity(result))


def _measure(measures, kind: PricingMeasure) -> Decimal:
    for item in measures:
        if item.measure is kind:
            return item.value
    raise ValueError(f"result does not carry {kind.value}")


def _require_curve(curve: DiscountCurveArtifact, ccy: str, maturity: date) -> None:
    if curve.curve_id != discount_curve_identity(curve):
        raise ValueError("discount curve artifact identity mismatch")
    if curve.currency.value != ccy:
        raise ValueError("ORE curve currency differs from the instrument")
    if curve.allow_extrapolation:
        raise ValueError("ORE differential refuses extrapolating curves")
    if curve.interpolation != "LOG_LINEAR_DISCOUNT":
        raise ValueError("ORE mapping requires log-linear discount curves")
    if maturity > curve.pillars[-1].pillar_date:
        raise ValueError("instrument maturity exceeds the curve's last pillar")


def _regular_dates(start: date, end: date, months: int) -> tuple[date, ...]:
    from .bond_pricing import _add_months

    dates = [start]
    index = 1
    while dates[-1] < end:
        dates.append(_add_months(start, index * months))
        index += 1
    if dates[-1] != end:
        raise ValueError("ORE overlap requires a regular no-stub schedule")
    return tuple(dates)


def _schedule_xml(start: date, end: date, months: int) -> str:
    return (
        "<ScheduleData><Rules>"
        f"<StartDate>{start.isoformat()}</StartDate><EndDate>{end.isoformat()}</EndDate>"
        f"<Tenor>{months}M</Tenor><Calendar>NullCalendar</Calendar>"
        "<Convention>Unadjusted</Convention><TermConvention>Unadjusted</TermConvention>"
        "<Rule>Forward</Rule><EndOfMonth>false</EndOfMonth><FirstDate/><LastDate/>"
        "</Rules></ScheduleData>"
    )


def _curve_config(curves, ccy: str) -> str:
    body = "".join(OREInputBuilder._curve_xml(curve_id, ccy, curve) for curve_id, curve in curves)
    return f"<?xml version=\"1.0\"?>\n<CurveConfiguration><YieldCurves>\n{body}</YieldCurves></CurveConfiguration>\n"


def _curve_market_lines(curves, ccy: str) -> tuple[str, ...]:
    return tuple(
        f"{curve.valuation_date.strftime('%Y%m%d')} "
        f"ZERO/RATE/{ccy}/{curve_id}/A365F/{pillar.pillar_date.isoformat()} {_plain(pillar.zero_rate)}"
        for curve_id, curve in curves
        for pillar in curve.pillars
    )


def _base_curves(snapshot, discount_policy, forwarding_policy):
    builder = DiscountCurveBuilder()
    return (
        builder.build(snapshot=snapshot, policy=discount_policy),
        builder.build(snapshot=snapshot, policy=forwarding_policy),
    )


def _aligned_tenors(discount: DiscountCurveArtifact, forwarding: DiscountCurveArtifact) -> tuple[str, ...]:
    tenors = tuple(p.tenor for p in discount.pillars)
    if tenors != tuple(p.tenor for p in forwarding.pillars):
        raise ValueError(
            "ORE simulation grid needs identical pillar tenors on both curves"
        )
    for curve in (discount, forwarding):
        for pillar in curve.pillars:
            if _tenor_date(curve.valuation_date, pillar.tenor) != pillar.pillar_date:
                raise ValueError("curve pillar dates are not tenor-aligned")
    return tenors


def _index_name(instrument: FixedFloatSwapInstrument) -> str:
    return f"{instrument.currency.value}-FCREF-{instrument.floating_leg_frequency_months}M"


def _simulation_xml(ccy: str, grid: str, index: str) -> str:
    return (
        "<?xml version=\"1.0\"?>\n<Simulation><Market>"
        f"<BaseCurrency>{ccy}</BaseCurrency><Currencies><Currency>{ccy}</Currency></Currencies>"
        f"<YieldCurves><Configuration><Tenors>{grid}</Tenors><Interpolation>LogLinear</Interpolation>"
        "<Extrapolation>Y</Extrapolation></Configuration></YieldCurves>"
        f"<Indices><Index>{index}</Index></Indices>"
        "<DefaultCurves><Names/><Tenors>1Y</Tenors></DefaultCurves>"
        "</Market></Simulation>\n"
    )


def _swap_bundle_with(instrument, discount, forwarding, **extensions) -> OREInputBundle:
    base = OREInputBuilder().swap_bundle(
        instrument=instrument,
        discount_curve=discount,
        forwarding_curve=forwarding,
    )
    return with_bundle_identity(replace(base, **extensions))


def _verify_revaluation(
    revaluation: ScenarioRevaluationResult,
    instrument: FixedFloatSwapInstrument,
    base_snapshot: MarketDataSnapshot,
    scenario: RiskScenario,
    discount_policy: CurveConstructionPolicy,
    forwarding_policy: CurveConstructionPolicy,
    base_curves: tuple[DiscountCurveArtifact, DiscountCurveArtifact],
) -> None:
    if revaluation.revaluation_id != scenario_revaluation_identity(revaluation):
        raise ValueError("scenario revaluation identity mismatch")
    if revaluation.instrument_id != instrument.instrument_id:
        raise ValueError("scenario revaluation belongs to another instrument")
    if revaluation.scenario_id != scenario.scenario_id:
        raise ValueError("scenario revaluation belongs to another scenario")
    if revaluation.base_snapshot_id != base_snapshot.snapshot_id:
        raise ValueError("scenario revaluation used another base snapshot")
    if tuple(c.curve_id for c in base_curves) != revaluation.base_curve_ids:
        raise ValueError("rebuilt base curves differ from the revaluation")
    shocked, _ = ScenarioMarketTransformer().apply(base_snapshot=base_snapshot, scenario=scenario)
    if shocked.snapshot_id != revaluation.shocked_snapshot_id:
        raise ValueError("shocked market state does not reproduce")
    rebuilt = _base_curves(shocked, discount_policy, forwarding_policy)
    if tuple(c.curve_id for c in rebuilt) != revaluation.shocked_curve_ids:
        raise ValueError("rebuilt shocked curves differ from the revaluation")


def _payload(item: object) -> dict[str, object]:
    def encode(value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, tuple):
            return [encode(v) for v in value]
        if hasattr(value, "__dataclass_fields__"):
            return {k: encode(getattr(value, k)) for k in value.__dataclass_fields__}
        return value

    return {
        name: encode(getattr(item, name))
        for name in item.__dataclass_fields__
        if name != "result_id"
    }


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
