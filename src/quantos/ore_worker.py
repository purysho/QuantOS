"""Isolated ORE worker process (Stage 14.1 service boundary).

ORE's SWIG bindings share the SWIG runtime type table with the QuantLib
Python package; loading both in one process lets objects from one library be
destroyed through the other's wrappers and crashes the interpreter. ORE also
keeps process-global QuantLib state and logging. First Current therefore
runs ORE only in this child process, which must never import QuantLib or any
First Current module that does.

Protocol: one JSON object on stdin with the input bundle fields, one JSON
object on stdout: {"ore_version", "npv", "currency", "trade_ids", "errors"}.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def main() -> int:
    request = json.loads(sys.stdin.read())
    import ORE

    with tempfile.TemporaryDirectory() as tmp:
        inputs = ORE.InputParameters()
        inputs.setAsOfDate(request["asof"])
        inputs.setBaseCurrency(request["base_currency"])
        inputs.setConventions(request["conventions_xml"])
        inputs.setCurveConfigs(request["curve_config_xml"])
        inputs.setTodaysMarketParams(request["todays_market_xml"])
        inputs.setPricingEngine(request["pricing_engine_xml"])
        inputs.setPortfolio(request["portfolio_xml"])
        inputs.setAnalytics("NPV")
        inputs.setResultsPath(tmp)
        inputs.setAllFixings(True)
        inputs.setEntireMarket(True)
        app = ORE.OREApp(inputs, str(Path(tmp) / "ore.log"), 1, False)
        response = {"ore_version": ORE.__version__}
        try:
            app.run(list(request["market_lines"]), [])
            response["errors"] = list(app.getErrors())
            report = app.getReport("npv")
            headers = [report.header(i) for i in range(report.columns())]
            response["trade_ids"] = list(report.dataAsString(headers.index("TradeId")))
            response["currency"] = list(report.dataAsString(headers.index("NpvCurrency")))
            response["npv"] = [repr(float(v)) for v in report.dataAsReal(headers.index("NPV"))]
        except RuntimeError as exc:
            response["errors"] = [f"ORE run failed: {exc}"]
        finally:
            app.closeLog()
    sys.stdout.write(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
