"""Export stored conversation transcripts into JSONL for fine-tuning.
Run from VIDEO/: python export_transcripts.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "human_bot")

try:
    from memory.store import ConversationStore
except Exception as e:
    print(f"Could not import ConversationStore: {e}")
    print("Make sure you run this from VIDEO/ and human_bot/ is on the path.")
    sys.exit(1)


def main() -> None:
    store = ConversationStore()
    convs = store.list(limit=500)

    rows = []
    for c in convs:
        turns = c.get("turns") or c.get("transcript") or []
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Neha, a female Auto Car Repair service advisor in Delhi NCR. "
                    "Speak short, natural Hinglish. Ask one question per turn. Never invent prices. "
                    "End every turn with one question or one next step. Use \"ji\" and feminine verb endings "
                    "(karti hoon, deti hoon)."
                ),
            }
        ]
        for t in turns:
            role = t.get("role", "user")
            text = (t.get("text") or "").strip()
            if text:
                messages.append({"role": role, "content": text})
        if len(messages) > 3:
            rows.append({"messages": messages})

    out = Path("ft_data.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Exported {len(rows)} conversations to {out.resolve()}")


if __name__ == "__main__":
    main()
