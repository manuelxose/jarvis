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
    """``runtime`` section: turn-level deadlines."""
    command_deadline_ms: int = 500


@dataclass(frozen=True)
class MemorySettings:
    """``memory`` section: SQLite path and recall size."""
    db_path: str = "memory/jarvis.db"
    max_recall: int = 6


@dataclass(frozen=True)
class AudioSettings:
    """``audio`` section: capture format, devices and barge-in."""
    sample_rate: int = 16000
    channels: int = 1
    input_device: Optional[int] = None
    output_device: Optional[int] = None
    # Energy barge-in: only safe with a headset (no acoustic echo cancellation).
    barge_in: bool = False


@dataclass(frozen=True)
class ActivationSettings:
    """``activation`` section: how the voice loop opens a turn."""
    mode: str = "wake_word"  # wake_word | push_to_talk | manual | continuous
    wake_word: str = "jarvis"
    cooldown_seconds: float = 1.2
    conversation_timeout_seconds: float = 8.0


@dataclass(frozen=True)
class STTSettings:
    """``stt`` section: speech-to-text provider and model."""
    provider: str = "whisper"  # whisper | sapi | alibaba_qwen
    model: str = "tiny"
    language: str = "es"
    device: str = "cpu"
    api_key: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class TTSSettings:
    """``tts`` section: text-to-speech provider, voice and clone worker."""
    provider: str = "local"  # local | sapi | alibaba_qwen | qwen_clone
    voice: str = ""
    language: str = "es"
    api_key: Optional[str] = field(default=None, repr=False)
    # qwen_clone: local Faster Qwen3-TTS worker (empty = platform default path)
    worker_python: str = ""
    profile_dir: str = ""
    model: str = ""
    chunk_size: int = 4
    # qwen_clone: seconds a turn waits for a still-loading clone before SAPI (0 = never)
    warmup_wait_seconds: float = 0.0


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
    """One entry of ``models.providers``: an OpenAI-compatible or Ollama endpoint."""
    name: str = ""
    kind: str = "openai_compat"  # openai_compat | ollama
    base_url: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False)
    model: str = ""
    timeout_seconds: float = 30.0
    temperature: float = 0.7
    # USD per million tokens, for cost telemetry and the daily cap (0 = unpriced)
    input_usd_per_million: float = 0.0
    output_usd_per_million: float = 0.0
    # Extra top-level request fields for openai_compat providers
    extra_body: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelSettings:
    """``models`` section: provider chain (first is primary) and daily spend cap."""
    providers: tuple[ModelProviderConfig, ...] = ()
    # Hard daily spend cap across cloud LLM providers; once reached, turns fall
    # back to local models until midnight.
    max_daily_usd: float = 1.0


@dataclass(frozen=True)
class HermesSettings:
    """``hermes`` section: the supervised agent child process."""
    command: tuple[str, ...] = ()
    timeout_seconds: float = 300.0
    restart_max: int = 3


