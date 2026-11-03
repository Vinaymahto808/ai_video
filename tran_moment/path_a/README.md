# Path A — TalkingHead (CPU, visitor's browser)

MIT. Renders a Ready Player Me `.glb` with Three.js. No GPU on the server.

1. Build an avatar from `../face.jpg` at https://readyplayer.me and paste the GLB URL into `avatar.json`.
2. Serve this folder over HTTP (ES modules will not load from `file://`):

```powershell
cd C:\Users\Galaxy\Desktop\VIDEO\tran_moment
python -m http.server 8765
```

3. Open http://127.0.0.1:8765/path_a/
4. Use **Speak greeting** (browser TTS + viseme map), then try moods / nod / blink.

To wire into `galaxy-assistant.js` later: load TalkingHead on the same page, `showAvatar()` with this GLB, and on each reply call `head.speakAudio({ audio, visemes, vtimes, vdurations })` with the TTS buffer. Keep the existing voice-only fallback if WebGL fails or fps drops.
