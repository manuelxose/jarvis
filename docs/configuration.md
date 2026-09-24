# Configuration

Jarvis reads one JSON file (`--config`, default `config.json`) and merges a sibling
`config.local.json` over it when present. The whole document is validated at load
time; a wrong type or an unknown key in a validated section fails with a precise
message.

| File | Versioned | Purpose |
|---|---|---|
| `config.json` | yes | Minimal secret-free base: local Ollama only. Used by CI and non-Windows runs. |
| `config.win.json` | yes | Full Windows setup: Whisper on CUDA, cloned voice, claps, workspaces. |
| `config.local.example.json` | yes | Template for cloud providers. |
| `config.local.json` | **no** (gitignored) | Your overrides: cloud providers, music file, voice ids. |

**Secrets** are never written in any file. A value of the form `"${NAME}"` is read
from the environment variable `NAME` at load time, and secrets never appear in
`repr`, logs, `doctor` output or reports.

## Core sections

| Section | Keys (default) |
|---|---|
| `runtime` (required) | `command_deadline_ms` (500): deadline of a fast command. |
| `memory` | `db_path` (`memory/jarvis.db`), `max_recall` (6): memories injected per turn. |
| `audio` | `sample_rate` (16000), `channels` (1), `input_device` / `output_device` (null = system default), `barge_in` (false: enable only with a headset, there is no echo cancellation). |
| `activation` | `mode` (`wake_word` \| `push_to_talk` \| `manual` \| `continuous`), `wake_word` (`jarvis`), `cooldown_seconds` (1.2), `conversation_timeout_seconds` (8: follow-up window without the wake word). |
| `stt` | `provider` (`whisper` \| `sapi` \| `alibaba_qwen`), `model`, `language` (`es`), `device` (`cpu` \| `cuda`), `api_key`. |
| `tts` | `provider` (`qwen_clone` \| `sapi` \| `alibaba_qwen` \| `local` = Coqui XTTS), `voice`, `language`, `api_key`; for `qwen_clone`: `worker_python`, `profile_dir`, `model`, `chunk_size` (4), `warmup_wait_seconds` (0: how long a reply waits for a still-loading clone before using SAPI). |
| `alibaba` | `region` (`singapore` \| `beijing`), `workspace_id`, `stt_model`, `tts_model`. See [alibaba-qwen.md](alibaba-qwen.md). |
| `models` | `providers`: ordered list, the first is primary; `max_daily_usd` (1.0): cloud spend cap per day. |
| `hermes` | `command` (default: bundled Ollama agent child), `timeout_seconds` (300), `restart_max` (3). |
| `tools` | `allowlist` (empty = all tools), `confirmation_timeout_seconds` (30). |

### Model providers

```json
{
  "name": "deepseek",
  "kind": "openai_compat",
  "base_url": "https://api.deepseek.com",
  "api_key": "${DEEPSEEK_API_KEY}",
  "model": "deepseek-flash",
  "timeout_seconds": 20,
  "temperature": 0.7,
  "input_usd_per_million": 0.3,
  "output_usd_per_million": 1.2,
  "extra_body": { "thinking": { "type": "disabled" } }
}
```

`kind` is `openai_compat` (any OpenAI-compatible endpoint) or `ollama`. Prices feed
the cost telemetry and the daily cap; without them the cap cannot count spend
(`priced: false` in `doctor --json`). `extra_body` adds top-level request fields;
the DeepSeek template disables reasoning, which otherwise spends the token budget
thinking and delays the first spoken word.

## Daemon and desktop sections

### `claps`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true | Listen for claps in the daemon. |
| `claps_required` | 2 | 2 or 3. The first clap only starts a speculative voice load. |
| `sensitivity` | 0.5 | 0–1. |
| `min_peak_dbfs` | -32 | Loudness gate; `jarvis claps calibrate` sets it 9 dB under your softest clap. |
| `min_gap_seconds` / `max_gap_seconds` | 0.12 / 0.9 | Spacing between claps (`max_gap` is also the candidate lifetime). |
| `quiet_before_seconds` / `quiet_after_seconds` | 0.8 / 0.35 | Silence required around the gesture. |
| `confirm_quiet_seconds` | 0 | > 0 waits for no extra transient (anti-rhythm guard, adds latency). |
| `cooldown_seconds` | 5 | Ignore claps after an activation. |

