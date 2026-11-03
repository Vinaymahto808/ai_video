"""Create the Simli custom face from the prepared Neha portrait, poll it, and switch the bot to it.

    python tran_moment/simli_face.py generate            # upload prepared/face.png -> new face id (Trinity-2)
    python tran_moment/simli_face.py status [face_id]    # generation queue / processing / completed
    python tran_moment/simli_face.py use <face_id>       # SIMLI_FACE_ID=<id>, previous id becomes SIMLI_FACE_FALLBACK
    python tran_moment/simli_face.py list                # faces this account owns

Reads SIMLI_API_KEY from voice_clone/.env (never printed). One Trinity-2 generation is consumed per
`generate`; the plan's monthly quota is checked by Simli (HTTP 403 "no generations left" means buy or wait:
https://app.simli.com/billing/generations). Consent rule (tran_moment/CONSENT.md): only a synthetic or
consented portrait goes up; prepared/face.png is the AI-rendered Neha.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "voice_clone" / ".env"
PORTRAIT = ROOT / "tran_moment" / "prepared" / "face.png"
API = "https://api.simli.ai"


def env_get(key: str) -> str:
    m = re.search(rf"^{key}=(.*)$", ENV.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip().strip('"').strip("'") if m else ""


def env_set(key: str, value: str) -> None:
    s = ENV.read_text(encoding="utf-8")
    s, n = re.subn(rf"^{key}=.*$", f"{key}={value}", s, flags=re.M)
    if n == 0:
        s = s.rstrip("\n") + f"\n{key}={value}\n"
    ENV.write_text(s, encoding="utf-8")


def headers() -> dict:
    key = env_get("SIMLI_API_KEY")
    if not key:
        sys.exit("SIMLI_API_KEY missing in voice_clone/.env")
    return {"x-simli-api-key": key, "Accept": "application/json"}


def generate(name: str = "neha") -> str:
    with PORTRAIT.open("rb") as f:
        r = requests.post(f"{API}/faces/trinity", params={"face_name": name, "gsVersion": "GSA_2.0"},
                          headers=headers(), files={"image": (PORTRAIT.name, f, "image/png")}, timeout=300)
    if r.status_code != 200:
        sys.exit(f"generate failed: HTTP {r.status_code} {r.text[:300]}")
    d = r.json()
    print(json.dumps({k: d.get(k) for k in ("face_id", "message", "dispatch_after", "warnings")}, indent=2))
    return d["face_id"]


def status(face_id: str | None = None) -> dict:
    r = requests.get(f"{API}/faces/trinity/generation_status", params={"face_id": face_id} if face_id else None,
                     headers=headers(), timeout=30)
    d = r.json()
    print(json.dumps(d, indent=2))
    return d


def wait(face_id: str, max_s: int = 1800) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_s:
        d = status(face_id)
        st = d.get("status")
        if st == "completed":
            return True
        if st in ("failed", "not_found"):
            return False
        time.sleep(20)
    return False


def use(face_id: str) -> None:
    prev = env_get("SIMLI_FACE_ID")
    env_set("SIMLI_FACE_ID", face_id)
    if prev and prev != face_id:
        env_set("SIMLI_FACE_FALLBACK", prev)
    print(f"SIMLI_FACE_ID={face_id}" + (f"  (fallback now {prev})" if prev and prev != face_id else ""))
    print("restart human_bot to pick it up")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "generate":
        fid = generate(argv[2] if len(argv) > 2 else "neha")
        print("waiting for the generation (Ctrl-C is safe; re-run `status` later) ...")
        if wait(fid):
            use(fid)
        else:
            print("not completed yet; run: python tran_moment/simli_face.py status", fid)
    elif cmd == "status":
        status(argv[2] if len(argv) > 2 else None)
    elif cmd == "use":
        use(argv[2])
    elif cmd == "list":
        r = requests.get(f"{API}/faces", headers=headers(), timeout=30)
        print(json.dumps(r.json(), indent=2)[:3000])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
