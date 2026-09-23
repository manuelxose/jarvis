@echo off
REM Isolated environment for the local voice-cloning TTS worker (Faster Qwen3-TTS).
REM faster-qwen3-tts needs transformers 5.x; Jarvis pins transformers 4.x for
REM coqui-tts, so the worker lives in its own venv and runs as a child process.
REM Downloads ONLY Qwen3-TTS-12Hz-0.6B-Base (not the 1.7B the upstream script fetches).
setlocal
if "%JARVIS_TTS_VENV%"=="" set "JARVIS_TTS_VENV=%LOCALAPPDATA%\jarvis\venv-tts"
set "HERE=%~dp0"

where uv >nul 2>&1 || (echo ERROR: uv not found. Install: winget install astral-sh.uv & exit /b 1)

if not exist "%JARVIS_TTS_VENV%\Scripts\python.exe" (
    uv venv "%JARVIS_TTS_VENV%" --python 3.11 || exit /b 1
)
REM torch 2.7.1+cu118 is proven on driver 536.99 (CUDA 12.2); no driver update needed.
uv pip install --python "%JARVIS_TTS_VENV%\Scripts\python.exe" -r "%HERE%..\requirements-tts.txt" --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cu118 || exit /b 1

"%JARVIS_TTS_VENV%\Scripts\python.exe" -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; print('torch', torch.__version__, 'cuda', torch.version.cuda, torch.cuda.get_device_name(0))" || exit /b 1
"%JARVIS_TTS_VENV%\Scripts\python.exe" -c "from huggingface_hub import snapshot_download; print(snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base'))" || exit /b 1
echo TTS worker environment ready: %JARVIS_TTS_VENV%
