"""Outbox + Bitrix24 mapping, without network.  python -m unittest human_bot.tests.test_crm_outbox  (from VIDEO/)"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crm.bitrix24 import Bitrix24Sink, _flatten  # noqa: E402
from crm.outbox import Outbox  # noqa: E402

RECORD = {"id": "abc123", "visitor_id": "v1", "started": 1_762_000_000.0, "turns": 2, "consent": 1, "phone": "9811223344", "name": "Rahul",
          "car": "Creta", "area": "Gurgaon", "problem": "AC repair", "intent": "ac", "lead": "WARM", "next_step": "pickup",
          "summary": "Creta, AC repair, in Gurgaon; 2 turns; next: pickup; lead WARM", "page_url": "https://autocarrepair.in/ac",
          "transcript": [{"role": "assistant", "text": "Namaste!"}, {"role": "user", "text": "AC thanda nahi"}], "audio_files": ["call.m4a"]}


class FlakySink:
    name = "fake"
    def __init__(self, fail_times=0):
        self.fail_times, self.calls = fail_times, []
    def push(self, record):
        self.calls.append(record["id"])
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("crm down")
        return {"id": "77", "action": "created"}


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "o.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def test_enqueue_is_idempotent_and_delivers_once(self):
        sink = FlakySink()
        ob = Outbox(self.db, sink, start_worker=False)
        self.assertTrue(ob.enqueue(RECORD))
        self.assertTrue(ob.enqueue(RECORD))        # refresh payload, still one row
        self.assertEqual(ob.process_once(), 1)
        self.assertEqual(sink.calls, ["abc123"])
        self.assertFalse(ob.enqueue(RECORD))       # already delivered: ignored
        self.assertEqual(ob.process_once(), 0)
        self.assertEqual(ob.status()["delivered"], 1)
        ob.close()

    def test_failure_backs_off_then_succeeds(self):
        sink = FlakySink(fail_times=1)
        ob = Outbox(self.db, sink, start_worker=False)
        ob.enqueue(RECORD)
        self.assertEqual(ob.process_once(), 0)      # first attempt fails
        st = ob.status()
        self.assertEqual(st["pending"], 1)
        self.assertIn("crm down", st["last_error"])
        self.assertEqual(ob.process_once(), 0)      # not due yet (backoff 30 s)
        ob.db.execute("UPDATE crm_outbox SET next_at=0")
        ob.db.commit()
        self.assertEqual(ob.process_once(), 1)      # retried and delivered
        ob.close()

    def test_queue_without_sink_keeps_rows(self):
        ob = Outbox(self.db, None, start_worker=False)
        ob.enqueue(RECORD)
        self.assertEqual(ob.process_once(), 0)
        self.assertEqual(ob.status()["pending"], 1)
        ob.close()


class Bitrix24MappingTests(unittest.TestCase):
    def test_lead_fields(self):
        f = Bitrix24Sink.lead_fields(RECORD)
        self.assertEqual(f["TITLE"], "ACR web call: Creta AC repair (Gurgaon)")
        self.assertEqual(f["PHONE"][0]["VALUE"], "9811223344")
        self.assertEqual(f["UF_CRM_CONVERSATION_ID"], "abc123")
        self.assertIn("Lead: WARM", f["COMMENTS"])
        self.assertIn("Neha: Namaste!", Bitrix24Sink.transcript_text(RECORD))

    def test_php_style_flattening(self):
        pairs = dict(_flatten({"fields": {"PHONE": [{"VALUE": "98", "VALUE_TYPE": "MOBILE"}], "TITLE": "t"}, "id": 5}))
        self.assertEqual(pairs["fields[PHONE][0][VALUE]"], "98")
        self.assertEqual(pairs["fields[TITLE]"], "t")
        self.assertEqual(pairs["id"], "5")


if __name__ == "__main__":
    unittest.main()
