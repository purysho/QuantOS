import unittest
from unittest.mock import patch


class CLIRouteTests(unittest.TestCase):
    def test_sec_document_routes_to_capture(self):
        from quantos import cli

        argv = [
            "quantos",
            "sec-document",
            "--event-id",
            "e1",
            "--db",
            "events.db",
        ]
        with patch("sys.argv", argv), patch.object(
            cli, "capture_sec_document", return_value=17
        ) as command:
            self.assertEqual(cli.main(), 17)
            command.assert_called_once()
            self.assertEqual(command.call_args.kwargs["event_id"], "e1")

    def test_research_import_routes_to_quarantine_import(self):
        from quantos import cli

        argv = [
            "quantos",
            "research-import",
            "--registry",
            "registry.json",
            "--catalog-db",
            "catalog.db",
        ]
        with patch("sys.argv", argv), patch.object(
            cli, "import_research_registry", return_value=23
        ) as command:
            self.assertEqual(cli.main(), 23)
            command.assert_called_once_with(
                registry="registry.json",
                catalog_db="catalog.db",
            )


if __name__ == "__main__":
    unittest.main()