@dataclass(frozen=True)
class ToolSettings:
    """``tools`` section: allowlist and confirmation timeout."""
    allowlist: frozenset[str] = frozenset()
    confirmation_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class RuntimeConfig:
    """The whole validated configuration (``config.json`` merged with ``config.local.json``)."""
    runtime: RuntimeSettings
    memory: MemorySettings
    audio: AudioSettings = field(default_factory=AudioSettings)
    activation: ActivationSettings = field(default_factory=ActivationSettings)
    stt: STTSettings = field(default_factory=STTSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    alibaba: AlibabaSettings = field(default_factory=AlibabaSettings)
    models: ModelSettings = field(default_factory=ModelSettings)
    hermes: HermesSettings = field(default_factory=HermesSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    # Desktop/daemon sections, validated at load by their owning modules and kept as plain
    # mappings: claps (ClapTuning + enabled), welcome (StartupOptions),
    # workspace (default_profile + profiles), desktop (scopes, trusted, apps),
    # daemon (hotkey, wake_word, control_port...). None of them hold secrets.
    claps: Mapping[str, Any] = field(default_factory=dict)
    welcome: Mapping[str, Any] = field(default_factory=dict)
    workspace: Mapping[str, Any] = field(default_factory=dict)
    desktop: Mapping[str, Any] = field(default_factory=dict)
    daemon: Mapping[str, Any] = field(default_factory=dict)
    voice: Mapping[str, Any] = field(default_factory=dict)  # VoicePolicy

    def public_dict(self) -> dict[str, dict[str, Any]]:
        return {
            "runtime": {"command_deadline_ms": self.runtime.command_deadline_ms},
            "memory": {
                "db_path": self.memory.db_path,
                "max_recall": self.memory.max_recall,
            },
            "audio": {
                "sample_rate": self.audio.sample_rate,
                "channels": self.audio.channels,
                "input_device": self.audio.input_device,
                "output_device": self.audio.output_device,
                "barge_in": self.audio.barge_in,
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
                "worker_python": self.tts.worker_python,
                "profile_dir": self.tts.profile_dir,
                "model": self.tts.model,
                "chunk_size": self.tts.chunk_size,
                "warmup_wait_seconds": self.tts.warmup_wait_seconds,
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
                        "input_usd_per_million": p.input_usd_per_million,
                        "output_usd_per_million": p.output_usd_per_million,
                    }
                    for p in self.models.providers
                ],
                "max_daily_usd": self.models.max_daily_usd,
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
            "claps": dict(self.claps),
            "welcome": dict(self.welcome),
            "workspace": dict(self.workspace),
            "desktop": dict(self.desktop),
            "daemon": dict(self.daemon),
            "voice": dict(self.voice),
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
    memory_data = _section(document, "memory")
    audio_data = _section(document, "audio")
    activation_data = _section(document, "activation")
    stt_data = _section(document, "stt")
    tts_data = _section(document, "tts")
    alibaba_data = _section(document, "alibaba")
    models_data = _section(document, "models")
    hermes_data = _section(document, "hermes")
    tools_data = _section(document, "tools")

    deadline = runtime_data.get("command_deadline_ms", 500)
    if isinstance(deadline, bool) or not isinstance(deadline, int) or deadline <= 0:
        raise ValueError("runtime.command_deadline_ms must be a positive integer")

    return RuntimeConfig(
        runtime=RuntimeSettings(command_deadline_ms=deadline),
        memory=MemorySettings(
            db_path=_string(memory_data, "db_path", "memory/jarvis.db"),
            max_recall=_positive_int(memory_data, "max_recall", 6, "memory.max_recall"),
        ),
        audio=AudioSettings(
            sample_rate=_positive_int(audio_data, "sample_rate", 16000, "audio.sample_rate"),
            channels=_positive_int(audio_data, "channels", 1, "audio.channels"),
            input_device=_optional_int(audio_data, "input_device"),
            output_device=_optional_int(audio_data, "output_device"),
            barge_in=_bool(audio_data, "barge_in", False),
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
            provider=_choice(tts_data, "provider", "local", {"local", "sapi", "alibaba_qwen", "qwen_clone"}, "tts"),
            voice=_string(tts_data, "voice", ""),
            language=_string(tts_data, "language", "es"),
            api_key=_resolve_secret(tts_data.get("api_key"), env),
            worker_python=_string(tts_data, "worker_python", ""),
            profile_dir=_string(tts_data, "profile_dir", ""),
            model=_string(tts_data, "model", ""),
            chunk_size=_positive_int(tts_data, "chunk_size", 4, "tts.chunk_size"),
            warmup_wait_seconds=_number(tts_data, "warmup_wait_seconds", 0.0),
        ),
        alibaba=AlibabaSettings(
            region=_choice(alibaba_data, "region", "singapore", {"singapore", "beijing"}, "alibaba"),
            workspace_id=_optional_string(alibaba_data, "workspace_id"),
            stt_model=_string(alibaba_data, "stt_model", "qwen3-asr-flash-realtime"),
            tts_model=_string(alibaba_data, "tts_model", "qwen3-tts-flash-realtime"),
        ),
        models=ModelSettings(
            providers=tuple(_parse_providers(models_data, env)),
            max_daily_usd=_number(models_data, "max_daily_usd", 1.0),
        ),
        hermes=HermesSettings(
            command=tuple(_parse_command(hermes_data)),
            timeout_seconds=_positive_number(hermes_data, "timeout_seconds", 300.0, "hermes.timeout_seconds"),
            restart_max=_positive_int(hermes_data, "restart_max", 3, "hermes.restart_max"),
        ),
        tools=ToolSettings(
            allowlist=frozenset(_parse_allowlist(tools_data)),
            confirmation_timeout_seconds=_positive_number(tools_data, "confirmation_timeout_seconds", 30.0, "tools.confirmation_timeout_seconds"),
        ),
        **_desktop_sections(document),
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
                input_usd_per_million=_number(item, "input_usd_per_million", 0.0),
                output_usd_per_million=_number(item, "output_usd_per_million", 0.0),
                extra_body=_extra_body(item),
            )
        )
    return providers


def _extra_body(item: Mapping[str, Any]) -> dict[str, Any]:
    value = item.get("extra_body", {})
    if not isinstance(value, dict):
        raise ValueError("models.providers.extra_body must be an object")
    return dict(value)


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


_DESKTOP_KEYS = {"authorized_scopes", "trusted_operations", "apps"}
_DAEMON_KEYS = {
    "hotkey", "wake_word", "wake_word_model", "control_port", "input_device", "min_free_vram_mb_for_ollama",
    "session_idle_seconds", "metrics_interval_seconds", "events_log",
}


def _desktop_sections(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate the desktop/daemon sections with the classes that consume them."""
    from jarvis.adapters.audio.claps import ClapTuning  # noqa: PLC0415
    from jarvis.application.startup import StartupOptions  # noqa: PLC0415
    from jarvis.application.workspace import parse_profiles  # noqa: PLC0415

    claps = dict(_section(document, "claps"))
    welcome = dict(_section(document, "welcome"))
    workspace = dict(_section(document, "workspace"))
    desktop = dict(_section(document, "desktop"))
    daemon = dict(_section(document, "daemon"))
    voice = dict(_section(document, "voice"))
    try:
        ClapTuning(**{k: v for k, v in claps.items() if k != "enabled"})
        StartupOptions(**{k: tuple(v) if k == "essential" else v for k, v in welcome.items()})
        from jarvis.application.voice_manager import VoicePolicy  # noqa: PLC0415

        VoicePolicy.from_config(voice)
    except TypeError as error:
        raise ValueError(f"unknown claps/welcome/voice setting: {error}") from error
    profiles = parse_profiles(workspace.get("profiles", {}))
    default = workspace.get("default_profile")
    if default is not None and default not in profiles:
        raise ValueError(f"workspace.default_profile {default!r} is not a defined profile")
    for name, data, allowed in (("desktop", desktop, _DESKTOP_KEYS), ("daemon", daemon, _DAEMON_KEYS)):
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown {name} settings: {sorted(unknown)}")
    for key in ("authorized_scopes", "trusted_operations"):
        value = desktop.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"desktop.{key} must be a list of strings")
    apps = desktop.get("apps", {})
    if not isinstance(apps, dict) or not all(isinstance(v, list) and all(isinstance(p, str) for p in v) for v in apps.values()):
        raise ValueError("desktop.apps must map names to command lists")
    return {"claps": claps, "welcome": welcome, "workspace": workspace, "desktop": desktop, "daemon": daemon, "voice": voice}
