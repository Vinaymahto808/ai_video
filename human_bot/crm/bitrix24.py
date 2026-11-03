"""Bitrix24 CRM sink: one lead per conversation, with the summary, the facts and the transcript.

Uses an *inbound webhook* (Bitrix24 → Developer resources → Other → Inbound webhook, scope `crm`), whose URL
looks like https://<portal>.bitrix24.in/rest/<user id>/<code>/ and carries the credential. Put it in
BITRIX24_WEBHOOK_URL (never in code, never logged).

Idempotency: before adding, the lead with UF_CRM_CONVERSATION_ID == conversation id is looked up; if the
field does not exist on the portal the lookup falls back to the phone number from the same day. A found
lead is updated, not duplicated. Nothing here ever reports success unless Bitrix24 returned a lead id.

Field mapping (CRM "Lead"):
    TITLE            "ACR web call: <car or 'car'> <problem or intent> (<area>)"
    NAME / PHONE     from the customer's own words (empty when not given; the lead is still created)
    SOURCE_ID        "WEB"            SOURCE_DESCRIPTION  page url
    COMMENTS         summary, intent, lead status, next step, link to the recording
    UF_CRM_CONVERSATION_ID (create this custom field once; string) = conversation id
    STATUS_ID        BITRIX24_LEAD_STATUS (default NEW)   ASSIGNED_BY_ID  BITRIX24_ASSIGNED_BY (optional)
Then crm.timeline.comment.add with the full transcript, so advisors read it on the lead card.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

PUBLIC_BASE = os.getenv("HUMAN_BOT_PUBLIC_URL", "").rstrip("/")  # for the recording link in COMMENTS


class Bitrix24Error(RuntimeError):
    pass


class Bitrix24Sink:
    name = "bitrix24"

    def __init__(self, webhook_url: str, status_id: str = "NEW", assigned_by: str = "", timeout: int = 20) -> None:
        self.url = webhook_url.rstrip("/") + "/"
        self.status_id = status_id or "NEW"
        self.assigned_by = assigned_by
        self.timeout = timeout

    # ---------------------------------------------------------------- REST
    def call(self, method: str, params: dict) -> dict:
        body = urllib.parse.urlencode(_flatten(params)).encode()
        req = urllib.request.Request(self.url + method + ".json", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as ex:
            detail = ex.read()[:300].decode("utf-8", "ignore")
            raise Bitrix24Error(f"{method} HTTP {ex.code}: {detail}") from ex
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            raise Bitrix24Error(f"{method}: {ex}") from ex
        if "error" in data:
            raise Bitrix24Error(f"{method}: {data.get('error')}: {data.get('error_description', '')[:200]}")
        return data

    # ---------------------------------------------------------------- mapping
    @staticmethod
    def lead_fields(rec: dict) -> dict:
        car = rec.get("car") or "car"
        topic = rec.get("problem") or (rec.get("intent") or "enquiry").replace("_", " ")
        area = f" ({rec['area']})" if rec.get("area") else ""
        comments = [
            f"Summary: {rec.get('summary') or ''}",
            f"Intent: {rec.get('intent') or ''}   Lead: {rec.get('lead') or ''}   Next step: {rec.get('next_step') or ''}",
            f"Turns: {rec.get('turns')}   Started: {time.strftime('%Y-%m-%d %H:%M', time.localtime(rec.get('started') or time.time()))}",
            f"Consent to record: {'yes' if rec.get('consent') else 'no'}",
        ]
        if PUBLIC_BASE and rec.get("audio_files"):
            merged = next((f for f in rec["audio_files"] if f.endswith(".m4a")), None)
            if merged:
                comments.append(f"Recording: {PUBLIC_BASE}/conversations/{rec['id']}/audio/{merged}")
        fields = {
            "TITLE": f"ACR web call: {car} {topic}{area}",
            "SOURCE_ID": "WEB",
            "SOURCE_DESCRIPTION": (rec.get("page_url") or "")[:250],
            "COMMENTS": "\n".join(comments)[:4000],
            "UF_CRM_CONVERSATION_ID": rec.get("id"),
            "OPENED": "Y",
        }
        if rec.get("name"):
            fields["NAME"] = rec["name"][:50]
        if rec.get("phone"):
            fields["PHONE"] = [{"VALUE": rec["phone"], "VALUE_TYPE": "MOBILE"}]
        return fields

    @staticmethod
    def transcript_text(rec: dict, limit: int = 9000) -> str:
        lines = []
        for t in rec.get("transcript") or []:
            who = "Customer" if t.get("role") == "user" else "Neha"
            lines.append(f"{who}: {t.get('text', '')}")
        text = "\n".join(lines)
        return text if len(text) <= limit else text[: limit - 20] + "\n[... truncated]"

    # ---------------------------------------------------------------- push
    def find_existing(self, rec: dict) -> str | None:
        try:
            r = self.call("crm.lead.list", {"filter": {"UF_CRM_CONVERSATION_ID": rec["id"]}, "select": ["ID"]})
            if r.get("result"):
                return str(r["result"][0]["ID"])
        except Bitrix24Error:
            pass  # field may not exist on this portal: fall through to the phone check
        if rec.get("phone"):
            since = time.strftime("%Y-%m-%dT00:00:00", time.localtime(rec.get("started") or time.time()))
            r = self.call("crm.lead.list", {"filter": {"PHONE": rec["phone"], ">=DATE_CREATE": since, "SOURCE_ID": "WEB"}, "select": ["ID", "TITLE"]})
            for lead in r.get("result") or []:
                if rec["id"] in (lead.get("TITLE") or "") or True:
                    return str(lead["ID"])
        return None

    def push(self, rec: dict) -> dict:
        fields = self.lead_fields(rec)
        if self.assigned_by:
            fields["ASSIGNED_BY_ID"] = self.assigned_by
        lead_id = self.find_existing(rec)
        if lead_id:
            self.call("crm.lead.update", {"id": lead_id, "fields": fields})
            action = "updated"
        else:
            fields["STATUS_ID"] = self.status_id
            r = self.call("crm.lead.add", {"fields": fields, "params": {"REGISTER_SONET_EVENT": "Y"}})
            lead_id = str(r.get("result") or "")
            if not lead_id:
                raise Bitrix24Error("crm.lead.add returned no id")
            action = "created"
        self.call("crm.timeline.comment.add", {"fields": {"ENTITY_ID": lead_id, "ENTITY_TYPE": "lead",
                                                           "COMMENT": "Neha web call transcript\n\n" + self.transcript_text(rec)}})
        return {"id": lead_id, "action": action}


def _flatten(params: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Bitrix24 expects PHP-style nested form keys: fields[PHONE][0][VALUE]=..."""
    out: list[tuple[str, str]] = []
    for k, v in params.items():
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, dict):
            out += _flatten(v, key)
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, (dict, list)):
                    out += _flatten({i: item}, key)
                else:
                    out.append((f"{key}[{i}]", str(item)))
        elif v is not None:
            out.append((key, str(v)))
    return out


def sink_from_env() -> Bitrix24Sink | None:
    url = os.getenv("BITRIX24_WEBHOOK_URL", "").strip()
    if not url:
        return None
    return Bitrix24Sink(url, status_id=os.getenv("BITRIX24_LEAD_STATUS", "NEW"), assigned_by=os.getenv("BITRIX24_ASSIGNED_BY", ""))
