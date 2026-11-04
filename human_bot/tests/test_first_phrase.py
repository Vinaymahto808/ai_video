"""First-clause streaming: the first TTS clip covers only the opening clause of the first sentence, so
audio starts while the model is still writing. The rest is flushed at normal sentence boundaries.
python -m unittest human_bot.tests.test_first_phrase  (from VIDEO/)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from persona.humanizer import split_first_phrase  # noqa: E402


class FirstPhraseTests(unittest.TestCase):
    def test_comma_splits_after_the_last_clause_inside_the_window(self):
        head, rest = split_first_phrase("Car service ka package 2500 se shuru hota hai, washing ke saath.")
        self.assertEqual(head, "Car service ka package 2500 se shuru hota hai,")
        self.assertEqual(rest, "washing ke saath.")

    def test_a_one_word_opener_is_not_a_clause(self):
        self.assertIsNone(split_first_phrase("Dekhiye,"))

    def test_a_joiner_starts_the_rest(self):
        head, rest = split_first_phrase("Pickup free hai aur washing bhi ho jayegi aapki")
        self.assertEqual(head, "Pickup free hai")
        self.assertEqual(rest, "aur washing bhi ho jayegi aapki")

    def test_a_finished_first_sentence_is_left_to_the_sentence_flush(self):
        self.assertIsNone(split_first_phrase("Package 2500 hai. Washing 300 extra."))

    def test_no_boundary_means_no_cut(self):
        self.assertIsNone(split_first_phrase("Car service ka package twenty five hundred se shuru hota hai"))

    def test_head_and_rest_reassemble_the_input(self):
        text = "Car service ka package 2500 se shuru hota hai, washing ke saath."
        head, rest = split_first_phrase(text)
        self.assertEqual((head + " " + rest).strip(), text)


if __name__ == "__main__":
    unittest.main()
