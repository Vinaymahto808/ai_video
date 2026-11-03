"""Prompt contract: every persona renders the call-flow, capture, boundary and tailoring blocks.
python -m unittest human_bot.tests.test_prompting  (from VIDEO/)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from persona.humanizer import wants_detail  # noqa: E402
from persona.prompting import build_compact_prompt, build_system_prompt, load_persona  # noqa: E402

PERSONA_DIR = Path(__file__).resolve().parents[1] / "persona"
PERSONAS = {p.stem: load_persona(p) for p in sorted(PERSONA_DIR.glob("persona_*.json"))}


class PromptContractTests(unittest.TestCase):
    def test_every_persona_has_the_new_blocks(self):
        self.assertGreaterEqual(len(PERSONAS), 3)
        for name, p in PERSONAS.items():
            with self.subTest(persona=name):
                for section, key in (("conversation", "visitor_types"), ("conversation", "max_words_detail"),
                                     ("sales", "flow"), ("sales", "capture_fields"), ("sales", "confirm_line"),
                                     ("guardrails", "out_of_scope_line"), ("guardrails", "competitors"),
                                     ("guardrails", "never_invent"), ("guardrails", "handoff_unavailable_line")):
                    self.assertIn(key, p[section], f"{name}: {section}.{key} missing")
                self.assertGreaterEqual(len(p["conversation"]["visitor_types"]), 4)
                self.assertGreaterEqual(len(p["sales"]["flow"]), 5)
                phone = next(f for f in p["sales"]["capture_fields"] if f["field"] == "phone")
                self.assertIn("only if", phone["when"])  # phone is asked only for a call-back / pickup / test drive

    def test_system_prompt_renders_every_block(self):
        for name, p in PERSONAS.items():
            with self.subTest(persona=name):
                t = build_system_prompt(p)
                for heading in ("## Who is on the call", "## Call flow", "## Capture and confirm",
                                "## Handoff and boundaries"):
                    self.assertIn(heading, t)
                self.assertIn("11. End every turn with either one question or one concrete next step", t)
                self.assertIn("12. Never invent", t)
                self.assertIn(p["guardrails"]["out_of_scope_line"], t)
                self.assertIn(p["guardrails"]["handoff_unavailable_line"], t)
                self.assertIn(p["sales"]["confirm_line"], t)
                for vt in p["conversation"]["visitor_types"]:
                    self.assertIn(vt["example"], t)
                for i, step in enumerate(p["sales"]["flow"], 1):
                    self.assertIn(f"{i}. {step}", t)
                self.assertIn(f"up to {p['conversation']['max_words_detail']} words", t)
                # she speaks for the dealer / workshop, never for a vendor
                self.assertNotIn("DaveAI", t)
                self.assertIn(p["company"], t)

    def test_blocks_are_optional_for_a_minimal_persona(self):
        p = dict(PERSONAS["persona_galaxy_toyota"])
        p["conversation"] = {k: v for k, v in p["conversation"].items() if k not in ("visitor_types", "max_words_detail")}
        p["sales"] = {k: v for k, v in p["sales"].items() if k not in ("flow", "capture_fields", "confirm_line")}
        p["guardrails"] = {k: v for k, v in p["guardrails"].items()
                           if k not in ("out_of_scope_line", "competitors", "never_invent", "handoff_unavailable_line")}
        t = build_system_prompt(p)
        self.assertNotIn("## Who is on the call", t)
        self.assertNotIn("## Call flow", t)
        self.assertNotIn("## Capture and confirm", t)
        self.assertIn("up to 70 words", t)  # default detail budget
        self.assertIn("Never invent prices, offers, guarantees", t)

    def test_compact_prompt_stays_small(self):
        for name, p in PERSONAS.items():
            with self.subTest(persona=name):
                t = build_compact_prompt(p)
                self.assertLess(len(t.split()), 140)
                self.assertIn("Never invent", t)
                self.assertIn("Competitors: stay neutral", t)


class DetailDetectorTests(unittest.TestCase):
    def test_detail_requests(self):
        for s in ("explain in detail", "thoda detail mein batao", "process kya hai poora", "samjhao na kaise hota hai",
                  "Can you elaborate?", "step by step batao"):
            self.assertTrue(wants_detail(s), s)

    def test_ordinary_turns(self):
        for s in ("", "Creta ka price?", "AC thanda nahi kar raha", "haan ji", "pickup Gurgaon se"):
            self.assertFalse(wants_detail(s), s)


if __name__ == "__main__":
    unittest.main()
