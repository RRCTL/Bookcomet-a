import unittest

from app.services.decision_evidence import build_decision_evidence, validate_decision_evidence


class DecisionEvidenceTests(unittest.TestCase):
    def test_standard_fields_exist(self):
        payload = build_decision_evidence(
            action="manual_match",
            stage="reconciliation",
            reason="user_selected_pair",
            outcome="matched",
            trace_id="demo-trace-001",
        )
        self.assertEqual(payload["schema_version"], "v1")
        self.assertEqual(payload["action"], "manual_match")
        self.assertEqual(payload["stage"], "reconciliation")
        self.assertEqual(payload["reason"], "user_selected_pair")
        self.assertEqual(payload["outcome"], "matched")
        self.assertEqual(payload["trace_id"], "demo-trace-001")
        self.assertTrue(isinstance(payload["metadata"], dict))
        self.assertTrue(isinstance(payload["timestamp"], str))
        self.assertTrue(isinstance(payload["content_hash"], str))
        self.assertGreater(len(payload["content_hash"]), 16)

    def test_build_rejects_empty_required_fields(self):
        with self.assertRaises(ValueError):
            build_decision_evidence(
                action=" ",
                stage="reconciliation",
                reason="user_selected_pair",
                outcome="matched",
            )

    def test_content_hash_stable_for_same_semantic_input(self):
        first = build_decision_evidence(
            action="manual_match",
            stage="reconciliation",
            reason="user_selected_pair",
            outcome="matched",
            trace_id="trace-1",
            metadata={"bank_txn_id": "b1", "ledger_txn_id": "l1"},
        )
        second = build_decision_evidence(
            action="manual_match",
            stage="reconciliation",
            reason="user_selected_pair",
            outcome="matched",
            trace_id="trace-1",
            metadata={"ledger_txn_id": "l1", "bank_txn_id": "b1"},
        )
        self.assertEqual(first["content_hash"], second["content_hash"])

    def test_validate_rejects_missing_field(self):
        with self.assertRaises(ValueError):
            validate_decision_evidence(
                {
                    "schema_version": "v1",
                    "action": "manual_match",
                    "stage": "reconciliation",
                    "reason": "ok",
                    "outcome": "matched",
                    "actor_user_id": None,
                    "source": None,
                    "trace_id": None,
                    "confidence": None,
                    "matched_by": None,
                    "notes": None,
                    # "metadata" missing on purpose
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            )


if __name__ == "__main__":
    unittest.main()
