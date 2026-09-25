from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path

import duckdb
import QuantLib as ql

from .pricing_risk_contracts import (
    Currency,
    MarketDataSnapshot,
    MarketQuoteType,
    QuoteUnit,
    market_data_snapshot_identity,
)


_TENOR_RE = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[DWMY])$")


@dataclass(frozen=True)
class CurveConstructionPolicy:
    curve_key: str
    currency: Currency
    minimum_pillars: int
    maximum_absolute_zero_rate: Decimal
    maximum_absolute_forward_rate: Decimal
    repricing_tolerance: Decimal
    allow_extrapolation: bool
    day_count: str
    interpolation: str
    compounding: str
    calendar: str
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.curve_key.strip():
            raise ValueError("curve_key is required")
        if self.minimum_pillars < 2:
            raise ValueError("minimum_pillars must be at least 2")
        for name in (
            "maximum_absolute_zero_rate",
            "maximum_absolute_forward_rate",
            "repricing_tolerance",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{name} must be finite and non-negative"
                )
        if self.maximum_absolute_zero_rate <= 0:
            raise ValueError(
                "maximum_absolute_zero_rate must be positive"
            )
        if self.maximum_absolute_forward_rate <= 0:
            raise ValueError(
                "maximum_absolute_forward_rate must be positive"
            )
        if self.day_count != "ACT_365_FIXED":
            raise ValueError(
                "Stage 11.3 supports ACT_365_FIXED only"
            )
        if self.interpolation != "LOG_LINEAR_DISCOUNT":
            raise ValueError(
                "Stage 11.3 supports LOG_LINEAR_DISCOUNT only"
            )
        if self.compounding != "CONTINUOUS":
            raise ValueError(
                "Stage 11.3 supports CONTINUOUS zero rates only"
            )
        if self.calendar != "NULL_CALENDAR":
            raise ValueError(
                "Stage 11.3 supports NULL_CALENDAR only"
            )
        if not self.rationale.strip():
            raise ValueError("curve policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("curve policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "curve-construction-policy",
            {
                "curve_key": self.curve_key.strip(),
                "currency": self.currency.value,
                "minimum_pillars": self.minimum_pillars,
                "maximum_absolute_zero_rate": str(
                    self.maximum_absolute_zero_rate
                ),
                "maximum_absolute_forward_rate": str(
                    self.maximum_absolute_forward_rate
                ),
                "repricing_tolerance": str(
                    self.repricing_tolerance
                ),
                "allow_extrapolation": self.allow_extrapolation,
                "day_count": self.day_count,
                "interpolation": self.interpolation,
                "compounding": self.compounding,
                "calendar": self.calendar,
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class DiscountCurvePillar:
    tenor: str
    pillar_date: date
    year_fraction: Decimal
    zero_rate: Decimal
    discount_factor: Decimal
    input_quote_id: str
    repricing_residual: Decimal


@dataclass(frozen=True)
class DiscountCurveArtifact:
    curve_id: str
    snapshot_id: str
    valuation_date: date
    curve_key: str
    currency: Currency
    policy_id: str
    input_quote_ids: tuple[str, ...]
    pillars: tuple[DiscountCurvePillar, ...]
    maximum_absolute_forward_rate: Decimal
    maximum_absolute_repricing_residual: Decimal
    quantlib_verified: bool
    quantlib_version: str
    interpolation: str
    allow_extrapolation: bool
    diagnostics: tuple[str, ...]
    order_authority: str
    capital_authority: str

    def discount_factor(self, target_date: date) -> Decimal:
        if target_date < self.valuation_date:
            raise ValueError(
                "discount factor target cannot precede valuation date"
            )
        if target_date == self.valuation_date:
            return Decimal("1")

        points = (
            (
                self.valuation_date,
                Decimal("0"),
                Decimal("1"),
            ),
            *tuple(
                (
                    item.pillar_date,
                    item.year_fraction,
                    item.discount_factor,
                )
                for item in self.pillars
            ),
        )
        target_t = _year_fraction(
            self.valuation_date,
            target_date,
        )
        if target_t > points[-1][1]:
            if not self.allow_extrapolation:
                raise ValueError(
                    "curve extrapolation is disabled by policy"
                )
            left = points[-2]
            right = points[-1]
        else:
            left = points[0]
            right = points[1]
            for candidate_left, candidate_right in zip(
                points,
                points[1:],
            ):
                if (
                    candidate_left[1]
                    <= target_t
                    <= candidate_right[1]
                ):
                    left = candidate_left
                    right = candidate_right
                    break

        left_t = left[1]
        right_t = right[1]
        if target_t == left_t:
            return left[2]
        if target_t == right_t:
            return right[2]
        if right_t <= left_t:
            raise ValueError("curve pillars are not strictly increasing")

        weight = (
            (target_t - left_t)
            / (right_t - left_t)
        )
        log_left = Decimal(
            str(math.log(float(left[2])))
        )
        log_right = Decimal(
            str(math.log(float(right[2])))
        )
        interpolated_log = (
            log_left
            + weight * (log_right - log_left)
        )
        return Decimal(
            str(math.exp(float(interpolated_log)))
        )


class DiscountCurveBuilder:
    """Build a frozen curve artifact from point-in-time continuous zero rates."""

    def __init__(self) -> None:
        self.quantlib_version = package_version("QuantLib")

    def build(
        self,
        *,
        snapshot: MarketDataSnapshot,
        policy: CurveConstructionPolicy,
    ) -> DiscountCurveArtifact:
        if (
            snapshot.snapshot_id
            != market_data_snapshot_identity(snapshot)
        ):
            raise ValueError("market snapshot identity mismatch")

        quotes = tuple(
            item
            for item in snapshot.quotes
            if item.quote_type is MarketQuoteType.ZERO_RATE
            and item.market_key == policy.curve_key
            and item.currency is policy.currency
        )
        if len(quotes) < policy.minimum_pillars:
            raise ValueError(
                "too few zero-rate quotes for curve construction"
            )
        if any(item.tenor is None for item in quotes):
            raise ValueError(
                "curve zero-rate quotes require explicit tenors"
            )
        if any(item.unit is not QuoteUnit.DECIMAL for item in quotes):
            raise ValueError(
                "curve zero-rate quotes must use DECIMAL units"
            )
        if len({item.tenor for item in quotes}) != len(quotes):
            raise ValueError("duplicate curve tenors")

        valuation_date = snapshot.valuation_time.date()
        raw_pillars: list[DiscountCurvePillar] = []
        for quote in quotes:
            assert quote.tenor is not None
            pillar_date = _tenor_date(
                valuation_date,
                quote.tenor,
            )
            year_fraction = _year_fraction(
                valuation_date,
                pillar_date,
            )
            if year_fraction <= 0:
                raise ValueError(
                    "curve pillar must follow valuation date"
                )
            if abs(quote.value) > policy.maximum_absolute_zero_rate:
                raise ValueError(
                    "zero-rate quote exceeds policy sanity bound"
                )
            discount_factor = Decimal(
                str(
                    math.exp(
                        -float(quote.value)
                        * float(year_fraction)
                    )
                )
            )
            if (
                not discount_factor.is_finite()
                or discount_factor <= 0
            ):
                raise ValueError(
                    "curve produced invalid discount factor"
                )
            reconstructed = Decimal(
                str(
                    -math.log(float(discount_factor))
                    / float(year_fraction)
                )
            )
            residual = reconstructed - quote.value
            raw_pillars.append(
                DiscountCurvePillar(
                    tenor=quote.tenor,
                    pillar_date=pillar_date,
                    year_fraction=year_fraction,
                    zero_rate=quote.value,
                    discount_factor=discount_factor,
                    input_quote_id=quote.quote_id,
                    repricing_residual=residual,
                )
            )

        pillars = tuple(
            sorted(
                raw_pillars,
                key=lambda item: item.pillar_date,
            )
        )
        if len({item.pillar_date for item in pillars}) != len(pillars):
            raise ValueError(
                "distinct tenors map to duplicate pillar dates"
            )
        if any(
            right.pillar_date <= left.pillar_date
            for left, right in zip(pillars, pillars[1:])
        ):
            raise ValueError(
                "curve pillar dates must be strictly increasing"
            )

        forwards = _forward_rates(
            valuation_date=valuation_date,
            pillars=pillars,
        )
        maximum_forward = max(
            (abs(item) for item in forwards),
            default=Decimal("0"),
        )
        if maximum_forward > policy.maximum_absolute_forward_rate:
            raise ValueError(
                "implied forward rate exceeds policy sanity bound"
            )
        maximum_residual = max(
            (
                abs(item.repricing_residual)
                for item in pillars
            ),
            default=Decimal("0"),
        )
        if maximum_residual > policy.repricing_tolerance:
            raise ValueError(
                "curve zero-rate repricing residual exceeds policy"
            )

        quantlib_verified = self._verify_with_quantlib(
            valuation_date=valuation_date,
            pillars=pillars,
            tolerance=policy.repricing_tolerance,
        )
        if not quantlib_verified:
            raise ValueError(
                "QuantLib discount curve differs from frozen curve pillars"
            )

        input_quote_ids = tuple(
            item.input_quote_id for item in pillars
        )
        diagnostics = (
            "continuous zero-rate inputs",
            "ACT_365_FIXED",
            "NULL_CALENDAR",
            "LOG_LINEAR_DISCOUNT interpolation",
            (
                "discount factors may exceed 1 under negative rates; "
                "positivity, not monotonic decrease, is enforced"
            ),
            "QuantLib pillar discount factors independently verified",
        )
        payload = {
            "snapshot_id": snapshot.snapshot_id,
            "valuation_date": valuation_date.isoformat(),
            "curve_key": policy.curve_key.strip(),
            "currency": policy.currency.value,
            "policy_id": policy.policy_id,
            "input_quote_ids": list(input_quote_ids),
            "pillars": [
                _pillar_payload(item) for item in pillars
            ],
            "maximum_absolute_forward_rate": str(maximum_forward),
            "maximum_absolute_repricing_residual": str(
                maximum_residual
            ),
            "quantlib_verified": quantlib_verified,
            "quantlib_version": self.quantlib_version,
            "interpolation": policy.interpolation,
            "allow_extrapolation": policy.allow_extrapolation,
            "diagnostics": list(diagnostics),
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return DiscountCurveArtifact(
            curve_id=_content_id("discount-curve", payload),
            snapshot_id=snapshot.snapshot_id,
            valuation_date=valuation_date,
            curve_key=policy.curve_key.strip(),
            currency=policy.currency,
            policy_id=policy.policy_id,
            input_quote_ids=input_quote_ids,
            pillars=pillars,
            maximum_absolute_forward_rate=maximum_forward,
            maximum_absolute_repricing_residual=maximum_residual,
            quantlib_verified=quantlib_verified,
            quantlib_version=self.quantlib_version,
            interpolation=policy.interpolation,
            allow_extrapolation=policy.allow_extrapolation,
            diagnostics=diagnostics,
            order_authority="NONE",
            capital_authority="NONE",
        )

    @staticmethod
    def _verify_with_quantlib(
        *,
        valuation_date: date,
        pillars: tuple[DiscountCurvePillar, ...],
        tolerance: Decimal,
    ) -> bool:
        dates = [_ql_date(valuation_date)] + [
            _ql_date(item.pillar_date) for item in pillars
        ]
        discounts = [1.0] + [
            float(item.discount_factor) for item in pillars
        ]
        try:
            curve = ql.DiscountCurve(
                dates,
                discounts,
                ql.Actual365Fixed(),
                ql.NullCalendar(),
            )
        except Exception as exc:
            raise ValueError(
                f"QuantLib discount curve construction failed: {exc}"
            ) from exc

        allowed = max(float(tolerance), 1e-12)
        for item in pillars:
            observed = float(
                curve.discount(_ql_date(item.pillar_date))
            )
            if not math.isfinite(observed):
                return False
            if abs(observed - float(item.discount_factor)) > allowed:
                return False
        return True


class DiscountCurveStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS discount_curves (
                curve_id VARCHAR PRIMARY KEY,
                snapshot_id VARCHAR NOT NULL,
                valuation_date DATE NOT NULL,
                curve_key VARCHAR NOT NULL,
                currency VARCHAR NOT NULL,
                policy_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, curve: DiscountCurveArtifact) -> bool:
        if curve.curve_id != discount_curve_identity(curve):
            raise ValueError(
                "discount curve content does not match curve_id"
            )
        payload = json.dumps(
            discount_curve_payload(curve),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM discount_curves
            WHERE curve_id = ?
            """,
            [curve.curve_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("discount curve identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO discount_curves
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                curve.curve_id,
                curve.snapshot_id,
                curve.valuation_date,
                curve.curve_key,
                curve.currency.value,
                curve.policy_id,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def discount_curve_identity(
    curve: DiscountCurveArtifact,
) -> str:
    return _content_id(
        "discount-curve",
        {
            key: value
            for key, value in discount_curve_payload(curve).items()
            if key != "curve_id"
        },
    )


def discount_curve_payload(
    curve: DiscountCurveArtifact,
) -> dict[str, object]:
    return {
        "curve_id": curve.curve_id,
        "snapshot_id": curve.snapshot_id,
        "valuation_date": curve.valuation_date.isoformat(),
        "curve_key": curve.curve_key,
        "currency": curve.currency.value,
        "policy_id": curve.policy_id,
        "input_quote_ids": list(curve.input_quote_ids),
        "pillars": [
            _pillar_payload(item) for item in curve.pillars
        ],
        "maximum_absolute_forward_rate": str(
            curve.maximum_absolute_forward_rate
        ),
        "maximum_absolute_repricing_residual": str(
            curve.maximum_absolute_repricing_residual
        ),
        "quantlib_verified": curve.quantlib_verified,
        "quantlib_version": curve.quantlib_version,
        "interpolation": curve.interpolation,
        "allow_extrapolation": curve.allow_extrapolation,
        "diagnostics": list(curve.diagnostics),
        "order_authority": curve.order_authority,
        "capital_authority": curve.capital_authority,
    }


def _forward_rates(
    *,
    valuation_date: date,
    pillars: tuple[DiscountCurvePillar, ...],
) -> tuple[Decimal, ...]:
    points = (
        (
            Decimal("0"),
            Decimal("1"),
        ),
        *tuple(
            (
                item.year_fraction,
                item.discount_factor,
            )
            for item in pillars
        ),
    )
    forwards = []
    for left, right in zip(points, points[1:]):
        dt = right[0] - left[0]
        if dt <= 0:
            raise ValueError(
                "curve year fractions must be strictly increasing"
            )
        forward = Decimal(
            str(
                -math.log(
                    float(right[1] / left[1])
                )
                / float(dt)
            )
        )
        if not forward.is_finite():
            raise ValueError("curve produced non-finite forward rate")
        forwards.append(forward)
    return tuple(forwards)


def _tenor_date(
    valuation_date: date,
    tenor: str,
) -> date:
    match = _TENOR_RE.fullmatch(tenor.strip().upper())
    if match is None:
        raise ValueError(
            f"unsupported curve tenor: {tenor!r}"
        )
    count = int(match.group("count"))
    unit = match.group("unit")
    if unit == "D":
        return valuation_date + timedelta(days=count)
    if unit == "W":
        return valuation_date + timedelta(weeks=count)
    if unit == "M":
        return _add_months(valuation_date, count)
    if unit == "Y":
        return _add_months(valuation_date, count * 12)
    raise AssertionError("unreachable tenor unit")


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(
        value.day,
        calendar.monthrange(year, month)[1],
    )
    return date(year, month, day)


def _year_fraction(
    start: date,
    end: date,
) -> Decimal:
    return Decimal((end - start).days) / Decimal("365")


def _pillar_payload(
    pillar: DiscountCurvePillar,
) -> dict[str, object]:
    return {
        "tenor": pillar.tenor,
        "pillar_date": pillar.pillar_date.isoformat(),
        "year_fraction": str(pillar.year_fraction),
        "zero_rate": str(pillar.zero_rate),
        "discount_factor": str(pillar.discount_factor),
        "input_quote_id": pillar.input_quote_id,
        "repricing_residual": str(pillar.repricing_residual),
    }


def _ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
