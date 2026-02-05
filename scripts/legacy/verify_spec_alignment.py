import omega_db
import sqlite3
import unittest
from state_machine import (
    MasterScriptStateMachine,
    OutputTrackStateMachine,
    ChangeClassifier,
    ChangeType,
    MasterState,
    TrackState
)
from notification_manager import NotificationManager

class TestSpecAlignment(unittest.TestCase):
    
    def test_master_state_machine(self):
        # Allow draft -> approved
        self.assertTrue(MasterScriptStateMachine.can_transition("draft", "approved"))
        # Allow approved -> locked
        self.assertTrue(MasterScriptStateMachine.can_transition("approved", "locked"))
        # Deny draft -> locked (must approve first)
        self.assertFalse(MasterScriptStateMachine.can_transition("draft", "locked"))
        # Allow locked -> draft (new version)
        self.assertTrue(MasterScriptStateMachine.can_transition("locked", "draft"))

    def test_track_state_machine(self):
        # Allow draft -> approved
        self.assertTrue(OutputTrackStateMachine.can_transition("draft", "approved"))
        # Allow approved -> rendered
        self.assertTrue(OutputTrackStateMachine.can_transition("approved", "rendered"))
        # Allow rendered -> delivered
        self.assertTrue(OutputTrackStateMachine.can_transition("rendered", "delivered"))
        # Allow delivered -> locked
        self.assertTrue(OutputTrackStateMachine.can_transition("delivered", "locked"))
        # Allow locked -> rendered (override)
        self.assertTrue(OutputTrackStateMachine.can_transition("locked", "rendered"))

    def test_decision_table(self):
        # Text Change Minor
        decision = ChangeClassifier.evaluate_change(ChangeType.TEXT_CHANGE_MINOR)
        self.assertTrue(decision["master_version_increment"])
        self.assertTrue(decision["pending_resync"]) # Dubs flagged
        self.assertEqual(decision["approval_role"], "reviewer")
        
        # Formatting Only
        decision = ChangeClassifier.evaluate_change(ChangeType.FORMATTING_ONLY)
        self.assertFalse(decision["master_version_increment"])
        self.assertFalse(decision["pending_resync"])
        self.assertEqual(decision["approval_role"], "operator")

        # Output Override
        decision = ChangeClassifier.evaluate_change(ChangeType.OUTPUT_OVERRIDE)
        self.assertFalse(decision["master_version_increment"])
        self.assertTrue(decision.get("output_only"))

    def test_db_schema_columns(self):
        # Ensure migration added the columns
        omega_db.ensure_schema()
        conn = omega_db._connect()
        c = conn.cursor()
        
        # Check Master Scripts
        c.execute("PRAGMA table_info(master_scripts)")
        cols = {row[1] for row in c.fetchall()}
        self.assertIn("version", cols)
        self.assertIn("locked_at", cols)
        
        # Check Tracks
        c.execute("PRAGMA table_info(tracks)")
        cols = {row[1] for row in c.fetchall()}
        self.assertIn("output_version", cols)
        self.assertIn("output_override", cols)
        self.assertIn("pending_resync", cols)
        self.assertIn("state", cols) # Added by me
        
        # Check Script Edits
        c.execute("PRAGMA table_info(script_edits)")
        cols = {row[1] for row in c.fetchall()}
        self.assertIn("change_type", cols)

        conn.close()

if __name__ == "__main__":
    unittest.main()
