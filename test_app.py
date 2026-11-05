import tempfile
import unittest
from pathlib import Path

import yojana_api


def profile(**overrides):
    result = {
        "age": 32,
        "state": "Maharashtra",
        "annual_income": 180000,
        "gender": "woman",
        "category": "general",
        "occupation": "farmer",
        "owns_land": True,
        "is_student": False,
        "has_lpg": False,
        "has_bank_account": True,
        "is_taxpayer": False,
    }
    result.update(overrides)
    return result


class SchemeMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = yojana_api.DATABASE
        yojana_api.DATABASE = Path(self.temp_dir.name) / "test.sqlite3"
        yojana_api.initialize_database()

    def tearDown(self):
        yojana_api.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def check(self, candidate):
        with yojana_api.database_connection() as connection:
            return yojana_api.evaluate_profile(candidate, connection)

    def test_possible_schemes_include_documents_steps_and_translations(self):
        matches = self.check(profile())
        by_id = {match["id"]: match for match in matches}

        self.assertIn("pm-kisan", by_id)
        self.assertIn("pm-ujjwala", by_id)
        self.assertIn("atal-pension-yojana", by_id)
        self.assertIn("pm-jay", by_id)
        self.assertTrue(by_id["pm-kisan"]["documents"])
        self.assertTrue(by_id["pm-kisan"]["steps"])
        self.assertIn("hi", by_id["pm-kisan"]["translations"])

    def test_rules_exclude_profiles_that_fail_basic_conditions(self):
        matches = self.check(profile(
            age=45,
            occupation="worker",
            owns_land=False,
            has_lpg=True,
            has_bank_account=False,
            is_taxpayer=True,
        ))
        scheme_ids = {match["id"] for match in matches}

        self.assertNotIn("pm-kisan", scheme_ids)
        self.assertNotIn("pm-ujjwala", scheme_ids)
        self.assertNotIn("atal-pension-yojana", scheme_ids)
        self.assertIn("pm-jay", scheme_ids)

    def test_student_match_is_discovered(self):
        matches = self.check(profile(occupation="student", is_student=True, owns_land=False))
        self.assertIn("nsp-post-matric", {match["id"] for match in matches})

    def test_pension_age_boundaries_are_inclusive(self):
        for age in (18, 40):
            with self.subTest(age=age):
                matches = self.check(profile(age=age, occupation="worker", owns_land=False))
                self.assertIn("atal-pension-yojana", {match["id"] for match in matches})

    def test_invalid_state_and_age_are_rejected(self):
        with self.assertRaises(ValueError):
            yojana_api.validate_profile(profile(state="Not a state"))
        with self.assertRaises(ValueError):
            yojana_api.validate_profile(profile(age=121))


if __name__ == "__main__":
    unittest.main()
