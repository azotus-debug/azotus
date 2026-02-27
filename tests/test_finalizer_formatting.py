#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.finalizer.formatting import strip_metadata_tags


class TestStripMetadataTags(unittest.TestCase):
    def test_preserves_lowercase_t_characters(self):
        original = "Þetta er test text með mótspyrnu og stöðu."
        cleaned = strip_metadata_tags(original)
        self.assertEqual(cleaned, original)

    def test_repairs_tab_escaped_t_at_word_start(self):
        original = "Og \tilheyrir textinn."
        cleaned = strip_metadata_tags(original)
        self.assertEqual(cleaned, "Og tilheyrir textinn.")

    def test_repairs_tab_escaped_t_before_punctuation(self):
        original = "hal\ta, taktu stöðu."
        cleaned = strip_metadata_tags(original)
        self.assertEqual(cleaned, "halta, taktu stöðu.")

    def test_removes_metadata_but_keeps_words(self):
        original = "<MUSIC> Þetta er texti [APPLAUSE] (UPBEAT MUSIC)"
        cleaned = strip_metadata_tags(original)
        self.assertEqual(cleaned, "Þetta er texti")


if __name__ == "__main__":
    unittest.main()
