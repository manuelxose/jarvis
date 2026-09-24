# voice_samples/

Local folder for your own audio. Everything here except this file is gitignored
(`*.wav`, `*.mp3`, …), so recordings never reach the repository.

Typical contents:

- A reference recording to import as the cloned voice:
  `python -m jarvis.voice_profile enroll --audio voice_samples\mi_voz.wav --text "transcripción exacta"`
  (or record directly with `python -m jarvis.voice_profile record`).
- A clean sample for the optional Alibaba cloud voice:
  `python scripts\alibaba_voice_clone.py create voice_samples\sample.wav`.
- Your startup music, referenced from `welcome.music_path` in `config.local.json`.

A good reference: 10–15 s of natural speech, quiet room, no clipping, mono,
16-bit, 16 kHz or more.
