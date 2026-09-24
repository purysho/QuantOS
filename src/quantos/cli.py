from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .gates import CapitalFirewall, LiveTradingDisabled
from .models import Event, OrderProposal
from .service import QuantOS


def demo() -> int:
    os = QuantOS()
    event = Event(
        entity_id="DEMO",
        event_type="earnings.release",
        event_time=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        knowledge_time=datetime(2026, 9, 24, 12, 0, 2, tzinfo=timezone.utc),
        source_id="demo://earnings",
        payload={"metric": "eps", "actual": 1.18, "expected": 1.05},
    )
    os.ingest(event)
    hypothesis, decision = os.analyze_numeric_surprise(
        event=event, metric="EPS", expected=1.05, observed=1.18
    )

    print("EVENT", event.event_id, event.event_type)
    print("HYPOTHESIS", hypothesis.epistemic_state.value, hypothesis.statement)
    print("SURPRISE", round(hypothesis.surprise or 0.0, 4))
    print("RESEARCH_GATE", "PASS" if decision.approved_for_shadow else "FAIL", decision.reasons)

    try:
        CapitalFirewall().authorize_live_order(
            OrderProposal(
                security_id="DEMO",
                side="BUY",
                quantity=100,
                reason_hypothesis_id=hypothesis.hypothesis_id,
            )
        )
    except LiveTradingDisabled as exc:
        print("CAPITAL_FIREWALL", "BLOCKED", str(exc))
        return 0

    raise RuntimeError("capital firewall unexpectedly allowed a live order")


def main() -> int:
    parser = argparse.ArgumentParser(prog="quantos")
    parser.add_argument("command", choices=["demo"])
    args = parser.parse_args()
    if args.command == "demo":
        return demo()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
