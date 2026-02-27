#!/usr/bin/env python3
"""
Unit tests for finalizer text quality fixes.

Tests:
  - Phase 0: Context-aware capitalization (cross-subtitle, within-subtitle, mid-line)
  - Phase 1: Double-dots, Amen concatenation, name-bleed, comma-case
  - Phase 2: Ultra-flash removal
  - Phase 3: Consecutive duplicate removal
  - Phase 4: Worship keyword safety net

Run:
    /usr/bin/python3 -m pytest tests/test_finalizer_quality.py -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.finalizer import _text_quality_pass


def _ev(text, start=0.0, end=1.0, **kwargs):
    """Helper to create a minimal event dict."""
    ev = {"text": text, "start": start, "end": end}
    ev.update(kwargs)
    return ev


class TestCapitalization(unittest.TestCase):
    """Phase 0: Context-aware capitalization."""

    def test_cross_subtitle_continuation_lowercases(self):
        """Previous subtitle doesn't end sentence → current starts lowercase."""
        events = [
            _ev("fólk byrjaði", start=0, end=1),
            _ev("Að húðskamma mig fyrir", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[1]["text"], "að húðskamma mig fyrir")

    def test_after_sentence_end_capitalizes(self):
        """Previous subtitle ends with period → current starts uppercase."""
        events = [
            _ev("Amen.", start=0, end=1),
            _ev("góðan daginn.", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[1]["text"], "Góðan daginn.")

    def test_line2_continuation_lowercases(self):
        """Line 1 doesn't end sentence → line 2 starts lowercase."""
        events = [
            _ev("Amen.", start=0, end=1),  # sentence end → next capitalizes
            _ev("Guð vill breyta hjörtum okkar\nÁður en hann breytir.", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(
            result[1]["text"],
            "Guð vill breyta hjörtum okkar\náður en hann breytir.",
        )

    def test_line2_after_sentence_keeps_capital(self):
        """Line 1 ends with sentence punctuation → line 2 stays capitalized."""
        events = [
            _ev('Hann sagði: "Já."\nEn ég trúði ekki.', start=0, end=1),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[0]["text"], 'Hann sagði: "Já."\nEn ég trúði ekki.')

    def test_midline_spurious_capital_lowercases(self):
        """Mid-line capital after non-sentence-ending word → lowercase."""
        events = [
            _ev("Amen.", start=0, end=1),
            _ev("Var Og hve þungbært.", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[1]["text"], "Var og hve þungbært.")

    def test_midline_proper_noun_stays(self):
        """Mid-line proper noun stays capitalized."""
        events = [
            _ev("Amen.", start=0, end=1),
            _ev("Var Guð alltaf til?", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[1]["text"], "Var Guð alltaf til?")

    def test_after_closing_quote_period_capitalizes(self):
        """Previous ends with '."' → current capitalizes."""
        events = [
            _ev('Hann sagði: "Amen."', start=0, end=1),
            _ev("svo fórum við.", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[1]["text"], "Svo fórum við.")

    def test_first_subtitle_always_capitalizes(self):
        """First subtitle in the list always capitalizes."""
        events = [
            _ev("góðan daginn.", start=0, end=1),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[0]["text"], "Góðan daginn.")

    def test_cross_subtitle_with_comma(self):
        """Previous ends with comma → continuation lowercases."""
        events = [
            _ev("Ég man eftir að koma af spítalanum,", start=0, end=1),
            _ev("Í áttunda sinn.", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[1]["text"], "í áttunda sinn.")

    def test_allcaps_words_preserved(self):
        """All-caps words like ÉG ER are not lowercased."""
        events = [
            _ev("Amen.", start=0, end=1),
            _ev("ÉG ER sá sem ÉG ER.", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertIn("ÉG ER", result[1]["text"])


class TestDoubleDots(unittest.TestCase):
    """Phase 1: Double-dots → ellipsis."""

    def test_double_dot_becomes_ellipsis(self):
        events = [_ev("Hann sagði.. og svo.")]
        result = _text_quality_pass(events, "is")
        self.assertIn("...", result[0]["text"])
        self.assertNotIn("..", result[0]["text"].replace("...", ""))

    def test_triple_dot_unchanged(self):
        events = [_ev("Hann sagði... og svo.")]
        result = _text_quality_pass(events, "is")
        self.assertEqual(result[0]["text"], "Hann sagði... og svo.")


class TestAmenConcat(unittest.TestCase):
    """Phase 1: Amen concatenation."""

    def test_amen_before_capital_gets_period(self):
        """Amen followed by a capital letter (new sentence) → insert period."""
        # "Amen Hann fór" — Amen ends, new sentence starts with capital
        events = [
            _ev("Amen.", start=0, end=1),  # prev ends sentence
            _ev("Amen Hann fór heim.", start=1, end=3),
        ]
        result = _text_quality_pass(events, "is")
        self.assertTrue(result[1]["text"].startswith("Amen."))

    def test_amen_amen_at_sentence_start(self):
        """Amen Amen at start of subtitle after sentence end → period inserted."""
        events = [
            _ev("Guð er góður.", start=0, end=1),
            _ev("Amen Amen", start=1, end=2),
        ]
        result = _text_quality_pass(events, "is")
        self.assertIn("Amen.", result[1]["text"])


class TestCommaCase(unittest.TestCase):
    """Phase 1: Comma-case fix."""

    def test_lowercase_after_comma(self):
        events = [_ev("Sagði, Hann var þar.")]
        result = _text_quality_pass(events, "is")
        self.assertIn(", hann", result[0]["text"])

    def test_proper_noun_after_comma_stays(self):
        events = [_ev("Sagði, Guð var þar.")]
        result = _text_quality_pass(events, "is")
        self.assertIn(", Guð", result[0]["text"])


class TestUltraFlash(unittest.TestCase):
    """Phase 2: Ultra-flash removal."""

    def test_removes_ultraflash(self):
        events = [
            _ev("Normal subtitle.", start=0, end=2),
            _ev("Flash!", start=2, end=2.1),  # 0.1s — too short
            _ev("Another normal.", start=3, end=5),
        ]
        result = _text_quality_pass(events, "is")
        texts = [e["text"] for e in result]
        self.assertNotIn("Flash!", texts)
        self.assertEqual(len(result), 2)

    def test_keeps_normal_duration(self):
        events = [_ev("Normal.", start=0, end=1.5)]
        result = _text_quality_pass(events, "is")
        self.assertEqual(len(result), 1)


class TestDuplicateRemoval(unittest.TestCase):
    """Phase 3: Consecutive duplicate removal."""

    def test_removes_consecutive_duplicate(self):
        events = [
            _ev("Hallelúja.", start=0, end=2),
            _ev("Hallelúja.", start=2, end=4),
            _ev("Amen.", start=4, end=6),
        ]
        result = _text_quality_pass(events, "is")
        texts = [e["text"] for e in result]
        self.assertEqual(texts.count("Hallelúja."), 1)
        self.assertEqual(result[0]["end"], 4)  # Extended timing

    def test_non_consecutive_duplicates_kept(self):
        events = [
            _ev("Hallelúja.", start=0, end=2),
            _ev("Amen.", start=2, end=4),
            _ev("Hallelúja.", start=4, end=6),
        ]
        result = _text_quality_pass(events, "is")
        texts = [e["text"] for e in result]
        self.assertEqual(texts.count("Hallelúja."), 2)


class TestWorshipSafetyNet(unittest.TestCase):
    """Phase 4: Worship keyword safety net."""

    def test_worship_block_removed(self):
        """4+ consecutive worship keyword matches → removed."""
        events = [
            _ev("Verðugur varstu, Drottinn.", start=0, end=2),
            _ev("Verðugur ertu, Guð.", start=2, end=4),
            _ev("Verðugur varstu alltaf.", start=4, end=6),
            _ev("Verðugur ertu, kóngur.", start=6, end=8),
            _ev("Hann er prestur.", start=8, end=10),  # Not worship
        ]
        result = _text_quality_pass(events, "is")
        texts = [e["text"] for e in result]
        # 4 worship events removed, 1 sermon event kept
        self.assertEqual(len(result), 1)
        self.assertIn("prestur", texts[0])

    def test_isolated_keyword_not_removed(self):
        """1-2 keyword hits in isolation → NOT removed (below threshold)."""
        events = [
            _ev("Hann sagði: verðugur ertu.", start=0, end=2),
            _ev("Hann er prestur.", start=2, end=4),
            _ev("Hún er líka prestur.", start=4, end=6),
        ]
        result = _text_quality_pass(events, "is")
        self.assertEqual(len(result), 3)  # All kept


if __name__ == "__main__":
    unittest.main()
