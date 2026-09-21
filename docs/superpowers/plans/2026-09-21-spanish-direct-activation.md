# Spanish Direct Voice Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute a command spoken in one utterance such as «Jarvis, abre Spotify» using the existing Spanish STT instead of the unreliable English wake-word model.

**Architecture:** `ActivationManager` will own strict wake-prefix parsing and return the remaining command while preserving its original text. `VoiceLoop` will use the existing VAD-bounded capture and STT path for activation, pass the remainder directly to `TurnManager`, and listen once more only when the user says the wake word alone.

**Tech Stack:** Python 3.11, asyncio, existing VAD/STT contracts, unittest.

**Spec:** `docs/superpowers/specs/2026-09-21-spanish-direct-activation-design.md`

## Global Constraints

- Reuse the configured VAD, STT, `ActivationManager`, and conversation window.
- Add no dependency or external service.
- In `wake_word` mode, only a phrase beginning with the standalone configured wake word may activate Jarvis.
- Preserve all activation modes other than `wake_word`.
- Transcribe a combined activation and command only once.

---

### Task 1: Parse a strict wake-word prefix

**Files:**
- Modify: `src/jarvis/adapters/audio/activation.py`
- Test: `tests/test_vad_activation.py`

**Interfaces:**
- Consumes: `ActivationManager.wake_word: str`
- Produces: `ActivationManager.command_after_wake_word(text: str) -> str | None`; `None` means no activation, `""` means activation without a command, and any other string is the command with original spelling preserved.

- [x] **Step 1: Write failing prefix extraction tests**

Add tests that require activation only at the start, accept accents and punctuation, preserve the command text, and reject substring matches:

```python
def test_extracts_command_only_after_leading_wake_word(self):
    manager = ActivationManager(wake_word="jarvis")

    self.assertEqual("abre Spotify", manager.command_after_wake_word("Jarvis, abre Spotify"))
    self.assertEqual("", manager.command_after_wake_word("¡Járvis!"))
    self.assertIsNone(manager.command_after_wake_word("hablé con Jarvis"))
    self.assertIsNone(manager.command_after_wake_word("jarvisito abre Spotify"))
```

