# Configuration

Jarvis reads one JSON file (`--config`, default `config.json`) and merges a sibling
`config.local.json` over it when present. The whole document is validated at load
time; a wrong type or an unknown key in a validated section fails with a precise
message.

| File | Versioned | Purpose |
|---|---|---|
| `config.json` | yes | Minimal secret-free base: local Ollama only. Used by CI and non-Windows runs. |
| `config.win.json` | yes | Full Windows setup: Whisper on CUDA, cloned voice, claps, a generic `dev` workspace. |
| `config.local.example.json` | yes | Template for `config.local.json`: name, folders, cloud provider. |
| `config.local.json` | **no** (gitignored) | Your personal overrides: name, folders, workspaces, music, cloud providers. |

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
| `tts` | `provider` (`qwen_clone` \| `sapi` \| `alibaba_qwen` \| `local` = Coqui XTTS), `voice`, `language`, `api_key`; for `qwen_clone`: `worker_python`, `profile_dir`, `model`, `chunk_size` (4), `warmup_wait_seconds` (0: how long a reply waits for a still-loading clone before using SAPI); `profile` (`auto` \| `fast` \| `cheap` \| `quality`, default `auto`) and `routing_evidence` (operator-supplied per-provider measurements used to reorder fallback candidates — see [Operator-gated TTS routing](alibaba-qwen.md#operator-gated-tts-routing-no-measured-winner-yet) for the schema and an example; one evidence row's `usd_per_character` also feeds the cost telemetry pricing table for that provider). |
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
| `min_peak_dbfs` | -32 | Loudness gate; `jarvis claps calibrate` sets it 12 dB under your softest clap, and never within 12 dB of the room's p99 noise. |
| `min_gap_seconds` / `max_gap_seconds` | 0.12 / 0.9 | Spacing between claps (`max_gap` is also the candidate lifetime). |
| `quiet_before_seconds` / `quiet_after_seconds` | 0.8 / 0.35 | Silence required around the gesture. |
| `confirm_quiet_seconds` | 0 | > 0 waits for no extra transient (anti-rhythm guard, adds latency). |
| `cooldown_seconds` | 5 | Ignore claps after an activation. |

Also: `window_seconds`, `max_gap_ratio`, `max_decay_seconds`, `min_hf_ratio`,
`confidence_threshold`. A saved calibration (`clap_calibration.json`) refines
`min_peak_dbfs` and `sensitivity` unless you pin them here.

Triple-clap mode is `claps_required: 3`; `python scripts/clap_eval.py --synthetic` (or
`--config config.win.json FILES`) checks false activations and detection latency for
either mode, and `jarvis claps test --file recording.wav` replays a recording.

### `welcome`

Run `jarvis startup` to play the whole sequence once and print its timings; see [startup.md](startup.md).

| Key | Default | Meaning |
|---|---|---|
| `owner_name` | `señor` | Name used in the welcome. |
| `welcome`, `welcome_variants`, `degraded` | Spanish templates | `{greeting}`, `{name}`, `{issues}` placeholders; variants per `morning` / `afternoon` / `evening`. |
| `music_path` | "" | Your own audio file (mp3/flac/wav). Jarvis never downloads music. |
| `music_url` | "" | Opened in the browser only when there is no file (cannot be ducked). |
| `music_volume`, `duck_volume`, `background_volume` | 0.55, 0.12, 0.10 | Startup, under speech, after the welcome. |
| `music_start_seconds`, `fade_in_seconds`, `duck_seconds`, `fade_out_seconds` | 0, 1.5, 0.35, 2.5 | Timing. |
| `welcome_delay_seconds` | 2.0 | Welcome starts this long after the music. |
| `after_welcome` | `fade` | `fade` or `restore` (music continues at `background_volume`). |
| `activation_sound` | "" | Empty = synthesized chime. |
| `voice_ready_timeout_seconds`, `announce_timeout_seconds`, `services_timeout_seconds` | 20, 30, 20 | Degraded-startup deadlines (a cold clone worker needs ~14-18 s to be ready). |
| `essential` | configuration, storage, audio in/out, STT, TTS, fast model | Components whose failure makes the welcome report a degraded start. |

### `voice` (cloned-voice lifecycle)

| Key | Default | Meaning |
|---|---|---|
| `preload` | `adaptive` | `adaptive`, `always` (keep ~4.2 GB VRAM loaded) or `on_demand`. |
| `speculative` | true | Start loading on the first clap. |
| `predictive_on_wake_word` | true | Start loading when the wake word is heard. |
| `cooldown_seconds` | 600 | Keep the model warm after a session. 0 = evict when the session ends (deferred until any in-flight synthesis finishes). |
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

Task fields are described in [desktop-control.md](desktop-control.md#workspaces); duplicate
rules, managed-only shutdown and Windows evidence are in [workspace.md](workspace.md).

## Environment variables

None of these are read from `config.json`; they are read directly with
`os.environ` (or, for `JARVIS_TTS_VENV`, only by a setup script). Never put
secret values in `config.json` — use the `"${NAME}"` substitution described
above instead.

| Variable | Default | Read by | Purpose |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | none (required if referenced) | `${DEEPSEEK_API_KEY}` substitution in a `models.providers[].api_key` | Secret for the DeepSeek (or any `${}`-templated) LLM provider. |
| `DASHSCOPE_API_KEY` | none (required if referenced) | `${DASHSCOPE_API_KEY}` substitution in `alibaba`/`stt`/`tts` `api_key`; read directly by `scripts/alibaba_voice_clone.py` | Secret for Alibaba Qwen cloud STT/TTS and voice-clone setup. |
| `JARVIS_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | `adapters/hermes/agent_child.py` | Ollama endpoint for the bundled Hermes agent child (`hermes.command` default), not the main model chain's Ollama fallback (that one is configured via `models.providers[].base_url`). |
| `JARVIS_OLLAMA_MODEL` | `mistral:7b-instruct` | `adapters/hermes/agent_child.py` | Model tag for the same Hermes agent child. |
| `JARVIS_OLLAMA_KEEP_ALIVE` | `2m` | `adapters/hermes/agent_child.py` | How long Ollama keeps the Hermes child's model resident (D024 VRAM policy); the runtime sets this from `models.providers` (`application/runtime.py::_hermes_env`) so it never fights the resident voice-clone TTS for VRAM. |
| `JARVIS_OLLAMA_OPTIONS` | unset | `adapters/hermes/agent_child.py` | JSON object of Ollama `options` (e.g. `{"num_gpu": 0}` for a CPU-only model) for the Hermes child; the runtime sets it from the Ollama provider's `extra_body.options` (`application/runtime.py::_hermes_env`). Malformed values are ignored. |
| `JARVIS_TTS_VENV` | `%LOCALAPPDATA%\jarvis\venv-tts` | `scripts/setup_tts_worker.bat` | Where the isolated cloned-voice worker venv is created. Setup-time only; the runtime resolves the worker interpreter from `tts.worker_python` or the same default path via `LOCALAPPDATA` (`adapters/tts/qwen_clone.py::default_worker_python`), not from this variable. |
| `JARVIS_VOICE_PROFILE` | `""` | `adapters/tts/qwen_worker.py` (`--profile-dir` default) | Default voice profile directory when the worker script is invoked directly rather than through `tts.profile_dir`. |
| `JARVIS_WSL_DISTRO` | `Ubuntu` | `adapters/tools/desktop.py` | WSL distribution name used to translate WSL paths for desktop tools. |

## Diagnostics

- **`jarvis doctor`** runs every health check without entering the runtime and
  prints a human-readable report; `--json` emits the same data as JSON,
  including the resolved model chain (`provider_order`, per-provider `kind`,
  `model`, `base_url`, `has_api_key`, and, for priced providers, `priced`,
  `spent_today_usd`, `max_daily_usd` from the spend ledger). Credential values
  are never included, only whether one is present.
- **`turn.cost` hub event** — published once per turn (success or cancel) with
  `trace_id`, `route`, `total_usd`, and `entries`: one row per priced call this
  turn (`fast_model`'s LLM usage and any TTS usage; STT is never costed), each
  with `provider`, `kind` (`llm` \| `tts`), `input_tokens`, `output_tokens`,
  `characters`, `audio_seconds`, and `usd` (`null` when the provider has no
  configured price).
- **Voice-loop "turn completed" log** — one line per turn:
  `route=... total=... ms cost_usd=... trace=...`, using the same `cost` the
  `turn.cost` event carries.
- **TTS profile ordering log** — logged once when the TTS chain is built:
  `TTS routing profile=<profile> providers=<ordered names> reason=<why>`. The
  reason is always a diagnostic string ("measured evidence", "configured order
  retained", etc.), never a raw measurement.
- **`perf_bench.py`** (`python scripts\perf_bench.py --config config.win.json`)
  produces `docs/bench/latest.json`; see [performance.md](performance.md) for
  the current numbers and [latency-report.md](latency-report.md) for the
  before/after comparison and what has not been measured yet.
