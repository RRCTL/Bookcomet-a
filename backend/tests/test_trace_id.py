import unittest
import uuid

from app.api.deps import get_trace_id


class TraceIdTests(unittest.TestCase):
    def test_keeps_existing_trace_id(self):
        value = get_trace_id("demo-trace-001")
        self.assertEqual(value, "demo-trace-001")

    def test_generates_trace_id_when_missing(self):
        value = get_trace_id(None)
        parsed = uuid.UUID(value)
        self.assertEqual(str(parsed), value)


if __name__ == "__main__":
    unittest.main()
