# Hans Hyundai (hans_hi) — Hindi voice dataset

Automated preview pehle se uthaye gaye steps ke liye, consent ke baad:
```
uv run python voice_clone/get_audio.py --file voice_clone/src/hans_video.mp4 --ranges "..." --name hans
uv run python voice_clone/make_dataset.py --voice hans_hi --language hi
```
- `wavs/`   — 2–10 s clips (`hans_hi_00001.wav` …)
- `metadata.csv` — `clip.wav|text` (Whisper transcript, clean-up ke baad)
- `dataset_info.json` — voice/language/speaker/consent info
- `hans_hi.zip` — Colab (`train_piper_colab.ipynb`) pe upload karne ke liye

> Note: konsa `<voice>` / source video Hans Hyundai ke liye use hoga — main wait kar
> raha hoon `consent/consent.json` (signed_on + signed document) ke liye, phir audio
> extract + dataset built hota hai.