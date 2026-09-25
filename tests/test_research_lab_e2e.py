import tempfile
import unittest
from pathlib import Path

from quantos.research_lab_demo import run_golden_research_lab


class ResearchLabGoldenWorkflowTests(unittest.TestCase):
    def test_end_to_end_research_lab_terminates_at_closed_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_golden_research_lab(Path(tmp))
            self.assertTrue(result.universe_id.startswith("investable-universe:"))
            self.assertTrue(result.factor_id.startswith("factor-spec:"))
            self.assertTrue(
                result.validation_plan_id.startswith("walk-forward-plan:")
            )
            self.assertTrue(result.backtest_id.startswith("economic-backtest:"))
            self.assertTrue(
                result.performance_analysis_id.startswith(
                    "performance-analysis:"
                )
            )
            self.assertTrue(
                result.multiple_testing_audit_id.startswith(
                    "multiple-testing-audit:"
                )
            )
            self.assertTrue(
                result.manifest_id.startswith("research-run-manifest:")
            )
            self.assertEqual(result.registry_stage, "PAPER")
            self.assertEqual(result.paper_health_before_close, "MEASURED")
            self.assertTrue(
                result.postmortem_id.startswith("paper-postmortem:")
            )
            self.assertTrue(
                result.comparison_id.startswith("prospective-comparison:")
            )
            self.assertTrue(
                result.revision_seed_id.startswith("research-revision-seed:")
            )
            self.assertEqual(result.final_paper_health, "CLOSED")
            self.assertEqual(result.capital_authority, "NONE")


if __name__ == "__main__":
    unittest.main()