Also: `window_seconds`, `max_gap_ratio`, `max_decay_seconds`, `min_hf_ratio`,
`confidence_threshold`. A saved calibration (`clap_calibration.json`) refines
`min_peak_dbfs` and `sensitivity` unless you pin them here.

### `welcome`

| Key | Default | Meaning |
|---|---|---|
| `owner_name` | `Manuel` | Name used in the welcome. |
| `welcome`, `welcome_variants`, `degraded` | Spanish templates | `{greeting}`, `{name}`, `{issues}` placeholders; variants per `morning` / `afternoon` / `evening`. |
| `music_path` | "" | Your own audio file (mp3/flac/wav). Jarvis never downloads music. |
| `music_url` | "" | Opened in the browser only when there is no file (cannot be ducked). |
| `music_volume`, `duck_volume`, `background_volume` | 0.55, 0.12, 0.10 | Startup, under speech, after the welcome. |
| `music_start_seconds`, `fade_in_seconds`, `duck_seconds`, `fade_out_seconds` | 0, 1.5, 0.35, 2.5 | Timing. |
| `welcome_delay_seconds` | 2.0 | Welcome starts this long after the music. |
| `after_welcome` | `fade` | `fade` or `restore` (music continues at `background_volume`). |
| `activation_sound` | "" | Empty = synthesized chime. |
| `voice_ready_timeout_seconds`, `announce_timeout_seconds`, `services_timeout_seconds` | 8, 30, 20 | Degraded-startup deadlines. |
| `essential` | configuration, storage, audio in/out, STT, TTS, fast model | Components whose failure makes the welcome report a degraded start. |

### `voice` (cloned-voice lifecycle)

| Key | Default | Meaning |
|---|---|---|
| `preload` | `adaptive` | `adaptive`, `always` (keep ~4.2 GB VRAM loaded) or `on_demand`. |
| `speculative` | true | Start loading on the first clap. |
| `predictive_on_wake_word` | true | Start loading when the wake word is heard. |
| `cooldown_seconds` | 600 | Keep the model warm after a session. |
| `max_gpu_mb` | 4500 | Only load with this much free VRAM. |
| `evict_on_pressure`, `min_free_vram_mb` | true, 700 | Evict when free VRAM drops below this. |
| `gpu_busy_processes` | [] | e.g. `["cyberpunk2077.exe"]`: never load or keep the model while running. |

### `daemon`

`hotkey` (`ctrl+alt+j`), `wake_word` (false), `wake_word_model` (`hey_jarvis`),
`control_port` (47811), `input_device`, `session_idle_seconds` (900; 0 = never end
a silent session), `min_free_vram_mb_for_ollama` (5000: skip the Ollama preload
when VRAM is tight), `metrics_interval_seconds` (0 = off), `events_log` (true).

### `desktop`

`authorized_scopes`: folders where MEDIUM-risk tools run without asking (WSL paths
are accepted). `trusted_operations`: tools that never ask at MEDIUM risk.
`apps`: extra spoken application names mapped to command lists. See
[desktop-control.md](desktop-control.md).

### `workspace`

`default_profile`, `startup_profile` (launched on activation) and `profiles`:

```json
"profiles": {
  "dev": {
    "description": "VS Code + terminal",
    "tasks": [
      { "name": "vscode", "command": ["code", "--remote", "wsl+Ubuntu", "/home/me/project"],
        "detect": { "window_title": "Visual Studio Code" } },
      { "name": "backend", "command": ["npm", "run", "dev"], "cwd": "C:\\project",
        "window": "hidden", "ready": { "http": "http://127.0.0.1:3000/health" },
        "depends_on": ["vscode"] }
    ]
  }
}
```

Task fields are described in [desktop-control.md](desktop-control.md#workspaces).
