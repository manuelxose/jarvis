# Windows cloud and fallback bake-off

Run from PowerShell at the repository root (through the same checkout used for the
voice acceptance run). This is the operator procedure that produces target-machine
evidence for the configured cloud-first route, the local Ollama baseline, and a
controlled pre-token cloud-failure handoff. The Linux CI host cannot prove the
selected cloud endpoint, the local Ollama service, or human response quality, so
these three runs must happen on the target Windows machine.

The evidence record is deliberately secret-safe: it retains only public provider
identity, route/fallback outcomes, first-token and total milliseconds, and a
bounded categorical quality assessment. Credentials, the prompt, generated answer
text, and free-form notes are never written into the record.

## Prepare the environment

Use the same virtual environment and interpreter as the rest of the Windows flow.
The correct path is `.\.venv\Scripts\python.exe` (not `..venv\...`). If PowerShell
blocks the local bootstrap, allow scripts in the current process first, then
prepare dependencies and Ollama:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\bootstrap.ps1 -SkipModelPull
```

`bootstrap.ps1` starts `ollama serve` if it is not running and verifies or pulls
the configured model. If you already have the model cached, `-SkipModelPull`
avoids re-downloading it.

## Configure the cloud-first override (secret-free)

Copy the tracked example to the ignored sibling override and provide the API key
only through the process environment — never inside a tracked file:

```powershell
Copy-Item config.local.example.json config.local.json
$env:OPENAI_API_KEY = "sk-..."
```

`config.local.json` is already in `.gitignore`. It references the literal
`${OPENAI_API_KEY}` placeholder, which `load_config` resolves from the environment
at run time. The key is never written to `config.json`, the example, logs, or
reports. If the key is not set, configuration fails with an actionable
`environment variable 'OPENAI_API_KEY' is required` error rather than a hang.

The layered merge prepends the cloud provider before the committed local Ollama
provider, so the resolved order is cloud-first with an Ollama fallback.

## Preflight with doctor

Before running the bake-off, confirm the resolved provider order, that the API key
is present only as a presence flag, and that the local fallback is reachable. The
`jarvis` package lives under `src/`; ensure it is importable once (for example
`pip install -e .` in the venv, or `$env:PYTHONPATH = (Resolve-Path .\src)` in the
current process), then run:

```powershell
.\.venv\Scripts\python.exe -m jarvis doctor
.\.venv\Scripts\python.exe -m jarvis doctor --json
```

The readable output must show the cloud provider as `primary` with `api_key=present`
(never the value), the Ollama provider as an ordered `fallback`, and
`local fallback ready: yes`. JSON mode exposes `model.provider_order`, `primary`,
`fallbacks`, and `local_fallback_ready`, with the key reduced to
`has_api_key: true/false`.

`local fallback ready: no` means Ollama is unreachable or the model is absent and
the fallback is not usable. Do not run the bake-off in that state — remediate
first (`ollama serve` when unreachable; `ollama pull <model>` when the configured
model is missing) and re-run `doctor` until it reports `yes`.

## Run the bake-off

The harness `scripts/model_bakeoff.py` uses only the standard library and the
existing Jarvis adapters, resolves the same production model chain that `doctor`
reports, and runs three sequential single-turn cases:

1. `cloud_chain` — the configured cloud-first chain, one real streamed turn.
2. `local_baseline` — the local Ollama provider alone, one real streamed turn.
3. `forced_fallback` — a forced transient primary failure before any token, proving
   the pre-token commitment rule hands off to Ollama without duplicating output.

Run from the repository root (so `config.json` and the sibling `config.local.json`
resolve). The streamed answers are written only to the diagnostic stream (stderr)
and are never retained:

```powershell
.\.venv\Scripts\python.exe scripts\model_bakeoff.py --output bakeoff-2026-09-20.json
```

Use `--prompt` only to change the fixed operator turn (it is never recorded). The
`--quality` flag applies one bounded category — `accepted`, `needs-review`, or
`rejected` — to the run. Read the three streamed answers (prefixed
`[case=...]` on stderr), then pass your categorical assessment. The default is
`needs-review`. If you need a distinct category per case, re-run with that
`--quality` value; never paste answer text or a free-form note into the record.

A successful run exits `0` and emits a compact table on stdout plus the JSON record
at `--output`. Any case whose outcome is not `ok` (for example `unavailable`,
`no_token`, or `missing_local`) is reported to stderr and the process exits non-zero;
see "Failure outcomes" below.

## Review the retained record

The record (`bakeoff-*.json`) contains, per case:

- `case` — `cloud_chain`, `local_baseline`, or `forced_fallback`.
- `providers` — public identity only: `role`, `name`, `kind`, `base_url`, `model`.
- `route` — the provider that committed output (`none` if no token arrived).
- `fallback` — `not_used`, `used`, or absent for the local-only baseline.
- `first_token_ms` / `total_ms` — monotonic timing for the committed stream.
- `quality` — `accepted`, `needs-review`, or `rejected` (omitted when there is no
  assessable answer).
- `outcome` — `ok`, `no_token`, `unavailable`, `partial`, or `missing_local`.

No `api_key`, prompt, response text, or free-form notes are present. Review the
endpoint/model identifiers, the first-token versus total timing, and the
route/fallback outcome. Assess answer quality by hand while reading the streamed
output; do not place prompts, response text, API keys, or personal data in the
record.

## Retain evidence safely

The `--output` filename pattern `bakeoff-*.json` is in `.gitignore`, so
target-machine evidence cannot be accidentally committed. Keep
`config.json` and `config.local.example.json` secret-free; never stage
`config.local.json`. If the record must be shared, transfer the JSON file out of
band (or paste its contents) — it contains no credential, prompt, or response
material by construction.

## Failure outcomes

The harness and the doctor preflight convert each failure into a bounded,
attributable signal rather than a silent or unbounded hang:

- **Cloud unavailable / no first token** — the primary fails before emitting a
  token; `ProviderChain` advances to the Ollama fallback. If no provider emits a
  token, the case is recorded `unavailable` or `no_token` and the process exits
  non-zero.
- **Ollama unreachable or model absent** — `doctor` reports
  `local fallback ready: no`; the bake-off is not run in that state. Remediate with
  `ollama serve` or `ollama pull <model>` and re-run `doctor`.
- **Missing local override or API key** — `load_config` fails with an actionable
  message (`environment variable 'OPENAI_API_KEY' is required`) and a non-success
  exit; nothing is recorded as a completed bake-off.
- **Post-token cloud failure** — a cloud stream that fails after emitting output
  stays committed (`outcome: partial`); the fallback does not re-emit or duplicate
  the local output.
- **Invalid quality category** — `--quality` outside the bounded vocabulary is
  rejected with an error and exit code 2.

A case that cannot produce an assessable answer is recorded as its unavailable/error
outcome; the procedure must not claim a completed bake-off for it.

## Load profile

Exactly one sequential turn runs per case (cloud, local, and controlled forced
fallback) through the existing bounded `ProviderChain` retry/timeout behavior.
There is no concurrency, batch loop, or persistent service, so a run issues at most
a handful of sequential provider requests and cannot amplify cloud cost.
