"""Submit a service request to the Auto Car Repair website backend (the same endpoint its Contact form uses).

Found 2026-11-03 in the site's bundle: POST https://autocarrepair.in/api/v1/leads, multipart form-data with
name, phone, email, location, service, source and custom_data[make|model|registration|year]; the response
carries `lead_id`. GET /api/v1/vehicles/lookup resolves a registration number (make, model, fuel, insurer).

Modes (ACR_LEADS_MODE):
    dry   (default) nothing is sent; the request is logged and kept in the CRM outbox / conversation record,
          and Neha tells the customer the team will call (no reference number is invented).
    live  the request is POSTed and the real lead_id is spoken. Turn on only after the website developer has
          confirmed the field names and allowed the bot's server (ACR_LEADS_KEY if they issue one).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

LEADS_URL = os.getenv("ACR_LEADS_URL", "https://autocarrepair.in/api/v1/leads")
LOOKUP_URL = os.getenv("ACR_VEHICLE_LOOKUP_URL", "https://autocarrepair.in/api/v1/vehicles/lookup")
MODE = (os.getenv("ACR_LEADS_MODE") or "dry").lower()
SOURCE = os.getenv("ACR_LEADS_SOURCE", "neha_bot")
KEY = os.getenv("ACR_LEADS_KEY", "")
UA = "human_bot/1.0 (Neha service advisor)"
LOG = []  # last dry-run payloads (for /health on loopback)


class LeadError(RuntimeError):
    pass


MODEL_MAKE = {
    "swift": "Maruti Suzuki", "dzire": "Maruti Suzuki", "baleno": "Maruti Suzuki", "brezza": "Maruti Suzuki", "ertiga": "Maruti Suzuki",
    "wagonr": "Maruti Suzuki", "alto": "Maruti Suzuki", "celerio": "Maruti Suzuki", "ciaz": "Maruti Suzuki",
    "creta": "Hyundai", "venue": "Hyundai", "i20": "Hyundai", "i10": "Hyundai", "verna": "Hyundai", "exter": "Hyundai", "alcazar": "Hyundai", "aura": "Hyundai",
    "city": "Honda", "amaze": "Honda", "jazz": "Honda", "wr-v": "Honda",
    "innova": "Toyota", "fortuner": "Toyota", "glanza": "Toyota", "hyryder": "Toyota", "camry": "Toyota",
    "nexon": "Tata", "punch": "Tata", "harrier": "Tata", "safari": "Tata", "altroz": "Tata", "tiago": "Tata",
    "xuv700": "Mahindra", "xuv300": "Mahindra", "scorpio": "Mahindra", "thar": "Mahindra", "bolero": "Mahindra",
    "seltos": "Kia", "sonet": "Kia", "carens": "Kia", "hector": "MG", "astor": "MG", "kushaq": "Skoda", "slavia": "Skoda",
    "taigun": "Volkswagen", "polo": "Volkswagen", "kwid": "Renault", "triber": "Renault", "magnite": "Nissan", "ecosport": "Ford",
}


def mode() -> str:
    return "live" if MODE == "live" else "dry"


def payload(slots: dict, conversation_id: str, page_url: str = "") -> dict:
    from crm.service_request import SERVICE_SLUG  # noqa: PLC0415
    car = (slots.get("car") or "").split()
    # the collector often has only a model name ("Creta"): fill the make from the model, never guess the model
    if len(car) == 1 and car[0].lower() in MODEL_MAKE:
        car = [MODEL_MAKE[car[0].lower()], car[0]]
    fields = {
        "name": slots.get("name", ""),
        "phone": slots.get("phone", ""),
        "location": slots.get("area", ""),
        "service": SERVICE_SLUG.get(slots.get("problem", ""), slots.get("problem", "")),
        "source": SOURCE,
        "message": f"Via Neha voice assistant. {slots.get('problem', '')}; pickup: {slots.get('pickup', 'not asked')}; when: {slots.get('when', '')}. Conversation {conversation_id}. {page_url}".strip(),
        "custom_data[conversation_id]": conversation_id,
    }
    if car:
        fields["custom_data[make]"] = car[0]
        if len(car) > 1:
            fields["custom_data[model]"] = " ".join(car[1:])
    if slots.get("registration"):
        fields["custom_data[registration]"] = slots["registration"]
    if slots.get("email"):
        fields["email"] = slots["email"]
    return {k: v for k, v in fields.items() if v}


def _multipart(fields: dict) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def submit(slots: dict, conversation_id: str, page_url: str = "") -> dict:
    """-> {"mode", "lead_id"|None, "sent": bool}. Raises LeadError only in live mode on a hard failure."""
    fields = payload(slots, conversation_id, page_url)
    if mode() != "live":
        LOG.append({"t": time.time(), "fields": fields})
        del LOG[:-20]
        sys.stderr.write(f"[leads] dry-run (not sent): {json.dumps(fields, ensure_ascii=False)[:300]}\n")
        return {"mode": "dry", "lead_id": None, "sent": False, "fields": fields}
    body, ctype = _multipart(fields)
    headers = {"Content-Type": ctype, "Accept": "application/json", "User-Agent": UA}
    if KEY:
        headers["X-Api-Key"] = KEY
    req = urllib.request.Request(LEADS_URL, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as ex:
        raise LeadError(f"leads HTTP {ex.code}: {ex.read()[:200].decode('utf-8', 'ignore')}") from ex
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise LeadError(f"leads: {ex}") from ex
    lead_id = data.get("lead_id") or (data.get("data") or {}).get("lead_id")
    if not lead_id:
        raise LeadError(f"leads: no lead_id in response {json.dumps(data)[:200]}")
    return {"mode": "live", "lead_id": str(lead_id), "sent": True, "fields": fields}


def lookup_vehicle(registration: str) -> dict | None:
    """Make/model/fuel/insurer for a registration number (live mode only; the backend calls Vahan)."""
    if mode() != "live" or not registration:
        return None
    q = urllib.parse.urlencode({"registration": registration})
    req = urllib.request.Request(f"{LOOKUP_URL}?{q}", headers={"Accept": "application/json", "User-Agent": UA, **({"X-Api-Key": KEY} if KEY else {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception:  # noqa: BLE001
        return None
    d = data.get("data") or data
    return d if isinstance(d, dict) and (d.get("maker") or d.get("make") or d.get("model")) else None
