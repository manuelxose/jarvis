"""Natural-language desktop requests -> validated multi-step tool plans.

The configured LLM chain (DeepSeek first, Ollama fallback) sees the tool
registry (names, descriptions, argument schemas) and the owner's desktop
context (last project, recent VS Code folders, last failed command) and returns
compact JSON. Plans are executed step by step through the ToolGateway, so the
risk policy applies to every step; the planner can never grant itself
permissions. New tools become plannable by registering them in the gateway.

Also here: :class:`VoiceConfirmer`, the spoken yes/no gate used by the
gateway for HIGH-risk actions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional

from jarvis.core.contracts import ModelProvider, TurnContext
from jarvis.core.errors import ToolError

logger = logging.getLogger("jarvis.planner")

MAX_STEPS = 6
PROGRESS_AFTER_SECONDS = 4.0

_PROMPT = """Eres el planificador de escritorio de Jarvis en el Windows de su dueño.
Convierte la petición en herramientas. Responde SOLO con JSON compacto, sin texto extra:
{{"steps":[{{"tool":"nombre","arguments":{{...}}}}],"say":"frase corta en español","ask":null}}
Si falta un dato esencial y el contexto no lo resuelve, devuelve steps vacío y "ask" con UNA pregunta concreta.
Usa rutas absolutas del contexto. Nunca inventes herramientas.
Herramientas:
{tools}
Contexto: {context}
Petición: {text}"""


@dataclass
class Plan:
    """Tool steps to run, a sentence to say, or one clarifying question to ask."""
    steps: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    say: str = ""
    ask: Optional[str] = None


def _schema_line(tool: Any) -> str:
    args = []
    for key, spec in (tool.input_schema or {}).items():
        spec = spec if isinstance(spec, Mapping) else {}
        kind = "|".join(spec["enum"]) if "enum" in spec else spec.get("type", "any")
        args.append(f"{key}{'' if spec.get('required') else '?'}:{kind}")
    return f"- {tool.name}({', '.join(args)}): {tool.description}"


def parse_plan(raw: str, known: set[str]) -> Plan:
    """Extract and validate the JSON plan; unknown tools are dropped."""
    match = re.search(r"\{.*\}", raw or "", flags=re.DOTALL)
    if match is None:
        raise ValueError("planner returned no JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("planner JSON must be an object")
    steps: list[tuple[str, dict[str, Any]]] = []
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool, arguments = step.get("tool"), step.get("arguments") or {}
        if tool in known and isinstance(arguments, dict):
            steps.append((tool, arguments))
        else:
            logger.warning("planner proposed unknown/invalid step %r", step)
    ask = data.get("ask")
    return Plan(steps[:MAX_STEPS], str(data.get("say") or "")[:300], str(ask)[:300] if ask else None)


class DesktopPlanner:
    """Turn a free-form desktop request into a JSON plan over the tool registry and run it through the gateway."""

    def __init__(
        self,
        *,
        model: ModelProvider,
        gateway: Any,
        context: Callable[[], Mapping[str, Any]],
        speak: Optional[Callable[[str, TurnContext], Awaitable[None]]] = None,
    ) -> None:
        self._model = model
        self._gateway = gateway
        self._context = context
        self.speak = speak

    def context_json(self) -> str:
        try:
            return json.dumps(self._context(), ensure_ascii=False, default=str)[:3000]
        except Exception:  # noqa: BLE001 - context is best effort
            return "{}"

    async def plan(self, text: str, context: TurnContext) -> Plan:
        tools = "\n".join(_schema_line(t) for t in self._gateway.tools())
        prompt = _PROMPT.format(tools=tools, context=self.context_json(), text=text)
        raw = "".join([token async for token in self._model.generate(prompt, context)])
        return parse_plan(raw, set(self._gateway.names))

    async def handle(self, text: str, context: TurnContext) -> str:
        """Plan and execute; returns the sentence to speak."""
        try:
            plan = await self.plan(text, context)
        except (ValueError, json.JSONDecodeError) as error:
            logger.warning("desktop plan failed: %s", error)
            return "No he entendido qué quieres que haga en el escritorio. ¿Puedes decirlo de otra forma?"
        if plan.ask:
            return plan.ask
        if not plan.steps:
            return plan.say or "No hay nada que hacer."
        results: list[str] = []
        announced = False  # one short progress update per request, not one per step
        for index, (name, arguments) in enumerate(plan.steps):
            task = asyncio.ensure_future(self._gateway.execute(name, arguments, context))
            done, _ = await asyncio.wait({task}, timeout=PROGRESS_AFTER_SECONDS)
            if not done and not announced and self.speak is not None:
                announced = True
                await self.speak(plan.say or "Un momento, sigo con ello.", context)
            try:
                result = await task
            except ToolError as error:
                done_count = f"He completado {index} de {len(plan.steps)} pasos. " if index else ""
                return f"{done_count}No he podido completar {name}: {_short(error)}"
            results.append(str(result))
        if len(plan.steps) == 1:
            return results[0]
        return (plan.say + " " if plan.say and not announced else "") + " ".join(r for r in results[-2:] if r)


def _short(error: Exception) -> str:
    return str(error).split(": ", 1)[-1][:160]


# -- spoken confirmation ------------------------------------------------------------

_YES = ("confirmo", "confirmado", "adelante", "hazlo", "si hazlo", "si confirmo", "procede", "si adelante", "si", "vale", "de acuerdo")
_NO = ("no", "cancela", "cancelar", "para", "espera", "negativo")


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    words = re.sub(r"[^a-z0-9\s]", " ", plain).split()
    if words and words[0] in ("jarvis", "yarvis"):
        words = words[1:]
    return " ".join(words)


def is_affirmative(text: str) -> bool:
    """Explicit yes only: short, starts with a yes-phrase, contains no negation."""
    words = _norm(text)
    if not words or len(words.split()) > 5:
        return False
    if any(w in _NO for w in words.split()):
        return False
    return any(words == yes or words.startswith(yes + " ") for yes in _YES)


class VoiceConfirmer:
    """Speak what will happen, then wait for the owner's next utterance."""

    def __init__(self, describe: Callable[[str, Mapping[str, Any]], str], timeout_seconds: float = 20.0) -> None:
        self._describe = describe
        self.timeout_seconds = timeout_seconds
        self.speak: Optional[Callable[[str, TurnContext], Awaitable[None]]] = None
        self._pending: Optional[asyncio.Future[str]] = None

    @property
    def waiting(self) -> bool:
        return self._pending is not None and not self._pending.done()

    def answer(self, text: str) -> None:
        if self.waiting:
            self._pending.set_result(text)

    async def __call__(self, name: str, arguments: Mapping[str, Any], context: TurnContext) -> bool:
        if self.speak is None:
            return False
        await self.speak(f"Atención: voy a {self._describe(name, arguments)}. ¿Confirmas? Di «confirmo» o «no».", context)
        self._pending = asyncio.get_running_loop().create_future()
        try:
            reply = await asyncio.wait_for(asyncio.shield(self._pending), self.timeout_seconds)
        except asyncio.TimeoutError:
            await self.speak("No he oído confirmación; no lo hago.", context)
            return False
        finally:
            self._pending = None
        approved = is_affirmative(reply)
        logger.info("confirmation for %s: %r -> %s", name, reply, approved)
        if not approved:
            await self.speak("Entendido, no lo hago.", context)
        return approved
