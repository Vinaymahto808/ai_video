"""Consent gate for voice cloning. Every step (audio, dataset, install) refuses to run without it.

A voice may only be cloned with the speaker's written permission for this use. Fill in consent/consent.json
(created from the template on first run) and put the signed document next to it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONSENT_DIR = HERE / "consent"
CONSENT_FILE = CONSENT_DIR / "consent.json"
TEMPLATE = {
    "speaker_name": "",
    "signed_on": "YYYY-MM-DD",
    "signed_document": "consent/<file name of the signed consent, e.g. consent_signed.pdf>",
    "permitted_use": "AI voice of the Hans Hyundai / Galaxy Toyota website and live assistant",
    "sources": ["<YouTube URL(s) or file(s) with this speaker's voice>"],
    "may_be_withdrawn": True,
    "notes": "Withdrawal: delete voice_clone/work/ and voice_models/clone/, and set PIPER_VOICE_* back in .env.",
}
REQUIRED = ("speaker_name", "signed_on", "signed_document", "permitted_use")


def require_consent() -> dict:
    """Return the consent record, or stop with instructions when it is missing or incomplete."""
    CONSENT_DIR.mkdir(parents=True, exist_ok=True)
    if not CONSENT_FILE.exists():
        CONSENT_FILE.write_text(json.dumps(TEMPLATE, indent=2), encoding="utf-8")
        sys.exit(f"Voice cloning needs the speaker's written consent.\n"
                 f"Fill in {CONSENT_FILE} and put the signed document in {CONSENT_DIR}, then run again.")
    c = json.loads(CONSENT_FILE.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if not c.get(k) or "<" in str(c[k]) or str(c[k]).startswith("YYYY")]
    if missing:
        sys.exit(f"{CONSENT_FILE}: fill in {', '.join(missing)}")
    doc = (HERE / c["signed_document"]).resolve()
    if not doc.exists():
        sys.exit(f"signed consent document not found: {doc}")
    return c
