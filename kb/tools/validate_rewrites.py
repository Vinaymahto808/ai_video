"""Second gate on the LLM rewrites: a claim word (free, warranty, cashless, nahi karte...) that is in the
rewrite but not in the call note means the model added a promise; such rows fall back to the cleaned
note. Prints the verdicts and rewrites kb/seed/qa_rewrites.json in place."""
import json, re
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "seed" / "qa_rewrites.json"
CLAIMS = ["free", "complimentary", "muft", "warranty", "guarantee", "cashless", "tow", "discount", "genuine", "insurance",
          "nahi dete", "nahi karte", "nahi hota", "nahi hoti", "nahi milta", "nahi milti", "nahi hai", "24", "same day", "aaj hi",
          "pickup", "drop", "whatsapp", "emi", "refund", "replace", "original", "duplicate", "ola", "uber"]
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
c = json.loads(P.read_text(encoding="utf-8"))
kept = dropped = 0
for k, v in c.items():
    out, src = (v.get("text") or ""), v["src"]
    if not out:
        continue
    lo, ls = " " + out.lower() + " ", " " + src.lower() + " "
    added = [w for w in CLAIMS if f" {w}" in lo and f" {w}" not in ls]
    lost = [n for n in NUM_RE.findall(src) if n not in out]
    if added or lost:
        v["error"] = f"claims added {added}" if added else f"numbers lost {lost}"; v["rejected"] = out; v["text"] = ""; dropped += 1
    else:
        kept += 1
P.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"rewrites kept={kept} rejected={dropped}")
for k, v in c.items():
    if v.get("rejected"): print("-", k[:30], v["error"])
