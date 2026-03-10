from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
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
from voice.audio_utils import check_microphone_capture, format_audio_device, get_audio_devices, play_audio, resolve_input_device
from voice.stt import STTService
from voice.tts import TTSService, sanitize_voice_text
from voice.wake_word import WakeWordListener


def configure_logging(level: int = logging.INFO, log_file: Path | None = None) -> None:
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
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(file_handler)


LOGGER = logging.getLogger("jarvis.main")


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return base_dir / path


def _normalize_for_wake(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    lowered = without_accents.lower()
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    return " ".join(cleaned.split())


def _extract_command_from_wake(text: str) -> tuple[bool, str]:
    norm = _normalize_for_wake(text)
    if not norm:
        return (False, "")

    tokens = norm.split()
    wake_aliases = {
        "jarvis",
        "jarbis",
        "yarvis",
        "jarviss",
        "jarviz",
        "jervis",
        "harvis",
        "jarvi",
        "jarbisz",
    }

    def _looks_like_wake(token: str) -> bool:
        if token in wake_aliases:
            return True
        if len(token) >= 4 and token.startswith("jarv"):
            return True
        if len(token) >= 4 and SequenceMatcher(a=token, b="jarvis").ratio() >= 0.75:
            return True
        return False

    for idx, token in enumerate(tokens):
        if _looks_like_wake(token):
            command_tokens = tokens[idx + 1 :]
            return (True, " ".join(command_tokens).strip())

        if idx + 1 < len(tokens):
            merged = token + tokens[idx + 1]
            if _looks_like_wake(merged):
                command_tokens = tokens[idx + 2 :]
                return (True, " ".join(command_tokens).strip())

    return (False, "")


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

    mic_ok, mic_rms, mic_message = check_microphone_capture(
        input_device_index=resolved_input_device,
        sample_rate=sample_rate,
        channels=channels,
        probe_seconds=0.9,
    )
    if not mic_ok:
        LOGGER.error("Microphone check failed (rms=%.2f): %s", mic_rms, mic_message)
        raise RuntimeError(
            "No hay acceso util al microfono. "
            "En Windows revisa: Configuracion > Privacidad y seguridad > Microfono, "
            "y habilita acceso para aplicaciones de escritorio."
        )
    LOGGER.info("Microphone check OK (rms=%.2f)", mic_rms)

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
        cooldown_seconds=float(config["wake_word"].get("cooldown_seconds", 1.5)),
        hard_trigger_hits=int(config["wake_word"].get("hard_trigger_hits", 1)),
        soft_trigger_hits=int(config["wake_word"].get("soft_trigger_hits", 4)),
        soft_trigger_ratio=float(config["wake_word"].get("soft_trigger_ratio", 0.6)),
        voice_rms_for_soft_trigger=float(config["wake_word"].get("voice_rms_for_soft_trigger", 30.0)),
        score_log_interval_seconds=float(config["wake_word"].get("score_log_interval_seconds", 5.0)),
    )

    wake_cfg = config["wake_word"]

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
        "openwakeword_enabled": bool(wake_cfg.get("openwakeword_enabled", True)),
        "wake_stt_fallback_enabled": bool(wake_cfg.get("stt_fallback_enabled", True)),
        "wake_stt_max_record_seconds": float(wake_cfg.get("stt_fallback_max_record_seconds", 2.2)),
        "wake_stt_probe_interval_seconds": float(wake_cfg.get("stt_fallback_probe_interval_seconds", 1.0)),
        "continuous_listen_when_disabled": bool(wake_cfg.get("continuous_listen_when_disabled", True)),
        "continuous_require_keyword": bool(wake_cfg.get("continuous_require_keyword", False)),
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
    openwakeword_enabled: bool = components["openwakeword_enabled"]
    wake_stt_fallback_enabled: bool = components["wake_stt_fallback_enabled"]
    wake_stt_max_record_seconds: float = components["wake_stt_max_record_seconds"]
    wake_stt_probe_interval_seconds: float = components["wake_stt_probe_interval_seconds"]
    continuous_listen_when_disabled: bool = components["continuous_listen_when_disabled"]
    continuous_require_keyword: bool = components["continuous_require_keyword"]
    stt_sample_rate_profiles: list[int] = []
    for sr in [stt_service.capture_sample_rate, 48000, 32000, 16000]:
        if sr not in stt_sample_rate_profiles:
            stt_sample_rate_profiles.append(sr)
    stt_profile_index = 0
    stt_input_device_profiles: list[int | None] = [stt_service.input_device]
    for device in get_audio_devices():
        idx = int(device.get("index"))
        if int(device.get("max_input_channels", 0)) < 1:
            continue
        name = str(device.get("name", "")).lower()
        if any(bad in name for bad in ("stereo mix", "mezcla", "loopback", "output", "mapper")):
            continue
        if idx not in stt_input_device_profiles:
            stt_input_device_profiles.append(idx)
    stt_input_profile_index = 0
    empty_stt_streak = 0
    pending_keyword_command = False

    if openwakeword_enabled:
        wake_listener.start()
    else:
        LOGGER.info("OpenWakeWord desactivado. Usando activacion por STT (frase con 'jarvis').")
        if continuous_listen_when_disabled:
            LOGGER.info("Modo escucha continua STT activo (sin wake word).")
    play_phrase(audio_cache, tts_service, "Jarvis a tu servicio", blocking=True)

    LOGGER.info("Jarvis listo. Esperando wake word...")
    last_stt_wake_probe = 0.0
    try:
        while True:
            user_text = ""
            if openwakeword_enabled and wake_listener.wait_for_wake_word(timeout=0.2):
                user_text = stt_service.transcribe_from_mic()
            elif (not openwakeword_enabled) and continuous_listen_when_disabled:
                spoken_text = stt_service.transcribe_from_mic()
                if pending_keyword_command:
                    if spoken_text:
                        user_text = spoken_text
                        pending_keyword_command = False
                    else:
                        user_text = ""
                elif spoken_text:
                    detected, inline_command = _extract_command_from_wake(spoken_text)
                    if detected:
                        user_text = inline_command if inline_command else ""
                        if not user_text and continuous_require_keyword:
                            pending_keyword_command = True
                            LOGGER.info("Wake detectado. Di tu comando...")
                            continue
                        if not user_text:
                            LOGGER.info("Wake en modo continuo: %s", spoken_text)
                            continue
                    else:
                        if continuous_require_keyword:
                            LOGGER.info("Ignorando frase sin palabra clave en modo continuo: %s", spoken_text)
                            empty_stt_streak = 0
                            continue
                        user_text = spoken_text
                else:
                    user_text = ""
            elif wake_stt_fallback_enabled:
                now = time.monotonic()
                if (now - last_stt_wake_probe) < wake_stt_probe_interval_seconds:
                    continue
                last_stt_wake_probe = now

                wake_text = stt_service.transcribe_for_wake(max_record_seconds=wake_stt_max_record_seconds)
                if not wake_text:
                    continue

                detected, inline_command = _extract_command_from_wake(wake_text)
                if not detected:
                    continue

                LOGGER.info("Wake STT fallback detected: %s", wake_text)
                if inline_command:
                    user_text = inline_command
                else:
                    user_text = stt_service.transcribe_from_mic()
            else:
                continue

            if not user_text:
                empty_stt_streak += 1
                if empty_stt_streak > 0 and (empty_stt_streak % 3 == 0) and len(stt_sample_rate_profiles) > 1:
                    stt_profile_index = (stt_profile_index + 1) % len(stt_sample_rate_profiles)
                    new_rate = stt_sample_rate_profiles[stt_profile_index]
                    stt_service.set_capture_sample_rate(new_rate)
                    LOGGER.warning(
                        "STT vacio repetido (%d intentos). Cambiando sample_rate de captura a %d Hz.",
                        empty_stt_streak,
                        new_rate,
                    )
                if empty_stt_streak > 0 and (empty_stt_streak % 9 == 0) and len(stt_input_device_profiles) > 1:
                    stt_input_profile_index = (stt_input_profile_index + 1) % len(stt_input_device_profiles)
                    new_input = stt_input_device_profiles[stt_input_profile_index]
                    stt_service.input_device = new_input
                    stt_profile_index = 0
                    stt_service.set_capture_sample_rate(stt_sample_rate_profiles[stt_profile_index])
                    LOGGER.warning(
                        "STT vacio persistente (%d intentos). Cambiando input_device a %s.",
                        empty_stt_streak,
                        format_audio_device(new_input),
                    )
                LOGGER.warning("No se detecto voz valida. Esperando nueva activacion...")
                continue
            empty_stt_streak = 0

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
        if openwakeword_enabled:
            wake_listener.stop()


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent
    log_file_env = os.getenv("JARVIS_LOG_FILE")
    log_path = Path(log_file_env) if log_file_env else (base_dir / "logs" / "jarvis.log")
    configure_logging(log_file=log_path)
    run()
