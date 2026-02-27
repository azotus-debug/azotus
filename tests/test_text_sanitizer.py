#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.text_sanitizer import (
    detect_likely_t_escape_corruption,
    repair_tab_escaped_t,
)


class TestRepairTabEscapedT(unittest.TestCase):
    def test_repairs_inline_tabs(self):
        self.assertEqual(repair_tab_escaped_t("be\ta"), "beta")

    def test_repairs_word_start_tabs(self):
        self.assertEqual(repair_tab_escaped_t("því \tilheyrir"), "því tilheyrir")

    def test_repairs_start_of_line_tabs(self):
        self.assertEqual(repair_tab_escaped_t("\tala"), "tala")

    def test_repairs_word_end_tabs(self):
        self.assertEqual(repair_tab_escaped_t("hal\ta."), "halta.")


class TestCorruptionDetection(unittest.TestCase):
    def test_detects_likely_t_escape_corruption(self):
        segments = [
            {
                "text": ("þe a er sýn min " * 160).strip(),
                "source_text": ("that is my son " * 160).strip(),
            }
        ]
        report = detect_likely_t_escape_corruption(
            segments,
            target_language="is",
            min_letters=200,
            min_t_ratio=0.01,
        )
        self.assertTrue(report["enabled"])
        self.assertTrue(report["suspicious"])

    def test_accepts_healthy_text(self):
        segments = [
            {
                "text": ("þetta er test texti " * 160).strip(),
                "source_text": ("that text is trusted " * 160).strip(),
            }
        ]
        report = detect_likely_t_escape_corruption(
            segments,
            target_language="is",
            min_letters=200,
            min_t_ratio=0.01,
        )
        self.assertTrue(report["enabled"])
        self.assertFalse(report["suspicious"])


if __name__ == "__main__":
    unittest.main()
