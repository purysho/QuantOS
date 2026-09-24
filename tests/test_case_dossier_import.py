import unittest


class CaseDossierImportTests(unittest.TestCase):
    def test_case_dossier_module_imports(self):
        from quantos.case_dossier import CaseDossierBuilder

        self.assertTrue(callable(CaseDossierBuilder))


if __name__ == "__main__":
    unittest.main()
