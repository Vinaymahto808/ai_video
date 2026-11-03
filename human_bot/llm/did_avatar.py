"""D-ID real-time talking-head stream for the card (AIVA-class motion from the still Neha portrait).

Server-side only: the API key never reaches the browser. The card does the WebRTC handshake through
these helpers (start → sdp → ice → say → stop); D-ID renders the portrait speaking with a Microsoft
Indian female voice and streams video back to the card.

    DID_API_KEY=<key from studio.d-id.com → API keys>      (voice_clone/.env or environment)
    HUMAN_BOT_AVATAR_PROVIDER=did                            (turns it on; default off → puppet)
    HUMAN_BOT_DID_VOICE_EN=en-IN-NeerjaNeural  HUMAN_BOT_DID_VOICE_HI=hi-IN-SwaraNeural

D-ID Talks Streams reference: POST /talks/streams, /talks/streams/{id}/sdp, /{id}/ice, /{id}, DELETE.
Legacy-but-supported API; the Agents Streams API is the newer surface and maps 1:1 onto these calls.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.d-id.com"
KEY = os.getenv("DID_API_KEY") or os.getenv("did_api_key") or ""
PROVIDER = (os.getenv("HUMAN_BOT_AVATAR_PROVIDER") or "").lower()
VOICE_EN = os.getenv("HUMAN_BOT_DID_VOICE_EN", "en-IN-NeerjaNeural")
VOICE_HI = os.getenv("HUMAN_BOT_DID_VOICE_HI", "hi-IN-SwaraNeural")
PORTRAIT = Path(__file__).resolve().parents[2] / "tran_moment" / "prepared" / "face.jpg"
UA = "human_bot/1.0 (+avatar)"
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LOCK = threading.Lock()
_STATE: dict[str, object] = {"image_url": None, "last_error": None}


class AvatarUnavailable(RuntimeError):
    pass


def enabled() -> bool:
    return PROVIDER == "did"


def status() -> dict:
    if not enabled():
        return {"provider": "puppet", "ready": False, "note": "set HUMAN_BOT_AVATAR_PROVIDER=did to enable the D-ID stream"}
    if not KEY:
        return {"provider": "did", "ready": False, "note": "DID_API_KEY missing"}
    return {"provider": "did", "ready": True, "image": bool(_STATE["image_url"]), "last_error": _STATE["last_error"],
            "voices": {"en": VOICE_EN, "hi": VOICE_HI}}


def _auth() -> str:
    # D-ID keys are issued as "username:password"; the header is Basic <key>. If a key arrives without a
    # colon it is already base64 — send it as is.
    k = KEY.strip()
    return "Basic " + (base64.b64encode(k.encode()).decode() if ":" in k else k)


def _call(method: str, path: str, body: dict | None = None, raw: bytes | None = None, ctype: str | None = None) -> dict:
    if not KEY:
        raise AvatarUnavailable("DID_API_KEY missing")
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Authorization": _auth(), "User-Agent": UA, "Accept": "application/json",
                                          **({"Content-Type": ctype or "application/json"} if data is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", "ignore")
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "ignore").replace("\n", " ")
        _STATE["last_error"] = f"HTTP {e.code} {path}: {detail}"
        raise AvatarUnavailable(_STATE["last_error"]) from e
    except Exception as e:  # noqa: BLE001
        _STATE["last_error"] = f"{type(e).__name__}: {e}"
        raise AvatarUnavailable(_STATE["last_error"]) from e


def portrait_url() -> str:
    """Upload the Neha portrait once per process; D-ID needs an https source_url it can fetch."""
    with _LOCK:
        if _STATE["image_url"]:
            return str(_STATE["image_url"])
        if not PORTRAIT.is_file():
            raise AvatarUnavailable(f"portrait missing: {PORTRAIT}")
        boundary = "----hhbot" + os.urandom(8).hex()
        img = PORTRAIT.read_bytes()
        form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"face.jpg\"\r\n"
                f"Content-Type: image/jpeg\r\n\r\n").encode() + img + f"\r\n--{boundary}--\r\n".encode()
        res = _call("POST", "/images", raw=form, ctype=f"multipart/form-data; boundary={boundary}")
        url = res.get("url")
        if not url:
            raise AvatarUnavailable(f"image upload: no url in {json.dumps(res)[:200]}")
        _STATE["image_url"] = url
        return url


def start_stream() -> dict:
    """→ {id, session_id, offer, ice_servers} for the browser to answer."""
    res = _call("POST", "/talks/streams", {"source_url": portrait_url(), "stream_warmup": True,
                                          "output_resolution": 720, "compatibility_mode": "auto",
                                          "config": {"stitch": True}})
    offer = res.get("offer") or res.get("jsep")
    if not res.get("id") or not offer:
        raise AvatarUnavailable(f"stream start: unexpected response {json.dumps(res)[:200]}")
    return {"id": res["id"], "session_id": res.get("session_id"), "offer": offer, "ice_servers": res.get("ice_servers", [])}


def send_answer(stream_id: str, session_id: str, answer: dict) -> dict:
    return _call("POST", f"/talks/streams/{stream_id}/sdp", {"answer": answer, "session_id": session_id})


def send_ice(stream_id: str, session_id: str, candidate: dict) -> dict:
    body = {"session_id": session_id}
    for k in ("candidate", "sdpMid", "sdpMLineIndex"):
        if k in candidate:
            body[k] = candidate[k]
    return _call("POST", f"/talks/streams/{stream_id}/ice", body)


def say(stream_id: str, session_id: str, text: str, voice: str | None = None) -> dict:
    text = text.strip()
    if len(text) < 3:
        text = (text + "...")[:3]
    v = voice or (VOICE_HI if _DEVANAGARI.search(text) else VOICE_EN)
    return _call("POST", f"/talks/streams/{stream_id}", {
        "session_id": session_id,
        "script": {"type": "text", "input": text[:2000], "provider": {"type": "microsoft", "voice_id": v}, "ssml": False},
        "config": {"stitch": True, "fluent": True},
    })


def stop_stream(stream_id: str, session_id: str) -> dict:
    return _call("DELETE", f"/talks/streams/{stream_id}", {"session_id": session_id})
