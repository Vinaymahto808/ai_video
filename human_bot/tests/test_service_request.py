"""Service-request slot collection, prompt block and confirmation logic (no network).
    python human_bot/tests/test_service_request.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crm import acr_leads  # noqa: E402
from crm.service_request import asked_confirmation, collect, is_no, is_yes, missing, prompt_block, summary_line, wants_request  # noqa: E402


def turns(*texts):
    return [{"role": "user" if i % 2 == 0 else "assistant", "text": t} for i, t in enumerate(texts)]


class CollectTests(unittest.TestCase):
    def test_collects_facts_from_customer_words_only(self):
        tr = turns("meri creta ka ac thanda nahi kar raha, gurgaon mein hoon", "Aapka naam?", "mera naam Rahul hai", "number?",
                   "9811223344", "gaadi number?", "DL 01 AB 1234, kal subah pickup kar lo")
        s = collect(tr)
        self.assertEqual(s["car"], "Creta"); self.assertEqual(s["problem"], "AC repair"); self.assertEqual(s["area"], "Gurgaon")
        self.assertEqual(s["name"], "Rahul"); self.assertEqual(s["phone"], "9811223344"); self.assertEqual(s["registration"], "DL01AB1234")
        self.assertEqual(s["when"], "kal"); self.assertEqual(s["pickup"], "yes")
        self.assertEqual(missing(s), [])
        self.assertIn("Creta", summary_line(s))

    def test_assistant_words_are_not_evidence(self):
        tr = turns("service karwani hai", "Aapki Creta ki service, Gurgaon se?")
        s = collect(tr)
        self.assertNotIn("car", s); self.assertNotIn("area", s)
        self.assertEqual(missing(s), ["name", "phone", "car", "problem", "area"][:5] if "problem" not in s else ["name", "phone", "car", "area"])

    def test_wants_request(self):
        self.assertTrue(wants_request(turns("brake se awaaz aa rahi hai"), collect(turns("brake se awaaz aa rahi hai"))))
        self.assertFalse(wants_request(turns("aap kahan ho"), {}))


class PromptAndConfirmTests(unittest.TestCase):
    def test_prompt_block_asks_one_missing_thing(self):
        b = prompt_block({"car": "Swift", "problem": "periodic service"}, False)
        self.assertIn("ask ONLY the first one", b); self.assertIn("aapka naam", b); self.assertIn("Never send the customer to the website form", b)

    def test_prompt_block_asks_for_confirmation_when_complete(self):
        full = {"name": "Rahul", "phone": "9811223344", "car": "Creta", "problem": "AC repair", "area": "Gurgaon"}
        self.assertIn("Main yeh request bhej doon?", prompt_block(full, False))
        self.assertIn("already asked", prompt_block(full, True))

    def test_yes_no_and_confirmation_detection(self):
        self.assertTrue(is_yes("haan bhej do")); self.assertTrue(is_yes("ok")); self.assertFalse(is_yes("nahi, number galat hai"))
        self.assertTrue(is_no("nahi ruko")); self.assertFalse(is_no("haan"))
        self.assertTrue(asked_confirmation("Rahul ji, Creta ki AC repair, Gurgaon se. Main yeh request bhej doon?"))
        self.assertFalse(asked_confirmation("Aapka number kya hai?"))


class LeadsPayloadTests(unittest.TestCase):
    def test_dry_mode_never_sends_and_maps_fields(self):
        slots = {"name": "Rahul", "phone": "9811223344", "car": "Hyundai Creta", "problem": "AC repair", "area": "Gurgaon", "registration": "DL01AB1234"}
        self.assertEqual(acr_leads.mode(), "dry")
        res = acr_leads.submit(slots, "conv123", "https://autocarrepair.in/ac")
        self.assertFalse(res["sent"]); self.assertIsNone(res["lead_id"])
        f = res["fields"]
        self.assertEqual(f["service"], "car-ac-service-repair"); self.assertEqual(f["custom_data[make]"], "Hyundai"); self.assertEqual(f["custom_data[model]"], "Creta")
        self.assertEqual(f["custom_data[registration]"], "DL01AB1234"); self.assertEqual(f["source"], "neha_bot"); self.assertEqual(f["custom_data[conversation_id]"], "conv123")


if __name__ == "__main__":
    unittest.main()
