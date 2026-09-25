"""Stage 14.1 — OpenSourceRisk/Engine (ORE) differential adapter.

ORE sits behind QuantOS contracts. QuantOS writes ORE's inputs
(conventions, curve configuration, today's-market mapping, pricing engines,
portfolio XML and market-data lines) from frozen QuantOS artifacts,
content-addresses that exact input bundle, runs ORE in-process, and compares
ORE's NPV with the Stage 11.5 independent reference that already agreed with
QuantLib. ORE never becomes the source of truth.

Frozen overlap (anything else fails closed before ORE runs):

* future-starting vanilla fixed/float swap, as in Stage 11.5;
* both curves are QuantOS zero-rate artifacts (ACT/365F, continuous,
  log-linear discount interpolation) with extrapolation disabled, mapped to
  ORE Direct zero segments with date-based quotes;
* the floating index is a convention-defined ORE Ibor index with zero
  settlement days, NullCalendar, Unadjusted, no end-of-month rule and the
  swap's floating day count, so ORE projects the simple forward over the
  exact accrual period like the reference does;
* NullCalendar, Unadjusted, Forward regular schedules, zero fixing days.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from xml.sax.saxutils import escape

import duckdb

from .interest_rate_curves import (
    CurveConstructionPolicy,
    DiscountCurveArtifact,
    DiscountCurveBuilder,
    discount_curve_identity,
)
from .pricing_risk_contracts import (
    DayCountConvention,
    FixedFloatSwapInstrument,
    MarketDataSnapshot,
    PayReceive,
    PricingMeasure,
    RiskScenario,
)
from .scenario_revaluation import (
    ScenarioMarketTransformer,
    ScenarioRevaluationResult,
    scenario_revaluation_identity,
)
from .swap_pricing import SwapPricingResult, swap_pricing_result_identity

ORE_DISTRIBUTION = "open-source-risk-engine"
ORE_PINNED_VERSION = "1.8.17.0"
ORE_ADAPTER_VERSION = "14.1"
ORE_TRADE_ID = "FC-SWAP"

# Serializes ORE worker launches; each run is an isolated child process.
ORE_LOCK = threading.RLock()

_ORE_DAY_COUNTS = {
    DayCountConvention.ACT_360: "A360",
    DayCountConvention.ACT_365_FIXED: "A365F",
}


class OREDifferentialState(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


def ore_is_available() -> bool:
    try:
        installed = package_version(ORE_DISTRIBUTION)
    except PackageNotFoundError:
        return False
    return installed == ORE_PINNED_VERSION


@dataclass(frozen=True)
class OREDifferentialPolicy:
    maximum_absolute_npv_difference: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        value = self.maximum_absolute_npv_difference
        if not value.is_finite() or value < 0:
            raise ValueError("ORE NPV tolerance must be finite and non-negative")
        if not self.rationale.strip():
            raise ValueError("ORE differential policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("ORE differential policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "ore-differential-policy",
            {
                "maximum_absolute_npv_difference": str(
                    self.maximum_absolute_npv_difference
                ),
                "rationale": self.rationale,
                "evidence_references": sorted(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class OREInputBundle:
    """The exact inputs handed to ORE, content-addressed for lineage."""

    bundle_id: str
    asof: date
    conventions_xml: str
    curve_config_xml: str
    todays_market_xml: str
    pricing_engine_xml: str
    portfolio_xml: str
    market_lines: tuple[str, ...]
    # Stage 14.3+ fields; defaults are omitted from identity so Stage 14.1
    # swap bundle IDs are unchanged.
    trade_id: str = ORE_TRADE_ID
    base_currency: str = ""
    analytics: tuple[str, ...] = ("NPV",)
    sensitivity_sim_xml: str = ""
    sensitivity_scenario_xml: str = ""
    stress_sim_xml: str = ""
    stress_scenario_xml: str = ""


_BUNDLE_EXTENSION_DEFAULTS = {
    "trade_id": ORE_TRADE_ID,
    "base_currency": "",
    "analytics": ("NPV",),
    "sensitivity_sim_xml": "",
    "sensitivity_scenario_xml": "",
    "stress_sim_xml": "",
    "stress_scenario_xml": "",
}


@dataclass(frozen=True)
class OREDifferentialResult:
    result_id: str
    swap_pricing_result_id: str
    instrument_id: str
    discount_curve_id: str
    forwarding_curve_id: str
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
    diagnostics: tuple[str, ...]
    trust_authority: str
    order_authority: str
    capital_authority: str


class OREInputBuilder:
    """Pure-Python mapping from QuantOS artifacts to ORE inputs."""

    def swap_bundle(
        self,
        *,
        instrument: FixedFloatSwapInstrument,
        discount_curve: DiscountCurveArtifact,
        forwarding_curve: DiscountCurveArtifact,
    ) -> OREInputBundle:
        self._validate(
            instrument=instrument,
            discount_curve=discount_curve,
            forwarding_curve=forwarding_curve,
        )
        ccy = instrument.currency.value
        asof = discount_curve.valuation_date
        index = (
            f"{ccy}-FCREF-{instrument.floating_leg_frequency_months}M"
        )
        curves = (
            ("FCDISC", discount_curve),
            ("FCFWD", forwarding_curve),
        )
        conventions = (
            "<?xml version=\"1.0\"?>\n<Conventions>\n"
            "  <Zero>\n"
            "    <Id>FC-ZERO-CONTINUOUS-A365F</Id>\n"
            "    <TenorBased>false</TenorBased>\n"
            "    <DayCounter>A365F</DayCounter>\n"
            "    <Compounding>Continuous</Compounding>\n"
            "  </Zero>\n"
            "  <IborIndex>\n"
            f"    <Id>{index}</Id>\n"
            "    <FixingCalendar>NullCalendar</FixingCalendar>\n"
            f"    <DayCounter>{_ORE_DAY_COUNTS[instrument.floating_leg_day_count]}</DayCounter>\n"
            "    <SettlementDays>0</SettlementDays>\n"
            "    <BusinessDayConvention>Unadjusted</BusinessDayConvention>\n"
            "    <EndOfMonth>false</EndOfMonth>\n"
            "  </IborIndex>\n"
            "</Conventions>\n"
        )
        curve_xml = "".join(
            self._curve_xml(curve_id, ccy, curve) for curve_id, curve in curves
        )
        curve_config = (
            "<?xml version=\"1.0\"?>\n<CurveConfiguration><YieldCurves>\n"
            f"{curve_xml}</YieldCurves></CurveConfiguration>\n"
        )
        todays_market = (
            "<?xml version=\"1.0\"?>\n<TodaysMarket>\n"
            "  <Configuration id=\"default\">\n"
            "    <DiscountingCurvesId>default</DiscountingCurvesId>\n"
            "    <IndexForwardingCurvesId>default</IndexForwardingCurvesId>\n"
            "  </Configuration>\n"
            "  <DiscountingCurves id=\"default\">\n"
            f"    <DiscountingCurve currency=\"{ccy}\">Yield/{ccy}/FCDISC</DiscountingCurve>\n"
            "  </DiscountingCurves>\n"
            "  <IndexForwardingCurves id=\"default\">\n"
            f"    <Index name=\"{index}\">Yield/{ccy}/FCFWD</Index>\n"
            "  </IndexForwardingCurves>\n"
            "</TodaysMarket>\n"
        )
        pricing_engine = (
            "<?xml version=\"1.0\"?>\n<PricingEngines>\n"
            "  <Product type=\"Swap\">\n"
            "    <Model>DiscountedCashflows</Model><ModelParameters/>\n"
            "    <Engine>DiscountingSwapEngine</Engine><EngineParameters/>\n"
            "  </Product>\n"
            "</PricingEngines>\n"
        )
        fixed_payer = instrument.fixed_leg_direction is PayReceive.PAY
        fixed_leg = self._leg_xml(
            kind="Fixed",
            payer=fixed_payer,
            instrument=instrument,
            day_count=instrument.fixed_leg_day_count,
            months=instrument.fixed_leg_frequency_months,
            leg_data=(
                "<FixedLegData><Rates>"
                f"<Rate>{_plain(instrument.fixed_rate)}</Rate>"
                "</Rates></FixedLegData>"
            ),
        )
        floating_leg = self._leg_xml(
            kind="Floating",
            payer=not fixed_payer,
            instrument=instrument,
            day_count=instrument.floating_leg_day_count,
            months=instrument.floating_leg_frequency_months,
            leg_data=(
                "<FloatingLegData>"
                f"<Index>{index}</Index>"
                f"<Spreads><Spread>{_plain(instrument.floating_spread)}</Spread></Spreads>"
                "<IsInArrears>false</IsInArrears>"
                "<FixingDays>0</FixingDays>"
                "</FloatingLegData>"
            ),
        )
        portfolio = (
            "<?xml version=\"1.0\"?>\n<Portfolio>\n"
            f"  <Trade id=\"{ORE_TRADE_ID}\">\n"
            "    <TradeType>Swap</TradeType>\n"
            "    <Envelope><CounterParty>FC-RESEARCH</CounterParty>"
            "<NettingSetId>FC-RESEARCH</NettingSetId><AdditionalFields/></Envelope>\n"
            f"    <SwapData>\n{fixed_leg}{floating_leg}    </SwapData>\n"
            "  </Trade>\n</Portfolio>\n"
        )
        market_lines = tuple(
            f"{asof.strftime('%Y%m%d')} "
            f"ZERO/RATE/{ccy}/{curve_id}/A365F/{pillar.pillar_date.isoformat()} "
            f"{_plain(pillar.zero_rate)}"
            for curve_id, curve in curves
            for pillar in curve.pillars
        )
        payload = {
            "asof": asof.isoformat(),
            "conventions_xml": conventions,
            "curve_config_xml": curve_config,
            "todays_market_xml": todays_market,
            "pricing_engine_xml": pricing_engine,
            "portfolio_xml": portfolio,
            "market_lines": list(market_lines),
        }
        return OREInputBundle(
            bundle_id=_content_id("ore-input-bundle", payload),
            asof=asof,
            conventions_xml=conventions,
            curve_config_xml=curve_config,
            todays_market_xml=todays_market,
            pricing_engine_xml=pricing_engine,
            portfolio_xml=portfolio,
            market_lines=market_lines,
        )

    @staticmethod
    def _validate(
        *,
        instrument: FixedFloatSwapInstrument,
        discount_curve: DiscountCurveArtifact,
        forwarding_curve: DiscountCurveArtifact,
    ) -> None:
        for curve in (discount_curve, forwarding_curve):
            if curve.curve_id != discount_curve_identity(curve):
                raise ValueError("discount curve artifact identity mismatch")
            if curve.currency is not instrument.currency:
                raise ValueError("ORE curve currency differs from the swap")
            if curve.allow_extrapolation:
                raise ValueError(
                    "ORE differential refuses extrapolating curves; "
                    "extrapolation semantics are not frozen across engines"
                )
            if curve.interpolation != "LOG_LINEAR_DISCOUNT":
                raise ValueError("ORE mapping requires log-linear discount curves")
            if instrument.maturity_date > curve.pillars[-1].pillar_date:
                raise ValueError("swap maturity exceeds a curve's last pillar")
        if discount_curve.valuation_date != forwarding_curve.valuation_date:
            raise ValueError("ORE curves must share one valuation date")
        if instrument.effective_date <= discount_curve.valuation_date:
            raise ValueError(
                "ORE differential supports future-starting swaps only"
            )
        for convention in (
            instrument.fixed_leg_day_count,
            instrument.floating_leg_day_count,
        ):
            if convention not in _ORE_DAY_COUNTS:
                raise ValueError(
                    f"{convention.value} has no frozen ORE mapping"
                )

    @staticmethod
    def _curve_xml(curve_id: str, ccy: str, curve: DiscountCurveArtifact) -> str:
        quotes = "".join(
            f"<Quote>ZERO/RATE/{ccy}/{curve_id}/A365F/{pillar.pillar_date.isoformat()}</Quote>"
            for pillar in curve.pillars
        )
        return (
            "  <YieldCurve>\n"
            f"    <CurveId>{curve_id}</CurveId>\n"
            f"    <CurveDescription>{escape(curve.curve_id)}</CurveDescription>\n"
            f"    <Currency>{ccy}</Currency>\n"
            "    <DiscountCurve/>\n"
            "    <Segments><Direct><Type>Zero</Type>"
            f"<Quotes>{quotes}</Quotes>"
            "<Conventions>FC-ZERO-CONTINUOUS-A365F</Conventions></Direct></Segments>\n"
            "    <InterpolationVariable>Discount</InterpolationVariable>\n"
            "    <InterpolationMethod>LogLinear</InterpolationMethod>\n"
            "    <YieldCurveDayCounter>A365F</YieldCurveDayCounter>\n"
            "    <Extrapolation>false</Extrapolation>\n"
            "  </YieldCurve>\n"
        )

    @staticmethod
    def _leg_xml(
        *,
        kind: str,
        payer: bool,
        instrument: FixedFloatSwapInstrument,
        day_count: DayCountConvention,
        months: int,
        leg_data: str,
    ) -> str:
        return (
            "      <LegData>\n"
            f"        <LegType>{kind}</LegType>\n"
            f"        <Payer>{'true' if payer else 'false'}</Payer>\n"
            f"        <Currency>{instrument.currency.value}</Currency>\n"
            f"        <Notionals><Notional>{_plain(instrument.notional)}</Notional></Notionals>\n"
            f"        <DayCounter>{_ORE_DAY_COUNTS[day_count]}</DayCounter>\n"
            "        <PaymentConvention>Unadjusted</PaymentConvention>\n"
            f"        {leg_data}\n"
            "        <ScheduleData><Rules>"
            f"<StartDate>{instrument.effective_date.isoformat()}</StartDate>"
            f"<EndDate>{instrument.maturity_date.isoformat()}</EndDate>"
            f"<Tenor>{months}M</Tenor>"
            "<Calendar>NullCalendar</Calendar>"
            "<Convention>Unadjusted</Convention>"
            "<TermConvention>Unadjusted</TermConvention>"
            "<Rule>Forward</Rule>"
            "<EndOfMonth>false</EndOfMonth>"
            "<FirstDate/><LastDate/>"
            "</Rules></ScheduleData>\n"
            "      </LegData>\n"
        )


class OREEngineRunner:
    """Runs ORE on one content-addressed input bundle in a child process.

    See ``quantos.ore_worker`` for why ORE never shares a process with the
    QuantLib Python package.
    """

    TIMEOUT_SECONDS = 300

    def __init__(self) -> None:
        if not ore_is_available():
            raise RuntimeError(
                f"ORE differential requires {ORE_DISTRIBUTION}=="
                f"{ORE_PINNED_VERSION} (install the 'ore' extra)"
            )
        self.ore_version = ORE_PINNED_VERSION

    def reports(
        self,
        bundle: OREInputBundle,
        names: tuple[str, ...],
    ) -> dict[str, dict[str, list]]:
        if bundle.bundle_id != ore_input_bundle_identity(bundle):
            raise ValueError("ORE input bundle identity mismatch")
        request = {
            "asof": bundle.asof.isoformat(),
            "base_currency": _bundle_currency(bundle),
            "conventions_xml": bundle.conventions_xml,
            "curve_config_xml": bundle.curve_config_xml,
            "todays_market_xml": bundle.todays_market_xml,
            "pricing_engine_xml": bundle.pricing_engine_xml,
            "portfolio_xml": bundle.portfolio_xml,
            "market_lines": list(bundle.market_lines),
            "analytics": list(bundle.analytics),
            "reports": list(names),
            "sensitivity_sim_xml": bundle.sensitivity_sim_xml,
            "sensitivity_scenario_xml": bundle.sensitivity_scenario_xml,
            "stress_sim_xml": bundle.stress_sim_xml,
            "stress_scenario_xml": bundle.stress_scenario_xml,
        }
        from .observability import span

        with span("ore", "worker", bundle_id=bundle.bundle_id, analytics=",".join(bundle.analytics)):
            with ORE_LOCK:
                completed = subprocess.run(
                    [sys.executable, "-m", "quantos.ore_worker"],
                    input=json.dumps(request),
                    capture_output=True,
                    text=True,
                    timeout=self.TIMEOUT_SECONDS,
                    check=False,
                )
            if completed.returncode != 0:
                raise ValueError(
                    "ORE worker failed with exit code "
                    f"{completed.returncode}: {completed.stderr[-2000:]}"
                )
            response = json.loads(completed.stdout)
            if response.get("ore_version") != self.ore_version:
                raise ValueError("ORE worker ran another ORE version")
            if response.get("errors"):
                raise ValueError(f"ORE reported errors: {response['errors']}")
            return response["reports"]

    def npv(self, bundle: OREInputBundle) -> Decimal:
        report = self.reports(bundle, ("npv",))["npv"]
        headers = report["headers"]
        rows = report["rows"]
        trade_ids = [row[headers.index("TradeId")] for row in rows]
        if trade_ids != [bundle.trade_id]:
            raise ValueError(f"ORE NPV report has unexpected trades {trade_ids}")
        if rows[0][headers.index("NpvCurrency")] != _bundle_currency(bundle):
            raise ValueError("ORE reported NPV in another currency")
        return Decimal(rows[0][headers.index("NPV")])


class OREFixedFloatSwapDifferential:
    def compare(
        self,
        *,
        swap_result: SwapPricingResult,
        instrument: FixedFloatSwapInstrument,
        discount_curve: DiscountCurveArtifact,
        forwarding_curve: DiscountCurveArtifact,
        policy: OREDifferentialPolicy,
        runner: OREEngineRunner | None = None,
    ) -> OREDifferentialResult:
        if swap_result.result_id != swap_pricing_result_identity(swap_result):
            raise ValueError("swap pricing result identity mismatch")
        if not swap_result.reference_verified:
            raise ValueError("swap reference was not verified against QuantLib")
        if swap_result.instrument_id != instrument.instrument_id:
            raise ValueError("swap result belongs to another instrument")
        if (
            swap_result.discount_curve_id != discount_curve.curve_id
            or swap_result.forwarding_curve_id != forwarding_curve.curve_id
        ):
            raise ValueError("swap result used other curves")
        if swap_result.order_authority != "NONE" or swap_result.capital_authority != "NONE":
            raise ValueError("swap result unexpectedly carries authority")
        quantlib_npv = next(
            (
                item.value
                for item in swap_result.measures
                if item.measure is PricingMeasure.NPV
            ),
            None,
        )
        if quantlib_npv is None:
            raise ValueError("swap result does not carry a QuantLib NPV")
        bundle = OREInputBuilder().swap_bundle(
            instrument=instrument,
            discount_curve=discount_curve,
            forwarding_curve=forwarding_curve,
        )
        runner = runner or OREEngineRunner()
        ore_npv = runner.npv(bundle)
        vs_reference = abs(ore_npv - swap_result.reference_npv)
        vs_quantlib = abs(ore_npv - quantlib_npv)
        matched = (
            vs_reference <= policy.maximum_absolute_npv_difference
            and vs_quantlib <= policy.maximum_absolute_npv_difference
        )
        result = OREDifferentialResult(
            result_id="",
            swap_pricing_result_id=swap_result.result_id,
            instrument_id=instrument.instrument_id,
            discount_curve_id=discount_curve.curve_id,
            forwarding_curve_id=forwarding_curve.curve_id,
            input_bundle_id=bundle.bundle_id,
            policy_id=policy.policy_id,
            ore_version=runner.ore_version,
            adapter_version=ORE_ADAPTER_VERSION,
            ore_npv=ore_npv,
            reference_npv=swap_result.reference_npv,
            quantlib_npv=quantlib_npv,
            absolute_difference_vs_reference=vs_reference,
            absolute_difference_vs_quantlib=vs_quantlib,
            state=(
                OREDifferentialState.MATCH
                if matched
                else OREDifferentialState.MISMATCH
            ),
            diagnostics=(
                "ORE in-process OREApp NPV analytic",
                "Direct zero segments, LogLinear discount, no extrapolation",
                "convention-defined Ibor index: 0 settlement, NullCalendar, Unadjusted",
                "future-starting vanilla fixed/float swap only",
                "ORE is compared, never substituted, for the QuantOS reference",
            ),
            trust_authority="REFERENCE_MATCH_ONLY" if matched else "NONE",
            order_authority="NONE",
            capital_authority="NONE",
        )
        return replace(result, result_id=ore_differential_result_identity(result))


@dataclass(frozen=True)
class OREScenarioDifferentialResult:
    result_id: str
    revaluation_id: str
    scenario_id: str
    instrument_id: str
    base_input_bundle_id: str
    shocked_input_bundle_id: str
    policy_id: str
    ore_version: str
    adapter_version: str
    reference_base_npv: Decimal
    reference_shocked_npv: Decimal
    reference_scenario_pnl: Decimal
    ore_base_npv: Decimal
    ore_shocked_npv: Decimal
    ore_scenario_pnl: Decimal
    absolute_pnl_difference: Decimal
    state: OREDifferentialState
    trust_authority: str
    order_authority: str
    capital_authority: str


@dataclass(frozen=True)
class ORERiskCubeDifferential:
    cube_differential_id: str
    scenario_differential_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    state: OREDifferentialState
    mismatched_scenario_ids: tuple[str, ...]
    maximum_absolute_pnl_difference: Decimal
    trust_authority: str
    order_authority: str
    capital_authority: str


class OREScenarioDifferential:
    """Stage 14.2: ORE revalues each Stage 11.6 swap scenario independently.

    QuantOS rebuilds the exact base and shocked curves from the frozen
    snapshot, scenario and curve policies, proves they are the curves the
    Stage 11.6 revaluation used, and hands only those to ORE. ORE's base NPV,
    shocked NPV and scenario P&L are then compared with the reference ones.
    """

    def compare(
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
    ) -> OREScenarioDifferentialResult:
        if revaluation.revaluation_id != scenario_revaluation_identity(revaluation):
            raise ValueError("scenario revaluation identity mismatch")
        if revaluation.instrument_id != instrument.instrument_id:
            raise ValueError("scenario revaluation belongs to another instrument")
        if revaluation.scenario_id != scenario.scenario_id:
            raise ValueError("scenario revaluation belongs to another scenario")
        if revaluation.base_snapshot_id != base_snapshot.snapshot_id:
            raise ValueError("scenario revaluation used another base snapshot")
        shocked_snapshot, state = ScenarioMarketTransformer().apply(
            base_snapshot=base_snapshot,
            scenario=scenario,
        )
        if (
            shocked_snapshot.snapshot_id != revaluation.shocked_snapshot_id
            or state.state_id != revaluation.scenario_market_state_id
        ):
            raise ValueError("shocked market state does not reproduce")
        builder = DiscountCurveBuilder()
        curves = {}
        for label, snapshot in (("base", base_snapshot), ("shocked", shocked_snapshot)):
            curves[label] = (
                builder.build(snapshot=snapshot, policy=discount_curve_policy),
                builder.build(snapshot=snapshot, policy=forwarding_curve_policy),
            )
        if tuple(c.curve_id for c in curves["base"]) != revaluation.base_curve_ids:
            raise ValueError("rebuilt base curves differ from the revaluation")
        if tuple(c.curve_id for c in curves["shocked"]) != revaluation.shocked_curve_ids:
            raise ValueError("rebuilt shocked curves differ from the revaluation")
        input_builder = OREInputBuilder()
        bundles = {
            label: input_builder.swap_bundle(
                instrument=instrument,
                discount_curve=pair[0],
                forwarding_curve=pair[1],
            )
            for label, pair in curves.items()
        }
        runner = runner or OREEngineRunner()
        ore_base = runner.npv(bundles["base"])
        ore_shocked = runner.npv(bundles["shocked"])
        ore_pnl = ore_shocked - ore_base
        differences = (
            abs(ore_base - revaluation.base_npv),
            abs(ore_shocked - revaluation.shocked_npv),
            abs(ore_pnl - revaluation.scenario_pnl),
        )
        matched = all(
            item <= policy.maximum_absolute_npv_difference for item in differences
        )
        result = OREScenarioDifferentialResult(
            result_id="",
            revaluation_id=revaluation.revaluation_id,
            scenario_id=scenario.scenario_id,
            instrument_id=instrument.instrument_id,
            base_input_bundle_id=bundles["base"].bundle_id,
            shocked_input_bundle_id=bundles["shocked"].bundle_id,
            policy_id=policy.policy_id,
            ore_version=runner.ore_version,
            adapter_version=ORE_ADAPTER_VERSION,
            reference_base_npv=revaluation.base_npv,
            reference_shocked_npv=revaluation.shocked_npv,
            reference_scenario_pnl=revaluation.scenario_pnl,
            ore_base_npv=ore_base,
            ore_shocked_npv=ore_shocked,
            ore_scenario_pnl=ore_pnl,
            absolute_pnl_difference=differences[2],
            state=(
                OREDifferentialState.MATCH
                if matched
                else OREDifferentialState.MISMATCH
            ),
            trust_authority="REFERENCE_MATCH_ONLY" if matched else "NONE",
            order_authority="NONE",
            capital_authority="NONE",
        )
        return replace(
            result,
            result_id=_content_id("ore-scenario-differential", _payload(result, "result_id")),
        )

    def cube(
        self,
        differentials: tuple[OREScenarioDifferentialResult, ...],
    ) -> ORERiskCubeDifferential:
        if not differentials:
            raise ValueError("ORE cube differential requires scenarios")
        for item in differentials:
            if item.result_id != _content_id(
                "ore-scenario-differential", _payload(item, "result_id")
            ):
                raise ValueError("ORE scenario differential identity mismatch")
        keys = [(item.scenario_id, item.instrument_id) for item in differentials]
        if len(keys) != len(set(keys)):
            raise ValueError("ORE cube differential repeats a cell")
        if len({item.policy_id for item in differentials}) != 1:
            raise ValueError("ORE cube cells use different policies")
        mismatched = tuple(
            sorted(
                {
                    item.scenario_id
                    for item in differentials
                    if item.state is not OREDifferentialState.MATCH
                }
            )
        )
        cube = ORERiskCubeDifferential(
            cube_differential_id="",
            scenario_differential_ids=tuple(sorted(i.result_id for i in differentials)),
            scenario_ids=tuple(sorted({i.scenario_id for i in differentials})),
            state=(
                OREDifferentialState.MISMATCH
                if mismatched
                else OREDifferentialState.MATCH
            ),
            mismatched_scenario_ids=mismatched,
            maximum_absolute_pnl_difference=max(
                i.absolute_pnl_difference for i in differentials
            ),
            trust_authority="NONE" if mismatched else "REFERENCE_MATCH_ONLY",
            order_authority="NONE",
            capital_authority="NONE",
        )
        return replace(
            cube,
            cube_differential_id=_content_id(
                "ore-risk-cube-differential", _payload(cube, "cube_differential_id")
            ),
        )


class OREDifferentialStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS ore_differentials (
                result_id VARCHAR PRIMARY KEY,
                input_bundle_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, result: OREDifferentialResult) -> bool:
        if result.result_id != ore_differential_result_identity(result):
            raise ValueError("ORE differential result identity mismatch")
        payload = json.dumps(
            ore_differential_result_payload(result),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            "SELECT payload_json FROM ore_differentials WHERE result_id = ?",
            [result.result_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("ORE differential identity conflict")
            return False
        self._con.execute(
            "INSERT INTO ore_differentials VALUES (?, ?, ?, ?)",
            [result.result_id, result.input_bundle_id, result.state.value, payload],
        )
        return True

    def close(self) -> None:
        self._con.close()


def ore_input_bundle_payload(bundle: OREInputBundle) -> dict[str, object]:
    payload: dict[str, object] = {
        "asof": bundle.asof.isoformat(),
        "conventions_xml": bundle.conventions_xml,
        "curve_config_xml": bundle.curve_config_xml,
        "todays_market_xml": bundle.todays_market_xml,
        "pricing_engine_xml": bundle.pricing_engine_xml,
        "portfolio_xml": bundle.portfolio_xml,
        "market_lines": list(bundle.market_lines),
    }
    for name, default in _BUNDLE_EXTENSION_DEFAULTS.items():
        value = getattr(bundle, name)
        if value != default:
            payload[name] = list(value) if isinstance(value, tuple) else value
    return payload


def ore_input_bundle_identity(bundle: OREInputBundle) -> str:
    return _content_id("ore-input-bundle", ore_input_bundle_payload(bundle))


def with_bundle_identity(bundle: OREInputBundle) -> OREInputBundle:
    return replace(bundle, bundle_id=ore_input_bundle_identity(bundle))


def ore_differential_result_payload(result: OREDifferentialResult) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name in result.__dataclass_fields__:
        if name == "result_id":
            continue
        value = getattr(result, name)
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, tuple):
            value = list(value)
        payload[name] = value
    return payload


def _payload(item: object, identity_field: str) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name in item.__dataclass_fields__:
        if name == identity_field:
            continue
        value = getattr(item, name)
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, tuple):
            value = list(value)
        payload[name] = value
    return payload


def ore_differential_result_identity(result: OREDifferentialResult) -> str:
    return _content_id("ore-differential", ore_differential_result_payload(result))


def _bundle_currency(bundle: OREInputBundle) -> str:
    if bundle.base_currency:
        return bundle.base_currency
    return bundle.market_lines[0].split(" ")[1].split("/")[2]


def _plain(value: Decimal) -> str:
    return format(value, "f")


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
