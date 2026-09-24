import unittest
from unittest.mock import patch


class RadarReviewCLIRouteTests(unittest.TestCase):
    def test_catalog_admit_routes_without_implicit_verification(self):
        from quantos import radar_cli

        argv = [
            "quantos-radar",
            "catalog-admit",
            "--queue-id",
            "review:abc",
        ]
        with patch("sys.argv", argv), patch.object(
            radar_cli, "admit_catalog_candidate", return_value=31
        ) as command:
            self.assertEqual(radar_cli.main(), 31)
            self.assertEqual(command.call_args.kwargs["queue_id"], "review:abc")

    def test_catalog_verify_file_requires_explicit_verifier_notes_and_uri(self):
        from quantos import radar_cli

        argv = [
            "quantos-radar",
            "catalog-verify-file",
            "--source-id",
            "ARXIV:2609.1v1",
            "--path",
            "paper.pdf",
            "--source-uri",
            "https://arxiv.org/abs/2609.1v1",
            "--verifier",
            "reviewer",
            "--notes",
            "Identity checked.",
        ]
        with patch("sys.argv", argv), patch.object(
            radar_cli, "verify_catalog_file", return_value=37
        ) as command:
            self.assertEqual(radar_cli.main(), 37)
            self.assertEqual(
                command.call_args.kwargs["verifier"],
                "reviewer",
            )
            self.assertEqual(
                command.call_args.kwargs["notes"],
                "Identity checked.",
            )


if __name__ == "__main__":
    unittest.main()
