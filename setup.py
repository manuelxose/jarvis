from __future__ import annotations

import subprocess
import sys
import tempfile
import json
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent


def print_step(step: str) -> None:
    print(f"[SETUP] {step}")


def check_python() -> None:
    print_step("1/10 Verificando Python compatible (3.10 o 3.11)...")
    major, minor = sys.version_info.major, sys.version_info.minor
    if (major, minor) < (3, 10) or (major, minor) >= (3, 12):
        raise RuntimeError(
            "Version de Python no compatible. Este proyecto requiere Python 3.10 o 3.11."
        )


def run_subprocess(
    command: list[str], timeout: int = 120, capture_output: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        if capture_output:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        return subprocess.run(command, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"No se encontro el ejecutable '{command[0]}'. "
            "Verifica instalacion y variable PATH."
        ) from exc


def find_ollama_executable() -> str:
    env_bin = os.environ.get("OLLAMA_BIN")
    if env_bin:
        env_path = Path(env_bin).expanduser()
        if env_path.exists():
            return str(env_path)

    in_path = shutil.which("ollama")
    if in_path:
        return in_path

    local_appdata = os.environ.get("LOCALAPPDATA")
    candidates: list[Path] = []
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / "Ollama" / "ollama.exe")
    candidates.append(Path("C:/Program Files/Ollama/ollama.exe"))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        "Ollama no esta instalado o no se encontro en PATH. "
        "Instala desde https://ollama.ai/download y reinicia la terminal."
    )


def check_ollama_running(base_url: str) -> str:
    print_step("2/10 Verificando Ollama instalado y en ejecucion...")
    ollama_cmd = find_ollama_executable()

    version_result = run_subprocess([ollama_cmd, "--version"], timeout=10)
    if version_result.returncode != 0:
        raise RuntimeError(
            "Ollama no esta instalado o no esta en PATH. Instala desde https://ollama.ai/download"
        )

    try:
        _http_get_json(f"{base_url}/api/tags", timeout=8)
    except Exception as exc:
        raise RuntimeError(
            "Ollama parece instalado, pero no esta corriendo. Inicia Ollama y reintenta."
        ) from exc
    return ollama_cmd


def ensure_model(base_url: str, model_name: str, ollama_cmd: str) -> None:
    print_step(f"3/10 Verificando modelo {model_name}...")
    tags = _http_get_json(f"{base_url}/api/tags", timeout=10)
    names = {item.get("name") for item in tags.get("models", [])}
    if model_name in names:
        print_step(f"Modelo {model_name} ya disponible.")
        return

    print_step(f"Descargando modelo {model_name} con ollama pull...")
    pull_result = run_subprocess(
        [ollama_cmd, "pull", model_name],
        timeout=3600,
        capture_output=False,
    )
    if pull_result.returncode != 0:
        raise RuntimeError(f"No se pudo descargar {model_name}. Codigo de salida: {pull_result.returncode}")


def install_requirements() -> None:
    print_step("4/10 Instalando dependencias Python...")
    req_file = BASE_DIR / "requirements.txt"
    if not req_file.exists():
        raise RuntimeError(f"No existe {req_file}")
    result = run_subprocess(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--prefer-binary",
            "-r",
            str(req_file),
        ],
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Fallo instalando dependencias:\n{result.stderr}")


def warmup_xtts(model_name: str) -> None:
    print_step("5/10 Verificando descarga de XTTS-v2...")
    os.environ["COQUI_TOS_AGREED"] = "1"
    from TTS.api import TTS  # noqa: PLC0415

    model = TTS(model_name)
    try:
        model.to("cpu")
    except Exception:
        pass


def verify_voice_samples() -> None:
    print_step("6/10 Verificando muestras de voz...")
    samples_dir = BASE_DIR / "voice_samples"
    wav_files = sorted(samples_dir.glob("*.wav"))
    if not wav_files:
        raise RuntimeError(
            "No hay archivos WAV en voice_samples/. "
            "Consulta voice_samples/README.md para instrucciones."
        )
    print_step(f"Encontradas {len(wav_files)} muestras WAV.")


def audio_test() -> None:
    print_step("7/10 Test rapido de audio (grabacion + reproduccion)...")
    import sounddevice as sd  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415

    sample_rate = 16000
    duration_seconds = 3
    recording = sd.rec(int(sample_rate * duration_seconds), samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        temp_path = Path(tmp.name)
    sf.write(temp_path, recording, sample_rate)
    data, sr = sf.read(temp_path, dtype="float32")
    sd.play(data, sr)
    sd.wait()
    temp_path.unlink(missing_ok=True)


def wake_word_test() -> None:
    print_step("8/10 Test de carga de wake word model...")
    from openwakeword.model import Model  # noqa: PLC0415
    from openwakeword.utils import download_models  # noqa: PLC0415

    download_models(model_names=["hey_jarvis"])
    Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")


def llm_test(base_url: str, model_name: str) -> None:
    print_step("9/10 Test de respuesta LLM local...")
    payload: dict[str, Any] = {
        "model": model_name,
        "stream": False,
        "messages": [{"role": "user", "content": "Hola"}],
    }
    response = _http_post_json(f"{base_url}/api/chat", payload, timeout=30)
    text = response.get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError("El modelo no devolvio contenido en test de LLM.")


def _http_get_json(url: str, timeout: int = 10) -> dict[str, Any]:
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
            return json.loads(data)
    except (URLError, HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Error HTTP GET {url}: {exc}") from exc


def _http_post_json(url: str, payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
            return json.loads(data)
    except (URLError, HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Error HTTP POST {url}: {exc}") from exc


def final_message() -> None:
    print_step("10/10 Todo correcto.")
    print("Sistema listo. Ejecuta: python main.py")


def main() -> None:
    ollama_url = "http://localhost:11434"
    model_name = "mistral:7b-instruct"
    xtts_model = "tts_models/multilingual/multi-dataset/xtts_v2"

    check_python()
    ollama_cmd = check_ollama_running(ollama_url)
    ensure_model(ollama_url, model_name, ollama_cmd)
    install_requirements()
    warmup_xtts(xtts_model)
    verify_voice_samples()
    audio_test()
    wake_word_test()
    llm_test(ollama_url, model_name)
    final_message()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[SETUP][ERROR] {exc}")
        raise SystemExit(1) from exc
