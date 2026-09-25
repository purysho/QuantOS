from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .pricing_risk_contracts import RiskScenario
from .scenario_revaluation import (
    ScenarioQuoteTransformation,
    ScenarioRevaluationResult,
    scenario_revaluation_identity,
)


class PortfolioRiskCubeState(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class PortfolioRiskPosition:
    instrument_id: str
    quantity: Decimal
    book_id: str
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("risk position requires instrument_id")
        if not self.quantity.is_finite() or self.quantity == 0:
            raise ValueError(
                "risk position quantity must be finite and non-zero"
            )
        if not self.book_id.strip():
            raise ValueError("risk position requires book_id")
        if self.label is not None and not self.label.strip():
            raise ValueError("risk position label cannot be blank")

    @property
    def position_id(self) -> str:
        return _content_id(
            "portfolio-risk-position",
            {
                "instrument_id": self.instrument_id.strip(),
                "quantity": str(self.quantity),
                "book_id": self.book_id.strip(),
                "label": (
                    self.label.strip()
                    if self.label is not None
                    else None
                ),
            },
        )


@dataclass(frozen=True)
class RiskCoverageCell:
    position_id: str
    instrument_id: str
    scenario_id: str
    covered: bool
    revaluation_id: str | None


@dataclass(frozen=True)
class PositionBaseValue:
    position_id: str
    instrument_id: str
    quantity: Decimal
    unit_npv: Decimal | None
    signed_base_value: Decimal | None
    absolute_base_value: Decimal | None
    concentration_on_gross_base: Decimal | None


@dataclass(frozen=True)
class ScenarioRiskSummary:
    scenario_id: str
    complete: bool
    portfolio_pnl: Decimal | None
    pnl_on_gross_base: Decimal | None
    largest_loss_position_id: str | None
    largest_loss: Decimal | None
    largest_gain_position_id: str | None
    largest_gain: Decimal | None


@dataclass(frozen=True)
class FiniteDifferenceScenarioSensitivity:
    scenario_id: str
    quote_type: str
    market_key: str
    tenor: str | None
    currency: str | None
    shock_kind: str
    shock_value: Decimal
    portfolio_pnl: Decimal
    pnl_per_unit_shock: Decimal


@dataclass(frozen=True)
class PortfolioRiskCube:
    cube_id: str
    state: PortfolioRiskCubeState
    base_snapshot_id: str
    valuation_time: datetime
    reporting_currency: str
    position_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    coverage: tuple[RiskCoverageCell, ...]
    missing_coverage: tuple[RiskCoverageCell, ...]
    position_base_values: tuple[PositionBaseValue, ...]
    net_base_value: Decimal | None
    gross_base_value: Decimal | None
    maximum_base_value_concentration: Decimal | None
    scenario_summaries: tuple[ScenarioRiskSummary, ...]
    finite_difference_sensitivities: tuple[
        FiniteDifferenceScenarioSensitivity, ...
    ]
    diagnostics: tuple[str, ...]
    var_authority: str
    order_authority: str
    capital_authority: str
    caveat: str


class PortfolioRiskCubeEngine:
    CAVEAT = (
        "This cube aggregates deterministic repricing evidence. It is not VaR, "
        "expected shortfall, a scenario probability model, a capital allocation, "
        "or trading authority."
    )

    def build(
        self,
        *,
        positions: tuple[PortfolioRiskPosition, ...],
        scenarios: tuple[RiskScenario, ...],
        revaluations: tuple[ScenarioRevaluationResult, ...],
    ) -> PortfolioRiskCube:
        if not positions:
            raise ValueError("risk cube requires at least one position")
        if not scenarios:
            raise ValueError("risk cube requires at least one scenario")

        position_ids = [item.position_id for item in positions]
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("duplicate risk position identities")
        scenario_ids = [item.scenario_id for item in scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("duplicate risk scenario identities")
        if len(revaluations) != len(
            {item.revaluation_id for item in revaluations}
        ):
            raise ValueError("duplicate scenario revaluation identities")
        for item in revaluations:
            if item.revaluation_id != scenario_revaluation_identity(item):
                raise ValueError("scenario revaluation identity mismatch")
            if (
                item.order_authority != "NONE"
                or item.capital_authority != "NONE"
            ):
                raise ValueError(
                    "scenario revaluation unexpectedly carries authority"
                )

        expected_instruments = {
            item.instrument_id for item in positions
        }
        expected_scenarios = set(scenario_ids)
        for item in revaluations:
            if item.instrument_id not in expected_instruments:
                raise ValueError(
                    "unexpected instrument appears in risk-cube revaluations"
                )
            if item.scenario_id not in expected_scenarios:
                raise ValueError(
                    "unexpected scenario appears in risk-cube revaluations"
                )

        by_key: dict[
            tuple[str, str],
            ScenarioRevaluationResult,
        ] = {}
        for item in revaluations:
            key = (item.instrument_id, item.scenario_id)
            existing = by_key.get(key)
            if existing is not None:
                raise ValueError(
                    "ambiguous revaluations for one instrument/scenario cell"
                )
            by_key[key] = item

        if not revaluations:
            raise ValueError(
                "risk cube requires at least one scenario revaluation"
            )
        base_snapshot_ids = {
            item.base_snapshot_id for item in revaluations
        }
        if len(base_snapshot_ids) != 1:
            raise ValueError(
                "risk cube cannot mix different base market snapshots"
            )
        valuation_times = {
            item.valuation_time for item in revaluations
        }
        if len(valuation_times) != 1:
            raise ValueError(
                "risk cube cannot mix different valuation timestamps"
            )
        valuation_time = next(iter(valuation_times))
        if valuation_time.tzinfo is None:
            raise ValueError(
                "risk cube valuation timestamp must be timezone-aware"
            )
        reporting_currencies = {
            item.reporting_currency for item in revaluations
        }
        if len(reporting_currencies) != 1:
            raise ValueError(
                "risk cube cannot mix reporting currencies without explicit FX"
            )
        base_snapshot_id = next(iter(base_snapshot_ids))
        reporting_currency = next(iter(reporting_currencies))

        scenario_state_ids: dict[str, str] = {}
        scenario_transformations: dict[
            str,
            tuple[ScenarioQuoteTransformation, ...],
        ] = {}
        for item in revaluations:
            previous_state = scenario_state_ids.get(item.scenario_id)
            if (
                previous_state is not None
                and previous_state != item.scenario_market_state_id
            ):
                raise ValueError(
                    "one scenario maps to multiple derived market states"
                )
            scenario_state_ids[item.scenario_id] = (
                item.scenario_market_state_id
            )
            previous_transformations = scenario_transformations.get(
                item.scenario_id
            )
            if (
                previous_transformations is not None
                and previous_transformations != item.transformations
            ):
                raise ValueError(
                    "one scenario has inconsistent quote transformations"
                )
            scenario_transformations[item.scenario_id] = (
                item.transformations
            )

        instrument_base: dict[
            str,
            tuple[str, str, Decimal],
        ] = {}
        for item in revaluations:
            signature = (
                item.base_request_id,
                item.base_pricing_result_id,
                item.base_npv,
            )
            previous = instrument_base.get(item.instrument_id)
            if previous is not None and previous != signature:
                raise ValueError(
                    "instrument base valuation differs across scenarios"
                )
            instrument_base[item.instrument_id] = signature

        canonical_positions = tuple(
            sorted(positions, key=lambda item: item.position_id)
        )
        canonical_scenarios = tuple(
            sorted(scenarios, key=lambda item: item.scenario_id)
        )

        coverage: list[RiskCoverageCell] = []
        for position in canonical_positions:
            for scenario in canonical_scenarios:
                item = by_key.get(
                    (position.instrument_id, scenario.scenario_id)
                )
                coverage.append(
                    RiskCoverageCell(
                        position_id=position.position_id,
                        instrument_id=position.instrument_id,
                        scenario_id=scenario.scenario_id,
                        covered=item is not None,
                        revaluation_id=(
                            item.revaluation_id
                            if item is not None
                            else None
                        ),
                    )
                )
        coverage_tuple = tuple(coverage)
        missing = tuple(
            item for item in coverage_tuple if not item.covered
        )
        state = (
            PortfolioRiskCubeState.COMPLETE
            if not missing
            else PortfolioRiskCubeState.INCOMPLETE
        )

        raw_base_values: list[tuple[PortfolioRiskPosition, Decimal | None]] = []
        for position in canonical_positions:
            signature = instrument_base.get(position.instrument_id)
            unit_npv = signature[2] if signature is not None else None
            signed = (
                unit_npv * position.quantity
                if unit_npv is not None
                else None
            )
            raw_base_values.append((position, signed))

        all_base_known = all(
            value is not None for _, value in raw_base_values
        )
        net_base: Decimal | None = None
        gross_base: Decimal | None = None
        max_concentration: Decimal | None = None
        if all_base_known:
            signed_values = tuple(
                value
                for _, value in raw_base_values
                if value is not None
            )
            net_base = sum(signed_values, Decimal("0"))
            gross_base = sum(
                (abs(item) for item in signed_values),
                Decimal("0"),
            )
            if gross_base > 0:
                max_concentration = max(
                    abs(item) / gross_base
                    for item in signed_values
                )

        position_base_values = tuple(
            PositionBaseValue(
                position_id=position.position_id,
                instrument_id=position.instrument_id,
                quantity=position.quantity,
                unit_npv=(
                    instrument_base[position.instrument_id][2]
                    if position.instrument_id in instrument_base
                    else None
                ),
                signed_base_value=value,
                absolute_base_value=(
                    abs(value) if value is not None else None
                ),
                concentration_on_gross_base=(
                    abs(value) / gross_base
                    if (
                        value is not None
                        and gross_base is not None
                        and gross_base > 0
                    )
                    else None
                ),
            )
            for position, value in raw_base_values
        )

        scenario_summaries: list[ScenarioRiskSummary] = []
        sensitivities: list[
            FiniteDifferenceScenarioSensitivity
        ] = []
        diagnostics: list[str] = [
            "portfolio values use signed position quantity times instrument NPV",
            "gross base value is sum of absolute signed base NPVs",
            "concentration is absolute base NPV share, not notional or delta exposure",
        ]
        if missing:
            diagnostics.append(
                "missing position/scenario cells are explicit; incomplete scenarios have no aggregate portfolio P&L"
            )

        for scenario in canonical_scenarios:
            position_pnls: list[tuple[str, Decimal]] = []
            complete = True
            for position in canonical_positions:
                item = by_key.get(
                    (position.instrument_id, scenario.scenario_id)
                )
                if item is None:
                    complete = False
                    continue
                position_pnls.append(
                    (
                        position.position_id,
                        item.scenario_pnl * position.quantity,
                    )
                )

            portfolio_pnl = (
                sum(
                    (value for _, value in position_pnls),
                    Decimal("0"),
                )
                if complete
                else None
            )
            pnl_on_gross = (
                portfolio_pnl / gross_base
                if (
                    portfolio_pnl is not None
                    and gross_base is not None
                    and gross_base > 0
                )
                else None
            )
            if position_pnls and complete:
                loss_id, loss = min(
                    position_pnls,
                    key=lambda item: item[1],
                )
                gain_id, gain = max(
                    position_pnls,
                    key=lambda item: item[1],
                )
            else:
                loss_id = None
                loss = None
                gain_id = None
                gain = None
            scenario_summaries.append(
                ScenarioRiskSummary(
                    scenario_id=scenario.scenario_id,
                    complete=complete,
                    portfolio_pnl=portfolio_pnl,
                    pnl_on_gross_base=pnl_on_gross,
                    largest_loss_position_id=loss_id,
                    largest_loss=loss,
                    largest_gain_position_id=gain_id,
                    largest_gain=gain,
                )
            )

            transformations = scenario_transformations.get(
                scenario.scenario_id,
                (),
            )
            if complete and portfolio_pnl is not None:
                if len(transformations) == 1:
                    transform = transformations[0]
                    if transform.shock_value != 0:
                        sensitivities.append(
                            FiniteDifferenceScenarioSensitivity(
                                scenario_id=scenario.scenario_id,
                                quote_type=transform.quote_type,
                                market_key=transform.market_key,
                                tenor=transform.tenor,
                                currency=transform.currency,
                                shock_kind=transform.shock_kind.value,
                                shock_value=transform.shock_value,
                                portfolio_pnl=portfolio_pnl,
                                pnl_per_unit_shock=(
                                    portfolio_pnl
                                    / transform.shock_value
                                ),
                            )
                        )
                    else:
                        diagnostics.append(
                            f"scenario {scenario.scenario_id} has zero shock; finite-difference ratio omitted"
                        )
                elif len(transformations) > 1:
                    diagnostics.append(
                        f"scenario {scenario.scenario_id} is multi-shock; no single-factor P&L attribution is inferred"
                    )

        canonical_summaries = tuple(
            sorted(
                scenario_summaries,
                key=lambda item: item.scenario_id,
            )
        )
        canonical_sensitivities = tuple(
            sorted(
                sensitivities,
                key=lambda item: (
                    item.scenario_id,
                    item.quote_type,
                    item.market_key,
                    item.tenor or "",
                    item.currency or "",
                ),
            )
        )
        payload = {
            "state": state.value,
            "base_snapshot_id": base_snapshot_id,
            "valuation_time": valuation_time.isoformat(),
            "reporting_currency": reporting_currency,
            "position_ids": [
                item.position_id for item in canonical_positions
            ],
            "scenario_ids": [
                item.scenario_id for item in canonical_scenarios
            ],
            "coverage": [
                _coverage_payload(item) for item in coverage_tuple
            ],
            "missing_coverage": [
                _coverage_payload(item) for item in missing
            ],
            "position_base_values": [
                _position_value_payload(item)
                for item in position_base_values
            ],
            "net_base_value": _optional_str(net_base),
            "gross_base_value": _optional_str(gross_base),
            "maximum_base_value_concentration": _optional_str(
                max_concentration
            ),
            "scenario_summaries": [
                _scenario_summary_payload(item)
                for item in canonical_summaries
            ],
            "finite_difference_sensitivities": [
                _sensitivity_payload(item)
                for item in canonical_sensitivities
            ],
            "diagnostics": list(diagnostics),
            "var_authority": "NONE",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PortfolioRiskCube(
            cube_id=_content_id("portfolio-risk-cube", payload),
            state=state,
            base_snapshot_id=base_snapshot_id,
            valuation_time=valuation_time,
            reporting_currency=reporting_currency,
            position_ids=tuple(
                item.position_id for item in canonical_positions
            ),
            scenario_ids=tuple(
                item.scenario_id for item in canonical_scenarios
            ),
            coverage=coverage_tuple,
            missing_coverage=missing,
            position_base_values=position_base_values,
            net_base_value=net_base,
            gross_base_value=gross_base,
            maximum_base_value_concentration=max_concentration,
            scenario_summaries=canonical_summaries,
            finite_difference_sensitivities=(
                canonical_sensitivities
            ),
            diagnostics=tuple(diagnostics),
            var_authority="NONE",
            order_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )


class PortfolioRiskCubeStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_risk_cubes (
                cube_id VARCHAR PRIMARY KEY,
                state VARCHAR NOT NULL,
                base_snapshot_id VARCHAR NOT NULL,
                reporting_currency VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, cube: PortfolioRiskCube) -> bool:
        if cube.cube_id != portfolio_risk_cube_identity(cube):
            raise ValueError(
                "portfolio risk cube content does not match cube_id"
            )
        payload = json.dumps(
            portfolio_risk_cube_payload(cube),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_risk_cubes
            WHERE cube_id = ?
            """,
            [cube.cube_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("portfolio risk cube identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO portfolio_risk_cubes
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                cube.cube_id,
                cube.state.value,
                cube.base_snapshot_id,
                cube.reporting_currency,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def portfolio_risk_cube_identity(
    cube: PortfolioRiskCube,
) -> str:
    return _content_id(
        "portfolio-risk-cube",
        {
            key: value
            for key, value in portfolio_risk_cube_payload(cube).items()
            if key not in {"cube_id", "caveat"}
        },
    )


def portfolio_risk_cube_payload(
    cube: PortfolioRiskCube,
) -> dict[str, object]:
    return {
        "cube_id": cube.cube_id,
        "state": cube.state.value,
        "base_snapshot_id": cube.base_snapshot_id,
        "valuation_time": cube.valuation_time.isoformat(),
        "reporting_currency": cube.reporting_currency,
        "position_ids": list(cube.position_ids),
        "scenario_ids": list(cube.scenario_ids),
        "coverage": [
            _coverage_payload(item) for item in cube.coverage
        ],
        "missing_coverage": [
            _coverage_payload(item) for item in cube.missing_coverage
        ],
        "position_base_values": [
            _position_value_payload(item)
            for item in cube.position_base_values
        ],
        "net_base_value": _optional_str(cube.net_base_value),
        "gross_base_value": _optional_str(cube.gross_base_value),
        "maximum_base_value_concentration": _optional_str(
            cube.maximum_base_value_concentration
        ),
        "scenario_summaries": [
            _scenario_summary_payload(item)
            for item in cube.scenario_summaries
        ],
        "finite_difference_sensitivities": [
            _sensitivity_payload(item)
            for item in cube.finite_difference_sensitivities
        ],
        "diagnostics": list(cube.diagnostics),
        "var_authority": cube.var_authority,
        "order_authority": cube.order_authority,
        "capital_authority": cube.capital_authority,
        "caveat": cube.caveat,
    }


def _coverage_payload(
    item: RiskCoverageCell,
) -> dict[str, object]:
    return {
        "position_id": item.position_id,
        "instrument_id": item.instrument_id,
        "scenario_id": item.scenario_id,
        "covered": item.covered,
        "revaluation_id": item.revaluation_id,
    }


def _position_value_payload(
    item: PositionBaseValue,
) -> dict[str, object]:
    return {
        "position_id": item.position_id,
        "instrument_id": item.instrument_id,
        "quantity": str(item.quantity),
        "unit_npv": _optional_str(item.unit_npv),
        "signed_base_value": _optional_str(
            item.signed_base_value
        ),
        "absolute_base_value": _optional_str(
            item.absolute_base_value
        ),
        "concentration_on_gross_base": _optional_str(
            item.concentration_on_gross_base
        ),
    }


def _scenario_summary_payload(
    item: ScenarioRiskSummary,
) -> dict[str, object]:
    return {
        "scenario_id": item.scenario_id,
        "complete": item.complete,
        "portfolio_pnl": _optional_str(item.portfolio_pnl),
        "pnl_on_gross_base": _optional_str(
            item.pnl_on_gross_base
        ),
        "largest_loss_position_id": (
            item.largest_loss_position_id
        ),
        "largest_loss": _optional_str(item.largest_loss),
        "largest_gain_position_id": (
            item.largest_gain_position_id
        ),
        "largest_gain": _optional_str(item.largest_gain),
    }


def _sensitivity_payload(
    item: FiniteDifferenceScenarioSensitivity,
) -> dict[str, object]:
    return {
        "scenario_id": item.scenario_id,
        "quote_type": item.quote_type,
        "market_key": item.market_key,
        "tenor": item.tenor,
        "currency": item.currency,
        "shock_kind": item.shock_kind,
        "shock_value": str(item.shock_value),
        "portfolio_pnl": str(item.portfolio_pnl),
        "pnl_per_unit_shock": str(
            item.pnl_per_unit_shock
        ),
    }


def _optional_str(
    value: Decimal | None,
) -> str | None:
    return str(value) if value is not None else None


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()