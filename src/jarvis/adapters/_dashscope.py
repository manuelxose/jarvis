"""Central Alibaba Cloud Model Studio (DashScope) endpoint/config resolver.

Shared by ``adapters/stt/alibaba_qwen.py`` and ``adapters/tts/alibaba_qwen.py``
so region, workspace, and auth wiring live in exactly one place instead of
being duplicated per adapter (see docs/engineering/alibaba-qwen-voice-research.md
for the sourcing behind these choices).

``dashscope`` (https://pypi.org/project/dashscope/) is the official Alibaba
SDK; it owns the WebSocket task protocol (run-task/continue-task/finish-task
framing, auth headers, reconnection) so the adapters never hand-roll it.
Import is lazy: a machine without the optional dependency installed can still
run every other provider.

Region note: ``dashscope.set_region()`` only ever points the *global*
``base_websocket_api_url`` at the workspace-dedicated
``{workspace_id}.{region}.maas.aliyuncs.com`` host. Alibaba's realtime TTS
class (``QwenTtsRealtime``) does not consult that global -- its own
constructor defaults to the Beijing legacy host -- so the TTS adapter must be
handed ``dashscope.base_websocket_api_url`` explicitly after calling
``configure_dashscope()``. The ASR class (``Recognition``) does consult the
global and needs no such override.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from jarvis.core.errors import ProviderConfigError

if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig

# Maps Jarvis's config.alibaba.region choice to DashScope's MaaS region code.
# "beijing" is DashScope's own default region and needs no set_region() call.
_REGION_CODES = {"singapore": "ap-southeast-1", "beijing": "cn-beijing"}


def dashscope_available() -> bool:
    """Return True when the optional ``dashscope`` SDK is importable."""
    try:
        import dashscope  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass(frozen=True)
class DashScopeSession:
    """Resolved, ready-to-use DashScope connection settings for one provider."""

    api_key: str
    workspace_id: str
    region_code: str
    websocket_url: str


def configure_dashscope(config: "RuntimeConfig", *, api_key: Optional[str], provider: str) -> DashScopeSession:
    """Validate config and point the ``dashscope`` SDK at the right region.

    Raises ``ProviderConfigError`` with an actionable message if the api key
    or workspace id required for the selected region is missing -- this is
    the "fail startup validation" requirement for the Alibaba realtime path.
    """
    import dashscope  # noqa: PLC0415

    if not api_key:
        raise ProviderConfigError("alibaba_qwen has no DASHSCOPE_API_KEY configured", provider=provider)

    region = config.alibaba.region
    region_code = _REGION_CODES.get(region)
    if region_code is None:
        raise ProviderConfigError(f"alibaba_qwen has an unsupported region: {region!r}", provider=provider)

    workspace_id = config.alibaba.workspace_id
    dashscope.api_key = api_key

    if region_code == "cn-beijing":
        # DashScope's own default region; the legacy dashscope.aliyuncs.com
        # host works without a workspace-dedicated set_region() call.
        websocket_url = dashscope.base_websocket_api_url
        return DashScopeSession(api_key, workspace_id or "", region_code, websocket_url)

    if not workspace_id:
        raise ProviderConfigError(
            "alibaba_qwen (region=singapore) requires alibaba.workspace_id "
            "(ALIBABA_MODEL_STUDIO_WORKSPACE_ID) to be set",
            provider=provider,
        )
    dashscope.set_region(region=region_code, workspace_id=workspace_id)
    return DashScopeSession(api_key, workspace_id, region_code, dashscope.base_websocket_api_url)
