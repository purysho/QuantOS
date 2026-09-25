import tempfile
import unittest
from decimal import Decimal

from quantos.execution_contracts import (
    CommissionRounding,
    ExecutionOrderType,
    ExecutionSide,
    ImmediatePartialFills,
    LiquidityRefresh,
    MarketOrderResidual,
    OrderActivationMode,
    RestingLimitFillPrice,
    SimulationOrderState,
    TimeInForce,
)

from tests.test_execution_nautilus import MS, SUBMIT, dataset, policy, quote, reference_side


def run(data, p, **order):
    with tempfile.TemporaryDirectory() as tmp:
        _, _, result, fills = reference_side(data, p, order, tmp)
    return result, fills


def book(*levels):
    return dataset(
        *(
            quote(at=SUBMIT + i * MS, sequence=i + 1, ask=ask, bid=str(Decimal(ask) - Decimal("0.02")), size=size)
            for i, (ask, size) in enumerate(levels)
        )
    )


class ReferenceModeTests(unittest.TestCase):
    def test_default_modes_keep_stage_12_2_policy_identity(self):
        base = policy()
        self.assertEqual(base.non_default_modes(), {})
        explicit = policy(order_activation=OrderActivationMode.IMMEDIATE_LATEST_BOOK)
        self.assertEqual(base.policy_id, explicit.policy_id)
        changed = policy(order_activation=OrderActivationMode.NEXT_QUOTE_ARRIVAL)
        self.assertNotEqual(base.policy_id, changed.policy_id)
        with self.assertRaises(ValueError):
            policy(liquidity_refresh="ON_LEVEL_SIZE_CHANGE")

    def test_next_quote_arrival_activation(self):
        data = dataset(
            quote(at=SUBMIT, sequence=1, ask="100.01", bid="99.99"),
            quote(at=SUBMIT + 5 * MS, sequence=2, ask="100.02", bid="100.00"),
            quote(at=SUBMIT + 10 * MS, sequence=3, ask="100.03", bid="100.01"),
        )
        immediate, _ = run(data, policy(order_latency_ms=7))
        nxt, fills = run(data, policy(order_latency_ms=7, order_activation=OrderActivationMode.NEXT_QUOTE_ARRIVAL))
        self.assertEqual(immediate.volume_weighted_average_price, Decimal("100.02"))
        self.assertEqual(nxt.volume_weighted_average_price, Decimal("100.03"))
        self.assertEqual(fills[0].fill_time, SUBMIT + 10 * MS)
        rejected, _ = run(data, policy(order_latency_ms=12, order_activation=OrderActivationMode.NEXT_QUOTE_ARRIVAL))
        self.assertEqual(rejected.final_state, SimulationOrderState.REJECTED)

    def test_market_residual_sweeps_one_tick_through(self):
        p = policy(market_order_residual=MarketOrderResidual.ONE_TICK_THROUGH)
        result, fills = run(book(("100.01", "60")), p, quantity="250")
        self.assertEqual([(f.quantity, f.price) for f in fills], [(Decimal("60"), Decimal("100.01")), (Decimal("190"), Decimal("100.02"))])
        self.assertEqual(result.final_state, SimulationOrderState.FILLED)
        ioc, _ = run(book(("100.01", "60")), policy(allow_partial_fills=True, market_order_residual=MarketOrderResidual.ONE_TICK_THROUGH), quantity="250", time_in_force=TimeInForce.IOC)
        self.assertEqual(ioc.filled_quantity, Decimal("60"))

    def test_resting_limit_fills_at_limit_price_with_level_memory(self):
        p = policy(
            allow_partial_fills=True,
            resting_limit_fill_price=RestingLimitFillPrice.LIMIT_PRICE,
            liquidity_refresh=LiquidityRefresh.ON_LEVEL_SIZE_CHANGE,
        )
        data = book(("100.00", "60"), ("100.03", "500"), ("100.00", "60"), ("99.99", "30"), ("100.00", "20"))
        result, fills = run(data, p, order_type=ExecutionOrderType.LIMIT, limit_price="100.01")
        self.assertEqual(
            [(f.quantity, f.price, f.liquidity.value) for f in fills],
            [
                (Decimal("60"), Decimal("100.00"), "UNKNOWN"),
                (Decimal("30"), Decimal("100.01"), "MAKER"),
                (Decimal("10"), Decimal("100.01"), "MAKER"),
            ],
        )
        every, _ = run(data, policy(allow_partial_fills=True), order_type=ExecutionOrderType.LIMIT, limit_price="100.01")
        self.assertEqual(every.volume_weighted_average_price, Decimal("100.00") * Decimal("0.6") + Decimal("100.00") * Decimal("0.4"))

    def test_commission_rounding_half_even(self):
        exact, _ = run(book(("50.00", "1000")), policy(commission_bps=Decimal("1")), quantity="3")
        rounded, _ = run(book(("50.00", "1000")), policy(commission_bps=Decimal("1"), commission_rounding=CommissionRounding.HALF_EVEN_MINOR_UNIT), quantity="5")
        self.assertEqual(exact.total_fees, Decimal("0.015"))
        self.assertEqual(rounded.total_fees, Decimal("0.02"))

    def test_ioc_always_partial(self):
        follow, _ = run(book(("100.01", "60")), policy(), time_in_force=TimeInForce.IOC)
        always, _ = run(book(("100.01", "60")), policy(immediate_partial_fills=ImmediatePartialFills.ALWAYS_ALLOW), time_in_force=TimeInForce.IOC)
        self.assertEqual(follow.filled_quantity, Decimal("0"))
        self.assertEqual(always.filled_quantity, Decimal("60"))
        sell, _ = run(book(("100.01", "60")), policy(immediate_partial_fills=ImmediatePartialFills.ALWAYS_ALLOW), time_in_force=TimeInForce.IOC, side=ExecutionSide.SELL)
        self.assertEqual(sell.volume_weighted_average_price, Decimal("99.99"))


if __name__ == "__main__":
    unittest.main()
