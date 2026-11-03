# Path B — 2D photo avatar (CPU)

MIT. Uses the real `face.jpg`, not a 3D character.

```powershell
cd C:\Users\Galaxy\Desktop\VIDEO
uv run --with mediapipe python tran_moment\check_and_prepare.py
uv run python tran_moment\path_b\generate_mouths.py
# optional, better timings:
.\tran_moment\path_b\install_rhubarb.ps1
.\tran_moment\path_b\make_sample_wav.ps1
uv run python tran_moment\path_b\generate_visemes.py --wav sample.wav --text (Get-Content tran_moment\path_b\sample_line.txt -Raw)
```

Then:

```powershell
cd C:\Users\Galaxy\Desktop\VIDEO\tran_moment
python -m http.server 8765
```

http://127.0.0.1:8765/path_b/overlay.html

To use this portrait in the **existing** widget canvas (no mouth PNGs, landmark warp already in `galaxy-assistant.js`):

```powershell
uv run python tran_moment\apply_to_widget.py --env
```

Restart `.\run.ps1`. Cache viseme JSON under `cache/` for fixed greetings so live turns stay cheap.
