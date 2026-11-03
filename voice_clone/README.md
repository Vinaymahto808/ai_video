# voice_clone — avatar ko ek asli insaan ki awaaz dena (sirf likhit permission ke saath)

Is folder se kisi **consent dene wale** insaan ki awaaz (YouTube video ya apni recording se) lekar ek Piper voice
fine-tune hoti hai. Phir website ka avatar aur live voice call usi awaaz mein bolte hain. Bolna is laptop ke CPU
par hota hai, training Google Colab ke free GPU par.

> **Permission zaroori hai.** Har script pehle `consent/consent.json` aur signed document check karti hai — uske
> bina kuch nahi chalta. Kisi celebrity, influencer ya anjaan insaan ki awaaz bina permission clone karna galat
> hai (aur kai jagah gair-kaanooni). YouTube se sirf wahi video lein jinhe use karne ka haq ho — apne channel ke
> video YouTube Studio → *Download* se seedhe mil jaate hain.

> **Awaaz Indian female honi chahiye (2026-10-14 ka faisla).** Assistant "Neha" hai — training/clone ke liye sirf
> ek consent dene wali **mahila** speaker ki recording lein; male awaaz train ya configure na karein. Live voice
> abhi **Gemini TTS `Kore`** hai (Delhi-NCR female, teeno brands ke liye) — detail aur audition clips:
> [`FEMALE_VOICE.md`](FEMALE_VOICE.md). `.env` mein `VOICE_CONVERT_SPEAKER` khaali hai; clone tab tak na lagayein
> jab tak `consent/consent.json` mein signed **mahila** speaker na ho. Neeche ke commands mein `neha` example naam hai.

## Steps

| # | Command | Kya hota hai |
|---|---|---|
| 0 | `uv run python voice_clone/get_audio.py --file x` (pehli baar) | `consent/consent.json` ka template banta hai — speaker ka naam, tareekh, use, signed document bharein |
| 1 | `uv run --with yt-dlp python voice_clone/get_audio.py --url "https://youtu.be/..." --ranges "00:30-04:10"` | audio → 22,050 Hz mono WAV, awaaz barabar; `--ranges` se sirf wo hisse jahan yahi insaan akela bol raha hai |
| 2 | `uv run python voice_clone/make_dataset.py --voice hans_hi --language hi` | Whisper transcript + 2–10 s clips + `metadata.csv` + `work/dataset/hans_hi.zip` |
| 3 | `train_piper_colab.ipynb` Colab mein | zip Drive par daalein, *Run all*; 1–2 din (disconnect par phir *Run all*, resume hota hai) |
| 4 | `uv run python voice_clone/install_voice.py --onnx "C:/Users/Galaxy/Downloads/hans_hi.onnx" --language hi` | `voice_models/clone/` mein copy, `.env` mein `PIPER_VOICE_HI`, test samples; server restart |

## Chhota sample (1–2 minute): OpenVoice V2 — training nahi

30 minute ki recording na ho to Piper fine-tune nahi hota. Tab **OpenVoice V2** (MIT, commercial OK) Piper ki
awaaz ko consented speaker ke tone mein badalta hai — is laptop ke CPU par, ek alag chhoti service ke roop mein
(`127.0.0.1:8020`). 30–60 second saaf awaaz kaafi hai.

Ek baar setup (ho chuka hai: `tools/openvoice-venv`, converter model, `voice_models/clone/piper_source.pt`):

```powershell
uv venv tools/openvoice-venv --python 3.10
uv pip install --python tools/openvoice-venv/Scripts/python.exe "torch==2.4.1" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python tools/openvoice-venv/Scripts/python.exe --no-deps "git+https://github.com/myshell-ai/OpenVoice.git"
uv pip install --python tools/openvoice-venv/Scripts/python.exe "numpy==1.23.5" "librosa==0.9.1" soundfile "huggingface_hub<1" unidecode "inflect==7.0.0" eng_to_ipa jieba pypinyin cn2an langid "setuptools<70"
uv run python voice_clone/make_piper_samples.py
tools\openvoice-venv\Scripts\python.exe -W ignore voice_clone\clone_speaker.py --source
```

