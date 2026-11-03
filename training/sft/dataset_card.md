# SFT dataset card

- source: `training\conversations.jsonl`
- kept: **14** conversations (train 13 / val 1)
- dropped: 0 unparseable, 0 duplicate
- avg assistant turn: **35.6 words** (target 12-18 for spoken replies)
- turns over budget: **26.7%**
- style flags: {"too_long": 4, "written_money": 5, "no_terminal_punct": 1}

## Read this before training
14 examples is a *prompt-tuning* set, not a fine-tuning set. At this size, LoRA will
memorise the 14 conversations and get worse at everything else. Use this set to:
  1. validate that the style contract in `persona/prompting.py` produces the replies you want;
  2. build the eval rubric;
  3. seed a bigger collection run (real calls / simulated customers).
Fine-tune only after ~300-1000 real or high-fidelity transcripts, and always hold out 10%
of *customers*, not turns, so you measure generalisation.
