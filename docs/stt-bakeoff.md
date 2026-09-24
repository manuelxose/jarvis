# STT bake-off on the reference Windows machine

Compare local faster-whisper turbo on CUDA with Alibaba Qwen realtime STT on the **same recorded clips** using `scripts/stt_bakeoff.py`. This is an evaluation, not a provider migration: local Whisper already meets the ≤2 s STT target, so cloud STT must demonstrate an improvement worth its cost. Run the live comparison on the RTX 3070 Windows machine; no cloud measurements can be inferred from offline tests.

## Prepare

- Use the working Windows Python environment with the local faster-whisper and Alibaba `dashscope` adapter dependencies installed. Keep `stt.provider` set to `whisper`; the harness constructs both adapters regardless of the runtime primary.
- Use `--config config.win.json` (or your actual validated config). It should retain `audio.sample_rate: 16000`, the local `stt.model` turbo checkpoint and `stt.device: "cuda"`. Put `stt.api_key: "${DASHSCOPE_API_KEY}"` and `alibaba.region: "singapore"`, `alibaba.workspace_id` (your own workspace ID), and `alibaba.stt_model: "qwen3-asr-flash-realtime"` in the gitignored `config.local.json` alongside the base config. Set `DASHSCOPE_API_KEY` in the operator's environment without printing or committing it. The key must match the workspace region; check model availability and region details in [Alibaba Qwen voice API reference](alibaba-qwen.md#1-realtime-streaming-asr-model-id) before the run. Do not paste keys into manifests, commands, screenshots, or results.
- Record roughly ten clips: short Spanish commands, longer Spanish sentences, Spanish with English technical terms (for example, “haz git push al repositorio”), and a couple of English utterances. Save each as **16 kHz, mono, 16-bit PCM WAV**; no resampling is performed. Record a literal reference transcript for each clip, with consent and without private data. Keep clips and manifest in an operator-controlled location; they contain speech and reference text.

Create `clips\manifest.json` with audio paths **relative to that manifest**, for example:

```json
{
  "clips": [
    {"id": "es-01", "audio": "es-01.wav", "language": "es", "reference": "abre el repositorio"},
    {"id": "es-tech-01", "audio": "es-tech-01.wav", "language": "es", "reference": "haz git push al repositorio"},
    {"id": "en-01", "audio": "en-01.wav", "language": "en", "reference": "open the repository"}
  ]
}
```

The clip `language` is **only a tag in the evidence**: both adapters use the configured `stt.language` (typically `es`), even for `en` clips. Compare English accuracy with that caveat; to test an English setting, run separately with `stt.language: "en"` and keep the results separate.

## Run and review

From the repository root in Windows PowerShell, replace `<USD_PER_AUDIO_MINUTE>` with the current nonnegative USD price per audio minute from the Alibaba pricing page (a number, not the angle-bracket placeholder):

```powershell
python scripts\stt_bakeoff.py --config config.win.json --manifest clips\manifest.json --usd-per-audio-minute alibaba_qwen=<USD_PER_AUDIO_MINUTE> --output stt-bakeoff.json
```

The default `--providers whisper,alibaba_qwen` runs sequentially, with 20 ms frames paced at real time (`--realtime`); allow for clip duration plus a per-run `--timeout` of 30 seconds by default. The terminal shows a compact table; `--json` emits the same secret-safe record as JSON to stdout, and `--output` writes it to a file. Optional diagnostics:

```powershell
python scripts\stt_bakeoff.py --config config.win.json --manifest clips\manifest.json --providers whisper --no-realtime --json
python scripts\stt_bakeoff.py --config config.win.json --manifest clips\manifest.json --providers alibaba_qwen --show-transcripts
```

`--no-realtime` is a fast diagnostic, **not** a live-latency substitute. `--show-transcripts` prints hypotheses to **stderr** for private review; they never appear in the JSON/table, but terminal capture may retain them. Avoid redirecting that output to a shared log. Do not publish the WAVs, manifest or transcript-bearing terminal capture. The evidence record excludes reference and hypothesis text, credentials, workspace ID, and audio bytes, but still contains clip IDs, language tags and machine/platform metadata; review it before sharing.

Each `runs` entry identifies `clip`, `language`, `provider` and `outcome`; `audio_seconds` is clip length. `first_partial_ms` measures from the beginning of the paced feed to the first transcript (partial **or** final). `final_ms` measures from the **last audio frame fed** to the last final transcript: it is the post-speech wait, not an end-to-end time, and is not directly ordered against `first_partial_ms`. It is `null` when the adapter provides no final or stops consuming before the last frame. `cer` and `wer` are normalized character/word error rates against the manifest reference (lower is better; case and accents are folded); an empty reference gives `null`. `estimated_usd` is audio duration in seconds ÷ 60 × operator-supplied USD/minute rate, or `null` when no rate was supplied; it is an estimate, not a billed invoice. To price local Whisper, supply a separate `--usd-per-audio-minute whisper=...` rate if appropriate.

`summary` groups providers by `ok` count, median `median_first_partial_ms` and `median_final_ms`, mean `mean_cer` and `mean_wer`, and `total_estimated_usd` over successful priced rows; missing observations are `null`. `schema`, `platform`, and `python` identify the record format and host. Outcomes are `ok`, `no_transcript`, `unavailable` (including timeouts), `config_error`, and `missing_audio` (absent, empty or incompatible WAV). Provider failures are isolated and named on stderr without raw provider messages; the process returns 1 when any row is not `ok`, 2 for bad arguments/manifest/config or output write failure, and 0 when all rows are `ok`. Fix failures and rerun before making a decision: aggregate medians exclude failed clips, so unequal successful sets are not a fair comparison. Check filesystem access, WAV format, configured region/workspace/key, network, and current pricing if a run fails; do not share raw exceptions or credentials.

## Decision

Compare the **same successful clips** on the target machine, with both providers under the default paced mode. Consider switching `stt.provider` to `alibaba_qwen` only if its median `final_ms` beats local Whisper **and** its mean WER is no worse, with an acceptable estimated cost; otherwise keep `whisper`. Consider first-partial latency and per-language errors as additional context. Record the observed figures, cost assumption and resulting choice as a new project decision before any provider-default change. The existing ≤2 s local path remains the baseline.

## Results

No live STT bake-off measurements have been recorded yet. Offline harness tests are not evidence that Alibaba Qwen improves latency or accuracy on the operator's Windows machine.
