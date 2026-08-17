import unittest

from fastapi import HTTPException

from app.services.rule_governance import normalize_pattern, validate_rule_payload


class RuleGovernanceValidationTests(unittest.TestCase):
    def test_normalize_pattern_handles_blank(self):
        self.assertIsNone(normalize_pattern("   "))
        self.assertEqual(normalize_pattern(" abc "), "abc")

    def test_validate_rule_payload_requires_at_least_one_pattern(self):
        with self.assertRaises(HTTPException):
            validate_rule_payload(
                rule_name="Rule A",
                keyword_pattern=None,
                vendor_pattern=None,
                amount_pattern=None,
            )

    def test_validate_rule_payload_rejects_short_keyword_tokens(self):
        with self.assertRaises(HTTPException):
            validate_rule_payload(
                rule_name="Rule A",
                keyword_pattern="a, b",
                vendor_pattern=None,
                amount_pattern=None,
            )

    def test_validate_rule_payload_accepts_valid_keyword(self):
        validate_rule_payload(
            rule_name="Rule A",
            keyword_pattern="apple store, macbook",
            vendor_pattern=None,
            amount_pattern=None,
        )


if __name__ == "__main__":
    unittest.main()
