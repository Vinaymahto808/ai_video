# Per-brand personas

Optional. Until you have three consented faces, every site can share `../face.jpg`.

```
brands/galaxy_toyota/face.jpg
brands/hans_hyundai/face.jpg
brands/autocarrepair/face.jpg
```

Then:

```powershell
uv run --with mediapipe python tran_moment\check_and_prepare.py --photo brands\hans_hyundai\face.jpg --out brands\hans_hyundai\prepared
```

Written consent (DPDP Act 2023) is required for a real staff likeness, same as the voice clone.
