"""The name a customer speaks alone is still captured: "Aapka naam kya hai?" -> "vinay".

Regression for 2026-11-04 (Render live): the customer answered the name question with just
"vinay", Neha replied "Maaf kijiye, main theek se samajh nahi paayi. Aapka naam kya hai?" and
the loop never ended — NAME_RE only knew "mera naam ...", and the phone asked as spoken words
("nine eight zero...") was invisible to the collector too.
python -m unittest human_bot.tests.test_bare_name  (from VIDEO/)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crm.service_request import collect, missing  # noqa: E402
from llm.client import offline_brain  # noqa: E402
from persona.prompting import load_persona  # noqa: E402

PERSONA = load_persona(Path(__file__).resolve().parents[1] / "persona" / "persona_auto_car_repair.json")


def transcript(*turns):
    out = []
    for role, text in turns:
        out.append({"role": role, "text": text})
    return out


NAME_Q = ("assistant", "Aapka naam kya hai?")
PHONE_Q = ("assistant", "Aapka mobile number bata dijiye, team usi par call karegi.")


class BareNameCollectorTests(unittest.TestCase):
    def test_bare_name_after_the_name_question(self):
        s = collect(transcript(NAME_Q, ("user", "vinay")))
        self.assertEqual(s["name"], "Vinay")

    def test_stt_case_mess_does_not_lose_the_name(self):
        for said in ("VInay", "VINAY"):
            with self.subTest(said=said):
                self.assertEqual(collect(transcript(NAME_Q, ("user", said)))["name"], "Vinay")

    def test_two_word_and_filler_forms(self):
        self.assertEqual(collect(transcript(NAME_Q, ("user", "vinay kumar")))["name"], "Vinay Kumar")
        self.assertEqual(collect(transcript(NAME_Q, ("user", "vinay hai")))["name"], "Vinay")

    def test_a_refusal_is_not_a_name(self):
        s = collect(transcript(NAME_Q, ("user", "nahi batana")))
        self.assertNotIn("name", s)

    def test_service_and_question_turns_are_not_names(self):
        s = collect(transcript(NAME_Q, ("user", "AC service karwana hai")))
        self.assertNotIn("name", s)
        s = collect(transcript(NAME_Q, ("user", "kya charges hain?")))
        self.assertNotIn("name", s)

    def test_no_name_question_means_no_bare_name(self):
        self.assertNotIn("name", collect(transcript(("assistant", "Aapki car ka model aur number kya hai?"),
                                                    ("user", "Swift"))))

    def test_full_flow_completes_with_spoken_digits(self):
        s = collect(transcript(
            ("assistant", "Aapki car ka model aur number kya hai?"), ("user", "Swift"),
            ("assistant", "Gaadi mein kya problem hai, ya kaunsi service karwani hai?"), ("user", "AC ka service karwana hai"),
            ("assistant", "Aap kis area mein hain, Gurgaon, Delhi ya Noida?"), ("user", "Gurgaon"),
            NAME_Q, ("user", "vinay"),
            PHONE_Q, ("user", "nine eight zero nine five five four four three three two"),
        ))
        self.assertEqual(missing(s), [])
        self.assertEqual(s["name"], "Vinay")
        self.assertEqual(s["phone"], "8095544332")


class BareNameBrainTests(unittest.TestCase):
    def test_brain_accepts_the_bare_name_and_moves_on(self):
        b = offline_brain(PERSONA)
        for turn in ("Swift gaadi hai", "DL 12 AB 4321", "AC ka service karwana hai", "Gurgaon"):
            b.reply(turn)
        self.assertIn("naam", b.reply("junk-noise-turn"))  # the name question is on the floor
        out = b.reply("vinay")
        self.assertNotIn("Maaf kijiye", out)  # the apology loop is gone
        self.assertIn("mobile number", out)
        self.assertEqual(b.slots["name"], "Vinay")

    def test_brain_does_not_invent_a_name_without_the_question(self):
        b = offline_brain(PERSONA)
        b.reply("vinay")  # the very first word, nothing asked yet
        self.assertNotIn("name", b.slots)


if __name__ == "__main__":
    unittest.main()
