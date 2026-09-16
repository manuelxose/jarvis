# Jarvis foundation operations

The foundation runtime supports Python 3.10 and 3.11 and uses no provider SDKs.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Create `config.json` with environment-variable references for secrets:

```json
{
  "runtime": {"command_deadline_ms": 500},
  "providers": {"fast_model_api_key": "${JARVIS_FAST_MODEL_API_KEY}"},
  "memory": {},
  "security": {}
}
```

Set `JARVIS_FAST_MODEL_API_KEY` before running this example. The key is optional; omit
`providers.fast_model_api_key` when no provider is configured.

Run diagnostics with:

```sh
jarvis doctor --config config.json
jarvis doctor --config config.json --json
jarvis run --config config.json --check-only
```

Before the audio, STT, TTS, model, Hermes, and network adapters arrive, the expected
state is `degraded` and these commands exit with status `1`. `doctor` reports them as
`unavailable`; it does not pretend voice components are healthy or play a success effect.
Invalid input or configuration exits with status `2`; a healthy diagnostic check exits
with status `0`.

Without an editable install, use the available interpreter and source path directly:

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
PYTHONPATH=src /usr/bin/python3 -m compileall -q src tests
PYTHONPATH=src /usr/bin/python3 -m jarvis --help
PYTHONPATH=src /usr/bin/python3 -m jarvis doctor --help
```
