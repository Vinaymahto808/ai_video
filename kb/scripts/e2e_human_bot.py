"""End-to-end: kb search API -> human_bot ACR persona -> one /chat turn with sources.

Run with both services up:
    cd kb && npm start                                    # :4100
    python human_bot/server/app.py --port 4003 --persona auto-car-repair
    python kb/scripts/e2e_human_bot.py "Gurgaon mein car AC repair karte ho? pickup milega?"
Exit 0 when the bot answered and cited at least one autocarrepair.in source.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

KB = "http://127.0.0.1:4100"
BOT = "http://127.0.0.1:4003"
Q = sys.argv[1] if len(sys.argv) > 1 else "Do you offer pickup and drop in Gurgaon, and is my warranty safe?"


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=120)


print("kb /health:", json.dumps(get(f"{KB}/health")["embedding"]))
hits = get(f"{KB}/api/knowledge/search?" + urllib.parse.urlencode({"website": "auto_car_repair", "q": Q, "k": 3}))
print(f"kb search ({hits['latency_ms']} ms):")
for h in hits["results"]:
    print(f"  [{h['score']}] {h['title']} — {h['url']}")

sess = json.loads(post(f"{BOT}/session", {"persona": "auto-car-repair"}).read())
print("bot greeting:", sess["greeting"], "| provider:", sess["provider"])

events, final, sources = [], None, []
with post(f"{BOT}/chat", {"session_id": sess["session_id"], "text": Q}) as r:
    ev = None
    for raw in r:
        line = raw.decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: ") and ev:
            data = json.loads(line[6:])
            events.append(ev)
            if ev == "sources":
                sources = data["results"]
            if ev == "final":
                final = data
            ev = None

print("events:", events)
print("sources:", [s["url"] for s in sources])
print("answer:", final["text"] if final else None)
ok = bool(final and final.get("text")) and any("autocarrepair.in" in (s.get("url") or "") for s in sources)
print("E2E", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
