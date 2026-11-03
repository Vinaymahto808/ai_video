"""Simli real-time lip-synced avatar for the card (audio-driven: our TTS speaks, Simli renders the face).

Server-side only: the API key is exchanged for a short-lived session token which the browser uses with
the `simli-client` SDK (WebRTC). Free tier: 50 min/month, template faces; a custom face from the Neha
portrait needs the Hobby plan (then set SIMLI_FACE_ID to that face).

    SIMLI_API_KEY=<key from app.simli.com>           (voice_clone/.env or environment)
    HUMAN_BOT_AVATAR_PROVIDER=simli                   (turns it on; "did" = D-ID; unset = puppet)
    SIMLI_FACE_ID=<face id>                           (template face; see GET /avatar/faces)

Endpoints used: POST https://api.simli.ai/compose/token  (x-simli-api-key → session_token)
                GET  https://api.simli.ai/faces           (faces visible to this key)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = "https://api.simli.ai"
KEY = os.getenv("SIMLI_API_KEY") or os.getenv("simli_api_key") or ""
PROVIDER = (os.getenv("HUMAN_BOT_AVATAR_PROVIDER") or "").lower()
# Simli's public demo face used throughout their docs/examples; replace with a template or custom face id.
FACE_ID = os.getenv("SIMLI_FACE_ID", "tmp9i8bbq7c")
MAX_SESSION_S = int(os.getenv("SIMLI_MAX_SESSION_S", "600"))
MAX_IDLE_S = int(os.getenv("SIMLI_MAX_IDLE_S", "120"))
UA = "human_bot/1.0 (+avatar)"
_STATE: dict[str, object] = {"last_error": None, "sessions": 0}


class AvatarUnavailable(RuntimeError):
    pass


def enabled() -> bool:
    return PROVIDER == "simli"


def status() -> dict:
    if not enabled():
        return {"provider": "puppet", "ready": False, "note": "set HUMAN_BOT_AVATAR_PROVIDER=simli to enable the Simli stream"}
    if not KEY:
        return {"provider": "simli", "ready": False, "note": "SIMLI_API_KEY missing"}
    return {"provider": "simli", "ready": True, "face_id": FACE_ID, "sessions": _STATE["sessions"], "last_error": _STATE["last_error"]}


def _call(method: str, path: str, body: dict | None = None) -> dict | list:
    if not KEY:
        raise AvatarUnavailable("SIMLI_API_KEY missing")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"x-simli-api-key": KEY, "User-Agent": UA, "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if data is not None else {})})
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


FACE_FALLBACK = os.getenv("SIMLI_FACE_FALLBACK", "")  # used while a custom face is still processing / invalid


def _token(fid: str) -> dict:
    return _call("POST", "/compose/token", {
        "faceId": fid, "apiVersion": "v2", "handleSilence": True,
        "maxSessionLength": MAX_SESSION_S, "maxIdleTime": MAX_IDLE_S, "audioInputFormat": "pcm16",
    })


def start_stream(face_id: str | None = None) -> dict:
    """→ {provider:'simli', session_token, face_id}; the browser connects with simli-client."""
    fid = (face_id or FACE_ID).strip()
    try:
        res = _token(fid)
    except AvatarUnavailable as ex:
        if "INVALID_FACE_ID" in str(ex) and FACE_FALLBACK and FACE_FALLBACK != fid:
            fid = FACE_FALLBACK            # e.g. the custom Neha face is still generating → template face
            res = _token(fid)
        else:
            raise
    tok = res.get("session_token") if isinstance(res, dict) else None
    if not tok or tok == "FAIL TOKEN":
        raise AvatarUnavailable(f"compose/token: {json.dumps(res)[:200]}")
    _STATE["sessions"] = int(_STATE["sessions"]) + 1
    _STATE["last_error"] = None
    return {"provider": "simli", "session_token": tok, "face_id": fid, "warnings": res.get("warnings", [])}


def faces() -> list:
    res = _call("GET", "/faces")
    return res if isinstance(res, list) else res.get("faces", [])
