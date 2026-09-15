# Jarvis v2 legacy extraction

## Retain as requirement

- Windows-oriented Python launch and device baseline, evidenced by `README.md`, `run_jarvis.ps1`, `setup.py`, and `main.py`.
- Voice capabilities: wake-word activation, microphone/device handling, silence/VAD-style recording, STT, TTS, and audio playback, evidenced by `voice/wake_word.py`, `voice/audio_utils.py`, `voice/stt.py`, and `voice/tts.py`.
- Audio-cache behavior and setup/diagnostic support, evidenced by `cache/audio_cache.py`, `setup.py`, `diagnostico_wakeword.py`, and `monitor_status.py`.
- Deterministic PC, AIS, trading-monitor, and web-search action domains, evidenced by `actions/pc_control.py`, `actions/ais_monitor.py`, `actions/trading_monitor.py`, `actions/web_search.py`, and `brain/action_router.py`.

## Retain as evidence only

- `main.py` composes configuration/logging, wake word, audio utilities, STT, TTS, Ollama, JSON memory, audio cache, prompt building, and action routing. Graphify verifies imports and the `build_runtime_components()` composition point.
- Legacy local conversation uses `brain/llm.py` (`OllamaClient`) and `brain/memory.py` (`MemoryStore`), which is persistent JSON memory for conversations and facts.
- Tracked voice samples exist as legacy assets; they remain in place as reference and were not read.
- No tracked automated application test files were found; the tracked test/config inventory contains `requirements.txt` and `setup.py`, but no `test*` or `*_test.py` application files.

## Replace in v2

- Replace the legacy `main.py` composition with the v2 lifecycle-owned runtime, bounded typed messages, and supervised managed Hermes child-process boundary.
- Do not treat current Ollama, STT, TTS, wake-word, VAD/audio, memory, or action-provider choices as v2 defaults. Provider selection follows the hybrid cloud-first benchmark and capability/fallback contracts.
- Replace JSON conversation-memory design with local, user-controlled v2 memory controls and the initial SQLite FTS5 retrieval path.
- Do not copy private logs, binary audio, voice recordings, conversation data, memory databases, secrets, or environment values into the rebuild.

## Not yet verified

- Actual installed microphone, speaker/headphone, network, and configured-provider performance on the target Windows hardware.
- Latency and quality of candidate cloud and local providers, including `speech_end_to_first_audio_ms` p50/p95.
- Hermes adapter protocol, child-process command, health/restart implementation, and degraded-mode behavior.
- Which legacy actions remain product requirements after v2 Tool Gateway permissions and confirmation policy are implemented.
