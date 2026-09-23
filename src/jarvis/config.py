"""Validated, immutable runtime configuration.

Secrets are resolved from environment variables (``${NAME}``) and are never
present in ``repr`` or ``public_dict`` output.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Union


_ENV_VALUE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# Sibling filename merged over the committed base config when present. The
# committed example lives at config.local.example.json; a developer copies it to
# config.local.json (gitignored) and fills in ``${ENV}``-referenced secrets.
_LOCAL_OVERRIDE_FILENAME = "config.local.json"

# Providers are a known shape; anything else is a configuration mistake.
_PROVIDER_KINDS = {"openai_compat", "ollama"}

# ponytail: ceiling on a single provider stream timeout. A value above this is
# almost always a misconfiguration. Raise it only for a legitimate provider that
# needs a longer first-byte window; prefer tightening per-provider timeouts.
_MAX_PROVIDER_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class RuntimeSettings:
    command_deadline_ms: int = 500


@dataclass(frozen=True)
class ProviderSettings:
    fast_model_api_key: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class MemorySettings:
    db_path: str = "memory/jarvis.db"
    retention_days: int = 90
    max_recall: int = 6


@dataclass(frozen=True)
class SecuritySettings:
    confirm_destructive: bool = True
    allow_arbitrary_shell: bool = False


@dataclass(frozen=True)
class AudioSettings:
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 1024
    input_device: Optional[int] = None
    output_device: Optional[int] = None


@dataclass(frozen=True)
class ActivationSettings:
    mode: str = "wake_word"  # wake_word | push_to_talk | manual | continuous
    wake_word: str = "jarvis"
    cooldown_seconds: float = 1.2
    conversation_timeout_seconds: float = 8.0


@dataclass(frozen=True)
class STTSettings:
    provider: str = "whisper"  # whisper | sapi | alibaba_qwen
    model: str = "tiny"
    language: str = "es"
    device: str = "cpu"
    api_key: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class TTSSettings:
    provider: str = "local"  # local | sapi | alibaba_qwen
    voice: str = ""
    language: str = "es"
    api_key: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class AlibabaSettings:
    """Alibaba Cloud Model Studio settings shared by the STT and TTS adapters.

    The API key lives on ``stt.api_key`` / ``tts.api_key`` like every other
    provider; both normally resolve to the same ``${DASHSCOPE_API_KEY}``.
    """

    region: str = "singapore"  # singapore | beijing
    workspace_id: Optional[str] = None
    stt_model: str = ""
    tts_model: str = ""


@dataclass(frozen=True)
class ModelProviderConfig:
    name: str = ""
    kind: str = "openai_compat"  # openai_compat | ollama
    base_url: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False)
    model: str = ""
    timeout_seconds: float = 30.0
    temperature: float = 0.7


@dataclass(frozen=True)
class ModelSettings:
    providers: tuple[ModelProviderConfig, ...] = ()


@dataclass(frozen=True)
class HermesSettings:
    command: tuple[str, ...] = ()
    timeout_seconds: float = 300.0
    restart_max: int = 3


@dataclass(frozen=True)
class ToolSettings:
    allowlist: frozenset[str] = frozenset()
    confirmation_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class StartupSettings:
    cue_phrase: str = "Jarvis listo"
    startup_sound: Optional[str] = None


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"


@dataclass(frozen=True)
class TimeoutSettings:
    stt_seconds: float = 15.0
    tts_seconds: float = 20.0
    model_seconds: float = 60.0
    hermes_seconds: float = 300.0
    tool_seconds: float = 30.0


@dataclass(frozen=True)
class LatencySettings:
    target_first_audio_ms: int = 1500


@dataclass(frozen=True)
class RuntimeConfig:
    runtime: RuntimeSettings
    providers: ProviderSettings
    memory: MemorySettings
    security: SecuritySettings
    audio: AudioSettings = field(default_factory=AudioSettings)
    activation: ActivationSettings = field(default_factory=ActivationSettings)
    stt: STTSettings = field(default_factory=STTSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    alibaba: AlibabaSettings = field(default_factory=AlibabaSettings)
    models: ModelSettings = field(default_factory=ModelSettings)
    hermes: HermesSettings = field(default_factory=HermesSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    startup: StartupSettings = field(default_factory=StartupSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    timeouts: TimeoutSettings = field(default_factory=TimeoutSettings)
    latency: LatencySettings = field(default_factory=LatencySettings)

    def public_dict(self) -> dict[str, dict[str, Any]]:
        return {
            "runtime": {"command_deadline_ms": self.runtime.command_deadline_ms},
            "providers": {
                "fast_model_api_key": (
                    "<redacted>" if self.providers.fast_model_api_key is not None else None
                )
            },
            "memory": {
                "db_path": self.memory.db_path,
                "retention_days": self.memory.retention_days,
                "max_recall": self.memory.max_recall,
            },
            "security": {
                "confirm_destructive": self.security.confirm_destructive,
                "allow_arbitrary_shell": self.security.allow_arbitrary_shell,
            },
            "audio": {
                "sample_rate": self.audio.sample_rate,
                "channels": self.audio.channels,
                "input_device": self.audio.input_device,
                "output_device": self.audio.output_device,
            },
            "activation": {
                "mode": self.activation.mode,
                "wake_word": self.activation.wake_word,
                "cooldown_seconds": self.activation.cooldown_seconds,
                "conversation_timeout_seconds": self.activation.conversation_timeout_seconds,
            },
            "stt": {
                "provider": self.stt.provider,
                "model": self.stt.model,
                "language": self.stt.language,
                "device": self.stt.device,
                "api_key": "<redacted>" if self.stt.api_key is not None else None,
            },
            "tts": {
                "provider": self.tts.provider,
                "voice": self.tts.voice,
                "language": self.tts.language,
                "api_key": "<redacted>" if self.tts.api_key is not None else None,
            },
            "alibaba": {
                "region": self.alibaba.region,
                "workspace_id": self.alibaba.workspace_id,
                "stt_model": self.alibaba.stt_model,
                "tts_model": self.alibaba.tts_model,
            },
            "models": {
                "providers": [
                    {
                        "name": p.name,
                        "kind": p.kind,
                        "base_url": p.base_url,
                        "api_key": "<redacted>" if p.api_key is not None else None,
                        "model": p.model,
                    }
                    for p in self.models.providers
                ]
            },
            "hermes": {
                "command": list(self.hermes.command),
                "timeout_seconds": self.hermes.timeout_seconds,
                "restart_max": self.hermes.restart_max,
            },
            "tools": {
                "allowlist": sorted(self.tools.allowlist),
                "confirmation_timeout_seconds": self.tools.confirmation_timeout_seconds,
            },
            "startup": {
                "cue_phrase": self.startup.cue_phrase,
                "startup_sound": self.startup.startup_sound,
            },
            "logging": {"level": self.logging.level},
            "timeouts": {
                "stt_seconds": self.timeouts.stt_seconds,
                "tts_seconds": self.timeouts.tts_seconds,
                "model_seconds": self.timeouts.model_seconds,
                "hermes_seconds": self.timeouts.hermes_seconds,
                "tool_seconds": self.timeouts.tool_seconds,
            },
            "latency": {"target_first_audio_ms": self.latency.target_first_audio_ms},
        }


def load_config(
    path: Union[str, Path], environ: Optional[Mapping[str, str]] = None
) -> RuntimeConfig:
    """Load configuration from *path*, expanding provider secrets from *environ*.

    A sibling gitignored ``config.local.json`` is merged over *path* before
    validation, so local model-provider secrets (API keys) never live in the
    committed, secret-free base configuration.
    """
    base_path = Path(path)
    document = _read_json_object(base_path)
    local_path = base_path.with_name(_LOCAL_OVERRIDE_FILENAME)
    if local_path.is_file():
        document = _merge_config_documents(document, _read_json_object(local_path))

    env = os.environ if environ is None else environ
    runtime_data = _section(document, "runtime", required=True)
    providers_data = _section(document, "providers")
    memory_data = _section(document, "memory")
    security_data = _section(document, "security")
    audio_data = _section(document, "audio")
    activation_data = _section(document, "activation")
    stt_data = _section(document, "stt")
    tts_data = _section(document, "tts")
    alibaba_data = _section(document, "alibaba")
    models_data = _section(document, "models")
    hermes_data = _section(document, "hermes")
    tools_data = _section(document, "tools")
    startup_data = _section(document, "startup")
    logging_data = _section(document, "logging")
    timeouts_data = _section(document, "timeouts")
    latency_data = _section(document, "latency")

    deadline = runtime_data.get("command_deadline_ms", 500)
    if isinstance(deadline, bool) or not isinstance(deadline, int) or deadline <= 0:
        raise ValueError("runtime.command_deadline_ms must be a positive integer")

    return RuntimeConfig(
        runtime=RuntimeSettings(command_deadline_ms=deadline),
        providers=ProviderSettings(
            fast_model_api_key=_resolve_secret(
                providers_data.get("fast_model_api_key"), env
            )
        ),
        memory=MemorySettings(
            db_path=_string(memory_data, "db_path", "memory/jarvis.db"),
            retention_days=_positive_int(memory_data, "retention_days", 90, "memory.retention_days"),
            max_recall=_positive_int(memory_data, "max_recall", 6, "memory.max_recall"),
        ),
        security=SecuritySettings(
            confirm_destructive=_bool(security_data, "confirm_destructive", True),
            allow_arbitrary_shell=_bool(security_data, "allow_arbitrary_shell", False),
        ),
        audio=AudioSettings(
            sample_rate=_positive_int(audio_data, "sample_rate", 16000, "audio.sample_rate"),
            channels=_positive_int(audio_data, "channels", 1, "audio.channels"),
            chunk_size=_positive_int(audio_data, "chunk_size", 1024, "audio.chunk_size"),
            input_device=_optional_int(audio_data, "input_device"),
            output_device=_optional_int(audio_data, "output_device"),
        ),
        activation=ActivationSettings(
            mode=_string(activation_data, "mode", "wake_word"),
            wake_word=_string(activation_data, "wake_word", "jarvis"),
            cooldown_seconds=_positive_number(activation_data, "cooldown_seconds", 1.2, "activation.cooldown_seconds"),
            conversation_timeout_seconds=_positive_number(activation_data, "conversation_timeout_seconds", 8.0, "activation.conversation_timeout_seconds"),
        ),
        stt=STTSettings(
            provider=_choice(stt_data, "provider", "whisper", {"whisper", "sapi", "alibaba_qwen"}, "stt"),
            model=_string(stt_data, "model", "tiny"),
            language=_string(stt_data, "language", "es"),
            device=_string(stt_data, "device", "cpu"),
            api_key=_resolve_secret(stt_data.get("api_key"), env),
        ),
        tts=TTSSettings(
            provider=_choice(tts_data, "provider", "local", {"local", "sapi", "alibaba_qwen"}, "tts"),
            voice=_string(tts_data, "voice", ""),
            language=_string(tts_data, "language", "es"),
            api_key=_resolve_secret(tts_data.get("api_key"), env),
        ),
        alibaba=AlibabaSettings(
            region=_choice(alibaba_data, "region", "singapore", {"singapore", "beijing"}, "alibaba"),
            workspace_id=_optional_string(alibaba_data, "workspace_id"),
            stt_model=_string(alibaba_data, "stt_model", "qwen3-asr-flash-realtime"),
            tts_model=_string(alibaba_data, "tts_model", "qwen3-tts-flash-realtime"),
        ),
        models=ModelSettings(providers=tuple(_parse_providers(models_data, env))),
        hermes=HermesSettings(
            command=tuple(_parse_command(hermes_data)),
            timeout_seconds=_positive_number(hermes_data, "timeout_seconds", 300.0, "hermes.timeout_seconds"),
            restart_max=_positive_int(hermes_data, "restart_max", 3, "hermes.restart_max"),
        ),
        tools=ToolSettings(
            allowlist=frozenset(_parse_allowlist(tools_data)),
            confirmation_timeout_seconds=_positive_number(tools_data, "confirmation_timeout_seconds", 30.0, "tools.confirmation_timeout_seconds"),
        ),
        startup=StartupSettings(
            cue_phrase=_string(startup_data, "cue_phrase", "Jarvis listo"),
            startup_sound=_optional_string(startup_data, "startup_sound"),
        ),
        logging=LoggingSettings(level=_string(logging_data, "level", "INFO")),
        timeouts=TimeoutSettings(
            stt_seconds=_positive_number(timeouts_data, "stt_seconds", 15.0, "timeouts.stt_seconds"),
            tts_seconds=_positive_number(timeouts_data, "tts_seconds", 20.0, "timeouts.tts_seconds"),
            model_seconds=_positive_number(timeouts_data, "model_seconds", 60.0, "timeouts.model_seconds"),
            hermes_seconds=_positive_number(timeouts_data, "hermes_seconds", 300.0, "timeouts.hermes_seconds"),
            tool_seconds=_positive_number(timeouts_data, "tool_seconds", 30.0, "timeouts.tool_seconds"),
        ),
        latency=LatencySettings(
            target_first_audio_ms=_positive_int(latency_data, "target_first_audio_ms", 1500, "latency.target_first_audio_ms"),
        ),
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as config_file:
        document = json.load(config_file)
    if not isinstance(document, dict):
        raise ValueError("configuration must be a JSON object")
    return document


def _merge_config_documents(
    base: Mapping[str, Any], override: Mapping[str, Any]
) -> dict[str, Any]:
    """Deep-merge *override* over *base*; provider lists merge by ``name``."""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if key == "providers" and isinstance(value, list):
            merged[key] = _merge_provider_lists(base.get(key), value)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            merged[key] = _merge_config_documents(base[key], value)
        else:
            merged[key] = value
    return merged


def _merge_provider_lists(base: Any, override: Any) -> list[dict[str, Any]]:
    """Merge provider lists by ``name``.

    Named matches merge per-field with the override winning and stay in their
    base position; providers that only appear in the override are prepended (in
    override order) so cloud providers land before the committed local Ollama
    provider.
    """
    merged: list[dict[str, Any]] = []
    if isinstance(base, list):
        for item in base:
            if not isinstance(item, dict):
                raise ValueError("each models.providers entry must be an object")
            merged.append(dict(item))

    prepended: list[dict[str, Any]] = []
    if isinstance(override, list):
        for item in override:
            if not isinstance(item, dict):
                raise ValueError("each models.providers entry must be an object")
            name = item.get("name")
            matched = False
            if name:
                for index, existing in enumerate(merged):
                    if existing.get("name") == name:
                        merged[index] = {**existing, **item}
                        matched = True
                        break
            if not matched:
                prepended.append(dict(item))

    return prepended + merged


def _section(
    document: Mapping[str, Any], name: str, required: bool = False
) -> Mapping[str, Any]:
    if name not in document:
        if required:
            raise ValueError("configuration requires a runtime object")
        return {}
    section = document[name]
    if not isinstance(section, dict):
        raise ValueError("configuration section {!r} must be an object".format(name))
    return section


def _resolve_secret(value: Optional[str], environ: Mapping[str, str]) -> Optional[str]:
    if value is None:
        return None
    match = _ENV_VALUE.fullmatch(value)
    if not match:
        return value
    name = match.group(1)
    if name not in environ:
        raise ValueError("environment variable {!r} is required".format(name))
    return environ[name]


def _string(data: Mapping[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError("{!r} must be a string".format(key))
    return value


def _choice(
    data: Mapping[str, Any], key: str, default: str, allowed: set[str], label: str
) -> str:
    value = _string(data, key, default).strip().lower()
    if value in allowed:
        return value
    if value == "cloud":
        raise ValueError("{} provider 'cloud' is out of scope for the offline loop".format(label))
    raise ValueError(
        "{} provider must be one of {}: got {!r}".format(label, sorted(allowed), value)
    )


def _optional_string(data: Mapping[str, Any], key: str) -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("{!r} must be a string".format(key))
    return value


def _bool(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError("{!r} must be a boolean".format(key))
    return value


def _positive_int(data: Mapping[str, Any], key: str, default: int, label: str) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("{} must be a positive integer".format(label))
    return value


def _optional_int(data: Mapping[str, Any], key: str) -> Optional[int]:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{!r} must be an integer".format(key))
    return value


def _positive_number(data: Mapping[str, Any], key: str, default: float, label: str) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("{} must be a positive number".format(label))
    return float(value)


def _parse_providers(data: Mapping[str, Any], environ: Mapping[str, str]) -> list[ModelProviderConfig]:
    raw = data.get("providers", [])
    if not isinstance(raw, list):
        raise ValueError("models.providers must be a list")
    providers: list[ModelProviderConfig] = []
    seen_names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each models.providers entry must be an object")
        name = _string(item, "name", "")
        if name and name in seen_names:
            raise ValueError("duplicate model provider name {!r}".format(name))
        if name:
            seen_names.add(name)
        providers.append(
            ModelProviderConfig(
                name=name,
                kind=_provider_kind(item),
                base_url=_optional_string(item, "base_url"),
                api_key=_resolve_secret(item.get("api_key"), environ),
                model=_string(item, "model", ""),
                timeout_seconds=_bounded_timeout(item),
                temperature=_number(item, "temperature", 0.7),
            )
        )
    return providers


def _provider_kind(item: Mapping[str, Any]) -> str:
    kind = _string(item, "kind", "openai_compat").strip().lower()
    if kind not in _PROVIDER_KINDS:
        raise ValueError(
            "models.providers kind must be one of {}: got {!r}".format(
                sorted(_PROVIDER_KINDS), kind
            )
        )
    return kind


def _bounded_timeout(item: Mapping[str, Any]) -> float:
    value = _positive_number(
        item, "timeout_seconds", 30.0, "models.providers.timeout_seconds"
    )
    if value > _MAX_PROVIDER_TIMEOUT_SECONDS:
        raise ValueError(
            "models.providers.timeout_seconds must not exceed {:.0f} seconds".format(
                _MAX_PROVIDER_TIMEOUT_SECONDS
            )
        )
    return value


def _number(data: Mapping[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{!r} must be a number".format(key))
    return float(value)


def _parse_command(data: Mapping[str, Any]) -> list[str]:
    raw = data.get("command", [])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(part, str) for part in raw):
        raise ValueError("hermes.command must be a list of strings")
    return list(raw)


def _parse_allowlist(data: Mapping[str, Any]) -> list[str]:
    raw = data.get("allowlist", [])
    if not isinstance(raw, list) or not all(isinstance(part, str) for part in raw):
        raise ValueError("tools.allowlist must be a list of strings")
    return list(raw)
