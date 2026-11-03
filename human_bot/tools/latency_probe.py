"""Latency probe for the hosted pair: per-step timings for one conversation, kb search, static assets,
then N concurrent conversations (session + chat first token + full reply). Prints p50/p95."""
import json, statistics, sys, threading, time, urllib.request, urllib.parse
B = sys.argv[1] if len(sys.argv) > 1 else "https://neha-bot-mkjv.onrender.com"
K = sys.argv[2] if len(sys.argv) > 2 else "https://neha-kb.onrender.com"
H = {"content-type": "application/json", "origin": B}
def post(path, body, timeout=90):
    return urllib.request.urlopen(urllib.request.Request(B + path, data=json.dumps(body).encode(), headers=H, method="POST"), timeout=timeout)
def timed(fn):
    t0 = time.perf_counter(); r = fn(); return (time.perf_counter() - t0) * 1000, r
def chat(sid, text):
    t0 = time.perf_counter(); r = post("/chat", {"session_id": sid, "text": text}); first = None; final = None
    for raw in r:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("data:"):
            if first is None: first = (time.perf_counter() - t0) * 1000
            d = json.loads(line[5:])
            if "chunks" in d: final = d
    return first, (time.perf_counter() - t0) * 1000, final
def one_conversation(texts, out):
    try:
        ms, r = timed(lambda: post("/session", {"persona": "auto-car-repair"})); s = json.loads(r.read()); sid = s["session_id"]
        rec = {"session_ms": ms, "chat_first_ms": [], "chat_total_ms": [], "provider": s.get("provider")}
        for t in texts:
            f, tot, final = chat(sid, t); rec["chat_first_ms"].append(f); rec["chat_total_ms"].append(tot)
            rec["offline"] = rec.get("offline", False) or bool(final and final.get("offline"))
        post("/end", {"session_id": sid}).read(); out.append(rec)
    except Exception as ex: out.append({"error": f"{type(ex).__name__}: {str(ex)[:80]}"})
def pct(v, p): v = sorted(v); return v[min(len(v) - 1, int(round((len(v) - 1) * p)))] if v else float("nan")
print("== single conversation")
ms, r = timed(lambda: urllib.request.urlopen(B + "/health", timeout=90)); print(f"health            {ms:7.0f} ms")
ms, r = timed(lambda: urllib.request.urlopen(B + "/embed?persona=auto-car-repair", timeout=90)); print(f"embed html        {ms:7.0f} ms  {len(r.read())//1024} KB")
ms, r = timed(lambda: urllib.request.urlopen(B + "/avatar_puppet/base.png?v=p", timeout=90)); print(f"base.png          {ms:7.0f} ms  {len(r.read())//1024} KB")
ms, r = timed(lambda: urllib.request.urlopen(K + "/api/knowledge/search?" + urllib.parse.urlencode({"website": "auto_car_repair", "q": "pickup free hai kya", "k": 3}), timeout=90)); d = json.loads(r.read()); print(f"kb search         {ms:7.0f} ms  mode={d.get('mode')} server={d.get('latency_ms')} ms")
ms, r = timed(lambda: urllib.request.urlopen(K + "/api/knowledge/search?" + urllib.parse.urlencode({"website": "auto_car_repair", "q": "pickup free hai kya", "k": 3}), timeout=90)); d = json.loads(r.read()); print(f"kb search (again) {ms:7.0f} ms  mode={d.get('mode')} server={d.get('latency_ms')} ms")
ms, r = timed(lambda: post("/session", {"persona": "auto-car-repair"})); s = json.loads(r.read()); sid = s["session_id"]; print(f"session           {ms:7.0f} ms  provider={s.get('provider')}")
chunk = (s.get("chunks") or [{}])[0]
t0 = time.perf_counter(); r = urllib.request.urlopen(B + "/tts?" + urllib.parse.urlencode({"text": chunk.get("text") or s["greeting"], "emo": chunk.get("emotion", "neutral"), "sid": sid}), timeout=90); fb = None; n = 0
while True:
    c = r.read(4096)
    if not c: break
    if fb is None: fb = (time.perf_counter() - t0) * 1000
    n += len(c)
print(f"tts first byte    {fb:7.0f} ms  total {(time.perf_counter()-t0)*1000:.0f} ms  {n//1024} KB")
for t in ["meri creta ki service karwani hai", "cashless insurance claim hota hai?"]:
    f, tot, final = chat(sid, t); print(f"chat first token  {f:7.0f} ms  total {tot:6.0f} ms  offline={bool(final and final.get('offline'))}  '{(final or {}).get('text','')[:50]}'")
post("/end", {"session_id": sid}).read()
for n in [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "3,6,10").split(",")]:
    out = []; ths = [threading.Thread(target=one_conversation, args=(["periodic service karwani hai", "creta h"], out)) for _ in range(n)]
    t0 = time.perf_counter(); [t.start() for t in ths]; [t.join() for t in ths]; wall = (time.perf_counter() - t0) * 1000
    ok = [o for o in out if "error" not in o]; errs = [o["error"] for o in out if "error" in o]
    firsts = [f for o in ok for f in o["chat_first_ms"] if f]; tots = [f for o in ok for f in o["chat_total_ms"]]; sess = [o["session_ms"] for o in ok]
    print(f"== {n} concurrent: ok={len(ok)} err={len(errs)} wall={wall:.0f} ms | session p50={pct(sess,.5):.0f} p95={pct(sess,.95):.0f} | first token p50={pct(firsts,.5):.0f} p95={pct(firsts,.95):.0f} | reply total p50={pct(tots,.5):.0f} p95={pct(tots,.95):.0f} | offline={sum(1 for o in ok if o.get('offline'))}/{len(ok)}")
    for e in errs[:3]: print("   err:", e)
    time.sleep(3)
