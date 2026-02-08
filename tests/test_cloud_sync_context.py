import unittest

from cloud_sync_service import _extract_job_context


class CloudSyncContextTests(unittest.TestCase):
    def test_trace_id_from_payload_top_level(self):
        payload = {
            "trace_id": "trace-top",
            "meta": {},
            "target_language": "is",
        }
        context = _extract_job_context("job-abc", payload)
        self.assertEqual(context["trace_id"], "trace-top")
        self.assertEqual(context["meta"]["trace_id"], "trace-top")

    def test_trace_id_from_meta_fallback(self):
        payload = {
            "meta": {"correlation_id": "trace-meta"},
            "target_language": "is",
        }
        context = _extract_job_context("job-def", payload)
        self.assertEqual(context["trace_id"], "trace-meta")
        self.assertEqual(context["meta"]["trace_id"], "trace-meta")

    def test_trace_id_defaults_to_job_id(self):
        context = _extract_job_context("job-ghi", None)
        self.assertEqual(context["trace_id"], "job-ghi")
        self.assertEqual(context["meta"]["trace_id"], "job-ghi")


if __name__ == "__main__":
    unittest.main()
