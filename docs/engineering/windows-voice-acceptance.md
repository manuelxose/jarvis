# Windows Voice Acceptance — Ollama Preflight (M003/S03)

This document records the exact manual acceptance procedure for the bounded
Ollama startup preflight on Windows, plus the measured outcomes that prove the
slice contract:

> Startup reports missing Ollama or the configured local model within a bounded
> timeout with an exact local remediation command, while voice runtime can
> continue where possible.

## 1. Preflight contract

`brain.llm.OllamaClient.preflight()` performs a single bounded `GET /api/tags`
probe and returns a frozen `OllamaPreflight`:

| Field | Meaning |
|-------|---------|
| `ok` | `True` when Ollama is reachable **and** the configured model is pulled. |
| `error` | `service_unavailable` (unreachable / timeout / HTTP error / malformed JSON) or `model_missing` (reachable but configured model absent), else `None`. |
| `duration_seconds` | Measured wall-clock duration of the probe (always present). |
| `remediation` | Exact local command to run: `ollama serve` or `ollama pull <model>`, else `None`. |
| `detail` | Human-readable diagnostic. |

Configured values come from `config.yaml` (`llm.*`):

- `base_url`: `http://localhost:11434`
- `model`: `mistral:7b-instruct`
- `preflight_timeout`: **5.0 s** default (bounded, overridable via
  `preflight(timeout=…)`)

## 2. Manual acceptance commands (Windows PowerShell)

Run these from the repository root with the virtual environment activated.
The one-liner probe is identical for every scenario:

```powershell
python -c "from brain.llm import OllamaClient; r = OllamaClient().preflight(); print(f'ok={r.ok} error={r.error} duration={r.duration_seconds:.3f}s'); print(f'remediation={r.remediation}'); print(r.detail)"
```

### Scenario A — Ollama not running → `service_unavailable`

```powershell
# 1. Make sure no Ollama is running (no `ollama serve`, Ollama app closed).
# 2. Run the probe.
python -c "from brain.llm import OllamaClient; r = OllamaClient().preflight(); print(f'ok={r.ok} error={r.error} duration={r.duration_seconds:.3f}s'); print(f'remediation={r.remediation}'); print(r.detail)"
```

**Expected (within the 5.0 s bound):**

```
ok=False error=service_unavailable duration=0.003s
remediation=ollama serve
Could not reach http://localhost:11434: ... Connection refused
```

### Scenario B — Ollama running, model missing → `model_missing`

```powershell
ollama serve          # or launch the Ollama desktop app
ollama list           # confirm mistral:7b-instruct is NOT listed
python -c "from brain.llm import OllamaClient; r = OllamaClient().preflight(); print(f'ok={r.ok} error={r.error} duration={r.duration_seconds:.3f}s'); print(f'remediation={r.remediation}'); print(r.detail)"
```

**Expected:**

```
ok=False error=model_missing duration=0.0xxs
remediation=ollama pull mistral:7b-instruct
Model mistral:7b-instruct not present among N local model(s)
```

### Scenario C — Ollama running, model present → `ok`

```powershell
ollama pull mistral:7b-instruct
python -c "from brain.llm import OllamaClient; r = OllamaClient().preflight(); print(f'ok={r.ok} error={r.error} duration={r.duration_seconds:.3f}s'); print(f'remediation={r.remediation}'); print(r.detail)"
```

**Expected:**

```
ok=True error=None duration=0.0xxs
remediation=None
Ollama ready: mistral:7b-instruct present
```

### Full startup observation

```powershell
python main.py
```

On startup, `build_runtime_components` runs the preflight and logs exactly one
line with the measured duration:

```
Ollama preflight: ok=False, error=service_unavailable, duration=0.003s
Ollama preflight failed (service_unavailable): Could not reach http://localhost:11434: ... Remediation: ollama serve
```

Voice runtime continues: the router is constructed with `llm_client=None` when
`ollama_ready` is false, and the run loop answers LLM-dependent commands with a
localized "no connection" message instead of an opaque late failure.

## 3. Measured outcomes

### 3.1 Automated test evidence (deterministic, no network)

`tests/test_ollama_preflight.py` pins the contract with 10 tests that patch
`brain.llm.requests.get`, so they are independent of any local Ollama install:

| Test | Scenario asserted |
|------|-------------------|
| `test_successful_preflight_reports_ready` | ok + no remediation, `GET /api/tags` timeout=2.0 |
| `test_service_unavailable_on_connection_refused` | `service_unavailable` + `ollama serve` |
| `test_service_unavailable_on_timeout_is_bounded` | timeout → `service_unavailable`, `Timed out after 2.0s` |
| `test_model_missing_reports_exact_pull_command` | `model_missing` + `ollama pull mistral:7b-instruct` |
| `test_http_error_status_is_service_unavailable` | HTTP 500 → `service_unavailable` |
| `test_malformed_response_is_service_unavailable` | bad JSON → `service_unavailable` + `Malformed response` |
| `test_preflight_timeout_override_is_passed_through` | `preflight(timeout=0.25)` → `timeout=0.25` |
| `test_default_preflight_timeout_is_bounded` | default `preflight_timeout == 5.0` |
| `test_failed_preflight_logs_duration_and_exact_remediation` | `main.run_ollama_preflight` logs `duration=` + remediation |
| `test_successful_preflight_logs_duration_without_remediation` | success logs `duration=` with no remediation |

Result: **10/10 passed** (`Ran 10 tests ... OK`).

### 3.2 Fresh command evidence (real probe, service down)

Executed on the Linux CI/dev box (no Ollama installed) against
`http://localhost:11434`:

```
ok=False error=service_unavailable duration=0.002s remediation='ollama serve'
detail="Could not reach http://localhost:11434: ... Connection refused"
```

This is the real, unbounded-by-design fast path: connection-refused returns
immediately (0.002 s) with the exact remediation. A genuinely hanging Ollama is
bounded by `preflight_timeout` (5.0 s default), producing
`Timed out after 5.0s reaching http://localhost:11434`.

## 4. Remediation commands (reference)

| Failure | Exact remediation |
|---------|-------------------|
| Ollama unreachable / timeout / HTTP error / malformed | `ollama serve` |
| Configured model not pulled | `ollama pull mistral:7b-instruct` |
| List local models | `ollama list` |
| Ollama version / install check | `ollama --version` |