- [x] **Step 2: Run the tests and confirm the missing-method failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_vad_activation.ActivationTests.test_extracts_command_only_after_leading_wake_word`

Expected: FAIL with `AttributeError: 'ActivationManager' object has no attribute 'command_after_wake_word'`.

- [x] **Step 3: Implement minimal prefix parsing**

Add module-level imports for `re` and `unicodedata`, a private normalization function, and the public method. Match the first Unicode word and compare normalized values exactly; do not use the current substring rule.

```python
def _normalize_word(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()

def command_after_wake_word(self, text: str) -> str | None:
    match = re.match(r"^\W*(\w+)(.*)$", text or "", flags=re.DOTALL)
    if match is None or _normalize_word(match.group(1)) != _normalize_word(self.wake_word):
        return None
    return match.group(2).lstrip(" \t,.:;!?¿¡-")

def matches_wake_word(self, text: str) -> bool:
    return self.command_after_wake_word(text) is not None
```

- [x] **Step 4: Run activation tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_vad_activation`

Expected: all activation and VAD tests PASS.

- [x] **Step 5: Commit the parser**

```bash
git add src/jarvis/adapters/audio/activation.py tests/test_vad_activation.py
git commit -m "feat: parse direct Jarvis activation prefix"
```

### Task 2: Activate and execute from one transcribed utterance

**Files:**
- Modify: `src/jarvis/application/voice_loop.py`
- Modify: `src/jarvis/application/runtime.py`
- Test: `tests/test_voice_loop.py`
- Test: `tests/test_runtime_voice_loop.py`

**Interfaces:**
- Consumes: `ActivationManager.command_after_wake_word(text: str) -> str | None` from Task 1.
- Produces: `VoiceLoop._capture_utterance(context: TurnContext) -> str | None`, which waits for speech, captures through trailing silence, and transcribes once.

- [x] **Step 1: Write failing direct-activation loop tests**

Add deterministic tests using `ScriptedAudioInput`, `EnergyVAD`, and `ScriptedSTT`:

```python
async def test_leading_wake_word_executes_remainder_in_same_utterance(self):
    loop, _, _ = _build(
        [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
        stt=ScriptedSTT([Transcript("Jarvis, abre Spotify", is_final=True)]),
    )

    await loop.run(max_turns=1)

    self.assertEqual(1, len(loop.turns))
    self.assertEqual("abre Spotify", loop.turns[0].transcript)

async def test_non_activation_utterance_is_discarded(self):
    loop, _, _ = _build(
        [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
        stt=ScriptedSTT([Transcript("abre Spotify", is_final=True)]),
    )

    await loop.run()

    self.assertEqual([], loop.turns)
```

Add a third test with two VAD-bounded utterances and two scripted transcripts (`"Jarvis"`, then `"abre Spotify"`) to prove wake-only activation listens for the next phrase.

- [x] **Step 2: Run the new loop tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_voice_loop`

Expected: the direct phrase is not stripped/executed and a non-activation phrase incorrectly reaches `TurnManager` under the old acoustic wake flow.

- [x] **Step 3: Implement VAD-bounded activation transcription**

Refactor the existing capture method so it ignores leading silence, starts buffering on the first `vad.is_speech(frame)`, and stops after `min_silence_frames` trailing silent frames. In `run()`:

```python
text = await self._capture_utterance(context)
if text is None:
    break
if self._activation.wake_word_required():
    text = self._activation.command_after_wake_word(text)
    if text is None:
        continue
    self._activation.note_activation()
    if not text:
        text = await self._capture_utterance(context)
        if text is None:
            break
if not text:
    continue
```

Remove `_arm` and the `WakeDetector` constructor dependency. Change barge-in to interrupt on `self._vad.is_speech(frame)`, because interruption should react to new speech rather than the removed English keyword model. Update both real and fake runtime builders to stop constructing or passing a wake detector. Do not instantiate or call Whisper twice for a combined phrase.

- [x] **Step 4: Update existing deterministic fixtures**

Change existing `wake_word` test transcripts such as `"hola jarvis"` to leading forms such as `"Jarvis, hola"`. Ensure fixture audio contains at least one frame above `EnergyVAD`'s threshold before trailing silence. Preserve the tests for cooldown, exhaustion, empty transcription, barge-in, and non-wake activation modes; adjust only assumptions invalidated by the approved design.

- [x] **Step 5: Run voice-loop and runtime integration tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_voice_loop tests.test_runtime_voice_loop tests.test_vad_activation`

Expected: all tests PASS.

- [x] **Step 6: Run the broader affected suite**

Run: `PYTHONPATH=src python3 -m unittest tests.test_audio_runtime tests.test_acceptance tests.test_hardware_acceptance tests.test_cli_run`

Expected: all tests PASS or hardware-only tests SKIP with their declared reason.

- [x] **Step 7: Commit the voice-loop change**

```bash
git add src/jarvis/application/voice_loop.py src/jarvis/application/runtime.py tests/test_voice_loop.py tests/test_runtime_voice_loop.py
git commit -m "feat: execute Spanish wake phrase and command directly"
```

### Task 3: Validate on Windows hardware

> Steps 2–4 need a real Windows machine with a microphone; this environment (WSL/Linux, no audio hardware) can only run Step 1. Run `run_jarvis.bat` on Windows and walk through Steps 2–4 there.

**Files:**
- Modify only if verification exposes a defect in Tasks 1–2.

**Interfaces:**
- Consumes: `run_jarvis.bat`, `config.win.json`, Windows microphone selected by the existing audio adapter.
- Produces: runtime evidence that a direct Spanish phrase creates one command turn without acoustic wake scores.

- [x] **Step 1: Run static and unit verification**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_vad_activation tests.test_voice_loop tests.test_runtime_voice_loop tests.test_cli tests.test_cli_run
git diff --check
```

Expected: all tests PASS and `git diff --check` exits 0.

- [ ] **Step 2: Start Jarvis on Windows**

Run from PowerShell in the repository root:

```powershell
.\run_jarvis.bat
```

Expected: Jarvis reaches listening state without repeatedly logging `wake score`.

- [ ] **Step 3: Exercise acceptance phrases**

Say each phrase once and inspect the log:

```text
Jarvis, dime la capital de Francia
abre Spotify
Jarvis
qué hora es
```

Expected: the first phrase creates one turn whose command excludes `Jarvis`; `abre Spotify` alone is ignored; `Jarvis` followed by `qué hora es` creates one turn.

- [ ] **Step 4: Record only implementation-caused corrections and commit if needed**

If the hardware check reveals a defect, add a failing deterministic test first, make the smallest correction, rerun Steps 1–3, and commit only the affected source and test files.
