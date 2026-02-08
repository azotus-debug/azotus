import unittest

from state_machine import InvalidTransitionError, record_transition


class StateMachineRegressionTests(unittest.TestCase):
    def test_finalized_to_burning_is_valid(self):
        record = record_transition(
            job_id="job-1",
            from_stage="FINALIZED",
            to_stage="BURNING",
        )
        self.assertTrue(record["valid"])
        self.assertEqual(record["from_stage"], "finalizing")
        self.assertEqual(record["to_stage"], "finalizing")

    def test_burning_to_completed_is_valid(self):
        record = record_transition(
            job_id="job-2",
            from_stage="BURNING",
            to_stage="COMPLETED",
        )
        self.assertTrue(record["valid"])
        self.assertEqual(record["from_stage"], "finalizing")
        self.assertEqual(record["to_stage"], "delivered")

    def test_queued_to_finalized_is_invalid(self):
        with self.assertRaises(InvalidTransitionError):
            record_transition(
                job_id="job-3",
                from_stage="QUEUED",
                to_stage="FINALIZED",
            )


if __name__ == "__main__":
    unittest.main()
