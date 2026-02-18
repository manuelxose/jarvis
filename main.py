from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import colorlog
import yaml

from actions.ais_monitor import AISMonitor
from actions.pc_control import PCController
from actions.trading_monitor import TradingMonitor
from actions.web_search import WebSearch
from brain.action_router import ActionRouter
from brain.llm import OllamaClient
from brain.memory import MemoryStore
from brain.prompt_builder import build_system_prompt
from cache.audio_cache import AudioCache
from voice.audio_utils import format_audio_device, play_audio, resolve_input_device
from voice.stt import STTService
from voice.tts import TTSService, sanitize_voice_text
from voice.wake_word import WakeWordListener


def configure_logging(level: int = logging.INFO) -> None:
    formatter = colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
    handler = colorlog.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)


LOGGER = logging.getLogger("jarvis.main")


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return base_dir / path


def generate_tts_with_timeout(tts: TTSService, text: str, timeout_seconds: float = 20.0) -> Path | None:
    result: dict[str, Any] = {"path": None, "error": None}

    def _worker() -> None:
        try:
            result["path"] = tts.generate_speech(text)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout_seconds)

    if thread.is_alive():
        return None
    if result["error"]:
        raise result["error"]
    return result["path"]


def play_phrase(cache: AudioCache, tts: TTSService, phrase: str, blocking: bool = True) -> None:
    cached = cache.get_cached_audio(phrase)
    if cached:
        play_audio(cached, blocking=blocking)
        return
    tts.speak(phrase, blocking=blocking)


