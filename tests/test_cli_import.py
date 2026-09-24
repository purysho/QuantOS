import unittest


class CLIImportTests(unittest.TestCase):
    def test_cli_module_imports_and_exposes_sec_document_command(self):
        from quantos import cli

        self.assertTrue(callable(cli.capture_sec_document))


if __name__ == "__main__":
    unittest.main()