Speaker (consent ke baad):

```powershell
uv run python voice_clone/get_audio.py --file voice_clone/src/sample_audio.mp4 --ranges "00:56-01:28" --name neha
tools\openvoice-venv\Scripts\python.exe -W ignore voice_clone\clone_speaker.py --name neha --wav voice_clone\work\audio\neha.wav
```

Chalana: `.env` mein `VOICE_CONVERT_URL=http://127.0.0.1:8020` aur `VOICE_CONVERT_SPEAKER=neha`, phir
`voice_clone\start_voice_service.ps1` (alag window) aur assistant restart. Service band ho to assistant apne aap
Piper ki normal awaaz use karta hai.

Sirf apni ek saaf recording se (10–60 second, ek hi insaan, bina background music) — YouTube ki zarurat nahi:

```powershell
uv run python voice_clone/get_audio.py --file "C:/path/my_voice.wav" --name neha
tools\openvoice-venv\Scripts\python.exe -W ignore voice_clone\clone_speaker.py --name neha --wav voice_clone\work\audio\neha.wav
```

Verify (Phase 1 acceptance — consent, embedding, service, ek asli conversion):

```powershell
uv run python voice_clone/verify_clone.py --speaker neha
```

Chaaron check pass hone par exit code `0`. Service kabhi band ho jaye to jawab nahi girta — assistant plain Piper
par chalta rehta hai (`tests/test_voice_clone.py::test_voice_conversion_and_fallback`).

Test (2026-10-10, synthetic awaazon se, kisi insaan ki nahi): conversion ke baad speaker-similarity 0.38 → **0.93**;
~3 s awaaz ~4.4 s mein badli (laptop par sirf 0.4 GB RAM khaali thi — RAM khaali ho to tez). Har jawab mein
itna samay judta hai.

## Achhi awaaz ke liye

* **Kam se kam 30 minute** saaf bolna (60 minute aur behtar). 10 minute se voice kharab hi banegi.
* Sirf **ek** insaan bolta ho — interviewer, audience, doosri awaazein dataset se bahar (`--ranges`).
* **Background music nahi** — music wale video mat lijiye; music awaaz ka hissa ban jaata hai.
* Step 2 ke baad `work/dataset/<voice>/metadata.csv` padhiye aur kuch clips suniye; galat transcript wali lines
  (aur unki wav) hata dijiye.
* Hindi/Hinglish ke liye `--language hi`, English ke liye `--language en` — dono alag voices hain
  (`PIPER_VOICE_HI`, `PIPER_VOICE_EN`).

## Permission wapas lena

Speaker kabhi bhi permission wapas le sakta hai: `voice_clone/work/` aur `voice_models/clone/` delete karein, `.env`
mein `PIPER_VOICE_HI` / `PIPER_VOICE_EN` purani voice par set karein, server restart karein.

## Kya verify hua, kya nahi

* Scripts ke hisse (consent gate, time ranges, clip grouping) tests mein: `tests/test_voice_clone.py`.
* Cloned voice ki acceptance check: `voice_clone/verify_clone.py` — consent → embedding → service → ek asli
  conversion (aur service band hone par plain Piper fallback test se prove hota hai).
* **Colab training is laptop par nahi chali** (NVIDIA GPU nahi hai) — commands piper1-gpl ki TRAINING.md ke hisaab
  se hain; koi cell fail ho to error message aksar missing package / option batata hai.
* Asli speaker ke audio par Step 1–2 tab chalenge jab aap consent aur video denge.

## Licences

| Hissa | Licence |
|---|---|
| Speaker ki awaaz / trained voice | speaker ki — consent ke hisaab se use |
| LJSpeech base checkpoint | dataset public domain |
| piper1-gpl training code | GPL-3.0 (sirf Colab par model banane ke liye) |
| faster-whisper, yt-dlp, ffmpeg (imageio-ffmpeg) | MIT, Unlicense, LGPL/GPL build — sirf taiyaari ke liye |