def build_runtime_components(base_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    llm_cfg = config["llm"]
    tts_cfg = config["tts"]
    stt_cfg = config["stt"]
    audio_cfg = config["audio"]
    ais_cfg = config["ais"]
    trading_cfg = config["trading"]
    jarvis_cfg = config["jarvis"]

    memory_store = MemoryStore(base_dir / "memory" / "conversations.json")
    memory_store.clear_old_messages(days=7)

    ollama_client = OllamaClient(
        base_url=llm_cfg.get("base_url", "http://localhost:11434"),
        model=llm_cfg.get("model", "mistral:7b-instruct"),
        temperature=float(llm_cfg.get("temperature", 0.7)),
        timeout=int(llm_cfg.get("timeout", 30)),
    )
    ollama_ready = ollama_client.check_availability()
    if not ollama_ready:
        LOGGER.error("Ollama no esta disponible. Arrancalo y confirma el puerto 11434.")
    elif not ollama_client.check_model_available():
        LOGGER.error(
            "Modelo %s no encontrado en Ollama. Ejecuta: ollama pull %s",
            ollama_client.model,
            ollama_client.model,
        )
        ollama_ready = False

    audio_cache = AudioCache(base_dir / "cache" / "cached_responses")

    tts_service = TTSService(
        tts_config=tts_cfg,
        cache=audio_cache,
        base_dir=base_dir,
    )
    tts_service.pregenerate_common_cache(background=True)

    sample_rate = int(audio_cfg.get("sample_rate", 16000))
    channels = int(audio_cfg.get("channels", 1))
    resolved_input_device = resolve_input_device(
        preferred_index=audio_cfg.get("input_device"),
        sample_rate=sample_rate,
        channels=channels,
        auto_select=bool(audio_cfg.get("auto_select_input", True)),
    )
    LOGGER.info("Input audio device selected: %s", format_audio_device(resolved_input_device))

    effective_audio_cfg = dict(audio_cfg)
    effective_audio_cfg["input_device"] = resolved_input_device

    stt_service = STTService(stt_config=stt_cfg, audio_config=effective_audio_cfg)

    pc_controller = PCController()
    ais_monitor = AISMonitor(
        host=ais_cfg.get("host", "localhost"),
        port=int(ais_cfg.get("port", 10110)),
    ) if bool(ais_cfg.get("enabled", True)) else None
    trading_monitor = TradingMonitor(
        mt4_log_path=resolve_path(base_dir, trading_cfg.get("mt4_log_path", "")),
    ) if bool(trading_cfg.get("enabled", True)) else None
    web_search = WebSearch(workspace_root=base_dir.parent, allow_online=True)

    router = ActionRouter(
        llm_client=ollama_client if ollama_ready else None,
        pc_control=pc_controller,
        ais_monitor=ais_monitor,
        trading_monitor=trading_monitor,
        web_search=web_search,
    )

    system_prompt = build_system_prompt(jarvis_cfg.get("user_name", "Manuel"))

    wake_listener = WakeWordListener(
        model_name=config["wake_word"].get("model", "hey_jarvis"),
        threshold=float(config["wake_word"].get("threshold", 0.35)),
        sample_rate=sample_rate,
        input_device_index=resolved_input_device,
    )

    return {
        "memory": memory_store,
        "ollama": ollama_client,
        "ollama_ready": ollama_ready,
        "audio_cache": audio_cache,
        "tts": tts_service,
        "stt": stt_service,
        "router": router,
        "system_prompt": system_prompt,
        "wake_listener": wake_listener,
        "max_history": int(llm_cfg.get("max_history_messages", 10)),
    }


def run() -> None:
    base_dir = Path(__file__).resolve().parent
    config = load_config(base_dir / "config.yaml")
    components = build_runtime_components(base_dir, config)

    memory_store: MemoryStore = components["memory"]
    ollama_client: OllamaClient = components["ollama"]
    ollama_ready: bool = components["ollama_ready"]
    audio_cache: AudioCache = components["audio_cache"]
    tts_service: TTSService = components["tts"]
    stt_service: STTService = components["stt"]
    router: ActionRouter = components["router"]
    system_prompt: str = components["system_prompt"]
    wake_listener: WakeWordListener = components["wake_listener"]
    max_history: int = components["max_history"]

    wake_listener.start()
    play_phrase(audio_cache, tts_service, "Jarvis a tu servicio", blocking=True)

    LOGGER.info("Jarvis listo. Esperando wake word...")
    try:
        while True:
            if not wake_listener.wait_for_wake_word(timeout=0.2):
                continue

            user_text = stt_service.transcribe_from_mic()
            if not user_text:
                LOGGER.warning("No se detecto voz valida. Esperando nueva activacion...")
                continue

            LOGGER.info("Usuario: %s", user_text)
            memory_store.add_message("user", user_text)

            processing_audio = audio_cache.get_cached_audio("Procesando tu solicitud")
            if processing_audio:
                play_audio(processing_audio, blocking=False)

            route_result = router.route(user_text)
            if route_result.get("handled"):
                assistant_text = route_result.get("response") or "Entendido."
            else:
                if not ollama_ready:
                    assistant_text = (
                        "No tengo conexion con Ollama ahora mismo. "
                        "Arranca Ollama y vuelve a intentarlo."
                    )
                else:
                    history = memory_store.get_recent_messages(n=max_history)
                    llm_messages = [
                        {"role": msg["role"], "content": msg["content"]}
                        for msg in history
                        if msg.get("role") in {"user", "assistant"}
                    ]
                    try:
                        assistant_text = ollama_client.chat(
                            messages=llm_messages,
                            system_prompt=system_prompt,
                        )
                    except Exception as llm_error:
                        LOGGER.error("LLM fallo: %s", llm_error)
                        assistant_text = "No he podido contactar con el modelo local."

            assistant_text = sanitize_voice_text(assistant_text)
            memory_store.add_message("assistant", assistant_text)
            LOGGER.info("Jarvis: %s", assistant_text)

            try:
                response_audio = generate_tts_with_timeout(tts_service, assistant_text, timeout_seconds=20.0)
                if response_audio is None:
                    LOGGER.warning("TTS tardo mas de 20 segundos. Respuesta solo en consola.")
                else:
                    play_audio(response_audio, blocking=True)
            except Exception as tts_error:
                LOGGER.error("Fallo TTS: %s", tts_error)
    except KeyboardInterrupt:
        LOGGER.info("Interrupcion recibida. Cerrando Jarvis...")
    finally:
        wake_listener.stop()


if __name__ == "__main__":
    configure_logging()
    run()
