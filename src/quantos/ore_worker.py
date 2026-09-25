"""Isolated ORE worker process (Stage 14 service boundary).

ORE's SWIG bindings share the SWIG runtime type table with the QuantLib
Python package; loading both in one process lets objects from one library be
destroyed through the other's wrappers and crashes the interpreter. ORE also
keeps process-global QuantLib state and logging. First Current therefore
runs ORE only in this child process, which must never import QuantLib or any
First Current module that does.

Protocol: one JSON object on stdin describing the input bundle and the
analytics to run; one JSON object on stdout:
{"ore_version", "errors", "reports": {name: {"headers": [...], "rows": [[...]]}}}
Numeric cells are transported as repr() strings so no precision is lost.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_OPTIONAL_INPUTS = {
    "sensitivity_sim_xml": "setSensiSimMarketParams",
    "sensitivity_scenario_xml": "setSensiScenarioData",
    "stress_sim_xml": "setStressSimMarketParams",
    "stress_scenario_xml": "setStressScenarioData",
}


def _cell(report, column: int, row: int):
    kind = report.columnType(column)
    if kind == 2:
        return report.dataAsString(column)[row]
    if kind == 1:
        return repr(float(report.dataAsReal(column)[row]))
    if kind == 0:
        return int(report.dataAsSize(column)[row])
    if kind == 3:
        return report.dataAsDate(column)[row].ISO()
    return None


def main() -> int:
    request = json.loads(sys.stdin.read())
    import ORE

    response = {"ore_version": ORE.__version__, "reports": {}}
    with tempfile.TemporaryDirectory() as tmp:
        inputs = ORE.InputParameters()
        inputs.setAsOfDate(request["asof"])
        inputs.setBaseCurrency(request["base_currency"])
        inputs.setConventions(request["conventions_xml"])
        inputs.setCurveConfigs(request["curve_config_xml"])
        inputs.setTodaysMarketParams(request["todays_market_xml"])
        inputs.setPricingEngine(request["pricing_engine_xml"])
        inputs.setPortfolio(request["portfolio_xml"])
        for field, setter in _OPTIONAL_INPUTS.items():
            if request.get(field):
                getattr(inputs, setter)(request[field])
        if request.get("sensitivity_scenario_xml"):
            inputs.setSensiPricingEngine(request["pricing_engine_xml"])
        if request.get("stress_scenario_xml"):
            inputs.setStressPricingEngine(request["pricing_engine_xml"])
        inputs.setAnalytics(",".join(request["analytics"]))
        inputs.setResultsPath(tmp)
        inputs.setAllFixings(True)
        inputs.setEntireMarket(True)
        app = ORE.OREApp(inputs, str(Path(tmp) / "ore.log"), 1, False)
        try:
            app.run(list(request["market_lines"]), [])
            response["errors"] = list(app.getErrors())
            available = set(app.getReportNames())
            for name in request["reports"]:
                if name not in available:
                    response["errors"].append(f"missing ORE report {name}")
                    continue
                report = app.getReport(name)
                response["reports"][name] = {
                    "headers": [report.header(i) for i in range(report.columns())],
                    "rows": [
                        [_cell(report, i, row) for i in range(report.columns())]
                        for row in range(report.rows())
                    ],
                }
        except RuntimeError as exc:
            response["errors"] = [f"ORE run failed: {exc}"]
        finally:
            app.closeLog()
    sys.stdout.write(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
